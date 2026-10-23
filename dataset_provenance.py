"""dataset_provenance.py -- the "Dataset Provenance" tab and its annotation
columns on the case-file Sheet (coder:dataset-provenance-sheet; spec: Lotext
documents/arbor-hills/wellfield-auto-staging/DESIGN-sheet-provenance-and-dedupe.md
§3-§5 and §7, approved by Trisha 2026-10-19).

What it keeps, all on the PUBLIC case-file Sheet (GSHEET_ID):

  * "Dataset Provenance": one row per (document copy, dataset version) saying
    which published or staged dataset a document fed. Key = (doc_id, dataset).
    doc_id is the nSITE doc id, "HC-<drive file id>" for a Hand-Curated copy,
    "CC-<drive file id>" for a meeting-archive copy, or "REL-<file name>" for a
    source file that only exists inside the GitHub release bundle.
  * "dataset_status" (New / Historical Documents, the column after the feed
    columns): a HYPERLINK formula to the document's provenance row, blank when
    the document has none.
  * "same_content_as" (Archived PDFs): the canonical doc id of a duplicate
    filing of the same content.
  * "same_as_monitor_doc" (Hand-Curated Files): the nSITE doc id of the monitor
    row a hand-curated file duplicates.

Writers: scripts/sync_dataset_provenance.py (the one-time seed from the
worker's provenance-map.csv) and the Lotext wellfield staging run, which calls
record_staged() for each newly staged document. Nothing in the daily pipeline
calls this module.

Write rules (the live Sheet is shared by every watcher):
  * Every cell that holds a doc id is written RAW. USER_ENTERED would turn a
    19-digit id into a float and drop its last digits. Only the pointer
    formula cells are USER_ENTERED.
  * Annotation cells are only ever filled when blank (or already equal); a
    different value is reported as a conflict and left alone.
  * A header cell is only written when blank or already equal. Anything else
    stops the run before a single data cell is written.
  * The provenance tab read raises on a transport failure: it is the upsert's
    key index, and a swallowed failure would append duplicate rows.
  * Rows are addressed by number. Feed tabs only ever grow at the bottom, but a
    backfill FORCE_REPROCESS purge deletes rows, so don't run this while a
    backfill purge is running. sync_pointers() is idempotent and heals a row
    a purge re-wrote without its pointer on the next run.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

import name_check
import sheet_writer as sw
from drive_client import GOOGLE_API_NUM_RETRIES

TAB = sw.TAB_DATASET_PROVENANCE
HEADERS = sw.DATASET_PROVENANCE_HEADERS

NOT_IN_RELEASE = "(not yet in any release)"
STATUS_STAGED = "staged for next release"
WITHDRAWN_PREFIX = "withdrawn from staging"
NSITE_HOST = "mienviro.michigan.gov"
POINTER_LABEL = "see Dataset Provenance"
RELEASE_URL = "https://github.com/tkunst/arbor-hills-monitor/releases/tag/{tag}"
NSITE_LINK = "https://mienviro.michigan.gov/ncore/downloadpdf/{doc_id}"

FEED_TABS = (sw.TAB_NEW, sw.TAB_HISTORICAL)
FEED_POINTER_HEADER = "dataset_status"
ARCHIVE_SAME_HEADER = "same_content_as"
HC_SAME_HEADER = "same_as_monitor_doc"
# Hand-Curated Files has no header list in this repo (it is maintained by the
# Lotext publish flow); its 13 columns are findings_feed.HANDCURATED_FIELDS
# (A..M). The annotation goes in the next one, N. Pinned by a test.
HC_WIDTH = 13
_HC_TITLE, _HC_ORIGIN, _HC_DRIVE = 1, 7, 9

# What a doc id may look like. It ends up inside a formula string literal, so
# the pattern is also the injection guard: no quotes, no spaces, no operators.
_DOC_ID_RE = re.compile(
    r"^(?:-?\d{1,20}|(?:HC|CC)-[A-Za-z0-9_-]{20,80}|REL-[A-Za-z0-9._-]{1,120})$")
_NSITE_ID_RE = re.compile(r"^-?\d{1,20}$")
_DRIVE_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]{20,})|[?&]id=([A-Za-z0-9_-]{20,})")


class ProvenanceError(RuntimeError):
    """A precondition failed (occupied header cell, bad doc id). Raised before
    anything is written for the step it guards."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def valid_doc_id(doc_id: str) -> bool:
    return bool(_DOC_ID_RE.fullmatch(doc_id or ""))   # fullmatch: "$" would allow a trailing newline


