# Overnight-coder handoff — Stream E CH4 two-tier alerting (40 ppm watch tier)

*Staged 2026-07-18 on Trisha's explicit ruling (she chose the two-tier CH4 scheme from
`DRAFT-arbor-hills-air-thresholds-research-2026-07-16.md`, Lotext). Read
`docs/overnight-coder.md` first. NOT a new source — it refines the already-merged,
already-ENABLED GFL perimeter-air stream (Stream E, ADR 014). **Stream E is LIVE
(`gfl_air.enabled: true`), so this is a LIVE-PATH change: per Step 3, verify against
the real FeatureServer before merging — do NOT merge on mocked-green alone.**
Touches `gfl_air_watcher` (and possibly `gfl_air_client`) — same files as the merged
liveness + 24h-average work; branch off current `main`.*

## Invocation

Branch name suggestion: `gfl-air-thresholds`.

## Goal

The two-tier CH4 scheme is half-built. The **500 ppm alert tier is live**
(`thresholds.ch4_ppm: 500`, set 2026-07-17 — the NESHAP SEM corrective-action
level, 40 CFR 63.1958(d) / Michigan Part 115). The **40 ppm early-warning tier**
(`watch_thresholds.ch4_ppm: 40` — GFL's own perimeter alarm level, ~0.08% LEL)
exists today only as a *display* reference: it appears on the snapshot tab and in
the alert email's "Early-warning WATCH levels" line, but crossing it sends
nothing.

**Make the 40 ppm CH4 watch tier actually notify** — a lower-urgency, clearly
labeled WATCH email when a station's readings cross ≥ 40 ppm — without flooding
and without touching the 500 ppm exceedance path.

## Scope (tight)

- **CH4 only.** No H2S watch tier — the H2S odor-nuisance level (~5–8 ppb) fired
  ~2,874× in 4 yrs and would flood (see the config comment). The H2S **750 ppb
  15-minute acute tier** also stays OUT — Stream E reads hourly and cannot compute
  a 15-min average; that remains a separate follow-on. Trisha's 2026-07-18 ruling
  covered the CH4 pair (40 watch / 500 alert) only.
- The 500 ppm CH4 exceedance alert and the H2S 24-hr-average alert are
  **unchanged**. Measurements-tab behavior is unchanged.
- No `enabled` flip — Stream E stays as Trisha set it; this changes alert
  quantity/channels only (same posture as PR #19).

## Approach (pinned)

1. **Config.** Add `gfl_air.watch_alert_recipients` (list; ship it set to
   `arbor-hills@trishakunst.com` only — review-tier alerts are Trisha-scoped, the
   PR #15 precedent). Empty list = watch tier is display-only (today's behavior,
   the rollback lever). Comment the block; update the long `thresholds:` comment
   so it no longer says the watch tier "needs a classifier tier" — it will have one.

2. **Once-per-episode, per station.** A WATCH email sends when a station crosses
   into ≥ 40 ppm and NOT again until that station has dropped back below 40 (the
   episode resets) — the same marker idiom as the liveness check's stale-episode
   marker in the GFL Air tab (PR #16). An episode that later reaches ≥ 500 ppm
   still fires the normal EXCEEDANCE alert independently (the tiers do not
   suppress each other).

3. **Distinct subject + body.** Subject clearly says WATCH (lower urgency), body
   states the tier basis in one line (GFL's own perimeter alarm level; not a
   health standard; NESHAP action tier is 500 ppm) so a reader can't mistake it
   for an exceedance. Reuse the existing email plumbing.

4. **Real-specimen gate (live-path).** Against the live FeatureServer, count
   historical hourly rows with CH4 ≥ 40 (sentinel 99999 excluded) over the full
   record and per recent month, and eyeball the episode count the marker logic
   would have produced. Normal perimeter readings run ~4–27 ppm, so episodes
   should be rare; if the live data shows ≥ 40 is common (i.e., the WATCH tier
   would flood), STOP and leave a draft PR with the numbers — do not merge a
   flood.

## Tests

Hermetic, in the existing style: threshold classification at 39/40/41 and
499/500/501; episode marker set/reset round-trip; watch email goes only to
`watch_alert_recipients`; empty recipients list = no send, display unchanged;
exceedance path untouched (existing tests keep passing).

## Done-when

- CI green; `/review` + `/security-review` clean (any med/high = hard stop).
- Real-specimen gate numbers recorded in the PR body (rows ≥ 40, episode count).
- ADR 014: add a decision line for the two-tier CH4 ruling (2026-07-18, Trisha).
- `overnight-coder-archive.md` (Lotext) block on merge, per convention.

## Left for Trisha

Nothing gated — no `enabled` flip. Optional later: widen
`watch_alert_recipients` beyond herself once the tier's volume is seen in
practice.
