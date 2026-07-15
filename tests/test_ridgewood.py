"""
Hermetic tests for Stream G (Ridge Wood Elementary H2S). No network, no creds, no
committed data — the report-list HTML is a small synthetic string and every report
PDF is built in-process with PyMuPDF (data-guard CI forbids committed *.pdf/*.html).
Covers the pure client (scrape / month-parse / fail-safe classifier) and the full
archiver run() flows (disabled no-op / backfill-suppresses-alerts / incremental
all-clear / incremental exceedance / dedup / Drive-optional / no-text-layer) driven
through a fake Sheets service.
"""
import copy
import re

import fitz

import ridgewood_client as rc
import ridgewood_archiver as ra
import sheet_writer as sw


# --- synthetic report PDF builder ---------------------------------------------

_FOOTNOTES = (
    "1 Notifications to be sent to the Ridge Wood Elementary School if a 15-minute "
    "average concentration exceeds 750 ppb (0.75 ppm, USEPA Acute Exposure Guideline "
    "Value) or a 24-hour average concentration exceeds 72 ppb (0.072 ppm).\n"
    "2 The 24-hour guideline value from the Michigan EGLE is 72 ppb (~100 microgram "
    "per cubic meter of air).\n"
)
_ALL_CLEAR = ("a. No notifications required to be sent to Ridge Wood Elementary for "
              "elevated H2S.\n")


def report_text(month, values, *, all_clear=True, footnotes=True, footnotes_before_table=False):
    """The extracted-text shape of one monthly report: a header, the section-7
    narrative (with or without the all-clear line), a Day/value table (one token per
    line, as fitz extracts real report cells), and the action-level footnotes (the
    landmine that must NOT trip the classifier). `footnotes_before_table` reproduces
    the REAL two-page layout — the footnotes end page 1 and the table is on page 2,
    so the footnotes precede the table in extracted text (verified in the spike)."""
    y, m = month.split("-")
    narrative = [
        "Arbor Hills Landfill, Inc.",
        "Ridge Wood Elementary Hydrogen Sulfide (H2S) Monitoring: 24-hour average concentrations",
        f"{rc._MONTH_NAMES[int(m)]} {y}",
        "6) Data - Average Air Concentrations",
        "a. Average concentrations are calculated from 10-second readings.",
        "b. The SPM Flex output range is 0.000 to 9.999 parts per million (0 to 9,999 parts per billion, ppb).",
        "7) Data Report (24-hour Average Concentrations)",
    ]
    if all_clear:
        narrative.append(_ALL_CLEAR.strip())
    narrative.append("b. All 24-hour average concentrations are low.")
    table = ["Day", "24-hr Average H2S Concentration", "(parts per billion; ppb)", "Comment"]
    for i, v in enumerate(values, start=1):
        table.append(f"{int(m)}/{i}/{y[2:]}")
        table.append(str(v))
    foot = [_FOOTNOTES.strip()] if footnotes else []
    ordered = narrative + (foot + table if footnotes_before_table else table + foot)
    return "\n".join(ordered)


def make_report_pdf(path, month, values, *, all_clear=True, footnotes=True, text=True):
    """Write a synthetic monthly report PDF. text=False makes an image-only (scanned)
    PDF with no text layer, to exercise the OCR/no-text fail-safe branch. The text
    branch paginates one line per insert_text call (so each date/value lands on its
    own extractable line, like the real report's table cells) and spills to a new
    page past the bottom margin — a full month's table spans ~2 pages, as it does live."""
    doc = fitz.open()
    if text:
        lines = report_text(month, values, all_clear=all_clear, footnotes=footnotes).split("\n")
        page = doc.new_page()
        y, top, bottom, line_h = 50.0, 50.0, 760.0, 11.0
        for ln in lines:
            if y > bottom:
                page = doc.new_page()
                y = top
            page.insert_text((40, y), ln, fontsize=8)
            y += line_h
    else:
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200))
        pix.clear_with(220)
        page.insert_image(fitz.Rect(20, 20, 180, 180), pixmap=pix)
    doc.save(path)
    doc.close()
    return path


# --- synthetic report-list page -----------------------------------------------

