"""scripts/backfill_mirror_ocr.py (ADR 069): the one-time OCR backfill for
already-mirrored scan-only PDFs. Loaded the same way
test_backfill_document_dates_titles.py loads its script module — by file path,
not via sys.path package import, matching this repo's established convention
for the one-off scripts/ tools."""
import importlib.util
import os
import time

import pytest

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


def test_main_dry_run_never_touches_drive_end_to_end(monkeypatch, tmp_path, capsys):
    # End-to-end proof (not just a signature check): running main() with no
    # --apply flag must never call ANY Drive function, even indirectly.
    tabs = {sw.TAB_ARCHIVE: [sw.ARCHIVE_HEADERS]}
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.chdir(tmp_path)   # so a stray local-state/ write (bug) can't land in the repo

    import drive_client
    monkeypatch.setattr(drive_client, "sheets_service", lambda: FakeSheets(tabs))

    def boom(*a, **k):
        raise AssertionError("dry-run must never call a Drive function")
    monkeypatch.setattr(ac, "oauth_drive_service", boom)
    monkeypatch.setattr(ac, "download_file", boom)
    monkeypatch.setattr(ac, "update_file_content", boom)
    monkeypatch.setattr(ac, "pin_head_revision", boom)

    rc = bm.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ZERO Drive API calls" in out


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
    monkeypatch.setattr(ocr_util, "image_only_pages", lambda path: set())
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
    monkeypatch.setattr(ocr_util, "image_only_pages", boom)
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
    monkeypatch.setattr(ocr_util, "image_only_pages", lambda path: {0})
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

    pages = {"n": 0}
    def fake_image_only_pages(path):
        pages["n"] += 1
        return {0} if pages["n"] == 1 else set()   # raw scan: page 0 flagged; after OCR+verify: fixed
    monkeypatch.setattr(ocr_util, "image_only_pages", fake_image_only_pages)
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


def test_process_candidate_no_improvement_never_touches_drive(monkeypatch, tmp_path):
    # M2 fix (found by independent review): a page with genuinely no
    # recoverable text (always-flagged, even after a real OCR pass) must
    # never be pinned/overwritten in Drive for zero gain -- the check runs
    # LOCALLY, before any Drive write.
    def fake_download(drive, fid, dest):
        open(dest, "wb").write(b"x")
        return dest
    monkeypatch.setattr(ac, "download_file", fake_download)
    monkeypatch.setattr(ocr_util, "image_only_pages", lambda path: {0})   # always still flagged, same set
    monkeypatch.setattr(ocr_util, "ensure_searchable", lambda path, **kw: path + ".searchable")
    pin_calls, update_calls = [], []
    monkeypatch.setattr(ac, "pin_head_revision", lambda *a, **k: pin_calls.append(1))
    monkeypatch.setattr(ac, "update_file_content", lambda *a, **k: update_calls.append(1))
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "ocr_incomplete"   # never silently "done"
    assert pin_calls == [] and update_calls == []  # no Drive write for zero benefit


def test_process_candidate_partial_improvement_writes_done_partial(monkeypatch, tmp_path):
    # HIGH finding fixed 2026-10-13 (independent review + the advisor): a
    # 50-page scan where OCR fixes 49 pages but one photo-log page stays
    # unreadable must NOT be thrown away -- it's a real improvement and
    # belongs in Drive, recorded `done_partial` (not `done`, not
    # `ocr_incomplete`).
    def fake_download(drive, fid, dest):
        open(dest, "wb").write(b"x")
        return dest
    monkeypatch.setattr(ac, "download_file", fake_download)
    pages = {"n": 0}
    def fake_image_only_pages(path):
        pages["n"] += 1
        # raw scan: pages 3 and 7 flagged; after OCR: only page 7 remains
        # (a genuinely unreadable photo); verify re-download: same.
        return {3, 7} if pages["n"] == 1 else {7}
    monkeypatch.setattr(ocr_util, "image_only_pages", fake_image_only_pages)
    monkeypatch.setattr(ocr_util, "ensure_searchable", lambda path, **kw: path + ".searchable")
    update_calls = []
    monkeypatch.setattr(ac, "pin_head_revision", lambda *a, **k: None)
    monkeypatch.setattr(ac, "update_file_content",
                        lambda drive, fid, path, **kw: update_calls.append(1) or fid)
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "done_partial"
    assert update_calls == [1]   # DOES write to Drive -- the partial win is real


