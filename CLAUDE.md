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
