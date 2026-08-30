"""backfill_metric_taxonomy.py — one-shot ADR-034 `other`-bucket backfill.

Expanding the metric enum (egle_doc_parser.METRIC_VALUES) only affects NEWLY
parsed documents. The thousands of Measurements rows already written as `other`
stay `other` until reclassified. This one-shot script does that reclassification,
in place, on the live case-file Sheet's Measurements tab.

How it works:
  1. Read every Measurements row; select the ones whose Metric column == "other".
  2. Deduplicate by NOTE text (the unit does not identify the substance, so two
     rows with the same note map identically — ADR 034 / the reviewed draft),
     and classify each DISTINCT note ONCE via the SAME model-based classifier the
     live pipeline uses: `egle_doc_parser.classify_note_metric`. One brain, one
     vocabulary — never a second, forkable classifier.
  3. Update ONLY the Metric column (col C) for rows whose note resolved to a named
     metric. value / unit / basis / well_id / as_of_date / note are left exactly
     as they are — this changes ONLY the metric label.

Safety properties (all required by the handoff):
  * DRY-RUN BY DEFAULT. With no flag it classifies, writes a human-reviewable
    Markdown report (before/after metric distribution + the full note->metric
    mapping), and writes NOTHING to the Sheet. `--apply` performs the in-place
    update and additionally emits a JSON revert manifest.
  * IDEMPOTENT. Only rows whose current Metric is exactly "other" are ever
    considered, so re-running after an apply is a no-op on already-reclassified
    rows. A note that resolves back to "other" is skipped (no wasted write).
  * REVERSIBLE. Because every touched row's old value is uniformly "other", the
    committed note->metric mapping in the report fully determines every remap;
    `--apply` also writes a per-row manifest (row, old, new, note) so a bad batch
    can be set back to "other".
  * RESUMABLE. Distinct-note classifications are cached to a JSON file as they are
    computed, so an interrupted run (network blip, ^C) resumes without re-billing
    already-classified notes.

This mutates a PUBLIC, operator-visible Sheet, so `--apply` is deliberately a
separate, human-initiated step — never run automatically by the overnight loop.
See ADR 034.

Credentials (same as the rest of the monitor): GDRIVE_SA_KEY (service-account
JSON path), GSHEET_ID (the case-file Sheet), ANTHROPIC_API_KEY. A `.env` in the
current directory is loaded automatically if present.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

# The Measurements tab and its metric/note columns are owned by sheet_writer; we
# reference the tab name by string to avoid importing the Sheets-writing surface
# just for a constant. Column positions are resolved from the live header row, so
# a future column reorder cannot silently corrupt the wrong cell.
MEASUREMENTS_TAB = "Measurements"


# ---------------------------------------------------------------------------
# Pure helpers (no I/O, no network) — unit-tested without any credentials
# ---------------------------------------------------------------------------


def _cell(row: list, i: int) -> str:
    """Trailing empty cells are omitted by the Sheets API, so index safely."""
    return row[i] if i < len(row) else ""


def _md_inline(text: str) -> str:
    """Sanitize a note for display inside a Markdown table code span: neutralize
    the pipe (column sep), backtick (code-span delimiter) and any newline so one
    odd note can't break the table. Display-only — never affects a Sheet write."""
    return (text or "").replace("\\", "\\\\").replace("|", "\\|") \
        .replace("`", "'").replace("\n", " ").replace("\r", " ")


def _col_letter(idx0: int) -> str:
    """0-based column index -> A1 column letter (0 -> 'A', 2 -> 'C', 26 -> 'AA')."""
    n = idx0 + 1
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def select_other_rows(header: list, rows: list) -> list[dict]:
    """Rows whose Metric == 'other', as dicts {row, note, unit}. `row` is the
    1-based SHEET row number (row 1 is the header, so the first data row is 2)."""
    mi = header.index("Metric")
    ni = header.index("Note")
    ui = header.index("Unit")
    out = []
    for offset, row in enumerate(rows[1:], start=2):
        if _cell(row, mi) == "other":
            out.append({"row": offset, "note": _cell(row, ni), "unit": _cell(row, ui)})
    return out


