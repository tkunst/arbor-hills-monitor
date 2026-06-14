# ADR 002 — Google Drive API, not a local Drive mount

*Status: accepted — 2026-06-13*

## Context

During development, the Documents folder and the case-file Sheet are visible on a
Mac as a `CloudStorage/GoogleDrive-.../` mount. It would be tempting to read and
write that path directly.

## Decision

All file I/O goes through the **Google Drive API** with a **service account**,
addressing the folder by its opaque **folder ID** — never a filesystem path.

## Why

- **GitHub Actions runners cannot see the Mac's Drive mount.** The whole point
  is unattended nightly runs in the cloud; a local mount doesn't exist there.
- **No personal data leaks.** `GDRIVE_FOLDER_ID` is an opaque Drive ID, not a
  path containing a personal email/name. The service-account email is not
  sensitive; only its key JSON is (a GitHub Secret).
- **Sharing model is clean.** Trisha shares the folder + Sheet with the
  service-account email manually; no OAuth dance, no per-user token in the repo.

## Consequences

- Inputs (existing PDFs) are found by listing the folder; outputs (downloaded +
  OCR'd PDFs) are uploaded back by the API. See `drive_client.py`.
- Setup is one-time and documented in `scripts/setup_gcp.md`.
