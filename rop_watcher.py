"""
rop_watcher.py — daily watch on EGLE's ROP (air Title V permit) sources for
Arbor Hills, alerting on any change. Standalone + self-terminating, the same
shape as pfas_watcher.py / civicclerk_watcher.py. See docs/decisions/017-rop-watch.md.

WHY: all three Arbor Hills air facilities have a ROP renewal IN PROCESS —
N2688 (landfill), N1504 (Energy), P1488 (Emerald RNG). A renewal reaching its
30-day PUBLIC COMMENT window is a second advocacy venue, easy to miss amid a
1,800-row statewide CSV. This watch is the trip-wire.

FIVE watched items, derived from THREE fetches (one per rop_client source):
  - csv:<SRN>     one item per target facility's extracted ROP task rows
                  (task-status / permit-status / dates advancing)
  - folder:N2688  the N2688 renewal folder's file list (a new draft ROP/staff
                  report appearing)
  - notice:N2688  whether N2688 appears in the statewide public-notice PDF
                  (the 30-day-comment-window trip-wire)

WHAT IT DOES per item (mirrors pfas_watcher/civicclerk_watcher exactly):
  - build a canonical snapshot + hash it,
  - compare to the last snapshot recorded in the "ROP Watch" tab (that tab IS
    the state — append-only, so no _meta clobber race),
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert describing what
    changed (row first = durable record; email best-effort),
  - hash unchanged -> no-op.

FETCH FAILURE (RopFetchError) is TRANSIENT per source: skip-and-warn if every
item derived from that source already has a baseline; LOUD exit 1 if ANY of
them has none yet (an activation-time block must surface, not silently no-op
forever — same posture as pfas_watcher/civicclerk_watcher). A successful CSV
fetch that structurally can't be parsed (RopParseError) is NOT given the same
treatment — it's ALWAYS loud, regardless of baseline status, since a column-
layout break is far more likely to persist than a network blip, and letting it
go quiet behind a baseline would hide a real EGLE format change forever (the
same silent-stall class ADR 014's liveness guard exists to catch).

GATED on rop.enabled (false by default). A brand-new poller against a live
external system ships disabled and a human flips it on (overnight-coder
procedure). Until enabled: true is on main, every run is a quiet no-op.

NO DRIVE / OAUTH (same scope call as pfas_watcher, ADR 012): the deliverable is
the ALERT + the durable Sheet row (which carries the full snapshot JSON), not a
document mirror. SMTP + Sheets (both already live) are all this needs.

Runs daily (see .github/workflows/rop-watch.yml).
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
import rop_client as rc
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
    this guards (the watch doing real work / emailing before rop.enabled is set)
    has a direct unit test. Mirrors pfas_watcher/civicclerk_watcher._should_run."""
    if not (cfg.get("rop") or {}).get("enabled"):
        return False, "rop.enabled is false — skipping (no-op)."
    return True, ""


# ---------------------------------------------------------------------------
# Snapshots (pure) — one per item kind
# ---------------------------------------------------------------------------


_FACILITY_ROW_FIELDS = (
    "permit_number", "version", "task_name", "rop_action", "rop_action_status",
    "task_status", "task_due", "task_completed", "permit_status",
    "issue_date", "effective_date", "expiration_date",
)

# Every field a row's ADDED/REMOVED alert line must show besides
# permit_number/version/task_name (already in the surrounding line) —
# DERIVED from _FACILITY_ROW_FIELDS, not a separately hand-maintained list, so
# a future field added to the row's hash/diff identity can't silently go
# unprinted the way task_status alone once did (a change confined to any
# unprinted field renders two identical-looking ADDED/REMOVED lines).
_DETAIL_FIELDS = tuple(f for f in _FACILITY_ROW_FIELDS
                       if f not in ("permit_number", "version", "task_name"))


