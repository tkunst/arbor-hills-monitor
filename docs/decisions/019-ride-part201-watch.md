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
