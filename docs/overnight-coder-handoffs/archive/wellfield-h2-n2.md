# Overnight-coder handoff — wellfield H₂/N₂ extraction (Attachment 3)

*Staged 2026-08-25. Read `docs/overnight-coder.md` first. This is a **feasibility-gated,
OCR-risk** item: the source tables may be image-only scans that a text parser cannot read.
Per Step 1 you MUST run the extractability spike FIRST and **STOP with a draft PR (no merge)**
if Attachment 3 has no text layer. Recommended model tier: **Sonnet**.*

## Invocation

Branch name suggestion: `wellfield-h2-n2`.

## What this is (and what it is NOT)

The published **wellfield dataset** (a wide CSV, one row per well-reading, released as the
`wellfield-data-*` GitHub release asset) currently carries `methane_CH4_pct`,
`carbon_dioxide_CO2_pct`, `oxygen_O2_pct`, a lumped **`balance_pct`**, and CO. It does **not**
carry N₂ or H₂ as their own columns — even though we hold H₂+N₂ in **Attachment 3** of several
WOI status reports. `balance_pct` is the lumped remainder (N₂ + H₂ + trace); this item breaks
the measured N₂ and H₂ out of that lump into their own columns.

This is a **deterministic table-parser extension** — it is emphatically **NOT** the
`coder:metric-taxonomy` build (that one edits the live LLM classifier `egle_doc_parser.py` and
backfills the Google Sheet's Measurements tab; different file, different data model, different
output). Do not touch `egle_doc_parser.py` or the Measurements tab here.

**It mirrors, almost exactly, how Attachment 2 (CO) was added** to `woi_table_parser.py`
(see `parse_co_data()` at ~line 259 and the `Attachment 2 ... CO Data` header regex at ~line
208). Att-3 (H₂/N₂) is the same shape, a different attachment.

## Source of truth

- **Canonical parser (what you edit):** `woi_table_parser.py` in this repo. It already parses
  Att-1 (`parse_gas_extraction`, census incl. `balance`) and Att-2 (`parse_co_data`, CO). You
  add Att-3.
- **Tests:** `tests/test_woi_parser.py` — add cases alongside the existing Att-1/Att-2 tests.
- **Which reports carry Att 3** (per the wellfield README + HOV-ETLF-CO index §7): **2022 H2,
  Q1 2024, 2024 H2, 2025 H1, 2025 H2, and 2026 H1.** The General/exceedance reports carry NO
  Att 3. The source PDFs live in the Lotext build folder's `source-docs/` (see the follow-on
  below); for the in-repo spike + tests, use whatever WOI specimen PDFs the repo's test
  fixtures already reference, plus one report you confirm has an Att-3 text layer.

## Part A — Feasibility spike + OCR-stop-gate (DO THIS FIRST)

Before writing any parser, for each of the ~6 Att-3-bearing reports, extract the raw text of
the Attachment-3 pages (same PyMuPDF text path the Att-1/Att-2 parsers use) and check:

- **Is there a text layer?** If a report's Att-3 pages return empty/near-empty text, it is an
  **image-only scan** — a text parser will silently return nothing for it. Do **NOT** ship a
  parser that yields empty H₂/N₂ for scanned reports and calls it done.
- **Gate outcome:**
  - If **all** Att-3-bearing reports have a clean text layer → proceed to Part B, merge-eligible.
  - If **some are scanned** → build the parser for the text-extractable ones, but **STOP: open
    a DRAFT PR (no autonomous merge)** that lists exactly which reports need OCR, and surface it
    for Trisha. OCR + re-verification of scanned Att-3 is a per-report judgment job she'll want
    to see, not an autonomous run. (This is the same reason the 2020–2023 downhole extraction
    needed OCR and human validation.)

Report the spike result explicitly in the PR description (per-report: text-layer yes/no, rows found).

## Part B — The Att-3 parser + tests (the in-repo deliverable)

For the text-extractable reports:

1. Add `parse_att3_h2n2(pdf_path, alias_map=None) -> list[...]` to `woi_table_parser.py`,
   modeled on `parse_co_data()`: find the Att-3 header, read the per-well table, return
   `(well_id, date, h2_pct, n2_pct)` records. Use the same well-ID handling/`alias_map` hook as
   the sibling parsers so IDs stay consistent.
2. **Cross-check against `balance`:** for a well+date present in both Att-1 and Att-3, the
   extracted **N₂ + H₂ should be ≤ and close to the Att-1 `balance_pct`** (balance = N₂ + H₂ +
   trace). Use this as a validation gate exactly like the Att-1 `CH4+CO2+O2+Balance ≈ 100` gate
   — it catches column misalignment. Flag, don't silently drop, rows that fail it.
3. Do **not** interpolate. Att-3 is sampled less often than the Att-1 census; where a well+date
   has no Att-3 reading, H₂/N₂ are simply absent (blank downstream). Never fabricate a value.
4. Tests: add Att-3 cases to `tests/test_woi_parser.py` (a positive extraction, the
   balance cross-check, and an image-only/empty-text case that asserts the gate triggers rather
   than returning silent empties). Keep all existing Att-1/Att-2 tests green.

ADR + tests + topology in the same PR. `docs/decisions/005-woi-table-extraction.md` is the
relevant existing ADR — extend it or add a sibling for Att-3.

## Part C — CSV rebuild + release (NAMED FOLLOW-ON — not this PR)

The published CSV and the `gh` release are built **offline in Lotext**, not in this repo:
`/Volumes/Samsung-Pro-2TB/Lotext/documents/arbor-hills/draft/arbor-hills-wellfield-data-<latest>/`
(`generate_wellfield_csv.py` imports a **bundled copy** of `woi_table_parser.py` from this repo).
So after this PR merges, a **separate step** (an interactive Lotext session, or you as a
post-merge data step if you can reach that folder) must:

1. Sync the updated `woi_table_parser.py` into the Lotext build folder's bundled copy.
2. Add `nitrogen_N2_pct` and `hydrogen_H2_pct` to `COLUMNS` + the row builder in
   `generate_wellfield_csv.py` (keep `balance_pct` — do not overwrite it; it stays as the
   cross-check).
3. Rebuild the CSV, re-cut the `wellfield-data-<date>` GitHub release, delete the superseded
   release, and update the build README + `arbor-hills-HOV-ETLF-CO-index.md` §7.

Call this out in the PR + archive entry as the required follow-on so it is not assumed to have
happened inside this PR.

## Verification

- Spike result reported per-report (text-layer yes/no).
- For text-extractable reports: extracted N₂+H₂ reconciles against Att-1 `balance` for shared
  well+date rows (the cross-check gate).
- Existing Att-1/Att-2 parser tests stay green; new Att-3 tests pass, including the
  scanned/empty-text gate test.

## Dependency / pins

`Dependency: null` — the source PDFs are already in hand; no worker prep needed. No worker pin
to release. Staged directly at Trisha's request 2026-08-25.
