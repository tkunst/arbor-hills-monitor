# Overnight-coder handoff — poll GFL's perimeter air-monitoring ArcGIS feed (Stream E)

*Staged 2026-07-13. New data source (data-sources doc row 6). Read
`docs/overnight-coder.md` first — this is a goal handed to that loop, not a new
procedure. Unlike the WOI handoff, this is a **brand-new poller against a live
external system**, so it ships `enabled: false` and is **feasibility-gated**: if
the spike shows the feed isn't pollable, stopping with a doc-PR is a valid,
successful outcome (overnight-coder Step 3), not a failure.*

## Invocation

Point the loop at this file (or paste the **Goal** below as the `/loop` goal).
Branch name suggestion: `gfl-air-arcgis`.

**Do the feasibility spike yourself — there is no pre-staged recon for your run.**
The overnight-worker is a ~1:30am timed job; this coder is kicked off manually at
bedtime and runs BEFORE it, so no worker recon exists when you start. Do the spike
per "Feasibility spike" below before writing any client code. (A
`DRAFT-gfl-arcgis-air-feasibility-*.md` under
`Cowork-claude/documents/arbor-hills/draft/` would only exist if a PRIOR night's
worker had produced one; if you happen to find one, use its endpoint findings, but
do not wait for or expect it.)

---

## Goal

GFL self-reports **perimeter air monitoring** for the Arbor Hills Landfill on a
public ArcGIS dashboard:
`https://barrgisonline.maps.arcgis.com/apps/dashboards/ac08a03f8018403ba6ae1c2243c00e99`.
This is the R3 (odor) / R4 (air quality) evidence stream, and it is potentially
the **highest-value source not yet automated**, because — unlike everything else
the monitor watches — it is *real perimeter readings* (H2S, VOC, PM, wind, odor),
not documents. If those readings are exceeding action levels at the fence line,
that is direct, quantitative R3/R4 evidence.

ArcGIS dashboards are almost always backed by a queryable **FeatureServer REST
endpoint** (`.../FeatureServer/0/query?where=1=1&outFields=*&f=json`), not just a
rendered page. **Build a poller (`gfl_air_client.py`) that pulls that feed on a
schedule, routes the readings into the case file, and alerts on exceedances** —
the same shape as WDS (Stream C) and MMPC (Stream D), shipped **disabled** until
Trisha flips it on.

## Why this is a good overnight goal

- The repo already has the full template for a new gated stream: `wds_client.py`
  (API client) + `wds_watcher.py` (daily poll + alerts) + `wds_archiver.py`
  (snapshots), all behind `wds.enabled`. This is that pattern against a simpler,
  auth-free source.
