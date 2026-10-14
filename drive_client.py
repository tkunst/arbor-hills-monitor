"""
drive_client.py — Google auth + the two API service builders used on the deploy
path, plus Drive file helpers kept for a future archive job.

Auth: a service-account key JSON whose path is in GDRIVE_SA_KEY. Share the target
Sheet with the service-account email as Editor — no OAuth dance, no per-user
credential in the repo. The service-account email is not sensitive; only the key
JSON is. See scripts/setup_gcp.md.

DEPLOY PATH = SHEETS ONLY. A service account on a personal Gmail has NO Drive
storage quota: it can edit a Sheet the user owns, but it CANNOT create files in
that user's My Drive. So the monitor neither archives PDFs to Drive nor stores a
JSON state file there — Sheet rows link to the canonical nSITE source URL, and
processing state lives in the Sheet's _state/_meta tabs (see ADR 006, which
supersedes ADR 001). The Drive helpers below (list/find/download/upload) are
intentionally OFF the deploy path; they are retained for a future OAuth-as-user
archive job that would re-add a durable PDF mirror without the quota limit.

ADDENDUM (2026-10-14, the backfill-mirror-ocr hang — see ADR 069's own
addendum for the full incident writeup): `drive_service()`/`sheets_service()`
below, and `archive_client.oauth_drive_service()`, now build their http
transport explicitly via `authorized_http()` instead of handing `build()` a
bare `credentials=`. **Corrected framing, found by direct inspection of the
installed library before writing this fix:** `build(credentials=...)`
already resolves, internally, to `googleapiclient.http.build_http()`, which
sets `httplib2.Http(timeout=60)` by default — NOT `timeout=None` as it first
appeared from testing a bare `httplib2.Http()` in isolation. So this change
does not newly bound a previously-unbounded call; `HTTP_TIMEOUT_S` below is
set to that SAME 60s default on purpose, to not loosen anything. What it
does do: makes that bound explicit and in-repo (previously it was tribal
knowledge buried in a third-party library default, invisible to anyone
reading this file), and gives tests something concrete to assert on — see
`tests/test_drive_client.py`.
"""
from __future__ import annotations

import os
from typing import Optional

# Explicit, in-repo version of googleapiclient's own 60s default (see the
# ADDENDUM above) for every httplib2-backed Drive/Sheets call this module
# and archive_client.py make. GOOGLE_API_NUM_RETRIES below already treats a
# socket timeout as transient and retries it, so this composes with that
# retry logic rather than fighting it (worst case per logical call: up to
# GOOGLE_API_NUM_RETRIES attempts at up to HTTP_TIMEOUT_S each, plus backoff
# — see GOOGLE_API_NUM_RETRIES's own comment).
HTTP_TIMEOUT_S = 60


def authorized_http(credentials, timeout: int = HTTP_TIMEOUT_S):
    """An AuthorizedHttp wrapping an httplib2.Http with an EXPLICIT socket
    timeout, for `build(http=authorized_http(creds), ...)` in place of
    `build(credentials=creds, ...)` — the two are mutually exclusive on
    build() (it raises ValueError if both are given). google_auth_httplib2
    and httplib2 are both already pinned in requirements.txt for other
    reasons (see requirements.in's own comment) — no new dependency.

    HIGH finding, fixed before merge (Step 5 independent review,
    2026-10-14): the first version of this function built a BARE
    `httplib2.Http(timeout=timeout)` directly, which drops something
    `googleapiclient.http.build_http()` — what `build(credentials=...)`
    was already using internally before this PR — does on top of setting
    the timeout: `http.redirect_codes = http.redirect_codes - {308}`.
    308 is used by Drive/YouTube resumable uploads as "Resume Incomplete,"
    not a real redirect; without that line, httplib2 tries to follow a
    mid-upload 308 as an actual redirect (no `Location` header present)
    and the upload fails. Every resumable upload in this codebase
    (`archive_client.upload_file()`, `archive_client.update_file_content()`
    — this backfill script's own re-upload step — and drive_client's own
    upload helper, all `MediaFileUpload(..., resumable=True)`) would have
    broken on any file large enough to span multiple chunks. Confirmed
    directly against this repo's pinned libraries (not just upstream
    source): `build_http().redirect_codes` excludes 308; a bare
    `httplib2.Http()` includes it; the original `authorized_http()`
    return value included it too. Fixed by building on `build_http()`
    itself (so `redirect_codes` is correct) and only overriding `.timeout`
    to make it an explicit, in-repo value instead of relying on
    `build_http()`'s own `socket.getdefaulttimeout()`-dependent default
    resolution."""
    import google_auth_httplib2
    from googleapiclient.http import build_http

    http = build_http()
    http.timeout = timeout
    return google_auth_httplib2.AuthorizedHttp(credentials, http=http)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Passed to every googleapiclient execute() call on the Sheets/Drive path. It turns
