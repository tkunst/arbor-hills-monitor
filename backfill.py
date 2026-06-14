"""
backfill.py — process the existing ~754 N2688 documents, one batch per run.

Seeds from the AUTHORITATIVE nSITE document list (not the unseen Documents.csv —
see docs/decisions/003-seed-from-nsite.md). For each not-yet-processed doc:
  download from nSITE -> parse (OCR + classify) -> upload OCR'd PDF to Drive ->
  write the Sheet row -> THEN record state (so a kill between the two double-
  writes the row on resume, never silently drops it).

Self-terminating: when the state file shows every doc processed, it logs
"Backfill complete" and exits 0 — a no-op. Runs nightly at 2am ET.
"""
from __future__ import annotations

import os
import sys
import tempfile

import drive_client as dc
import sheet_writer as sw
import nsite_client as nc
from egle_doc_parser import parse_document
from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS, RISK_NAMES
from config_loader import load_config

MAX_ERRORS_PER_DOC = 3  # give up on a poison doc after this many failures


def drive_view_link(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def run() -> int:
    cfg = load_config()
    folder_id = os.environ["GDRIVE_FOLDER_ID"]
    sheet_id = os.environ["GSHEET_ID"]
    model = cfg["anthropic_model"]
    batch_size = cfg["backfill_batch_size"]
    page_threshold = cfg["large_doc_page_threshold"]
    max_kw_pages = cfg["large_doc_max_keyword_pages"]

    drive = dc.drive_service()
    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)

    state = dc.read_state(drive, folder_id)
    state.setdefault("processed", {})
    state.setdefault("errors", {})

    session = nc.make_session()
    docs = nc.fetch_site_documents(session, cfg["facility_id"])
    if not docs:
        print("[backfill] nSITE returned 0 documents — aborting (transient?).")
        return 1

    def done_or_poisoned(d):
        did = d["doc_id"]
        return did in state["processed"] or state["errors"].get(did, 0) >= MAX_ERRORS_PER_DOC

    todo = [d for d in docs if not done_or_poisoned(d)]
    print(f"[backfill] {len(docs)} total, {len(state['processed'])} done, {len(todo)} remaining.")
    if not todo:
        print("[backfill] Backfill complete — nothing to do.")
        return 0

    batch = todo[:batch_size]
    tmp = tempfile.gettempdir()
    processed_this_run = 0

    for d in batch:
        did = d["doc_id"]
        canonical = f"N2688_{did}.pdf"
        local = os.path.join(tmp, canonical)
        try:
            # Skip the nSITE download if a prior run already uploaded this PDF.
            existing = dc.find_file_by_name(drive, folder_id, canonical)
            if existing:
                dc.download_file(drive, existing, local)
            else:
                nc.download_pdf(session, d, local)

            parsed = parse_document(
                local, d, RISK_REGISTER,
                model=model, signal_keywords=SIGNAL_KEYWORDS,
                page_threshold=page_threshold, max_keyword_pages=max_kw_pages,
                max_tokens=cfg["classification_max_tokens"],
            )

            file_id = dc.upload_file(drive, folder_id, local, canonical)
            link = drive_view_link(file_id)

            # Sheet row FIRST, then state — order matters for crash-safety.
            sw.write_document(
                sheets, sheet_id, parsed, d, link, RISK_NAMES,
                feed_tab=sw.TAB_HISTORICAL,
            )
            state["processed"][did] = {
                "document_name": d["document_name"],
                "date_filed": d["date_filed"],
                "doc_type": parsed.doc_type,
                "severity": parsed.severity,
                "risks": parsed.risks,
                "ocr_applied": parsed.ocr_applied,
                "page_count": parsed.page_count,
            }
            state["errors"].pop(did, None)
            dc.write_state(drive, folder_id, state)
            processed_this_run += 1
            print(f"  ok  {d['date_filed']}  [{parsed.doc_type}/{parsed.severity}]  "
                  f"{d['document_name'][:50]}")
        except Exception as e:  # noqa: BLE001
            state["errors"][did] = state["errors"].get(did, 0) + 1
            dc.write_state(drive, folder_id, state)
            print(f"  ERR {d['document_name'][:50]}: {e} "
                  f"(attempt {state['errors'][did]}/{MAX_ERRORS_PER_DOC})")
        finally:
            if os.path.exists(local):
                os.remove(local)

    # Keep the Risk Register tab current after each batch.
    try:
        sw.rebuild_risk_register_tab(sheets, sheet_id, RISK_REGISTER)
    except Exception as e:  # noqa: BLE001
        print(f"[backfill] risk-register rebuild skipped: {e}")

    remaining = len([d for d in docs if not done_or_poisoned(d)])
    print(f"[backfill] processed {processed_this_run} this run; {remaining} remaining.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
