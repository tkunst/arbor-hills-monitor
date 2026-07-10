"""
sheet_writer.py — Google Sheets case file (the human-facing tabs + the internal
processing-state tabs).

Human tabs:
  - "New Documents"        live feed of incoming docs (watcher writes here)
  - "Historical Documents" backfilled docs (backfill writes here; same schema)
  - "Evidence by Risk"     THE case file: evidence-type docs only, one row per
                           (risk, doc) — fan-out, so a doc tagged R4+R8 makes
                           two rows. Filter to R8, print, hand to EGLE.
  - "Risk Register"        R1-R8 with auto-counted evidence + most-recent date.
  - "Measurements"         atomic structured readings, one row per (doc, reading).

Internal tabs (prefixed "_", ignored by the Conservancy):
  - "_state"               per-document processing event log (append-only).
  - "_meta"                global singletons (pending digest, MMPC, last run).

Both internal tabs replace the old Drive JSON state file: a Google service
account on a personal Gmail has no Drive storage quota and cannot CREATE files,
but it CAN write cells in a Sheet the user already owns and shared. See ADR 006.

The routing/fan-out logic is pure (feed_row / evidence_rows) so it's unit-tested
without touching the Sheets API. Tab creation is idempotent (create-if-absent).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Iterable

FEED_HEADERS = [
    "Date Filed", "Document Name", "Type", "Risks", "Severity",
    "Summary", "Key Data Point", "Link", "Facility",
]
EVIDENCE_HEADERS = [
    "Risk", "Risk Name", "Date Filed", "Document Name",
    "Key Data Point", "Full Summary", "Link", "Facility",
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
    "Date Filed", "Document Name", "Note", "Link", "Facility",
]
# Internal processing-state tabs. "_state" is an APPEND-ONLY event log — one row
# per processing attempt — so there is never a read-modify-write race and the
# 754-doc backfill never has to rewrite a 150k-char JSON blob. "_meta" holds the
# three small global singletons as one JSON cell each.
STATE_HEADERS = ["Doc ID", "Status", "Error Count", "Processed At", "Payload JSON"]
META_HEADERS = ["Key", "Value JSON"]
# Durable PDF mirror index (written by the optional archiver, see ADR 007). One
# row per mirrored doc; the "Archive Link" is a Drive copy that survives EGLE
# removing/renaming the nSITE source. Append-only and keyed by Doc ID for dedup.
ARCHIVE_HEADERS = [
    "Doc ID", "Document Name", "Date Filed", "Risks",
    "Source (nSITE) Link", "Archive Link", "Archived At",
]

TAB_NEW = "New Documents"
TAB_HISTORICAL = "Historical Documents"
TAB_EVIDENCE = "Evidence by Risk"
TAB_REGISTER = "Risk Register"
TAB_MEASUREMENTS = "Measurements"
TAB_ARCHIVE = "Archived PDFs"
TAB_STATE = "_state"
TAB_META = "_meta"
# Stream C (WDS solid-waste) case-file tab. Deliberately NOT in _TAB_HEADERS:
# ensure_tabs() must not create it, so the Conservancy-visible Sheet gains no
# empty "WDS" tab until Stream C is actually enabled. wds_watcher creates it
# on demand via ensure_wds_tab() only inside an enabled run.
TAB_WDS = "WDS (Solid Waste)"
WDS_HEADERS = [
    "Date", "Change", "Collection", "Severity", "Risks", "Item", "Detail", "Link",
]

_TAB_HEADERS = {
    TAB_NEW: FEED_HEADERS,
    TAB_HISTORICAL: FEED_HEADERS,
    TAB_EVIDENCE: EVIDENCE_HEADERS,
    TAB_REGISTER: REGISTER_HEADERS,
    TAB_MEASUREMENTS: MEASUREMENTS_HEADERS,
    TAB_ARCHIVE: ARCHIVE_HEADERS,
    TAB_STATE: STATE_HEADERS,
    TAB_META: META_HEADERS,
}

# The _meta keys, with the defaults used when a key has never been written.
# `wds_seen` holds Stream C's per-collection seen-set + last_count (see
# wds_watcher). It is a JSON singleton like the others — ~420 short id/hash pairs
# stay well under the 50k-char cell cap. Present in defaults so read_state loads
# it and write_meta persists it with zero extra plumbing, even while Stream C is
# disabled (it just stays {}).
_META_DEFAULTS = {
    "pending_digest": [],
    "mmpc_minutes_found": {},
    "wds_seen": {},
    "last_run": "",
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
        metadata.get("facility_name", ""),
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
            metadata.get("facility_name", ""),
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
            metadata.get("facility_name", ""),
        ])
    return rows


# ---------------------------------------------------------------------------
# Sheets API
# ---------------------------------------------------------------------------


def ensure_tabs(service, sheet_id: str) -> None:
    """Create any missing tabs and reconcile each tab's header row (row 1) on
    EVERY run. Rewriting row 1 from _TAB_HEADERS is idempotent — it touches only
    the header, never data rows — so a newly-added column (e.g. "Facility")
    appears on already-created tabs and any header drift self-heals. See ADR 008."""
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
    # Reconcile the header row on every tab (created or pre-existing) each run.
    for title, headers in _TAB_HEADERS.items():
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


# ---------------------------------------------------------------------------
# Processing state (lives in the Sheet, not a Drive file — see ADR 006)
# ---------------------------------------------------------------------------


def read_state(service, sheet_id: str) -> dict:
    """Reconstruct the processing-state dict by reducing the _state event log and
    the _meta singletons. Returns the same shape the old Drive JSON state did:
    {processed: {doc_id: payload}, errors: {doc_id: count},
     pending_digest: [...], mmpc_minutes_found: {...}, last_run: "..."}.

    _state is append-only and chronological, so a doc's 'error' rows always
    precede its 'processed' row: we count errors as we go and clear them when the
    'processed' row arrives. Missing tabs (first run) reduce to an empty state."""
    state = {"processed": {}, "errors": {}, "skipped": {}}
    state.update({k: _copy_default(k) for k in _META_DEFAULTS})

    for r in _tab_rows(service, sheet_id, TAB_STATE, "A2:E"):
        doc_id = r[0]
        status = r[1] if len(r) > 1 else ""
        if status == "processed":
            state["processed"][doc_id] = _load_json(r[4] if len(r) > 4 else "", {})
            state["errors"].pop(doc_id, None)
        elif status == "skipped":
            # Terminal: an unprocessable-source doc, made visible as a stub feed
            # row instead of being silently dropped. Not "processed" (never
            # classified), but done — no retries, excluded from remaining.
            state["skipped"][doc_id] = _load_json(r[4] if len(r) > 4 else "", {})
            state["errors"].pop(doc_id, None)
        elif status == "error" and doc_id not in state["processed"] and doc_id not in state["skipped"]:
            state["errors"][doc_id] = state["errors"].get(doc_id, 0) + 1

    for r in _tab_rows(service, sheet_id, TAB_META, "A2:B"):
        key = r[0]
        if key in _META_DEFAULTS and len(r) > 1 and r[1]:
            state[key] = _load_json(r[1], _copy_default(key))
    return state


def mark_processed(service, sheet_id: str, doc_id: str, payload: dict, processed_at: str) -> None:
    """Append a 'processed' event for one doc. Append-only: crash-safe, no race."""
    _append_state_row(
        service, sheet_id,
        [doc_id, "processed", 0, processed_at, json.dumps(payload, sort_keys=True)],
    )


def mark_error(service, sheet_id: str, doc_id: str, error_count: int, processed_at: str) -> None:
    """Append an 'error' event for one doc (error_count is the running count)."""
    _append_state_row(service, sheet_id, [doc_id, "error", error_count, processed_at, ""])


def mark_skipped(service, sheet_id: str, doc_id: str, payload: dict, processed_at: str) -> None:
    """Append a terminal 'skipped' event for a doc whose source can't be parsed
    (legacy/encrypted .doc, zip, raw image). Pairs with a write_stub_row() so the
    doc is visible in the feed; clears its error count so it isn't retried."""
    _append_state_row(
        service, sheet_id,
        [doc_id, "skipped", "", processed_at, json.dumps(payload, sort_keys=True)],
    )