- The source is **public and keyless** — an ArcGIS FeatureServer query needs no
  credentials (simpler than nSITE's session or the Drive service account). No new
  secret to provision.
- It ships `enabled: false`, so it cannot affect the live monitor until a
  deliberate human step. That makes it safe to build and merge autonomously even
  though the spike touches an external system.

## This is a NEW SOURCE — `enabled: false`, and feasibility-gated

Per `docs/overnight-coder.md` Step 3, a brand-new poller against a live external
system:

1. **Ships with its config flag `enabled: false`** (the Stream C / D pattern).
   Flipping it on is a separate, later, human step this loop never takes,
   regardless of how green CI is. Mocked-green is enough to *merge disabled*; it
   is not enough to go live.
2. **Is feasibility-gated.** If the spike shows the feed can't actually be polled
   (no open service behind the dashboard, or it returns no Arbor Hills readings),
   that is a **Step-3 stop**: commit the recon findings as a doc, open a draft PR
   explaining the dealbreaker, and leave it. Do **not** force a speculative
   implementation to reach a green build.

## Feasibility spike (do this FIRST)

Confirm, before writing the real client, that the feed is genuinely pollable:

- Find the backing service. Inspect the dashboard's network calls or the ArcGIS
  item's REST services directory for a `FeatureServer`/`MapServer`. GET only.
- Query it: `.../FeatureServer/<layer>/query?where=1=1&outFields=*&f=json`.
  Confirm it returns **Arbor Hills perimeter readings** (not some unrelated
  layer), and capture: the service URL(s), the field schema (which pollutants,
  units, station/location field), the **timestamp field** (so the poller can
  detect new readings), and the update cadence.
- **Decision the spike settles (branch the build on it):**
  - **(A) Queryable numeric readings** → build the measurement poller below (the
    high-value path).
  - **(B) A rendered dashboard with no open service, or a service with no
    machine-readable readings** → fall back to a **content-hash page-watch** on
    the dashboard (reuse the `pfas_client` / `pfas_watcher` pattern, ADR 012) so
    at least a change fires an alert; note in the ADR that structured extraction
    wasn't feasible.
  - **(C) No pollable surface at all** (login-walled, tiled images only) →
    Step-3 stop, doc-PR, done.

De-risk the spike in the scratchpad; don't commit sample responses (see Tests).

## Approach (pinned — don't re-litigate the design decisions)

1. **New, isolated client — `gfl_air_client.py`.** Model it on `wds_client.py`:
   an `enabled`-gated fetch that queries the FeatureServer and returns parsed
   readings. **Do NOT touch `egle_doc_parser.py`** — this is a structured-API
   source, it never goes through the document parser (the Decode base stays
   domain-agnostic). Keep all ArcGIS/GFL specifics in this new module.

2. **Route readings as ADR-004 measurements (path A).** Each perimeter reading
   becomes a `Measurement` with `basis="measured"` (it is a real reading, never a
   permitted ceiling — the ADR-004 / CLAUDE.md invariant). Map the ArcGIS fields
   to the existing measurement schema (`well_id` → the perimeter station id, a new
   `metric` per pollutant — coordinate with the metric-taxonomy roadmap draft, see
   below; `location_type` = `perimeter_station`). Write via the existing
   `sheet_writer.measurement_rows` / `TAB_MEASUREMENTS` path — do not invent a new
   schema. A small `GFL Air` summary tab (latest reading per station × pollutant)
   is the human-facing artifact, matching how WOI got a summary tab.

3. **Daily poll + alerts — `gfl_air_watcher.py`.** Model on `wds_watcher.py`:
   pull the feed, diff against the last-seen timestamp in `_state`, write new
   readings (Sheet row BEFORE state entry — the crash-safe invariant), and fire an
   urgent alert when a reading crosses an action level. **Keep alert thresholds
   conservative and config-driven**, and treat "what counts as an exceedance" as a
   value Trisha confirms when she enables the stream — a wrong threshold is a
   false-alarm generator, so default to the published health/odor action levels
   and note the source in the ADR.

4. **Config — `gfl_air: { enabled: false }`** in `config.yml`, matching the
   `wds` / `mmpc_archive` / `pfas` blocks (copy their comment: the 3-step
   "provision → set enabled: true → commit" activation note). The stream is a
   **no-op until `enabled: true` is on `main`** — a deliberate human step, not
   this loop's.

5. **Workflow.** Add a scheduled GitHub Actions workflow (`gfl-air.yml`) mirroring
   `pfas-watch.yml` / the WDS job, with the same `concurrency` group so it never
   races the state file. It runs on schedule but is a no-op while `enabled:false`.

6. **Tests (hermetic, mocked, no secrets — `pytest -q` green).** Mock the ArcGIS
   HTTP response with a **small synthetic fixture** (a few features, in
   `tests/conftest.py` — never commit a real captured response; CLAUDE.md forbids
   committed data files and `data-guard` CI enforces it). Cover: the client parses
   features → readings; field-mapping to `Measurement` (basis=measured); the
   watcher writes new readings and skips already-seen timestamps; an over-threshold
   reading fires `is_urgent`; and the `enabled:false` gate makes the whole thing a
   no-op. If path B (page-watch), test the content-hash normalizer instead, like
   `tests/test_pfas.py`.

7. **Docs (same PR — Step 8 requires ADRs reflect final state).** New ADR (next
   number, **014** — "GFL perimeter air, ArcGIS FeatureServer, Stream E"):
   the source, the spike's finding (path A/B/C), the schema mapping, the alert
   thresholds + their source, and the `enabled:false` activation step. Add the
   stream to `README.md` (Streams table + a residual-risk line: "rides GFL's
   undocumented ArcGIS service; if Barr changes it the fetch fails loudly").
   Update `CLAUDE.md`'s architecture list. Regenerate topology.

## Existing pieces you're building on (don't rebuild)

- `wds_client.py` / `wds_watcher.py` / `wds_archiver.py` + the `wds.enabled` gate
  — the exact template for a new gated stream (ADR 009).
- `pfas_client.py` / `pfas_watcher.py` — the content-hash page-watch fallback
  (path B) if there's no structured feed (ADR 012).
- `sheet_writer.measurement_rows` + `TAB_MEASUREMENTS` — the measurement write
  path already exists; add a summary tab beside it, don't reinvent the schema.
- `is_urgent` (`email_alerts.py`) — already alerts off measured readings and
  excludes permitted ceilings; feed it the new readings, no change needed there.
- The metric-taxonomy + `location_type` roadmap drafts (staged by the
  overnight-worker as `DRAFT-measurements-metric-taxonomy-*.md` and
  `DRAFT-measurements-location-type-*.md` under
  `Cowork-claude/documents/arbor-hills/draft/`) — if those are reviewed by build
  time, use their vocabulary for the pollutant `metric` and
  `location_type=perimeter_station` so this source lands taxonomy-clean rather
  than dumping everything into `other`.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: the dashboard has no open service (path C).** Detection: the
  spike. Recovery: Step-3 stop with a doc-PR — a legitimate outcome, not a failed
  night. This is why the goal is feasibility-gated up front.
- **Manageable: the ArcGIS field schema drifts / Barr renames the layer.**
  Mitigation: the fetch fails loudly (aborts the run) rather than silently
  archiving nothing — same posture as MMPC's CivicClerk dependency; log the
  reading count per poll so a drop to zero is visible, not silent.
- **Manageable: wrong alert threshold floods or starves.** Mitigation: thresholds
  are config-driven and default to published action levels with the source cited
  in the ADR; and the stream ships `enabled:false`, so Trisha reviews the
  thresholds before any alert can fire live.
- **Manageable: GFL-reported data is the operator's own self-report.** Mitigation:
  label the source honestly in the Sheet (Facility/Document Name make clear it is
  GFL-self-reported perimeter monitoring, not an EGLE measurement), the same way
  operator claims are attributed as claims elsewhere.
- **Residual risk (accept + note in PR):** a future ArcGIS format change
  under-extracts until the per-poll reading-count log flags it; no auto-recovery
  beyond that signal + the `enabled:false` / disable lever.

## Definition of done

Green `pytest -q`; the spike's finding (path A/B/C) recorded in ADR 014; a new
`gfl_air` stream that is a **no-op while `enabled:false`** (client + watcher +
workflow + config + tests); README/CLAUDE.md/topology updated in the same PR; PR
merged per overnight-coder Step 8 with a closing comment that states plainly
**(a)** which path the spike landed on and **(b)** that going live is a separate
Trisha step (provision nothing needed — it's keyless — just set `enabled: true`
after she reviews the alert thresholds). If the spike lands on path C, the "done"
state is instead a **draft PR** with the recon doc and no client code.
