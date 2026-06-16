"""Urgency logic — especially the permitted-vs-measured temperature distinction,
which is the credibility-critical case."""
import email_alerts as ea
from egle_doc_parser import ParsedDoc

CFG = {"urgent": {"severity_is_urgent": True, "measured_temp_urgent_f": 145}}


def _doc(severity="routine", measurements=None, full_text=""):
    return ParsedDoc(
        summary="s", key_data_point="k", doc_type="evidence", risks=["R8"],
        severity=severity, full_text=full_text, ocr_applied=False, page_count=1,
        measurements=measurements or [],
    )


def test_severity_urgent_fires():
    assert ea.is_urgent(_doc(severity="urgent"), CFG) is True


def test_measured_temp_at_or_above_threshold_fires():
    m = [{"metric": "temperature", "value": 150, "unit": "F", "basis": "measured",
          "well_id": "AHW272"}]
    assert ea.is_urgent(_doc(measurements=m), CFG) is True


def test_permitted_ceiling_does_not_fire():
    # The credibility test: a 180F PERMITTED ceiling must NOT trigger urgent --
    # even though the document text literally says "180 F" (the regex fallback
    # must NOT run once any structured temperature was extracted).
    m = [{"metric": "temperature", "value": 180, "unit": "F",
          "basis": "permitted_limit", "well_id": "AHW263"}]
    doc = _doc(
        severity="notable",
        measurements=m,
        full_text="HOV waiver requested: ceiling of 180 F for well AHW263.",
    )
    assert ea.is_urgent(doc, CFG) is False


def test_measured_below_threshold_does_not_fire():
    m = [{"metric": "temperature", "value": 140, "unit": "F", "basis": "measured"}]
    assert ea.is_urgent(_doc(measurements=m), CFG) is False


def test_max_measured_temp_excludes_permitted():
    m = [
        {"metric": "temperature", "value": 180, "unit": "F", "basis": "permitted_limit"},
        {"metric": "temperature", "value": 138, "unit": "F", "basis": "measured"},
    ]
    assert ea.max_measured_temp_f(_doc(measurements=m)) == 138


def test_celsius_measured_is_converted():
    m = [{"metric": "temperature", "value": 70, "unit": "C", "basis": "measured"}]  # 158F
    assert ea.max_measured_temp_f(_doc(measurements=m)) == 158
    assert ea.is_urgent(_doc(measurements=m), CFG) is True


def test_free_text_fallback_when_no_structured_measurements():
    # No structured measurements -> fall back to scanning text.
    assert ea.is_urgent(_doc(full_text="probe read 165 F at the wellhead"), CFG) is True


# --- recipient resolution: config.yml + private ALERT_RECIPIENTS_EXTRA env ---

def test_resolve_recipients_config_only(monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS_EXTRA", raising=False)
    assert ea.resolve_recipients({"alert_recipients": ["a@x.com"]}) == ["a@x.com"]


def test_resolve_recipients_merges_env_and_dedups(monkeypatch):
    # The env carries PRIVATE addresses kept out of the public repo's config.yml.
    monkeypatch.setenv("ALERT_RECIPIENTS_EXTRA", "a@x.com, b@yahoo.com ; c@x.com")
    assert ea.resolve_recipients({"alert_recipients": ["a@x.com"]}) == [
        "a@x.com", "b@yahoo.com", "c@x.com",
    ]


def test_resolve_recipients_blank_env_is_noop(monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENTS_EXTRA", "  ")
    assert ea.resolve_recipients({"alert_recipients": ["a@x.com"]}) == ["a@x.com"]
