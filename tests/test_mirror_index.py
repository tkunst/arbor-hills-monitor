"""mirror_index (ADR 073): the public Mirror Index tab — pure row building,
strict reads, upsert by drive_id, and the never-raises record() wrapper; plus
archive_client's flag-off/flag-on behavior and which uploaders may index."""
import inspect

import pytest

import archive_client as ac
import mirror_index as mi
import ocr_util
from ocr_util import OcrStats
from test_pfas_watcher import FakeSheets

ON = {"mirror_index": {"enabled": True}}


@pytest.fixture(autouse=True)
def _fresh_ensure_cache():
    mi._ENSURED.clear()
    yield
    mi._ENSURED.clear()
OFF = {"mirror_index": {"enabled": False}}


def _stats(pages=3, image_only=(), engine="none"):
    fz = frozenset(image_only)
    return OcrStats(sha256="", pages=pages, image_only_pages=fz, image_only_before=fz,
                    ocr_ran=False, engine=engine)


META = {"id": "D1", "webViewLink": "http://drive/D1", "sha256Checksum": "a" * 64,
        "md5Checksum": "b" * 32, "size": "1234", "headRevisionId": "R1"}


# --- pure --------------------------------------------------------------------

@pytest.mark.parametrize("stats,expected", [
    (None, "unknown"),
    (_stats(pages=0), "unknown"),
    (_stats(), "native"),
    (_stats(engine="OCRmyPDF 17.12.1 / x"), "ocr_full"),
    (_stats(pages=3, image_only={0, 1, 2}), "no_text"),
    (_stats(pages=3, image_only={2}), "ocr_partial"),
    (_stats(pages=3, image_only={2}, engine="OCRmyPDF 17"), "ocr_partial"),
    (OcrStats("", 3, None, None, False, "none"), "unknown"),
])
def test_text_status_table(stats, expected):
    assert mi.text_status(stats) == expected


def test_build_row_in_header_order():
    row = mi.build_row(META, _stats(pages=5, image_only={4}), source_kind="nsite",
                       source_key="-123", original_sha256="", now="T")
    assert len(row) == len(mi.HEADERS)
    d = dict(zip(mi.HEADERS, row))
    assert d["drive_id"] == "D1" and d["source_kind"] == "nsite" and d["source_key"] == "-123"
    assert d["mirror_sha256"] == "a" * 64 and d["mirror_md5"] == "b" * 32
    assert d["size"] == "1234" and d["head_revision"] == "R1"
    assert d["pages"] == 5 and d["image_only_pages"] == 1 and d["text_status"] == "ocr_partial"
    assert d["ocr_engine"] == "none" and d["original_sha256"] == "" and d["indexed_at"] == "T"


def test_build_row_missing_checksums_are_blank_never_guessed():
    row = mi.build_row({"id": "D2"}, None, source_kind="mmpc", source_key="7", now="T")
    d = dict(zip(mi.HEADERS, row))
    assert d["mirror_sha256"] == "" and d["mirror_md5"] == "" and d["text_status"] == "unknown"
    assert d["pages"] == "" and d["image_only_pages"] == ""


def test_latest_by_drive_id_last_row_wins():
    rows = [["D1", "nsite", "1", "old"], ["D2", "mmpc", "2", "x"], ["D1", "nsite", "1", "new"], []]
    got = mi.latest_by_drive_id(rows)
    assert got["D1"]["mirror_sha256"] == "new" and set(got) == {"D1", "D2"}


def test_target_is_none_when_disabled_and_checks_kind():
    s = FakeSheets()
    assert mi.target(OFF, s, "SID", "nsite", "1") is None
    assert mi.target({}, s, "SID", "nsite", "1") is None
    assert mi.target(None, s, "SID", "nsite", "1") is None
    t = mi.target(ON, s, "SID", "mmpc", 42, local_is_source=True)
    assert t.source_key == "42" and t.local_is_source is True and t.tab == mi.TAB
    with pytest.raises(ValueError):
        mi.target(ON, s, "SID", "govqa", "1")


# --- sheet I/O ---------------------------------------------------------------

