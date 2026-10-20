"""woi_router: content-based WOI detection, exhaustive-measurement replacement
(with the data-loss guard + peak-temp preservation), and is_urgent through the
wired path. Hermetic — synthetic PDFs built in-process, no network, no secrets."""
from types import SimpleNamespace

import fitz

import email_alerts as ea
import woi_router


CFG = {
    "woi": {"auto_route": True},
    "large_doc_page_threshold": 30,
    "urgent": {"severity_is_urgent": True, "measured_temp_urgent_f": 145,
               "measured_temp_watch_f": 131},
}
META = {"document_name": "nForm Document", "type_name": "nForm Document",
        "date_filed": "2025-07-23", "facility_name": "Arbor Hills Landfill"}


# --- synthetic WOI PDF -------------------------------------------------------
# The real reports linearize as: Well ID / Date+Time / [ADJ] / 9 gas columns.
# CH4 8.1 + CO2 40 + O2 7.0 + Balance 44.9 = 100.0 -> passes the sum-to-100 gate;
# the temp is the 6th column. Built with the same in-process-PyMuPDF approach as
# tests/conftest.py so no PDF is ever committed.

def _reading_lines(well, temp, adj=False, dt="3/14/2025 15:40"):
    lines = [well, dt]
    if adj:
        lines.append("ADJ")
    lines += ["8.1", "40", "7.0", "44.9", "0.5", str(temp), "10", "-5", "-40"]
    return lines


# The table's own column header (one line, so each label word sits at a
# rising x). woi_table_parser reads the column order from it (layout guard).
HEADER_FULL = ("Well ID Date/Time ADJ CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. "
               "Temp. Flow Well Pres. Header Pres. Comments")


def _build_pdf(path, readings, header=True, total_pages=45, per_page=5, co=None,
               header_line=HEADER_FULL):
    """readings: list of (well, temp) or (well, temp, adj). Places the
    'Gas Extraction Report' header on p8 and the gas rows across interior pages
    (small font so many rows fit); pads to total_pages of filler. `co`, if given,
    is a list of (well, date, ppm) written as an Attachment-2 CO page on the last page."""
    norm = [(r[0], r[1], r[2] if len(r) > 2 else False) for r in readings]
    chunks = [norm[i:i + per_page] for i in range(0, len(norm), per_page)]
    doc = fitz.open()
    for p in range(total_pages):
        page = doc.new_page()
        if header and p == 7:
            page.insert_text((20, 20), "Attachment 1\nGas Extraction Report\n" + header_line,
                             fontsize=6)
        elif co and p == total_pages - 1:  # Attachment 2 follows Attachment 1
            lines = ["Attachment 2 - June 2025 CO Data", "Well ID", "Date", "ppm"]
            for well, date, ppm in co:
                lines += [well, date, str(ppm)]
            page.insert_text((20, 20), "\n".join(lines), fontsize=6)
        elif 10 <= p < 10 + len(chunks):
            block = []
            for well, temp, adj in chunks[p - 10]:
                block += _reading_lines(well, temp, adj)
            page.insert_text((20, 20), "\n".join(block), fontsize=5)
        else:
            page.insert_text((20, 20), f"Filler page {p} — narrative text.", fontsize=8)
    doc.save(str(path))
    doc.close()
    return str(path)


def _many(n, temp=90, start=200):
    return [(f"AHW{start + i}", temp) for i in range(n)]


def _parsed(measurements=None, severity="notable", full_text=""):
    return SimpleNamespace(measurements=measurements if measurements is not None else [],
                           severity=severity, key_data_point="", summary="",
                           full_text=full_text)


# --- detector ----------------------------------------------------------------

def test_is_woi_report_true_for_header_and_large(tmp_path):
    pdf = _build_pdf(tmp_path / "woi.pdf", _many(3), header=True, total_pages=35)
    assert woi_router.is_woi_report(pdf, META, page_threshold=30) is True


def test_is_woi_report_false_without_header(tmp_path):
    pdf = _build_pdf(tmp_path / "no_hdr.pdf", _many(3), header=False, total_pages=35)
    assert woi_router.is_woi_report(pdf, META, page_threshold=30) is False