def page_html(months_with_suffix, *, include_old_format_dup=True, extra_nonpdf=True):
    """A minimal report-list page: one Files/*.pdf link per (month, cache_buster),
    optionally the old-format Dec-2020 duplicate (no YYYY-MM prefix) and some
    non-PDF chrome links that must be ignored."""
    links = []
    if extra_nonpdf:
        links.append('<a href="/Home/Ridgewood">Home</a>')
        links.append('<link href="/theme/site.css?rev=99" rel="stylesheet">')
    for month, suffix in months_with_suffix:
        suf = f"_{suffix}" if suffix else ""
        links.append(
            f'<a href="/Files/{month}_Arbor Hills H2S Data_24-hour ave{suf}.pdf">{month}</a>')
    if include_old_format_dup:
        links.append('<a href="/GFLEnvironmental/Files/Report_Arbor%20Hills%20'
                     'Landfill%20202012%20H2S%20Data_24-hour%20ave%20concentrations.pdf">old</a>')
    # pad so fetch_page's min-body guard would pass on the real fetch path
    pad = "<!-- " + "x" * 2100 + " -->"
    return "<html><body>" + "\n".join(links) + pad + "</body></html>"


# ==============================================================================
# Pure client: link scrape + month parse
# ==============================================================================

def test_scrape_parses_months_dedups_and_flags_old_format():
    html = page_html([("2026-05", None), ("2026-04", "3486"), ("2026-04", "9999")])
    reports, unparsed = rc.scrape_report_links(html)
    months = [r["month"] for r in reports]
    assert months == ["2026-04", "2026-05"]        # sorted asc, 2026-04 deduped to one
    assert len(unparsed) == 1                       # the old-format Dec-2020 dup
    assert "202012" in unparsed[0]


def test_scrape_absolutizes_and_keeps_first_link_per_month():
    html = page_html([("2026-04", "3486"), ("2026-04", "0000")], include_old_format_dup=False)
    reports, _ = rc.scrape_report_links(html)
    assert len(reports) == 1
    assert reports[0]["url"].startswith("https://www.arborhillsmonitoring.com/Files/")
    assert "3486" in reports[0]["url"]              # first link for the month wins


def test_parse_month_and_none_for_old_format():
    assert rc.parse_month("2026-05_Arbor Hills H2S Data_24-hour ave.pdf") == "2026-05"
    assert rc.parse_month("2020-12_Arbor Hills H2S Data_24-hour ave_5697.pdf") == "2020-12"
    assert rc.parse_month("Report_Arbor Hills Landfill 202012 H2S Data.pdf") is None


def test_iter_new_reports_newest_first_and_dedup():
    reports = [{"month": m, "url": f"u/{m}"} for m in ["2026-01", "2026-02", "2026-03"]]
    new = rc.iter_new_reports(reports, {"2026-02"})
    assert [r["month"] for r in new] == ["2026-03", "2026-01"]   # newest first, 02 excluded


def test_report_title_derived_from_month():
    assert rc.report_title("2026-05") == "Ridge Wood Elementary H2S 24-hr averages — May 2026"


# ==============================================================================
# Pure client: PDF extraction + fail-safe classifier
# ==============================================================================

def test_extract_text_detects_text_layer(tmp_path):
    p = make_report_pdf(str(tmp_path / "r.pdf"), "2026-05", ["<1"] * 31)
    text, npages, has_text = rc.extract_text(p)
    assert has_text is True and npages >= 1
    assert "Ridge Wood Elementary" in text


def test_extract_text_flags_scanned_pdf_as_no_text(tmp_path):
    p = make_report_pdf(str(tmp_path / "s.pdf"), "2026-05", [], text=False)
    _, _, has_text = rc.extract_text(p)
    assert has_text is False


def test_content_hash_stable_and_bytes_or_path(tmp_path):
    p = make_report_pdf(str(tmp_path / "h.pdf"), "2026-05", ["<1"] * 3)
    with open(p, "rb") as fh:
        data = fh.read()
    assert rc.content_hash(p) == rc.content_hash(data)
    assert len(rc.content_hash(p)) == 64            # sha-256 hex


def test_parse_daily_values_scopes_to_table_only(tmp_path):
    # Footnote/narrative numbers (9,999 / 72 / 750 / 0.000) must NOT be captured.
    p = make_report_pdf(str(tmp_path / "t.pdf"), "2026-05", ["<1", "<1", "2"])
    text, _, _ = rc.extract_text(p)
    days = rc.parse_daily_values(text)
    tokens = sorted({v for _, v in days if v})
    assert tokens == ["2", "<1"]
    assert len(days) == 3


THR = {"h2s_24h_ppb": 72, "h2s_15min_ppb": 750}


