"""key_data_point guard + urgency_reason + HOV ceiling context
(coder:keydatapoint-guard, docs/overnight-coder-handoffs/keydatapoint-guard.md).

The 2026-10-08 bug: an [URGENT] email and the public Sheet row for an N2688 WOI
Status Report said a well read 202F. The 202 was a Depth to Bottom sounding (feet)
on a page the classifier saw without its column-header page. woi_router had
already replaced the measurements with the exhaustive, validated table parse, but
the headline (key_data_point) was still the model's windowed text. These tests pin
the fix: for a routed report the headline comes from woi_router's own data, the
Sheet row carries it, and the urgent body says why it fired. Hermetic: synthetic
PDFs built in-process (tests/test_woi_router.py's builder), no network."""
from types import SimpleNamespace

import pytest

import doc_pipeline as dp
import email_alerts as ea
import sheet_writer as sw
import woi_router
from tests.test_woi_router import CFG, META, _build_pdf, _many

FABRICATED_KDP = "Well AHW331 recorded 202°F, far above the 145°F MACT limit."
FABRICATED_SUMMARY = ("The report shows AHW331 at 202°F, well above the MACT limit. "
                      "Several wells remain on the WOI list.")


def _parsed(kdp=FABRICATED_KDP, summary=FABRICATED_SUMMARY, severity="routine",
            measurements=None, severity_floor=None, severity_before_floor=""):
    return SimpleNamespace(doc_type="evidence", severity=severity, risks=["R8"],
                           display_title="", document_date="",
                           measurements=list(measurements or []), deadlines=[],
                           key_data_point=kdp, summary=summary, full_text="",
                           severity_floor=list(severity_floor or []),
                           severity_before_floor=severity_before_floor)


def _page59_pdf(tmp_path):
    """The real bug shape. The report's own gas table (header on p8) holds
    AHWW312R as the hottest well at 158.3F on 3/14/2025. Nothing in the table is
    202. The 202 the classifier reported was a Depth to Bottom sounding on a
    'page 59' with no header row of its own; that page isn't a gas table, so
    the exhaustive parse never turns it into a reading."""
    reads = _many(54, temp=90) + [("AHWW312R", 158.3), ("AHW272R4", 150.0)]
    return _build_pdf(tmp_path / "woi.pdf", reads)


# --- 1. deterministic headline -----------------------------------------------

def test_build_key_data_point_formats_hottest_well():
    routed = {"summary": [{"well": "AHWW312R", "max_temp_f": 158.3,
                           "max_temp_date": "8/29/2023 10:15"},
                          {"well": "AHW272R4", "max_temp_f": 150.0,
                           "max_temp_date": "8/30/2023 09:00"}]}
    assert woi_router.build_key_data_point(routed, None, CFG) == (
        "AHWW312R: max measured 158.3°F (2023-08-29, Gas Extraction Report).")


def test_build_key_data_point_none_without_a_temperature():
    assert woi_router.build_key_data_point({"summary": []}, None, CFG) is None
    assert woi_router.build_key_data_point(
        {"summary": [{"well": "X", "max_temp_f": None, "max_temp_date": None}]}, None, CFG) is None


def test_woi_routed_key_data_point_uses_deterministic_temp(tmp_path):
    # Regression for the windowed-page bug (handoff item 4): the classifier's
    # fabricated 202F headline must not survive a successful route.
    parsed = _parsed()
    routed = woi_router.route_measurements(parsed, _page59_pdf(tmp_path), META, CFG)
    assert routed is not None
    top = routed["summary"][0]
    assert top["well"] == "AHWW312R" and top["max_temp_f"] == 158.3
    woi_router.apply_deterministic_headline(parsed, routed, CFG)
    assert "202°F" not in parsed.key_data_point
    assert parsed.key_data_point == (
        "AHWW312R: max measured 158.3°F (2025-03-14, Gas Extraction Report).")


