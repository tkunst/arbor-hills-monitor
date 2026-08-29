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

`tests/test_neshap_parser.py` — 43 hermetic tests operating on synthetic
`[(text, page)]` / page-text lists (the same `L()` helper pattern as
`tests/test_woi_parser.py`), no PDF opened at test time. A handful use
pytest's `monkeypatch` fixture to stub the module's one fitz-touching
function (`_page_texts`) so the actual public entry points
(`parse_exceedances`/`parse_enhanced_monitoring`/`parse_report`) are
exercised directly rather than only their pure sub-functions. Per this
repo's own data-file/local-path rules (`CLAUDE.md` "Forbidden patterns" —
`docs/decisions/002`), no test hardcodes a `/Volumes/...` path or loads a real
PDF; the two real 2025 PDFs were used only in this build session's own
feasibility/correctness spike (matched every handoff sanity-check number
exactly — see above, re-verified after every round of review fixes), and
their real structural quirks (the TOC trap, the H1/H2 Appendix-F date-format
mismatch, the exceeded-is-per-row nuance, the Y-mapping not being hardcoded
to the all-"N" case both real reports happen to show, the real H1 report's
visual-inspection table repeating its column header across a page break) are
encoded as literal synthetic fixtures in the test file instead.
`pytest -q`: 1258 passed (1215 pre-existing + 43 new).

Two rounds of independent review (Step 5 of `overnight-coder.md`, a fresh
diff-only subagent each round) plus a Step 6 security pass
(`docs/security-reviews/security-review-2026-08-28-neshap-exceedance-extraction.md`
— zero medium/high findings; note that file's own "Tooling note" on why an
OWASP-tracing subagent stood in for the literal `/security-review` command)
ran against this PR before merge; round 1 found 8 issues
(silent divider-page failure on any leading page content, a silent-row-drop
adjacency limit, a regex-overlap question, no per-row validity gate, an
overly-broad well-ID regex, a downwell-phrasing precision gap, a test-
coverage gap on the PDF-facing entry points, and cosmetic nits) and round 2
found 7 more against the round-1 fixes (a `plausible` property that was
defined but never consumed, a lost asterisk-tolerance side effect of the
`WELL_RE` tightening, a genuine data-integrity gap where a missed closing
divider could let one appendix's parser bleed into the next appendix's rows
because Appendix A's and Appendix F's date formats coincide, a stale test-
count claim in this ADR, a plausible-sounding but empirically-unconfirmed
multi-page-header concern, a missing test, and a cosmetic nit). All were
resolved: genuine bugs fixed and re-verified against both real PDFs
(including the divider-bleed gap, which is a real fix — `_section_end`/
`_find_next_any_divider_page` below); two claims (the lost asterisk
tolerance, the multi-page repeated-header risk) were independently
re-checked against the real PDFs rather than taken on faith and found not to
apply to either real report as it stands today, with a hermetic regression
test added for the real repeated-header shape either way; the `plausible`
property was kept as documented API surface for the named CSV follow-on
(the same role `woi_table_parser.WOIReading.valid` plays via
`per_well_summary`'s `valid_only`) rather than built out further, since this
module has no equivalent aggregation function yet to wire it into.

**`_section_end` / `_find_next_any_divider_page`** (added in round 2): the
original `parse_exceedances`/`parse_enhanced_monitoring`/`parse_report` fell
back to "scan to end of document" whenever a section's expected closing
divider (Appendix B for A, Appendix G for F) wasn't found. Appendix A's
pressure/temperature rows and Appendix F's H2-style enhanced-monitoring rows
share the identical `MM/DD/YY HH:MM` date shape, so a missed Appendix B
divider could have let Appendix A's parser run straight into Appendix F and
mis-parse its rows as spurious pressure/temperature exceedance readings —
silently *wrong* data, not just missing data. `_section_end` now falls back
to the nearest LATER appendix divider of any letter before falling back to
end-of-document, closing that gap without needing to hardcode every real
report's exact appendix ordering.

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
