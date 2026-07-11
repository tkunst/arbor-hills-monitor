"""Durable PDF archive (ADR 007): the Archived PDFs index helpers and the OAuth
config gate. No network, no creds, no Drive — the run() orchestration needs live
credentials and is a deploy-time check, same as backfill/watcher."""
import re

import pytest

import sheet_writer as sw
import archive_client as ac


# --- a tiny fake Sheets service (append + ranged get, like the real values API) ---

class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
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
