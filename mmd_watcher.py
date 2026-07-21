"""
mmd_watcher.py — daily watch on EGLE MMD Open Data's records for the Arbor
Hills wdsids, alerting on any change. Standalone + self-terminating, the same
shape as rop_watcher.py / pfas_watcher.py. See docs/decisions/018-mmd-open-data-watch.md.

WHY: this is the STATE'S OWN registry view of the facility — the layer-0 record
set carries the landfill's disposal-area status ("Active - Accepting"), its
Part 115 status, and a map-hidden (show=0) compost registration with an
expiration date. A status flip here (disposal area no longer accepting, a
compost registration lapsing or renewing, the hidden record surfacing, or the
separate 465941 compost-area record APPEARING in the service) is early, citable
signal in the airspace (R1) and MMP-expansion fights. Statuses change rarely,
so this watch is near-silent in steady state.

ONE watched item per configured wdsid, all derived from ONE fetch:
  - mmd:475946   the landfill's record set (SolidWaste + hidden CMPST records)
  - mmd:465941   the compost-area record set (EMPTY today — its first record
                 appearing is the trip-wire, the empty set is the baseline)

WHAT IT DOES per item (mirrors rop_watcher exactly):
  - build a canonical snapshot + hash it,
  - compare to the last snapshot recorded in the "MMD Watch" tab (that tab IS
    the state — append-only, so no _meta clobber race),
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert describing what
    changed (row first = durable record; email best-effort),
  - hash unchanged -> no-op.

FETCH FAILURE (MmdFetchError) is TRANSIENT: skip-and-warn if every watched
wdsid already has a baseline; LOUD exit 1 if ANY has none yet (an activation-
time block must surface, not silently no-op forever). A response that fetched
but is structurally wrong (MmdParseError — features missing, result truncated,
schema fields gone) is ALWAYS loud regardless of baseline status — a service
reorganization persists across runs, and going quiet would hide it forever
(same posture as rop_watcher's RopParseError).

GATED on mmd.enabled. Runs daily (see .github/workflows/mmd-watch.yml).

NO DRIVE / OAUTH (same scope call as pfas_watcher/rop_watcher, ADR 012): the
deliverable is the ALERT + the durable Sheet row (which carries the full
snapshot JSON), not a document mirror. SMTP + Sheets are all this needs.
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
import mmd_client as mc
import email_alerts as ea
from config_loader import load_config

# Human-readable names for the default watched wdsids (label context only —
# never used for matching; an unknown wdsid just gets a generic label).
_KNOWN_WDSIDS = {
    475946: "Arbor Hills Landfill, Inc",
    465941: "Arbor Hills Compost Area",
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
    this guards (the watch doing real work / emailing before mmd.enabled is set)
    has a direct unit test. Mirrors rop_watcher/pfas_watcher._should_run."""
    if not (cfg.get("mmd") or {}).get("enabled"):
        return False, "mmd.enabled is false — skipping (no-op)."
    return True, ""


def item_label(wdsid: int) -> str:
    name = _KNOWN_WDSIDS.get(int(wdsid))
    suffix = f" ({name})" if name else ""
    return f"MMD Open Data — WDS {wdsid}{suffix}"


# ---------------------------------------------------------------------------
# Snapshot + diff (pure)
# ---------------------------------------------------------------------------


