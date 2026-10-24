"""ADR 067: temperature-consistency check on classifier free text.

Every "bad" string below is a real key data point that reached the public
Sheet before this check existed; every "good" string is a real one that must
keep passing (false withholds are the main risk of this check)."""
import egle_doc_parser as edp
import kdp_check as kc

REF = kc.TempReference(regulatory={131.0, 145.0},
                       hov_values={150.0, 155.0, 160.0, 170.0, 180.0},
                       max_plausible_wellhead_f=250.0, top_hov_wells={"AHW272R4"})

BAD = [
    # the 180°F HOV belongs to AHW272R4 only (the corrected parser's first
    # re-run of this document still said it)
    "AHWW312R measured a wellhead temperature of 170.4°F on 6/13/2022 (well "
    "operating under an approved 180°F HOV waiver).",
    # H2S ppm misread as a temperature, with an invented limit
    "Highest measured temperature: AHWW414R at 553°F on 5/14/24 (349°F above MACT "
    "limit of 204°F). Multiple wells operating under Higher Operating Value (HOV) "
    "waivers; dozens of wells >200°F in H1 2024.",
    "Highest measured temperature reading: well AHWW414R at 553°F on 5/14/24.",
    # invented 135°F "baseline MACT limit"
    "Well AHWW312R measured 170.2°F on multiple dates in June–July 2022 (35.2°F above "
    "the 135°F baseline MACT limit); Well AHWW0300 measured 154.4°F in June 2022 "
    "(19.4°F above MACT limit).",
    # arithmetic that doesn't reconcile with the limit it names
    "Well AHWW0286R: 180°F requested operating temperature (35°F above the 131°F NSPS limit).",
    "Well AHWW0313 recorded measured temperatures of 145.5–148.6°F (5.5–8.6°F above the "
    "140°F permitted limit and 0.5–8.6°F above the 145°F urgent threshold).",
]

GOOD = [
    "Arbor Hills' March 28, 2019 HOV request table shows well AHWW0286R seeking 180°F "
    "(35°F above the 145°F NESHAP MACT limit).",
    "180°F requested for well AHW272R4 — 35°F above MACT limit of 145°F; 16 WOI wells "
    "approved for HOV waiver extension through May 1, 2028.",
    "AHW272R4 measured temperature of 170.8°F on 2/10/2025, approaching the requested "
    "HOV ceiling of 180°F (35°F above the MACT limit of 145°F).",
    "HOV approval: 170°F for AHW263R5 (25°F above MACT limit of 145°F).",
    "Well AHWW0279 measured at 156.1°F (6.1°F above the 150°F HOV limit).",
    "Well AHWW0313 measured 147.1°F on 3/27/2024 (2°F above the 145°F urgent threshold).",
    "Approved HOVs range from 150°F to 180°F; AHW272R4 at 180°F.",
    "Enclosed flare combustion temperature averaged 1,612°F during the stack test.",
    "Flare F-2 operated at 1450 F during the performance test.",
    "AHWW0279 measured 154.6°F at the wellhead (as found), 4/8/2026.",
    "Methane at the surface reached 553 ppm near AHWW414R.",
    "",
]


def test_known_bad_strings_are_flagged():
    for s in BAD:
        assert kc.check_temperature_claims(s, REF), s


def test_known_good_strings_pass():
    for s in GOOD:
        assert kc.check_temperature_claims(s, REF) == [], s


def test_hov_gap_not_judged_without_hov_list():
    ref = kc.TempReference()  # no HOV list configured
    assert kc.check_temperature_claims(
        "Well X measured 156.1°F (6.1°F above the HOV limit).", ref) == []


def test_reference_from_config_reads_existing_keys():
    ref = kc.reference_from_config({"temperature_thresholds": {
        "epa_gas_operating_f": 131, "mact_f": 145,
        "hov_values_f": [150, 180], "max_plausible_wellhead_f": 240}})
    assert ref.regulatory == {131.0, 145.0}
    assert ref.hov_values == {150.0, 180.0}
    assert ref.max_plausible_wellhead_f == 240.0


def _fields(kdp, summary="A report."):
    return {"summary": summary, "key_data_point": kdp, "doc_type": "evidence",
            "risks": [], "severity": "routine"}


def test_enforce_passes_clean_output_without_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(edp, "_classify_with_claude",
                        lambda *a, **k: calls.append(k) or _fields("x"))
    out = edp._enforce_temperature_consistency(_fields(GOOD[0]), "t", {}, [], "m", ref=REF)
    assert out["key_data_point"] == GOOD[0] and calls == []


