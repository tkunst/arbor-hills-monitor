"""One-off: run the 12-month CivicClerk keyword-watch historical backfill
(ADR 036) exactly once and email Trisha the summary report.

The real logic lives in civicclerk_watcher.run_historical_backfill — this is
just the thin invocation wrapper (matches this repo's convention: e.g.
scripts/gen_findings_feed.py around findings_feed.py). Delete this file (and
the temporary workflow that runs it, oneoff-meeting-watch-keyword-backfill.yml)
once the send is confirmed — this is a one-time sweep, not a capability the
repo carries forward. The ONGOING keyword watch lives permanently in
civicclerk_watcher.run() (see ADR 036), gated on civicclerk_watch.keyword_scan
.enabled; this script only seeds its baselines and reports the history.
"""
import os
import sys

# scripts/ is sys.path[0] when run as `python3 scripts/oneoff_...py`, not the
# repo root — same fix as scripts/gen_findings_feed.py needs for its imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import civicclerk_watcher as cw  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(cw.run_historical_backfill(months=12))
