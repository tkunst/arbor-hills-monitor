# ADR 019 — Stream J: EGLE RIDE / Part 201 + UST status watch (RRDOpenData ArcGIS)

Date: 2026-07-23
Status: accepted
Builds on: ADR 012 (page-watch scope: alert + Sheet row, no Drive), ADR 015/017
(snapshot-diff watch idiom), ADR 014 (ArcGIS query idiom; the silent-stall
failure class), ADR 018 (the direct template — a keyless
`gisagoegle.state.mi.us` ArcGIS status watch)

## Context

EGLE tracks Part 201 contaminated-site remediation status for the Arbor Hills
area (Salem Landfill and its neighbors) and the GFL Part 211 UST facility
through RIDE (Remediation Information Data Exchange). RIDE's own web
application is an **Angular SPA behind a login — auth-walled, no anonymous
document API** (confirmed by the overnight-worker's recon, queue item #69,
2026-07-15). But EGLE separately publishes the underlying per-site **status**
summary as a **keyless ArcGIS REST MapServer**
(`gisagoegle.state.mi.us/arcgis/rest/services/EGLE/RRDOpenData/MapServer`) —
the same host and query idiom as Stream I's MMD watch, here with two layers:

- **Layer 0** — Part 201 remediation sites, key field `SiteID`. The 5 Arbor
  Hills-area sites: Salem Landfill (81000033), Arbor Hills - East (81000004),
  7667 Chubb Rd (81000835), 7941 Salem Rd (81000840), MITC Corridor
  (82008712).
- **Layer 1** — Part 211 underground storage tanks, key field `FacilityID`.
  The GFL Environmental USA UST at 7811 Chubb Rd (00040223).

This build's own re-confirmation (2026-07-23, live queries, the mandatory
real-specimen check the `enabled: false` gate requires before any new-source
build) found all 6 records still return with the documented fields, matching
worker #69's recon values exactly (e.g. MITC Corridor's `RiskCondition` "Risks
Controlled-Interim", the GFL UST's `Open_Release` 0). It also surfaced two
`SiteName` values carrying trailing whitespace in the live service
(`"7667 Chubb Rd "`, `"7941 Salem Road  "`) — canonicalization strips this
(see Decision 3), same as MMD's field-normalization posture.

Why watch it: this is the **state's own registry view** of contaminated-site
risk (R5 — water quality / groundwater). A `RiskCondition` flip (e.g. "Risks
Present and Require Action in Short-term" → "Risks Controlled-Interim"), a
`Contaminants` list changing, or a new `Open_Release` on the GFL UST is early,
citable signal for the case file. Statuses change rarely, so this watch is
near-silent in steady state.

## Decision

### 1. Two queries (one per layer), one watched item per record

`ride_client.fetch_site_records`/`fetch_ust_records` each issue ONE
`<key field> IN (...)` query against their respective layer for every
configured id; `ride_watcher` derives one item per id — `ride:81000033`
through `ride:82008712` for the sites, `ride:00040223` for the UST — the same
one-fetch-many-items shape as MMD/ROP. Unlike MMD (a single layer, numeric
`wdsid`), RRDOpenData's key fields are TEXT (`FacilityID` carries a leading
zero, `'00040223'`), so the `IN (...)` clause values are single-quoted and
quote-doubled rather than `int()`-coerced.

### 2. Explicit `outFields`, never `outFields=*`; `returnGeometry=false`

Same ADR 018 lesson, applied slightly more strictly: rather than fetching
every field and dropping OID/coordinates in canonicalization (MMD's
approach), the RIDE client's `outFields` list is the watched-field set
itself — `OID` and geometry are never fetched at all. This also keeps
`ProjectManaager` (the schema's own typo, preserved only in code comments,
never watched) out of the response entirely: a project-manager reassignment
is admin churn, not remediation signal, and would false-alert the same way
OID renumbering would.

### 3. Canonical record: `RiskCondition` primary, `Contaminants`/`Open_Release` secondary

- **Layer 0:** `SiteID, SiteName, RiskCondition, Contaminants, LastUpdated`.
- **Layer 1:** `FacilityID, FacilityName, RiskCondition, Open_Release, LastUpdated`.