@pytest.mark.parametrize("kdp", [
    # Owner ruling 2026-10-17: a headline that isn't about temperature (a
    # Consent Judgment compilation's deadline or enforcement line) is kept.
    "Consent Judgment quarterly report; corrective-action deadline 2026-11-01.",
    "EGLE issued a Violation Notice for the gas collection system.",
    # A temperature the tables DO support is kept too.
    "AHWW312R reached 158°F, above the 145°F MACT limit.",
])
def test_headline_kept_when_not_contradicting_the_tables(tmp_path, kdp):
    parsed = _parsed(kdp=kdp, summary="ok")
    routed = woi_router.route_measurements(parsed, _page59_pdf(tmp_path), META, CFG)
    woi_router.apply_deterministic_headline(parsed, routed, CFG)
    assert parsed.key_data_point == kdp


@pytest.mark.parametrize("kdp", ["AHW331 reached 160°F.", "AHW331 hit the 145°F MACT limit.",
                                 "AHW272R4 at its 180°F HOV ceiling.",
                                 # Accurate reading plus a reference value: still
                                 # replaced (the ruling's accepted cost, pinned).
                                 "AHWW312R at 140°F, under the 145°F MACT limit."])
def test_headline_citing_a_reference_value_above_the_peak_is_replaced(tmp_path, kdp):
    # Owner ruling 2026-10-17: no reference-value exemption for headlines.
    # Table peak here is 140F, so 145/160/180 are all unsupported.
    pdf = _build_pdf(tmp_path / "cool.pdf", _many(54, temp=90) + [("AHWW312R", 140.0)])
    parsed = _parsed(kdp=kdp, summary="ok")
    routed = woi_router.route_measurements(parsed, pdf, META, CFG)
    woi_router.apply_deterministic_headline(parsed, routed, CFG)
    assert parsed.key_data_point.startswith("AHWW312R: max measured 140°F")


def test_summary_keeps_the_reference_value_exemption(tmp_path):
    pdf = _build_pdf(tmp_path / "cool.pdf", _many(54, temp=90) + [("AHWW312R", 140.0)])
    summary = "No well reached the 145°F MACT limit."
    parsed = _parsed(kdp="", summary=summary)
    routed = woi_router.route_measurements(parsed, pdf, META, CFG)
    woi_router.apply_deterministic_headline(parsed, routed, CFG)
    assert parsed.summary == summary


def test_empty_headline_is_filled_from_the_tables(tmp_path):
    parsed = _parsed(kdp="", summary="ok")
    routed = woi_router.route_measurements(parsed, _page59_pdf(tmp_path), META, CFG)
    woi_router.apply_deterministic_headline(parsed, routed, CFG)
    assert parsed.key_data_point.startswith("AHWW312R: max measured 158.3°F")


# --- pipeline level: the Sheet row, not just the email -----------------------

@pytest.fixture
def pipeline(monkeypatch):
    rows = []
    monkeypatch.setattr(dp.nc, "download_pdf", lambda *a: None)
    monkeypatch.setattr(dp.av, "mirror_one_now", lambda *a, **k: "LINK")
    # Capture STRINGS at write time: parsed is mutable, so holding the object
    # would hide a fix applied after the Sheet row was written.
    monkeypatch.setattr(dp.sw, "write_document",
                        lambda s, sid, p, *a, **k: rows.append((p.key_data_point, p.summary)))
    monkeypatch.setattr(dp.sw, "ensure_woi_tabs", lambda *a, **k: None)
    monkeypatch.setattr(dp.sw, "write_woi_summary", lambda *a, **k: None)
    monkeypatch.setattr(dp.sw, "mark_processed", lambda *a, **k: None)
    monkeypatch.setattr(dp.rf, "log_for_document", lambda *a, **k: None)
    return rows


def _ctx(parsed, pdf):
    cfg = {**CFG, "large_doc_max_keyword_pages": 10, "classification_max_tokens": 10,
           "temperature_thresholds": {"max_plausible_wellhead_f": 250,
                                      "hov_values_f": [150, 155, 160, 170, 180]}}
    return dp.RunContext(cfg=cfg, sheets=None, sheet_id="SID", session=None, model="m",
                         well_alias_map=None, parse=lambda *a, **k: parsed, now=lambda: "T")


