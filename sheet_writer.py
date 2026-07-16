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

Stream C (WDS solid-waste, ADR 009) gets its own tabs, structurally parallel to
the four above: "WDS New Documents" / "WDS Historical Documents" (same
WDS_HEADERS schema — live feed vs. one-off backfill dump, see
scripts/dump_wds_historical.py) / "WDS Evidence by Risk" (fan-out, same shape
as Evidence by Risk) / "WDS Page Snapshots" (raw-HTML portal-drift insurance,
see wds_archiver.py — WDS has no per-record PDFs to mirror the way Archived
PDFs does for nSITE, so a page snapshot is the real analog). All four are
created on demand via ensure_wds_tabs() only once Stream C is enabled/dumped —
same no-empty-tab-pre-activation policy the single WDS tab had before. WDS
shares the existing "_state"/"_meta" internal tabs (more _meta keys: wds_seen,
wds_snapshot_hashes) — no separate WDS internal tabs.

A fifth tab created alongside those four, "All Evidence by Risk", merges BOTH
Evidence-by-Risk tabs into one common schema (see all_evidence_rows() /
rebuild_all_evidence_tab()) — so filtering/sorting by Risk shows every risk's
evidence in one place, not split by which portal it came from.

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
# Stream C (WDS solid-waste) case-file tabs — structurally parallel to the
# nSITE New/Historical/Evidence tabs above. Deliberately NOT in _TAB_HEADERS:
# ensure_tabs() must not create them, so the Conservancy-visible Sheet gains no
# empty "WDS" tabs until Stream C is actually enabled/dumped. wds_watcher +
# scripts/dump_wds_historical.py + wds_archiver.py create them on demand via
# ensure_wds_tabs().
TAB_WDS_NEW = "WDS New Documents"
TAB_WDS_HISTORICAL = "WDS Historical Documents"
TAB_WDS_EVIDENCE = "WDS Evidence by Risk"
TAB_WDS_SNAPSHOTS = "WDS Page Snapshots"
# Mirror D (ADR 010) — same "created on demand, not by ensure_tabs()" treatment
# as the WDS tabs above, so the Sheet gains no empty tab until mmpc_archive is
# actually enabled/run.
TAB_MMPC_ARCHIVE = "MMPC Archived Files"
# PFAS page-watch (ADR 012) — same on-demand policy: no tab until the watch runs.
# This tab is BOTH the human-readable change log AND the watch's state: the most
# recent row per URL holds the last content hash + normalized text, so change
# detection needs no _meta key (append-only ⇒ race-free, unlike the _meta
# singletons every other job must not write concurrently). See pfas_watcher.py.
TAB_PFAS = "PFAS Page Watch"
# Unified view across BOTH Evidence-by-Risk tabs: one row per (risk, evidence
# item) regardless of source, so filtering/sorting by Risk shows everything —
# not just whichever portal's own tab you happened to open. Not itself WDS
# data (it carries nSITE rows too), but it only earns its keep once WDS
# evidence exists to merge in, so it's created alongside the WDS tabs (see
# _WDS_TAB_HEADERS) rather than unconditionally by ensure_tabs().
TAB_ALL_EVIDENCE = "All Evidence by Risk"
# WOI Well Summary (ADR 005 integration, woi_router) — same on-demand policy as
# the WDS/MMPC/PFAS tabs: no tab appears until a WOI Status Report is actually
# routed (ensure_woi_tabs). One row per well; the alert/watch-band readings also
# ride the Measurements tab, and the exhaustive ~14k-reading dump stays
# reproducible via scripts/woi_summary.py.
TAB_WOI_SUMMARY = "WOI Well Summary"
# GFL perimeter air (Stream E, ADR 014) — same on-demand policy as the WDS/MMPC/
# PFAS/WOI tabs: no tab appears until gfl_air_watcher actually polls. This tab is
# BOTH the small human-facing snapshot (latest reading per perimeter station) AND
# the watcher's cursor store: its OBJECTID column holds the highest reading id
# ingested so far, and gfl_air_watcher reads max(OBJECTID) back as the incremental
# cursor. It is written REPLACE (clear body + rewrite the ~6 station rows every
# run), so it stays small and always shows the current snapshot — unlike the
# append-only PFAS/WDS tabs. Safe as a non-append-only state store because only
# gfl-air.yml writes it (its own concurrency group serializes it with itself), so
# there is no cross-workflow clobber the way shared _meta has — which is exactly
# why the cursor lives here, not in _meta (a separate workflow must never write
# _meta; see pfas_watcher / ADR 014).
TAB_GFL_AIR = "GFL Air"
# CivicClerk meeting-change watch (ADR 015) — same on-demand policy as the WDS/
# MMPC/PFAS/WOI/GFL tabs: no tab appears until civicclerk_watcher actually runs.
# Append-only ⇒ race-free state (the last row per Event ID IS the last snapshot),
# exactly like the PFAS Page Watch tab — NOT _meta, so a separate workflow can't
# clobber it. Distinct from MMPC Archived Files (that mirrors category-72 PDFs to
# Drive by fileId; this watches a HAND-PICKED list of MMPC + BOC events for ANY
# change — date, title, status, or its document set — and alerts, no Drive).
TAB_MEETING_WATCH = "Meeting Watch"
# Ridge Wood Elementary H2S monthly reports (Stream G, ADR 016) — same on-demand
# policy as the WDS/MMPC/PFAS/WOI/GFL/Meeting tabs: no tab appears until
# ridgewood_archiver actually runs. Append-only, keyed by Month (YYYY-MM) in col A
# for dedup — the mmpc_archived_file_ids idiom (Sheet-derived ⇒ race-free, NOT
# _meta, since this is its own workflow). Distinct from the GFL Air tab (that is
# Stream E's live perimeter feed; this is the school-adjacent Barr/EPA-agreement
# monitor's monthly QA'd reports). The month's 24-hr-average value rides the shared
# Measurements tab; this tab is the archive index + provenance record.
TAB_RIDGEWOOD = "Ridge Wood Reports"
# ROP (air Title V permit) watch (ADR 017) — same on-demand policy as the
# WDS/MMPC/PFAS/WOI/GFL/Meeting/Ridgewood tabs: no tab appears until rop_watcher
# actually runs. Append-only, keyed by Item (e.g. "csv:N2688") in col B for
# dedup/state — the PFAS/Meeting Watch idiom (Sheet-derived ⇒ race-free, NOT
# _meta). One row per observed state of one of the FIVE watched items (the three
# target facilities' ROP CSV rows, the N2688 folder listing, the statewide
# public-notice N2688 mention) — "baseline" (first sighting, silent) or
# "changed" (fires an alert). See rop_watcher.py.
TAB_ROP = "ROP Watch"
# Shared by WDS New + Historical, the same way FEED_HEADERS is shared by
# TAB_NEW/TAB_HISTORICAL. "Change" is new/changed (live) or historical (dump).
WDS_HEADERS = [
    "Date", "Change", "Collection", "Severity", "Risks", "Item", "Detail", "Link",
]
# WDS analog of EVIDENCE_HEADERS: leads with the fan-out key (Risk), then reuses
# WDS_HEADERS' own column names (Item/Detail, not Document Name/Key Data Point —
# a WDS record isn't a filed document).
WDS_EVIDENCE_HEADERS = [
    "Risk", "Risk Name", "Date", "Change", "Collection", "Severity", "Item", "Detail", "Link",
]
# Merged nSITE + WDS evidence (see TAB_ALL_EVIDENCE). The two source tabs don't
# line up column-for-column (nSITE: Document Name/Key Data Point/Full Summary/
# Facility; WDS: Item/Detail/Severity/Collection), so this is a deliberately
# smaller common schema, not a superset — "Source" preserves provenance and
# "Facility / Collection" folds in whichever of those two the row's source
# tab actually has.
ALL_EVIDENCE_HEADERS = [
    "Risk", "Risk Name", "Date", "Source", "Item", "Detail", "Facility / Collection", "Link",
]
# Raw-HTML page snapshots (wds_archiver.py) — portal-drift insurance for a
# 2001-era ASP.NET app with no per-record PDFs to mirror. One row per
# (collection, page) actually uploaded — content-hash-gated (wds_snapshot_hashes
# below), not written every night regardless of whether the page changed.
WDS_SNAPSHOT_HEADERS = [
    "Date", "Collection", "Page", "Content Hash", "Drive Link", "Fetched At",
]
# Mirror D (ADR 010) — MMPC agenda/minutes/other PDFs auto-pulled from
# CivicClerk. One row per archived file, keyed by File ID for dedup (same
# idiom as ARCHIVE_HEADERS/archived_doc_ids(), not the WDS content-hash
# pattern — these are static per-ID PDFs like nSITE's, not a live page).
# Deliberately NOT in _TAB_HEADERS: no "MMPC Archived Files" tab appears
# until mmpc_archive.enabled is actually turned on (ensure_mmpc_tabs()).
MMPC_ARCHIVE_HEADERS = [
    "File ID", "Meeting Date", "Type", "Document Name",
    "Event ID", "Archive Link", "Archived At",
]
# PFAS page-watch (ADR 012). One row per observed state of a watched page —
# "baseline" (first sighting, silent) or "changed" (fires an alert). The last
# column carries the full normalized content: it's what next run diffs against
# and a durable dated snapshot of what the page said (the honest "snapshot" — no
# Drive/OAuth needed, well under the 50k-char cell cap for an ~8 KB page). It's
# last so the human-facing columns read cleanly to its left.
PFAS_SNAPSHOT_HEADERS = [
    "Date", "Page", "URL", "Change", "Content Hash", "Chars",
    "Note", "Fetched At", "Normalized Text",
]

