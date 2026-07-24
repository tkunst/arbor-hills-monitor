# Overnight-coder handoff — watch EGLE's Part 201 / UST status feed for the Arbor Hills sites

*Staged 2026-07-23. New data source (`arbor-hills-multiple-data-sources.md` row 5).
Read `docs/overnight-coder.md` first — this is a goal handed to that loop, not a new
procedure. New poller against a live external system, so it ships `enabled: false`.
**Unlike the GFL-air handoff, the feasibility recon is already done** — the
overnight-worker ran it (queue item #69, verdict PARTIALLY POLLABLE) and its findings
are folded in below. Do NOT re-spike RIDE; confirm #69 still holds with one live query
(see "Feasibility gate") and build.*

## Invocation

Point the loop at this file (or paste the **Goal** below as the goal). Branch name
suggestion: `ride-part201`.

The recon lives at `Cowork-claude/documents/arbor-hills/draft/DRAFT-ride-part201-feasibility-2026-07-15.md`
(outside this repo). Everything you need from it — the endpoint, the two layers, the
site IDs, the field schema, the query pattern — is reproduced below, so you should not
need to open it. If you do want the source, it is there.

---

## Goal

EGLE tracks Part 201 contaminated-site remediation status for the Arbor Hills area
(Salem Landfill and its neighbors) and the GFL Part 211 UST facility. The **RIDE web
application itself is auth-walled and not machine-accessible** (#69 confirmed: an
Angular SPA behind a login; no anonymous document API). But EGLE **separately publishes
a keyless, public ArcGIS REST service** that exposes the per-site **status** summary —
risk condition, contaminant classes, and a genuine last-updated timestamp — for exactly
these sites.

**Build a status-change watch (`ride_client.py` + `ride_watcher.py`) against that ArcGIS
service** — the same shape as the MMD watch (Stream I, ADR 018): one keyless query per
layer, snapshot-diff per site, alert plus an append-only Sheet row when a site's status
changes. It ships **disabled** until Trisha flips it on.

This is a **status watch, not a measurement poller.** The service returns per-site status
strings (`RiskCondition`, `Contaminants`), not numeric readings — so it does NOT touch
the Measurement schema or `egle_doc_parser.py`. Model it on `mmd_client`/`mmd_watcher`
(also a keyless `gisagoegle.state.mi.us` ArcGIS status watch), not on `gfl_air_client`.

## Why this is a good overnight goal

- The repo already has the exact template: **`mmd_client.py` + `mmd_watcher.py` +
  `mmd-watch.yml` + ADR 018** — a keyless ArcGIS snapshot-diff status watch against the
  same `gisagoegle.state.mi.us` host, shipped one-fetch-many-items behind `mmd.enabled`.
  This is that pattern pointed at a different service (RRDOpenData) with two layers.
- The source is **public and keyless** — no ESRI token, no session, no new secret.
- It ships `enabled: false`, so it cannot affect the live monitor until a deliberate
  human step. Safe to build and merge autonomously.
- Statuses change rarely, so the watch is near-silent in steady state — a `RiskCondition`
  flip (e.g. "Risks Controlled-Interim" → "Require Action in Short-term") or a new
  `Open_Release` on the GFL UST is early, citable R5 signal.

## This is a NEW SOURCE — `enabled: false`

Per `docs/overnight-coder.md` Step 3, a brand-new poller against a live external system
ships with its config flag `enabled: false` (the Stream C/D/E/I pattern). Flipping it on
is a separate, later, human step this loop never takes, regardless of how green CI is.
Mocked-green is enough to *merge disabled*; it is not enough to go live.

There is no existing `ride:` block, so you are creating it fresh at `enabled: false` —
there is no stale-flag risk here. (Still: per Step 3, the enabled/disabled state you
build is read from `config.yml`, never assumed. You are writing the block, so write it
`false`; do not flip any flag.)

## Feasibility gate (do this FIRST — one query, not a full spike)

The feasibility spike is already done (#69). What remains is the mandatory
real-specimen check that the `enabled: false` gate still requires — cheap here because
the service is keyless. Before writing the client, run **one** live query and confirm
the six Arbor Hills records still return:

- Layer 0 (Part 201), the 5 `SiteID`s below, and
- Layer 1 (UST), the GFL `FacilityID` below.

If they still return records with the documented fields, proceed to the build. **If the
service is gone, auth-walled now, or returns none of the six records, that is a Step-3
stop** — commit a short recon-update doc, open a draft PR explaining the dealbreaker, and
leave it. Do not force a speculative build against a feed that no longer answers. (This
is the same feasibility-gated posture as the GFL-air handoff; the only difference is #69
already did the discovery, so this is a confirm, not a hunt.)

## The service (from #69 — do not re-discover)

- **Base:** `https://gisagoegle.state.mi.us/arcgis/rest/services/EGLE/RRDOpenData/MapServer`
- **Auth:** none (public, keyless). It is a MapServer; the `/<layer>/query` endpoint works
  identically to a FeatureServer for reads. ArcGIS reports failures as **HTTP 200 +
  `{"error": ...}`** (same trip-wire as MMD) — a non-200, non-JSON, or `error` payload is a
  fetch failure, not data.

### Layer 0 — Part 201 sites (key field `SiteID`)

Watched site IDs:

| SiteID | SiteName | RiskCondition (as of #69) |
|--------|----------|---------------------------|
| 81000033 | Salem Landfill | Risks Present and Require Action in Short-term |
| 81000004 | Arbor Hills - East | Risks Present and Require Action in Short-term |
| 81000835 | 7667 Chubb Rd | Risks Present and Require Action in Short-term |
| 81000840 | 7941 Salem Rd | Risks Present and Require Action in Long-term |
| 82008712 | MITC Corridor | Risks Controlled-Interim |

Query: `where=SiteID IN ('81000033','81000004','81000835','81000840','82008712')`.

### Layer 1 — GFL Part 211 UST (key field `FacilityID`)

GFL Environmental USA, LLC, 7811 Chubb Rd — `FacilityID = '00040223'`,
`RegulatoryProgram = '211'`. As of #69: `RiskCondition` "No Known Risks", `Open_Release`
0, `Total_Tank` 3. (GFL is a UST site, so it is in Layer 1, NOT Layer 0.)

Query: `where=FacilityID = '00040223'`.

## Approach (pinned — don't re-litigate the design)

1. **New, isolated client — `ride_client.py`.** Model it on `mmd_client.py`: a keyless
   fetch that issues ONE `where` query per layer and returns parsed records. Two layers,
   two queries (Layer 0 keyed on `SiteID`, Layer 1 on `FacilityID`). **Do NOT touch
   `egle_doc_parser.py`** — this is a structured-API status source, no documents (the
   Decode base stays domain-agnostic, same posture as Streams E/F/H/I).

2. **Explicit `outFields`, never `outFields=*`; `returnGeometry=false`.** This is how you
   keep `OBJECTID` and geometry out of the snapshot without special-casing — ADR 018's
   OID/coordinate false-alert lesson (a server-side republish renumbers OID and would
   false-alert every site at once). Fetch only the watched fields:
   - **Layer 0:** `SiteID, SiteName, RiskCondition, Contaminants, LastUpdated`
   - **Layer 1:** `FacilityID, FacilityName, RiskCondition, Open_Release, LastUpdated`
   - **Exclude `ProjectManaager`** (yes, the schema typo is real — preserve it if you ever
     reference the field, but do not watch it): a project-manager reassignment is admin
     churn, not remediation signal, and would false-alert. `RiskCondition` is the primary
     watch field; `Contaminants` and `Open_Release` are secondary signal.
   - Convert `LastUpdated` (epoch ms) → UTC `YYYY-MM-DD` so snapshots are human-readable
     in the Sheet and hash-stable (ADR 018 §3).

3. **Snapshot-diff per site — `ride_watcher.py`.** Model on `mmd_watcher.py` /
   `rop_watcher.py`: the one-fetch-many-items shape. One watched item per record —
   `ride:81000033`, … , `ride:00040223`. Diff the canonical record (full-field multiset
   diff, ADR 018 §3 — not a partial key) against the last snapshot in `_state`; write the
   Sheet row BEFORE the state entry (the crash-safe invariant); fire an alert when a
   site's canonical record changes. Include the full snapshot JSON in the Sheet row
   (durable record + diff state in one, exactly like MMD).

4. **Fetch failures transient; structural breaks always loud** (ADR 018 §4). A
   network/non-200/non-JSON/ArcGIS-`error` response is a `RideFetchError` — skip-and-warn
   if every watched item has a baseline, loud exit 1 if any doesn't. A response missing
   `features`, flagging `exceededTransferLimit`, or missing a watched field is a
   `RideParseError` — **always loud** regardless of baseline (a service reorg persists
   across runs; going quiet would hide it forever — the ADR 014 silent-stall class).

5. **Config — `ride: { enabled: false, ... }`** in `config.yml`, copying the `mmd` block's
   shape (the `enabled` comment, the `recipients:` Trisha-only override with its
   "delete to send to the full list once seen in the wild" note). Put the site IDs and the
   UST facility ID in config (commented, like `mmd.wdsids`), not hardcoded in the client.
   The stream is a **no-op until `enabled: true` is on `main`** — a deliberate human step,
   not this loop's.

6. **Workflow — `ride-watch.yml`** mirroring `mmd-watch.yml`, with the **same
   `concurrency` group** so it never races the shared `_state` file. It runs on schedule
   but is a no-op while `enabled: false`.

7. **Tests (hermetic, mocked, no secrets — `pytest -q` green).** Mock the two ArcGIS
   responses with a **small synthetic fixture** (a few features per layer, in
   `tests/conftest.py` — never commit a real captured response; CLAUDE.md forbids
   committed data files and `data-guard` CI enforces it). Cover: the client parses both
   layers → canonical records; `OBJECTID`/geometry are absent from the snapshot even when
   present in the mocked response (the `outFields`/`returnGeometry` guarantee); the watcher
   writes new baselines and skips unchanged records; a `RiskCondition` change fires an
   alert; a `RideParseError` exits loud even with baselines present; and the
   `enabled: false` gate makes the whole thing a no-op.

8. **Docs (same PR — Step 8 requires ADRs reflect final state).** New **ADR 019** —
   "Stream J: EGLE RIDE / Part 201 + UST status watch (RRDOpenData ArcGIS)": the source,
   the two-layer split and why (SiteID vs FacilityID), the canonical-record field choice
   (why `ProjectManaager`/OID/geometry are excluded), the RiskCondition-primary watch, the
   fetch-vs-parse failure split, and the `enabled: false` activation step. Add the stream
   to `README.md` (Streams table + a residual-risk line: "rides EGLE's RRDOpenData ArcGIS
   service; if EGLE reorganizes it the fetch fails loudly"). Update `CLAUDE.md`'s
   architecture list. Regenerate topology.

## Existing pieces you're building on (don't rebuild)

- **`mmd_client.py` / `mmd_watcher.py` / `mmd-watch.yml` / ADR 018** — the exact template:
  a keyless `gisagoegle.state.mi.us` ArcGIS snapshot-diff status watch, one-fetch-many-
  items, OID/coords excluded, loud structural break, Trisha-only recipients. Yours differs
  only in: two layers (MMD uses layer 0 only), a different service (RRDOpenData vs
  MmdOpenData), and it ships `enabled: false` (MMD shipped enabled because Trisha directed
  it live; this is an autonomous overnight build, so it ships disabled).
- **`rop_watcher.py`** — the one-fetch-many-items summarizer shape (one query, split into
  per-item snapshots) that MMD itself borrowed.
- **`sheet_writer.py`** — add an append-only `RIDE Watch` tab beside the others (snapshot
  JSON per row), the way MMD added its tab; don't invent a new schema pattern.
- **`email_alerts.py` / the recipients override** — reuse the per-watch `recipients`
  override (Trisha-only to start), same as `mmd.recipients` / Meeting Watch.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: the service is gone / now auth-walled / returns none of the six
  records.** Detection: the one-query feasibility gate above. Recovery: Step-3 stop with a
  doc-PR — a legitimate outcome, not a failed night.
- **Manageable: EGLE reorganizes the RRDOpenData schema or renames a field.** Mitigation:
  `RideParseError` fires loud (aborts the run) rather than silently archiving nothing —
  same posture as MMD/ROP; log the record count per poll so a drop to zero is visible.
- **Manageable: OID/coordinate churn false-alerts every site.** Mitigation: explicit
  `outFields` + `returnGeometry=false` keep them out of the canonical record entirely (ADR
  018's exact lesson) — designed out, not patched.
- **Manageable: admin churn (`ProjectManaager` reassignment, a display-coordinate nudge)
  reads as a status change.** Mitigation: those fields are not in the watched set; only
  RiskCondition/Contaminants/Open_Release/LastUpdated are.
- **Source honesty:** this is EGLE's own regulatory registry (not an operator self-report),
  so it is authoritative status — label the Sheet row as EGLE RRDOpenData so provenance is
  clear, but no self-report caveat is needed (contrast Stream E's GFL-self-reported air).
- **Residual risk (accept + note in PR):** after baseline, a persistent fetch failure goes
  skip-and-warn quiet (the same accepted residual as Streams H/I; a decommission/redirect
  is a fetch failure and lands here) — no auto-recovery beyond the per-poll count log + the
  `enabled: false` disable lever.

## Definition of done

Green `pytest -q`; the feasibility gate's result recorded in ADR 019 (confirmed live, or —
if it failed — a draft-PR Step-3 stop); a new `ride` stream that is a **no-op while
`enabled: false`** (client + watcher + workflow + config + tests); README/CLAUDE.md/
topology updated in the same PR; PR merged per overnight-coder Step 8 with a closing
comment that states plainly **(a)** that the six Arbor Hills records were confirmed live
and **(b)** that going live is a separate Trisha step (keyless — nothing to provision, just
set `ride.enabled: true` after she reviews the watched fields). If the feasibility gate
fails, the "done" state is instead a **draft PR** with the recon-update doc and no client
code.

## Then Step 9 (after a successful merge)

Archive `coder:ride-part201` from
`Cowork-claude/documents/overnight-coder-queue.md` to `overnight-coder-archive.md`, and
release the worker pin: in `Cowork-claude/documents/overnight-queue.md`, change item #69's
`[coder-prep -> coder:ride-part201]` tag to
`[coder-prep CONSUMED by coder:ride-part201 2026-07-2X]`. Commit those Lotext files locally
by explicit path, never push (per overnight-coder.md Step 9).
