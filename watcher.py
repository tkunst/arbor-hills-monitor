"""
watcher.py — daily run: new N2688 filings + MMPC minutes polling + alerts.

  - New docs: anything in the nSITE list not already in _state.
    Download -> parse -> Sheet row (linked to the nSITE source) -> alert/digest
    -> THEN append the 'processed' state event (crash-safe order). Urgent docs
    trigger a same-day email; everything else accrues into the Sunday digest.
  - MMPC: compute whether today is inside a meeting's minutes-polling window
    (2nd-Wednesday rule); if so, check the configured minutes URL and alert once
    when minutes appear.
  - Digest: on Sunday, email the accumulated non-urgent items and clear them.

State lives in the Sheet's _state (per-doc event log) and _meta (digest / MMPC /
last-run singletons) tabs — not a Drive file (see ADR 006). Runs daily at 6am ET.
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
import retry_policy as rp
from egle_doc_parser import parse_document
from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS, RISK_NAMES
from config_loader import load_config

MAX_ERRORS_PER_DOC = 3  # give up on a poison doc after this many failures (cf. backfill)


def _today():
    return datetime.now(_ET).date() if _ET else datetime.now().date()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
    sheet_id = os.environ["GSHEET_ID"]
    model = cfg["anthropic_model"]
    today = _today()

    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)

    state = sw.read_state(sheets, sheet_id)

    session = nc.make_session()
    docs = nc.fetch_all_documents(session, cfg)
    if not docs:
        print("[watcher] nSITE returned 0 documents — aborting (transient?).")
        return 1

    # Skip docs already processed, terminally skipped (unprocessable source —
    # legacy .doc, encrypted PDF, raw image), AND poison docs that have failed
    # MAX_ERRORS_PER_DOC times. Without the skipped check, mark_skipped() clears
    # error counts in read_state(), so skipped docs slip through as "new" on
    # every run and trigger fresh download-fail retry loops.
    def done_or_poisoned(d):
        did = d["doc_id"]
        return (did in state["processed"] or did in state["skipped"]
                or state["errors"].get(did, 0) >= MAX_ERRORS_PER_DOC)

    new_docs = [d for d in docs if not done_or_poisoned(d)]

    # Anti-stampede guard: the watcher is for incremental new filings. A backlog
    # larger than the cap means backfill hasn't cleared history yet — defer doc
    # processing to backfill (don't stampede historical docs into the live feed,
    # don't fire urgent alerts on years-old exceedances, don't overflow the
    # single-cell _meta pending_digest). MMPC + digest housekeeping still run.
    cap = (cfg.get("watcher") or {}).get("max_new_docs_per_run", 25)
    if len(new_docs) > cap:
        print(f"[watcher] {len(new_docs)} unprocessed docs > cap {cap}: backfill "
              f"still in progress — deferring doc processing to the backfill job.")
        new_docs = []
    else:
        print(f"[watcher] {len(new_docs)} new document(s).")
    tmp = tempfile.gettempdir()

    for d in new_docs:
        did = d["doc_id"]
        local = os.path.join(tmp, f"{d.get('facility_srn', 'N2688')}_{did}.pdf")
        try:
            nc.download_pdf(session, d, local)
            parsed = parse_document(
                local, d, RISK_REGISTER, model=model,
                signal_keywords=SIGNAL_KEYWORDS,
                page_threshold=cfg["large_doc_page_threshold"],
                max_keyword_pages=cfg["large_doc_max_keyword_pages"],
                max_tokens=cfg["classification_max_tokens"],
            )
            link = d["doc_url"]  # canonical nSITE source (resolves unauthenticated)

            # Crash-safe order: durable Sheet row first, the 'processed' state
            # event last (a crash before it re-processes the doc next run — a
            # duplicate row, never a silent drop; see ADR 006). The alert/digest
            # sits between them and is BEST-EFFORT: the Sheet is the system of
            # record, so a failed email must not block marking the doc done nor
            # trigger a daily reprocess. send_email() already no-ops when SMTP is
            # unconfigured; here we also swallow a configured-but-failing send.
            sw.write_document(sheets, sheet_id, parsed, d, link, RISK_NAMES, feed_tab=sw.TAB_NEW)

            if ea.is_urgent(parsed, cfg):
                try:
                    ea.send_urgent_alert(parsed, d, link, cfg)
                    print(f"  URGENT emailed: {d['document_name'][:50]}")
                except Exception as ae:  # noqa: BLE001 — notification is best-effort
                    print(f"  URGENT ALERT FAILED to send (doc still recorded): "
                          f"{d['document_name'][:50]}: {ae}")
            else:
                state["pending_digest"].append(_digest_record(parsed, d, link))
                sw.write_meta(sheets, sheet_id, state)

            payload = {
                "document_name": d["document_name"],
                "date_filed": d["date_filed"],
                "doc_type": parsed.doc_type,
                "severity": parsed.severity,
                "risks": parsed.risks,
            }
            state["processed"][did] = payload
            sw.mark_processed(sheets, sheet_id, did, payload, _now())
            print(f"  ok  {d['date_filed']}  [{parsed.doc_type}/{parsed.severity}]  "
                  f"{d['document_name'][:50]}")
        except Exception as e:  # noqa: BLE001
            if rp.is_transient(e):
                # Infrastructure/quota error (API cap, 429, 5xx, network) — not the
                # document's fault. Retry next run WITHOUT a poison strike, so a
                # capped or outage window can't silently skip a real filing (the
                # 2026-07-07 false-poison incident). The doc stays unprocessed.
                print(f"  RETRY (transient, no strike) {d['document_name'][:50]}: {e}")
                continue
            cnt = state["errors"].get(did, 0) + 1
            state["errors"][did] = cnt
            sw.mark_error(sheets, sheet_id, did, cnt, _now())
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
                sw.write_meta(sheets, sheet_id, state)

    # --- Sunday digest ---
    if today.weekday() == 6 and state["pending_digest"]:
        items = [_record_to_item(r) for r in state["pending_digest"]]
        ea.send_digest(items, cfg)
        state["pending_digest"] = []
        sw.write_meta(sheets, sheet_id, state)
        print(f"[watcher] sent Sunday digest ({len(items)} item(s)).")

    try:
        sw.rebuild_risk_register_tab(sheets, sheet_id, RISK_REGISTER)
    except Exception as e:  # noqa: BLE001
        print(f"[watcher] risk-register rebuild skipped: {e}")

    state["last_run"] = _now()
    sw.write_meta(sheets, sheet_id, state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
