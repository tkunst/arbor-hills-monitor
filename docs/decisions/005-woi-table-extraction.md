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
AH-format (`EW-008`, `V-10`) was never read. Under a header whose text starts
`Well ID Alias` (`_ALIAS_LABEL_RE`; the word "alias" elsewhere in a header does
not count), a row now starts at Well ID / Alias / Date/Time. When the page's
line positions are available (the PDF path), the Well ID line must also sit in
the page's Well ID column, the leftmost well-shaped line in the text's own
direction (`_WELL_COLUMN_TOLERANCE`). On 4,549 corpus pages every such start
sits exactly there. So a comment line that names a well can't take over the
next row when that row's alias prints nothing. A start that fails this check
is counted in `off_column_rows` and logged. It is then read with main's
two-line rule, or not at all. Positions are taken only on pages whose text
contains "Alias". The Well ID must match `WELL_RE`; the Alias matches the looser
`ALIAS_RE`, or is a blank `---`. `well_id`/`raw_well_id` come from the Well
ID, and the Alias is kept as printed in `WOIReading.alias_as_printed` (None when
blank). Under an Alias label the header reader also stops at that three-line
start, so the first row after each header keeps its Well ID. Any other header
ends where main ended it. In tables with no Alias label the row start
is unchanged. The cell reading and gas-only rules below apply to every table.
The unlabeled-ADJ rule keys on the Well ID (its partner rule is below). An Alias table whose Well ID isn't
AH-format falls back to the old two-line start (the alias becomes the well), as
on main.

