"""
upcoming.py — the weekly digest's "Upcoming Activities (next 14 days)" section.

Reads a HAND-MAINTAINED "Upcoming" tab on a SEPARATE, PRIVATE Google Sheet
(`GSHEET_ID_PRIVATE`) shared ONLY with the service account and Trisha — never the
operator (GFL). That privacy is the whole point: the public case-file Sheet
(`GSHEET_ID`) is operator-visible and must never carry strategy-flavored key
dates, but this private tab may. See the handoff (Lotext
`handoffs/2026-07-13-arbor-hills-monitor-upcoming-activities-email.md`) and Arbor
Hills Action Center §3 item 13.

Tab columns: `date` (ISO YYYY-MM-DD), `end_date` (optional, for ranges), `title`.

Split by testability: `render_upcoming()` is PURE (unit-tested against a fixed
`today`); `fetch_upcoming()` / `ensure_upcoming_tab()` do the authenticated Sheets
I/O (the service-account path `sheet_writer` already uses — NOT the public gviz
CSV endpoint, which cannot read a private Sheet).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

UPCOMING_TAB = "Upcoming"
UPCOMING_HEADERS = ["date", "end_date", "title"]


def _parse_iso(s) -> date | None:
    s = (s or "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def ensure_upcoming_tab(service, sheet_id: str) -> None:
    """Create the private Sheet's "Upcoming" tab + header if absent, and reconcile
    the header every run — the same self-healing pattern as
    `sheet_writer.ensure_*_tabs()`. The CODE only guarantees the tab/header exist
    (so a fetch never fails on a missing tab); the ROWS are populated by Trisha
    from key-dates Section A (a human step — the monitor repo does not read the
    Lotext master)."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if UPCOMING_TAB not in existing:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": UPCOMING_TAB}}}]},
        ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{UPCOMING_TAB}'!A1",
        valueInputOption="RAW",
        body={"values": [UPCOMING_HEADERS]},
    ).execute()


def fetch_upcoming(service, sheet_id: str, tab: str = UPCOMING_TAB) -> list[dict]:
    """Read the private "Upcoming" tab via the authenticated Sheets API. Returns
    [{date, end_date, title}] (dates parsed to `date`; `end_date` may be None),
    skipping rows with no parseable date or a blank title. Never raises for a
    missing tab / transient error — returns [] so the digest degrades to no
    section rather than crashing the Sunday run."""
    try:
        resp = (
            service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"'{tab}'!A2:C").execute()
        )
    except Exception:  # noqa: BLE001 — missing tab / transient API error -> no section
        return []
    out: list[dict] = []
    for r in resp.get("values", []) or []:
        if not r:
            continue
        d = _parse_iso(r[0] if len(r) > 0 else "")
        title = (r[2] if len(r) > 2 else "").strip()
        if d is None or not title:
            continue
        out.append({
            "date": d,
            "end_date": _parse_iso(r[1] if len(r) > 1 else ""),
            "title": title,
        })
    return out


def render_upcoming(entries: list[dict], today: date, horizon_days: int = 14) -> str:
    """PURE: filter to the window [today, today+horizon_days], sort, and render the
    block. Returns "" when nothing falls in the window (the caller then OMITS the
    section entirely). A ranged entry (with `end_date`) counts as in-window when
    its [start, end] range INTERSECTS the window, so a multi-day event already
    under way still shows."""
    horizon = today + timedelta(days=horizon_days)
    rows = []
    for e in entries:
        start = e.get("date")
        if start is None:
            continue
        end = e.get("end_date") or start
        if end < start:                      # malformed range — treat as single day
            end = start
        if end < today or start > horizon:   # range does not intersect the window
            continue
        rows.append((start, end, (e.get("title") or "").strip()))
    if not rows:
        return ""
    rows.sort(key=lambda t: (t[0], t[1]))
    lines = [f"UPCOMING ACTIVITIES (next {horizon_days} days):"]
    for start, end, title in rows:
        when = start.isoformat() if end == start else f"{start.isoformat()} to {end.isoformat()}"
        lines.append(f"  - {when}  {title}")
    lines.append("")
    return "\n".join(lines)
