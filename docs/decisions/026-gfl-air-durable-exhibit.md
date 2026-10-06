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