def test_enforce_retries_once_and_uses_clean_retry(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(k.get("correction"))
        return _fields(GOOD[-2])
    monkeypatch.setattr(edp, "_classify_with_claude", fake)
    out = edp._enforce_temperature_consistency(_fields(BAD[0]), "t", {}, [], "m", ref=REF)
    assert out["key_data_point"] == GOOD[-2]
    assert len(calls) == 1 and "consistency check" in calls[0]


def test_enforce_withholds_when_retry_still_fails(monkeypatch):
    monkeypatch.setattr(edp, "_classify_with_claude", lambda *a, **k: _fields(BAD[2]))
    out = edp._enforce_temperature_consistency(_fields(BAD[0]), "t", {}, [], "m", ref=REF)
    assert out["key_data_point"] == kc.WITHHELD_KDP
    assert out["summary"] == "A report."


def test_enforce_withholds_when_retry_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(edp, "_classify_with_claude", boom)
    out = edp._enforce_temperature_consistency(
        _fields(BAD[1], summary=BAD[1]), "t", {}, [], "m", ref=REF)
    assert out["key_data_point"] == kc.WITHHELD_KDP
    assert out["summary"] == kc.WITHHELD_SUMMARY


def test_prompt_states_reference_values_and_drops_old_example():
    p = edp._build_system_prompt([{"id": "R8", "name": "n", "description": "d"}])
    assert "131F" in p and "145F" in p and "ppm, not degrees" in p
    assert "35F above MACT" not in p


# --- gaps 1-3 (ADR 067) -----------------------------------------------------
import email_alerts as ea  # noqa: E402


def test_sanitize_retags_implausible_measured_temperature():
    ms = [{"metric": "temperature", "value": 553, "unit": "F", "basis": "measured",
           "well_id": "AHWW414R", "note": "HCH Meter"},
          {"metric": "temperature", "value": 154.6, "unit": "F", "basis": "measured"},
          {"metric": "temperature", "value": 1612, "unit": "F", "basis": "measured",
           "note": "enclosed flare combustion temperature"}]
    out, n = kc.sanitize_measurements(ms, REF)
    assert n == 1
    assert out[0]["basis"] == "unknown" and out[1]["basis"] == "measured"
    assert out[2]["basis"] == "measured"  # flare exempt
    assert kc.max_plausible_measured_f(out, REF) == 154.6


def test_text_fallback_ignores_non_wellhead_values():
    assert ea._max_temperature_f("AHWW414R 553 F on 5/14/24") is None
    assert ea._max_temperature_f("probe read 165 F at the wellhead") == 165


def test_withheld_urgent_without_plausible_reading_drops_to_notable(monkeypatch):
    bad = dict(_fields(BAD[1]), severity="urgent",
               measurements=[{"metric": "temperature", "value": 553, "unit": "F",
                              "basis": "measured", "well_id": "AHWW414R"}])
    monkeypatch.setattr(edp, "_classify_with_claude", lambda *a, **k: dict(bad))
    out = edp._enforce_temperature_consistency(dict(bad), "t", {}, [], "m", ref=REF)
    assert out["key_data_point"] == kc.WITHHELD_KDP
    assert out["severity"] == "notable"
    assert out["measurements"][0]["basis"] == "unknown"
    assert ea.is_urgent(type("P", (), {"severity": out["severity"],
                                        "measurements": out["measurements"],
                                        "key_data_point": out["key_data_point"],
                                        "summary": "", "full_text": "553 F"})(),
                        {"urgent": {"measured_temp_urgent_f": 145}}) is False


def test_withheld_urgent_kept_when_plausible_reading_backs_it(monkeypatch):
    bad = dict(_fields(BAD[2]), severity="urgent",
               measurements=[{"metric": "temperature", "value": 170.2, "unit": "F",
                              "basis": "measured"}])
    monkeypatch.setattr(edp, "_classify_with_claude", lambda *a, **k: dict(bad))
    out = edp._enforce_temperature_consistency(dict(bad), "t", {}, [], "m", ref=REF)
    assert out["severity"] == "urgent"


def test_sanitize_leaves_unattached_high_readings_alone():
    # stack / thermal-oxidizer readings carry no well id: not wellhead, keep as measured
    ms = [{"metric": "temperature", "value": 1700, "unit": "F", "basis": "measured",
           "note": "EURNGTOX operating temperature"},
          {"metric": "temperature", "value": 447, "unit": "F", "basis": "measured",
           "note": "Flare thermocouple reading"}]
    out, n = kc.sanitize_measurements(ms, REF)
    assert n == 0 and all(m["basis"] == "measured" for m in out)


def test_sanitize_leaves_flare_ids_alone():
    # real Measurements rows: flare ids sit in the Well ID column
    ms = [{"metric": "temperature", "value": 1701, "unit": "F", "basis": "measured",
           "well_id": "EUENCLOSEDFLARE2", "note": "Control panel reading at 10:00 AM"},
          {"metric": "temperature", "value": 1520, "unit": "F", "basis": "measured",
           "well_id": "391", "note": "compliant temperature reading"},
          {"metric": "temperature", "value": 1875.08, "unit": "F", "basis": "measured",
           "well_id": "TE391", "note": "waste temperature at peak"}]
    out, n = kc.sanitize_measurements(ms, REF)
    assert n == 0 and all(m["basis"] == "measured" for m in out)


def test_mact_limit_is_one_number_in_config():
    # urgent.measured_temp_urgent_f and temperature_thresholds.mact_f are tied
    # by a YAML anchor so they can never drift apart.
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yml").read_text())
    assert cfg["urgent"]["measured_temp_urgent_f"] == cfg["temperature_thresholds"]["mact_f"] == 145
