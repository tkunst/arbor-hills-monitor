"""
verify_state.py — prove Sheet-backed processing state round-trips against the
REAL Google Sheets API (ADR 006). No Anthropic call, no nSITE call, no cost.

The unit tests exercise read_state / mark_processed / write_meta against a fake
Sheets service — they verify our MODEL of the API, not the API. This closes that
gap: it creates the tabs, appends a throwaway `_state` row, reads it back through
the same read_state the backfill/watcher use, asserts it reduced correctly, then
clears the `_state` body so the tab is clean for the first real backfill.

If this fails or the row doesn't read back, the append-only state isn't
persisting — which would make the backfill reprocess the same batch every run.
Run it BEFORE the first 50-doc backfill.

Needs: GDRIVE_SA_KEY (path to the service-account JSON) and GSHEET_ID. Run it on
a Sheet whose `_state` tab is empty (i.e., before backfill) — it clears that tab
on success.

  python scripts/verify_state.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client as dc
import sheet_writer as sw

SMOKE_ID = "VERIFY-STATE-SMOKE"


def main() -> int:
    if not os.environ.get("GDRIVE_SA_KEY") or not os.environ.get("GSHEET_ID"):
        print("Set GDRIVE_SA_KEY (path to the SA JSON) and GSHEET_ID first.")
        return 2
    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()

    print("ensure_tabs() ...")
    sw.ensure_tabs(sheets, sheet_id)

    payload = {"document_name": "verify_state smoke row", "severity": "routine",
               "doc_type": "other", "risks": []}
    print(f"mark_processed({SMOKE_ID!r}) ...")
    sw.mark_processed(sheets, sheet_id, SMOKE_ID, payload, "verify_state")

    print("read_state() ...")
    state = sw.read_state(sheets, sheet_id)

    ok = SMOKE_ID in state["processed"] and state["processed"][SMOKE_ID] == payload
    if not ok:
        print("\nFAIL: smoke row did not round-trip through read_state.")
        print(f"  processed keys: {list(state['processed'])[:10]}")
        print("  -> appends are not visible to read_state; do NOT start backfill.")
        return 1

    # Clean up: clear the _state body (rows 2+). Safe because this is meant to run
    # before backfill, when the only row is the smoke row we just wrote.
    sheets.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"'{sw.TAB_STATE}'!A2:E", body={}
    ).execute()
    print("\nPASS: Sheet-backed state round-trips against the live API. _state cleared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