# CivicClerk meeting-change watch (ADR 015). One row per observed state of a
# watched meeting event — "baseline" (first sighting, silent) or "changed" (fires
# an alert). The last column carries the canonical snapshot JSON (event name/date/
# status + the sorted document set): it's what next run diffs against AND a durable
# dated record of what the meeting looked like. Last so the human columns read
# cleanly to its left (same layout choice as PFAS_SNAPSHOT_HEADERS).
MEETING_WATCH_HEADERS = [
    "Date", "Group", "Meeting", "Event ID", "URL", "Change",
    "Snapshot Hash", "# Files", "Note", "Checked At", "Snapshot JSON",
]

# Ridge Wood Elementary H2S monthly reports (Stream G, ADR 016). One row per
# archived month, keyed by Month (YYYY-MM, col A) for dedup — same idiom as
# MMPC_ARCHIVE_HEADERS. Carries provenance (source URL / content hash / fetched-at),
# because this is evidence, plus the extracted monthly max 24-hr average and the
# classifier's alert verdict. The Drive mirror link is optional (blank when the
# Drive folder secret isn't provisioned — the extract+alert safety function does
# not depend on it; see ridgewood_archiver.py).
RIDGEWOOD_REPORT_HEADERS = [
    "Month", "Report Title", "Max 24-hr Avg (ppb)", "# Days", "Alert",
    "Source URL", "Content Hash", "Archive Link", "Fetched At",
]

# ROP watch (ADR 017). One row per observed state of a watched item — "baseline"
# (first sighting, silent) or "changed" (fires an alert). The last column carries
# the canonical snapshot JSON: what next run diffs against AND a durable dated
# record of what the source said. Last so the human columns read cleanly to its
# left (same layout choice as PFAS_SNAPSHOT_HEADERS/MEETING_WATCH_HEADERS).
ROP_WATCH_HEADERS = [
    "Date", "Item", "Label", "Change", "Snapshot Hash", "Note", "Checked At",
    "Snapshot JSON",
]

# WOI Well Summary (ADR 005 integration). One row per well from a routed WOI
# Status Report's per_well_summary(): max as-found temperature (with the O2/CH4
# read at that same moment), max O2, WOI-list membership, and reading count — the
# hand-to-EGLE per-well rollup. Not in _TAB_HEADERS: created on demand by
# ensure_woi_tabs() so no empty tab appears until a WOI report is routed.
WOI_SUMMARY_HEADERS = [
    "Report Date", "Well", "On WOI List", "Max Temp (F)", "Date of Max Temp",
    "O2 % at Max Temp", "CH4 % at Max Temp", "Max O2 % (any reading)",
    "# Readings", "Document Name", "Link",
]