def distinct_notes(other: list[dict]) -> dict[str, str]:
    """Distinct note -> a representative unit (first seen). Classification keys on
    the note alone; the unit rides along only as weak context for the model."""
    rep: dict[str, str] = {}
    for r in other:
        rep.setdefault(r["note"], r["unit"])
    return rep


def note_frequencies(other: list[dict]) -> Counter:
    """How many `other` rows carry each distinct note. Used to rank a bounded
    (--limit) sample toward the highest-coverage notes, and to report coverage."""
    return Counter(r["note"] for r in other)


def top_notes_by_frequency(rep: dict[str, str], freq: Counter, n: int) -> dict[str, str]:
    """The n most common notes (by row count), preserving rep's unit mapping.
    A bounded sample of these covers the most rows per classification call."""
    keep = [note for note, _ in freq.most_common(n)]
    return {note: rep[note] for note in keep}


def build_plan(other: list[dict], note_to_metric: dict[str, str]) -> list[dict]:
    """One update per `other` row whose note resolved to a NAMED metric. Skips
    rows that stayed 'other' (no-op). Each item: {row, old, new, note}."""
    plan = []
    for r in other:
        new = note_to_metric.get(r["note"], "other")
        if new != "other":
            plan.append({"row": r["row"], "old": "other", "new": new, "note": r["note"]})
    return plan


def metric_distribution(header: list, rows: list) -> Counter:
    mi = header.index("Metric")
    return Counter(_cell(r, mi) for r in rows[1:])


def project_after(before: Counter, plan: list[dict]) -> Counter:
    """Apply the plan to a copy of the current distribution (pure projection)."""
    after = Counter(before)
    for u in plan:
        after["other"] -= 1
        after[u["new"]] += 1
    return after


