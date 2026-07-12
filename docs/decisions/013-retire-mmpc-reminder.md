# ADR 013 — Retire the MMPC minutes reminder

*Status: accepted — 2026-07-12. Supersedes the reminder half of the MMPC
handling; see ADR 010 (Mirror D), whose "Alternatives" left this call to Trisha.*

## Context

The monitor had **two** MMPC (Washtenaw County Materials Management Planning
Committee) mechanisms running in parallel:

- **The reminder** (`mmpc_watcher.py`, in `watcher.py`, built before ADR 010).
  During each meeting's poll window it did a plain HTTP GET on the CivicClerk
  category page and, if the response looked "updated" (HTTP 200 + >500 bytes),
  emailed a "minutes likely posted, go check" link. Trisha then downloaded and
  uploaded each PDF into the `MMPC-meeting-minutes/` Drive folder **by hand**.
- **Mirror D** (`mmpc_client.py` + `mmpc_archiver.py`, ADR 010, enabled
  2026-07-10). It calls CivicClerk's public JSON API directly, enumerates every
  published Agenda/Minutes/Other PDF, and downloads + uploads each into the same
  Drive folder automatically — the manual step the reminder existed to prompt.

ADR 010 deliberately shipped Mirror D as additive and left the reminder
untouched ("retiring it is Trisha's call — see Alternatives"), with residual
risk #4 naming the parallel-run overlap as accepted-but-temporary. Mirror D has
now run cleanly on its own schedule, so the overlap can end.

## Decision

**Retire the reminder.** Mirror D is the sole MMPC path. Once Mirror D
downloads the minutes automatically, a "go check the page" email has no action
left to prompt — it is pure noise. Removed in this change:

- `mmpc_watcher.py` — the whole module (meeting-date math + minutes poller).
- The MMPC polling block and `import mmpc_watcher` in `watcher.py`.
- `tests/test_mmpc.py` — its tests (all reminder-only).
- The `mmpc:` block in `config.yml` (`minutes_url`, `meeting_dates`, the
  poll-window params, the 2nd-Wednesday rule). The `mmpc_archive:` block that
  drives Mirror D is untouched.
- The `mmpc_minutes_found` `_meta` state key (the reminder's per-meeting
  "already emailed" set). See the state-migration note below.

## State migration (the one non-trivial removal)

`mmpc_minutes_found` was a `_meta` singleton, and `sheet_writer.write_meta`
persists `_meta` **positionally** (one row per key in `_META_DEFAULTS` order,
written as a block starting at `A2`). Dropping a non-last key therefore shifts
every later key's row up by one, leaving a now-orphaned trailing row that
`read_meta` would pick up as a **stale** value for a different key (`last_run`).

Fix: `write_meta` now overwrites a fixed `_META_CELL_ROWS`-row span (live keys
first, then blank rows) instead of only the live keys, so an orphan from a
removed key is blanked on the next write. `read_meta` skips blank rows. A live
Sheet written by the old code self-heals on its first run under the new code —
proven by `tests/test_state.py::test_removed_meta_key_clears_orphan_without_losing_live_state`,
which seeds the pre-retirement 5-row layout and asserts every live key survives
and `last_run` reads fresh, not orphaned.

## Consequences

- One fewer email class; no behavioural change for anyone but the removal of a
  redundant notification. The Conservancy still attends every meeting, and
  Mirror D archives every published document regardless.
- The reminder's residual risk ("MMPC minutes URL is best-effort — poll a
  hard-coded URL") is gone. It is replaced by Mirror D's own residual risk
  (CivicClerk's undocumented JSON API could change; the fetch fails loudly
  rather than silently — ADR 010, residual risk #3).
- The topology map (`docs/topology/`) drops the `mmpc_watcher` node. That
  snapshot is regenerated but already predates Mirror D and PFAS, so a full
  refresh via its documented procedure is still owed separately.

## Alternatives considered

- **Keep the reminder as a belt-and-suspenders backup.** Rejected: Mirror D and
  the reminder read the same CivicClerk source, so the reminder adds no
  independent coverage — if the API breaks, Mirror D fails loudly anyway. A
  duplicate email is noise, not redundancy.
- **Keep `mmpc_minutes_found` as a vestigial `_meta` key** to avoid touching the
  state-write path. Rejected in favour of the `write_meta` fix, which is small,
  fully tested, and hardens the persistence layer against any future key
  removal — a real improvement, not just this cleanup.