def check_doc_id(doc_id: str) -> str:
    if not valid_doc_id(doc_id):
        raise ProvenanceError(f"not a valid provenance doc id: {doc_id!r}")
    return doc_id


def published_status(tag: str) -> str:
    return f"published (since {tag})"


def covered_status(via_doc_id: str) -> str:
    return f"covered (0 new; via {via_doc_id})"


def col_letter(idx0: int) -> str:
    s, n = "", idx0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def drive_file_id(link: str) -> str:
    m = _DRIVE_ID_RE.search(link or "")
    return (m[1] or m[2]) if m else ""


def resolve_drive_prefix(prefix: str, drive_ids: Iterable[str]) -> str:
    """The one full Drive file id starting with `prefix`. The worker's
    provenance-map.csv carries ids cut to 20 characters; a link built from one
    would not open. Zero or several matches is an error, never a guess."""
    hits = sorted({d for d in drive_ids if prefix and d.startswith(prefix)})
    if len(hits) != 1:
        raise ProvenanceError(
            f"drive id prefix {prefix!r} matches {len(hits)} Hand-Curated files (need exactly 1)")
    return hits[0]


_LINK_LETTER = "H"  # FEED_HEADERS "Link"; pinned by a test


def pointer_formula(doc_id: str, gid: int, link: str, row: int) -> str:
    """The dataset_status cell for feed row `row`, whose Link is `link`.

    MATCH finds the doc's provenance row by id at read time, so sorting or
    appending to the provenance tab never breaks the link; IFERROR shows blank
    instead of #N/A if that row is ever removed. The outer IF checks the
    cell's OWN row still holds `link`: rows are written by number, so if a
    backfill purge deletes rows between the read and the write, a formula that
    lands on another document's row shows blank instead of a false pointer.
    (Sheets shifts the $H reference with the row when rows are deleted, so a
    correctly placed formula stays correct.)"""
    check_doc_id(doc_id)
    if not link or len(link) > 2000 or any(c in link for c in "\r\n"):
        raise ProvenanceError("pointer link must be one line of at most 2000 characters")
    lit = link.replace('"', '""')
    return (f'=IF(${_LINK_LETTER}{int(row)}="{lit}",'
            f'IFERROR(HYPERLINK("#gid={int(gid)}&range=A"&MATCH("{doc_id}",'
            f"'{TAB}'!A:A,0),\"{POINTER_LABEL}\"),\"\"),\"\")")


def feed_row_doc_id(link: str, archive_link_to_id: dict) -> str:
    """The nSITE doc id behind a feed row's Link: the Archived PDFs index for a
    Drive mirror link, else the link's final path segment when it is an nSITE
    download URL. "" when neither applies."""
    if not link:
        return ""
    if link in archive_link_to_id:
        return archive_link_to_id[link]
    if (urlparse(link).hostname or "") == NSITE_HOST:
        did = sw._link_doc_id(link)
        return did if _NSITE_ID_RE.fullmatch(did) else ""
    return ""


def row_from(d: dict) -> list:
    return ["" if d.get(h) is None else str(d.get(h)) for h in HEADERS]


