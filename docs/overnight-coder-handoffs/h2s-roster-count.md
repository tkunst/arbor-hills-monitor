# Overnight-coder handoff — derive the H2S silent-station count from a roster, not a hard-coded 6

*Staged 2026-10-16, follow-up to PR #107 (`gfl-air: list silent stations in the
screening table instead of dropping them`, merged). Read `docs/overnight-coder.md`
first. This touches the live perimeter-air SCREENING email text. Recommended
tier: Sonnet.

**Cloud-ready:** Runs in cloud mode: ends at a DRAFT PR. No live credentials. Any
real-specimen / live verification is done locally by Trisha before merge.

**Live-path check:** `gfl_air.enabled: true` in `config.yml` today. Treat this
as a live-path change per `docs/overnight-coder.md` Step 3 regardless of
anything below: no mocked-green autonomous merge, real-specimen verification
(or at minimum a careful dry-run differential against the real station list)
is required before this is mergeable, and this build does not flip any flag.*

## Background

PR #107 made the SCREENING email list every registry station, including one
that stopped reporting ("no data since ..."), instead of silently dropping it.
It introduced `format_screening_email(..., n_stations_expected: int = 6)` in
`gfl_air_emails.py` — **verify the current line number; it was `:447` when this
handoff was written** — and nothing in this repo passes that parameter, so
every call site silently uses the default `6`. The PR's own "Tracked follow-up"
note says exactly what's wrong and points at the fix:

> The "not listed" note compares the row count to a hard-coded 6. An extra
> `MS-` feature in layer 0 (a new MS-7, or a naming variant such as "MS-01"
> beside "MS-1") could hide a missing expected station, or show one physical
> station twice (silent under one name, reporting under the other). Names
> match today. The fix would be an explicit expected-station list in config.

So the actual ask (confirm this is still accurate against current code before
building): adding or removing a monitored station should be a one-line
`config.yml` change, not a code change, and the count used in "stations
reporting N/6" / "N station(s) not listed" must come from that config, never
a magic number baked into the function signature.

**What's already in this repo relevant to the fix:**

- `gfl_air_client.fetch_station_coords()` is the **live registry fetch** — it
  returns every physical `MS-*` station the ArcGIS layer currently knows about
  (even one with no geometry), each with a `last_read_iso`. This is "what
  physically exists right now," fetched fresh every run, best-effort (raises
  `GflAirFetchError` on failure — the caller already has a `registry_available`
  fallback path for that case; keep it).
- There is **no static, human-maintained roster** of "which stations SHOULD
  exist" anywhere in `config.yml` today (verify this is still true — grep
  `config.yml`'s `gfl_air:` block for any station-name list before assuming
  it needs to be added from scratch).

## What to build

1. **Add an explicit, human-maintained station roster to `config.yml`** under
   the `gfl_air:` block (name it clearly, e.g. `stations:` or
   `expected_stations:` — pick whichever reads naturally alongside the
   existing keys there) listing the current six station names (`MS-1`
   through `MS-6`, or whatever the live names actually are — confirm against
   `fetch_station_coords()`'s real output or the existing tests' fixtures
   before hardcoding names into config). This roster is the single source of
   truth for "how many stations are supposed to exist" — adding or removing a
   station is a one-line edit here, not a code change.
2. **Thread `n_stations_expected = len(roster)` from config through to
   `format_screening_email()`** at its real call site(s) in
   `gfl_air_watcher.py` (grep for `format_screening_email(` to find them —
   there may be more than one call site, e.g. live vs. historical/backfill
   paths). Remove the `= 6` default, or keep it only as a last-resort
   fallback if the config key is ever missing (log loudly if so — never fail
   silently back to a magic number without saying so in the run log).
3. **Reconcile roster vs. live registry, don't just trust registry count
   blindly.** The PR's own follow-up note flags the real risk: a live
   registry station name that ISN'T in the roster (a genuinely new `MS-7`, or
   a renamed/duplicate variant like `MS-01` beside `MS-1`) should be
   **visible, not silently absorbed** — either into the count or out of it.
   Add a check: if `fetch_station_coords()` returns a station name not in the
   roster, log it loudly (and consider whether it should also appear in the
   email itself, e.g. an "unexpected station seen: MS-7" line) rather than
   either (a) silently counting it as one of the expected N, which could mask
   a genuinely-missing expected station, or (b) silently dropping it, which
   would hide a real new monitor coming online. Use your judgment on the
   exact wording/placement; the acceptance criterion is that a roster/registry
   mismatch is never invisible.

## Tests

- A roster-driven `n_stations_expected` test: shrink the roster to 5 in a test
  config and confirm "reporting N/5" / the not-listed count both reflect 5,
  not a hardcoded 6.
- A reconciliation test: registry returns a station name absent from the
  roster; assert it's surfaced (logged at minimum) rather than silently
  folded into or out of the count.
- A reconciliation test: roster lists a station the registry fetch doesn't
  return at all this run (distinct from "silent" — genuinely absent from the
  registry response, e.g. the station was physically removed from the
  service); confirm this doesn't crash and is handled sensibly (treat as
  silent/no-data, matching PR #107's own existing "silent" row handling, or
  flag explicitly — your call, document it).
- Existing PR #107 tests (silent-station listing, the five/six specimen in
  its PR description) must stay green; update any that hardcode `6` as an
  assumption rather than deriving it from the test's own roster fixture.
- `pytest -q` fully green.

## Out of scope

- Changing anything about the SCREENING email's episode-detection logic, the
  exceedance tier, or `station_snapshot`/the `GFL Air` Sheet tab/cursor — all
  untouched by PR #107 and untouched here.
- Confirming the real current station count/names against the live ArcGIS
  service — this cloud session has no network access to it. Use whatever the
  existing tests' fixtures already establish as the known station set, and
  say explicitly in the PR that the real roster value needs a human
  double-check against the live service before merge.

## Acceptance

- `pytest -q` fully green, including the new roster/reconciliation tests.
- The PR states: the roster lives in `config.yml`, the magic `6` is gone from
  `gfl_air_emails.py`'s signature (or demoted to an explicit, logged
  fallback), and a roster/registry mismatch is now visible in the run log
  rather than silent.
- CLAUDE.md: if `gfl_air_emails.py` or `gfl_air_client.py` already has an
  Architecture entry, extend it with the roster/registry distinction; if not,
  note that as a separate, pre-existing documentation gap rather than
  silently fixing it as part of this PR's scope creep.
