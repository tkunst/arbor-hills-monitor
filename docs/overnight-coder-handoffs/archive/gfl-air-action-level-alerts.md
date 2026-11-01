# Overnight-coder handoff — Perimeter CJ action-level alerts: consolidated open + close, per (station, gas), with an episode log

*Staged 2026-09-03 (Trisha-directed, co-designed live). Read `docs/overnight-coder.md`
first. This is a **LIVE-PATH change** — `gfl_air.enabled: true` on `main` — so per
`overnight-coder.md` Step 3 real-specimen verification is MANDATORY (no mocked-green-only
merge) and per Step 8 open a **DRAFT PR for Trisha's review, do NOT auto-merge**: it changes
what alert emails go out to real recipients (and the 5 county commissioners join the WATCH
tier ~2026-09-17). Recommended tier: **Sonnet** (or Opus for the state-machine care).*

*This **refactors the existing CH4-40 ppm WATCH** (built by `coder:gfl-air-thresholds`,
ADR on the two-tier CH4 scheme) from "one email per station per episode" into the
consolidated model below, and **adds an H2S 30 ppb tier** in the same model. It does NOT
touch the separate EXCEEDANCE-tier alerts (CH4 >= 500 ppm instantaneous; H2S 24-hr rolling
avg >= 72 ppb, `coder:gfl-air-24h-average`) — those stay exactly as they are, on the full
alert list. Build order: safe to do standalone; avoid a merge collision with any other
`gfl_air_client`/`gfl_air_watcher` item.*

## Why (the enforcement point — this is not cosmetic)

The **canonical threshold source is the SET-report B1 master table**
(`documents/arbor-hills/SET-report/html/B1-symptom-master-table.html` in Lotext —
primary-verified "what Arbor Hills is held to"). Pull every number + its framing from there.
Validated against the Consent Judgment PDF this session
(`CJ-2020-0593-CE-consent-judgment-2022-03-07.pdf`):

- **Perimeter H2S Action Level = 30 ppb, rolling avg over a 15-minute period (CJ ¶def T).**
  The CJ's OWN enforceable fenceline trigger. Every exceedance drives a **¶5.5 root-cause
  analysis + prevent-recurrence + 48-hour correction** (records duty ¶5.5(F)).
- **Perimeter Methane Action Level = 40 ppm, 15-min rolling (CJ ¶def U)** — same enforceable
  response. The monitor's existing 40 ppm WATCH IS this CJ line.
- The **72 ppb (24-hr) / 750 ppb (15-min acute)** numbers are **EGLE ITSL health-screening**,
  NOT the CJ trigger. The monitor currently WATCH-alerts H2S nowhere and exceedance-alerts
  H2S only at 72 ppb / 24-hr avg — **weaker AND slower than the CJ's own 30 ppb / 15-min**.
  This handoff closes that gap.

**The data (live ArcGIS feed, layer 4, queried 2026-09-03; use these as backtest anchors):**
across the record H2S crossed **30 ppb 56 times on 24 days** (peak **326.9 ppb** MS-3
2025-06-19; MS-2 dominant; a **5-station same-hour event** 2022-12-08 18:00:00Z at 74-78 ppb;
multi-hour episodes 2023-10-05/06 and 2024-02-22/23), which de-dupe to **~34 per-station
episodes** (2022:7 / 2023:17 / 2024:7 / 2025:3). It NEVER reached 750 ppb (0). So 30 ppb is
consequential but does not flood (~1 email/day-of-exceedance after consolidation).

## Goal

Replace the per-station CH4 WATCH with a **consolidated, per-(station, gas), open-and-close
action-level alerting system** over the perimeter feed, and persist each episode as a durable
record that can back an "RCA-was-done-properly" review.

### 1. Thresholds (the WATCH / action-level tier)

- **H2S >= 30 ppb** (CJ Perimeter H2S Action Level, ¶def T) — NEW.
- **CH4 >= 40 ppm** (CJ Perimeter Methane Action Level, ¶def U) — MOVED into this model.

