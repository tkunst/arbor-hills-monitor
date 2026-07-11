"""
archiver.py — mirror every processed N2688 PDF into Trisha's Drive (ADR 007).

The durable-copy insurance against nSITE link rot: Sheet rows link to EGLE's
canonical URL (ADR 006), which dies if EGLE removes or re-IDs a document. This
job keeps a permanent copy in a Drive folder Trisha owns and records each one in
the "Archived PDFs" tab. Decoupled from the main pipeline: it grows alongside
the backfill and never blocks classification or alerts.

DUAL CREDENTIAL (intentional): Sheet reads/writes go through the SERVICE ACCOUNT
(drive.file does not grant Sheets access); the PDF upload goes through the OAUTH
user client (the service account has no Drive quota). So the workflow needs five
env vars: GDRIVE_SA_KEY + GSHEET_ID, plus the four GOAUTH_* (see archive_client).

  - Not configured (no GOAUTH_*): quiet no-op, exit 0 (archiving is optional).
  - Configured but the token is dead: loud failure, exit 1 — a silent skip would
    let the mirror fall behind invisibly, defeating the whole point.

Runs nightly at 3am ET (after the 2am backfill). Self-terminating.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import drive_client as dc
import sheet_writer as sw
import nsite_client as nc
import archive_client as ac
from config_loader import load_config


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run() -> int:
    if not ac.is_configured():
        print("[archive] GOAUTH_* not set — archiving disabled (no-op). "
              "See scripts/oauth_setup.py + docs/decisions/007.")
        return 0

    cfg = load_config()
    sheet_id = os.environ["GSHEET_ID"]
    batch_size = (cfg.get("archive") or {}).get("batch_size", 100)

    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)

    # OAuth Drive: fail LOUDLY here if the refresh token is revoked/invalid, so
    # the workflow-failure email fires instead of the mirror silently stalling.
    try:
        drive = ac.oauth_drive_service()
    except Exception as e:  # noqa: BLE001
        print(f"[archive] OAuth Drive auth FAILED ({e}). Re-run scripts/oauth_setup.py "
              f"and update the GOAUTH_REFRESH_TOKEN secret.")
        return 1

    state = sw.read_state(sheets, sheet_id)
    processed = state["processed"]
    already = sw.archived_doc_ids(sheets, sheet_id)

    # Join processed doc IDs to the live nSITE list for the download URL + names.
    # (_state stores classification metadata but not doc_url — ADR 007.)
    session = nc.make_session()
    docs = nc.fetch_all_documents(session, cfg)
    if not docs:
        print("[archive] nSITE returned 0 documents — aborting (transient?).")
        return 1
    by_id = {d["doc_id"]: d for d in docs}

    todo = [did for did in processed if did not in already]
    print(f"[archive] {len(processed)} processed, {len(already)} archived, "
          f"{len(todo)} to mirror.")
    if not todo:
        print("[archive] Archive complete — nothing to mirror.")
        return 0

    tmp = tempfile.gettempdir()
    mirrored = 0
    missing = 0
    for did in todo[:batch_size]:
        meta = by_id.get(did)
        payload = processed.get(did) or {}
        if not meta:
            # Processed once but no longer in nSITE — the exact link-rot case the
            # mirror guards against, but if it is already gone we cannot fetch it.
            missing += 1
            print(f"  skip {did}: not in current nSITE list (cannot download).")
            continue
        srn = meta.get("facility_srn", "N2688")
        local = os.path.join(tmp, f"{srn}_{did}.pdf")
        try:
            nc.download_pdf(session, meta, local)
            # Upload (durable copy) FIRST, then record the index row — a crash
            # between them re-uploads next run, made idempotent by find_in_folder.
            link = ac.upload_pdf(drive, local, f"{srn}_{did}.pdf", ac.folder_id())
            sw.append_archive_row(
                sheets, sheet_id, did,
                payload.get("document_name") or meta.get("document_name", ""),
                payload.get("date_filed") or meta.get("date_filed", ""),
                payload.get("risks", []),
                meta.get("doc_url", ""),
                link, _now(),
            )
            mirrored += 1
            print(f"  ok  {meta.get('date_filed','')}  {meta.get('document_name','')[:50]}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {did}: {e}")
        finally:
            if os.path.exists(local):
                os.remove(local)

    remaining = len([did for did in processed if did not in already]) - mirrored
    print(f"[archive] mirrored {mirrored} this run; {missing} missing from nSITE; "
          f"{max(remaining, 0)} remaining.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
