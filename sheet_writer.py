"""
sheet_writer.py — Google Sheets case file (four tabs).

Tabs:
  - "New Documents"        live feed of incoming docs (watcher writes here)
  - "Historical Documents" backfilled docs (backfill writes here; same schema)
  - "Evidence by Risk"     THE case file: evidence-type docs only, one row per
                           (risk, doc) — fan-out, so a doc tagged R4+R8 makes
                           two rows. Filter to R8, print, hand to EGLE.
  - "Risk Register"        R1-R8 with auto-counted evidence + most-recent date.

The routing/fan-out logic is pure (feed_row / evidence_rows) so it's unit-tested
without touching the Sheets API. Tab creation is idempotent (create-if-absent).
"""
from __future__ import annotations

from typing import Iterable

FEED_HEADERS = [
    "Date Filed", "Document Name", "Type", "Risks", "Severity",
    "Summary", "Key Data Point", "Link",
]
EVIDENCE_HEADERS = [
    "Risk", "Risk Name", "Date Filed", "Document Name",
    "Key Data Point", "Full Summary", "Link",
]
REGISTER_HEADERS = [
    "Risk", "Risk Name", "Description", "Evidence Count",
    "Most Recent Evidence Date", "Status Note",
]
# Atomic structured readings — one row per (document, reading). "Basis"
# distinguishes a measured value from a permitted ceiling / HOV-waiver limit.
# Filter to Metric=temperature, Basis=measured and sort by Well ID + As-Of Date
# to derive a per-well time series WITHOUT reprocessing source documents.
MEASUREMENTS_HEADERS = [
    "As-Of Date", "Well ID", "Metric", "Value", "Unit", "Basis",
    "Date Filed", "Document Name", "Note", "Link",
]

TAB_NEW = "New Documents"
TAB_HISTORICAL = "Historical Documents"
TAB_EVIDENCE = "Evidence by Risk"
TAB_REGISTER = "Risk Register"
TAB_MEASUREMENTS = "Measurements"

_TAB_HEADERS = {
    TAB_NEW: FEED_HEADERS,
    TAB_HISTORICAL: FEED_HEADERS,
    TAB_EVIDENCE: EVIDENCE_HEADERS,
    TAB_REGISTER: REGISTER_HEADERS,
    TAB_MEASUREMENTS: MEASUREMENTS_HEADERS,
}


# ---------------------------------------------------------------------------
# Pure routing logic (unit-tested without the API)
# ---------------------------------------------------------------------------


def feed_row(parsed, metadata: dict, link: str) -> list:
    """One row for the New/Historical feed tab."""
    return [
        metadata.get("date_filed", ""),
        metadata.get("document_name", ""),
        parsed.doc_type,
        ", ".join(parsed.risks),
        parsed.severity,
        parsed.summary,
        parsed.key_data_point,
        link,
    ]


def measurement_rows(parsed, metadata: dict, link: str) -> list[list]:
    """One row per structured reading. as_of_date falls back to the doc's filed
    date when the reading carries no date of its own."""
    rows = []
    for m in getattr(parsed, "measurements", []) or []:
        rows.append([
            m.get("as_of_date") or metadata.get("date_filed", ""),
            m.get("well_id") or "",
            m.get("metric", ""),
            m.get("value", ""),
            m.get("unit", ""),
            m.get("basis", ""),
            metadata.get("date_filed", ""),
            metadata.get("document_name", ""),
            m.get("note") or "",
            link,
        ])
    return rows


def evidence_rows(parsed, metadata: dict, link: str, risk_names: dict) -> list[list]:
    """Zero or more rows for the Evidence-by-Risk tab. Only evidence-type docs
    produce rows, and they fan out to one row per risk the doc speaks to."""
    if parsed.doc_type != "evidence" or not parsed.risks:
        return []
    rows = []
    for rid in parsed.risks:
        rows.append([
            rid,
            risk_names.get(rid, rid),
            metadata.get("date_filed", ""),
            metadata.get("document_name", ""),
            parsed.key_data_point,
            parsed.summary,
            link,
        ])
    return rows


# ---------------------------------------------------------------------------
# Sheets API
# ---------------------------------------------------------------------------


def ensure_tabs(service, sheet_id: str) -> None:
    """Create any of the four tabs that don't exist, and write its header row.
    Idempotent — safe on every run."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    requests = [
        {"addSheet": {"properties": {"title": title}}}
        for title in _TAB_HEADERS
        if title not in existing
    ]
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute()
    # Write headers for any tab that was just created (or is empty).
    for title, headers in _TAB_HEADERS.items():
        if title not in existing:
            _set_header(service, sheet_id, title, headers)


def _set_header(service, sheet_id: str, tab: str, headers: list) -> None:
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()


def append_rows(service, sheet_id: str, tab: str, rows: Iterable[list]) -> None:
    rows = [list(r) for r in rows]
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def write_document(
    service,
    sheet_id: str,
    parsed,
    metadata: dict,
    link: str,
    risk_names: dict,
    feed_tab: str = TAB_NEW,
) -> None:
    """Append a document to its feed tab, (if evidence) to Evidence by Risk, and
    its structured readings to Measurements."""
    append_rows(service, sheet_id, feed_tab, [feed_row(parsed, metadata, link)])
    append_rows(service, sheet_id, TAB_EVIDENCE, evidence_rows(parsed, metadata, link, risk_names))
    append_rows(service, sheet_id, TAB_MEASUREMENTS, measurement_rows(parsed, metadata, link))


def rebuild_risk_register_tab(service, sheet_id: str, risk_register: list[dict]) -> None:
    """Recompute the Risk Register summary from the Evidence-by-Risk tab:
    per-risk evidence count + most recent evidence date. Overwrites the tab body
    (rows 2+) so it always reflects current evidence."""
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{TAB_EVIDENCE}'!A2:G")
        .execute()
    )
    rows = resp.get("values", [])
    counts: dict[str, int] = {}
    latest: dict[str, str] = {}
    for r in rows:
        if not r:
            continue
        rid = r[0]
        date_filed = r[2] if len(r) > 2 else ""
        counts[rid] = counts.get(rid, 0) + 1
        if date_filed and date_filed > latest.get(rid, ""):
            latest[rid] = date_filed

    body_rows = []
    for risk in risk_register:
        rid = risk["id"]
        body_rows.append([
            rid,
            risk["name"],
            risk["description"],
            counts.get(rid, 0),
            latest.get(rid, ""),
            "",  # Status Note — hand-maintained
        ])
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{TAB_REGISTER}'!A2",
        valueInputOption="RAW",
        body={"values": body_rows},
    ).execute()