def facility_snapshot(rows: list[dict], srn: str) -> dict:
    """Canonical, hash-stable snapshot of one facility's ROP task rows. Sorted by
    the FULL field tuple (not just permit_number/version/task_name) because the
    real export can carry two rows sharing that identity with different values
    otherwise (e.g. a permit version re-recorded as both "Extended" and
    "Superseded" at different dates) — sorting on a partial key would leave that
    tie's order dependent on input order (a stable sort only orders by the key
    given), making the hash silently unstable across an otherwise-identical
    re-fetch. Deliberately excludes `name`/`srn` from the per-row payload
    (constant within one facility's snapshot)."""
    keyed = sorted(
        ({f: r.get(f, "") for f in _FACILITY_ROW_FIELDS}
         for r in rows if r.get("srn") == srn),
        key=lambda d: tuple(d[f] for f in _FACILITY_ROW_FIELDS),
    )
    return {"srn": srn, "rows": keyed}


def folder_snapshot(entries: list[dict]) -> dict:
    """Canonical snapshot of the N2688 folder listing. Keyed on name+is_dir only
    (not date/size) — the signal this watch cares about is a NEW entry appearing,
    per the handoff; an existing file's metadata churning is not itself alert-
    worthy and this repo already learned that lesson once (PFAS's Sitecore
    cache-busters, ADR 012)."""
    return {"entries": sorted(
        ({"name": e["name"], "is_dir": e["is_dir"]} for e in entries),
        key=lambda e: e["name"])}


def notice_snapshot(mentioned: bool, context: str) -> dict:
    """Canonical snapshot of the statewide notice's N2688 mention. `context` is
    only kept when mentioned (a False->False no-op must hash identically run to
    run even if unrelated notice text elsewhere shifts)."""
    return {"mentioned": bool(mentioned), "context": context if mentioned else ""}


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as civicclerk_watcher.snapshot_hash."""
    import hashlib
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Diff summaries (pure) — one per item kind
# ---------------------------------------------------------------------------


def summarize_facility_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two facility snapshots.
    Diffs the FULL row as a multiset (collections.Counter), not a dict keyed by
    the partial (permit_number, version, task_name) identity — the real export
    can carry two rows sharing that partial identity with different other
    fields (see facility_snapshot's docstring: a permit version re-recorded as
    both "Extended" and "Superseded"), and a dict keyed on the partial identity
    would silently DROP one of them from the diff. A row whose fields change
    shows as its old shape REMOVED + its new shape ADDED (no separate "updated"
    case) — less granular than a field-by-field diff, but never loses a row to
    a key collision. Pure — unit-tested."""
    def _tup(r):
        return tuple(r.get(f, "") for f in _FACILITY_ROW_FIELDS)

    old_counts = Counter(_tup(r) for r in old.get("rows", []))
    new_counts = Counter(_tup(r) for r in new.get("rows", []))
    added = new_counts - old_counts
    removed = old_counts - new_counts

    def _detail(r: dict) -> str:
        return ", ".join(f"{f}={r[f] or '—'}" for f in _DETAIL_FIELDS)

    parts: list[str] = []
    lines: list[str] = []
    for t, n in sorted(added.items()):
        r = dict(zip(_FACILITY_ROW_FIELDS, t))
        parts.append("new task/version row")
        lines.extend([f"+ ADDED    permit {r['permit_number']} v{r['version']} — "
                      f"{r['task_name']} ({_detail(r)})"] * n)
    for t, n in sorted(removed.items()):
        r = dict(zip(_FACILITY_ROW_FIELDS, t))
        parts.append("task/version row removed")
        lines.extend([f"- REMOVED  permit {r['permit_number']} v{r['version']} — "
                      f"{r['task_name']} ({_detail(r)})"] * n)

    if not parts:
        return "changed (no row-level diff — see snapshot)", ""
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return "; ".join(seen), "\n".join(lines)