def plan_upsert(existing: list[list], desired: list[dict], now: str,
                overwrite_if: Optional[Callable[[str], bool]] = None) -> tuple[list, list, dict]:
    """(updates [(grid_row, row)], appends [row], counts). Key (doc_id, dataset);
    the first existing row with a key wins. first_release_tag is immutable once
    set (design §3). last_updated changes only when something else does.
    `overwrite_if(existing_status)`, when given, must be true for an existing
    row to be updated; otherwise it is kept as is (counted "kept"), so the
    staging run never undoes a status a person set by hand."""
    index: dict = {}
    for i, r in enumerate(existing):
        r = list(r) + [""] * (len(HEADERS) - len(r))
        key = (r[0], r[3])
        if r[0] and key not in index:
            index[key] = (i + 2, r)
    updates, appends = [], []
    counts = {"appended": 0, "updated": 0, "unchanged": 0, "kept": 0}
    seen = set()
    for d in desired:
        check_doc_id(d["doc_id"])
        key = (d["doc_id"], d.get("dataset", ""))
        if key in seen:
            raise ProvenanceError(f"duplicate provenance key in one write: {key}")
        seen.add(key)
        new = row_from(d)
        if key in index:
            grid_row, old = index[key]
            if overwrite_if is not None and not overwrite_if(old[5]):
                counts["kept"] += 1
                continue
            if old[6]:
                new[6] = old[6]
            if new[:8] == old[:8]:
                counts["unchanged"] += 1
                continue
            new[8] = now
            updates.append((grid_row, new))
            counts["updated"] += 1
        else:
            new[8] = now
            appends.append(new)
            counts["appended"] += 1
    return updates, appends, counts


# ---------------------------------------------------------------------------
# Sheets I/O
# ---------------------------------------------------------------------------


def _get(service, sheet_id: str, a1: str, render: Optional[str] = None) -> list[list]:
    kw = {"valueRenderOption": render} if render else {}
    return (service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=a1, **kw)
            .execute(num_retries=GOOGLE_API_NUM_RETRIES).get("values", []))


def _tab_gids(service, sheet_id: str) -> dict:
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute(
        num_retries=GOOGLE_API_NUM_RETRIES)
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def _batch_write(service, sheet_id: str, data: list[dict], value_input: str) -> None:
    if not data:
        return
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={"valueInputOption": value_input, "data": data},
    ).execute(num_retries=GOOGLE_API_NUM_RETRIES)


def ensure_header_cell(service, sheet_id: str, tab: str, idx0: int, header: str,
                       apply: bool = True) -> str:
    """Write `header` into row 1 at column idx0 if that cell is blank. Returns
    "present" / "written" / "would write". Raises if the cell holds anything
    else, before any data cell is touched."""
    cell = f"'{tab}'!{col_letter(idx0)}1"
    cur = _get(service, sheet_id, cell)
    val = cur[0][0] if cur and cur[0] else ""
    if val == header:
        return "present"
    if val:
        raise ProvenanceError(f"{cell} already holds {val!r}; refusing to overwrite it")
    if not apply:
        return "would write"
    _batch_write(service, sheet_id, [{"range": cell, "values": [[header]]}], "RAW")
    return "written"


def ensure_tab(service, sheet_id: str) -> None:
    sw.ensure_tab(service, sheet_id, TAB, HEADERS)


def read_rows(service, sheet_id: str) -> list[list]:
    """The provenance tab's data rows. Raises on any read failure (the upsert
    key index; see the module docstring). A missing tab reads as a failure
    too: callers run ensure_tab() first."""
    return _get(service, sheet_id, f"'{TAB}'!A2:I")


def upsert(service, sheet_id: str, desired: list[dict], now: str, apply: bool = True,
           overwrite_if: Optional[Callable[[str], bool]] = None) -> dict:
    """Insert or update provenance rows by (doc_id, dataset). RAW only."""
    existing = read_rows(service, sheet_id) if apply or _has_tab(service, sheet_id) else []
    updates, appends, counts = plan_upsert(existing, desired, now, overwrite_if)
    if apply:
        _batch_write(service, sheet_id, [
            {"range": f"'{TAB}'!A{n}:I{n}", "values": [row]} for n, row in updates], "RAW")
        if appends:
            # No retry: an append that committed before a dropped connection would
            # be repeated, and a duplicate key confuses every later upsert. A failed
            # run is simply re-run; the read above then sees what landed.
            service.spreadsheets().values().append(
                spreadsheetId=sheet_id, range=f"'{TAB}'!A1", valueInputOption="RAW",
                insertDataOption="INSERT_ROWS", body={"values": appends},
            ).execute(num_retries=0)
    return counts


def _has_tab(service, sheet_id: str) -> bool:
    return TAB in _tab_gids(service, sheet_id)


