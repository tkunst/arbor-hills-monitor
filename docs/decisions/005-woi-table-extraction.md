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
  and drives the same-day `is_urgent` alert. Each hot reading emits temperature +
  oxygen + **methane** (a first-class metric since 2026-07-13, not `other`).
- **CO too (Attachment 2).** `woi_router` also routes the monthly carbon-monoxide
  series (`parse_co_data` → `carbon_monoxide` measurements) — CO is a combustion
  early-warning signal, a small bounded set (WOI wells, monthly), emitted for
  every reading (not temperature-gated), but only AFTER the temperature-anchor
  guard so it can't stand in for a missing temperature.
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

### Re-extracting the two historical reports (`FORCE_REPROCESS_DOC_IDS`)

Future WOI reports route automatically. The two reports filed **before** this
change are already `processed` via the old windowed path, and neither
`RETRY_POISONED` nor `RETRY_DOC_IDS` re-touches a `processed` doc. `backfill.py`
adds **`FORCE_REPROCESS_DOC_IDS`** — the one override that bypasses the
`processed` gate — to re-extract them cleanly:

- It is **surgical**: only the named doc_ids are processed, never the normal
  backlog (backfill sends no alerts, so it must not silently sweep up pending
  docs — the risk that disabled the nightly schedule).
- Because re-processing an already-recorded doc would otherwise pile fresh rows
  on the stale ones, each doc's existing rows are **purged** from the append-only
  tabs (`sheet_writer.purge_doc_rows`, matched by the doc_id in each row's Link):
  Historical/New/Evidence/Measurements/WOI-Summary. The windowed footprint of the
  181-page report is 22 such rows (1 feed + 3 evidence + 18 measurements). The
  other tabs the doc appears in are handled without a direct purge: **All Evidence
  by Risk** and **Risk Register** are *derived* and recomputed by the post-run
  rebuilds (forced on a re-extract so it holds even if Stream C is disabled);
  **_state** is append-only (a fresh `processed` event supersedes the old);
  **Archived PDFs** is left intact (it indexes the unchanged Drive-mirrored PDF —
  purging it would orphan the mirror).
- It defaults to a **DRY RUN**: `FORCE_REPROCESS_DOC_IDS` alone only previews the
  purge + re-extract and mutates nothing. A second switch, `FORCE_REPROCESS_APPLY`,
  is required to execute. Google Sheets File → Version history is the restore
  path if a purge ever goes wrong.

Operator recipe (backfill `workflow_dispatch`): set `force_reprocess_doc_ids` to
the report doc_ids, run once with `force_reprocess_apply` **off** to review the
preview, then re-run with it **on**. Verify each id is a real WOI report first
(the detector needs the "Gas Extraction Report" header). The 181pp report is
`7022559137978826651` (verified); the 323pp report is identified by page-count
only — confirm against the live tab before applying.

## Consequence

The most important R8 evidence is now fully and verifiably extractable. The
per-well summary it produces (max temp, concurrent O₂/CH₄, WOI tag) is the
hand-to-EGLE artifact and the source for the plan's Crisis Comparison Matrix
baseline cells.

## Addendum (2026-10-15, coder:woi-layout-guard): per-table layout, all-page detection, bad pages

A read-only re-run of every WOI-mentioning filing found three parser defects on
the live path (none had reached the WOI Well Summary tab):

1. **Column order is now read from each table's own header.** Rows are parsed
   in order, and the filed reports use six column layouts (`LAYOUTS` in
   `woi_table_parser.py`), two of them 8 numbers wide. Reading the "no Diff.
   Press" layout as the 9-number one put each row's flow in the temperature
   field: the Q3 2023 HOV WOI report (65pp) gave AHWW0402 = 526.8°F on 8/3/2023
   (the page shows 110.5°F) and 82 readings over 200°F, all passing the
   sum-to-100 gate. The order comes from each label word's position on the page,
   in the table's own reading direction (wrapped labels come out of the text
   extractor out of order, and some tables are printed rotated). A layout is
   carried forward until the next header. Rows under an unrecognized gas
   header, under another table's "Well ID" header (CO data), or before any
   header are never emitted; they are reported as rejected tables. A table
   whose flow column is negative on more than 25% of rows (the column-shift
   signature: 94% on the shifted read, 0% on every correctly read table) is
   rejected whole. `parse_gas_extraction()` keeps its list return;
   `parse_gas_extraction_report()` also returns the rejected tables and
   skipped pages.
2. **The "Gas Extraction Report" header is searched on every page** (it was
   the first 25). The Q3 2023 Consent Judgment report (1006pp) carries it on
   p163.
