# Session 2026-07-15 — GFL air liveness check (Stream E hardening)

Overnight-coder loop against `docs/overnight-coder-handoffs/gfl-air-liveness.md`,
closing ADR 014's one residual — the **OBJECTID-reset silent stall**. The wrinkle
this session: the handoff's core premise changed under it, turning a
"merge-on-mocked-green" hardening of a *disabled* stream into a **live-path change**.

## What it is

Stream E polls GFL's perimeter-air ArcGIS feed with an incremental cursor and queries
`OBJECTID > cursor` (verified monotonic-with-time). If Barr ever rebuilds the
FeatureServer and the new OBJECTIDs reset **below** the stored cursor (~17.6M), that
query returns nothing **forever** — a permanent silent zero indistinguishable from a
healthy "no new readings." The over-cap guard only catches the opposite direction
(OIDs bumped higher). For a stream that is quiet-by-design until an exceedance, that is
the one way a real reading could go unseen — exactly what the project's fail-safe ethos
exists to prevent, and the residual ADR 014 flagged to fix before enabling.

## The premise change (why this became a live-path change)

The handoff (staged 2026-07-14) assumed `gfl_air.enabled: false` — "no-op while
disabled, merges on mocked-green, no live spike." Between session start and the first
edit, a concurrent session committed **`d3bac7b` — `gfl_air.enabled: true`** (Trisha's
directed enable). Session-Ridgewood had left a coordination heads-up naming exactly
this consequence: the gate is now `true`, so the liveness check is a **live-path
change**, and per overnight-coder Step 3 it must be **verified against a real specimen
before autonomous merge — not merged on mocked-green alone**. Stream E was already
running without the guard, which *raised* the item's priority (shipping it reduces live
risk) rather than being a reason to defer.

## Shipped

- **PR #16 → `main` `d8a56d7`** (fast-forward, linear on `a68a282`). Config
  `gfl_air.max_stale_days` (default 3); a pure `liveness_decision` + an isolated
  `_check_liveness` in `gfl_air_watcher.py`; `gfl_air_latest_as_of` + column-N
  stale-marker helpers in `sheet_writer.py`; 15 new hermetic tests (335 total green);
  ADR 014's OBJECTID-reset residual flipped to **mitigated**; README/CLAUDE notes.
- **No `enabled` flip.** The change never touches the gate — Stream E stays enabled as
  Trisha left it, now guarded.

## Design

- **Trigger.** Only on the zero-new-readings branch (`if not readings`), after the poll
  has committed to writing nothing and not advancing the cursor. On that poll, read the
  newest `As-Of (UTC)` from the GFL Air tab; if it is older than `max_stale_days`, send
  one "feed appears stale" alert — its own message + `[GFL air liveness]` subject tag,
  never the `[URGENT]`/exceedance path.
- **Once per stale episode.** A stale-warned marker records the As-Of last alerted on,
  stored in **column N** of the GFL Air tab — outside the A:L station write span, so the
  REPLACE snapshot write never clobbers it and no `clear()` wipes it. It **self-resets
  without an explicit clear** because As-Of is monotonic: a recovered-then-restalled
  feed carries a newer As-Of that differs from the marker, re-arming the alert.
- **Isolation.** The whole check is double-wrapped in try/except and best-effort: any
  tab-read or send failure is caught-and-logged and never propagates, so a liveness bug
  cannot break the poll or touch the measurements system-of-record. The marker is
  written only after a successful send, so a failed send retries until it lands exactly
  once.
- **Conscious scope calls** (all flagged, not silently decided): recipients go to the
  full `alert_recipients` list (Trisha-only scoping is a trivial follow-on, per Ridge
  Wood's `review_recipients`); a persistent *fetch error* is a separate silent-quiet
  vector left out by design (it returns early at the `GflAirFetchError` handler and logs
  each run); an unparseable As-Of skips-and-logs rather than firing a misleading alert.

## The Step-3 real-specimen gate

Reframed by the advisor: the live risk of this change is not a parse bug (the As-Of is
our own round-tripped string) but a **false stale alert** on a freshly-enabled stream,
which would burn the credibility the project treats as load-bearing. So the spike asked
whether the *healthy* feed ever legitimately goes quiet for `max_stale_days`. Driving
the real `gfl_air_client` against the live FeatureServer:

- Over 2022-05…2026-07 (**36,446** distinct hourly timestamps), the healthy feed's
  **largest gap is 17h**; gaps >24h: **0**, >48h: **0**, >72h: **0**. So the 72h default
  has a **~4.2× margin** over the largest gap ever observed and cannot false-fire on
  normal cadence.
- `OBJECTID > current_max` returns `[]` — the stall signal is exactly the branch the
  check hooks.
- `reading_iso` round-trips against a real reading (`'2026-07-15T13:00Z'` parses back).

The spike (throwaway, not committed) is the merge gate; a mocked-green build alone would
not have authorized the merge.

## Process

- Coordination: re-read the shared `.claude/COORDINATION.md`, absorbed Session-Ridgewood's
  live-path heads-up, claimed surfaces before writing, committed by explicit path (never
  `-A`), and on close updated the top banner (not just a log entry — the file's own
  documented lesson).
- Gates: `/review` no findings; `/security-review` plus an independent adversarial
  subagent → zero medium/high; push-to-main CI all green (tests/markdownlint/links/
  secrets); privacy pre-push gate passed. Merged fast-forward after re-verifying `main`
  had not moved under me.
- A test-harness note worth remembering: the `FakeSheets` mock was row-based (ignored
  column offsets), so it had to be made **column-faithful** (overlay-preserving `update`,
  column-sliced `get`) or the column-N marker would have tested green while diverging
  from the real Sheets API — the exact mock/reality gap ADR 011 warns about. The change
  is a no-op for existing column-A ranges.

## Final state

- `main` @ `d8a56d7`, clean, in sync with origin; 335 tests green.
- Stream E: `enabled: true` (untouched), now guarded by the liveness check.
- Overnight-coder queue: `coder:gfl-air-liveness` archived; sibling
  `coder:gfl-air-24h-average` (same watcher, not in flight) flagged to rebase onto
  `d8a56d7`. Committed locally in the Cowork repo, not pushed (Lotext convention).

## Non-blocking follow-ups (flagged in PR #16, none require action)

- Once-per-episode means a *permanent* stall produces exactly one email; if missed there
  is no re-alert. This is the handoff's spec ("don't re-alert every quiet day"), not a
  defect — a gentle re-alert cadence is a possible refinement.
- Scope the stale alert to Trisha-only (operational-health signal) via the Ridge Wood
  `review_recipients` precedent, if the full-list default proves noisy.
- Extend the same check to the persistent fetch-error path.
- The marker's Sheet round-trip is covered by hermetic tests but not live-verifiable (a
  stall can't be manufactured on a healthy feed); the failure modes are benign in both
  directions. Named as an accepted residual, not silent.
