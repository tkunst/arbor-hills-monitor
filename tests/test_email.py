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
    # The credibility test: a 180F PERMITTED ceiling must NOT trigger urgent.
    m = [{"metric": "temperature", "value": 180, "unit": "F",
          "basis": "permitted_limit", "well_id": "AHW263"}]
    assert ea.is_urgent(_doc(severity="notable", measurements=m), CFG) is False


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
