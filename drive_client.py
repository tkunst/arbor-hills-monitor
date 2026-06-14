"""
drive_client.py — Google Drive API access (service account, folder ID).

GitHub Actions runners cannot see a local Mac Drive mount, so ALL file I/O goes
through the Drive API:
  - download Documents-folder PDFs by listing the folder,
  - upload downloaded + OCR'd PDFs back to the same folder,
  - read/write the canonical state file (egle-n2688-state.json) in that folder.

Auth: a service-account key JSON whose path is in GDRIVE_SA_KEY. Share the folder
(and the .gsheet) with the service-account email — no OAuth dance, no per-user
credential in the repo. The service-account email is not sensitive; only the key
JSON is. See scripts/setup_gcp.md.
"""
from __future__ import annotations

import io
import json
import os
from typing import Optional

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

STATE_FILENAME = "egle-n2688-state.json"


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


# ---------------------------------------------------------------------------
# State file (canonical copy lives in Drive, so it survives across runners)
# ---------------------------------------------------------------------------


def read_state(service, folder_id: str) -> dict:
    """Return the state dict, or a fresh empty state if the file doesn't exist."""
    file_id = find_file_by_name(service, folder_id, STATE_FILENAME)
    if not file_id:
        return {"processed": {}}
    buf = io.BytesIO()
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    try:
        return json.loads(buf.getvalue().decode("utf-8"))
    except Exception:
        return {"processed": {}}


def write_state(service, folder_id: str, state: dict, tmp_dir: str = "/tmp") -> str:
    """Write the state dict back to Drive (create or update). Returns file ID."""
    local = os.path.join(tmp_dir, STATE_FILENAME)
    with open(local, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    return upload_file(service, folder_id, local, STATE_FILENAME, mime_type="application/json")
