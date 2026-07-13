# Arbor Hills Document Monitor

Automated monitoring of Michigan EGLE regulatory filings for the **Arbor Hills
complex**, built for the [Conservancy
Initiative](https://theconservancyinitiative.org). It tracks four co-located,
EGLE-regulated facilities — the **Landfill (N2688)**, the **Remediation Area**
(water/PFAS), **Arbor Hills Energy (N1504)** (the SO2 turbine plant), and
**Emerald RNG (P1488)** — backfilling the existing documents and watching for new
filings, classifying each against the Conservancy's risk register, maintaining a
full-text-searchable PDF archive plus a Google Sheet case file, and emailing
alerts. Each document is tagged with its facility (see
`docs/decisions/008-multi-facility.md`).

All inputs are **public regulatory data** from EGLE's nSITE portal. No credentials
live in the repo — cloud secrets are GitHub Secrets / local `.env`.

## What it does

1. **Backfill** (nightly, self-terminating): processes the existing documents
   across the tracked facilities in batches of 50, then becomes a no-op once done.
2. **Daily watcher**: picks up new filings and classifies them. Optionally (when
   `wds.enabled: true`) it also
   polls **Stream C** — the EGLE Waste Data System (Part-115 solid waste, site
   475946): quarterly groundwater reports (R5), annual capacity/airspace (R1),
   permit/license applications (R1), and enforcement (R2). Off by default; see
   `docs/decisions/009-wds-stream-c.md` for activation.
3. **Classify**: each document is OCR'd if needed, then sent to Claude with the
   risk register and signal keywords. Output per doc:
   - `summary`, `key_data_point`
   - `doc_type`: evidence / procedural / opinion
   - `risks`: which of R1–R8 it speaks to
   - `severity`: routine / notable / urgent
   - `measurements[]`: structured readings (temperature/CO/O₂), each flagged
     **measured vs permitted_limit** — see `docs/decisions/004`.
4. **Case file** (Google Sheet tabs):
   - *New Documents* / *Historical Documents* — the live + backfilled feeds.
   - *Evidence by Risk* — evidence docs only, one row per (risk, doc). Filter to
     R8, print, hand to EGLE.
   - *Risk Register* — R1–R8 with auto-counted evidence + most-recent date.
   - *Measurements* — every structured reading; derive per-well temperature
     trends from here without reprocessing.
5. **Alerts** (SMTP): urgent → same-day email; procedural + everything else →
   weekly Sunday digest. Recipients in `config.yml` (edit the list, no code
   change).
6. **PFAS page-watch** (daily, optional — `pfas.enabled: true`): a content-hash
   watch on EGLE's PFAS-investigation page for Arbor Hills (prose EGLE edits in
   place — no feed to parse). Emails a diff when the page's `<main>` content
   changes, ignoring the site's rotating theme cache-busters. Off by default; see
   `docs/decisions/012-pfas-page-watch.md` for activation.

> **A note on the document links (expected behavior).** Every case-file row's
> **Link** column points to EGLE's nSITE portal
> (`https://mienviro.michigan.gov/ncore/downloadpdf/<id>`). Clicking one
> sometimes shows a **"Server Error in '/ncore' Application"** page **while still
> downloading the file** — this is a harmless, intermittent quirk of EGLE's
> portal, *not* a problem with the document or the link. **The file that
> downloads is the correct, complete PDF.** (Verified: a direct fetch returns a
> valid `application/pdf`, and the monitor ingests these files server-side
> without ever seeing the error page — which is why, e.g., the 181-page 2025 WOI
> Status Report processed fine.) If the download doesn't start, just reload the
> link, or open the report's file directly. Nothing to fix on our side.

## Risk register (R1–R8)

R1 expansion eligibility · R2 violations history · R3 odor nuisance · R4 air
quality · R5 water quality · R6 environmental justice · R7 truck traffic ·
**R8 overheating / ETLF** (the evidence-dense new one — HOV waivers, WOI Status
Reports, temperature exceedances, CJ No. 2020-0593-CE). Full text in
`risk_register.py`.

## Develop / run tests

```sh
bash scripts/setup.sh   # venv + deps; <10 min from a fresh clone
pytest -q               # hermetic: synthetic PDFs, all APIs mocked, no secrets
```

## Deploy checklist (before the first scheduled run)

1. **GCP service account** — create project, enable the Sheets API, make a
   service account, download its key JSON. Full steps: `scripts/setup_gcp.md`.
2. **Share** the case-file Sheet with the service-account email **as Editor**.
   (No Drive folder share is needed for the *service account* — it has no Drive
   storage quota on a personal Gmail and cannot create files there, so the main
   Sheet rows link to the canonical nSITE source URL and processing state lives
   in the Sheet's own `_state` / `_meta` tabs. See
   `docs/decisions/006-state-in-sheet-no-drive-archive.md`. Durable PDF archiving
   is handled separately by the OAuth archiver — step 10, active since
   2026-06-15.)
3. **GitHub Secrets**: `ANTHROPIC_API_KEY`, `GDRIVE_SA_KEY` (the key JSON
   contents), `GSHEET_ID`, and (for email) `SMTP_HOST`, `SMTP_PORT`,
   `SMTP_USER`, `SMTP_PASSWORD`. `GDRIVE_FOLDER_ID` is no longer used and can be
   left unset.
