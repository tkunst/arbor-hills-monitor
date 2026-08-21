# CLAUDE.md — arbor-hills-monitor

Context for any AI agent (or human) working in this repo.

## What this is

An independent, automated monitor of EGLE regulatory filings for the Arbor
Hills Landfill (SRN N2688). It backfills the ~754 existing documents and
watches for new ones, classifies each against a risk register (R1–R8), keeps a
full-text-searchable PDF archive + a Google Sheet case file, and sends alerts.

**Editorial posture (Trisha's direction, 2026-08-21):** the monitor is a
neutral, provenance-agnostic data-scientist newsletter — not the voice or an
arm of any advocacy organization. It takes data from any source with clear
provenance (EGLE, the operator, township/county records, community groups
alike) and labels each item by source. Do not attribute the project itself to,
or describe it as built for, any single advocacy group in public-facing text
(README, this file, code comments, the public Sheet, email/digest content).
See `documents/arbor-hills/arbor-hills-voice-guide.md` in Lotext for the full
voice guide.

All EGLE data is public regulatory data. **Tier 0** project (solo dev, no PII,
external users but no sensitive data). Public repo.

## Architecture (one line each)

- `egle_doc_parser.py` — THE reusable module (the Decode parsing base). PDF →
  `ParsedDoc`. Domain-agnostic: the risk register is passed in, never hardcoded.
- `risk_register.py` — R1–R8 + R8 signal keywords (single source of truth).
- `nsite_client.py` — EGLE nSITE API: session, list, download.
- `drive_client.py` — Google Drive + Sheets API (service account, folder ID).
- `sheet_writer.py` — the four+1 sheet tabs; routing/fan-out is pure & tested.
- `mmpc_client.py` — CivicClerk JSON API: enumerate + fetch MMPC event PDFs.
- `mmpc_archiver.py` — Mirror D: auto-archive MMPC Agenda/Minutes PDFs (ADR 010).
  (The old in-watcher "go check the minutes" reminder was retired; see ADR 013.)
- `email_alerts.py` — SMTP urgent alerts + weekly digest; urgency is pure.
- `backfill.py` — nightly batch of 50, self-terminating, resumable.
- `watcher.py` — daily new-doc check + alerts (+ WDS Stream C when enabled).
- `pfas_client.py` — content-hash normalizer for EGLE's PFAS pages (isolates
  `<main>`, ignores Sitecore theme cache-busters). Pure; stdlib-only.
- `pfas_watcher.py` — daily page-watch: alerts on any change vs. the last
  snapshot in the `PFAS Page Watch` tab. Gated on `pfas.enabled`. See ADR 012.
- `gfl_air_client.py` — Stream E: GFL's public ArcGIS perimeter-air FeatureServer
  (H2S/CH4 fenceline readings). Fetch + pure ADR-004 mapping; structured-API
  source, never goes through `egle_doc_parser`. See ADR 014.
- `gfl_air_watcher.py` — daily poll of the GFL air feed: incremental OBJECTID
  cursor (in the `GFL Air` tab), readings → Measurements (`basis=measured`),
  same-day exceedance alerts via its own classifier. Gated on `gfl_air.enabled`.
  H2S alerts on a rolling per-station 24-hr AVERAGE (`gfl_air.h2s_avg_window_hours`,
  server-side query; matches the 72 ppb 24-hr action level — set `0` to restore the
  instantaneous alert); CH4 stays instantaneous. See ADR 014 decision 4 + addendum.
  A liveness check (`gfl_air.max_stale_days`) alerts once if a poll finds zero new
  readings while the newest is stale — mitigates ADR 014's OBJECTID-reset silent
  stall (the cursor resetting below the stored value → `OBJECTID > cursor` empty
  forever). Marker in the `GFL Air` tab's column N; self-resets on recovery.
  A CH4 WATCH tier (`gfl_air.watch_thresholds`) fires below the action level; if
  `gfl_air.watch_alert_recipients` is set, it emails once per station per
  continuous episode (>=40ppm, re-arms below 40) to that list only — separate
  from, and never mixed into, the full-list exceedance/anomaly email. Episode
  marker in column O (fail-safe: unreadable/failed-write → more alerting, never
  suppression). Empty `watch_alert_recipients` = display-only (rollback lever).
  See ADR 014's 2026-07-21 addendum.
