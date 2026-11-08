"""
wds_watcher.py — Stream C: poll EGLE WDS (solid waste) for site 475946, diff
against stored state, classify NEW / CHANGED records, and emit alerts. Also
polls `wds.extra_sites` (other landfills, ADR 074) with their own rules and
scoped recipients; see check_extra_sites() at the bottom.

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
import re

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


_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _iso_date(s: str) -> str:
    """Normalize WDS's unpadded M/D/YYYY dates (e.g. '4/30/2025', straight from
    the scraped page) to ISO 'YYYY-MM-DD', matching nsite_client.py's own
    isoformat() convention. Every Sheet write uses valueInputOption=RAW (stored
    as literal text, never a native Sheets date), so unpadded M/D/YYYY sorts by
    leading digit — i.e. by MONTH, not by year — both for a human sorting the
    Sheet and for rebuild_risk_register_tab()'s '>' string comparison that picks
    the 'most recent evidence' date. ISO text sorts correctly either way.
    Anything that doesn't match M/D/YYYY (blank, or a bare Year for annual
    reports, which already sorts fine on its own) passes through unchanged —
    a parse miss on this 2001-era portal must never raise."""
    m = _MDY_RE.match(s.strip()) if s else None
    if not m:
        return s
    mm, dd, yyyy = m.groups()
    return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"


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
        if yrs < floor:  # NOT `yrs and yrs < floor`: 0.0 is falsy, and 0.0
                         # years (airspace exhausted) is the most R1-critical
                         # signal — it must not short-circuit to watch.
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


def _classify_penalty(r, changed):
    """WDS penalty — a nested sub-grid under a compliance action, carrying the
    dollar amount the action row itself lacks (assessed / scheduled / paid).

    A NEW penalty is NOTABLE (R2), deliberately NOT urgent. Penalties render on the
    SAME ComplianceActions page as the `compliance_actions` collection, whose
    classifier already fires URGENT on exactly the parent events a penalty attaches
    to (violation notice / compliance order / consent decree / FINAL MONETARY). If
    the penalty were urgent too, one real enforcement event would send TWO urgent
    emails. So compliance_actions stays the same-day trip-wire; the penalty adds its
    dollar figure to the weekly digest. (See docs/decisions/043.)

    A *changed* penalty is a payment backfill — its only mutable fields are the
    scheduled/paid amounts and dates — i.e. the penalty getting paid: good news ->
    watch, never a re-fired notable."""
    if changed:
        return "watch", "evidence", ["R2"]
    return "notable", "evidence", ["R2"]


def _classify_composting_registration(r, changed):
    """Composting registration on the Utilization module — the across-the-road
    compost parcel's Part 115 authorization. A NEW registration is NOTABLE (R1): a
    fresh authorization to compost on the expansion-adjacent parcel (a renewal shows
    up as a new row with a new receipt date, so it is covered here too). An in-place
    change — e.g. Registration Status flipping 'Accepting from public' -> 'EXPIRED',
    or an expiration-date edit — is routine lifecycle -> watch."""
    return ("watch" if changed else "notable"), "evidence", ["R1"]


def _classify_composting_report(r, changed):
    """Composting annual report-year on the Utilization module (yard-waste and
    finished-compost tonnages). A routine annual filing — watch (R1: refreshes the
    compost-activity picture next to the landfill), new or backfilled alike."""
    return "watch", "evidence", ["R1"]


COLLECTIONS = {
    "qmr": {
        "identity": lambda r: (_g(r, "Due Date"), _g(r, "Date Received")),
        "content": lambda r: (_g(r, "Statistical Exceedence?"), _g(r, "Review Notes")),
        "date": lambda r: _iso_date(_g(r, "Date Received", "Due Date")),
        "label": lambda r: f"QMR groundwater report (due {_g(r, 'Due Date') or '?'})",
        "detail": lambda r: (f"Statistical Exceedence: {_g(r, 'Statistical Exceedence?') or '-'}. "
                             f"{_g(r, 'Review Notes')}").strip(),
        "classify": _classify_qmr,
    },
    "applications": {
        "identity": lambda r: (_g(r, "Application Type"), _g(r, "Receipt Date")),
        "content": lambda r: (_g(r, "Closure Type"), _g(r, "Closure Date")),
        "date": lambda r: _iso_date(_g(r, "Receipt Date")),
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
        "date": lambda r: _iso_date(_g(r, "Evaluation Date")),
        "label": lambda r: f"EGLE evaluation ({_g(r, 'Evaluation Date') or '?'})",
        "detail": lambda r: f"{_g(r, 'Primary Reason')} — {_g(r, 'Evaluation Status')}".strip(" —"),
        "classify": _classify_evaluation,
    },
    "compliance_actions": {
        # Identity includes Corrective Action Component + Company Response DUE
        # Date (both set once, at issuance) because EGLE's WDS grid can list
        # MULTIPLE distinct compliance-action records under the same (date,
        # type) — e.g. two separate corrective-action components of the same
        # violation notice. (date, type) alone collapsed those into one
        # tracked slot, so the diff loop compared them against each other and
        # flapped forever, firing a false 'changed' event every run with no
        # real change on EGLE's side — found live 2026-07-22 on the Arbor
        # Hills 10/10/2023 and 11/3/2023 violation notices. Company Response
        # DATE (the actual response, filled in whenever it happens) stays in
        # content — that's the genuine backfill this mutation-detection exists
        # for (see test_compliance_action_changed_violation_is_not_repeat_urgent).
        "identity": lambda r: (_g(r, "Compliance Action Date"), _g(r, "Compliance Action Type"),
                               _g(r, "Corrective Action Component"), _g(r, "Company Response Due Date")),
        "content": lambda r: (_g(r, "Company Response Date"),),
        "date": lambda r: _iso_date(_g(r, "Compliance Action Date")),
        "label": lambda r: f"Compliance action — {_g(r, 'Compliance Action Type') or '?'} ({_g(r, 'Compliance Action Date') or '?'})",
        "detail": lambda r: f"Lead program {_g(r, 'Lead Program')}; determined by {_g(r, 'Determined By')}".strip("; "),
        "classify": _classify_compliance_action,
        # ADR 025: map this record's already-structured fields onto the six-field
        # compliance-deadline schema (Compliance Deadlines tab). WDS carries no
        # separate extension field, so extension_due_date stays blank. Only this
        # collection defines a "deadline" extractor; the others have no dated
        # obligation to capture.
        "deadline": lambda r: {
            "item_description": _g(r, "Corrective Action Component")
            or _g(r, "Compliance Action Type"),
            "due_date": _iso_date(_g(r, "Company Response Due Date")),
            "extension_due_date": "",
            "actual_completion_date": _iso_date(_g(r, "Company Response Date")),
            "compelled_by": f"{_g(r, 'Compliance Action Type') or 'Compliance action'}"
            f" ({_g(r, 'Compliance Action Date') or '?'})",
            "compliance_doc_effective_date": _iso_date(_g(r, "Compliance Action Date")),
        },
    },
    "penalties": {
        # Identity keys on the four fields set once at assessment and never edited:
        # Action Date + Penalty Type + Document # + Assessment Amount. Two penalties
        # can share (date, doc#) — e.g. the 5/25/2023 pair on doc 115-05-2023, an
        # FA $15,300 and an AC $1,424.46 — so Penalty Type + Assessment Amount are
        # in the key to keep them distinct (they would otherwise flap 'changed'
        # against each other every run, the exact collision the compliance_actions
        # identity fix addressed; see test_penalties_sharing_date_and_doc_dont_collide).
        # The mutable payment fields live in content, so a later payment backfill
        # re-alerts (as a watch, per _classify_penalty).
        "identity": lambda r: (_g(r, "Action Date"), _g(r, "Penalty Type"),
                               _g(r, "Document #"), _g(r, "Assessment Amount")),
        "content": lambda r: (_g(r, "Scheduled Date"), _g(r, "Scheduled Amount"),
                              _g(r, "Date Paid"), _g(r, "Amount Paid")),
        "date": lambda r: _iso_date(_g(r, "Action Date")),
        "label": lambda r: f"WDS penalty — {_g(r, 'Penalty Type') or '?'} {_g(r, 'Assessment Amount') or ''}".strip(),
        "detail": lambda r: (f"{_g(r, 'Assessment Amount') or '?'} assessed "
                             f"({_g(r, 'Action Type') or 'compliance action'}, doc {_g(r, 'Document #') or '?'}); "
                             f"paid {_g(r, 'Amount Paid') or '-'} {_g(r, 'Date Paid')}").strip(),
        "classify": _classify_penalty,
    },
    "composting_registrations": {
        # Application Receipt Date is the natural per-registration key (one
        # registration per application; the six 475946 rows have six distinct
        # receipt dates). Status / expiration / admin-completeness are the mutable
        # lifecycle fields that re-alert (as a watch) when they change.
        "identity": lambda r: (_g(r, "Application Receipt Date"),),
        "content": lambda r: (_g(r, "Registration Status"),
                              _g(r, "Registration Expiration Date"),
                              _g(r, "Is Administratively Complete?"),
                              _g(r, "Admin Completeness Review Date"),
                              _g(r, "Registration Types")),
        "date": lambda r: _iso_date(_g(r, "Application Receipt Date")),
        "label": lambda r: f"Composting registration (received {_g(r, 'Application Receipt Date') or '?'})",
        "detail": lambda r: (f"Status {_g(r, 'Registration Status') or '?'}; "
                             f"expires {_g(r, 'Registration Expiration Date') or '?'}; "
                             f"admin complete {_g(r, 'Is Administratively Complete?') or '?'}").strip(),
        "classify": _classify_composting_registration,
    },
    "composting_reports": {
        # One row per report year; the tonnage detail is an opaque fingerprint
        # (Report Detail) so a later backfill re-alerts (watch). Bare Year sorts
        # fine on its own, like the annual collection — no _iso_date needed.
        "identity": lambda r: (_g(r, "Year"),),
        "content": lambda r: (_g(r, "Report Detail"),),
        "date": lambda r: _g(r, "Year"),
        "label": lambda r: f"Composting annual report — {_g(r, 'Year') or '?'}",
        "detail": lambda r: f"Composting report {_g(r, 'Year') or '?'}: {_g(r, 'Report Detail') or '-'}",
        "classify": _classify_composting_report,
    },
}


def _idkey(spec, r) -> str:
    return "|".join(str(x) for x in spec["identity"](r))


def _content_hash(spec, r) -> str:
    if "hash" in spec:  # extra-site override (ADR 074): a readable value, not a digest
        return spec["hash"](r)
    raw = "|".join(str(x) for x in spec["content"](r))
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _event_from_row(name: str, r: dict, kind: str, cfg_wds: dict, prev_hash=None,
                    classify=None, spec=None) -> dict:
    """Build one event dict for a single row, shared by diff_collection() (live
    new/changed path) and historical_events() (one-off bulk-dump path) — the
    ONLY place classification happens, so the two paths can never drift.
    `classify` overrides the Arbor Hills classifier and `spec` the collection
    spec; only the extra-site path (ADR 074) passes them."""
    spec = spec or COLLECTIONS[name]
    floor = float(cfg_wds.get("years_remaining_floor", 3.0))
    if classify is not None:
        # Extra-site classifiers also get the record's previous stored value
        # (None for a new record).
        sev, dtype, risks = classify(r, kind == "changed", prev_hash)
    elif name == "annual":
        sev, dtype, risks = _classify_annual(r, kind == "changed", floor)
    else:
        sev, dtype, risks = spec["classify"](r, kind == "changed")
    return {
        "name": name, "kind": kind, "severity": sev, "doc_type": dtype,
        "risks": risks, "label": spec["label"](r), "detail": spec["detail"](r),
        "date": spec["date"](r), "idkey": _idkey(spec, r), "prev_hash": prev_hash,
        "hash": _content_hash(spec, r),
        # ADR 025: only compliance_actions defines a deadline extractor; None else.
        "deadline": spec["deadline"](r) if "deadline" in spec else None,
    }


def historical_events(name: str, rows: list[dict], cfg_wds: dict) -> list[dict]:
    """One kind='historical' event per currently-fetched row of `name`, regardless
    of any seen-state. Used ONLY by the one-off scripts/dump_wds_historical.py —
    never by check_wds — so it never touches _meta.wds_seen and never alerts.
    kind='historical' (not 'new') so a WDS Historical Documents row is never
    mistaken for something the live watcher observed today. Applies the same
    identity-less-row filter diff_collection() does (the trailing blank
    add-a-record template row WDS grids carry)."""
    spec = COLLECTIONS[name]
    rows = [r for r in rows if any(str(x).strip() for x in spec["identity"](r))]
    return [_event_from_row(name, r, "historical", cfg_wds) for r in rows]


# ---------------------------------------------------------------------------
# Pure diff engine (no network, no email — unit-tested directly)
# ---------------------------------------------------------------------------

def diff_collection(name: str, rows: list[dict], seen_entry: dict, cfg_wds: dict,
                    classify=None, hasher=None):
    """Diff freshly-fetched `rows` for one collection against `seen_entry`
    ({"records": {idkey: hash}, "last_count": N}).

    Returns (events, new_seen_entry, note). `events` is a list of
    {name, kind: new|changed, severity, doc_type, risks, label, detail, date,
    idkey, prev_hash, hash}. `prev_hash`/`hash` let the orchestrator revert a
    record's seen-state if its urgent alert fails to send (so it re-alerts next
    run rather than being silently buried). Enforces rules A (bad-fetch guard)
    and B (silent baseline / over-cap) here so the orchestrator stays simple. A
    baseline/guard result yields events=[] and a fully-updated seen entry (records
    everything, alerts nothing). `classify` is passed through to
    _event_from_row (None = the Arbor Hills classifiers) and `hasher` replaces
    the stored per-record value (None = the 12-char content digest). Both are
    used only by the extra-site path (ADR 074)."""
    spec = COLLECTIONS[name]
    if hasher is not None:
        spec = {**spec, "hash": hasher}
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
        events.append(_event_from_row(name, r, kind, cfg_wds, prev_hash=prev,
                                      classify=classify, spec=spec))

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


def wds_link(site: str = _SITE) -> str:
    return f"https://www.egle.state.mi.us/wdspi/Dashboard.aspx?w={site}"


def format_urgent_body(ev: dict) -> str:
    changed = " (UPDATED)" if ev["kind"] == "changed" else " (NEW)"
    return (
        f"URGENT — Arbor Hills WDS (solid waste) change flagged.\n\n"
        f"{ev['label']}{changed}\n"
        f"Risks: {', '.join(ev['risks'])}\n\n"
        f"{ev['detail']}\n\n"
        f"Source (EGLE Waste Data System):\n  {ev.get('link') or wds_link()}\n"
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
        "link": ev.get("link") or wds_link(),
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

        link = wds_link(w)
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

    # Extra (related) sites, ADR 074 — AFTER the Arbor Hills loop, so nothing
    # above changes when `wds.extra_sites` is absent or empty, and isolated so a
    # fault on another landfill's pages can never cost an Arbor Hills alert.
    try:
        check_extra_sites(state, cfg, send_email, fetchers=fetchers)
    except Exception as e:  # noqa: BLE001 — extra sites must never break Stream C
        print(f"[wds] extra sites step skipped: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Extra (related) WDS sites — ADR 074. Another landfill whose solid-waste record
# matters to Arbor Hills (today: Pine Tree Acres, 398972, whose 75M CY
# expansion is the biggest single input to the regional capacity model).
#
# A plain BEFORE/NOW change watch, like the other snapshot watches: any change
# to a watched record — a new row, a changed field, a row no longer listed —
# goes out in ONE email per site per run showing what it said before and what
# it says now. No severity tiers and no inference about what a change means
# (an earlier design guessed "construction permit issued" and kept finding new
# ways to guess wrong); these records change a few times a year at most.
#
# These sites are NOT Arbor Hills evidence: their changes never touch the Arbor
# Hills Sheet tabs, the risk register or the coalition Sunday digest. Email goes
# only to the site's scoped `recipients`; empty = log only (never the shared
# list). State lives in the SAME `_meta.wds_seen` dict under
# "<site_id>:<collection>" keys (an Arbor Hills key never contains a colon), and
# the stored per-record value is the readable field text itself, so the next
# run can print "before". The ADR 024 `_wds_seen` backup/recovery log covers
# these keys as-is. Rule A (bad-fetch guard) and Rule B(i) (silent first
# baseline) apply unchanged; Rule B(ii)'s over-cap re-baseline is off (however
# many changes, it is one email, so nothing is ever silently dropped).
# ---------------------------------------------------------------------------

# Collections an extra site may watch, and the fields shown as before/now.
RELATED_FIELDS = {
    "applications": ["Closure Type", "Closure Date"],
    "annual": ["Total Permitted Capacity", "Yrs Remaining End", "Waste_Total", "Waste_Tons"],
}

_NOT_LISTED = "(not listed)"
_NO_LONGER = "(no longer listed)"


def _related_value(fields):
    """The stored per-record value: the watched fields as readable text,
    whitespace collapsed, a blank shown as '(blank)'."""
    def f(r):
        return "; ".join(f"{k}: {' '.join(str(_g(r, k)).split()) or '(blank)'}" for k in fields)
    return f


def _related_watch(r, changed, prev=None):
    return "watch", "related", []


def _key_label(name: str, idkey: str) -> str:
    """A readable label from a stored idkey (used for a row no longer listed,
    whose row dict is gone)."""
    parts = idkey.split("|")
    if name == "applications" and len(parts) == 2:
        return f"{parts[0] or 'Application'} (received {parts[1] or '?'})"
    if name == "annual":
        return f"Annual Landfill Report — FY{parts[0] or '?'}"
    return idkey


def _extra_key(site_id: str, name: str) -> str:
    return f"{site_id}:{name}"


def format_related_changes(changes: list[dict], site_name: str, site_id: str) -> str:
    """Before/now email body (pure). `changes` items: {label, before, now, link}."""
    lines = [f"Changes to {site_name}'s records in EGLE's Waste Data System "
             f"(WDS site {site_id}):", ""]
    for c in changes:
        lines += [c["label"], f"  Before: {c['before']}", f"  Now:    {c['now']}",
                  f"  Source: {c['link']}", ""]
    lines.append("An automated comparison of the WDS pages since the last check; open the "
                 "source to read the record.")
    return "\n".join(lines) + "\n"


def _send_ok(send_email, subject, body, cfg, recipients) -> bool:
    """True only when the mail was actually handed to SMTP. send_email returns
    False (no exception) when SMTP isn't configured; treating that as delivered
    would commit the change as seen and lose it."""
    try:
        return send_email(subject, body, cfg, recipients=recipients) is not False
    except Exception as e:  # noqa: BLE001
        print(f"[wds]   send raised {type(e).__name__}")
        return False


def check_extra_sites(state: dict, cfg: dict, send_email, fetchers=None) -> None:
    """Poll each `wds.extra_sites` entry's collections and send one before/now
    email per site per run. Never raises on one site's or one collection's
    failure; a collection that raises before its changes are collected keeps
    its previous seen-state."""
    wds_cfg = cfg.get("wds") or {}
    primary = str(wds_cfg.get("site_id", _SITE))
    fetchers = fetchers or wc.FETCHERS
    if not wds_cfg.get("extra_sites_enabled"):
        return
    seen = state.setdefault("wds_seen", {})
    for site in wds_cfg.get("extra_sites") or []:
        try:
            _check_one_extra_site(site or {}, primary, seen, wds_cfg, cfg, send_email, fetchers)
        except Exception as e:  # noqa: BLE001 — one site never stops the next
            print(f"[wds] extra site step failed: {type(e).__name__}: {e}")


# The identity field that gets a " #2", " #3" suffix when one fetch lists two
# rows with the same key (see _disambiguate).
_RELATED_KEY_FIELD = {"applications": "Receipt Date", "annual": "Year"}

# Runs in a row a stored row must be missing from an otherwise good fetch
# before it is reported "no longer listed" (a short page from WDS must not
# read as records deleted, then re-added the next day).
_GONE_AFTER_MISSES = 2


def _disambiguate(coll: str, rows: list[dict], value) -> list[dict]:
    """Copies of `rows` where a key listed more than once in this fetch gets a
    stable " #2", " #3" suffix on its last identity field, ordered by the
    stored value — so two same-type applications received the same day are
    two records, not one record flapping between two values every run."""
    spec = COLLECTIONS[coll]
    field = _RELATED_KEY_FIELD[coll]
    groups: dict = {}
    for r in rows:
        if any(str(x).strip() for x in spec["identity"](r)):
            groups.setdefault(_idkey(spec, r), []).append(r)
    out = [r for r in rows if not any(str(x).strip() for x in spec["identity"](r))]
    for k, rs in groups.items():
        rs = sorted(rs, key=value)
        out.append(rs[0])
        for n, r in enumerate(rs[1:], start=2):
            out.append({**r, field: f"{_g(r, field)} #{n}"})
    return out


def _collect(coll, rows, entry, wds_cfg, sid):
    """(new_entry, changes, note) for one collection — pure apart from the
    page-URL lookup. The caller restores `entry` itself if the email isn't sent."""
    fields = RELATED_FIELDS[coll]
    value = _related_value(fields)
    rows = _disambiguate(coll, rows, value)
    site_cfg = {**wds_cfg, "max_new_wds_alerts_per_run": 10**6}  # B(ii) off: never drop
    events, new_entry, note = diff_collection(
        coll, rows, entry, site_cfg, classify=_related_watch, hasher=value)
    link = wc.page_url(coll, sid)
    old = entry.get("records") or {}
    changes = []
    for ev in events:
        prev = ev["prev_hash"]
        changes.append({"label": ev["label"], "before": _NOT_LISTED if prev is None else prev,
                        "now": ev["hash"], "link": link})
    # Rows no longer listed. Only on a real diff: a first-run baseline (old is
    # empty) or a Rule A skip (new_entry IS the old entry) touches nothing.
    # diff_collection keeps every old key, so compare against the keys of the
    # rows actually fetched. A row must be missing on _GONE_AFTER_MISSES runs
    # in a row before it is reported (then dropped, so it is reported once);
    # the count lives in the entry's "missing" map and clears when it returns.
    if old and new_entry is not entry:
        spec = COLLECTIONS[coll]
        current = {_idkey(spec, r) for r in rows
                   if any(str(x).strip() for x in spec["identity"](r))}
        missing = {}
        prior = entry.get("missing") or {}
        for k in sorted(set(old) - current):
            n = int(prior.get(k, 0)) + 1
            if n < _GONE_AFTER_MISSES:
                missing[k] = n
                continue
            changes.append({"label": _key_label(coll, k), "before": old[k],
                            "now": _NO_LONGER, "link": link})
            new_entry["records"].pop(k, None)
        if missing:
            new_entry["missing"] = missing
    elif entry.get("missing"):
        new_entry["missing"] = entry["missing"]
    return new_entry, changes, note


