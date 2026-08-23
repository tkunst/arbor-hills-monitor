"""Durable PDF archive (ADR 007): the Archived PDFs index helpers and the OAuth
config gate. No network, no creds, no Drive — run()'s own top-level orchestration
needs live credentials and is a deploy-time check, same as backfill/watcher.
mirror_one_now() (ADR 007 addendum) IS fully unit-testable, since it never
raises to its caller by design — every branch (not configured, dead token,
already-archived, success, mid-mirror failure) is exercised below with fakes."""
import os
import re

import pytest

import sheet_writer as sw
import archive_client as ac
import archiver as av


# --- a tiny fake Sheets service (append + ranged get, like the real values API) ---

class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self, num_retries=0):
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs  # {tab: [header_row, data_row, ...]}

    @staticmethod
    def _tab(rng):
        return re.match(r"'([^']+)'", rng).group(1)

    @staticmethod
    def _ncols(rng):
        # "'T'!A2:A" -> 1 col, "'T'!A2:G" -> 7 cols
        m = re.search(r"![A-Z](\d+):([A-Z])", rng)
        if not m:
            return None
        return ord(m.group(2)) - ord("A") + 1

    def get(self, spreadsheetId, range):
        rows = self._tabs.get(self._tab(range), [])
        n = self._ncols(range)
        out = [list(r) for r in rows[1:]]  # skip header
        if n:
            out = [r[:n] for r in out]
        return _Req({"values": out})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._tabs.setdefault(self._tab(range), [["hdr"]]).extend(body["values"])
        return _Req({})


class FakeSheets:
    def __init__(self, seed_archive_header=True):
        tabs = {}
        if seed_archive_header:
            tabs[sw.TAB_ARCHIVE] = [sw.ARCHIVE_HEADERS]
        self._values = _Values(tabs)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


def test_archived_doc_ids_empty_when_tab_absent():
    assert sw.archived_doc_ids(FakeSheets(seed_archive_header=False), "SID") == set()


def test_append_archive_row_round_trips_and_joins_risks():
    svc = FakeSheets()
    sw.append_archive_row(
        svc, "SID", "doc-1", "WOI Status Report", "2025-08-07",
        ["R4", "R8"], "https://nsite/doc-1", "https://drive/abc", "2026-06-14T03:00:00",
    )
    assert sw.archived_doc_ids(svc, "SID") == {"doc-1"}
    # The written row carries 7 cells with risks joined to a string.
    row = svc._values._tabs[sw.TAB_ARCHIVE][1]
    assert len(row) == len(sw.ARCHIVE_HEADERS)
    assert row[3] == "R4, R8"
    assert row[5] == "https://drive/abc"


def test_append_archive_row_accepts_prejoined_risk_string():
    svc = FakeSheets()
    sw.append_archive_row(
        svc, "SID", "doc-2", "Letter", "2025-01-02",
        "R1", "src", "arch", "ts",
    )
    assert svc._values._tabs[sw.TAB_ARCHIVE][1][3] == "R1"


def test_is_configured_requires_credentials_plus_folder(monkeypatch):
    for k in (*ac.CREDENTIAL_ENV, "GOAUTH_ARCHIVE_FOLDER_ID", "GOAUTH_MMPC_FOLDER_ID"):
        monkeypatch.delenv(k, raising=False)
    assert ac.is_configured() is False
    # Three credentials set -> still not configured (no folder yet).
    monkeypatch.setenv("GOAUTH_CLIENT_ID", "x")
    monkeypatch.setenv("GOAUTH_CLIENT_SECRET", "y")
    monkeypatch.setenv("GOAUTH_REFRESH_TOKEN", "z")
    assert ac.is_configured() is False
    monkeypatch.setenv("GOAUTH_ARCHIVE_FOLDER_ID", "fid")
    assert ac.is_configured() is True
    assert ac.folder_id() == "fid"


