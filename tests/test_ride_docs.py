"""ride_docs_client.py / ride_docs_watcher.py (Stream T, ADR 061) — the RIDE
anonymous DOCUMENT listing watch.

Fixtures are trimmed Python literals modelled on the REAL response shape
(GetContentManagerFilesForLocationFilesTable, live-verified 2026-09-28: 37 files
for location 2085 / site 81000004, all `uri`s unique) — never committed JSON or
PDFs (the repo's data-guard forbids both). Titles here are deliberately the
non-sensitive ones: real RIDE titles can carry residents' names and street
addresses, which is exactly why the watcher's rows are private-Sheet-only, and
why several tests below pin that invariant.
"""
import copy
import inspect
import os
import re
from pathlib import Path

import pytest

import ride_docs_client as rdc
import ride_docs_watcher as rdw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(rdc, "MIN_INTERVAL", 0)


# ==============================================================================
# Fixtures
# ==============================================================================


def raw(uri, title="Confirmed Release Report", index_type="Correspondence",
        doc_date="2016-05-17T00:00:00.000", size=269554,
        created="2023-01-24T12:48:28.000", folder="DEQ-RRD/F/2023/683",
        ext="PDF", doc_no="DEQ-RRD-JAX/D/2023/1765"):
    """A raw file record with the real response's nesting."""
    return {
        "fileName": title, "indexType": index_type, "extension": ext,
        "contentManagerFolderUri": 34838954, "documentSize": size,
        "dateOfDocument": doc_date,
        "contentManagerFolderUriNavigation": {
            "fullAddress": "10690 6 Mile Road, Northville, MI", "city": "Northville",
            "uri": 34838954, "title": "Arbor Hills Landfill Incorporated - Site File",
            "contentManagerNumber": folder, "editStatus": 0,
        },
        "uri": uri, "contentManagerCreatedOn": created,
        "title": f"{index_type} - {title}", "contentManagerNumber": doc_no, "editStatus": 0,
    }


LOC = {"location_id": 2085, "program_num": "81000004", "name": "Arbor Hills - East",
       "location_type": "201"}


def three_files():
    return [
        raw(35715058, "July 2004", "Scoring Package (Historical)", "2004-07-14T00:00:00.000", 90332),
        raw(34838960, "Confirmed Release Report", "Correspondence", "2016-05-17T00:00:00.000", 269554),
        raw(34838961, "Closure Report", "Remediation/Investigation - Report Workplan (RI)",
            "2016-06-02T00:00:00.000", 19386135),
    ]


# ==============================================================================
# Client — canonicalization (pure)
# ==============================================================================


def test_file_view_canonicalizes_every_field():
    v = rdc.file_view(raw(35715058, "July 2004", "Scoring Package (Historical)",
                          "2004-07-14T00:00:00.000", 90332, "2025-07-16T10:20:40.000"))
    assert v == {
        "uri": "35715058", "title": "Scoring Package (Historical) - July 2004",
        "file_name": "July 2004", "index_type": "Scoring Package (Historical)",
        "extension": "PDF", "size": "90332", "date_of_document": "2004-07-14",
        "created_on": "2025-07-16", "folder_number": "DEQ-RRD/F/2023/683",
        "doc_number": "DEQ-RRD-JAX/D/2023/1765",
    }
    assert set(v) == set(rdc.FILE_FIELDS)


def test_1900_placeholder_document_date_is_blank_not_a_1900_date():
    v = rdc.file_view(raw(1, doc_date="1900-01-31T00:00:00.000"))
    assert v["date_of_document"] == ""


def test_missing_or_garbage_fields_never_crash():
    v = rdc.file_view({"uri": 7, "documentSize": None, "dateOfDocument": "not-a-date"})
    assert v["uri"] == "7" and v["size"] == "0" and v["date_of_document"] == "not-a-date"
    assert v["title"] == "" and v["folder_number"] == "" and v["created_on"] == ""


def test_record_hash_is_stable_and_sensitive():
    a = rdc.file_view(raw(1))
    assert rdc.record_hash(a) == rdc.record_hash(copy.deepcopy(a))
    for field, value in (("size", "1"), ("title", "x"), ("date_of_document", "2001-01-01"),
                         ("index_type", "Other"), ("folder_number", "F/1")):
        b = dict(a, **{field: value})
        assert rdc.record_hash(a) != rdc.record_hash(b), field


def test_safe_filename_is_title_free_and_cannot_traverse():
    """The mirror name lands in Drive queries, which googleapiclient prints on errors
    and retries — so it carries NO part of the (possibly resident-naming) title."""
    assert rdc.safe_filename("35715058", "PDF", "ab12cd34") == "35715058_ab12cd34.pdf"
    assert rdc.safe_filename("9", "", "") == "9.bin"
    n = rdc.safe_filename("../9/..", "../p\x00df", "zz/..")
    assert n == "9.pdf" and "/" not in n and ".." not in n and "\x00" not in n
    assert "title" not in inspect.signature(rdc.safe_filename).parameters


# ==============================================================================
# Client — HTTP (a fake session; no network)
# ==============================================================================


class FakeResp:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content if payload is None else b"{}"
        self.headers = headers or ({"content-type": "application/json"} if payload is not None else {})
        self._closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def close(self):
        self._closed = True


