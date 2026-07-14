# Session 2026-07-14 — GFL perimeter air (Stream E) + the overnight-coder queue system

Two pieces of work this session: (1) the overnight-coder build of Stream E (GFL
perimeter air), merged; (2) a Trisha-requested overnight-coder *queue* system
(mirroring the Lotext overnight-worker queue), staged in the Cowork workspace.

## 1. Stream E — GFL perimeter air (merged)

Ran the overnight-coder loop (`docs/overnight-coder.md`) against
`docs/overnight-coder-handoffs/gfl-air-arcgis.md`. Kicked off ~1am (timed so the
heavy build landed near/after the weekly usage reset).

**Spike verdict: path A.** The dashboard
(`barrgisonline.maps.arcgis.com/.../ac08a03f...`) is backed by a public, keyless
**Barr ArcGIS FeatureServer** (`enterprisegis.barr.com/.../GFL_Arbor_Hills_Public_Web_Data/FeatureServer`).
Table 4 "Monitoring Data" carries **hourly H2S (ppb) + CH4 (ppm)** at six perimeter
stations (MS-1..MS-6) since 2022-05 — ~214k rows. First source of real fenceline
readings, not documents.

**Shipped (PR #12 → `main` `4ed3e7f`, ff, ADR 014, `enabled:false`):**

- `gfl_air_client.py` — fetch + pure ADR-004 mapping. Incremental **OBJECTID
  cursor** (`OBJECTID > cursor`): monotonic-on-insert, so skip-proof and dedup-free
  (a raw-epoch `Date >` 400s; a timestamp watermark risks tz-skew). Never touches
  `egle_doc_parser`.
- `gfl_air_watcher.py` — standalone pfas-shaped watcher (own `gfl-air.yml`, own
  concurrency group). First-run baseline (alert none), over-cap re-baseline, its own
  exceedance classifier (not the temperature `is_urgent`), one same-day email/poll.
- State in the **`GFL Air` tab, not `_meta`** (a separate workflow must not clobber
  the daily watcher's `_meta`); the tab's OBJECTID column IS the cursor. Readings
  ride the Measurements tab (`basis=measured`, `metric=hydrogen_sulfide/methane`).
- `measurements_mode: digest` default (the hourly feed is ~288 rows/day at `all`).
- Sentinels (999/99999) excluded from the measured series, surfaced as anomalies.

**Advisor-caught issues, all resolved:**

- Read the real template code before writing (not the handoff's description of it).
- The merge-blocking **first-run bound**: `since=None → where=1=1` would pull 214k
  rows; fixed with a baseline that seeds the cursor to the current max.
- **In-place mutation** check — re-queried the same OBJECTIDs and confirmed the
  numeric H2S/CH4 are stable at insert (only Text/Temp finalize, C→F), so the
  cursor can't miss a correction. Recorded in ADR 014.
- **OBJECTID gap** (~4.1M..17.6M for 214k rows) — verified the lowest OBJECTIDs
  carry the oldest (2022) dates, so it's monotonic-with-time (a high-starting shared
  sequence + snapshot churn, not a table reload). Folded into ADR 014 (`dfeb438`)
  with two residuals: a service-reload OBJECTID-reset silent-stall (→ add a liveness
  check before enabling) and the over-cap alert-suppression note.

**Gates:** 264 tests green; `/review` (one crash-safety finding — the summary
tab's clear+update window, fixed with a single padded `update`); `/security-review`
zero med/high; verified live end-to-end against the real feed. Push-to-main CI all
green.

**Left for Trisha (in PR #12):** confirm the thresholds (H2S 72 ppb = Michigan
ITSL; CH4 12,500 ppm = 25% LEL) then set `enabled:true`; add the liveness check
first (now queued as coder #2).

## 2. Backlog + the overnight-coder queue system (Cowork)

All in `/Volumes/Samsung-Pro-2TB/Cowork-claude/`:

- **`documents/arbor-hills/arbor-hills-multiple-data-sources.md`** — marked item 6
  ✅ (Stream E shipped) and rewrote item 8's "How to Get It" to point at a new
  recon spec. (Item 8 recon: it's the *Barr/EPA-agreement* H2S monitor at Ridge
  Wood Elementary — **monthly text-layer PDF reports**, a document-archive source,
  distinct from Stream E. Its published action levels — 72 ppb 24-hr / 750 ppb
  15-min — independently confirmed Stream E's H2S threshold.)
- **`documents/arbor-hills/source-docs/item-08-ridge-wood-h2s-how-to-get.md`** — the
  full item-8 acquisition spec (the recon).
- **`documents/overnight-coder-queue.md`** (new) — active coder queue: how Trisha
  starts (cd repo → `claude --dangerously-skip-permissions` → `!pwd` anchor →
  paste goal), a `Dependency:` (null | `worker #NN`) + `Handoff:` + cut/paste Goal
  box per item, keyed by **stable slugs** (`coder:ridgewood-h2s`, …), and 7 staged
  items (`coder:ridgewood-h2s` + `coder:gfl-air-liveness` are `null`/ready; the rest
  gated on worker items 66–69 / a new backup design-spike).
- **`documents/overnight-coder-archive.md`** (new) — completed builds (GFL #12,
  WOI #9) + pre-queue history.
- **`documents/overnight-queue.md`** — rewired the worker convention to
  **`[coder-prep -> coder:<slug>]`** naming the specific coder item it gates
  (replacing "next night's coder"/"tomorrow"): 66→`coder:metric-taxonomy`,
  67→`coder:location-type`, 68→`coder:well-id-normalization`, 69→`coder:ride-part201`;
  marked worker #74 (GFL recon fallback) MOOT.
- **`docs/overnight-coder.md`** (repo) — added **Step 9** (automatic queue
  maintenance: on merge, archive the item by slug + release the consumed worker pin,
  commit the Cowork files locally, never push) and a **STOPPED-state** convention (a
  stopped item stays in the active queue, annotated, pointing at its draft PR).
- **`TASKS.md`** — two not-urgent follow-ups: optimize the overnight worker/coder
  timing (the coder runs before the worker, so worker-gated builds carry a ~2-night
  latency) and automate the nightly Apple Note.
- This repo: **`docs/overnight-coder-handoffs/ridgewood-h2s.md`** +
  **`gfl-air-liveness.md`** (new) — the `coder:ridgewood-h2s` and
  `coder:gfl-air-liveness` handoffs, both staged so they're runnable now.
- Bonus: demonstrated pushing a Goal to an Apple Note (`osascript`) for
  `coder:ridgewood-h2s`.

**Item 9 (backup / 3-2-1-1-0) critique** was delivered to Trisha directly (not
written to the doc): it's really 3 tasks (worker write-up + worker design-spike +
a *decomposed* set of coder builds); the Seagate offline leg can't run from CI; the
"0" (verified restore) is the unstated crux; and the highest-value first slice is a
provenance-stamped archive of the self-reported feeds (queued as `coder:selfreport-provenance`).

## Follow-ups

- Enable Stream E after threshold confirm + the `coder:gfl-air-liveness` check.
- Run `coder:ridgewood-h2s` or `coder:gfl-air-liveness` — both ready now.
- Stage worker items' outputs to unblock `coder:ride-part201` /
  `coder:metric-taxonomy` / `coder:location-type` / `coder:well-id-normalization`;
  stage a backup design-spike for `coder:selfreport-provenance`.
- Not urgent (in TASKS.md): optimize overnight worker/coder timing; automate the
  nightly Apple Note.
- Non-blocking: re-inject `docs/topology/TOPOLOGY.html` via `/modernize-map`.

## Session close (2026-07-14)

Everything durable and merged. Nothing in flight.

- **`arbor-hills-monitor` `main`:** Stream E (PR #12 `4ed3e7f`) + ADR-014 accuracy
  pass (`dfeb438`) + Ridge Wood/session docs (`7d0248d`) + overnight-coder Step 9 /
  STOPPED / gfl-air-liveness handoff (`b897bea`). All pushed; push-to-main CI green.
- **Cowork workspace** (committed locally, not pushed — Lotext convention,
  `b01658e`): the two overnight-coder queue docs, the worker-queue slug rewire +
  #74-moot, the item-8 recon + backlog updates (items 6/8), and the two not-urgent
  TASKS follow-ups.
- **Two ready coder items** to run any night: `coder:ridgewood-h2s`,
  `coder:gfl-air-liveness`. **Two human steps** before Stream E goes live: confirm
  thresholds, then `enabled:true` (after the liveness build).

## Addendum — threshold equivalence (2026-07-14)

Per Trisha: where the two air monitors' thresholds are equivalent, use them.

- **H2S 72 ppb is now SETTLED** — it is simultaneously the Michigan EGLE ITSL
  (24-hr) *and* the Ridge Wood monitor's own published 24-hr action level (item 8),
  so two independent official sources agree. Updated `config.yml` + ADR 014
  decision 4 + the activation notes from "Trisha confirms" to "settled" for H2S
  (value unchanged at 72; noted the 24-hr-avg-vs-hourly nuance and the operator's
  additional 750 ppb 15-min *acute* level as a small follow-on tier).
- **CH4 12,500 ppm stays PENDING** — no second monitor measures CH4, so it isn't
  cross-validated.
- **New overnight-worker item #75** (Cowork queue) researches publicly-available
  recommended H2S/CH4 thresholds (values + averaging periods + sources) to firm up
  CH4 before enabling. `[coder: none]`; a multi-tier outcome could later stage a
  `coder:gfl-air-thresholds` build. Item 6 backlog cell updated to match.
