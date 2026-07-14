"""
civicclerk_watcher.py — watch a HAND-PICKED list of Washtenaw County CivicClerk
meeting events for ANY change, and alert the moment one changes (ADR 015).

WHY (vs. Mirror D / mmpc_archiver): Mirror D mirrors every category-72 (MMPC) PDF
to Drive by fileId — an archive, silent, and blind to a meeting being moved,
renamed, cancelled, or having a document replaced/removed. This watch is the
complement: it snapshots the whole MEETING RECORD (title, date/time, publish
status, notice, and the full document set) for specific events across MMPC AND the
Board of Commissioners (categories 26/27, which Mirror D does not touch at all),
and emails a change alert. It downloads nothing — the deliverable is the ALERT,
like pfas_watcher.

WHAT IT DOES each run (per watched event that is DUE today — see is_due_today):
  - fetch the single event via mmpc_client.fetch_event (structured OData, same
    host as Mirror D — one event by id, not a whole category),
  - build a canonical snapshot (event_snapshot) and hash it (snapshot_hash),
  - compare to the last snapshot recorded in the "Meeting Watch" tab (that tab IS
    the state — append-only, so no _meta clobber race),
  - FIRST sighting → record a silent "baseline" row (no alert; nothing to report
    yet, and firing on activation day would just be noise),
  - hash changed → record a "changed" row THEN email an alert describing what
    changed (row first = durable record; email best-effort),
  - hash unchanged → no-op.

VANISH vs. ERROR (ADR 015, mirrors the fail-safe elsewhere in this repo):
  - a network/HTTP/JSON error (MMPCFetchError) is TRANSIENT → skip-and-warn if a
    baseline exists (a blip must never diff into a false alert); LOUD exit 1 if
    NO baseline exists yet (activation-time block surfaces instead of no-oping
    forever, same as pfas_watcher),
  - a SUCCESSFUL fetch that returns no event (200 + empty) for a previously-seen
    meeting is a REAL change (cancelled / removed / renumbered) → alert.

CADENCE is a pure function of config + today, not the cron: one workflow fires
twice daily; each event decides whether it's due (MMPC every run; BOC weekly plus
daily in the 3 days before the meeting). See is_due_today.

GATED on civicclerk_watch.enabled. Recipients are civicclerk_watch.recipients
(Trisha only) — NOT the shared alert_recipients list. Runs on its own schedule
(.github/workflows/meeting-watch.yml).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import mmpc_client as mc
import email_alerts as ea
from config_loader import load_config

_PORTAL = "https://washtenawcomi.portal.civicclerk.com/event/{id}/files"

# The canonical snapshot for an event the portal no longer returns. Distinct from
# any live snapshot (which carries present=True), so a vanish is naturally a hash
# change that summarize_change explains as a cancellation/removal.
GONE_SNAPSHOT = {"present": False}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_date() -> date:
    return (datetime.now(_ET) if _ET else datetime.now()).date()


def _parse_date(s) -> date | None:
    """Parse an ISO 'YYYY-MM-DD' (the config event_date) to a date, or None."""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _load_json(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return fallback


# --- gate (pure) ----------------------------------------------------------------


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact bug
    this guards (the watch doing real work / emailing before the flag is set) has a
    direct unit test. Mirrors pfas_watcher/wds_archiver._should_run."""
    if not (cfg.get("civicclerk_watch") or {}).get("enabled"):
        return False, "civicclerk_watch.enabled is false — skipping (no-op)."
    return True, ""


# --- cadence (pure) -------------------------------------------------------------