# GFL perimeter air (Stream E, ADR 014). One row per perimeter station, the latest
# reading (REPLACE semantics — refreshed every run). "OBJECTID" is load-bearing:
# it is the watcher's cursor (max across rows = last reading ingested). "H2S/CH4
# Status" is gfl_air_client.classify_reading's per-pollutant verdict (ok /
# exceedance / sentinel / missing) so a human sees a flagged reading at a glance.
# Temp/Wind are meteorological CONTEXT only — never an alert metric (the feed's
# newest-hour Temp is briefly Celsius before it finalizes to Fahrenheit; see ADR).
GFL_AIR_SUMMARY_HEADERS = [
    "Station", "As-Of (UTC)", "H2S (ppb)", "H2S Status", "CH4 (ppm)", "CH4 Status",
    "Wind (mph)", "Wind Dir", "Temp (F)", "OBJECTID", "Note", "Link",
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
# wds_watcher). `wds_snapshot_hashes` holds wds_archiver.py's per-(collection,
# page) last-uploaded content hash, so a nightly run only uploads + logs a page
# whose HTML actually changed since last time (not ~5-20 near-identical files
# every single night forever). Both are JSON singletons like the others — small
# by construction (~420 short id/hash pairs; 5 collections x a few pages each)
# — well under the 50k-char cell cap. Present in defaults so read_state loads
# them and write_meta persists them with zero extra plumbing, even while Stream C
# is disabled (they just stay {}).
# NOTE on removing a key: the _meta tab is written positionally (write_meta
# below), so dropping a key here without care would leave a now-orphaned trailing
# row that read_meta could pick up as a stale value for a *different* key. That's
# why write_meta blanks a fixed row span (_META_CELL_ROWS) rather than only the
# live keys. The retired `mmpc_minutes_found` key (MMPC "go check" reminder,
# ADR 013) was removed this way — its old row is cleared on the next write.
_META_DEFAULTS = {
    "pending_digest": [],
    "wds_seen": {},
    "wds_snapshot_hashes": {},
    "last_run": "",
}

# write_meta overwrites this many _meta rows every time (live keys first, blanks
# after) so a key removed from _META_DEFAULTS can't leave a stale orphan row
# behind. Comfortably above the historical max key count (was 5). Only ever grow
# this; never shrink it below the largest key set the tab has held.
_META_CELL_ROWS = 8


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


def woi_summary_rows(summary: list[dict], metadata: dict, link: str) -> list[list]:
    """One row per well from woi_table_parser.per_well_summary() for a routed WOI
    Status Report (pure — unit-tested without the Sheets API). The parser already
    sorts hottest-first; None numeric cells render as blank so an ambient well
    with no reading doesn't write the literal 'None'."""
    def cell(v):
        return "" if v is None else v

    rows = []
    for d in summary:
        rows.append([
            metadata.get("date_filed", ""),
            d.get("well", ""),
            "yes" if d.get("is_woi") else "no",
            cell(d.get("max_temp_f")),
            cell(d.get("max_temp_date")),
            cell(d.get("o2_at_max_temp")),
            cell(d.get("ch4_at_max_temp")),
            cell(d.get("max_o2_pct")),
            cell(d.get("n_readings")),
            metadata.get("document_name", ""),
            link,
        ])
    return rows


def gfl_air_summary_rows(stations: list[dict], link: str) -> list[list]:
    """One row per perimeter station from the latest-reading snapshot the watcher
    builds (pure — unit-tested without the Sheets API). Each dict carries the
    reading's values + classify_reading statuses + the OBJECTID (the cursor).
    None numeric cells render blank so a station with no current reading doesn't
    write the literal 'None'."""
    def cell(v):
        return "" if v is None else v

    rows = []
    for s in stations:
        rows.append([
            s.get("station", ""),
            s.get("as_of", ""),
            cell(s.get("h2s")),
            s.get("h2s_status", ""),
            cell(s.get("ch4")),
            s.get("ch4_status", ""),
            cell(s.get("wind")),
            cell(s.get("direction")),
            cell(s.get("temp")),
            cell(s.get("oid")),
            s.get("note", ""),
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
            metadata.get("facility_name", ""),
        ])
    return rows


def wds_evidence_rows(ev: dict, risk_names: dict) -> list[list]:
    """WDS analog of evidence_rows(): zero or more rows for WDS Evidence by Risk,
    one per risk, for a doc_type=='evidence' wds_watcher event. Consumes the
    event's own (severity, doc_type, risks, label, detail, date) fields as-is —
    no reclassification here."""
    if ev.get("doc_type") != "evidence" or not ev.get("risks"):
        return []
    rows = []
    for rid in ev["risks"]:
        rows.append([
            rid,
            risk_names.get(rid, rid),
            ev.get("date", ""),
            ev.get("kind", ""),
            ev.get("name", ""),
            ev.get("severity", ""),
            ev.get("label", ""),
            ev.get("detail", ""),
            ev.get("link", ""),
        ])
    return rows


def all_evidence_rows(nsite_rows: list[list], wds_rows: list[list]) -> list[list]:
    """Merge nSITE's Evidence by Risk rows + WDS's Evidence by Risk rows into one
    common ALL_EVIDENCE_HEADERS schema, tagged with a Source column so
    provenance survives even though the two tabs' native schemas don't line up
    column-for-column. Pure — takes plain row lists (as read from either tab),
    no Sheets API — so it's unit-tested directly, same as evidence_rows() /
    wds_evidence_rows(). Row order is nSITE rows then WDS rows; the caller
    sorts/filters in the Sheet UI (dates are ISO-normalized on both sides, so a
    plain sort now works — see wds_watcher._iso_date)."""
    out = []
    for r in nsite_rows:
        if not r:
            continue
        risk = r[0]
        risk_name = r[1] if len(r) > 1 else ""
        date = r[2] if len(r) > 2 else ""
        item = r[3] if len(r) > 3 else ""
        kdp = r[4] if len(r) > 4 else ""
        summary = r[5] if len(r) > 5 else ""
        link = r[6] if len(r) > 6 else ""
        facility = r[7] if len(r) > 7 else ""
        detail = f"{kdp} — {summary}".strip(" —")
        out.append([risk, risk_name, date, "nSITE", item, detail, facility, link])
    for r in wds_rows:
        if not r:
            continue
        risk = r[0]
        risk_name = r[1] if len(r) > 1 else ""
        date = r[2] if len(r) > 2 else ""
        collection = r[4] if len(r) > 4 else ""
        item = r[6] if len(r) > 6 else ""
        detail = r[7] if len(r) > 7 else ""
        link = r[8] if len(r) > 8 else ""
        out.append([risk, risk_name, date, "WDS", item, detail, collection, link])
    return out


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
    """Recompute the Risk Register summary from BOTH Evidence-by-Risk tabs (nSITE
    + WDS): per-risk evidence count + most recent evidence date, unioned across
    the two tabs so R1/R2/R5 etc. aren't silently undercounted once WDS Evidence
    by Risk exists. Both tabs put their date in column C (index 2) despite
    different remaining schemas, so one _tally() helper serves both. Overwrites
    the tab body (rows 2+) so it always reflects current evidence from both
    streams."""
    counts: dict[str, int] = {}
    latest: dict[str, str] = {}

    def _tally(rows: list[list], date_col: int) -> None:
        for r in rows:
            if not r:
                continue
            rid = r[0]
            date_val = r[date_col] if len(r) > date_col else ""
            counts[rid] = counts.get(rid, 0) + 1
            if date_val and date_val > latest.get(rid, ""):
                latest[rid] = date_val

    nsite_resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{TAB_EVIDENCE}'!A2:G")
        .execute()
    )
    _tally(nsite_resp.get("values", []), date_col=2)

    wds_resp = _tab_rows(service, sheet_id, TAB_WDS_EVIDENCE, "A2:I")
    _tally(wds_resp, date_col=2)

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