def wdsid_snapshot(records: list[dict], wdsid: int) -> dict:
    """Canonical, hash-stable snapshot of one wdsid's layer-0 records. Filters
    the shared fetch by wdsid, canonicalizes each record (mmd_client.record_view),
    and sorts by the FULL field tuple — one wdsid legitimately carries multiple
    records (one per module registration), and sorting on a partial key would
    leave ties order-dependent (the rop_watcher.facility_snapshot lesson). An
    EMPTY record set is a valid snapshot: for 465941 "absent from the service"
    IS the baseline, and a record appearing is the change."""
    views = sorted(
        (mc.record_view(r) for r in records
         if str(r.get("wdsid", "")).strip() == str(int(wdsid))),
        key=lambda d: tuple(d[f] for f in mc.RECORD_FIELDS),
    )
    return {"wdsid": str(int(wdsid)), "records": views}


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as rop_watcher/civicclerk_watcher.snapshot_hash."""
    import hashlib
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def summarize_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two wdsid snapshots. Diffs
    the FULL record as a multiset (collections.Counter) keyed on every canonical
    field — a record whose fields change shows as its old shape REMOVED + its
    new shape ADDED, never lost to a key collision (rop_watcher.summarize_
    facility_change's rationale). Every canonical field is printed in the
    ADDED/REMOVED lines, so a change confined to any one field is always
    visible. The absent->present / present->absent transitions get their own
    notes — for 465941, appearance IS the story. Pure — unit-tested."""
    def _tup(r):
        return tuple(r.get(f, "") for f in mc.RECORD_FIELDS)

    old_rows = old.get("records", [])
    new_rows = new.get("records", [])
    old_counts = Counter(_tup(r) for r in old_rows)
    new_counts = Counter(_tup(r) for r in new_rows)
    added = new_counts - old_counts
    removed = old_counts - new_counts

    def _detail(r: dict) -> str:
        return ", ".join(f"{f}={r[f] or '—'}" for f in mc.RECORD_FIELDS
                         if f not in ("wdsid", "module", "actcode", "facilitytype"))

    lines: list[str] = []
    for t, n in sorted(added.items()):
        r = dict(zip(mc.RECORD_FIELDS, t))
        lines.extend([f"+ ADDED    {r['module'] or '—'}/{r['actcode'] or '—'} — "
                      f"{r['facilitytype'] or '—'} ({_detail(r)})"] * n)
    for t, n in sorted(removed.items()):
        r = dict(zip(mc.RECORD_FIELDS, t))
        lines.extend([f"- REMOVED  {r['module'] or '—'}/{r['actcode'] or '—'} — "
                      f"{r['facilitytype'] or '—'} ({_detail(r)})"] * n)

    if old_rows and not new_rows:
        note = ("facility NO LONGER LISTED in EGLE's MMD Open Data "
                "(every record removed)")
    elif new_rows and not old_rows:
        note = ("facility NOW APPEARS in EGLE's MMD Open Data (was absent — "
                "the state has started tracking it)")
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


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested."""
    shown = body or "(no further detail — see the MMD Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills record changed in EGLE MMD Open Data.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE Materials Management Division's "
        "public ArcGIS facility registry — the state's own view of the "
        "landfill's disposal-area / Part 115 / compost-registration statuses. "
        "A change here (a status flip, a registration lapsing, or the compost "
        "area appearing as a tracked facility) is early signal worth reviewing "
        "at the source.\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _diff_and_record(sheets, sheet_id, today, key, label, snap, cfg,
                     recipients) -> str:
    """Baseline/compare/record/alert for one item. Returns "baseline" /
    "changed" / "unchanged". Durable row FIRST, alert email SECOND
    (best-effort) — a crash between them loses the alert, never the record,
    and never re-fires next run since the row already advanced the stored
    hash. Each best-effort step gets its own try so a bug in one can never
    abort run()'s processing of the other watched wdsids (rop_watcher's
    per-item guarantee)."""
    new_hash = snapshot_hash(snap)
    snap_json = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    last = sw.last_mmd_snapshot(sheets, sheet_id, key)

    if last is None:
        sw.append_mmd_watch_row(sheets, sheet_id, today, key, label, "baseline",
                                new_hash, "initial snapshot (no alert)", _now(), snap_json)
        print(f"[mmd-watch] {label}: baseline recorded ({new_hash}).")
        return "baseline"

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[mmd-watch] {label}: unchanged ({new_hash}).")
        return "unchanged"

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_change(old_snap, snap)
    sw.append_mmd_watch_row(sheets, sheet_id, today, key, label, "changed",
                            new_hash, note, _now(), snap_json)
    print(f"[mmd-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — formatting is best-effort; row is recorded
        print(f"[mmd-watch] {label}: change recorded but alert body FORMATTING failed: {e}")
        return "changed"
    try:
        ea.send_email(f"[MMD watch] {label} changed", email_body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
        print(f"[mmd-watch] {label}: change recorded but alert email FAILED: {e}")
    return "changed"


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[mmd-watch] {reason}")
        return 0

    mcfg = cfg.get("mmd") or {}
    wdsids = [int(w) for w in (mcfg.get("wdsids") or mc.DEFAULT_WDSIDS)]
    recipients = mcfg.get("recipients") or None  # None -> full alert_recipients list

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_mmd_tabs(sheets, sheet_id)

    today = _today()
    keys = [f"mmd:{w}" for w in wdsids]

    try:
        records = mc.fetch_records(wdsids)
        print(f"[mmd-watch] fetched {len(records)} record(s) for {len(wdsids)} wdsid(s).")
    except mc.MmdParseError as e:
        # A structural break (service reorganized), not a network blip — this
        # persists across runs, so it is ALWAYS loud (never gated on baseline
        # status; see the module docstring).
        print(f"[mmd-watch] STRUCTURAL failure (failing loudly — this is not "
              f"a transient blip): {e}")
        return 1
    except mc.MmdFetchError as e:
        snaps = sw.last_mmd_snapshots(sheets, sheet_id, keys)
        if all(v is not None for v in snaps.values()):
            print(f"[mmd-watch] fetch failed, skipping this run "
                  f"(baselines preserved, not diffed): {e}")
            return 0
        print(f"[mmd-watch] NO BASELINE for at least one wdsid and fetch "
              f"failed (failing loudly so activation surfaces it): {e}")
        return 1

    counts = {"baseline": 0, "changed": 0, "unchanged": 0}
    for wdsid in wdsids:
        snap = wdsid_snapshot(records, wdsid)
        result = _diff_and_record(sheets, sheet_id, today, f"mmd:{wdsid}",
                                  item_label(wdsid), snap, cfg, recipients)
        counts[result] += 1

    print(f"[mmd-watch] done — {counts['changed']} changed, {counts['baseline']} "
          f"baselined, {counts['unchanged']} unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
