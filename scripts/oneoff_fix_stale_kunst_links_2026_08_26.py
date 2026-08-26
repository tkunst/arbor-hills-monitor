"""One-off fix for 7 Archived PDFs rows still pointing at the old, pre-cutover
Drive identity.

The 2026-08-25 Case File Mirror cutover (`13d4717`, ADR — see CLAUDE.md's
"cutover" note) repointed the archiver at the kunst.trisha OAuth identity and
a same-day script bulk-repointed 1699/1706 existing Archived PDFs rows to
their already-migrated kunst.trisha file IDs. 7 rows were left out: they were
archived 2026-08-25T11:23-11:24, in the gap between the copy-migration
snapshot and the cutover landing that evening, so no migrated file existed
for them to repoint to. They do NOT self-resolve (the archiver dedupes by
doc_id and never re-mirrors an already-archived doc) — verified live
2026-08-26, they're still owned by the old identity.

Fix: re-download each doc's PDF straight from nSITE (the canonical source,
column E "Source (nSITE) Link") and re-upload it via the now-kunst.trisha
OAuth credentials, then overwrite ONLY that row's Archive Link cell (col F)
in place — no new row, no change to Doc ID/Name/Date Filed/Risks/Source Link/
Archived At.

Delete this file (and the temporary workflow that runs it) once confirmed —
this is a one-time correction, not a capability the repo carries forward.
"""
import os
import sys
import tempfile

import archive_client as ac
import drive_client as dc
import nsite_client as nc
from config_loader import load_config

# The 7 doc_ids identified live 2026-08-26 (Archived At in the 2026-08-25
# 11:23-11:24 gap). Hardcoded deliberately -- this is a targeted one-time
# fix, not a general-purpose scan.
STALE_DOC_IDS = [
    "-4245910840274776203",
    "-1998324047986636269",
    "3503475944289810339",
    "-7648064088625030344",
    "-2202646365709182057",
    "-1880568531366408926",
    "4177295883340260229",
]


def _find_rows(sheets, sheet_id: str) -> dict:
    """{doc_id: 1-based sheet row number} for the Archived PDFs tab, header row
    excluded (data starts at row 2)."""
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range="Archived PDFs!A2:A"
    ).execute()
    rows = resp.get("values", [])
    out = {}
    for i, r in enumerate(rows):
        if r:
            out[r[0]] = i + 2
    return out


def run() -> int:
    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    row_by_doc_id = _find_rows(sheets, sheet_id)

    missing_rows = [d for d in STALE_DOC_IDS if d not in row_by_doc_id]
    if missing_rows:
        print(f"[fix] ABORT: doc_ids not found in Archived PDFs tab: {missing_rows}")
        return 1

    cfg = load_config()
    session = nc.make_session()
    docs = nc.fetch_all_documents(session, cfg)
    by_id = {d["doc_id"]: d for d in docs}

    drive = ac.oauth_drive_service()

    fixed = 0
    for doc_id in STALE_DOC_IDS:
        meta = by_id.get(doc_id)
        if not meta:
            print(f"  SKIP {doc_id}: not in current nSITE list (link rot — cannot re-download).")
            continue

        srn = meta.get("facility_srn", "N2688")
        tmp = tempfile.gettempdir()
        local = os.path.join(tmp, f"{srn}_{doc_id}_kunstfix.pdf")
        try:
            nc.download_pdf(session, meta, local)
            link = ac.upload_pdf(drive, local, f"{srn}_{doc_id}.pdf", ac.folder_id())
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {doc_id}: {e}")
            continue
        finally:
            if os.path.exists(local):
                os.remove(local)

        row = row_by_doc_id[doc_id]
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"Archived PDFs!F{row}",
            valueInputOption="RAW",
            body={"values": [[link]]},
        ).execute()
        print(f"  ok  {doc_id} (row {row}) -> {link}")
        fixed += 1

    print(f"[fix] fixed {fixed}/{len(STALE_DOC_IDS)} rows.")
    return 0 if fixed == len(STALE_DOC_IDS) else 1


if __name__ == "__main__":
    sys.exit(run())