def test_is_configured_folder_env_is_per_mirror(monkeypatch):
    # Mirror D generalization (ADR 010): credentials shared, folder ID is not —
    # one mirror's folder being set must not make another mirror look ready.
    monkeypatch.setenv("GOAUTH_CLIENT_ID", "x")
    monkeypatch.setenv("GOAUTH_CLIENT_SECRET", "y")
    monkeypatch.setenv("GOAUTH_REFRESH_TOKEN", "z")
    monkeypatch.setenv("GOAUTH_ARCHIVE_FOLDER_ID", "fid-b")
    monkeypatch.delenv("GOAUTH_MMPC_FOLDER_ID", raising=False)
    assert ac.is_configured("GOAUTH_ARCHIVE_FOLDER_ID") is True
    assert ac.is_configured("GOAUTH_MMPC_FOLDER_ID") is False
    monkeypatch.setenv("GOAUTH_MMPC_FOLDER_ID", "fid-d")
    assert ac.is_configured("GOAUTH_MMPC_FOLDER_ID") is True
    assert ac.folder_id("GOAUTH_MMPC_FOLDER_ID") == "fid-d"


def test_to_archive_is_processed_minus_archived():
    # Mirrors the set logic in archiver.run(): only processed-but-not-yet-archived.
    processed = {"a": {}, "b": {}, "c": {}}
    already = {"b"}
    todo = [d for d in processed if d not in already]
    assert set(todo) == {"a", "c"}


# --- Mirror D (ADR 010): MMPC Archived Files tab helpers ---

class FakeMMPCSheets:
    def __init__(self, seed_header=True):
        tabs = {}
        if seed_header:
            tabs[sw.TAB_MMPC_ARCHIVE] = [sw.MMPC_ARCHIVE_HEADERS]
        self._values = _Values(tabs)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


def test_mmpc_archived_file_ids_empty_when_tab_absent():
    # Pre-first-run: ensure_mmpc_tabs() hasn't created the tab yet. Must return
    # an empty set, not raise — unlike archived_doc_ids(), which assumes
    # ensure_tabs() already ran unconditionally at the start of every watcher
    # run (Mirror D's tab is create-on-demand, like the WDS tabs).
    assert sw.mmpc_archived_file_ids(FakeMMPCSheets(seed_header=False), "SID") == set()


def test_mmpc_archived_file_ids_returns_strings():
    svc = FakeMMPCSheets()
    sw.append_mmpc_archive_row(
        svc, "SID", 9107, "2026-02-11", "Minutes", "MMPC Minutes Feb 11 2026",
        4000, "https://drive/xyz", "2026-07-10T05:00:00",
    )
    # file_id passed in as an int; the Sheet-derived set must be str-typed so
    # mmpc_client.iter_new_files() (which compares str(file_id)) matches it.
    assert sw.mmpc_archived_file_ids(svc, "SID") == {"9107"}


def test_append_mmpc_archive_row_shape():
    svc = FakeMMPCSheets()
    sw.append_mmpc_archive_row(
        svc, "SID", 9000, "2026-02-11", "Agenda", "MMPC Agenda Feb 11 2026",
        4000, "https://drive/abc", "2026-07-10T05:00:00",
    )
    row = svc._values._tabs[sw.TAB_MMPC_ARCHIVE][1]
    assert len(row) == len(sw.MMPC_ARCHIVE_HEADERS)
    assert row == [9000, "2026-02-11", "Agenda", "MMPC Agenda Feb 11 2026",
                    4000, "https://drive/abc", "2026-07-10T05:00:00"]


# --- archived_doc_links (ADR 007 addendum): {doc_id: archive_link} lookup ---

def test_archived_doc_links_empty_when_tab_absent():
    assert sw.archived_doc_links(FakeSheets(seed_archive_header=False), "SID") == {}


def test_archived_doc_links_maps_id_to_link():
    svc = FakeSheets()
    sw.append_archive_row(
        svc, "SID", "doc-1", "Letter", "2025-01-02",
        ["R1"], "https://nsite/doc-1", "https://drive/abc", "ts",
    )
    sw.append_archive_row(
        svc, "SID", "doc-2", "Memo", "2025-01-03",
        [], "https://nsite/doc-2", "https://drive/xyz", "ts",
    )
    assert sw.archived_doc_links(svc, "SID") == {
        "doc-1": "https://drive/abc", "doc-2": "https://drive/xyz"}


