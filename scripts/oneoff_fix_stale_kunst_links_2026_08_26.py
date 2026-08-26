"""One-off fix for 7 documents whose public-facing Drive link still points at
the old, pre-cutover Drive identity.

The 2026-08-25 Case File Mirror cutover (`13d4717`, ADR — see CLAUDE.md's
"cutover" note) repointed the archiver at the kunst.trisha OAuth identity and
a same-day script bulk-repointed 1699/1706 existing Archived PDFs rows to
their already-migrated kunst.trisha file IDs. 7 rows were left out: they were
archived 2026-08-25T11:23-11:24, in the gap between the copy-migration
snapshot and the cutover landing that evening, so no migrated file existed
for them to repoint to. They do NOT self-resolve (the archiver dedupes by
doc_id and never re-mirrors an already-archived doc) — verified live
2026-08-26, they were still owned by the old identity.

TWO PASSES, run once already (pass 1) and now extended with pass 2 after
discovering it wasn't sufficient on its own:

  Pass 1 (already run, kept here idempotent): re-download each doc's PDF
  straight from nSITE and re-upload it via the kunst.trisha OAuth
  credentials, then overwrite that row's Archive Link cell (col F) in the
  Archived PDFs tab. find_in_folder()'s reuse-by-name check makes re-running
  this pass safe — it returns the same link instead of re-uploading.

  Pass 2 (why pass 1 alone didn't fix the public page): gen_findings_feed.py
  renders the New Documents / Historical Documents tabs' OWN "Link" column,
  swapping in the Archived PDFs tab's link only for rows whose Link is still
  a raw nSITE URL (findings_feed.resolve_display_link parses an nSITE doc_id
  out of the link to do that join). For these 7 docs, mirror_one_now()
  succeeded inline at doc-processing time, so their Link column already held
  a Drive URL from the start — no nSITE doc_id to parse, so the join never
  fires and Archived PDFs' fix is invisible to the public page. Pass 2
  patches the New/Historical tabs' Link cell directly wherever it holds one
  of the 7 known-stale Drive file IDs.

Delete this file (and the temporary workflow that runs it) once confirmed —
this is a one-time correction, not a capability the repo carries forward.
"""
import os
import re
import sys
import tempfile

import archive_client as ac
import drive_client as dc
import nsite_client as nc
from config_loader import load_config

# The 7 doc_ids identified live 2026-08-26 (Archived At in the 2026-08-25
# 11:23-11:24 gap), mapped to the stale Drive file ID their public link still
# carried at the time. Hardcoded deliberately -- this is a targeted one-time
# fix, not a general-purpose scan.
STALE_DOC_IDS = {
    "-4245910840274776203": "1GU9ZGLU9p1fkcw-8-uKSx8AJhm9qnaxL",
    "-1998324047986636269": "1UmtQuoSVG36UIwe5FkZxPsLznb1DCfzT",
    "3503475944289810339": "1Og1WpuwbXBN3BhXiX-4lSOa6ruuCCICE",
    "-7648064088625030344": "15REkBlhrMvvqqC_-ybC9wwpgjLLXR9br",
    "-2202646365709182057": "1amK5pkYfoU6rGtrERxtM5U3ODapigIcl",
    "-1880568531366408926": "1gmTBIig_KG4NQd-qgRy-E8tTBap9Q5N7",
    "4177295883340260229": "1YfAQBSDl6Y3PvKlUz2bhdXcoHoM1vBm3",
}

_FID_RE = re.compile(r"/d/([a-zA-Z0-9_-]+)")


def _find_rows(sheets, sheet_id: str, tab: str, col: str = "A") -> dict:
    """{cell value: 1-based sheet row number} for column `col` of `tab`,
    header row excluded (data starts at row 2)."""
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!{col}2:{col}"
    ).execute()
    rows = resp.get("values", [])
    out = {}
    for i, r in enumerate(rows):
        if r:
            out[r[0]] = i + 2
    return out


def _repoint_archived_pdfs(sheets, sheet_id: str) -> dict:
    """Pass 1: re-mirror each stale doc under kunst.trisha, repoint its
    Archived PDFs row. Returns {doc_id: new_link}."""
    row_by_doc_id = _find_rows(sheets, sheet_id, "Archived PDFs")
    missing = [d for d in STALE_DOC_IDS if d not in row_by_doc_id]
    if missing:
        print(f"[fix] ABORT: doc_ids not found in Archived PDFs tab: {missing}")
        return {}

    cfg = load_config()
    session = nc.make_session()
    docs = nc.fetch_all_documents(session, cfg)
    by_id = {d["doc_id"]: d for d in docs}
    drive = ac.oauth_drive_service()

    new_links = {}
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
        print(f"  ok  Archived PDFs row {row} ({doc_id}) -> {link}")
        new_links[doc_id] = link

    return new_links


def _repoint_feed_tab(sheets, sheet_id: str, tab: str, old_fid_to_new_link: dict) -> int:
    """Pass 2: scan one of the New/Historical Documents tabs' Link column (H)
    for any of the known stale Drive file IDs and overwrite that cell with
    the new link. Returns the count fixed in this tab."""
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!H2:H"
    ).execute()
    rows = resp.get("values", [])

    fixed = 0
    for i, r in enumerate(rows):
        if not r or not r[0]:
            continue
        m = _FID_RE.search(r[0])
        if not m:
            continue
        old_fid = m.group(1)
        new_link = old_fid_to_new_link.get(old_fid)
        if not new_link:
            continue
        row_num = i + 2
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!H{row_num}",
            valueInputOption="RAW",
            body={"values": [[new_link]]},
        ).execute()
        print(f"  ok  {tab} row {row_num} (fid {old_fid}) -> {new_link}")
        fixed += 1
    return fixed


def run() -> int:
    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()

    new_links_by_doc_id = _repoint_archived_pdfs(sheets, sheet_id)
    if len(new_links_by_doc_id) != len(STALE_DOC_IDS):
        print(f"[fix] pass 1 incomplete: {len(new_links_by_doc_id)}/{len(STALE_DOC_IDS)} — "
              "aborting before pass 2 to avoid a partial old_fid->new_link map.")
        return 1

    old_fid_to_new_link = {
        STALE_DOC_IDS[doc_id]: link for doc_id, link in new_links_by_doc_id.items()
    }

    total_pass2 = 0
    for tab in ("New Documents", "Historical Documents"):
        total_pass2 += _repoint_feed_tab(sheets, sheet_id, tab, old_fid_to_new_link)

    print(f"[fix] pass 1: {len(new_links_by_doc_id)}/{len(STALE_DOC_IDS)} Archived PDFs rows. "
          f"pass 2: {total_pass2} feed-tab Link cells repointed.")
    return 0 if total_pass2 == len(STALE_DOC_IDS) else 1


if __name__ == "__main__":
    sys.exit(run())