def test_upsert_creates_tab_appends_then_updates_in_place():
    s = FakeSheets()
    r1 = mi.build_row(META, _stats(), source_kind="nsite", source_key="1", now="T1")
    mi.upsert_row(s, "SID", r1, tab="Mirror Index TEST")
    other = mi.build_row(dict(META, id="D9"), _stats(), source_kind="nsite", source_key="9", now="T1")
    mi.upsert_row(s, "SID", other, tab="Mirror Index TEST")
    r2 = mi.build_row(dict(META, sha256Checksum="c" * 64), _stats(), source_kind="nsite",
                      source_key="1", now="T2")
    mi.upsert_row(s, "SID", r2, tab="Mirror Index TEST")
    tab = s._values._tabs["Mirror Index TEST"]
    assert tab[0] == mi.HEADERS and len(tab) == 3      # header + 2 rows, no duplicate
    idx = mi.read_index(s, "SID", tab="Mirror Index TEST")
    assert idx["D1"]["mirror_sha256"] == "c" * 64 and idx["D1"]["indexed_at"] == "T2"
    assert mi.TAB not in s._values._tabs                # the real tab name is untouched


def test_upsert_updates_the_real_row_even_with_a_blank_row_above():
    s = FakeSheets()
    s._values._tabs[mi.TAB] = [list(mi.HEADERS), [], ["D1", "nsite", "1", "old"]]
    mi.upsert_row(s, "SID", mi.build_row(META, None, source_kind="nsite", source_key="1", now="T"))
    tab = s._values._tabs[mi.TAB]
    assert tab[1] == [] and tab[2][0] == "D1" and tab[2][3] == "a" * 64 and len(tab) == 3


class _BoomSheets(FakeSheets):
    def __init__(self):
        super().__init__()
        self._values._tabs[mi.TAB] = [list(mi.HEADERS)]
        orig = self._values.get

        def get(spreadsheetId, range):
            raise RuntimeError("503 backend error")
        self._values.get = get
        self._orig = orig


def test_read_index_raises_on_transport_failure():
    with pytest.raises(RuntimeError):
        mi.read_index(_BoomSheets(), "SID")


def test_upsert_never_appends_blind_after_a_failed_read():
    s = _BoomSheets()
    with pytest.raises(RuntimeError):
        mi.upsert_row(s, "SID", mi.build_row(META, None, source_kind="nsite", source_key="1"))
    assert s._values._tabs[mi.TAB] == [list(mi.HEADERS)]


def test_record_never_raises_and_logs_class_only(caplog):
    t = mi.IndexTarget(_BoomSheets(), "SID", "nsite", "secret-doc-name")
    assert mi.record(t, META, _stats()) is False
    assert "RuntimeError" in caplog.text
    assert "secret-doc-name" not in caplog.text and "D1" not in caplog.text and "503" not in caplog.text
    assert mi.record(None, META, _stats()) is False
    assert mi.record(mi.IndexTarget(FakeSheets(), "SID", "nsite", "1"), {}, None) is False


# --- archive_client wiring ---------------------------------------------------

class _Files:
    def __init__(self, result):
        self.result, self.create_fields, self.update_fields = result, [], []

    def list(self, **kw):
        return _Exec({"files": []})

    def create(self, body, media_body, fields):
        self.create_fields.append(fields)
        return _Exec(self.result)

    def update(self, fileId, media_body, fields):
        self.update_fields.append(fields)
        return _Exec(self.result)


class _Exec:
    def __init__(self, r):
        self.r = r

    def execute(self, **kw):
        return self.r


class _Drive:
    def __init__(self, result):
        self._f = _Files(result)

    def files(self):
        return self._f