def test_archived_doc_links_last_write_wins_on_duplicate_doc_id():
    svc = FakeSheets()
    sw.append_archive_row(svc, "SID", "doc-1", "n", "d", [], "src", "https://drive/old", "ts")
    sw.append_archive_row(svc, "SID", "doc-1", "n", "d", [], "src", "https://drive/new", "ts2")
    assert sw.archived_doc_links(svc, "SID") == {"doc-1": "https://drive/new"}


# --- mirror_one_now (ADR 007 addendum): the inline, best-effort single-doc
# mirror watcher.py/backfill.py call so a same-day Sheet row / alert can link
# to Drive instead of the referer-fragile nSITE link. It never raises to its
# caller by design — every branch below asserts that explicitly, not just the
# happy path, since a raise here would break the watcher's whole run. ---

def _doc(doc_id="doc-1", nsite_link="https://nsite/doc-1"):
    return {
        "doc_id": doc_id, "doc_url": nsite_link, "document_name": "A Letter",
        "date_filed": "2026-08-07", "facility_srn": "N2688",
    }


class _FakeDrive:
    """Stands in for the object archive_client.oauth_drive_service() would
    return — mirror_one_now() never calls any method on it directly (it only
    passes it through to archive_client.upload_pdf, which we monkeypatch
    separately), so an opaque sentinel is enough."""


def test_not_configured_returns_nsite_link_untouched(monkeypatch):
    monkeypatch.setattr(av.ac, "is_configured", lambda: False)
    calls = []
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: calls.append("oauth") or _FakeDrive())
    svc = FakeSheets()
    link = av.mirror_one_now(object(), svc, "SID", _doc(), {})
    assert link == "https://nsite/doc-1"
    assert calls == []  # never even tried to authenticate
    assert sw.archived_doc_ids(svc, "SID") == set()  # nothing recorded


def test_dead_oauth_token_falls_back_without_raising(monkeypatch):
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    def _boom():
        raise RuntimeError("invalid_grant: token revoked")
    monkeypatch.setattr(av.ac, "oauth_drive_service", _boom)
    svc = FakeSheets()
    link = av.mirror_one_now(object(), svc, "SID", _doc(), {})
    assert link == "https://nsite/doc-1"
    assert sw.archived_doc_ids(svc, "SID") == set()


def test_already_archived_reuses_existing_link_no_reupload(monkeypatch):
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: _FakeDrive())
    svc = FakeSheets()
    sw.append_archive_row(
        svc, "SID", "doc-1", "A Letter", "2026-08-07",
        [], "https://nsite/doc-1", "https://drive/existing", "ts")
    uploaded = []
    monkeypatch.setattr(av.ac, "upload_pdf", lambda *a, **kw: uploaded.append(1) or "SHOULD_NOT_BE_CALLED")

    link = av.mirror_one_now(object(), svc, "SID", _doc(), {})

    assert link == "https://drive/existing"
    assert uploaded == []  # no re-upload for a doc already in the index


def test_success_uploads_records_and_returns_drive_link(monkeypatch, tmp_path):
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: _FakeDrive())
    monkeypatch.setattr(av.ac, "folder_id", lambda: "FID")
    upload_calls = []
    def _upload(drive, local_path, name, folder_id):
        upload_calls.append((local_path, name, folder_id))
        return "https://drive/new-mirror"
    monkeypatch.setattr(av.ac, "upload_pdf", _upload)
    downloaded = []
    monkeypatch.setattr(av.nc, "download_pdf", lambda session, doc, local: downloaded.append(local))

    local = str(tmp_path / "already-on-disk.pdf")
    svc = FakeSheets()
    link = av.mirror_one_now(object(), svc, "SID", _doc(), {}, local_path=local)

    assert link == "https://drive/new-mirror"
    assert downloaded == []  # local_path was supplied — never re-fetched from nSITE
    assert upload_calls == [(local, "N2688_doc-1.pdf", "FID")]
    assert sw.archived_doc_ids(svc, "SID") == {"doc-1"}
    row = svc._values._tabs[sw.TAB_ARCHIVE][1]
    assert row[4] == "https://nsite/doc-1"  # source link preserved
    assert row[5] == "https://drive/new-mirror"


