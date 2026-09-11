# ADR 039: Consolidated perimeter action-level screening — per-(station,gas) open/close episodes on the CJ enforceable levels

**Status:** accepted (2026-09-10) · **Stream E** (`gfl_air`) · ships to the live path
(`gfl_air.enabled: true`), opened as a **draft PR for Trisha's review** — it changes
outbound alert emails, so it is not auto-merged (overnight-coder Step 8).

## Context

Stream E polls GFL's public perimeter-air ArcGIS feed (six MS-* stations, hourly H2S
ppb + CH4 ppm; ADR 014). It already had:

- an **EXCEEDANCE** tier — CH4 ≥ 500 ppm (NESHAP), H2S ≥ 72 ppb as a **24-hr rolling
  average** (EGLE ITSL) — emailing `[URGENT]` to the full list (ADR 014 + its
  2026-07-18 addendum); and
- a per-station **CH4-40 WATCH** — one dedicated email per station per continuous
  ≥40 ppm episode, to a Trisha-scoped list (ADR 014's 2026-07-21 addendum).

The gap this ADR closes (Trisha-directed, co-designed 2026-09-03): the **Consent
Judgment's own enforceable perimeter action levels** were only partly watched.

- **Perimeter H2S Action Level = 30 ppb, 15-minute rolling average (CJ ¶def T).** The
  monitor watched H2S *nowhere* below the 72 ppb / 24-hr ITSL exceedance — i.e. it was
  **weaker AND slower** than the CJ's own enforceable trigger.
- **Perimeter Methane Action Level = 40 ppm, 15-minute rolling average (CJ ¶def U).**
  The existing CH4-40 WATCH already tracked this line but as a CH4-only, per-station
  notification with no closeout and no durable episode record.

Canonical threshold source: the SET-report B1 master table
(`documents/arbor-hills/SET-report/html/B1-symptom-master-table.html`, primary-verified
"what Arbor Hills is held to"), validated against the CJ PDF
(`CJ-2020-0593-CE-consent-judgment-2022-03-07.pdf`).

## Decision

Replace the CH4-40 WATCH with a **consolidated, per-`(station, gas)`, open-and-close
action-level SCREENING system** over the perimeter feed, and persist each episode as a
durable RCA-tracking record.

### 1. Thresholds — the WATCH (action-level) tier

`gfl_air.watch_thresholds` gains `h2s_ppb: 30` alongside `ch4_ppm: 40`. A gas absent
from `watch_thresholds` is display-only (no episode, no alert). The EXCEEDANCE tier
(`gfl_air.thresholds`: 72 / 500) is **untouched**.

### 2. `⚖️` These are SCREENING alerts on PUBLIC HOURLY data — NOT exceedance determinations

The load-bearing legal framing (formal review, 2026-09-03). The CJ defines both action
levels as **15-minute rolling averages** computed from **~1-minute** sampling (¶def
T/U; ¶5.4); AHL retains those measurements + exceedance + RCA + corrective-action
records (¶5.5(F)). The public feed gives **hourly** values only. So an hourly value
above the numeric benchmark is a **LEAD** for EGLE to obtain and examine AHL's nonpublic
records — **never itself an exceedance finding.** Every email says so and never asserts
a legal exceedance. The mandatory wording (each a corrected legal point, in
`gfl_air_watcher.format_screening_email` / `format_closeout_email`, verified by
`tests/test_gfl_air.py`):

- Obligated party is **Arbor Hills Landfill, Inc. ("AHL")**, the CJ defendant — **not
  "GFL"** (the operator). "the Consent Judgment action level applicable to AHL."
- "the publicly reported hourly value exceeded the numerical benchmark corresponding to
  the Consent Judgment action level; **verification against the required 15-minute
  rolling-average data is necessary**" — never "crossed the enforceable action level."
- The **48-hour** deadline is on **CORRECTING** the exceedance (¶5.5(E)), not on
  completing the RCA.
- "potentially implicates **¶5.5(D) or ¶5.5(E)** depending on whether Cell 6/4F
  excavation or relocation activities were underway" (the alert can't know operational
  status).
- Flag **STRICTLY ABOVE** the benchmark on the **raw unrounded numeric** (¶5.5 "above"):
  a value that displays as 40 but is exactly 40.0 is NOT flagged; a value above that
  rounds to the benchmark shows extra precision and says so.
- "below benchmark" ≠ **BDL**: BDL only where the source's `_Text` says so; "returned
  below benchmark" ≠ AHL corrected anything (only that the public hourly number fell
  back under).
- **Methane flammability** wording: never "near-explosive." The sentence "If confirmed
  as a representative ambient-air concentration, X% methane falls within methane's
  ~5-15% flammable range and warrants immediate safety assessment" fires **only** when
  X (= ppm/10000) actually reaches ~5% (50,000 ppm) — at ordinary 40-500 ppm perimeter
  readings (0.004-0.05%) it does not (asserting otherwise would be the overstated claim
  the accuracy brand forbids).