def rebuild_all_evidence_tab(service, sheet_id: str) -> None:
    """Recompute 'All Evidence by Risk' from BOTH Evidence-by-Risk tabs (nSITE +
    WDS) via all_evidence_rows(). Unlike rebuild_risk_register_tab() (a fixed
    8-row register, safe to blind-overwrite), this tab's row count tracks total
    evidence and only grows under normal operation — but isn't GUARANTEED to
    (e.g. a manual re-dump can shrink the source tabs), so the range is
    cleared before writing rather than assuming monotonic growth."""
    nsite_resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{TAB_EVIDENCE}'!A2:H")
        .execute()
    )
    wds_rows = _tab_rows(service, sheet_id, TAB_WDS_EVIDENCE, "A2:I")
    body_rows = all_evidence_rows(nsite_resp.get("values", []), wds_rows)

    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"'{TAB_ALL_EVIDENCE}'!A2:H", body={}
    ).execute()
    if body_rows:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{TAB_ALL_EVIDENCE}'!A2",
            valueInputOption="RAW",
            body={"values": body_rows},
        ).execute()


# ---------------------------------------------------------------------------
# Processing state (lives in the Sheet, not a Drive file — see ADR 006)
# ---------------------------------------------------------------------------


def read_meta(service, sheet_id: str) -> dict:
    """Read just the _meta singletons (pending_digest, wds_seen,
    wds_snapshot_hashes, last_run) — no _state scan. For a caller that
    only touches _meta (e.g. wds_archiver.py, scripts/dump_wds_historical.py),
    read_state()'s full _state event-log scan is pure overhead. Also lets such a
    caller re-read FRESH right before each write_meta() call instead of writing
    back a stale run-start snapshot of keys it doesn't own — shrinks (doesn't
    eliminate) the window where a concurrent job's _meta write could be
    clobbered, since write_meta() always overwrites every key at once."""
    state = {k: _copy_default(k) for k in _META_DEFAULTS}
    for r in _tab_rows(service, sheet_id, TAB_META, "A2:B"):
        if not r:
            continue  # a blank padding row (write_meta clears orphans) — skip
        key = r[0]
        if key in _META_DEFAULTS and len(r) > 1 and r[1]:
            state[key] = _load_json(r[1], _copy_default(key))
    return state


def read_state(service, sheet_id: str) -> dict:
    """Reconstruct the processing-state dict by reducing the _state event log and
    the _meta singletons. Returns the same shape the old Drive JSON state did:
    {processed: {doc_id: payload}, errors: {doc_id: count},
     pending_digest: [...], wds_seen: {...}, last_run: "..."}.

    _state is append-only and chronological, so a doc's 'error' rows always
    precede its 'processed' row: we count errors as we go and clear them when the
    'processed' row arrives. Missing tabs (first run) reduce to an empty state."""
    state = {"processed": {}, "errors": {}, "skipped": {}}

    for r in _tab_rows(service, sheet_id, TAB_STATE, "A2:E"):
        doc_id = r[0]
        status = r[1] if len(r) > 1 else ""
        if status == "processed":
            state["processed"][doc_id] = _load_json(r[4] if len(r) > 4 else "", {})
            state["errors"].pop(doc_id, None)
            # A doc can go skipped -> processed via a targeted RETRY_DOC_IDS
            # retry (ADR 011) after a parser fix makes it processable — clear
            # the now-stale 'skipped' entry so state doesn't claim both
            # outcomes for the same doc indefinitely.
            state["skipped"].pop(doc_id, None)
        elif status == "skipped":
            # Terminal: an unprocessable-source doc, made visible as a stub feed
            # row instead of being silently dropped. Not "processed" (never
            # classified), but done — no retries, excluded from remaining.
            state["skipped"][doc_id] = _load_json(r[4] if len(r) > 4 else "", {})
            state["errors"].pop(doc_id, None)
        elif status == "error" and doc_id not in state["processed"] and doc_id not in state["skipped"]:
            state["errors"][doc_id] = state["errors"].get(doc_id, 0) + 1

    state.update(read_meta(service, sheet_id))
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
    cleared every Sunday; last_run is tiny; wds_seen is bounded by record count
    (~420 short id/hash pairs, grows only as EGLE files new records). That's
    unlike the 754-entry processed map, which is why per-doc state is rows in
    _state and these singletons are cells in _meta.

    We overwrite a fixed _META_CELL_ROWS-row span (live keys first, then blank
    rows) rather than just the live keys, so a key removed from _META_DEFAULTS
    can't leave a stale trailing row that read_meta would later mis-read as
    another key's value (see the _META_DEFAULTS note; ADR 013 removed one)."""
    rows = [
        [k, json.dumps(state.get(k, _copy_default(k)), sort_keys=True)]
        for k in _META_DEFAULTS
    ]
    # Pad to a fixed span so any orphan rows from a since-removed key are blanked.
    while len(rows) < _META_CELL_ROWS:
        rows.append(["", ""])
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
# Stream C — WDS case-file tabs (created on demand only when Stream C is
# enabled/dumped)
# ---------------------------------------------------------------------------

_WDS_TAB_HEADERS = {
    TAB_WDS_NEW: WDS_HEADERS,
    TAB_WDS_HISTORICAL: WDS_HEADERS,
    TAB_WDS_EVIDENCE: WDS_EVIDENCE_HEADERS,
    TAB_WDS_SNAPSHOTS: WDS_SNAPSHOT_HEADERS,
    TAB_ALL_EVIDENCE: ALL_EVIDENCE_HEADERS,
}


