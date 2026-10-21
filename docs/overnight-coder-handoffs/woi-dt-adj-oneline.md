# coder:woi-dt-adj-oneline (spec, copied from the overnight-coder queue 2026-10-21)

Copied verbatim from the queue block; this block is the spec.

## coder:woi-dt-adj-oneline -- WOI parser: read rows whose timestamp and ADJ print on one line ("10/21/2022 11:46 ADJ"; 3,540 unread lines) -- Trisha-approved 2026-10-21

- **Dependency:** null (builds on PR #166, merged 2026-10-21 `59462a4`).
- **Handoff:** none needed; this block is the spec (source: ADR 005 addendum 2026-10-20, "Follow-up (not done here)").
- **Readiness:** READY, LOCAL run (needs the doc-cache corpus). `woi_table_parser` is a LIVE path: real-specimen verification is mandatory, no mocked-green merge; ends at a DRAFT PR.
- **What happened:** some reports print a row's timestamp and `ADJ` on ONE line (`10/21/2022 11:46 ADJ`). `DT_RE` does not match that line, so the row reader never starts the row. This hits ordinary tables and split tables alike: 3,540 lines across 6 corpus reports (byte-different copies counted once): `5379a301` 1,768, `31dbbade` 1,201, `eb09e953` 199, `ed1617e8` 159, `d609654d` 131, `7504fbc4` 82. On `eb09e953`, 117 split-table right-half rows are counted in `split_unread_rows` because of it.
- **Do:** (a) widen `DT_RE` (or add a sibling pattern) so a timestamp line ending in `ADJ` starts a row flagged ADJ, exactly as the two-line form does today; (b) the `split_unread_rows` leftovers on `eb09e953` should then join (target 0 unread there, or explain each remainder); (c) tests: the exact one-line form, with and without extra spaces, plus the existing two-line form unchanged.
- **Verify (live path):** 91-file regression vs origin/main: 0 readings lost, 0 changed; per-report counts of added readings (expect roughly the 3,540 above); every added reading cross-checked against another copy in the corpus where one exists (mismatches = stop); every new >=145F reading listed with its own reading date; downstream `route_measurements` / headline changes per report, as PR #166 did.
- **Goal (paste this):**

  ```
  Follow docs/overnight-coder.md. Goal + full spec: the coder:woi-dt-adj-oneline block in /Volumes/Samsung-Pro-2TB/Lotext/documents/overnight-coder-queue.md -- read that block first, it IS the goal. Copy it to docs/overnight-coder-handoffs/woi-dt-adj-oneline.md as the PR's first commit.

  In one line: in woi_table_parser.py, make a timestamp line that ends in ADJ ("10/21/2022 11:46 ADJ") start an ADJ row just like the two-line form, so the 3,540 such lines across 6 reports are read (and eb09e953's 117 split_unread_rows join). LIVE path: 91-file regression (every reading main reads unchanged), per-report added counts, cross-filing value check, every new >=145F reading listed, downstream headline changes. Start from a fresh branch off origin/main. End at a DRAFT PR.
  ```
