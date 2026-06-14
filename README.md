# Arbor Hills Document Monitor

Automated monitoring of Michigan EGLE regulatory filings for the **Arbor Hills
Landfill (SRN N2688)**, built for the [Conservancy
Initiative](https://theconservancyinitiative.org). It backfills the ~754 existing
documents and watches for new filings, classifies each against the Conservancy's
risk register, maintains a full-text-searchable PDF archive plus a Google Sheet
case file, and emails alerts.

All inputs are **public regulatory data** from EGLE's nSITE portal. No credentials
live in the repo — cloud secrets are GitHub Secrets / local `.env`.

## What it does

1. **Backfill** (nightly, self-terminating): processes the existing N2688
   documents in batches of 50, then becomes a no-op once done.
2. **Daily watcher**: picks up new filings, classifies them, and runs the MMPC
   meeting-minutes polling logic.
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
   (No Drive folder share is needed — a service account on a personal Gmail has
   no Drive storage quota and cannot create files there, so the monitor links
   Sheet rows to the canonical nSITE source URL instead of archiving PDFs, and
   keeps processing state in the Sheet's own `_state` / `_meta` tabs. See
   `docs/decisions/006-state-in-sheet-no-drive-archive.md`.)
3. **GitHub Secrets**: `ANTHROPIC_API_KEY`, `GDRIVE_SA_KEY` (the key JSON
   contents), `GSHEET_ID`, and (for email) `SMTP_HOST`, `SMTP_PORT`,
   `SMTP_USER`, `SMTP_PASSWORD`. `GDRIVE_FOLDER_ID` is no longer used and can be
   left unset.
4. **Verify the MMPC minutes URL** with the Conservancy (they attend every
   meeting) and set `mmpc.minutes_url` in `config.yml`.
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

## Scheduling

- `backfill.yml` — 2am ET daily, batches of 50, self-terminating.
- `daily.yml` — 6am ET daily (new docs + MMPC + alerts). **Schedule starts
  DISABLED** (only `workflow_dispatch`); uncomment the `schedule:` block after
  backfill completes — see deploy step 9.

## Cost

Backfill of ~754 docs at Haiku rates ≈ **$2–4 total**; routine monitoring (a few
docs/day) is essentially free. Model is configurable in `config.yml`.

## Residual risks (accepted)

- **Nothing watches the watcher.** If a scheduled run fails, detection relies on
  **GitHub's workflow-failure emails** — confirm those are enabled for the repo
  owner (GitHub → Settings → Notifications → Actions). Recovery: re-run the
  failed workflow; runs are idempotent and resume from the Sheet's `_state` tab.
- **No durable PDF archive (ADR 006).** Sheet rows link to the canonical nSITE
  source rather than a Drive copy, because the service account has no Drive
  quota. If EGLE removes or renames a document, that link dies. Recovery: a
  fast-follow OAuth-as-user archive job can mirror PDFs into Drive and add an
  `Archive Link` column, driven off the doc IDs already in `_state`, without
  reprocessing anything.
- **MMPC minutes URL is best-effort.** We poll a hard-coded URL within the
  computed window; if the county changes where minutes land, polling silently
  finds nothing. Mitigation: the Conservancy attends every meeting, so a missed
  posting is caught by a human. Re-verify the URL if minutes stop being detected.
- **Classification is model output.** `key_data_point` and `measurements` can be
  wrong. The original PDF link is on every row; the `basis` flag and the
  measured-only urgency rule guard the highest-stakes error (permitted ceiling
  read as a crisis). Spot-check the Sheet against source PDFs.
- **nSITE API shape could change.** Verified working 2026-06-13 (754 docs). If a
  daily run returns 0 docs it aborts rather than wiping state.

## License / reuse

Public regulatory-data tooling — useful to other Great Lakes advocacy groups.
The parser (`egle_doc_parser.py`) is intentionally domain-agnostic.