def render_report_md(
    before: Counter,
    after: Counter,
    plan: list[dict],
    note_to_metric: dict[str, str],
    other: list[dict],
    *,
    generated_at: str,
    model: str,
    applied: bool,
) -> str:
    """Human-reviewable Markdown report — the committed, reviewable, reversible
    record. Pure (no I/O)."""
    total = sum(before.values())
    before_other = before.get("other", 0)
    after_other = after.get("other", 0)
    moved = len(plan)
    # rows the classifier left in `other` (genuinely unplaceable) — the residual
    residual_notes = sorted(
        {r["note"] for r in other if note_to_metric.get(r["note"], "other") == "other"}
    )
    # rows gained per new metric
    gained = Counter(u["new"] for u in plan)

    L = []
    L.append("# Metric taxonomy backfill — dry-run report (ADR 034)")
    L.append("")
    L.append(f"- Generated: {generated_at}")
    L.append(f"- Model: `{model}`")
    L.append(f"- Mode: {'APPLIED to the live Sheet' if applied else 'DRY-RUN (no Sheet writes)'}")
    L.append("")
    L.append("## Headline (the success signal)")
    L.append("")
    pct_before = (before_other / total * 100) if total else 0
    pct_after = (after_other / total * 100) if total else 0
    L.append(f"| | rows | % of tab |")
    L.append(f"|---|---:|---:|")
    L.append(f"| Total Measurements rows | {total} | 100% |")
    L.append(f"| `other` BEFORE | {before_other} | {pct_before:.1f}% |")
    L.append(f"| `other` AFTER (projected) | {after_other} | {pct_after:.1f}% |")
    L.append(f"| Rows reclassified | {moved} | {(moved / total * 100) if total else 0:.1f}% |")
    L.append("")
    L.append("## Rows gained per newly-populated metric")
    L.append("")
    L.append("| metric | rows moved in |")
    L.append("|---|---:|")
    for m, c in gained.most_common():
        L.append(f"| `{m}` | {c} |")
    L.append("")
    L.append(f"## Residual `other` ({len(residual_notes)} distinct notes still unplaceable)")
    L.append("")
    L.append("These notes the classifier could not place on a named metric; they "
             "remain `other` by design (genuine fallback). Eyeball for anything "
             "that SHOULD have a metric:")
    L.append("")
    if residual_notes:
        for n in residual_notes:
            L.append(f"- `{_md_inline(n)}`")
    else:
        L.append("- _(none — every `other` note resolved to a named metric)_")
    L.append("")
    L.append("## Full note → metric mapping (the reversible record)")
    L.append("")
    L.append("Every distinct note among the `other` rows and the metric it was "
             "assigned. Old value is uniformly `other`, so this table fully "
             "determines every row's remap.")
    L.append("")
    L.append("| distinct note | assigned metric |")
    L.append("|---|---|")
    for note in sorted(note_to_metric):
        L.append(f"| `{_md_inline(note)}` | `{note_to_metric[note]}` |")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# I/O — Sheets read/write, classification loop, env
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Best-effort: load a .env in cwd so a local run has creds without exporting.
    Never overrides an already-set variable."""
    if not os.path.exists(".env"):
        return
    with open(".env") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def read_measurements(service, sheet_id: str) -> tuple[list, list]:
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{MEASUREMENTS_TAB}'!A1:K")
        .execute(num_retries=5)
    )
    rows = resp.get("values", [])
    if not rows:
        raise RuntimeError("Measurements tab is empty — refusing to proceed.")
    return rows[0], rows


def classify_notes(
    rep_units: dict[str, str],
    *,
    model: str,
    client,
    cache_path: str,
    classifier=None,
) -> dict[str, str]:
    """Classify each distinct note once (resumable via cache_path). `classifier`
    defaults to egle_doc_parser.classify_note_metric; injectable for tests."""
    if classifier is None:
        import egle_doc_parser
        classifier = egle_doc_parser.classify_note_metric

    note_to_metric: dict[str, str] = {}
    if cache_path and os.path.exists(cache_path):
        with open(cache_path) as fh:
            note_to_metric = json.load(fh)

    todo = [n for n in rep_units if n not in note_to_metric]
    print(f"  {len(rep_units)} distinct notes; {len(note_to_metric)} cached; "
          f"{len(todo)} to classify", file=sys.stderr)
    for i, note in enumerate(todo, start=1):
        if not note.strip():
            note_to_metric[note] = "other"  # no note -> nothing to classify
        else:
            note_to_metric[note] = classifier(
                note, rep_units[note], model=model, client=client
            )
        if cache_path and (i % 25 == 0 or i == len(todo)):
            with open(cache_path, "w") as fh:
                json.dump(note_to_metric, fh)
            print(f"  classified {i}/{len(todo)}", file=sys.stderr)
    return note_to_metric


def apply_backfill(service, sheet_id: str, note_to_metric: dict[str, str], *,
                   manifest_path: str, meta: dict) -> list[dict]:
    """Re-read the tab, re-derive the plan against CURRENT row numbers, write the
    revert manifest, THEN apply. Split out of main() so the TOCTOU-safe re-read is
    unit-testable. Classifications are reused from note_to_metric (already cached),
    so the fresh read costs one Sheets call. Returns the applied plan."""
    header, rows = read_measurements(service, sheet_id)
    other = select_other_rows(header, rows)
    plan = build_plan(other, note_to_metric)
    # Manifest BEFORE apply, from the fully-known plan — a crash mid-apply still
    # leaves a complete per-row record (a superset is safe: reverting a still-
    # `other` row back to `other` is a no-op).
    with open(manifest_path, "w") as fh:
        json.dump({**meta, "updates": plan}, fh, indent=0)
    apply_plan(service, sheet_id, header, plan)
    return plan


def apply_plan(service, sheet_id: str, header: list, plan: list[dict], batch: int = 500) -> None:
    """Write only the Metric column (col C) for each planned row, in batches."""
    col = _col_letter(header.index("Metric"))
    data = [
        {"range": f"'{MEASUREMENTS_TAB}'!{col}{u['row']}", "values": [[u["new"]]]}
        for u in plan
    ]
    for i in range(0, len(data), batch):
        chunk = data[i:i + batch]
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": chunk},
        ).execute(num_retries=5)
        print(f"  applied {min(i + batch, len(data))}/{len(data)} cell updates", file=sys.stderr)


def _default_model() -> str:
    """Match the production classifier's model (config.yml `anthropic_model`)."""
    try:
        import yaml
        with open("config.yml") as fh:
            cfg = yaml.safe_load(fh) or {}
        return cfg.get("anthropic_model", "claude-haiku-4-5")
    except Exception:
        return "claude-haiku-4-5"


