# CLAUDE.md — arbor-hills-monitor

Context for any AI agent (or human) working in this repo.

## What this is

Automated monitoring of EGLE regulatory filings for the Arbor Hills Landfill
(SRN N2688), built for the Conservancy Initiative (a non-technical community
advocacy group). It backfills the ~754 existing documents and watches for new
ones, classifies each against a risk register (R1–R8), keeps a full-text-
searchable PDF archive + a Google Sheet case file, and sends alerts.

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
  renewal folder's file list, and the statewide ROP_Public_Notice.pdf's N2688
  mention. Stdlib + fitz; keeps `egle_doc_parser` untouched. See ADR 017.
- `rop_watcher.py` — Stream H: snapshots + diffs the three ROP sources' five
  derived items vs. the `ROP Watch` tab, alerting on any change — a facility's
  task/permit status advancing, a new file in the N2688 folder, or N2688
  appearing in the statewide public notice (the 30-day-comment trip-wire).
  Gated on `rop.enabled`. See ADR 017.
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

## Tests

`pytest -q` — hermetic (synthetic PDFs, all network/API mocked, no secrets).
Run before every commit.

## Before first deploy

See `README.md` → Deploy checklist and `scripts/setup_gcp.md`. The MMPC document
archive (Mirror D) is **already activated** — the `GOAUTH_MMPC_FOLDER_ID` secret
was set 2026-07-11 and `mmpc_archive.enabled: true` is live on `main` (ADR 010).
No activation step remains for it.
