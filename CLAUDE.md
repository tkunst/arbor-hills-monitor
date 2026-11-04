# CLAUDE.md — arbor-hills-monitor

Context for any AI agent (or human) working in this repo.

## What this is

An independent, automated monitor of EGLE regulatory filings for the Arbor
Hills Landfill (SRN N2688). It backfills the ~754 existing documents and
watches for new ones, classifies each against a risk register (R1–R10), keeps a
full-text-searchable PDF archive + a Google Sheet case file, and sends alerts.

**Editorial posture — UPDATED (Trisha's direction, 2026-08-30): "accuracy over
posturing."** Strict neutrality is no longer absolute. The brand is *accuracy*,
not the *appearance* of neutrality, and the two layers are held to different
bars:

- **The automated data layer** — the alerts, the Sunday digest, the public
  case-file Sheet, the findings feed, anything the pipeline emits without a human
  in the loop — stays rigorously factual and **source-labeled** (EGLE, the
  operator, township/county records, community groups alike, each labeled by
  source). This is the machine whose whole value is being unarguable; keep it
  provenance-agnostic and never overstate. Source-labeling here is *accuracy*,
  not neutrality, so it stays regardless of the posture change.
- **The editorial / visualization layer** — the pages Trisha authors (the
  thermal map, analysis pages, briefs, hosted visualizations) — MAY carry a
  clear community-concern **point of view**, *provided every checkable claim
  holds up to the refutation gate* (Trisha's blind-refutation process) and every
  number says where it came from. A viewpoint page with one sloppy or overstated
  claim is the real failure mode; a sourced viewpoint is fine.

This supersedes the strict-neutrality rule below for authored/editorial content,
but not the data layer. See `documents/arbor-hills/arbor-hills-voice-guide.md` in
Lotext for the voice guide (update pending to match this ruling).

*(Prior direction, 2026-08-21, kept for history — now relaxed per above:) the
monitor is a neutral, provenance-agnostic data-scientist newsletter — not the
voice or an arm of any advocacy organization; do not attribute the project to,
or describe it as built for, any single advocacy group in public-facing text.*

All EGLE data is public regulatory data. **Tier 0** project (solo dev, no PII,
external users but no sensitive data). Public repo.

## Architecture (one line each)

The full per-module notes (history, edge cases, why things are the way they are)
are in **`docs/architecture.md`**. Read a module's entry there before changing the
module, and update that entry in the same PR.

**Core pipeline (EGLE nSITE documents)**

- `config_loader.py` — loads `config.yml`; `load_dotenv()` for local scripts.
- `nsite_client.py` — EGLE nSITE API: session, list, download (PDF-parsing stack imported lazily).
- `egle_doc_parser.py` — the reusable PDF → `ParsedDoc` classifier (the Decode base); urgency floor, classifier timeout.
- `ocr_util.py` — shared OCR: `classify`, `ocr_in_place`, `needs_ocr`, `ensure_searchable` (ADR 069).
- `kdp_check.py` — deterministic temperature consistency check on the model's key data point / summary (ADR 067).
- `retry_policy.py` — transient vs. permanent per-document errors, so infra blips never strike a filing.
- `risk_register.py` — R1–R10 + signal keywords (single source of truth).
- `document_titles.py` — generic nSITE titles → display names (ADR 065).
- `name_check.py` — no-LLM personal-name / internal-content check for anything public.
- `poison_doc_extractor.py` — non-PDF nSITE sources (.msg, .docx, images, zips) → PDF (ADR 011).
- `poison_stub.py` — terminal stub row for an unreadable document.
- `well_aliases.py` — well-ID rulings from `data/well_registry.json` (ADR 068).
- `woi_router.py` / `woi_table_parser.py` — WOI Status Report detection + exhaustive table parse; table-built headline (ADR 005).
- `neshap_table_parser.py` — NESHAP semi-annual report dataset extractor; not on the live path (ADR 033).
- `well_watch.py` — named single-well temperature trip-wires (ADR 058).
- `doc_pipeline.py` — `process_one_doc()`, the one per-document pipeline behind watcher and backfill.
- `doc_pipeline_liveness.py` — stall guard for the document pipeline.
- `watcher.py` — daily new-document check + alerts (+ WDS Stream C); anti-stampede cap.
- `backfill.py` — nightly batch of 50, resumable; FORCE_REPROCESS read cache.
- **Facility scope (ADR 064)** — `core` vs. `scope: related` facilities; related docs go only to "Related Documents".

**Storage, archive, alerts**

- `drive_client.py` — Google Drive + Sheets API (service account).
- `archive_client.py` — Drive access as Trisha via OAuth for the durable mirrors (ADR 007).
- `archiver.py` — nSITE PDF → Drive mirror, inline + nightly catch-up (ADR 007).
- `snapshot_exporter.py` — snapshot → immutable Drive copies for four snapshot-diff watchers (ADR 072).
- `sheet_writer.py` — the Sheet tabs; routing/fan-out is pure and tested.
- `dataset_provenance.py` — the public Sheet's `Dataset Provenance` tab + annotation columns.
- `email_alerts.py` — urgent alerts + weekly digest; urgency decision is pure.
- `owner_notices.py` — owner-only weekly email + same-day owner alerts (ADR 070).
- `review_flags.py` — "needs human review" log on the private Sheet (ADR 067 amendment).
- `report_tracker.py` / `report_tracker_seed.py` — expected reports vs. arrivals, private Sheet, and its seed data (ADR 071).
- `upcoming.py` — reads the private Sheet's hand-kept `Upcoming` tab for the Sunday digest.
- `findings_feed.py` / `public_comment_feed.py` — pure renderers for the public Findings and Public Comment pages.
- `fulltext_search.py` + `scripts/build_fulltext_cache.py` / `build_fulltext_index.py` — full-text search over the Mirror PDFs (Pagefind bundle, built in CI, never committed; ADR 062 addendum).
- `scripts/backfill_metric_taxonomy.py` — one-shot ADR 034 Measurements reclassify; dry run by default.

**County / meeting sources**

- `mmpc_client.py` — CivicClerk JSON API (MMPC, BOC, DPW).
- `mmpc_archiver.py` — Mirror D: MMPC agenda/minutes PDFs → Drive (ADR 010).
- `civicclerk_archiver.py` — DPW + BOC agenda/minutes → Drive (ADR 037).
- `civicclerk_watcher.py` — Stream F: meeting change-watch + keyword scan (ADR 015, 036).

**Other watched sources (Streams C–V)**

- `wds_watcher.py` / `wds_client.py` / `wds_archiver.py` — Stream C: EGLE WDS collections (ADR 043).
- `pfas_client.py` / `pfas_watcher.py` — EGLE PFAS page-watch (ADR 012).
- `gfl_air_client.py` / `gfl_air_watcher.py` — Stream E: GFL perimeter-air feed, alerts, screening episodes (ADR 014, 039).
- `gfl_air_episodes.py` / `gfl_air_emails.py` / `gfl_air_liveness.py` — Stream E's pure halves; station roster.
- `gfl_air_hourly_capture.py` — hourly durable capture of the air feed (ADR 026).
- `gfl_feed_snapshot.py` — monthly full snapshot of the air feed + change detection (ADR 063).
- `ridgewood_client.py` / `ridgewood_archiver.py` — Stream G: Ridge Wood H2S reports (ADR 016).
- `ridgewood_reverify.py` — monthly re-verify of every archived Ridge Wood report (ADR 066).
- `rop_client.py` / `rop_watcher.py` — Stream H: ROP (Title V) permit watch (ADR 017).
- `mmd_client.py` / `mmd_watcher.py` — Stream I: EGLE MMD Open Data (ADR 018).
- `ride_client.py` / `ride_watcher.py` — Stream J: RIDE Part 201 + UST status (ADR 019).
- `ride_docs_client.py` / `ride_docs_watcher.py` — Stream T: RIDE anonymous document listing, private only (ADR 061).
- `nsite_submissions_watcher.py` — Stream K: nSITE Submissions profile; tiered polling + shared site registry (ADR 020–022).
- `nsite_watch_common.py` — shared scaffolding for the six nSITE profile watchers below.
- `nsite_violations_watcher.py` — Stream L: Violations (ADR 023).
- `nsite_compliance_actions_watcher.py` — Stream M: Compliance Actions (ADR 028).
- `nsite_evaluations_watcher.py` — Stream N: Evaluations (ADR 029).
- `nsite_permits_watcher.py` — Stream O: Permits (ADR 030).
- `nsite_complaints_watcher.py` — Stream P: Complaints (ADR 031).
- `nsite_public_notices_watcher.py` — Stream Q: Active Public Notices (ADR 032).
- `pfas_pws_client.py` / `pfas_pws_watcher.py` — Stream R: public water supply PFAS sampling (ADR 042).
- `gfl_info_site_client.py` / `gfl_info_site_watcher.py` — Stream S: the operator's informational website (ADR 041).
- `govqa_client.py` / `govqa_watcher.py` — Stream U: EGLE's public FOIA archive, private only (ADR 059).
- `mpart_client.py` / `mpart_watcher.py` — Stream V: MPART PFAS open-data layers (ADR 060).
- `egle_newsroom_watcher.py` — EGLE newsroom watch.

## Activation status (generated — do not hand-edit the table)

The table below is generated by `scripts/gen_stream_status.py` from every
`enabled`/`*_enabled` flag in `config.yml`, at any nesting depth, and pinned
by `tests/test_stream_status.py` (same pattern as `tests/test_sitemap.py` for
`site/sitemap.xml`). Regenerate with `python3 scripts/gen_stream_status.py`
and paste its output between the markers verbatim — a hand edit here drifts
exactly the way the hand-written per-module notes did. Those notes, now in
`docs/architecture.md`, still carry each flag's activation *date* and the human
decision behind it; this table only answers "is it live right now."

<!-- stream-status:begin -->
| Config flag | Live |
| --- | --- |
| `data_lifecycle.snapshot_export.enabled` | yes |
| `wds.enabled` | yes |
| `mmpc_archive.enabled` | yes |
| `civicclerk_archive.enabled` | yes |
| `pfas.enabled` | yes |
| `gfl_air.enabled` | yes |
| `gfl_air.capture.enabled` | yes |
| `gfl_air.hourly_capture.enabled` | yes |
| `gfl_feed_snapshot.enabled` | yes |
| `civicclerk_watch.enabled` | yes |
| `civicclerk_watch.keyword_scan.enabled` | yes |
| `ridgewood.enabled` | yes |
| `ridgewood.reverify_enabled` | yes |
| `rop.enabled` | yes |
| `mmd.enabled` | yes |
| `ride.enabled` | yes |
| `ride_docs.enabled` | yes |
| `nsite_submissions.enabled` | yes |
| `nsite_violations.enabled` | yes |
| `nsite_compliance_actions.enabled` | yes |
| `nsite_evaluations.enabled` | yes |
| `nsite_permits.enabled` | yes |
| `nsite_complaints.enabled` | no |
| `nsite_public_notices.enabled` | yes |
| `gfl_info_site.enabled` | yes |
| `pfas_pws.enabled` | yes |
| `govqa.enabled` | yes |
| `mpart.enabled` | yes |
| `owner_weekly.enabled` | yes |
| `owner_weekly.enforcement_alert_enabled` | yes |
| `report_tracking.enabled` | yes |
| `report_tracking.review_email_enabled` | no |
| `egle_newsroom.enabled` | yes |
| `fulltext_search.enabled` | yes |
<!-- stream-status:end -->

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
- **Never treat hardware/transport QA as data QA.** A source's own self-report
  that a sensor was "powered, communicating, within specified ranges" (or a file
  transfer completing, or a request returning HTTP 200) proves the pipe worked —
  it proves nothing about whether the payload is correct. An electrochemical
  sensor can degrade and report a stable, plausible, *wrong* value while every
  device-level self-check still passes; this is exactly the GFL perimeter MS-2/
  MS-5 failure mode (frozen for 14+ months, never flagged by any operational
  self-report, only caught by examining the raw feed for zero variance) — see
  `analysis/perimeter-h2s-sensor-anomaly-2026-09-05/` in Lotext. Trust an
  affirmative self-disclosure of a real event (a source naming "the pump failed,"
  "27 hours of data lost") — that channel is reliable. Never trust the *absence*
  of one as evidence the payload itself was ever independently verified; that
  requires a reference-level check (a calibration/audit against a known value,
  or a content-level statistical check such as unexpected-zero-variance
  detection), not a transport/operational-status flag. Ridge Wood's own monthly
  reports illustrate the gap directly: "Audits" reads verbatim identically —
  "No audits of the network were conducted" — across all 70 months on file,
  zero exceptions, as of 2026-10-12.
- **Don't inherit the old scraper's `doc_date == today` filter.** Backfill needs
  full history. (Already handled in `nsite_client`.)

## Assumptions (never make these)

- **Never assume a data point was read during the period in the report's
  title.** Reports reprint older readings (back tables, historical trend
  pages, carried-forward rows), so every reading's own date is the date that
  counts, not the report's period or its filing date. Real example: the N2688
  "HOV WOI Quarterly Status Report H1 2024" (nSITE doc `-6975595422894935530`,
  filed 2026-10-08) has as its table-wide peak AHWW312R at 158.3°F dated
  **2023-08-29**, a year before H1 2024. Anything that says "max", "latest",
  "this period" or "current" (headlines, alerts, the digest, the Sheet) must
  carry the reading's own date, and must not describe an out-of-period reading
  as the period's value.

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
  It also holds the GovQA / RRD Documents watch tabs and the owner-review tabs
  (Parser Review Flags, Owner Notices, Hand-Curated Routing Log), so
  `daily.yml`, `backfill.yml` and `route-hand-curated.yml` all receive
  `GSHEET_ID_PRIVATE`.
- **State tabs vs. data tabs — and state reads must raise.** A tab any
  watcher DIFFS or BASELINES off of (`_state`, `_meta`, `_wds_seen`,
  `GFL Air` — its OBJECTID cursor read (`gfl_air_cursor`), its two liveness
  reads (the As-Of-max read and the column-N stale-marker read), and its
  column-O episode-state read are each their own bare `.get()`/`.execute()`
  with no try/except, so a TRANSPORT failure (429/5xx/network) on any of them
  propagates too — though the episode-state read is deliberately,
  separately fail-SAFE on a CONTENT problem: a missing/blank/unparseable
  cell or a legacy JSON-array value returns `{}` (empty-episodes) rather
  than raising, by design, since the risk of mis-parsing old state is
  silently swallowing a real screening/closeout alert, not a stale baseline
  — and every other `<X> Watch` snapshot tab: ROP/MMD/RIDE/PFAS/Public Water
  Supply PFAS/GFL Info Site/Meeting/MPART Data/Submissions/Violations/
  Compliance Actions/Evaluations/Permits/Complaints/Public Notices/
  EGLE Newsroom, plus the private-Sheet `GovQA Archive Watch`/
  `RRD Documents`) must RAISE on a genuine TRANSPORT read failure, never
  swallow it to `[]` — a swallowed transient error misreads as "nothing seen
  yet" and silently re-baselines over real state (the ADR 018/023 "spurious
  baseline becomes the state" failure). Enforced two ways in the code:
  `sheet_writer._tab_rows_strict()` (added when PR #135 fixed this across
  most state/snapshot tabs) for the tabs it covers, and — for tabs with
  their own reader predating that helper or never built on it
  (`read_mpart_rows`, `read_govqa_rows`, `read_rrd_docs_rows`,
  `egle_news_seen_urls` (its own docstring states the identical "deliberately
  does NOT use `_tab_rows`" reasoning), the GFL Air reads named above, and
  the six nSITE-profile readers for Violations/Compliance Actions/
  Evaluations/Permits/Complaints/Public Notices — seven of the nSITE PROFILE
  watchers import `nsite_client` in total, Submissions included, but
  Submissions' OWN state read does use `_tab_rows_strict`, so it's not one
  of these six bespoke ones) — a bespoke per-tab read with no
  try/except at all, which raises by construction. The LENIENT `_tab_rows()`
  (returns `[]` on ANY failure, including a genuinely absent tab) is correct
  ONLY for append-only DEDUP-INDEX tabs whose worst-case failure is redundant
  work, never a lost alert or an overwritten record — `_tab_rows`'s own
  docstring is the audited, authoritative list of its remaining callers
  (`archived_doc_ids`/`archived_doc_links`, `mmpc_archived_file_ids`/
  `civicclerk_archived_file_ids`, `wds_historical_collections_dumped`,
  `ridgewood_last_event`/`ridgewood_multi_link_flagged_months`). A few tabs
  are HYBRID — simultaneously the run's state (last row per key = current)
  AND a durable append-only record (every row is also data):
  `GFL Info Site Watch`, `Ridge Wood Reports`, `RRD Documents`,
  `EGLE Newsroom Watch` — these get the strict/raising contract above.
  `Evidence by Risk` and `WDS Evidence by Risk` are a DIFFERENT exception
  worth naming: they're read back too, but only by
  `rebuild_risk_register_tab()`/`rebuild_all_evidence_tab()` to recompute two
  derived ROLL-UP tabs (`Risk Register`, `All Evidence by Risk`) — never to
  detect what's new — and that read already raises (a bare read for
  `Evidence by Risk`, `_tab_rows_strict()` for `WDS Evidence by Risk`). The
  private-Sheet owner-review tabs (Parser Review Flags, Owner Notices,
  Hand-Curated Routing Log) are append-only DATA tabs, never diffed or
  baselined; the owner weekly email reads them with `_tab_rows_strict()` so a
  failed read skips the email instead of reporting a false all-clear. Every
  OTHER tab (New/Historical/Related Documents, Measurements,
  Compliance Deadlines, Risk Register, All Evidence by Risk, Hand-Curated
  Files, WOI Well Summary, Perimeter Action-Level Episodes,
  `WDS New Documents`, `WDS Page Snapshots`) is pure DATA, never
  diffed/baselined and never read back as run state
  (`WDS New Documents`/`WDS Page Snapshots` confirmed by grep to have no
  reader anywhere in `sheet_writer.py` — the WDS snapshot dedup this might
  look like it should feed actually runs through `_meta`'s
  `wds_snapshot_hashes` instead). One tab lives OUTSIDE `sheet_writer.py`
  entirely and so is outside every bucket above by construction: the
  private Sheet's hand-maintained `Upcoming` tab (defined in `upcoming.py`,
  read by `fetch_upcoming()`) is read LENIENTLY by design — it returns
  `[]` on any read error so the Sunday digest drops that section rather
  than failing the run; nothing diffs or baselines off it, so a false-empty
  read costs one week's missing digest section, never a lost alert or an
  overwritten record.

## Tests

`pytest -q` — hermetic (synthetic PDFs, all network/API mocked, no secrets).
Run before every commit.

## Before first deploy

See `README.md` → Deploy checklist and `scripts/setup_gcp.md`. The MMPC document
archive (Mirror D) is **already activated** — the `GOAUTH_MMPC_FOLDER_ID` secret
was set 2026-07-11 and `mmpc_archive.enabled: true` is live on `main` (ADR 010).
No activation step remains for it.
