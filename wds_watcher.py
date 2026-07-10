"""
wds_watcher.py — Stream C: poll EGLE WDS (solid waste) for site 475946, diff
against stored state, classify NEW / CHANGED records, and emit alerts.

Runs from watcher.py ONLY when `wds.enabled` is true (default false). See
docs/decisions/009-wds-stream-c.md. The four safety rules (each learned the hard
way — see the 2026-07-07 backfill/false-poison incident and the 2026-07-09 WDS
crawl session) are the reason this file is careful:

  A. Never diff a bad fetch. A collection that returns 0 rows, or collapses well
     below its last known count, is treated as a transient read failure — we skip
     the diff and warn. A short/failed fetch must NEVER be read as "records were
     deleted" (false flood) or drive a recovery that re-adds them as "new".

  B. Enabling is self-protecting. The FIRST run with an empty seen-set (never
     baselined) records every current row as seen and alerts on NONE — so flipping
     `enabled: true` cannot blast the list with ~420 historical records even if
     nobody ran the seed script. The same silent-baseline path also catches an
     over-cap run (a data anomaly): record all, alert none, warn loudly.

  C. Detect MUTATION, not just new rows. WDS back-fills fields after a record
     first appears: a QMR's Statistical-Exceedence flag + Review Notes arrive
     later, and — the single most important R1 signal — a Construction Permit
     application's Closure Type transitions pending -> Issued. So each record is
     keyed on IDENTITY (immutable) and carries a content HASH of its mutable
     fields; a changed hash re-alerts.

  D. WDS has its OWN classifier. It never touches email_alerts.is_urgent /
     _max_temperature_f — those scan free text for Fahrenheit readings and would
     trip on a QMR well id or an annual report's capacity digits. And a CME
     compliance action fires urgent only on a real violation / assessed penalty,
     never on a "PAID/RESOLVED" (that's good news).

Alerts route through the existing SMTP path (email_alerts.send_email) — no new
recipient surface. Urgent -> same-day email; notable/watch -> the weekly digest.
"""
from __future__ import annotations

import hashlib
import json

import wds_client as wc

# Fraction of the last-known row count below which a fetch is deemed suspect and
# the diff is skipped (rule A). 89 QMRs dropping to 3 = almost certainly a bad
# page, not 86 deletions. A legitimately shrinking collection is not a thing WDS
# does (these grids only grow), so this is a safe floor.
_COLLAPSE_FRACTION = 0.5


# ---------------------------------------------------------------------------
# Per-collection specs — identity (immutable key), content (mutable fields we
# re-alert on), a human label, and the WDS-specific severity classifier.
# ---------------------------------------------------------------------------

def _g(r, *keys):
    for k in keys:
        if r.get(k):
            return r[k]
    return ""


def _classify_qmr(r, changed):
    """QMR groundwater report. Exceedance = notable (R5); clean = watch. A
    blank->Yes back-fill is a CHANGE whose new content is 'Yes' -> notable."""
    exceed = _g(r, "Statistical Exceedence?").strip().lower()
    sev = "notable" if exceed == "yes" else "watch"
    return sev, "evidence", ["R5"]


def _classify_application(r, changed):
    """Solid-waste application. A Construction Permit (new, or transitioning to
    Issued) is the earliest hard signal of a physical expansion -> urgent (R1).
    An Operating License is notable."""
    typ = _g(r, "Application Type")
    if "construction permit" in typ.lower():
        return "urgent", "evidence", ["R1"]
    return "notable", "procedural", ["R1"]


def _classify_annual(r, changed, floor=3.0):
    """Annual Landfill Report. New report = watch (refresh capacity trend); but
    if estimated years-of-capacity-remaining is below the floor -> notable (R1
    airspace pressure)."""
    try:
        yrs = float(_g(r, "Yrs Remaining End", "Yrs Remaining Start"))
        if yrs and yrs < floor:
            return "notable", "evidence", ["R1"]
    except (TypeError, ValueError):
        pass
    return "watch", "evidence", ["R1"]


def _classify_evaluation(r, changed):
    """CME inspection/evaluation. Watch-tier (mostly routine in-compliance CEIs);
    the enforcement weight lives in compliance actions."""
    return "watch", "procedural", ["R2"]


