# ADR 018 — Stream I: EGLE MMD Open Data watch

Date: 2026-07-21
Status: accepted
Builds on: ADR 012 (page-watch scope: alert + Sheet row, no Drive), ADR 015/017
(snapshot-diff watch idiom), ADR 014 (ArcGIS query idiom; the silent-stall
failure class)

## Context

EGLE's Materials Management Division publishes its statewide facility registry
as a **keyless ArcGIS REST MapServer**
(`gisagoegle.state.mi.us/arcgis/rest/services/EGLE/MmdOpenData/MapServer`) —
nine layers (Materials Management Facilities master, Compost, Scrap Tire,
E-recyclers, Digesters, Transfer Stations, **Part 115 Landfills**, Part 111
TSDFs, Recycling Establishments). Trisha found the service 2026-07-21 and asked
for it to be recorded as a data source and automated.

Recon (2026-07-21, live queries) established:

- **Layer 0 is the master (facility, module) table** — it carries every
  module's record for a wdsid, so the per-module layers never need separate
  queries. All layers share one schema.
- **wdsid 475946 (Arbor Hills Landfill, Inc)** carries TWO records: the
  SolidWaste landfill record (`facilitytype` "Type II MSW Landfill",
  `disposalareastatus` **"Active - Accepting"**, plus a per-facility EGLE
  landfill page link) and a **map-hidden** (`show=0`) Utilization/CMPST record
  (`compoststatus` "Accepting from public", **registration expires
  2030-08-05**). Hidden-from-the-map but present-in-the-data is exactly why
  this watch queries the data, not the map.
- **wdsid 465941 (Arbor Hills Compost Area)** — a separate WDS record for the
  compost/expansion parcel — is **absent from the service entirely**.
- ArcGIS reports failures as HTTP 200 + `{"error": ...}`; query responses
  carry a `fields` array (a schema trip-wire) and an `exceededTransferLimit`
  flag (a truncation trip-wire).

Why watch it: this is the **state's own registry view** of the facility. A
`disposalareastatus` flip, a Part 115 status change, the compost registration
lapsing/renewing, the hidden record surfacing, or 465941 **appearing** in the
service at all (the state starting to track the expansion parcel) is early,
citable signal in the airspace (R1) and MMP-expansion fights. Statuses change
rarely, so the watch is near-silent in steady state.

## Decision

### 1. One query, layer 0 only, one watched item per wdsid

`mmd_client.fetch_records` issues ONE `wdsid IN (...)` query against layer 0
for every configured wdsid; `mmd_watcher` splits the shared result into one
item per wdsid (`mmd:475946`, `mmd:465941`) — the rop_watcher one-fetch-many-
items shape. Per-module layers are redundant with layer 0 and are not queried.

### 2. An empty record set is a valid snapshot

For 465941, "absent from the service" IS the baseline; the record APPEARING is
the change (note: "facility NOW APPEARS … the state has started tracking it").
Mirrors Stream H's notice:N2688 mention trip-wire. No special-casing beyond
the summarizer's absent↔present notes.

### 3. Canonical record excludes OID and lat/long; keeps `show`

`OID` is server-assigned and renumbers on a service republish — including it
would false-alert every watched wdsid at once. The coordinates are EGLE-side
interpolations (churn-prone display data — the ADR 012 cache-buster lesson).
`show` is kept: a hidden record becoming visible (or vice versa) is signal.
Date fields (epoch ms) are converted to UTC `YYYY-MM-DD` so snapshots are
human-readable in the Sheet and hash-stable. Records sort by the FULL field
tuple (the ADR 017 partial-key lesson); diffs are full-record multiset diffs
(Counter), so a row can never be lost to a key collision, and every canonical
field is printed in the ADDED/REMOVED lines.

### 4. Fetch failures transient; structural breaks always loud

`MmdFetchError` (network / non-200 / non-JSON / ArcGIS `error` payload) —
skip-and-warn if every wdsid has a baseline, loud exit 1 if any doesn't
(activation-time blocks must surface). `MmdParseError` (no `features`,
`exceededTransferLimit`, schema missing a canonical field) is **always loud**
regardless of baseline status — a service reorganization persists across runs,
and going quiet would hide it forever (the ADR 014 silent-stall class; same
split as Stream H's RopParseError).

### 5. Alert + Sheet row only; no Drive; recipients Trisha-only to start

Same scope call as ADR 012/017: the deliverable is the alert + the append-only
`MMD Watch` tab row (which carries the full snapshot JSON — durable record and
diff state in one). `mmd.recipients` ships scoped to Trisha (Meeting Watch
precedent for a brand-new stream); deleting the override sends to the full
Conservancy `alert_recipients` once the alert copy has been seen in the wild.

### 6. Ships enabled

Unlike the overnight-coder new-source gate (ships `enabled: false`, human
flips), this build was **interactively directed by Trisha** ("automate for
monitor to use it", 2026-07-21) with her present — the same basis on which
Stream F shipped live. Safe because the first run baselines every item
silently and steady-state alert volume is ~zero. `enabled: false` remains the
pause lever (state survives in the tab).

## Consequences / residual risks (accepted)

- **A persistent fetch failure after baseline goes quiet** (skip-and-warn every
  run) — the same accepted residual as Stream H; a liveness-style guard is a
  possible follow-on for both.
- **Adding a field to `RECORD_FIELDS` re-hashes every snapshot** → one
  "changed" alert per wdsid on the next run (visible, reviewable, then quiet).
- **EGLE could re-encode dates or re-type fields** → shows as a one-time
  visible diff (str()-normalization means no crash), not a silent drift.
- **The watch sees only what the registry publishes** — a real-world status
  change EGLE doesn't record here won't fire; this stream complements (never
  replaces) WDS Stream C and the document streams.

## Alternatives considered

- **Watch the per-module layers (1, 6, …) individually** — redundant: layer 0
  carries every module's record; more queries, same information, more schema
  surface to drift.
- **Include OID / coordinates in the snapshot** — rejected: republish/
  re-interpolation churn would fire false alerts (the exact PFAS cache-buster
  failure ADR 012 was built around).
- **Poll the whole Part 115 layer for statewide changes** — out of scope; this
  monitor is Arbor-Hills-scoped, and statewide churn would swamp the signal.
- **Route through egle_doc_parser** — not applicable; a structured-API status
  source, no documents (same posture as Streams E/F/H — the Decode base stays
  domain-agnostic).

## Activation

Ships `mmd.enabled: true` (Trisha's interactive direction, above). First
enabled run baselines both wdsids silently — 465941's baseline is the empty
set. Pause = flip `enabled: false` (tab state survives); resume re-diffs
against the last recorded snapshots.
