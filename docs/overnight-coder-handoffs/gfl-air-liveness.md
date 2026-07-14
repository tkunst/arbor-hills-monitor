# Overnight-coder handoff — GFL air liveness check (Stream E hardening)

*Staged 2026-07-14. A small hardening of the already-merged GFL perimeter-air
stream (ADR 014, `enabled:false`). Read `docs/overnight-coder.md` first. This is
NOT a new source — it edits an existing (disabled) stream, so there is no spike;
it stays `enabled:false` and merges on mocked-green.*

## Invocation

Branch name suggestion: `gfl-air-liveness`. Do this BEFORE the stream is enabled.

## Goal

Close the one residual ADR 014 flagged as "fix before enabling": the **OBJECTID
reset silent stall.** The GFL air poller advances an OBJECTID cursor and queries
`OBJECTID > cursor`. That cursor is verified monotonic-with-time today, but if Barr
ever rebuilds the FeatureServer and the new OBJECTIDs reset **below** the stored
cursor (~17.6M), `OBJECTID > cursor` returns nothing **forever** — a permanent
silent zero that looks identical to "no new readings." The over-cap guard only
catches the opposite direction. For a stream that is quiet-by-design until an
exceedance, that silent stall is exactly the miss the project's fail-safe ethos
exists to prevent.

**Add a config-driven liveness check** so the stream ALERTS when it has seen no new
readings for too long, instead of going quiet.

## Approach (pinned)

1. **Config:** add `gfl_air.max_stale_days` (default e.g. `3`) to the `gfl_air`
   block in `config.yml`, with a comment.
2. **Watcher:** in `gfl_air_watcher`, on a poll that finds **zero** new readings,
   compare the newest reading's timestamp (available from the `GFL Air` snapshot
   tab / cursor row — the most recent `As-Of (UTC)`) against now. If it is older
   than `max_stale_days`, send **one** same-day alert via `ea.send_email` — its own
   "GFL air feed appears stale (no new readings in N days)" message, clearly NOT an
   exceedance. Fire it at most once per stale episode (don't re-alert every quiet
   day — e.g. gate on a "already warned for this stale window" marker, or only warn
   when crossing the threshold).
3. **Gate:** the whole thing is a no-op while `gfl_air.enabled` is false (it rides
   the same `_should_run` gate).
4. **Tests:** hermetic — a poll with zero new readings and a stale newest-reading
   fires the alert; a fresh newest-reading does not; disabled = no-op; the
   once-per-episode gate holds.

## Definition of done

Green `pytest -q`; the liveness alert works behind the `enabled:false` gate; update
ADR 014's "OBJECTID-reset silent stall" residual from "add a liveness check before
enabling" to **mitigated** (describe the shipped check). Same-PR ADR edit +
README/CLAUDE.md note if warranted. Stays disabled — enabling the stream is still
Trisha's step; this just makes it safe to enable.