def archive_index(service, sheet_id: str) -> tuple[dict, dict]:
    """({archive_link: doc_id}, {doc_id: (document_name, archive_link)}) from
    Archived PDFs A:F."""
    link_to_id, by_id = {}, {}
    for r in _get(service, sheet_id, f"'{sw.TAB_ARCHIVE}'!A2:F"):
        if not r or not r[0]:
            continue
        link = r[5] if len(r) > 5 else ""
        if link:
            link_to_id[link] = r[0]
        by_id[r[0]] = (r[1] if len(r) > 1 else "", link)
    return link_to_id, by_id


def check_feed_headers(service, sheet_id: str) -> None:
    """Raise if either feed tab's dataset_status header cell holds something
    else. Checks both tabs before anything is written anywhere."""
    idx = sw.FEED_TAB_HEADERS.index(FEED_POINTER_HEADER)
    for tab in FEED_TABS:
        ensure_header_cell(service, sheet_id, tab, idx, FEED_POINTER_HEADER, apply=False)


def sync_pointers(service, sheet_id: str, apply: bool = True) -> dict:
    """Make every New/Historical Documents row's dataset_status cell match the
    provenance tab: the pointer formula when the row's doc has a provenance
    row, blank otherwise. Writes only cells that differ. The cells are read
    with valueRenderOption=FORMULA (a formula reads back as its displayed
    value otherwise, and every cell would look changed)."""
    gids = _tab_gids(service, sheet_id)
    if TAB not in gids:
        return {"skipped": "no Dataset Provenance tab yet"}
    check_feed_headers(service, sheet_id)
    prov_ids = {r[0] for r in read_rows(service, sheet_id) if r and r[0]}
    link_to_id, _ = archive_index(service, sheet_id)
    idx = sw.FEED_TAB_HEADERS.index(FEED_POINTER_HEADER)
    letter = col_letter(idx)
    link_col = sw.FEED_HEADERS.index("Link")
    counts: dict = {}
    data = []
    for tab in FEED_TABS:
        ensure_header_cell(service, sheet_id, tab, idx, FEED_POINTER_HEADER, apply=apply)
        rows = _get(service, sheet_id, f"'{tab}'!{col_letter(link_col)}2:{letter}",
                    render="FORMULA")
        set_n = clear_n = 0
        for i, r in enumerate(rows):
            link = r[0] if r else ""
            cur = r[idx - link_col] if len(r) > idx - link_col else ""
            did = feed_row_doc_id(link, link_to_id)
            want = pointer_formula(did, gids[TAB], link, i + 2) if did in prov_ids else ""
            if cur != want:
                data.append({"range": f"'{tab}'!{letter}{i + 2}", "values": [[want]]})
                if want:
                    set_n += 1
                else:
                    clear_n += 1
        counts[tab] = {"set": set_n, "cleared": clear_n}
    if apply:
        _batch_write(service, sheet_id, data, "USER_ENTERED")
    return counts


def annotate_archive_duplicates(service, sheet_id: str, pairs: dict, apply: bool = True) -> dict:
    """same_content_as on Archived PDFs: {duplicate_doc_id: canonical_doc_id}.
    Fills a blank cell only; a different existing value is a conflict."""
    for a, b in pairs.items():
        check_doc_id(a)
        check_doc_id(b)
        if not (_NSITE_ID_RE.fullmatch(a) and _NSITE_ID_RE.fullmatch(b)) or a == b:
            raise ProvenanceError(f"same_content_as needs two different nSITE ids: {a} -> {b}")
    idx = sw.ARCHIVE_TAB_HEADERS.index(ARCHIVE_SAME_HEADER)
    letter = col_letter(idx)
    ensure_header_cell(service, sheet_id, sw.TAB_ARCHIVE, idx, ARCHIVE_SAME_HEADER, apply=apply)
    rows = _get(service, sheet_id, f"'{sw.TAB_ARCHIVE}'!A2:{letter}")
    data, out = [], {"written": [], "present": [], "conflict": [], "missing": []}
    found = set()
    for i, r in enumerate(rows):
        did = r[0] if r else ""
        if did not in pairs:
            continue
        found.add(did)
        cur = r[idx] if len(r) > idx else ""
        if cur == pairs[did]:
            out["present"].append(did)
        elif cur:
            out["conflict"].append(did)
        else:
            data.append({"range": f"'{sw.TAB_ARCHIVE}'!{letter}{i + 2}", "values": [[pairs[did]]]})
            out["written"].append(did)
    out["missing"] = sorted(set(pairs) - found)
    if apply:
        _batch_write(service, sheet_id, data, "RAW")
    return out


