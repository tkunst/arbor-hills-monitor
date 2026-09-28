"""govqa_client.py / govqa_watcher.py (Stream U, ADR 059) — the EGLE GovQA public
FOIA archive watch.

HTML fixtures are trimmed Python literals shaped like the REAL markup (captured
2026-09-28 from the live archive: grid rows with aria-labelled cells and
`redirectInfo('<rid>')`, the 'Page 1 of 10 (97 items)' pager text, and a request
detail page with `Reference No:` labels and `rptAttachments$ctlNN$lnkStreamCloud`
postback links) — never committed HTML/PDF/JSON files (data-guard forbids them).
The request texts are synthetic; real ones can name residents and street addresses,
which is exactly why the watcher's rows are private-Sheet-only (pinned below).
"""
import ast
import copy
import inspect
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
        f'onclick="redirectInfo(&#39;{rid}&#39;)"><span>Details</span></a></td></tr>'
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


@pytest.mark.parametrize("status,terminal,released", [
    ("GRANTED – Records", True, True), ("GRANTED/DENIED – Exempt in Part", True, True),
    ("DENIED – No Records", True, False), ("CANCELLED", True, False), ("ABANDONED", True, False),
    ("PARTIAL", False, True), ("WAITING FOR PAYMENT", False, False), ("New Request", False, False),
    ("UTLR", False, False), ("Cost estimate sent", False, False), ("", False, False),
    ("SOME STATUS NOBODY HAS SEEN", False, False),          # unknown = OPEN (fail-safe)
])
def test_status_classification(status, terminal, released):
    assert gq.is_terminal(status) is terminal and gq.is_released(status) is released


def test_parse_rows_reads_real_grid_structure():
    rows = gq.parse_rows(GRID)
    assert [r["request_no"] for r in rows] == ["E615953-091526", "E615662-090926", "E613141-071526"]
    first = rows[0]
    assert first["created"] == "9/16/2026 1:00:00 AM" and first["status"] == "GRANTED – Records"
    assert first["rid"] == "714629" and "Arbor Hills Landfill & the Six Mile" in first["summary"]   # entity unescaped
    assert gq.parse_rows("<table></table>") == []


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
    latin = "Request Number,Create Date,Summary,Request Status\nE615953-091526,d,caf\xe9,GRANTED \u2013 Records\n"
    row = gq.parse_gridview_csv(latin.encode("cp1252"))[0]                  # a cp1252 file: 0xE9 / 0x96 are not valid UTF-8
    assert row["summary"] == "caf\xe9" and row["status"] == "GRANTED – Records"


def test_phrase_in_is_case_and_whitespace_insensitive():
    assert gq.phrase_in("Records for ARBOR   HILLS\nlandfill", "arbor hills")
    assert not gq.phrase_in("Ann Arbor Housing", "Arbor Hills")
    assert not gq.phrase_in("Ann Arbor Hillsdale", "Arbor Hills") and not gq.phrase_in("SRN 106900", "10690")
    assert gq.phrase_in("at 10690 6 Mile Rd, (Arbor Hills)", "10690") and gq.phrase_in("(Arbor Hills).", "arbor hills")
    assert not gq.phrase_in("anything", "")


def test_safe_filename_cannot_traverse_or_start_with_a_dot():
    n = gq.safe_filename("E614007-080526", "../../etc/passwd\x00 / Cost: Estimate?.docx")
    assert n.startswith("E614007-080526__") and n.endswith(".docx")
    assert "/" not in n and "\\" not in n and ".." not in n and "\x00" not in n
    assert gq.safe_filename("evil/../x", "a.pdf").startswith("E000000-000000__")
    assert not gq.safe_filename("E614007-080526", "...").split("__")[1].startswith(".")
    assert len(gq.safe_filename("E614007-080526", "x" * 500 + ".pdf")) < 140


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


def test_sweep_stops_at_the_first_page_holding_a_known_request():
    g = FakeGrid({"t": pages([R("E600003-010126"), R("E600002-010126")], [R("E600001-010126")])})
    res = gq.sweep_term(g, "t", lambda n: n == "E600002-010126", max_pages=5)
    assert res.pages_read == 1 and res.stopped_on_known and len(res.rows) == 2 and g.cursor == 0


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


def test_lookup_returns_none_when_the_archive_does_not_show_the_request():
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


