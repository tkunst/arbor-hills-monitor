#!/usr/bin/env python3
"""scripts/report_tracker_summary.py -- the morning briefing's Check 16
(ADR 071): a READ-ONLY look at the latest `Report Tracker Runs` row on the
PRIVATE Sheet, rendered as one line:

  Missing/overdue reports: 24 open (+2 added, -1 completed since 2026-10-22) · arbor-hills: 18 open (+2/-1) · carleton-farms: 6 open (+0/-0)

Usage:
  python3 scripts/report_tracker_summary.py          # the line
  python3 scripts/report_tracker_summary.py --json   # {"status": ..., "line": ..., ...}

`status` is one of: ok | stale (latest run older than --max-age-days, default
2) | no-runs (tab empty or absent) | disabled (report_tracking.enabled is
false) | skipped (no GSHEET_ID_PRIVATE / credentials) | error. Always exits
0 (the briefing's shell check reads `status`, a crash here must never take
the whole infra snapshot down). Reads a `.env` in the repo root when present
(the same loader the other local scripts use). Nothing is written anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)


def summary_line(run: dict, today: date, max_age_days: int = 2) -> dict:
    """Pure: the latest Runs row (as a {header: value} dict) -> result dict."""
    run_date = (run.get("run_date") or "")[:10]
    try:
        rd = date.fromisoformat(run_date)
    except ValueError:
        return {"status": "error", "line": f"⚠️ report tracker: unreadable run_date {run_date!r}"}
    age = (today - rd).days
    status = "stale" if age > max_age_days else "ok"
    mode = run.get("mode") or ""
    tag = " [shadow]" if mode == "shadow" else ""
    line = (f"Missing/overdue reports{tag}: {run.get('open_count', '?')} open "
            f"(+{run.get('added_today', '0')} added, -{run.get('completed_today', '0')} completed since {run_date})")
    if run.get("per_facility"):
        line += f" · {run['per_facility']}"
    extras = []
    if str(run.get("proposed_matches") or "0") not in ("0", ""):
        extras.append(f"{run['proposed_matches']} proposed match(es) to review")
    if str(run.get("unmatched_periodic") or "0") not in ("0", ""):
        extras.append(f"{run['unmatched_periodic']} unmatched periodic title(s)")
    if str(run.get("bad_rows") or "0") not in ("0", ""):
        extras.append(f"{run['bad_rows']} hand-table row(s) skipped")
    if extras:
        line += " · " + "; ".join(extras)
    if status == "stale":
        line = f"⚠️ report tracker last ran {age} days ago ({run_date}) · " + line
    return {"status": status, "line": line, "run_date": run_date, "mode": mode,
            "open": run.get("open_count", ""), "added": run.get("added_today", ""),
            "completed": run.get("completed_today", ""), "per_facility": run.get("per_facility", ""),
            "age_days": age}


def latest_run(service, sheet_id: str) -> dict | None:
    import sheet_writer as sw
    rows = sw._tab_rows_strict(service, sheet_id, sw.TAB_REPORT_TRACKER_RUNS, "A2:L")
    if not rows:
        return None
    r = list(rows[-1]) + [""] * (len(sw.REPORT_TRACKER_RUN_HEADERS) - len(rows[-1]))
    return dict(zip(sw.REPORT_TRACKER_RUN_HEADERS, (str(v) for v in r)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Latest Report Tracker Runs row, as a briefing line.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-age-days", type=int, default=2)
    args = ap.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    result: dict
    try:
        from config_loader import load_config
        cfg = load_config()
        if not ((cfg.get("report_tracking") or {}).get("enabled")):
            result = {"status": "disabled", "line": "⏭️ report tracker disabled (report_tracking.enabled: false)"}
        else:
            env_path = os.path.join(_REPO, ".env")
            if os.path.exists(env_path):
                with open(env_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            priv = (os.environ.get("GSHEET_ID_PRIVATE") or "").strip()
            if not priv or priv == (os.environ.get("GSHEET_ID") or "").strip():
                result = {"status": "skipped", "line": "⏭️ report tracker: GSHEET_ID_PRIVATE not set locally"}
            elif not os.environ.get("GDRIVE_SA_KEY"):
                result = {"status": "skipped", "line": "⏭️ report tracker: GDRIVE_SA_KEY not set locally"}
            else:
                import drive_client as dc
                run = latest_run(dc.sheets_service(), priv)
                if run is None:
                    result = {"status": "no-runs", "line": "ℹ️ report tracker: no run recorded yet"}
                else:
                    result = summary_line(run, today, args.max_age_days)
    except Exception as e:  # noqa: BLE001 -- never take the infra snapshot down
        result = {"status": "error", "line": f"⚠️ report tracker check failed — {type(e).__name__}"}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["line"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
