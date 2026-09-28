"""govqa_client.py / govqa_watcher.py (Stream U, ADR 059) — the EGLE GovQA public
FOIA archive watch.

HTML fixtures are trimmed Python literals shaped like the REAL markup (captured
2026-09-28 from the live archive: grid rows with aria-labelled cells and
`redirectInfo('<rid>')`, the 'Page 1 of 10 (97 items)' pager text, the grid's
'No data to display' empty row, and a request detail page with `Reference No:` labels and
`rptAttachments$ctlNN$lnkStreamCloud` postback links) — never committed HTML/PDF/JSON
files (data-guard forbids them). The request texts are synthetic; real ones can name
residents and street addresses, which is exactly why the watcher's rows are private-Sheet-
only (pinned below).
"""
import ast
import copy
import hashlib
import logging
import os
import re
from pathlib import Path

import pytest

import govqa_client as gq
import govqa_watcher as gw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(gq, "MIN_INTERVAL", 0)


# ==============================================================================
# HTML fixtures (real structure, synthetic content)
# ==============================================================================


def grid_row(i, no, created, summary, status, rid):
    return (
        f'<tr id="gridView_DXDataRow{i}" class="dxgvDataRow_MaterialCompact">'
        f'<td class="dxgv dx-al" aria-label="Request Number: {no}">{no}</td>'
        f'<td class="grid-cell-word-wrap dxgv" aria-label="Create Date: {created}">{created}</td>'
        f'<td class="grid-cell-word-wrap dxgv" aria-label="Summary: {summary}">{summary}</td>'
        f'<td class="dxgv dx-al" aria-label="Request Status: {status}">{status}</td>'
        f'<td class="dxgv dx-ac"><a href="javascript:void(0);" aria-label="View Details" '
        f'onclick="redirectInfo(&#39;{rid}&#39;)"><i class="fa"></i></a></td></tr>'      # icons only, like the real cell
    )


GRID = (
    "<table>"
    + grid_row(0, "E615953-091526", "9/16/2026 1:00:00 AM", "Records for Arbor Hills Landfill &amp; the Six Mile Rd site",
               "GRANTED – Records", "714629")
    + grid_row(1, "E615662-090926", "9/9/2026 1:00:00 AM", "UST records within Ann Arbor Technology Park",
               "DENIED – No Records", "712001")
    + grid_row(2, "E613141-071526", "7/16/2026 1:00:00 AM", "Facility: Arbor Hills Landfill, Inc. WDS 475946",
               "PARTIAL", "697858")
    + '<tr class="dxgvHeader"><td>Request Number</td></tr>'
    + '<tr class="dxgvDataRow_x"><td>not-an-e-number</td><td>x</td></tr>'
    + "</table><b class=\"dxp-summary\">Page 1 of 10 (97 items)</b>"
)

EMPTY_GRID = ('<table><tr id="gridView_DXEmptyRow" class="dxgvEmptyDataRow_MaterialCompact"><td class="dxgv" colspan="5">'
              "<div> No data to display </div></td></tr></table>")

DETAIL = """
<form method="post" action="./RequestArchiveDetails.aspx?rid=703017&amp;view=1" id="qacRequestPublicSummary">
<input type="hidden" name="__VIEWSTATE" value="VS&amp;1" />
<input type="hidden" name="__EVENTTARGET" value="" />
<input type="submit" name="btnGo" value="Go" />
<input type="hidden" name="main-nav" value="x" />
<span style="font-weight: bold">Reference No:</span></div></div><div class="col"><p class="grid-cell-word-wrap">E614007-080526</p>
<span style="font-weight: bold">Create Date:</span></div></div><div class="col"><p class="grid-cell-word-wrap">8/6/2026 1:00:00 AM</p>
<span style="font-weight: bold">Public Record Desired:</span></div></div><div class="col"><p class="grid-cell-word-wrap">Dear FOIA
Coordinator: please provide all records for 10690 6 Mile Road.</p>
<span style="font-weight: bold">Close Date:</span></div></div><div class="col"><p class="grid-cell-word-wrap">8/20/2026 1:22:00 PM</p>
<span style="font-weight: bold">Address:</span></div></div><div class="col"><p class="grid-cell-word-wrap"></p>
<a onclick="return IsDownloadable(...)" id="rptAttachments_ctl00_lnkStreamCloud" class="qac_link"
   href="javascript:__doPostBack(&#39;rptAttachments$ctl00$lnkStreamCloud&#39;,&#39;&#39;)">10690_6_mile_2021.pdf</a>
<a id="rptAttachments_ctl01_lnkStreamCloud" class="qac_link"
   href="javascript:__doPostBack('rptAttachments$ctl01$lnkStreamCloud','')">Cost &amp; Estimate.docx</a>
</form>
"""

SUMMARY = ('<html><input type="hidden" name="__VIEWSTATE" value="VSTATE" />'
           '<input type="hidden" name="__EVENTTARGET" value="" />'
           '<input type="text" name="txtSearch" value="" /><input type="text" name="txtRefsearch" value="" />'
           '<input type="submit" name="filterButton" value="FILTER" />'
           '<input type="hidden" name="viewport" value="x" /></html>')


# ==============================================================================
# Client — pure parsing
# ==============================================================================


@pytest.mark.parametrize("raw", ["GRANTED – Records", "GRANTED - Records", "GRANTED � Records",
                                 "  GRANTED—Records ", "GRANTED&#8211; Records"])
def test_normalize_status_makes_every_dash_variant_identical(raw):
    assert gq.normalize_status(raw) == "GRANTED – Records"


def test_a_hyphen_inside_a_word_is_not_a_separator():
    assert gq.normalize_status("Well-known - thing") == "Well-known – thing"
    assert gq.normalize_status("CANCELLED - Duplicate") == "CANCELLED – Duplicate"


@pytest.mark.parametrize("status,terminal,released", [
    ("GRANTED – Records", True, True), ("GRANTED/DENIED – Exempt in Part", True, True),
    ("DENIED – No Records", True, False), ("CANCELLED – Duplicate", True, False), ("ABANDONED", True, False),
    ("PARTIAL", False, True), ("WAITING FOR PAYMENT", False, False), ("New Request", False, False),
    ("UTLR", False, False), ("Received", False, False), ("", False, False),
    ("SOME STATUS NOBODY HAS SEEN", False, False),          # unknown = OPEN (fail-safe)
])
def test_status_classification(status, terminal, released):
    assert gq.is_terminal(status) is terminal and gq.is_released(status) is released


def test_parse_rows_reads_real_grid_structure_by_aria_label():
    rows = gq.parse_rows(GRID)
    assert [r["request_no"] for r in rows] == ["E615953-091526", "E615662-090926", "E613141-071526"]
    first = rows[0]
    assert first["created"] == "9/16/2026 1:00:00 AM" and first["status"] == "GRANTED – Records"
    assert first["rid"] == "714629" and "Arbor Hills Landfill & the Six Mile" in first["summary"]   # entity unescaped
    assert gq.parse_rows("<table></table>") == []


def test_an_empty_cell_cannot_shift_the_other_columns():
    row = ('<tr class="dxgvDataRow_M"><td aria-label="Request Number: E615953-091526">E615953-091526</td>'
           '<td aria-label="Create Date: 9/16/2026">9/16/2026</td><td aria-label="Summary: "></td>'
           '<td aria-label="Request Status: GRANTED – Records">GRANTED – Records</td></tr>')
    r = gq.parse_rows("<table>" + row + "</table>")[0]
    assert r["summary"] == "" and r["status"] == "GRANTED – Records" and r["created"] == "9/16/2026"
    # a row with no aria-labels at all falls back to the non-empty cells, positionally
    plain = ('<tr class="dxgvDataRow_M"><td>E615953-091526</td><td>9/16/2026</td><td>Some text</td><td>PARTIAL</td></tr>')
    r2 = gq.parse_rows("<table>" + plain + "</table>")[0]
    assert (r2["created"], r2["summary"], r2["status"]) == ("9/16/2026", "Some text", "PARTIAL")


def test_parse_grid_state_only_believes_zero_rows_with_the_empty_marker():
    assert gq.parse_grid_state(GRID) == "rows" and gq.parse_grid_state(EMPTY_GRID) == "empty"
    assert gq.parse_grid_state("<html>Just a moment…</html>") == "unknown"
    assert gq.parse_grid_state("") == "unknown"


def test_parse_pager():
    assert gq.parse_pager(GRID) == (1, 10, 97)
    assert gq.parse_pager("no pager here") is None


def test_parse_detail_reads_fields_and_both_postback_quotings():
    d = gq.parse_detail(DETAIL)
    assert d["reference"] == "E614007-080526" and d["created"] == "8/6/2026 1:00:00 AM"
    assert d["closed"] == "8/20/2026 1:22:00 PM" and "10690 6 Mile Road" in d["description"]
    assert d["files"] == [
        {"target": "rptAttachments$ctl00$lnkStreamCloud", "name": "10690_6_mile_2021.pdf"},
        {"target": "rptAttachments$ctl01$lnkStreamCloud", "name": "Cost & Estimate.docx"},
    ]


def test_parse_detail_without_a_reference_is_structural():
    with pytest.raises(gq.GovqaStructuralError):
        gq.parse_detail("<html>Session Timeout — please log in</html>")


def test_hidden_inputs_drops_buttons_and_nav_and_unescapes():
    f = gq.hidden_inputs(DETAIL)
    assert f == {"__VIEWSTATE": "VS&1", "__EVENTTARGET": ""}
    assert "btnGo" in gq.hidden_inputs(DETAIL, keep_buttons=True)