def write_stub_row(service, sheet_id: str, metadata: dict, link: str, reason: str,
                   feed_tab: str = TAB_HISTORICAL) -> None:
    """Append a minimal feed row for an unprocessable-source document, so it shows
    up in the case-file Sheet (title, date, facility, link) instead of being
    silently absent. No classification, no measurements — just a visible pointer.
    `link` should be the native downloadfile URL so a human can open the source."""
    stub = SimpleNamespace(
        doc_type="(unprocessable source)",
        risks=[],
        severity="skipped",
        summary=reason,
        key_data_point="",
    )
    append_rows(service, sheet_id, feed_tab, [feed_row(stub, metadata, link)])


def write_meta(service, sheet_id: str, state: dict) -> None:
    """Persist each _meta singleton as one JSON cell. Each stays far under the
    50k-char cell cap — but for a different reason per key: pending_digest is
    cleared every Sunday; mmpc_minutes_found and last_run are tiny; wds_seen is
    bounded by record count (~420 short id/hash pairs, grows only as EGLE files
    new records). That's unlike the 754-entry processed map, which is why per-doc
    state is rows in _state and these singletons are cells in _meta."""
    rows = [
        [k, json.dumps(state.get(k, _copy_default(k)), sort_keys=True)]
        for k in _META_DEFAULTS
    ]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{TAB_META}'!A2",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()


