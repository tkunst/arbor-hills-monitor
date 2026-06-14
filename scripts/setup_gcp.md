# GCP service-account + GitHub Secrets setup (one-time)

This is the deploy-day setup. None of it is needed to develop or run the tests —
only to actually write the case-file Sheet and send mail.

> **Sheet only, no Drive folder (ADR 006).** A service account on a personal
> `@gmail.com` Drive has **no storage quota** and cannot create files there, so
> the monitor does not archive PDFs to Drive and does not keep a Drive state
> file. It links Sheet rows to the canonical nSITE source URL and stores state
> in the Sheet's own `_state` / `_meta` tabs. So you only enable the **Sheets**
> API and only share the **Sheet** — there is no folder to share, and
> `GDRIVE_FOLDER_ID` is not used.

## 1. Create a GCP project + enable the Sheets API

1. <https://console.cloud.google.com> → create a project (e.g.
   `arbor-hills-monitor`).
2. APIs & Services → Library → enable:
   - **Google Sheets API**
   - (The Drive API can stay off — nothing on the deploy path calls it.)

## 2. Create a service account + key

1. APIs & Services → Credentials → Create credentials → **Service account**.
2. Name it (e.g. `arbor-monitor-sa`). No project roles are required — access is
   granted by *sharing* the specific folder/Sheet with it (step 3), which is
   least-privilege.
3. Open the service account → **Keys** → Add key → Create new key → **JSON**.
   Download it. This file is the only real secret here.
4. Note the service-account **email** (looks like
   `arbor-monitor-sa@<project>.iam.gserviceaccount.com`). It is not sensitive.

## 3. Share the Sheet with the service account

The service account starts with access to nothing. Grant exactly what it needs:

1. Open the **case-file Sheet** → Share → add the service-account email as
   **Editor** (Editor, not Viewer — it has to write rows and the `_state` tab).

Copy the Sheet ID from its URL:

- Sheet ID: `https://docs.google.com/spreadsheets/d/<THIS_IS_THE_SHEET_ID>/edit`

## 4. SMTP app password (Gmail example)

1. Enable 2-Step Verification on the sending Google account.
2. <https://myaccount.google.com/apppasswords> → generate an app password.
3. Use `smtp.gmail.com` / port `587` / your address / that app password.

## 5. Anthropic API key

From <https://console.anthropic.com> → API Keys. Route this to API credits, not
a Max plan (the backfill is a batch job).

## 6. GitHub Secrets

Repo → Settings → Secrets and variables → Actions → New repository secret, for
each:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `GDRIVE_SA_KEY` | the **entire contents** of the service-account JSON file |
| `GSHEET_ID` | the Sheet ID |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | the sending address |
| `SMTP_PASSWORD` | the app password |

The workflows write `GDRIVE_SA_KEY` to a temp file at runtime and point
`GDRIVE_SA_KEY` (the env var) at that path — so the same variable name is a file
path locally and JSON contents in CI.

## 7. Local `.env` (for running outside CI)

```sh
cp .env.example .env
# fill in values; GDRIVE_SA_KEY is the PATH to the downloaded JSON locally
```

## 8. Verify

Trigger the `backfill` workflow manually (Actions → backfill → Run workflow) and
watch one batch of 50 complete, then check the Sheet. After that, the schedules
run unattended.
