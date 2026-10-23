"""
review_flags.py — the "needs human review" log for parser output the monitor
deliberately did NOT use (coder:woi-layout-guard, ADR 067 + ADR 005 addenda,
2026-10-15).

Two things used to be dropped silently:

  * a temperature above `temperature_thresholds.max_plausible_wellhead_f`
    (250°F). ADR 067 made the free-text urgency fallback skip such numbers and
    kdp_check re-tag such a classifier measurement `basis="unknown"`: both are
    correct protections (a >250°F number is usually a flare temperature or a
    misread column, never a reason for an URGENT alert on its own), but nobody
    was told the number existed. A WOI table reading above the ceiling now
    gets the same protection in woi_router, plus a flag.
  * a WOI gas table the parser rejected (unknown column layout, or the
    column-shift signature) and pages MuPDF could not read.

Each is now one row in the "Parser Review Flags" tab of the PRIVATE Sheet
(GSHEET_ID_PRIVATE, shared only with the service account and Trisha):
document id, link, kind, value, well, reading time, pages, a fixed-phrase
detail. The review list itself is never written to the public case-file
Sheet. (The above-ceiling VALUES are still on public record the established
way: a classifier reading kdp_check re-tagged, and a WOI as-found reading
woi_router held out, both stay in Measurements with basis "unknown" and a
note, which is what keeps them from reading as measured values there.) With
no private Sheet configured the flags are counted in the log and not
written. It is a DATA tab (append-only, never diffed or baselined).

This module never sends email. `flags_since()` is the one read entry point,
for the owner-only weekly email / same-day alert to call later.
"""
from __future__ import annotations

import os
from typing import Optional

import email_alerts as ea
import kdp_check
import sheet_writer as sw
import woi_table_parser as wtp

TAB_REVIEW_FLAGS = "Parser Review Flags"
REVIEW_FLAG_HEADERS = [
    "Flagged At", "Doc ID", "Document Name", "Kind", "Value (F)", "Well ID",
    "Reading", "Pages", "Detail", "Link",
]

KIND_WOI_TEMP = "woi_temp_above_ceiling"          # WOI table reading > ceiling
KIND_MEASUREMENT_TEMP = "measurement_temp_above_ceiling"  # classifier reading re-tagged
KIND_TEXT_TEMP = "text_temp_above_ceiling"        # free-text number the fallback skipped
KIND_REJECTED_TABLE = "rejected_gas_table"
KIND_UNREADABLE_PAGES = "unreadable_pages"
TEMP_KINDS = (KIND_WOI_TEMP, KIND_MEASUREMENT_TEMP, KIND_TEXT_TEMP)

# One document can carry many above-ceiling readings (a shifted table had 82).
# Keep the log readable: at most this many per-reading WOI flags per document,
# then one summary flag for the rest.
MAX_WOI_TEMP_FLAGS_PER_DOC = 25

_RETAG_NOTE = kdp_check.RETAG_NOTE


def max_plausible_f(cfg: dict) -> float:
    return float((cfg.get("temperature_thresholds") or {}).get("max_plausible_wellhead_f", 250))


def flag(kind: str, *, value_f=None, well_id: str = "", reading: str = "",
         pages: str = "", detail: str = "") -> dict:
    return {"kind": kind, "value_f": value_f, "well_id": well_id,
            "reading": reading, "pages": pages, "detail": detail}


def woi_flags(above_ceiling: list, rejected_tables: list, skipped_pages: list,
              max_f: float) -> list[dict]:
    """Flags for one WOI report. `above_ceiling` = WOIReading objects whose
    temperature exceeds max_f (dropped from every alerting path by
    woi_router); `rejected_tables` = woi_table_parser.RejectedTable;
    `skipped_pages` = 1-based unreadable pages. Pure."""
    out = []
    for r in above_ceiling[:MAX_WOI_TEMP_FLAGS_PER_DOC]:
        out.append(flag(KIND_WOI_TEMP, value_f=r.temp, well_id=r.well_id,
                        reading=r.dt + (" ADJ" if r.adj else ""), pages=str(r.page),
                        detail=f"WOI table temperature above the {max_f:g}F plausibility "
                               "ceiling; not used for alerts or the summary"))
    rest = above_ceiling[MAX_WOI_TEMP_FLAGS_PER_DOC:]
    if rest:
        out.append(flag(KIND_WOI_TEMP, value_f=max(r.temp for r in rest),
                        pages=wtp._page_ranges(sorted({r.page for r in rest})),
                        detail=f"{len(rest)} more WOI table temperatures above the "
                               f"{max_f:g}F ceiling (max shown)"))
    for t in rejected_tables:
        pages = f"{t.first_page}-{t.last_page}" if t.last_page != t.first_page else str(t.first_page)
        out.append(flag(KIND_REJECTED_TABLE, pages=pages,
                        detail=f"{t.n_rows} gas rows not used: {t.reason}"))
    if skipped_pages:
        out.append(flag(KIND_UNREADABLE_PAGES, pages=wtp._page_ranges(skipped_pages),
                        detail=f"{len(skipped_pages)} page(s) could not be read; the rest was parsed"))
    return out


