# Overnight-coder handoff — Stream E H2S 24-hour rolling average (match the action level)

*Staged 2026-07-14. A refinement of the already-merged GFL perimeter-air stream
(Stream E, ADR 014, `enabled:false`). Read `docs/overnight-coder.md` first. NOT a
new source — it edits an existing (disabled) stream, so there is no spike; it stays
`enabled:false` and merges on mocked-green. Touches `gfl_air_client` /
`gfl_air_watcher` (the same files as `coder:gfl-air-liveness` — build either order,
just avoid a merge collision; the two could also be combined into one Stream-E
hardening PR).*

## Invocation

Branch name suggestion: `gfl-air-24h-average`. Do this before the stream is enabled.

## Goal

The H2S action level Stream E alerts on — **72 ppb** — is a **24-HOUR AVERAGE**
level (it is simultaneously the Michigan EGLE H2S ITSL, a 24-hr average, and the
Ridge Wood monitor's own published 24-hr action level; ADR 014 decision 4). But
Stream E currently applies 72 ppb to a **single instantaneous hourly reading**, so
one hot hour alerts even when the 24-hour average is well under the action level —
it over-alerts and doesn't match how the level is defined.

**Make the H2S exceedance alert fire on a rolling 24-hour average per station**
(reading ≥ the action level on the *average*, not a single reading), so Stream E
matches the action level exactly.

## Scope (tight)

- **H2S only.** CH4 stays **instantaneous** — its threshold (25% of the LEL,
  `40 CFR 258.23`) is an *explosivity* boundary limit, not a 24-hr-average health
  level, so averaging it would be wrong. Do not average CH4.
- This does **not** add the 750 ppb 15-minute *acute* tier (Stream E reads hourly,
  so it can't compute a 15-minute average) — that stays a separate follow-on.
- Individual instantaneous readings still land in the Measurements tab unchanged
  (`basis=measured`). This changes only the H2S **alerting** quantity.

## Approach (pinned)

1. **Config.** Add `gfl_air.h2s_avg_window_hours` (default `24`) and
   `gfl_air.h2s_avg_min_readings` (default e.g. `12`) to the `gfl_air` block, with
   comments. When `h2s_avg_window_hours > 0`, the H2S alert compares the trailing
   window average against `thresholds.h2s_ppb`; `= 0` restores the old instantaneous
   behavior (a rollback lever).

2. **Compute the average server-side (one cheap query).** ArcGIS can return the
   per-station average over the window in a single grouped statistics query — no
   client-side loop. Add a `gfl_air_client` function, e.g.
   `fetch_h2s_window_avg(cfg_gfl, hours)`:

   ```text
   GET {FS}/4/query
     ?where=Date > TIMESTAMP 'YYYY-MM-DD HH:MM:SS' AND H2S <> 999   (exclude the sentinel)
     &groupByFieldsForStatistics=LocName
     &outStatistics=[{"statisticType":"avg","onStatisticField":"H2S","outStatisticFieldName":"avgH2S"},
                     {"statisticType":"count","onStatisticField":"H2S","outStatisticFieldName":"n"}]
     &returnGeometry=false&f=json
   ```

   Returns `{LocName, avgH2S, n}` per station over the trailing window. The
   `Date > TIMESTAMP '...'` syntax is spike-verified (raw-epoch `Date >` 400s — see
   ADR 014 decision 1); the cutoff is `now - h2s_avg_window_hours`. BDL readings
   (value 0.0) correctly count toward the average as reported; the `H2S <> 999`
   clause keeps the no-data sentinel out. Cheap (bounded window, ~0.2s). Keep it a
   pure-ish client function; raise `GflAirFetchError` on failure (a failed average
   query must not be read as "0 ppb" — skip the average alert that poll and warn).

3. **Classifier / watcher.** In the poll, after writing the new readings, fetch the
   per-station window averages and fire the H2S exceedance alert when
   `avgH2S >= thresholds.h2s_ppb` **and** `n >= h2s_avg_min_readings` (a sparse
   window — a feed gap leaving only a couple of readings — must NOT let one spike
   masquerade as "the 24-hr average"; if `n` is too low, skip the average alert and
   note it). The alert message must say it is the **24-hour average** (e.g.
   "MS-6 24-hr avg H2S = 78 ppb ≥ 72 ppb action level") so it reads honestly.
   `classify_reading` stays for the per-reading status shown in the summary/anomaly
   view; the AVERAGE drives the H2S *alert*. CH4's instantaneous exceedance path is
   unchanged.

4. **Summary tab (nice-to-have).** Add an `H2S 24-hr avg (ppb)` column to the
   `GFL Air` snapshot so the averaged value is visible beside the latest
   instantaneous reading. Keep it additive (extend `GFL_AIR_SUMMARY_HEADERS` +
   `gfl_air_summary_rows`); if you add a column, update `gfl_air_cursor`'s range/
   index accordingly (OBJECTID stays the cursor).

5. **Gate + tests.** No-op while `gfl_air.enabled` is false. Hermetic tests (mock
   the grouped-stats response): a station whose 24-hr average ≥ 72 alerts even when
   no single reading is ≥ 72; a station with one spike but a sub-72 average does
   NOT alert; the sentinel is excluded from the average; `n < h2s_avg_min_readings`
   suppresses the alert (and is noted); `h2s_avg_window_hours: 0` restores
   instantaneous behavior; CH4 alerting is unchanged.

6. **Cheap real-feed sanity check** (the stream is disabled, but the feed is live):
   run the grouped-avg query against the real FeatureServer once and confirm the
   per-station 24-hr averages are sane (low single-digit ppb, well under 72) vs the
   instantaneous readings — don't commit any captured response (data-guard).

## Docs

Update **ADR 014 decision 4**: change the "24-hr-avg-vs-hourly nuance … a
refinement, not a blocker" note to record that Stream E now computes a rolling
24-hour average per station to match the action level exactly (the instantaneous
path remains available via `h2s_avg_window_hours: 0`). README/CLAUDE.md only if
warranted.

## Adversarial review

- **Sparse window / feed gap:** `h2s_avg_min_readings` guard (above) stops a couple
  of readings from being treated as the day's average.
- **Failed average query:** raises `GflAirFetchError` → skip the average alert that
  poll and warn; never read a failed query as 0 ppb.
- **Don't double-alert:** the H2S alert is the average; the instantaneous value is
  informational (summary/measurements), not a second alert trigger.
- **Averaging window vs poll cadence:** the server-side trailing-window query is
  independent of the OBJECTID poll batch, so a missed/off-schedule poll doesn't skew
  the average (it always looks back a true `now - window`).

## Definition of done

Green `pytest -q`; the H2S alert fires on the rolling 24-hour per-station average
(config-driven window + min-readings guard), CH4 unchanged, disabled = no-op; ADR
014 decision 4 updated; real-feed averages sanity-checked. Stays `enabled:false` —
enabling the stream is still Trisha's step; this just makes the H2S alert match the
action level's own averaging period.
