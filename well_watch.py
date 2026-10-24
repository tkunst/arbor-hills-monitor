"""
well_watch.py — named single-well temperature trip-wires (ADR 058).

WHY: email_alerts.is_urgent already fires on ANY measured wellhead temperature
>= 145F, but every WOI Status Report carries HOV-permitted wells above 145F
(e.g. AHWW0279 at 154.6F in 2026 H1), so that alert fires on every report and
never says WHICH well crossed. A specific well Trisha is watching (first case:
AHW263R5, holding at 142-144F through June 2026, 170F HOV granted 7/23/2025 but
absent from the 4/7/2026 16-well renewal) would be buried. This module sends a
dedicated email naming the well, value, reading time, as-found vs ADJ, and the
filing, the first time a newly processed WOI-format filing shows that well at or
above its threshold.

Scope + limits (stated so nobody over-trusts it):
* Runs only on WOI-format filings routed by woi_router (WOI Status Reports and
  Gas-Extraction Exceedance filings). The NESHAP semi-annual Appendix A roster
  is NOT on the live path (neshap_table_parser is a dataset tool), and
  spreadsheets EGLE emails outside nSITE never reach the monitor.
* Latency = filing latency + one daily run. There is no live wellhead feed.

Matching is by well NUMBER via a regex, not the exact ID: wells get redrilled
(R5 -> R6) and the record mixes AHW/AHWW prefixes and zero-padding, so an exact
ID would silently stop matching after a redrill. ADJ (post-adjustment) readings
are scanned too and labeled: near the line an ADJ reading can be the one that
crosses. `since` drops re-printed historical rows (some reports carry history
tables) so an old 2025 reading can't masquerade as new.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable

import email_alerts


def _watches(cfg: dict) -> list[dict]:
    return list((cfg.get("well_watch") or {}).get("wells") or [])


def _reading_date(dt: str):
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", dt or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def find_hits(readings: Iterable, cfg: dict) -> list[tuple[dict, list]]:
    """Return [(watch, [readings >= threshold])] for each configured watch with
    at least one hit. Pure: no I/O. A reading with an unparseable date is KEPT
    when a `since` is set (fail toward alerting, never silently drop)."""
    out = []
    readings = list(readings)
    for w in _watches(cfg):
        pat = re.compile(w["pattern"], re.I)
        thr = float(w.get("threshold_f", 145))
        since = w.get("since")
        since_d = date.fromisoformat(str(since)) if since else None
        hits = []
        for r in readings:
            if r.temp is None or not getattr(r, "valid", True):
                continue
            if not (pat.match(r.well_id or "") or pat.match(r.raw_well_id or "")):
                continue
            if r.temp < thr:
                continue
            d = _reading_date(r.dt)
            if since_d and d is not None and d < since_d:
                continue
            hits.append(r)
        if hits:
            hits.sort(key=lambda r: (_reading_date(r.dt) or date.min, r.dt))
            out.append((w, hits))
    return out


def compose(watch: dict, hits: list, meta: dict, link: str) -> tuple[str, str]:
    peak = max(hits, key=lambda r: r.temp)
    label = watch.get("label") or watch["pattern"]
    thr = float(watch.get("threshold_f", 145))
    subject = (f"[WELL WATCH] {peak.well_id} at {peak.temp:.1f}F "
               f"(>= {thr:.0f}F) in {meta.get('document_name', 'a new filing')}")
    lines = [
        f"Well watch '{label}' tripped: {len(hits)} reading(s) at or above {thr:.1f}F.",
        "",
        f"Filing: {meta.get('document_name', '')} (filed {meta.get('date_filed', '')})",
        f"Link: {link}",
        "",
        "Readings (as-found = field reading; ADJ = after adjustment):",
    ]
    for r in hits:
        kind = "ADJ" if r.adj else "as-found"
        lines.append(f"  {r.well_id:<12} {r.dt:<17} {r.temp:6.1f}F  {kind:<8} p.{r.page}")
    if watch.get("note"):
        lines += ["", watch["note"]]
    return subject, "\n".join(lines)


def check_and_alert(readings, meta: dict, link: str, cfg: dict) -> int:
    """Send one email per tripped watch. Returns the number of watches that
    tripped. Best-effort by design at the call site (the Sheet is the system of
    record); a send failure raises to the caller, which logs and moves on."""
    tripped = find_hits(readings, cfg)
    for watch, hits in tripped:
        subject, body = compose(watch, hits, meta, link)
        recips = watch.get("recipients") or (cfg.get("well_watch") or {}).get("recipients")
        email_alerts.send_email(subject, body, cfg, recipients=recips or None)
        print(f"  [well_watch] {subject}")
    return len(tripped)
