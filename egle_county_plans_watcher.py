"""
egle_county_plans_watcher.py — watch named counties' rows in EGLE's "Currently
Approved County Plans and Plan Amendments" table and email when one changes
(ADR 074).

WHY THIS EXISTS. The Pine Tree Acres expansion (Macomb County) needs EGLE to
approve Macomb's solid-waste plan amendment. EGLE records an approval by adding
the amendment to that county's row on its solid-waste planning page (a starred
year, e.g. "Marquette | 2007*"). Checked 2026-11-07: Macomb's row reads
"Macomb | Appendices", no amendment listed. Nothing else in the monitor sees
this page.

WHAT IT DOES each run (from watcher.py's daily run, gated on
egle_county_plans.enabled):
  1. Fetch the page with the repo's browser-UA fetcher (pfas_client.fetch_page;
     a bare client gets 403, a browser UA gets 200).
  2. For each configured county, read its row from the table that follows the
     "Currently Approved County Plans" heading: the row's visible text plus the
     file names its links point at (query strings dropped, so EGLE re-uploading
     the same PDF under a new ?rev= is not a change).
  3. Compare with the last recorded row (the "EGLE County Plans Watch" tab IS
     the state, append-only). A difference -> email quoting the old and new
     text, then record the new row.

ORDERING. For a change, the email goes out BEFORE the row is written: a crash
between the two re-sends the alert next day, it never loses it. A failed send
writes nothing, so the next run retries. (This event may happen once in
years; a duplicate email is the cheaper failure.)

FAILURES. A fetch error, or a page where the county's row can't be found, is a
FAILED CHECK, never a change: it appends a `fetch_failed` row. When failed
checks span `stale_alert_after_days` distinct days in a row (default 3), ONE
stale email goes out; the next successful read appends `recovered` and resets
the streak. First successful read for a county baselines silently.

RECIPIENTS are scoped (egle_county_plans.recipients); an empty list means no
email, never the shared alert list. A read error on the state tab raises (never
treated as a first run).
"""
from __future__ import annotations

import html as _html
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlsplit

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

DEFAULT_URL = ("https://www.michigan.gov/egle/about/organization/materials-management/"
               "solid-waste/planning/solid-waste-management-plans")
_HEADING = "Currently Approved County Plans"

# Statuses that carry a reference row (the text later reads compare against).
_REF = {"baseline", "changed", "recovered"}
_FAIL = "fetch_failed"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _row_text(fragment: str) -> str:
    """Visible text of a row, with the spacing around '|' and '*' tidied so
    markup whitespace alone never reads as a change."""
    t = _text(fragment)
    t = re.sub(r"\s*\|\s*", " | ", t)
    t = re.sub(r"\s+\*", "*", t)
    return t


def extract_county_row(page_html: str, county: str) -> dict | None:
    """{"text", "files"} for `county`'s row in the approved-plans table, or None
    when the heading, the table or the row can't be found. Pure — unit-tested.

    The row is the one whose text before any '|' or '(' is `county`,
    case-insensitive, optionally followed by a plan year ("Washtenaw 2019**"). `files` is the comma-joined list of link file
    names in page order, query string dropped."""
    i = page_html.find(_HEADING)
    if i < 0:
        return None
    t0 = page_html.find("<table", i)
    if t0 < 0:
        return None
    t1 = page_html.find("</table>", t0)
    table = page_html[t0:t1 if t1 > 0 else len(page_html)]
    # The county name may carry a plan year in the same link ("Washtenaw 2019**").
    want = re.compile(re.escape(county.strip()) + r"(?:\s+\d{4}\**)?", re.I)
    for m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", table, re.S | re.I):
        row = m.group(1)
        if "<td" not in row.lower():
            continue  # the header row
        text = _row_text(row)
        first = re.split(r"[|(]", text, maxsplit=1)[0].strip()
        if not want.fullmatch(first):
            continue
        files = []
        for h in re.findall(r'href="([^"]+)"', row, re.I):
            path = urlsplit(_html.unescape(h)).path
            files.append(path.rsplit("/", 1)[-1])
        return {"text": text, "files": ",".join(files)}
    return None