- **Hourly-proxy caveat** in the email text: this hourly-reading proxy for the CJ's
  15-min rolling level likely **UNDERCOUNTS** true CJ exceedances; GFL's continuous
  monitor + its ¶6.3 Perimeter Action Level Log are the authoritative 15-minute record.
- **HISTORICAL vs LIVE in the SUBJECT.** Data not from the current ET day (backfill /
  older hours) → `[HISTORICAL SCREENING ALERT — NOT A LIVE INCIDENT]`; a live event →
  `[SCREENING ALERT]`. The subject names all implicated gases ("H2S + methane" for a
  dual-gas event), the stations, and the date + year in ET.
- Every email carries the standardized screening block: facility / AHL / authority /
  Event ID(s) / event state / exact ET period / source + retrieval time + hourly-basis-
  UNKNOWN / data quality / the six-monitor table / ±2-hour context per flagged station /
  station coordinates + map link / requested EGLE review / AG-status line.

### 3. Episode lifecycle — keyed on `(station, gas)`, NOT station alone

`gfl_air_watcher.process_episodes` (pure). Per `(station, gas)` with a configured watch
threshold, walking the poll's OBJECTID/Date-ASC readings:

- **OPENS** on the first reading **strictly above** the threshold while armed (records
  `event_id`, `opened_at/value`, `peak`, `n_over`).
- **stays open** across subsequent above readings (no re-alert), tracking the running
  peak (value + timestamp) and `n_over` — persisted across the daily runs, so an
  episode routinely spans several polls.
- **CLOSES / re-arms** on the first real reading **at-or-below** the threshold; that
  value + time is the "returned below benchmark" recovery point.

`(station, gas)` independence is the load-bearing correctness point (Trisha,
2026-09-03): a station's open methane episode must not suppress a new H2S open for the
same station. A **no-data reading never opens or closes** — recovery is read only from
an affirmative real below-benchmark reading, never from silence or a fault marker.

**No-data / TEST handling (correctness-critical, spike 2026-09-10).** `999` is a shared
no-data/fault marker: every one of the feed's 24 `CH4=999` rows carries
`CH4_Text='TEST'` (23 also `H2S=999`). The numeric-sentinel config excludes 999 for H2S
but only 99999 for CH4 — so a bare port would flag a `CH4=999` maintenance reading as a
real methane action-level crossing and email heavy legal framing about it.
`gfl_air_client.is_no_data` therefore excludes a reading whose `_Text` is `TEST` (for
either gas) in addition to the numeric sentinel. Scoped to this screening tier; the
untouched exceedance-tier paths are unchanged (see Residuals).

### 4. Two consolidated emails per run — DELIVERY consolidated, DATA never

To the WATCH tier (`gfl_air.watch_alert_recipients` + `GFL_AIR_WATCH_RECIPIENTS_EXTRA`).
Per daily run, at most:

- **ONE SCREENING (open) email** — iff ≥1 episode opened this run. Lists ALL six
  monitors' own verbatim per-station readings for BOTH gases (▲ = strictly above),
  the newly-detected + continuing episodes, and a ±2h context block per flagged
  station. Static template, `<data>` slotted in, **no per-send LLM**.
- **ONE CLOSEOUT (close) email** — iff ≥1 episode closed this run. Start / peak /
  return / duration per episode, same standardized framing.
- A **continuing-only** run (elevated but nothing newly opened/closed) sends nothing —
  no re-alert.

Consolidate DELIVERY, never DATA: no averaging, no site-wide roll-up, no dropping the
non-exceeding stations. The watch tier is **never** folded into the full-list
exceedance/anomaly email (`alert_lines(..., include_watch=False)` always).

**Empty `watch_alert_recipients` = display-only rollback lever:** the episode engine is
skipped entirely (no emails, no episode-log writes), and the snapshot tab still shows
watch status. Empty `watch_thresholds` for a gas = that gas is display-only.

### 5. Durable episode log — the RCA-tracking artifact