def test_details_builds_the_url_from_the_session_and_validates_the_rid():
    http = FakeHTTP([summary_ok(), ("POST", "Open", Resp(200, GRID)),
                     ("GET", "(S(abc123))/RequestArchiveDetails.aspx?rid=703017&view=1", Resp(200, DETAIL))])
    s = gq.ArchiveSession(session=http)
    s.lookup("E615953-091526")
    assert s.details("703017")["reference"] == "E614007-080526"
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
    import hashlib
    assert info["sha256"] == hashlib.sha256(PDF).hexdigest() and info["md5"] == hashlib.md5(PDF, usedforsecurity=False).hexdigest()
    post, get = http.calls[1], http.calls[2]
    assert post["data"]["__EVENTTARGET"] == "rptAttachments$ctl00$lnkStreamCloud" and post["allow_redirects"] is False
    assert get["method"] == "GET" and get.get("data") is None            # the redirect is followed as a GET


def test_download_refuses_a_redirect_to_a_non_allowlisted_host(tmp_path):
    s, _ = loaded_session([("POST", "RequestArchiveDetails",
                            Resp(302, headers={"location": "https://evil.example.com/x.pdf"}))])
    with pytest.raises(gq.GovqaFetchError, match="non-allowlisted"):
        s.download("rptAttachments$ctl00$lnkStreamCloud", str(tmp_path / "f"), max_bytes=100)
    s2, _ = loaded_session([("POST", "RequestArchiveDetails",
                             Resp(302, headers={"location": "http://michiganegle.govqa.us/plain"}))])   # not https
    with pytest.raises(gq.GovqaFetchError, match="non-allowlisted"):
        s2.download("rptAttachments$ctl00$lnkStreamCloud", str(tmp_path / "f"), max_bytes=100)


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
def test_download_enforces_the_size_cap_and_removes_the_partial(tmp_path, resp):
    s, _ = loaded_session([("POST", "RequestArchiveDetails", resp)])
    dest = tmp_path / "f.pdf"
    with pytest.raises(ValueError):
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


def test_playwright_grid_raises_a_structural_error_when_playwright_is_missing(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(gq.GovqaStructuralError, match="playwright is not installed"):
        gq.PlaywrightGrid().__enter__()


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
        _row("term:Holloway", "baseline"), _row("csv:abc", "ingested"),
        _row("E1-1", "baseline"),                                                       # ignored: not an E-number
        _row("E600001-010126", "baseline", status="New Request", terms="Holloway", rid="9"),
        _row("E600001-010126", "status", status="GRANTED – Records", rid="9", closed="1/2/2026"),
        _row("E600002-010126", "nomatch", status="PARTIAL"),
        _row("E600003-010126", "new", status="Received"), _row("E600003-010126", "nomatch"),   # nomatch never un-matches
        _row("file:E600001-010126:a.pdf", "file-listed"), _row("file:E600001-010126:a.pdf", "file-failed"),
        _row("file:E600001-010126:a.pdf", "file-failed"),
        _row("file:E600001-010126:b.pdf", "file-listed"), _row("file:E600001-010126:b.pdf", "file-staged"),
        _row("file:E600001-010126:b.pdf", "file-listed"),                                # a re-list never demotes staged
    ])
    assert st["terms"] == {"Holloway"} and st["csvs"] == {"abc"}
    rq = st["requests"]
    assert rq["E600001-010126"]["status"] == "GRANTED – Records" and rq["E600001-010126"]["matched"]
    assert rq["E600001-010126"]["rid"] == "9" and rq["E600001-010126"]["terms"] == "Holloway"
    assert rq["E600002-010126"]["matched"] is False and rq["E600003-010126"]["matched"] is True
    assert st["files"]["file:E600001-010126:a.pdf"]["fails"] == 2 and st["files"]["file:E600001-010126:a.pdf"]["state"] == "listed"
    assert st["files"]["file:E600001-010126:b.pdf"]["state"] == "staged"


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
        self.download_error = {}
        self.file_bytes = b"%PDF-fake" + b"z" * 50
        self._current = None

    def lookup(self, no):
        self.lookups.append(no)
        if self.lookup_error:
            raise self.lookup_error
        r = self.rows.get(no)
        return dict(r) if r else None

    def details(self, rid):
        self.detail_calls.append(rid)
        self._current = self.details_by_rid.get(rid, {"reference": "?", "closed": "", "files": []})
        return copy.deepcopy(self._current)

    def download(self, target, dest, max_bytes):
        import hashlib
        name = next(f["name"] for f in self._current["files"] if f["target"] == target)
        self.downloads.append(name)
        if name in self.download_error:
            raise self.download_error[name]
        if len(self.file_bytes) > max_bytes:
            raise ValueError("over cap")
        Path(dest).write_bytes(self.file_bytes)
        return {"size": len(self.file_bytes), "sha256": hashlib.sha256(self.file_bytes).hexdigest(),
                "md5": hashlib.md5(self.file_bytes, usedforsecurity=False).hexdigest(), "content_type": "application/pdf"}