def test_is_woi_report_false_when_under_page_threshold(tmp_path):
    # Header present but only 20 pp — a small doc that merely mentions the phrase.
    pdf = _build_pdf(tmp_path / "small.pdf", _many(3), header=True, total_pages=20)
    assert woi_router.is_woi_report(pdf, META, page_threshold=30) is False


# --- route_measurements: replacement + guards --------------------------------

def test_route_replaces_measurements_on_hot_report(tmp_path):
    # 54 cool wells + one 177F well; >= _MIN_VALID_READINGS so the guard passes.
    reads = _many(54, temp=90) + [("AHW272R4", 177)]
    pdf = _build_pdf(tmp_path / "hot.pdf", reads)
    parsed = _parsed(measurements=[{"metric": "temperature", "value": 999,
                                    "unit": "F", "basis": "measured"}])
    routed = woi_router.route_measurements(parsed, pdf, META, CFG)
    assert routed is not None
    assert routed["n_valid"] >= 55 and routed["n_readings"] >= 55
    temps = [m["value"] for m in parsed.measurements if m["metric"] == "temperature"]
    assert 177 in temps                 # exhaustive set is in
    assert 999 not in temps             # the windowed/generic measurement is gone
    assert 90 not in temps              # only >= watch-band readings emitted


def test_route_skips_when_auto_route_off(tmp_path):
    reads = _many(54, temp=90) + [("AHW272R4", 177)]
    pdf = _build_pdf(tmp_path / "hot.pdf", reads)
    cfg = {**CFG, "woi": {"auto_route": False}}
    orig = [{"metric": "temperature", "value": 5, "unit": "F", "basis": "measured"}]
    parsed = _parsed(measurements=list(orig))
    assert woi_router.route_measurements(parsed, pdf, META, cfg) is None
    assert parsed.measurements == orig                 # untouched


def test_route_skips_non_woi_doc(tmp_path):
    # Large, plenty of gas rows, but NO 'Gas Extraction Report' header.
    pdf = _build_pdf(tmp_path / "plain.pdf", _many(60, temp=90), header=False)
    orig = [{"metric": "oxygen", "value": 3, "unit": "percent", "basis": "measured"}]
    parsed = _parsed(measurements=list(orig))
    assert woi_router.route_measurements(parsed, pdf, META, CFG) is None
    assert parsed.measurements == orig


def test_route_guard_keeps_generic_when_few_valid(tmp_path):
    # Header + large, but only a handful of readings (< _MIN_VALID_READINGS): a
    # false-positive / format-drift signal -> do NOT replace (no data loss).
    pdf = _build_pdf(tmp_path / "sparse.pdf", _many(5, temp=160))
    orig = [{"metric": "temperature", "value": 42, "unit": "F", "basis": "measured"}]
    parsed = _parsed(measurements=list(orig))
    assert woi_router.route_measurements(parsed, pdf, META, CFG) is None
    assert parsed.measurements == orig


def test_route_includes_co_measurements(tmp_path):
    # CO (Attachment 2) is routed too — kept regardless of temperature.
    reads = _many(54, temp=90) + [("AHW272R4", 177)]
    co = [("AHWW0279", "6/24/2025", "110"), ("AHW272R4", "6/23/2025", "70")]
    pdf = _build_pdf(tmp_path / "woi_co.pdf", reads, co=co)
    parsed = _parsed()
    routed = woi_router.route_measurements(parsed, pdf, META, CFG)
    assert routed is not None and routed["n_co"] == 2
    co_ms = [m for m in parsed.measurements if m["metric"] == "carbon_monoxide"]
    assert {m["value"] for m in co_ms} == {110.0, 70.0}
    assert all(m["basis"] == "measured" for m in co_ms)


def test_route_labels_methane_not_other(tmp_path):
    # CH4 is emitted as metric='methane' (first-class), never 'other'.
    reads = _many(54, temp=90) + [("AHW272R4", 177)]
    pdf = _build_pdf(tmp_path / "woi.pdf", reads)
    parsed = _parsed()
    woi_router.route_measurements(parsed, pdf, META, CFG)
    metrics = {m["metric"] for m in parsed.measurements}
    assert "methane" in metrics and "other" not in metrics


# --- is_urgent through the wired path ----------------------------------------

