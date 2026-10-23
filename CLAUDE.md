# CLAUDE.md — arbor-hills-monitor

Context for any AI agent (or human) working in this repo.

## What this is

An independent, automated monitor of EGLE regulatory filings for the Arbor
Hills Landfill (SRN N2688). It backfills the ~754 existing documents and
watches for new ones, classifies each against a risk register (R1–R8), keeps a
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

- `config_loader.py` — twelve lines: `load_config()` reads this repo's own
  `config.yml` with `yaml.safe_load`. No secrets here (those are env/GitHub
  Secrets only); this is the chokepoint dozens of other modules across the
  repo use to read the non-secret config, including
  `kdp_check.default_reference()`
  (see below) and `scripts/gen_stream_status.py` — though NOT every
  `scripts/gen_*.py` job (`gen_findings_feed.py`/`gen_sitemap.py`/
  `gen_public_comment_feed.py` don't touch it).
- `egle_doc_parser.py` — THE reusable module (the Decode parsing base). PDF →
  `ParsedDoc`. Domain-agnostic in the sense Decode needs: the risk register is
  passed in, never hardcoded. **That claim is not true end-to-end**, though,
  stated plainly rather than refactored here: `parse_document()` takes an
  optional `temperature_reference` kwarg (passed to
  `_enforce_temperature_consistency()` as its own `ref` parameter), but
  no `parse_document()` call site anywhere in this repo (`watcher.py`,
  `backfill.py`, or the `scripts/` one-offs —
  `backfill_document_dates_titles.py`, `smoke_one.py`, and
  `recheck_key_data_points.py`'s own `run_llm()`) ever passes one, so in
  practice it always falls through to `kdp_check.default_reference()`, which
  reads THIS repo's own `config.yml` (`temperature_thresholds`) directly —
  see `kdp_check.py` below for the one narrow exception to "nobody builds a
  ref." (See the `nsite_client.py` entry further down for a SEPARATE
  coupling fact in the same spirit — a different module, not this one — now
  resolved by a deferred import.)
  **ADR 065** added
  `document_date`/`document_date_method` (a deterministic
  dateline/label regex pass over the doc's own raw first pages, LLM only as
  fallback — same classification call, no extra LLM call) and
  `display_title` (the model's raw proposal, only requested when the caller
  passes `title_is_generic=True`; still domain-agnostic — this module has no
  opinion on what makes a title generic). `classify()`/`ocr_in_place()` moved
  to `ocr_util.py` (ADR 069); both names stay importable here unchanged.
  **Untrusted input + urgency floor (PR #142, 2026-10-15):** document text goes
  to the model inside `<document>…</document>` and filer metadata (nSITE name,
  date, type) inside `<metadata>…</metadata>`, both run through
  `_neutralize_fences` (look-alike `<>/` mapped, Unicode Cf stripped, any
  document/metadata tag opener bracketed; linear-time, never alters numbers).
  After classification a deterministic floor can only RAISE severity to urgent:
  a measured reading >= the MACT value (`urgent.measured_temp_urgent_f`, the
  same YAML anchor as `temperature_thresholds.mact_f`), "CO spike"/"smoldering"
  or a field technician's `,SSO Well`/`,SSO` comment tag on ANY page
  (`risk_register.hard_urgency_signal`, scanned page by page), or an
  enforcement-action title (`risk_register.enforcement_title_signal`: Violation
  Notice / NOV / VN (#n) / Enforcement Notice / (Administrative) Consent Order,
  not correspondence, responses or reports). The floor records why it fired on
  `ParsedDoc.severity_floor` (`measured_temperature` / `text_signal`) and the
  severity it replaced on `severity_before_floor` (coder:keydatapoint-guard),
  so `woi_router` can undo a temperature-only floor the table parse refutes. "Subsurface oxidation" and a bare
  "SSO" are deliberately NOT triggers (WOI-report boilerplate).
  `_SEVERITY_HELP` makes an enforcement action by ANY authority (EPA, EGLE,
  county, YCUA/WTUA/township) urgent when the document IS the action.
  `parse_document()`'s OCR gate now checks BOTH `classify()` (unchanged
  verdict) and the new `needs_ocr()`: a `classify()`-triggered case keeps the
  exact old raise-on-failure behavior, while a case ONLY `needs_ocr()` catches
  (a page `classify()`'s 12-page sample misses) is fail-open — a document that
  classifies fine on `main` can never become a poison strike over this.
  **Classifier timeout (stall-hardening part (b), 2026-10-23):** both
  classifier call sites build their client through
  `_anthropic_client(max_tokens)`, which sets an explicit per-attempt
  `timeout` instead of the SDK's 600 s default: `classify_timeout_s()` (pure)
  = the SDK's own expected-generation-time formula (the 128k output
  tokens/hour figure it uses to decide when streaming is required; an
  explicit client timeout bypasses that guard) for that `max_tokens` plus
  60 s headroom, clamped to [120 s, 600 s] — ~521 s
  for `classification_max_tokens` = 16384, 120 s for the 256-token note
  classifier — with a 10 s connect timeout. Sized from the OUTPUT budget on
  purpose: a timeout below the slowest legitimate response would be worse
  than the default (a timeout is `APITimeoutError`, retried twice by the SDK
  and a no-strike transient to `retry_policy`, so a large WOI classification
  that always exceeded it would be retried every run forever, never stubbed,
  invisible to the liveness guard while other documents process). Lowering
  `classification_max_tokens` lowers the timeout automatically; streaming is
  the real lever on run time. `scripts/term_search.py` and
  `backfill_metric_taxonomy.py` construct their own clients and are NOT
  covered.
- `ocr_util.py` — **ADR 069**: the shared OCR module. `classify()`/
  `ocr_in_place()` (moved from `egle_doc_parser.py`, unchanged) plus the NEW
  `needs_ocr()`/`ensure_searchable()` pair, used by both the Drive-mirror
  upload path AND `parse_document()`'s gate above: a page-by-page (not
  sampled) check for a page with LITERALLY NO text and an image — exactly
  matching `ocrmypdf --skip-text`'s own behavior (confirmed empirically: it
  skips a page the instant it has ANY text, even one stray character) — and a
  fail-open OCR-to-a-temp-copy helper. Wired into `archive_client.upload_file()`
  so every PDF upload, across all
  seven call sites, either already has a text layer or gets one before
  landing in Drive. Never raises; SEC-001 count-only logging.