def ensure_wds_tabs(service, sheet_id: str) -> None:
    """Create any missing WDS tabs (all four together — mirrors ensure_tabs()
    creating New+Historical+Evidence together for nSITE even though only some
    are written on a given run) and reconcile each tab's header row on EVERY
    run — same self-healing policy ensure_tabs() uses for nSITE (ADR 008), so a
    newly-added column in WDS_HEADERS/WDS_EVIDENCE_HEADERS/WDS_SNAPSHOT_HEADERS
    reaches already-created tabs too, not just ones created after the change.
    Called only from an enabled Stream C run or the one-off dump/archiver
    scripts, so no WDS tab appears until Stream C is actually used."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    requests = [
        {"addSheet": {"properties": {"title": title}}}
        for title in _WDS_TAB_HEADERS
        if title not in existing
    ]
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute()
    for title, headers in _WDS_TAB_HEADERS.items():
        _set_header(service, sheet_id, title, headers)


def wds_event_row(ev: dict) -> list:
    """One WDS New/Historical Documents row from a wds_watcher event dict (pure —
    unit-tested). Works identically whether ev['kind'] is new/changed (live) or
    historical (bulk dump). The link is site-aware (set by check_wds, or by the
    dump/archiver scripts, from wds.site_id); falls back to the dashboard root
    only if an event was built without one."""
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


def write_wds_event(service, sheet_id: str, ev: dict, risk_names: dict,
                    feed_tab: str = TAB_WDS_NEW) -> None:
    """Append one WDS event to its feed tab (WDS New Documents by default; the
    live watcher never passes anything else — the historical dump script calls
    the lower-level wds_event_row()/wds_evidence_rows() + append_rows() directly
    for batching, not this function) and, if doc_type=='evidence', fan it out to
    WDS Evidence by Risk — same two-tabs-in-one-call shape as write_document()."""
    append_rows(service, sheet_id, feed_tab, [wds_event_row(ev)])
    append_rows(service, sheet_id, TAB_WDS_EVIDENCE, wds_evidence_rows(ev, risk_names))


def wds_historical_collections_dumped(service, sheet_id: str) -> set:
    """The set of WDS collections (col C = Collection) already present in WDS
    Historical Documents. Modeled on archived_doc_ids(): lets
    scripts/dump_wds_historical.py skip a collection it already wrote instead of
    double-appending its rows on a re-run."""
    return {r[2] for r in _tab_rows(service, sheet_id, TAB_WDS_HISTORICAL, "A2:C") if len(r) > 2 and r[2]}


def append_wds_snapshot_row(
    service, sheet_id: str, date: str, collection: str, page: int,
    content_hash: str, drive_link: str, fetched_at: str,
) -> None:
    """Append one row to WDS Page Snapshots, AFTER the Drive upload succeeds
    (crash-safe: a kill before this re-uploads next run — see wds_archiver.py's
    content-hash gate for why a re-upload of unchanged content is itself rare)."""
    append_rows(service, sheet_id, TAB_WDS_SNAPSHOTS, [[
        date, collection, page, content_hash, drive_link, fetched_at,
    ]])


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


def ensure_mmpc_tabs(service, sheet_id: str) -> None:
    """Create the MMPC Archived Files tab if missing and reconcile its header
    row on every run (same self-healing policy as ensure_tabs()/
    ensure_wds_tabs()). Called only from mmpc_archiver.py, so the tab doesn't
    appear until Mirror D actually runs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_MMPC_ARCHIVE not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_MMPC_ARCHIVE}}}]},
        ).execute()
    _set_header(service, sheet_id, TAB_MMPC_ARCHIVE, MMPC_ARCHIVE_HEADERS)


def mmpc_archived_file_ids(service, sheet_id: str) -> set:
    """The set of CivicClerk File IDs already mirrored to Drive (col A of MMPC
    Archived Files, as strings — Sheets returns cell values as strings/numbers
    inconsistently, so callers compare with str(file_id)). Modeled exactly on
    archived_doc_ids(); _tab_rows() already returns [] for a tab that doesn't
    exist yet (first run, before ensure_mmpc_tabs() has created it), so no
    extra handling is needed here."""
    return {str(r[0]) for r in _tab_rows(service, sheet_id, TAB_MMPC_ARCHIVE, "A2:A") if r and r[0]}


def append_mmpc_archive_row(
    service, sheet_id: str, file_id, meeting_date: str, doc_type: str,
    document_name: str, event_id, archive_link: str, archived_at: str,
) -> None:
    """Append one row to MMPC Archived Files AFTER the Drive upload succeeds
    (crash-safe, same ordering rationale as append_archive_row())."""
    append_rows(service, sheet_id, TAB_MMPC_ARCHIVE, [[
        file_id, meeting_date, doc_type, document_name,
        event_id, archive_link, archived_at,
    ]])


# ---------------------------------------------------------------------------
# PFAS page-watch (ADR 012) — the tab is the state (append-only ⇒ race-free)
# ---------------------------------------------------------------------------


def ensure_pfas_tabs(service, sheet_id: str) -> None:
    """Create the PFAS Page Watch tab if missing and reconcile its header row on
    every run (same self-healing policy as ensure_mmpc_tabs()). Called only from
    pfas_watcher.py, so the tab doesn't appear until the watch actually runs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_PFAS not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_PFAS}}}]},
        ).execute()
    _set_header(service, sheet_id, TAB_PFAS, PFAS_SNAPSHOT_HEADERS)


def last_pfas_snapshot(service, sheet_id: str, url: str) -> tuple[str, str] | None:
    """Return (content_hash, normalized_text) from the most recent row for `url`,
    or None if the page has never been snapshotted. This is the change-detection
    state: a None means 'baseline this page', and a hash mismatch means 'changed'.
    Reading the last matching row (not a _meta cell) is what makes the watch
    race-free — the tab is append-only, so no concurrent job can clobber it the
    way _meta singletons get clobbered. Rows are appended chronologically, so the
    last URL match is the latest snapshot."""
    latest = None
    for r in _tab_rows(service, sheet_id, TAB_PFAS, "A2:I"):
        if len(r) > 2 and r[2] == url:
            latest = r
    if latest is None:
        return None
    content_hash = latest[4] if len(latest) > 4 else ""
    text = latest[8] if len(latest) > 8 else ""
    return content_hash, text


def append_pfas_snapshot_row(
    service, sheet_id: str, date: str, page: str, url: str, change: str,
    content_hash: str, chars: int, note: str, fetched_at: str, normalized_text: str,
) -> None:
    """Append one PFAS Page Watch row. Written BEFORE the change email is sent
    (durable record first, alert best-effort second — same crash-safe ordering as
    the rest of the monitor: a kill after the row but before the email loses the
    alert, never the record, and never re-fires next run since the row already
    advances the stored hash)."""
    append_rows(service, sheet_id, TAB_PFAS, [[
        date, page, url, change, content_hash, chars, note, fetched_at, normalized_text,
    ]])


# ---------------------------------------------------------------------------
# CivicClerk meeting-change watch (ADR 015) — the tab is the state (append-only
# ⇒ race-free), exactly like the PFAS Page Watch tab above.
# ---------------------------------------------------------------------------


def ensure_meeting_watch_tabs(service, sheet_id: str) -> None:
    """Create the Meeting Watch tab if missing and reconcile its header row on
    every run (same self-healing policy as ensure_pfas_tabs()). Called only from
    civicclerk_watcher.py, so the tab doesn't appear until the watch actually
    runs — same no-empty-tab policy as the WDS/MMPC/PFAS/WOI/GFL tabs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_MEETING_WATCH not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_MEETING_WATCH}}}]},
        ).execute()
    _set_header(service, sheet_id, TAB_MEETING_WATCH, MEETING_WATCH_HEADERS)