def _doc():
    return {"doc_id": "1", "document_name": "nForm Document", "date_filed": "2026-10-08",
            "facility_scope": "core", "type_name": "nForm Document"}


def test_sheet_row_gets_corrected_key_data_point(pipeline, tmp_path):
    pdf = _page59_pdf(tmp_path)
    parsed = _parsed()
    alerts = []
    job = dp.Job(name="watcher", feed_tab=sw.TAB_NEW, payload=lambda p, d: {},
                 alert=lambda p, d, link: alerts.append((p.key_data_point, p.summary)))
    # process_one_doc deletes `local` in its finally; hand it a copy.
    local = tmp_path / "local.pdf"
    local.write_bytes((tmp_path / "woi.pdf").read_bytes())
    state = {"processed": {}, "errors": {}, "skipped": {}}
    assert dp.process_one_doc(_ctx(parsed, pdf), state, _doc(), job, str(local)) == "processed"
    (kdp, summary), = pipeline
    assert "202°F" not in kdp and "AHWW312R" in kdp and "158.3°F" in kdp
    # The summary carried the same fabricated number: withheld, not published.
    assert "202°F" not in summary and summary == woi_router.WITHHELD_WOI_SUMMARY
    assert alerts == [(kdp, summary)]                  # the alert sees the same text


def test_unrouted_document_keeps_classifier_text(pipeline, tmp_path, monkeypatch):
    monkeypatch.setattr(dp.woi_router, "route_measurements", lambda *a, **k: None)
    parsed = _parsed(kdp="AHW272 read 150°F.", summary="A short summary.")
    job = dp.Job(name="backfill", feed_tab=sw.TAB_HISTORICAL, payload=lambda p, d: {})
    state = {"processed": {}, "errors": {}, "skipped": {}}
    assert dp.process_one_doc(_ctx(parsed, ""), state, _doc(), job,
                              str(tmp_path / "x.pdf")) == "processed"
    assert pipeline == [("AHW272 read 150°F.", "A short summary.")]


