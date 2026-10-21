# Monitor parser: join WOI gas tables split across pages (spec, 2026-10-20)

**Status: SPEC ONLY.** Trisha approved writing this up on 2026-10-20 ("write up split-table join PR for overnight coder but do not start"). Queued as `coder:woi-split-table-join`. This is the follow-up to PR #165 (`coder:woi-parser-wellid-dashes`, merged 2026-10-20 as `96ea52a`).

## Why

PR #165 found that the ~9,500 rows still rejected as "column layout not recognized" are not unknown layouts. They are the **left halves of gas tables printed across two page ranges**.

- **Left half:** Well ID, [Alias], Date/Time, ADJ, CH4, CO2, O2, Balance, and sometimes Diff Press. Every row has its well and timestamp.
- **Right half:** Temp, Diff Press (when not on the left), Flow, Well Pres, Header Pres and Comments, sometimes followed by pump-count columns. It is printed on the **same number of pages, starting right after the left range**, and it carries **no Well ID and no timestamp**.

#165 kept these tables rejected. A gas-only layout would have:

- reported every well in them as having no temperature;
- double-counted O2;
- stopped the reject from catching misread Temp headers.

The fix is to join each left row to its right row. That recovers the temperatures, and most of these readings appear nowhere else in the report.

## The four reports (doc-cache sha prefix; several cache files are byte-different copies)

| Report | Left (gas) pages | Right (Temp) pages | Rejected rows | Also printed elsewhere with a temp |
|---|---|---|---|---|
| `5379a301…` (= `c44aca9a…`) | 517–554 | 555–592 | 1,374 | 219 (16%) |
| `eb09e953…` (= `804eb0d0…`) | 1–70 | 71–140 | 2,447 | 280 (11%) |
| `afc0218c…` (= `70aa2ee5…`) | 1–60 and 191–220 | 61–120 and 221–250 | 2,394 + 1,062 | 0 |
| `ed1617e8…` (= `78a21bc9…`) | 1–78 | 79–156 | 2,298 | 304 (13%) |

Measured 2026-10-20 by scripts in the #165 session scratchpad:

- The rejected rows are the "rejected" column, read as gas-only.
- The "also printed elsewhere" counts are by well + timestamp + ADJ against that report's own parsed readings.

## Feasibility already measured (2026-10-20)

**Rows align by vertical position.**
- Each left page *k* pairs with right page *k + n*, where *n* is the number of left pages.
- On every pair, every left row's across-the-text coordinate (the row "y" in the text's own frame) has a right-page cell line at exactly the same coordinate.
- 271 of 276 pairs line up one to one. On the other 5, the right page has 1–4 extra coordinates, probably wrapped comment lines that start with a number. Every left row still has its match.
- Method: PyMuPDF `get_text("dict")`. Each line's coordinate is `-ox*sin + oy*cos` from its first span's origin, with `(cos, sin)` = the line's `dir`. Right-page cell lines are the lines that start with a number or `---`.

**Right-half column order can't come from text order.**
- `ed1617e8` p80 prints the labels as `Temp., Diff. Press., Flow, Well Pres, Header Pres`. The cells, though, are `3.56, 133.4, 52.5, -5.58, -42.49`: Diff Press first, then Temp.
- The order must come from the label words' positions, the way `_page_header_columns` already does for left headers. The right half has no "Well ID" anchor, so the reading frame has to come from the line `dir`.
- The labels are not always on the first right page. `5379a301` p555 prints only `Temp., Comments`, and the full label set is on p556. The coder should collect labels across the right range, or from the first right page that carries them.

**Cell order within a right row is not one line per cell.**
- Values can share a line: `-34.83 No Change,Second Reading: :`.
- Rows are interleaved with comment lines.
- Group a right row's cells by coordinate, and order them along the text direction using each line's start position.

## The change (in `woi_table_parser.py`)

