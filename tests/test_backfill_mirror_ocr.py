"""scripts/backfill_mirror_ocr.py (ADR 069): the one-time OCR backfill for
already-mirrored scan-only PDFs. Loaded the same way
test_backfill_document_dates_titles.py loads its script module — by file path,
not via sys.path package import, matching this repo's established convention
for the one-off scripts/ tools."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "backfill_mirror_ocr",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "backfill_mirror_ocr.py"),
)
bm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bm)

import archive_client as ac  # noqa: E402
import ocr_util  # noqa: E402
import sheet_writer as sw  # noqa: E402


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_file_id_from_file_d_form():
    link = "https://drive.google.com/file/d/ABC123_-xyz/view?usp=drivesdk"
    assert bm.extract_file_id(link) == "ABC123_-xyz"


def test_extract_file_id_from_query_id_form():
    assert bm.extract_file_id("https://drive.google.com/open?id=XYZ789") == "XYZ789"
    assert bm.extract_file_id("https://drive.google.com/uc?id=XYZ999&export=download") == "XYZ999"


def test_extract_file_id_none_for_blank_or_unrecognized():
    assert bm.extract_file_id("") is None
    assert bm.extract_file_id(None) is None
    assert bm.extract_file_id("not a drive link at all") is None


def test_state_round_trip(tmp_path):
    path = str(tmp_path / "state.json")
    state = {}
    bm._record(state, "k1", "done", link="http://x", tab="Archived PDFs")
    bm.save_state(state, path=path)
    loaded = bm.load_state(path=path)
    assert loaded["k1"]["status"] == "done"
    assert loaded["k1"]["link"] == "http://x"


def test_load_state_empty_when_file_absent(tmp_path):
    assert bm.load_state(path=str(tmp_path / "nope.json")) == {}


# ---------------------------------------------------------------------------
# enumerate_tab — Sheets reads only, no Drive call
# ---------------------------------------------------------------------------


class _Req:
    def __init__(self, result, raise_on_execute=None):
        self._result = result
        self._raise = raise_on_execute

    def execute(self, num_retries=0):
        if self._raise:
            raise self._raise
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs  # {tab_name: [[row], [row], ...]} including header row 0

    def get(self, spreadsheetId, range):
        import re
        tab = re.match(r"'([^']+)'", range).group(1)
        if tab not in self._tabs:
            return _Req(None, raise_on_execute=RuntimeError(f"Unable to parse range for tab {tab!r}"))
        return _Req({"values": self._tabs[tab]})


class FakeSheets:
    def __init__(self, tabs):
        self._values = _Values(tabs)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


def test_enumerate_tab_returns_rows_with_id_and_link():
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "Letter", "2026-01-01", "R1", "src1", "http://drive/file/d/FID1/view", "ts"],
            ["doc-2", "Memo", "2026-01-02", "", "src2", "http://drive/file/d/FID2/view", "ts"],
        ],
    }
    out = bm.enumerate_tab(FakeSheets(tabs), "SID", sw.TAB_ARCHIVE, "Doc ID", "Archive Link")
    assert [c["id"] for c in out] == ["doc-1", "doc-2"]
    assert out[0]["key"] == f"{sw.TAB_ARCHIVE}:doc-1"
    assert out[0]["link"].endswith("FID1/view")


def test_enumerate_tab_skips_rows_with_no_link():
    tabs = {
        sw.TAB_MMPC_ARCHIVE: [
            sw.MMPC_ARCHIVE_HEADERS,
            [9000, "2026-01-01", "Agenda", "n", 123, "", "ts"],   # upload never succeeded -- no link
            [9001, "2026-01-02", "Minutes", "n", 123, "http://drive/file/d/FID3/view", "ts"],
        ],
    }
    out = bm.enumerate_tab(FakeSheets(tabs), "SID", sw.TAB_MMPC_ARCHIVE, "File ID", "Archive Link")
    assert [c["id"] for c in out] == ["9001"]


def test_enumerate_tab_ridgewood_revisions_keys_by_row_not_just_month():
    # "Month" is not unique in Ridge Wood Revisions -- two events can share a
    # month. Keys must differ so neither silently shadows the other in state.
    tabs = {
        sw.TAB_RIDGEWOOD_REVISIONS: [
            sw.RIDGEWOOD_REVISIONS_HEADERS,
            ["2026-05", "revised", "t1", "h0", "h1", "http://drive/file/d/A/view", "u", "note1"],
            ["2026-05", "revised", "t2", "h1", "h2", "http://drive/file/d/B/view", "u", "note2"],
        ],
    }
    out = bm.enumerate_tab(FakeSheets(tabs), "SID", sw.TAB_RIDGEWOOD_REVISIONS,
                           "Month", "New Archive Link")
    assert len(out) == 2
    assert out[0]["key"] != out[1]["key"]


def test_enumerate_tab_missing_tab_returns_empty_not_raise():
    out = bm.enumerate_tab(FakeSheets({}), "SID", sw.TAB_CIVICCLERK_ARCHIVE, "File ID", "Archive Link")
    assert out == []


# ---------------------------------------------------------------------------
# dry_run_report — must make ZERO Drive calls (no `drive` argument exists at
# all in its signature or body — this is the structural proof)
# ---------------------------------------------------------------------------


def test_dry_run_report_signature_has_no_drive_parameter():
    import inspect
    params = inspect.signature(bm.dry_run_report).parameters
    assert "drive" not in params


def test_dry_run_report_counts_candidates_and_resolved():
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "http://drive/file/d/F1/view", "ts"],
            ["doc-2", "n", "d", "", "s", "http://drive/file/d/F2/view", "ts"],
        ],
    }
    state = {f"{sw.TAB_ARCHIVE}:doc-1": {"status": "done"}}
    report = bm.dry_run_report(FakeSheets(tabs), "SID", state)
    assert "ZERO Drive API calls" in report
    assert "2 candidate row(s)" in report.replace("    ", " ")  # tolerate column padding
    assert "1 " in report  # the resolved count appears somewhere


# ---------------------------------------------------------------------------
# process_candidate — never raises; every outcome is a recorded status
# ---------------------------------------------------------------------------


def _fake_drive():
    return object()  # process_candidate never touches `drive` directly -- only via ac.* calls


def test_process_candidate_bad_link_is_recorded_without_any_download(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ac, "download_file", lambda *a, **k: calls.append(1))
    result = bm.process_candidate(_fake_drive(), {"link": "not a drive link"}, str(tmp_path))
    assert result["status"] == "error_bad_link"
    assert calls == []


def test_process_candidate_download_failure_is_recorded(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("network blip")
    monkeypatch.setattr(ac, "download_file", boom)
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "error_download"


def test_process_candidate_skip_has_text_no_ocr_no_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "download_file", lambda drive, fid, dest: open(dest, "wb").write(b"x") and dest)
    monkeypatch.setattr(ocr_util, "needs_ocr", lambda path: False)
    pin_calls, update_calls = [], []
    monkeypatch.setattr(ac, "pin_head_revision", lambda *a, **k: pin_calls.append(1))
    monkeypatch.setattr(ac, "update_file_content", lambda *a, **k: update_calls.append(1))
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "skip_has_text"
    assert pin_calls == [] and update_calls == []   # never touches Drive write surface


def test_process_candidate_malformed_pdf_recorded_not_retried_logic(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "download_file", lambda drive, fid, dest: open(dest, "wb").write(b"x") and dest)

    def boom(path):
        raise RuntimeError("fitz: cannot open broken document")
    monkeypatch.setattr(ocr_util, "needs_ocr", boom)
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "error_malformed"


def test_process_candidate_ocr_failed_fail_open_never_uploads(monkeypatch, tmp_path):
    local_holder = {}

    def fake_download(drive, fid, dest):
        open(dest, "wb").write(b"x")
        local_holder["path"] = dest
        return dest
    monkeypatch.setattr(ac, "download_file", fake_download)
    monkeypatch.setattr(ocr_util, "needs_ocr", lambda path: True)
    # ensure_searchable is fail-open: returns the SAME path on OCR failure.
    monkeypatch.setattr(ocr_util, "ensure_searchable", lambda path, **kw: local_holder["path"])
    update_calls = []
    monkeypatch.setattr(ac, "update_file_content", lambda *a, **k: update_calls.append(1))
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "ocr_failed"
    assert update_calls == []          # never re-uploaded a file that wasn't actually fixed


def test_process_candidate_done_pins_revision_before_update_never_creates(monkeypatch, tmp_path):
    # The handoff's own test requirement: --apply calls files().update with
    # the original id, never create. update_file_content()/pin_head_revision()
    # are archive_client functions that, by construction, never call
    # files().create() at all -- this proves the ORDER (pin before update)
    # and that the SAME file id flows through every step.
    call_order = []

    def fake_download(drive, fid, dest):
        call_order.append(("download", fid))
        import fitz
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "real text now")
        doc.save(dest)
        doc.close()
        return dest
    monkeypatch.setattr(ac, "download_file", fake_download)

    needs = {"n": 0}
    def fake_needs_ocr(path):
        needs["n"] += 1
        return needs["n"] == 1   # first check (raw scan): True; second (post-OCR verify): False
    monkeypatch.setattr(ocr_util, "needs_ocr", fake_needs_ocr)
    monkeypatch.setattr(ocr_util, "ensure_searchable",
                        lambda path, **kw: path + ".searchable")
    # ensure_searchable's fake above returns a path that doesn't exist on disk;
    # process_candidate must not care -- it only passes it through to
    # update_file_content, which is itself mocked below.
    monkeypatch.setattr(ac, "pin_head_revision", lambda drive, fid: call_order.append(("pin", fid)))
    monkeypatch.setattr(ac, "update_file_content",
                        lambda drive, fid, path, **kw: call_order.append(("update", fid)) or fid)

    cand = {"link": "https://drive.google.com/file/d/FID42/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))

    assert result["status"] == "done"
    assert call_order == [("download", "FID42"), ("pin", "FID42"), ("update", "FID42"), ("download", "FID42")]


def test_process_candidate_still_scanned_after_ocr_is_incomplete_not_done(monkeypatch, tmp_path):
    def fake_download(drive, fid, dest):
        open(dest, "wb").write(b"x")
        return dest
    monkeypatch.setattr(ac, "download_file", fake_download)
    monkeypatch.setattr(ocr_util, "needs_ocr", lambda path: True)   # always still flagged, even post-update
    monkeypatch.setattr(ocr_util, "ensure_searchable", lambda path, **kw: path + ".searchable")
    monkeypatch.setattr(ac, "pin_head_revision", lambda *a, **k: None)
    monkeypatch.setattr(ac, "update_file_content", lambda drive, fid, path, **kw: fid)
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "ocr_incomplete"   # never silently "done"


def test_process_candidate_id_changed_is_a_defensive_error_not_done(monkeypatch, tmp_path):
    def fake_download(drive, fid, dest):
        open(dest, "wb").write(b"x")
        return dest
    monkeypatch.setattr(ac, "download_file", fake_download)
    monkeypatch.setattr(ocr_util, "needs_ocr", lambda path: True)
    monkeypatch.setattr(ocr_util, "ensure_searchable", lambda path, **kw: path + ".searchable")
    monkeypatch.setattr(ac, "pin_head_revision", lambda *a, **k: None)
    monkeypatch.setattr(ac, "update_file_content", lambda drive, fid, path, **kw: "SOMETHING_ELSE")
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "error_id_changed"


# ---------------------------------------------------------------------------
# run_apply — resumable, --limit caps ACTUAL OCR attempts only
# ---------------------------------------------------------------------------


def test_run_apply_resume_skips_already_resolved(monkeypatch):
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
        ],
    }
    state = {f"{sw.TAB_ARCHIVE}:doc-1": {"status": "done"}}
    calls = []
    monkeypatch.setattr(bm, "process_candidate", lambda *a, **k: calls.append(1) or {"status": "done"})
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100)
    assert calls == []           # never re-processed -- "done" is terminal
    assert result["scanned"] == 0


def test_run_apply_limit_caps_actual_ocr_attempts_not_skips(monkeypatch):
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
            ["doc-2", "n", "d", "", "s", "https://drive.google.com/file/d/F2/view", "ts"],
            ["doc-3", "n", "d", "", "s", "https://drive.google.com/file/d/F3/view", "ts"],
        ],
    }
    state = {}
    outcomes = iter([{"status": "skip_has_text"}, {"status": "done"}, {"status": "done"}])
    monkeypatch.setattr(bm, "process_candidate", lambda *a, **k: next(outcomes))
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=1)
    # doc-1 is a free skip (doesn't count); doc-2 is the 1 actual OCR attempt
    # allowed by limit=1; doc-3 is never even scanned.
    assert result["fixed"] == 1
    assert result["scanned"] == 2
    assert f"{sw.TAB_ARCHIVE}:doc-3" not in state