# Free-text: a °F number above the ceiling, with the same regex the urgency
# fallback uses. Flare / combustion context (±150 chars) is skipped: flare
# temperatures of 1,400°F+ are real and expected, and flagging every flare
# report would bury the flags that matter.
_CONTEXT_CHARS = 150


# The urgency fallback's own matcher (ea.temperature_matches): one pattern for both
# (it reads "1,500°F" as 1500 and skips EPA Method 19 F-factor values).


def implausible_text_temps(text: str, max_f: float) -> list[int]:
    """°F values above max_f in free text, outside flare/combustion context,
    de-duplicated, highest first. Pure."""
    if not text:
        return []
    vals = set()
    for m in ea.temperature_matches(text):
        v = int(m.group(1).replace(",", ""))
        if v <= max_f:
            continue
        ctx = text[max(0, m.start() - _CONTEXT_CHARS):m.end() + _CONTEXT_CHARS]
        if kdp_check._FLARE_CTX_RE.search(ctx):
            continue
        vals.add(v)
    return sorted(vals, reverse=True)


def parsed_flags(parsed, cfg: dict) -> list[dict]:
    """Flags for a classified document (any document, WOI or not):

    * a classifier MEASURED temperature kdp_check re-tagged `basis="unknown"`
      for being above the ceiling (it stays in Measurements with its note);
    * when is_urgent had to fall back to free text (no structured temperature
      at all), each above-ceiling number that fallback skipped.

    Neither can fire an URGENT alert; these flags only make them visible.
    Pure."""
    max_f = max_plausible_f(cfg)
    out = []
    ms = list(getattr(parsed, "measurements", None) or [])
    for m in ms:
        if m.get("metric") == "temperature" and _RETAG_NOTE in (m.get("note") or ""):
            out.append(flag(KIND_MEASUREMENT_TEMP, value_f=m.get("value"),
                            well_id=str(m.get("well_id") or ""),
                            reading=str(m.get("as_of_date") or ""),
                            detail=f"classifier reading above the {max_f:g}F ceiling; "
                                   "kept with basis=unknown, not used for alerts"))
    if not any(m.get("metric") == "temperature" for m in ms):
        hay = "\n".join(str(getattr(parsed, k, "") or "")
                        for k in ("key_data_point", "summary", "full_text"))
        for v in implausible_text_temps(hay, max_f):
            out.append(flag(KIND_TEXT_TEMP, value_f=v,
                            detail=f"{v}F in the document text is above the {max_f:g}F "
                                   "ceiling; skipped by the urgency check"))
    return out


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def rows(flags: list[dict], metadata: dict, link: str, flagged_at: str) -> list[list]:
    """Sheet rows for one document's flags. Pure."""
    return [[flagged_at, metadata.get("doc_id", ""), metadata.get("document_name", ""),
             f["kind"], _cell(f.get("value_f")), f.get("well_id", ""),
             f.get("reading", ""), f.get("pages", ""), f.get("detail", ""), link]
            for f in flags]


def record(service, sheet_id: str, flags: list[dict], metadata: dict, link: str,
           flagged_at: str) -> int:
    """Append one document's flags. Returns the number written. Callers treat
    this as best-effort (wrap it; a failure must never block the document)."""
    if not flags:
        return 0
    sw.ensure_tab(service, sheet_id, TAB_REVIEW_FLAGS, REVIEW_FLAG_HEADERS)
    sw.append_rows(service, sheet_id, TAB_REVIEW_FLAGS, rows(flags, metadata, link, flagged_at))
    return len(flags)