def test_is_urgent_true_through_wired_path(tmp_path):
    reads = _many(54, temp=90) + [("AHW272R4", 177)]
    pdf = _build_pdf(tmp_path / "hot.pdf", reads)
    parsed = _parsed(severity="notable", full_text="HOV permitted ceiling 180 F")
    woi_router.route_measurements(parsed, pdf, META, CFG)
    assert ea.is_urgent(parsed, CFG) is True           # 177 measured >= 145


def test_is_urgent_false_on_cool_report_with_ceiling_in_text(tmp_path):
    # ADVISOR #1 INVERSE (the regression this whole design guards against): no
    # reading >= 131, but full_text carries an 180F permitted ceiling. Peak-temp
    # preservation keeps >= 1 measured temperature, so is_urgent decides from
    # structured data (peak < 145 -> False) and NEVER regexes the ceiling out of
    # full_text (which would false-fire off a permitted limit — ADR 004).
    pdf = _build_pdf(tmp_path / "cool.pdf", _many(60, temp=120))
    parsed = _parsed(severity="notable",
                     full_text="Attachment: HOV permitted ceiling of 180 F (a limit, not a reading).")
    routed = woi_router.route_measurements(parsed, pdf, META, CFG)
    assert routed is not None                           # a cool report still routes
    assert parsed.measurements                          # peak preserved -> non-empty
    assert any(m["metric"] == "temperature" for m in parsed.measurements)
    assert ea.is_urgent(parsed, CFG) is False           # NOT a false-fire


def test_bug_would_false_fire_without_peak_preservation():
    """Documents WHY route_measurements preserves the peak temp: an empty
    measurement set + a permitted ceiling in full_text sends is_urgent to its
    free-text fallback and false-fires. Peak-preservation (the test above)
    prevents exactly this."""
    parsed = _parsed(measurements=[], severity="notable",
                     full_text="HOV permitted ceiling of 180 F")
    assert ea.is_urgent(parsed, CFG) is True


# --- coder:woi-layout-guard ---------------------------------------------------

def test_is_woi_report_finds_header_past_page_25(tmp_path):
    # The Q3 2023 Consent Judgment report carries its gas table from p163; the
    # old 25-page scan never saw it.
    doc = fitz.open()
    for p in range(45):
        page = doc.new_page()
        page.insert_text((20, 20), "Gas Extraction Report" if p == 40 else f"Narrative {p}",
                         fontsize=8)
    path = tmp_path / "late.pdf"
    doc.save(str(path))
    doc.close()
    assert woi_router.is_woi_report(str(path), META, page_threshold=30) is True


def test_reading_above_plausibility_ceiling_never_alerts_and_is_flagged(tmp_path, monkeypatch):
    # A valid row (gases sum to 100) whose temperature is 526.8: the shape a
    # shifted column produces. It must reach no alerting path, but be flagged.
    reads = _many(54, temp=90) + [("AHWW0402", 526.8), ("AHW272R4", 140)]
    pdf = _build_pdf(tmp_path / "ceil.pdf", reads)
    cfg = {**CFG, "temperature_thresholds": {"max_plausible_wellhead_f": 250}}
    parsed = _parsed(severity="notable")
    flags = []
    routed = woi_router.route_measurements(parsed, pdf, META, cfg, review_flags=flags)
    assert routed is not None
    temps = [m["value"] for m in parsed.measurements
             if m["metric"] == "temperature" and m["basis"] == "measured"]
    assert 526.8 not in temps and 140 in temps
    held = [m for m in parsed.measurements if m["value"] == 526.8]
    assert [(m["metric"], m["basis"]) for m in held] == [("temperature", "unknown")]
    assert "plausibility ceiling" in held[0]["note"]
    assert ea.is_urgent(parsed, cfg) is False                    # 140 < 145, 526.8 held out
    assert all(r.temp != 526.8 for r in routed["readings"])     # well_watch never sees it
    assert all(d["max_temp_f"] != 526.8 for d in routed["summary"])
    assert [f["kind"] for f in flags] == ["woi_temp_above_ceiling"]
    assert flags[0]["value_f"] == 526.8 and flags[0]["well_id"] == "AHWW0402"


