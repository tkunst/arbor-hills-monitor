#!/usr/bin/env python3
"""gen_findings_feed.py — regenerate the public Findings feed (site/findings/)
from the case-file Sheet. Thin I/O wrapper around findings_feed.py's pure
render logic: read-only against the Sheet — only `values().get` against
New Documents / Historical Documents (see CLAUDE.md's "Two Sheets" invariant;
GSHEET_ID is the public/operator-visible one) — then writes static HTML.

Usage: python3 scripts/gen_findings_feed.py
Reads GDRIVE_SA_KEY / GSHEET_ID from the environment, same as every other
entrypoint in this repo (see .env.example). Run by .github/workflows/
findings-feed.yml on a schedule; the workflow commits the result if it
changed. Also fine to run locally to preview a regeneration before pushing.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone

# Repo root on path so `import drive_client` etc. resolve when run as
# `python3 scripts/gen_findings_feed.py` — scripts/ is sys.path[0], not the
# repo root (same idiom as scripts/term_search.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client  # noqa: E402
import findings_feed  # noqa: E402
import sheet_writer  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "site", "findings")


_COUNT_RE = re.compile(r'class="findings-count">([\d,]+) documents')


def _previous_total(out_dir: str) -> int | None:
    """The document count the last run wrote to index.html, read back out of
    its own rendered `findings-count` line — None on the very first run (no
    index.html yet) or if it can't be parsed. Feeds findings_feed.
    is_suspicious_shrink() so a bad Sheet read never silently auto-commits a
    collapsed public feed (see that function's docstring)."""
    path = os.path.join(out_dir, "index.html")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        m = _COUNT_RE.search(f.read())
    return int(m.group(1).replace(",", "")) if m else None


def _tab_values(service, sheet_id: str, tab: str) -> list:
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{tab}'!A2:I")
        .execute(num_retries=drive_client.GOOGLE_API_NUM_RETRIES)
    )
    return resp.get("values", [])


def main() -> None:
    sheet_id = os.environ["GSHEET_ID"]
    service = drive_client.sheets_service()

    new_rows = _tab_values(service, sheet_id, sheet_writer.TAB_NEW)
    historical_rows = _tab_values(service, sheet_id, sheet_writer.TAB_HISTORICAL)
    rows = findings_feed.merge_and_sort(new_rows, historical_rows)

    previous_total = _previous_total(OUT_DIR)
    if findings_feed.is_suspicious_shrink(previous_total, len(rows)):
        raise SystemExit(
            f"Refusing to write: {len(rows)} document(s) read, down from "
            f"{previous_total} last run (more than a "
            f"{int((1 - findings_feed.SHRINK_GUARD_RATIO) * 100)}% drop). "
            "This looks like a bad Sheet read, not real data loss — the feed "
            "only grows in normal operation. Leaving the existing site/"
            "findings/ untouched; investigate before re-running."
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pages = findings_feed.build_pages(rows, generated_at)

    os.makedirs(OUT_DIR, exist_ok=True)
    # Clear existing pages first — a shrinking dataset (a purge/re-dump) must
    # not leave an orphaned high-numbered page reachable after the real page
    # count drops.
    for name in os.listdir(OUT_DIR):
        if name.endswith(".html"):
            os.remove(os.path.join(OUT_DIR, name))
    for filename, page_html in pages.items():
        with open(os.path.join(OUT_DIR, filename), "w", encoding="utf-8") as f:
            f.write(page_html)

    print(f"Wrote {len(pages)} page(s), {len(rows)} document(s), to {OUT_DIR}")


if __name__ == "__main__":
    main()