def last_meeting_snapshot(service, sheet_id: str, event_id) -> tuple[str, str] | None:
    """Return (snapshot_hash, snapshot_json) from the most recent row for this
    event_id, or None if the event has never been snapshotted. None means
    'baseline this event'; a hash mismatch means 'changed'. Reading the last
    matching row (not a _meta cell) is what makes the watch race-free — the tab is
    append-only, so no concurrent job can clobber it. event_id is compared as a
    string: it's an int in the API/config but Sheets round-trips cell values as
    strings (same normalization as mmpc_archived_file_ids())."""
    latest = None
    target = str(event_id)
    for r in _tab_rows(service, sheet_id, TAB_MEETING_WATCH, "A2:K"):
        if len(r) > 3 and str(r[3]) == target:
            latest = r
    if latest is None:
        return None
    snap_hash = latest[6] if len(latest) > 6 else ""
    snap_json = latest[10] if len(latest) > 10 else ""
    return snap_hash, snap_json


def append_meeting_watch_row(
    service, sheet_id: str, date: str, group: str, meeting: str, event_id, url: str,
    change: str, snapshot_hash: str, n_files: int, note: str, checked_at: str,
    snapshot_json: str,
) -> None:
    """Append one Meeting Watch row. Written BEFORE the change email is sent
    (durable record first, alert best-effort second — same crash-safe ordering as
    append_pfas_snapshot_row: a kill after the row but before the email loses the
    alert, never the record, and never re-fires next run since the row already
    advances the stored hash)."""
    append_rows(service, sheet_id, TAB_MEETING_WATCH, [[
        date, group, meeting, event_id, url, change,
        snapshot_hash, n_files, note, checked_at, snapshot_json,
    ]])


# ---------------------------------------------------------------------------
# Ridge Wood Elementary H2S monthly reports (Stream G, ADR 016) — Month-keyed
# dedup, the mmpc_archived_file_ids idiom (Sheet-derived ⇒ race-free, NOT _meta).
# ---------------------------------------------------------------------------


def ensure_ridgewood_tabs(service, sheet_id: str) -> None:
    """Create the Ridge Wood Reports tab if missing and reconcile its header row on
    every run (same self-healing policy as ensure_mmpc_tabs()). Called only from
    ridgewood_archiver.py, so the tab doesn't appear until the archiver runs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_RIDGEWOOD not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_RIDGEWOOD}}}]},
        ).execute()
    _set_header(service, sheet_id, TAB_RIDGEWOOD, RIDGEWOOD_REPORT_HEADERS)


def ridgewood_archived_months(service, sheet_id: str) -> set:
    """The set of report months (YYYY-MM) already archived (col A of Ridge Wood
    Reports, as strings). Modeled on mmpc_archived_file_ids(); _tab_rows() returns
    [] for a tab that doesn't exist yet (first run, before ensure_ridgewood_tabs()
    has created it), so no extra first-run handling is needed."""
    return {str(r[0]) for r in _tab_rows(service, sheet_id, TAB_RIDGEWOOD, "A2:A") if r and r[0]}


def append_ridgewood_report_row(
    service, sheet_id: str, month: str, title: str, max_24h: str, n_days,
    alert: str, source_url: str, content_hash: str, archive_link: str, fetched_at: str,
) -> None:
    """Append one Ridge Wood Reports row. This row is the dedup 'done' marker,
    written LAST (after the Drive upload + the Measurements write) so a crash before
    it re-processes the month next run — an idempotent Drive re-upload plus at most a
    duplicate monthly measurement, never a dropped month. Full crash-safe ordering
    rationale in ridgewood_archiver.py."""
    append_rows(service, sheet_id, TAB_RIDGEWOOD, [[
        month, title, max_24h, n_days, alert,
        source_url, content_hash, archive_link, fetched_at,
    ]])


# ---------------------------------------------------------------------------
# ROP watch (ADR 017) — the tab is the state (append-only ⇒ race-free), exactly
# like the PFAS Page Watch / Meeting Watch tabs above.
# ---------------------------------------------------------------------------


def ensure_rop_tabs(service, sheet_id: str) -> None:
    """Create the ROP Watch tab if missing and reconcile its header row on every
    run (same self-healing policy as ensure_pfas_tabs()/ensure_meeting_watch_tabs()).
    Called only from rop_watcher.py, so the tab doesn't appear until the watch
    actually runs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_ROP not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_ROP}}}]},
        ).execute()
    _set_header(service, sheet_id, TAB_ROP, ROP_WATCH_HEADERS)


def last_rop_snapshot(service, sheet_id: str, item_key: str) -> tuple[str, str] | None:
    """Return (snapshot_hash, snapshot_json) from the most recent row for this
    item_key (e.g. "csv:N2688", "folder:N2688", "notice:N2688"), or None if the
    item has never been snapshotted. None means 'baseline this item'; a hash
    mismatch means 'changed'. Reading the last matching row (not a _meta cell) is
    what makes the watch race-free — the tab is append-only, so no concurrent job
    can clobber it (same idiom as last_pfas_snapshot/last_meeting_snapshot)."""
    latest = None
    for r in _tab_rows(service, sheet_id, TAB_ROP, "A2:H"):
        if len(r) > 1 and r[1] == item_key:
            latest = r
    if latest is None:
        return None
    snap_hash = latest[4] if len(latest) > 4 else ""
    snap_json = latest[7] if len(latest) > 7 else ""
    return snap_hash, snap_json


