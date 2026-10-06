# ADR 026 — Durable GFL perimeter-air exhibit (Stream E)

Date: 2026-07-26
Status: accepted (ships disabled)
Builds on: ADR 014 (Stream E GFL air), ADR 007 (OAuth durable Drive archive),
ADR 024/025 (durability model). Closes Gap G1-E-1 and Gap G3-E-1 from
`docs/data-lifecycle-architecture.md`.

## Context

Stream E is the only source of real fenceline READINGS (GFL's self-reported
hourly H2S/CH4 at the six perimeter monitors). Its durability was the weakest of
any stream:

- `gfl_air_watcher` used `drive_client` only for `sheets_service()` — there was
  **no Drive copy of any air reading** (Gap G3-E-1). The ArcGIS FeatureServer is
  the sole system of record: a live URL that can be retired, restructured, or
  pruned.
- `measurements_mode: digest` records only exceedances + daily peaks to the
  mutable Measurements tab, so the **full recent history is not captured locally**
  (Gap G1-E-1).

A court/advocacy exhibit of "H2S/CH4 at monitor X on date Y" had nothing durable
and self-contained to point at.

(Note: the 40 ppm CH4 WATCH email, once-per-episode dedup, and the wind/direction/
temp snapshot already exist on `main` — this ADR adds only the durable store.)

## Decision

Capture the selected readings to an **immutable, app-only Google Drive folder**,
gated so it ships safe and dark until a human enables it.

### 1. What is captured (Trisha's spec)

Per monitor (M1–M6): `monitor_id`, `timestamp`, `h2s_ppb`, `ch4_ppm`,
`wind_direction`, `wind_speed`, and `temp` (the one extra metric in the payload).
Selection (`select_capture_rows`, pure/unit-tested), per poll batch
(OBJECTID-ASC):

- **Every reading whose own classification is `exceedance` or `watch`** (Tier-1
  CH4 >= 40) — the full hourly series through any elevated period.
- Otherwise, **at least one reading per `baseline_hours` (default 8) window per
  station** — a downsample of the calm background.

Elevation is decided by the existing `gfl_air_client.classify_reading` (reused,
not reinvented), so the capture boundary tracks the same thresholds the alerts do.

### 2. Where it lands

