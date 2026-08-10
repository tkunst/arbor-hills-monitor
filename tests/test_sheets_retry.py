"""Regression guard for the transient-retry hardening (drive_client.
GOOGLE_API_NUM_RETRIES). Every googleapiclient .execute() on the Sheets/Drive path
must pass num_retries so a single transient Google 5xx/429 blip is retried with
backoff instead of aborting a whole watcher run — the failure mode that took
down mmd-watch on 2026-08-08 (a Sheets 503 from ensure_mmd_tabs' first .get()).

Two guards:
  1. behavioral — a recording fake proves the real append/ensure paths thread
     num_retries through to .execute();
  2. source-scan — an invariant that no bare .execute() (without num_retries)
     is reintroduced anywhere in the two API modules, including at the ~50 call
     sites the behavioral test doesn't individually exercise.
"""
import pathlib
import re

import drive_client as dc
import sheet_writer as sw


# ---------------------------------------------------------------------------
# 1. Behavioral: num_retries actually reaches .execute()
# ---------------------------------------------------------------------------


class _RecordingReq:
    def __init__(self, result, calls):
        self._result = result
        self._calls = calls

    def execute(self, num_retries=None):
        # A bare .execute() would record None here (not GOOGLE_API_NUM_RETRIES),
        # so the assertions below catch it.
        self._calls.append(num_retries)
        return self._result


class _RecordingValues:
    def __init__(self, calls):
        self._calls = calls

    def get(self, spreadsheetId, range):
        return _RecordingReq({"values": []}, self._calls)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        return _RecordingReq({}, self._calls)

    def update(self, spreadsheetId, range, valueInputOption, body):
        return _RecordingReq({}, self._calls)


class _RecordingSheets:
    def __init__(self):
        self.calls = []
        self._values = _RecordingValues(self.calls)

    def spreadsheets(self):
        return self

    def get(self, spreadsheetId):
        # Report the MMD tab as already present so ensure_mmd_tabs takes the
        # no-addSheet branch; the get() + _set_header() update() still run.
        return _RecordingReq(
            {"sheets": [{"properties": {"title": sw.TAB_MMD}}]}, self.calls)

    def batchUpdate(self, spreadsheetId, body):
        return _RecordingReq({}, self.calls)

    def values(self):
        return self._values


def test_append_rows_threads_num_retries_through_to_execute():
    svc = _RecordingSheets()
    sw.append_rows(svc, "SID", "SomeTab", [["a", "b"]])
    assert svc.calls == [dc.GOOGLE_API_NUM_RETRIES]


def test_ensure_tabs_path_passes_num_retries_on_every_call():
    svc = _RecordingSheets()
    sw.ensure_mmd_tabs(svc, "SID")   # get() (exists-check) + _set_header() update()
    assert svc.calls, "ensure_mmd_tabs made no API calls"
    assert all(n == dc.GOOGLE_API_NUM_RETRIES for n in svc.calls), (
        f"a call reached .execute() without num_retries: {svc.calls}")


def test_num_retries_is_a_sane_positive_count():
    assert isinstance(dc.GOOGLE_API_NUM_RETRIES, int)
    assert 1 <= dc.GOOGLE_API_NUM_RETRIES <= 10


# ---------------------------------------------------------------------------
# 2. Source-scan invariant: no bare .execute() anywhere in the API modules
# ---------------------------------------------------------------------------

_BARE_EXECUTE = re.compile(r"\.execute\((?!num_retries=)")


# Every module that talks to the Google API on an UNATTENDED scheduled path
# (a watcher, an archiver, or the Sunday digest). The manual one-off scripts in
# scripts/ (oauth_setup, create_oauth_folder, verify_state) are deliberately NOT
# here: a human runs them interactively and re-runs on a transient blip, so
# they're outside the aborted-scheduled-run failure class this hardening fixes.
_UNATTENDED_API_MODULES = (
    "sheet_writer.py", "drive_client.py", "archive_client.py", "upcoming.py",
)


def test_no_bare_execute_call_remains_in_the_api_modules():
    """Every `.execute(` in the unattended-path Google API modules must be
    `.execute(num_retries=...)`. A bare one reintroduces the single-blip-aborts-
    the-run bug this hardening fixed — and there are ~60 call sites across these
    modules, so a new one is easy to add without noticing. (The explanatory
    comments deliberately write `execute()` without the leading dot so they
    don't trip this.)"""
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = {}
    for mod in _UNATTENDED_API_MODULES:
        src = (root / mod).read_text()
        hits = [i + 1 for i, line in enumerate(src.splitlines())
                if _BARE_EXECUTE.search(line)]
        if hits:
            offenders[mod] = hits
    assert not offenders, f"bare .execute() (no num_retries) found: {offenders}"