# on the library's built-in exponential-backoff retry, which fires on TRANSIENT
# Google-side errors — 5xx (500/502/503/504 "service currently unavailable") and
# 429/403 rate-limit — plus socket/SSL blips. Without it a SINGLE transient error
# aborts a whole watcher run: e.g. the Sheets 503 that failed mmd-watch on
# 2026-08-08 came from ensure_mmd_tabs' first `.get()`, before any diff, and took
# the run down with exit 1 (it self-recovered the next day). A PERSISTENT error
# (bad creds, missing/again-unavailable sheet) still raises after the retries, so
# a real problem still fails loud rather than being masked. 5 attempts back off
# up to ~62s of jittered sleep worst case (2+4+8+16+32s); a timeout-class
# transient (socket.timeout is retried too) adds per-attempt request time on top
# — still well inside every watch job's 15-minute timeout, and a killed job just
# reruns on the next schedule, so there is no correctness risk either way.
GOOGLE_API_NUM_RETRIES = 5


def _credentials():
    from google.oauth2 import service_account

    key_path = os.environ["GDRIVE_SA_KEY"]
    return service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)


def drive_service():
    from googleapiclient.discovery import build

    return build("drive", "v3", http=authorized_http(_credentials()), cache_discovery=False)


def sheets_service():
    from googleapiclient.discovery import build

    return build("sheets", "v4", http=authorized_http(_credentials()), cache_discovery=False)


def list_files(service, folder_id: str) -> list[dict]:
    """Return [{id, name, mimeType}] for all non-trashed files in the folder."""
    out: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute(num_retries=GOOGLE_API_NUM_RETRIES)
        )
        out.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def find_file_by_name(service, folder_id: str, name: str) -> Optional[str]:
    """Return the file ID of a file with this exact name in the folder, or None."""
    safe = name.replace("'", "\\'")
    resp = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and name = '{safe}' and trashed = false",
            fields="files(id, name)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute(num_retries=GOOGLE_API_NUM_RETRIES)
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def download_file(service, file_id: str, dest_path: str) -> str:
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
    return dest_path


def upload_file(
    service,
    folder_id: str,
    local_path: str,
    name: str,
    mime_type: str = "application/pdf",
) -> str:
    """Create the file, or update it in place if a file with that name already
    exists in the folder (idempotent for re-runs). Returns the file ID."""
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    existing = find_file_by_name(service, folder_id, name)
    if existing:
        f = (
            service.files()
            .update(fileId=existing, media_body=media, supportsAllDrives=True)
            .execute(num_retries=GOOGLE_API_NUM_RETRIES)
        )
        return f["id"]
    meta = {"name": name, "parents": [folder_id]}
    f = (
        service.files()
        .create(body=meta, media_body=media, fields="id", supportsAllDrives=True)
        .execute(num_retries=GOOGLE_API_NUM_RETRIES)
    )
    return f["id"]