def _wire(monkeypatch, cfg=CFG, grid=None, archive=None):
    fake = RecordingSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PRIV")
    monkeypatch.setenv("GSHEET_ID", "PUB")
    for k in [k for k in os.environ if k.startswith("GOAUTH_")]:
        monkeypatch.delenv(k)
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(gw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))
    grid = grid or FakeGrid()
    archive = archive or FakeArchive()
    return fake, sent, grid, archive


def go(grid, archive, argv=None):
    return gw.run(argv or [], make_grid=lambda: grid, make_session=lambda: archive, sleep=lambda s: None)


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


def test_second_run_stops_at_known_requests_and_writes_nothing(monkeypatch):
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


def test_a_keyword_added_later_baselines_its_own_first_sweep_silently(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["keywords"].append({"term": "Napier"})
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    g2 = first_run_grid()
    g2.pages_by_term["Napier"] = pages([R("E500001-010125", "Napier Rd culvert"), R("E500000-010125", "old Napier")])
    assert go(g2, arch) == 0
    assert {r[gw.C_KEY] for r in rows_of(fake, "baseline")} >= {"E500001-010125", "E500000-010125", "term:Napier"}
    assert rows_of(fake, "new") == [] and sent == []                                         # history is not "new"


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
    # ...and it asked ONCE: the next run is an ordinary incremental one (stops at a known request), quiet, exit 0
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


def test_an_unavailable_browser_is_loud_but_the_recheck_path_still_runs(monkeypatch):
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
    assert {r[gw.C_KEY] for r in rows_of(fake, "file-listed")} == {"file:E600003-010126:a.pdf", "file:E600003-010126:b.docx"}
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


def test_recheck_count_is_capped(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["max_open_rechecks"] = 2
    grid = FakeGrid({"Arbor Hills": pages([R(f"E60000{i}-010126", AH, "Received") for i in range(5)]), "Holloway": pages([])})
    fake, sent, _, arch = _wire(monkeypatch, cfg=cfg, grid=grid)
    assert go(grid, arch) == 0
    q = FakeGrid({"Arbor Hills": pages([R("E600009-020226", AH, "Received")]), "Holloway": pages([])})
    q.pages_by_term["Arbor Hills"] = pages([R("E600004-010126", AH, "Received")])
    arch.lookups.clear()
    assert go(q, arch) == 0 and len(arch.lookups) == 2 and arch.lookups == ["E600004-010126", "E600003-010126"]   # newest first


def test_watch_requests_baseline_silently_then_alert_on_a_status_change(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(watch_requests=["E614606-090126"], keywords=[])
    fake, sent, grid, arch = _wire(monkeypatch, cfg=cfg)
    arch.rows["E614606-090126"] = R("E614606-090126", "Peer landfill records", "Cost estimate sent", rid="8006")
    assert go(grid, arch) == 0 and sent == [] and len(rows_of(fake, "baseline")) == 1
    arch.rows["E614606-090126"] = R("E614606-090126", "Peer landfill records", "GRANTED – Records", rid="8006")
    assert go(grid, arch) == 0 and len(sent) == 1 and "Cost estimate sent → GRANTED – Records" in sent[0][1]


def test_empty_recipients_is_display_only_never_the_coalition_list(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["govqa"]["recipients"] = []
    fake, sent, grid, arch = _baselined(monkeypatch, cfg=cfg)
    arch.rows["E600003-010126"] = R("E600003-010126", AH, "GRANTED – Records", rid="7003")
    assert go(grid, arch) == 0
    assert len(rows_of(fake, "status")) == 1 and sent == []


def test_send_failure_still_leaves_the_rows(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    arch.rows["E600003-010126"] = R("E600003-010126", AH, "PARTIAL", rid="7003")
    monkeypatch.setattr(gw.ea, "send_email", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp")))
    assert go(grid, arch) == 0 and len(rows_of(fake, "status")) == 1


def test_a_recheck_that_keeps_failing_is_reported_not_silent(monkeypatch):
    fake, sent, grid, arch = _baselined(monkeypatch)
    arch.lookup_error = gq.GovqaFetchError("timeout")
    assert go(grid, arch) == 1 and len(sent) == 1 and "gave up after 3" in sent[0][1]


def test_a_sheet_read_failure_propagates_instead_of_treating_everything_as_new(monkeypatch):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    assert go(grid, arch) == 0
    monkeypatch.setattr(gw.sw, "read_govqa_rows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sheets 503")))
    with pytest.raises(RuntimeError):
        go(first_run_grid(), arch)
    assert sent == []


# --- CSV drop ------------------------------------------------------------------------------


CSV_TEXT = ("Request Number,Create Date,Summary,Request Status\n"
            'E500010-010125,1/1/2025,"Arbor Hills Landfill air permits",GRANTED � Records\n'
            "E500011-010125,1/2/2025,Carleton Farms fees,GRANTED - Records\n"
            "E500012-010125,1/3/2025,the Holloway pit,DENIED - No Records\n")


def _wire_csv(monkeypatch, files, data=CSV_TEXT.encode()):
    fake, sent, grid, arch = _wire(monkeypatch, grid=first_run_grid())
    monkeypatch.setenv("GOAUTH_GOVQA_CSV_FOLDER_ID", "CSVFOLDER")
    monkeypatch.setattr(gw.dc, "drive_service", lambda: "DRIVE")
    monkeypatch.setattr(gw.dc, "list_files", lambda svc, fid: files)

    def _dl(svc, fid, dest):
        Path(dest).write_bytes(data)
        return dest
    monkeypatch.setattr(gw.dc, "download_file", _dl)
    return fake, sent, grid, arch


def test_csv_drop_ingests_matching_new_requests_silently_once(monkeypatch):
    fake, sent, grid, arch = _wire_csv(monkeypatch, [{"id": "F1", "name": "govqa_export.csv"}, {"id": "F2", "name": "notes.txt"}])
    assert go(grid, arch) == 0
    got = {r[gw.C_KEY] for r in rows_of(fake, "baseline") if r[gw.C_KEY].startswith("E")}
    assert {"E500010-010125", "E500012-010125"} <= got and "E500011-010125" not in got     # keyword-filtered
    assert rows_of(fake, "ingested")[0][gw.C_KEY] == "csv:F1" and sent == []
    n = len(rows_of(fake))
    assert go(first_run_grid(), arch) == 0 and len(rows_of(fake)) == n                     # never re-ingested
    ing = [r for r in rows_of(fake, "baseline") if r[gw.C_KEY] == "E500010-010125"][0]
    assert ing[gw.C_STATUS] == "GRANTED – Records"                                          # dash normalized


def test_an_unreadable_csv_folder_is_reported_but_never_sinks_the_run(monkeypatch):
    fake, sent, grid, arch = _wire_csv(monkeypatch, [])
    monkeypatch.setattr(gw.dc, "list_files", lambda svc, fid: (_ for _ in ()).throw(RuntimeError("drive 500")))
    assert go(grid, arch) == 1
    assert len(rows_of(fake, "baseline")) >= 3 and "CSV drop folder unreadable" in sent[0][1]


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


def test_staging_downloads_hashes_and_uploads_to_the_private_folder_only(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch)
    uploads = _staging_env(monkeypatch)
    assert go(grid, arch) == 0
    staged = rows_of(fake, "file-staged")
    assert len(staged) == 3 and {u[1] for u in uploads} == {"STAGE"}
    assert all(r[gw.C_SHA] and r[gw.C_MD5] and r[gw.C_LINK].startswith("https://drive/") for r in staged)
    assert all(re.fullmatch(r"E600003-010126__[a-z]\.pdf", u[0]) for u in uploads)
    assert "FILES STAGED" in sent[0][1]
    n = len(arch.downloads)
    assert go(grid, arch) == 0 and len(arch.downloads) == n                                  # staged files are never re-downloaded


def test_staging_skips_files_identical_by_md5_to_a_held_folder(monkeypatch):
    import hashlib
    fake, sent, grid, arch = _released_world(monkeypatch, names=("a.pdf",))
    cfg = copy.deepcopy(CFG)
    cfg["govqa"].update(download_attachments=True, max_file_mb=1, held_folder_envs=["GOAUTH_ARCHIVE_FOLDER_ID"])
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    uploads = _staging_env(monkeypatch, GOAUTH_ARCHIVE_FOLDER_ID="HELD")
    md5 = hashlib.md5(arch.file_bytes, usedforsecurity=False).hexdigest()
    monkeypatch.setattr(gw, "_held_md5s", lambda drive, ids: {md5} if ids == ["HELD"] else set())
    monkeypatch.setattr(gw.dc, "drive_service", lambda: "DRIVE-SA")
    assert go(grid, arch) == 0
    assert len(rows_of(fake, "file-held")) == 1 and rows_of(fake, "file-staged") == [] and uploads == []


def test_oversized_files_are_skipped_once_and_a_failing_file_is_retried_then_given_up_on(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch, names=("big.pdf", "flaky.pdf", "ok.pdf"))
    arch.file_bytes = b"%PDF" + b"x" * 10
    arch.download_error = {"big.pdf": ValueError("over cap"), "flaky.pdf": gq.GovqaFetchError("boom")}
    _staging_env(monkeypatch)
    for _ in range(4):
        assert go(grid, arch) == 0
    assert [r[gw.C_KEY] for r in rows_of(fake, "file-staged")] == ["file:E600003-010126:ok.pdf"]
    assert arch.downloads.count("big.pdf") == 1                                              # skipped once, never retried
    assert arch.downloads.count("flaky.pdf") == 3                                            # exactly _MAX_FILE_FAILS attempts
    assert len(rows_of(fake, "file-failed")) == 2 and len(rows_of(fake, "file-skipped")) == 2


def test_staging_refuses_a_folder_that_equals_another_mirrors_folder(monkeypatch):
    fake, sent, grid, arch = _released_world(monkeypatch)
    uploads = _staging_env(monkeypatch, folder="SHARED", GOAUTH_ARCHIVE_FOLDER_ID="SHARED")
    assert go(grid, arch) == 1
    assert arch.downloads == [] and uploads == [] and rows_of(fake, "file-staged") == []
    assert len(rows_of(fake, "file-listed")) == 3                                            # listing/rows/alerts unaffected


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
    arch.download_error = {"Jane_Doe_123_Main_St.pdf": RuntimeError(LEAKY)}
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


def test_no_print_statement_can_emit_request_text_or_attachment_names():
    """Static pin: a print()/f-string in the two modules must never interpolate the
    file name, the file key, or the request text/excerpt (those live in the private
    Sheet only; stdout is a public log)."""
    for name in _NEW:
        for m in re.finditer(r"print\((.*?)\)\s*$", (ROOT / name).read_text(), re.S | re.M):
            stmt = m.group(1)
            for banned in ('["name"]', "{key}", "summary", "excerpt", "file_name", "f['name']"):
                assert banned not in stmt.split("\n")[0], (name, banned, stmt[:80])


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
    guard = inspect.getsource(gw._private_sheet_id)
    code_only = re.sub(r'""".*?"""', "", watcher.replace(guard, ""), flags=re.S)
    code_only = re.sub(r"#.*", "", code_only)
    assert not re.search(r"GSHEET_ID(?!_PRIVATE)", code_only)
    assert re.search(r'GSHEET_ID"\)', guard)
    assert "GSHEET_ID" not in (ROOT / "govqa_client.py").read_text().replace("GSHEET_ID_PRIVATE", "")
    # the client never writes anywhere but the caller-supplied local path
    client_src = (ROOT / "govqa_client.py").read_text()
    assert "sheets_service" not in client_src and "drive_service" not in client_src and "send_email" not in client_src


def test_workflow_never_receives_the_public_sheet_id_and_ships_disabled():
    wf = (ROOT / ".github" / "workflows" / "govqa-watch.yml").read_text()
    assert "secrets.GSHEET_ID_PRIVATE" in wf and not re.search(r"secrets\.GSHEET_ID\s*\}\}", wf)
    from config_loader import load_config
    cfg = load_config()["govqa"]
    assert cfg["enabled"] is False and cfg["download_attachments"] is False
    assert cfg["recipients"] == ["arbor-hills@trishakunst.com"]
    assert (ROOT / "requirements.txt").read_text().lower().count("playwright") == 0     # optional, installed by the workflow only
