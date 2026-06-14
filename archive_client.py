"""
archive_client.py — Google Drive access for the durable PDF mirror, authenticated
as Trisha via OAuth (NOT the service account).

Why OAuth and not the service account: a service account on a personal Gmail has
no Drive storage quota and cannot create files (that limit is the whole reason
for ADR 006). OAuth-as-user uses Trisha's own quota, so it CAN create the mirror
folder and upload PDFs. Scope is restricted to `drive.file` — the token can only
see and manage files this app itself created, never the rest of her Drive.

Credentials come from four env vars (see scripts/oauth_setup.py, which mints
them): GOAUTH_CLIENT_ID, GOAUTH_CLIENT_SECRET, GOAUTH_REFRESH_TOKEN, and
GOAUTH_ARCHIVE_FOLDER_ID (the mirror folder, created once during setup so
rediscovery is a fixed ID rather than a name search).

This module is import-light: the google OAuth libs are imported lazily so the
core monitor (which never archives) doesn't need google-auth-oauthlib.
"""
from __future__ import annotations

import os

# drive.file: per-file access limited to files created by THIS app. Enough to
# create the mirror folder, upload into it, and list/skip what's already there.
OAUTH_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

REQUIRED_ENV = (
    "GOAUTH_CLIENT_ID",
    "GOAUTH_CLIENT_SECRET",
    "GOAUTH_REFRESH_TOKEN",
    "GOAUTH_ARCHIVE_FOLDER_ID",
)


def is_configured() -> bool:
    """True only if all four OAuth env vars are present. The archiver no-ops
    quietly when this is False (like SMTP) — archiving is optional."""
    return all(os.environ.get(k) for k in REQUIRED_ENV)


def folder_id() -> str:
    return os.environ["GOAUTH_ARCHIVE_FOLDER_ID"]


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


def find_in_folder(service, name: str) -> str | None:
    """Return the file ID of a file with this exact name in the mirror folder, or
    None. Under drive.file the listing only sees files this app created, which is
    exactly the mirror — so this is the idempotency check for re-runs."""
    safe = name.replace("'", "\\'")
    resp = (
        service.files()
        .list(
            q=f"'{folder_id()}' in parents and name = '{safe}' and trashed = false",
            fields="files(id, name)",
            pageSize=1,
        )
        .execute()
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def upload_pdf(service, local_path: str, name: str) -> str:
    """Upload (or reuse) one PDF in the mirror folder. Returns its webViewLink —
    the durable, shareable URL recorded in the Archived PDFs tab. Idempotent:
    if a file of that name already exists it is reused, not duplicated."""
    from googleapiclient.http import MediaFileUpload

    existing = find_in_folder(service, name)
    if existing:
        got = service.files().get(fileId=existing, fields="webViewLink").execute()
        return got["webViewLink"]

    media = MediaFileUpload(local_path, mimetype="application/pdf", resumable=True)
    meta = {"name": name, "parents": [folder_id()]}
    f = (
        service.files()
        .create(body=meta, media_body=media, fields="id, webViewLink")
        .execute()
    )
    return f["webViewLink"]
