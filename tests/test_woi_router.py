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


def _build_pdf(path, readings, header=True, total_pages=45, per_page=5):
    """readings: list of (well, temp) or (well, temp, adj). Places the
    'Gas Extraction Report' header on p8 and the gas rows across interior pages
    (small font so many rows fit); pads to total_pages of filler."""
    norm = [(r[0], r[1], r[2] if len(r) > 2 else False) for r in readings]
    chunks = [norm[i:i + per_page] for i in range(0, len(norm), per_page)]
    doc = fitz.open()
    for p in range(total_pages):
        page = doc.new_page()
        if header and p == 7:
            page.insert_text((20, 20), "Attachment 1\nGas Extraction Report", fontsize=8)
        elif 9 <= p < 9 + len(chunks):
            block = []
            for well, temp, adj in chunks[p - 9]:
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
