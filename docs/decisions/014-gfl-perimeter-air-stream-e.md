# ADR 014 — GFL perimeter air monitoring (ArcGIS FeatureServer, Stream E)

*Status: built — 2026-07-14 (`gfl_air.enabled: false` pending Trisha's review of
the alert thresholds; see Activation). Spike + implementation verified against the
live feed end-to-end.*

## Context

Backlog row 6 of `arbor-hills-multiple-data-sources.md`: GFL self-reports
**perimeter air monitoring** for the Arbor Hills Landfill on a public ArcGIS
dashboard
(`barrgisonline.maps.arcgis.com/apps/dashboards/ac08a03f8018403ba6ae1c2243c00e99`).

This is the **first source that yields real fenceline READINGS, not documents** —
and therefore the highest-value source not previously automated. Every other
stream watches filings (nSITE air docs, WDS solid-waste records, CivicClerk MMPC
PDFs) or a prose page (PFAS). This one carries actual **hourly H2S (ppb) and CH4
(ppm) measurements at six perimeter stations** (MS-1..MS-6), plus meteorology. If
those readings exceed action levels at the fence line, that is direct, quantitative
**R3 (odor) / R4 (air quality)** evidence.

Because it is a brand-new poller against a live external system, it ships
`enabled: false` and is **feasibility-gated**: the spike had to prove the feed is
genuinely pollable before any client code was written.

## The feasibility spike (settled: PATH A — queryable numeric readings)

