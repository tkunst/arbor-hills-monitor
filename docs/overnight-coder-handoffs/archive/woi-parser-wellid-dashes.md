# Monitor parser change still needed: round-4 "parser-first" items (spec, 2026-10-19)

**Status: SPEC ONLY. Nothing in `tkunst/arbor-hills-monitor` was changed.**

**✅ RULED 2026-10-19 (Trisha):** (1) open the monitor parser PR: YES (queued as `coder:woi-parser-wellid-dashes`); (2) keep gas-only readings: YES (item 3 below is in scope); (3) capture valve-setting + comments columns: NOT YET (item 5 stays out of scope). This is the
"monitor parser PR first" half of the TASKS item *GO? Wellfield parser-first fix pass
(round 4 findings)*. Trisha ruled 2026-10-19: "Check if PR 161 is needed first, if not go
ahead and process."

## Why this is still open

**"PR 161" is not a PR.** No PR #161 exists in the monitor repo.

- **Issue #161** is "Urgency: the parser's floor and the alert check read two separate
  145°F settings". It is about alerting: `egle_doc_parser` floor vs
  `email_alerts.is_urgent`, and rounded vs unrounded comparison.
  - It touches no WOI table parsing.
  - It does not block the wellfield staging.
  - It stays open as its own item (TASKS line 733).
- **The two parser PRs merged since the round-4 review don't cover the round-4 parser
  items:**
  - #163 (`4bb63bc`) widens the gas-sum gate to 95.0–101.5.
  - #164 (`b3e82a9`) reads the second same-time row as ADJ in tables with no ADJ column.
  - Neither one touches row start, `---` handling, or the minimum-columns rule.

The round-4 items, checked against `woi_table_parser.py` on `origin/main` `ee5b5d9`:

| Round-4 finding | Cause on main |
|---|---|
| **HIGH-1:** the parser reads the **Alias** column, not Well ID. This gives 222 excess rows (828 under alias spellings). | `_is_row_start(i)` needs `WELL_RE` at `i` and `DT_RE` at `i+1`. In a `Well ID / Alias / Date` table the token just before the date is the **Alias**, so the Alias becomes the row's well. |
| **314 readings dropped** where the Alias is not AH-format (`EW-008`, `V-10`, `EW-17R`). | `WELL_RE = ^AH[A-Z0-9]+\*{0,4}$` fails on the Alias, so the row is never started. |
| **HIGH-2:** `header_pres` is blank on 14,810 new rows (base 2025 H2: 0 of 13,326). | Numbers are collected only while a whole line matches `FLOAT_RE`. A line `27.6 ---` or `-0.68 No Change: :` ends collection, so the value on it and every later column are lost. |
| **1,457 readings dropped** that have a printed temperature when flow is `---`. | Same cause. A `---` that shares the temperature's line stops collection before Temp, and the `len(nums) > layout.index("temp")` rule then drops the row. |
| **2,620 gas-only readings dropped** (temperature printed `---`). | Same rule: a row needs values through Temp. |

**Also open: whole tables rejected as "column layout not recognized".** These rejections
were already on NEEDS-REVIEW on 10/15. They are not part of the round-4 accounting.

- Under "no data gets dropped" they are another gap: ~9,500 unique printed rows, and it
  is not yet known how many are new.
- They come from the 2026-02-05 nSITE filings. Several are byte-different copies of the
  same report, so the same page ranges and counts repeat.
- In every case the header was read as **gas columns only**:
  - `-8005756543724138564` and `7178938722542851531`: pp517–554, 1,374 rows, read as
    `ch4,co2,o2,balance,diff_press`;
  - `-8299201530671053060` and `3332516035587210221`: pp1–70, 2,419 rows, same columns;
  - `1230339285137039248` and `2143683964897446383`: pp1–60 (2,394 rows) and pp191–220
    (1,062 rows), same columns;
  - `3105320987195007474` and `464826453042482776`: pp1–78, 2,270 rows, read as
    `ch4,co2,o2,balance`.
- These are either gas-only tables (no Temp column, which ties to item 3 below) or a
  header whose Temp/Flow labels the word-position reader misses.
- **The PR should:**
  - say which;
  - add the layout(s) to `LAYOUTS` if real;
  - keep the reject for anything still unrecognized.

