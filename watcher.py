"""
watcher.py — daily run: new N2688 filings + MMPC minutes polling + alerts.

  - New docs: anything in the nSITE list not already in the state file.
    Download -> parse -> upload -> Sheet row -> THEN state (crash-safe order).
    Urgent docs trigger a same-day email; everything else accrues into the
    Sunday digest.
  - MMPC: compute whether today is inside a meeting's minutes-polling window
    (2nd-Wednesday rule); if so, check the configured minutes URL and alert once
    when minutes appear.
  - Digest: on Sunday, email the accumulated non-urgent items and clear them.

Runs daily at 6am ET.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import nsite_client as nc
import email_alerts as ea
import mmpc_watcher as mw
from egle_doc_parser import parse_document
from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS, RISK_NAMES
from config_loader import load_config


def _today():
    return datetime.now(_ET).date() if _ET else datetime.now().date()


def drive_view_link(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def _digest_record(parsed, d: dict, link: str) -> dict:
    return {
        "date_filed": d["date_filed"],
        "document_name": d["document_name"],
        "doc_type": parsed.doc_type,
        "severity": parsed.severity,
        "risks": parsed.risks,
        "key_data_point": parsed.key_data_point,
        "link": link,
    }


def _record_to_item(rec: dict) -> dict:
    parsed = SimpleNamespace(
        doc_type=rec["doc_type"],
        severity=rec["severity"],
        risks=rec.get("risks", []),
        key_data_point=rec.get("key_data_point", ""),
        summary=rec.get("key_data_point", ""),
    )
    meta = {"date_filed": rec["date_filed"], "document_name": rec["document_name"]}
    return {"parsed": parsed, "metadata": meta, "link": rec["link"]}


def run() -> int:
    cfg = load_config()
    folder_id = os.environ["GDRIVE_FOLDER_ID"]
    sheet_id = os.environ["GSHEET_ID"]
    model = cfg["anthropic_model"]
    today = _today()

    drive = dc.drive_service()
    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)

    state = dc.read_state(drive, folder_id)
    state.setdefault("processed", {})
    state.setdefault("errors", {})
    state.setdefault("pending_digest", [])
    state.setdefault("mmpc_minutes_found", {})

    session = nc.make_session()
    docs = nc.fetch_site_documents(session, cfg["facility_id"])
    if not docs:
        print("[watcher] nSITE returned 0 documents — aborting (transient?).")
        return 1

    new_docs = [d for d in docs if d["doc_id"] not in state["processed"]]
    print(f"[watcher] {len(new_docs)} new document(s).")
    tmp = tempfile.gettempdir()

    for d in new_docs:
        did = d["doc_id"]
        canonical = f"N2688_{did}.pdf"
        local = os.path.join(tmp, canonical)
        try:
            nc.download_pdf(session, d, local)
            parsed = parse_document(
                local, d, RISK_REGISTER, model=model,
                signal_keywords=SIGNAL_KEYWORDS,
                page_threshold=cfg["large_doc_page_threshold"],
                max_keyword_pages=cfg["large_doc_max_keyword_pages"],
                max_tokens=cfg["classification_max_tokens"],
            )
            file_id = dc.upload_file(drive, folder_id, local, canonical)
            link = drive_view_link(file_id)

            sw.write_document(sheets, sheet_id, parsed, d, link, RISK_NAMES, feed_tab=sw.TAB_NEW)
            state["processed"][did] = {
                "document_name": d["document_name"],
                "date_filed": d["date_filed"],
                "doc_type": parsed.doc_type,
                "severity": parsed.severity,
                "risks": parsed.risks,
            }

            if ea.is_urgent(parsed, cfg):
                ea.send_urgent_alert(parsed, d, link, cfg)
                print(f"  URGENT emailed: {d['document_name'][:50]}")
            else:
                state["pending_digest"].append(_digest_record(parsed, d, link))

            dc.write_state(drive, folder_id, state)
            print(f"  ok  {d['date_filed']}  [{parsed.doc_type}/{parsed.severity}]  "
                  f"{d['document_name'][:50]}")
        except Exception as e:  # noqa: BLE001
            state["errors"][did] = state["errors"].get(did, 0) + 1
            dc.write_state(drive, folder_id, state)
            print(f"  ERR {d['document_name'][:50]}: {e}")
        finally:
            if os.path.exists(local):
                os.remove(local)

    # --- MMPC minutes polling ---
    mtg = mw.active_polling_meeting(today, cfg["mmpc"])
    if mtg:
        key = mtg.isoformat()
        if not state["mmpc_minutes_found"].get(key):
            found, note = mw.check_minutes_posted(session, cfg["mmpc"]["minutes_url"])
            print(f"[watcher] MMPC {key}: polling minutes — {note}")
            if found:
                state["mmpc_minutes_found"][key] = True
                ea.send_email(
                    f"MMPC minutes likely posted for the {key} meeting",
                    f"The MMPC minutes page for the {key} meeting (Arbor Hills "
                    f"expansion is an R1 concern) appears updated.\n\n"
                    f"{cfg['mmpc']['minutes_url']}\n\n({note})",
                    cfg,
                )
                dc.write_state(drive, folder_id, state)

    # --- Sunday digest ---
    if today.weekday() == 6 and state["pending_digest"]:
        items = [_record_to_item(r) for r in state["pending_digest"]]
        ea.send_digest(items, cfg)
        state["pending_digest"] = []
        dc.write_state(drive, folder_id, state)
        print(f"[watcher] sent Sunday digest ({len(items)} item(s)).")

    try:
        sw.rebuild_risk_register_tab(sheets, sheet_id, RISK_REGISTER)
    except Exception as e:  # noqa: BLE001
        print(f"[watcher] risk-register rebuild skipped: {e}")

    state["last_run"] = datetime.now().isoformat()
    dc.write_state(drive, folder_id, state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