Config-driven, with a rollback lever: extend the existing `gfl_air.watch_thresholds` block
(today `ch4_ppm: 40`) to add `h2s_ppb: 30`. Empty/absent for a gas = that gas is
display-only (no alert), matching the existing rollback convention. Keep
`gfl_air.thresholds` (the EXCEEDANCE tier: `h2s_ppb: 72` used as the 24-hr avg, `ch4_ppm: 500`
instantaneous) UNCHANGED.

### 2. Episode lifecycle state — keyed on (station, gas), NOT station alone

This is the load-bearing correctness point (Trisha, 2026-09-03). Track episode state
**independently per `(station, gas)` pair**. A station in an active CH4 episode must NOT
suppress a new H2S alert for the same station, and vice versa. Per (station, gas), an episode:

- **OPENS** when a reading crosses its threshold while that (station, gas) is armed
  (last state below threshold, or first-ever).
- **stays open** across subsequent readings >= threshold (no re-alert). The watcher polls
  **once daily** (`cron: 0 13 * * *`) and processes a whole day's readings in one batch, so an
  episode routinely spans multiple runs — the state MUST persist across runs (see 5).
- While open, **track the running peak**: max value seen + its timestamp.
- **CLOSES / re-arms** when a reading for that (station, gas) drops back below threshold. That
  below-threshold reading's value + time is the "returned to normal" recovery point.

### 3. The consolidated per-run SCREENING email (Model A) — legal framing is load-bearing