- `civicclerk_watcher.py` — Stream F: twice-daily change-watch on a hand-picked
  list of MMPC + Washtenaw County BOC meeting events (via `mmpc_client.fetch_event`).
  Snapshots each event's title/date/status/document-set into the `Meeting Watch`
  tab and alerts (Trisha only) on any change. Cadence is a pure function per event
  (`is_due_today`): MMPC every run; BOC weekly + daily in the 3 days before a
  meeting. Alert-only (no Drive). Gated on `civicclerk_watch.enabled`. See ADR 015.
- `ridgewood_client.py` — Stream G: fetch + parse Barr Engineering's monthly Ridge
  Wood Elementary H2S reports. Scrapes the `Files/*.pdf` report links off the public
  page (never constructs a URL), parses `YYYY-MM`, extracts text via fitz, and runs a
  pure, fail-safe + footnote-safe classifier (numeric ≥72 ppb → alert; missing
  all-clear phrase → alert). Stdlib + fitz; keeps `egle_doc_parser` untouched. ADR 016.
- `ridgewood_archiver.py` — Stream G: diff scraped months vs. the `Ridge Wood Reports`
  tab, mirror each new PDF to Drive (optional — the extract+alert safety function
  doesn't depend on it), write the month's max 24-hr average → Measurements
  (`basis=measured`, Barr/EPA-agreement monitor), same-day alert on a stated 72/750 ppb
  exceedance. Modeled on `mmpc_archiver`. Gated on `ridgewood.enabled`. See ADR 016.
- `rop_client.py` — Stream H: fetch + parse for the ROP (air Title V permit)
  watch. EPA ROP Monthly Report CSV rows for N2688/N1504/P1488 (excludes the
  M3333 "Emerald" name-collision by exact SRN match, never by name), the N2688
  renewal folder's file list, and the statewide ROP_Public_Notice.pdf mention
  for EACH target SRN. Stdlib + fitz; keeps `egle_doc_parser` untouched. See ADR 017.
- `rop_watcher.py` — Stream H: snapshots + diffs the three ROP sources' seven
  derived items vs. the `ROP Watch` tab, alerting on any change — a facility's
  task/permit status advancing, a new file in the N2688 folder, or ANY target
  SRN (N2688 / N1504 / P1488) appearing in the statewide public notice (the
  30-day-comment trip-wire). Gated on `rop.enabled`. See ADR 017.
- `mmd_client.py` — Stream I: EGLE MMD Open Data (keyless public ArcGIS
  registry, layer 0). One `wdsid IN (...)` query + canonical record views
  (OID/coords excluded — republish churn; `show` kept — hidden→visible is
  signal; epoch-ms dates → ISO). Fetch-vs-structural error split mirrors
  rop_client. Structured-API source, never goes through `egle_doc_parser`.
  See ADR 018.
- `mmd_watcher.py` — Stream I: daily snapshot-diff of each watched wdsid's
  record set vs. the `MMD Watch` tab — status flips, the hidden compost
  registration changing/surfacing, or a watched wdsid APPEARING in the service
  at all (465941, the expansion-parcel trip-wire; an empty record set is a
  valid baseline). Gated on `mmd.enabled`. See ADR 018.
- `ride_client.py` — Stream J: EGLE RIDE / Part 201 + UST status (keyless public
  ArcGIS RRDOpenData, two layers — RIDE's own web app is auth-walled with no
  anonymous API). One `SiteID IN (...)` / `FacilityID IN (...)` query per layer;
  explicit `outFields` (never `*`) + `returnGeometry=false` keep OID/geometry
  out of the fetch entirely (`ProjectManaager` excluded too — admin churn, not
  signal). Fetch-vs-structural error split mirrors mmd_client. Structured-API
  source, never goes through `egle_doc_parser`. See ADR 019.
- `ride_watcher.py` — Stream J: daily snapshot-diff of each watched Part 201
  site's / Part 211 UST's record vs. the `RIDE Watch` tab — a `RiskCondition`
  flip, a `Contaminants` change, or a new `Open_Release` alerts (R5, water
  quality). Gated on `ride.enabled` (new source; ships `false`). See ADR 019.
- `nsite_submissions_watcher.py` — Stream K: snapshot-diff of every site in
  the `nsite_sites` registry that has a `nsite_submissions.tiers` entry (a
  SEPARATE, wider 19-entry set than the Documents `facilities:` list — ADR
  021) against its nSITE **Submissions**
  profile (application/service-request intake — a sibling profile to
  Documents, added after a JPA never showed up in Documents at all) vs. the
  `Submissions Watch` tab. Keyed on Submission Reference Number (globally
  unique per filing, unlike ROP's task rows): a ref never seen before alerts
  as a brand-new filing, distinctly from an existing ref's status advancing.
  `fetch_site_submissions` (`nsite_client.py`) deliberately RAISES on fetch
  failure rather than swallowing to `[]` like `fetch_site_documents` — this
  watcher diffs the result, so a silent `[]` would misread a fetch outage as
  "everything removed." Also added the WRD Land & Water Interface facility
  (the JPA's own site — EGLE assigns a separate nSITE ID per program area
  even for the same physical facility, same as the RA/AQD split in ADR 011)
  and `AHLI` (a bare duplicate registration with genuine recent activity) to
  `facilities:`. Gated on `nsite_submissions.enabled` (ships `true` — Trisha
  directed this build live and it was verified against a real
  `workflow_dispatch` run before merging, so it skips the overnight-build
  new-source `false` default). See ADR 020.
- **Tiered polling (ADR 021):** each srn in `nsite_submissions.tiers` maps to
  a `daily|biweekly|quarterly` poll cadence — resolving all 19 of Trisha's
  MiEnviro subscriptions to real nSITE IDs surfaced 14 more sites beyond the
  original 5, mostly dormant duplicate/historical registrations. Cadence is
  `_is_due(cadence, srn, today)`, a pure, stateless function that hash-
  staggers sites within a tier and fires across a 3-day window per period
  (not one exact day), so a single missed/failed daily run doesn't blank a
  quarterly site out for a full quarter. The Documents `facilities:` list is
  deliberately untouched by this — only the Submissions watch reads `tiers`.
- **Shared nSITE site registry (ADR 022):** `config.yml`'s top-level
  `nsite_sites` holds the canonical srn/name/id identity for every nSITE site
  this monitor knows about — extracted out of `nsite_submissions.sites`
  before a second profile watcher (`coder:nsite-violations-watch`, queued
  next) could duplicate the same 19 tuples a second time. Each profile-
  specific watch (Submissions today) references it by srn and adds only its
  own `poll` cadence (`nsite_submissions.tiers`); a `tiers` srn missing from
  `nsite_sites` raises loudly (`KeyError`) rather than silently going
  unwatched. Pure refactor — verified behavior-preserving (the resolved
  19-site set is byte-identical pre/post) before merging, since
  `nsite_submissions.enabled: true` is already live.
- `nsite_violations_watcher.py` — Stream L: daily snapshot-diff of EGLE's
  nSITE **Violations** profile (the state's own *enforcement* record — a
  formal out-of-compliance finding, not a filing or a permit status) for
  every srn in `nsite_violations.tiers`, against the `Violations Watch` tab.
  `fetch_site_violations` (`nsite_client.py`) RAISES on fetch failure like
  `fetch_site_submissions` — a swallowed `[]` would read as "every violation
  resolved at once". Item key `viol:<srn>`; `_is_due` is IMPORTED from
  `nsite_submissions_watcher` (never copied — there's an identity test), but
  the TIERS are this profile's own (3/3/13, assigned from observed violation
  counts and recency; Submissions' are 6/6/7). Two findings from the live
  feasibility gate shape the design and should not be "simplified" away:
  (1) the profile has **no unique-ID field** — not one of the eight fields,
  nor any composite, is unique within a site's record set — so the diff is a
  full-record `Counter` **multiset**, and the multiset (not a set) is
  load-bearing because RA's 299 records hold only 108 distinct tuples;
  (2) a Sheets cell caps at 50,000 chars and the Submissions-style
  one-object-per-record snapshot is 130,188 for RA, so the snapshot is
  persisted **run-length counted** (24,884), with `_cell_payload` degrading
  to a digest multiset above `snapshot_char_budget`. `snapshot_hash` is
  always taken over the FULL snapshot, never the truncated payload. Makes no
  severity judgment about which EGLE status is good or bad. Gated on
  `nsite_violations.enabled` (new source; ships `false`). Its workflow lives
  at `.github/workflows/nsite-violations-watch.yml` — moved out of
  `docs/pending-workflows/` when PR #36 merged (the build session's token
  lacked the `workflow` OAuth scope, so the file was parked until a
  scope-bearing SSH push could land it). The 2pm-ET cron runs as a no-op
  until `enabled` is flipped. See ADR 023.
- `nsite_compliance_actions_watcher.py` — Stream M: daily snapshot-diff of
  EGLE's nSITE **Compliance Actions** profile (the formal actions the regulator
  takes in *response* to a violation — Violation Notices, Consent Orders,
  Consent Judgments; the documented other half of the enforcement story
  Stream L watches) for every srn in `nsite_compliance_actions.tiers`, against
  the `Compliance Actions Watch` tab. Item key `ca:<srn>`; a near-verbatim copy
  of the Violations watch (`NsiteStructuralError`, batched RAISING tab read,
  per-site try covering the write, `alerting_is_configured()` gate, imported
  `_is_due`, run-length counted snapshot). The feasibility gate (live, all 5
  non-dormant sites) found the candidate key `cmplActnCmplActnNum` **non-unique**
  (N2688 files one federal case number on two records) → full-record `Counter`
  MULTISET diff, same as Violations — but the ADDED/REMOVED lines lead with the
  action **`num`**, not `category` (which is a bare "Administrative"/"Civil"),
  so a status advance (Issued→Closed) on a known action is legible. Makes no
  severity judgment. Tiers are this profile's own (**2 daily / 4 biweekly / 13
  quarterly**), NOT a copy of Violations' 3/3/13 — N1504 drops to biweekly
  because its two actions are both *Closed*. Gated on
  `nsite_compliance_actions.enabled` (new source; ships `false`). Its workflow
  is parked at `docs/pending-workflows/nsite-compliance-actions-watch.yml` (the
  build token lacked the `workflow` OAuth scope) and must be `git mv`'d into
  `.github/workflows/` before the flag is flipped. See ADR 028.

