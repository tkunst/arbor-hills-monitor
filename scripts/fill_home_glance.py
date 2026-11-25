#!/usr/bin/env python3
"""fill_home_glance.py -- fill the homepage GlanceCard and "Newest filing"
card (site/index.html) from site/public-records/glance.json and the time of
the last successful scheduled daily.yml run.

Run by pages.yml just before the site is uploaded, so the deployed homepage is
current on every deploy (pages.yml redeploys after every findings-feed run).
The committed site/index.html keeps whatever values it was last filled with
and never carries a "Last checked" line, so a deploy where this step is
skipped or fails still shows no stale timestamp.

Usage: LAST_CHECK_AT=2026-11-12T11:23:45Z python3 scripts/fill_home_glance.py
Standard library only (pages.yml installs no dependencies). Never fails the
deploy: any problem prints a warning and leaves site/index.html untouched.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import home_glance  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO_ROOT, "site", "index.html")
GLANCE = os.path.join(REPO_ROOT, "site", "public-records", "glance.json")


def main() -> int:
    try:
        with open(GLANCE, encoding="utf-8") as f:
            glance = home_glance.load_glance(f.read())
        with open(INDEX, encoding="utf-8") as f:
            page = f.read()
        checked = home_glance.parse_utc(os.environ.get("LAST_CHECK_AT"))
        filled = home_glance.fill(page, glance, checked, datetime.now(timezone.utc))
    except Exception as e:  # noqa: BLE001 -- never fail a deploy over the card
        print(f"::warning::homepage glance not filled ({type(e).__name__}); "
              "deploying the committed values without a Last checked line.")
        return 0
    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(filled)
    print("Homepage glance filled"
          + (" (with Last checked)." if home_glance.checked_is_fresh(
              checked, datetime.now(timezone.utc)) else " (Last checked hidden)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