**This email is a SCREENING alert on PUBLIC HOURLY data, NOT a determination that a Consent
Judgment action level was exceeded (formal legal review, 2026-09-03).** The CJ defines BOTH action
levels as **15-minute rolling averages** computed from **~once-per-minute** sampling (¶def T/U; the
CJ monitoring spec at ¶5.4: "provide continuous measurement of H2S and methane using a sampling
rate of approximately once per minute"), and AHL must retain those measurements + exceedance
records + RCAs + corrective-action records (¶5.5(F)). The public ArcGIS feed gives only HOURLY
values. So an hourly value above the numeric benchmark is a **LEAD** — a concrete reason for EGLE
to obtain and examine the nonpublic 15-min / 1-min records — **not itself an exceedance finding.**
Every email must make this explicit and must NEVER assert a legal exceedance. (For an AG recipient
these are even less self-sufficient: the AG needs EGLE's technical verification + a documented
compliance conclusion, so the email carries an explicit "AG status: informational lead" line.)

**Consolidate DELIVERY, never DATA; Model A = ONE email per run; STATIC template** (unchanged: the
watcher runs daily `cron: 0 13 * * *`, so one email per run carries the all-monitor snapshot + any
newly-detected + any returned-below-benchmark; every value is that station's own verbatim hourly
reading; no averaging/roll-up; fixed boilerplate authored once, only `<data>` slotted in, NO
per-send LLM — deterministic, testable, auditable).

**MANDATORY wording (do not paraphrase loosely — each is a corrected legal point):**
- Obligated party is **Arbor Hills Landfill, Inc. ("AHL")** — the defendant — **NOT "GFL."** Say
  "the Consent Judgment action level applicable to AHL," never "GFL's own action level."
- Say **"the publicly reported hourly value exceeded the numerical benchmark corresponding to the
  Consent Judgment action level; verification against the required 15-minute rolling-average data
  is necessary"** — NOT "crossed the enforceable CJ action level."
- The **48-hour** deadline is on **CORRECTING** the exceedance (¶5.5(E)), NOT on completing the RCA.
- **¶5.5(D) vs (E):** (E) applies at all times OTHER than during Cell 6/4F waste excavation/
  relocation; DURING those activities (D) routes the response through the Cell 6/4F Waste Relocation
  and Odor Control Plan (§3.6). The alert cannot know operational status, so say "potentially
  implicates ¶5.5(D) or ¶5.5(E) depending on whether Cell 6/4F excavation or relocation activities
  were underway."
- **"Above," not "at":** flag a station only if its RAW, UNROUNDED value is STRICTLY GREATER than the
  benchmark (¶5.5 says "above"). A value that displays as 40 but is exactly 40.0 is NOT flagged; if
  the unrounded value is above but rounds to the benchmark, show extra precision and say so.
- **"Below benchmark" != "BDL":** a recovery reading below the benchmark is not "below detection."
  Only say BDL when below the reporting/detection limit, and then give the detection limit + value.
- **"Returned below benchmark" != corrected:** it only means the public hourly number fell back
  under the benchmark; it does NOT establish AHL corrected anything.
- **Methane safety wording (verbatim intent):** never "near-explosive" or a categorical hazard
  claim. Use: "If confirmed as a representative ambient-air concentration, <X>% methane falls within
  methane's ~5-15% flammable range and warrants immediate safety assessment" (compute % from ppm).

**HISTORICAL vs LIVE — mark it in the SUBJECT.** If the reported data is not from the current day
(backfill, or the run processes older hours), the subject MUST lead with
`[HISTORICAL SCREENING ALERT — NOT A LIVE INCIDENT]`; a live event leads with `[SCREENING ALERT]`.
Without this a 2022/2023 event arriving in 2026 misleads recipients and discredits the system. The
subject names ALL implicated gases (a dual-gas event says "H2S + methane," never one gas), the
station(s), and the date + YEAR in ET.

**The standardized body (fixed template; `<slots>` filled from data):**

```
[SCREENING ALERT]  |  [HISTORICAL SCREENING ALERT — NOT A LIVE INCIDENT]
Arbor Hills perimeter — <gas(es)> hourly value(s) above CJ benchmark — <station(s)> — <date, ET>

AUTOMATED SCREENING ALERT — PUBLIC HOURLY DATA
This is NOT a formal determination that a Consent Judgment action level was exceeded. The public
source provides HOURLY values only; it does not provide the ~1-minute measurements the Consent
Judgment requires to calculate or verify the 15-minute rolling average that DEFINES an exceedance.

Facility:            Arbor Hills Landfill, 10690 West Six Mile Road, Salem Township, MI
Responsible entity:  Arbor Hills Landfill, Inc. ("AHL") — defendant under the Consent Judgment
Authority:           Consent Judgment No. 2020-0593-CE (30th Circuit Court, entered 2022-03-07)
Event ID:            AHL-<YYYY-MM-DD>-<station>-<seq>            e.g. AHL-2025-06-19-MS3-001
Event state:         Newly detected | Continuing | Returned below benchmark | HISTORICAL BACKFILL
Observed period:     <start> to <end>  (America/Detroit, ET; exact timestamps incl. year)
Source:              <public ArcGIS feed URL> · retrieved <run timestamp, ET>
                     Hourly value basis: NOT documented by the source (verified 2026-09-03: the Barr
                     FeatureServer service/layer/field descriptions and the public dashboard item are
                     all blank) — whether the hourly value is instantaneous, an hourly average, or an
                     hourly max is UNKNOWN from public data. State exactly that; it is a further
                     reason to obtain AHL's underlying ~1-minute measurements.
Data quality:        stations reporting <n>/6 · missing-data / 999-&-99999-sentinel flags: <…> ·
                     calibration status: <unknown unless published>

WHAT TRIGGERED THIS — public hourly values vs the CJ numeric benchmark:
  Perimeter H2S — CJ Action Level = 30 ppb as a 15-MINUTE ROLLING AVERAGE (¶def T)
  Perimeter CH4 — CJ Action Level = 40 ppm as a 15-MINUTE ROLLING AVERAGE (¶def U)
  The values below are PUBLIC HOURLY readings compared to those numeric benchmarks. Whether a
  15-minute rolling-average exceedance occurred can be determined only from AHL's nonpublic data.

All six perimeter monitors @ <time, ET>    (▲ = public hourly value ABOVE the benchmark)
  Station  H2S(ppb)   CH4(ppm)   wind(dir@mph)   [station coords / map link]
  MS-1     <v>▲/·     <v>▲/·     <d@s>
  ...MS-2..MS-6...
  Context: a compact table of the 2 HOURS BEFORE AND AFTER each flagged station (inline or attached).

RETURNED BELOW BENCHMARK (public hourly series recovered — does NOT establish AHL corrected it):
  <station>·<gas> — first-above <t1,val> · highest public hourly value <peak,t> ·
  first-below-benchmark <t2,val> · elapsed <duration>

POTENTIAL CONSENT-JUDGMENT IMPLICATIONS (only if verified as a 15-min-rolling exceedance):
  Potentially implicates ¶5.5(D) or ¶5.5(E) depending on whether Cell 6/4F excavation or relocation
  activities were underway. Under ¶5.5(E) AHL must conduct a root-cause analysis and appropriate
  corrective actions, and CORRECT the exceedance within 48 hours of detection — or request an
  extension under ¶16.4. (The 48-hour deadline applies to CORRECTION, not to completing the RCA.)

REQUESTED EGLE REVIEW: obtain AHL's underlying ~1-minute measurements and 15-minute rolling averages
for this period; determine whether a 15-minute rolling-average exceedance occurred; and review the
required root-cause analysis, corrective-action record, and any ¶16.4 extension (¶5.5(E)/(F)).
AG STATUS: informational lead pending EGLE technical verification — not a compliance determination.

Processing note: the monitor ingests the public feed in a DAILY BATCH, so one email may cover
several past hours at once; "newly detected / returned below benchmark" describe the PUBLIC HOURLY
series, not AHL's compliance status.
```

**New data the template requires the watcher to produce** (beyond the readings). Feed facts VERIFIED
2026-09-03 against layer 4 (`.../FeatureServer/4`), use them:
- **Event ID** per (station,gas,episode): `AHL-<date>-<station>-<seq>`.
- **Exact ET timestamps.** The feed carries `Date_Text` already in Eastern (e.g. `06/18/2025, 08:00
  PM`), but it is **fixed EST with NO DST** (verified) — so in summer it trails the real EDT clock by
  1h. Either convert the raw `Date` epoch to `America/Detroit` (correct DST) and state the tz, or use
  `Date_Text` and state the fixed-EST caveat. Never emit "Z"/UTC to recipients. Readings are exactly
  hourly, on the hour.
- **Hourly-value basis = UNKNOWN** (service/layer/field/dashboard metadata all blank, verified) —
  state it as unknown; do not claim avg/max/point.
- **BDL from the feed:** `H2S_Text`/`CH4_Text` = `'BDL'` when below detection (vs a numeric string).
  Use `_Text` for the BDL qualifier so "below benchmark" is never miswritten as "below detection."
- **Strictly-`>` on the NUMERIC field, not `_Text`.** `_Text` rounds (326.9 -> `'327'`; a value shown
  as `40` may be `40.4`), so the numeric `H2S`/`CH4` (1-decimal) drives the flag and any near-benchmark
  precision display.
- **run retrieval timestamp + source URL; data-quality** (stations reporting n/6, `999`/`99999`
  sentinel + missing flags, calibration = not published); **wind** (`Direction`/`Speed` on the feed) +
  **station coordinates / map link**; **±2-hour context** rows per flagged station (one small windowed
  query); **HISTORICAL** determination (data-date older than run date => historical subject + state).
The "returned below benchmark" fingerprint replaces "CLOSED/corrected." All DATA in a STATIC frame —
still no per-send LLM.

### 5. Durable episode log (the RCA-tracking artifact)

Every CLOSED episode also writes one row to a NEW Sheet tab **"Perimeter Action-Level
Episodes"** (add `TAB_PERIMETER_EPISODES` to `sheet_writer.py`): `station, gas, threshold,
opened_at, opened_value, peak_value, peak_at, cleared_at, cleared_value, duration_hours,
n_readings_over, source=CJ ¶def T/U`. This is the clean, durable record Trisha will track the
RCA-was-done-properly review against (does GFL's ¶6.3 Perimeter Action Level Log + quarterly
RCA report show an RCA + prevent-recurrence correction for each of these?). The closeout EMAIL
is the ping; the LOG ROW is the artifact. Open episodes get an interim row (or a live "open"
snapshot) so a long-running episode is visible before it closes — coder's call, keep it simple.

## Honest-measurement caveat (put it IN the email text)

The CJ level is a **15-minute rolling average**; the public ArcGIS feed is **hourly**, so we
CANNOT compute the true 15-min rolling average — we alert on **any hourly reading crossing the
level** as the closest available proxy. State this in both emails ("hourly-reading proxy for the
CJ's 15-min rolling action level; GFL's own continuous monitor + its ¶6.3 Perimeter Action Level
Log are the authoritative 15-minute record"). This proxy likely UNDERCOUNTS true CJ exceedances.
Never present an hourly-proxy count as the official CJ exceedance count.

## Recipients / routing

The consolidated OPEN + CLOSE emails go to the **WATCH tier**
(`gfl_air.watch_alert_recipients` + the `GFL_AIR_WATCH_RECIPIENTS_EXTRA` secret) — the same list
the 5 county commissioners join ~2026-09-17. Dedicated subject/body; NEVER merged into the
full-list EXCEEDANCE/anomaly email. Empty watch list = display-only rollback lever (unchanged).

## Approach (pinned)

1. **Config:** add `watch_thresholds.h2s_ppb: 30` (+ comments citing ¶def T and the
   hourly-vs-15min-rolling caveat). Leave `thresholds` (exceedance tier) alone.
2. **Client/classifier:** `classify_reading` already takes `watch_thresholds`; extend so it
   classifies H2S against `watch_thresholds.h2s_ppb` (it currently only watches CH4). Exclude the
   `999` no-data sentinel and BDL handling exactly as the existing paths do.
3. **Episode engine (the real work):** a `(station, gas) -> episode` state map, persisted across
   daily runs (extend the existing watch-episode state store the CH4-40 watch already uses;
   find it — likely a Sheet tab or the `GFL Air` snapshot — and re-key it per (station, gas)).
   Per run: update peaks, detect opens, detect closes; emit ONE consolidated open email and ONE
   consolidated close email if there is >=1 open/close respectively; write episode-log rows on
   close.
4. **Refactor the CH4-40 watch** into this consolidated model (retire the per-station-email
   behavior). Keep the CH4 classifier tier; only the notification/dedup changes.
5. **Backtest (real-specimen, mandatory):** replay the historical feed and confirm the engine
   reproduces the known episodes — the 2022-12-08 5-station same-hour open (one email, five
   stations), the 326.9 ppb MS-3 2025-06-19 spike, the multi-day 2023-10 / 2024-02 MS-2 episodes
   (correct peak + duration + close), and the ~34 per-station episode total. Confirm the
   consolidated email count (<= 1 open-email per exceedance-day).
6. **Tests (hermetic, mock the feed):** per-(station,gas) independence (a station's open CH4
   episode does NOT suppress its H2S open, and vice versa); a 5-station same-run open => ONE email
   listing five; an episode spanning multiple runs opens once, tracks the cross-run peak, closes
   once with correct start/peak/end/duration; a below-threshold reading re-arms; sentinel excluded;
   empty watch list => display-only; the EXCEEDANCE tier (500 ppm / 72 ppb-24hr) is unchanged.

## Docs / topology

New ADR (next number in `docs/decisions/`) documenting: the CJ ¶def T/U action levels as the
WATCH tier, the per-(station,gas) episode state machine, the consolidated open/close model, the
episode-log tab, and the hourly-vs-15min-rolling proxy. Update the `coder:gfl-air-thresholds`
ADR to note the CH4-40 watch was folded into this consolidated model. README/CLAUDE.md Streams
list + topology as warranted.

## Definition of done

Green `pytest -q`; consolidated OPEN and CLOSE WATCH emails (one per run, per gas, values per
station) fire on the CJ perimeter action levels (H2S 30 ppb + CH4 40 ppm) with correct
per-(station,gas) episode lifecycle; each closed episode writes a "Perimeter Action-Level
Episodes" log row (start/peak/cleared/duration); the hourly-proxy caveat is in the email text;
the EXCEEDANCE tier is untouched; real-feed backtest reproduces the documented episodes; ADR +
tests + topology in the same PR. **Open a DRAFT PR for Trisha — do NOT auto-merge** (live path,
changes outbound alerts). Config still exposes the rollback levers (empty threshold / empty watch
list => display-only).
