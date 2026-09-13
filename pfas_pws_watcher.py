"""
pfas_pws_watcher.py — daily watch on EGLE's Public Water Supply PFAS sampling
results for supplies in the Arbor Hills capture zone, alerting on a NEW sampling
round or any DETECTION. Standalone + self-terminating, the same shape as
mmd_watcher.py / rop_watcher.py. See docs/decisions/042-pfas-pws-watch.md.

WHY: Salem Elementary (WSSN 2001381) sits in the landfill's capture zone, is
sampled ~annually, and has come back all-non-detect (<2 ppt) across six rounds
Dec-2020->Feb-2025. This is the DRINKING-WATER half of the PFAS picture the
landfill's own leachate/groundwater record can't see. The signal is (a) a new
sampling round appearing and (b) any first DETECTION of a regulated PFAS.

ONE watched item per configured WSSN, all derived from ONE fetch:
  - pws:2001381   Salem Elementary's sampling record (6 clean rounds today)

WHAT IT DOES per item (mirrors mmd_watcher):
  - build a canonical snapshot (rounds keyed by SysSampleCode) + hash it,
  - compare to the last snapshot in the "Public Water Supply PFAS Watch" tab
    (that tab IS the state -- append-only, so no _meta clobber race),
  - FIRST sighting -> silent "baseline" row (any historical detection is noted
    in the row + logged, NOT alerted -- the watch is forward-looking),
  - hash changed -> for each new/changed round, route any DETECTION to the shared
    Measurements tab (basis=measured, system of record) FIRST, THEN record a
    "changed"/"detection" row (advances the stored hash), THEN email an alert
    (best-effort). A NEW ROUND that is all-non-detect still alerts (the negative
    result is itself the news: "sampled again, still clean"); a DETECTION (or an
    UNRECOGNIZED value -- fail-safe) elevates the subject.
  - hash unchanged -> no-op.

Ordering note (Measurements -> watch row -> email): a crash after Measurements
but before the watch row re-detects next run (a duplicate Measurements row is
low-stakes and rare), never a LOST detection/alert -- the ridgewood_archiver
ordering, safe in the alert-preserving direction.

FETCH FAILURE (PwsFetchError) is TRANSIENT: skip-and-warn if every watched WSSN
already has a baseline; LOUD exit 1 if ANY has none yet (activation must
surface). A structurally-wrong response (PwsParseError -- features missing,
truncated, schema drift) is ALWAYS loud regardless of baseline status: a service
reorganization persists across runs and could hide a real detection forever.

GATED on pfas_pws.enabled (false by default -- new external source; a human
flips it on + picks recipients, this loop never does). Runs daily (see
.github/workflows/pfas-pws-watch.yml). NO Drive/OAuth -- the deliverable is the
ALERT + the durable Sheet row (which carries the full snapshot JSON) + the
Measurements rows for any detection. NEVER routes through egle_doc_parser.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import pfas_pws_client as pc
import email_alerts as ea
from config_loader import load_config


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
    this guards (the watch doing real work / emailing before pfas_pws.enabled is
    set) has a direct unit test. Mirrors mmd_watcher/pfas_watcher._should_run."""
    if not (cfg.get("pfas_pws") or {}).get("enabled"):
        return False, "pfas_pws.enabled is false — skipping (no-op)."
    return True, ""


def item_label(wssn, system: str = "") -> str:
    sysname = f" ({system})" if system else ""
    return f"Public Water Supply PFAS — WSSN {wssn}{sysname}"


# ---------------------------------------------------------------------------
# Snapshot + diff (pure)
# ---------------------------------------------------------------------------


def wssn_snapshot(records: list[dict], wssn) -> dict:
    """Canonical, hash-stable snapshot of one WSSN's sampling rounds: filter the
    shared fetch by WSSN, canonicalize each row (pfas_pws_client.record_view),
    and key by round_key (SysSampleCode). An EMPTY record set is a valid snapshot
    (a watched WSSN with no results yet — its first round appearing is the
    change), mirroring mmd_watcher.wdsid_snapshot."""
    rounds = {}
    for r in records:
        if str(r.get("WSSN", "")).strip() != str(int(wssn)):
            continue
        view = pc.record_view(r)
        rounds[pc.round_key(view)] = view
    return {"wssn": str(int(wssn)), "rounds": rounds}