def _classify_compliance_action(r, changed):
    """CME enforcement action. Urgent ONLY on a genuine adverse action — a
    violation notice, an assessed/demanded penalty, a compliance order, a consent
    judgment, civil/criminal action. NOT on a 'PAID/RESOLVED/CLOSED' row (good
    news) and not on routine correspondence (-> notable).

    A *changed* (vs new) adverse action is a backfill on an action we already
    alerted: the Type is part of the record identity, so `changed` can never mean
    the severity rose — only that a mutable field (e.g. Company Response Date)
    was filled in later. So downgrade a changed adverse action to notable rather
    than re-firing a duplicate URGENT for a case we already flagged."""
    t = _g(r, "Compliance Action Type").upper()
    if any(w in t for w in ("PAID", "RESOLVED", "CLOSED", "WITHDRAWN", "RESCIND")):
        return "watch", "procedural", ["R2"]
    if any(w in t for w in ("VIOLATION", "PENALTY DEMAND", "COMPLIANCE ORDER",
                            "CONSENT", "CIVIL", "CRIMINAL", "ENFORCEMENT",
                            "FINAL MONETARY")):
        return ("notable" if changed else "urgent"), "evidence", ["R2"]
    return "notable", "procedural", ["R2"]


COLLECTIONS = {
    "qmr": {
        "identity": lambda r: (_g(r, "Due Date"), _g(r, "Date Received")),
        "content": lambda r: (_g(r, "Statistical Exceedence?"), _g(r, "Review Notes")),
        "date": lambda r: _g(r, "Date Received", "Due Date"),
        "label": lambda r: f"QMR groundwater report (due {_g(r, 'Due Date') or '?'})",
        "detail": lambda r: (f"Statistical Exceedence: {_g(r, 'Statistical Exceedence?') or '-'}. "
                             f"{_g(r, 'Review Notes')}").strip(),
        "classify": _classify_qmr,
    },
    "applications": {
        "identity": lambda r: (_g(r, "Application Type"), _g(r, "Receipt Date")),
        "content": lambda r: (_g(r, "Closure Type"), _g(r, "Closure Date")),
        "date": lambda r: _g(r, "Receipt Date"),
        "label": lambda r: f"{_g(r, 'Application Type') or 'Application'} (received {_g(r, 'Receipt Date') or '?'})",
        "detail": lambda r: f"Closure: {_g(r, 'Closure Type') or 'pending'} {_g(r, 'Closure Date')}".strip(),
        "classify": _classify_application,
    },
    "annual": {
        "identity": lambda r: (_g(r, "Year"),),
        "content": lambda r: (_g(r, "Yrs Remaining End"), _g(r, "Waste_Total"),
                              _g(r, "Waste_Tons"), _g(r, "Total Permitted Capacity")),
        "date": lambda r: _g(r, "Year"),
        "label": lambda r: f"Annual Landfill Report — FY{_g(r, 'Year') or '?'}",
        "detail": lambda r: (f"Total waste {_g(r, 'Waste_Total') or '?'} CYDS "
                             f"({_g(r, 'Waste_Tons') or '?'} tons); "
                             f"years capacity remaining (end) {_g(r, 'Yrs Remaining End') or '?'}; "
                             f"permitted capacity {_g(r, 'Total Permitted Capacity') or '?'}"),
        "classify": _classify_annual,
    },
    "evaluations": {
        "identity": lambda r: (_g(r, "Evaluation Date"), _g(r, "Responsible Person")),
        "content": lambda r: (_g(r, "Evaluation Status"),),
        "date": lambda r: _g(r, "Evaluation Date"),
        "label": lambda r: f"EGLE evaluation ({_g(r, 'Evaluation Date') or '?'})",
        "detail": lambda r: f"{_g(r, 'Primary Reason')} — {_g(r, 'Evaluation Status')}".strip(" —"),
        "classify": _classify_evaluation,
    },
    "compliance_actions": {
        "identity": lambda r: (_g(r, "Compliance Action Date"), _g(r, "Compliance Action Type")),
        "content": lambda r: (_g(r, "Corrective Action Component"), _g(r, "Company Response Date")),
        "date": lambda r: _g(r, "Compliance Action Date"),
        "label": lambda r: f"Compliance action — {_g(r, 'Compliance Action Type') or '?'} ({_g(r, 'Compliance Action Date') or '?'})",
        "detail": lambda r: f"Lead program {_g(r, 'Lead Program')}; determined by {_g(r, 'Determined By')}".strip("; "),
        "classify": _classify_compliance_action,
    },
}


