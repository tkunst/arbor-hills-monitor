# ADR 007 — Durable PDF archive via OAuth-as-user (optional, automated)

*Status: accepted — 2026-06-14.*

## Context

ADR 006 dropped the Drive PDF archive because the service account has no Drive
storage quota, and pointed Sheet rows at the canonical nSITE URL instead. That
left one accepted residual risk: **nSITE link rot.** If EGLE removes, renames, or
re-IDs a document (common when a government records system is re-platformed), the
Sheet link 404s and the evidence is gone — exactly when it might matter for an
EGLE/commissioner submission. Trisha chose to close that risk now with a durable
mirror, run automatically.

## Decision

A separate, optional **archiver** mirrors every processed PDF into a Drive folder
**Trisha owns**, and records each in a visible **"Archived PDFs"** tab.

**Auth — OAuth-as-user, `drive.file` scope.** The service account can't create
files; OAuth-as-Trisha uses *her* quota, so it can. Scope is the narrow
`drive.file` (per-file access to files this app created), so a leaked token
cannot read the rest of her Drive — only the mirror it built. The refresh token
is long-lived because the consent screen is **published to Production** (the
7-day expiry is a Testing-mode artifact; for a single user, publishing is a
one-time "unverified app" click-through, no Google review). `scripts/oauth_setup.py`
runs the consent flow (`run_local_server`, not the deprecated OOB flow), creates
the mirror folder, and prints the four secrets.

**Dual credential, by necessity.** `drive.file` does NOT grant Sheets access, so
Sheet reads/writes stay on the **service account** while only the PDF upload uses
the **OAuth client**. The `archive.yml` workflow therefore needs FIVE inputs:
`GDRIVE_SA_KEY` + `GSHEET_ID` (Sheets) and `GOAUTH_CLIENT_ID` /
`GOAUTH_CLIENT_SECRET` / `GOAUTH_REFRESH_TOKEN` / `GOAUTH_ARCHIVE_FOLDER_ID`
(Drive).

**Decoupled from the main pipeline.** The archiver does not run inside
backfill/watcher. It reads `_state` for the processed doc IDs, **joins them to a
fresh `fetch_site_documents()` call** for the download URL + metadata (`_state`
stores classification fields but not `doc_url`), skips what's already in
"Archived PDFs", and mirrors the rest in batches. It grows alongside the backfill
and never blocks classification or alerts. The cost is a second nSITE download
per doc — cheap, and worth the decoupling.

**A separate visible tab, not edits to the existing tabs.** Writing the durable
link into the New/Historical/Evidence rows would need fragile cross-tab cell
updates and risks Sheets **formula injection** on document names beginning with
`=`. Instead "Archived PDFs" (`Doc ID | Document Name | Date Filed | Risks |
Source (nSITE) Link | Archive Link | Archived At`) is the durable index; the
Evidence-by-Risk artifact keeps its nSITE links for live reference.

**Audience.** Files the app creates are private to Trisha by default. Because the
Conservancy should be able to open Archive Links and these are already-public
EGLE filings, the **mirror folder is shared once, manually, as "Anyone with the
link → Viewer"** (sharing cascades to the PDFs inside). Done in the Drive UI, not
programmatically — `drive.file` is not relied on to grant `permissions.create`.

## Adversarial review (folded into the implementation)

- **Refresh token revoked / invalid** (password change, manual revoke, 6-month
  idle). *Detection:* the archiver refreshes the token at startup and, once
  `GOAUTH_*` is configured, **exits non-zero on auth failure** so the GitHub
  workflow-failure email fires — a silent skip would let the mirror fall behind
  invisibly. *Recovery:* re-run `oauth_setup.py`, update `GOAUTH_REFRESH_TOKEN`.
- **Not configured at all.** Distinct from the above: with no `GOAUTH_*` set the
  archiver is a **quiet no-op (exit 0)**, like the SMTP path — archiving is
  optional and the core monitor must not fail because it's off.
- **Trisha's Drive fills up.** OAuth uses her quota, so uploads 403 on a full
  Drive. *Detection:* workflow failure. *Recovery:* free space; PDFs are small
  and the job resumes (append-after-upload + find-in-folder make it idempotent).
- **Crash mid-doc.** Upload happens BEFORE the index row is written; a crash
  between them re-uploads next run, deduped by `find_in_folder`. No silent drop,
  at worst a transient orphan that the next run reuses.
- **Doc already gone from nSITE** (the exact link-rot case, but pre-existing).
  Can't download what's already removed; the archiver logs it as `missing` and
  moves on. Mitigation is simply running this job *early* so the window of
  unmirrored docs is small.
- **Manual deletion of a mirrored file.** Under `drive.file` the app loses sight
  of a file it no longer can see and would re-create it next run — acceptable.

## Consequences

- A new optional subsystem: `archive_client.py`, `archiver.py`,
  `scripts/oauth_setup.py`, `.github/workflows/archive.yml`, the "Archived PDFs"
  tab, and `google-auth-oauthlib` (only the archive path imports it).
- `archive.yml` is safe to leave scheduled from day one: it idles (no-op) until
  the `GOAUTH_*` secrets exist, then mirrors whatever the backfill has processed.
- The durable mirror is one-way (nSITE → Drive). It is insurance, not the system
  of record; the Sheet + `_state` remain authoritative for what was processed.