def snapshot_hash(snap: dict) -> str:
    """Stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as mmd_watcher/rop_watcher.snapshot_hash."""
    import hashlib
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def diff_rounds(old_snap: dict, new_snap: dict) -> dict:
    """{new: [view], changed: [(old,new)], removed: [view]} between two WSSN
    snapshots, keyed by round_key. A NEW round is the primary signal; a CHANGED
    round (EGLE corrected a value) is rarer but still surfaced; a REMOVED round
    is unusual (EGLE rarely deletes) and noted. Pure — unit-tested."""
    old = old_snap.get("rounds", {}) or {}
    new = new_snap.get("rounds", {}) or {}
    new_keys = [k for k in new if k not in old]
    removed_keys = [k for k in old if k not in new]
    changed = [(old[k], new[k]) for k in new if k in old and old[k] != new[k]]
    return {
        "new": [new[k] for k in sorted(new_keys)],
        "changed": sorted(changed, key=lambda t: pc.round_key(t[1])),
        "removed": [old[k] for k in sorted(removed_keys)],
    }


def all_detections(views: list[dict]) -> list[dict]:
    """Flatten round_detections across a list of round views, each tagged with
    its round context (date/loc/system/wssn) — the set routed to Measurements
    and named in a detection alert."""
    out = []
    for v in views:
        for d in pc.round_detections(v):
            out.append({**d, "sample_date": v.get("sample_date", ""),
                        "loc": v.get("loc", ""), "system": v.get("system", ""),
                        "wssn": v.get("wssn", ""), "round_key": pc.round_key(v)})
    return out


