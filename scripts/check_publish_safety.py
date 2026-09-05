#!/usr/bin/env python3
"""check_publish_safety.py -- the pre-publish GATE for the public Public Records
feed. Deterministic, NO-LLM (see name_check.py).

Scans exactly the fields that render on the page (via the same parse functions
the generator uses), for personal names + internal markers:

- HAND-CURATED published fields (document_name/title + source/source_public):
  a reliable DENYLIST hit is a HARD BLOCK -- exit 1, so findings-feed.yml's
  commit/push step never runs and nothing deploys. A heuristic (advisory) hit
  is a WARNING only (a possible NEW name to confirm) -- never blocks, because a
  false positive there can't be "removed" and would wedge publishing.

- AUTO feed published fields (document_name + summary + key_data_point): a
  denylist hit is WARN-ONLY. The live auto feed already carries some names
  (from nSITE titles / the classifier) -- a pre-existing exposure tracked as
  its own cleanup, deliberately NOT allowed to block the daily regeneration
  here (scope decision, 2026-09-05).

Wire this as a findings-feed.yml step BEFORE "Commit and push": a non-zero exit
fails the job before the commit, gating the deploy.

Usage: python3 scripts/check_publish_safety.py
Reads GDRIVE_SA_KEY / GSHEET_ID from the environment. Read-only against the Sheet.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client  # noqa: E402
import findings_feed  # noqa: E402
import name_check  # noqa: E402
import sheet_writer  # noqa: E402

# The rendered text fields to scan, per source (see findings_feed.render_entry).
_HANDCURATED_FIELDS = ("document_name", "source")
_AUTO_FIELDS = ("document_name", "summary", "key_data_point")


def _scan_fields(row: dict, fields) -> dict:
    """{denylist:[...], heuristic:[...]} aggregated across the row's published
    fields, each hit annotated with the field it came from."""
    deny, heur = [], []
    for f in fields:
        text = row.get(f) or ""
        for h in name_check.find_denylist_hits(text):
            deny.append({**h, "field": f})
        for h in name_check.find_heuristic_hits(text):
            heur.append({**h, "field": f})
    return {"denylist": deny, "heuristic": heur}


def evaluate(handcurated_parsed: list[dict], auto_parsed: list[dict]) -> dict:
    """Pure: returns {block:[...], warn_handcurated:[...], warn_auto:[...]}.
    `block` is the list of hand-curated rows with a reliable denylist hit -- a
    non-empty `block` means the gate must fail (exit 1)."""
    block, warn_hc, warn_auto = [], [], []
    for row in handcurated_parsed:
        res = _scan_fields(row, _HANDCURATED_FIELDS)
        if res["denylist"]:
            block.append({"name": row.get("document_name") or "(untitled)", "hits": res["denylist"]})
        if res["heuristic"]:
            warn_hc.append({"name": row.get("document_name") or "(untitled)", "hits": res["heuristic"]})
    for row in auto_parsed:
        res = _scan_fields(row, _AUTO_FIELDS)
        if res["denylist"]:
            warn_auto.append({"name": row.get("document_name") or "(untitled)", "hits": res["denylist"]})
    return {"block": block, "warn_handcurated": warn_hc, "warn_auto": warn_auto}


def _fmt(hits) -> str:
    return ", ".join(f"{h['match']} (in {h['field']})" for h in hits)


def main() -> int:
    sheet_id = os.environ["GSHEET_ID"]
    service = drive_client.sheets_service()

    def tab(name, a1):
        return (
            service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"'{name}'!{a1}")
            .execute(num_retries=drive_client.GOOGLE_API_NUM_RETRIES)
        ).get("values", [])

    handcurated = findings_feed.parse_handcurated_rows(
        tab(sheet_writer.TAB_HANDCURATED, "A2:M"))
    auto = (findings_feed.parse_feed_rows(tab(sheet_writer.TAB_NEW, "A2:I"))
            + findings_feed.parse_feed_rows(tab(sheet_writer.TAB_HISTORICAL, "A2:I")))

    result = evaluate(handcurated, auto)

    for w in result["warn_auto"]:
        print(f"::warning title=Name in auto feed (pre-existing, not blocking)::"
              f"{w['name']} -- {_fmt(w['hits'])}")
    for w in result["warn_handcurated"]:
        print(f"::warning title=Possible new name in hand-curated field (review)::"
              f"{w['name']} -- {_fmt(w['hits'])}")

    if result["block"]:
        for b in result["block"]:
            print(f"::error title=Personal name / internal marker in a PUBLISHED "
                  f"hand-curated field::{b['name']} -- {_fmt(b['hits'])}")
        print(f"\nPUBLISH BLOCKED: {len(result['block'])} hand-curated record(s) would "
              f"publish a personal name or internal marker. Redact the title / "
              f"source_public in the Sheet (keep org/role/date, drop the person), "
              f"then re-run. See name_check.py.")
        return 1

    print(f"publish-safety OK: {len(handcurated)} hand-curated + {len(auto)} auto "
          f"rows scanned; no blocking names/markers in published hand-curated fields."
          + (f" ({len(result['warn_auto'])} pre-existing auto-feed name warning(s).)"
             if result["warn_auto"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