def test_parse_gridview_csv_handles_bom_multiline_and_replacement_chars():
    text = ('Request Number,Create Date,Summary,Request Status\r\n'
            'E615953-091526,9/16/2026 1:00:00 AM,"Line one\r\n\r\nline two, with comma",GRANTED � Records\r\n'
            'not-a-number,x,y,z\r\nE615662-090926,9/9/2026,Short,DENIED - No Records\r\n')
    rows = gq.parse_gridview_csv(b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert [r["request_no"] for r in rows] == ["E615953-091526", "E615662-090926"]
    assert rows[0]["summary"] == "Line one line two, with comma" and rows[0]["status"] == "GRANTED – Records"
    assert rows[1]["status"] == "DENIED – No Records" and rows[0]["rid"] is None
    latin = "Request Number,Create Date,Summary,Request Status\nE615953-091526,d,caf\xe9,GRANTED – Records\n"
    row = gq.parse_gridview_csv(latin.encode("cp1252"))[0]                  # a cp1252 file: 0xE9 / 0x96 are not valid UTF-8
    assert row["summary"] == "caf\xe9" and row["status"] == "GRANTED – Records"


def test_phrase_in_is_case_and_whitespace_insensitive_and_whole_word():
    assert gq.phrase_in("Records for ARBOR   HILLS\nlandfill", "arbor hills")
    assert not gq.phrase_in("Ann Arbor Housing", "Arbor Hills")
    assert not gq.phrase_in("Ann Arbor Hillsdale", "Arbor Hills") and not gq.phrase_in("SRN 106900", "10690")
    assert gq.phrase_in("at 10690 6 Mile Rd, (Arbor Hills)", "10690") and gq.phrase_in("(Arbor Hills).", "arbor hills")
    assert not gq.phrase_in("anything", "")


def test_the_rid_is_read_only_from_the_details_link_never_from_request_text():
    """The Summary cell is text a third party wrote. `redirectInfo(999999)` in it must not steer which
    request's detail page is opened."""
    steered = grid_row(0, "E615953-091526", "9/16/2026", "please see redirectInfo(999999) and OnMoreInfoClick(this, 888888)",
                       "GRANTED – Records", "714629")
    assert gq.parse_rows("<table>" + steered + "</table>")[0]["rid"] == "714629"
    no_link = re.sub(r"<a .*?</a>", "", steered)
    assert gq.parse_rows("<table>" + no_link + "</table>")[0]["rid"] is None


def test_parse_grid_state_ignores_style_and_script_blocks():
    css = "<style>.dxgvEmptyDataRow_Material { color: red } /* No data to display */</style><script>var s='dxgvDataRow'</script>"
    assert gq.parse_grid_state(css + "<html>Just a moment…</html>") == "unknown"
    assert gq.parse_grid_state(css + EMPTY_GRID) == "empty" and gq.parse_grid_state(css + GRID) == "rows"


def test_read_grid_refuses_a_page_with_data_rows_that_cannot_be_read():
    """The likeliest markup-change failure: rows are present but no cell parses. It must NOT pass for
    'no requests' (which would baseline an empty term or report a request as not shown)."""
    weird = '<table><tr class="dxgvDataRow_x"><td>who knows</td><td>x</td></tr></table>'
    with pytest.raises(gq.GovqaFetchError, match="none could be read"):
        gq.read_grid(weird)
    with pytest.raises(gq.GovqaFetchError, match="did not render"):
        gq.read_grid("<html>Just a moment…</html>")
    assert gq.read_grid(EMPTY_GRID) == [] and len(gq.read_grid(GRID)) == 3
    http = FakeHTTP([summary_ok(), ("POST", "OpenRecordsSummary", Resp(200, weird))])
    with pytest.raises(gq.GovqaFetchError, match="none could be read"):
        gq.ArchiveSession(session=http).lookup("E615953-091526")


def test_sweep_marks_a_full_page_with_no_pager_and_refuses_an_impossible_one():
    ten = [R(f"E7000{i:02d}-010126") for i in range(10)]
    res = gq.sweep_term(FakeGrid({"t": pages(ten)}), "t", lambda n: False, max_pages=3)
    assert res.pager_missing and len(res.rows) == 10 and not res.overflow          # cannot tell whether older pages exist
    few = gq.sweep_term(FakeGrid({"t": pages(ten[:3])}), "t", lambda n: False, max_pages=3)
    assert not few.pager_missing
    with pytest.raises(gq.GovqaFetchError, match="no pager"):
        gq.sweep_term(FakeGrid({"t": pages(ten + [R("E700099-010126")])}), "t", lambda n: False, max_pages=3)


def test_with_backoff_stops_retrying_when_the_time_budget_is_spent_and_survives_a_dead_on_retry():
    slept, calls = [], []

    def fn():
        calls.append(1)
        raise gq.GovqaFetchError("timeout")
    with pytest.raises(gq.GovqaStructuralError, match="time budget"):
        gq.with_backoff(fn, "x", sleep=slept.append, should_stop=lambda: True)
    assert len(calls) == 1 and slept == []                                  # one attempt, no wait, no retry
    n = []

    def flaky():
        n.append(1)
        if len(n) < 2:
            raise gq.GovqaFetchError("timeout")
        return "ok"

    def dead_restart():
        raise gq.GovqaFetchError("browser is gone")
    assert gq.with_backoff(flaky, "x", sleep=lambda s: None, on_retry=dead_restart) == "ok"   # a failing restart never aborts


def test_details_on_a_reused_session_that_expired_resets_and_retries_once():
    shell = "<html>Session Timeout — please log in</html>"
    http = FakeHTTP([("GET", "RequestArchiveDetails", Resp(200, shell)),          # the reused session has expired
                     summary_ok(),                                                  # ...so a FRESH one is opened
                     ("GET", "(S(abc123))/RequestArchiveDetails", Resp(200, DETAIL))])
    s = gq.ArchiveSession(session=http)
    s._summary_page_url = SESSION_URL
    assert s.details("703017")["reference"] == "E614007-080526" and http.cookie_clears == 1


def test_details_on_a_fresh_session_that_still_answers_wrongly_is_a_structural_error():
    http = FakeHTTP([summary_ok(), ("GET", "RequestArchiveDetails", Resp(200, "<html>changed markup</html>"))])
    with pytest.raises(gq.GovqaStructuralError):
        gq.ArchiveSession(session=http).details("703017")
    assert len(http.calls) == 2                                                    # no second retry loop


def test_reset_forgets_the_session():
    http = FakeHTTP([])
    s = gq.ArchiveSession(session=http)
    s._summary_page_url, s._detail_html = SESSION_URL, "x"
    s.reset()
    assert s._summary_page_url == "" and s._detail_html == "" and http.cookie_clears == 1


def test_next_page_keeps_polling_through_a_transiently_unreadable_page():
    """Mid-navigation the DOM is briefly not a grid; that must not abort the page advance."""
    pageA = GRID
    pageB = GRID.replace("E615953-091526", "E615000-010126")
    seen = iter([pageA, "<html>loading…</html>", "<html>loading…</html>", pageB, pageB, pageB])

    class FakePage:
        def content(self):
            return self._cur

        def inner_text(self, sel):
            return "Page 2 of 10 (97 items)"

        def evaluate(self, js):
            self.js = js

        def wait_for_timeout(self, ms):
            self._cur = next(seen)

    fp = FakePage()
    fp._cur = next(seen)
    grid = gq.PlaywrightGrid()
    grid.page = fp
    rows, pager = grid.next_page()
    assert rows[0]["request_no"] == "E615000-010126" and "GVPagerOnClick" in fp.js


def test_parse_detail_refuses_a_page_whose_attachment_links_were_not_all_read():
    """The M-1 hole: if the link text is wrapped (an icon, a <span>) the name regex reads too few files,
    and a short list would be recorded as the whole release and closed for good."""
    icon = DETAIL.replace('>10690_6_mile_2021.pdf</a>', '><i class="fa fa-file"></i></a>')
    with pytest.raises(gq.GovqaStructuralError, match="attachment links"):
        gq.parse_detail(icon)
    none = re.sub(r"<a [^>]*rptAttachments.*?</a>", "", DETAIL, flags=re.S)
    assert gq.parse_detail(none)["files"] == []                              # a release with no attachments is still fine
    assert len(gq.parse_detail(DETAIL)["files"]) == 2


def test_parse_pager_takes_the_LAST_match_so_request_text_cannot_hide_pages():
    assert gq.parse_pager("row text: Page 1 of 1 (1 items) ... Page 1 of 10 (97 items)") == (1, 10, 97)
    assert gq.parse_pager("only Page 3 of 4 (37 items)") == (3, 4, 37) and gq.parse_pager("nothing") is None


def test_the_session_refuses_a_summary_or_details_response_that_ended_on_another_host():
    evil = "https://evil.example.com/WEBAPP/_rs/(S(x))/OpenRecordsSummary.aspx"
    with pytest.raises(gq.GovqaFetchError, match="non-allowlisted"):
        gq.ArchiveSession(session=FakeHTTP([("GET", "Open", Resp(200, SUMMARY, url=evil))])).lookup("E615953-091526")
    s = gq.ArchiveSession(session=FakeHTTP([("GET", "RequestArchiveDetails", Resp(200, DETAIL, url="https://evil.example.com/x"))]))
    s._summary_page_url = SESSION_URL
    with pytest.raises(gq.GovqaFetchError, match="non-allowlisted"):
        s.details("703017")


def test_details_with_expect_reference_refuses_and_never_remembers_a_mismatched_page(tmp_path):
    http = FakeHTTP([summary_ok(), ("GET", "RequestArchiveDetails", Resp(200, DETAIL))])
    s = gq.ArchiveSession(session=http)
    with pytest.raises(gq.GovqaStructuralError, match="different request"):
        s.details("703017", expect_reference="E999999-010126")
    assert s._detail_html == ""                                              # nothing remembered: a later download() cannot use it
    with pytest.raises(gq.GovqaFetchError, match="before details"):
        s.download("rptAttachments$ctl00$lnkStreamCloud", str(tmp_path / "f"), 100)
    ok = gq.ArchiveSession(session=FakeHTTP([summary_ok(), ("GET", "RequestArchiveDetails", Resp(200, DETAIL))]))
    assert ok.details("703017", expect_reference="E614007-080526")["reference"] == "E614007-080526"


# ==============================================================================
# Client — backoff + sweep
# ==============================================================================


def test_with_backoff_retries_fetch_errors_with_a_fresh_session_then_succeeds():
    calls, slept, restarts = [], [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise gq.GovqaFetchError("timeout")
        return "ok"
    assert gq.with_backoff(fn, "x", sleep=slept.append, on_retry=lambda: restarts.append(1)) == "ok"
    assert len(calls) == 3 and slept == [30, 120] and len(restarts) == 2


def test_with_backoff_gives_up_loudly_after_max_attempts_and_never_hangs():
    slept = []
    with pytest.raises(gq.GovqaStructuralError) as e:
        gq.with_backoff(lambda: (_ for _ in ()).throw(gq.GovqaFetchError("boom")), "sweep 'X'",
                        sleep=slept.append)
    assert "gave up after 3" in str(e.value) and slept == [30, 120]     # no wait after the final failure


def test_with_backoff_does_not_swallow_other_exceptions():
    with pytest.raises(KeyError):
        gq.with_backoff(lambda: {}["x"], "x", sleep=lambda s: None)


class FakeGrid:
    """A stand-in for PlaywrightGrid: term -> list of pages; each page is (rows, pager)."""

    def __init__(self, pages_by_term=None, fail_first=0):
        self.pages_by_term, self.fail_first = pages_by_term or {}, fail_first
        self.cursor, self.term, self.restarts, self.searches = 0, None, 0, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def restart(self):
        self.restarts += 1

    def search(self, term):
        self.searches.append(term)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise gq.GovqaFetchError("search timeout")
        self.term, self.cursor = term, 0
        pages = self.pages_by_term.get(term, [([], None)])
        return pages[0]

    def next_page(self):
        self.cursor += 1
        return self.pages_by_term[self.term][self.cursor]


def R(no, summary="Arbor Hills Landfill records", status="New Request", created="9/1/2026 1:00:00 AM", rid=None):
    return {"request_no": no, "created": created, "summary": summary, "status": status,
            "rid": rid or no[1:7]}


def pages(*page_rows, items=None):
    n = len(page_rows)
    total = items if items is not None else sum(len(p) for p in page_rows)
    return [(p, (i + 1, n, total) if n > 1 else None) for i, p in enumerate(page_rows)]


def test_sweep_reads_every_page_when_nothing_is_known():
    g = FakeGrid({"t": pages([R("E600003-010126"), R("E600002-010126")], [R("E600001-010126")])})
    res = gq.sweep_term(g, "t", lambda n: False, max_pages=5)
    assert [r["request_no"] for r in res.rows] == ["E600003-010126", "E600002-010126", "E600001-010126"]
    assert res.pages_read == 2 and res.total_pages == 2 and not res.stopped_on_known and not res.overflow


def test_sweep_stops_at_the_first_page_that_holds_ONLY_known_requests():
    g = FakeGrid({"t": pages([R("E600003-010126"), R("E600002-010126")], [R("E600001-010126")])})
    res = gq.sweep_term(g, "t", lambda n: n in {"E600003-010126", "E600002-010126"}, max_pages=5)
    assert res.pages_read == 1 and res.stopped_on_known and len(res.rows) == 2 and g.cursor == 0


def test_a_mixed_page_keeps_reading_so_a_date_tie_across_a_page_boundary_is_not_missed():
    g = FakeGrid({"t": pages([R("E600009-010126"), R("E600003-010126")], [R("E600008-010126"), R("E600002-010126")],
                             [R("E600001-010126")])})
    known = {"E600003-010126", "E600002-010126", "E600001-010126"}
    res = gq.sweep_term(g, "t", lambda n: n in known, max_pages=5)
    assert "E600008-010126" in {r["request_no"] for r in res.rows}          # the unknown row on page 2 was reached
    assert res.pages_read == 3 and res.stopped_on_known


def test_sweep_flags_overflow_instead_of_paging_forever():
    g = FakeGrid({"t": pages(*[[R(f"E6000{i}{j}-010126") for j in range(2)] for i in range(6)])})
    res = gq.sweep_term(g, "t", lambda n: False, max_pages=3)
    assert res.pages_read == 3 and res.overflow and not res.stopped_on_known and res.total_pages == 6


def test_sweep_of_an_empty_or_pagerless_result():
    res = gq.sweep_term(FakeGrid({"t": [([], None)]}), "t", lambda n: False, max_pages=3)
    assert res.rows == [] and res.pages_read == 1 and not res.overflow


# ==============================================================================
# Client — ArchiveSession (fake HTTP; no network)
# ==============================================================================


class Resp:
    def __init__(self, status=200, text="", url="", headers=None, content=b""):
        self.status_code, self.text, self.url = status, text, url
        self.headers = headers or {}
        self.content = content or text.encode()
        self.closed = False

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def close(self):
        self.closed = True


class FakeHTTP:
    """Scripted requests.Session: a list of (method, url-substring, Resp) consumed in order."""

    def __init__(self, script):
        self.script, self.calls, self.headers = list(script), [], {}
        self.cookie_clears = 0
        outer = self

        class _Cookies:
            def clear(self):
                outer.cookie_clears += 1
        self.cookies = _Cookies()

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        m, frag, resp = self.script.pop(0)
        assert m == method and frag in url, f"unexpected {method} {url}; wanted {m} ..{frag}.."
        return resp


SESSION_URL = "https://michiganegle.govqa.us/WEBAPP/_rs/(S(abc123))/OpenRecordsSummary.aspx?view=1"


def summary_ok():
    return ("GET", "OpenRecordsSummary.aspx", Resp(200, SUMMARY, url=SESSION_URL))


def test_lookup_posts_the_form_to_the_session_url_with_txtRefsearch():
    http = FakeHTTP([summary_ok(), ("POST", "(S(abc123))/OpenRecordsSummary.aspx", Resp(200, GRID))])
    row = gq.ArchiveSession(session=http).lookup("E615662-090926")
    assert row["request_no"] == "E615662-090926" and row["status"] == "DENIED – No Records" and row["rid"] == "712001"
    post = http.calls[1]
    assert post["data"]["txtRefsearch"] == "E615662-090926" and post["data"]["filterButton"] == "FILTER"
    assert post["data"]["__VIEWSTATE"] == "VSTATE" and "viewport" not in post["data"]
    assert post["headers"]["Referer"] == SESSION_URL


def test_lookup_returns_none_only_when_the_grid_positively_says_it_is_empty():
    http = FakeHTTP([summary_ok(), ("POST", "OpenRecordsSummary", Resp(200, EMPTY_GRID))])
    assert gq.ArchiveSession(session=http).lookup("E999999-010126") is None
    # ...and a page that is neither a grid nor an empty grid is a FETCH error, not "not found"
    http = FakeHTTP([summary_ok(), ("POST", "OpenRecordsSummary", Resp(200, "<html>Just a moment…</html>"))])
    with pytest.raises(gq.GovqaFetchError, match="did not render"):
        gq.ArchiveSession(session=http).lookup("E999999-010126")
    # a grid that lists OTHER requests but not this one is also "not shown"
    http = FakeHTTP([summary_ok(), ("POST", "OpenRecordsSummary", Resp(200, GRID))])
    assert gq.ArchiveSession(session=http).lookup("E999999-010126") is None


@pytest.mark.parametrize("bad", ["E1", "e615953-091526", "E615953-09152x", "", "../E615953-091526"])
def test_lookup_rejects_malformed_numbers_before_any_request(bad):
    http = FakeHTTP([])
    with pytest.raises(ValueError):
        gq.ArchiveSession(session=http).lookup(bad)
    assert http.calls == []


def test_lookup_transport_and_page_shape_failures_are_fetch_errors():
    with pytest.raises(gq.GovqaFetchError):
        gq.ArchiveSession(session=FakeHTTP([("GET", "Open", Resp(200, "<html>challenge</html>", url=SESSION_URL))])).lookup("E615953-091526")
    with pytest.raises(gq.GovqaFetchError):
        gq.ArchiveSession(session=FakeHTTP([("GET", "Open", Resp(503, "", url=SESSION_URL))])).lookup("E615953-091526")


def test_details_works_on_a_fresh_session_by_opening_the_summary_first():
    """The sessionless path: no prior lookup(), so there is no (S(...)) session URL yet."""
    http = FakeHTTP([summary_ok(),
                     ("GET", "(S(abc123))/RequestArchiveDetails.aspx?rid=703017&view=1", Resp(200, DETAIL, url=SESSION_URL.replace("OpenRecordsSummary", "RequestArchiveDetails")))])
    s = gq.ArchiveSession(session=http)
    assert s.details("703017")["reference"] == "E614007-080526"
    assert [c["method"] for c in http.calls] == ["GET", "GET"] and "(S(abc123))" in http.calls[1]["url"]
    for bad in ("70a", "703017&x=1", "../1", ""):
        with pytest.raises(ValueError):
            s.details(bad)


PDF = b"%PDF-1.4\n" + b"y" * 3000
BLOB = "https://1michigandeq.blob.core.usgovcloudapi.net/michigandeq/abc.pdf?sig=zzz"


def loaded_session(script):
    http = FakeHTTP([("GET", "RequestArchiveDetails", Resp(200, DETAIL))] + script)
    s = gq.ArchiveSession(session=http)
    s._summary_page_url = SESSION_URL
    s.details("703017")
    return s, http


def test_download_reposts_the_form_and_follows_the_redirect_to_the_blob_store(tmp_path):
    s, http = loaded_session([
        ("POST", "RequestArchiveDetails", Resp(302, headers={"location": BLOB})),
        ("GET", "blob.core.usgovcloudapi.net", Resp(200, headers={"content-type": "application/pdf"}, content=PDF))])
    dest = tmp_path / "f.pdf"
    info = s.download("rptAttachments$ctl00$lnkStreamCloud", str(dest), max_bytes=100_000)
    assert dest.read_bytes() == PDF and info["size"] == len(PDF)
    assert info["sha256"] == hashlib.sha256(PDF).hexdigest() and info["md5"] == hashlib.md5(PDF, usedforsecurity=False).hexdigest()
    post, get = http.calls[1], http.calls[2]
    assert post["data"]["__EVENTTARGET"] == "rptAttachments$ctl00$lnkStreamCloud" and post["allow_redirects"] is False
    assert get["method"] == "GET" and get.get("data") is None            # the redirect is followed as a GET


@pytest.mark.parametrize("location", [
    "https://evil.example.com/x.pdf", "http://michiganegle.govqa.us/plain",                       # wrong host / not https
    "https://otheraccount.blob.core.usgovcloudapi.net/x.pdf", "https://x.blob.core.windows.net/x.pdf",   # another storage account
    "https://michiganegle.govqa.us.evil.example/x", "https://1michigandeq.blob.core.usgovcloudapi.net.evil.io/x"])
def test_download_refuses_any_redirect_outside_the_two_exact_hosts(tmp_path, location):
    s, _ = loaded_session([("POST", "RequestArchiveDetails", Resp(302, headers={"location": location}))])
    with pytest.raises(gq.GovqaFetchError, match="non-allowlisted"):
        s.download("rptAttachments$ctl00$lnkStreamCloud", str(tmp_path / "f"), max_bytes=100)


def test_download_html_answer_means_an_expired_session_and_leaves_no_file(tmp_path):
    s, _ = loaded_session([("POST", "RequestArchiveDetails",
                            Resp(200, "<html>timeout</html>", headers={"content-type": "text/html; charset=utf-8"}))])
    dest = tmp_path / "f"
    with pytest.raises(gq.GovqaFetchError, match="expired"):
        s.download("rptAttachments$ctl00$lnkStreamCloud", str(dest), max_bytes=10_000)
    assert not dest.exists()


@pytest.mark.parametrize("resp", [
    Resp(200, headers={"content-type": "application/pdf", "content-length": "5000"}, content=PDF),   # declared > cap
    Resp(200, headers={"content-type": "application/pdf"}, content=PDF),                              # streamed > cap
])
def test_download_enforces_the_size_cap_with_its_own_exception_and_removes_the_partial(tmp_path, resp):
    s, _ = loaded_session([("POST", "RequestArchiveDetails", resp)])
    dest = tmp_path / "f.pdf"
    with pytest.raises(gq.GovqaTooLargeError):
        s.download("rptAttachments$ctl00$lnkStreamCloud", str(dest), max_bytes=100)
    assert not dest.exists() and resp.closed


def test_download_empty_body_and_bad_targets(tmp_path):
    s, _ = loaded_session([("POST", "RequestArchiveDetails", Resp(200, headers={"content-type": "application/pdf"}, content=b""))])
    with pytest.raises(gq.GovqaFetchError, match="empty"):
        s.download("rptAttachments$ctl00$lnkStreamCloud", str(tmp_path / "f"), max_bytes=100)
    s, _ = loaded_session([])
    for bad in ("evil$target", "rptAttachments$ctl00$lnkStreamCloud; drop", "__VIEWSTATE"):
        with pytest.raises(ValueError):
            s.download(bad, str(tmp_path / "f"), max_bytes=100)
    with pytest.raises(gq.GovqaFetchError):
        gq.ArchiveSession(session=FakeHTTP([])).download("rptAttachments$ctl00$lnkStreamCloud", str(tmp_path / "f"), 1)


def test_playwright_grid_translates_every_launch_failure_to_a_structural_error(monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(gq.GovqaStructuralError, match="playwright is not installed"):
        gq.PlaywrightGrid().__enter__()

    class _PW:
        def start(self):
            raise RuntimeError("Executable doesn't exist at /x/chrome (https://cdn.example/secret?sig=1)")
    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: _PW()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    with pytest.raises(gq.GovqaStructuralError) as e:
        gq.PlaywrightGrid().__enter__()
    assert "browser could not be started" in str(e.value) and "RuntimeError" in str(e.value) and "secret" not in str(e.value)


# ==============================================================================
# Watcher — pure helpers
# ==============================================================================


def _row(key, event, **kw):
    r = [""] * 16
    r[gw.C_KEY], r[gw.C_EVENT] = key, event
    for name, idx in (("created", gw.C_CREATED), ("closed", gw.C_CLOSED), ("status", gw.C_STATUS), ("terms", gw.C_TERMS),
                      ("rid", gw.C_RID)):
        if name in kw:
            r[idx] = kw[name]
    return r


def test_keywords_accept_strings_and_dicts_and_skip_blanks():
    cfg = {"govqa": {"keywords": ["Holloway", {"term": "Arbor Hills", "require": ["Arbor Hills"]}, {"term": "  "}, ""]}}
    assert gw._keywords(cfg) == [{"term": "Holloway", "require": []},
                                 {"term": "Arbor Hills", "require": ["Arbor Hills"]}]


def test_keyword_matches_requires_every_phrase_and_trusts_the_site_when_none_given():
    assert gw.keyword_matches({"term": "x", "require": []}, "text without the term at all")
    kw = {"term": "Six Mile", "require": ["Six Mile", "Salem"]}
    assert gw.keyword_matches(kw, "10690 six mile rd, Salem Twp") and not gw.keyword_matches(kw, "Six Mile Livonia")
    assert not gw.keyword_matches({"term": "Arbor Hills", "require": ["Arbor Hills"]}, "Ann Arbor Housing")


def test_csv_rows_match_on_any_keyword_term_in_the_summary_ignoring_query_quotes():
    kws = [{"term": "Holloway", "require": []}, {"term": '"Arbor Hills"', "require": []}]
    assert gw.csv_row_matches(kws, "the old Holloway landfill") and gw.csv_row_matches(kws, "ARBOR HILLS records")
    assert not gw.csv_row_matches(kws, "Carleton Farms records") and not gw.csv_row_matches(kws, "Ann Arbor Hillsdale")
    assert gw.bare_term('  "Great Lakes Recycling" ') == "Great Lakes Recycling" and gw.bare_term("10690") == "10690"


def test_build_state_folds_requests_files_terms_and_csvs():
    st = gw.build_state([
        _row("term:Holloway", "baseline"), _row("term:Big", "partial"), _row("csv:abc:0123456789abcdef", "ingested"),
        _row("E1-1", "baseline"),                                                       # ignored: not an E-number
        _row("E600001-010126", "baseline", status="New Request", terms="Holloway", rid="9"),
        _row("E600001-010126", "status", status="GRANTED – Records", rid="9", closed="1/2/2026"),
        _row("E600002-010126", "nomatch", status="PARTIAL"),
        _row("E600003-010126", "new", status="Received"), _row("E600003-010126", "nomatch"),   # nomatch never un-matches
        _row("file:E600001-010126:a.pdf#1", "file-listed"), _row("file:E600001-010126:a.pdf#1", "file-failed"),
        _row("file:E600001-010126:a.pdf#1", "file-failed"),
        _row("file:E600001-010126:b.pdf#1", "file-listed"), _row("file:E600001-010126:b.pdf#1", "file-staged"),
        _row("file:E600001-010126:b.pdf#1", "file-listed"),                              # a re-list never demotes staged
        _row("file:E600001-010126:image001.png#1", "file-listed"), _row("file:E600001-010126:image001.png#2", "file-listed"),
        _row("file:E600001-010126:report#3#1", "file-listed"),                            # a name that itself ends in #3
        _row("list:E600001-010126", "list-pending"), _row("list:E600002-010126", "list-pending"),
        _row("list:E600002-010126", "file-list-done"),
    ])
    assert st["terms"] == {"Holloway", "Big"} and st["csvs"] == {"abc:0123456789abcdef"}
    rq = st["requests"]
    assert rq["E600001-010126"]["status"] == "GRANTED – Records" and rq["E600001-010126"]["matched"]
    assert rq["E600001-010126"]["rid"] == "9" and rq["E600001-010126"]["terms"] == "Holloway"
    assert rq["E600002-010126"]["matched"] is False and rq["E600003-010126"]["matched"] is True
    assert st["files"]["file:E600001-010126:a.pdf#1"]["fails"] == 2 and st["files"]["file:E600001-010126:a.pdf#1"]["state"] == "listed"
    assert st["files"]["file:E600001-010126:b.pdf#1"]["state"] == "staged"
    dup = st["files"]["file:E600001-010126:image001.png#2"]
    assert dup["name"] == "image001.png" and dup["occ"] == 2 and st["files"]["file:E600001-010126:image001.png#1"]["occ"] == 1
    odd = st["files"]["file:E600001-010126:report#3#1"]
    assert odd["name"] == "report#3" and odd["occ"] == 1                                # the LAST #n is the occurrence, always
    assert st["list_pending"] == {"E600001-010126"}                                      # E600002 was marked done
    assert gw.file_key("E600001-010126", "a.pdf") == "file:E600001-010126:a.pdf#1"
    assert gw.file_key("E600001-010126", "a.pdf", 3) == "file:E600001-010126:a.pdf#3"


def test_build_state_tolerates_rows_with_trailing_cells_stripped():
    st = gw.build_state([["2026-09-28", "E600001-010126", "baseline"], ["2026-09-28", "term:X"]])
    assert st["requests"]["E600001-010126"]["matched"] and st["requests"]["E600001-010126"]["status"] == "" and st["terms"] == {"X"}


def test_staged_name_is_content_addressed_and_never_carries_the_attachment_name():
    n = gw.staged_name("E614007-080526", "ab" * 32, "Jane_Doe_123_Main_St.PDF")
    assert n == "E614007-080526__abababababababab.pdf"
    assert "Jane" not in n and gw.staged_name("E614007-080526", "cd" * 32, "noext") == "E614007-080526__cdcdcdcdcdcdcdcd"
    assert gw.staged_name("evil/../x", "ef" * 32, "a.b/../c").startswith("E000000-000000__")
    assert gw.staged_name("E614007-080526", "ab" * 32, "x.p?d*f") == "E614007-080526__abababababababab.pdf"
    # an unknown "extension" (really a slice of the attachment's own name) never reaches a Drive name or query
    assert gw.staged_name("E614007-080526", "ab" * 32, "John.Smith letter") == "E614007-080526__abababababababab"
    assert gw.staged_name("E614007-080526", "ab" * 32, "a.tar.gz") == "E614007-080526__abababababababab"


def test_report_shows_sections_only_when_present_and_carries_the_privacy_note():
    body = gw.format_report(
        [{"request_no": "E600001-010126", "status": "New Request", "created": "9/1/2026", "terms": "Holloway",
          "summary": "x" * 500, "n_files": 0}],
        [({"request_no": "E600002-010126", "status": "GRANTED – Records", "created": "8/1/2026", "terms": "watch",
           "summary": "s", "closed": "9/2/2026", "n_files": 3}, "Received")],
        [], ["keyword 'X' needs a CSV export"])
    assert "NEW requests" in body and "STATUS CHANGES" in body and "NEEDS ATTENTION" in body and "FILES STAGED" not in body
    assert "Received → GRANTED – Records" in body and "3 file(s) listed" in body and "…" in body     # excerpt truncated
    assert "PRIVATE" in body and "file contents are not read" in body


def test_report_lists_cap_with_a_plus_n_more_line_for_every_section():
    many_new = [{"request_no": f"E6{i:05d}-010126", "status": "New", "created": "d", "terms": "t", "summary": "s"} for i in range(40)]
    many_chg = [({"request_no": f"E5{i:05d}-010126", "status": "GRANTED – Records", "created": "d", "terms": "t", "summary": "s"}, "New")
                for i in range(30)]
    many_st = [{"request": "E600001-010126", "size": 1, "sha256": "a" * 64} for _ in range(30)]
    body = gw.format_report(many_new, many_chg, many_st, [])
    assert body.count("+ 15 more request(s)") == 1 and body.count("+ 5 more change(s)") == 1 and body.count("+ 5 more file(s)") == 1


# ==============================================================================
# Watcher — run() flows
# ==============================================================================

CFG = {
    "govqa": {
        "enabled": True,
        "keywords": [{"term": "Arbor Hills", "require": ["Arbor Hills"]}, {"term": "Holloway"}],
        "max_pages_per_term": 5, "max_open_rechecks": 25,
        "recipients": ["trisha@example.org"],
    },
}


class RecordingSheets(FakeSheets):
    """A FakeSheets that records every spreadsheetId it is asked to touch."""

    def __init__(self):
        super().__init__()
        self.ids = set()
        inner, outer = self._values, self

        class _V:
            def get(self, spreadsheetId, range):
                outer.ids.add(spreadsheetId)
                return inner.get(spreadsheetId, range)

            def append(self, spreadsheetId, **kw):
                outer.ids.add(spreadsheetId)
                return inner.append(spreadsheetId, **kw)

            def update(self, spreadsheetId, **kw):
                outer.ids.add(spreadsheetId)
                return inner.update(spreadsheetId, **kw)
        self._v = _V()

    def get(self, spreadsheetId):
        self.ids.add(spreadsheetId)
        return super().get(spreadsheetId)

    def batchUpdate(self, spreadsheetId, body):
        self.ids.add(spreadsheetId)
        return super().batchUpdate(spreadsheetId, body)

    def values(self):
        return self._v


class FakeArchive:
    """A stand-in for ArchiveSession, driven by dicts."""

    def __init__(self, rows=None, details=None):
        self.rows = rows if rows is not None else {}
        self.details_by_rid = details if details is not None else {}
        self.lookups, self.detail_calls, self.downloads = [], [], []
        self.lookup_error = None
        self.resets = 0
        self.details_error = None
        self.download_error = {}                   # file name -> exception (or list of exceptions, consumed in order)
        self.file_bytes = b"%PDF-fake" + b"z" * 50
        self._current = None

    def sync_from(self, grid):
        """A real archive knows every request its grid shows: fill `rows` from the grid's pages
        (explicitly set rows win)."""
        for pages_ in grid.pages_by_term.values():
            for page_rows, _pager in pages_:
                for r in page_rows:
                    self.rows.setdefault(r["request_no"], dict(r))

    def lookup(self, no):
        self.lookups.append(no)
        if self.lookup_error:
            raise self.lookup_error
        r = self.rows.get(no)
        return dict(r) if r else None

    def reset(self):
        self.resets += 1

    def details(self, rid, expect_reference=None):
        self.detail_calls.append(rid)
        if self.details_error:
            raise self.details_error
        default = {"reference": next((n for n, r in self.rows.items() if r.get("rid") == rid), "?"), "closed": "", "files": []}
        page = self.details_by_rid.get(rid, default)
        if expect_reference and page["reference"] != expect_reference:              # like ArchiveSession: never remembered
            raise gq.GovqaStructuralError(f"the detail page for rid {rid} is for a different request")
        self._current = page
        return copy.deepcopy(self._current)

    def download(self, target, dest, max_bytes):
        name = next(f["name"] for f in self._current["files"] if f["target"] == target)
        self.downloads.append(name)
        err = self.download_error.get(name)
        if isinstance(err, list):
            err = err.pop(0) if err else None
        if err:
            raise err
        data = self.file_bytes + name.encode() + b"|" + target.encode()     # distinct content per attachment
        if len(data) > max_bytes:
            raise gq.GovqaTooLargeError("over cap")
        Path(dest).write_bytes(data)
        return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(), "content_type": "application/pdf"}


def _wire(monkeypatch, cfg=CFG, grid=None, archive=None, send=None):
    fake = RecordingSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PRIV")
    monkeypatch.setenv("GSHEET_ID", "PUB")
    for k in [k for k in os.environ if k.startswith("GOAUTH_")]:
        monkeypatch.delenv(k)
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: fake)

    def _send(subj, body, c, recipients=None):
        if send is not None:
            return send(subj, body, c, recipients)
        sent.append((subj, body, recipients))
        return True
    monkeypatch.setattr(gw.ea, "send_email", _send)
    grid = grid or FakeGrid()
    archive = archive or FakeArchive()
    return fake, sent, grid, archive


def go(grid, archive, argv=None, **kw):
    if hasattr(archive, "sync_from"):
        archive.sync_from(grid)
    return gw.run(argv or [], make_grid=lambda: grid, make_session=lambda: archive, sleep=lambda s: None, **kw)


def rows_of(fake, event=None, key=None):
    rows = fake._values._tabs.get(sw.TAB_GOVQA, [])[1:]
    return [r for r in rows if (event is None or r[gw.C_EVENT] == event) and (key is None or r[gw.C_KEY] == key)]


AH = "Records for the Arbor Hills Landfill"


def first_run_grid():
    return FakeGrid({
        "Arbor Hills": pages([R("E600003-010126", AH), R("E600002-010126", "Housing in Ann Arbor, Hillsdale"),
                              R("E600001-010126", "arbor hills 2019")]),
        "Holloway": pages([R("E599999-010126", "Holloway pit")]),
    })


def test_disabled_is_a_noop_touching_nothing(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, cfg={"govqa": {"enabled": False}})
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    assert go(grid, arch) == 0 and grid.searches == []


@pytest.mark.parametrize("private,public", [(None, "PUB"), ("", "PUB"), ("SAME", "SAME")])
def test_fails_closed_without_a_distinct_private_sheet(monkeypatch, private, public):
    fake, sent, grid, arch = _wire(monkeypatch)
    if private is None:
        monkeypatch.delenv("GSHEET_ID_PRIVATE")
    else:
        monkeypatch.setenv("GSHEET_ID_PRIVATE", private)
    monkeypatch.setenv("GSHEET_ID", public)
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    assert go(grid, arch) == 1 and sent == [] and grid.searches == []


@pytest.mark.parametrize("public_tab", ["TAB_NEW", "TAB_EVIDENCE", "TAB_MEASUREMENTS"])
def test_refuses_a_spreadsheet_that_holds_the_public_case_file_tabs_even_when_GSHEET_ID_is_unset(monkeypatch, public_tab):
    """The check that works in CI, where the workflow never sets GSHEET_ID: a GSHEET_ID_PRIVATE
    secret copy-pasted from the PUBLIC id names a spreadsheet that has the public tabs."""
    fake, sent, grid, arch = _wire(monkeypatch)
    monkeypatch.delenv("GSHEET_ID")
    fake._values._tabs[getattr(sw, public_tab)] = [["header"]]
    assert go(grid, arch) == 1
    assert sw.TAB_GOVQA not in fake._values._tabs and grid.searches == [] and sent == []          # nothing was created or written


def test_first_run_baselines_silently_records_nomatch_and_marks_the_terms(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    assert sorted(r[gw.C_KEY] for r in rows_of(fake, "baseline") if r[gw.C_KEY].startswith("E")) == \
        ["E599999-010126", "E600001-010126", "E600003-010126"]
    nm = rows_of(fake, "nomatch")
    assert [r[gw.C_KEY] for r in nm] == ["E600002-010126"] and nm[0][gw.C_EXCERPT] == ""      # no request text kept
    assert {r[gw.C_KEY] for r in rows_of(fake, "baseline") if r[gw.C_KEY].startswith("term:")} == \
        {"term:Arbor Hills", "term:Holloway"}
    assert sent == [] and fake.ids == {"PRIV"}                                              # never the public Sheet


def test_second_run_stops_at_a_fully_known_page_and_writes_nothing(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    n = len(rows_of(fake))
    g2 = first_run_grid()
    assert go(g2, arch) == 0 and len(rows_of(fake)) == n and sent == []


def test_a_new_request_after_baseline_is_recorded_then_alerted_with_its_excerpt(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    g2 = FakeGrid({"Arbor Hills": pages([R("E600009-020226", "Arbor Hills expansion FOIA from a neighbour", "New Request"),
                                         R("E600003-010126", AH)]),
                   "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 0
    new = rows_of(fake, "new")
    assert [r[gw.C_KEY] for r in new] == ["E600009-020226"] and "expansion FOIA" in new[0][gw.C_EXCERPT]
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "1 new request" in subj and "E600009-020226" in body and "expansion FOIA" in body
    assert recipients == ["trisha@example.org"]
    assert go(g2, arch) == 0 and len(sent) == 1                                               # not re-alerted


def test_a_keyword_added_later_baselines_its_own_first_sweep_silently_and_in_full(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"].append({"term": "Napier"})
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    g2 = first_run_grid()
    # a request that another term already recorded sits on page 1 of Napier: a FIRST sweep must not stop there
    g2.pages_by_term["Napier"] = pages([R("E600003-010126", AH), R("E500001-010125", "Napier Rd culvert")],
                                       [R("E500000-010125", "old Napier")])
    assert go(g2, arch) == 0
    assert {r[gw.C_KEY] for r in rows_of(fake, "baseline")} >= {"E500001-010125", "E500000-010125", "term:Napier"}
    assert rows_of(fake, "new") == [] and sent == []                                          # history is not "new"


def test_a_request_found_by_a_baselined_and_a_fresh_term_is_alertable(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"].append({"term": "10690"})
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    g2 = first_run_grid()
    g2.pages_by_term["Arbor Hills"] = pages([R("E600010-020226", "Arbor Hills at 10690 6 Mile"), R("E600003-010126", AH)])
    g2.pages_by_term["10690"] = pages([R("E600010-020226", "Arbor Hills at 10690 6 Mile")])
    assert go(g2, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "new")] == ["E600010-020226"] and len(sent) == 1


def test_a_nomatch_request_is_upgraded_when_a_term_that_matches_it_finds_it_later(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "nomatch")] == ["E600002-010126"]
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"] = [{"term": "Arbor Hills"}, {"term": "Holloway"}]                 # `require` relaxed
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    g2 = first_run_grid()
    g2.pages_by_term["Arbor Hills"] = pages([R("E600002-010126", "Housing in Ann Arbor, Hillsdale"), R("E600003-010126", AH)])
    assert go(g2, arch) == 0
    up = [r for r in rows_of(fake, "new") if r[gw.C_KEY] == "E600002-010126"]
    assert len(up) == 1 and "upgraded from no-match" in up[0][gw.C_NOTE]
    assert len(sent) == 1 and "E600002-010126" in sent[0][1]
    assert go(g2, arch) == 0 and len(sent) == 1                                               # and only once


def test_a_first_sweep_overflow_asks_for_a_csv_export_once_and_marks_the_term_partial(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(max_pages_per_term=2, keywords=[{"term": "Big"}])
    big = pages(*[[R(f"E70000{i}-010126"), R(f"E70001{i}-010126")] for i in range(6)])
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=FakeGrid({"Big": big}))
    assert go(grid, arch) == 1
    assert [r[gw.C_EVENT] for r in rows_of(fake, key="term:Big")] == ["partial"]              # documented gap, not a fake "baseline"
    assert "export a CSV" in rows_of(fake, key="term:Big")[0][gw.C_NOTE]
    assert len(rows_of(fake, "baseline")) == 4                                               # what was read is kept (silently)
    assert len(sent) == 1 and "CSV" in sent[0][1] and "Big" in sent[0][1]
    # ...and it asked ONCE: the next run is an ordinary incremental one (stops at a fully-known page), quiet, exit 0
    again = FakeGrid({"Big": big})
    assert go(again, arch) == 0 and len(sent) == 1 and again.cursor == 0


def test_an_overflow_on_an_already_baselined_keyword_alerts_the_new_rows_and_stays_loud(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(max_pages_per_term=2, keywords=[{"term": "Big"}])
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=FakeGrid({"Big": pages([R("E600001-010126")])}))
    assert go(grid, arch) == 0                                                               # baselined
    burst = pages(*[[R(f"E80000{i}-010126"), R(f"E80001{i}-010126")] for i in range(6)])   # a burst larger than the cap
    assert go(FakeGrid({"Big": burst}), arch) == 1
    assert len(rows_of(fake, "new")) == 4 and any("CSV" in s[1] for s in sent)               # what was read alerts; a human is asked


def test_a_term_that_keeps_timing_out_is_reported_and_the_run_moves_on(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    grid.fail_first = 99                                                                     # every search attempt times out
    assert go(grid, arch) == 1
    assert grid.searches.count("Arbor Hills") == 3 and "Holloway" in grid.searches           # 3 attempts, then it moved on
    assert grid.restarts >= 2 and rows_of(fake, "baseline") == []
    assert len(sent) == 1 and "gave up after 3" in sent[0][1]


def test_three_failing_keywords_in_a_row_trip_the_circuit_breaker(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"] = [{"term": t} for t in ("A", "B", "C", "D", "E")]
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg)
    grid.fail_first = 999
    assert go(grid, arch) == 1
    assert set(grid.searches) == {"A", "B", "C"} and "circuit breaker" in sent[0][1]          # D and E were never attempted


def test_the_time_budget_stops_a_phase_and_says_so(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["time_budget_minutes"] = 1
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=first_run_grid())
    ticks = iter([0.0] + [999.0] * 200)                                                       # the budget is gone right after setup
    assert go(grid, arch, clock=lambda: next(ticks)) == 1
    assert grid.searches == [] and "time budget reached" in sent[0][1]


def test_every_keyword_empty_while_requests_are_on_record_is_a_broken_read_not_no_news(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    n = len(rows_of(fake))
    blank = FakeGrid({"Arbor Hills": [([], None)], "Holloway": [([], None)]})
    assert go(blank, arch) == 1
    assert len(rows_of(fake)) == n and "every keyword returned zero rows" in sent[0][1]


def test_a_keyword_that_legitimately_has_no_matches_is_baselined_when_others_have_rows(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"] = [{"term": "Holloway"}, {"term": "81000004"}]
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=FakeGrid({"Holloway": pages([R("E599999-010126", "Holloway pit")]),
                                                                        "81000004": [([], None)]}))
    assert go(grid, arch) == 0
    assert {"term:Holloway", "term:81000004"} <= {r[gw.C_KEY] for r in rows_of(fake, "baseline")}


def test_an_unavailable_browser_is_loud_but_the_by_number_path_still_runs(monkeypatch):
    class NoBrowser:
        def __enter__(self):
            raise gq.GovqaStructuralError("playwright is not installed")
    fake, sent, grid, arch = _wire(monkeypatch)
    arch.rows["E614606-090126"] = R("E614606-090126", "Pine Tree records", "New Request")
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["watch_requests"] = ["E614606-090126"]
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    assert gw.run([], make_grid=lambda: NoBrowser(), make_session=lambda: arch, sleep=lambda s: None) == 1
    assert [r[gw.C_KEY] for r in rows_of(fake, "baseline")] == ["E614606-090126"]            # by-number path unaffected
    assert "playwright is not installed" in sent[0][1]


def test_any_browser_launch_error_type_is_contained(monkeypatch):
    class Boom:
        def __enter__(self):
            raise RuntimeError("browserType.launch: something Playwright-shaped")
    fake, sent, grid, arch = _wire(monkeypatch)
    assert gw.run([], make_grid=lambda: Boom(), make_session=lambda: arch, sleep=lambda s: None) == 1
    assert "keyword sweep unavailable: RuntimeError" in sent[0][1]


# --- the report is ALWAYS sent, even when a later phase blows up ---------------------------------------


def test_a_phase_that_crashes_after_rows_were_written_still_sends_the_alert(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    g2 = FakeGrid({"Arbor Hills": pages([R("E600009-020226", "Arbor Hills expansion FOIA", "New Request"), R("E600003-010126", AH)]),
                   "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    monkeypatch.setattr(gw.Run, "phase_recheck", lambda self: (_ for _ in ()).throw(RuntimeError("Sheets 503")))
    assert go(g2, arch) == 1
    assert [r[gw.C_KEY] for r in rows_of(fake, "new")] == ["E600009-020226"]                  # the row is written...
    assert len(sent) == 1 and "E600009-020226" in sent[0][1] and "re-check phase failed: RuntimeError" in sent[0][1]   # ...and so is the alert


def test_a_malformed_watch_request_is_reported_and_never_crashes_the_run(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["watch_requests"] = ["E614606", "e614606-090126", " E614606-090126 "]
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=first_run_grid())
    arch.rows["E614606-090126"] = R("E614606-090126", "Peer landfill", "New Request", rid="8006")
    assert go(grid, arch) == 1
    assert arch.lookups == ["E614606-090126"]                                                 # only the valid one was looked up
    assert sent[0][1].count("is not a full E-number") == 2
    assert "E614606-090126" in {r[gw.C_KEY] for r in rows_of(fake, "baseline")}


def test_a_send_that_fails_makes_the_run_red_but_keeps_the_rows(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid(), send=lambda *a: False)
    assert go(grid, arch) == 0                                                                 # baseline: nothing to send
    g2 = FakeGrid({"Arbor Hills": pages([R("E600009-020226", "Arbor Hills expansion", "New Request"), R("E600003-010126", AH)]),
                   "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 1 and len(rows_of(fake, "new")) == 1

def test_a_send_that_RAISES_makes_the_run_red_but_keeps_the_rows(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid(), send=lambda *a: (_ for _ in ()).throw(RuntimeError("smtp")))
    assert go(grid, arch) == 0                                                                 # baseline: nothing to send
    g2 = FakeGrid({"Arbor Hills": pages([R("E600009-020226", "Arbor Hills expansion", "New Request"), R("E600003-010126", AH)]),
                   "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 1 and len(rows_of(fake, "new")) == 1                                # the exception branch really ran


# --- re-check + status changes + released files ------------------------------------------


def _baselined(monkeypatch, status="WAITING FOR PAYMENT", cfg=CFG):
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=FakeGrid({
        "Arbor Hills": pages([R("E600003-010126", AH, status, rid="7003")]), "Holloway": pages([])}))
    assert go(grid, arch) == 0
    quiet = FakeGrid({"Arbor Hills": pages([R("E600003-010126", AH, status, rid="7003")]), "Holloway": pages([])})
    return fake, sent, quiet, arch


def test_an_open_request_that_changes_status_alerts_and_lists_its_released_files(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    arch.rows["E600003-010126"] = R("E600003-010126", AH, "GRANTED – Records", rid="7003")
    arch.details_by_rid["7003"] = {"reference": "E600003-010126", "closed": "9/2/2026 1:00 PM", "files": [
        {"target": "rptAttachments$ctl00$lnkStreamCloud", "name": "a.pdf"},
        {"target": "rptAttachments$ctl01$lnkStreamCloud", "name": "b.docx"}]}
    assert go(grid, arch) == 0
    st = rows_of(fake, "status")
    assert len(st) == 1 and "'WAITING FOR PAYMENT' -> 'GRANTED – Records'" in st[0][gw.C_NOTE]
    listed = rows_of(fake, "file-listed")
    assert {r[gw.C_KEY] for r in listed} == {"file:E600003-010126:a.pdf#1", "file:E600003-010126:b.docx#1"}
    assert all(r[gw.C_CLOSED] == "9/2/2026 1:00 PM" for r in listed)                          # the Closed column is written
    assert len(sent) == 1 and "STATUS CHANGES" in sent[0][1] and "2 file(s) listed" in sent[0][1]
    assert arch.downloads == []                                                              # download_attachments is off
    assert go(grid, arch) == 0 and len(sent) == 1                                            # terminal now: not re-checked
    assert arch.lookups.count("E600003-010126") == 1


def test_terminal_requests_are_never_rechecked_and_unknown_statuses_are_open(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch, status="DENIED – No Records")
    assert go(grid, arch) == 0 and arch.lookups == []
    fake, sent, grid, arch = _baselined(monkeypatch, status="A STATUS NOBODY HAS SEEN")
    arch.rows["E600003-010126"] = R("E600003-010126", AH, "A STATUS NOBODY HAS SEEN", rid="7003")
    assert go(grid, arch) == 0 and arch.lookups == ["E600003-010126"] and sent == []


def test_recheck_count_is_capped_and_the_truncation_is_reported(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["max_open_rechecks"] = 2
    grid = FakeGrid({"Arbor Hills": pages([R(f"E60000{i}-010126", AH, "Received") for i in range(5)]), "Holloway": pages([])})
    fake, sent, _, arch = _wire(monkeypatch, cfg=cfg, grid=grid)
    assert go(grid, arch) == 0
    q = FakeGrid({"Arbor Hills": pages([R("E600004-010126", AH, "Received")]), "Holloway": pages([])})
    arch.lookups.clear()
    assert go(q, arch) == 0 and arch.lookups == ["E600004-010126", "E600003-010126"]          # newest first
    assert len(sent) == 1 and "3 open request(s) were not re-checked" in sent[0][1]            # said out loud (exit stays 0)


def test_lookups_that_keep_returning_nothing_are_reported(monkeypatch):
    grid = FakeGrid({"Arbor Hills": pages([R(f"E60000{i}-010126", AH, "Received") for i in range(5)]), "Holloway": pages([])})
    fake, sent, _, arch = _wire(monkeypatch, grid=grid)
    assert go(grid, arch) == 0                                                # five open requests recorded (baseline; no lookups yet)
    arch.rows.clear()                                                         # the archive stops answering for them
    quiet = FakeGrid({"Arbor Hills": pages([R("E600004-010126", AH, "Received")]), "Holloway": pages([])})
    arch_quiet = arch
    monkeypatch.setattr(arch_quiet, "sync_from", lambda grid: None)           # ...so do NOT re-teach it from the grid
    assert go(quiet, arch) == 1
    assert "returned nothing for 5 of 5" in sent[0][1]
    arch.rows.update({f"E60000{i}-010126": R(f"E60000{i}-010126", AH, "Received", rid=f"60{i}") for i in range(5)})
    assert go(quiet, arch) == 0 and len(sent) == 1                            # recovered: no problem, no repeat noise


def test_watch_requests_baseline_silently_then_alert_on_a_status_change(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(watch_requests=["E614606-090126"], keywords=[])
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg)
    arch.rows["E614606-090126"] = R("E614606-090126", "Peer landfill records", "Cost estimate sent", rid="8006")
    assert go(grid, arch) == 0 and sent == [] and len(rows_of(fake, "baseline")) == 1
    arch.rows["E614606-090126"] = R("E614606-090126", "Peer landfill records", "GRANTED – Records", rid="8006")
    assert go(grid, arch) == 0 and len(sent) == 1 and "Cost estimate sent → GRANTED – Records" in sent[0][1]


def test_an_explicit_watch_request_overrides_a_nomatch(monkeypatch):
    cfg = copy.deepcopy(CFG)
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=first_run_grid())
    assert go(grid, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "nomatch")] == ["E600002-010126"]
    cfg["govqa"]["watch_requests"] = ["E600002-010126"]
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    arch.rows["E600002-010126"] = R("E600002-010126", "Housing in Ann Arbor, Hillsdale", "Received", rid="6002")
    assert go(first_run_grid(), arch) == 0
    assert any(r[gw.C_KEY] == "E600002-010126" and r[gw.C_TERMS] == "watch" for r in rows_of(fake, "baseline"))
    arch.rows["E600002-010126"] = R("E600002-010126", "Housing in Ann Arbor, Hillsdale", "GRANTED – Records", rid="6002")
    assert go(first_run_grid(), arch) == 0 and len(sent) == 1 and "Received → GRANTED – Records" in sent[0][1]


def test_empty_recipients_is_display_only_never_the_coalition_list(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["recipients"] = []
    fake, sent, grid, arch = _baselined(monkeypatch, cfg=cfg)
    arch.rows["E600003-010126"] = R("E600003-010126", AH, "GRANTED – Records", rid="7003")
    assert go(grid, arch) == 0
    assert len(rows_of(fake, "status")) == 1 and sent == []


def test_a_recheck_that_keeps_failing_is_reported_not_silent(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    arch.lookup_error = gq.GovqaFetchError("timeout")
    assert go(grid, arch) == 1 and len(sent) == 1 and "gave up after 3" in sent[0][1]


def test_a_sheet_read_failure_propagates_instead_of_treating_everything_as_new(monkeypatch):
    class Flaky(FakeSheets):
        broken = False

        def __init__(self):
            super().__init__()
            outer, inner = self, self._values

            class _V:
                def get(self, spreadsheetId, range):
                    if outer.broken and "GovQA Archive Watch" in range:
                        raise RuntimeError("sheets 503")
                    return inner.get(spreadsheetId, range)

                def append(self, *a, **k):
                    return inner.append(*a, **k)

                def update(self, *a, **k):
                    return inner.update(*a, **k)
            self._v = _V()

        def values(self):
            return self._v

    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    flaky = Flaky()
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: flaky)
    assert go(grid, arch) == 0
    flaky.broken = True
    with pytest.raises(RuntimeError):
        go(first_run_grid(), arch)
    assert sent == [] and len(flaky._values._tabs[sw.TAB_GOVQA]) > 1                          # nothing was re-recorded as new


# --- CSV drop ------------------------------------------------------------------------------


CSV_TEXT = ("Request Number,Create Date,Summary,Request Status\n"
            'E500010-010125,1/1/2025,"Arbor Hills Landfill air permits",GRANTED � Records\n'
            "E500011-010125,1/2/2025,Carleton Farms fees,GRANTED - Records\n"
            "E500012-010125,1/3/2025,the Holloway pit,DENIED - No Records\n")


def _wire_csv(monkeypatch, files, data=CSV_TEXT.encode(), grid=None):
    fake, sent, grid, arch = _wire(monkeypatch, grid=grid or first_run_grid())
    monkeypatch.setenv("GOAUTH_GOVQA_CSV_FOLDER_ID", "CSVFOLDER")
    monkeypatch.setattr(gw.dc, "drive_service", lambda: "DRIVE")
    monkeypatch.setattr(gw.dc, "list_files", lambda svc, fid: files)
    box = {"data": data}

    def _dl(svc, fid, dest):
        Path(dest).write_bytes(box["data"])
        return dest
    monkeypatch.setattr(gw.dc, "download_file", _dl)
    return fake, sent, grid, arch, box


def test_csv_drop_ingests_matching_old_requests_silently_once(monkeypatch):
    fake, sent, grid, arch, box = _wire_csv(monkeypatch, [{"id": "F1", "name": "govqa_export.csv"}, {"id": "F2", "name": "notes.txt"}])
    assert go(grid, arch) == 0
    got = {r[gw.C_KEY] for r in rows_of(fake, "baseline") if r[gw.C_KEY].startswith("E")}
    assert {"E500010-010125", "E500012-010125"} <= got and "E500011-010125" not in got     # keyword-filtered
    assert rows_of(fake, "ingested")[0][gw.C_KEY].startswith("csv:F1:") and sent == []
    n = len(rows_of(fake))
    assert go(first_run_grid(), arch) == 0 and len(rows_of(fake)) == n                     # never re-ingested
    ing = [r for r in rows_of(fake, "baseline") if r[gw.C_KEY] == "E500010-010125"][0]
    assert ing[gw.C_STATUS] == "GRANTED – Records"                                          # dash normalized


def test_the_csv_never_absorbs_a_request_the_sweep_should_alert_on(monkeypatch):
    """A CSV exported today contains what was posted since the last run. The sweep runs FIRST in
    the same run, so that request is alerted as new; only older, still-unknown history is baselined."""
    files = []
    fake, sent, grid, arch, box = _wire_csv(monkeypatch, files)
    assert go(grid, arch) == 0                                                              # run 1: no CSV yet; the sweep baselines
    files.append({"id": "F1", "name": "today.csv"})
    box["data"] = (CSV_TEXT + 'E600009-020226,2/2/2026,"Arbor Hills expansion FOIA",New Request\n').encode()
    g2 = FakeGrid({"Arbor Hills": pages([R("E600009-020226", "Arbor Hills expansion FOIA", "New Request"), R("E600003-010126", AH)]),
                   "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "new")] == ["E600009-020226"] and len(sent) == 1   # alerted, NOT absorbed
    csv_base = {r[gw.C_KEY] for r in rows_of(fake, "baseline") if r[gw.C_TERMS] == "csv"}
    assert csv_base == {"E500010-010125", "E500012-010125"}                                   # only the older history


def test_a_csv_replaced_in_place_under_the_same_drive_id_is_re_read(monkeypatch):
    fake, sent, grid, arch, box = _wire_csv(monkeypatch, [{"id": "F1", "name": "x.csv"}])
    assert go(grid, arch) == 0 and len(rows_of(fake, "ingested")) == 1
    box["data"] = (CSV_TEXT + "E500020-010125,1/9/2025,another Holloway request,GRANTED - Records\n").encode()
    assert go(first_run_grid(), arch) == 0
    assert len(rows_of(fake, "ingested")) == 2 and any(r[gw.C_KEY] == "E500020-010125" for r in rows_of(fake, "baseline"))


def test_a_csv_with_no_parseable_rows_is_reported_and_not_marked_ingested(monkeypatch):
    fake, sent, grid, arch, box = _wire_csv(monkeypatch, [{"id": "F1", "name": "x.csv"}], data=b"not,the,right\ncolumns,at,all\n")
    assert go(grid, arch) == 1
    assert rows_of(fake, "ingested") == [] and "has no parseable rows" in sent[0][1]


def test_an_unreadable_csv_folder_is_reported_but_never_sinks_the_run(monkeypatch):
    fake, sent, grid, arch, box = _wire_csv(monkeypatch, [])
    monkeypatch.setattr(gw.dc, "list_files", lambda svc, fid: (_ for _ in ()).throw(RuntimeError("drive 500")))
    assert go(grid, arch) == 1
    assert len(rows_of(fake, "baseline")) >= 3 and "CSV drop phase failed" in sent[0][1]


# --- the release listing survives failures (review round 2, M1) --------------------------------------


def _release(arch, files=("a.pdf", "b.docx")):
    arch.rows["E600003-010126"] = R("E600003-010126", AH, "GRANTED – Records", rid="7003")
    arch.details_by_rid["7003"] = {"reference": "E600003-010126", "closed": "9/2/2026 1:00 PM", "files": [
        {"target": f"rptAttachments$ctl0{i}$lnkStreamCloud", "name": n} for i, n in enumerate(files)]}


def test_a_release_whose_listing_fails_is_retried_next_run_even_though_the_request_is_terminal(monkeypatch):
    """The status row makes the request terminal (never re-checked). If reading its detail page then
    fails, the listing must not be lost — the E614007 case this stream exists for."""
    fake, sent, grid, arch = _baselined(monkeypatch)
    _release(arch)
    arch.details_error = gq.GovqaFetchError("timeout")
    assert go(grid, arch) == 1                                                       # said out loud
    assert len(rows_of(fake, "status")) == 1 and len(rows_of(fake, "list-pending")) == 1
    assert rows_of(fake, "file-listed") == [] and rows_of(fake, "file-list-done") == [] and arch.resets >= 2
    arch.details_error = None
    assert go(grid, arch) == 0
    assert {r[gw.C_KEY] for r in rows_of(fake, "file-listed")} == {"file:E600003-010126:a.pdf#1", "file:E600003-010126:b.docx#1"}
    assert len(rows_of(fake, "file-list-done")) == 1
    assert arch.lookups.count("E600003-010126") == 1                                 # terminal: NOT re-checked — the marker drove the retry
    n = len(rows_of(fake))
    assert go(grid, arch) == 0 and len(rows_of(fake)) == n                            # done: quiet from now on


def test_a_crash_between_the_status_row_and_the_listing_loses_nothing(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    _release(arch)
    with monkeypatch.context() as m:
        m.setattr(gw.Run, "phase_list_files", lambda self: (_ for _ in ()).throw(RuntimeError("runner killed")))
        assert go(grid, arch) == 1
    assert len(rows_of(fake, "status")) == 1 and len(rows_of(fake, "list-pending")) == 1 and rows_of(fake, "file-listed") == []
    assert go(grid, arch) == 0 and len(rows_of(fake, "file-listed")) == 2 and len(rows_of(fake, "file-list-done")) == 1


def test_the_pending_marker_and_the_status_row_are_written_in_one_append(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    _release(arch)
    appends = []
    real = gw.sw.append_govqa_rows
    monkeypatch.setattr(gw.sw, "append_govqa_rows", lambda svc, sid, rows: (appends.append([r[gw.C_EVENT] for r in rows]), real(svc, sid, rows))[1])
    assert go(grid, arch) == 0
    assert ["status", "list-pending"] in appends                                     # no crash can separate them


def test_a_released_request_with_no_attachments_is_marked_done_and_not_retried(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    _release(arch, files=())
    assert go(grid, arch) == 0 and len(rows_of(fake, "file-list-done")) == 1 and rows_of(fake, "file-listed") == []
    calls = len(arch.detail_calls)
    assert go(grid, arch) == 0 and len(arch.detail_calls) == calls


def test_history_that_was_already_released_at_baseline_is_not_listed(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch, status="GRANTED – Records")
    assert go(grid, arch) == 0
    assert rows_of(fake, "list-pending") == [] and arch.detail_calls == []


def test_a_new_request_that_is_already_released_gets_its_files_listed(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    arch.details_by_rid["7009"] = {"reference": "E600009-020226", "closed": "", "files": [
        {"target": "rptAttachments$ctl00$lnkStreamCloud", "name": "release.pdf"}]}
    g2 = FakeGrid({"Arbor Hills": pages([R("E600009-020226", "Arbor Hills FOIA", "GRANTED – Records", rid="7009"),
                                         R("E600003-010126", AH)]), "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "list-pending")] == ["list:E600009-020226"]
    assert [r[gw.C_KEY] for r in rows_of(fake, "file-listed")] == ["file:E600009-020226:release.pdf#1"]
    assert "1 file(s) listed" in sent[0][1]


def test_a_released_request_the_grid_gave_no_details_link_for_is_looked_up_by_number(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    arch.rows["E600009-020226"] = R("E600009-020226", "Arbor Hills FOIA", "GRANTED – Records", rid="7009")   # the archive knows the rid
    arch.details_by_rid["7009"] = {"reference": "E600009-020226", "closed": "", "files": [
        {"target": "rptAttachments$ctl00$lnkStreamCloud", "name": "release.pdf"}]}
    g2 = FakeGrid({"Arbor Hills": pages([dict(R("E600009-020226", "Arbor Hills FOIA", "GRANTED – Records"), rid=None),
                                         R("E600003-010126", AH)]), "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 0
    assert len(rows_of(fake, "file-listed")) == 1 and "E600009-020226" in arch.lookups


def test_a_detail_page_for_a_different_request_is_never_recorded_under_this_one(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    _release(arch)
    arch.details_by_rid["7003"]["reference"] = "E999999-010126"                      # the rid led somewhere else
    assert go(grid, arch) == 1
    assert rows_of(fake, "file-listed") == [] and rows_of(fake, "file-list-done") == [] and len(rows_of(fake, "list-pending")) == 1
    assert "different request" in sent[0][1]


# --- a watched request that never resolves is loud (M2) ------------------------------------------------


def test_a_watch_request_that_is_never_found_is_reported_every_run_until_it_resolves(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(watch_requests=["E614606-090126"], keywords=[])
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg)
    assert go(grid, arch) == 0 and len(sent) == 1                                     # a mistyped date suffix: not silent
    assert "E614606-090126 was not found in the archive" in sent[0][1] and "date suffix" in sent[0][1]
    assert go(grid, arch) == 0 and len(sent) == 2                                     # ...and again tomorrow
    arch.rows["E614606-090126"] = R("E614606-090126", "Peer landfill", "New Request", rid="8006")
    assert go(grid, arch) == 0 and len(sent) == 2 and len(rows_of(fake, "baseline")) == 1   # resolved: baselined silently


# --- keyword sweep robustness (M3 + containment) ---------------------------------------------------------


def test_a_first_sweep_page_of_ten_rows_with_no_pager_text_is_marked_partial(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"] = [{"term": "Big"}]
    ten = [R(f"E7000{i:02d}-010126") for i in range(10)]
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=FakeGrid({"Big": pages(ten)}))
    assert go(grid, arch) == 0                                                        # advisory: not a red run
    assert [r[gw.C_EVENT] for r in rows_of(fake, key="term:Big")] == ["partial"] and len(rows_of(fake, "baseline")) == 10
    assert "no pager text" in sent[0][1] and "CSV" in sent[0][1]


def test_an_impossible_pageless_result_is_a_failed_keyword_not_a_baseline(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"] = [{"term": "Big"}]
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=FakeGrid({"Big": pages([R(f"E7000{i:02d}-010126") for i in range(11)])}))
    assert go(grid, arch) == 1
    assert rows_of(fake, key="term:Big") == [] and rows_of(fake, "baseline") == [] and "no pager" in sent[0][1]


def test_an_unexpected_error_in_one_keyword_does_not_cost_the_others_their_finds(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    real = grid.search
    grid.search = lambda term: (_ for _ in ()).throw(RuntimeError("driver bug")) if term == "Arbor Hills" else real(term)
    assert go(grid, arch) == 1
    assert "E599999-010126" in {r[gw.C_KEY] for r in rows_of(fake, "baseline")}       # Holloway's finds were kept
    assert "RuntimeError" in sent[0][1] and rows_of(fake, key="term:Arbor Hills") == []


def test_a_browser_that_cannot_restart_does_not_abort_the_retry(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    grid.fail_first = 1
    grid.restart = lambda: (_ for _ in ()).throw(gq.GovqaFetchError("browser is gone"))
    assert go(grid, arch) == 0 and len(rows_of(fake, "baseline")) >= 4                # the second attempt succeeded


# --- listing: every event, strikes, contained lookup, kept rid (round 3) ------------------------------


def test_a_request_under_a_status_this_code_has_never_seen_is_still_listed(monkeypatch):
    """A release under an unseen status must not be hidden by a status whitelist."""
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    arch.details_by_rid["7011"] = {"reference": "E600011-020226", "closed": "", "files": [
        {"target": "rptAttachments$ctl00$lnkStreamCloud", "name": "release.pdf"}]}
    g2 = FakeGrid({"Arbor Hills": pages([R("E600011-020226", "Arbor Hills FOIA", "RELEASED – NEW WORDING", rid="7011"),
                                         R("E600003-010126", AH)]), "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "file-listed")] == ["file:E600011-020226:release.pdf#1"]


def test_a_listing_that_keeps_failing_is_struck_then_given_up_and_reported_once(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    _release(arch)
    arch.details_error = gq.GovqaFetchError("timeout")
    codes = [go(grid, arch) for _ in range(5)]
    assert codes == [1, 1, 1, 1, 0]                                                   # loud four times, then a non-fatal give-up
    assert len(rows_of(fake, "list-failed")) == 4 and len(rows_of(fake, "list-skipped")) == 1
    assert "gave up listing its released files after 5 failed attempts" in sent[-1][1]
    n = len(rows_of(fake))
    assert go(grid, arch) == 0 and len(rows_of(fake)) == n                            # no longer pending: quiet
    assert rows_of(fake, "file-list-done") == []


def test_a_fresh_release_event_restarts_the_listing_strikes():
    st = gw.build_state([_row("list:E600001-010126", "list-pending"), _row("list:E600001-010126", "list-failed"),
                         _row("list:E600001-010126", "list-failed")])
    assert st["list_fails"] == {"E600001-010126": 2} and st["list_pending"] == {"E600001-010126"}
    st = gw.build_state([_row("list:E600001-010126", "list-failed"), _row("list:E600001-010126", "list-pending")])
    assert st["list_fails"] == {}                                                       # a new release event starts a fresh count
    st = gw.build_state([_row("list:E600001-010126", "list-pending"), _row("list:E600001-010126", "list-skipped")])
    assert st["list_pending"] == set()


def test_a_lookup_that_fails_while_listing_costs_only_that_request(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    arch.details_by_rid["7010"] = {"reference": "E600010-020226", "closed": "", "files": [
        {"target": "rptAttachments$ctl00$lnkStreamCloud", "name": "b.pdf"}]}
    g2 = FakeGrid({"Arbor Hills": pages([dict(R("E600012-020226", "Arbor Hills FOIA A", "GRANTED – Records"), rid=None),
                                         R("E600010-020226", "Arbor Hills FOIA B", "GRANTED – Records", rid="7010"),
                                         R("E600003-010126", AH)]), "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    arch.lookup_error = gq.GovqaFetchError("timeout")
    assert go(g2, arch) == 1
    assert [r[gw.C_KEY] for r in rows_of(fake, "file-listed")] == ["file:E600010-020226:b.pdf#1"]     # B was not blocked by A
    assert [r[gw.C_KEY] for r in rows_of(fake, "list-failed")] == ["list:E600012-020226"]
    assert "E600012-020226" in sent[0][1] and "could not be listed" in sent[0][1]


def test_the_rid_found_by_number_is_kept_on_the_done_row_and_restored_from_it(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    arch.rows["E600009-020226"] = R("E600009-020226", "Arbor Hills FOIA", "GRANTED – Records", rid="7009")
    arch.details_by_rid["7009"] = {"reference": "E600009-020226", "closed": "", "files": []}
    g2 = FakeGrid({"Arbor Hills": pages([dict(R("E600009-020226", "Arbor Hills FOIA", "GRANTED – Records"), rid=None),
                                         R("E600003-010126", AH)]), "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    assert go(g2, arch) == 0
    done = rows_of(fake, "file-list-done")
    assert len(done) == 1 and done[0][gw.C_RID] == "7009"
    st = gw.build_state([r for r in fake._values._tabs[sw.TAB_GOVQA][1:]])
    assert st["requests"]["E600009-020226"]["rid"] == "7009"                            # restored for staging next run


def test_a_request_with_no_rid_cannot_hog_the_staging_batch(monkeypatch):
    """A's files sort first but its rid is unknown; with max_downloads_per_run=1 it would take the only slot
    and nothing would ever be staged for B."""
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(keywords=[], download_attachments=True, max_downloads_per_run=1, max_file_mb=1)
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg)
    uploads = _staging_env(monkeypatch)
    real = gw.build_state

    def seeded(rows):
        st = real(rows)
        blank = {"status": "GRANTED – Records", "created": "", "closed": "", "matched": True, "terms": "t"}
        st["requests"]["E600001-010126"] = dict(blank, rid="")                 # A: no rid known
        st["requests"]["E600003-010126"] = dict(blank, rid="7003")             # B: fine
        for no, name in (("E600001-010126", "a.pdf"), ("E600003-010126", "b.pdf")):
            st["files"][gw.file_key(no, name)] = {"request": no, "name": name, "occ": 1, "state": "listed", "fails": 0}
        return st
    monkeypatch.setattr(gw, "build_state", seeded)
    arch.details_by_rid["7003"] = {"reference": "E600003-010126", "closed": "", "files": [
        {"target": "rptAttachments$ctl00$lnkStreamCloud", "name": "b.pdf"}]}
    assert go(FakeGrid(), arch) == 0
    assert arch.downloads == ["b.pdf"] and len(uploads) == 1                       # B got the slot A could not use


def test_a_malformed_rid_or_key_in_the_sheet_costs_only_that_request(monkeypatch):
    """A hand-edited private-Sheet cell must not abort the listing phase for every other request."""
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    arch.details_by_rid["7010"] = {"reference": "E600010-020226", "closed": "", "files": []}
    real = gw.build_state

    def seeded(rows):
        st = real(rows)
        st["list_pending"].update({"E600020-010126", "E600010-020226"})
        st["requests"]["E600020-010126"] = {"status": "GRANTED – Records", "created": "", "closed": "", "rid": "70x", "matched": True, "terms": "t"}
        st["requests"]["E600010-020226"] = {"status": "GRANTED – Records", "created": "", "closed": "", "rid": "7010", "matched": True, "terms": "t"}
        return st
    monkeypatch.setattr(gw, "build_state", seeded)
    assert go(first_run_grid(), arch) == 1                                          # the bad one is reported...
    assert [r[gw.C_KEY] for r in rows_of(fake, "list-failed")] == ["list:E600020-010126"]
    assert [r[gw.C_KEY] for r in rows_of(fake, "file-list-done")] == ["list:E600010-020226"]   # ...and the other is still listed


def test_a_fresh_release_event_restarts_the_strike_count_in_the_same_run_too(monkeypatch):
    """4 persisted strikes, then the status changes: the new list-pending must start the count over (as a
    reload would), not give the release up after ONE more failure."""
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    arch.details_error = gq.GovqaFetchError("timeout")
    new = FakeGrid({"Arbor Hills": pages([R("E600009-020226", "Arbor Hills FOIA", "New Request", rid="7009"), R("E600003-010126", AH)]),
                    "Holloway": pages([R("E599999-010126", "Holloway pit")])})
    for _ in range(4):                                                              # the request appears; four listings fail
        assert go(new, arch) == 1
    assert len(rows_of(fake, "list-failed")) == 4 and rows_of(fake, "list-skipped") == []
    arch.rows["E600009-020226"] = R("E600009-020226", "Arbor Hills FOIA", "GRANTED – Records", rid="7009")
    assert go(new, arch) == 1                                                       # status change: a FRESH list-pending
    assert rows_of(fake, "list-skipped") == [] and len(rows_of(fake, "list-failed")) == 5     # one more strike, NOT a give-up


# --- keyword sweep guards (round 3) ---------------------------------------------------------------------


def test_one_keyword_going_dark_is_reported_while_the_others_still_return_rows(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    g2 = FakeGrid({"Arbor Hills": first_run_grid().pages_by_term["Arbor Hills"], "Holloway": [([], None)]})   # Holloway stops matching
    assert go(g2, arch) == 0                                                                                  # advisory, not red
    assert "keyword 'Holloway' returned zero items although it matched recorded requests before" in sent[0][1]


def test_every_keyword_empty_on_the_activation_run_writes_no_markers(monkeypatch):
    """Otherwise every `term:` marker would exist with no requests, and the next real run would alert the
    whole history as NEW."""
    blank = FakeGrid({"Arbor Hills": [([], None)], "Holloway": [([], None)]})
    fake, sent, grid, arch = _wire(monkeypatch, grid=blank)
    assert go(blank, arch) == 1
    assert rows_of(fake) == [] and "every keyword returned zero rows" in sent[0][1]


def test_the_partial_note_names_the_actual_cause(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(max_pages_per_term=2, keywords=[{"term": "Big"}, {"term": "Ten"}])
    big = pages(*[[R(f"E70000{i}-010126"), R(f"E70001{i}-010126")] for i in range(6)])
    ten = pages([R(f"E7100{i:02d}-010126") for i in range(10)])
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg, grid=FakeGrid({"Big": big, "Ten": ten}))
    go(grid, arch)
    assert "max_pages_per_term=2" in rows_of(fake, key="term:Big")[0][gw.C_NOTE]
    assert "no pager text" in rows_of(fake, key="term:Ten")[0][gw.C_NOTE] and "max_pages_per_term" not in rows_of(fake, key="term:Ten")[0][gw.C_NOTE]


# --- CSV history is not re-checked in the run that ingested it -------------------------------------------


def test_a_csv_ingested_open_request_is_not_rechecked_or_alerted_in_the_same_run(monkeypatch):
    csv = 'Request Number,Create Date,Summary,Request Status\nE500013-010125,1/4/2025,"Arbor Hills stack test",New Request\n'
    fake, sent, grid, arch, box = _wire_csv(monkeypatch, [{"id": "F1", "name": "x.csv"}], data=csv.encode())
    arch.rows["E500013-010125"] = R("E500013-010125", "Arbor Hills stack test", "GRANTED – Records", rid="5013")   # moved since the export
    assert go(grid, arch) == 0 and sent == [] and "E500013-010125" not in arch.lookups     # history: no alert in the ingesting run
    assert go(first_run_grid(), arch) == 0                                             # ...then an ordinary open request
    assert len(sent) == 1 and "New Request → GRANTED – Records" in sent[0][1]


# --- staging the attachments to a private folder --------------------------------------------


def _released_world(monkeypatch, names=("a.pdf", "b.pdf", "c.pdf"), download=True, cfg=None):
    cfg = copy.deepcopy(cfg or CFG)
    cfg["govqa"].update(download_attachments=download, max_downloads_per_run=10, max_file_mb=1)
    fake, sent, grid, arch = _baselined(monkeypatch, cfg=cfg)
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    arch.rows["E600003-010126"] = R("E600003-010126", AH, "GRANTED – Records", rid="7003")
    arch.details_by_rid["7003"] = {"reference": "E600003-010126", "closed": "9/2/2026", "files": [
        {"target": f"rptAttachments$ctl0{i}$lnkStreamCloud", "name": n} for i, n in enumerate(names)]}
    return fake, sent, grid, arch


def _staging_env(monkeypatch, folder="STAGE", **others):
    for k in ("GOAUTH_CLIENT_ID", "GOAUTH_CLIENT_SECRET", "GOAUTH_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv(gw.STAGING_ENV_DEFAULT, folder)
    for k, v in others.items():
        monkeypatch.setenv(k, v)
    uploads = []
    monkeypatch.setattr(gw.ac, "oauth_drive_service", lambda: "DRIVE-OAUTH")
    monkeypatch.setattr(gw.ac, "upload_file",
                        lambda svc, path, name, mime, folder: uploads.append((name, folder)) or f"https://drive/{name}")
    return uploads


def test_staging_downloads_hashes_and_uploads_content_addressed_names_to_the_private_folder_only(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("Jane_Doe_123_Main_St.pdf", "b.pdf", "c.pdf"))
    uploads = _staging_env(monkeypatch)
    assert go(grid, arch) == 0
    staged = rows_of(fake, "file-staged")
    assert len(staged) == 3 and {u[1] for u in uploads} == {"STAGE"}
    assert all(r[gw.C_SHA] and r[gw.C_MD5] and r[gw.C_LINK].startswith("https://drive/") for r in staged)
    assert all(re.fullmatch(r"E600003-010126__[0-9a-f]{16}\.pdf", u[0]) for u in uploads)     # no attachment name in Drive
    assert any(r[gw.C_FILE] == "Jane_Doe_123_Main_St.pdf" for r in staged)                    # ...the real name is in the private Sheet
    assert "FILES STAGED" in sent[0][1] and "Jane_Doe" not in sent[0][1]
    n = len(arch.downloads)
    assert go(grid, arch) == 0 and len(arch.downloads) == n                                  # staged files are never re-downloaded


def test_duplicate_attachment_names_are_keyed_by_occurrence_and_each_is_staged(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("image001.png", "image001.png", "image001.png"))
    arch.file_bytes = b"\x89PNG"
    uploads = _staging_env(monkeypatch)
    assert go(grid, arch) == 0
    keys = sorted(r[gw.C_KEY] for r in rows_of(fake, "file-staged"))
    assert keys == ["file:E600003-010126:image001.png#1", "file:E600003-010126:image001.png#2", "file:E600003-010126:image001.png#3"]
    assert len({u[0] for u in uploads}) == 3 and arch.downloads == ["image001.png"] * 3    # three distinct targets were used


def test_staging_skips_files_identical_by_md5_to_a_held_folder(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("a.pdf",))
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(download_attachments=True, max_file_mb=1, held_folder_envs=["GOAUTH_ARCHIVE_FOLDER_ID"])
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    uploads = _staging_env(monkeypatch, GOAUTH_ARCHIVE_FOLDER_ID="HELD")
    md5 = hashlib.md5(arch.file_bytes + b"a.pdf|rptAttachments$ctl00$lnkStreamCloud", usedforsecurity=False).hexdigest()
    monkeypatch.setattr(gw, "_held_md5s", lambda drive, ids: {md5} if ids == ["HELD"] else set())
    monkeypatch.setattr(gw.dc, "drive_service", lambda: "DRIVE-SA")
    assert go(grid, arch) == 0
    assert len(rows_of(fake, "file-held")) == 1 and rows_of(fake, "file-staged") == [] and uploads == []


def test_oversized_files_are_skipped_once_and_never_retried(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("big.pdf", "ok.pdf"))
    arch.download_error = {"big.pdf": gq.GovqaTooLargeError("over cap")}
    _staging_env(monkeypatch)
    for _ in range(3):
        assert go(grid, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "file-staged")] == ["file:E600003-010126:ok.pdf#1"]
    assert arch.downloads.count("big.pdf") == 1 and len(rows_of(fake, "file-skipped")) == 1   # a size skip costs no strikes


def test_a_transient_download_failure_is_retried_in_run_without_costing_a_strike(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("a.pdf",))
    arch.download_error = {"a.pdf": [gq.GovqaFetchError("download answered text/html — session expired")]}   # first attempt only
    _staging_env(monkeypatch)
    assert go(grid, arch) == 0
    assert len(rows_of(fake, "file-staged")) == 1 and rows_of(fake, "file-failed") == []
    assert arch.downloads == ["a.pdf", "a.pdf"] and len(arch.detail_calls) >= 2               # details were reloaded for the retry


def test_a_persistently_failing_file_strikes_five_times_then_is_reported_and_never_blocks_the_rest(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("flaky.pdf", "ok.pdf"))
    arch.download_error = {"flaky.pdf": [gq.GovqaFetchError("boom")] * 99}
    _staging_env(monkeypatch)
    for _ in range(6):
        assert go(grid, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "file-staged")] == ["file:E600003-010126:ok.pdf#1"]
    assert len(rows_of(fake, "file-failed")) == 4 and len(rows_of(fake, "file-skipped")) == 1      # the 5th strike is a skip
    assert any("gave up after 5 failed staging attempts" in s[1] for s in sent)                    # ...and it is said out loud


def test_a_dead_oauth_token_is_reported_and_the_listing_survives(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch)
    _staging_env(monkeypatch)
    monkeypatch.setattr(gw.ac, "oauth_drive_service", lambda: (_ for _ in ()).throw(RuntimeError("invalid_grant")))
    assert go(grid, arch) == 1
    assert len(rows_of(fake, "file-listed")) == 3 and rows_of(fake, "file-staged") == []
    assert "staging phase failed: RuntimeError" in sent[0][1] and "STATUS CHANGES" in sent[0][1]     # the status alert still went out


def test_staging_refuses_a_folder_that_equals_another_mirrors_folder(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch)
    uploads = _staging_env(monkeypatch, folder="SHARED", GOAUTH_ARCHIVE_FOLDER_ID="SHARED")
    assert go(grid, arch) == 1
    assert arch.downloads == [] and uploads == [] and rows_of(fake, "file-staged") == []
    assert len(rows_of(fake, "file-listed")) == 3                                            # listing/rows/alerts unaffected


def test_staging_also_refuses_the_public_pdf_archive_folder(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch)
    uploads = _staging_env(monkeypatch, folder="PUBPDF", GDRIVE_FOLDER_ID="PUBPDF")
    assert go(grid, arch) == 1 and arch.downloads == [] and uploads == []


def test_staging_not_configured_lists_but_downloads_nothing(monkeypatch, capsys):
    fake, sent, grid, arch = _released_world(monkeypatch)
    assert go(grid, arch) == 0
    assert "staging not configured" in capsys.readouterr().out and arch.downloads == []
    assert len(rows_of(fake, "file-listed")) == 3


def test_download_attachments_off_never_downloads_even_when_staging_is_configured(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, download=False)
    _staging_env(monkeypatch)
    assert go(grid, arch) == 0 and arch.downloads == [] and len(rows_of(fake, "file-listed")) == 3


# --- log hygiene: the Actions log is world-readable ------------------------------------------------


LEAKY = ("boom https://1michigandeq.blob.core.usgovcloudapi.net/michigandeq/x.pdf?rscd=attachment%3B+filename%3D"
         "Jane_Doe_123_Main_St.pdf&sig=SECRETSIG failed")


def test_scrub_removes_urls_and_truncates():
    assert gq.scrub(LEAKY) == "boom <url> failed"
    schemeless = ("HTTPSConnectionPool(host='1michigandeq.blob.core.usgovcloudapi.net', port=443): Max retries "
                  "exceeded with url: /michigandeq/x.pdf?rscd=attachment%3B+filename%3DJane_Doe.pdf&sig=SECRETSIG (Caused by X)")
    out = gq.scrub(schemeless, 400)
    assert "Jane_Doe" not in out and "SECRETSIG" not in out and "<url>" in out
    assert gq.scrub("x" * 500) == "x" * 200 and gq.scrub("https://a/b https://c/d") == "<url> <url>"


def test_transport_errors_never_carry_the_url(tmp_path):
    import requests as rq

    class Boom:
        headers = {}

        def request(self, *a, **k):
            raise rq.ConnectionError(LEAKY)
    with pytest.raises(gq.GovqaFetchError) as e:
        gq.ArchiveSession(session=Boom()).lookup("E615953-091526")
    assert "Jane_Doe" not in str(e.value) and "SECRETSIG" not in str(e.value)
    s, _ = loaded_session([])
    s.http.request = lambda *a, **k: (_ for _ in ()).throw(rq.ConnectionError(LEAKY))
    with pytest.raises(gq.GovqaFetchError) as e2:
        s.download("rptAttachments$ctl00$lnkStreamCloud", str(tmp_path / "f"), 100)
    assert "Jane_Doe" not in str(e2.value) and "SECRETSIG" not in str(e2.value)


def test_a_failing_stage_never_puts_an_attachment_name_or_signed_url_in_stdout(monkeypatch, capsys):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("Jane_Doe_123_Main_St.pdf",))
    arch.download_error = {"Jane_Doe_123_Main_St.pdf": [RuntimeError(LEAKY)] * 9}
    _staging_env(monkeypatch)
    assert go(grid, arch) == 0
    out = capsys.readouterr().out
    assert "Jane_Doe" not in out and "SECRETSIG" not in out and "blob.core" not in out
    note = rows_of(fake, "file-failed")[0][gw.C_NOTE]
    assert "SECRETSIG" not in note and "blob.core" not in note and "<url>" in note      # the private Sheet is scrubbed too


def test_an_unhandled_error_reports_class_and_scrubbed_message_only(monkeypatch, capsys):
    monkeypatch.setattr(gw, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(LEAKY)))
    assert gw.main() == 1
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "Jane_Doe" not in out and "SECRETSIG" not in out


def test_main_silences_googleapiclients_retry_logger_which_prints_request_urls(monkeypatch):
    """googleapiclient logs 'Sleeping … retry … <method> <uri>' on every 5xx/429 retry — and a
    Drive query URI embeds file names. main() must silence it before anything runs."""
    monkeypatch.setattr(gw, "run", lambda *a, **k: 0)
    for name in ("googleapiclient", "googleapiclient.http"):
        logging.getLogger(name).setLevel(logging.WARNING)
    assert gw.main() == 0
    assert logging.getLogger("googleapiclient.http").getEffectiveLevel() == logging.CRITICAL
    assert logging.getLogger("googleapiclient").getEffectiveLevel() == logging.CRITICAL


_BANNED_IN_PRINT_NAMES = {"summary", "excerpt", "file_name", "key", "f", "row", "entry", "rq", "det", "items", "pending", "r"}
_BANNED_IN_PRINT_SUBSCRIPTS = {"name", "summary", "excerpt", "file_name"}
_EXC_NAMES = {"e", "exc", "err", "ex", "error"}


def _call_name(node):
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def _print_violations(source: str) -> list:
    """Every way a print() in `source` could put request text, an attachment name or a raw
    exception (which can embed a signed URL) into the WORLD-READABLE Actions log. Each
    exception Name is judged on its OWN position: it must sit inside a scrub()/type() call —
    one safe `type(e)` elsewhere in the same print does not excuse a raw `{e}` beside it."""
    bad, tree = [], ast.parse(source)
    for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) == "print"]:
        safe = {id(n) for c in ast.walk(call) if isinstance(c, ast.Call) and _call_name(c) in ("scrub", "type")
                for n in ast.walk(c)}
        for node in ast.walk(call):
            if isinstance(node, ast.Name) and node.id in _BANNED_IN_PRINT_NAMES:
                bad.append((call.lineno, node.id))
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and node.slice.value in _BANNED_IN_PRINT_SUBSCRIPTS:
                bad.append((call.lineno, node.slice.value))
            if isinstance(node, ast.Name) and node.id in _EXC_NAMES and id(node) not in safe:
                bad.append((call.lineno, f"raw exception {node.id}"))
    return bad


def test_no_print_can_interpolate_request_text_attachment_names_or_raw_exceptions():
    checked = 0
    for module in ("govqa_client.py", "govqa_watcher.py"):
        src = (ROOT / module).read_text()
        assert _print_violations(src) == [], module
        checked += sum(1 for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and _call_name(n) == "print")
    assert checked >= 15                                                            # the pin actually looked at the prints


@pytest.mark.parametrize("snippet", [
    'print(f"{type(e).__name__}: {e}")',                       # the bypass: a safe type(e) beside a raw {e}
    'print("x", str(exc))',
    'print(f"failed {row}")', 'print(f"{f[\'name\']}")', 'print(err)',
])
def test_the_print_pin_really_catches_leaks(snippet):
    assert _print_violations(snippet), snippet


@pytest.mark.parametrize("snippet", ['print(f"{type(e).__name__}: {gq.scrub(e)}")', 'print("x", scrub(exc, 100))', 'print(len(rows))'])
def test_the_print_pin_allows_scrubbed_and_typed_exceptions(snippet):
    assert _print_violations(snippet) == [], snippet


# --- probe ---------------------------------------------------------------------------------------


def test_probe_runs_even_when_disabled_and_writes_nothing(monkeypatch, capsys):
    fake, sent, grid, arch = _wire(monkeypatch, cfg={"govqa": {"enabled": False, "keywords": ["Holloway"]}},
                                   grid=FakeGrid({"Holloway": pages([R("E608533-040926", "Holloway pit")])}))
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    arch.rows["E614007-080526"] = R("E614007-080526", "x", "GRANTED/DENIED – Exempt in Part", rid="703017")
    arch.details_by_rid["703017"] = {"reference": "E614007-080526", "closed": "", "files": [{"target": "t", "name": "n"}] * 3}
    assert gw.run(["--probe"], make_grid=lambda: grid, make_session=lambda: arch) == 0
    out = capsys.readouterr().out
    assert "PROBE OK" in out and "3 file(s)" in out and "Holloway" in out and sent == []


def test_probe_failure_exits_nonzero(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch)
    arch.lookup_error = gq.GovqaFetchError("blocked")
    assert gw.run(["--probe"], make_grid=lambda: grid, make_session=lambda: arch) == 1


# ==============================================================================
# HARD RULE: never publish (ADR 059)
# ==============================================================================

_NEW = ("govqa_client.py", "govqa_watcher.py")


def _code_references(path):
    tree = ast.parse((ROOT / path).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_no_path_reaches_the_public_feed_the_public_sheet_or_another_archiver():
    forbidden = {"findings_feed", "gen_findings_feed", "archiver", "wds_archiver", "mmpc_archiver",
                 "civicclerk_archiver", "ridgewood_archiver", "public_comment_feed"}
    for name in _NEW:
        assert not (_code_references(name) & forbidden), name
    watcher = (ROOT / "govqa_watcher.py").read_text()
    guard = "\n".join(__import__("inspect").getsource(f) for f in (gw._private_sheet_id,))
    code_only = re.sub(r'""".*?"""', "", watcher.replace(guard, ""), flags=re.S)
    code_only = re.sub(r"#.*", "", code_only)
    assert not re.search(r"GSHEET_ID(?!_PRIVATE)", code_only)
    assert re.search(r'GSHEET_ID"\)', guard)
    assert "GSHEET_ID" not in (ROOT / "govqa_client.py").read_text().replace("GSHEET_ID_PRIVATE", "")
    client_src = (ROOT / "govqa_client.py").read_text()
    assert "sheets_service" not in client_src and "drive_service" not in client_src and "send_email" not in client_src


def test_the_workflow_never_receives_the_public_sheet_id_ships_disabled_and_pins_playwright():
    wf = (ROOT / ".github" / "workflows" / "govqa-watch.yml").read_text()
    assert "secrets.GSHEET_ID_PRIVATE" in wf and not re.search(r"secrets\.GSHEET_ID\s*\}\}", wf)
    assert re.search(r"pip install playwright==\d+\.\d+\.\d+", wf)
    from config_loader import load_config
    cfg = load_config()["govqa"]
    assert cfg["enabled"] is False and cfg["download_attachments"] is False
    assert cfg["recipients"] == ["arbor-hills@trishakunst.com"]
    assert (ROOT / "requirements.txt").read_text().lower().count("playwright") == 0     # optional, installed by the workflow only
    assert "secrets.GDRIVE_FOLDER_ID" in wf                                            # passed for the staging guard only
    other = {n for n in re.findall(r"GOAUTH_[A-Z_]*FOLDER_ID", "".join(p.read_text() for p in (ROOT / ".github" / "workflows").glob("*.yml")))}
    assert other <= set(re.findall(r"GOAUTH_[A-Z_]*FOLDER_ID", wf)), other - set(re.findall(r"GOAUTH_[A-Z_]*FOLDER_ID", wf))


def test_shipped_keywords_are_valid_and_multiword_terms_are_quoted():
    from config_loader import load_config
    kws = gw._keywords(load_config())
    assert [k["term"] for k in kws] == ['"Arbor Hills"', "Holloway", "10690", '"Six Mile"', "Napier", '"Great Lakes Recycling"',
                                        "10833", "N2688", "475946", "81000004"]
    for k in kws:
        bare = gw.bare_term(k["term"])
        assert (" " not in bare) or k["term"].startswith('"'), k                     # an unquoted multi-word term would be OR-ed word by word
        assert k["require"] == []                                                    # no `require` by default: it dropped real hits
