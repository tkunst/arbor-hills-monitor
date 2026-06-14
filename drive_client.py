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
"""
from __future__ import annotations

import os
from typing import Optional

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _credentials():
    from google.oauth2 import service_account

    key_path = os.environ["GDRIVE_SA_KEY"]
    return service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)


def drive_service():
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def sheets_service():
    from googleapiclient.discovery import build

    return build("sheets", "v4", credentials=_credentials(), cache_discovery=False)


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
            .execute()
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
        .execute()
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
            .execute()
        )
        return f["id"]
    meta = {"name": name, "parents": [folder_id]}
    f = (
        service.files()
        .create(body=meta, media_body=media, fields="id", supportsAllDrives=True)
        .execute()
    )
    return f["id"]