3. **An unreadable page is skipped**, in every page loop here and in
   `egle_doc_parser` (text extraction, the urgency-floor scan, the date pass,
   and the `needs_ocr()` gate, which now fails open on a read error). The Q1
   2024 HOV WOI report (90pp, 20 pages MuPDF cannot find in its page tree) had
   failed every attempt; it now parses to 6,178 readings.

Rejected tables, unreadable pages and readings above the 250°F plausibility
ceiling are logged to the Parser Review Flags tab (ADR 067 amendment).

Verified against 51 real filings (nSITE + the hand-curated copies), comparing
well, time, ADJ, the four gas columns and temperature with the old parser's
output: identical for every document except

- the Q3 2023 HOV report (both copies): 4,354 shifted rows now read
  correctly, 0 readings over 200°F (was 82), max 162.1°F, AHWW0402 on 8/3/2023
  = 110.5°F as printed. Its two tables overlap: 234 well/time readings appear
  in both (p14 and p49). Since 2026-10-16 `woi_router` collapses such a
  cross-table repeat (same reading, consistent columns, different pages) to its
  most complete copy before Measurements, the summary and well_watch
  (`woi_table_parser.merge_cross_table_duplicates`; the parser's own output is
  unchanged): 816 -> 672 measurements for this report. The merge also
  removes exact cross-table repeats from five other reports in the specimen
  set (measurements before -> after; nSITE doc ids, both copies where two):
  the 58pp report `-2136493721019571731` / `8700874927542414020` 767 -> 650;
  the 73pp 2022-11-15 report `3838978899799471728` / `6318109379845527354`
  895 -> 847; the 77pp report `6899614001871945975` / `8586108705270421879`
  729 -> 657; the hand-curated Drive copies of the 73pp 2022-05-12 report
  1846 -> 1681 and the 119pp 2024-07-09 report 1379 -> 1316. In every one,
  peaks, the per-well summary and urgency are unchanged; only exact repeats
  are removed;
- the Q1 2024 HOV report (both copies): 6,178 readings (was 0; pages 71-90
  skipped);
- the 465pp Gas Extraction report (both copies): its gas + Temp only table
  (4,364 rows) was never read before, and one row on p98 that the old parser
  read as 84.0°F (a number from the next row) now reads 105.9°F.

Tables that still yield no readings now appear as rejected tables instead of
being silently empty: the 250pp Gas Extraction pair (gas + Diff. Press, no
Temp column), and the no-Temp tables inside four Gas Extraction subset
reports (1,374-2,419 rows each, never emitted by either parser).

## Addendum (2026-10-17): the gas-sum check is a measured one-sided window

`WOIReading.valid` used to require CH4+CO2+O2+Balance within 100 ± 1.5. That
dropped real readings. In the 2026-10-08 report (nSITE `-6975595422894935530`),
AHWW0279's 5/31/2024 readings sum to 97.5 and 97.8. The reading on p. 31 is
154.2°F and the one on p. 103 is 154.4°F, and both were discarded. The well's
reported maximum fell to 153.3°F.

Measured across 32 locally held WOI-format reports (217,384 parsed rows), every
row's sum lies between 96.2 and 100.2. The values form a continuous tail below
100, with no gap that would separate a misread row. The old window dropped 258
of these real rows. At least five were at or above 145°F, including AHW263R5 at
160.1°F (6/27/2025).

The check is now `GAS_SUM_MIN` (95.0) ≤ sum ≤ `GAS_SUM_MAX` (101.5). A shifted
read still fails it: a pressure or temperature value in a gas slot gives sums
like 40 or 78. With the new window the 32-report scan drops 0 rows, and the
parsed rows and rejected tables are unchanged. The 2026-10-08 report now routes
AHWW0279's 5/31/2024 rows, and its maximum is 154.4°F. Reports processed
before this change keep their old summaries until they are re-extracted.

## Addendum (2026-10-17): unlabeled ADJ rows

