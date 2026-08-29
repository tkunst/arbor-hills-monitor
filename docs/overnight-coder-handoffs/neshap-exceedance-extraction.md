# Overnight-coder handoff — NESHAP semi-annual report table extraction

*Staged 2026-08-28. Read `docs/overnight-coder.md` first. This is a **feasibility-gated,
OCR-risk** item: the NESHAP appendix tables may be image-only scans a text parser cannot read.
Per Step 1/3 you MUST run the extractability spike FIRST and **STOP with a draft PR (no merge)**
if the target appendices have no text layer. Recommended model tier: **Sonnet**.*

## Invocation

Branch name suggestion: `neshap-exceedance-extraction`.

## What this is (and what it is NOT)

GFL files a **Semi-Annual NESHAP Report** (40 CFR Part 63 Subpart AAAA, ROP-N2688-2011) for the
Arbor Hills landfill. Two are now public (hand-curated 2026-08-28): **2025 H1** (Jan–Jun 2025,
transmittal 2025-09-10) and **2025 H2** (Jul–Dec 2025, transmittal 2026-03-13). These carry
structured compliance data that is **NOT in any current public dataset** — the wellfield
`wellfield-data-*` release is built only from WOI Status Reports + Gas-Extraction Exceedance
filings (confirmed: no NESHAP `source_report` value exists in it). This item extracts the
NESHAP tabular data into a **new, separate structured dataset**.

This is a **deterministic table-parser** build — it is **NOT** the wellfield parser
(`woi_table_parser.py`), **NOT** `coder:wellfield-h2-n2`, and **NOT** the LLM classifier
(`egle_doc_parser.py`). Different documents, different data model, different output. Do not
touch those files.

## Target data (scope, highest-value first)

From each NESHAP report, extract these into structured rows (one CSV per table type, or a long
CSV with a `table` discriminator — your call, document it):

1. **Appendix A — exceedance rosters.** Per-reading rows: `well_id, reading_date, parameter`
   (pressure / temperature), `value, limit, exceeded` (bool). H2 2025 had ~654 pressure + 38
   temperature exceedance readings (263R5 ×29 temp, 328R ×9) — use those counts as a sanity
   check on your parse.
2. **Appendix F — enhanced-monitoring + visual subsurface-oxidation inspection log.** Per-row:
   `well_id, reading_date`, the enhanced-monitoring reading(s), and the visual-inspection
   `smoke / ash / damage` Y/N fields. H2 2025 had 28 enhanced-monitoring readings for 263R5 +
   4 visual inspections (all "N").
3. **Report-level metadata row** (one per report): `reporting_period, transmittal_date,
   rca_required` (per §63.1981(h)(7): H1/H2 2025 both = **no** RCA, all exceedances corrected
   within the federal window), `wells_enhanced_monitored` (H1 = 263R5 + 328R; H2 = 263R5 only),
   `downwell_monitoring_conducted` (both = no).

Do **not** attempt full NLP of the narrative body — capture the narrative facts above as the
report-level flags, not free text. AHW272R4 deliberately does **not** appear in either report
(it was within its 180°F HOV), so an empty 272R4 result is correct, not a parse miss.

## Source of truth

- **Source PDFs** (now public, hand-curated): `Hand-Curated Public Records` Drive folder —
  `2025-09-10-arbor-hills-neshap-report-2025-first-semi-annual.pdf`
  (`1PSy1gx2saeXiSthdywy2zzfkjc8eaaVg`) and
  `2026-03-13-arbor-hills-neshap-report-2025-second-semi-annual.pdf`
  (`1Di9mLUPbuieU592q8OUQ2S3luO-teB3X`). Local copies (Lotext, for the spike/tests):
  `documents/arbor-hills/source-docs/fce-records-request-2026-zip/AAAA Semi-Annual NESHAP *.pdf`.
- **Pattern to mirror:** `woi_table_parser.py` shows the repo's table-parse idiom
  (attachment-header regex → per-row field extraction → typed dataclass → CSV). The NESHAP
  appendices are the same shape (header → tabular rows), a different document.
- **Tests:** new `tests/test_neshap_parser.py`, alongside the WOI parser tests. Use the two
  real NESHAP PDFs as specimens (this repo's history: real specimens catch bugs synthetic
  fixtures miss — ADR 011).

## Feasibility gate (do this in Step 3 before the real build)

Spike: does Appendix A / F have a **text layer** (`fitz`/`pdfplumber` return real table text),
or is it an **image-only scan**? If image-only, **STOP** — commit the spike finding + a draft
PR per `overnight-coder.md`'s "stopped for Trisha", do **not** OCR-and-hope unattended. (Same
posture as `coder:wellfield-h2-n2`.)

## Deliverable + output

- A `neshap_table_parser.py` (new file) + tests, green `pytest -q`.
- Output: a **new CSV dataset** (e.g. `neshap-exceedances-<date>.csv` +
  `neshap-enhanced-monitoring-<date>.csv`, or one long CSV) — **not committed to the repo**
  (repo forbids data files; the parser + tests are what land). The CSV build + a public release
  / hand-curation of the dataset is a **named follow-on** (mirror the wellfield-dataset release
  pattern), not part of this PR's merge.
- This is a **new, non-live path** (no existing scheduled job consumes it yet) — so it can merge
  on green CI + clean reviews per `overnight-coder.md` Step 8, without a live-specimen gate
  beyond the two real PDFs used in tests. It does not wire into any poller; wiring NESHAP into
  the nightly pipeline (if ever wanted) is a separate future item.

## Why it matters

The NESHAP reports are the compliance record for the elevated-temperature story — they document
that **no root-cause analysis and no downwell monitoring** happened during the March-2025
AHW272R4 event window, and exactly which wells were (and weren't) enhanced-monitored. Turning
that into queryable data makes the "the hottest well got the least scrutiny" finding
independently checkable, not just assertable.
