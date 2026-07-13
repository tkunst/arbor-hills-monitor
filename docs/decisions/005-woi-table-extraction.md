# ADR 005 — WOI Status Reports get a dedicated table-aware extractor

*Status: accepted — 2026-06-14. Supersedes keyword-windowing for this one
document class. Schema unchanged (ADR 004 still canonical).*

## Context

The semi-annual **WOI (Wells of Interest) Status Reports** are the densest R8
(overheating / ETLF) evidence — 180–320 pages of per-well monitoring tables.
Running the real *2025 First Semi-Annual WOI Status Report* (181 pp) through the
generic parser exposed two failures:

1. **Keyword-windowing captures <5% of the data.** It sends the cover + the first
   10 keyword-matched pages = 11 of 181 pages. But 100 pages contain O₂ data, 44
   contain temperature, and the report holds 478 wells with ~14,000 readings.
2. **The tables don't extract cleanly.** PyMuPDF `find_tables()` returns 0 tables;
   `get_text()` linearizes the grid. So even windowed pages reach the model as
   "table soup."

## Decision

Add `woi_table_parser.py`: a dedicated extractor that parses **all** pages of a
WOI report with a line state-machine over the linearized text, validates each
reading, and emits ADR-004 `measurements[]`. No schema change.

Reading shape (Attachment 1 "Gas Extraction Report"):
`Well ID / Date+Time / [ADJ] / CH4 CO2 O2 Balance Diff.Press Temp Flow Well-Pres
Header-Pres / comment`. The **time** on the timestamp distinguishes these from the
date-only CO rows in Attachment 2 (skipped). Validation gate:
`CH4+CO2+O2+Balance ≈ 100` — passed **99% of 13,976** readings on the 2025 report.

## Three correctness rules baked in (from the plan session's review)

1. **Denominator.** Attachment 1 is the **full wellfield** (478 wells), which is
   broader than the formally-designated WOI. Report counts *with* their
   denominator and tag WOI membership via `extract_woi_well_list()` (reads the
   Attachment-2 "Wells of Interest Per … HOV Approval Letter" tables). On the 2025
   report: 22 wells ≥131°F, **of which 10 are on the 27-well WOI list**. Never
   imply all 478 (or all 22) are WOI.
2. **Asterisks are not WOI flags.** Ids carry `*`/`**`/`***`/`****` whose meaning
   is report-/attachment-specific footnotes. `canonicalize()` strips them; WOI
   status comes from the list cross-reference, not the star count.
3. **Cross-report id changes.** Wells are abandoned and replaced with new ids
   (e.g. AHW0177→AHWW177R) and EGLE issued a formal WOI-id update on 6/22/2023.
   `canonicalize(id, alias_map)` applies a per-physical-well alias map so a YoY
   time series doesn't fragment one well across two ids. Within a single report
   no map is needed; the map is required only when joining reports.

## Measurement emission

`to_measurements()` emits `temperature` + `oxygen` + `methane` (as `other`,
note="methane (CH4)") per reading, `basis="measured"`. ADJ (post-valve-
adjustment) rows are skipped by default so a well/time isn't double-counted; the
as-found reading is what the well was actually doing. Low CH₄ + elevated temp +
some O₂ at the same well is the subsurface-oxidation signature (e.g. AHW272R4 =
177°F, 7% O₂, 8% CH₄).

## CO (Attachment 2)

`parse_co_data()` extracts the monthly carbon-monoxide (ppm) tables from
Attachment 2 (WOI wells only — Attachment 2 *is* the WOI list). Rows are
`Well ID / Date / ppm`; only canonical pages with a standalone `ppm` header are
parsed, which excludes a malformed second copy of the tables that carries Excel
date-serial leaks (a backstop also drops any value ≥ 10000 ppm). `co_to_measurements()`
emits `carbon_monoxide` measurements; `per_well_co_summary()` gives the monthly
series + first→last rise. On the 2025 report: 16 WOI wells, max 150 ppm
(AHW272R4 — the same well that is hottest), with CO rising at 14 of 16 wells into
June 2025. CO is a combustion product, so this is the early-warning row of the
matrix. `scripts/co_summary.py` is the hand-to-EGLE generator.

## Integration (DONE — 2026-07-13, `woi_router.py`)

`woi_table_parser` is now wired into the live pipeline by **`woi_router.py`**, which
`watcher.py` and `backfill.py` call after `parse_document` and **before**
`is_urgent` / `write_document`. The dispatch lives ABOVE `parse_document` so the
Decode base (`egle_doc_parser.py`) stays domain-agnostic — it never learns
"Gas Extraction Report" or WOI.

- **Detection is content-based, not name-based.** A WOI report is detected by the
  Attachment-1 "Gas Extraction Report" header plus a page count over the large-doc
  threshold. The nSITE filename is deliberately NOT a signal: the real reports are
  filed under generic names ("nForm Document" / "Site") — verified against the live
  181-page 2025 report (nSITE doc_id `7022559137978826651`, type_name
  "nForm Document"), so a filename gate would never fire. (This is a deliberate,
  evidence-backed departure from the original handoff, which proposed an nSITE-name
  signal.)
- **Augment, don't replace the narrative.** `parse_document` still produces the five
  model-derived fields; `woi_router` then REPLACES `parsed.measurements` with the
  exhaustive validated set (non-ADJ, valid, at/above the 131 °F watch band), so a
  measured exceedance buried past the keyword window now lands in `measurements[]`
  and drives the same-day `is_urgent` alert.
- **Two safety rules (from the plan's adversarial review):**
  - *Data-loss guard* — route only if the exhaustive parse yields at least 50 valid
    readings (a real report has ~14,000; a misdetected narrative doc yields ~0).
    Otherwise keep the generic measurements and log loudly. This makes an
    over-trigger harmless and doubles as the EGLE-format-drift alarm.
  - *Peak-temp preservation* — always keep the peak as-found temperature in the
    emitted set. `is_urgent` decides from `measurements[]` whenever any temperature
    is present and only regexes free text when there is none; on an all-cool report
    an unguarded ≥131 trim would emit zero temperatures and send `is_urgent` to its
    free-text fallback, which would match the HOV permitted ceilings (155/180 °F) on
    the windowed cover and FALSE-FIRE off a permitted limit — the exact conflation
    ADR 004 forbids.
- **Volume.** A new `WOI Well Summary` tab (one row per well, from
  `per_well_summary()`); only the ≥131 °F readings ride the Measurements tab. The
  full ~14k-reading dump stays reproducible via `scripts/woi_summary.py`.
- **Kill-switch.** `woi.auto_route` (config), default **on** — a rollback lever, not a
  new-source enable gate; flip it off to fall back to windowing.

Verified end-to-end against the real 181-page 2025 report before merge: 13,976
readings, 99.6 % valid, 478 wells, hottest AHW272R4 = 177 °F → wired `is_urgent`
returns `True` (it did not under windowing).

## Consequence

The most important R8 evidence is now fully and verifiably extractable. The
per-well summary it produces (max temp, concurrent O₂/CH₄, WOI tag) is the
hand-to-EGLE artifact and the source for the plan's Crisis Comparison Matrix
baseline cells.
