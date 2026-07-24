"""
ride_watcher.py — daily watch on EGLE's RRDOpenData ArcGIS service (RIDE) for
the Arbor-Hills-area Part 201 sites (Layer 0) and the GFL Part 211 UST (Layer
1), alerting on any status change. Standalone + self-terminating, the same
shape as mmd_watcher.py / rop_watcher.py. See docs/decisions/019-ride-part201-watch.md.

WHY: this is the STATE'S OWN registry view of contaminated-site remediation
status (R5 — water quality / groundwater). A RiskCondition flip (e.g. "Risks
Present and Require Action in Short-term" -> "Risks Controlled-Interim"), a
Contaminants list changing, or a new Open_Release on the GFL UST is early,
citable signal for the case file. Statuses change rarely, so this watch is
near-silent in steady state.

SIX watched items, derived from TWO fetches (one per layer):
  - ride:<SiteID>      one item per Part 201 site's Layer-0 record —
                       81000033 Salem Landfill, 81000004 Arbor Hills - East,
                       81000835 7667 Chubb Rd, 81000840 7941 Salem Rd,
                       82008712 MITC Corridor.
  - ride:<FacilityID>  the GFL Part 211 UST's Layer-1 record — 00040223.

WHAT IT DOES per item (mirrors mmd_watcher/rop_watcher exactly):
  - build a canonical snapshot + hash it,
  - compare to the last snapshot recorded in the "RIDE Watch" tab (that tab IS
    the state — append-only, so no _meta clobber race),
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert describing what
    changed (row first = durable record; email best-effort),
  - hash unchanged -> no-op.

FETCH FAILURE (RideFetchError) is TRANSIENT, evaluated per layer: skip-and-warn
if every item derived from that layer already has a baseline; LOUD exit 1 if
ANY of them has none yet (an activation-time block must surface, not silently
no-op forever). A response that fetched but is structurally wrong
(RideParseError) is ALWAYS loud regardless of baseline status — a service
reorganization persists across runs, and going quiet would hide it forever
(same posture as mmd_watcher's MmdParseError / rop_watcher's RopParseError).

GATED on ride.enabled (false by default — brand-new poller against a live
external system, ships disabled per overnight-coder's new-source gate).
Flipping it on is a separate, later, human step. Runs daily (see
.github/workflows/ride-watch.yml).

NO DRIVE / OAUTH (same scope call as pfas/rop/mmd, ADR 012): the deliverable is
the ALERT + the durable Sheet row (which carries the full snapshot JSON), not a
document mirror. SMTP + Sheets are all this needs.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import ride_client as rc
import email_alerts as ea
from config_loader import load_config

# Human-readable names for the default watched ids (label context only — never
# used for matching; an unknown id just gets a generic label).
_KNOWN_SITE_NAMES = {
    "81000033": "Salem Landfill",
    "81000004": "Arbor Hills - East",
    "81000835": "7667 Chubb Rd",
    "81000840": "7941 Salem Rd",
    "82008712": "MITC Corridor",
}
_KNOWN_FACILITY_NAMES = {
    "00040223": "GFL Environmental USA, LLC — Part 211 UST",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _load_json(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return fallback


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact bug
    this guards (the watch doing real work / emailing before ride.enabled is
    set) has a direct unit test. Mirrors mmd_watcher/rop_watcher._should_run."""
    if not (cfg.get("ride") or {}).get("enabled"):
        return False, "ride.enabled is false — skipping (no-op)."
    return True, ""


def site_label(site_id) -> str:
    name = _KNOWN_SITE_NAMES.get(str(site_id))
    suffix = f" ({name})" if name else ""
    return f"RIDE Part 201 — Site {site_id}{suffix}"


def ust_label(facility_id) -> str:
    name = _KNOWN_FACILITY_NAMES.get(str(facility_id))
    suffix = f" ({name})" if name else ""
    return f"RIDE Part 211 UST — Facility {facility_id}{suffix}"


# ---------------------------------------------------------------------------
# Snapshot + diff (pure)
# ---------------------------------------------------------------------------


def site_snapshot(records: list[dict], site_id) -> dict:
    """Canonical, hash-stable snapshot of one Part 201 site's Layer-0
    record(s). Filters the shared fetch by SiteID, canonicalizes via
    ride_client.site_record_view, and sorts by the FULL field tuple (the
    ADR 018 partial-key lesson — never lose a row to a key collision). An
    EMPTY record set is a valid snapshot: a site disappearing from the
    registry (or, pre-baseline, never having appeared) is itself signal, not
    an error."""
    views = sorted(
        (rc.site_record_view(r) for r in records
         if str(r.get("SiteID", "")).strip() == str(site_id)),
        key=lambda d: tuple(d[f] for f in rc.LAYER0_FIELDS),
    )
    return {"site_id": str(site_id), "records": views}