def test_process_candidate_verify_failure_after_successful_update_is_done_unverified(
        monkeypatch, tmp_path):
    # M3 fix (found by independent review): if the UPDATE succeeds but the
    # post-update verify step fails (a transient re-download blip), the
    # result must NOT be `error_upload` -- that would make a later run
    # re-OCR and re-upload a file that's probably already fixed.
    download_calls = {"n": 0}
    def fake_download(drive, fid, dest):
        download_calls["n"] += 1
        if download_calls["n"] == 1:
            open(dest, "wb").write(b"x")
            return dest
        raise RuntimeError("transient re-download blip")   # the VERIFY download
    monkeypatch.setattr(ac, "download_file", fake_download)
    pages = {"n": 0}
    def fake_image_only_pages(path):
        pages["n"] += 1
        return {0} if pages["n"] == 1 else set()   # raw: flagged; post-OCR local check: fixed
    monkeypatch.setattr(ocr_util, "image_only_pages", fake_image_only_pages)
    monkeypatch.setattr(ocr_util, "ensure_searchable", lambda path, **kw: path + ".searchable")
    monkeypatch.setattr(ac, "pin_head_revision", lambda *a, **k: None)
    monkeypatch.setattr(ac, "update_file_content", lambda drive, fid, path, **kw: fid)
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "done_unverified"


def test_process_candidate_id_changed_is_a_defensive_error_not_done(monkeypatch, tmp_path):
    def fake_download(drive, fid, dest):
        open(dest, "wb").write(b"x")
        return dest
    monkeypatch.setattr(ac, "download_file", fake_download)
    pages = {"n": 0}
    def fake_image_only_pages(path):
        pages["n"] += 1
        return {0} if pages["n"] == 1 else set()   # raw: flagged; post-OCR local check: fixed
    monkeypatch.setattr(ocr_util, "image_only_pages", fake_image_only_pages)
    monkeypatch.setattr(ocr_util, "ensure_searchable", lambda path, **kw: path + ".searchable")
    monkeypatch.setattr(ac, "pin_head_revision", lambda *a, **k: None)
    monkeypatch.setattr(ac, "update_file_content", lambda drive, fid, path, **kw: "SOMETHING_ELSE")
    cand = {"link": "https://drive.google.com/file/d/FID/view"}
    result = bm.process_candidate(_fake_drive(), cand, str(tmp_path))
    assert result["status"] == "error_id_changed"


# ---------------------------------------------------------------------------
# run_apply — resumable, --limit caps ACTUAL OCR attempts only
# ---------------------------------------------------------------------------


def test_run_apply_resume_skips_already_resolved(monkeypatch, tmp_path):
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
        ],
    }
    state = {f"{sw.TAB_ARCHIVE}:doc-1": {"status": "done"}}
    calls = []
    monkeypatch.setattr(bm, "process_candidate", lambda *a, **k: calls.append(1) or {"status": "done"})
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100,
                          state_path=str(tmp_path / "state.json"))
    assert calls == []           # never re-processed -- "done" is terminal
    assert result["scanned"] == 0


def test_run_apply_limit_caps_actual_ocr_attempts_not_skips(monkeypatch, tmp_path):
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
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=1,
                          state_path=str(tmp_path / "state.json"))
    # doc-1 is a free skip (doesn't count); doc-2 is the 1 actual OCR attempt
    # allowed by limit=1; doc-3 is never even scanned.
    assert result["fixed"] == 1
    assert result["scanned"] == 2
    assert f"{sw.TAB_ARCHIVE}:doc-3" not in state


def test_run_apply_retryable_status_is_reattempted_not_skipped(monkeypatch, tmp_path):
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
        ],
    }
    state = {f"{sw.TAB_ARCHIVE}:doc-1": {"status": "error_download"}}   # retryable
    calls = []
    monkeypatch.setattr(bm, "process_candidate", lambda *a, **k: calls.append(1) or {"status": "done"})
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100,
                          state_path=str(tmp_path / "state.json"))
    assert calls == [1]          # retryable statuses ARE re-attempted
    assert state[f"{sw.TAB_ARCHIVE}:doc-1"]["status"] == "done"


