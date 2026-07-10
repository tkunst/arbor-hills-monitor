"""
seed_wds_state.py — one-shot baseline for Stream C (WDS solid waste).

Records EVERY current WDS record (site 475946) as "already seen" in the Sheet's
_meta `wds_seen` cell, and sends NO alerts. Run this ONCE before flipping
`wds.enabled: true`, so the first live watcher run alerts only on genuinely-new
or changed records — not the ~420 historical rows.

OPTIONAL: the first enabled watcher run also self-baselines silently if you skip
this (wds_watcher rule B), so this script is a convenience / explicit re-baseline
tool, not a required step. Use it to re-baseline after a known WDS data change you
don't want alerted.

Needs the Sheets creds the watcher uses (GDRIVE_SA_KEY + GSHEET_ID). No Claude,
no SMTP — it only reads WDS (public) and writes one _meta cell.

  python scripts/seed_wds_state.py            # baseline all configured collections
  python scripts/seed_wds_state.py qmr annual # baseline only these
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client as dc
import sheet_writer as sw
import wds_watcher as ww
import wds_client as wc
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
    sw.ensure_tabs(sheets, sheet_id)
    state = sw.read_state(sheets, sheet_id)
    seen = state.setdefault("wds_seen", {})

    total = 0
    for name in which:
        if name not in ww.COLLECTIONS:
            print(f"  ? unknown collection {name!r} — skipping")
            continue
        try:
            rows = wc.FETCHERS[name](w)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name}: fetch failed — {e} (leaving its state untouched)")
            continue
        # Force a baseline via the diff engine's empty-seen path (records all,
        # alerts none), regardless of any existing state.
        _events, entry, note = ww.diff_collection(name, rows, {"records": {}, "last_count": 0}, wds_cfg)
        seen[name] = entry
        total += entry["last_count"]
        print(f"  seeded {name}: {entry['last_count']} record(s)")

    sw.write_meta(sheets, sheet_id, state)
    print(f"Baseline written to _meta wds_seen ({total} records across {len(which)} collection(s)). "
          f"No alerts sent. Safe to set wds.enabled: true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