def test_classify_all_clear_is_routine_despite_footnote_landmine():
    v = rc.classify_report(report_text("2026-05", ["<1"] * 31), THR)
    assert v["alert"] is False and v["severity"] == "routine"
    assert v["exceed_24h"] is False and v["all_clear"] is True
    assert v["value_token"] == "<1" and v["all_days_below_1"] is True


def test_classify_24h_exceedance_alerts_on_numeric_only():
    v = rc.classify_report(report_text("2026-06", ["<1", "85", "3"]), THR)
    assert v["alert"] is True and v["exceed_24h"] is True
    assert v["value_token"] == "85"
    assert any("85 ppb" in r for r in v["reasons"])


def test_classify_boundary_at_threshold_is_exceedance():
    assert rc.classify_report(report_text("2026-06", ["72"]), THR)["exceed_24h"] is True
    assert rc.classify_report(report_text("2026-06", ["71"]), THR)["exceed_24h"] is False


def test_classify_missing_all_clear_alerts_failsafe():
    v = rc.classify_report(report_text("2026-07", ["<1", "<1"], all_clear=False), THR)
    assert v["alert"] is True and v["all_clear"] is False
    assert v["exceed_24h"] is False                 # no numeric exceedance
    assert any("all-clear" in r for r in v["reasons"])


def test_classify_parse_anomaly_when_no_table():
    v = rc.classify_report("narrative only, no daily table.\n" + _FOOTNOTES, THR)
    assert v["alert"] is True and v["parse_anomaly"] is True
    assert v["value_token"] == ""


def test_classify_handles_real_layout_footnotes_before_table():
    # The live PDF orders narrative -> footnotes (end of page 1) -> table (page 2);
    # parse_daily_values runs on full text so ordering is immaterial, but pin it so a
    # future strip_footnotes refactor can't silently break real-layout parsing.
    clear = rc.classify_report(report_text("2026-05", ["<1"] * 31, footnotes_before_table=True), THR)
    assert clear["alert"] is False and clear["n_days"] == 31 and clear["all_clear"] is True
    exceed = rc.classify_report(
        report_text("2026-06", ["<1", "85"], footnotes_before_table=True), THR)
    assert exceed["exceed_24h"] is True and exceed["value_token"] == "85"


def test_strip_footnotes_removes_action_level_definitions():
    body = rc.strip_footnotes(report_text("2026-05", ["<1"]))
    assert "exceeds 750 ppb" not in body and "exceeds 72 ppb" not in body
    assert "No notifications required" in body       # section 7a survives the strip


# ==============================================================================
# Archiver run() flows — fake Sheets + monkeypatched network/drive/email
# ==============================================================================

def _tab(rng):
    return re.match(r"'([^']+)'", rng).group(1)