def ust_snapshot(records: list[dict], facility_id) -> dict:
    """Canonical, hash-stable snapshot of one Part 211 UST's Layer-1
    record(s). Same shape/rationale as site_snapshot."""
    views = sorted(
        (rc.ust_record_view(r) for r in records
         if str(r.get("FacilityID", "")).strip() == str(facility_id)),
        key=lambda d: tuple(d[f] for f in rc.LAYER1_FIELDS),
    )
    return {"facility_id": str(facility_id), "records": views}


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as mmd_watcher/rop_watcher.snapshot_hash."""
    import hashlib
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _summarize(old_rows: list[dict], new_rows: list[dict], fields: tuple[str, ...],
              detail_fields: tuple[str, ...], absent_note: str, present_note: str,
              ) -> tuple[str, str]:
    """Shared (note, body) diff for one item's record list — a full-record
    multiset diff (collections.Counter) keyed on every canonical field, so a
    record whose fields change shows as its old shape REMOVED + its new shape
    ADDED, never lost to a key collision (the mmd_watcher.summarize_change /
    rop_watcher.summarize_facility_change rationale). Parameterized by
    `fields`/`detail_fields` because Layer 0 and Layer 1 have different
    schemas; `absent_note`/`present_note` let each caller phrase the
    appear/disappear transition in its own vocabulary (site vs. facility)."""
    def _tup(r):
        return tuple(r.get(f, "") for f in fields)

    old_counts = Counter(_tup(r) for r in old_rows)
    new_counts = Counter(_tup(r) for r in new_rows)
    added = new_counts - old_counts
    removed = old_counts - new_counts

    def _detail(r: dict) -> str:
        return ", ".join(f"{f}={r[f] or '—'}" for f in detail_fields)

    lines: list[str] = []
    for t, n in sorted(added.items()):
        r = dict(zip(fields, t))
        lines.extend([f"+ ADDED    ({_detail(r)})"] * n)
    for t, n in sorted(removed.items()):
        r = dict(zip(fields, t))
        lines.extend([f"- REMOVED  ({_detail(r)})"] * n)

    if old_rows and not new_rows:
        note = absent_note
    elif new_rows and not old_rows:
        note = present_note
    elif not lines:
        note = "changed (no record-level diff — see snapshot)"
    else:
        parts = []
        if added:
            parts.append(f"{sum(added.values())} record(s) added/updated")
        if removed:
            parts.append(f"{sum(removed.values())} record(s) removed/superseded")
        note = "; ".join(parts)
    return note, "\n".join(lines)


def summarize_site_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) for one Part 201 site's snapshot change. Pure."""
    site_id = new.get("site_id") or old.get("site_id") or "?"
    detail_fields = tuple(f for f in rc.LAYER0_FIELDS if f not in ("SiteID", "SiteName"))
    return _summarize(
        old.get("records", []), new.get("records", []), rc.LAYER0_FIELDS, detail_fields,
        f"site {site_id} NO LONGER LISTED in EGLE's RRDOpenData (record removed)",
        f"site {site_id} NOW APPEARS in EGLE's RRDOpenData (was absent)",
    )