def test_rejected_table_is_flagged_even_when_not_routed(tmp_path):
    # An unknown layout (no Temp column): nothing routed, the rejection is flagged.
    hdr = "Well ID Date/Time ADJ CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. Comments"
    pdf = _build_pdf(tmp_path / "unknown.pdf", _many(60, temp=90), header_line=hdr)
    parsed = _parsed()
    flags = []
    assert woi_router.route_measurements(parsed, pdf, META, CFG, review_flags=flags) is None
    assert [f["kind"] for f in flags] == ["rejected_gas_table"]
    assert "column layout not recognized" in flags[0]["detail"]


def test_route_keeps_classifier_measurements_it_does_not_rederive(tmp_path):
    reads = _many(54, temp=90) + [("AHW272R4", 177)]
    pdf = _build_pdf(tmp_path / "keep.pdf", reads)
    h2s = {"metric": "hydrogen_sulfide", "value": 12, "unit": "ppb", "basis": "measured"}
    parsed = _parsed(measurements=[h2s, {"metric": "temperature", "value": 999,
                                         "unit": "F", "basis": "measured"}])
    woi_router.route_measurements(parsed, pdf, META, CFG)
    assert h2s in parsed.measurements
    assert 999 not in [m["value"] for m in parsed.measurements if m["metric"] == "temperature"]


def test_gas_only_rows_route_without_changing_any_max_temperature(tmp_path):
    # ADR 005 addendum 2026-10-19: a row whose Temp prints "---" is kept as a
    # gas-only reading. It must not move any well's max temperature, the peak,
    # the emitted temperatures, the headline, the severity floor or well_watch.
    import well_watch
    base_reads = _many(54, temp=90) + [("AHW272R4", 177)]
    base = woi_router.route_measurements(_parsed(), _build_pdf(tmp_path / "base.pdf", base_reads),
                                         META, CFG)
    reads = base_reads + [("AHW272R4", "---"), ("AHW999", "---")]
    pdf = _build_pdf(tmp_path / "gas_only.pdf", reads)
    parsed = _parsed(severity="urgent")
    parsed.severity_floor, parsed.severity_before_floor = ["measured_temperature"], "notable"
    routed = woi_router.route_measurements(parsed, pdf, META, CFG)
    gas_only = [r for r in routed["readings"] if r.temp is None]
    assert {r.well_id for r in gas_only} == {"AHW272R4", "AHW999"}
    by_well = {d["well"]: d for d in routed["summary"]}
    base_max = {d["well"]: d["max_temp_f"] for d in base["summary"]}
    assert {w_: by_well[w_]["max_temp_f"] for w_ in base_max} == base_max
    assert by_well["AHW272R4"]["max_temp_f"] == 177.0
    assert by_well["AHW999"]["max_temp_f"] is None
    assert routed["summary"][0]["well"] == "AHW272R4"   # wells with no temp sort last
    temps = [m["value"] for m in parsed.measurements if m["metric"] == "temperature"]
    assert temps == [177.0]
    assert woi_router.build_key_data_point(routed, cfg=CFG).startswith("AHW272R4: max measured 177")
    woi_router.recheck_severity_floor(parsed, routed, CFG)
    assert parsed.severity == "urgent"                   # the real 177F reading survives
    cfg = {**CFG, "well_watch": {"wells": [{"pattern": r"^AHW+0*272", "threshold_f": 145}]}}
    (_, hits), = well_watch.find_hits(routed["readings"], cfg)
    assert [h.temp for h in hits] == [177.0]


def test_guard_ignores_gas_only_rows_when_temperatures_dont_parse(tmp_path):
    # A report whose Temp cells all fail to parse must not route on the
    # strength of its gas columns: the classifier's 177 stays (as on main).
    reads = _many(54, temp="177.0*") + [("AHW1", 90)]
    pdf = _build_pdf(tmp_path / "bad_temp.pdf", reads)
    parsed = _parsed(measurements=[{"metric": "temperature", "value": 177,
                                    "unit": "F", "basis": "measured"}])
    assert woi_router.route_measurements(parsed, pdf, META, CFG) is None
    assert [m["value"] for m in parsed.measurements] == [177]


def test_guard_counts_only_readings_with_a_temperature(tmp_path):
    reads = _many(10, temp=90) + _many(60, temp="---", start=400)
    pdf = _build_pdf(tmp_path / "mostly_gas_only.pdf", reads)
    assert woi_router.route_measurements(_parsed(), pdf, META, CFG) is None
