"""
mmpc_archiver.py — mirror MMPC agenda/minutes/other PDFs from CivicClerk into
Trisha's Drive (Mirror D, ADR 010), replacing the manual "email alert -> Trisha
downloads and uploads by hand" flow with an automatic one.

DUAL CREDENTIAL (same pattern as archiver.py/wds_archiver.py, see
archive_client.py): Sheet reads/writes go through the SERVICE ACCOUNT; the PDF
upload goes through the OAUTH user client, into the SAME Drive folder Trisha
already hand-populates (MMPC-meeting-minutes/, GOAUTH_MMPC_FOLDER_ID) — a
different folder from Mirror B/C's GOAUTH_ARCHIVE_FOLDER_ID, so this needs its
own secret (see docs/decisions/010).

DEDUP: unlike the WDS page-snapshot archiver (content-hash gated, because a WDS
"record" is a page whose content changes), MMPC files are static per-ID PDFs
once CivicClerk publishes them — exactly like nSITE's model. So this follows
archiver.py's idiom: the "already done" set comes from reading the MMPC
Archived Files Sheet tab itself (sheet_writer.mmpc_archived_file_ids()), not a
separate _meta key.

GATED on mmpc_archive.enabled, same reasoning as WDS's wds.enabled (see
wds_archiver.py's docstring and ADR 009's Addendum, which documents catching
exactly this class of bug — an archiver that only checked OAuth config, not the
feature flag — before merge). Until Trisha sets mmpc_archive.enabled: true,
this workflow runs on schedule but no-ops every time.

  - mmpc_archive.enabled is false, or GOAUTH_*/GOAUTH_MMPC_FOLDER_ID not set:
    quiet no-op, exit 0.
  - Configured but the OAuth token is dead: loud failure, exit 1 — a silent
    skip would let the mirror fall behind invisibly.

Runs on its own schedule (see .github/workflows/mmpc-archive.yml). The existing
mmpc_watcher.py email-reminder flow in watcher.py is left untouched — Trisha's
call whether to retire it now that this exists (see ADR 010's Alternatives).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import drive_client as dc
import sheet_writer as sw
import mmpc_client as mc
import archive_client as ac
from config_loader import load_config

FOLDER_ENV = "GOAUTH_MMPC_FOLDER_ID"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _should_run(cfg: dict, oauth_configured: bool) -> tuple[bool, str]:
    """Pure gate check — testable without any Sheets/Drive/network mocking.
    mmpc_archive.enabled is checked FIRST, mirroring wds_archiver._should_run()
    exactly, for the same reason: the flag is the primary activation switch and
    must win over 'but the credentials happen to be configured already' (they
    are, since GOAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN are shared with Mirror B/C
    and were live long before Mirror D existed)."""
    if not (cfg.get("mmpc_archive") or {}).get("enabled"):
        return False, "mmpc_archive.enabled is false — skipping (no-op)."
    if not oauth_configured:
        return False, (f"GOAUTH_* / {FOLDER_ENV} not set — archiving disabled (no-op). "
                        "See scripts/oauth_setup.py + docs/decisions/010.")
    return True, ""


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg, ac.is_configured(FOLDER_ENV))
    if not should_run:
        print(f"[mmpc-archive] {reason}")
        return 0

    mmpc_cfg = cfg.get("mmpc_archive") or {}
    category_id = mmpc_cfg.get("category_id", 72)
    sheet_id = os.environ["GSHEET_ID"]

    sheets = dc.sheets_service()
    sw.ensure_mmpc_tabs(sheets, sheet_id)

    # OAuth Drive: fail LOUDLY here if the refresh token is revoked/invalid, so
    # the workflow-failure email fires instead of the mirror silently stalling.
    try:
        drive = ac.oauth_drive_service()
    except Exception as e:  # noqa: BLE001
        print(f"[mmpc-archive] OAuth Drive auth FAILED ({e}). Re-run scripts/oauth_setup.py "
              f"and update the GOAUTH_REFRESH_TOKEN secret.")
        return 1

    session = mc.make_session()
    try:
        files = mc.fetch_mmpc_files(session, category_id)
    except mc.MMPCFetchError as e:
        # A failed/partial fetch must never be treated as "CivicClerk published
        # nothing" — abort the run rather than diff against an incomplete list.
        print(f"[mmpc-archive] fetch failed, aborting this run: {e}")
        return 1

    already = sw.mmpc_archived_file_ids(sheets, sheet_id)
    todo = list(mc.iter_new_files(files, already))
    print(f"[mmpc-archive] {len(files)} published file(s) on CivicClerk, "
          f"{len(already)} already archived, {len(todo)} to mirror.")
    if not todo:
        print("[mmpc-archive] nothing new — up to date.")
        return 0

    tmp = tempfile.gettempdir()
    folder = ac.folder_id(FOLDER_ENV)
    mirrored = 0
    for f in todo:
        file_id = f["file_id"]
        local = os.path.join(tmp, f"{file_id}.pdf")
        try:
            mc.download_file(session, file_id, local)
            # Upload (durable copy) FIRST, then record the index row — a crash
            # between them re-uploads next run (idempotent per find_in_folder,
            # modulo the drive.file-scope caveat documented in archive_client.py
            # — this app can't see the ~24 files Trisha already hand-uploaded,
            # only its own prior uploads, but the Sheet-derived `already` set
            # above is the real dedup check, not the Drive listing).
            link = ac.upload_pdf(drive, local, f"{file_id}.pdf", folder)
            sw.append_mmpc_archive_row(
                sheets, sheet_id, file_id, (f["event_date"] or "")[:10], f["type"],
                f["name"], f["event_id"], link, _now(),
            )
            mirrored += 1
            print(f"  ok  {(f['event_date'] or '')[:10]}  [{f['type']}]  {f['name'][:60]}")
        except Exception as e:  # noqa: BLE001 — one file's failure must not abort the batch
            print(f"  ERR fileId={file_id}: {e}")
        finally:
            if os.path.exists(local):
                os.remove(local)

    print(f"[mmpc-archive] mirrored {mirrored} of {len(todo)} new file(s) this run.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
