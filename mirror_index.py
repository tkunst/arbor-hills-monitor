"""
mirror_index.py — the `Mirror Index` tab: one row of FILE METADATA per PDF this
monitor uploads to the public Drive mirror (ADR 073, CODER-PLAN step 3).

Why: every tool that reads the Mirror (the full-text cache, the OCR backfill,
the wellfield staging job) used to re-download and re-scan files to learn what
Drive already knows (sha256/md5/size/revision) and what the upload already
computed (page count, image-only pages, whether OCR ran). This tab records both
once, at upload time, so later steps can skip that work.

The fail-safe rule every READER must follow (ADR 073): skip work only when
Drive's LIVE `sha256Checksum` equals this tab's `mirror_sha256`. A missing row,
a blank sha, a mismatch, or a read error means "process the file the old way".

Shape of this module:
  - pure:   text_status(), build_row(), latest_by_drive_id()
  - reads:  read_index() — STRICT (sheet_writer._tab_rows_strict): a transport
            failure raises, never reads as "empty" (this is a state tab).
  - writes: upsert_row() — upsert by drive_id; record() — the never-raises
            wrapper archive_client calls after a successful upload.
  - config: target() — returns None unless `mirror_index.enabled` is true, so
            with the flag off upload_file() does exactly what it did before.

Only callers that pass an IndexTarget are indexed. Today: nSITE PDFs
(archiver.py, both paths), MMPC and CivicClerk meeting PDFs. GovQA, RIDE
documents, Ridge Wood, GFL air captures, snapshot exports and WDS pages never
pass one (private, non-PDF, or out of scope — see ADR 073).

The tab is created on demand (sheet_writer.ensure_tab) the first time an
enabled run writes a row — deliberately NOT in sheet_writer._TAB_HEADERS, so no
empty public tab appears before Trisha flips the flag (ADR 073).

SEC-001: log lines here carry a failure class only — never a file name,
drive id, or exception text (public Actions logs).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

TAB = "Mirror Index"
HEADERS = [
    "drive_id", "source_kind", "source_key", "mirror_sha256", "mirror_md5", "size",
    "original_sha256", "head_revision", "pages", "image_only_pages", "text_status",
    "ocr_engine", "raw_revision_pinned", "indexed_at",
]
# hand_curated rows come only from scripts/backfill_mirror_index.py (those
# files have no monitor upload path).
SOURCE_KINDS = ("nsite", "mmpc", "civicclerk", "hand_curated")
TEXT_STATUSES = ("native", "ocr_full", "ocr_partial", "no_text", "unknown")

# Fields asked of Drive's create/update response ONLY when indexing is on
# (flag off keeps upload_file()'s original "id, webViewLink" request).
DRIVE_FIELDS = "id, webViewLink, sha256Checksum, md5Checksum, size, headRevisionId"


@dataclass(frozen=True)
class IndexTarget:
    """Where and how to record one upload. Built by the caller (who knows the
    source) only when the flag is on; see target()."""
    sheets: object
    sheet_id: str
    source_kind: str
    source_key: str
    # True only when the local file handed to the upload is exactly the bytes
    # the source served (MMPC/CivicClerk downloads). nSITE files may have been
    # OCR'd in place or synthesized from .msg/.docx before upload, so for them
    # original_sha256 stays blank rather than recording a hash that only looks
    # like the source's.
    local_is_source: bool = False
    tab: str = TAB


def enabled(cfg: dict | None) -> bool:
    return bool(((cfg or {}).get("mirror_index") or {}).get("enabled"))


def target(cfg: dict | None, sheets, sheet_id: str, source_kind: str, source_key: str,
           *, local_is_source: bool = False, tab: str = TAB) -> IndexTarget | None:
    """An IndexTarget, or None when `mirror_index.enabled` is off (then the
    upload behaves exactly as it did before this module existed)."""
    if not enabled(cfg) or sheets is None or not sheet_id:
        return None
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown mirror_index source_kind: {source_kind!r}")
    return IndexTarget(sheets, sheet_id, source_kind, str(source_key),
                       local_is_source=local_is_source, tab=tab)


def text_status(stats) -> str:
    """ADR 073's table, from an ocr_util.OcrStats describing the UPLOADED file:

      unknown     -- no stats, or 0 pages (could not be read / nothing to judge)
      native      -- no image-only page left, engine "none" (no OCRmyPDF stamp)
      ocr_full    -- no image-only page left, OCRmyPDF stamp
      no_text     -- every page is image-only
      ocr_partial -- some pages still image-only (with or without OCR)

    `native` and `ocr_full` both mean "every page with an image also has text":
    those are the only two a reader may treat as already searchable."""
    if stats is None:
        return "unknown"
    pages = getattr(stats, "pages", None)
    image_only = getattr(stats, "image_only_pages", None)
    if not pages or image_only is None:
        return "unknown"
    n_img = len(image_only)
    if n_img == 0:
        return "native" if getattr(stats, "engine", "none") == "none" else "ocr_full"
    if n_img >= pages:
        return "no_text"
    return "ocr_partial"


def build_row(drive_meta: dict, stats, *, source_kind: str, source_key: str,
              original_sha256: str = "", raw_revision_pinned: str = "",
              now: str | None = None) -> list:
    """One Mirror Index row, in HEADERS order. Pure. Checksums come from
    Drive's own response (blank when Drive didn't return one: a blank sha is
    never skippable, by the fail-safe rule)."""
    pages = getattr(stats, "pages", None) if stats is not None else None
    image_only = getattr(stats, "image_only_pages", None) if stats is not None else None
    engine = getattr(stats, "engine", "") if stats is not None else ""
    return [
        str(drive_meta.get("id") or ""),
        source_kind,
        str(source_key),
        str(drive_meta.get("sha256Checksum") or ""),
        str(drive_meta.get("md5Checksum") or ""),
        str(drive_meta.get("size") or ""),
        original_sha256 or "",
        str(drive_meta.get("headRevisionId") or ""),
        "" if pages is None else pages,
        "" if image_only is None else len(image_only),
        text_status(stats),
        engine or "",
        raw_revision_pinned or "",
        now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ]


def latest_by_drive_id(rows: list[list]) -> dict[str, dict]:
    """{drive_id: {header: value}} from data rows (no header row). LAST row
    per drive_id wins: two workflows can race an upsert and leave a duplicate
    row, and the later write is the current one (ADR 073)."""
    out: dict[str, dict] = {}
    for r in rows:
        if not r or not str(r[0]).strip():
            continue
        padded = list(r) + [""] * (len(HEADERS) - len(r))
        out[str(r[0]).strip()] = dict(zip(HEADERS, padded[:len(HEADERS)]))
    return out


def _read_data_rows(sheets, sheet_id: str, tab: str) -> list[list]:
    import sheet_writer as sw
    rows = sw._tab_rows_strict(sheets, sheet_id, tab, "A:N")
    if rows and rows[0] and str(rows[0][0]).strip() == HEADERS[0]:
        rows = rows[1:]
    return rows


def read_index(sheets, sheet_id: str, tab: str = TAB) -> dict[str, dict]:
    """STRICT read of the whole index (this is a state tab later steps skip
    work from): a transport failure RAISES; a genuinely absent tab is {}."""
    return latest_by_drive_id(_read_data_rows(sheets, sheet_id, tab))


# (sheets client, sheet_id, tab) this process has already ensure_tab()'d, so
# a batch of uploads pays the tab check once, not per row.
_ENSURED: set = set()

_ORIGINAL_COL = HEADERS.index("original_sha256")


def upsert_row(sheets, sheet_id: str, row: list, tab: str = TAB) -> None:
    """Write `row` over the LAST existing row with the same drive_id, else
    append it. Creates the tab on first use. RAISES on any failure, including
    a failed strict read (never appends blind: an unknown current state would
    mean a duplicate row) -- record() is the never-raises layer.

    `original_sha256` is provenance of the bytes the SOURCE served, recorded
    once at upload: a later write with it blank (e.g. the OCR backfill
    replacing the file's content) keeps the existing value."""
    import sheet_writer as sw
    from drive_client import GOOGLE_API_NUM_RETRIES

    key = (id(sheets), sheet_id, tab)
    if key not in _ENSURED:
        sw.ensure_tab(sheets, sheet_id, tab, HEADERS)
        _ENSURED.add(key)
    # Rows with POSITIONS kept (blank rows stay as []), so a hit maps to its
    # real sheet row; _tab_rows_strict drops blank rows, which would shift
    # the row number. No try/except: any failure raises (never append blind).
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A:N",
    ).execute(num_retries=GOOGLE_API_NUM_RETRIES)
    data = resp.get("values", [])
    drive_id = str(row[0])
    hit = None
    for i, r in enumerate(data):
        if i > 0 and r and str(r[0]).strip() == drive_id:
            hit = i
    if hit is None:
        sw.append_rows(sheets, sheet_id, tab, [row])
        return
    old = data[hit]
    if not row[_ORIGINAL_COL] and len(old) > _ORIGINAL_COL and old[_ORIGINAL_COL]:
        row = list(row)
        row[_ORIGINAL_COL] = old[_ORIGINAL_COL]
    sheet_row = hit + 1  # values index 0 = sheet row 1 (the header)
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"'{tab}'!A{sheet_row}",
        valueInputOption="RAW", body={"values": [row]},
    ).execute(num_retries=GOOGLE_API_NUM_RETRIES)


def record(index: IndexTarget | None, drive_meta: dict, stats, *,
           original_sha256: str = "", raw_revision_pinned: str = "") -> bool:
    """Best-effort index write after a SUCCESSFUL Drive upload. Never raises:
    a missing row only means "process this file the old way" later. Returns
    True when a row was written."""
    if index is None:
        return False
    try:
        if not drive_meta.get("id"):
            log.warning("mirror_index: upload response had no id (class=no_drive_id)")
            return False
        row = build_row(drive_meta, stats, source_kind=index.source_kind,
                        source_key=index.source_key, original_sha256=original_sha256,
                        raw_revision_pinned=raw_revision_pinned)
        upsert_row(index.sheets, index.sheet_id, row, index.tab)
        return True
    except Exception as e:  # noqa: BLE001 — the upload already succeeded; never fail it
        log.warning("mirror_index: index write skipped (class=%s)", type(e).__name__)
        return False