4. **MMPC document archive (Mirror D)** — **done, active since 2026-07-11**: the
   `GOAUTH_MMPC_FOLDER_ID` secret is set and `mmpc_archive.enabled: true` is on
   `main`, so Mirror D auto-downloads every MMPC Agenda/Minutes PDF from
   CivicClerk (see `docs/decisions/010`). The older "go check the minutes page"
   reminder email was retired — see `docs/decisions/013`.
5. **Set the real alert recipients** in `config.yml`.
6. **Branch protection** on `main` (require the CI checks).
7. **Verify Sheet-backed state against the real API first** (no Anthropic call,
   no cost): `python scripts/verify_state.py` — creates the tabs, writes a
   throwaway `_state` row, reads it back through the same `read_state` the jobs
   use, asserts it round-trips, then clears it. This proves the append-only
   state actually persists against the live Sheets API (the unit tests only
   exercise a fake), so the backfill can't silently reprocess the same batch
   forever. Run it before the first real batch.
8. **Smoke-test one document live** (~$0.01, needs only `ANTHROPIC_API_KEY`):
   `python scripts/smoke_one.py` — validates the live `messages.parse` shape and
   surfaces any output truncation before you commit to a 50-doc batch. If it ever
   reports "Classification truncated at max_tokens", raise
   `classification_max_tokens` in `config.yml`.
9. **Run `backfill` to completion BEFORE enabling the daily schedule.** Trigger
   `backfill` manually (`workflow_dispatch`); each run does 50 docs and logs
   `N total, M done, K remaining`. Confirm `M` advances run-to-run (≈50→100→…).
   Repeat (or let the 2am `backfill` cron run nightly, ~15 days) until it logs
   **"Backfill complete"**. Only THEN uncomment the `schedule:` block in
   `.github/workflows/daily.yml` and push. Enabling the daily watcher while
   history is unprocessed would flood the live feed with historical docs and
   fire urgent alerts on years-old exceedances (the watcher has a
   `max_new_docs_per_run` backstop, but disabling the schedule is the clean fix).
10. **Durable PDF mirror — ACTIVE since 2026-06-15** (was optional; now set up).
    Insurance against nSITE link rot (ADR 007). The four `GOAUTH_*` secrets are
    set, the mirror folder ("Arbor Hills EGLE Document Mirror") is created and
    shared "Anyone with the link → Viewer", and `archive.yml` runs daily,
    mirroring each processed PDF into Trisha's Drive and filling the **Archived
    PDFs** tab. As of 2026-06-17 it is mid-backfill (~100 PDFs/run, ~1,249
    remaining; expected complete ~June 30), so not every Sheet row has an
    Archive Link yet. To re-do the setup (e.g. after a token revoke): run
    `python scripts/oauth_setup.py <oauth-client.json>`, re-set the `GOAUTH_*`
    secrets it prints, and re-share the mirror folder. Full steps:
    `scripts/setup_gcp.md` §9.

## Scheduling

- `backfill.yml` — 2am ET daily, batches of 50, self-terminating.
- `daily.yml` — 6am ET daily (new docs + alerts). **Schedule starts
  DISABLED** (only `workflow_dispatch`); uncomment the `schedule:` block after
  backfill completes — see deploy step 9.
- `archive.yml` — 3am ET daily (durable PDF mirror). **ACTIVE since 2026-06-15**
  (the `GOAUTH_*` secrets are set); mid-backfill — see deploy step 10.

## Cost

Backfill of ~754 docs at Haiku rates ≈ **$2–4 total**; routine monitoring (a few
docs/day) is essentially free. Model is configurable in `config.yml`.

## Residual risks (accepted)

- **Nothing watches the watcher.** If a scheduled run fails, detection relies on
  **GitHub's workflow-failure emails** — confirm those are enabled for the repo
  owner (GitHub → Settings → Notifications → Actions). Recovery: re-run the
  failed workflow; runs are idempotent and resume from the Sheet's `_state` tab.
- **nSITE link rot (now actively mitigated by the archive, ADR 007).** The
  Evidence/New/Historical Sheet rows link to the canonical nSITE source rather
  than a Drive copy, because the service account has no Drive quota (ADR 006). If
  EGLE removes or renames a document, that link dies. The OAuth archiver (deploy
  step 10) closes this by mirroring every PDF into Trisha's Drive and recording
  it in the **Archived PDFs** tab — **active since 2026-06-15**. The residual
  window is now just whatever the archiver hasn't caught up on (mid-backfill as
  of 2026-06-17, ~1,249 remaining), shrinking daily until backfill completes.
- **MMPC archiving rides CivicClerk's undocumented JSON API.** Mirror D (ADR 010)
  downloads MMPC PDFs through a public API found by inspecting the portal's own
  traffic; if CivicClerk changes it, the fetch fails loudly (aborts the run)
  rather than silently archiving nothing. The older poll-a-URL "go check the
  minutes" reminder this superseded was retired in ADR 013.
- **Classification is model output.** `key_data_point` and `measurements` can be
  wrong. The original PDF link is on every row; the `basis` flag and the
  measured-only urgency rule guard the highest-stakes error (permitted ceiling
  read as a crisis). Spot-check the Sheet against source PDFs.
- **nSITE API shape could change.** Verified working 2026-06-13 (754 docs). If a
  daily run returns 0 docs it aborts rather than wiping state.

## License / reuse

Public regulatory-data tooling — useful to other Great Lakes advocacy groups.
The parser (`egle_doc_parser.py`) is intentionally domain-agnostic.