def summarize_ust_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) for the Part 211 UST's snapshot change. Pure."""
    facility_id = new.get("facility_id") or old.get("facility_id") or "?"
    detail_fields = tuple(f for f in rc.LAYER1_FIELDS if f not in ("FacilityID", "FacilityName"))
    return _summarize(
        old.get("records", []), new.get("records", []), rc.LAYER1_FIELDS, detail_fields,
        f"facility {facility_id} NO LONGER LISTED in EGLE's RRDOpenData (record removed)",
        f"facility {facility_id} NOW APPEARS in EGLE's RRDOpenData (was absent)",
    )


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested."""
    shown = body or "(no further detail — see the RIDE Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills Part 201 / Part 211 UST record changed in "
        "EGLE's RIDE RRDOpenData registry.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's Part 201 contaminated-site "
        "remediation / Part 211 underground-storage-tank status registry "
        "(RRDOpenData) — the state's own view of risk condition, contaminant "
        "classes, and open releases. A change here is early, citable R5 "
        "(water quality) signal worth reviewing at the source.\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _diff_and_record(sheets, sheet_id, today, key, label, snap, summarize_fn,
                     cfg, recipients) -> str:
    """Baseline/compare/record/alert for one item. Returns "baseline" /
    "changed" / "unchanged". Durable row FIRST, alert email SECOND
    (best-effort) — a crash between them loses the alert, never the record,
    and never re-fires next run since the row already advanced the stored
    hash. Each best-effort step gets its own try so a bug in one can never
    abort run()'s processing of the other watched items (mmd/rop_watcher's
    per-item guarantee)."""
    new_hash = snapshot_hash(snap)
    snap_json = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    last = sw.last_ride_snapshot(sheets, sheet_id, key)

    if last is None:
        sw.append_ride_watch_row(sheets, sheet_id, today, key, label, "baseline",
                                 new_hash, "initial snapshot (no alert)", _now(), snap_json)
        print(f"[ride-watch] {label}: baseline recorded ({new_hash}).")
        return "baseline"

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[ride-watch] {label}: unchanged ({new_hash}).")
        return "unchanged"

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_fn(old_snap, snap)
    sw.append_ride_watch_row(sheets, sheet_id, today, key, label, "changed",
                             new_hash, note, _now(), snap_json)
    print(f"[ride-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — formatting is best-effort; row is recorded
        print(f"[ride-watch] {label}: change recorded but alert body FORMATTING failed: {e}")
        return "changed"
    try:
        ea.send_email(f"[RIDE watch] {label} changed", email_body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
        print(f"[ride-watch] {label}: change recorded but alert email FAILED: {e}")
    return "changed"


def _all_baselined(sheets, sheet_id, keys) -> bool:
    """Whether EVERY key already has a baseline — the gate for treating a
    fetch failure as a transient skip-and-warn rather than a loud
    activation-time block. One batched tab read for all `keys` (the
    mmd_watcher/rop_watcher _all_baselined idiom)."""
    snaps = sw.last_ride_snapshots(sheets, sheet_id, keys)
    return all(v is not None for v in snaps.values())


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[ride-watch] {reason}")
        return 0

    rcfg = cfg.get("ride") or {}
    site_ids = [str(s) for s in (rcfg.get("site_ids") or rc.DEFAULT_SITE_IDS)]
    facility_ids = [str(f) for f in (rcfg.get("facility_ids") or rc.DEFAULT_FACILITY_IDS)]
    recipients = rcfg.get("recipients") or None  # None -> full alert_recipients list

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_ride_tabs(sheets, sheet_id)

    today = _today()
    counts = {"baseline": 0, "changed": 0, "unchanged": 0}
    exit_code = 0

    # --- Layer 0: Part 201 sites --------------------------------------------
    site_keys = [f"ride:{s}" for s in site_ids]
    site_records = None
    try:
        site_records = rc.fetch_site_records(site_ids)
        print(f"[ride-watch] Layer 0: fetched {len(site_records)} record(s) "
              f"for {len(site_ids)} site(s).")
    except rc.RideParseError as e:
        # A structural break (service reorganized), not a network blip — this
        # persists across runs, so it is ALWAYS loud (never gated on baseline
        # status; see the module docstring).
        print(f"[ride-watch] Layer 0: STRUCTURAL failure (failing loudly — "
              f"this is not a transient blip): {e}")
        exit_code = 1
    except rc.RideFetchError as e:
        if _all_baselined(sheets, sheet_id, site_keys):
            print(f"[ride-watch] Layer 0: fetch failed, skipping this run "
                  f"(baselines preserved, not diffed): {e}")
        else:
            print(f"[ride-watch] Layer 0: NO BASELINE for at least one site "
                  f"and fetch failed (failing loudly so activation surfaces it): {e}")
            exit_code = 1

    if site_records is not None:
        for site_id in site_ids:
            snap = site_snapshot(site_records, site_id)
            result = _diff_and_record(sheets, sheet_id, today, f"ride:{site_id}",
                                      site_label(site_id), snap, summarize_site_change,
                                      cfg, recipients)
            counts[result] += 1

    # --- Layer 1: GFL Part 211 UST -------------------------------------------
    facility_keys = [f"ride:{f}" for f in facility_ids]
    ust_records = None
    try:
        ust_records = rc.fetch_ust_records(facility_ids)
        print(f"[ride-watch] Layer 1: fetched {len(ust_records)} record(s) "
              f"for {len(facility_ids)} facility(ies).")
    except rc.RideParseError as e:
        print(f"[ride-watch] Layer 1: STRUCTURAL failure (failing loudly — "
              f"this is not a transient blip): {e}")
        exit_code = 1
    except rc.RideFetchError as e:
        if _all_baselined(sheets, sheet_id, facility_keys):
            print(f"[ride-watch] Layer 1: fetch failed, skipping this run "
                  f"(baselines preserved, not diffed): {e}")
        else:
            print(f"[ride-watch] Layer 1: NO BASELINE for at least one facility "
                  f"and fetch failed (failing loudly so activation surfaces it): {e}")
            exit_code = 1

    if ust_records is not None:
        for facility_id in facility_ids:
            snap = ust_snapshot(ust_records, facility_id)
            result = _diff_and_record(sheets, sheet_id, today, f"ride:{facility_id}",
                                      ust_label(facility_id), snap, summarize_ust_change,
                                      cfg, recipients)
            counts[result] += 1

    print(f"[ride-watch] done — {counts['changed']} changed, {counts['baseline']} "
          f"baselined, {counts['unchanged']} unchanged.")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
