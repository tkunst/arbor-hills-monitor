# ADR 024 — Stream C `wds_seen` append-only backup + recovery log

Date: 2026-07-26
Status: accepted
Builds on: ADR 009 (WDS Stream C), ADR 006 (`_state`/`_meta` split), ADR 017/018
(append-only snapshot-diff watch tabs). Closes Gap G1-C-1 from the data-lifecycle
architecture (`docs/data-lifecycle-architecture.md`).

## Context

Stream C's diff cursor — which WDS records have been seen, per collection — lives
in the **`_meta.wds_seen`** singleton cell. `write_meta()` overwrites a fixed
row span of the `_meta` tab every run (`sheet_writer.py`), so `wds_seen` is a
**Tier-3 mutable** store: the pipeline rewrites it on every run.

That is strictly worse than the other watch streams. ROP, MMD, nSITE Submissions,
and PFAS each keep their observation history in an **append-only** tab, so their
state is recoverable from the tab itself. `wds_seen` is not: if the `_meta` tab is
cleared or corrupted, the entire WDS diff history is lost, and on the next run
**every WDS record re-fires as "new."** The `max_new_wds_alerts_per_run`
anti-stampede guard would likely catch that and silently re-baseline — but leaning
on the safety net to paper over a lost cursor is not durability.

## Decision

Add an **append-only backup + recovery log** for the cursor, without changing the
proven live diff path.

1. **The live cursor is unchanged.** `check_wds` still reads and updates
   `_meta.wds_seen` exactly as before, and `write_meta` still persists it. Zero
   change to the live alerting path → zero risk of breaking WDS alerting. This is
   the deliberate safety choice: the diff logic that decides whether an
   enforcement record fires an urgent email is not touched.

2. **New append-only tab `_wds_seen`** (created by `ensure_wds_tabs`;
   underscore-prefixed = internal, like `_meta`/`_state`). Columns: Checked At,
   Collection, Last Count, Snapshot Hash, Records JSON. `append_wds_seen_log()`
   writes **one row per collection whose snapshot changed** since its last logged
   row (append-on-change, matching the ROP/MMD idiom; WDS changes rarely, so the
   log stays lean). Rows are never overwritten.

3. **Automatic recovery.** At the start of the WDS step, if `_meta.wds_seen` is
   empty but the `_wds_seen` log has data, the watcher restores the cursor from
   `read_wds_seen_log_latest()` (the newest row per collection). A `_meta` wipe is
   therefore no longer catastrophic: the cursor is rebuilt and the run diffs
   normally instead of re-firing everything.

4. **Cold-start / migration is implicit and safe.** On the first run after this
   ships, `_meta.wds_seen` is populated (unchanged) and the log is empty, so
   recovery is a no-op and the post-run append **seeds** the log from the current
   cursor. No transition run ever reads an empty log in place of a live cursor,
   so nothing re-fires. (A naïve migration that stopped reading `_meta` and read
   the still-empty log would have caused exactly the failure this fixes — avoided
   by keeping `_meta` primary and treating the log as backup + recovery only.)

5. **Best-effort backup.** The append is wrapped by the caller; a failure writing
   the backup log logs a warning and never breaks the run, because the live
   cursor is `_meta.wds_seen`.

## Consequences

- Gap G1-C-1 closed: the cursor is now recoverable from an append-only tab, and a
  `_meta` corruption self-heals instead of blasting the recipient list.
- One extra Sheet read per WDS-enabled run (to compare hashes / recover) and, on
  a change, a small append. Negligible; WDS is polled daily and changes rarely.
- The `_wds_seen` tab is visible on the (public) Sheet, like the existing `_meta`
  and `_state` internal tabs. Its content is id/hash pairs only — the same data
  `_meta.wds_seen` already exposes — so this adds no new disclosure.
- `_meta.wds_seen` remains the source of truth in normal operation; the log is
  redundant by design (redundancy is the durability goal).

## Alternatives considered

- **Make the log the sole source of truth (drop `_meta.wds_seen`).** Cleaner
  conceptually and exact parity with the other watch tabs, but it rewrites the
  live read/write path of an enabled notification stream and carries the
  cold-start re-fire landmine. Rejected for this PR in favor of the
  lower-risk backup+recovery design; can be revisited once the log has proven
  itself in production.
- **Append every run unconditionally (one row per collection per run).** Simpler,
  but grows the tab ~5 rows/day forever even when nothing changes. Append-on-change
  gives a leaner, more meaningful audit trail (each row is a real state
  transition) and still supports latest-per-collection recovery.