def decide(rows: list[list], county: str, fetched: dict | None, error: str,
           today: str, now: str, stale_after_days: int = 3) -> tuple[list, list]:
    """Pure decision step for one county. `rows` are the tab's rows (any county,
    COUNTY_PLANS_HEADERS order: date, county, status, text, files, alerted, note,
    checked_at); `fetched` is extract_county_row()'s result or None on a failed
    check (`error` says why).

    Returns (new_rows, alerts). Each alert is (kind, subject, body, row_index):
    `row_index` points into new_rows at the row to write only after the email
    went out (kind "changed"); None for alerts whose row is written regardless."""
    mine = [r for r in rows if len(r) > 2 and r[1] == county]
    ref = next((r for r in reversed(mine) if r[2] in _REF), None)
    trailing = []
    for r in reversed(mine):
        if r[2] != _FAIL:
            break
        trailing.append(r)

    if fetched is None:
        days = {r[0] for r in trailing} | {today}
        already = any(len(r) > 5 and r[5] == "stale" for r in trailing)
        stale = len(days) >= stale_after_days and not already
        row = [today, county, _FAIL, "", "", "stale" if stale else "", error[:300], now]
        alerts = []
        if stale:
            alerts.append(("stale",
                           f"[EGLE county plans] {county} row unreadable for {len(days)} days",
                           format_stale(county, len(days), error), None))
        return [row], alerts

    text, files = fetched["text"], fetched["files"]
    if ref is None:
        return [[today, county, "baseline", text, files, "no",
                 "first read, no alert", now]], []
    old_text = ref[3] if len(ref) > 3 else ""
    old_files = ref[4] if len(ref) > 4 else ""
    if (text, files) != (old_text, old_files):
        row = [today, county, "changed", text, files, "yes", "", now]
        return [row], [("changed",
                        f"[EGLE county plans] {county} row changed",
                        format_change(county, old_text, old_files, text, files), 0)]
    if trailing:
        return [[today, county, "recovered", text, files, "no",
                 f"readable again after {len(trailing)} failed check(s)", now]], []
    return [], []


def format_change(county: str, old_text: str, old_files: str, text: str, files: str,
                  url: str = DEFAULT_URL) -> str:
    return (
        f"{county} County's row in EGLE's \"Currently Approved County Plans and Plan "
        "Amendments\" table changed.\n\n"
        f"Before: {old_text or '(blank)'}\n"
        f"        files: {old_files or '(none)'}\n"
        f"Now:    {text or '(blank)'}\n"
        f"        files: {files or '(none)'}\n\n"
        "On this page EGLE marks amendments with * and entire amended plans with **.\n"
        "An automated page comparison; open the page to read the change:\n"
        f"  {url}\n")


def format_stale(county: str, days: int, error: str, url: str = DEFAULT_URL) -> str:
    return (
        f"The monitor could not read {county} County's row on EGLE's county plans page "
        f"for {days} days in a row, so a change there would go unseen.\n\n"
        f"Last error: {error or '(none recorded)'}\n\n"
        f"Please check the page by hand: {url}\n"
        "This email is sent once per outage; the next good read resets it.\n")


def check(sheets, sheet_id: str, cfg: dict, send_email, fetch_page=None) -> None:
    """One run for every configured county. Raises on a state-tab read error
    (the caller isolates it); a page fetch failure is a recorded failed check."""
    import sheet_writer as sw
    pc_cfg = cfg.get("egle_county_plans") or {}
    url = pc_cfg.get("url") or DEFAULT_URL
    counties = [str(c) for c in (pc_cfg.get("counties") or [])]
    recipients = list(pc_cfg.get("recipients") or [])
    stale_after = int(pc_cfg.get("stale_alert_after_days", 3))
    if not counties:
        print("[county-plans] no counties configured — nothing to do")
        return
    if fetch_page is None:
        import pfas_client as pc
        fetch_page = pc.fetch_page

    page, error = None, ""
    try:
        page = fetch_page(url)
    except Exception as e:  # noqa: BLE001 — any fetch failure is a failed check
        error = f"fetch failed: {type(e).__name__}"

    sw.ensure_county_plans_tab(sheets, sheet_id)
    rows = sw.read_county_plans_rows(sheets, sheet_id)  # raises on read error
    today, now = _today(), _now()
    for county in counties:
        fetched, err = None, error
        if page is not None:
            fetched = extract_county_row(page, county)
            if fetched is None:
                err = "row not found (page layout changed?)"
        new_rows, alerts = decide(rows, county, fetched, err, today, now, stale_after)
        for kind, subject, body, idx in alerts:
            if not recipients:
                print(f"[county-plans] {county}: {kind} (no recipients, log only)")
                if idx is not None:
                    new_rows[idx][5] = "no"
                continue
            try:
                body = body.replace(DEFAULT_URL, url)
                send_email(subject, body, cfg, recipients=recipients)
                print(f"[county-plans] {county}: {kind} emailed")
            except Exception as e:  # noqa: BLE001
                print(f"[county-plans] {county}: {kind} send FAILED ({type(e).__name__})")
                if idx is not None:
                    # Write nothing for this change: the next run sees the same
                    # difference and tries the email again.
                    new_rows[idx] = None
                elif kind == "stale":
                    # Keep the failed-check row but unmark it, so the next
                    # failed check tries the stale email again.
                    new_rows[0][5] = ""
        new_rows = [r for r in new_rows if r is not None]
        if new_rows:
            sw.append_county_plans_rows(sheets, sheet_id, new_rows)
        status = new_rows[0][2] if new_rows else "unchanged"
        print(f"[county-plans] {county}: {status}")


def run() -> int:
    """Manual entry point (python egle_county_plans_watcher.py)."""
    from config_loader import load_config
    import drive_client as dc
    import email_alerts as ea
    cfg = load_config()
    if not (cfg.get("egle_county_plans") or {}).get("enabled"):
        print("[county-plans] egle_county_plans.enabled is false — skipping (no-op).")
        return 0
    check(dc.sheets_service(), os.environ["GSHEET_ID"], cfg, ea.send_email)
    return 0


if __name__ == "__main__":
    sys.exit(run())