def _check_one_extra_site(site, primary, seen, wds_cfg, cfg, send_email, fetchers) -> None:
    sid = str(site.get("site_id") or "").strip()
    if not sid.isdigit() or sid == primary:
        print(f"[wds] extra site {sid!r}: missing, non-numeric or the primary site — skipping")
        return
    name = site.get("name") or f"WDS site {sid}"
    recipients = list(site.get("recipients") or [])
    pending = []  # (key, previous entry) per collection with changes
    changes = []
    for coll in site.get("collections") or []:
        if coll not in RELATED_FIELDS or coll not in fetchers:
            print(f"[wds] {sid}: collection {coll!r} is not watchable for extra sites — skipping")
            continue
        try:
            rows = fetchers[coll](sid)
        except Exception as e:  # noqa: BLE001 — transient: skip, warn
            print(f"[wds] {sid}:{coll}: fetch failed, skipping this run: {e}")
            continue
        key = _extra_key(sid, coll)
        try:
            entry = seen.get(key) or {"records": {}, "last_count": 0}
            new_entry, coll_changes, note = _collect(coll, rows, entry, wds_cfg, sid)
        except Exception as e:  # noqa: BLE001 — keep the old seen-state
            print(f"[wds] {sid}:{coll}: diff failed, seen-state kept: {type(e).__name__}: {e}")
            continue
        seen[key] = new_entry
        print(f"[wds] {sid}:{note}" + (f"; {len(coll_changes)} change(s)" if coll_changes else ""))
        if coll_changes:
            changes += coll_changes
            pending.append((key, entry))
    if not changes:
        return
    if not recipients:
        print(f"[wds]   {sid}: {len(changes)} change(s) (no recipients, log only)")
        return
    subject = f"{name} WDS: {len(changes)} record change(s)"
    if _send_ok(send_email, subject, format_related_changes(changes, name, sid), cfg, recipients):
        print(f"[wds]   {sid}: change email sent ({len(changes)} change(s))")
        return
    # Not sent: restore each changed collection's previous entry, so the next
    # run sees (and reports) exactly the same changes again.
    for key, entry in pending:
        seen[key] = entry
    print(f"[wds]   {sid}: change email not sent — reverted to re-send next run")
