"""
civicclerk_archiver.py — mirror DPW (Board of Public Works, categoryId 68) and
Board of Commissioners (categoryId 26/27) agenda/minutes/other PDFs from
CivicClerk into Trisha's Drive (ADR 037).

WHY A NEW MODULE (not an extension of mmpc_archiver.py): mmpc_archiver.py
(Mirror D, ADR 010) is hardcoded to one category (72, MMPC) and one Drive
folder (GOAUTH_MMPC_FOLDER_ID) and has been running live, nightly, since
2026-07-11. This module reuses the exact same fetch/download/upload/dedup
building blocks (mmpc_client.fetch_mmpc_files/iter_new_files/download_file,
archive_client.upload_pdf) but drives them from a CONFIGURABLE list of
mirrors — {category_id, folder_env, group} triples — so it can fan out to
multiple categories and multiple destination Drive folders in one run,
without touching Mirror D's tested, live code path at all.

DUAL CREDENTIAL (same pattern as mmpc_archiver.py/archiver.py): Sheet reads/
writes go through the SERVICE ACCOUNT; PDF uploads go through the shared
OAUTH user client (archive_client.py) — the SAME GOAUTH_CLIENT_ID/SECRET/
REFRESH_TOKEN Mirror D already uses, just pointed at different destination
folders per mirror entry.

DEDUP: exactly Mirror D's idiom — CivicClerk file IDs are a single global
sequence across every category, so ONE Sheet tab (CivicClerk Archived Files)
and one "already archived" set (civicclerk_archived_file_ids) covers every
mirror; a file already archived under one mirror entry is never re-fetched
under another.

PER-MIRROR FAIL-SAFES (the one meaningful difference from Mirror D, which only
ever has one category/folder to worry about):
  - a mirror whose folder_env secret isn't set yet is SKIPPED (not a hard
    failure) — lets Trisha activate DPW and BOC independently as each
    folder's secret gets provisioned, rather than requiring both at once;
  - a mirror's CivicClerk fetch failing does not abort OTHER mirrors in the
    same run (isolated per-mirror, like run_historical_backfill's per-category
    loop in civicclerk_watcher.py) — but the run still exits non-zero so a
    persistent failure doesn't look silently healthy;
  - one file's download/upload failure never aborts the batch (same as
    Mirror D).

GATED on civicclerk_archive.enabled — ships false (new external-write
capability against a live third-party API; Trisha activates once the Drive
folders + secrets exist, same three-step pattern as every other new stream in
this repo). Runs on its own schedule (.github/workflows/civicclerk-archive.yml).
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


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/Drive/network mocking, same
    class of test as mmpc_archiver._should_run(). Credential/folder checks are
    NOT part of this gate (unlike mmpc_archiver's single-folder check) because
    they're evaluated per-mirror in run() — a missing folder secret for ONE
    mirror must not block another mirror whose secret IS set."""
    if not (cfg.get("civicclerk_archive") or {}).get("enabled"):
        return False, "civicclerk_archive.enabled is false — skipping (no-op)."
    return True, ""


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[civicclerk-archive] {reason}")
        return 0

    ac_cfg = cfg.get("civicclerk_archive") or {}
    mirrors = ac_cfg.get("mirrors") or []
    if not mirrors:
        print("[civicclerk-archive] enabled but no mirrors configured — nothing to archive.")
        return 0

    if not all(os.environ.get(k) for k in ac.CREDENTIAL_ENV):
        print("[civicclerk-archive] GOAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN not set — "
              "archiving disabled (no-op). See scripts/oauth_setup.py + docs/decisions/037.")
        return 0

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_civicclerk_archive_tabs(sheets, sheet_id)

    # OAuth Drive: fail LOUDLY here if the shared refresh token is revoked/
    # invalid, so the workflow-failure email fires instead of every mirror
    # silently stalling (same reasoning as mmpc_archiver.py).
    try:
        drive = ac.oauth_drive_service()
    except Exception as e:  # noqa: BLE001
        print(f"[civicclerk-archive] OAuth Drive auth FAILED ({e}). Re-run "
              f"scripts/oauth_setup.py and update the GOAUTH_REFRESH_TOKEN secret.")
        return 1

    session = mc.make_session()
    already = sw.civicclerk_archived_file_ids(sheets, sheet_id)
    tmp = tempfile.gettempdir()
    exit_code = 0
    total_mirrored = 0

    for mirror in mirrors:
        category_id = (mirror or {}).get("category_id")
        folder_env = (mirror or {}).get("folder_env")
        group = (mirror or {}).get("group") or f"category {category_id}"
        if category_id is None or not folder_env:
            print(f"[civicclerk-archive] skipping malformed mirror entry: {mirror!r}")
            continue
        folder = os.environ.get(folder_env)
        if not folder:
            print(f"[civicclerk-archive] {group}: {folder_env} not set — "
                  f"skipping this mirror (no-op).")
            continue

        try:
            files = mc.fetch_mmpc_files(session, category_id)
        except mc.MMPCFetchError as e:
            # A failed/partial fetch must never be treated as "nothing
            # published" — skip THIS mirror (not the whole run, unlike
            # mmpc_archiver's single-category abort) but surface it loudly.
            print(f"[civicclerk-archive] {group}: fetch failed, skipping this "
                  f"mirror this run: {e}")
            exit_code = 1
            continue

        todo = list(mc.iter_new_files(files, already))
        print(f"[civicclerk-archive] {group}: {len(files)} published file(s), "
              f"{len(todo)} new.")
        if not todo:
            continue

        for f in todo:
            file_id = f["file_id"]
            local = os.path.join(tmp, f"{file_id}.pdf")
            try:
                mc.download_file(session, file_id, local)
                link = ac.upload_pdf(drive, local, f"{file_id}.pdf", folder)
                sw.append_civicclerk_archive_row(
                    sheets, sheet_id, file_id, (f["event_date"] or "")[:10], f["type"],
                    f["name"], f["event_id"], group, link, _now(),
                )
                already.add(str(file_id))  # in-run dedup guard against the
                                            # same file_id appearing via more
                                            # than one mirror entry this run
                total_mirrored += 1
                print(f"  ok  [{group}]  {(f['event_date'] or '')[:10]}  "
                      f"[{f['type']}]  {f['name'][:60]}")
            except Exception as e:  # noqa: BLE001 — one file's failure must not abort the batch
                print(f"  ERR [{group}] fileId={file_id}: {e}")
            finally:
                if os.path.exists(local):
                    os.remove(local)

    print(f"[civicclerk-archive] mirrored {total_mirrored} new file(s) this run.")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
