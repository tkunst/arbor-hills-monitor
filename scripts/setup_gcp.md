# GCP service-account + GitHub Secrets setup (one-time)

This is the deploy-day setup. None of it is needed to develop or run the tests —
only to actually write the case-file Sheet and send mail.

> **Sheet only, no Drive folder (ADR 006).** A service account on a personal
> `@gmail.com` Drive has **no storage quota** and cannot create files there, so
> the monitor does not archive PDFs to Drive and does not keep a Drive state
> file. It links Sheet rows to the canonical nSITE source URL and stores state
> in the Sheet's own `_state` / `_meta` tabs. So the core deploy only enables the
> **Sheets** API and only shares the **Sheet** — there is no folder to share, and
> `GDRIVE_FOLDER_ID` is not used. (The optional durable PDF mirror in §9 is the
> one exception: it uses Drive via OAuth-as-you, not the service account.)

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

## 9. (Optional) Durable PDF mirror — OAuth setup (ADR 007)

This is the only part that uses **Drive** and **OAuth-as-you** (everything above
is the quota-free service account). It mirrors every processed PDF into a Drive
folder you own, as insurance against nSITE link rot. Skip it if linking to the
live EGLE source is good enough; the core monitor does not need it.

Why OAuth and not the service account: the service account has no Drive quota and
cannot create files (that is the whole reason for ADR 006). OAuth uses *your*
quota. The scope is restricted to `drive.file`, so the token can only touch files
this app creates, never the rest of your Drive.

1. **Enable the Drive API** (APIs & Services → Library → **Google Drive API**).
2. **OAuth consent screen:** add the scope `.../auth/drive.file`, add yourself as
   a user, and **publish to "In production."** This removes the 7-day refresh-token
   expiry that Testing mode imposes. For a single user it is a one-time
   "Google hasn't verified this app" click-through, not a review process.
3. **Create an OAuth client:** Credentials → Create credentials → **OAuth client
   ID** → type **Desktop app**. Download its JSON.
4. **Run the one-time consent + folder setup locally** (needs a browser):
   ```sh
   pip install -r requirements.txt          # provides google-auth-oauthlib
   python scripts/oauth_setup.py ~/Downloads/client_secret_XXXX.json
   ```
   Sign in as yourself and approve. The script creates the mirror folder and
   prints four values.
5. **Set the four secrets** it prints (the prompt form keeps them out of shell
   history):

   | Secret | Value |
   |---|---|
   | `GOAUTH_CLIENT_ID` | from the script output |
   | `GOAUTH_CLIENT_SECRET` | from the script output |
   | `GOAUTH_REFRESH_TOKEN` | from the script output (sensitive) |
   | `GOAUTH_ARCHIVE_FOLDER_ID` | the mirror folder ID it created |

   Then clear your terminal scrollback.
6. **Share the mirror folder** so the Conservancy can open Archive Links: in
   Drive, right-click the new folder → Share → General access → **Anyone with the
   link → Viewer**. Sharing cascades to the PDFs inside. (These are already-public
   EGLE filings, so this exposes nothing new.) Done in the UI, not in code —
   `drive.file` is not relied on to change permissions.

The `archive.yml` schedule is already on and is a no-op until these secrets exist.
Once set, it mirrors a batch nightly at 3am ET and fills the **Archived PDFs** tab.
A revoked token makes the job fail loudly (workflow-failure email); re-run
`oauth_setup.py` and update `GOAUTH_REFRESH_TOKEN`.
