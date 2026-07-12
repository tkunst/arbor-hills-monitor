"""Sheet-backed processing state (ADR 006): append-only _state log reduction and
_meta round-trip, exercised through a fake Sheets service. No network, no creds.

The fake mimics just the slice of the Sheets values API the state code uses:
  values().get(range="'TAB'!A2:E").execute()   -> {"values": [...data rows...]}
  values().append(range="'TAB'!A1", body=...)  -> appends rows to the tab
  values().update(range="'TAB'!A2", body=...)  -> overwrites from the 2nd row down
Each tab keeps a header row at index 0 so the A2-anchored ranges behave like the
real API (header excluded from gets, preserved by appends/updates)."""
import json
import re

import sheet_writer as sw


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs  # {tab_name: [row, row, ...]} including a header at [0]

    @staticmethod
    def _tab(rng):
        return re.match(r"'([^']+)'", rng).group(1)

    def get(self, spreadsheetId, range):
        rows = self._tabs.get(self._tab(range), [])
        return _Req({"values": [list(r) for r in rows[1:]]})  # skip header

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._tabs.setdefault(self._tab(range), [["hdr"]]).extend(body["values"])
        return _Req({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        tab = self._tab(range)
        rows = self._tabs.setdefault(tab, [["hdr"]])
        for i, row in enumerate(body["values"], start=1):  # A2 -> data index 1
            if i < len(rows):
                rows[i] = row
            else:
                rows.append(row)
        return _Req({})


class FakeSheets:
    """Stands in for the googleapiclient sheets service object."""
    def __init__(self):
        # Seed header rows so A2-anchored ranges skip them, like the real Sheet.
        self._values = _Values({
            sw.TAB_STATE: [sw.STATE_HEADERS],
            sw.TAB_META: [sw.META_HEADERS],
        })

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


def test_empty_state_when_tabs_absent():
    # A truly fresh Sheet: get() raises-equivalent (tab missing) -> [].
    svc = FakeSheets()
    svc._values._tabs.clear()  # simulate tabs not yet created
    state = sw.read_state(svc, "SID")
    assert state["processed"] == {}
    assert state["errors"] == {}
    assert state["pending_digest"] == []
    assert state["wds_seen"] == {}
    assert state["last_run"] == ""


def test_processed_event_round_trips():
    svc = FakeSheets()
    payload = {"document_name": "WOI Report", "severity": "urgent", "risks": ["R4"]}
    sw.mark_processed(svc, "SID", "doc-1", payload, "2026-06-14T02:00:00")
    state = sw.read_state(svc, "SID")
    assert "doc-1" in state["processed"]
    assert state["processed"]["doc-1"] == payload
    assert state["errors"] == {}


def test_errors_counted_then_cleared_by_processed():
    svc = FakeSheets()
    # Append-only, chronological: two failures then a success for the same doc.
    sw.mark_error(svc, "SID", "doc-2", 1, "2026-06-14T02:00:01")
    sw.mark_error(svc, "SID", "doc-2", 2, "2026-06-14T02:00:02")
    sw.mark_processed(svc, "SID", "doc-2", {"severity": "routine"}, "2026-06-14T02:00:03")
    state = sw.read_state(svc, "SID")
    assert "doc-2" in state["processed"]      # success wins
    assert "doc-2" not in state["errors"]     # earlier errors cleared


def test_poison_doc_accumulates_error_count():
    svc = FakeSheets()
    for n in (1, 2, 3):
        sw.mark_error(svc, "SID", "doc-3", n, f"2026-06-14T02:00:0{n}")
    state = sw.read_state(svc, "SID")
    assert state["errors"]["doc-3"] == 3      # counts error rows, never processed
    assert "doc-3" not in state["processed"]


def test_skipped_event_is_terminal_and_clears_errors():
    svc = FakeSheets()
    # Errors then a terminal 'skipped' (unprocessable source, stubbed into feed).
    sw.mark_error(svc, "SID", "doc-s", 1, "2026-06-15T01:00:00")
    sw.mark_error(svc, "SID", "doc-s", 2, "2026-06-15T01:00:01")
    sw.mark_skipped(svc, "SID", "doc-s", {"reason": "encrypted .doc"}, "2026-06-15T01:00:02")
    state = sw.read_state(svc, "SID")
    assert state["skipped"]["doc-s"] == {"reason": "encrypted .doc"}
    assert "doc-s" not in state["errors"]      # errors cleared on skip
    assert "doc-s" not in state["processed"]   # skipped != classified


def test_processed_after_skipped_clears_skipped():
    # ADR 011: a targeted RETRY_DOC_IDS retry can turn a terminally-skipped
    # doc into a genuine success once a parser fix makes it processable.
    # 'skipped' must not linger alongside 'processed' for the same doc.
    svc = FakeSheets()
    sw.mark_skipped(svc, "SID", "doc-r", {"reason": "was .msg, unsupported"}, "2026-07-07T00:00:00")
    sw.mark_processed(svc, "SID", "doc-r", {"severity": "notable"}, "2026-07-11T00:00:00")
    state = sw.read_state(svc, "SID")
    assert "doc-r" in state["processed"]
    assert "doc-r" not in state["skipped"]     # the stale skip entry is cleared


def test_meta_round_trips_and_defaults_are_isolated():
    svc = FakeSheets()
    state = sw.read_state(svc, "SID")
    state["pending_digest"].append({"document_name": "X"})
    state["wds_snapshot_hashes"]["p1"] = "hashA"
    state["last_run"] = "2026-06-14T06:00:00"
    sw.write_meta(svc, "SID", state)

    reloaded = sw.read_state(svc, "SID")
    assert reloaded["pending_digest"] == [{"document_name": "X"}]
    assert reloaded["wds_snapshot_hashes"] == {"p1": "hashA"}
    assert reloaded["last_run"] == "2026-06-14T06:00:00"

    # The module-level defaults must not have been mutated by the round trip.
    fresh = sw.read_state(FakeSheets(), "SID2")
    assert fresh["pending_digest"] == []
    assert fresh["wds_snapshot_hashes"] == {}


def test_read_meta_matches_read_state_meta_slice():
    # read_meta() is read_state()'s _meta half, factored out so callers that
    # never touch _state (wds_archiver.py, dump_wds_historical.py) don't pay
    # for a full _state scan just to reach one _meta key.
    svc = FakeSheets()
    sw.mark_processed(svc, "SID", "doc-1", {"severity": "urgent"}, "2026-06-14T02:00:00")
    state = sw.read_state(svc, "SID")
    state["wds_seen"]["qmr"] = {"records": {"k": "h"}, "last_count": 1}
    sw.write_meta(svc, "SID", state)

    meta = sw.read_meta(svc, "SID")
    assert "processed" not in meta and "errors" not in meta   # _state-only keys absent
    assert meta["wds_seen"] == {"qmr": {"records": {"k": "h"}, "last_count": 1}}
    assert meta == {k: state[k] for k in sw._META_DEFAULTS}


def test_read_meta_empty_when_tab_absent():
    svc = FakeSheets()
    svc._values._tabs.clear()
    meta = sw.read_meta(svc, "SID")
    assert meta["wds_seen"] == {}
    assert meta["wds_snapshot_hashes"] == {}
    assert meta["last_run"] == ""


def test_removed_meta_key_clears_orphan_without_losing_live_state():
    """Migration guard (ADR 013): a Sheet written by the OLD code has an extra
    `mmpc_minutes_found` row, so the retired key sits BETWEEN live keys and its
    removal shifts every later key up one — leaving a stale trailing `last_run`.
    One write with the new (shorter) key set must (a) preserve every live key's
    value, (b) return the FRESH last_run not the orphaned stale one, and (c) drop
    the retired key entirely. This is what write_meta's fixed-span blanking buys."""
    svc = FakeSheets()
    # Seed the pre-ADR-013 layout: 5 data rows, each live key a distinct value,
    # last_run deliberately stale so a positional mis-read would be visible.
    svc._values._tabs[sw.TAB_META] = [
        list(sw.META_HEADERS),
        ["pending_digest", json.dumps([{"document_name": "old-item"}])],
        ["mmpc_minutes_found", json.dumps({"2026-06-10": True})],  # retired key
        ["wds_seen", json.dumps({"qmr": {"records": {"k": "h"}, "last_count": 2}})],
        ["wds_snapshot_hashes", json.dumps({"p1": "oldhash"})],
        ["last_run", json.dumps("2026-01-01T00:00:00")],           # STALE orphan
    ]

    # New code reads (retired key is ignored — not in _META_DEFAULTS), advances
    # last_run, and writes back exactly once, as a real run would.
    state = sw.read_state(svc, "SID")
    assert "mmpc_minutes_found" not in state          # retired key never surfaces
    state["last_run"] = "2026-07-12T06:00:00"
    sw.write_meta(svc, "SID", state)

    reloaded = sw.read_meta(svc, "SID")
    assert reloaded["pending_digest"] == [{"document_name": "old-item"}]       # (a)
    assert reloaded["wds_seen"] == {"qmr": {"records": {"k": "h"}, "last_count": 2}}
    assert reloaded["wds_snapshot_hashes"] == {"p1": "oldhash"}
    assert reloaded["last_run"] == "2026-07-12T06:00:00"                       # (b)
    assert "mmpc_minutes_found" not in reloaded                                 # (c)
