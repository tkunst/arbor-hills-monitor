"""route_hand_curated.py — route hand-curated documents (dedupe-curate intake)
into the urgent/digest queue via email_alerts.route_hand_curated_urgent_or_digest().

Hand-Curated Files additions never run through egle_doc_parser.parse_document(),
so they have no other path into pending_digest or pending_urgent_recap at all
(gap found 2026-09-20 — see route_hand_curated_urgent_or_digest's docstring).
This script is the manual trigger that closes it for a specific document, on
demand, after a dedupe-curate session has already appended its Hand-Curated
Files row and made its own judgment call on severity.

Local-only / attended-run tool (like scripts/oauth_setup.py,
scripts/seed_wds_state.py) — NOT wired to any schedule, since a "severity":
"urgent" entry can send a real same-day [URGENT] email to real recipients.
Run it deliberately, locally or via the route-hand-curated workflow_dispatch.

Input: HAND_CURATED_ROUTE_JSON env var, a JSON array of objects, each:
  {document_name, date_filed, doc_type, severity, risks, key_data_point, link}
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client as dc
import sheet_writer as sw
import email_alerts as ea
from config_loader import load_config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run() -> int:
    raw = os.environ.get("HAND_CURATED_ROUTE_JSON", "").strip()
    if not raw:
        print("[route_hand_curated] HAND_CURATED_ROUTE_JSON is empty — nothing to do.")
        return 0
    docs = json.loads(raw)
    if not docs:
        print("[route_hand_curated] empty list — nothing to do.")
        return 0

    cfg = load_config()
    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    state = sw.read_meta(sheets, sheet_id)

    for d in docs:
        sent = ea.route_hand_curated_urgent_or_digest(
            document_name=d["document_name"],
            date_filed=d["date_filed"],
            doc_type=d["doc_type"],
            severity=d["severity"],
            risks=d.get("risks") or [],
            key_data_point=d["key_data_point"],
            link=d["link"],
            cfg=cfg,
            state=state,
            sent_at=_now(),
        )
        label = "URGENT — SENT" if sent else "queued for the Sunday digest"
        print(f"  {label}: {d['document_name']} ({d['date_filed']})")

    sw.write_meta(sheets, sheet_id, state)
    print(f"[route_hand_curated] {len(docs)} document(s) routed; _meta persisted.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