An immutable JSON per poll (`gfl-air-capture-<date>-oid<max>.json`) uploaded via
the shared OAuth Drive client (`archive_client.upload_file`) into a **new app-only
folder** keyed by `GOAUTH_GFL_AIR_FOLDER_ID` — the same one-folder-per-mirror
pattern as MMPC (ADR 010) and Ridge Wood (ADR 016). One file per poll is
genuinely immutable (no read-modify-write race); the monthly summary **PDF is
rendered from these files only when an exhibit is actually needed** (deferred, per
Trisha's standing "immutable now, PDF later" rule).

### 3. Ships DISABLED (activation is a separate human step)

`gfl_air.capture.enabled` defaults `false`, and `_write_capture` is a **silent
no-op unless both the flag is on AND the OAuth creds + `GOAUTH_GFL_AIR_FOLDER_ID`
are configured**. So this merges safe on a mocked-green build (the ADR-009/010
new-source pattern): it cannot write anything, touch the live stream, or fail a
run until Trisha creates the folder, adds the secret, and flips the flag. The
capture call in `run()` is additionally best-effort (wrapped) and runs after the
measurements/cursor writes, so it can never affect the system-of-record data, the
cursor, or the alert path.

## Consequences

- Gaps G1-E-1 and G3-E-1 closed once enabled: a durable, structured, immutable
  Drive copy of the fenceline readings, independent of the live ArcGIS feed and
  the mutable Measurements tab.
- No behavior change until activated (flag off + no secret = no-op).
- Manual activation prerequisite (Trisha-only): create the app-only Drive folder,
  set `GOAUTH_GFL_AIR_FOLDER_ID`, set `capture.enabled: true`.
- Raw reading values (including sentinels) are stored verbatim — the capture is a
  faithful record; interpretation stays downstream.

## Alternatives considered

- **An append-only Sheet tab** (like `_wds_seen`). No new secret and ships
  immediately, but at ~18 baseline rows/day plus every elevated hour it grows a
  Sheet tab fast over years, and it is Tier-2 (Sheet) rather than the Tier-1
  immutable Drive exhibit Trisha asked for. Rejected; a Drive export could still
  be layered on later if wanted.
- **A single monthly file accumulated via read-modify-write.** Fewer files, but
  reintroduces an RMW race and makes the file mutable. Rejected in favor of
  immutable per-poll files that a monthly render aggregates.
- **Snapshot-level episode state for the capture boundary.** More machinery;
  per-reading classification already captures every elevated hour faithfully
  without tracking cross-poll episode markers.

## Addendum 2026-10-06: capture every reading, every field

**Why.** GFL's public dashboard shows only the `H2S_Text`/`CH4_Text` labels, which read
"BDL" for every value below 7 ppb, including stuck sensors' constant values; the numbers
exist only in the ArcGIS feed. The `sample` capture kept about 3 of every 24 calm hourly
readings per station (one poll on 2026-10-06 captured 14 of about 144), so most numeric
values were preserved nowhere but GFL's feed. Trisha: "make sure we have a backup of all
actual readings for both CH4 and H2S... Don't want to rely on that dashboard staying live."

**Decision.** New `gfl_air.capture.mode`: `all` (now live) keeps every perimeter reading;
`sample` keeps the original behavior. Each capture row now also carries `h2s_text`,
`ch4_text`, and `raw`, a verbatim copy of every field the source returned.
`_READING_FIELDS` now requests every measurement field the layer publishes
(adds `Relative_Humidity`, `Barometric_Pressure`, `Direction_Text`, `Date_Text`).
About 150 readings and about 80 KB of JSON per daily poll.

**Failure handling.** A capture gap is never silent: the run finishes its alert path
and then exits 1 (the GitHub failure email surfaces it) when the upload fails, when
capture is enabled without the Drive folder/creds, when the over-cap branch re-baselines
(it captures the oldest cap+1 fetched rows first, but anything past them is skipped), or
when the source rejected the extended field list and the readings query fell back to
the core fields (live alerting keeps working; the capture is thinner). An unknown or empty
`capture.mode` captures everything (fails safe). The monthly full-feed snapshot (separate
change, ADR 063) is the backstop that recovers anything a flagged run missed, and it also
catches upstream deletions or edits. A one-time manual full snapshot of the whole feed
(225,699 readings, 2022-05-01 to 2026-10-06, every field, SHA-256 manifest) was taken on
2026-10-06 and kept outside the repo, with an off-site copy in Trisha's private Drive.

**Real-specimen check.** Live fetch with the new field list: the server returned all 14
fields; `mode: all` captured 120 of 120 perimeter readings, each with numeric values,
labels, and the full raw record.

## Addendum 2026-10-06 (hourly): capture about every hour

**Why.** The daily run (8am ET) captures every reading since its last poll, so a reading
could sit in GFL's feed for up to about 24 hours before we held a copy. If the source
changed or removed a reading inside that window, the original would never be seen.
Trisha chose hourly saves.

**Decision.** `gfl_air_hourly_capture.py`, run by `.github/workflows/gfl-air-hourly-capture.yml`
at 23 minutes past each hour (GitHub's cron often runs late, so in practice every 1-2
hours). It saves every new perimeter reading to the same app-only Drive folder in the same
capture format, named `gfl-air-capture-<date>-oid<max>-h.json` (the `-h` keeps its files
distinct from the daily run's, so neither silently replaces the other), reusing
`_capture_row` and `_write_capture`.
No alerts and no Sheet writes. Its cursor is derived from Drive (the highest `oid<N>` among
existing capture file names, listing the last 3 days first and widening only if nothing is
found), so it shares no state with the daily run and a failed hour is simply retried by the
next. Both jobs' files count toward the cursor, and a reading may appear in two files
(harmless). With no capture file visible at all (a new folder or a rotated OAuth client),
the job saves the source's newest rows at once so a cursor exists from then on. A large
backlog is captured oldest-first, `max_readings_per_run` (5,000) per run.

**Failure policy.** A failed hour logs and exits 0, because the next hour retries and the
daily run still captures everything since its own Sheet cursor and exits 1 on any capture
gap. Runs starting in four UTC hours (03, 09, 15, 21) exit 1 on failure, so a persistent
problem sends a few GitHub failure emails a day, and a late or dropped scheduled run cannot
hide it for a whole day. Source OBJECTIDs going backwards (a table reset that would make
the cursor miss new readings) and new rows that are not perimeter readings both count as
failures. A missing Drive configuration always exits 1.

**Residual.** A source-side reinsert that renumbers every OBJECTID would make this job copy
the whole table again, 5,000 readings per run, until it catches up (duplicates, no loss).
The monthly snapshot reports such a reinsert (ADR 063).

**Real-specimen check.** With a cursor three hours behind the live feed and the upload
stubbed out, the job fetched and captured 15 readings (MS-2 to MS-6, 3 hours; MS-1 is
silent), each with all 14 source fields in `raw`.