def summarize_folder_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing added/removed N2688 folder entries. Pure."""
    old_names = {e["name"] for e in old.get("entries", [])}
    new_names = {e["name"] for e in new.get("entries", [])}
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    parts: list[str] = []
    lines: list[str] = []
    if added:
        parts.append(f"{len(added)} new file(s)")
        lines.extend(f"+ ADDED    {n}" for n in added)
    if removed:
        parts.append(f"{len(removed)} file(s) removed")
        lines.extend(f"- REMOVED  {n}" for n in removed)
    if not parts:
        return "changed (no name-level diff — see snapshot)", ""
    return "; ".join(parts), "\n".join(lines)


def summarize_notice_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) for the N2688-mention trip-wire. Pure."""
    was, now = bool(old.get("mentioned")), bool(new.get("mentioned"))
    if now and not was:
        return ("N2688 now appears in the statewide ROP public notice — likely "
                "the 30-day public comment window has opened", new.get("context", ""))
    if was and not now:
        return ("N2688 no longer appears in the statewide ROP public notice "
                "(comment window likely closed)", "")
    return "changed (mention status unchanged, context text shifted)", new.get("context", "")


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested."""
    shown = body or "(no further detail — see the ROP Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills ROP (air Title V permit) source changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's ROP monthly report, the N2688 "
        "renewal folder, and the statewide ROP public-notice PDF — trip-wiring a "
        "renewal advancing or reaching its 30-day public comment window. Review "
        "the source directly for full context.\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _diff_and_record(sheets, sheet_id, today, key, label, snap, summarize_fn,
                      cfg, recipients) -> str:
    """Baseline/compare/record/alert for one item. Returns "baseline" / "changed"
    / "unchanged". Durable row FIRST, alert email SECOND (best-effort) — a crash
    between them loses the alert, never the record, and never re-fires next run
    since the row already advanced the stored hash."""
    new_hash = snapshot_hash(snap)
    snap_json = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    last = sw.last_rop_snapshot(sheets, sheet_id, key)

    if last is None:
        sw.append_rop_watch_row(sheets, sheet_id, today, key, label, "baseline",
                                 new_hash, "initial snapshot (no alert)", _now(), snap_json)
        print(f"[rop-watch] {label}: baseline recorded ({new_hash}).")
        return "baseline"

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[rop-watch] {label}: unchanged ({new_hash}).")
        return "unchanged"

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_fn(old_snap, snap)
    sw.append_rop_watch_row(sheets, sheet_id, today, key, label, "changed",
                             new_hash, note, _now(), snap_json)
    print(f"[rop-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is
    # best-effort alerting for THIS item only. Each step gets its own try so a
    # bug in either one (a) is never misattributed to the other's failure mode,
    # and (b) can never escape _diff_and_record and abort run()'s processing of
    # every other independent item (other CSV facilities, the folder check, the
    # notice check) — the "partial activation block, not all-or-nothing"
    # guarantee (ADR 017 section 4) applies per ITEM, not just per SOURCE.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — formatting is best-effort; row is recorded
        print(f"[rop-watch] {label}: change recorded but alert body FORMATTING failed: {e}")
        return "changed"
    try:
        ea.send_email(f"[ROP watch] {label} changed", email_body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
        print(f"[rop-watch] {label}: change recorded but alert email FAILED: {e}")
    return "changed"


def _all_baselined(sheets, sheet_id, keys) -> bool:
    """Whether EVERY key already has a baseline — the gate for treating a fetch
    failure as a transient skip-and-warn rather than a loud activation-time
    block. Matters when a source derives more than one item (the CSV's per-
    facility keys): if `rop.srns` is edited later to add a facility, that new
    item has no baseline yet even though its siblings do, and a fetch failure
    right then must still surface loudly — `any(...)` would wrongly treat the
    siblings' baselines as enough to go quiet, silently skipping the new
    facility's activation-time block forever. One batched tab read for all
    `keys` (sw.last_rop_snapshots), not one full-tab read per key."""
    snaps = sw.last_rop_snapshots(sheets, sheet_id, keys)
    return all(v is not None for v in snaps.values())


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[rop-watch] {reason}")
        return 0

    rcfg = cfg.get("rop") or {}
    srns = tuple(rcfg.get("srns") or rc.TARGET_SRNS)
    recipients = rcfg.get("recipients") or None  # None -> full alert_recipients list

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_rop_tabs(sheets, sheet_id)

    today = _today()
    counts = {"baseline": 0, "changed": 0, "unchanged": 0}
    exit_code = 0

    # --- 1. CSV-derived items (one fetch, len(srns) facility items) --------
    csv_keys = [f"csv:{srn}" for srn in srns]
    rows = None
    try:
        csv_text, last_modified = rc.fetch_csv()
        rows = rc.parse_csv_rows(csv_text, srns)
        print(f"[rop-watch] CSV: fetched {len(rows)} target row(s) "
              f"(Last-Modified: {last_modified or 'unknown'}).")
    except rc.RopParseError as e:
        # A structural break (wrong column layout), not a network blip — this
        # is likely to PERSIST across runs, unlike a fetch error. Once every
        # item already has a baseline, a plain skip-and-warn would let a real
        # EGLE format change go unnoticed forever behind a quiet log line (the
        # same silent-stall failure class ADR 014's liveness guard exists to
        # catch for Stream E). Always loud, regardless of baseline status.
        print(f"[rop-watch] CSV: STRUCTURAL parse failure (failing loudly — this "
              f"is not a transient blip): {e}")
        exit_code = 1
    except rc.RopFetchError as e:
        if _all_baselined(sheets, sheet_id, csv_keys):
            print(f"[rop-watch] CSV: fetch failed, skipping this run "
                  f"(baseline preserved, not diffed): {e}")
        else:
            print(f"[rop-watch] CSV: NO BASELINE and fetch failed "
                  f"(failing loudly so activation surfaces it): {e}")
            exit_code = 1

    if rows is not None:
        for srn in srns:
            label = f"ROP monthly report — {srn}"
            snap = facility_snapshot(rows, srn)
            result = _diff_and_record(sheets, sheet_id, today, f"csv:{srn}", label,
                                       snap, summarize_facility_change, cfg, recipients)
            counts[result] += 1

    # --- 2. N2688 folder listing --------------------------------------------
    entries = None
    try:
        html_text = rc.fetch_folder_listing()
        entries = rc.parse_folder_listing(html_text)
        print(f"[rop-watch] N2688 folder: {len(entries)} entries listed.")
    except rc.RopFetchError as e:
        if _all_baselined(sheets, sheet_id, ["folder:N2688"]):
            print(f"[rop-watch] N2688 folder: fetch failed, skipping this run "
                  f"(baseline preserved, not diffed): {e}")
        else:
            print(f"[rop-watch] N2688 folder: NO BASELINE and fetch failed "
                  f"(failing loudly so activation surfaces it): {e}")
            exit_code = 1

    if entries is not None:
        snap = folder_snapshot(entries)
        result = _diff_and_record(sheets, sheet_id, today, "folder:N2688",
                                   "N2688 ROP renewal folder — file list", snap,
                                   summarize_folder_change, cfg, recipients)
        counts[result] += 1

    # --- 3. Statewide public-notice PDF -------------------------------------
    mentioned = None
    context = ""
    try:
        pdf_bytes = rc.fetch_notice_pdf()
        mentioned, context = rc.notice_mentions_srn(pdf_bytes, "N2688")
        print(f"[rop-watch] Statewide notice: N2688 mentioned = {mentioned}.")
    except rc.RopFetchError as e:
        if _all_baselined(sheets, sheet_id, ["notice:N2688"]):
            print(f"[rop-watch] Statewide notice: fetch failed, skipping this run "
                  f"(baseline preserved, not diffed): {e}")
        else:
            print(f"[rop-watch] Statewide notice: NO BASELINE and fetch failed "
                  f"(failing loudly so activation surfaces it): {e}")
            exit_code = 1

    if mentioned is not None:
        snap = notice_snapshot(mentioned, context)
        result = _diff_and_record(sheets, sheet_id, today, "notice:N2688",
                                   "Statewide ROP public notice — N2688 mention", snap,
                                   summarize_notice_change, cfg, recipients)
        counts[result] += 1

    print(f"[rop-watch] done — {counts['changed']} changed, {counts['baseline']} "
          f"baselined, {counts['unchanged']} unchanged.")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