def summarize_change(diff: dict) -> tuple[str, str, bool]:
    """(note, body, is_detection): describe what changed. `is_detection` is True
    when any new/changed round carries a DETECTION or an UNRECOGNIZED value
    (fail-safe) — the watcher uses it to elevate the alert subject. Pure."""
    changed_new_views = diff["new"] + [n for (_o, n) in diff["changed"]]
    dets = all_detections(changed_new_views)
    lines: list[str] = []

    for v in diff["new"]:
        d = pc.round_detections(v)
        if d:
            det_str = "; ".join(
                f"{x['analyte']} {x['raw']} ppt"
                + (" (UNRECOGNIZED value — review)" if x["state"] == "unrecognized" else "")
                for x in d)
            lines.append(f"+ NEW ROUND {v['sample_date']} @ {v['loc'] or '—'}: "
                         f"DETECTION — {det_str}")
        else:
            lines.append(f"+ NEW ROUND {v['sample_date']} @ {v['loc'] or '—'}: "
                         "all seven regulated PFAS non-detect")
    for _old, v in diff["changed"]:
        lines.append(f"~ ROUND UPDATED {v['sample_date']} @ {v['loc'] or '—'} "
                     "(a prior result was revised — see snapshot)")
    for v in diff["removed"]:
        lines.append(f"- ROUND REMOVED {v['sample_date']} @ {v['loc'] or '—'}")

    n_new = len(diff["new"])
    if dets:
        note = f"PFAS DETECTED — {len(dets)} result(s) across {n_new} new round(s)"
    elif n_new:
        note = f"{n_new} new sampling round(s), all non-detect"
    elif diff["changed"] or diff["removed"]:
        note = "sampling record revised (see detail)"
    else:
        note = "changed (no round-level diff — see snapshot)"
    return note, "\n".join(lines), bool(dets)


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested. Source-labeled per the
    data-layer accuracy rule; no MCL comparison (a single MCL regime would be a
    dual-regime accuracy trap — see ADR 042); the raw value + analyte + date is
    actionable on its own."""
    shown = body or "(no further detail — see the Public Water Supply PFAS Watch tab.)"
    return (
        "A watched Public Water Supply's PFAS sampling record changed in EGLE's "
        "MPART data.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's Public Water Supply PFAS sampling "
        "results (the seven Michigan-regulated PFAS; values in ppt/ng/L; the "
        "non-detect reporting limit is <2 ppt). It is the DRINKING-WATER record "
        "for a supply in the landfill's capture zone — a NEW round or any "
        "detection is worth reviewing at the source. This is a screening signal, "
        "not a regulatory determination; compare any detection to the applicable "
        "state and federal PFAS drinking-water standards.\n"
    )


# ---------------------------------------------------------------------------
# Measurements routing (detections only)
# ---------------------------------------------------------------------------

_MEASURE_UNIT = "ppt"


def measurement_dicts(detections: list[dict]) -> list[dict]:
    """One Measurements-tab reading dict per DETECTION (never non-detects — the
    source feed is the record for those). Value stored verbatim as the feed
    presents it; basis=measured. An UNRECOGNIZED value is still routed (its raw
    token in Value, flagged in Note) so a possible detection is never dropped."""
    out = []
    for d in detections:
        flag = " (UNRECOGNIZED value — needs review)" if d["state"] == "unrecognized" else ""
        out.append({
            "as_of_date": d["sample_date"],
            "well_id": f"WSSN {d['wssn']} {d['loc']}".strip(),
            "metric": f"pfas_{d['analyte'].lower()}",
            "value": d["raw"],
            "unit": _MEASURE_UNIT,
            "basis": "measured",
            "note": (f"EGLE Public Water Supply PFAS sampling ({d['system']}); "
                     f"regulated 7-PFAS panel; DETECTION{flag}. Screening signal, "
                     "not a regulatory determination."),
        })
    return out


def _measure_metadata(system: str, wssn: str, date: str) -> dict:
    return {
        "date_filed": date,
        "document_name": f"EGLE PWS PFAS sampling — {system} {date}".strip(),
        "facility_name": system or f"Public Water Supply (WSSN {wssn})",
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _diff_and_record(sheets, sheet_id, today, key, wssn, snap, cfg, recipients,
                     query_url) -> str:
    """Baseline/compare/record/alert for one WSSN. Returns "baseline" /
    "changed" / "detection" / "unchanged". Measurements (system of record) are
    written BEFORE the watch row (which advances the stored hash), and the email
    is best-effort LAST — a crash re-detects rather than losing a detection.
    Each best-effort step gets its own try so one bug can't abort the other
    watched WSSNs (mmd_watcher's per-item guarantee)."""
    new_hash = snapshot_hash(snap)
    snap_json = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    system = next((v.get("system", "") for v in snap.get("rounds", {}).values()
                   if v.get("system")), "")
    label = item_label(wssn, system)
    last = sw.last_pfas_pws_snapshot(sheets, sheet_id, key)

    if last is None:
        # First sighting: silent baseline. Note (do NOT alert) any historical
        # detection already on record — the watch is forward-looking.
        hist = all_detections(list(snap.get("rounds", {}).values()))
        note = "initial snapshot (no alert)"
        if hist:
            det_str = "; ".join(f"{d['analyte']} {d['raw']} ppt @ {d['sample_date']}"
                                for d in hist)
            note = f"initial snapshot (no alert) — HISTORICAL detection(s) on record: {det_str}"
            print(f"[pws-pfas] {label}: baseline has historical detection(s): {det_str}")
        sw.append_pfas_pws_watch_row(sheets, sheet_id, today, key, label,
                                     "baseline", new_hash, note, _now(), snap_json)
        print(f"[pws-pfas] {label}: baseline recorded ({new_hash}).")
        return "baseline"

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[pws-pfas] {label}: unchanged ({new_hash}).")
        return "unchanged"

    old_snap = _load_json(last_snap_json, {"rounds": {}})
    diff = diff_rounds(old_snap, snap)
    note, body, is_detection = summarize_change(diff)

    # (1) Measurements (system of record) FIRST — only detections, basis=measured.
    dets = all_detections(diff["new"] + [n for (_o, n) in diff["changed"]])
    if dets:
        try:
            meas = measurement_dicts(dets)
            parsed = SimpleNamespace(measurements=meas)
            md = _measure_metadata(dets[0]["system"], dets[0]["wssn"],
                                   dets[0]["sample_date"])
            rows = sw.measurement_rows(parsed, md, query_url)
            sw.append_rows(sheets, sheet_id, sw.TAB_MEASUREMENTS, rows)
            print(f"[pws-pfas] {label}: wrote {len(rows)} detection(s) to Measurements.")
        except Exception as e:  # noqa: BLE001 — best-effort; the watch row + alert still record it
            print(f"[pws-pfas] {label}: Measurements write FAILED (alert still fires): {e}")

    # (2) Watch row SECOND — advances the stored hash (crash-safe: a crash before
    # this re-detects next run, never loses the detection).
    change = "detection" if is_detection else "changed"
    sw.append_pfas_pws_watch_row(sheets, sheet_id, today, key, label, change,
                                 new_hash, note, _now(), snap_json)
    print(f"[pws-pfas] {label}: {change.upper()} ({last_hash} -> {new_hash}; {note}).")

    # (3) Email LAST — best-effort.
    subj = (f"[PWS PFAS] DETECTION — {label}" if is_detection
            else f"[PWS PFAS] {label} — {note}")
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — formatting best-effort; row recorded
        print(f"[pws-pfas] {label}: change recorded but alert body FORMATTING failed: {e}")
        return change
    try:
        ea.send_email(subj, email_body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert best-effort; row recorded
        print(f"[pws-pfas] {label}: change recorded but alert email FAILED: {e}")
    return change


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[pws-pfas] {reason}")
        return 0

    pcfg = cfg.get("pfas_pws") or {}
    wssns = [int(w) for w in (pcfg.get("wssns") or pc.DEFAULT_WSSNS)]
    recipients = pcfg.get("recipients") or None  # None -> full alert_recipients list
    query_url = pcfg.get("service_url") or pc.DEFAULT_QUERY_URL
    if not wssns:
        print("[pws-pfas] pfas_pws.enabled is true but no wssns configured — nothing to watch.")
        return 0

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_pfas_pws_tabs(sheets, sheet_id)
    sw.ensure_tabs(sheets, sheet_id)  # the shared Measurements tab must exist

    today = _today()
    keys = [f"pws:{w}" for w in wssns]

    try:
        records = pc.fetch_results(wssns, url=query_url)
        print(f"[pws-pfas] fetched {len(records)} row(s) for {len(wssns)} WSSN(s).")
    except pc.PwsParseError as e:
        # Structural break (service reorganized) — persists across runs, ALWAYS loud.
        print(f"[pws-pfas] STRUCTURAL failure (failing loudly — not a transient "
              f"blip): {e}")
        return 1
    except pc.PwsFetchError as e:
        snaps = sw.last_pfas_pws_snapshots(sheets, sheet_id, keys)
        if all(v is not None for v in snaps.values()):
            print(f"[pws-pfas] fetch failed, skipping this run "
                  f"(baselines preserved, not diffed): {e}")
            return 0
        print(f"[pws-pfas] NO BASELINE for at least one WSSN and fetch failed "
              f"(failing loudly so activation surfaces it): {e}")
        return 1

    counts = {"baseline": 0, "changed": 0, "detection": 0, "unchanged": 0}
    for wssn in wssns:
        snap = wssn_snapshot(records, wssn)
        result = _diff_and_record(sheets, sheet_id, today, f"pws:{wssn}", wssn,
                                  snap, cfg, recipients, query_url)
        counts[result] += 1

    print(f"[pws-pfas] done — {counts['detection']} detection, {counts['changed']} "
          f"changed, {counts['baseline']} baselined, {counts['unchanged']} unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