def annotate_hand_curated(service, sheet_id: str, matches: dict, apply: bool = True) -> dict:
    """same_as_monitor_doc on Hand-Curated Files: {curated_filename: doc_id}.
    A row is annotated only when its origin_url names that same doc id (the
    evidence the two are the same document); otherwise it is a conflict."""
    for doc_id in matches.values():
        if not _NSITE_ID_RE.fullmatch(check_doc_id(doc_id)):
            raise ProvenanceError(f"same_as_monitor_doc needs an nSITE id: {doc_id}")
    letter = col_letter(HC_WIDTH)
    ensure_header_cell(service, sheet_id, sw.TAB_HANDCURATED, HC_WIDTH, HC_SAME_HEADER, apply=apply)
    rows = _get(service, sheet_id, f"'{sw.TAB_HANDCURATED}'!A2:{letter}")
    data, out = [], {"written": [], "present": [], "conflict": [], "missing": []}
    found = set()
    for i, r in enumerate(rows):
        name = r[0] if r else ""
        if name not in matches:
            continue
        found.add(name)
        want = matches[name]
        origin = r[_HC_ORIGIN] if len(r) > _HC_ORIGIN else ""
        cur = r[HC_WIDTH] if len(r) > HC_WIDTH else ""
        if sw._link_doc_id(origin) != want:
            out["conflict"].append(name)
        elif cur == want:
            out["present"].append(name)
        elif cur:
            out["conflict"].append(name)
        else:
            data.append({"range": f"'{sw.TAB_HANDCURATED}'!{letter}{i + 2}", "values": [[want]]})
            out["written"].append(name)
    out["missing"] = sorted(set(matches) - found)
    if apply:
        _batch_write(service, sheet_id, data, "RAW")
    return out


def _clean(title: str) -> str:
    """`title` if name_check finds no listed name or internal marker in it,
    else "" (the caller falls back to the next candidate). Feed titles are
    already on the public Sheet, but some still carry a regulator's name; this
    tab doesn't copy them."""
    return title if title and name_check.is_clean_for_publish(title) else ""


def describe(service, sheet_id: str, doc_ids: Iterable[str]) -> dict:
    """{doc_id: (title, link)} from what the Sheet already shows publicly: the
    feed's resolved Document Name and the Drive mirror link for an nSITE id,
    the Hand-Curated title and drive_link for HC-<id>. Never a raw ledger
    label, and every title passes name_check (see _clean)."""
    want = set(doc_ids)
    link_to_id, by_id = archive_index(service, sheet_id)
    feed_names: dict = {}
    name_col, link_col = sw.FEED_HEADERS.index("Document Name"), sw.FEED_HEADERS.index("Link")
    for tab in FEED_TABS:
        for r in _get(service, sheet_id, f"'{tab}'!A2:{col_letter(link_col)}"):
            link = r[link_col] if len(r) > link_col else ""
            did = feed_row_doc_id(link, link_to_id)
            if did in want and len(r) > name_col and _clean(r[name_col]):
                feed_names.setdefault(did, r[name_col])
    hc: dict = {}
    if any(d.startswith(("HC-", "CC-")) for d in want):
        for r in _get(service, sheet_id, f"'{sw.TAB_HANDCURATED}'!A2:J"):
            link = r[_HC_DRIVE] if len(r) > _HC_DRIVE else ""
            fid = drive_file_id(link)
            if fid:
                title = _clean(r[_HC_TITLE] if len(r) > _HC_TITLE else "") or _clean(r[0])
                hc.setdefault(fid, (title, link))
    out = {}
    for d in want:
        if _NSITE_ID_RE.fullmatch(d):
            name, arch = by_id.get(d, ("", ""))
            out[d] = (feed_names.get(d) or _clean(name) or f"nSITE document {d}",
                      arch or NSITE_LINK.format(doc_id=d))
        elif d.startswith("HC-"):
            title, link = hc.get(d[3:], ("", ""))
            out[d] = (title or f"Hand-curated file {d[3:]}",
                      link or f"https://drive.google.com/file/d/{d[3:]}/view")
        elif d.startswith("CC-"):
            out[d] = (f"Meeting archive file {d[3:]}",
                      f"https://drive.google.com/file/d/{d[3:]}/view")
        else:
            out[d] = (d, "")
    return out