def test_upload_flag_off_is_unchanged(text_pdf, monkeypatch):
    calls = []
    monkeypatch.setattr(mi, "record", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(ocr_util, "file_sha256", lambda p: calls.append("hash") or "x")
    drive = _Drive({"id": "D1", "webViewLink": "http://drive/D1"})
    assert ac.upload_pdf(drive, text_pdf, "a.pdf", "F") == "http://drive/D1"
    assert drive._f.create_fields == ["id, webViewLink"]   # same request as before ADR 073
    assert calls == []                                       # no hashing, no index write


def test_upload_flag_on_records_drive_checksums_and_stats(text_pdf):
    s = FakeSheets()
    drive = _Drive(META)
    t = mi.target(ON, s, "SID", "mmpc", "77", local_is_source=True)
    assert ac.upload_pdf(drive, text_pdf, "a.pdf", "F", index=t) == "http://drive/D1"
    assert drive._f.create_fields == [mi.DRIVE_FIELDS]
    row = mi.read_index(s, "SID")["D1"]
    assert row["source_kind"] == "mmpc" and row["mirror_sha256"] == "a" * 64
    import fitz
    with fitz.open(text_pdf) as d:
        n = len(d)
    assert row["text_status"] == "native" and str(row["pages"]) == str(n)
    assert row["original_sha256"] == ocr_util.file_sha256(text_pdf)


def test_upload_flag_on_nsite_leaves_original_sha_blank(text_pdf):
    s = FakeSheets()
    t = mi.target(ON, s, "SID", "nsite", "-5")
    ac.upload_pdf(_Drive(META), text_pdf, "a.pdf", "F", index=t)
    assert mi.read_index(s, "SID")["D1"]["original_sha256"] == ""


def test_index_write_failure_never_fails_the_upload(text_pdf):
    t = mi.IndexTarget(_BoomSheets(), "SID", "nsite", "1")
    assert ac.upload_pdf(_Drive(META), text_pdf, "a.pdf", "F", index=t) == "http://drive/D1"


def test_non_pdf_upload_is_never_indexed(tmp_path):
    local = tmp_path / "p.html"
    local.write_text("<html></html>")
    s = FakeSheets()
    drive = _Drive(META)
    ac.upload_file(drive, str(local), "p.html", "text/html", "F",
                   index=mi.target(ON, s, "SID", "nsite", "1"))
    assert drive._f.create_fields == ["id, webViewLink"] and mi.TAB not in s._values._tabs


def test_update_file_content_flag_off_unchanged_and_on_records(text_pdf):
    drive = _Drive({"id": "D1"})
    assert ac.update_file_content(drive, "D1", text_pdf) == "D1"
    assert drive._f.update_fields == ["id"]
    s = FakeSheets()
    drive = _Drive(dict(META))
    ac.update_file_content(drive, "D1", text_pdf, index=mi.target(ON, s, "SID", "nsite", "1"),
                           ocr_stats=_stats(pages=1, engine="OCRmyPDF 17"), raw_revision_pinned="R0")
    assert "webViewLink" not in drive._f.update_fields[0] and "sha256Checksum" in drive._f.update_fields[0]
    row = mi.read_index(s, "SID")["D1"]
    assert row["text_status"] == "ocr_full" and row["raw_revision_pinned"] == "R0"


@pytest.mark.parametrize("modname", [
    "govqa_watcher", "ride_docs_watcher", "gfl_air_watcher", "gfl_feed_snapshot",
    "snapshot_exporter", "wds_archiver", "ridgewood_archiver", "ridgewood_reverify",
])
def test_private_and_out_of_scope_uploaders_never_index(modname):
    # ADR 073: only nSITE / MMPC / CivicClerk PDFs are indexed. GovQA and RIDE
    # documents are private; the rest are non-PDF or out of scope.
    import importlib
    src = inspect.getsource(importlib.import_module(modname))
    assert "mirror_index" not in src and "index=" not in src


def test_archiver_builds_nsite_target_only_when_enabled(monkeypatch, tmp_path):
    import archiver as av
    seen = []
    monkeypatch.setattr(av.ac, "upload_pdf", lambda *a, **k: seen.append(k.get("index")) or "L")
    monkeypatch.setattr(av.ac, "folder_id", lambda: "F")
    monkeypatch.setattr(av.sw, "append_archive_row", lambda *a, **k: None)
    doc = {"doc_id": "-42", "doc_url": "u"}
    s = FakeSheets()
    av._mirror_and_record(None, None, s, "SID", doc, "n", "d", [], "u", local_path="x", index_cfg=OFF)
    av._mirror_and_record(None, None, s, "SID", doc, "n", "d", [], "u", local_path="x", index_cfg=ON)
    assert seen[0] is None
    assert seen[1].source_kind == "nsite" and seen[1].source_key == "-42"
    assert seen[1].local_is_source is False


def test_upsert_on_a_header_only_tab_appends():
    s = FakeSheets()
    s._values._tabs[mi.TAB] = [list(mi.HEADERS)]
    mi.upsert_row(s, "SID", mi.build_row(META, None, source_kind="nsite", source_key="1", now="T"))
    assert len(s._values._tabs[mi.TAB]) == 2


def test_ensure_tab_runs_once_per_sheets_client(monkeypatch):
    import sheet_writer as sw
    calls = []
    real = sw.ensure_tab
    monkeypatch.setattr(sw, "ensure_tab", lambda *a, **k: calls.append(1) or real(*a, **k))
    s = FakeSheets()
    for i in range(3):
        mi.upsert_row(s, "SID", mi.build_row(dict(META, id=f"D{i}"), None,
                                             source_kind="nsite", source_key=str(i), now="T"))
    assert calls == [1]


def test_content_replacement_keeps_the_upload_time_original_sha(text_pdf):
    s = FakeSheets()
    t = mi.target(ON, s, "SID", "civicclerk", "5", local_is_source=True)
    ac.upload_pdf(_Drive(META), text_pdf, "a.pdf", "F", index=t)
    first = mi.read_index(s, "SID")["D1"]["original_sha256"]
    assert first == ocr_util.file_sha256(text_pdf)
    # The OCR backfill replaces the content with a DIFFERENT file; even with a
    # local_is_source target, the replacement is never recorded as the source.
    other = text_pdf + ".ocr.pdf"
    import shutil
    shutil.copyfile(text_pdf, other)
    with open(other, "ab") as fh:
        fh.write(b"%extra")
    ac.update_file_content(_Drive(dict(META, sha256Checksum="c" * 64)), "D1", other, index=t,
                           ocr_stats=_stats(pages=1, engine="OCRmyPDF 17"))
    row = mi.read_index(s, "SID")["D1"]
    assert row["mirror_sha256"] == "c" * 64 and row["original_sha256"] == first
    assert len(s._values._tabs[mi.TAB]) == 2


def test_mirror_one_now_passes_the_callers_cfg(monkeypatch):
    import archiver as av
    got = []
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: object())
    monkeypatch.setattr(av.sw, "archived_doc_links", lambda *a: {})
    monkeypatch.setattr(av, "_mirror_and_record", lambda *a, **k: got.append(k.get("index_cfg")) or "L")
    av.mirror_one_now(None, FakeSheets(), "SID", {"doc_id": "1", "doc_url": "u"}, {}, index_cfg=ON)
    assert got == [ON]