def main(argv=None) -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="ADR-034 Measurements `other`-bucket backfill.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write the live Sheet (default: dry-run, no writes).")
    ap.add_argument("--report", default="docs/backfill-reports/metric-taxonomy-dry-run-report.md",
                    help="Markdown report output path (committed record).")
    ap.add_argument("--cache", default="backfill_metric_taxonomy_cache.json",
                    help="Resumable note->metric cache (gitignored *.json).")
    ap.add_argument("--manifest", default="backfill_metric_taxonomy_manifest.json",
                    help="Per-row revert manifest written on --apply (gitignored *.json).")
    ap.add_argument("--model", default=None, help="Override the classifier model.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Classify at most N distinct notes (smoke-testing only).")
    args = ap.parse_args(argv)

    model = args.model or _default_model()
    sheet_id = os.environ["GSHEET_ID"]

    import drive_client
    import anthropic
    service = drive_client.sheets_service()
    client = anthropic.Anthropic()

    print(f"Reading Measurements from Sheet {sheet_id[:8]}…", file=sys.stderr)
    header, rows = read_measurements(service, sheet_id)
    other = select_other_rows(header, rows)
    rep = distinct_notes(other)
    freq = note_frequencies(other)
    print(f"  {len(rows) - 1} data rows; {len(other)} are `other`; "
          f"{len(rep)} distinct notes.", file=sys.stderr)

    if args.limit:
        rep = top_notes_by_frequency(rep, freq, args.limit)
        covered = sum(freq[n] for n in rep)
        pct = (covered / len(other) * 100) if other else 0
        print(f"  --limit: the {len(rep)} highest-volume notes cover "
              f"{covered}/{len(other)} `other` rows ({pct:.1f}%).", file=sys.stderr)

    note_to_metric = classify_notes(
        rep, model=model, client=client, cache_path=args.cache
    )
    plan = build_plan(other, note_to_metric)
    before = metric_distribution(header, rows)
    after = project_after(before, plan)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = render_report_md(
        before, after, plan, note_to_metric, other,
        generated_at=generated_at, model=model, applied=args.apply,
    )
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w") as fh:
        fh.write(report)

    total = sum(before.values())
    print("", file=sys.stderr)
    print(f"other BEFORE : {before.get('other', 0)} "
          f"({before.get('other', 0) / total * 100:.1f}% of {total})", file=sys.stderr)
    print(f"other AFTER  : {after.get('other', 0)} "
          f"({after.get('other', 0) / total * 100:.1f}%) — {len(plan)} rows reclassified",
          file=sys.stderr)
    print(f"report       : {args.report}", file=sys.stderr)

    if not args.apply:
        print("\nDRY-RUN: no Sheet writes. Re-run with --apply to update the live tab.",
              file=sys.stderr)
        return 0

    # apply_backfill re-reads the tab immediately before writing and re-derives
    # the plan against CURRENT row numbers — closing all but a small inter-batch
    # window against a concurrent mid-sheet delete/insert (e.g. purge_doc_rows'
    # deleteDimension on Measurements) that would otherwise shift the absolute
    # rows captured at first read and send a write to the wrong cell.
    print("\nRe-reading Measurements and applying against current rows…", file=sys.stderr)
    applied = apply_backfill(
        service, sheet_id, note_to_metric,
        manifest_path=args.manifest,
        meta={"generated_at": generated_at, "model": model},
    )
    print(f"Done. Applied {len(applied)} metric updates. Revert manifest: "
          f"{args.manifest} (set those rows' Metric back to 'other' to revert).",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
