"""
dump_wds_historical.py — one-off bulk dump of Stream C's ~420 pre-existing WDS
records as VISIBLE rows in "WDS Historical Documents" + "WDS Evidence by Risk",
the WDS analog of the nSITE backfill populating "Historical Documents".

Without this, the ~420 records are only ever recorded as opaque id/hash pairs in
the Sheet's _meta `wds_seen` cell (via seed_wds_state.py or check_wds()'s own
first-run silent baseline) — never as rows a human can read. This script fixes
that, once, before `wds.enabled: true` lands.

Per collection: fetch -> historical_events() (kind='historical', reuses the
SAME classify/label/detail/date logic the live watcher uses, via
wds_watcher._event_from_row — no reclassification duplicated here) -> batch-
append to WDS Historical Documents, THEN WDS Evidence by Risk -> re-baseline
`wds_seen[name]` from the SAME fetch just dumped, persisted immediately.

IDEMPOTENCY (a re-run must never double every row): before touching a
collection, check wds_historical_collections_dumped() — if it already has rows
there, skip it entirely. Delete a collection's rows from WDS Historical
Documents first if you deliberately want to re-dump it.

ORDERING (why Historical is written before Evidence, not after): the two
appends aren't atomic — a crash between them leaves Historical rows durable but
Evidence rows missing for that collection. Re-running then SKIPS the collection
(the idempotency guard fires on Historical, not Evidence), so the gap is
detectable (operator sees N historical / 0 evidence for a collection that should
have both) rather than a silent double-write. The alternative order (Evidence
first) would instead risk *duplicating* evidence rows on a crash-and-retry —
worse for an evidentiary count. For `qmr` and `annual`, EVERY record classifies
as doc_type=='evidence', so the printed "N historical, M evidence" line should
show N == M for those two collections — a quick sanity check that nothing was
cut off mid-run.

Needs the Sheets creds the watcher uses (GDRIVE_SA_KEY + GSHEET_ID). No Claude,
no SMTP, no OAuth/Drive — it only reads WDS (public) and writes Sheet rows +
one _meta cell per collection.

  python scripts/dump_wds_historical.py            # dump all configured collections
  python scripts/dump_wds_historical.py qmr annual # dump only these
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client as dc
import sheet_writer as sw
import wds_watcher as ww
import wds_client as wc
from risk_register import RISK_NAMES
from config_loader import load_config


def main() -> int:
    cfg = load_config()
    wds_cfg = cfg.get("wds") or {}
    w = str(wds_cfg.get("site_id", "475946"))
    which = sys.argv[1:] or (wds_cfg.get("collections") or list(ww.COLLECTIONS))

    sheet_id = os.environ.get("GSHEET_ID")
    if not sheet_id:
        print("Set GSHEET_ID (and GDRIVE_SA_KEY) first.")
        return 2

    sheets = dc.sheets_service()
    sw.ensure_wds_tabs(sheets, sheet_id)
    state = sw.read_state(sheets, sheet_id)
    seen = state.setdefault("wds_seen", {})

    already = sw.wds_historical_collections_dumped(sheets, sheet_id)
    link = ww.wds_link(w)

    total_hist = total_ev = 0
    for name in which:
        if name not in ww.COLLECTIONS:
            print(f"  ? unknown collection {name!r} — skipping")
            continue
        if name in already:
            print(f"  skip {name}: already has rows in {sw.TAB_WDS_HISTORICAL!r} — "
                  f"delete its rows there first to force a re-dump.")
            continue
        try:
            rows = wc.FETCHERS[name](w)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name}: fetch failed — {e} (nothing written)")
            continue

        events = ww.historical_events(name, rows, wds_cfg)
        for ev in events:
            ev["link"] = link
        hist_rows = [sw.wds_event_row(ev) for ev in events]
        ev_rows = [row for ev in events for row in sw.wds_evidence_rows(ev, RISK_NAMES)]

        # Historical rows FIRST, then Evidence — see module docstring on why this
        # ordering, not the reverse, is the safer crash-window default.
        sw.append_rows(sheets, sheet_id, sw.TAB_WDS_HISTORICAL, hist_rows)
        sw.append_rows(sheets, sheet_id, sw.TAB_WDS_EVIDENCE, ev_rows)

        # Re-baseline from the SAME fetch just dumped, so wds_seen matches exactly
        # what's now visible in WDS Historical Documents (not a possibly-stale
        # earlier baseline from an unrelated seed_wds_state.py run).
        _events, entry, _note = ww.diff_collection(name, rows, {"records": {}, "last_count": 0}, wds_cfg)
        seen[name] = entry
        sw.write_meta(sheets, sheet_id, state)

        total_hist += len(hist_rows)
        total_ev += len(ev_rows)
        print(f"  dumped {name}: {len(hist_rows)} historical row(s), {len(ev_rows)} evidence row(s)")

    print(f"Done: {total_hist} historical row(s), {total_ev} evidence row(s) across "
          f"{len(which)} collection(s). No alerts sent. Safe to set wds.enabled: true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