@pytest.mark.parametrize("modname,kind", [("mmpc_archiver", "mmpc"), ("civicclerk_archiver", "civicclerk")])
def test_meeting_archivers_build_a_source_target(modname, kind):
    import importlib
    src = inspect.getsource(importlib.import_module(modname).run)
    assert f'mi.target(cfg, sheets, sheet_id, "{kind}", file_id, local_is_source=True)' in src
    assert "index=index" in src


# --- readers' half of the fail-safe rule (CODER-PLAN step 5) -------------------

class _FakeBatch:
    def __init__(self, svc, callback):
        self.svc, self.cb, self.calls = svc, callback, []

    def add(self, req, request_id):
        self.calls.append((req, request_id))

    def execute(self):
        self.svc.batches.append([rid for _, rid in self.calls])
        if self.svc.fail_batch is not None and len(self.svc.batches) - 1 == self.svc.fail_batch:
            raise RuntimeError("batch failed")
        for req, rid in self.calls:
            if rid in self.svc.errors:
                self.cb(rid, None, RuntimeError("404"))
            else:
                self.cb(rid, {"id": rid, "sha256Checksum": self.svc.shas.get(rid, "")}, None)


class _FakeDrive:
    def __init__(self, shas, errors=(), fail_batch=None):
        self.shas, self.errors, self.fail_batch, self.batches = shas, set(errors), fail_batch, []

    def new_batch_http_request(self, callback):
        return _FakeBatch(self, callback)

    def files(self):
        return self

    def get(self, fileId, fields):
        assert "sha256Checksum" in fields
        return ("get", fileId)


def test_live_sha256_batches_and_drops_failed_or_blank_lookups():
    drive = _FakeDrive({"a": "SA", "b": "SB", "c": "", "d": "SD"}, errors={"b"})
    got = mi.live_sha256(drive, ["a", "b", "c", "d", "a", ""], batch_size=2)
    assert got == {"a": "SA", "d": "SD"}             # b failed, c had no sha
    assert drive.batches == [["a", "b"], ["c", "d"]]  # deduplicated, blank id dropped


def test_live_sha256_stops_after_a_failed_batch_and_never_raises():
    drive = _FakeDrive({"a": "SA", "b": "SB", "c": "SC"}, fail_batch=1)
    assert mi.live_sha256(drive, ["a", "b", "c"], batch_size=1) == {"a": "SA"}
    assert len(drive.batches) == 2
    assert mi.live_sha256(None, ["a"]) == {} and mi.live_sha256(drive, []) == {}


@pytest.mark.parametrize("row,live,expected", [
    ({"mirror_sha256": "S"}, "S", True),
    ({"mirror_sha256": "S"}, "T", False),
    ({"mirror_sha256": ""}, "", False),
    ({"mirror_sha256": ""}, "S", False),
    ({"mirror_sha256": "S"}, None, False),
    (None, "S", False),
])
def test_matches_live_is_the_only_skip(row, live, expected):
    assert mi.matches_live(row, live) is expected
