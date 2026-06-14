# GCP service-account + GitHub Secrets setup (one-time)

This is the deploy-day setup. None of it is needed to develop or run the tests —
only to actually read/write the Drive folder + Sheet and send mail.

## 1. Create a GCP project + enable APIs

1. <https://console.cloud.google.com> → create a project (e.g.
   `arbor-hills-monitor`).
2. APIs & Services → Library → enable both:
   - **Google Drive API**
   - **Google Sheets API**

## 2. Create a service account + key

1. APIs & Services → Credentials → Create credentials → **Service account**.
2. Name it (e.g. `arbor-monitor-sa`). No project roles are required — access is
   granted by *sharing* the specific folder/Sheet with it (step 3), which is
   least-privilege.
3. Open the service account → **Keys** → Add key → Create new key → **JSON**.
   Download it. This file is the only real secret here.
4. Note the service-account **email** (looks like
   `arbor-monitor-sa@<project>.iam.gserviceaccount.com`). It is not sensitive.

## 3. Share the folder + Sheet with the service account

The service account starts with access to nothing. Grant exactly what it needs:

1. In Google Drive, open the **EGLE Documents folder** → Share → add the
   service-account email as **Editor**.
2. Open the **case-file Sheet** → Share → add the same email as **Editor**.

Copy the IDs from their URLs:

- Folder ID: `https://drive.google.com/drive/folders/<THIS_IS_THE_FOLDER_ID>`
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
| `GDRIVE_FOLDER_ID` | the Drive folder ID |
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