def _idkey(spec, r) -> str:
    return "|".join(str(x) for x in spec["identity"](r))


def _content_hash(spec, r) -> str:
    raw = "|".join(str(x) for x in spec["content"](r))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Pure diff engine (no network, no email — unit-tested directly)
# ---------------------------------------------------------------------------

def diff_collection(name: str, rows: list[dict], seen_entry: dict, cfg_wds: dict):
    """Diff freshly-fetched `rows` for one collection against `seen_entry`
    ({"records": {idkey: hash}, "last_count": N}).

    Returns (events, new_seen_entry, note). `events` is a list of
    {name, kind: new|changed, severity, doc_type, risks, label, detail, date,
    idkey, prev_hash, hash}. `prev_hash`/`hash` let the orchestrator revert a
    record's seen-state if its urgent alert fails to send (so it re-alerts next
    run rather than being silently buried). Enforces rules A (bad-fetch guard)
    and B (silent baseline / over-cap) here so the orchestrator stays simple. A
    baseline/guard result yields events=[] and a fully-updated seen entry (records
    everything, alerts nothing)."""
    spec = COLLECTIONS[name]
    records = dict(seen_entry.get("records") or {})
    last_count = int(seen_entry.get("last_count") or 0)
    # Drop rows with an empty identity — WDS grids carry a trailing blank
    # add-a-record template row (e.g. Applications returns 11 rows, one blank).
    # An identity-less row can't be tracked and must never become a phantom
    # "seen" record or fire a classifier on empty fields.
    rows = [r for r in rows if any(str(x).strip() for x in spec["identity"](r))]
    n = len(rows)

    def full_seen():
        recs = {_idkey(spec, r): _content_hash(spec, r) for r in rows}
        return {"records": recs, "last_count": len(recs)}

    # Rule A — bad-fetch guard. 0 rows, or a collapse below half the last known
    # count, is treated as a transient read failure: keep the old seen-set, emit
    # nothing, warn. (WDS grids only grow, so a real shrink doesn't happen.)
    if last_count and (n == 0 or n < last_count * _COLLAPSE_FRACTION):
        return [], seen_entry, f"{name}: fetch returned {n} (last {last_count}) — suspected bad fetch, skipped"

    # Rule B(i) — silent baseline on first-ever sight of this collection.
    if not records:
        e = full_seen()
        return [], e, f"{name}: baselined {e['last_count']} record(s), no alerts (first run)"

    # Diff.
    events = []
    floor = float(cfg_wds.get("years_remaining_floor", 3.0))
    for r in rows:
        k = _idkey(spec, r)
        h = _content_hash(spec, r)
        if k not in records:
            kind, prev = "new", None
        elif records[k] != h:
            kind, prev = "changed", records[k]
        else:
            continue
        records[k] = h
        if name == "annual":
            sev, dtype, risks = _classify_annual(r, kind == "changed", floor)
        else:
            sev, dtype, risks = spec["classify"](r, kind == "changed")
        events.append({
            "name": name, "kind": kind, "severity": sev, "doc_type": dtype,
            "risks": risks, "label": spec["label"](r), "detail": spec["detail"](r),
            "date": spec["date"](r), "idkey": k, "prev_hash": prev, "hash": h,
        })

    # Rule B(ii) — over-cap defense. If a single run produced more alert events
    # than the cap, this is almost certainly a first-enable-without-seed or a WDS
    # data anomaly, not 30 real same-day filings. Re-baseline silently and warn,
    # rather than blast the list. (Mirrors watcher.py's max_new_docs_per_run.)
    cap = int(cfg_wds.get("max_new_wds_alerts_per_run", 20))
    if len(events) > cap:
        e = full_seen()
        return [], e, (f"{name}: {len(events)} change events > cap {cap} — "
                       f"re-baselined silently (suspected first-enable/anomaly), no alerts")

    return events, {"records": records, "last_count": n}, f"{name}: {len(events)} event(s) from {n} record(s)"