def review_sheet_id() -> Optional[str]:
    """The PRIVATE Sheet id the review log lives on, or None: unset, or equal
    to the public GSHEET_ID (both jobs that write flags receive GSHEET_ID, so
    this check is effective in CI too)."""
    priv = (os.environ.get("GSHEET_ID_PRIVATE") or "").strip()
    if not priv or priv == (os.environ.get("GSHEET_ID") or "").strip():
        return None
    return priv


# Tabs only the PUBLIC case file has: a "private" Sheet holding one is the
# public one (or another public Sheet) by misconfiguration. Same check as
# govqa_watcher.looks_like_public_sheet. Cached per Sheet id for the run.
_PUBLIC_TABS = (sw.TAB_NEW, sw.TAB_EVIDENCE, sw.TAB_MEASUREMENTS)
_public_sheet_cache: dict = {}


def _is_public_sheet(service, sheet_id: str) -> bool:
    if sheet_id not in _public_sheet_cache:
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute(
            num_retries=sw.GOOGLE_API_NUM_RETRIES)
        titles = {t["properties"]["title"] for t in meta.get("sheets", [])}
        _public_sheet_cache[sheet_id] = bool(titles & set(_PUBLIC_TABS))
    return _public_sheet_cache[sheet_id]


def private_sheet_for_write(service) -> tuple[Optional[str], str]:
    """(sheet_id, "") when the private Sheet is configured and is not a public
    case-file Sheet; else (None, reason) with a fixed-phrase reason. The ONE
    check every owner-only/review write goes through (also owner_notices.py)."""
    sheet_id = review_sheet_id()
    if sheet_id is None:
        return None, "no private Sheet configured"
    if _is_public_sheet(service, sheet_id):
        return None, "GSHEET_ID_PRIVATE holds public case-file tabs"
    return sheet_id, ""


def collect(flags: list[dict], parsed=None, cfg: Optional[dict] = None) -> list[dict]:
    """`flags` plus parsed_flags(parsed, cfg); never raises (a failing scan of
    the parsed document is logged by class and the router's flags are kept)."""
    out = list(flags)
    if parsed is not None:
        try:
            out += parsed_flags(parsed, cfg or {})
        except Exception as pe:  # noqa: BLE001 — keep the router's flags regardless
            print(f"  review-flag scan of the parsed document skipped: {type(pe).__name__}")
    return out


def log_for_document(service, flags: list[dict], metadata: dict, link: str,
                     flagged_at: str, parsed=None, cfg: Optional[dict] = None) -> int:
    """For the watcher/backfill per-document path: add parsed_flags(parsed,
    cfg) to `flags` and record() them on the private Sheet. Entirely
    best-effort: any failure (including computing the flags) is logged by
    exception class only (the Actions log is public) and never blocks the
    document. Returns the number written (0 on failure or no private Sheet)."""
    flags = collect(flags, parsed, cfg)
    try:
        if not flags:
            return 0
        sheet_id, why = private_sheet_for_write(service)
        if sheet_id is None:
            print(f"  {len(flags)} review flag(s) not written: {why}")
            return 0
        n = record(service, sheet_id, flags, metadata, link, flagged_at)
        print(f"  {n} review flag(s) logged")
        return n
    except Exception as fe:  # noqa: BLE001 — the review log is best-effort
        print(f"  review-flag write skipped (doc still recorded): {type(fe).__name__}")
        return 0


def flags_since(service, sheet_id: str, since_iso: str,
                kinds: Optional[tuple] = None) -> list[dict]:
    """Every logged flag with Flagged At >= since_iso (ISO-8601 strings compare
    in time order), optionally limited to `kinds`. STRICT read: a transport
    failure raises (a weekly "nothing to review" must never come from a failed
    read); a never-created tab reads as no flags."""
    out = []
    for r in sw._tab_rows_strict(service, sheet_id, TAB_REVIEW_FLAGS, "A2:J"):
        r = list(r) + [""] * (len(REVIEW_FLAG_HEADERS) - len(r))
        rec = dict(zip(REVIEW_FLAG_HEADERS, r))
        if rec["Flagged At"] < since_iso:
            continue
        if kinds and rec["Kind"] not in kinds:
            continue
        out.append(rec)
    return out

