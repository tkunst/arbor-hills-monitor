"""
archive_client.py — Google Drive access for the durable PDF mirror, authenticated
as Trisha via OAuth (NOT the service account).

Why OAuth and not the service account: a service account on a personal Gmail has
no Drive storage quota and cannot create files (that limit is the whole reason
for ADR 006). OAuth-as-user uses Trisha's own quota, so it CAN create the mirror
folder and upload PDFs. Scope is restricted to `drive.file` — the token can only
see and manage files this app itself created, never the rest of her Drive.

Credentials come from three shared env vars (see scripts/oauth_setup.py, which
mints them): GOAUTH_CLIENT_ID, GOAUTH_CLIENT_SECRET, GOAUTH_REFRESH_TOKEN. The
DESTINATION folder is a separate env var per mirror — GOAUTH_ARCHIVE_FOLDER_ID
for Mirror B (nSITE PDFs + WDS page snapshots), GOAUTH_MMPC_FOLDER_ID for
Mirror D (MMPC minutes/agenda/other PDFs, ADR 010) — each folder created once
during setup so rediscovery is a fixed ID rather than a name search. Callers
pass their folder's ID explicitly to find_in_folder()/upload_file() rather than
this module hardcoding a single destination.

This module is import-light: the google OAuth libs are imported lazily so the
core monitor (which never archives) doesn't need google-auth-oauthlib.
"""
from __future__ import annotations

import os

# num_retries for exponential-backoff retry on transient Google Drive 5xx/429
# errors, so a blip doesn't abort an archiver run — same rationale + value as
# the Sheets path (this constant covers Drive too). See drive_client.
# authorized_http(): an explicit, finite HTTP socket timeout for the Drive
# transport this module builds — see drive_client's own ADDENDUM docstring
# (2026-10-14) for why this doesn't change actual behavior (60s was already
# googleapiclient's own default), just makes it explicit and in-repo.
from drive_client import GOOGLE_API_NUM_RETRIES, authorized_http

# drive.file: per-file access limited to files created by THIS app. Enough to
# create the mirror folder, upload into it, and list/skip what's already there.
OAUTH_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

CREDENTIAL_ENV = (
    "GOAUTH_CLIENT_ID",
    "GOAUTH_CLIENT_SECRET",
    "GOAUTH_REFRESH_TOKEN",
)


def is_configured(folder_env: str = "GOAUTH_ARCHIVE_FOLDER_ID") -> bool:
    """True only if the shared credential env vars AND the given mirror's
    folder env var are present. Each archiver (archiver.py, wds_archiver.py,
    mmpc_archiver.py) passes its own folder_env so one mirror being configured
    doesn't falsely report another as ready. No-ops quietly when False (like
    SMTP) — archiving is optional."""
    return (all(os.environ.get(k) for k in CREDENTIAL_ENV)
            and bool(os.environ.get(folder_env)))


def folder_id(folder_env: str = "GOAUTH_ARCHIVE_FOLDER_ID") -> str:
    return os.environ[folder_env]