def test_run_apply_same_file_failing_twice_is_persistent_not_a_second_abort(monkeypatch, tmp_path):
    # MEDIUM fix (round-2 independent review): a file that fails error_upload
    # on retry, the SAME way, is file-specific, not systemic -- the run must
    # NOT abort a second time over it (which would make the backfill stall
    # forever, since the same file is always the first thing re-attempted).
    # It's recorded terminal (error_upload_persistent) and the run continues
    # to doc-2.
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
            ["doc-2", "n", "d", "", "s", "https://drive.google.com/file/d/F2/view", "ts"],
        ],
    }
    state = {f"{sw.TAB_ARCHIVE}:doc-1": {"status": "error_upload"}}   # already failed once

    def fake_process(drive, cand, tmp_dir, **kwargs):
        if cand["id"] == "doc-1":
            return {"status": "error_upload", "note": "PermissionDenied"}
        return {"status": "done"}   # doc-2 succeeds -- isolates doc-1's persistent failure
    monkeypatch.setattr(bm, "process_candidate", fake_process)
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100,
                          state_path=str(tmp_path / "state.json"))
    assert result["aborted"] is False
    assert result["scanned"] == 2   # doc-2 WAS reached, unlike the systemic-abort case
    assert state[f"{sw.TAB_ARCHIVE}:doc-1"]["status"] == "error_upload_persistent"
    # And the persistent status is now terminal -- a THIRD run must not
    # retry it again.
    assert "error_upload_persistent" not in bm._RETRYABLE_STATUSES


def test_run_apply_aborts_rest_of_run_on_systemic_failure(monkeypatch, tmp_path):
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
            ["doc-2", "n", "d", "", "s", "https://drive.google.com/file/d/F2/view", "ts"],
        ],
        sw.TAB_MMPC_ARCHIVE: [
            sw.MMPC_ARCHIVE_HEADERS,
            [9000, "d", "Agenda", "n", 1, "https://drive.google.com/file/d/F3/view", "ts"],
        ],
    }
    state = {}
    monkeypatch.setattr(bm, "process_candidate",
                        lambda *a, **k: {"status": "error_upload", "note": "PermissionDenied"})
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100,
                          state_path=str(tmp_path / "state.json"))
    assert result["aborted"] is True
    assert result["scanned"] == 1    # doc-2 and the MMPC tab were never reached
    assert f"{sw.TAB_ARCHIVE}:doc-2" not in state
    assert f"{sw.TAB_MMPC_ARCHIVE}:9000" not in state


def test_run_apply_saves_state_incrementally_not_only_at_the_end(monkeypatch, tmp_path):
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
        ],
    }
    state = {}
    state_path = str(tmp_path / "state.json")
    assert not os.path.exists(state_path)   # nothing on disk before the run

    monkeypatch.setattr(bm, "process_candidate", lambda *a, **k: {"status": "done"})
    bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100, state_path=state_path)

    # Read the file INDEPENDENTLY of the in-memory `state` dict run_apply
    # mutated, to prove the save genuinely happened (not just an in-memory
    # update) -- a crash right after the candidate's status was recorded
    # must not lose it.
    on_disk = bm.load_state(state_path)
    assert on_disk[f"{sw.TAB_ARCHIVE}:doc-1"]["status"] == "done"


# ---------------------------------------------------------------------------
# ADDENDUM (2026-10-14, coder:ocr-hang-fix): per-candidate progress lines +
# the per-candidate watchdog -- the direct fix for "the stuck file is
# unknown" (the 2026-10-14 incident, run 38585715746: one file fixed,
# then zero log output for ~4h53m before a manual cancel).
# ---------------------------------------------------------------------------


def test_candidate_timed_out_is_a_base_exception_not_caught_by_except_exception():
    # process_candidate() (and ensure_searchable()/the Drive calls it
    # wraps) catch broad `except Exception` internally to stay fail-open --
    # the watchdog exception MUST NOT be one of those, or it would get
    # silently relabeled (e.g. as ocr_failed/error_download) instead of
    # reaching run_apply().
    assert issubclass(bm._CandidateTimedOut, BaseException)
    assert not issubclass(bm._CandidateTimedOut, Exception)


def test_alarm_handler_raises_candidate_timed_out():
    with pytest.raises(bm._CandidateTimedOut):
        bm._alarm_handler(None, None)


def test_error_timeout_is_terminal_and_counts_against_limit_but_not_abort():
    assert "error_timeout" in bm._COUNTS_AGAINST_LIMIT   # a real, if failed, attempt
    assert "error_timeout" not in bm._RETRYABLE_STATUSES  # not auto-retried
    assert "error_timeout" not in bm._ABORT_RUN_STATUSES  # one stuck file ≠ systemic


