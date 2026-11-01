# Overnight-coder handoff: consolidate sheet_writer copy blocks and the nSITE watcher scaffolding

*Staged 2026-10-14 from the code-modernization assessment (Technical Debt #2, #3, #4, #5).
Read `docs/overnight-coder.md` first. **Dependency: `coder:state-reads-raise` merged**
(the new generic readers must be the raising kind). Behavior-preserving refactor of LIVE
paths: the existing test suite is the equivalence harness. **DRAFT PR(s) for Trisha's
review, no autonomous merge.** Recommended tier: **Sonnet**. This is large: ship it as
up to three separate PRs in the order below, and STOP after any one if review rounds do
not converge.*

## Invocation

Branch name suggestions: `sheet-writer-generic`, `nsite-watch-common`, `process-one-doc`.

## PR 1: generic sheet_writer helpers

- About 830 lines of `sheet_writer.py` are near-identical apart from the tab/header
  constants (AST-compared): 25 `ensure_*_tab(s)` functions, 12 `append_*_watch_row`
  functions, and the `last_*_snapshots` readers (after `coder:state-reads-raise`, all of
  them raise).
- Replace them with `ensure_tab(service, sheet_id, tab, headers)`,
  `append_watch_row(service, sheet_id, tab, row)`, `last_snapshots(service, sheet_id, tab,
  key_col, ...)`. Keep the old public names as thin one-line wrappers in this PR so no
  caller changes; a later PR can inline them.
- The 42 tab constants stay where they are. Do not rename any tab.

## PR 2: nsite_watch_common

- Byte-identical helpers copied across watchers: `_now` (16 copies), `_today_date` (8),
  `_today` (8), `alerting_is_configured` (6), `_row_digest` (5), `_load_json` (12 copies, 2
  variants). `run()` is ~72% similar across violations / compliance actions / evaluations /
  permits / complaints / public notices.
- Move the helpers and a parameterized `run` skeleton into `nsite_watch_common.py`. Each
  watcher passes its config key, key prefix, sheet functions, and its OWN
  `_diff_and_record` / `summarize_*_change` / `_cell_payload`.
- **Do not merge the diff designs.** Multiset (violations, compliance actions),
  ref-number-keyed (evaluations, permits, public notices) and fingerprint (complaints) are
  deliberate (ADR 023 / 029 / 031). Leave `nsite_submissions_watcher.py` out (only ~28%
  similar).
- Add identity tests for each shared helper, like the existing `_is_due` identity test.

## PR 3: one per-document pipeline (+ optional gfl_air split)

- `watcher.run` (~170) and `backfill.run` (~164) share ~55 lines (title resolution,
  `parse_document`, alias/router threading, logging); ADRs 065, 067 and 068 each had to be
  wired into both. Extract `process_one_doc()` used by both. Preserve the inline
  `archiver.mirror_one_now()` call and the Sheet-row-before-state invariant.
- Optional, only if PR 3's review converges in one round: split `gfl_air_watcher.py`
  (1,685 lines, ~7 concerns) along its seams: episodes, email rendering, liveness/capture,
  thin `run`. Pure moves, no logic change.

## Verification (Step 3)

Each PR: full suite green, plus a local read-only run of two affected watchers against
the live Sheet showing identical outputs before/after (same rows, same snapshot hashes).
Report the line-count delta.