- `kdp_check.py` — **ADR 067**: a deterministic consistency check (pure
  logic; `default_reference()` below is its ONE I/O entry point) run on
  the classifier's free-text `key_data_point`/`summary` before they reach
  the public Sheet/feed, catching cases where the model writes a
  wellhead-temperature claim the code itself would never make (a "135°F
  baseline MACT limit", an H2S ppm value reported as a °F reading). Checks
  a stated regulatory limit against reference values, reconciles "N°F above
  X" gap language, flags an implausible (>`max_plausible_wellhead_f`)
  non-flare wellhead reading, and catches the site's top HOV value
  attributed to a well that doesn't hold it. A failing field is withheld
  (`WITHHELD_KDP`/`WITHHELD_SUMMARY`) after one retry; `sanitize_measurements()`
  re-tags an implausible MEASURED temperature reading `basis="unknown"`
  rather than dropping it. `default_reference()` calls
  `config_loader.load_config()` to build the `TempReference` from
  `config.yml`'s `temperature_thresholds` block, falling back to the plain
  131/145°F defaults if config can't be read. See the `egle_doc_parser.py`
  entry above for who actually calls it: no `parse_document()` call site
  passes `temperature_reference` — the ONE exception is
  `recheck_key_data_points.py`, which bypasses `parse_document()`'s kwarg
  entirely and instead builds its own `ref` via
  `reference_from_config()`, passed straight to `check_fields()`.
- `retry_policy.py` — classifies a per-document processing exception as
  TRANSIENT (infra/quota — a 429, a 5xx, an ANTHROPIC 401/403 read from a
  direct `status_code` attribute (a revoked, expired or mis-scoped key is
  never the document's fault — stall-hardening review, 2026-10-23; before
  that a dead key would have stubbed every real filing "skipped" within
  three runs; an nSITE download 401/403, carried on `.response.status_code`,
  is per-document and still strikes), the 2026-07-07 workspace API cap's
  400 "usage limit" message, the `anthropic` SDK's own connection/timeout/
  rate-limit/server/authentication error types, and a `requests`
  connection error/timeout) — checked on the exception AND everything it
  was raised `from`, since `nsite_client.download_pdf` wraps its last
  failure in a `RuntimeError ... from` it (so an nSITE 503 or dropped
  connection is an outage, not a strike; a 404 or a non-PDF body still
  strikes) vs. PERMANENT (a genuinely unprocessable
  source — bad PDF, OCR failure, truncation, generic 400). `watcher.py`/
  `backfill.py` both call `is_transient()` (confirmed at their respective
  poison-strike sites) before accruing a strike, so an API-cap or infra blip
  can no longer permanently mark a real filing "skipped" the way it did
  pre-fix: the module's own motivating incident was the 2026-07-07
  workspace-API-cap exhaustion causing a June-16-filed EGLE Surface Emission
  Monitoring inspection package (two urgent docs) to be silently marked
  "skipped" with no recovery path. Duck-typed detection; `anthropic` is an
  optional import so callers don't need it installed just to classify an
  error.
- `risk_register.py` — R1–R8 + R8 signal keywords (single source of truth).
- `document_titles.py` — Arbor-Hills-specific half of ADR 065, kept OUT of
  egle_doc_parser.py on purpose: `title_is_generic` (config-driven exact/
  prefix match against nSITE's six generic placeholder titles),
  `sanitize_display_title` (strips `name_check` denylist hits, longest match
  first, falling back to `""` — never a retry call to the model), and
  `resolve_display_name` (doc_id override > sanitized display_title > raw
  nSITE title). `watcher.py`/`backfill.py` call all three in one chokepoint
  right after `parse_document()`, mutating `d["document_name"]` (resolved),
  `d["egle_title"]` (preserved raw), `d["document_date"]` in place so every
  downstream write (Drive mirror, feed/evidence rows, digest/alert, `_state`
  payload) inherits them for free. `poison_stub.py` (no parse exists for a
  poison doc) applies only the override map.
- `nsite_client.py` — EGLE nSITE API: session, list, download. **A coupling
  that used to exist, fixed 2026-10-23 (coder:poison-import-move):** until
  then `nsite_client.py` imported `poison_doc_extractor` at MODULE level, and
  that module imports `fitz`/`defusedxml` at module level too — so every one
  of the seven nSITE PROFILE watchers (Streams K/L/M/N/O/P/Q — all seven
  import `nsite_client`; six of them, L/M/N/O/P/Q, use a bespoke raising
  reader for their OWN state, while K/Submissions uses `_tab_rows_strict`,
  see the state-vs-data Invariant below), which only need a structured JSON
  API, not a PDF, loaded the PDF-parsing stack the instant they `import
  nsite_client`. The import is now DEFERRED: `poison_doc_extractor` is
  imported inside `download_pdf()` (its only user), immediately before the
  ADR 011 extractor fallback, the same import-light pattern `archive_client.py`
  uses for the google OAuth libs. `tests/test_nsite_import_isolation.py` pins
  it in a clean subprocess: a bare `import nsite_client`, and a bare import of
  each of the seven profile watchers, leaves `poison_doc_extractor`,
  `egle_doc_parser`, `fitz` and `defusedxml` OUT of `sys.modules`; the
  `download_pdf()` fallback path then loads the extractor on demand. The
  LLM/OCR half (`egle_doc_parser`/`anthropic`/`ocrmypdf`) was never in that
  chain — `poison_doc_extractor` only imports those lazily, inside the
  functions that actually need them (see its own entry below).
- `poison_doc_extractor.py` — **ADR 011**: text extraction + PDF synthesis for
  nSITE documents whose source isn't a PDF (.msg, .docx, bare images, zip
  bundles) — the gap `nsite_client.download_pdf()` alone can't close. Wired in
  as that function's last-resort fallback (imported LAZILY inside
  `download_pdf()` since 2026-10-23, see that entry above): native bytes are sniffed for
  format and, if supported, synthesized into a PDF containing every
  extractable piece of content, so `egle_doc_parser.parse_document()`'s
  existing classify/OCR/extract pipeline ingests it unchanged downstream.
  Recurses into `.msg`/zip-bundle attachments (nested `.msg`, PDF pages merged
  via `fitz.insert_pdf`, `.docx`/`.xls`/`.xlsx` attachments, image attachments
  OCR'd proactively). `extract_msg`/`openpyxl`/`xlrd` and
  `from egle_doc_parser import ocr_in_place` are all deferred (function-level)
  imports — only `fitz`/`defusedxml` load at module import time (which is why
  the `nsite_client.py` entry above defers importing THIS module).
- `well_aliases.py` — **ADR 068**: the curated well-ID rulings. The SOURCE OF
  TRUTH is the committed `data/well_registry.json` (ADR 068 addendum 2026-10-22):
  spelling aliases, ruled new wells and GFL-labeled gas probes (GFL's own "Probe ID"
  column), each with its citation and date, changed by PR only. `data/well_aliases.json`
  (the {alias: canonical} map) is GENERATED from it (`python3 well_aliases.py --export`;
  a test pins the two equal); never hand-edit either one outside a PR. Lotext's wellfield
  staging reads the registry from this repo. Both are loaded ONCE per run in
  `watcher.run`/`backfill.run` (`load_alias_map()`, `load_registry()`) and fail loudly
  on a missing or malformed file. The rule is exact match only (trailing `*` stripped);
  there are no short-form, spacing or prepend-AH rules, and redrills (R2/R3) are never
  aliases. It is threaded into woi_router (Part A) and into
  `parse_document(well_alias_map=)` after ADR 067's temperature check (Part B). A
  relabeled Measurements row keeps its old id in `Well ID As Filed` (appended last
  column). `_needs_canonical_review` logs possible unmapped renames (log only), skipping
  every id the registry has ruled on. Probes are marked `Point Type` = probe in the WOI
  Well Summary (appended last column), are never the table-built headline, and never
  reach `well_watch`; Measurements are unchanged (a probe reading still emits one if it
  meets the usual rule). `scripts/backfill_well_aliases.py` relabels existing rows: it defaults to
  a dry run, and `--apply` is Trisha's to run.
- `drive_client.py` — Google Drive + Sheets API (service account, folder ID).
- `sheet_writer.py` — the four+1 sheet tabs; routing/fan-out is pure & tested.
  **ADR 065** appended `Document Date`/`EGLE Title` at the END of
  `FEED_HEADERS`/`EVIDENCE_HEADERS`/`ARCHIVE_HEADERS`/`ALL_EVIDENCE_HEADERS`
  (WDS rows in All Evidence get blanks — not a filed nSITE document).
- `dataset_provenance.py` — **coder:dataset-provenance-sheet (2026-10-22)**: the
  public Sheet's `Dataset Provenance` tab (one row per document copy and dataset
  version: published / covered / staged / withdrawn from staging) plus three
  annotation columns after the row-builder columns: `dataset_status` on
  New/Historical Documents (L; a formula that links to the doc's provenance row
  only while its own row's Link still matches, so a write displaced by a purge
  shows blank), `same_content_as` on Archived PDFs (J), `same_as_monitor_doc` on
  Hand-Curated Files (N). These columns are NOT in `_TAB_HEADERS` (the daily
  pipeline never touches them; rollback = delete the tab and clear the columns),
  and a new `FEED_HEADERS`/`ARCHIVE_HEADERS` column would collide with them (a
  test pins the widths). Written only by `scripts/sync_dataset_provenance.py`
  (one-time seed from the Lotext `provenance-map.csv`, dry run by default) and the
  Lotext wellfield staging run (`record_staged()` after every real run: staged
  rows, withdrawals, pointer re-sync; never overwrites a hand-set status). Doc
  ids are written RAW; annotation cells fill only when blank; header cells are
  checked on both feed tabs before any write; the tab read raises (it is the
  upsert's key index); titles pass `name_check`.
- `mmpc_client.py` — CivicClerk JSON API: enumerate + fetch MMPC event PDFs.
- `mmpc_archiver.py` — Mirror D: auto-archive MMPC Agenda/Minutes PDFs (ADR 010).
  (The old in-watcher "go check the minutes" reminder was retired; see ADR 013.)
- `civicclerk_archiver.py` — a NEW sibling to Mirror D (ADR 037, added
  2026-09-04): auto-archives DPW (categoryId 68) + Board of Commissioners
  (26/27) Agenda/Minutes/Other PDFs into Trisha's Drive, same building blocks
  as Mirror D (`mmpc_client.fetch_mmpc_files`/`download_file`,
  `archive_client.upload_pdf`) but driven from a config-list of
  `{category_id, folder_env, group}` mirrors instead of one hardcoded
  category/folder — so DPW and BOC land in separate Drive folders (BOC's two
  categories share one). `mmpc_archiver.py` itself is completely untouched.
  A mirror with no folder secret set is skipped (not fatal); a mirror's fetch
  failure is isolated from the others. Dedup is ONE shared set/tab across all
  mirrors (`CivicClerk Archived Files`) since CivicClerk file IDs are a single
  global sequence, not per-category. Gated on `civicclerk_archive.enabled`
  (shipped `false`; activated 2026-09-06 (Trisha) — DPW + BOC Drive folders
  created, `GOAUTH_DPW_FOLDER_ID`/`GOAUTH_BOC_FOLDER_ID` set).
- `archiver.py` — the durable nSITE PDF → Drive mirror (ADR 007). Two entry
  points: `mirror_one_now()`, called INLINE from watcher.py/backfill.py at
  write time (ADR 007 addendum, 2026-08-23) so a same-day Sheet row/alert can
  link to the durable Drive copy instead of the referer-fragile nSITE link
  (nSITE's `downloadpdf`/`downloadfile` reject requests referred from a
  google.com origin — which is what happens clicking from Gmail or a Sheets
  cell — a Drive-hosted link isn't); and `run()`, the original nightly 3am ET
  job, now a catch-up net for whatever the inline path missed. Both share one
  Archived PDFs index (`sheet_writer.archived_doc_links()`) so they never
  double-upload. `mirror_one_now()` NEVER raises to its caller — any failure
  (not configured, dead OAuth token, network, quota) falls back to the nSITE
  link; mirroring must never block classification or alerting. The `run()`
  catch-up branch re-downloads raw from nSITE and never went through
  `parse_document()`'s own OCR step — **ADR 069** identifies this as the
  likely primary source of the mirror's pre-fix scan-only PDFs; now covered
  by `archive_client.upload_file()`'s own OCR wiring regardless.
- `snapshot_exporter.py` — **ADR 072 (2026-10-23; ships OFF)**: the ONE
  generic snapshot → immutable Drive exporter for the four snapshot-diff
  watchers whose only durable record was a Tier-2 append-only tab:
  `rop_watcher.py`, `mmd_watcher.py`, `nsite_submissions_watcher.py` (each
  exports its `snap_json`) and `pfas_watcher.py` (its normalized `content`,
  as `.txt`). Each calls `export_snapshot()` immediately BEFORE every
  snapshot append (baseline and changed rows alike) so the write order is
  the data-lifecycle doc's invariant, Drive (Tier 1) → Sheet row → alert.
  Best-effort by contract: returns the webViewLink or `None` and NEVER raises
  (flag off, not configured, dead token, network, quota — class-only log),
  so it can never block a row or an alert; a PER-RUN circuit breaker turns
  the first failure in a process into a silent skip for every later item
  that run, so a Drive outage costs one retry-backoff timeout per run, not
  one per item (ROP's seven-item baseline inside a 15-minute job was the
  case that motivated it). Gated on the cross-cutting
  `data_lifecycle.snapshot_export.enabled` (its own top-level block, not any
  one watcher's; `is_enabled()` is checked before any env read); one SHARED
  app-only folder for all four streams, `GOAUTH_SNAPSHOT_FOLDER_ID`, with
  the `rop_`/`mmd_`/`subm_`/`pfas_` filename prefix keeping them apart.
  `snapshot_name()` = `<stream>_<key>_<date>_<hash>.<ext>` — the hash is
  load-bearing because `archive_client.upload_file()` dedupes BY NAME. Sheet
  row schemas are unchanged (the filename's date/key/hash IS the row's join
  key). The four workflows already pass the OAuth creds + folder secret
  through, so activation is folder → secret → flag (see `config.yml`).
  RIDE, PDF rendering, and `wds_archiver`/`gfl_feed_snapshot` are out of
  scope by ruling.
- `email_alerts.py` — SMTP urgent alerts + weekly digest; urgency is pure.
  **ADR 065** added `is_correspondence_letter` (EGLE's own letterhead marker
  plus a substantive-signal phrase, both pure/unit-tested): a doc already
  flagged `title_was_generic` that reads as a real EGLE approval/extension/
  corrective-action/enforcement letter gets pinned to its own
  "CORRESPONDENCE & ENFORCEMENT" digest section instead of the ordinary soft
  line — absorbs the retired `schedule-title-overrides.md` handoff's Option A.
  **coder:keydatapoint-guard (2026-10-17):** `is_urgent()` is now the bool of
  one `_urgency_decision()`, whose reasons `urgency_reason()` joins, so the two
  can't drift. `format_urgent_body(..., cfg)` prints "Urgent because: …" next
  to "Severity (as recorded): …" (an `[URGENT]` subject over a routine-severity
  body now says which path fired), plus HOV approved-ceiling context per
  alerted well from `hov_ceiling_for()` / `temperature_thresholds.hov_ceilings`
  (exact well id, effective dates; 21 wells filled 2026-10-17 from the
  2024-2026 EGLE HOV letters; "Approved ceiling for <well> not in this monitor's roster"
  otherwise, since the 2019-2023 letters were never pulled). The ceiling is
  display-only: it never changes whether an alert fires, and the 145°F
  threshold is unchanged.
- `woi_router.py` — **ADR 005**: detects a WOI Status Report by content (the
  "Gas Extraction Report" header on any page, over the large-doc threshold —
  never by nSITE name), parses it exhaustively with `woi_table_parser`, and
  REPLACES the classifier's windowed temperature/O2/CH4/CO measurements before
  `is_urgent`/`write_document` (data-loss guard: fewer than 50 valid readings
  → not routed; peak-temp preservation; above-ceiling readings held out and
  flagged, ADR 067 amendment). **Headline (coder:keydatapoint-guard,
  2026-10-17):** for a routed report, `apply_deterministic_headline()`
  replaces `key_data_point` with the hottest well in the table parse
  (`build_key_data_point()`) only when the model's headline states a wellhead-range °F
  value more than 1°F above every reading (or is empty), and withholds a
  `summary` on the same test (`text_contradicts_routed()`; HOV/regulatory
  reference values are exempt in the summary only, never in the headline —
  owner ruling 2026-10-17). A headline that isn't about temperature (a
  Consent Judgment compilation's deadline or enforcement line) is kept (owner
  ruling 2026-10-17). `recheck_severity_floor()` undoes `parse_document`'s
  urgency floor when it fired ONLY on a measured reading (its new
  `ParsedDoc.severity_floor == ["measured_temperature"]`) and no table reading
  at/above the MACT value survives: severity returns to
  `severity_before_floor`, so an invented 202°F the tables discard no longer
  fires `[URGENT]` (owner ruling 2026-10-17, a deliberate firing change for
  this case only). The model's own urgent, a text/title floor and a real
  surviving reading at/above `mact_f` (or one that rounds up to
  `measured_temp_urgent_f`) all still fire. `doc_pipeline` calls both right
  after routing and BEFORE the Sheet row is written, so the row, alert,
  digest and `_state` payload all carry them (each best-effort). This
  is the second layer after `kdp_check` (ADR 067): kdp_check catches an
  implausible number in the model's text; this catches a plausible-looking
  number (the 2026-10-08 "202°F", a Depth to Bottom sounding in feet) the
  table parse doesn't support. Unrouted documents are unchanged.
- `backfill.py` — nightly batch of 50, self-terminating, resumable. Mirrors
  each doc to Drive inline (`archiver.mirror_one_now()`) before writing its
  Sheet row. **FORCE_REPROCESS read cache (coder:backfill-purge-read-cache,
  2026-10-23):** one `sheet_writer.PurgeReadCache` per run, filled in
  `backfill.run` (outside `process_one_doc`) and threaded into
  `purge_doc_rows(cache=)` for both the dry-run preview and the apply path, so
  the metadata, the Archived PDFs index (read STRICTLY there) and each of the
  six purge tabs are read once per run instead of once per doc (run
  39699682035: ~8 reads x 58 ids vs. 60 reads/minute). The cache decides
  WHICH tabs hold a doc's rows, never which row indices get deleted: on apply
  a tab with matches is re-read immediately before its `deleteDimension` is
  built (minutes pass between the fill and `doc_pipeline._purge`), and after
  a successful delete the cache keeps the post-delete layout. A Sheets 429 on
  `ensure_tabs`/`read_state`/the fill/the dry-run purge is waited out once
  (`_waiting_out_quota`: a flat `QUOTA_WAIT_S` = 61 s, which covers a sliding
  window too, retry once, then fail); `process_one_doc` is deliberately not
  wrapped (its own per-doc handling already treats a 429 as a no-strike
  transient).
- **Facility scope (ADR 064, 2026-10-07):** each `facilities:` entry is `core`
  (default — Arbor Hills/GFL) or `scope: related` (a neighboring/watershed site,
  e.g. the Johnson Creek drain restoration). Related docs go ONLY to the
  "Related Documents" tab — never New/Historical, Evidence, Measurements,
  Deadlines, the digest, urgent alerts or the website. The eight neighbor-
  development sites (Coldwater Ridge, Toll, etc.) are not in `facilities:` at
  all (profile watches only). `tests/test_facility_scope.py` pins the set —
  classify any new facility on purpose; never add a non-GFL site as core.
- `poison_stub.py` — shared terminal step for a poison doc: on its
  `MAX_ERRORS_PER_DOC`-th failure, write a stub feed row + mark it `skipped`.
  Called from BOTH watcher.py's and backfill.py's per-doc except handlers, so
  the doc is stubbed whichever job caused the last strike (issue #82).
- `review_flags.py` — **ADR 067 amendment (2026-10-15)**: the "needs human
  review" log for parser output the monitor deliberately did NOT use: a
  temperature above `max_plausible_wellhead_f` (a WOI table reading, a
  classifier reading kdp_check re-tagged, or a number the free-text urgency
  fallback skipped; flare context excluded), a rejected WOI gas table, and
  unreadable pages. One row each in the append-only **Parser Review Flags**
  tab on the PRIVATE Sheet only (`private_sheet_for_write`: `GSHEET_ID_PRIVATE`
  set, different from `GSHEET_ID`, and holding no public case-file tab),
  best-effort, after the document row. `flags_since()` is the strict read.
- `owner_notices.py` — **ADR 070 (2026-10-15)**: the owner-only channel. A
  Sunday "For Trisha only" email (unreadable filings, unrouted hand-curated
  rows, failed alert sends, parser review flags) and same-day owner alerts (an
  unreadable filing with an enforcement title, from `poison_stub`; an
  above-ceiling temperature flag, watcher only). Recipients are
  `load_owner_emails` ONLY, never a list or a fallback. Gated on
  `owner_weekly.enabled` / `owner_weekly.enforcement_alert_enabled` (both ship
  false). State: `_meta.last_owner_weekly`, plus two PRIVATE-Sheet tabs, the
  **Owner Notices** log and the **Hand-Curated Routing Log** (written by
  `scripts/route_hand_curated.py` before `write_meta`).
- `doc_pipeline.py` — consolidation PR 3 (2026-10-16): `process_one_doc()`,
  the ONE per-document pipeline behind `watcher.run` and `backfill.run`
  (download, parse, ADR 065 chokepoint, inline `archiver.mirror_one_now()`,
  related-scope shortcut, WOI routing, Sheet row, WOI summary, review flags,
  `processed` state LAST, shared strike / poison-stub handling). A `Job` holds
  only where the two jobs differ (feed tab, payload, the watcher's deadlines /
  well_watch / urgent-digest / owner review-flag email, the backfill's
  error-count clearing); the backfill's FORCE purge is the `purge_first=`
  argument to `process_one_doc()`, not a `Job` field. A cross-cutting change
  is wired ONCE, here.
- `watcher.py` — daily new-doc check + alerts (+ WDS Stream C when enabled).
  Stream C (`wds_watcher.py` + `wds_client.py`) polls eight WDS collections for
  site 475946: applications/qmr/annual/evaluations/compliance_actions plus
  **penalties** (nested penalty sub-grid on the ComplianceActions page — new
  assessed penalty = notable R2, NOT urgent, to avoid a double-fire with
  compliance_actions' urgent on the same page-event; payment backfill = watch) and
  **composting_registrations** / **composting_reports** (the Utilization module's
  Reg + RptYr grids — new registration = notable R1, new report-year = watch R1).
  All notable/watch → weekly digest. `wds_archiver.py` de-dups snapshots by page
  URL so penalties/compliance_actions and the two composting grids don't
  double-snapshot their shared pages. See ADR 043.
  Same inline-mirror-before-write as backfill.py.
  **Stall guard (stall-hardening, 2026-10-23):** `run()` now counts each
  run's unprocessed / processed / struck / deferred documents and hands them
  to `_check_liveness()` (the orchestration wrapper, mirroring
  `gfl_air_watcher._check_liveness`) right after the doc loop — see
  `doc_pipeline_liveness.py` below. Best-effort and isolated: it can never
  fail the run. **Anti-stampede cap (stall-hardening part (c)):** over
  `watcher.max_new_docs_per_run`, `_split_by_history()` (pure) separates
  the LIVE new filings — from an ESTABLISHED facility (one with any
  `processed` or `skipped` document in state) AND filed within the last
  `watcher.live_filing_window_days` (30) — from the BACKLOG: a facility
  with no processing history (just added to `facilities:`), or the
  years-old remainder of a facility whose backfill has only partly drained
  (the review-found hole: one manual backfill batch gives a new facility
  "history," and without the recency test its leftover filings would
  stampede into the live feed with urgent alerts). The backlog is deferred
  to backfill exactly as before; the live filings are still processed when
  they alone fit under the cap, so a new facility can never again freeze
  live monitoring of every other one (2026-07-24). An established
  facility's own over-cap burst, or a live share that itself exceeds the
  cap, keeps the old whole-run deferral — now alarmed by the liveness
  guard. Under the cap nothing changed (the split never runs). A partial
  deferral still counts toward `consecutive_deferrals` (the backlog is not
  draining), while `last_activity_at` advances. Backfill's schedule is
  intentionally off (it raced the watcher, 2026-07-07), so draining a
  backlog is a manual `workflow_dispatch` — the notice says so.
- `doc_pipeline_liveness.py` — **stall-hardening part (a)** (handoff
  `docs/overnight-coder-handoffs/stall-hardening.md`; the 2026-08-02
  digest-recovery lesson, when two newly added facilities pushed the backlog
  over `watcher.max_new_docs_per_run`, the watcher deferred EVERY run to a
  backfill whose schedule was off, and nothing alarmed for two weeks). The
  pure half, split like `gfl_air_liveness.py`: `update_health()`,
  `liveness_decision()`, `mark_warned()`, the body/subject. State is ONE
  `_meta` singleton, `doc_pipeline_health` (`_META_DEFAULTS`; persisted by
  the existing `write_meta`), holding `last_activity_at` (the last run, UTC,
  on which the watcher processed a document, struck a genuinely
  unprocessable one (a key or infrastructure problem is never a strike —
  `retry_policy` now counts 401/403 as transient for exactly this reason),
  or had nothing unprocessed — a quiet week is NOT a stall; three daily
  runs that each saw unprocessed documents and got through none of them
  is; staleness is counted in UTC calendar days so cron jitter can't
  shift it) and
  `consecutive_deferrals` (runs in a row that deferred a backlog under the
  cap; reset by a run that deferred nothing), plus two once-per-episode
  warned markers (`warned_stale_for` = the activity value warned on, the
  `warned_as_of` idiom; `warned_deferrals`, cleared with the counter).
  Thresholds `watcher.max_stale_days` / `watcher.max_consecutive_deferrals`
  (both 3; 0 disables). The notice goes to the OWNER list only
  (`owner_notices.send_owner_email`), is labeled a pipeline-health notice
  (never `[URGENT]`, never an exceedance), and the markers are set only after
  a successful send. An unparseable timestamp never fires a quantified stale
  alarm; on the first run with the guard (no timestamp yet)
  `seed_from_last_run()` seeds it from `_meta.last_run` (the last COMPLETED
  watcher run — not proof of progress, but it bounds the unknown so a stall
  already under way is alarmed `max_stale_days` later instead of never),
  and the deferral counter needs no timestamp at all. A corrupt (non-object)
  cell is replaced, logged, never fatal; the mid-run `write_meta` is skipped
  when nothing in the cell changed.
- `well_watch.py` — **ADR 058**: named single-well temperature trip-wires.
  `email_alerts.is_urgent` already fires on any measured wellhead temperature
  ≥145°F, but several wells hold an EGLE HOV above that, so the generic alert
  fires on nearly every WOI Status Report and never says WHICH well crossed.
  `watcher.py` calls `well_watch.check_and_alert()` after `woi_router` routes a
  WOI-format filing, and a configured well (`well_watch.wells` in `config.yml`)
  at or above its own threshold gets its own dedicated email naming the well,
  value, reading time, as-found vs. ADJ, and the filing. Matches by well
  NUMBER via regex (not exact ID — wells get redrilled, R5→R6, and the AHW/AHWW
  prefix/zero-padding is inconsistent); `since` drops re-printed historical
  rows. Runs only on the live WOI-routed path — the NESHAP semi-annual roster
  and any spreadsheet EGLE emails outside nSITE are not covered.
- `pfas_client.py` — content-hash normalizer for EGLE's PFAS pages (isolates
  `<main>`, ignores Sitecore theme cache-busters). Pure; stdlib-only.
- `pfas_watcher.py` — daily page-watch: alerts on any change vs. the last
  snapshot in the `PFAS Page Watch` tab. Gated on `pfas.enabled`. See ADR 012.
- `gfl_air_client.py` — Stream E: GFL's public ArcGIS perimeter-air FeatureServer
  (H2S/CH4 fenceline readings). Fetch + pure ADR-004 mapping; structured-API
  source, never goes through `egle_doc_parser`. See ADR 014.
  `fetch_station_coords()` is the live station **REGISTRY** (layer 0: every
  `MS-*` station the source lists right now, with coordinates and
  `last_read_iso`; fetched fresh each run, best-effort) — distinct from the
  human-maintained station **ROSTER** in `config.yml`
  (`gfl_air.expected_stations`, coder:h2s-roster-count 2026-10-23: which
  stations are SUPPOSED to exist). The roster is reconciled against the
  readings on every poll and against the registry on every run that fetches
  it (see the `gfl_air_episodes.py` entry below); neither is trusted alone.
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
  A consolidated ACTION-LEVEL SCREENING system (`gfl_air.watch_thresholds`:
  H2S 30 ppb / CJ ¶def T, CH4 40 ppm / CJ ¶def U — ADR 039, added 2026-09-10,
  replacing the old per-station CH4-40 watch) runs a per-`(station, gas)`
  open/close episode state machine (state JSON in the `GFL Air` tab's column O;
  a legacy array reads as empty → fail-safe re-derive). Per daily run it emails
  the `watch_alert_recipients` list (+ `GFL_AIR_WATCH_RECIPIENTS_EXTRA`) at most
  ONE consolidated SCREENING (open) email (all six monitors' verbatim readings
  for both gases, flagged STRICTLY above; ±2h context; station coords) and ONE
  CLOSEOUT (close) email (start/peak/return/duration per episode); a
  continuing-only run sends nothing (no re-alert). Each closed episode also
  appends a row to the "Perimeter Action-Level Episodes" tab (the RCA-tracking
  log). **These are SCREENING alerts on PUBLIC HOURLY data, NOT CJ exceedance
  determinations** — the CJ levels are 15-min rolling averages from ~1-min
  sampling; the feed is hourly. Mandatory legal framing (party = "Arbor Hills
  Landfill, Inc. (AHL)" not GFL; "hourly value exceeded the numerical benchmark…
  verification vs the 15-min rolling avg is needed"; 48h = CORRECTION not the RCA;
  ¶5.5(D)/(E); HISTORICAL subject tag; methane %-flammable only if ≥5%). NEVER
  mixed into the full-list exceedance/anomaly email (`include_watch=False`
  always). Empty `watch_alert_recipients` = display-only (rollback lever); a gas
  absent from `watch_thresholds` = display-only. The EXCEEDANCE tier (CH4 500 /
  H2S 72-24hr-avg) is untouched. Episode state committed like the cursor; emails
  best-effort (no retry, matching the exceedance email). See ADR 039 (+ ADR 014's
  2026-07-21 addendum, now folded in).
- `gfl_air_episodes.py` / `gfl_air_emails.py` / `gfl_air_liveness.py` — the
  pure halves of Stream E, split out of `gfl_air_watcher.py` on 2026-10-16
  (pure moves; one duplicate `_ET` later removed): the per-(station, gas) episode state machine +
  America/Detroit time helpers + monitor rows; every email's rendering; the
  liveness decision + body. `gfl_air_watcher.py` keeps the orchestration
  (run, capture, measurements, the liveness wrapper) and re-exports every
  moved name, so `gfl_air_watcher.<name>` still works.
  **Station roster (coder:h2s-roster-count, 2026-10-23):** the SCREENING
  email's "stations reporting N/<len>" denominator comes from
  `gfl_air_episodes.station_roster()` reading `gfl_air.expected_stations`
  (adding/removing a monitor is a one-line config edit), not from the
  hard-coded 6 PR #107 left in `format_screening_email()`'s signature. That
  6 survives only as `FALLBACK_N_STATIONS_EXPECTED`, used when the config key
  is missing/empty — `run()` logs a CONFIG WARNING on EVERY run that happens,
  and the email's data-quality line says the count is the legacy default.
  `roster_mismatch(roster, seen)` reconciles the roster against what was
  seen — on EVERY poll against the batch's readings (logged: a reading from a
  station not on the roster, a roster station with no reading), and on
  every run that fetches the layer-0 registry (an episode opened or closed)
  against readings ∪ registry: a station seen but not on the
  roster (a new MS-7, an "MS-01" beside "MS-1") is logged as ROSTER MISMATCH
  and named in the email as "not in this monitor's configured roster", listed
  but NEVER counted toward the expected N (so it can't mask a silent roster
  station); a roster station with neither a reading nor a registry entry
  (distinct from PR #107's "silent": registry entry, no reading) gets no
  invented row — it is named in the "(N station(s) not listed — MS-1: …)" note
  and logged. A roster entry that is blank, not a string, or lacks
  `station_prefix` is DROPPED with a CONFIG WARNING (never rendered as a
  fact about the source). A mismatch is never invisible. See ADR 039's
  2026-10-23 addendum.
- `gfl_air_hourly_capture.py` — ADR 026 addendum (2026-10-06): a SEPARATE
  hourly durable-capture job alongside the daily Stream E poll above, shrinking
  the window in which a reading could sit in GFL's feed (and be silently
  edited/removed) unseen from ~24h to ~1-2h. Deliberately minimal — no alerts,
  no Sheet, no case-file cursor. Its cursor is DERIVED FROM DRIVE (the highest
  `oid<N>` already captured in the app-only GFL Air Exhibit folder, shared with
  the daily job's own files), so the two jobs never race over shared state.
  A failed hour exits 0 (silently retried next hour) except four fixed UTC
  hours/day (`LOUD_HOURS_UTC`), which exit 1 so a persistent failure still
  surfaces a few times a day. Gated on `gfl_air.hourly_capture.enabled`
  (added + activated together, 2026-10-06, Trisha).
- `gfl_feed_snapshot.py` — **ADR 063**: a monthly FULL snapshot of GFL's public
  perimeter-air feed (every row of every layer, ~226k hourly H2S/CH4 readings,
  verbatim) with change detection against the previous snapshot — the backstop
  for whatever the daily/hourly captures above missed (an over-cap re-baseline,
  a failed upload) or for the source silently deleting/editing PAST readings.
  Uploads an immutable zip (data + manifest with per-file SHA-256 and a row-
  count cross-check against the server's own count) to the GFL Air Exhibit
  Drive folder FIRST, then compares against the newest earlier snapshot that
  itself carries a `.compared` marker; a DELETED or EDITED past reading, or a
  changed field set, emails Trisha (the owner list) ONLY — the new snapshot's
  `.compared` marker is set only after that succeeds, so a failed comparison
  or email retries against the same baseline next run. Gated on
  `gfl_feed_snapshot.enabled` (switched on 2026-10-06, Trisha — the module's
  own docstring is stale and still says "ships false"; a future change should
  fix that comment to match).
- `civicclerk_watcher.py` — Stream F: twice-daily change-watch on MMPC +
  Washtenaw County BOC meeting events (hand-picked, via `mmpc_client.fetch_event`)
  plus the Board of Public Works/DPA (categoryId 68, AUTO-DISCOVERED via the new
  `mmpc_client.fetch_category_events` — the county doesn't reliably pre-create
  future DPA event stubs the way MMPC/BOC do, so a hand-picked list can't cover
  "the moment it's agendized"; `discoverable_events` bounds each run to recent +
  future meetings only, `discover_since_days`). Snapshots each event's
  title/date/status/document-set into the `Meeting Watch` tab and alerts (Trisha
  only) on any change. Cadence is a pure function per event (`is_due_today`):
  MMPC/DPA every run; BOC weekly + daily in the 3 days before a meeting.
  **Keyword scan (ADR 036, added 2026-09-03):** gated on
  `civicclerk_watch.keyword_scan.enabled`, every new/changed Agenda/Minutes file
  on a due-checked event (any group) is downloaded (`mmpc_client
  .download_file_bytes`) and its text scanned (`find_keyword_hits` — case-
  insensitive, word-boundary, hyphen/whitespace/line-wrap tolerant) against a
  configured keyword list (Arbor Hills / GFL / plan amendment / siting /
  consistency determination / etc., closing the gap where an expansion item is
  agendized on a county body without ever touching EGLE's nSITE system, which
  every other stream depends on). A hit elevates the alert's subject + body and
  overrides an otherwise-silent first-sighting baseline. Two keywords
  ("consistency", "siting") are deliberately short/broad and will false-positive
  on ordinary county business — kept anyway, fail-open by explicit direction;
  every alert names its matched keyword + a short excerpt for fast triage. A
  one-time `run_historical_backfill` (invoked via the temporary
  `scripts/oneoff_meeting_watch_keyword_backfill.py` +
  `.github/workflows/oneoff-meeting-watch-keyword-backfill.yml`, both deleted
  after use) swept the prior 12 months across all four categories, emailed one
  summary report, and silently seeded baselines so the live watch never
  re-alerts on that history. Alert-only (no Drive). Gated on
  `civicclerk_watch.enabled`. See ADR 015 + ADR 036.
- `ridgewood_client.py` — Stream G: fetch + parse Barr Engineering's monthly Ridge
  Wood Elementary H2S reports. Scrapes the `Files/*.pdf` report links off the public
  page (never constructs a URL), parses `YYYY-MM`, extracts text via fitz, and runs a
  pure, fail-safe + footnote-safe classifier (numeric ≥72 ppb → alert; missing
  all-clear phrase → alert). Stdlib + fitz; keeps `egle_doc_parser` untouched. ADR 016.
- `ridgewood_archiver.py` — Stream G: diff scraped months vs. the `Ridge Wood Reports`
  tab, mirror each new PDF to Drive (optional — the extract+alert safety function
  doesn't depend on it), write the month's max 24-hr average → Measurements
  (`basis=measured`, Barr/EPA-agreement monitor), same-day alert on a stated 72/750 ppb
  exceedance. Modeled on `mmpc_archiver`. Gated on `ridgewood.enabled`. See ADR 016 —
  **ADR 069** resolved its deferred-OCR note: `ocr_util.ensure_searchable()` now runs
  BEFORE text extraction, and any value extracted only via OCR is pinned to the
  review tier (never a plain "ok"), never suppressing a real `exceed_24h` alert.
- `ridgewood_reverify.py` — Stream G add-on (ADR 066, added 2026-10-12; the Ridge
  Wood slice of `coder:selfreport-provenance`): monthly re-download of EVERY month
  Stream G has archived (not just new ones) + a committed, tamper-evident
  `data/provenance/ridgewood-manifest.json` (one entry per report *version*; the git
  commit history is the signature — no cryptographic signing, Trisha's 2026-10-08
  ruling). Detects a silently REVISED PDF (new version mirrored to the same Drive
  folder under its own filename, old copy never touched) and a report confirmed
  absent from Barr's page on two fetches in the same run (removal APPENDS a new
  version rather than mutating the superseded one, so a month that later reappears
  with byte-identical content gets a fresh "original" version correcting the stale
  removed flag, instead of silently staying marked removed forever). Bootstraps a
  month's first manifest entry from Stream G's own Sheet row
  (`sheet_writer.ridgewood_report_rows`, new/read-only) rather than today's date.
  Changed/removed/reappeared/multi-link events go to a SEPARATE append-only
  `Ridge Wood Revisions` tab (never a second row in `Ridge Wood Reports`; a month
  with more than one posted report link is logged + alerted ONCE, never re-alerted
  for the same persistent condition) and an OWNER-ONLY alert (`ea.load_owner_emails`,
  never the public/review lists) —
  the manifest is only advanced + saved once that alert sends, so a failed send
  retries idempotently next run. `ridgewood_client.py` gained two additive-only
  helpers (`scrape_all_report_links`, `allowed_report_host`); Stream G's own
  `scrape_report_links`/`download_report`/the archiver itself are untouched. Gated
  on `ridgewood.reverify_enabled` (shipped `false`; activated 2026-10-12 (Trisha)
  — see ADR 066).
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
  ArcGIS RRDOpenData, two layers — status only; RIDE's app itself is not used
  — its public inventory page was found on 2026-09-28 to list FILES anonymously,
  see the ADR 019 addendum). One `SiteID IN (...)` / `FacilityID IN (...)` query per layer;
  explicit `outFields` (never `*`) + `returnGeometry=false` keep OID/geometry
  out of the fetch entirely (`ProjectManaager` excluded too — admin churn, not
  signal). Fetch-vs-structural error split mirrors mmd_client. Structured-API
  source, never goes through `egle_doc_parser`. See ADR 019.
- `ride_watcher.py` — Stream J: daily snapshot-diff of each watched Part 201
  site's / UST facility's record vs. the `RIDE Watch` tab — a `RiskCondition`
  flip, a `Contaminants` change, or a new `Open_Release` alerts (R5, water
  quality). Gated on `ride.enabled` (built disabled; live `true` since
  2026-08-07). Watches Layer-1 facilities 00040223 (GFL) and 00038889 (Arbor
  Hills Landfill Inc, added 2026-09-28). See ADR 019.
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
  `nsite_violations.enabled` (shipped `false`; activated 2026-08-06
  (Trisha)). Its workflow lives at
  `.github/workflows/nsite-violations-watch.yml` — moved out of
  `docs/pending-workflows/` when PR #36 merged (the build session's token
  lacked the `workflow` OAuth scope, so the file was parked until a
  scope-bearing SSH push could land it). See ADR 023.
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
  `nsite_compliance_actions.enabled` (shipped `false`; activated 2026-08-09
  (Trisha)). Its workflow lives at
  `.github/workflows/nsite-compliance-actions-watch.yml` — moved out of
  `docs/pending-workflows/` (which now holds only a README) the same way
  Stream L's was. See ADR 028.
- `nsite_evaluations_watcher.py` — Stream N: daily snapshot-diff of EGLE's
  nSITE **Evaluations** profile (the underlying inspection record a violation
  or compliance action often stems from — a Violations record already
  carries `evalEvalNum`, joining a violation back to its evaluation) for
  every srn in `nsite_evaluations.tiers`, against the `Evaluations Watch`
  tab. Item key `eval:<srn>`. The feasibility gate (live, all 19 sites) found
  `evalEvalNum` **IS unique** per site (477/477 at N2688, 40/40 at RA) —
  UNLIKE Violations/Compliance Actions, so the diff is **ref-number-keyed**
  (the Submissions idiom, not a multiset): a new `eval_num` alerts as a new
  evaluation, an existing one with a changed field alerts as detail
  advancing. No status field exists in this profile, so a new evaluation
  appearing IS the primary signal. N2688's 477 records serialize to 75,494
  chars even in the compact positional snapshot form — OVER the Sheets cell
  cap — so unlike Violations/CA (where the same budget guard never actually
  fires), N2688 runs in a digest-degraded mode on every real run; the
  degraded form keeps `[eval_num, digest]` pairs (not an anonymous digest
  multiset) so a truncated diff still names exactly which evaluation is
  new/changed/removed. Makes no severity judgment. Tiers are this profile's
  own (**1 daily / 4 biweekly / 14 quarterly**), NOT a copy of any sibling —
  only N2688 has recent evaluation activity (latest 2026-08-07). Gated on
  `nsite_evaluations.enabled` (shipped `false`; activated 2026-08-22 (Trisha)
  — see ADR 029; no new secret). Its workflow landed directly in
  `.github/workflows/nsite-evaluations-watch.yml` (this build session's SSH
  key authenticated non-interactively, so the `workflow`-OAuth-scope parking
  Streams L/M needed did not apply). See ADR 029.
- `nsite_permits_watcher.py` — Stream O: daily snapshot-diff of EGLE's nSITE
  **Permits** profile (a facility's permit lifecycle — issued, extended,
  expiring, terminated — across EVERY permit type on file, broader than
  Stream H's targeted ROP-renewal watch) for every srn in
  `nsite_permits.tiers`, against the `Permits Watch` tab. Item key
  `prmt:<srn>`. The feasibility gate (live, every site with any permits)
  found `prmtPrmtNum` **IS unique** per site (9/9 at N2688, 4/4 at N1504) —
  like Evaluations, so the diff is **ref-number-keyed**: a new `prmt_num`
  alerts as a new permit; an existing one with a changed field — most
  importantly `status` (e.g. Extended -> Expired) or `termination_date`
  populating — alerts as that permit's status advancing, THE primary signal
  this watch exists for (tested explicitly, not left to a generic
  "field changed" case). **Overlaps Stream H at exactly three permit numbers**
  (ROP0000224/0656/0236, confirmed live, all currently "Extended") — NOT
  suppressed from the diff; `format_change_body` disambiguates explicitly
  that this watch trip-wires permit status/lifecycle, a different event than
  Stream H's public-comment-window entry. Makes no severity judgment beyond
  that disambiguation. Tiers are this profile's own (**3 daily / 2 biweekly /
  14 quarterly**), NOT a copy of any sibling — all three ROP-holding sites
  (N2688/N1504/P1488) are daily here, since each currently holds an
  "Extended" permit, this profile's own headline concern. The budget-
  degradation guard is inherited for structural parity but verified-inert at
  real Permits volumes (max 9 records). Gated on `nsite_permits.enabled`
  (shipped `false`; activated 2026-08-22 (Trisha) — see ADR 030; no new
  secret). Its workflow landed directly in
  `.github/workflows/nsite-permits-watch.yml` (this build session's SSH key
  authenticated non-interactively, so the `workflow`-OAuth-scope parking
  Streams L/M needed did not apply). See ADR 030.
- `nsite_complaints_watcher.py` — Stream P: daily snapshot-diff of EGLE's
  nSITE **Complaints** profile (citizen/agency reports against a facility,
  often the trigger for an inspection) for every srn in
  `nsite_complaints.tiers`, against the `Complaints Watch` tab. Item key
  `cmplt:<srn>`. The feasibility gate (live, all 19 sites) found
  `submSubmRefNum` **IS unique** (6,396/6,396 at N2688, 5/5 at RA) — but
  UNLIKE Evaluations/Permits this is NOT ref-number-keyed: N2688's volume
  (6,396 records, live-fetched 2026-08-22) breaks even Violations'/
  Compliance Actions' own digest-multiset degradation (~102,336 chars here —
  still 2x the 50,000-char Sheets cell cap, since complaints carry ZERO
  duplicate-tuple compression, unlike Violations' 64%-collapsing RA). So the
  snapshot is a `{n, hash, latest[K]}` fingerprint instead — small BY
  CONSTRUCTION, not degraded into smallness. `hash` is over the sorted
  ref-number SET only (immune to the EDT/EST date-offset flip); `latest` is
  the K=50 most-recently-received complaints, kept as display CONTEXT only —
  NEVER diffed to name a specific complaint as new. An earlier version tried
  exactly that (naming whenever fewer than K arrived since the last check)
  and three independent Step 5 review rounds each found a different way it
  misnamed an old survivor as new — a removal promoting a hidden entry into
  view; the same at the bottom of the window; an existing complaint's
  `received_date` being corrected by EGLE, which re-sorts it into view
  without changing `hash` (ref-set-only) and without any removal at all —
  proving the gap structural (this snapshot never stores old's full
  ref-number set, so no check on it can distinguish genuinely-new from
  merely-revealed) rather than a fixable edge case. So every growth case
  gets the same honest count-change note plus recent-complaint context
  (`summarize_complaints_change`), unconditionally — the sole exception is a
  confirmed-empty (`n==0`) prior baseline, where naming IS sound since there
  is no possibility of a hidden survivor. A count decrease is always labeled
  a removal, never misread as new. Makes no severity judgment (this profile
  carries no status field at all). Tiers are
  this profile's own (**1 daily / 3 biweekly / 15 quarterly**), assigned from
  the complaint-filing RATE (N2688's trailing-365-day rate, ~0.16/day), not
  the raw 6,396 — only N2688 is daily. Gated on `nsite_complaints.enabled`
  (new source; ships `false`). Its workflow landed directly in
  `.github/workflows/nsite-complaints-watch.yml` (this build session's SSH
  key authenticated non-interactively, so the `workflow`-OAuth-scope parking
  Streams L/M needed did not apply). See ADR 031.
- `nsite_public_notices_watcher.py` — Stream Q: daily snapshot-diff of EGLE's
  nSITE **Active Public Notices** profile (the formal comment-window
  announcements — permit renewals, draft permits, hearings) for every srn in
  `nsite_public_notices.tiers`, against the `Public Notices Watch` tab. Item
  key `pubntc:<srn>`; a near-copy of the Evaluations/Permits ref-number-keyed
  watch pattern, but keyed on an id regex-extracted from the notice's own
  `publicNotifPnurl` URL — NOT `publicNotifExtrnlPublNoticeNum` (null on
  every live record seen), and deliberately NOT conditional on that field
  ever populating, to avoid a false remove+add pair if it later does (see
  ADR 032). **This is the 6th and LAST of the six originally-unpolled nSITE
  profiles.** Live sampling (2026-07-24 and re-confirmed 2026-08-25, a month
  apart) found just ONE record total across all 19 sites, and both times it
  was itself a ROP renewal comment-window notice Stream H (`rop_client.py`,
  ADR 017) already watches through a different mechanism; no program-area
  field exists anywhere in this profile's schema (confirmed against the
  API's own field metadata) to scope around that overlap via a structured
  filter. **Resolved 2026-08-26 (Trisha-directed):** rather than pure
  standalone or a Stream-H state-based dedupe, `_all_changed_notices_look_
  like_rop` suppresses the EMAIL — never the durable Sheet row, written
  unconditionally either way — when every notice in a change reads as a ROP
  renewal by keyword match on `comments`, failing open (still alerting) on
  any non-match, mixed batch, duplicate-key state, or degraded/unreadable
  snapshot. Gated by `nsite_public_notices.rop_alert_suppression` (defaults
  `true`; `false` reverts to pure standalone without a code change).
  **Activated 2026-08-26 (Trisha-directed):** `nsite_public_notices.enabled:
  true` is live. The suppression heuristic is validated only against the two
  real ROP records seen to date — a novel non-ROP notice that happens to
  mention "ROP" in passing would have its email (not its Sheet row)
  wrongly suppressed; `rop_alert_suppression: false` is the rollback if that
  ever bites. See ADR 032 + its 2026-08-26 addendum.
- `gfl_info_site_client.py` — Stream S: fetch + normalize for the change-watch
  on the OPERATOR's own public "informational website" (arborhillslandfill.com,
  launched 2026-09-10 for the expansion). NEW external (non-EGLE) source — never
  goes through `egle_doc_parser`. `requests` + a browser UA renders the full DOM
  incl. the COLLAPSED FAQ accordion answers (WordPress server-renders them; no
  headless browser needed — verified from residential + datacenter IPs). Cloudflare-
  fronted, so normalization isolates `<main id=SiteContent>`, strips scripts /
  per-request nonces / `?ver=` cache-busters / the CF beacon (proven hash-STABLE
  across two live fetches while raw HTML differs), keeps sorted link PATHS
  (query-stripped) so a new doc link still trips the hash, and decodes any
  Cloudflare-obfuscated (`data-cfemail`) or `mailto:` email so a future email
  contact surfaces (the site is phone-only at launch). Discovery follows
  `/sitemap.xml` (a WP/Rank-Math INDEX) one level, falls back to a nav-crawl, and
  unions a `seed_paths` floor; challenge detection gates on content-PRESENCE (no
  `<main>`/too-short → raise), never on artifact strings (the ambient CF beacon).
  Stdlib + requests. See ADR 041.
- `gfl_info_site_watcher.py` — Stream S: daily snapshot + change-diff. Check set =
  discovered ∪ every-URL-ever-recorded ∪ seed floor; per URL, classify vs the last
  row in the append-only `GFL Info Site Watch` tab (the tab IS the state AND a
  dated durable snapshot — pfas_watcher idiom, no Drive): initial run baselines
  every page SILENTLY in ONE atomic write; steady-state new page → new-page alert;
  hash changed → changed alert (capped unified diff, with a NEW-EMAIL-CONTACT
  callout); a page now HTTP 404/410 → pending-removal (SILENT), a 2nd consecutive
  404 → removed-page alert (2-run DEBOUNCE so a transient site-wide 404 — a WP
  permalink flush / CDN purge / deploy — can't fire false removals; a `(removed)`
  sentinel so it never re-alerts); a removed page returning → new-page (returned).
  A SEED page's first sighting is a SILENT baseline (a launch page, not "new").
  REMOVAL is confirmed by real HTTP status, never sitemap-absence (a flaky sitemap
  can't fire a false removal). Anti-stampede: > `max_new_pages_per_run` new (or
  removed) at once → silent re-baseline / one consolidated removal notice. LIVENESS:
  all pages failing post-baseline → exit 1 (never silently blind). Child-sitemap
  fetches are SSRF-host-guarded. Row-before-email (crash-safe). Recipients scoped VERBATIM
  (Trisha only to start, + `GFL_INFO_SITE_RECIPIENTS_EXTRA`); EMPTY ⇒ DISPLAY-ONLY
  (rows, no email — never falls back to the coalition list). PROBE mode
  (`--probe` / workflow_dispatch `probe=true`) is a fetch-path diagnostic that runs
  regardless of the flag and touches neither Sheet nor email — the pre-activation
  Azure-runner Cloudflare check. Gated on `gfl_info_site.enabled` (shipped
  `false`; activated 2026-09-12 (Trisha-directed) — Azure-runner probe green,
  recipients = Trisha). See ADR 041.
- `pfas_pws_client.py` — Stream R: fetch + canonicalize for the Public Water
  Supply PFAS sampling watch. Keyless EGLE ArcGIS FeatureServer (the data behind
  the MPART PFAS map), one query per run (`WSSN IN (...)`, explicit outFields);
  Fetch-vs-Parse error split (mirror `mmd_client`), `epoch_ms_to_date`, canonical
  `record_view`. The seven MI-regulated PFAS are String columns; `classify_value`
  is FAIL-SAFE — non-detect ONLY on a recognized `<`-limit / `ND`/`BDL`/`U`
  token, a detection on any number (a quantified value below 2 ppt counts;
  trailing qualifiers like `J` stripped), and anything else UNRECOGNIZED →
  treated as a POSSIBLE detection (never silently dropped). Round key =
  `SysSampleCode` (0 null table-wide, unique) with a `(date,loc)` fallback.
  Stdlib only; NEVER routes through `egle_doc_parser`. See ADR 042.
- `pfas_pws_watcher.py` — Stream R: daily snapshot-diff per watched WSSN (Salem
  Elementary 2001381 + future capture-zone supplies) vs the `Public Water Supply
  PFAS Watch` tab. Alerts on a NEW sampling round (even all-non-detect — "sampled
  again, still clean" is the news) OR any DETECTION (subject elevated); detections
  route to the shared Measurements tab (`basis=measured`, `metric=pfas_<analyte>`,
  value verbatim). Write order Measurements → watch row (advances hash) → email
  (crash re-detects, never loses a detection). First sighting baselines silently
  (a historical detection is noted, not alerted — forward-looking). FetchError =
  skip-and-warn after baseline / loud on activation; ParseError = always loud.
  **No MCL comparison** (MI-2020 vs federal-2024 dual-regime accuracy trap — the
  raw value+analyte+date is actionable; see ADR 042). Recipients scoped verbatim
  (Trisha to start). Gated on `pfas_pws.enabled` (shipped `false`; activated
  2026-09-13 (Trisha-directed) — keyless EGLE ArcGIS, no runner probe needed).
  See ADR 042.

- `mpart_client.py` — Stream V: fetch + canonicalize for MPART's PFAS open-data layers
  (ADR 060): surface-water samples (`PfasOpenData/MapServer/0`; composite key
  `LabSampleId|SiteCode|date|Duplicate`; four regulated analytes as value/flag/MDL), fish PFOS
  results (`FcmpOpenData/FeatureServer/1`, a TABLE, key `SampleID`) and MPART's PFAS sites/AOIs
  (an ArcGIS Online layer). Explicit `outFields`, no geometry, https-only, and no OBJECTID/
  GlobalID/coordinates/EGLE-staff-contact fields ever fetched. Flags/codes are kept AS
  PUBLISHED (`flag_text`; tokens only for logic); `fish_code_meaning` uses EGLE's own wording
  (K/J/I/QNS). A versioned `{"v":2,"rows":{key:hash16},"hits":{site|date|analyte:value}}` snapshot (blank key =
  structural error; duplicate key = disambiguated `#n`; >45k chars = structural error). A
  rejected query (ArcGIS error 400) or a retired layer (HTTP 404/410) is structural. Stdlib only. The public-water-supply layer
  is NOT here (Stream R owns it).
- `mpart_watcher.py` — Stream V: daily snapshot-diff of the three layers into the
  `MPART Data Watch` tab (public Sheet — public data, no PII); silent baseline, then
  new/changed/removed alerts. EGLE's layer descriptions (saved 2026-09-26) call both STATIC PULLS (2/2025; 1/14/2026, updated
  annually), so a new row can mean EGLE republished. New/changed surface-water rows are SCREENED against
  the Rule 57 non-drinking-water values (PFOS 12 / PFOA 170 / PFHxS 210 / PFNA 30 ng/L, config
  `thresholds_ng_l` + `thresholds_verified_year`): screening wording (subject says "Rule 57
  screening", never "exceedance"), a `K` flag is never compared, samples older than a value's
  verified year and units that don't read as ng/L are not screened (the non-drink values are applied
  to every row in the box — the alert says so), and only a NEW (site, date, analyte) result raises
  the subject (the snapshot records announced hits, never pruned). Fish: EGLE's code definitions,
  no threshold. Suspect empty/shrunken responses (`max_shrink_fraction`) get their own
  `shrink-held` counter (never shared with fetch failures) and are accepted only when the SAME
  shrunken snapshot has been held twice; an empty FIRST response is loud, nothing baselined;
  recorded-skip liveness alert (weekly repeat); a lost alert (change or liveness) or an item
  exception makes the run red; the Sheet read raises (no silent re-baseline); empty recipients =
  display-only. `--probe`. Gated on `mpart.enabled` (activated 2026-09-29). See ADR 060.
- `govqa_client.py` — Stream U: fetch + parse for EGLE's PUBLIC FOIA archive on GovQA (ADR 059),
  following the handoff's §1C-bis method. KEYWORD LIST via lazily imported headless Playwright
  (`PlaywrightGrid`; NOT in requirements.txt — the workflow installs a pinned version only when
  enabled/probing; any launch error becomes a structural error): one term per search, newest first,
  paged with `ASPx.GVPagerOnClick`; `sweep_term` stops at the first page whose requests are ALL
  already known (mixed pages keep reading) and flags `overflow`; `with_backoff` = fresh context +
  30 s/2 min retries, then a structural error and move on. A zero-row grid is believed only with
  its "No data to display" marker (`parse_grid_state`). ONE REQUEST BY NUMBER via a plain
  `requests` cookie session (`ArchiveSession`: lookup -> details (opens the session itself if
  needed) -> streamed download that re-POSTs the form and follows the 302 by hand, https-only to
  EXACTLY the archive host and the state's Azure storage account; size cap = `GovqaTooLargeError`).
  Cells are parsed by aria-label. `scrub()` strips URLs from every message that can reach a log.
  `parse_gridview_csv` for a human-exported CSV. Never writes anywhere.
- `govqa_watcher.py` — Stream U: daily, in GUARDED phases with the report sent from a `finally`
  (an exception cannot lose an alert; a hard kill mid-staging could): keyword sweep (a keyword's first sweep reads all pages to
  `max_pages_per_term`, baselines silently, `term:` marker last; a page-cap hit writes a `partial`
  marker and asks for a CSV once; `nomatch` rows are upgradeable), CSV drop (AFTER the sweep; keyed
  by Drive id + content hash), re-check of watched/open requests by number (unknown status = open;
  truncation, mass-misses, a watch_requests entry the archive never shows, and a keyword that goes
  dark are reported), file listing for EVERY new/status event (`file:<E>:<name>#<n>`; a
  `list-pending` marker is written in the SAME append as the new/status row and a `file-list-done`
  marker — carrying the rid — after the detail page was read, so a failed/killed listing is retried
  next run even though the request is terminal; 5 failed attempts give up as `list-skipped`; the
  detail page's Reference No must match and its attachment-link count must equal the names read), and optional staging under CONTENT-ADDRESSED names `<E>__<sha16>[.<known ext>]`
  (attachment names never reach Drive queries/logs; in-run retry on a FRESH session; 5 strikes then
  reported). A grid with data rows none of which parse, or >10 rows with no pager, is a fetch error
  (never "no requests"); a first sweep of a full page with no pager text is marked `partial`. Circuit breaker + wall-clock budget.
  **HARD RULE: private only** — rows to `GSHEET_ID_PRIVATE` (fails closed if unset, equal to
  `GSHEET_ID`, or — the CI-effective check — the spreadsheet already holds public case-file tabs);
  staging folder != any other `GOAUTH_*_FOLDER_ID` / `GDRIVE_FOLDER_ID`; nothing feeds `findings_feed`; EMPTY
  `recipients` = display-only; a report that can't be sent makes the run red. stdout is public: no
  print interpolates request text or an attachment name (AST-pinned), googleapiclient's retry logger
  is silenced, `main()` prints class + scrubbed message only. `--probe`. Gated on `govqa.enabled`
  (shipped `false`; ACTIVATED 2026-09-28, notify-only). See ADR 059.
- `ride_docs_client.py` — Stream T: fetch + canonicalize for RIDE's ANONYMOUS
  document listing (ADR 061). RIDE's public Inventory of Facilities page gives
  every visitor a "Public User" session (no credentials, no login); its own front
  end then calls three JSON endpoints (program -> locationId; the location's file
  list, keyed by unique `uri`; file bytes by `uri`) that plain `requests` replays
  after a cookie warm-up (a bare POST is 405). Stable paging (sort on `uri`),
  streaming size-capped downloads with SHA-256/MD5, the 1900-01-31 placeholder
  date -> blank, a TITLE-FREE mirror filename (`<uri>_<hash8>.<ext>`: the name
  lands in Drive queries, which googleapiclient prints), and `probe()`. Never touches a
  credential, never routes through `egle_doc_parser`.
- `ride_docs_watcher.py` — Stream T: daily watch on the DOCUMENTS RRD lists for the
  `ride:` Part 201 sites (95 files across 5 locations at build). Events append to
  the `RRD Documents` tab (last row per key = state): silent baseline per location
  (file rows + `loc:` marker in one append), then new / changed / removed alerts, a
  bounded PRIVATE Drive mirror, and a liveness alert after N consecutive skipped
  runs; an EMPTY listing where files were listed, a baselined program that stops
  resolving, or one that resolves to a different locationId is a skipped run (never
  a diff; the relocation is red + alerted, never silently re-baselined).
  **HARD RULE: private only** — rows go to `GSHEET_ID_PRIVATE` (fails closed if
  unset, or — the CI-effective check — the spreadsheet holds public case-file tabs;
  the `GSHEET_ID` equality check is local-only, the workflow never receives it),
  files to a private folder that must not equal any other `GOAUTH_*_FOLDER_ID`,
  nothing feeds `findings_feed`; EMPTY `recipients` = display-only, never the
  coalition list. stdout is public: printed errors are class + HTTP status only,
  `main()` silences googleapiclient's retry logger. Alerts show BOTH document date
  and added-to-RIDE date (backlog digitization). `--probe` = pre-activation runner
  check (exit 1 if any program doesn't resolve). Manual dispatch input
  `mirror_uris` (comma-separated RIDE file ids) mirrors just those files now.
  Gated on `ride_docs.enabled` (shipped `false`; ACTIVATED 2026-09-28). See ADR 061.
- `egle_newsroom_watcher.py` — standalone, self-terminating watch on EGLE's
  newsroom landing page (no ADR; added 2026-10-01, Trisha-directed), the same
  shape as `pfas_watcher.py` but on a tighter cadence. The landing page lists
  only the newest four items and is the only server-rendered view (the full
  archive is a keyless JS search; the GovDelivery feed refuses non-browser
  requests), so `egle-newsroom-watch.yml` runs every 3 hours and fires a GAP
  NOTICE if every listed item comes back new (more may have scrolled past
  between polls). Items never seen
  before (the `EGLE Newsroom Watch` tab IS the state) are scored against
  `egle_newsroom.keywords` in `config.yml` — `specific` terms alert on any
  hit, `area` terms only alongside a `topic` term (county names appear in
  unrelated statewide lists), `topic` terms alert on a title hit or
  `topic_min_body_hits` body hits — using ONLY the article body (each page's
  trailing "Related News" block would otherwise leak a neighbor's keywords
  in). Durable row first, alert email second (best-effort). First run
  baselines silently; a state-tab read error raises; zero parsed article
  links on a page that did fetch is a liveness failure (exit 1). Recipients
  are scoped (`egle_newsroom.recipients`) and never fall back to the shared
  `alert_recipients` list. Gated on `egle_newsroom.enabled` (shipped `true` at
  build, 2026-10-01, on Trisha's explicit request — not the overnight-coder
  new-source `false` default).
- `public_comment_feed.py` — **ADR 035**: pure render logic (no I/O, same
  shape as `findings_feed.py`) for the public "Public Comment Periods" page
  (`site/public-comment/`) — an Open table and a Closed table, sourced from
  EGLE's live active public notices (`nsite_client.fetch_site_public_notices`)
  plus the statewide ROP notice (`rop_client.py`), via the thin I/O wrapper
  `scripts/gen_public_comment_feed.py`. Since EGLE drops a notice from its
  active feed once the comment window ends, state (closed periods, and any
  hand-set Outcome) rides inside the committed page itself as a hidden JSON
  block that each run reads back — the same self-referential trick
  `gen_findings_feed` uses. No `enabled` flag; it has its own scheduled
  workflow (`public-comment.yml`) and is always on. Editorial note baked into
  the module: this page is FACTUAL RECORDS ONLY — it never tells anyone to
  comment; that call to action lives on the separate advocacy site.

## Activation status (generated — do not hand-edit the table)

The table below is generated by `scripts/gen_stream_status.py` from every
`enabled`/`*_enabled` flag in `config.yml`, at any nesting depth, and pinned
by `tests/test_stream_status.py` (same pattern as `tests/test_sitemap.py` for
`site/sitemap.xml`). Regenerate with `python3 scripts/gen_stream_status.py`
and paste its output between the markers verbatim — a hand edit here drifts
exactly the way the per-module prose above did. That prose still carries each
flag's activation *date* and the human decision behind it; this table only
answers "is it live right now."

<!-- stream-status:begin -->
| Config flag | Live |
| --- | --- |
| `data_lifecycle.snapshot_export.enabled` | no |
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
| `egle_newsroom.enabled` | yes |
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