class FakeSession:
    """Routes by URL substring -> a FakeResp (or a list consumed in order)."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def _resp(self, url):
        for frag, r in self.routes.items():
            if frag in url:
                return r.pop(0) if isinstance(r, list) else r
        raise AssertionError(f"unrouted url {url}")

    def request(self, method, url, timeout=None, **kw):
        self.calls.append((method, url, kw.get("json")))
        return self._resp(url)

    def post(self, url, json=None, stream=False, timeout=None, headers=None):
        self.calls.append(("POST", url, json))
        return self._resp(url)


def files_payload(records, total=None):
    return FakeResp(payload={"totalRows": len(records) if total is None else total, "page": 1,
                             "data": records})


def test_open_session_warms_up_then_accepts_a_public_user(monkeypatch):
    fake = FakeSession({"inventory-of-facilities": FakeResp(200, content=b"<html>shell</html>"),
                        "GetAppSettings": FakeResp(200, {"applicationUser": {"userName": "Public1"}})})
    monkeypatch.setattr(rdc.requests, "Session", lambda: fake)
    assert rdc.open_session() is fake
    assert [c[1].rsplit("/", 1)[-1] for c in fake.calls] == ["facilities", "GetAppSettings"]


@pytest.mark.parametrize("settings", [FakeResp(405), FakeResp(200, {"nope": 1}), FakeResp(200, content=b"<html>")])
def test_open_session_raises_fetch_error_when_the_warmup_did_not_take(monkeypatch, settings):
    fake = FakeSession({"inventory-of-facilities": FakeResp(200, content=b"x"), "GetAppSettings": settings})
    monkeypatch.setattr(rdc.requests, "Session", lambda: fake)
    with pytest.raises(rdc.RideDocsFetchError):
        rdc.open_session()


def test_resolve_location_exact_match_only():
    s = FakeSession({"GetFacilitiesTable": FakeResp(payload={"totalRows": 2, "data": [
        {"locationId": 1, "programNum": "810000041", "name": "wrong contains-match"},
        {"locationId": 2085, "programNum": "81000004", "name": "Arbor Hills - East",
         "locationType": {"name": "201"}}]})})
    assert rdc.resolve_location(s, "81000004") == LOC


def test_resolve_location_returns_none_when_not_in_inventory():
    s = FakeSession({"GetFacilitiesTable": FakeResp(payload={"totalRows": 0, "data": []})})
    assert rdc.resolve_location(s, "00040223") is None


def test_resolve_location_structural_drift_is_parse_error():
    s = FakeSession({"GetFacilitiesTable": FakeResp(payload={"rows": []})})
    with pytest.raises(rdc.RideDocsParseError):
        rdc.resolve_location(s, "81000004")


def test_fetch_location_files_returns_records_sorted_by_uri_request():
    s = FakeSession({"ForLocationFilesTable": files_payload(three_files())})
    assert len(rdc.fetch_location_files(s, 2085)) == 3
    body = s.calls[0][2]
    assert body["sortColumnName"] == "uri"          # stable paging key, not the tied date
    assert body["filter"] == [{"columnName": "locationId", "filterMode": 0, "text": "2085"}]


def test_fetch_location_files_pages_until_total(monkeypatch):
    monkeypatch.setattr(rdc, "PAGE_ROWS", 2)
    s = FakeSession({"ForLocationFilesTable": [files_payload(three_files()[:2], total=3),
                                               files_payload(three_files()[2:], total=3)]})
    assert [r["uri"] for r in rdc.fetch_location_files(s, 2085)] == [35715058, 34838960, 34838961]
    assert [c[2]["pageNumber"] for c in s.calls] == [1, 2]


@pytest.mark.parametrize("payload,why", [
    ({"data": [], }, "no totalRows"),
    ({"totalRows": 2, "data": [raw(5), raw(5)]}, "duplicate uri"),
    ({"totalRows": 1, "data": [dict(raw(5), uri=None)]}, "record without uri"),
    ({"totalRows": 1, "data": [dict(raw(5), uri="5a")]}, "non-numeric uri"),
])
def test_fetch_location_files_structural_problems_are_parse_errors(payload, why):
    s = FakeSession({"ForLocationFilesTable": FakeResp(payload=payload)})
    with pytest.raises(rdc.RideDocsParseError):
        rdc.fetch_location_files(s, 2085)


def test_fetch_location_files_total_mismatch_is_a_parse_error():
    """Page 1 has 1 of 2 records, page 2 is EMPTY: the loop stops short of totalRows.
    (Distinct pages, so the duplicate-uri check cannot be what fires.)"""
    s = FakeSession({"ForLocationFilesTable": [files_payload(three_files()[:1], total=2),
                                               files_payload([], total=2)]})
    with pytest.raises(rdc.RideDocsParseError, match="totalRows=2"):
        rdc.fetch_location_files(s, 2085)


@pytest.mark.parametrize("data", [[1, 2], ["x"]])
def test_non_object_records_are_parse_errors_not_crashes(data):
    s = FakeSession({"ForLocationFilesTable": FakeResp(payload={"totalRows": len(data), "data": data}),
                     "GetFacilitiesTable": FakeResp(payload={"totalRows": 1, "data": data})})
    with pytest.raises(rdc.RideDocsParseError):
        rdc.fetch_location_files(s, 2085)
    with pytest.raises(rdc.RideDocsParseError):
        rdc.resolve_location(s, "81000004")


def test_non_integer_location_id_is_a_parse_error():
    s = FakeSession({"GetFacilitiesTable": FakeResp(payload={"totalRows": 1, "data": [
        {"locationId": "abc", "programNum": "81000004"}]})})
    with pytest.raises(rdc.RideDocsParseError):
        rdc.resolve_location(s, "81000004")


def test_405_is_a_transient_fetch_error_not_a_parse_error():
    s = FakeSession({"ForLocationFilesTable": FakeResp(405)})
    with pytest.raises(rdc.RideDocsFetchError):
        rdc.fetch_location_files(s, 2085)


PDF = b"%PDF-1.4\n" + b"x" * 5000


def test_download_streams_to_disk_and_hashes(tmp_path):
    import hashlib
    s = FakeSession({"GetFileContents": FakeResp(200, content=PDF, headers={"content-type": "application/pdf"})})
    dest = tmp_path / "f.pdf"
    info = rdc.download_file(s, "35715058", str(dest), max_bytes=10_000)
    assert dest.read_bytes() == PDF and info["size"] == len(PDF)
    assert info["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert info["md5"] == hashlib.md5(PDF, usedforsecurity=False).hexdigest()
    assert s.calls[0][2] == {"uri": 35715058}


@pytest.mark.parametrize("resp,exc", [
    (FakeResp(200, {"error": "expired"}), rdc.RideDocsFetchError),
    (FakeResp(200, content=b"<html>", headers={"content-type": "text/html"}), rdc.RideDocsFetchError),
    (FakeResp(200, content=b"", headers={"content-type": "application/pdf"}), rdc.RideDocsFetchError),
    (FakeResp(200, content=b"NOTAPDF" * 10, headers={"content-type": "application/pdf"}), rdc.RideDocsFetchError),
    (FakeResp(500), rdc.RideDocsFetchError),
    (FakeResp(200, content=PDF, headers={"content-type": "application/pdf",
                                         "content-length": str(len(PDF))}), rdc.RideDocsTooLargeError),
])
def test_download_failures_raise_and_leave_no_file(tmp_path, resp, exc):
    dest = tmp_path / "f.pdf"
    with pytest.raises(exc):
        rdc.download_file(FakeSession({"GetFileContents": resp}), "1", str(dest), max_bytes=100)
    assert not dest.exists() and resp._closed


def test_download_enforces_the_cap_while_streaming_without_a_content_length(tmp_path):
    dest = tmp_path / "f.pdf"
    resp = FakeResp(200, content=PDF, headers={"content-type": "application/pdf"})
    with pytest.raises(rdc.RideDocsTooLargeError):
        rdc.download_file(FakeSession({"GetFileContents": resp}), "1", str(dest), max_bytes=100)
    assert not dest.exists()


def test_a_non_pdf_body_error_carries_no_file_bytes(tmp_path):
    s = FakeSession({"GetFileContents": FakeResp(200, content=b"Jane Doe lives at", headers={
        "content-type": "application/octet-stream"})})
    with pytest.raises(rdc.RideDocsFetchError) as ei:
        rdc.download_file(s, "5", str(tmp_path / "f"), 10_000)
    assert "Jane" not in str(ei.value)


def test_a_slow_drip_download_hits_the_wall_clock_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(rdc, "_pace", lambda: None)       # pacing reads the clock too
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(rdc.time, "monotonic", lambda: next(clock))
    s = FakeSession({"GetFileContents": FakeResp(200, content=PDF * 50, headers={
        "content-type": "application/pdf"})})
    monkeypatch.setattr(rdc, "_DOWNLOAD_CHUNK", 1000)
    with pytest.raises(rdc.RideDocsFetchError, match="deadline"):
        rdc.download_file(s, "5", str(tmp_path / "f"), 10**9, deadline_s=600)
    assert not (tmp_path / "f").exists()


@pytest.mark.parametrize("bad", ["abc", "1; DROP", "../1", "", "1.5", "\u0661\u0662"])
def test_download_refuses_non_numeric_uris(tmp_path, bad):
    with pytest.raises(rdc.RideDocsFetchError):
        rdc.download_file(FakeSession({}), bad, str(tmp_path / "x"), max_bytes=100)


# ==============================================================================
# Watcher — state + diff (pure)
# ==============================================================================


def _row(key, event, lid="2085", program="81000004", title="", rec_hash="", link="", note=""):
    r = [""] * 19
    r[rdw.C_KEY], r[rdw.C_EVENT], r[rdw.C_LOC], r[rdw.C_PROGRAM] = key, event, lid, program
    r[rdw.C_TITLE], r[rdw.C_HASH], r[rdw.C_LINK], r[rdw.C_NOTE] = title, rec_hash, link, note
    return r


def test_build_state_last_row_wins_and_record_change_resets_mirror():
    files, locs = rdw.build_state([
        _row("loc:2085", "baseline"),
        _row("rrd:1", "baseline", rec_hash="h1", title="T"),
        _row("rrd:1", "mirrored", link="LINK", rec_hash="h1"),
        _row("rrd:2", "baseline", rec_hash="h2"),
        _row("rrd:2", "mirror-failed"), _row("rrd:2", "mirror-failed"),
        _row("rrd:3", "baseline", rec_hash="h3"), _row("rrd:3", "mirror-skipped"),
        _row("rrd:4", "baseline", rec_hash="h4"), _row("rrd:4", "removed"),
        _row("rrd:9", "mirrored", link="ORPHAN"),                       # never seen: ignored
    ])
    assert locs == {"loc:2085": {"program": "81000004", "skips": 0, "cause": "", "causes": set(),
                                "current": True}}
    assert files["rrd:1"]["mirror_link"] == "LINK" and files["rrd:1"]["title"] == "T"
    assert files["rrd:2"]["fails"] == 2 and not files["rrd:2"]["skipped"]
    assert files["rrd:3"]["skipped"] and files["rrd:4"]["removed"] and "rrd:9" not in files
    # a changed record resets the mirror bookkeeping
    files, _ = rdw.build_state([_row("rrd:1", "baseline", rec_hash="h1"), _row("rrd:1", "mirrored", link="L"),
                                _row("rrd:1", "changed", rec_hash="h1b")])
    assert files["rrd:1"]["mirror_link"] == "" and files["rrd:1"]["hash"] == "h1b"


def views_of(records):
    return {v["uri"]: v for v in (rdc.file_view(r) for r in records)}


def test_diff_first_sighting_is_all_baseline():
    d = rdw.diff_location(2085, views_of(three_files()), {}, baselined=False)
    assert len(d["baseline"]) == 3 and not (d["new"] or d["changed"] or d["removed"])


def test_diff_classifies_new_changed_removed_and_reappeared():
    recs = three_files()
    views = views_of(recs)
    files, _ = rdw.build_state(
        [_row(f"rrd:{u}", "baseline", rec_hash=rdc.record_hash(v)) for u, v in views.items()]
        + [_row("rrd:999", "baseline", rec_hash="gone")])
    now = views_of(recs[:2] + [raw(35715099, "New Doc")])
    now["34838960"] = dict(now["34838960"], size="1")            # changed record
    d = rdw.diff_location(2085, now, files, baselined=True)
    assert [v["uri"] for v in d["new"]] == ["35715099"]
    assert [v["uri"] for _, v in d["changed"]] == ["34838960"]
    assert sorted(u for u, _ in d["removed"]) == ["34838961", "999"]
    # a removed file that returns is `new` again
    files["rrd:34838961"]["removed"] = True
    d2 = rdw.diff_location(2085, views_of(recs), files, baselined=True)
    assert [v["uri"] for v in d2["new"]] == ["34838961"]


def test_diff_ignores_other_locations_files_for_removal():
    files, _ = rdw.build_state([_row("rrd:5", "baseline", lid="777", rec_hash="h")])
    assert rdw.diff_location(2085, {}, files, baselined=True)["removed"] == []


# ==============================================================================
# Watcher — copy (pure)
# ==============================================================================


def test_new_body_shows_both_dates_the_backlog_caveat_and_privacy_note():
    body = rdw.format_new_body(rdw.location_label(LOC),
                               [rdc.file_view(raw(35715058, "July 2004", created="2025-07-16T00:00:00.000"))])
    assert "document date 2016-05-17" in body and "added to RIDE 2025-07-16" in body
    assert "digitizing its backlog" in body and "NOT been reviewed" in body
    assert "residents' names" in body and "Nothing here is published" in body


def test_unknown_date_is_labelled_unknown_not_1900():
    v = rdc.file_view(raw(1, doc_date="1900-01-31T00:00:00.000"))
    body = rdw.format_new_body("L", [v])
    assert "unknown (RIDE lists no date)" in body and "1900" not in body


def test_bodies_cap_the_list_but_report_the_total():
    many = [rdc.file_view(raw(1000 + i)) for i in range(40)]
    body = rdw.format_new_body("L", many)
    assert "40 file(s)" in body and "+ 15 more" in body


def test_removed_body_carries_last_known_titles():
    body = rdw.format_removed_body("L", [("55", {"title": "Closure Report"})])
    assert "Closure Report" in body and "uri 55" in body and "NO LONGER LISTED" in body


# ==============================================================================
# Watcher — run() flows (fake Sheets, canned client, captured mailer)
# ==============================================================================

CFG = {
    "ride": {"site_ids": ["81000004"]},
    "ride_docs": {"enabled": True, "mirror": False, "recipients": ["trisha@example.org"]},
}


class RecordingSheets(FakeSheets):
    """A FakeSheets that records every spreadsheetId it is asked to touch."""

    def __init__(self):
        super().__init__()
        self.ids = set()
        self.read_error = None          # raised by a DATA-row read of the RRD tab (not the header)
        inner = self._values
        outer = self

        class _V:
            def get(self, spreadsheetId, range):
                outer.ids.add(spreadsheetId)
                if outer.read_error and sw.TAB_RRD_DOCS in range and "A2" in range:
                    raise outer.read_error
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


class World:
    """The canned RIDE: program -> location, location -> raw records."""

    def __init__(self):
        self.locations = {"81000004": dict(LOC)}
        self.files = {2085: three_files()}
        self.fetch_error = None
        self.list_error = None
        self.session_error = None
        self.downloads = []


def _wire(monkeypatch, tmp_path, world=None, cfg=CFG):
    world = world or World()
    fake = RecordingSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PRIV")
    monkeypatch.setenv("GSHEET_ID", "PUB")
    for k in [k for k in os.environ if re.fullmatch(r"GOAUTH_.*", k)]:
        monkeypatch.delenv(k)
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)
    monkeypatch.setattr(rdw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(rdw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(rdw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _open():
        if world.session_error:
            raise world.session_error
        return "SESSION"
    monkeypatch.setattr(rdw.rdc, "open_session", _open)

    def _resolve(session, pn, timeout=60):
        if world.fetch_error:
            raise world.fetch_error
        return copy.deepcopy(world.locations.get(pn))
    monkeypatch.setattr(rdw.rdc, "resolve_location", _resolve)

    def _list(session, lid, timeout=60):
        if world.list_error:
            raise world.list_error
        return copy.deepcopy(world.files[lid])
    monkeypatch.setattr(rdw.rdc, "fetch_location_files", _list)
    return world, fake, sent


def _rows(fake, event=None):
    rows = fake._values._tabs.get(sw.TAB_RRD_DOCS, [])[1:]
    return [r for r in rows if event is None or r[rdw.C_EVENT] == event]


def test_disabled_is_a_noop_touching_nothing(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg={"ride_docs": {"enabled": False}})
    monkeypatch.setattr(rdw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    monkeypatch.setattr(rdw.rdc, "open_session", lambda: (_ for _ in ()).throw(AssertionError("fetched")))
    assert rdw.run([]) == 0


@pytest.mark.parametrize("private,public", [(None, "PUB"), ("", "PUB"), ("SAME", "SAME")])
def test_fails_closed_without_a_distinct_private_sheet(monkeypatch, tmp_path, private, public):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    if private is None:
        monkeypatch.delenv("GSHEET_ID_PRIVATE")
    else:
        monkeypatch.setenv("GSHEET_ID_PRIVATE", private)
    monkeypatch.setenv("GSHEET_ID", public)
    monkeypatch.setattr(rdw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    assert rdw.run([]) == 1
    assert sent == []


def test_first_run_baselines_silently_and_only_touches_the_private_sheet(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    assert len(_rows(fake, "baseline")) == 3 + 1          # 3 files + the loc marker
    assert any(r[rdw.C_KEY] == "loc:2085" for r in _rows(fake))
    assert sent == []
    assert fake.ids == {"PRIV"}                            # NEVER the public Sheet


def test_second_run_unchanged_writes_nothing_and_stays_quiet(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    n = len(_rows(fake))
    assert rdw.run([]) == 0
    assert len(_rows(fake)) == n and sent == []


def test_new_file_writes_a_row_then_alerts_scoped_recipients(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.files[2085].append(raw(35715099, "Brand New Report", created="2026-09-30T08:00:00.000"))
    assert rdw.run([]) == 0
    new = _rows(fake, "new")
    assert len(new) == 1 and new[0][rdw.C_KEY] == "rrd:35715099"
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "1 new RRD file" in subj and "Brand New Report" in body
    assert recipients == ["trisha@example.org"]
    assert rdw.run([]) == 0 and len(sent) == 1             # no re-alert next run


def test_changed_record_alerts(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.files[2085][1]["documentSize"] = 1234
    assert rdw.run([]) == 0
    assert len(_rows(fake, "changed")) == 1 and len(sent) == 1
    assert "record changed" in sent[0][0]


def test_removed_file_alerts_once_and_a_return_is_new_again(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    gone = world.files[2085].pop(2)
    assert rdw.run([]) == 0
    assert len(_rows(fake, "removed")) == 1 and len(sent) == 1
    assert "no longer listed" in sent[0][0] and "Closure Report" in sent[0][1]
    assert rdw.run([]) == 0 and len(sent) == 1             # not re-alerted
    world.files[2085].append(gone)
    assert rdw.run([]) == 0
    assert len(_rows(fake, "new")) == 1 and len(sent) == 2


def test_empty_recipients_is_display_only_never_the_coalition_list(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["recipients"] = []
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    assert rdw.run([]) == 0
    world.files[2085].append(raw(35715099, "Brand New Report"))
    assert rdw.run([]) == 0
    assert len(_rows(fake, "new")) == 1                    # row still recorded
    assert sent == []                                      # send_email never called (it would fan out to everyone)


def test_send_failure_still_leaves_the_row(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.files[2085].append(raw(35715099))
    monkeypatch.setattr(rdw.ea, "send_email", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp")))
    assert rdw.run([]) == 0
    assert len(_rows(fake, "new")) == 1


def test_default_program_list_is_ride_site_ids_and_can_be_overridden():
    assert rdw._program_nums({"ride": {"site_ids": ["1", "2"]}, "ride_docs": {}}) == ["1", "2"]
    assert rdw._program_nums({"ride": {"site_ids": ["1"]}, "ride_docs": {"program_nums": [9]}}) == ["9"]


def test_unlisted_program_is_skipped_quietly(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    world.locations = {}
    assert rdw.run([]) == 0 and _rows(fake) == []


# --- failure modes ---------------------------------------------------------------


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    n = len(_rows(fake))
    world.list_error = rdc_fetch_error()
    assert rdw.run([]) == 0
    assert len(_rows(fake)) == n + 1 and len(_rows(fake, "fetch-skipped")) == 1   # only the skip note
    assert not _rows(fake, "new") and sent == []                                  # below the liveness threshold


def test_state_counts_consecutive_skips_and_resets_on_recovery():
    _, locs = rdw.build_state([_row("loc:2085", "baseline"), _row("loc:2085", "fetch-skipped"),
                               _row("loc:2085", "fetch-skipped")])
    assert locs["loc:2085"]["skips"] == 2
    _, locs = rdw.build_state([_row("loc:2085", "baseline"), _row("loc:2085", "fetch-skipped"),
                               _row("loc:2085", "fetch-ok"), _row("loc:2085", "fetch-skipped")])
    assert locs["loc:2085"]["skips"] == 1


def test_persistent_outage_sends_exactly_one_liveness_alert_then_recovers(monkeypatch, tmp_path):
    """The silent-death mode: RIDE's bot defense starts challenging the runner AFTER
    the baseline. One alert at the Nth consecutive skipped run (not before, not
    repeatedly), and a later good run records fetch-ok and resets the counter."""
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.list_error = rdc_fetch_error()
    for i in (1, 2):
        assert rdw.run([]) == 0
        assert sent == [], i                                     # below the threshold of 3
    assert rdw.run([]) == 0
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "unreachable for 3 runs" in subj and "2085" in subj and "going UNSEEN" in body
    assert "405 bot challenge" in body and recipients == ["trisha@example.org"]
    assert rdw.run([]) == 0 and len(sent) == 1                   # not repeated while the outage continues
    world.list_error = None
    assert rdw.run([]) == 0
    assert len(_rows(fake, "fetch-ok")) == 1
    world.list_error = rdc_fetch_error()
    assert rdw.run([]) == 0 and len(sent) == 1                   # counter was reset: 1 skip, no new alert
    assert len(_rows(fake, "new")) == 0                          # skips never invent new files
    assert fake.ids == {"PRIV"}


def test_session_level_outage_counts_toward_every_baselined_location(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["stale_alert_after_skips"] = 2
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    assert rdw.run([]) == 0
    world.session_error = rdc_fetch_error()
    assert rdw.run([]) == 0 and sent == []
    assert rdw.run([]) == 0 and len(sent) == 1
    assert len(_rows(fake, "fetch-skipped")) == 2


def test_no_baseline_means_loud_and_no_skip_rows(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    world.list_error = rdc_fetch_error()
    assert rdw.run([]) == 1
    assert _rows(fake, "fetch-skipped") == []


def rdc_fetch_error():
    return rdc.RideDocsFetchError("405 bot challenge")


def test_fetch_failure_without_baseline_is_loud(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    world.list_error = rdc_fetch_error()
    assert rdw.run([]) == 1


def test_parse_error_is_always_loud_even_with_a_baseline(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.list_error = rdc.RideDocsParseError("dup uri")
    assert rdw.run([]) == 1


def test_session_failure_is_skip_after_baseline_and_loud_before(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    world.session_error = rdc_fetch_error()
    assert rdw.run([]) == 1                                # nothing baselined yet
    world.session_error = None
    assert rdw.run([]) == 0                                # baseline
    world.session_error = rdc_fetch_error()
    assert rdw.run([]) == 0                                # skip-and-warn


def test_a_sheet_read_failure_propagates_instead_of_rebaselining(monkeypatch, tmp_path):
    """A swallowed read error would look like 'never baselined' and silently absorb
    genuinely-new files into a fresh baseline. The read must raise."""
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.files[2085].append(raw(35715099, "Would Be Swallowed"))
    fake.read_error = RuntimeError("sheets 503")          # the REAL helper's read fails
    with pytest.raises(RuntimeError):
        rdw.run([])
    assert _rows(fake, "new") == [] and _rows(fake, "baseline")    # nothing re-baselined either
    assert len(_rows(fake, "baseline")) == 3 + 1


# --- probe -------------------------------------------------------------------------


def test_probe_runs_even_when_disabled_and_touches_nothing_else(monkeypatch, tmp_path, capsys):
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg={"ride": {"site_ids": ["81000004"]},
                                                          "ride_docs": {"enabled": False}})
    monkeypatch.setattr(rdw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    monkeypatch.setattr(rdw.rdc, "probe", lambda programs: [
        {"program_num": "81000004", "location_id": 2085, "name": "Arbor Hills - East", "n_files": 37}])
    assert rdw.run(["--probe"]) == 0
    out = capsys.readouterr().out
    assert "location 2085" in out and "37 file(s)" in out and "PROBE OK" in out


def test_probe_fails_when_a_program_does_not_resolve(monkeypatch, tmp_path, capsys):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(rdw.rdc, "probe", lambda programs: [
        {"program_num": "81000004", "location_id": 2085, "name": "A", "n_files": 3},
        {"program_num": "99999999", "location_id": None, "name": "", "n_files": 0}])
    assert rdw.run(["--probe"]) == 1
    out = capsys.readouterr().out
    assert "PROBE FAILED" in out and "99999999" in out and "PROBE OK" not in out


def test_probe_failure_exits_nonzero(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(rdw.rdc, "probe", lambda programs: (_ for _ in ()).throw(rdc_fetch_error()))
    assert rdw.run(["--probe"]) == 1


# --- private Drive mirror ------------------------------------------------------------


def _mirror_env(monkeypatch, folder="RRDFOLDER", **others):
    for k in ("GOAUTH_CLIENT_ID", "GOAUTH_CLIENT_SECRET", "GOAUTH_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv(rdw.FOLDER_ENV, folder)
    for k, v in others.items():
        monkeypatch.setenv(k, v)


def _wire_mirror(monkeypatch, world, fail=None, too_large=()):
    uploads = []

    def _download(session, uri, dest, max_bytes, expect_pdf=True, timeout=300):
        world.downloads.append(uri)
        if uri in too_large:
            raise rdc.RideDocsTooLargeError(f"uri {uri}: too big")
        if fail and uri in fail:
            raise rdc.RideDocsFetchError("boom")
        Path(dest).write_bytes(PDF)
        return {"size": len(PDF), "sha256": "S" * 64, "md5": "M" * 32, "content_type": "application/pdf"}
    monkeypatch.setattr(rdw.rdc, "download_file", _download)
    monkeypatch.setattr(rdw.ac, "oauth_drive_service", lambda: "DRIVE")
    monkeypatch.setattr(rdw.ac, "upload_file",
                        lambda svc, path, name, mime, folder: uploads.append((name, folder)) or f"https://drive/{name}")
    return uploads


def test_mirror_uploads_to_the_private_folder_and_records_hashes(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["mirror"] = True
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    _mirror_env(monkeypatch)
    uploads = _wire_mirror(monkeypatch, world)
    assert rdw.run([]) == 0
    mirrored = _rows(fake, "mirrored")
    assert len(mirrored) == 3 and {u[1] for u in uploads} == {"RRDFOLDER"}
    assert all(r[rdw.C_LINK].startswith("https://drive/") and r[rdw.C_SHA] == "S" * 64
               and r[rdw.C_MD5] == "M" * 32 for r in mirrored)
    assert all(re.fullmatch(r"\d+_[0-9a-f]{8}\.pdf", u[0]) for u in uploads)   # title-free
    assert fake.ids == {"PRIV"}
    n = len(world.downloads)
    assert rdw.run([]) == 0 and len(world.downloads) == n            # already mirrored: no re-download


def test_mirror_is_capped_per_run_and_drains_over_runs(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"].update(mirror=True, max_mirror_per_run=2)
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    _mirror_env(monkeypatch)
    _wire_mirror(monkeypatch, world)
    assert rdw.run([]) == 0 and len(_rows(fake, "mirrored")) == 2
    assert rdw.run([]) == 0 and len(_rows(fake, "mirrored")) == 3


def test_too_large_is_recorded_skipped_and_never_retried(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["mirror"] = True
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    _mirror_env(monkeypatch)
    _wire_mirror(monkeypatch, world, too_large={"34838961"})
    assert rdw.run([]) == 0
    assert [r[rdw.C_KEY] for r in _rows(fake, "mirror-skipped")] == ["rrd:34838961"]
    n = world.downloads.count("34838961")
    assert rdw.run([]) == 0 and world.downloads.count("34838961") == n


def test_a_failing_file_is_retried_then_given_up_on_without_blocking_the_rest(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["mirror"] = True
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    _mirror_env(monkeypatch)
    _wire_mirror(monkeypatch, world, fail={"34838960"})
    for _ in range(4):
        assert rdw.run([]) == 0
    assert len(_rows(fake, "mirrored")) == 2                         # the other two were unaffected
    assert len(_rows(fake, "mirror-failed")) == 2 and len(_rows(fake, "mirror-skipped")) == 1
    assert world.downloads.count("34838960") == 3                    # exactly _MAX_MIRROR_FAILS attempts


def test_mirror_refuses_a_folder_that_equals_another_mirrors_folder(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["mirror"] = True
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    _mirror_env(monkeypatch, folder="SHARED", GOAUTH_ARCHIVE_FOLDER_ID="SHARED")
    _wire_mirror(monkeypatch, world)
    assert rdw.run([]) == 1                                          # loud
    assert world.downloads == [] and _rows(fake, "mirrored") == []
    assert len(_rows(fake, "baseline")) == 3 + 1                     # listing/rows/alerts unaffected


def test_mirror_refuses_the_public_pdf_archive_folder(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["mirror"] = True
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    _mirror_env(monkeypatch, folder="PUBPDF", GDRIVE_FOLDER_ID="PUBPDF")
    _wire_mirror(monkeypatch, world)
    assert rdw.run([]) == 1 and world.downloads == []


def test_mirror_not_configured_is_a_quiet_skip(monkeypatch, tmp_path, capsys):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["mirror"] = True
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    assert rdw.run([]) == 0
    assert "mirror not configured" in capsys.readouterr().out and world.downloads == []


# --- silent-forever paths for a baselined location (review round 1) -----------------


def test_empty_listing_after_baseline_is_a_skip_not_a_mass_removal(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["stale_alert_after_skips"] = 2
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    assert rdw.run([]) == 0
    saved = world.files[2085]
    world.files[2085] = []                                   # totalRows 0
    assert rdw.run([]) == 0
    assert _rows(fake, "removed") == [] and sent == []
    assert "EMPTY" in _rows(fake, "fetch-skipped")[0][rdw.C_NOTE]
    assert rdw.run([]) == 0 and len(sent) == 1               # liveness at the threshold
    assert "UNSEEN" in sent[0][1]
    world.files[2085] = saved
    assert rdw.run([]) == 0
    assert _rows(fake, "new") == [] and len(_rows(fake, "fetch-ok")) == 1   # no re-alert storm


def test_empty_listing_for_a_location_with_no_live_files_is_not_a_skip(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    world.files[2085] = []
    assert rdw.run([]) == 0                                  # baseline of an empty location
    assert rdw.run([]) == 0
    assert _rows(fake, "fetch-skipped") == []


def test_baselined_program_that_stops_resolving_is_skipped_and_alerted(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.locations = {}
    assert rdw.run([]) == 0
    assert len(_rows(fake, "fetch-skipped")) == 1 and len(sent) == 1
    assert "no longer found" in sent[0][0] and "81000004" in sent[0][0]
    assert rdw.run([]) == 0 and len(sent) == 1               # first-occurrence alert is once


def test_program_that_moves_to_a_new_location_is_loud_never_silently_rebaselined(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.locations["81000004"] = dict(LOC, location_id=4242)
    world.files[4242] = three_files() + [raw(35715099, "Genuinely New")]
    assert rdw.run([]) == 1                                  # red
    assert not any(r[rdw.C_KEY] == "loc:4242" for r in _rows(fake))   # NOT baselined
    assert _rows(fake, "new") == [] and len(sent) == 1
    subj, body, _ = sent[0]
    assert "moved to a different RIDE location" in subj and "loc:4242" in body
    assert rdw.run([]) == 1 and len(sent) == 1               # alert once, red every run
    # Trisha accepts the move with the one documented manual row:
    fake._values._tabs[sw.TAB_RRD_DOCS].append(_row("loc:4242", "baseline", lid="4242"))
    assert rdw.run([]) == 0
    new = _rows(fake, "new")
    assert [r[rdw.C_KEY] for r in new] == ["rrd:35715099"]  # the unseen file alerts; known ones don't
    assert "1 new RRD file" in sent[-1][0]


def _move_2085_to_4242(world, fake):
    world.locations["81000004"] = dict(LOC, location_id=4242)
    world.files[4242] = world.files.pop(2085)


def _accept(fake, lid="4242"):
    fake._values._tabs[sw.TAB_RRD_DOCS].append(_row(f"loc:{lid}", "baseline", lid=lid))


def test_after_an_accepted_relocation_removals_are_still_detected(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    _move_2085_to_4242(world, fake)
    assert rdw.run([]) == 1
    _accept(fake)
    assert rdw.run([]) == 0 and _rows(fake, "new") == []    # known files: no noise
    world.files[4242].pop(0)                               # a file first recorded under 2085 vanishes
    assert rdw.run([]) == 0
    assert [r[rdw.C_KEY] for r in _rows(fake, "removed")] == ["rrd:35715058"]
    world.files[4242] = []                                 # and an empty listing is still a skip
    assert rdw.run([]) == 0
    assert len(_rows(fake, "removed")) == 1 and "[empty]" in _rows(fake, "fetch-skipped")[-1][rdw.C_NOTE]


def test_a_superseded_location_never_accrues_skips_or_a_false_liveness_alert(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    _move_2085_to_4242(world, fake)
    assert rdw.run([]) == 1 and rdw.run([]) == 1           # 2085 at 2 skips ([relocated])
    _accept(fake)
    assert rdw.run([]) == 0
    n = len(sent)
    world.session_error = rdc_fetch_error()
    assert rdw.run([]) == 0
    skipped = [r[rdw.C_KEY] for r in _rows(fake, "fetch-skipped")]
    assert skipped[-1] == "loc:4242" and skipped.count("loc:2085") == 2
    assert len(sent) == n                                  # no "2085 unreachable for 3 runs"


def test_a_dropped_program_stops_accruing_skips(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    assert rdw.run([]) == 0
    cfg2 = copy.deepcopy(CFG)
    cfg2["ride"]["site_ids"] = ["81000033"]
    monkeypatch.setattr(rdw, "load_config", lambda: copy.deepcopy(cfg2))
    world.session_error = rdc_fetch_error()
    for _ in range(4):
        rdw.run([])
    assert _rows(fake, "fetch-skipped") == [] and sent == []


def test_a_cause_change_mid_streak_still_sends_the_cause_alert(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.list_error = rdc_fetch_error()
    for _ in range(3):
        rdw.run([])
    assert len(sent) == 1                                  # the generic liveness alert
    world.list_error = None
    _move_2085_to_4242(world, fake)
    assert rdw.run([]) == 1
    assert len(sent) == 2 and "moved to a different RIDE location" in sent[-1][0]
    assert rdw.run([]) == 1 and len(sent) == 2             # same cause: not repeated


def _two_sites(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride"]["site_ids"] = ["81000004", "81000033"]
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    world.locations["81000033"] = dict(LOC, location_id=9549, program_num="81000033")
    world.files[9549] = [raw(40000001, "Salem Doc")]
    return world, fake, sent


def test_a_merge_onto_another_programs_location_is_loud_not_silent(monkeypatch, tmp_path):
    world, fake, sent = _two_sites(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    world.locations["81000004"] = dict(LOC, location_id=9549)      # RIDE merges it into 9549
    assert rdw.run([]) == 1
    assert "moved to a different RIDE location" in sent[-1][0]
    assert _rows(fake, "fetch-skipped")[-1][rdw.C_KEY] == "loc:2085"


def test_a_mistyped_accept_row_does_not_silently_accept(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    _move_2085_to_4242(world, fake)
    assert rdw.run([]) == 1
    fake._values._tabs[sw.TAB_RRD_DOCS].append(_row("loc:4242", "baseline", lid="4242", program="8100004"))
    assert rdw.run([]) == 1                                # still refused (wrong program typed)


def test_a_flapping_relocation_alert_is_not_resent(monkeypatch, tmp_path):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    _move_2085_to_4242(world, fake)
    assert rdw.run([]) == 1 and len(sent) == 1
    world.session_error = rdc_fetch_error()
    rdw.run([])                                            # [fetch] in the same streak
    world.session_error = None
    assert rdw.run([]) == 1
    relocation_alerts = [x for x in sent if "moved to a different" in x[0]]
    assert len(relocation_alerts) == 1
    assert "Accept it" in sent[-1][1]                      # the 3rd skip's liveness copy fits the cause


@pytest.mark.parametrize("order", ["A-first", "B-first"])
def test_a_uri_moving_between_watched_locations_is_moved_not_removed_then_new(monkeypatch, tmp_path, order):
    world, fake, sent = _two_sites(monkeypatch, tmp_path)
    if order == "B-first":
        world.locations = {"81000033": world.locations["81000033"], "81000004": world.locations["81000004"]}
        cfg = copy.deepcopy(CFG)
        cfg["ride"]["site_ids"] = ["81000033", "81000004"]
        monkeypatch.setattr(rdw, "load_config", lambda: copy.deepcopy(cfg))
    assert rdw.run([]) == 0
    world.files[9549].append(world.files[2085].pop(0))
    assert rdw.run([]) == 0 and rdw.run([]) == 0
    assert sent == [] and _rows(fake, "removed") == [] and _rows(fake, "new") == []
    assert [(r[rdw.C_KEY], r[rdw.C_LOC]) for r in _rows(fake, "moved")] == [("rrd:35715058", "9549")]
    world.files[9549].pop()                                # it later vanishes from its NEW location
    assert rdw.run([]) == 0
    assert [r[rdw.C_KEY] for r in _rows(fake, "removed")] == ["rrd:35715058"]


def test_a_uri_moving_between_watched_locations_in_one_run_is_not_double_alerted(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride"]["site_ids"] = ["81000004", "81000033"]
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    world.locations["81000033"] = dict(LOC, location_id=9549, program_num="81000033")
    world.files[9549] = [raw(40000001, "Salem Doc")]
    assert rdw.run([]) == 0
    world.files[9549].append(world.files[2085].pop(0))     # A (2085) is processed first
    assert rdw.run([]) == 0
    n_alerts = len(sent)
    assert rdw.run([]) == 0 and len(sent) == n_alerts      # no "reappeared" alert the next run


def test_subjects_and_logs_carry_ids_not_ride_facility_names(monkeypatch, tmp_path, capsys):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    world.locations["81000004"]["name"] = "123 Private Lane"
    assert rdw.run([]) == 0
    world.files[2085].append(raw(35715099, "Brand New Report"))
    assert rdw.run([]) == 0
    subj, body, _ = sent[0]
    assert "Private Lane" not in subj and "Private Lane" in body
    assert "Private Lane" not in capsys.readouterr().out


def test_session_failure_with_an_unbaselined_program_still_records_skips(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride"]["site_ids"] = ["81000004", "81000033"]
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    assert rdw.run([]) == 0                                  # 81000033 not in the World: unbaselined
    world.session_error = rdc_fetch_error()
    assert rdw.run([]) == 1                                  # loud: one program has no baseline
    assert len(_rows(fake, "fetch-skipped")) == 1            # ...but the baselined one still counts


def test_stale_threshold_zero_cannot_disable_liveness(monkeypatch, tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["stale_alert_after_skips"] = 0
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    assert rdw.run([]) == 0
    world.list_error = rdc_fetch_error()
    assert rdw.run([]) == 0 and len(sent) == 1


def test_smtp_not_configured_is_reported_not_silent(monkeypatch, tmp_path, capsys):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    assert rdw.run([]) == 0
    monkeypatch.setattr(rdw.ea, "send_email", lambda *a, **k: False)
    world.files[2085].append(raw(35715099))
    assert rdw.run([]) == 0
    assert "NOT SENT" in capsys.readouterr().out


def test_refuses_a_private_id_that_points_at_the_public_sheet(monkeypatch, tmp_path):
    """The CI-effective guard: the workflow never receives GSHEET_ID, so the env
    comparison can't fire there — but the public Sheet's own tabs give it away."""
    world, fake, sent = _wire(monkeypatch, tmp_path)
    monkeypatch.delenv("GSHEET_ID")
    fake._values._tabs[sw.TAB_NEW] = [["header"]]
    assert rdw.run([]) == 1
    assert sw.TAB_RRD_DOCS not in fake._values._tabs and sent == []


# --- the Actions log is public: nothing resident-derived may reach it --------------

SENSITIVE = "Jane Q Resident 123 Elm St"


def _http_error(uri):
    import httplib2
    from googleapiclient.errors import HttpError
    return HttpError(httplib2.Response({"status": 404}), b'{"error": {"message": "File not found"}}', uri=uri)


def test_a_drive_error_carrying_a_title_never_reaches_stdout_or_stderr(monkeypatch, tmp_path, capsys):
    cfg = copy.deepcopy(CFG)
    cfg["ride_docs"]["mirror"] = True
    world, fake, sent = _wire(monkeypatch, tmp_path, cfg=cfg)
    world.files[2085] = [raw(35715058, SENSITIVE)]
    _mirror_env(monkeypatch)
    _wire_mirror(monkeypatch, world)
    q = f"https://www.googleapis.com/drive/v3/files?q=name+%3D+%27{SENSITIVE}%27"

    def _boom(*a, **k):
        raise _http_error(q)
    monkeypatch.setattr(rdw.ac, "upload_file", _boom)
    monkeypatch.setattr(rdw.sys, "argv", ["ride_docs_watcher.py"])
    assert rdw.main() == 0
    out = capsys.readouterr()
    assert "HttpError (HTTP 404)" in out.out
    assert "Resident" not in out.out + out.err and "Elm" not in out.out + out.err
    assert len(_rows(fake, "mirror-failed")) == 1                  # the detail went to the PRIVATE Sheet only


def test_main_reports_an_unhandled_error_as_its_class_only(monkeypatch, tmp_path, capsys):
    world, fake, sent = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(rdw.dc, "sheets_service",
                        lambda: (_ for _ in ()).throw(_http_error(f"https://x/?q={SENSITIVE}")))
    monkeypatch.setattr(rdw.sys, "argv", ["ride_docs_watcher.py"])
    assert rdw.main() == 1
    out = capsys.readouterr()
    assert "FAILED: HttpError" in out.out and "Resident" not in out.out + out.err


def test_main_silences_the_googleapiclient_retry_logger(monkeypatch, tmp_path):
    import logging
    world, fake, sent = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(rdw.sys, "argv", ["ride_docs_watcher.py"])
    loggers = [logging.getLogger(n) for n in ("googleapiclient", "googleapiclient.http")]
    before = [lg.level for lg in loggers]
    try:
        rdw.main()
        assert not logging.getLogger("googleapiclient.http").isEnabledFor(logging.WARNING)
    finally:
        for lg, lvl in zip(loggers, before):
            lg.setLevel(lvl)


def test_no_print_interpolates_a_raw_exception_or_a_title():
    """AST pin: every print() in the watcher interpolates no bare exception variable
    (only _err(e)) and no view title/file name."""
    import ast
    tree = ast.parse((ROOT / "ride_docs_watcher.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            for fv in (n for a in node.args for n in ast.walk(a) if isinstance(n, ast.FormattedValue)):
                src = ast.unparse(fv.value)
                assert src not in ("e", "err", "str(e)", "str(err)"), src
                assert "title" not in src and "file_name" not in src, src


# ==============================================================================
# HARD RULE: never publish (ADR 061)
# ==============================================================================

_NEW = ("ride_docs_client.py", "ride_docs_watcher.py")


def _code_references(path):
    """Imported module names + every referenced identifier in a source file, from
    the AST (so a docstring/comment that STATES the rule doesn't trip the pin)."""
    import ast
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


def test_no_path_reaches_the_public_feed_or_the_public_sheet():
    """Static pin: neither new module imports or references the findings feed /
    the other mirrors' archivers, and the ONLY string reference to the public
    GSHEET_ID is the fail-closed comparison inside _private_sheet_id()."""
    forbidden = {"findings_feed", "gen_findings_feed", "archiver", "wds_archiver",
                 "mmpc_archiver", "civicclerk_archiver", "ridgewood_archiver", "public_comment_feed"}
    for name in _NEW:
        assert not (_code_references(name) & forbidden), name
    watcher = (ROOT / "ride_docs_watcher.py").read_text()
    guard = inspect.getsource(rdw._private_sheet_id)
    remainder = watcher.replace(guard, "")
    code_only = re.sub(r'""".*?"""', "", remainder, flags=re.S)          # docstrings state the rule
    code_only = re.sub(r"#.*", "", code_only)                             # ... and so do comments
    assert not re.search(r"GSHEET_ID(?!_PRIVATE)", code_only)
    assert re.search(r'GSHEET_ID"\)', guard)                             # the guard compares against it


def test_workflow_never_receives_the_public_sheet_id_and_ships_disabled():
    wf = (ROOT / ".github" / "workflows" / "ride-docs-watch.yml").read_text()
    assert "secrets.GSHEET_ID_PRIVATE" in wf
    assert not re.search(r"secrets\.GSHEET_ID\s*\}\}", wf)
    from config_loader import load_config
    assert load_config()["ride_docs"]["enabled"] is False


def test_shipped_config_recipients_are_scoped_and_program_default_is_ride_sites():
    from config_loader import load_config
    cfg = load_config()
    assert cfg["ride_docs"]["recipients"] == ["arbor-hills@trishakunst.com"]
    assert rdw._program_nums(cfg) == [str(s) for s in cfg["ride"]["site_ids"]]


def test_workflow_passes_every_other_mirror_folder_id_for_the_equality_guard():
    """The folder guard only protects against folder ids it can SEE. Every
    GOAUTH_*_FOLDER_ID any other workflow uses must be passed to this one."""
    wf_dir = ROOT / ".github" / "workflows"
    mine = (wf_dir / "ride-docs-watch.yml").read_text()
    others = set()
    for f in wf_dir.glob("*.yml"):
        if f.name != "ride-docs-watch.yml":
            others |= set(re.findall(r"\b(GOAUTH_[A-Z0-9_]+_FOLDER_ID|GDRIVE_FOLDER_ID)\s*:", f.read_text()))
    passed = set(re.findall(r"\b(GOAUTH_[A-Z0-9_]+_FOLDER_ID|GDRIVE_FOLDER_ID)\s*:", mine))
    assert others and others <= passed, sorted(others - passed)
