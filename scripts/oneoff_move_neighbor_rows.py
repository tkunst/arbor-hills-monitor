#!/usr/bin/env python3
"""ONE-OFF (ADR 064): move the neighbor sites' already-written rows off the
public Arbor Hills tabs.

The 2026-09-25 roster expansion (ADRs 044-057) put neighboring nSITE sites in
`facilities:` as if they were Arbor Hills, so ~270 of their documents landed in
New/Historical Documents (and thus the public-records pages), Evidence by Risk,
Measurements and Compliance Deadlines, and some were queued in `_meta`
pending_digest for the coalition digest. Going forward the watcher routes them
correctly; this script fixes what is already written:

  * related-scope facilities (config.yml `scope: related`): feed rows are COPIED
    to "Related Documents", then removed from the core tabs;
  * the dropped neighbor-development sites (no longer in `facilities:`): rows
    are removed from the core tabs (the JSON backup is their record);
  * matching `pending_digest` / `pending_urgent_recap` entries are removed.

Matching is by the exact Facility cell (the facility name the pipeline wrote).
Default is a DRY RUN that changes nothing. `--apply` first writes a full JSON
backup of every tab it touches (plus `_meta`) to `--backup-dir`, which must be
outside the repo (data files are never committed), then applies.

Usage:
  python scripts/oneoff_move_neighbor_rows.py                       # dry run
  python scripts/oneoff_move_neighbor_rows.py --apply --backup-dir DIR

Delete this script (and its test) once applied — like the other oneoff_* scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sheet_writer as sw  # noqa: E402
from config_loader import load_config  # noqa: E402
from drive_client import GOOGLE_API_NUM_RETRIES  # noqa: E402

# The eight sites dropped from `facilities:` by ADR 064, by the facility name
# their rows carry (copied verbatim from config.yml as of 2026-10-06).
DROPPED_NAMES = frozenset({
    "Plymouth Township Exchange, Five Mile Rd (WRP037948)",
    "7480 Napier Rd lumber facility, Five Mile & Napier (WRP039817)",
    "DTE work, N side of Five Mile Napier to Ridge (WRP039922)",
    "Five Mile Rd E of Napier commercial pre-app (HQG-PB0D-FXQ6N)",
    "Northville Twp site, NW corner Five Mile & Ridge (WRP037947)",
    "Toll Brothers Coldwater Ridge aquatic herbicide COC (ANC9430361)",
    "Coldwater Ridge residential development, Ridge Rd (WRP041048)",
    "Ridge Rd structure replacement over Johnson Drain (WRP017601)",
})

FEED_TABS = (sw.TAB_NEW, sw.TAB_HISTORICAL)
# tab -> 0-based index of its facility column
TABS = {
    sw.TAB_NEW: sw.FEED_HEADERS.index("Facility"),
    sw.TAB_HISTORICAL: sw.FEED_HEADERS.index("Facility"),
    sw.TAB_EVIDENCE: sw.EVIDENCE_HEADERS.index("Facility"),
    sw.TAB_MEASUREMENTS: sw.MEASUREMENTS_HEADERS.index("Facility"),
    sw.TAB_COMPLIANCE_DEADLINES: sw.COMPLIANCE_DEADLINE_HEADERS.index("Facility / Site"),
}
FEED_LINK = sw.FEED_HEADERS.index("Link")


def related_names(cfg: dict) -> frozenset:
    return frozenset(f["name"] for f in cfg["facilities"] if f.get("scope") == "related")


def plan(values_by_tab: dict, related: frozenset, dropped: frozenset) -> dict:
    """Pure. values_by_tab: {tab: data rows (header excluded)}. Returns
    {"delete": {tab: [0-based data-row index]}, "copy": [feed rows for Related
    Documents], "links": set of feed-row links being moved/removed}."""
    targets = related | dropped
    delete: dict = {}
    copy: list = []
    links: set = set()
    for tab, col in TABS.items():
        rows = values_by_tab.get(tab, [])
        idx = [i for i, r in enumerate(rows) if len(r) > col and r[col] in targets]
        delete[tab] = idx
        if tab in FEED_TABS:
            for i in idx:
                r = rows[i]
                if len(r) > FEED_LINK and r[FEED_LINK]:
                    links.add(r[FEED_LINK])
                if r[TABS[tab]] in related:
                    copy.append(r)
    return {"delete": delete, "copy": copy, "links": links}


def prune_pending(entries: list, links: set) -> tuple[list, list]:
    """Pure. Split pending digest/recap records into (kept, removed) by link."""
    kept = [e for e in entries if e.get("link") not in links]
    removed = [e for e in entries if e.get("link") in links]
    return kept, removed


def _read(service, sheet_id: str, tab: str) -> list:
    resp = (service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"'{tab}'")
            .execute(num_retries=GOOGLE_API_NUM_RETRIES))
    return resp.get("values", [])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", help="required with --apply; outside the repo")
    args = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    import drive_client as dc

    cfg = load_config()
    related = related_names(cfg)
    stale = DROPPED_NAMES & {f["name"] for f in cfg["facilities"]}
    if stale:
        print(f"refusing: still in facilities: {sorted(stale)}")
        return 2
    sheet_id = os.environ["GSHEET_ID"]
    service = dc.sheets_service()

    full = {tab: _read(service, sheet_id, tab) for tab in TABS}
    p = plan({t: v[1:] for t, v in full.items()}, related, DROPPED_NAMES)
    state = sw.read_state(service, sheet_id)
    kept_d, rm_d = prune_pending(state["pending_digest"], p["links"])
    kept_u, rm_u = prune_pending(state["pending_urgent_recap"], p["links"])

    for tab, idx in p["delete"].items():
        print(f"{tab}: remove {len(idx)} row(s)")
    print(f"{sw.TAB_RELATED}: copy {len(p['copy'])} row(s)")
    print(f"pending_digest: remove {len(rm_d)}; pending_urgent_recap: remove {len(rm_u)}")
    if not args.apply:
        print("DRY RUN — nothing changed. Re-run with --apply --backup-dir DIR.")
        return 0

    if not args.backup_dir:
        print("refusing: --apply needs --backup-dir")
        return 2
    repo = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    bdir = os.path.realpath(args.backup_dir)
    if bdir == repo or bdir.startswith(repo + os.sep):
        print("refusing: --backup-dir must be outside the repo")
        return 2
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = os.path.join(bdir, f"neighbor-rows-backup-{stamp}.json")
    with open(backup, "w") as fh:
        json.dump({"sheet_id": sheet_id, "tabs": full, "meta": _read(service, sheet_id, sw.TAB_META),
                   "plan": {"delete": p["delete"], "copy": p["copy"]},
                   "pending_removed": {"digest": rm_d, "urgent_recap": rm_u}}, fh)
    print(f"backup written: {backup}")

    # 1. Copy first, so a crash after this point leaves duplicates, never a loss.
    sw.ensure_tabs(service, sheet_id)
    sw.append_rows(service, sheet_id, sw.TAB_RELATED, p["copy"])

    # 2. Delete bottom-up per tab, after re-checking each row is unchanged.
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute(
        num_retries=GOOGLE_API_NUM_RETRIES)
    gid = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    requests = []
    for tab, idx in p["delete"].items():
        now = _read(service, sheet_id, tab)[1:]
        for i in sorted(idx, reverse=True):
            if i >= len(now) or now[i] != full[tab][1:][i]:
                print(f"refusing: {tab} changed since it was read; nothing deleted")
                return 3
            requests.append({"deleteDimension": {"range": {
                "sheetId": gid[tab], "dimension": "ROWS",
                "startIndex": i + 1, "endIndex": i + 2}}})
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute(num_retries=GOOGLE_API_NUM_RETRIES)

    # 3. Pending digest / recap.
    if rm_d or rm_u:
        state["pending_digest"], state["pending_urgent_recap"] = kept_d, kept_u
        sw.write_meta(service, sheet_id, state)

    # 4. Derived tabs.
    from risk_register import RISK_REGISTER
    sw.rebuild_risk_register_tab(service, sheet_id, RISK_REGISTER)
    sw.rebuild_all_evidence_tab(service, sheet_id)
    print(f"done: {len(requests)} row(s) removed, {len(p['copy'])} copied, "
          f"{len(rm_d) + len(rm_u)} pending entr(ies) removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