## Forbidden patterns (do not do these)

- **Never commit PDFs or data files.** No `*.pdf`, `*.csv`, `*.xml`, `*.json`
  (except the allowlisted config in `.gitignore`). Test fixtures synthesize PDFs
  in `tests/conftest.py` — never committed. CI (`data-guard`) enforces this.
- **Never hardcode credentials or secrets.** API keys, SMTP passwords, the
  service-account key → `.env` (local) / GitHub Secrets (CI). CI (`gitleaks`)
  enforces this.
- **Never hardcode local filesystem paths** (no `/Volumes/...`, no
  `CloudStorage/GoogleDrive-...`). Actions runners can't see them — all file I/O
  goes through the Drive API by folder ID. See `docs/decisions/002`.
- **Never conflate a measured reading with a permitted ceiling.** The
  `basis` field on every measurement (`measured` vs `permitted_limit`) is
  load-bearing for credibility. See `docs/decisions/004`.
- **Don't inherit the old scraper's `doc_date == today` filter.** Backfill needs
  full history. (Already handled in `nsite_client`.)

## Invariants

- Write the Sheet row BEFORE the state entry (crash-safe; a kill between them
  re-writes the row, never drops it).
- Sheet tabs are created idempotently (`ensure_tabs`).
- Workflows use a `concurrency` group so two runs never race the state file.
- **Two Sheets, two visibility rules.** `GSHEET_ID` is the case-file Sheet and is
  **public/operator-visible** — never put anything on it you wouldn't hand the
  operator. `GSHEET_ID_PRIVATE` (optional; the Sunday
  digest's "Upcoming" tab, read by `upcoming.py`) is a **separate Sheet shared
  ONLY with the service account and Trisha** — never share it with the operator.
  Keep strategy-flavored key dates on the private Sheet, never the public one.

## Tests

`pytest -q` — hermetic (synthetic PDFs, all network/API mocked, no secrets).
Run before every commit.

## Before first deploy

See `README.md` → Deploy checklist and `scripts/setup_gcp.md`. The MMPC document
archive (Mirror D) is **already activated** — the `GOAUTH_MMPC_FOLDER_ID` secret
was set 2026-07-11 and `mmpc_archive.enabled: true` is live on `main` (ADR 010).
No activation step remains for it.