# ---------------------------------------------------------------------------
# Alert composition (pure)
# ---------------------------------------------------------------------------

_SITE = "475946"


def _wds_link(site: str = _SITE) -> str:
    return f"https://www.egle.state.mi.us/wdspi/Dashboard.aspx?w={site}"


def format_urgent_body(ev: dict) -> str:
    changed = " (UPDATED)" if ev["kind"] == "changed" else " (NEW)"
    return (
        f"URGENT — Arbor Hills WDS (solid waste) change flagged.\n\n"
        f"{ev['label']}{changed}\n"
        f"Risks: {', '.join(ev['risks'])}\n\n"
        f"{ev['detail']}\n\n"
        f"Source (EGLE Waste Data System):\n  {ev.get('link') or _wds_link()}\n"
    )


def digest_record(ev: dict) -> dict:
    """Shape a WDS event to the same record the nSITE digest uses, so it flows
    through the existing Sunday digest (watcher._record_to_item + send_digest)."""
    return {
        "date_filed": ev["date"],
        "document_name": f"[WDS] {ev['label']}" + (" (updated)" if ev["kind"] == "changed" else ""),
        "doc_type": ev["doc_type"],
        "severity": ev["severity"],
        "risks": ev["risks"],
        "key_data_point": ev["detail"],
        "link": ev.get("link") or _wds_link(),
    }


# ---------------------------------------------------------------------------
# Orchestration (fetch -> diff -> route). Fetchers injected for testability.
# ---------------------------------------------------------------------------

def check_wds(state: dict, cfg: dict, send_email, fetchers=None, on_row=None) -> None:
    """Poll every enabled WDS collection, update state['wds_seen'] in place, send
    urgent emails immediately and append notable/watch items to state's pending
    digest. `send_email(subject, body, cfg)` and optional `on_row(event)` (Sheet
    writer) are injected. Never raises on a single collection's failure."""
    wds_cfg = cfg.get("wds") or {}
    w = str(wds_cfg.get("site_id", _SITE))
    enabled_collections = wds_cfg.get("collections") or list(COLLECTIONS)
    fetchers = fetchers or wc.FETCHERS
    seen = state.setdefault("wds_seen", {})

    for name in enabled_collections:
        if name not in COLLECTIONS:
            print(f"[wds] unknown collection {name!r} — skipping")
            continue
        try:
            rows = fetchers[name](w)
        except Exception as e:  # noqa: BLE001 — WDSFetchError / network → skip, warn
            print(f"[wds] {name}: fetch failed, skipping this run: {e}")
            continue

        entry = seen.get(name) or {"records": {}, "last_count": 0}
        events, new_entry, note = diff_collection(name, rows, entry, wds_cfg)
        seen[name] = new_entry
        print(f"[wds] {note}")

        link = _wds_link(w)
        for ev in events:
            ev["link"] = link
            if on_row:
                try:
                    on_row(ev)
                except Exception as se:  # noqa: BLE001 — Sheet write best-effort
                    print(f"[wds] Sheet write failed for {ev['idkey']}: {se}")
            if ev["severity"] == "urgent":
                subject = f"[URGENT] Arbor Hills WDS: {ev['label']}"
                try:
                    send_email(subject, format_urgent_body(ev), cfg)
                    print(f"[wds]   URGENT emailed: {ev['label']}")
                except Exception as ae:  # noqa: BLE001 — notification best-effort
                    # Don't bury an urgent signal on a transient SMTP fault: revert
                    # this record's seen-hash so the NEXT run re-alerts, instead of
                    # committing it as 'seen' and never firing again. (prev_hash is
                    # None for a brand-new record -> drop it entirely.)
                    recs = new_entry["records"]
                    if ev.get("prev_hash") is None:
                        recs.pop(ev["idkey"], None)
                    else:
                        recs[ev["idkey"]] = ev["prev_hash"]
                    new_entry["last_count"] = len(recs)
                    print(f"[wds]   URGENT send FAILED — reverted seen-state to re-alert next run: {ae}")
            else:
                state.setdefault("pending_digest", []).append(digest_record(ev))
