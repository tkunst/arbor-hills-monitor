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

    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


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
        .execute()
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def upload_file(service, local_path: str, name: str, mimetype: str, folder_id: str) -> str:
    """Upload (or reuse) one file in the given folder. Returns its webViewLink —
    the durable, shareable URL recorded in the Archived PDFs / WDS Page Snapshots
    / MMPC Archived Files tabs. Idempotent per find_in_folder()'s caveat above.
    Shared by archiver.py (nSITE PDFs), wds_archiver.py (HTML page snapshots),
    and mmpc_archiver.py (MMPC PDFs) — same drive.file scope, different folder
    IDs and mimetypes."""
    from googleapiclient.http import MediaFileUpload

    existing = find_in_folder(service, name, folder_id)
    if existing:
        got = service.files().get(fileId=existing, fields="webViewLink").execute()
        return got["webViewLink"]

    media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
    meta = {"name": name, "parents": [folder_id]}
    f = (
        service.files()
        .create(body=meta, media_body=media, fields="id, webViewLink")
        .execute()
    )
    return f["webViewLink"]


def upload_pdf(service, local_path: str, name: str, folder_id: str) -> str:
    """Thin wrapper over upload_file() for a PDF mirror (archiver.py, mmpc_archiver.py)."""
    return upload_file(service, local_path, name, "application/pdf", folder_id)
