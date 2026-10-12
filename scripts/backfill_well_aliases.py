"""backfill_well_aliases.py — one-shot ADR-068 well-ID alias relabel of EXISTING
Measurements rows.

The live pipeline (watcher/backfill, from ADR 068 on) relabels a pre-rename
spelling (e.g. `AHWW0290`) to its canonical id (`AHWW290R`) as rows are written.
Rows already on the Sheet keep their old spelling until this script relabels them.

  python scripts/backfill_well_aliases.py              # DRY-RUN (default): no writes
  python scripts/backfill_well_aliases.py --apply      # Trisha only, after review

Rule: EXACTLY the live rule — `well_aliases.canonical_well_id()`, an exact alias-key
match after stripping trailing `*`, using the SAME committed map
(`data/well_aliases.json`). No fuzzy, short-form, spacing or prepend-AH rules.

What --apply writes, per matched row: the "Well ID" cell -> canonical id, and the
"Well ID As Filed" cell -> the old id (so the relabel stays auditable on the Sheet
itself). Nothing else. Safety:
  * DRY-RUN BY DEFAULT: prints per-key row counts + a sample; writes nothing.
  * IDEMPOTENT: a relabeled row's Well ID is canonical, never an alias key, so a
    re-run plans nothing for it.
  * REFUSES unless the live header already has "Well ID As Filed" (it appears
    once the ADR-068 code's ensure_tabs has run on a real watcher/backfill run).
  * TOCTOU-SAFE: --apply re-reads the tab and re-derives the plan against CURRENT
    row numbers immediately before one batchUpdate (a FORCE_REPROCESS purge can
    shift rows between the dry-run and the apply).
  * REVERSIBLE: --apply writes a JSON manifest (row, old, new) BEFORE writing;
    revert = set each row's Well ID back to `old` and clear Well ID As Filed.

This mutates the PUBLIC case-file Sheet, so --apply is a human step — never run
by the overnight loop. Credentials: GDRIVE_SA_KEY + GSHEET_ID (a `.env` in the
current directory is loaded if present).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import well_aliases  # noqa: E402

MEASUREMENTS_TAB = "Measurements"
AS_FILED = "Well ID As Filed"


# ---------------------------------------------------------------------------
# Pure helpers — unit-tested without credentials
# ---------------------------------------------------------------------------


def _col_letter(idx: int) -> str:
    s, n = "", idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(row: list, idx) -> str:
    return row[idx] if idx is not None and idx < len(row) else ""


def build_plan(header: list, rows: list, alias_map: dict) -> list[dict]:
    """One entry per data row whose Well ID the live rule would relabel.
    `rows` includes the header at index 0; `row` is the 1-based Sheet row."""
    wi = header.index("Well ID")
    fi = header.index(AS_FILED) if AS_FILED in header else None
    li = header.index("Link") if "Link" in header else None
    plan = []
    for i, r in enumerate(rows[1:], start=2):
        old = _cell(r, wi)
        new = well_aliases.canonical_well_id(old, alias_map)
        if new != old:
            plan.append({
                "row": i, "old": old, "new": new,
                "as_filed_existing": _cell(r, fi),
                "link": _cell(r, li),
                "sample": [_cell(r, header.index(h)) if h in header else ""
                           for h in ("As-Of Date", "Metric", "Value", "Unit", "Document Name")],
            })
    return plan


def per_key_counts(plan: list[dict]) -> Counter:
    return Counter((u["old"], u["new"]) for u in plan)


def render_summary(header: list, rows: list, plan: list[dict], alias_map: dict,
                   sample_n: int = 15) -> str:
    counts = per_key_counts(plan)
    L = [f"Measurements data rows: {len(rows) - 1}",
         f"Header has '{AS_FILED}': {'yes' if AS_FILED in header else 'NO (apply will refuse)'}",
         f"Alias keys in map: {len(alias_map)}; keys that match live rows: "
         f"{len({o.rstrip('*') for o, _ in counts})}; rows to relabel: {len(plan)}",
         "", "Per key (old -> canonical: rows):"]
    for (old, new), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        L.append(f"  {old:<12} -> {new:<10} {n:>5}")
    if plan:
        L += ["", f"Sample ({min(sample_n, len(plan))} of {len(plan)}): "
                  "row | old -> new | as-of | metric | value unit | document | link doc id"]
        step = max(1, len(plan) // sample_n)
        for u in plan[::step][:sample_n]:
            asof, metric, value, unit, doc = u["sample"]
            segs = [s for s in str(u["link"]).split("?")[0].split("/")
                    if s and s not in ("view", "edit")]
            tail = segs[-1] if segs else ""
            L.append(f"  {u['row']} | {u['old']} -> {u['new']} | {asof} | {metric} | "
                     f"{value} {unit} | {doc[:40]} | {tail}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def read_measurements(service, sheet_id: str) -> tuple[list, list]:
    resp = (service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"'{MEASUREMENTS_TAB}'!A1:Z")
            .execute(num_retries=5))
    rows = resp.get("values", [])
    if not rows:
        raise RuntimeError("Measurements tab is empty — refusing to proceed.")
    return rows[0], rows


def apply_backfill(service, sheet_id: str, alias_map: dict, *, manifest_path: str,
                   meta: dict) -> list[dict]:
    """Re-read, re-plan against CURRENT rows, write the manifest, then ONE
    batchUpdate of the Well ID + Well ID As Filed cells."""
    header, rows = read_measurements(service, sheet_id)
    if AS_FILED not in header:
        raise RuntimeError(f"live Measurements header has no '{AS_FILED}' column yet — "
                           "let one ADR-068 watcher/backfill run (ensure_tabs) add it first.")
    plan = build_plan(header, rows, alias_map)
    if not plan:
        return plan
    with open(manifest_path, "w") as fh:
        json.dump({**meta, "updates": [{k: u[k] for k in ("row", "old", "new")}
                                       for u in plan]}, fh, indent=0)
    wcol = _col_letter(header.index("Well ID"))
    fcol = _col_letter(header.index(AS_FILED))
    data = []
    for u in plan:
        data.append({"range": f"'{MEASUREMENTS_TAB}'!{wcol}{u['row']}", "values": [[u["new"]]]})
        if not u["as_filed_existing"]:
            data.append({"range": f"'{MEASUREMENTS_TAB}'!{fcol}{u['row']}", "values": [[u["old"]]]})
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id, body={"valueInputOption": "RAW", "data": data},
    ).execute(num_retries=5)
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ADR-068 well-ID alias relabel of existing Measurements rows.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write the live Sheet (default: dry-run, no writes).")
    ap.add_argument("--manifest", default="backfill_well_aliases_manifest.json",
                    help="Per-row revert manifest written on --apply (gitignored *.json).")
    args = ap.parse_args(argv)

    from backfill_metric_taxonomy import _load_dotenv
    _load_dotenv()
    import drive_client

    alias_map = well_aliases.load_alias_map()
    sheet_id = os.environ["GSHEET_ID"]
    service = drive_client.sheets_service()
    header, rows = read_measurements(service, sheet_id)
    plan = build_plan(header, rows, alias_map)
    print(render_summary(header, rows, plan, alias_map))

    if not args.apply:
        print("\nDRY-RUN: no Sheet writes. Re-run with --apply to relabel the live tab.")
        return 0
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    applied = apply_backfill(service, sheet_id, alias_map, manifest_path=args.manifest,
                             meta={"generated_at": generated_at,
                                   "alias_count": len(alias_map)})
    print(f"\nDone. Relabeled {len(applied)} rows. Revert manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