def _credentials():
    """Build user credentials from the stored refresh token. Raises on a revoked
    or invalid token (the archiver lets that propagate so the workflow fails
    loudly — a silent skip would let the mirror fall behind invisibly)."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOAUTH_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOAUTH_CLIENT_ID"],
        client_secret=os.environ["GOAUTH_CLIENT_SECRET"],
        scopes=OAUTH_SCOPES,
    )
    creds.refresh(Request())  # exchange the refresh token now; fail fast if dead
    return creds


def oauth_drive_service():
    from googleapiclient.discovery import build

    return build("drive", "v3", http=authorized_http(_credentials()), cache_discovery=False)


def find_in_folder(service, name: str, folder_id: str) -> str | None:
    """Return the file ID of a file with this exact name in the given folder, or
    None. Under drive.file the listing only sees files this app created, which is
    exactly the mirror — so this is the idempotency check for re-runs. NOTE: for
    a folder this app didn't create (e.g. Mirror D's pre-existing hand-populated
    MMPC-meeting-minutes/), this only sees files THIS app has previously uploaded
    there, never files placed by hand — so it cannot detect (and will not dedupe
    against) a same-named file Trisha uploaded manually. Harmless in practice:
    each archiver's own Sheet-tab-derived "already archived" set is the real
    dedupe check; this is just a belt-and-suspenders re-run guard."""
    safe = name.replace("'", "\\'")
    resp = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and name = '{safe}' and trashed = false",
            fields="files(id, name)",
            pageSize=1,
        )
        .execute(num_retries=GOOGLE_API_NUM_RETRIES)
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def upload_file(service, local_path: str, name: str, mimetype: str, folder_id: str) -> str:
    """Upload (or reuse) one file in the given folder. Returns its webViewLink —
    the durable, shareable URL recorded in the Archived PDFs / WDS Page Snapshots
    / MMPC Archived Files tabs. Idempotent per find_in_folder()'s caveat above.
    Shared by archiver.py (nSITE PDFs), wds_archiver.py (HTML page snapshots),
    mmpc_archiver.py (MMPC PDFs), civicclerk_archiver.py, ridgewood_archiver.py/
    ridgewood_reverify.py, and govqa_watcher.py/ride_docs_watcher.py's own
    direct upload_file() calls — same drive.file scope, different folder IDs
    and mimetypes.

    coder:mirror-ocr (2026-10-13): every PDF upload (mimetype ==
    "application/pdf") goes through ocr_util.ensure_searchable() first, so
    EVERY upload path funnels through this one choke point — not just the
    nSITE/MMPC/CivicClerk/Ridge Wood archivers that call the upload_pdf()
    wrapper below, but also the two watchers above that call upload_file()
    directly with a conditional PDF mimetype. ensure_searchable() is a no-op
    (returns local_path unchanged, no subprocess) for a file that already has
    a full text layer — which is the common case here, since the watcher/
    backfill pipeline already OCR'd via egle_doc_parser.parse_document()
    before ever reaching this upload. It's fail-open: any OCR failure just
    uploads the original file, never raises, never drops the document.
    `ocr_util` is imported lazily (not at module top) so this module stays
    import-light for callers that never touch a PDF (wds_archiver.py's HTML
    snapshots, the two watchers' non-PDF uploads)."""
    from googleapiclient.http import MediaFileUpload

    existing = find_in_folder(service, name, folder_id)
    if existing:
        got = service.files().get(fileId=existing, fields="webViewLink").execute(num_retries=GOOGLE_API_NUM_RETRIES)
        return got["webViewLink"]

    upload_path = local_path
    if mimetype == "application/pdf":
        import ocr_util

        upload_path = ocr_util.ensure_searchable(local_path)

    try:
        media = MediaFileUpload(upload_path, mimetype=mimetype, resumable=True)
        meta = {"name": name, "parents": [folder_id]}
        f = (
            service.files()
            .create(body=meta, media_body=media, fields="id, webViewLink")
            .execute(num_retries=GOOGLE_API_NUM_RETRIES)
        )
        return f["webViewLink"]
    finally:
        if upload_path != local_path:
            try:
                os.remove(upload_path)
            except OSError:
                pass


def upload_pdf(service, local_path: str, name: str, folder_id: str) -> str:
    """Thin wrapper over upload_file() for a PDF mirror (archiver.py, mmpc_archiver.py)."""
    return upload_file(service, local_path, name, "application/pdf", folder_id)


# ---------------------------------------------------------------------------
# coder:mirror-ocr (2026-10-13), for scripts/backfill_mirror_ocr.py — re-reading
# and in-place-overwriting a file THIS APP already mirrored. Separate from
# drive_client.download_file()/upload_file(): those use the SERVICE ACCOUNT,
# which has no access to this OAuth-as-user-owned mirror (same dual-credential
# split documented throughout this repo — Sheets via service account, Drive via
# OAuth). Kept here rather than in the backfill script so the Drive-I/O surface
# for app-created files stays in one module.
# ---------------------------------------------------------------------------


def download_file(service, file_id: str, dest_path: str) -> str:
    """Download one file this app has access to (drive.file scope: only files
    it created) to dest_path. Used by the OCR backfill to re-read an
    already-mirrored PDF for inspection/repair."""
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk(num_retries=GOOGLE_API_NUM_RETRIES)
    return dest_path


def pin_head_revision(service, file_id: str) -> str:
    """Pin the file's CURRENT head revision with keepForever=True, before its
    content is overwritten. The Drive API purges non-head revisions of a
    binary file ~30 days after new content lands unless pinned — without this,
    overwriting a scan-only mirror with its OCR'd replacement would silently
    lose the original scan-only bytes from revision history a month later,
    with no error at the time it happens. Returns the pinned revision id."""
    meta = (
        service.files()
        .get(fileId=file_id, fields="headRevisionId")
        .execute(num_retries=GOOGLE_API_NUM_RETRIES)
    )
    rev_id = meta["headRevisionId"]
    service.revisions().update(
        fileId=file_id, revisionId=rev_id, body={"keepForever": True},
    ).execute(num_retries=GOOGLE_API_NUM_RETRIES)
    return rev_id


def update_file_content(service, file_id: str, local_path: str,
                        mimetype: str = "application/pdf") -> str:
    """Overwrite a file's content IN PLACE (files().update with a new
    media_body) — SAME file id, so every Sheet link / public-records page
    link / emailed link that points at it keeps working; no files().create,
    ever. Returns the file id (re-confirmed from the response, not just
    echoed back) so the caller can assert it didn't change."""
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
    f = (
        service.files()
        .update(fileId=file_id, media_body=media, fields="id")
        .execute(num_retries=GOOGLE_API_NUM_RETRIES)
    )
    return f["id"]
