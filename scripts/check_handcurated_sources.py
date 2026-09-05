#!/usr/bin/env python3
"""check_handcurated_sources.py -- a NON-BLOCKING, non-silent source-quality
report for the Hand-Curated Files tab.

Every hand-curated record that reaches the public Public Records feed publishes
a redacted external `source_public` (issuing/holding body) that renders as a
"Source: ..." tag. A row with a BLANK `source_public` renders the visible
placeholder "Source: not stated" instead of silently dropping the tag (see
findings_feed.render_entry) -- the feed never falls back to the internal
`source` column, which may carry personal names. This script reports which
hand-curated rows currently have a blank/whitespace `source_public` so Trisha
can fill them in -- it is deliberately NON-BLOCKING: it always exits 0 and never
fails the feed regeneration. It only *reports* (GitHub Actions ::warning::
annotations + a plain summary), never gates.

This is distinct from any publish GATE (personal names / internal notes) -- this
one is a data-quality nudge about missing sources, not a safety block.

Usage: python3 scripts/check_handcurated_sources.py
Reads GDRIVE_SA_KEY / GSHEET_ID from the environment, same as every other
entrypoint in this repo. Read-only against the Sheet.
"""
from __future__ import annotations

import os
import sys

# Repo root on path so top-level modules resolve when run as
# `python3 scripts/check_handcurated_sources.py` (scripts/ is sys.path[0]).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client  # noqa: E402
import findings_feed  # noqa: E402
import sheet_writer  # noqa: E402


def _blank(v: str | None) -> bool:
    return not (v or "").strip()


def find_blank_source_rows(handcurated_rows: list) -> list[dict]:
    """The parsed Hand-Curated rows whose `source` is blank -- i.e. the ones
    that will render 'Source: not stated' on the public page. Pure; unit-
    testable without any network."""
    parsed = findings_feed.parse_handcurated_rows(handcurated_rows)
    return [r for r in parsed if _blank(r.get("source"))]


def _emit(msg: str) -> None:
    # A GitHub Actions ::warning:: annotation surfaces in the run summary and
    # the Checks UI without failing the job; also print a plain line so the
    # same output is legible when run locally (outside Actions).
    print(f"::warning title=Hand-curated source missing::{msg}")
    print(f"  [source-check] {msg}")


def main() -> int:
    sheet_id = os.environ["GSHEET_ID"]
    service = drive_client.sheets_service()
    rows = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{sheet_writer.TAB_HANDCURATED}'!A2:M")
        .execute(num_retries=drive_client.GOOGLE_API_NUM_RETRIES)
    ).get("values", [])

    blanks = find_blank_source_rows(rows)
    total = len(findings_feed.parse_handcurated_rows(rows))
    if not blanks:
        print(f"[source-check] OK: all {total} hand-curated rows have a source.")
        return 0

    print(
        f"[source-check] {len(blanks)} of {total} hand-curated rows have a BLANK "
        f"source and will publish as 'Source: not stated':"
    )
    for r in blanks:
        name = (r.get("document_name") or "(untitled)").strip()
        date = (r.get("date_filed") or "(no date)").strip()
        _emit(f"{date} -- {name}")
    # NON-BLOCKING: report only, never fail the run.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