**Cells are tokens.** Values used to be read one whole line at a time, and the
row ended at the first line that wasn't a bare number. A line such as
`27.6 ---` or `-0.68 No Change: :` therefore ended the row, so its value and
every later column were lost, and a row with `---` before Temp was dropped.
Each line is now split on whitespace (`_read_values`). A number fills the next
column. `---` fills it with None: the cell is blank but keeps its position. A
line that starts with any other token is not read. A line with a cell followed
by other text is the row's last cell followed by the Comments column
(`-0.68 No Change: :`). It is read only when it holds one cell, that cell
completes the row, and its column is Well Pres or Header Pres. The row ends
there. Otherwise the row ends before that line. So a comment that starts with a
number (`150 ft of water`, `2 turns open`) can never fill Temp, Flow or a gas
column. Across the corpus below, 23,794 lines mix cells and text. Each one's
text is a Comments-column entry ("Opened Valve…", "No Change…", "Second
Reading…"), and each one supplies a single Header Pres cell.

**A comment-line cell must line up with its column.** Text alone can't tell
`-0.68 No Change: :` (a Header Pres cell, then the comment) from a row whose
Header Pres printed nothing, followed by a comment that starts with a number
(`2 turns open`). The page can. A comment starts at the Comments column, which
lies past the Header Pres column's right edge. So a number at the start of a
comment ends at least one character width past that edge. A real Header Pres
cell ends on it.

On the PDF path the parser reads each page twice:

- The first pass collects the right edge of every bare-number cell read into
  the last column of an emitted row whose last column is Well Pres or Header
  Pres. The median per page is that column's edge.
- The second pass reads a cell that shares its line with a comment only when its
  right edge is within 0.3 of the cell's own character width of that edge
  (`_LAST_COLUMN_TOLERANCE`).

The tolerance is scaled to the font because the filings print these tables at
very different sizes. A page with no reference cell of its own uses an adjacent
page's. A cell that fails, or can't be checked, is kept as a blank (None). It
was printed, so its position stays known and the row is still read; only its
value is not trusted. Such cells are counted in `unaligned_cells` and logged. A
`---` comment-line cell is blank either way.

Line geometry comes from one text page, built with `get_text()`'s own flags, and
its dict and word lists. It must match the plain-text lines exactly, or the
page has no geometry. Geometry is taken only on pages with an Alias label or a
number-then-text line.

Measured on the corpus (23,793 comment-line cells):

- Every real cell ends within 0.07 of a character width of its column's edge.
- The column is not always one exact edge. On some pages the bare cells spread
  up to ~2.5 character widths, consistent with decimal alignment. The per-page
  median and the cells that share a line with a comment sit on the common edge.
- A comment's number would sit at least 1 character width past it.
- No cell is read as blank. The one cell on a page with no reference cell uses
  the adjacent page's edge.

The header-less fragment path (`_parse_lines`, tests and scripts) has no
geometry and keeps the text rules. Remaining case: a bare number on its own
line after a row whose last cells are `---` is read as the last cell. Main did
the same for rows with no `---`.

The pressure columns are not monitor measurements (alerts and the Measurements
tab use temperature, O2, CH4 and CO). They are published in the wellfield
dataset (`stage_wellfield.py` stages every column), and Flow and the pressures
drive the column-shift check and the cross-table duplicate merge here. That is
why they get the same accuracy bar as the other columns.

**Positions are trusted only when the row is complete.** A row may need the new
reader (a `---`, or two cells on one line such as `100 150`) but still not fill
every column. Then some cell printed nothing at all, and where the later cells
belong is unknown. Only the cells main's reader took are kept, and those are
the positions main trusted. If that leaves nothing through Temp, the row is
counted in `ambiguous_rows` and not emitted. This stops a value from shifting
into Temp when Diff Press printed nothing.

**Main's rows keep main's behavior.** For each row, `_read_values` also reports
how many cells main's one-number-per-line reader would have taken.

- The negative-flow column-shift check judges main's rows on the Flow cells
  main read. Added rows can therefore neither reject a table main kept nor
  dilute a shifted one below the 25% threshold.
- The added rows get the same check on their own Flow cells, with the same
  20-row minimum. If they show the signature, only they are rejected
  ("column-shift signature in rows read past a blank cell"), and main's rows
  stay.
- The unlabeled-ADJ partner is the last row main would have emitted. Added rows
  (gas-only, or read past a `---`) take a partner's flag but never become one,
  so no row main emitted changes its ADJ flag. A recovered as-found row and
  main's next same-time row can therefore both read as as-found.

**Gas-only rows.** A row is emitted when its four gas columns hold numbers and
its cells reach the Temp position, even if Temp (or any later column) is
`---`. It carries no temperature, and `valid` still gates it on the gas sum. A
row whose cells end before Temp is not a blank Temp. That happens, for example,
when the Temp cell is a token the parser can't read, such as `177.0*`. Such a
row is counted in `truncated_rows` and not emitted, as on main. Otherwise it
would turn into a "no temperature" reading. A gas-only row is never the
as-found partner in the unlabeled-ADJ rule (see above), so the same-time row
after it keeps its real temperature as an as-found reading. Rows with no cells at all are neither emitted nor counted, as on main.
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

**Fuzzing.** Main and this parser were run on 600,000 random line sequences,
and the final version on 300,000 more in a second, independent fuzzer.
They mix headers (with and without Alias, ADJ and Diff Press), `---`, shared
lines, comments, stray well ids and timestamps. Every row main emitted is still
emitted with the same values. Outside Alias-labeled tables, every row keeps the
same ADJ flag and well id. Inside them, rows are re-keyed by design.

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
  - 306 all-`---` rows and 1,037 partial rows were counted. 0 rows were
    truncated, 0 were ambiguous, 0 Alias row starts were off-column, and 0
    comment-line cells were read as blank.
- 3,299 rows moved from the Alias spelling to the Well ID. 1,068 of them have
  a non-AH alias.
- Rows with a blank `header_pres` fell from 143,695 to 47,911. Of the rest,
  46,522 sit on pages where no row prints that column.
- Rejected rows rose from 9,519 to 9,573. The 54 new ones are rows in two of
  the split tables whose Alias is non-AH or blank. Main never started those
  rows. The reject still counts only the cells main's reader took (one bare
  number per line). A `---`, a shared line, and anything after them never
  count toward it.

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