def test_downloads_when_no_local_path_given(monkeypatch, tmp_path):
    # run()'s own batch loop calls this path (no pre-existing local file);
    # mirror_one_now must still work without one, by downloading itself.
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: _FakeDrive())
    monkeypatch.setattr(av.ac, "folder_id", lambda: "FID")
    monkeypatch.setattr(av.ac, "upload_pdf", lambda *a, **kw: "https://drive/new")
    downloaded = []
    def _dl(session, doc, local):
        downloaded.append(local)
        open(local, "wb").write(b"%PDF-fake")
    monkeypatch.setattr(av.nc, "download_pdf", _dl)
    monkeypatch.setattr(av.tempfile, "gettempdir", lambda: str(tmp_path))

    svc = FakeSheets()
    link = av.mirror_one_now(object(), svc, "SID", _doc(), {})

    assert link == "https://drive/new"
    assert len(downloaded) == 1
    assert not os.path.exists(downloaded[0])  # cleaned up after upload (no local_path given)


def test_upload_failure_falls_back_and_records_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: _FakeDrive())
    monkeypatch.setattr(av.ac, "folder_id", lambda: "FID")
    def _boom(*a, **kw):
        raise RuntimeError("Drive quota exceeded")
    monkeypatch.setattr(av.ac, "upload_pdf", _boom)

    local = str(tmp_path / "on-disk.pdf")
    open(local, "wb").write(b"%PDF-fake")
    svc = FakeSheets()
    link = av.mirror_one_now(object(), svc, "SID", _doc(), {}, local_path=local)

    assert link == "https://nsite/doc-1"  # fell back, did not raise
    assert sw.archived_doc_ids(svc, "SID") == set()  # nothing recorded on failure


def test_sheet_write_failure_after_a_successful_upload_still_falls_back(monkeypatch, tmp_path):
    """Upload succeeds, THEN the index-row write fails (a transient Sheets
    error) — the PDF is now orphaned in Drive (uploaded, unindexed), which is
    real but accepted: find_in_folder's idempotency means the next run (or
    archive.yml's own catch-up) reuses it under the same name rather than
    re-uploading. What must NEVER happen is this bubbling up and taking down
    the caller's run — verify the fallback link still comes back cleanly."""
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: _FakeDrive())
    monkeypatch.setattr(av.ac, "folder_id", lambda: "FID")
    upload_calls = []
    monkeypatch.setattr(av.ac, "upload_pdf",
                         lambda *a, **kw: upload_calls.append(1) or "https://drive/orphaned")

    def _boom(*a, **kw):
        raise RuntimeError("Sheets API 503")
    monkeypatch.setattr(av.sw, "append_archive_row", _boom)

    local = str(tmp_path / "on-disk.pdf")
    open(local, "wb").write(b"%PDF-fake")
    svc = FakeSheets()
    link = av.mirror_one_now(object(), svc, "SID", _doc(), {}, local_path=local)

    assert upload_calls == [1]  # the upload DID happen (the orphan)
    assert link == "https://nsite/doc-1"  # but the caller still gets a safe fallback, not a raise
    assert sw.archived_doc_ids(svc, "SID") == set()  # the index row never landed


def test_drive_state_reused_across_calls_within_one_run(monkeypatch, tmp_path):
    monkeypatch.setattr(av.ac, "is_configured", lambda: True)
    oauth_calls = []
    monkeypatch.setattr(av.ac, "oauth_drive_service", lambda: oauth_calls.append(1) or _FakeDrive())
    monkeypatch.setattr(av.ac, "folder_id", lambda: "FID")
    monkeypatch.setattr(av.ac, "upload_pdf", lambda *a, **kw: "https://drive/x")

    svc = FakeSheets()
    drive_state = {}
    local1 = str(tmp_path / "a.pdf")
    local2 = str(tmp_path / "b.pdf")
    open(local1, "wb").write(b"1")
    open(local2, "wb").write(b"2")

    av.mirror_one_now(object(), svc, "SID", _doc("doc-1", "https://nsite/doc-1"), drive_state, local_path=local1)
    av.mirror_one_now(object(), svc, "SID", _doc("doc-2", "https://nsite/doc-2"), drive_state, local_path=local2)

    assert len(oauth_calls) == 1  # OAuth handshake happened once, not per document