def is_due_today(cadence, event_date: date | None, today: date) -> bool:
    """Whether a watched event should be CHECKED on `today`. Pure — unit-tested.

    cadence is either:
      - the string "every_run" (or None) → always due (the MMPC group), or
      - a dict {weekly_weekday: 0-6 (Mon=0), daily_before_days: N} → due on the
        weekly weekday OR on any of the N days up to and INCLUDING the meeting date
        (0 <= (event_date - today).days <= N). This is how the BOC group is checked
        weekly but daily in the 3 days before each meeting.

    Fail-safe: an unrecognized cadence defaults to DUE — a watch must never
    silently stop checking because of a config typo (the repo's fail-safe ethos)."""
    if not cadence or cadence == "every_run":
        return True
    if not isinstance(cadence, dict):
        return True  # unknown scalar cadence — check rather than silently skip
    if event_date is not None:
        before = int(cadence.get("daily_before_days", 0) or 0)
        delta = (event_date - today).days
        if 0 <= delta <= before:
            return True
    wd = cadence.get("weekly_weekday")
    if wd is not None and today.weekday() == int(wd):
        return True
    return False


# --- snapshot + diff (pure) -----------------------------------------------------


def event_snapshot(ev: dict) -> dict:
    """Canonical, hash-stable snapshot of a meeting event. Deliberately includes
    ONLY meaningful, stable fields — the volatile display-order `sort` and the
    per-upload storage `url` (a rotating GUID path) are EXCLUDED so a re-render
    can't false-alarm (the repo already ate this with PFAS's Sitecore cache-
    busters). Files are keyed/sorted by fileId so display-order churn is invisible;
    a file's identity is fileId, and its meaningful attributes are type/name/
    publishOn. event_name is kept because a "CANCELLED — …" rename is exactly what
    we want to catch."""
    files = sorted(
        [{"file_id": f.get("fileId"),
          "type": f.get("type") or "",
          "name": f.get("name") or "",
          "publish_on": f.get("publishOn") or ""}
         for f in (ev.get("publishedFiles") or [])],
        key=lambda d: (d["file_id"] is None, d["file_id"] if d["file_id"] is not None else 0),
    )
    return {
        "present": True,
        "event_name": ev.get("eventName") or "",
        "event_date": ev.get("eventDate") or "",
        "is_published": ev.get("isPublished") or "",
        "event_notice": ev.get("eventNotice") or "",
        "is_deleted": bool(ev.get("isDeleted")),
        "files": files,
    }


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON → sha256)."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def summarize_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body): a short human note for the row + a fuller per-line body for
    the email, describing what changed between two snapshots. Pure — unit-tested."""
    if not new.get("present", True):
        return ("meeting no longer on the portal (cancelled / removed?)",
                "The county's portal no longer returns this meeting event — it may "
                "have been cancelled, deleted, or renumbered. Open the page to check.")

    parts: list[str] = []
    lines: list[str] = []

    if not old.get("present", True):
        parts.append("meeting re-appeared")
        lines.append("The meeting event is present on the portal again.")

    for key, label in (("event_name", "title"), ("event_date", "date/time"),
                       ("is_published", "publish status"),
                       ("event_notice", "notice"), ("is_deleted", "deleted flag")):
        ov, nv = old.get(key), new.get(key)
        if ov != nv:
            parts.append(f"{label} changed")
            lines.append(f"{label}: {ov!r} -> {nv!r}")

    old_files = {f["file_id"]: f for f in old.get("files", [])}
    new_files = {f["file_id"]: f for f in new.get("files", [])}
    for k in new_files:
        if k not in old_files:
            f = new_files[k]
            parts.append("document added")
            lines.append(f"+ ADDED    [{f['type']}]  {f['name']}")
    for k in old_files:
        if k not in new_files:
            f = old_files[k]
            parts.append("document removed")
            lines.append(f"- REMOVED  [{f['type']}]  {f['name']}")
    for k in new_files:
        if k in old_files and new_files[k] != old_files[k]:
            f = new_files[k]
            parts.append("document updated")
            lines.append(f"~ UPDATED  [{f['type']}]  {f['name']}")

    if not parts:
        return ("changed (no field-level diff — see snapshot)", "")
    # de-dup the note while preserving order
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return "; ".join(seen), "\n".join(lines)


def event_url(event_id) -> str:
    return _PORTAL.format(id=event_id)


def format_change_body(label: str, url: str, note: str, body_diff: str) -> str:
    """The change-alert email body. Pure — unit-tested."""
    shown = body_diff or (
        "(no field-level detail — open the page to see what changed.)")
    return (
        "A watched public meeting changed on the county's CivicClerk portal.\n\n"
        f"Meeting: {label}\n"
        f"Page:    {url}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on the meeting record — its title, date/time, "
        "publish status, and its agenda/minutes/other document set. Open the page "
        "above for the full context.\n"
    )


# --- run ------------------------------------------------------------------------


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[meeting-watch] {reason}")
        return 0

    wcfg = cfg.get("civicclerk_watch") or {}
    groups = wcfg.get("groups") or []
    recipients = wcfg.get("recipients") or None  # None -> whole list (we always set it)
    if not groups:
        print("[meeting-watch] enabled but no groups configured — nothing to watch.")
        return 0

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_meeting_watch_tabs(sheets, sheet_id)

    session = mc.make_session()
    today = _today_date()
    exit_code = 0
    changed = baselined = unchanged = not_due = 0

    for group in groups:
        gname = (group or {}).get("name", "")
        cadence = (group or {}).get("cadence")
        for entry in (group or {}).get("events") or []:
            event_id = (entry or {}).get("id")
            if event_id is None:
                continue
            label = (entry or {}).get("label") or f"{gname} event {event_id}"
            ev_date = _parse_date((entry or {}).get("event_date"))

            if not is_due_today(cadence, ev_date, today):
                not_due += 1
                continue

            url = event_url(event_id)
            last = sw.last_meeting_snapshot(sheets, sheet_id, event_id)

            try:
                ev = mc.fetch_event(session, event_id)
            except mc.MMPCFetchError as e:
                if last is None:
                    # No baseline yet AND unreadable: surface an activation block
                    # loudly rather than a silent forever-no-op (see module doc).
                    print(f"[meeting-watch] {label}: NO BASELINE and fetch failed "
                          f"(failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                else:
                    print(f"[meeting-watch] {label}: fetch failed, skipping this run "
                          f"(baseline preserved, not diffed): {e}")
                continue

            if ev is None and last is None:
                # A configured event returns nothing on FIRST sight — almost always
                # a wrong id. Don't baseline a phantom (a later real 200-empty would
                # then read as 'unchanged'); warn and let next run retry.
                print(f"[meeting-watch] {label}: event id {event_id} returned no data "
                      f"on first sight — NOT baselining (check the id).")
                continue

            snap = event_snapshot(ev) if ev is not None else dict(GONE_SNAPSHOT)
            new_hash = snapshot_hash(snap)
            snap_files = snap.get("files")
            n_files = len(snap_files) if isinstance(snap_files, list) else 0
            snap_json = json.dumps(snap, sort_keys=True, ensure_ascii=False)

            if last is None:
                sw.append_meeting_watch_row(
                    sheets, sheet_id, today.isoformat(), gname, label, event_id, url,
                    "baseline", new_hash, n_files, "initial snapshot (no alert)",
                    _now(), snap_json)
                baselined += 1
                print(f"[meeting-watch] {label}: baseline recorded "
                      f"({new_hash}, {n_files} file(s)).")
                continue

            last_hash, last_snap_json = last
            if new_hash == last_hash:
                unchanged += 1
                continue

            # Changed: durable row FIRST, alert email SECOND (best-effort). A crash
            # between them loses the alert, never the record — and the row already
            # advanced the stored hash, so next run won't re-fire.
            old_snap = _load_json(last_snap_json, {"present": True})
            note, body_diff = summarize_change(old_snap, snap)
            sw.append_meeting_watch_row(
                sheets, sheet_id, today.isoformat(), gname, label, event_id, url,
                "changed", new_hash, n_files, note, _now(), snap_json)
            changed += 1
            print(f"[meeting-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
            try:
                ea.send_email(
                    f"[Meeting watch] {label} changed",
                    format_change_body(label, url, note, body_diff),
                    cfg, recipients=recipients,
                )
            except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
                print(f"[meeting-watch] {label}: change recorded but alert email FAILED: {e}")

    print(f"[meeting-watch] done — {changed} changed, {baselined} baselined, "
          f"{unchanged} unchanged, {not_due} not-due-today.")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