## Addendum (2026-10-20): gas tables split across two page ranges

Spec: `docs/overnight-coder-handoffs/woi-split-table-join.md`
(`coder:woi-split-table-join`, the follow-up to the 2026-10-19 addendum).

**What these tables are.** Four reports in the corpus print each gas table
in two page ranges. The left half (Well ID, [Alias], Date/Time, ADJ, CH4,
CO2, O2, Balance, sometimes Diff. Press) runs on n pages. The right half
(Temp, Flow, Well Pres, Header Pres, Diff. Press when the left lacks it,
then Comments and sometimes pump counts) runs on the n pages right after it
and prints no Well ID and no timestamp. Read alone, the left half was
rejected ("column layout not recognized"), which is what the 2026-10-19
addendum kept.

**The join** (`_split_plan`, `_settle_split`, and the `split` path in
`_parse_table_lines_once`):

- A candidate is a rejected table whose header reads the four gas columns
  and maybe Diff. Press, and nothing else (`_is_split_left`). The first
  parse records it directly (`on_split_candidate`); nothing is parsed back
  out of `RejectedTable.header`.
- The left half must start on a page that prints that header (some reports
  print it on the first left page only), and no later left page may print
  a different one. The n pages after it must each read as a right half: one
  text direction, no Well ID, no well-ID line, no timestamp anywhere, at
  least one line of cells. The page after them must not: a Temp label or a
  complete row there means the page counts differ. A page of other columns
  (one report prints pump counts and liquid depths on a third range) ends
  the run.
- Left page k pairs with right page k + n. A left row's coordinate is the
  baseline of its Date/Time line across the text (`-ox*sin + oy*cos` of the
  line's first span origin, in the line's own direction). Its right row is
  the right-page cells on the same baseline (`_SPLIT_ROW_TOLERANCE` 0.5 pt;
  every pair in the corpus differs by exactly 0).