def _start_row(rng):
    m = re.search(r"![A-Z]+(\d+)", rng)
    return int(m.group(1)) if m else 1


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs

    def get(self, spreadsheetId, range):
        rows = self._tabs.get(_tab(range))
        if rows is None:
            return _Req({"values": []})
        return _Req({"values": [list(r) for r in rows[_start_row(range) - 1:]]})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._tabs.setdefault(_tab(range), []).extend(list(r) for r in body["values"])
        return _Req({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        rows = self._tabs.setdefault(_tab(range), [])
        start = _start_row(range) - 1
        for i, row in enumerate(body["values"]):
            idx = start + i
            while len(rows) <= idx:
                rows.append([])
            rows[idx] = list(row)
        return _Req({})


class FakeSheets:
    def __init__(self):
        self._values = _Values({})

    def spreadsheets(self):
        return self

    def get(self, spreadsheetId):
        return _Req({"sheets": [{"properties": {"title": t}} for t in self._values._tabs]})

    def batchUpdate(self, spreadsheetId, body):
        for req in body["requests"]:
            self._values._tabs.setdefault(req["addSheet"]["properties"]["title"], [])
        return _Req({})

    def values(self):
        return self._values


def _cfg(**over):
    rw = {
        "enabled": True,
        "page_url": "http://page",
        "base_url": "https://www.arborhillsmonitoring.com",
        "thresholds": copy.deepcopy(THR),
        "max_new_reports_per_run": 12,
        "review_recipients": ["trisha@example.test"],
    }
    rw.update(over)
    return {"ridgewood": rw, "alert_recipients": ["a@example.com"]}


def _wire(monkeypatch, cfg, *, months, specs, drive=True):
    """Wire the archiver against a fake Sheets service + synthetic page/PDFs.
    `months` -> the (month, suffix) list on the page; `specs[month]` -> the kwargs
    make_report_pdf uses for that month's PDF."""
    fake = FakeSheets()
    sent = []
    uploads = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(ra, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(ra.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(ra.rc, "fetch_page", lambda url=None, timeout=60: page_html(months))
    monkeypatch.setattr(ra.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def fake_download(url, dest, timeout=60):
        month = rc.parse_month(url.split("/Files/", 1)[-1])
        make_report_pdf(dest, month, **specs[month])
        return dest
    monkeypatch.setattr(ra.rc, "download_report", fake_download)

    monkeypatch.setattr(ra.ac, "is_configured", lambda env=ra.FOLDER_ENV: drive)
    monkeypatch.setattr(ra.ac, "folder_id", lambda env=ra.FOLDER_ENV: "FID")
    if drive:
        monkeypatch.setattr(ra.ac, "oauth_drive_service", lambda: object())

        def fake_upload(service, local, name, folder):
            uploads.append(name)
            return f"http://drive/{name}"
        monkeypatch.setattr(ra.ac, "upload_pdf", fake_upload)
    return fake, sent, uploads


def _rows(fake, tab):
    return fake._values._tabs.get(tab, [])[1:]      # drop the header row


def _spec_allclear(vals=("<1",) * 30):
    return {"values": list(vals), "all_clear": True}


# --- gate ----------------------------------------------------------------------

def test_should_run_false_when_disabled():
    assert ra._should_run({"ridgewood": {"enabled": False}})[0] is False


def test_should_run_false_when_key_absent():
    assert ra._should_run({})[0] is False


def test_should_run_true_when_enabled():
    assert ra._should_run({"ridgewood": {"enabled": True}})[0] is True


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    # No network/Sheets wiring at all: if run() tried to touch them it would crash.
    monkeypatch.setattr(ra, "load_config", lambda: {"ridgewood": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(ra.dc, "sheets_service", boom)
    monkeypatch.setattr(ra.rc, "fetch_page", boom)
    assert ra.run() == 0


# --- incremental steady state --------------------------------------------------

def test_incremental_single_allclear_writes_rows_no_alert(monkeypatch):
    cfg = _cfg()
    fake, sent, uploads = _wire(
        monkeypatch, cfg, months=[("2026-05", None)],
        specs={"2026-05": _spec_allclear(("<1",) * 31)})
    assert ra.run() == 0
    reports = _rows(fake, sw.TAB_RIDGEWOOD)
    meas = _rows(fake, sw.TAB_MEASUREMENTS)
    assert len(reports) == 1 and reports[0][0] == "2026-05"
    assert reports[0][4] == "ok"                    # Alert column
    assert reports[0][2] == "<1"                    # Max 24-hr Avg
    assert reports[0][7] == "http://drive/ridgewood-h2s-2026-05.pdf"   # Archive Link
    assert len(meas) == 1
    assert meas[0][2] == "hydrogen_sulfide" and meas[0][4] == "ppb" and meas[0][5] == "measured"
    assert sent == []                               # all-clear -> no email
    assert uploads == ["ridgewood-h2s-2026-05.pdf"]


def test_incremental_exceedance_writes_rows_and_emails(monkeypatch):
    cfg = _cfg()
    fake, sent, uploads = _wire(
        monkeypatch, cfg, months=[("2026-06", None)],
        specs={"2026-06": {"values": ["<1", "85", "3"], "all_clear": True}})
    assert ra.run() == 0
    reports = _rows(fake, sw.TAB_RIDGEWOOD)
    assert reports[0][4] == "EXCEEDANCE" and reports[0][2] == "85"
    assert _rows(fake, sw.TAB_MEASUREMENTS)[0][3] == "85"     # Measurements Value
    assert len(sent) == 1 and "URGENT" in sent[0][0]
    assert sent[0][2] is None                                # exceedance -> full alert_recipients list


def test_missing_all_clear_emails_review_not_urgent(monkeypatch):
    cfg = _cfg()
    fake, sent, _ = _wire(
        monkeypatch, cfg, months=[("2026-07", None)],
        specs={"2026-07": {"values": ["<1", "<1"], "all_clear": False}})
    assert ra.run() == 0
    assert _rows(fake, sw.TAB_RIDGEWOOD)[0][4] == "review"
    assert len(sent) == 1 and "review" in sent[0][0] and "URGENT" not in sent[0][0]
    assert sent[0][2] == ["trisha@example.test"]             # review-tier -> Trisha only, not the full list


def test_dedup_skips_already_archived_month(monkeypatch):
    cfg = _cfg()
    fake, sent, _ = _wire(
        monkeypatch, cfg, months=[("2026-05", None)],
        specs={"2026-05": _spec_allclear(("<1",) * 31)})
    ra.run()
    assert len(_rows(fake, sw.TAB_RIDGEWOOD)) == 1
    ra.run()                                         # second run: nothing new
    assert len(_rows(fake, sw.TAB_RIDGEWOOD)) == 1   # not re-appended


# --- backfill suppresses alerts ------------------------------------------------

def test_backfill_over_cap_suppresses_alerts_but_records(monkeypatch):
    cfg = _cfg(max_new_reports_per_run=2)
    months = [("2026-05", None), ("2026-06", None), ("2026-07", None)]
    specs = {
        "2026-05": _spec_allclear(("<1",) * 31),
        "2026-06": {"values": ["<1", "90"], "all_clear": True},   # an exceedance...
        "2026-07": _spec_allclear(("<1",) * 30),
    }
    fake, sent, _ = _wire(monkeypatch, cfg, months=months, specs=specs)
    assert ra.run() == 0
    # 3 new > cap 2 -> backlog: process 2 (newest first: 07, 06), alerts SUPPRESSED
    processed = _rows(fake, sw.TAB_RIDGEWOOD)
    assert {r[0] for r in processed} == {"2026-07", "2026-06"}
    assert sent == []                                # exceedance recorded, but no email in a backfill
    assert any(r[4] == "EXCEEDANCE" for r in processed)   # still recorded honestly


# --- Drive-optional + no-text-layer fail-safes ---------------------------------

def test_drive_not_configured_still_extracts_and_alerts(monkeypatch):
    cfg = _cfg()
    fake, sent, uploads = _wire(
        monkeypatch, cfg, months=[("2026-06", None)],
        specs={"2026-06": {"values": ["85"], "all_clear": True}}, drive=False)
    assert ra.run() == 0
    reports = _rows(fake, sw.TAB_RIDGEWOOD)
    assert reports[0][7] == ""                       # Archive Link blank (no mirror)
    assert len(_rows(fake, sw.TAB_MEASUREMENTS)) == 1  # extraction still happened
    assert len(sent) == 1                            # alert still fired


def test_transient_drive_upload_failure_still_measures_and_alerts(monkeypatch):
    # A single upload throwing (healthy token, network blip) must NOT block the
    # month's measurement + exceedance alert — the mirror is best-effort per file.
    cfg = _cfg()
    fake, sent, _ = _wire(
        monkeypatch, cfg, months=[("2026-06", None)],
        specs={"2026-06": {"values": ["90"], "all_clear": True}}, drive=True)

    def boom(service, local, name, folder):
        raise RuntimeError("transient ArcGIS/Drive 503")
    monkeypatch.setattr(ra.ac, "upload_pdf", boom)

    assert ra.run() == 0
    reports = _rows(fake, sw.TAB_RIDGEWOOD)
    assert reports[0][0] == "2026-06" and reports[0][7] == ""   # recorded, blank Archive Link
    assert reports[0][4] == "EXCEEDANCE"
    assert len(_rows(fake, sw.TAB_MEASUREMENTS)) == 1           # measurement still written
    assert len(sent) == 1                                       # alert still fired despite mirror failure


def test_no_text_layer_mirrors_and_alerts_without_measurement(monkeypatch):
    cfg = _cfg()
    fake, sent, uploads = _wire(
        monkeypatch, cfg, months=[("2026-08", None)],
        specs={"2026-08": {"values": [], "text": False}})
    assert ra.run() == 0
    reports = _rows(fake, sw.TAB_RIDGEWOOD)
    assert reports[0][4] == "review"                 # flagged for OCR/manual review
    assert reports[0][7].startswith("http://drive/") # still mirrored
    assert _rows(fake, sw.TAB_MEASUREMENTS) == []     # no fabricated measurement
    assert len(sent) == 1 and sent[0][2] == ["trisha@example.test"]   # review-tier -> Trisha only


def test_review_recipients_unset_falls_back_to_full_list(monkeypatch):
    # With review_recipients unset, a review-tier alert falls back to the full list.
    cfg = _cfg(review_recipients=None)
    fake, sent, _ = _wire(
        monkeypatch, cfg, months=[("2026-07", None)],
        specs={"2026-07": {"values": ["<1", "<1"], "all_clear": False}})
    assert ra.run() == 0
    assert len(sent) == 1 and sent[0][2] is None      # None -> send_email uses alert_recipients
