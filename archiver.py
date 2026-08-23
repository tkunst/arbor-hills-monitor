"""
archiver.py — mirror every processed N2688 PDF into Trisha's Drive (ADR 007).

The durable-copy insurance against nSITE link rot: Sheet rows link to EGLE's
canonical URL (ADR 006), which dies if EGLE removes or re-IDs a document. This
job keeps a permanent copy in a Drive folder Trisha owns and records each one in
the "Archived PDFs" tab.

TWO ways a document gets mirrored (ADR 007 addendum, 2026-08-23):
  1. mirror_one_now() — called INLINE from watcher.py/backfill.py the moment a
     document is first processed, so its Sheet row and any alert email can link
     to the durable Drive copy immediately, not up to ~21h later. This is now
     the PRIMARY path for new documents. See mirror_one_now()'s own docstring
     for why a mirror link is needed at all, not just nicer (nSITE's referer
     rejection).
  2. run() below (this module's own scheduled job, still 3am ET nightly) — a
     CATCH-UP NET for anything mirror_one_now() missed (Drive/OAuth was down
     that moment, a transient error) plus the pre-2026-08-23 backlog. It is
     idempotent against mirror_one_now() via the same "Archived PDFs" index
     (sw.archived_doc_links()/archived_doc_ids()) both paths read and write, so
     the two never double-upload the same doc_id.
Neither path blocks or degrades classification/alerting: mirror_one_now() ALWAYS
falls back to the nSITE link on any failure (never raises to its caller); run()
below is a fully separate, decoupled job that alerting has no dependency on.

DUAL CREDENTIAL (intentional): Sheet reads/writes go through the SERVICE ACCOUNT
(drive.file does not grant Sheets access); the PDF upload goes through the OAUTH
user client (the service account has no Drive quota). So a workflow calling
either path needs five env vars: GDRIVE_SA_KEY + GSHEET_ID, plus the four
GOAUTH_* (see archive_client) — watcher.py's/backfill.py's workflows now carry
these too, not just archive.yml's.

run()'s own behavior, unchanged:
  - Not configured (no GOAUTH_*): quiet no-op, exit 0 (archiving is optional).
  - Configured but the token is dead: loud failure, exit 1 — a silent skip would
    let the mirror fall behind invisibly, defeating the whole point of this
    being a dedicated, monitorable catch-up job (mirror_one_now(), by contrast,
    treats the identical dead-token condition as a quiet per-run fallback to
    nSITE links — the right behavior differs by caller, not by mistake: this
    job's entire purpose is archiving, so a dead token deserves to be loud
    here; watcher.py's/backfill.py's purpose is alerting/classification, where
    archiving is a bonus that must degrade silently instead of blocking them).

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


def _mirror_and_record(session, drive, sheets, sheet_id: str, doc: dict,
                        document_name: str, date_filed: str, risks, nsite_link: str,
                        local_path: str | None = None) -> str:
    """Upload doc to Drive and record the Archived PDFs index row, returning
    the Drive link. RAISES on any failure (download, upload, or Sheet write)
    — callers decide what "failure" means for them: run()'s batch loop logs
    and moves to the next doc; mirror_one_now() below catches this and falls
    back to nsite_link. Kept as one function so the upload+record sequence —
    and its crash-safety ordering (upload the durable copy FIRST, record the
    index row second, so a crash between them just re-uploads next time via
    find_in_folder's idempotency) — is defined exactly once rather than
    duplicated between the batch and inline call sites.

    `local_path`: if the caller ALREADY has the PDF on disk (watcher.py/
    backfill.py download every doc anyway, to parse it), pass that path to
    avoid fetching the same document from nSITE twice — the file is left
    untouched afterward, since the caller owns its lifecycle. If omitted
    (run()'s own batch loop, which has no other reason to have the PDF
    already), this downloads it to a temp path and cleans up after itself."""
    did = doc["doc_id"]
    srn = doc.get("facility_srn", "N2688")
    if local_path:
        link = ac.upload_pdf(drive, local_path, f"{srn}_{did}.pdf", ac.folder_id())
        sw.append_archive_row(
            sheets, sheet_id, did, document_name, date_filed, risks, nsite_link, link, _now())
        return link

    tmp = tempfile.gettempdir()
    local = os.path.join(tmp, f"{srn}_{did}.pdf")
    try:
        nc.download_pdf(session, doc, local)
        link = ac.upload_pdf(drive, local, f"{srn}_{did}.pdf", ac.folder_id())
        sw.append_archive_row(
            sheets, sheet_id, did, document_name, date_filed, risks, nsite_link, link, _now())
        return link
    finally:
        if os.path.exists(local):
            os.remove(local)


def mirror_one_now(session, sheets, sheet_id: str, doc: dict, drive_state: dict,
                    document_name: str = "", date_filed: str = "", risks=None,
                    local_path: str | None = None) -> str:
    """Best-effort, SYNCHRONOUS single-document Drive mirror — called inline
    from watcher.py/backfill.py at write time (not on the nightly archive.yml
    schedule), so a same-day Sheet row / alert email for a BRAND NEW document
    can link to the durable Drive copy instead of the referer-fragile nSITE
    link, without waiting for archive.yml's next 3am run (which can only
    mirror docs already in `_state.processed` from a PRIOR run — a same-day
    new filing would otherwise go a full cycle, up to ~21h, before getting a
    mirror; see ADR 007's addendum).

    WHY a mirror link is needed at all, not just nicer: nSITE's own
    `downloadpdf`/`downloadfile` endpoints reject requests whose Referer
    header isn't one they recognize (confirmed live: HTTP 500/401 when the
    Referer is a google.com origin) — which is exactly what happens when a
    human clicks the link from inside Gmail or a Google Sheets cell, since
    Google's own link-click-through routes through a google.com-hosted
    redirect first. A Drive-hosted link is immune: the destination IS a
    Google service, so it isn't rejected regardless of how it was reached
    (confirmed live: the same referer that 500s against nSITE returns a
    real PDF, no auth wall, straight from a Drive mirror link).

    ALWAYS returns a usable link — doc["doc_url"] (the nSITE link) on ANY
    failure: archiving not configured, a dead/revoked OAuth token, a
    network error, a Drive quota problem, a Sheet write failure. Mirroring
    is a bonus layered on top of the core write/alert path; it must never
    block or degrade it (ADR 007's resilience principle — preserved here
    even though the mirror now runs inline rather than on its own separate
    schedule: merging WHEN it runs is not the same as making the alert
    DEPEND on it succeeding). Every failure is logged, never raised.

    `drive_state` is a plain dict the CALLER owns and reuses across an
    entire watcher.py/backfill.py run (not created fresh per document), so
    the OAuth handshake and the one Archived PDFs index read happen AT MOST
    ONCE per run — not once per new document. Shape: {} on first call;
    populated with "service" (the Drive service, or None if unavailable
    this run) and "links" ({doc_id: archive_link} from archived_doc_links())
    the first time this function is called, then reused as-is on every
    subsequent call within the same run.

    `local_path`: pass the PDF's already-downloaded path if the caller has
    one (watcher.py/backfill.py always do, to parse it) so this never
    re-fetches the same document from nSITE a second time — see
    _mirror_and_record's own docstring."""
    nsite_link = doc["doc_url"]
    doc_id = doc["doc_id"]

    if not ac.is_configured():
        return nsite_link

    if "service" not in drive_state:
        try:
            drive_state["service"] = ac.oauth_drive_service()
            drive_state["links"] = sw.archived_doc_links(sheets, sheet_id)
        except Exception as e:  # noqa: BLE001 — best-effort for the whole run
            print(f"  [mirror] OAuth Drive unavailable this run, using nSITE "
                  f"links: {e}")
            drive_state["service"] = None
            drive_state["links"] = {}

    drive = drive_state["service"]
    if drive is None:
        return nsite_link

    existing = drive_state["links"].get(doc_id)
    if existing:
        return existing

    try:
        link = _mirror_and_record(
            session, drive, sheets, sheet_id, doc,
            document_name or doc.get("document_name", ""),
            date_filed or doc.get("date_filed", ""),
            risks or [], nsite_link,
            local_path=local_path,
        )
        drive_state["links"][doc_id] = link
        return link
    except Exception as e:  # noqa: BLE001 — this doc keeps its nSITE link
        print(f"  [mirror] {doc_id}: mirror failed, using nSITE link: {e}")
        return nsite_link


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
        try:
            _mirror_and_record(
                session, drive, sheets, sheet_id, meta,
                payload.get("document_name") or meta.get("document_name", ""),
                payload.get("date_filed") or meta.get("date_filed", ""),
                payload.get("risks", []),
                meta.get("doc_url", ""),
            )
            mirrored += 1
            print(f"  ok  {meta.get('date_filed','')}  {meta.get('document_name','')[:50]}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {did}: {e}")

    remaining = len([did for did in processed if did not in already]) - mirrored
    print(f"[archive] mirrored {mirrored} this run; {missing} missing from nSITE; "
          f"{max(remaining, 0)} remaining.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