def append_rop_watch_row(
    service, sheet_id: str, date: str, item_key: str, label: str, change: str,
    snapshot_hash: str, note: str, checked_at: str, snapshot_json: str,
) -> None:
    """Append one ROP Watch row. Written BEFORE the change email is sent (durable
    record first, alert best-effort second — same crash-safe ordering as
    append_pfas_snapshot_row/append_meeting_watch_row)."""
    append_rows(service, sheet_id, TAB_ROP, [[
        date, item_key, label, change, snapshot_hash, note, checked_at, snapshot_json,
    ]])


# ---------------------------------------------------------------------------
# WOI Well Summary (ADR 005 integration, woi_router) — created on demand
# ---------------------------------------------------------------------------


def ensure_woi_tabs(service, sheet_id: str) -> None:
    """Create the WOI Well Summary tab if missing and reconcile its header row on
    every run (same self-healing policy as ensure_mmpc_tabs()/ensure_pfas_tabs()).
    Called from the watcher/backfill only when a WOI Status Report is actually
    routed (woi_router), so the tab doesn't appear until then — same no-empty-tab
    policy as the WDS/MMPC/PFAS tabs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_WOI_SUMMARY not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_WOI_SUMMARY}}}]},
        ).execute()
    _set_header(service, sheet_id, TAB_WOI_SUMMARY, WOI_SUMMARY_HEADERS)


def write_woi_summary(
    service, sheet_id: str, summary: list[dict], metadata: dict, link: str
) -> None:
    """Append the per-well summary rows for one routed WOI report. Best-effort at
    the call site: the feed row + Measurements are the system of record, so a
    summary-tab write failure must not block marking the doc processed. Re-routing
    a report (rare — normal operation processes each once) re-appends its rows, the
    same 'duplicate row, never a drop' tradeoff the feed/Measurements tabs accept."""
    append_rows(service, sheet_id, TAB_WOI_SUMMARY, woi_summary_rows(summary, metadata, link))


# ---------------------------------------------------------------------------
# GFL perimeter air (Stream E, ADR 014) — the "GFL Air" tab is BOTH the latest-
# reading snapshot AND the watcher's OBJECTID cursor. Created on demand; written
# REPLACE (not append) so it stays a small current snapshot.
# ---------------------------------------------------------------------------


def ensure_gfl_air_tabs(service, sheet_id: str) -> None:
    """Create the GFL Air tab if missing and reconcile its header row on every run
    (same self-healing policy as ensure_pfas_tabs()/ensure_woi_tabs()). Called only
    from gfl_air_watcher.py, so the tab doesn't appear until the watch actually
    runs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if TAB_GFL_AIR not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB_GFL_AIR}}}]},
        ).execute()
    _set_header(service, sheet_id, TAB_GFL_AIR, GFL_AIR_SUMMARY_HEADERS)


# Fixed write span for the GFL Air snapshot: the six MS-* stations plus headroom.
# The snapshot is written over this many rows every time (live rows first, then
# blank rows), so a shrinking station set (a station goes dark) can't leave an
# orphan row — AND the whole snapshot lands in ONE update() call. That single call
# is what makes the cursor crash-safe: a clear()+update() pair could die between
# the two, leaving the tab EMPTY, which the next run would read as "first run" and
# re-baseline — silently skipping every reading since the lost cursor. With one
# update, a crash leaves either the old cursor (harmless re-ingest next run) or the
# new one, never an empty tab. Only ever GROW this; never shrink it below the
# largest station count the tab has held (same rule as _META_CELL_ROWS).
_GFL_SUMMARY_ROWS = 12


def write_gfl_air_summary(service, sheet_id: str, stations: list[dict], link: str) -> None:
    """REPLACE the GFL Air snapshot with the current latest-reading-per-station
    rows in a SINGLE padded update (no clear). Replace (not append) keeps the tab
    small and current, and — because the OBJECTID column IS the cursor — makes the
    stored cursor exactly max(OBJECTID) over the current stations. Written AFTER the
    Measurements rows for the same poll (crash-safe order: a kill between them
    re-ingests the batch next run — a duplicate reading, never a dropped one — and
    the cursor only advances once this snapshot lands). See _GFL_SUMMARY_ROWS for
    why this is one update, not clear+update."""
    rows = gfl_air_summary_rows(stations, link)
    ncols = len(GFL_AIR_SUMMARY_HEADERS)
    while len(rows) < _GFL_SUMMARY_ROWS:
        rows.append([""] * ncols)        # blank the tail so a shrink leaves no orphan
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{TAB_GFL_AIR}'!A2",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()


def gfl_air_cursor(service, sheet_id: str):
    """The stored incremental cursor = the highest OBJECTID in the GFL Air tab, or
    None if the tab has no data rows yet (a genuine first run → the watcher
    baselines). MUST be called AFTER ensure_gfl_air_tabs (so the tab exists).

    Two correctness details:
      - Parse OBJECTID to int before max(). Sheets returns RAW cells as text, and
        "9" > "17614325" as strings — a string max() would silently REWIND the
        cursor and re-ingest ~214k rows. int(float(...)) also tolerates a "1234.0".
      - This does NOT swallow a read error the way _tab_rows does. A clean read
        with no rows returns None (baseline); an API failure PROPAGATES, so the
        watcher can skip-and-warn instead of mistaking a transient blip for 'first
        run' and re-baselining (which would skip real readings). See ADR 014."""
    oid_col = GFL_AIR_SUMMARY_HEADERS.index("OBJECTID")
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{TAB_GFL_AIR}'!A2:L")
        .execute()
    )
    best = None
    for r in resp.get("values", []):
        if len(r) > oid_col and str(r[oid_col]).strip():
            try:
                oid = int(float(str(r[oid_col]).strip()))
            except ValueError:
                continue
            best = oid if best is None else max(best, oid)
    return best


def gfl_air_latest_as_of(service, sheet_id: str):
    """The newest reading timestamp currently stored in the GFL Air tab — max over
    the station rows' 'As-Of (UTC)' column — or None if no row carries a parseable
    one. The liveness check (gfl_air_watcher) reads this on a zero-new-readings poll
    to tell a healthy quiet (recent As-Of) from a silent stall (an As-Of that is
    days old — ADR 014's OBJECTID-reset residual). Only rows that carry a real
    OBJECTID are considered, so a marker/annotation cell can never be mistaken for a
    reading. As-Of is stored as 'YYYY-MM-DDTHH:MMZ', which sorts chronologically, so
    the string max is the latest time. MUST be called after ensure_gfl_air_tabs."""
    asof_col = GFL_AIR_SUMMARY_HEADERS.index("As-Of (UTC)")
    oid_col = GFL_AIR_SUMMARY_HEADERS.index("OBJECTID")
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{TAB_GFL_AIR}'!A2:L")
        .execute()
    )
    best = None
    for r in resp.get("values", []):
        if len(r) <= max(asof_col, oid_col) or not str(r[oid_col]).strip():
            continue                         # blank/padding row, or not a reading row
        val = str(r[asof_col]).strip()
        if val and (best is None or val > best):
            best = val
    return best