The staging fixes in `PROCESS-NOTES-2026-10-19.md` were applied without this change.
These five gaps remain in the staged set until the parser PR merges and staging is re-run.

## What the lines look like (cached CJ/VGC reports, PyMuPDF `get_text()` lines)

```text
'AHWFLROT', 'AHWFLROT', '2/18/2025 15:36', '49', '35.9', '1.7', '13.4 ---', '23.6 ---', '---', '---', 'No Change: :'
'AHW264R3', 'AHW264R3', '10/25/2023 8:38', '---', '---', '---', '---'
... '27.6 ---', '14.15', '-0.68 No Change: :', <next row's Well ID>
```

(Doc-cache files `0bbf365b1033…` p196 and `0f2abeab850a…` p187.)

## The change

1. **Read the Well ID column.**
   - Applies only under a gas header whose label text contains `Alias` (detect it like
     `_ADJ_LABEL_RE`).
   - A row there starts at `WELL ID, ALIAS, DATE TIME`:
     - the Well ID matches `WELL_RE`;
     - the Alias matches a looser ID pattern (`^[A-Z0-9][A-Z0-9-]*\*{0,4}$`, so `EW-008`
       and `V-10` qualify).
   - `well_id` is the canonicalized **Well ID**. `raw_well_id` is the Well ID as printed.
     Add `alias_as_printed: Optional[str]` to `WOIReading`.
   - Tables with no Alias label are parsed exactly as today.
   - Keep the unlabeled-ADJ rule (#164) keyed on the Well ID.
2. **Tokenize value lines; never stop on `---`.**
   - Split each line after Date/Time (and ADJ) on whitespace.
   - A numeric token fills the next layout slot.
   - `---` fills the next slot with `None`: a blank cell, still a position.
   - A non-numeric, non-`---` token (a comment such as `No Change: :`, `Flare`) ends the
     row **after** any numeric tokens before it on that line are taken.
   - Stop at `len(layout)` slots or at the next row start / header.
3. **Keep gas-only readings.**
   - Emit a row when the four gas columns are present, even if Temp (or anything after it)
     is `None`.
   - `valid` keeps its gas-sum gate. A row with Temp `None` simply carries no temperature.
   - **This needs Trisha's ruling.** The open question on the TASKS line is "keep gas-only
     readings?". Her 2026-10-16 "no data gets dropped" ruling points to yes, but it has not
     been ruled.
4. **Rows that print `---` in every column.** Do not emit them: there is no reading. Count
   them in a new `GasExtractionParse.blank_rows` so the number is visible.
5. **Not in scope unless Trisha rules it:** capturing the valve-setting and comments
   columns (the other open question on the TASKS line).

## Tests to require in the PR

- **Synthetic line fixtures** for the three printed shapes above:
  - value plus `---` on one line;
  - value plus comment on one line;
  - all-`---` row;
  - a Well-ID/Alias pair with a non-AH alias (`AHEW0008` / `EW-008`);
  - a letter-O alias (`AHWTR022` / `AHWTRO22`).
- **Regression:** the 32 local WOI-format specimens.
  - Every reading on main must still be read, with identical values and the same Well ID
    wherever the table has no Alias column.
  - Report the new counts per specimen.
- **Expected direction on the staged backlog** (round-4 audit numbers, to be re-measured):
  - +~1,457 readings with temperature;
  - +~314 non-AH-alias readings;
  - +~2,620 gas-only readings (if ruled);
  - `header_pres` filled on most of the ~14,810 new rows;
  - the alias-spelling excess falls to ~0 without relying on the alias map.
- **Downstream:**
  - `woi_router.route_measurements`, `well_watch` and `recheck_severity_floor` must accept
    `temp is None` rows without changing any well's max temperature.
  - The site peak is unchanged.

## After the PR merges (Lotext side)

1. Re-run `stage_wellfield.py --no-intake` from a clean `main` (README, "Run"). The
   alias entries added 2026-10-19 stay. They still relabel the published 10-09 rows
   (`AHWTGW17`, the letter-O rows) and any report that prints the alias spelling in the
   Well ID column itself.
2. Separately, the 59-entry alias map has **not** been exported to the monitor's
   `data/well_aliases.json`. That is a small monitor PR of its own (NEW-WELLS-REVIEW,
   "What happens after a ruling", step 1).
3. Review round 5, then the refutation gate.
