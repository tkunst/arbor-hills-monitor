# Overnight-coder handoff: state and snapshot reads must raise, not return "no data"

*Staged 2026-10-14 from the code-modernization assessment
(`Lotext/modernization/analysis/arbor-hills-monitor/ASSESSMENT.md`, Technical Debt #1).
Read `docs/overnight-coder.md` first. This changes LIVE paths (the daily pipeline and
eight enabled watchers), so per Step 3 it needs real-specimen verification and ends at a
**DRAFT PR for Trisha's review, no autonomous merge.** Recommended tier: **Sonnet**.*

## Invocation

Branch name suggestion: `state-reads-raise`.

## The defect (verify every line reference in Step 1; the code may have moved)

- `sheet_writer._tab_rows` (`sheet_writer.py` ~1224-1235) catches EVERY exception and
  returns `[]`. Its docstring says that covers "the tab is absent ... or a transient read
  error". The second case is the bug.
- `read_meta` (~1093) and `read_state` (~1112) are built on it. `watcher.run` reads state
  at ~190 and calls `sw.write_meta(...)` unconditionally at ~461. So one transient Sheets
  read failure at the start of a run resets `pending_digest`, `pending_urgent_recap` and
  `wds_seen` to defaults, and the end-of-run write overwrites the real stored values.
  Queued digest items are lost silently.
- `backfill.py` (~184): a failed `_state` read makes every doc look unprocessed, so the
  batch re-classifies up to 50 docs and writes duplicate rows.
- The same lenient reader backs the snapshot reads for eight enabled watchers:
  `last_submissions_snapshots`, ROP, MMD, PFAS-PWS, RIDE, PFAS page watch, meeting watch,
  and the GFL info-site first-run check. A failed read there looks like "first run" and
  silently re-baselines, which permanently erases an un-alerted change.
- The six newer nSITE profile readers (violations / compliance actions / evaluations /
  permits / complaints / public notices) ALREADY raise. They are the model to copy.
- The repo's own ADRs name this harm: ADR 018 lists it as an open cross-stream follow-on;
  ADR 023 describes the "spurious baseline becomes the state" failure.

## What to build

1. A strict reader (`_tab_rows_strict`, or a `strict=` parameter) that distinguishes the
   two cases: a genuinely ABSENT tab (Sheets returns a 400 "Unable to parse range") may
   still return `[]`; every other exception (HttpError 5xx/429, network, timeout,
   exhausted retries) RAISES.
2. Use it for `_state`, `_meta`, and every `last_*_snapshots` / first-run read listed
   above. Keep the lenient reader ONLY for append-only dedup sets where an empty read
   cannot cause a destructive write (audit each remaining caller and say why in the PR).
3. Each caller that now sees a raise must fail the run loudly (non-zero exit, so the
   Actions run goes red) WITHOUT performing its end-of-run write. Check `watcher.run`'s
   ordering: no `write_meta` after a failed `read_state`.
4. Tests: for each converted reader, a test that a transient error raises and that the
   caller does not write state afterward; a test that an absent tab still reads as empty
   on a genuine first run. Existing tests must stay green.

## Out of scope

The generic `ensure_tab` / `append_watch_row` / `last_snapshots` consolidation of
`sheet_writer.py` is `coder:sheet-writer-watcher-consolidation`, which depends on this.
Do not do it here; keep this diff small and reviewable.

## Verification (Step 3)

No new source; real-specimen check = run `watcher.py` and two of the converted watchers
read-only against the live Sheet locally (SA key works read-only, see the memory note on
the local Anthropic key) and confirm reads succeed and return the same rows as before.
Then simulate a failure (patch the service to raise a 503) and show the run exits
non-zero with no write. Paste both in the PR.
