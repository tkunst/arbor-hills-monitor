# ADR 068 — Well-ID alias normalization (curated map, exact match, WOI + Measurements)

*Status: **BUILT, DRAFT PR** — 2026-10-12 (handoff `docs/overnight-coder-handoffs/well-id-normalization.md`,
queue slug `coder:well-id-normalization`). Live-path change: not auto-merged.*

## Context

One physical gas well is printed under different id spellings across EGLE filings, so its
history splits into two timelines. Most cases are an era switch: the 0-padded era
(`AHWW0290`) and the R-suffix era (`AHWW290R`). `woi_table_parser.canonicalize(well_id,
alias_map)` has had an alias hook since ADR 005, but every call site passed nothing. The
Measurements / `egle_doc_parser` path had no hook at all.

On real specimens (five WOI Status Reports, 2022-H2 to 2026-1H):

- `AHWW0290` + `AHWW290R` = 138 + 52 readings, now one well with 190.
- `AHWW0302` + `AHWW302R` = 151 + 53, now one well with 204.
- Distinct wells across the five reports go from 541 to 517.

The handoff's example `AHWW0177` appears in none of those reports and in only one live
Measurements row.

## Decision

1. **One map, one source.** The map is the `ALIASES` dict in Lotext's
   `merge_well_aliases.py`, the same source the wellfield dataset and explorer use. It
   holds 38 pairs, each verified and Trisha-approved, and the canonical spelling is EGLE's
   Well Master List.
   - It reaches this repo ONLY through that file's `--export-only` generator, as
     `data/well_aliases.json`. The file is committed with an exact-path allowlist in
     `.gitignore` and in `data-guard.yml`.
   - New pairs are added in Lotext and re-exported, never by hand here. The superseded
     2026-07-14 draft table is not used.
   - Trisha's 2026-08-30 "HIGH-confidence core only" ruling is satisfied by construction:
     the map contains only verified pairs.
2. **`well_aliases.py`** is the single loader. `load_alias_map()` caches per path and
   raises `WellAliasError` when:
   - the file is missing or unreadable;
   - `count` does not equal the number of pairs;
   - an entry is a self-alias or a chain;
   - an entry is not AH-form;
   - an entry touches an id in `EXCLUDE_FROM_NORMALIZATION`.
   `watcher.run()` and `backfill.run()` load it once, before touching the Sheet and outside
   the per-doc `try`. A bad map therefore turns the run red. It cannot turn into per-doc
   poison strikes, and it cannot cause a silent fallback to the generic WOI parse.
3. **Exact match only** (`canonical_well_id`). Trailing `*` is stripped, then the id is
   mapped only if it is a key. Nothing is fuzzy: no case-folding, spacing, short-form or
   "prepend AH" rules. Unmapped ids are left exactly as filed. Redrill generations (R2,
   R3, …) are different wells and are never inferred as aliases.
4. **Part A — WOI path.** `woi_router.route_measurements(..., alias_map=)` passes the map
   into `parse_gas_extraction`, `parse_co_data` and `extract_woi_well_list`. The per-well
   summary, the WOI list, the CO series, the measurements and `well_watch` all see
   canonical ids. `scripts/woi_summary.py` and `scripts/co_summary.py` load the same map.
   - Merging spellings can make two CO readings share a `(well, month)` key.
     `_dedupe_co` then keeps the reading printed under the CANONICAL spelling (the
     current-era table), not whichever page came first, and logs both values. No such
     collision occurs in the 10 local reports.
   - Merging also shortens the WOI list when a report's historical letter tables list both
     spellings. 2026-1H goes from 27 to 23; that is de-duplication of the same wells.
5. **Part B — Measurements path** (Trisha ruled YES on 2026-10-12).
   `parse_document(..., well_alias_map=)` relabels the classifier's measurements as the
   last step on its output, AFTER ADR 067's `_enforce_temperature_consistency` has
   finished. That check may retry the classification or re-tag a reading, so the relabel
   only ever sees finalized measurements.
   - This happens before `is_urgent`, the digest and `write_document`. `egle_doc_parser`
     stays domain-agnostic: the map is passed in, like `risk_register`.
   - WOI-routed measurements are already canonical, and running the rule again changes
     nothing (no chains).
   - Accepted trade-off (Step 5 review): `parse_document` imports `well_aliases` lazily,
     the same way it already imports `kdp_check`. The map itself stays caller-supplied,
     but the exclusion list and the AH-form rule live in that module. Moving the relabel
     out to watcher/backfill would decouple it fully, at the cost of running it after
     ADR 067's check from two call sites instead of one.
6. **Auditability.** The Measurements tab gains **`Well ID As Filed`**, appended at the
   END so positional readers are unaffected (Facility stays at index 10). It is filled
   only when the map relabeled the row, so a blank cell means the id is as filed.
   Footnote `*` markers are stripped there on every path (WOI, Measurements, backfill).
   `ensure_tabs` adds the header on the next run.
7. **`_needs_canonical_review`** logs possible unmapped renames for QA. It is logged once
   per id per process and never mapped automatically. Two signals:
   - two different AH-form ids in one batch with the same prefix (W-runs collapsed), the
     same number (zero padding ignored) and the same generation, where no-R and R count
     as one generation and R2/R3 stay distinct;
   - a short or spaced well-number form such as `272R4`, `WW 237R3` or `Well 260R`.
8. **Backfill.** Existing Sheet rows are NOT rewritten by this build.
   `scripts/backfill_well_aliases.py` uses the same rule and map.
   - By default it is a dry run that prints per-key counts and a sample.
   - `--apply` refuses until the live header has `Well ID As Filed`. It then re-reads and
     re-plans against the current rows, writes a revert manifest first, and makes one
     `batchUpdate` of the Well ID and Well ID As Filed cells.
   - The manifest records, per row, whether the As-Filed cell was written, so a revert
     never clears a pre-existing value. Run it while no watcher/backfill workflow is
     running, since a row deletion between the re-read and the write would shift rows.
   - Trisha runs `--apply` after review. The `WOI Summary` tab's `Well` column is not
     backfilled.

## Consequences

- Live dry-run (2026-10-12): 20 of 38 keys match live rows. 769 of 16,526 Measurements
  rows would be relabeled.
  - Four of those rows belong to the known-bad H2S-as-temperature documents
    (`…8106181551281726432`, `…7258077595449370109`). A separate cleanup follows this
    PR, so this one leaves them alone.
  - The other 18 keys match nothing yet. That is harmless; they are there for future
    filings.
- A document's free-text `summary`/`key_data_point` is the model's paraphrase and still
  names whatever spelling the filing used. Its Measurements rows show the canonical id,
  with the as-filed spelling in `Well ID As Filed`.
- Alert subjects, the digest and the WOI Summary now name the canonical id.
  `well_watch` patterns match on either the canonical or the raw id. The one configured
  pattern, `AHW263R5`, is not in the map.
- Failure modes and recovery:
  - **Malformed export:** the loader raises, so CI tests and the next run fail red.
    Recover by re-exporting.
  - **Wrong pair in Lotext:** fix it there, re-export, and revert the affected rows with
    the manifest.
  - **Exact match too narrow:** short or spaced ids still fragment, but they are logged.

## Not done (out of scope)

- Redrill lineage, i.e. merging successive physical wells drilled at one borehole.
- The `location_type` axis.
- Any short-form rule.
- `AHWTS01R`/`AHWWTS01` and the `391`/`392` flare-vs-well question stay unmapped, as
  directed.