# The stale-warned marker lives in column N — OUTSIDE the A:L station write span
# (GFL_AIR_SUMMARY_HEADERS is 12 cols) — so write_gfl_air_summary's REPLACE update
# never clobbers it, and there is no clear() to wipe it. It records the As-Of we
# last sent a liveness alert for; the gate is "warned == current newest As-Of". This
# SELF-RESETS without an explicit clear because As-Of is monotonic: when the feed
# recovers and later stalls again, the new stall carries a NEWER As-Of that differs
# from this marker, re-arming the alert. See ADR 014.
_GFL_STALE_MARKER_LABEL_CELL = "N1"
_GFL_STALE_MARKER_VALUE_CELL = "N2"


def gfl_air_stale_marker(service, sheet_id: str):
    """The As-Of string the liveness check last alerted on (once-per-stale-episode
    gate), or None if it has never fired. Stored in column N (see the note above)."""
    resp = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{TAB_GFL_AIR}'!{_GFL_STALE_MARKER_VALUE_CELL}")
        .execute()
    )
    vals = resp.get("values", [])
    if vals and vals[0] and str(vals[0][0]).strip():
        return str(vals[0][0]).strip()
    return None


def set_gfl_air_stale_marker(service, sheet_id: str, as_of: str) -> None:
    """Record the As-Of a liveness alert just fired on, so the next quiet poll for
    the SAME stall stays silent (once per episode). Writes a human label (N1) + the
    value (N2) in one update; column N is outside the station write span."""
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{TAB_GFL_AIR}'!{_GFL_STALE_MARKER_LABEL_CELL}",
        valueInputOption="RAW",
        body={"values": [["Stale-Warned As-Of (liveness)"], [as_of]]},
    ).execute()


# ---------------------------------------------------------------------------
# Force-reprocess support: purge a doc's rows so a re-extract is clean, not
# additive (backfill FORCE_REPROCESS_DOC_IDS — see backfill.py).
# ---------------------------------------------------------------------------

# The human, APPEND-ONLY tabs a document leaves rows in, paired with the 0-based
# index of that tab's "Link" column (the Link carries the doc's nSITE URL, whose
# final path segment is the doc_id — the join key). write_document re-adds fresh
# rows to exactly these tabs, so purging them makes the re-extract clean.
#
# Tabs a doc can ALSO appear in but that are deliberately NOT purged here — each
# handled a different, correct way:
#   - "All Evidence by Risk": DERIVED from "Evidence by Risk" (rebuilt via
#     clear+rewrite by rebuild_all_evidence_tab); purging Evidence + that rebuild
#     leaves it consistent, so a direct purge would be redundant.
#   - "Risk Register": DERIVED (per-risk counts, no doc rows); recomputed by
#     rebuild_risk_register_tab.
#   - "_state"/"_meta": the append-only processing log; a fresh 'processed' event
#     supersedes the old one, so rewriting it would break crash-safety.
#   - "Archived PDFs": the durable Drive-mirror index. Re-classifying a doc does
#     not change the mirrored source PDF, so the archive row stays valid — purging
#     it would orphan the mirror.
_PURGE_TABS = [
    (TAB_HISTORICAL, FEED_HEADERS.index("Link")),
    (TAB_NEW, FEED_HEADERS.index("Link")),
    (TAB_EVIDENCE, EVIDENCE_HEADERS.index("Link")),
    (TAB_MEASUREMENTS, MEASUREMENTS_HEADERS.index("Link")),
    (TAB_WOI_SUMMARY, WOI_SUMMARY_HEADERS.index("Link")),
]


def _link_doc_id(link) -> str:
    """The final path segment of a Link URL (…/downloadpdf/<doc_id> or
    …/downloadfile/<doc_id>), which is the nSITE doc_id. Query string stripped.
    Exact-segment match avoids a substring collision between two doc_ids."""
    return str(link).split("?")[0].rstrip("/").split("/")[-1]


def purge_doc_rows(service, sheet_id: str, doc_id: str, dry_run: bool = False) -> dict:
    """Delete every row belonging to `doc_id` from the human feed / evidence /
    measurement / WOI-summary tabs (matched by the doc_id being the final path
    segment of the row's Link), returning {tab: rows_matched}.

    Used by backfill's FORCE_REPROCESS_DOC_IDS path so a re-extract of an already-
    processed doc is CLEAN — the stale windowed rows are removed before the fresh
    exhaustive rows are written, instead of piling up beside them (which would,
    e.g., double-count a well's temperature in the Measurements tab).

    `dry_run=True` finds and reports the matches WITHOUT deleting — the preview an
    operator reviews before applying. Deletes are issued bottom-up per tab so row
    indices don't shift mid-batch. Safety net: this only ever runs for an explicit
    allowlist, and Google Sheets keeps File → Version history, so a mistaken purge
    is restorable in the UI."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    gid = {s["properties"]["title"]: s["properties"]["sheetId"]
           for s in meta.get("sheets", [])}

    requests: list[dict] = []
    counts: dict[str, int] = {}
    for tab, link_col in _PURGE_TABS:
        if tab not in gid:
            continue  # tab not created yet (e.g. WOI Summary before any route)
        resp = (
            service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"'{tab}'!A2:Z")
            .execute()
        )
        values = resp.get("values", [])
        # values[i] is data row i, i.e. 0-based grid row (i + 1) since row 0 is the
        # header. Sheets only trims TRAILING empty rows, so interior indices stay
        # aligned for any row that actually carries a Link.
        matched = [
            i + 1 for i, r in enumerate(values)
            if len(r) > link_col and r[link_col] and _link_doc_id(r[link_col]) == doc_id
        ]
        counts[tab] = len(matched)
        if not dry_run:
            for grid_row in sorted(matched, reverse=True):  # bottom-up within the tab
                requests.append({"deleteDimension": {"range": {
                    "sheetId": gid[tab], "dimension": "ROWS",
                    "startIndex": grid_row, "endIndex": grid_row + 1,
                }}})

    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute()
    return counts