The ArcGIS dashboard is backed by a keyless, public **FeatureServer** (Barr
Engineering's ArcGIS Enterprise), reachable with plain GET — no auth, no secret to
provision:

```text
https://enterprisegis.barr.com/server/rest/services/GFL/GFL_Arbor_Hills_Public_Web_Data/FeatureServer
```

- **Layer 4 "Monitoring Data"** (a table, ~214,595 rows back to 2022-05, hourly) is
  the readings: `LocName` (station MS-1..MS-6), `Date` (epoch-ms), `H2S` (ppb),
  `CH4` (ppm), plus `Speed`/`Direction`/`Temp`/`Relative_Humidity`/`Barometric_Pressure`
  and `*_Text` display strings ("BDL" = below detection limit).
- **Layer 0 "Monitoring Locations"** is the current-per-station snapshot; it also
  carries a stale "10-Meter MET Tower" row with mismapped fields, so we filter to
  `MS-*` and drive everything off layer 4.

So the spike landed on **path A** (the high-value path): build the measurement
poller. Paths B (page-watch fallback) and C (stop with a doc-PR) did not apply.

## Load-bearing decisions

### 1. The incremental cursor is OBJECTID, not a timestamp

The readings table is ~214k rows; a full pull is a non-starter, and the poller
must fetch only what is new since last run. Two candidates:

- A `Date >` timestamp watermark. The server **rejects a raw-epoch** `Date > <ms>`
  (HTTP 400) and requires `Date > TIMESTAMP 'YYYY-MM-DD HH:MM:SS'`, which reintroduces
  timezone-boundary risk (a watermark read in the wrong zone could **skip** readings)
  and needs a de-dup pass for the overlap.
- **OBJECTID** — the server-assigned auto-increment. `OBJECTID > cursor` is
  monotonic with insertion (verified live: the newest readings always carry the
  highest OBJECTIDs), so it is **skip-proof and dedup-free** — no timezone skew, no
  boundary overlap, no de-dup. This is what we use.

The cursor is stored as the `max(OBJECTID)` in the **"GFL Air" tab** (see decision
5), and re-read as an integer each run.

**Verified monotonic-with-time (2026-07-14).** OBJECTID ranges from ~4.14M (min) to
~17.6M (max) for only ~214k surviving rows — an ~60× gap that, left unexplained,
would undermine the cursor. Querying the **lowest** OBJECTIDs settles it: OIDs
4,139,326–330 carry the **oldest** dates (2022-05-05, the start of the record). So
low OID = old, high OID = new — OBJECTID tracks insertion time, and the gap is a
high-starting *shared* server sequence (it begins at ~4.1M, not 0) plus snapshot-
row churn (Layer 0's current-per-station rows are delete+reinserted hourly),
**not** a table reload that renumbers Layer 4. The 2022 rows still hold their
original low OIDs, so the readings table is append-mostly and the `> cursor` walk
never misses or re-sees them.

### 2. In-place mutation does NOT touch the numerics (verified) — so the cursor is safe

An OBJECTID cursor walks past each row once and never revisits it, so it would miss
any post-insert correction to a row's values (the WDS "Rule C" hazard). The spike
found that a freshly-inserted row **is** briefly "preliminary" — its `H2S_Text`/`CH4_Text`
are blank and `Temp` is in Celsius — and is then finalized in place (text backfilled,
`Temp` converted to Fahrenheit). **But the numeric `H2S`/`CH4` — the only alerting
signal — is stable at insert:** re-querying the same OBJECTIDs after finalization
returned byte-identical `H2S`/`CH4` (only `*_Text` and `Temp` changed). So the
cursor cannot miss an H2S/CH4 correction, because there is none.

**Residual (accepted):** if GFL ever begins correcting the numerics post-insert,
the cursor would miss the correction. This is low-likelihood, the stream ships
disabled with Trisha reviewing, and a per-poll reading-count log makes a systemic
change visible. Meteorology (`Temp`, wind) is stored as **context only** and never
alerted on — which also sidesteps the Celsius/Fahrenheit finalization entirely.

### 3. Sentinels (999 / 99999) are excluded from the measured series but SURFACED

The feed uses `H2S = 999` (23× in 4 yrs) and `CH4 = 99999` (2×) as sentinels. Their
semantics are genuinely unknowable from the outside — "no data" or "off-scale
high"? Recording `999` as a measured value would both corrupt the series and
false-fire an exceedance, so sentinels are **excluded from measurements**. But they
are **not silently dropped**: the classifier marks them an `anomaly` and, by
default (`alert_on_sentinel: true`), they are surfaced in the same-day email and in
the snapshot's Status column.

Per the project's fail-safe principle (don't infer "benign" when external semantics
are unknowable — the WDS `0.0`-years precedent), **this ADR recommends treating a
sentinel as an alert-worthy anomaly**, which is the shipped default. Trisha can set
`alert_on_sentinel: false` if she learns the sentinel is pure no-data.

### 4. Conservative, cited, config-driven alert thresholds — H2S settled, CH4 pending

A wrong threshold floods or starves, so "what counts as an exceedance" is a value
that must rest on published action levels, cited so the artifact stays credible:

- **H2S `72 ppb` — SETTLED (2026-07-14).** 72 ppb is *both* the Michigan EGLE H2S
  Initial Threshold Screening Level (ITSL, 100 µg/m³ over a 24-hr average ≈
  **72 ppb**, health screening) *and* the **Ridge Wood Elementary monitor's own
  published 24-hr action level** (data-sources item 8 / Stream F — Barr's
  school-adjacent H2S monitor). Two independent official sources landing on the same
  number settles it: use 72 ppb for both air-monitor streams, no longer "pending
  confirmation". **Nuance:** 72 ppb is a *24-hour-average* level, and Stream E
  applies it to *hourly instantaneous* readings — deliberately conservative (a
  single hot hour alerts); whether to compute a rolling 24-hr average to match the
  action level exactly is a refinement, not a blocker. The Ridge Wood source also
  publishes a **750 ppb 15-minute *acute*** level; adding that as a higher-urgency
  second tier is a small follow-on (a classifier tier). A far lower **odor-nuisance**
  level (~5–8 ppb) stays a *watch* reference, not the alert default: `H2S >= 5 ppb`
  occurred ~2,874× in 4 years, so alerting there would flood.
- **CH4 `12,500 ppm` — PENDING (not cross-validated).** No second monitor measures
  CH4, so there is no equivalent to confirm it against. `40 CFR 258.23` requires
  methane below the **Lower Explosive Limit** (LEL = 5% vol = 50,000 ppm) at the
  facility boundary; `12,500 ppm` is 25% of the LEL, a common action trigger.
  Perimeter readings run ~4–27 ppm, so this fires only on a genuine gas-migration
  event; a lower "elevated landfill gas" watch level (~100 ppm) is an option. An
  overnight-worker research item (queue #75) is staged to find published/recommended
  CH4 (and H2S) thresholds and firm this up before the stream is enabled.

The stream uses its **OWN classifier** (`gfl_air_client.classify_reading`), never
`email_alerts.is_urgent` — that one thresholds on Fahrenheit temperature and would
never fire on H2S/CH4 (the same "a new stream brings its own classifier" call WDS
made in ADR 009).

### 5. State lives in the "GFL Air" tab, NOT `_meta`

`_meta` singletons (`wds_seen`, `pending_digest`, …) are written by `write_meta`,
which overwrites **all** keys at once. That is safe for WDS because WDS runs *inside*
the daily watcher (one process). This poller runs from its **own workflow**
(`gfl-air.yml`, own concurrency group), so writing `_meta` could clobber the daily
watcher's state if the two overlapped — exactly why PFAS/MMPC keep their state in
their own tabs. The **"GFL Air" tab** is therefore both the small human snapshot
(latest reading per station) **and** the cursor store (its `OBJECTID` column). Only
`gfl-air.yml` writes it, and its own concurrency group serializes it with itself,
so there is no cross-workflow clobber.

Two correctness details this forces:

- **Parse OBJECTID to `int` before `max()`.** Sheets returns RAW cells as text, and
  `"9" > "17614325"` as strings — a string `max()` would silently rewind the cursor
  and re-ingest ~214k rows. `gfl_air_cursor` int-parses.
- **`gfl_air_cursor` does not swallow a read error** the way `_tab_rows` does: a
  clean read with no rows returns `None` (→ first-run baseline); an API failure
  propagates (→ the watcher skips-and-warns), so a transient read blip is never
  mistaken for "first run" and re-baselined.

### 6. Measurement volume: `digest` by default (deviation from the handoff, flagged)

The handoff said "each reading becomes a Measurement." The spike revealed the feed
is **hourly** — × 6 stations × 2 pollutants ≈ **288 rows/day** into the shared
Measurements tab (which other rebuilds scan), and hourly `H2S=0.0 BDL` is noise. So
the default `measurements_mode: digest` writes **every exceedance individually plus
one per-station daily peak per pollutant** (~12/day); `all` opts into full fidelity.

The **source feed remains the system of record** for the complete hourly history,
so `digest` loses nothing *while the feed exists*. If GFL ever purges history, only
captured rows survive — if in-Sheet preservation of the full series matters, the
right answer is a raw-archive-to-Drive fast-follow (the `wds_archiver.py` pattern),
not firehosing the case file. This is Trisha's call; the deviation is surfaced here
and in the PR rather than silently downsampled.

### 7. GFL-self-reported attribution is in the ROW, not just this ADR

Every reading is GFL's **own self-report**, not an EGLE measurement. The Measurements
rows carry that in the human-visible columns — Document Name = "GFL perimeter air
monitoring (GFL self-reported)", Facility = "Arbor Hills Landfill", and each Note =
"GFL self-reported perimeter air monitoring; location_type=perimeter_station" — the
same way operator claims are attributed as claims elsewhere.

## Schema mapping (ADR-004 Measurement)

| ArcGIS field | Measurement | note |
| --- | --- | --- |
| `H2S` (ppb) | `metric=hydrogen_sulfide`, `unit=ppb`, `basis=measured` | new metric name (see below) |
| `CH4` (ppm) | `metric=methane`, `unit=ppm`, `basis=measured` | existing metric |
| `LocName` | `well_id` | perimeter station MS-1..MS-6 |
| `Date` | `as_of_date` (ISO-8601 UTC) | epoch-ms → `YYYY-MM-DDTHH:MMZ` |
| — | `note` | self-reported attribution + `location_type=perimeter_station` + BDL flag |

Two schema notes, both deferred to the metric-taxonomy / `location_type` roadmap
(unreviewed at build time, so not pre-empted here):

- `metric=hydrogen_sulfide` is a new value beyond the doc-classifier's `Literal`
  enum (`temperature`/`carbon_monoxide`/`oxygen`/`methane`/`other`). It is an honest
  name — **not** `other`, which would erase the pollutant identity — stored as text
  in the Sheet (no schema break). This source never goes through the Claude
  classifier, so the enum does not gate it.
- The Measurement schema has **no `location_type` column** yet, so
  `perimeter_station` rides the `note` rather than being dropped.

## Architecture

- `gfl_air_client.py` — fetch + pure mapping (isolated; never touches
  `egle_doc_parser.py`, so the Decode document base stays domain-agnostic). Typed
  `GflAirFetchError` (never a silent `[]`).
- `gfl_air_watcher.py` — standalone daily poll (pfas_watcher-shaped `run()`/
  `_should_run()`), gated on `gfl_air.enabled`. First-run baseline (alert none),
  incremental OBJECTID poll, over-cap re-baseline, own classifier, one same-day
  email per poll.
- `sheet_writer.py` — additive: `TAB_GFL_AIR`, `GFL_AIR_SUMMARY_HEADERS`,
  `gfl_air_summary_rows`, `ensure_gfl_air_tabs`, `write_gfl_air_summary` (replace),
  `gfl_air_cursor` (int-parsed). Readings ride the existing Measurements tab.
- `.github/workflows/gfl-air.yml` — scheduled (8am ET), own concurrency group,
  no-op while disabled.

## Activation (all Trisha's — this loop never flips it live)

Same three-step pattern as Streams C/D and PFAS:

1. review + merge this PR (the disabled stream is safe to merge on mocked-green);
2. the first enabled run baselines the current readings (records the snapshot +
   sets the cursor, alerts on none) — no seed script needed, and it cannot blast
   the ~214k-row history;
3. **confirm the alert thresholds** (`gfl_air.thresholds`) — H2S 72 ppb is settled
   (equivalent across both air monitors; decision 4), CH4 12,500 ppm is pending the
   overnight-worker threshold research (queue #75) — then set `enabled: true` and
   commit.

It is keyless — no secret to provision.

## Adversarial review — mitigations built in, residual risks accepted

- **Show-stopper (the dashboard has no open service):** did not occur — the spike
  confirmed path A. Recovery would have been a Step-3 stop with a doc-PR.
- **Schema drift / Barr renames the layer:** the fetch fails **loudly**
  (`GflAirFetchError` aborts the run) rather than silently archiving nothing; the
  per-poll reading-count log makes a drop to zero visible.
- **Source-side full-table reinsert (every OBJECTID bumped):** the over-cap guard
  (`max_new_readings_per_run`) re-baselines instead of stampeding ~214k rows into
  the case file (WDS Rule B(ii)).
- **Wrong threshold floods/starves:** thresholds are config-driven, cited, and the
  stream ships disabled so Trisha reviews them before any alert can fire.
- **GFL-reported data is the operator's own self-report:** attributed as such in the
  row (decision 7).
- **OBJECTID-reset silent stall (accepted, watch before enabling):** the cursor is
  verified monotonic-with-time *today* (decision 1). But if Barr ever rebuilds the
  service and the new OIDs reset **below** the stored cursor (~17.6M), `OBJECTID >
  cursor` would return nothing **forever** — a permanent silent zero that looks
  identical to "no new readings." The over-cap guard only catches the opposite
  (OIDs bumped *higher*). There is no auto-recovery beyond the per-poll reading-
  count log, and a log line is not an alert — so for a stream that is quiet-by-
  design until an exceedance, this is the one way a real reading could go unseen.
  **Before enabling, add a liveness check** (alert if N days pass with zero new
  readings); until then, `enabled: false` bounds the blast radius to nil.
- **Over-cap suppresses a genuine catch-up's alerts (accepted):** a poll returning
  more than `max_new_readings_per_run` re-baselines and alerts on **none** — correct
  as stale-data deferral (the `watcher.max_new_docs_per_run` posture), but it does
  mean a real exceedance inside a >cap catch-up window is recorded-not-alerted. The
  window only arises after a multi-day outage or a feed reinsert.
- **Residual (accepted):** a future numeric-mutation change (decision 2) or a source
  format change could under-capture until the reading-count log flags it — no
  auto-recovery beyond that signal + the `enabled: false` / disable lever; and
  `digest` mode relies on the source feed remaining the system of record for full
  history (decision 6).

## Verification

- **Spike:** live queries against the FeatureServer established the endpoint, the
  schema, the ~214k-row scale, the hourly cadence, the sentinel values, and the
  no-numeric-mutation finding.
- **Live end-to-end:** the actual `gfl_air_client` code (not just mocks) was run
  against the real feed — baseline returns the 6 MS stations, the mapping produces
  correct `basis=measured` measurements with attribution, incremental fetch respects
  the cursor, and a bad URL raises `GflAirFetchError`.
- **Hermetic tests:** `tests/test_gfl_air.py` (small synthetic ArcGIS fixture, no
  committed data) covers the mapping, classifier, digest/all selection, cursor
  int-parse, and every `run()` flow (baseline / incremental / skip-seen / over-cap /
  sentinel / read-error).