- The right half's column order comes from its label positions, never the
  text order: on `ed1617e8` the text reads "Temp." first, but Diff. Press is
  printed first. Label words one space apart form a phrase; phrases that
  overlap along the text form one column (a wrapped label); each column runs
  from its label's start to the next label's start. The measurement columns
  must come first, each named once; Comments and pump counts follow and are
  never read (Trisha's 2026-10-19 ruling). Labels are read from the right
  page's own header band (above its first row of cells), or, on a page that
  prints none, from the nearest labeled right page.
- A cell must also line up with its column's bare cells: its centre, start
  or end within `_SPLIT_ALIGN_TOLERANCE` (0.3) of a character width of their
  median. On a page without labels, the median is the labeled page's, so a
  page drawn elsewhere can't put Flow under Temp. Measured: every corpus
  cell is within 0.03 of a character width (centred columns) or exactly 0
  (the right-aligned table). A comment that starts with a number sits at the
  Comments position and lines up with nothing, so it never fills a column.
  `---` is a blank cell that keeps its column.
- The left columns plus the right measurement columns must equal one of
  `LAYOUTS` as a set. A right row is used only when every measurement column
  holds exactly one aligned cell.
- A page joins only if every left row has its own right row (none shared)
  and every complete right row no left row claims sits on a left-page
  timestamp line the row reader did not start. On `eb09e953` 117 right rows
  are such leftovers: their left row prints `10/21/2022 11:46 ADJ` on one
  line, which `DT_RE` does not match, so main never reads that row (see the
  follow-up below). Any other leftover means the halves don't agree.
- A joined row takes its Well ID, Alias, timestamp and ADJ from the left
  half, then goes through everything a normal row does: the blank/partial
  counts, `valid`, the negative-flow check of its own table, the
  unlabeled-ADJ rule, and, in the router, `merge_cross_table_duplicates`,
  the plausibility ceiling and the data-loss guard.
- Rows that don't join stay rejected, counted in `split_unjoined_rows` and
  listed as a `RejectedTable` with the fixed reason
  `split table: rows not joined to a right-half row`. A left row whose left
  cells are not all read is left to the old reject. A table whose right half
  isn't found stays rejected exactly as before. All of this is best-effort:
  an exception in the geometry leaves that table rejected, and
  `parse_gas_extraction_report` never raises from it.

**Proof that the right rows are paired.** The same reports print 803 of the
joined readings elsewhere with a temperature (by well, timestamp and ADJ:
`ed1617e8` 304, `5379a301` 219, `eb09e953` 280). Every one matches exactly
on every column both copies print: CH4, CO2, O2, Balance, Diff. Press (left
on two reports, right on `ed1617e8`, where the text order would have swapped
it with Temp) and Temp on all three, and Flow on `eb09e953`. Ten of them
have two copies elsewhere (two readings in the same minute), and the joined
pair equals that pair. `afc0218c` prints none of its joined readings
elsewhere with a temperature. Its two split tables (pp 1-60 and 191-220)
print 166 of the same readings, each joined from a different right range,
and all 166 agree on all nine columns. Well Pres and Header Pres are never
printed twice in the same report; the check against other filings below
covers them.

**Guards added in review** (each has a synthetic-PDF test). A candidate's
left pages must each print rows and each right page must hold a complete
right row, so two split tables in a row (or footer-only pages) can't lend one
table the other's right half. A right row is not joinable if a number sits
before the first label, or a measurement column holds a second or unaligned
number; only the unaligned leading number of a comment line is skipped. A
right page without labels must have its columns of numbers under the same
labels as the page it borrows from, and no label holding two such columns.
A row with one more left cell than its header names is not joined. An
unjoined row is still the unlabeled-ADJ partner of the next row. The page
after the right run being unreadable refuses the join, and any exception in
the join keeps the first parse. Right-half rows whose left row is never read
are counted in `split_unread_rows`.

**Measured effect** (91 WOI-format cache files, 42 distinct parse outputs,
same corpus as the 2026-10-19 addendum; counts over the 42):

- Every reading main reads is still read with identical values, ADJ flag,
  well id, alias and page: 0 lost, 0 changed.
- Readings rose from 278,407 to 287,980: +9,573, all joined split-table
  rows (`ed1617e8` 2,296, `5379a301` 1,374, `eb09e953` 2,447, `afc0218c`
  3,456). Rejected rows fell from 9,573 to 0; 0 rows were left unjoined.
  The only other counter that moved: `partial_rows` +2 (two left rows of
  `ed1617e8` with a `---` gas cell, which main never counted).
- Every joined row is valid (gas sum) and has a temperature; joined
  temperatures run 23.7-169.7°F. Reading dates run 2022-07-08 to 2024-03-28,
  a year and more before these 2026-02-05 filings.
- Every one of the 9,573 joined readings is also printed, by well,
  timestamp and ADJ, in at least one OTHER filing in the corpus that main
  reads normally (not a byte-different copy of the same report), and all of
  them agree on every column both print, Well Pres and Header Pres included
  (two `eb09e953` rows print no Diff. Press or pressures there). So the
  joined readings largely duplicate data the monitor already holds from
  those filings, and they confirm the join.

Through `route_measurements` (live `config.yml` and alias map), on the
four reports:

- `merge_cross_table_duplicates` collapses the in-report overlap as
  expected (304, 280; 220 on `5379a301`).
- No per-well maximum or site peak changes except through a joined reading.
  Site peak: unchanged on `ed1617e8` (170.2°F, 2022-06-13) and `eb09e953`
  (171.8°F, 2021-11-04). On `5379a301` it rises from 152.0°F (2024-03-13) to
  158.3°F (AHWW312R, **2023-08-29**), the out-of-period reading the
  CLAUDE.md Assumptions section names; the headline carries its own date.
- `afc0218c` did not route on main (every row rejected, so the data-loss
  guard held). It now routes: 650 measurements, peak 169.7°F (AHWW312R,
  2022-07-28).
- New temperature measurements ≥ 131°F: 7, 220, 7 and 212; ≥ 145°F: 0, 147,
  0 and 125. 702 joined readings are ≥ 145°F (as-found and ADJ; listed on
  PR #166). `well_watch` hits are unchanged (none). Severity after
  `recheck_severity_floor` stays urgent where it was.

**Residual risk, accepted.** Nothing on a right page names its row. Pairing
rests on the page offset, equal baselines and the column checks above. A
right page from the wrong position with the same row grid, or a page without
labels drawn exactly one column over with evenly spaced columns and the same
number of number columns under each label, can't be told apart by geometry.
The real reports print one template on every page, and every joined reading
in the corpus matches another filing.

**Follow-up (not done here).** Some rows print their timestamp and `ADJ` on
one line (`10/21/2022 11:46 ADJ`). `DT_RE` doesn't match that line, so the
row reader never starts the row, in split tables (117 right-half rows on
`eb09e953`, counted in `split_unread_rows`) and in ordinary tables alike.
Such lines appear in six corpus reports (counting byte-different copies
once), 3,540 in all: `5379a301` 1,768, `31dbbade` 1,201, `eb09e953` 199,
`ed1617e8` 159, `d609654d` 131, `7504fbc4` 82. Widening `DT_RE` adds readings to tables
main already reads, so it needs its own regression. (Done: see the
2026-10-21 addendum.)

Nothing is re-extracted by this change. Re-extracting the four reports and
re-running `stage_wellfield.py` wait for Trisha's go.

## Addendum (2026-10-21): timestamp and ADJ printed on one line

Spec: `docs/overnight-coder-handoffs/woi-dt-adj-oneline.md`
(`coder:woi-dt-adj-oneline`, the follow-up named in the 2026-10-20
addendum).

**What these rows are.** Some reports print an after-adjustment row's
timestamp and its `ADJ` token on one text line (`10/21/2022 11:46 ADJ`)
instead of two. `DT_RE` only matches a bare timestamp, so the row reader never
started these rows. Their cells were skipped, in ordinary tables and in split
tables alike (on `eb09e953` the right-half rows they leave are the 117
`split_unread_rows`).

**Change.** `DT_ADJ_RE` matches a timestamp followed by whitespace and `ADJ`,
and nothing else on the line. `_dt_line()` returns the timestamp and an ADJ
flag for either form, and the row reader and the split planner's "every left
page prints rows" check use it. A row started this way is flagged ADJ, exactly
as a literal `ADJ` line after the timestamp flags it (one printed after it as
well is the same token), and its `dt` is the timestamp part only, so both
forms give the same reading.

Main never read these rows, so nothing about them may move a row main reads:

- **Header.** The header text, and so the ADJ and Alias labels and the text
  columns read from it, is still the text main read: it ends at the first row
  whose timestamp prints alone. Main's header ran on through a first row
  printed in the one-line form (and could take its `ADJ` as a label); it still
  does. Only the row reader starts earlier, at that row.
- **Unlabeled ADJ.** They never become the unlabeled-ADJ partner.
- **Negative flow.** Their Flow cells are checked in their own pool, apart from
  main's rows and apart from the rows read past a `---`. If they show the
  signature only they are rejected, with the reason "column-shift signature
  in rows started by a one-line timestamp ADJ". Neither added pool can take
  the other's rows with it.
- **Last-column edge.** They don't feed the per-page edge that places a
  pressure cell sharing its line with a comment.
- **Split tables.** They don't count toward a page's pairing verdict, which is
  decided by main's rows exactly as before. On a joined page, each takes the
  leftover complete right row at its own baseline when no other left row
  claims it. One that has none stays unjoined on its own (counted in
  `split_unjoined_rows`) and the rest of the page still joins.

Not matched, on purpose: `ADJ` with no space before it, lower case, anything
after `ADJ`, and `AM`/`PM` timestamps. (The corpus prints 5,951
`M/D/YYYY H:MM AM` / `PM` lines; none follows a Well ID line, so none is a
gas-table row start.)

**Measured effect** (91 WOI-format cache files, 42 distinct parse outputs,
the same corpus as the 2026-10-19 and 2026-10-20 addenda; origin/main @
`59462a4` vs this change):

- Every reading main reads is still read with identical values, ADJ flag,
  well id, alias and page: 0 lost, 0 changed.
- Readings rose from 287,980 to 291,713: +3,733, every one flagged ADJ.
  Per report (byte-identical twins counted once): `5379a301` 1,767,
  `31dbbade` 1,196, `eb09e953` 199, `3cdf4c5e` 199, `ed1617e8` 159,
  `d609654d` 131, `7504fbc4` 82. `3cdf4c5e` is a full-width reprint of
  `eb09e953`'s tables, so its 199 lines are the same rows; that is why the
  total is above the 3,540 counted with copies merged.
- Of the 3,739 one-line lines, 6 start no reading: 4 rows of `31dbbade`
  print `---` in every cell (counted in `blank_rows`, +4), and one row each
  of `31dbbade` and `5379a301` is well `HC1LARON`, which `WELL_RE` does not
  read in either form. One added `5379a301` row prints all-zero gas and no
  temperature; it is kept and is not valid, as for any such row.
- On `eb09e953` the 117 right-half rows now join: `split_unread_rows` 117 to
  0, `split_joined_rows` 2,447 to 2,564. No other counter moved, and no table
  was rejected.
- Every added reading is also printed, by well, timestamp and ADJ, in at
  least one OTHER filing in the corpus where main reads it (in the two-line
  form), and every one agrees on all the columns both print. Temperature is
  among them for all but one (the all-zero row above). 0 mismatches.
- Added temperatures run 4.8-172.5°F. 119 added readings are at or above
  145°F (listed on the PR); all are ADJ readings, and their reading dates run
  2021-10-12 to 2024-03-30.

Through `route_measurements` (live `config.yml` and alias map), on the seven
reports: Measurements, per-well maxima, site peak, key data point and severity
after `recheck_severity_floor` are identical to main, and `well_watch` hits
and review flags are unchanged. That is expected: Measurements and the per-well
summary use as-found readings only. Only the reading counts move.

**Known, accepted (review round 2, all low).**

- A table main leaves rejected stays rejected: a split-table left page that
  prints only one-line rows does not count as a page of rows (main's
  `DT_RE` rule), so no join is planned and no row main started becomes a
  reading.
- Main's column-shift reject entry keeps main's rows and page range; one-line
  rows rejected with it get their own entry.
- Other reject entries (rows under an unrecognized header, split rows not
  joined) and the row counters (`blank_rows`, `partial_rows` and the like)
  now include the one-line rows they hold, because those rows are now read.
  This changes counts and review-flag text only, never a reading.
- `31dbbade` prints 86 ADJ readings twice, one-line on about p25-26 and
  two-line on about p79-81. `merge_cross_table_duplicates` keeps the first
  copy, so the kept reading's page moves from p79 to p25. The values are
  identical; ADJ readings never reach Measurements or the headline, so only
  review-flag page numbers can change.
- A one-line first row under a header is read into the header text, as on
  main, so its ADJ counts as an ADJ label there. On a page boundary that can
  leave a page of one-line rows as its own rejected table: recovery is
  incomplete in that case, and no main reading changes.

Nothing is re-extracted by this change. Re-extracting these reports waits for
Trisha's go.