1. **Detect a split table.** It is a gas header that #165 rejects as "column layout not recognized", whose header words read gas columns only (`ch4,co2,o2,balance[,diff_press]`, no Temp). It must be followed, directly after its last page, by a run of pages that:
   - carry Temp-family labels and numeric cell lines;
   - carry no row timestamps;
   - have the same page count as the left range.

   Anything that doesn't fit stays rejected exactly as today, with the same reason text.
2. **Pair pages** by offset (left *k* ↔ right *k + n*). If the counts differ, don't join and keep the reject.
3. **Pair rows by coordinate.**
   - Each left row's coordinate comes from its Well ID or timestamp line.
   - A right row is the set of cell lines at that coordinate, within a small tolerance. Measure the tolerance; the corpus shows exact matches.
   - If any left row on a page has no right row, or two left rows share one, don't join that page. Count it and keep those rows rejected.
4. **Read the right row's cells** in column order (the label positions), with the #165 cell rules:
   - `---` is a blank cell that keeps its position;
   - a comment ends the row;
   - a value that shares a line with a comment must line up with its column (the geometry check from #165).

   The combined columns must form one of the known measurement sets in `LAYOUTS`, compared by column name, not order. Otherwise keep the reject.
5. **Emit** one `WOIReading` per joined row. Use the left half's Well ID, Alias, timestamp and ADJ, and the existing `canonicalize` / alias map. The joined row then goes through everything a normal row goes through:
   - `valid`;
   - the negative-flow check, run per joined table;
   - the unlabeled-ADJ rule;
   - `merge_cross_table_duplicates`;
   - the router's plausibility ceiling and guard.
6. **Count** what doesn't join, and log it as counts only, in a new `GasExtractionParse` field (e.g. `split_unjoined_rows`) and/or a `RejectedTable` with a fixed reason phrase.
7. **Out of scope:** the Comments column, valve settings, and the pump-count columns printed after Comments on some right pages. That is Trisha's 2026-10-19 "NOT YET" ruling. Read only through Header Pres.

## Tests to require

- **Synthetic PDF fixtures** (built in-process, never committed):
  - a left/right split, unrotated and rotated;
  - right-half labels in a different order from their text order;
  - a right row with a value and a comment on one line;
  - a `---` cell;
  - a right page with an extra wrapped-comment line;
  - a page-count mismatch, which must stay rejected;
  - a left row with no right match, which must be counted and not joined.
- **Validation against the report's own duplicates.** For the 803 overlap rows (219 + 280 + 304), the joined row's values must equal the copy printed elsewhere in the same report:
  - CH4/CO2/O2/Balance on the left;
  - Temp/Flow/pressures on the right.

  This is the real proof that the join pairs the right rows. Report how many match exactly. Any mismatch is a stop.
- **Regression:** the 91 local WOI-format cache files (the same corpus as #165: every PDF under `/Volumes/Samsung-Pro-2TB/data/wellfield-auto-staging-cache/doc-cache/` and `/Volumes/Samsung-Pro-2TB/data/vgc-liquid-cache/` that `woi_router.is_woi_report()` accepts; 42 distinct parse outputs).
  - Every reading main reads is still read, with identical values, ADJ flag and well id.
  - No reading main emits is lost.
  - Report the new joined counts per report.
  - The site peak and each well's max temperature may only change by newly joined readings. List every one, and every joined reading ≥ 145°F.
- **Downstream:** `route_measurements`, `well_watch`, `recheck_severity_floor` and `build_key_data_point` run on the joined readings. List any new ≥ 131°F measurement.

## Live path

`woi_table_parser` is live, so verification on the real specimens is mandatory and a green run with mocked inputs is not enough to merge. End at a **draft PR** for Trisha. Add an ADR 005 addendum.

## After it merges (Lotext side)

Re-extracting already-processed reports, and re-running `stage_wellfield.py`, happen only on Trisha's go. These four reports are 2026-02-05 nSITE filings that are already in the staged backlog. The new-readings release (TASKS "Wellfield release plan", step B) would then include the joined readings.
