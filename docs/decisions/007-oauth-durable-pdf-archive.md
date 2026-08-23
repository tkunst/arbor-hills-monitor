# ADR 007 — Durable PDF archive via OAuth-as-user (optional, automated)

*Status: accepted — 2026-06-14; **activated in production 2026-06-15** — the four
`GOAUTH_*` secrets are set, the mirror folder ("Arbor Hills EGLE Document Mirror")
is created and shared "Anyone with the link → Viewer", and `archive.yml` runs
daily. Mid-backfill as of 2026-06-17 (~1,249 PDFs remaining).*

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

## Addendum (2026-08-23): inline mirroring, and the real reason a mirror link matters

**A second failure mode nSITE link rot didn't cover, found live:** Trisha
reported a stored `downloadpdf/<doc_id>` link that threw an error when she
clicked it, even though the exact same URL worked when pasted directly. Tested
live: `mienviro.michigan.gov`'s `downloadpdf`/`downloadfile` endpoints reject
requests whose `Referer` header isn't one they recognize — confirmed with a
plain `curl -e "https://www.google.com/"` reproducing the identical "Runtime
Error"/401 page from her own screenshots on a link that succeeds with no
referer. This matters far beyond a one-off broken link: **Google's own
link-click-through (opening a link from inside Gmail, or clicking a URL typed
into a Sheets cell) routes through a `google.com`-hosted redirect before
reaching the destination**, so essentially every nSITE link this monitor ever
emailed or wrote into the Sheet was at risk of erroring for a human clicking
it from the two places she'd actually encounter it — not a rare edge case.
Direct verification confirmed the fix: the identical `google.com` referer that
500s against nSITE returns a real PDF, no auth wall, straight from a Drive
mirror link — because the destination is a Google service, immune to nSITE's
own referer check by construction.

**This means the mirror isn't just link-rot insurance anymore — it's the
functional fix for referer rejection, and the timing of WHEN a document gets
mirrored now matters in a way it didn't before.** Under the original design,
`archive.yml`'s nightly 3am run can only mirror doc_ids already present in
`_state.processed`, populated by watcher.py/backfill.py runs. A same-day new
filing is discovered by the 6am watcher AFTER that day's 3am archive run
already happened — so it would wait a FULL cycle (up to ~21h) for a Drive
mirror, during which its Sheet row and any alert email carry only the
referer-fragile nSITE link. For a same-day urgent alert — exactly the kind of
document most likely to be opened immediately — that gap defeats the whole
purpose.

**Fix: `archiver.mirror_one_now()`, called INLINE from watcher.py/backfill.py
at write time**, not only on `archive.yml`'s own schedule. There was no
principled reason left to keep mirroring exclusively on a separate nightly
job — that separation was how this ADR happened to sequence the original
build ("it grows alongside the backfill"), not a resilience requirement in
its own right. The resilience requirement — mirroring must never block or
degrade classification/alerting — is unchanged and is preserved explicitly:
`mirror_one_now()` ALWAYS falls back to the nSITE link on any failure (not
configured, a dead/revoked OAuth token, a network error, a Drive quota
problem, a Sheet-write failure) and never raises to its caller. Merging WHEN
the mirror runs is not the same as making the alert DEPEND on it succeeding.

`archive.yml`'s own scheduled `run()` is unchanged in mechanism and kept
running — it is now a **catch-up net**: anything `mirror_one_now()` missed in
the moment (a transient OAuth/Drive blip) plus the pre-2026-08-23 backlog.
Both paths read and write the same "Archived PDFs" index
(`sheet_writer.archived_doc_links()`/`archived_doc_ids()`), so they coordinate
naturally and never double-upload the same doc_id.

**Consequence for credential exposure:** `daily.yml` (watcher.py) and
`backfill.yml` now also carry the four `GOAUTH_*` secrets, previously scoped
to `archive.yml` alone. Unset, `mirror_one_now()` behaves exactly as before
this addendum (quiet fallback to the nSITE link) — no new failure mode, only
a new success path.

**Scope note:** this addendum covers the GO-FORWARD path only — new documents
processed from 2026-08-23 onward. It does not retroactively rewrite the
~1,720 already-written nSITE links sitting in the New/Historical Documents
tabs (a separate, explicitly scoped decision — see the repair-plan discussion
in the session notes — since many already have a mirror in "Archived PDFs"
and could be swapped, but that touches the public/operator-visible Sheet's
existing cell contents and needs its own confirmation, not a silent bundle
into this change).
