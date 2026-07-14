# Session: 2026-07-13 — Overnight-coder/worker pipeline staging (GFL handoff, worker queue, handoffs rename)

One-line: staged the next overnight-coder goal (GFL ArcGIS perimeter air, Row 6)
as a handoff + kickoff sheet, filled the overnight-worker queue with 9 Arbor Hills
analysis/recon tasks under a new coder-dependency pin convention, corrected the
queue ordering for the coder-runs-before-worker reality, and renamed
`docs/handoffs/` to `docs/overnight-coder-handoffs/`.

Base at session start: `5ecd4ce`. Follow-on to the same-day WOI extraction session
(`docs/session-2026-07-13-woi-live-extraction-and-measurements-quality.md`).

## 1. Candidate sweep (overnight-coder vs overnight-worker)

- Reviewed `docs/roadmap.md`, the Cowork data-sources doc
  (`arbor-hills-multiple-data-sources.md`, "Need to get / not yet automated"
  table), and the README residual risks.
- Split the candidates three ways: **coder** (new-source builds, both
  feasibility-gated: GFL ArcGIS air Row 6, RIDE Part 201 Row 5); **worker**
  (analysis/research/draft: the three Measurements design drafts the roadmap calls
  for, the PFAS-HMP FOIA, vision-classification scoping); and **ops / Trisha** (the
  14 poison-doc `RETRY_DOC_IDS` backfill, still pending; the roadmap builds pending
  review).

## 2. GFL ArcGIS handoff (Row 6) — this repo

- New `docs/overnight-coder-handoffs/gfl-air-arcgis.md`: a self-contained
  overnight-coder goal spec to build a `gfl_air_client.py` poller against GFL's
  public ArcGIS FeatureServer perimeter-air feed (the first source that would give
  real fenceline readings, R3 odor / R4 air, not just documents).
- New-source shape: ships `gfl_air.enabled:false` (the Stream C/D pattern),
  feasibility-gated — path A (queryable numeric readings) builds an ADR-004
  measurement poller with exceedance alerts; path B falls back to a content-hash
  page-watch; path C is a valid Step-3 stop. New ADR 014 + tests + workflow scoped
  in the handoff. Adversarial-review section folded in.
- Cut-and-paste kickoff sheet staged in Cowork
  (`documents/arbor-hills/overnight-coder-paste-2026-07-14-gfl-air.md`).

## 3. Overnight-worker queue (Cowork) — 9 Arbor Hills items + a pin convention

- Verified the live case-file Google Sheet is publicly readable (gviz CSV export,
  no auth), so the three Measurements design drafts can pull it directly.
- Staged items 66-74: metric-taxonomy draft, location_type draft, well-ID
  alias-map draft, RIDE recon, PFAS-HMP FOIA draft, vision scoping, WOI Q1-2024
  re-fetch, basis-integrity audit, and the GFL recon (fallback).
- New reusable convention on the worker queue: a `[coder-prep]` / `[coder: none]`
  / `[coder: fallback]` pin tag on each item, so concurrent sessions do not drop an
  item an upcoming coder build depends on.

## 4. Timing correction — the coder runs BEFORE the worker

- The overnight-coder is a manual bedtime kickoff (~11pm); the worker is a ~1:30am
  timed job. So worker output only ever reaches the NEXT night's coder (after
  Trisha's daytime review), never the same night's, and the coder always
  self-spikes.
- Reordered the worker queue so the three design drafts lead — they are the
  uniquely valuable output (the coder should not invent a curated well-ID alias map
  or a metric taxonomy autonomously). Recons demoted; the GFL recon became a
  redundant `[coder: fallback]` because tonight's coder does the GFL spike itself.
- The GFL handoff and the kickoff sheet were updated so the coder self-spikes and
  does not wait for a recon file that will not exist during its run.

## 5. Renamed docs/handoffs to docs/overnight-coder-handoffs

- Makes the folder name say what it holds: overnight-CODER goal specs, distinct
  from the Lotext overnight-WORKER (a distinction `docs/overnight-coder.md` already
  draws in prose). Both handoffs moved (`woi-auto-routing.md`, `gfl-air-arcgis.md`).
- Updated every reference: a `backfill.py` comment and two prior session docs in
  this repo, plus the Cowork queue and kickoff sheet. The `backfill.yml` comment
  points to a different (Cowork) `handoffs/` file and was left as-is.

## State / pending

- **This repo:** this session doc + the GFL handoff + the rename are committed to
  `main`. No code behavior change (comment + docs only).
- **Cowork (separate local repo):** the worker queue, the comms-log pocket ask
  (Q11, the corrupted Q1-2024 WOI clean-copy request), and the kickoff sheet are
  committed there locally (that repo is never pushed).
- **Next overnight-coder goal:** GFL ArcGIS (kickoff sheet ready). Tonight's worker
  (1:30am) does the three design drafts.
- **Pending (Trisha):** review the design drafts once produced (they unblock the
  roadmap builds), and decide the following coder goal (RIDE vs. a reviewed roadmap
  build).
