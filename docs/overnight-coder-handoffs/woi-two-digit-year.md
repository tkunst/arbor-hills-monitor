# coder:woi-two-digit-year (spec, copied from the overnight-coder queue 2026-10-21)

Copied verbatim from the queue block; this block is the spec.

## coder:woi-two-digit-year -- WOI parser: read timestamps printed with two-digit years ("10/1/25 7:56", "10/1/25 15:55 ADJ"; ~19,750 unread lines in 2 reports) -- Trisha-approved 2026-10-21

- **Dependency:** null (`coder:woi-dt-adj-oneline` / PR #167 merged 2026-10-21 `dad8796`; both change the `DT_RE` / `DT_ADJ_RE` / `_dt_line` row-start rules).
- **Handoff:** none needed; this block is the spec (found by PR #167's round-3 review, 2026-10-21).
- **Readiness:** READY, LOCAL run (needs the doc-cache corpus). `woi_table_parser` is a LIVE path: real-specimen verification is mandatory, no mocked-green merge; ends at a DRAFT PR.
- **What happened:** two doc-cache reports print the date with a two-digit year. Neither main nor #167 reads any row of them by text parsing (0 readings): `97d39ba7` has 4,047 bare-timestamp lines plus 3,923 one-line `timestamp ADJ` lines; `6d0f88ed` has 11,786 bare-timestamp lines.
- **Do:** (a) accept `M/D/YY H:MM` (and `... ADJ`) as a row-start timestamp; (b) normalize the stored `dt` to the four-digit form (`10/1/2025 7:56`) so dedupe keys match readings already held from other filings (decide and document the century rule: 20YY, with a sanity bound against the filing date); (c) tests for both forms plus near-misses (date-only, three-digit year, AM/PM).
- **Verify (live path):** 91-file regression vs origin/main: 0 readings lost, 0 changed; per-report added counts for the 2 reports; every added reading cross-checked against another filing where one exists (mismatches = stop); every new >=145F reading listed with its own reading date; downstream `route_measurements` / headline / severity changes per report (these are as-found readings too, so headlines CAN move; list them).
- **Goal (paste this):**

  ```
  Follow docs/overnight-coder.md. Goal + full spec: the coder:woi-two-digit-year block in /Volumes/Samsung-Pro-2TB/Lotext/documents/overnight-coder-queue.md -- read that block first, it IS the goal. Copy it to docs/overnight-coder-handoffs/woi-two-digit-year.md as the PR's first commit.

  In one line: in woi_table_parser.py, read row timestamps printed with a two-digit year ("10/1/25 7:56", and "10/1/25 15:55 ADJ"), storing dt in the four-digit form, so reports 97d39ba7 and 6d0f88ed (~19,750 lines, 0 read today) parse. LIVE path: 91-file regression (every reading main reads unchanged), per-report added counts, cross-filing value check, every new >=145F reading listed, downstream headline/severity changes. Start from a fresh branch off origin/main. End at a DRAFT PR.
  ```