`LastUpdated` (epoch ms) converts to UTC `YYYY-MM-DD` so snapshots are
human-readable in the Sheet and hash-stable. Every string field is
`.strip()`-normalized (the live service pads some `SiteName` values with
trailing whitespace, discovered during this build's live re-check). Records
sort by the FULL field tuple (the ADR 018 partial-key lesson); diffs are full-
record multiset diffs (`Counter`), so a record can never be lost to a key
collision, and every canonical field is printed in the ADDED/REMOVED lines.

### 4. Fetch failures transient (per layer); structural breaks always loud

`RideFetchError` (network / non-200 / non-JSON / ArcGIS `error` payload) —
skip-and-warn if every item derived from that layer already has a baseline,
loud exit 1 if any doesn't (activation-time blocks must surface). This is
evaluated **per layer independently**: a Layer-1 (UST) fetch failure doesn't
block Layer-0 (sites) processing and vice versa, mirroring ROP's per-source
independence. `RideParseError` (no `features`, `exceededTransferLimit`,
schema missing a canonical field) is **always loud** regardless of baseline
status — a service reorganization persists across runs, and going quiet would
hide it forever (the ADR 014 silent-stall class; same split as MMD/ROP).

### 5. Alert + Sheet row only; no Drive; recipients Trisha-only to start

Same scope call as ADR 012/017/018: the deliverable is the alert + the
append-only `RIDE Watch` tab row (which carries the full snapshot JSON —
durable record and diff state in one). `ride.recipients` ships scoped to
Trisha (Meeting Watch/MMD precedent for a brand-new stream); deleting the
override sends to the full Conservancy `alert_recipients` once the alert copy
has been seen in the wild.

### 6. Ships disabled

Unlike Stream I (interactively directed live by Trisha), this is an
**unattended overnight-coder build** against a brand-new external source, so
it ships `ride.enabled: false` per the overnight-coder new-source gate. The
live feasibility re-check (Decision context above) is not a substitute for
the human activation step — it only confirms the client can be built safely,
not that Trisha has reviewed the watched fields and alert copy. Flipping it
on is a separate, later, human step; no secret needs provisioning (keyless).

## Consequences / residual risks (accepted)

- **A persistent fetch failure after baseline goes quiet** (skip-and-warn
  every run, per layer) — the same accepted residual as Streams H/I; a
  liveness-style guard is a possible follow-on. The loud "structural" split
  only covers breaks that still return ArcGIS-shaped JSON — a
  decommission/redirect/bot wall is a fetch failure and lands here, so this
  residual is the likeliest silent-death mode.
- **`ride:<id>` keys share one namespace across both layers.** A `SiteID` and
  a `FacilityID` colliding would merge two unrelated items' snapshot history
  under one key. Checked against the current data: site IDs are 8-digit
  numbers (`8100xxxx`/`8200xxxx`); the UST facility ID is `00040223` — no
  overlap today. Accepted per the handoff's pinned `ride:<ID>` naming
  (matching MMD/ROP's flat key style) rather than pre-emptively namespacing
  by layer; a future site/facility id colliding would need a naming fix, not
  a design one.
- **`Contaminants` is a free-text list from EGLE** — a reordering or
  rewording (not a substantive change) would still hash-differ and fire a
  "changed" alert. Same class of risk MMD accepts for its string fields;
  the record is printed in full in the alert body so a human can tell a
  cosmetic edit from a real one at a glance.
- **Adding a field to `LAYER0_FIELDS`/`LAYER1_FIELDS` re-hashes every
  snapshot** → one "changed" alert per item on the next run (visible,
  reviewable, then quiet).
- **The watch sees only what RRDOpenData publishes** — a real-world status
  change EGLE doesn't record here won't fire; this stream complements (never
  replaces) the document streams and WDS.

## Alternatives considered

- **Poll RIDE's web application directly** — rejected per #69: auth-walled
  Angular SPA, no anonymous document API.
- **One query per site/facility instead of one `IN (...)` per layer** — more
  requests for the same information; rejected for the same reason MMD queries
  all its wdsids in one call.
- **Fetch `outFields=*` and drop OID/geometry in canonicalization (MMD's
  approach)** — considered, but explicit `outFields` is simpler here (no
  fields to drop after the fact) and matches the handoff's pinned approach;
  either would produce the same canonical record.
- **Include `ProjectManaager` in the canonical record** — rejected: admin
  reassignment churn, not remediation signal (same rationale as excluding
  OID/coordinates).
- **Route through `egle_doc_parser`** — not applicable; a structured-API
  status source, no documents (same posture as Streams E/F/H/I — the Decode
  base stays domain-agnostic).

## Activation

Ships `ride.enabled: false`. Activation: review the watched fields and alert
copy, then flip `enabled: true` in `config.yml` (no secret to provision —
keyless, same as MMD/ROP). First enabled run baselines all 6 items silently.
Pause = flip back to `enabled: false` (tab state survives); resume re-diffs
against the last recorded snapshots.

## Addendum 2026-09-28 — UST 00038889 added; the "no anonymous document API" premise superseded

### Watched set: `00038889` joins `00040223`

`ride.facility_ids` now also carries Layer-1 FacilityID **`00038889`**. This ADR
states only what the registry itself says about it (live layer-1 query,
2026-09-28): `FacilityName` "Arbor Hills Landfill Inc", `RegulatoryProgram` 213,
`ReleaseStatus` "Closed", `Total_Release` 1, `Closed_Release` 1, `Open_Release` 0,
`HighestClassification` "Class 4", `RiskCondition` "No Longer A Facility",
`Total_Tank` 2, `Active_Tank` 0, `LastUpdated` 2024-04-26. The layer gives no
release or closure date. It is watched because the overnight-coder handoff
(2026-09-28), working from EGLE FOIA request E614007-080526, found it missing from
the watch list; a change to `Open_Release`, `RiskCondition` or `LastUpdated` —
Layer 1's own diffed fields (`ride_client.LAYER1_FIELDS`) — would otherwise go
unseen. This does NOT cover a `ReleaseStatus` flip (e.g. Closed -> Open) or the
tank/release counters, since Layer 1 exposes those fields but this watch's
`facility_ids` query never fetches them. The watch makes no other claim about
the facility.

`RegulatoryProgram` shows why no label asserts a program: Layer 1 mixes 211 and
213 records (`00040223`, GFL, is 211; `00038889` is 213). The item label prefix
is therefore the neutral "RIDE UST registry — Facility <id>" (it was "RIDE Part
211 UST"), and each facility is named by its registry `FacilityName` only. Labels
are not part of the snapshot hash, so the wording change alerts on nothing.

The shipped `ride:` stream was already `enabled: true`, so this was a live-path
edit. Real-specimen verification:

- The live layer-1 query returns both records with the fields the canonical view
  already reads (above); `00040223` is unchanged.
- The watcher's first-sighting path records a silent `baseline` row for an id with
  no prior row (pinned by
  `test_adding_a_new_ust_to_an_established_watch_baselines_only_it_silently`, whose
  Layer-1 fake answers with one record and then two, as the real service does).
- A real production run of the workflow on the PR branch (`workflow_dispatch`)
  reported `0 changed, 1 baselined, 6 unchanged` and sent no email.

The item key stays `ride:<FacilityID>`; `00038889` cannot collide with any Part 201
`SiteID` (those are `8100xxxx`/`8200xxxx`).

### Superseded premise: RIDE's public page does list documents anonymously

The Context section above cites worker #69's 7/2026 recon: RIDE is "behind a
login" with "no anonymous document API". For **documents** that is superseded.
Re-checked 2026-09-28 with a headless browser — **no credentials, no login
click**:

- RIDE's public Inventory of Facilities page (`/RIDE/inventory-of-facilities/
  facilities?...programNum=<id>`) gives every anonymous visitor a "Public User"
  session on its own (`GET /RIDE/Home/GetAppSettings` returns `userName`
  "Public1").
- Its own front end then calls JSON endpoints that a plain HTTP client can replay
  once it holds the session cookies (a `POST` without them returns 405):
  `POST api/Location/GetFacilitiesTable` (program number -> `locationId`),
  `POST api/ContentManagerFile/GetContentManagerFilesForLocationFilesTable`
  (the location's file list; 37 files for 81000004, each with a unique `uri`),
  and `POST api/ContentManagerFile/GetFileContents` (returns the PDF).
- The page's own disclaimer says records maintained by RRD are available for
  download through RIDE.

Whether RIDE's app also exposes *status* anonymously was **not investigated**;
this stream keeps using RRDOpenData, unchanged. A document watch built on those
endpoints is a separate stream (different source, key — file `uri`, not `SiteID` —
and sensitivity: file titles can include resident street addresses) proposed in
its own PR, not in this change.

### Accepted behaviours

- **First run:** `_all_baselined()` now includes `ride:00038889`, which has no
  baseline until its first successful run. If that very first run hits a
  transient Layer-1 fetch error, it exits loud (red) instead of skip-and-warn — the
  documented activation-time rule — and self-heals on the next good run (pinned by
  `test_ust_fetch_error_is_skip_when_fully_baselined_but_loud_for_a_new_id`).
- **Date-only alerts:** the snapshot hash includes `LastUpdated`, so a routine EGLE
  re-stamp of a dormant record emails a date-only change; the body prints the old
  and new values, so it is recognizable at a glance. Pre-existing for every watched
  item.
- **Alert boilerplate:** the change email's closing sentence ("early, citable R5
  signal") is generic to every item, including a record whose registry status is
  "No Longer A Facility". Left unchanged here (recipient is Trisha only); a future
  copy pass could make it status-aware.
- **"NO LONGER LISTED":** if EGLE ever drops `00038889` from the layer, the watch
  sends an accurate alert — the designed behaviour, not a false positive.