def test_run_apply_prints_progress_line_before_and_after_each_candidate(
        monkeypatch, tmp_path, capsys):
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
        ],
    }
    state = {}
    monkeypatch.setattr(bm, "process_candidate", lambda *a, **k: {"status": "done"})
    bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100,
                state_path=str(tmp_path / "state.json"))
    out = capsys.readouterr().out
    key = f"{sw.TAB_ARCHIVE}:doc-1"
    assert f"[backfill] 1 {key} starting" in out
    assert any(
        line.startswith(f"[backfill] 1 {key} done ") and line.rstrip().endswith("s")
        for line in out.splitlines()
    ), out


def test_run_apply_progress_lines_never_leak_notes_or_links(monkeypatch, tmp_path, capsys):
    # Key = "{tab}:{id}" only (nSITE/CivicClerk ids are public, per the
    # module docstring) -- never a title, link, or result["note"] (which can
    # carry a Drive file id pair on error_id_changed, or an exception class
    # name on error_download/error_upload). This is the public Actions log.
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s",
             "https://drive.google.com/file/d/SUPER_SECRET_LINK_TOKEN/view", "ts"],
        ],
    }
    state = {}
    monkeypatch.setattr(
        bm, "process_candidate",
        lambda *a, **k: {"status": "error_download", "note": "ConnectionResetSecretDetail"},
    )
    bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100,
                state_path=str(tmp_path / "state.json"))
    out = capsys.readouterr().out
    assert "ConnectionResetSecretDetail" not in out
    assert "SUPER_SECRET_LINK_TOKEN" not in out


def test_run_apply_watchdog_times_out_a_hung_candidate_and_continues(monkeypatch, tmp_path):
    # A short ceiling for test speed -- the mechanism (signal.alarm) is the
    # same regardless of the configured number of seconds.
    monkeypatch.setattr(bm, "_CANDIDATE_TIMEOUT_S", 1)
    tabs = {
        sw.TAB_ARCHIVE: [
            sw.ARCHIVE_HEADERS,
            ["doc-1", "n", "d", "", "s", "https://drive.google.com/file/d/F1/view", "ts"],
            ["doc-2", "n", "d", "", "s", "https://drive.google.com/file/d/F2/view", "ts"],
        ],
    }
    state = {}

    def fake_process(drive, cand, tmp_dir, phase=None):
        if cand["id"] == "doc-1":
            if phase is not None:
                phase[0] = "ocr"
            time.sleep(5)              # well past the 1s watchdog ceiling
            return {"status": "done"}  # never reached -- the alarm fires first
        return {"status": "done"}
    monkeypatch.setattr(bm, "process_candidate", fake_process)

    t0 = time.monotonic()
    result = bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), state, limit=100,
                          state_path=str(tmp_path / "state.json"))
    elapsed = time.monotonic() - t0

    assert elapsed < 4   # bounded by the 1s watchdog, not doc-1's 5s sleep
    assert state[f"{sw.TAB_ARCHIVE}:doc-1"]["status"] == "error_timeout"
    assert "phase=ocr" in state[f"{sw.TAB_ARCHIVE}:doc-1"]["note"]
    assert state[f"{sw.TAB_ARCHIVE}:doc-2"]["status"] == "done"   # the run CONTINUED
    assert result["aborted"] is False
    assert result["scanned"] == 2
    assert result["fixed"] == 2   # error_timeout counts against --limit too


def test_run_apply_restores_the_previous_sigalrm_handler_on_exit(tmp_path):
    import signal

    def someone_elses_handler(signum, frame):
        pass  # never actually invoked in this test -- just an identity marker

    previous = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, someone_elses_handler)
        tabs = {sw.TAB_ARCHIVE: [sw.ARCHIVE_HEADERS]}  # no rows -- loop body never runs
        bm.run_apply(FakeSheets(tabs), "SID", _fake_drive(), {}, limit=100,
                    state_path=str(tmp_path / "state.json"))
        # run_apply() must not leave its OWN _alarm_handler installed
        # process-wide after it returns -- it restores whatever was there
        # before it ran.
        assert signal.getsignal(signal.SIGALRM) is someone_elses_handler
    finally:
        signal.signal(signal.SIGALRM, previous)