Attachment 1 of the 2026-10-08 report (nSITE `-6975595422894935530`, pp. 8–80)
has no ADJ column. Its header reads Well ID, Date/Time, CH4, CO2, O2, Balance,
Diff. Press, Temp, Flow, Well Pres, Header Pres. GFL still prints each adjusted
well as three lines: the as-found row, a comment line (e.g. "Opened Valve 1/2
turn to 1 turn:"), and then the after-adjustment row under the SAME well and
timestamp. The labeled back table (pp. 96–103) prints the literal `ADJ` token on
that second row. The parser only looked for the token, so it recorded both rows
as as-found. In this report 1,513 of the 4,082 pairs differ in at least one
value (1,200 in temperature). See "Measured effect" below.

**Rule.** Each gas table's header is checked for an `ADJ` label
(`_ADJ_LABEL_RE`). If the header has one, rows are read as before: only the
token marks ADJ. If it doesn't, a row is ADJ when the row right before it in
the same table has the same printed well (stars stripped) and timestamp and is
not itself ADJ. A pair printed twice therefore reads as-found, ADJ, as-found,
ADJ, which is how the labeled table prints the same thing. A pair may straddle
a page break. The rule restarts with each table (a change in ADJ labeling is a
new table). The header-less fragment path (`_parse_lines`) only trusts the
token.

**Checked against the report's own labels.** All 195 well/timestamp keys that
appear in both Attachment 1 and the labeled back table get the identical
(ADJ, temp, CH4) sequence in both.

**Side effect: cross-table twins now merge.** `merge_cross_table_duplicates`
keys on the ADJ flag. Before, an Attachment-1 pair with identical values plus
its back-table twin formed one group with two rows on the same page, which the
same-page guard keeps whole, so all three reached Measurements. Now each
Attachment-1 row merges with its labeled twin.

`well_watch` already scans ADJ rows (it labels them), so its trip-wires are
unchanged; only the label in the email changes. Reports processed before this
change keep their old rows until they are re-extracted.

**Measured effect (router run before and after, 2026-10-17).** Across the 32
locally held WOI-format reports, only the 2026-10-08 report has a gas table
with no ADJ label (one other unlabeled header exists but has no data rows
under it). The other 31 reports produce identical rows, flags, summaries and
measurements. In the 2026-10-08 report:

- 4,082 Attachment-1 rows are now ADJ (pp. 8–80); every row's values are
  unchanged.
- Readings merged across tables went from 79 to 390.
- Emitted measurements dropped from 1,388 to 884. As-found ≥ 131°F temperature
  readings went from 452 to 284: 121 were after-adjustment rows and 47 were
  back-table twins that now merge. The 32 CO rows are unchanged.
- 31 wells' max temperature fell by 0.1–0.7°F (e.g. AHWW0533 133.8 → 133.7,
  AHEW0006 101.5 → 100.8); their summary's O2/CH4-at-max-temp follow the new
  max reading. No well's max O2 changed. The site peak (158.3°F) is unchanged.

## Addendum (2026-10-19): Well ID under Alias headers, `---` cells, gas-only rows

Spec: `docs/overnight-coder-handoffs/woi-parser-wellid-dashes.md` (round-4
wellfield review). Trisha's rulings of 2026-10-19: keep gas-only readings;
do not capture the valve-setting or comments columns.

**Well ID, not Alias.** Some gas tables print Well ID, Alias, Date/Time. A row
used to start wherever a well-shaped token sat right before a timestamp, so in
these tables the Alias became the row's well, and a row whose Alias was not
AH-format (`EW-008`, `V-10`) was never read. Under a header whose text has an
`Alias` label (`_ALIAS_LABEL_RE`), a row now starts at Well ID / Alias /
Date/Time. The Well ID must match `WELL_RE`; the Alias matches the looser
`ALIAS_RE`, or is a blank `---`. `well_id`/`raw_well_id` come from the Well
ID, and the Alias is kept as printed in `WOIReading.alias_as_printed` (None when
blank). The header reader also stops at that three-line start, so the first row
after each header keeps its Well ID. In tables with no Alias label the row start
is unchanged. The cell reading and gas-only rules below apply to every table.
The unlabeled-ADJ rule keys on the Well ID. An Alias table whose Well ID isn't
AH-format falls back to the old two-line start (the alias becomes the well), as
on main.

**Cells are tokens.** Values used to be read one whole line at a time, and the
row ended at the first line that wasn't a bare number. A line such as
`27.6 ---` or `-0.68 No Change: :` therefore ended the row, so its value and
every later column were lost, and a row with `---` before Temp was dropped.
Each line is now split on whitespace (`_read_values`). A number fills the next
column. `---` fills it with None: the cell is blank but keeps its position. A
line that starts with any other token is not read. A line with cells followed by
other text is the row's last cells followed by the Comments column
(`-0.68 No Change: :`). It is read only when its cells complete the row exactly and
the cells before it already reach past Temp, and the row ends there. Otherwise
the row ends before that line. So a comment that starts with a number
(`150 ft of water`) can never supply Temp or any column before it, and
`2 turns open` does not fill a column after a short row. Across the corpus below, 23,794 lines mix cells and text. Each one's text
is a Comments-column entry ("Opened Valve…", "No Change…", "Second Reading…").
Every one completed its row. Residual: a row one or two cells short after Temp
(pressure columns not printed), followed by a comment whose leading numbers
happen to complete it exactly, would still have those numbers read as the
pressure cells.