WITHDRAWN_REASON = "no longer in the staged set"
_WITHDRAWN_RE = re.compile(
    re.escape(WITHDRAWN_PREFIX) + r" \d{4}-\d{2}-\d{2}: " + re.escape(WITHDRAWN_REASON))


def _staging_owned(status: str) -> bool:
    """A row the staging run may rewrite: one whose status is exactly what it
    writes itself. Any other status (the publish flip, a note typed by hand,
    even one that starts "withdrawn from staging") is never overwritten."""
    return status == STATUS_STAGED or bool(_WITHDRAWN_RE.fullmatch(status))


def withdraw_stale(service, sheet_id: str, keep: set, now: str) -> int:
    """Mark every "staged for next release" row whose doc is not in `keep` as
    withdrawn (status + last_updated only, RAW). Returns how many."""
    data = []
    for i, r in enumerate(read_rows(service, sheet_id)):
        r = list(r) + [""] * (len(HEADERS) - len(r))
        if r[3] == NOT_IN_RELEASE and r[5] == STATUS_STAGED and r[0] not in keep:
            n = i + 2
            data.append({"range": f"'{TAB}'!F{n}", "values": [[f"{WITHDRAWN_PREFIX} {now[:10]}: {WITHDRAWN_REASON}"]]})
            data.append({"range": f"'{TAB}'!I{n}", "values": [[now]]})
    _batch_write(service, sheet_id, data, "RAW")
    return len(data) // 2


def record_staged(service, sheet_id: str, staged: list[dict], now: str) -> dict:
    """The wellfield staging run's write (design §7 step e), run after every
    non-dry staging run:

      * one "staged for next release" row per document in tonight's staged
        set (`staged` = [{"doc_id", "rows_contributed"}]; title and link come
        from describe()). A row whose status a person changed is kept.
      * every older "staged" row whose doc is no longer staged (a release took
        it, or it was dropped) is marked "withdrawn from staging <date>: no
        longer in the staged set". One reason on purpose: telling "consumed"
        from "dropped" needs state across runs (PR #170 review). The publish
        step (manual) adds the published row.
      * a pointer re-sync, which also restores pointers a purge removed.

    Every precondition (ids, feed header cells) is checked before any write."""
    for s in staged:
        check_doc_id(s["doc_id"])
    gids = _tab_gids(service, sheet_id)
    if not staged and TAB not in gids:
        return {"appended": 0, "updated": 0, "unchanged": 0, "kept": 0, "withdrawn": 0,
                "pointers": {"skipped": "no Dataset Provenance tab yet"}}
    check_feed_headers(service, sheet_id)
    counts = {"appended": 0, "updated": 0, "unchanged": 0, "kept": 0}
    if staged:
        ensure_tab(service, sheet_id)
        info = describe(service, sheet_id, [s["doc_id"] for s in staged])
        desired = [{
            "doc_id": s["doc_id"], "document_title": info[s["doc_id"]][0],
            "link": info[s["doc_id"]][1], "dataset": NOT_IN_RELEASE, "source_report": "",
            "status": STATUS_STAGED, "first_release_tag": "",
            "rows_contributed": s.get("rows_contributed", ""),
        } for s in staged]
        counts = upsert(service, sheet_id, desired, now, overwrite_if=_staging_owned)
    counts["withdrawn"] = withdraw_stale(service, sheet_id, {s["doc_id"] for s in staged}, now)
    counts["pointers"] = sync_pointers(service, sheet_id)
    return counts