A new tab **"Perimeter Action-Level Episodes"** (`sheet_writer.TAB_PERIMETER_EPISODES`).
One append-only row per **closed** episode: station, gas, threshold, opened/peak/cleared
(ET) + values, duration_hours, n_readings_over, source (CJ ¶def T/U), Event ID, logged
(UTC). This is the clean record Trisha tracks the "was an RCA done properly" review
against (does GFL's ¶6.3 Perimeter Action Level Log + quarterly RCA report show an RCA +
prevent-recurrence correction for each?). Open-episode visibility comes from the GFL Air
snapshot tab + the OPEN email + the column-O state map; the log holds closed episodes.
The episode-log **row is written BEFORE the state entry** (repo ordering). Note the
OBJECTID cursor is committed *upstream* (in `run()`, before the episode engine),
independent of the column-O state — so this is NOT a true atomic retry the way the
doc-ingestion path is (these readings aren't re-fetched next run). On a rare Sheets-write
**double** fault it degrades toward a duplicate/slightly-wrong close row, or (an open-only
run that recovers before the next daily run) a lost durable log row — never a false
`[URGENT]`, never lost measurements. See Residual 1.

### 6. State store, timezone, coordinates

- **State:** the open-episode map is JSON in the GFL Air tab's **column O** (reusing the
  retired CH4-watch marker cell; a legacy array reads as `{}` → fail-safe re-derive),
  outside the `A:L` snapshot span and column N's liveness marker.
- **Timezone:** every recipient-facing time is `America/Detroit` (correct DST) computed
  from the raw `Date` epoch — never the feed's fixed-EST `Date_Text` (which trails EDT
  by 1h in summer), never UTC/Z. The Event-ID + subject date is the ET date, so the
  `2025-06-19T01:00Z` MS-3 spike is labeled **2025-06-18** (ET) — deliberate.
- **Coordinates:** layer 4 (readings) is a Table with no geometry; coords are fetched
  best-effort from the current-per-station layer 0 (`outSR=4326`, keyed by `Name`). A
  coords or ±2h-context fetch failure degrades the email (dashboard link / "context
  unavailable"), never blocks it.

### 7. Email-delivery posture (deliberate)

The episode **STATE is committed like the cursor** (system of record); the SCREENING /
CLOSEOUT **emails are best-effort with no retry**, matching this stream's existing
exceedance-email posture (a failed SMTP send there is logged and lost, not retried).
This differs from the retired CH4-watch's send-gated marker. Rationale: (a) consistency
with the more-important `[URGENT]` exceedance email in the same file; (b) it avoids the
duplicate-legal-alert risk a retry would introduce; (c) the durable artifacts (episode
state + the closed-episode log row) always capture the episode even if an email is lost.
**Residual (accepted):** a transient SMTP failure at the exact moment an episode opens
loses *that* OPEN email (the CLOSEOUT + log row still fire). Flagged for Trisha.

## Real-specimen backtest (live feed, 2026-09-10 — mandatory, live path)

Replayed the historical feed through `process_episodes`:

- **H2S episodes: 33** (handoff anchor ~34), by ET year **7 / 16 / 7 / 3** (anchor
  7/17/7/3) — the ±1 is de-dup boundary judgment on adjacent crossings.
- **2022-12-08 five-station same-hour open** reproduced exactly: MS-1/2/3/4/6 all open
  at `2022-12-08T18:00Z` → ONE consolidated SCREENING email listing five.
- **MS-3 2025-06-19 326.9 ppb spike** reproduced: peak 326.9, duration 1.0 h, n_over 1.
- **MS-2 multi-day** reproduced: 2023-10-05/06 (peak 154.8, 4 h) and 2024-02-22/23
  (peak 146.2, 4 h), correct peaks / durations / closes.
- Consolidated email count: 23 distinct open-days for 33 episodes → **≤1 open-email per
  exceedance-day**, not a flood.

## Residuals / follow-ons (flagged, NOT fixed here)

1. **Best-effort delivery + non-atomic log write** (§5, §7) — (a) a transient SMTP
   failure at open-time loses that OPEN alert (the CLOSEOUT + durable log row still
   capture the episode; no retry, matching the exceedance email); (b) because the
   cursor commits upstream of the column-O state, a rare Sheets-write **double** fault
   degrades toward a duplicate/slightly-wrong close row, or an unlogged episode in the
   open-only-then-recovers-before-next-run case — never a false `[URGENT]`, never lost
   measurements.
2. **Pre-existing, out of scope:** the untouched EXCEEDANCE tier treats a `CH4=999`
   `TEST` reading as ≥500 ppm → a potential false `[URGENT]` to the full list. Not
   introduced here (this ADR protects the exceedance tier); the `is_no_data`
   TEST-exclusion added here fixes it for the screening tier only. A future change could
   extend the TEST/999 exclusion to the exceedance path.
3. **Recipients** — the 5 (soon 9) county commissioners join `watch_alert_recipients`
   ~2026-09-17; this build does not change the roster.

## Consequences

The monitor now screens on the CJ's own enforceable fenceline triggers (stricter +
faster than the ITSL exceedance tier), consolidates delivery to at most two emails per
run without ever averaging or dropping station data, keeps a durable RCA-tracking log,
and holds every checkable claim to the "accuracy over posturing" bar. The EXCEEDANCE
tier is unchanged. Ships to the live path as a draft PR for Trisha's review.