def test_headline_failure_keeps_model_text_and_never_strikes(pipeline, tmp_path, monkeypatch):
    monkeypatch.setattr(dp.woi_router, "route_measurements",
                        lambda *a, **k: {"summary": [], "readings": []})
    monkeypatch.setattr(dp.woi_router, "apply_deterministic_headline",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    parsed = _parsed(kdp="model text", summary="model summary")
    job = dp.Job(name="backfill", feed_tab=sw.TAB_HISTORICAL, payload=lambda p, d: {})
    state = {"processed": {}, "errors": {}, "skipped": {}}
    assert dp.process_one_doc(_ctx(parsed, ""), state, _doc(), job,
                              str(tmp_path / "x.pdf")) == "processed"
    assert state["errors"] == {} and pipeline == [("model text", "model summary")]


# --- urgency floor recheck (owner ruling 2026-10-17) -------------------------

FAKE_202 = {"metric": "temperature", "value": 202, "unit": "F", "basis": "measured",
            "well_id": "AHW331", "as_of_date": "2025-03-14"}


def _run_alerting(tmp_path, pipeline, parsed, reads):
    """process_one_doc with the real is_urgent as the alert decision."""
    pdf = _build_pdf(tmp_path / "woi.pdf", reads)
    fired = []
    ctx = _ctx(parsed, pdf)
    job = dp.Job(name="watcher", feed_tab=sw.TAB_NEW, payload=lambda p, d: {},
                 alert=lambda p, d, link: fired.append((p.severity, ea.is_urgent(p, ctx.cfg))))
    local = tmp_path / "local.pdf"
    local.write_bytes((tmp_path / "woi.pdf").read_bytes())
    state = {"processed": {}, "errors": {}, "skipped": {}}
    assert dp.process_one_doc(ctx, state, _doc(), job, str(local)) == "processed"
    return fired[0]


def test_invented_reading_the_tables_discard_does_not_fire_urgent(pipeline, tmp_path):
    # The 2026-10-08 shape: the classifier's fabricated measured 202F floored
    # severity to urgent; the tables' real peak is 140F. No [URGENT].
    parsed = _parsed(severity="urgent", measurements=[FAKE_202],
                     severity_floor=["measured_temperature"], severity_before_floor="notable")
    assert ea.is_urgent(parsed, CFG) is True                      # before routing: fires
    severity, urgent = _run_alerting(tmp_path, pipeline, parsed,
                                     _many(54, temp=90) + [("AHWW312R", 140.0)])
    assert (severity, urgent) == ("notable", False)


def test_real_reading_at_or_above_145_still_fires_urgent(pipeline, tmp_path):
    parsed = _parsed(severity="urgent", measurements=[FAKE_202],
                     severity_floor=["measured_temperature"], severity_before_floor="notable")
    severity, urgent = _run_alerting(tmp_path, pipeline, parsed,
                                     _many(54, temp=90) + [("AHWW312R", 158.3)])
    assert (severity, urgent) == ("urgent", True)


@pytest.mark.parametrize("floor", [["text_signal"], ["measured_temperature", "text_signal"], []])
def test_other_urgent_sources_are_untouched(pipeline, tmp_path, floor):
    # An SSO/CO-spike/enforcement-title floor, or the classifier's own urgent
    # (no floor recorded), still fires even when the tables are cool.
    parsed = _parsed(severity="urgent", measurements=[FAKE_202], severity_floor=floor,
                     severity_before_floor="routine" if floor else "")
    severity, urgent = _run_alerting(tmp_path, pipeline, parsed,
                                     _many(54, temp=90) + [("AHWW312R", 140.0)])
    assert (severity, urgent) == ("urgent", True)


def _floored(peak_f, before="notable"):
    return SimpleNamespace(severity="urgent", severity_floor=["measured_temperature"],
                           severity_before_floor=before,
                           measurements=[{"metric": "temperature", "value": peak_f, "unit": "F",
                                          "basis": "measured"}])


def test_recheck_keeps_urgent_when_the_peak_rounds_to_the_threshold():
    # is_urgent fires on 144.6 (rounds to 145); the record must agree.
    p = _floored(144.6)
    woi_router.recheck_severity_floor(p, {}, CFG)
    assert p.severity == "urgent" and ea.is_urgent(p, CFG) is True


def test_recheck_keeps_urgent_when_thresholds_drift_apart():
    # The floor fired at mact_f 145; is_urgent's threshold set to 150. A real
    # 147F table reading still keeps the document urgent.
    cfg = {**CFG, "urgent": {"measured_temp_urgent_f": 150},
           "temperature_thresholds": {"mact_f": 145}}
    p = _floored(147.0)
    woi_router.recheck_severity_floor(p, {}, cfg)
    assert p.severity == "urgent"


def test_recheck_reverts_to_recorded_severity_or_notable():
    p = _floored(140.0, before="routine")
    woi_router.recheck_severity_floor(p, {}, CFG)
    assert p.severity == "routine"
    p = _floored(140.0, before="")
    woi_router.recheck_severity_floor(p, {}, CFG)
    assert p.severity == "notable"


def test_severity_recheck_failure_keeps_severity_and_never_strikes(pipeline, tmp_path,
                                                                    monkeypatch):
    monkeypatch.setattr(dp.woi_router, "recheck_severity_floor",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    parsed = _parsed(severity="urgent", measurements=[FAKE_202],
                     severity_floor=["measured_temperature"], severity_before_floor="notable")
    severity, urgent = _run_alerting(tmp_path, pipeline, parsed,
                                     _many(54, temp=90) + [("AHWW312R", 140.0)])
    assert severity == "urgent"


def test_parse_document_records_why_the_floor_fired():
    import egle_doc_parser as edp
    p = edp.ParsedDoc(summary="", key_data_point="", doc_type="evidence", risks=[],
                      severity="routine", full_text="", ocr_applied=False, page_count=1)
    assert p.severity_floor == [] and p.severity_before_floor == ""


# --- summary guard: narrow on purpose ----------------------------------------

def _routed(temps):
    readings = [SimpleNamespace(temp=t) for t in temps]
    return {"summary": [], "readings": readings}


_GUARD_CFG = {**CFG, "temperature_thresholds": {
    "epa_gas_operating_f": 131, "mact_f": 145, "max_plausible_wellhead_f": 250,
    "hov_values_f": [150, 155, 160, 170, 180]}}


@pytest.mark.parametrize("summary", [
    "AHWW312R peaked at 158.3°F.",                 # the routed peak itself
    "AHWW312R peaked near 159°F.",                 # model rounding, within 1F
    "Wells AHW272R4 holds an HOV of 180°F.",        # an HOV value, a reference number
    "Above the 145°F MACT limit and 131°F EPA limit.",
    "A flare ran at 1,500°F.",                      # above the plausibility ceiling
    "Ambient air was 85°F.",                        # below the watch band
    "No temperatures stated.",
    "AHWW312R peaked at 158.3 degrees Fahrenheit.",
])
def test_summary_guard_keeps_consistent_summaries(summary):
    assert woi_router.text_contradicts_routed(summary, _routed([158.3, 150.0, 90]),
                                                 _GUARD_CFG) == []


def test_summary_guard_flags_a_value_above_every_reading():
    assert woi_router.text_contradicts_routed(
        FABRICATED_SUMMARY, _routed([158.3, 150.0, 90]), _GUARD_CFG) == [202.0]


@pytest.mark.parametrize("summary", ["AHW331 hit 202 degrees Fahrenheit.", "AHW331 hit 202º F.",
                                     "AHW331 hit 202˚F.", "AHW331 hit 202 deg F."])
def test_summary_guard_reads_other_degree_spellings(summary):
    assert woi_router.text_contradicts_routed(
        summary, _routed([158.3]), _GUARD_CFG) == [202.0]


# --- 2. urgency_reason --------------------------------------------------------

def _doc_m(measurements, severity="routine"):
    return SimpleNamespace(doc_type="evidence", severity=severity, risks=["R8"],
                           key_data_point="k", summary="s", full_text="",
                           measurements=measurements)


def _temp(v, well="AHWW312R", basis="measured", date="2023-08-29"):
    return {"metric": "temperature", "value": v, "unit": "F", "basis": basis,
            "well_id": well, "as_of_date": date}


def test_urgent_body_states_urgency_reason():
    parsed = _doc_m([_temp(158.3)], severity="routine")
    assert ea.is_urgent(parsed, CFG) is True
    body = ea.format_urgent_body(parsed, {"document_name": "D", "date_filed": "x"}, "L", CFG)
    assert "Urgent because: measured temperature 158.3°F at AHWW312R" in body
    assert "145°F" in body
    assert "Severity (as recorded): routine" in body
    # The bare "Severity:" label is gone: the body never shows a severity
    # without saying whose it is.
    assert "   Severity:" not in body


def test_urgency_reason_names_both_triggers():
    parsed = _doc_m([_temp(150, well="AHW272R4")], severity="urgent")
    reason = ea.urgency_reason(parsed, CFG)
    assert "measured temperature 150°F at AHW272R4" in reason
    assert 'severity is "urgent"' in reason and "deterministic urgency floor" in reason
    assert "hand-curator" in reason


def test_urgency_reason_classifier_only():
    parsed = _doc_m([_temp(120)], severity="urgent")
    reason = ea.urgency_reason(parsed, CFG)
    assert reason.startswith('the document\'s recorded severity is "urgent"')
    assert "measured" not in reason


def test_urgency_reason_free_text_fallback():
    parsed = SimpleNamespace(severity="routine", measurements=[], key_data_point="",
                             summary="", full_text="The well read 151 F on Tuesday.")
    assert ea.is_urgent(parsed, CFG) is True
    reason = ea.urgency_reason(parsed, CFG)
    assert "151°F" in reason and "free-text" in reason


def test_urgency_reason_shows_rounding_it_used():
    # max_measured_temp_f rounds before comparing (unchanged); the reason must
    # not print "144.6F, at or above 145F" without saying so.
    parsed = _doc_m([_temp(144.6)])
    assert ea.is_urgent(parsed, CFG) is True
    assert "144.6°F (rounds to 145°F)" in ea.urgency_reason(parsed, CFG)


def test_reason_names_the_highest_reading_on_a_rounding_tie():
    parsed = _doc_m([_temp(150.3, well="A"), _temp(150.4, well="B")])
    assert "150.4°F at B" in ea.urgency_reason(parsed, CFG)


def test_mact_label_only_when_threshold_is_the_mact_value():
    parsed = _doc_m([_temp(150)])
    assert "(MACT limit)" in ea.urgency_reason(parsed, CFG)
    other = {"urgent": {"measured_temp_urgent_f": 140}}
    assert "(MACT limit)" not in ea.urgency_reason(parsed, other)


def test_severity_urgent_short_circuits_before_reading_measurements():
    # As before this change: an urgent-severity doc fires without its
    # measurements being read, so a malformed entry can't make it raise.
    parsed = _doc_m([{"metric": "temperature", "basis": "measured", "value": 150, "unit": 5}],
                    severity="urgent")
    assert ea.is_urgent(parsed, CFG) is True
    # ...and the body built for it doesn't raise either (the alert still goes).
    body = ea.format_urgent_body(parsed, {}, "L", CFG)
    assert "Urgent because: (reason unavailable)." in body


def test_urgency_reason_empty_when_not_urgent():
    assert ea.urgency_reason(_doc_m([_temp(120)]), CFG) == ""


def test_is_urgent_unchanged_by_reason_function():
    # One decision function: is_urgent and urgency_reason can never disagree.
    cases = [_doc_m([_temp(t)], severity=s) for t in (100, 144.4, 144.6, 145, 200)
             for s in ("routine", "notable", "urgent")]
    for p in cases:
        assert ea.is_urgent(p, CFG) is bool(ea.urgency_reason(p, CFG))


# --- 3. HOV ceiling context (additive only) ----------------------------------

HOV_CFG = {**CFG, "temperature_thresholds": {
    "hov_ceilings": [{"well_id": "AHW272R4", "ceiling_f": 180,
                      "effective_start": "2025-02-19", "effective_end": None}]}}


def test_hov_ceiling_lookup_exact_id_and_dates():
    assert ea.hov_ceiling_for("AHW272R4", "2025-03-01", HOV_CFG) == 180
    assert ea.hov_ceiling_for("ahw272r4*", "2025-03-01", HOV_CFG) == 180
    assert ea.hov_ceiling_for("AHW272R4", "2025-01-01", HOV_CFG) is None   # before the grant
    assert ea.hov_ceiling_for("AHW272R3", "2025-03-01", HOV_CFG) is None   # a redrill isn't it
    assert ea.hov_ceiling_for("AHW272", "2025-03-01", HOV_CFG) is None
    assert ea.hov_ceiling_for("AHW272R4", "2025-03-01", CFG) is None       # no roster at all


@pytest.mark.parametrize("entry", [{"well_id": "AHW272R4"},
                                   {"well_id": "AHW272R4", "ceiling_f": "180F"},
                                   {"well_id": "AHW272R4", "ceiling_f": None}])
def test_malformed_roster_entry_never_breaks_the_alert(entry):
    cfg = {**CFG, "temperature_thresholds": {"hov_ceilings": [entry]}}
    assert ea.hov_ceiling_for("AHW272R4", "2025-03-01", cfg) is None
    parsed = _doc_m([_temp(150, well="AHW272R4", date="2025-03-01")])
    assert ea.is_urgent(parsed, cfg) is True
    body = ea.format_urgent_body(parsed, {}, "L", cfg)
    assert "Approved ceiling for AHW272R4 not in this monitor's roster." in body


def test_non_iso_reading_date_is_treated_as_undated():
    cfg = {**CFG, "temperature_thresholds": {"hov_ceilings": [
        {"well_id": "AHW272R4", "ceiling_f": 180, "effective_start": "2025-02-19",
         "effective_end": "2025-12-31"}]}}
    assert ea.hov_ceiling_for("AHW272R4", "8/29/2023", cfg) == 180
    assert ea.hov_ceiling_for("AHW272R4", "2026-03-01", cfg) is None


@pytest.mark.parametrize("value", [154.4, 185.5])
def test_urgent_fires_regardless_of_hov_ceiling_label(value):
    parsed = _doc_m([_temp(value, well="AHW272R4", date="2025-03-01")])
    with_roster = ea.is_urgent(parsed, HOV_CFG)
    without = ea.is_urgent(parsed, CFG)
    assert with_roster is True and without is True
    assert bool(ea.urgency_reason(parsed, HOV_CFG)) is True
    body = ea.format_urgent_body(parsed, {}, "L", HOV_CFG)
    rel = "below ceiling" if value < 180 else "above ceiling"
    assert f"HOV approved ceiling for AHW272R4: 180°F. Measured: {value}°F ({rel})." in body


def test_hov_label_never_makes_a_below_threshold_reading_fire():
    parsed = _doc_m([_temp(140, well="AHW272R4", date="2025-03-01")])
    assert ea.is_urgent(parsed, HOV_CFG) is False


def test_not_in_roster_label():
    parsed = _doc_m([_temp(158.3, well="AHWW312R")])
    body = ea.format_urgent_body(parsed, {}, "L", HOV_CFG)
    assert "Approved ceiling for AHWW312R not in this monitor's roster." in body
    assert "No approved ceiling" not in body


def test_format_urgent_body_without_cfg_still_works():
    # Old two-arg-plus-link callers keep working (defaults, no HOV lines).
    body = ea.format_urgent_body(_doc_m([_temp(150)]), {"document_name": "D"}, "L")
    assert "Urgent because:" in body and "Severity (as recorded): routine" in body


def test_config_roster_agrees_with_top_hov_wells():
    # kdp_check's top_hov_wells (the only well allowed 180F) and the new
    # roster must not disagree about who holds the top value.
    from config_loader import load_config
    tt = load_config()["temperature_thresholds"]
    top = max(tt["hov_values_f"])
    holders = {e["well_id"] for e in tt["hov_ceilings"] if e["ceiling_f"] == top}
    assert holders == set(tt["top_hov_wells"])


def test_config_roster_matches_the_egle_letters():
    # The filled roster (2026-10-17), pinned against the HOV letters: the
    # 4/7/2026 16-well renewal values, the superseded 2024 wells ending on that
    # grant's 5/1/2026 term, and AHW263R5 ending on its last confirmed date.
    from config_loader import load_config
    cfg = load_config()
    renewal = {"AHWW258R": 150, "AHW259R5": 170, "AHW272R4": 180, "AHWW0279": 160,
               "AHW285R3": 170, "AHWW287R": 150, "AHWW290R": 170, "AHWW0300": 160,
               "AHWW301R": 160, "AHWW302R": 160, "AHW311R2": 150, "AHW312R2": 160,
               "AHWW0313": 160, "AHWW0315": 150, "AHWWTS01": 150, "AHWTS01R": 150}
    for well, ceiling in renewal.items():
        assert ea.hov_ceiling_for(well, "2027-06-01", cfg) == ceiling, well
        assert ea.hov_ceiling_for(well, "2028-05-02", cfg) is None, well
    for well in ("AHW259R4", "AHW285R2", "AHWW311R", "AHWW312R", "AHW263R5"):
        assert ea.hov_ceiling_for(well, "2026-05-02", cfg) is None, well
    assert ea.hov_ceiling_for("AHW263R5", "2025-08-01", cfg) == 170
    assert ea.hov_ceiling_for("AHW263R5", "2025-07-22", cfg) is None
    assert ea.hov_ceiling_for("AHWW312R", "2024-06-01", cfg) == 160
    assert ea.hov_ceiling_for("AHW312R2", "2024-06-01", cfg) is None  # before its 7/10/2024 grant
    assert ea.hov_ceiling_for("AHWW0279", "2024-04-29", cfg) is None  # 2019-2023 letters not on file
    ids = [e["well_id"] for e in cfg["temperature_thresholds"]["hov_ceilings"]]
    assert len(ids) == len(set(ids)) == 21