**Gas-only rows.** A row is emitted when its four gas columns hold numbers and
its cells reach the Temp position, even if Temp (or any later column) is
`---`. It carries no temperature, and `valid` still gates it on the gas sum. A
row whose cells end before Temp is not a blank Temp. That happens, for example,
when the Temp cell is a token the parser can't read, such as `177.0*`. Such a
row is counted in `truncated_rows` and not emitted, as on main. Otherwise it
would turn into a "no temperature" reading. A gas-only row is also never the
as-found partner in the unlabeled-ADJ rule. If it were, the same-time row after
it, which carries the real temperature, would become ADJ and drop out of
alerting. Rows with no cells at all are neither emitted nor counted, as on main.
`woi_router`'s data-loss guard (`_MIN_VALID_READINGS`) counts only valid
readings WITH a temperature, so gas-only rows can't make a report whose
temperatures don't parse replace the classifier's measurements. The three
not-emitted counts are logged after each parse (counts only). A row whose cells are all `---` has no
reading. It is counted in `GasExtractionParse.blank_rows` and not emitted. A row
with some values but not all four gas columns is counted in `partial_rows` and
not emitted. That count also includes short rows from other tables that carry
no Well ID header, such as the 2-value probe rows. 42 rows print values
(among them, possibly, a temperature) with every gas cell blank; there is no
gas sum to validate them, so they are counted, not emitted.

**Unrecognized layouts: split tables, still rejected.** The ~9,500 rows rejected
as "column layout not recognized" come from four distinct reports (eight cache
files). The header word positions read gas columns only
(`ch4,co2,o2,balance[,diff_press]`), and the pages really do print only those
columns. They are not gas-only tables. Each is the left half of a table split
across pages. The right half (Temp, Flow, Well/Header Pres, Comments) is printed
on the same number of pages directly after it, with no Well ID or timestamp:

- `5379a301…` pp517–554, then pp555–592;
- `eb09e953…` pp1–70, then pp71–140;
- `afc0218c…` pp1–60, then pp61–120, and pp191–220, then pp221–250;
- `ed1617e8…` pp1–78, then pp79–156.

Between 0% and 16% of these rows also appear with a temperature elsewhere in
the same report. A gas-only layout would not be a real layout. It would also:

- report every well in those halves as having no temperature;
- double-count O2 for the overlap rows, which can't merge because their temp
  is None;
- accept any future header whose Temp label is misread, and catching that
  misread is the reason for the reject.

So no layout was added, and the reject stays. Joining each half by page offset
and row order is a separate follow-up.

**Measured effect.** Run on 91 locally held WOI-format cache files (the
wellfield staging doc-cache plus the VGC cache), which give 42 distinct
parse outputs. The counts below are over the 42.

- Every reading main read is still read: 0 lost, 0 changed values. Matching
  is on page, timestamp, ADJ and printed id.
- Outside Alias tables, no reading's well id changed.
- 99,428 cells that were blank on main are now filled. All of them come from
  lines read past a `---`, or from the last cell on a value-plus-comment line.
- Readings rose from 268,289 to 278,407:
  - 6,647 new readings have a temperature. All are valid, they range from
    6.2 to 140.2°F, none is ≥ 145°F, and 5 are ≥ 131°F.
  - 3,471 new readings are gas-only.
  - 306 all-`---` rows, 1,038 partial rows and 0 truncated rows were counted.
- 3,299 rows moved from the Alias spelling to the Well ID. 1,068 of them have
  a non-AH alias.
- Rows with a blank `header_pres` fell from 143,695 to 47,910. Of the rest,
  46,522 sit on pages where no row prints that column.
- Rejected rows rose from 9,519 to 9,573. The 54 new ones are rows in two of
  the split tables whose Alias is non-AH or blank. Main never started those
  rows. The reject still counts only the leading numeric cells, as main did:
  a `---` cell, and anything after it, never counts toward it.

Through `route_measurements`, using the live `config.yml` and alias map:

- In all 40 distinct reports that route, the site peak is unchanged.
- 297 per-well max temperatures differ:
  - 189 come from recovered temperature rows;
  - 52 are wells re-keyed from their Alias spelling;
  - 56 are Alias spellings that no longer appear;
  - 0 come from gas-only rows.
- Emitted measurements changed in two reports (+6 and +3), from recovered
  rows at ≥ 131°F.
- `well_watch` hits are unchanged.
- Per-well max O2 moves where gas-only or recovered rows add readings.

Reports processed before this change keep their old rows until they are
re-extracted. On re-extraction, an Alias-table row's "Well ID As Filed" no
longer carries the alias spelling. The alias stays on `WOIReading` only and
is not written to the Sheet.
