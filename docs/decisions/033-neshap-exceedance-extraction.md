# ADR 033 — NESHAP Semi-Annual Report table extraction

*Status: **BUILT, standalone** — 2026-08-28. Ships as a parser + tests only, not
wired into any watcher or poller (a new dataset, not a live monitoring path).
CSV build + hand-curated public release is a named follow-on, not this change.*

## Context

GFL files a **Semi-Annual NESHAP Report** (40 CFR Part 63 Subpart AAAA,
ROP-N2688-2011) for Arbor Hills. Two are now public (hand-curated 2026-08-28):
**2025 H1** (Jan–Jun 2025, transmittal 2025-09-10) and **2025 H2** (Jul–Dec
2025, transmittal 2026-03-13). These carry structured compliance data that is
**not in any current public dataset** — the `wellfield-data-*` release is
built only from WOI Status Reports + Gas-Extraction Exceedance filings
(confirmed: no NESHAP `source_report` value exists in it).

The reports are the compliance record for the elevated-temperature story:
they document that no root-cause analysis and no downwell monitoring
happened during the reporting windows, and exactly which wells were (and
weren't) enhanced-monitored. Turning that into queryable data makes "the
hottest well got the least scrutiny" independently checkable.

This is **not** the WOI parser (`woi_table_parser.py`), **not**
`coder:wellfield-h2-n2`, and **not** the LLM classifier (`egle_doc_parser.py`).
Different documents, different data model, different output; none of those
three files were touched by this change.

## Feasibility spike (Step 1/3, done before writing the parser)

Per the handoff's OCR-risk framing, the spike question was: do the Appendix
A/F tables have a real text layer, or are they image-only scans? Run against
both real 2025 PDFs (`fitz`/PyMuPDF `get_text()`):

- **Full text layer.** Every appendix used here returns real, densely tabular
  text — not scanned images. **Text-extractable → merge-eligible**, per the
  handoff's own decision tree.
- A full-parse spike against both real reports matched **every sanity-check
  number in the handoff exactly**: 2025 H2 — 654 pressure + 38 temperature
  exceedance readings (263R5 x29, 328R x9 of the temperature readings), 28
  enhanced-monitoring readings + 4 visual inspections (all "N") for 263R5, no
  RCA required, no downwell monitoring conducted. 2025 H1 —
  `wells_enhanced_monitored` = {263R5, 328R} as expected, RCA/downwell both
  also "no."
- **AHW272R4 confirmed to legitimately appear in the Pressure table but never
  the Temperature table**, in either report — it holds an approved 180F HOV,
  so its temperature never crosses that raised threshold. An empty 272R4
  temperature result is correct, not a parse miss.

Two real, non-obvious format inconsistencies surfaced only by testing both
real PDFs (matching this repo's own ADR 011 precedent — real specimens catch
bugs synthetic fixtures miss):

1. **The report's own table of contents lists "APPENDIX A" then "APPENDIX B"
   back-to-back** as consecutive TOC lines. A naive whole-document per-line
   scan for the first "APPENDIX A"..."APPENDIX B" span grabs the TOC instead
   of the real section (both letters appear on the same TOC page, so the
   first `start`/`end` pair found is the wrong one). Fixed by scanning
   **whole-page** text for a page that *starts with* `APPENDIX <letter>`
   after stripping — only a genuine divider page satisfies that (the TOC page
   starts with `CONTENTS`).
2. **Appendix F's date/time format is not consistent between the two real
   reports.** 2025 H1 prints bare `M/D/YYYY` with no time at all (despite the
   column header reading "Date & Time"); 2025 H2 prints `MM/DD/YY HH:MM`. The
   date regex accepts both shapes; Appendix A's own dates, by contrast, are
   consistently `MM/DD/YY HH:MM` (with a time) across both reports.

## Decision

Add `neshap_table_parser.py`, mirroring `woi_table_parser.py`'s idiom
(attachment-header regex → per-row field extraction → typed dataclass → CSV-
ready rows), against three targets:

1. **Appendix A ("Wellfield Exceedance Reports")** → `ExceedanceReading`
   (`well_id, reading_date, parameter, value, limit, exceeded, duration,
   page`). Two same-shaped tables (Pressure, then Temperature) share one
   divider page, switched on a standalone `(Pressure)` / `(Temperature)`
   line. `exceeded` is **computed per row** against a constant baseline
   threshold (pressure: zero/positive gauge pressure; temperature: >145F,
   the NESHAP wellhead standard) — **not** assumed True for every row just
   because the well appears in an "exceedance" table: an episode's
   closing/return-to-negative reading is logged in the same table, with its
   Duration filled in, and does not itself exceed. Per-well HOV overrides
   (like 272R4's 180F) are **not** modeled — `TEMPERATURE_LIMIT_F` is the one
   constant baseline the report narrative itself states, documented as such
   in the module.
2. **Appendix F ("Enhanced Monitoring Results")** → `EnhancedMonitoringReading`
   (well_id, reading_date, methane_pct, oxygen_pct, gas_temp_f, co_ppm) and
   `VisualInspection` (well_id, reading_date, staff_name, smoke_observed,
   ash_observed, damage_observed). Two same-shaped tables share one divider
   page, switched on a standalone `Methane` / `Staff Person` line. Carbon
   monoxide is the one optional numeric field (often blank in the source).
3. **Report-level metadata** → `ReportMetadata` (reporting_period,
   transmittal_date, rca_required, downwell_monitoring_conducted,
   wells_enhanced_monitored). `rca_required` / `downwell_monitoring_conducted`
   are read from the narrative via two known-phrasing regexes — deliberately
   **not** general NLP, per the handoff ("capture the narrative facts... not
   free text"). Both real reports only ever say "not required" / "not
   conducted"; there is no real exemplar of a report requiring an RCA or
   conducting downwell monitoring, so **a non-match returns `None`
   (couldn't-determine), never a guessed `True`**. `wells_enhanced_monitored`
   is deliberately **not** read from the narrative's "One (1) well was
   subject to..." sentence — it's the set of well IDs that actually appear in
   the parsed Appendix F table, which is checkable against the roster instead
   of trusting a hand-typed count.

## Tests

`tests/test_neshap_parser.py` — 25 hermetic tests operating on synthetic
`[(text, page)]` / page-text lists (the same `L()` helper pattern as
`tests/test_woi_parser.py`), no PDF opened at test time. Per this repo's own
data-file/local-path rules (`CLAUDE.md` "Forbidden patterns" —
`docs/decisions/002`), no test hardcodes a `/Volumes/...` path or loads a real
PDF; the two real 2025 PDFs were used only in this build session's own
feasibility/correctness spike (matched every handoff sanity-check number
exactly — see above), and their real structural quirks (the TOC trap, the
H1/H2 Appendix-F date-format mismatch, the exceeded-is-per-row nuance, the
Y-mapping not being hardcoded to the all-"N" case both real reports happen to
show) are encoded as literal synthetic fixtures in the test file instead.
`pytest -q`: 1240 passed (1215 pre-existing + 25 new).

## Consequences

- **No poller wiring.** This ships as a standalone parser + tests; nothing
  schedules it, nothing calls it from `watcher.py`/`backfill.py`. Building the
  actual CSV dataset and hand-curating/publishing it (mirroring the
  `wellfield-data-*` release pattern) is a **named follow-on**, not this
  change.
- **No CSV committed.** Per `CLAUDE.md`'s forbidden patterns, output data
  files are never committed; only the parser + tests + this ADR land.
- `docs/overnight-coder-handoffs/neshap-exceedance-extraction.md` — the
  handoff this ADR implements — is committed alongside this change (it was
  untracked before this PR).
