"""
smoke_one.py — parse ONE live nSITE document end-to-end through Claude.

Validates the `messages.parse(output_format=...)` shape against the real API and
surfaces truncation, for ~$0.01 — run this BEFORE the 50-doc backfill batch.

Needs only ANTHROPIC_API_KEY (no Drive/Sheets/SMTP). ocrmypdf only if the chosen
doc is image-only.

  python scripts/smoke_one.py            # first document in the nSITE list
  python scripts/smoke_one.py 5          # the 6th document (index 5)
  python scripts/smoke_one.py <nsite_doc_id>
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nsite_client as nc
from egle_doc_parser import parse_document
from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS
from config_loader import load_config


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first.")
        return 2
    cfg = load_config()
    session = nc.make_session()
    docs = nc.fetch_site_documents(session, cfg["facility_id"])
    if not docs:
        print("nSITE returned 0 documents.")
        return 1

    sel = sys.argv[1] if len(sys.argv) > 1 else "0"
    if sel.isdigit() and int(sel) < len(docs):
        doc = docs[int(sel)]
    else:
        doc = next((d for d in docs if d["doc_id"] == sel), docs[0])

    print(f"Doc: {doc['date_filed']}  {doc['document_name']}\n  {doc['doc_url']}")
    local = os.path.join(tempfile.gettempdir(), f"smoke_{doc['doc_id']}.pdf")
    nc.download_pdf(session, doc, local)
    try:
        parsed = parse_document(
            local, doc, RISK_REGISTER,
            model=cfg["anthropic_model"], signal_keywords=SIGNAL_KEYWORDS,
            page_threshold=cfg["large_doc_page_threshold"],
            max_keyword_pages=cfg["large_doc_max_keyword_pages"],
            max_tokens=cfg["classification_max_tokens"],
        )
    finally:
        if os.path.exists(local):
            os.remove(local)

    print(f"\n  doc_type : {parsed.doc_type}")
    print(f"  severity : {parsed.severity}")
    print(f"  risks    : {parsed.risks}")
    print(f"  ocr      : {parsed.ocr_applied}   pages: {parsed.page_count}")
    print(f"  key_data : {parsed.key_data_point}")
    print(f"  summary  : {parsed.summary}")
    print(f"  measurements ({len(parsed.measurements)}):")
    for m in parsed.measurements:
        print(f"    - {m.get('metric')} {m.get('value')}{m.get('unit')} "
              f"[{m.get('basis')}] well={m.get('well_id')} {m.get('note') or ''}")
    print("\nSDK shape OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