def _append_state_row(service, sheet_id: str, row: list) -> None:
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{TAB_STATE}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def _tab_rows(service, sheet_id: str, tab: str, a1: str) -> list[list]:
    """Return the non-empty value rows of a tab range, or [] if the tab is absent
    (a fresh Sheet before ensure_tabs, or a transient read error)."""
    try:
        resp = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=f"'{tab}'!{a1}")
            .execute()
        )
    except Exception:  # noqa: BLE001 — missing tab / transient API error
        return []
    return [r for r in resp.get("values", []) if r]


def _load_json(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return fallback


def _copy_default(key: str):
    """A fresh mutable copy of a _meta default (never share the module-level one)."""
    return json.loads(json.dumps(_META_DEFAULTS[key]))


# ---------------------------------------------------------------------------
# Durable PDF archive index (written by the optional archiver — see ADR 007)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stream C — WDS case-file tab (created on demand only when Stream C is enabled)
# ---------------------------------------------------------------------------


def ensure_wds_tab(service, sheet_id: str) -> None:
    """Create the WDS tab + header if absent. Called only from an enabled Stream C
    run, so the tab never appears until Trisha turns Stream C on. Header is written
    only when the tab is first created, so a subsequent manual header edit isn't
    stomped (and we skip a redundant write every run)."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_WDS not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_WDS}}}]},
        ).execute()
        _set_header(service, sheet_id, TAB_WDS, WDS_HEADERS)


def wds_event_row(ev: dict) -> list:
    """One WDS tab row from a wds_watcher event dict (pure — unit-tested). The link
    is site-aware (set by check_wds from wds.site_id); falls back to the dashboard
    root only if an event was built without one."""
    return [
        ev.get("date", ""),
        ev.get("kind", ""),
        ev.get("name", ""),
        ev.get("severity", ""),
        ", ".join(ev.get("risks", []) or []),
        ev.get("label", ""),
        ev.get("detail", ""),
        ev.get("link", "https://www.egle.state.mi.us/wdspi/"),
    ]


def write_wds_event(service, sheet_id: str, ev: dict) -> None:
    """Append one new/changed WDS record to the WDS tab."""
    append_rows(service, sheet_id, TAB_WDS, [wds_event_row(ev)])


def archived_doc_ids(service, sheet_id: str) -> set:
    """The set of Doc IDs already mirrored to Drive (col A of the Archived PDFs
    tab). Used by the archiver to skip docs it has already uploaded."""
    return {r[0] for r in _tab_rows(service, sheet_id, TAB_ARCHIVE, "A2:A") if r[0]}


def append_archive_row(
    service, sheet_id: str, doc_id: str, document_name: str, date_filed: str,
    risks, source_link: str, archive_link: str, archived_at: str,
) -> None:
    """Append one row to the Archived PDFs index AFTER the Drive upload succeeds
    (crash-safe: a kill before this re-uploads next run, and the find-in-folder
    check makes that idempotent). risks may be a list or a pre-joined string."""
    risks_str = ", ".join(risks) if isinstance(risks, (list, tuple)) else (risks or "")
    append_rows(service, sheet_id, TAB_ARCHIVE, [[
        doc_id, document_name, date_filed, risks_str,
        source_link, archive_link, archived_at,
    ]])
