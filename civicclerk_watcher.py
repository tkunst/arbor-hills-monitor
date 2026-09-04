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

VANISH vs. ERROR (ADR 015, mirrors the fail-safe elsewhere in this repo) — for a
HAND-PICKED event (an `events:` list entry):
  - a network/HTTP/JSON error (MMPCFetchError) is TRANSIENT → skip-and-warn if a
    baseline exists (a blip must never diff into a false alert); LOUD exit 1 if
    NO baseline exists yet (activation-time block surfaces instead of no-oping
    forever, same as pfas_watcher),
  - a SUCCESSFUL fetch that returns no event (200 + empty) for a previously-seen
    meeting is a REAL change (cancelled / removed / renumbered) → alert.
  This guarantee is WEAKER for an AUTO-DISCOVER group (`category_id`, ADR 036):
  an event that ages out of `discover_since_days`, or is genuinely deleted
  outright (not just flagged `isDeleted`, which the snapshot hash already
  catches normally) rather than merely cancelled, simply stops appearing in
  `discoverable_events` — this watch loses track of it silently, with no
  "vanished" alert. Accepted residual risk; see ADR 036.

CADENCE is a pure function of config + today, not the cron: one workflow fires
twice daily; each event decides whether it's due (MMPC/DPA every run; BOC weekly
plus daily in the 3 days before the meeting). See is_due_today.

CATEGORY AUTO-DISCOVER (ADR 036): a group may set `category_id` instead of a
hand-picked `events:` list — one paginated `mmpc_client.fetch_category_events`
call replaces the per-event `fetch_event` lookups, and every event on/after
`today - discover_since_days` is diffed exactly like a hand-picked entry (same
snapshot/hash/tab). Added for categoryId 68 (the DPA), which doesn't reliably
pre-create future event stubs the way MMPC/BOC do.

KEYWORD SCAN (ADR 036): gated on civicclerk_watch.keyword_scan.enabled, every
Agenda/Minutes file that's new or changed on a due-checked event (any group) is
downloaded and its text scanned (find_keyword_hits) against a configured list —
closing the gap where a GFL plan-amendment/siting/consistency-determination item
is agendized on a county body without ever touching EGLE's nSITE system. A hit
elevates the alert and overrides an otherwise-silent first-sighting baseline.

GATED on civicclerk_watch.enabled. Recipients are civicclerk_watch.recipients
(Trisha only) — NOT the shared alert_recipients list. Runs on its own schedule
(.github/workflows/meeting-watch.yml).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import fitz  # pymupdf — the repo's existing PDF text-layer dependency

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


# --- keyword scan (ADR 015 addendum) ---------------------------------------------
#
# Closes the county-side/off-nSITE gap: a GFL plan-amendment, siting application,
# MMP consistency-determination, or "Good Neighbor Plan" agenda item can be
# agendized on a Washtenaw County CivicClerk meeting (DPA/MMPC/BOC) with nothing
# ever touching EGLE's nSITE system — every other watcher in this repo is blind to
# it. This section adds keyword-scanning of Agenda/Minutes PDF TEXT on top of the
# existing per-event snapshot/hash diff above; it never replaces or weakens that
# diff, only adds a second, content-level signal on files the diff already flagged
# as new/changed (or present at a meeting's first sighting).


def _is_scannable_file_type(type_str: str | None) -> bool:
    """Whether a published-file `type` string is an Agenda or Minutes document —
    the only types worth downloading + OCR/text-scanning for keywords (Notice/
    Other files are procedural, not substantive). Substring, not equality, so
    "Agenda Packet" style variants still match."""
    t = (type_str or "").lower()
    return "agenda" in t or "minutes" in t


def _keyword_pattern(keyword: str) -> re.Pattern:
    """A case-insensitive, word-boundary regex for `keyword` that tolerates a
    hyphen / extra whitespace / line-wrap newline BETWEEN its words — so the
    keyword "251 acre" also matches a source PDF's "251-acre" or "251\\nacre" (a
    line-wrap mid-phrase), and a single-word keyword like "GFL" or "siting" only
    matches as a whole word (never as a substring of an unrelated word). Mirrors
    rop_client.notice_mentions_srn's proven \\b-boundary approach, generalized to
    multi-word phrases."""
    tokens = keyword.split()
    body = r"[\s\-]+".join(re.escape(t) for t in tokens)
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


def find_keyword_hits(text: str, keywords: list[str]) -> list[tuple[str, str]]:
    """Pure. For each keyword present in `text`, return (keyword, excerpt) — a
    ~140-char window of the ORIGINAL text around the first match, collapsed to
    single-line for readability. Order follows `keywords`; a keyword that matches
    more than once is reported once (its first hit). Every keyword in the caller's
    list is checked independently — including short, deliberately broad ones like
    "consistency"/"siting"/"GFL" that this repo's own review flagged as
    higher-false-positive-risk; kept exactly as directed (fail OPEN: a keyword-scan
    tool that misses a real hit is worse than one that occasionally over-fires —
    every hit carries its matched keyword + excerpt so a false positive is a
    two-second read, not a mystery)."""
    hits: list[tuple[str, str]] = []
    for kw in keywords:
        m = _keyword_pattern(kw).search(text)
        if not m:
            continue
        start, end = max(0, m.start() - 70), min(len(text), m.end() + 70)
        excerpt = " ".join(text[start:end].split())
        hits.append((kw, excerpt))
    return hits


def files_to_scan(old_files: list[dict], new_files: list[dict]) -> list[dict]:
    """Pure. The Agenda/Minutes files in `new_files` that are either NEW (fileId
    not in old_files) or CHANGED (same fileId, different attributes — e.g. a
    replaced upload) relative to `old_files`. Deliberately excludes unchanged and
    removed files — an unchanged file was already scanned on a prior run (or never
    existed then, in which case it's "new" now); a removed file has nothing left
    to download. Called with old_files=[] on a brand-new event's first sighting,
    which correctly scans every file present at that first sighting (the case
    that matters most: a DPA/MMPC/BOC event whose agenda ALREADY carries an
    Arbor Hills item the moment this watch first sees it)."""
    old_by_id = {f["file_id"]: f for f in old_files}
    out = []
    for f in new_files:
        if not _is_scannable_file_type(f.get("type")):
            continue
        prev = old_by_id.get(f.get("file_id"))
        if prev is None or prev != f:
            out.append(f)
    return out


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract the full text layer from a PDF's bytes via PyMuPDF. Raises
    MMPCFetchError on a corrupt/truncated body (download_file_bytes's `%PDF` magic
    -byte check only confirms the HEADER) — routed through the same skip-and-warn
    fail-safe as any other fetch failure in scan_files_for_keywords, never a
    silent crash of the whole watch run over one bad PDF. Deliberately narrow
    (fitz.FileDataError only, not a bare `except Exception`) so a genuine bug
    surfaces loudly instead of being relabeled a routine transient blip — same
    reasoning as rop_client.notice_mentions_srn's docstring."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            return "\n".join(doc[i].get_text() for i in range(len(doc)))
        finally:
            doc.close()
    except fitz.FileDataError as e:
        raise mc.MMPCFetchError(f"PDF could not be parsed: {e}") from e


def scan_files_for_keywords(session, files: list[dict], keywords: list[str]) -> list[dict]:
    """For each file dict (needs file_id/type/name), download its PDF bytes and
    scan for keyword hits. Returns a flat list of {file, keyword, excerpt} dicts —
    one entry per (file, keyword) hit found, across all files. A download or
    parse failure for ONE file is logged and skipped, never raised — keyword-
    scanning is a best-effort ENHANCEMENT layered on top of the change alert that
    already fired (or is about to, for a first-sighting baseline); failing to
    scan a file must never suppress or crash the base document-added/changed
    flow this augments."""
    hits: list[dict] = []
    for f in files:
        try:
            pdf_bytes = mc.download_file_bytes(session, f.get("file_id"))
            text = extract_pdf_text(pdf_bytes)
        except mc.MMPCFetchError as e:
            print(f"[meeting-watch] keyword scan: could not read file "
                  f"{f.get('file_id')} ({f.get('name')}): {e}")
            continue
        for kw, excerpt in find_keyword_hits(text, keywords):
            hits.append({"file": f, "keyword": kw, "excerpt": excerpt})
    return hits


def format_keyword_hits(hits: list[dict]) -> str:
    """Pure. Render keyword hits as a prominent alert block; "" if no hits. Each
    hit names the FILE it was found in, the matched keyword, and a short excerpt —
    so a possible false positive (e.g. "consistency" in an unrelated budget
    line) is a two-second triage, not a mystery."""
    if not hits:
        return ""
    lines = ["⚠️ KEYWORD MATCH — this meeting's agenda/minutes may concern "
              "Arbor Hills / GFL:", ""]
    for h in hits:
        f = h["file"]
        lines.append(f"  [{f.get('type', '')}] {f.get('name', '')}  "
                      f"— matched {h['keyword']!r}")
        lines.append(f"    …{h['excerpt']}…")
    return "\n".join(lines)


def discoverable_events(events: list[dict], cutoff: date) -> list[dict]:
    """Pure. `events` (raw CivicClerk event dicts) whose eventDate is on or after
    `cutoff`, sorted oldest-first. Used by the category_id AUTO-DISCOVER group
    mode to bound each run's fetch to recent + future meetings only — full
    history is covered once by the 12-month backfill (run_historical_backfill),
    not re-baselined (and re-downloaded/re-scanned) every single run. An event
    with no parseable eventDate is excluded (fail-safe: never guess a date)."""
    out = []
    for ev in events:
        d = _parse_date((ev.get("eventDate") or "")[:10])
        if d is not None and d >= cutoff:
            out.append(ev)
    return sorted(out, key=lambda e: e.get("eventDate") or "")


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

    kw_cfg = wcfg.get("keyword_scan") or {}
    kw_enabled = bool(kw_cfg.get("enabled")) and bool(kw_cfg.get("keywords"))
    keywords = kw_cfg.get("keywords") or []

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_meeting_watch_tabs(sheets, sheet_id)

    session = mc.make_session()
    today = _today_date()
    exit_code = 0
    changed = baselined = unchanged = not_due = keyword_alerts = 0

    for group in groups:
        gname = (group or {}).get("name", "")
        cadence = (group or {}).get("cadence")
        category_id = (group or {}).get("category_id")

        if category_id is not None:
            # AUTO-DISCOVER mode (ADR 015 addendum) — the group has no hand-picked
            # `events:` list because future event IDs for this category don't exist
            # on the portal yet (confirmed live for categoryId 68, the DPA). One
            # paginated fetch for the whole category; entries carry the ALREADY-
            # fetched event, so no extra per-event API call follows.
            since_days = int((group or {}).get("discover_since_days") or 400)
            cutoff = today - timedelta(days=since_days)
            try:
                raw_events = mc.fetch_category_events(session, category_id)
            except mc.MMPCFetchError as e:
                if sw.meeting_watch_group_has_rows(sheets, sheet_id, gname):
                    print(f"[meeting-watch] {gname}: category fetch failed, "
                          f"skipping this run (baselines preserved): {e}")
                else:
                    print(f"[meeting-watch] {gname}: NO BASELINE and category "
                          f"fetch failed (failing loudly so activation surfaces "
                          f"it): {e}")
                    exit_code = 1
                continue
            entries = [
                (ev.get("id"), (ev.get("eventName") or gname),
                 _parse_date((ev.get("eventDate") or "")[:10]), ev)
                for ev in discoverable_events(raw_events, cutoff)
                if ev.get("id") is not None
            ]
        else:
            entries = [
                ((entry or {}).get("id"),
                 (entry or {}).get("label") or f"{gname} event {(entry or {}).get('id')}",
                 _parse_date((entry or {}).get("event_date")), None)
                for entry in (group or {}).get("events") or []
                if (entry or {}).get("id") is not None
            ]

        for event_id, label, ev_date, prefetched_ev in entries:
            if not is_due_today(cadence, ev_date, today):
                not_due += 1
                continue

            url = event_url(event_id)
            last = sw.last_meeting_snapshot(sheets, sheet_id, event_id)

            if prefetched_ev is not None:
                ev = prefetched_ev
            else:
                try:
                    ev = mc.fetch_event(session, event_id)
                except mc.MMPCFetchError as e:
                    if last is None:
                        # No baseline yet AND unreadable: surface an activation
                        # block loudly rather than a silent forever-no-op (see
                        # module doc).
                        print(f"[meeting-watch] {label}: NO BASELINE and fetch "
                              f"failed (failing loudly so activation surfaces "
                              f"it): {e}")
                        exit_code = 1
                    else:
                        print(f"[meeting-watch] {label}: fetch failed, skipping "
                              f"this run (baseline preserved, not diffed): {e}")
                    continue

                if ev is None and last is None:
                    # A configured event returns nothing on FIRST sight — almost
                    # always a wrong id. Don't baseline a phantom (a later real
                    # 200-empty would then read as 'unchanged'); warn and let
                    # next run retry.
                    print(f"[meeting-watch] {label}: event id {event_id} "
                          f"returned no data on first sight — NOT baselining "
                          f"(check the id).")
                    continue

            snap = event_snapshot(ev) if ev is not None else dict(GONE_SNAPSHOT)
            new_hash = snapshot_hash(snap)
            snap_files = snap.get("files")
            snap_files = snap_files if isinstance(snap_files, list) else []
            n_files = len(snap_files)
            snap_json = json.dumps(snap, sort_keys=True, ensure_ascii=False)

            if last is None:
                to_scan = files_to_scan([], snap_files) if kw_enabled else []
                hits = scan_files_for_keywords(session, to_scan, keywords) if to_scan else []
                note = "initial snapshot (no alert)"
                if hits:
                    kws = ", ".join(sorted({h["keyword"] for h in hits}))
                    note = f"initial snapshot — KEYWORD MATCH: {kws}"
                sw.append_meeting_watch_row(
                    sheets, sheet_id, today.isoformat(), gname, label, event_id, url,
                    "baseline", new_hash, n_files, note, _now(), snap_json)
                baselined += 1
                print(f"[meeting-watch] {label}: baseline recorded "
                      f"({new_hash}, {n_files} file(s))"
                      f"{' — KEYWORD MATCH' if hits else ''}.")
                if hits:
                    keyword_alerts += 1
                    body = (format_keyword_hits(hits) + "\n\n"
                            "This is a NEWLY-SEEN meeting (not a change to one "
                            "already being watched) whose agenda/minutes already "
                            f"carry this content.\n\nMeeting: {label}\nPage: {url}\n")
                    try:
                        ea.send_email(f"[Arbor Hills ALERT] Keyword match — {label}",
                                      body, cfg, recipients=recipients)
                    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
                        print(f"[meeting-watch] {label}: keyword match recorded "
                              f"but alert email FAILED: {e}")
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
            old_files = old_snap.get("files") or []
            to_scan = files_to_scan(old_files, snap_files) if kw_enabled else []
            hits = scan_files_for_keywords(session, to_scan, keywords) if to_scan else []
            if hits:
                # Fold the match into the DURABLE row's Note, not just the
                # best-effort email — same as the baseline branch above. Without
                # this, a keyword hit on a change is only ever recoverable from
                # an ephemeral Actions log if the email send happens to fail.
                kws = ", ".join(sorted({h["keyword"] for h in hits}))
                note = f"{note} — KEYWORD MATCH: {kws}"
            sw.append_meeting_watch_row(
                sheets, sheet_id, today.isoformat(), gname, label, event_id, url,
                "changed", new_hash, n_files, note, _now(), snap_json)
            changed += 1
            print(f"[meeting-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note})"
                  f"{' — KEYWORD MATCH' if hits else ''}.")
            subject = (f"[Arbor Hills ALERT] {label} changed" if hits
                       else f"[Meeting watch] {label} changed")
            body = format_change_body(label, url, note, body_diff)
            if hits:
                keyword_alerts += 1
                body = format_keyword_hits(hits) + "\n\n" + body
            try:
                ea.send_email(subject, body, cfg, recipients=recipients)
            except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
                print(f"[meeting-watch] {label}: change recorded but alert email FAILED: {e}")

    print(f"[meeting-watch] done — {changed} changed, {baselined} baselined, "
          f"{unchanged} unchanged, {not_due} not-due-today, "
          f"{keyword_alerts} keyword-match alert(s).")
    return exit_code


# --- one-time 12-month historical keyword backfill (ADR 036) ---------------------
#
# A single, explicitly-invoked sweep (never scheduled) of the past N months across
# every watched CivicClerk category — including the two hand-picked ones (72/26/27)
# that have never been keyword-scanned before this feature existed. Read-only
# against the live API; the ONLY Sheet write is a SILENT baseline row for an event
# that doesn't already have one (never touches an existing baseline — a category
# 26/27 event already tracked by the live hand-picked groups keeps its real
# snapshot history intact). Reuses event_snapshot/snapshot_hash/
# append_meeting_watch_row/scan_files_for_keywords/find_keyword_hits verbatim —
# NOT a reimplementation — so a baseline this backfill writes hashes IDENTICALLY
# to one the live watch would have written, and a later live run of an
# already-known event reads "unchanged", never a false "changed" alert.

# (category_id, descriptive group label — matches config.yml's group names where
# one already exists, for a consistent "Group" column in the Meeting Watch tab).
BACKFILL_CATEGORIES = [
    (68, "Washtenaw County Board of Public Works (DPA)"),
    (72, "MMPC"),
    (26, "Washtenaw County BOC — Board Meeting"),
    (27, "Washtenaw County BOC — Working Session"),
]


def format_backfill_report(
    hit_events: list[dict], n_events: int, n_baselined: int, n_already_known: int,
    months: int,
) -> str:
    """Pure. The one-time backfill's summary email body. Keyword hits are listed
    FIRST and prominently (the whole point of the report); a clean scan still
    gets a clear "nothing found" line rather than silence. `hit_events` items:
    {group, label, date, url, hits: [{file, keyword, excerpt}, ...]}."""
    lines = [
        f"One-time historical scan: the last {months} months of Washtenaw County "
        "CivicClerk meetings (Board of Public Works, MMPC, and the Board of "
        "Commissioners) checked against the Arbor Hills keyword-watch list.",
        "",
    ]
    if hit_events:
        lines.append(f"⚠️ {len(hit_events)} meeting(s) matched a keyword — reviewed FIRST:")
        lines.append("")
        for h in hit_events:
            lines.append(f"  {h['date']}  [{h['group']}]  {h['label']}")
            lines.append(f"    {h['url']}")
            for hit in h["hits"]:
                f = hit["file"]
                lines.append(f"    - [{f.get('type', '')}] {f.get('name', '')} "
                              f"— matched {hit['keyword']!r}")
                lines.append(f"      …{hit['excerpt']}…")
            lines.append("")
    else:
        lines.append(f"No keyword matches found in the last {months} months.")
        lines.append("")
    lines.append(
        f"Scanned {n_events} meeting(s) total across Board of Public Works/MMPC/BOC "
        f"({n_baselined} newly recorded as silent baselines in the Meeting Watch tab, "
        f"{n_already_known} already tracked by the live watch). These meetings are "
        "now baselined — the live watch alerts only on FUTURE changes, not this "
        "history, so this report will not repeat.")
    lines.append("")
    lines.append(
        "Coverage note: this backfill (and the ongoing keyword watch it seeds) "
        "covers AGENDIZED items only — what CivicClerk actually published. It does "
        "NOT cover a raw paper/UPS filing to the county that never reached a public "
        "meeting agenda (a FOIA gap; see ADR 036).")
    return "\n".join(lines)


def run_historical_backfill(months: int = 12) -> int:
    """One-time (never scheduled): scan the past `months` months across every
    watched category (68/72/26/27) against civicclerk_watch.keyword_scan.keywords,
    email Trisha ONE summary report of every hit found, and silently baseline any
    event that doesn't already have a Meeting Watch row — so the live watch, once
    it reaches these same events on its normal cadence, sees them as already-known
    (no re-baseline, no false "changed" alert; see the module-level note above).
    Uses the configured keyword list regardless of keyword_scan.enabled — this is
    an explicit one-off report, not gated by the ongoing watch's toggle."""
    cfg = load_config()
    wcfg = cfg.get("civicclerk_watch") or {}
    keywords = ((wcfg.get("keyword_scan") or {}).get("keywords")) or []
    recipients = wcfg.get("recipients") or None

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_meeting_watch_tabs(sheets, sheet_id)
    session = mc.make_session()
    today = _today_date()
    cutoff = today - timedelta(days=months * 31)  # generous — never UNDER-covers

    hit_events: list[dict] = []
    n_events = n_baselined = n_already_known = 0

    for category_id, gname in BACKFILL_CATEGORIES:
        try:
            raw_events = mc.fetch_category_events(session, category_id)
        except mc.MMPCFetchError as e:
            print(f"[backfill] category {category_id} ({gname}) fetch failed "
                  f"(skipping this category): {e}")
            continue

        for ev in discoverable_events(raw_events, cutoff):
            event_id = ev.get("id")
            if event_id is None:
                continue
            n_events += 1
            label = ev.get("eventName") or gname
            ev_date = (ev.get("eventDate") or "")[:10]
            url = event_url(event_id)

            snap = event_snapshot(ev)
            to_scan = files_to_scan([], snap["files"]) if keywords else []
            hits = scan_files_for_keywords(session, to_scan, keywords) if to_scan else []
            if hits:
                hit_events.append({"group": gname, "label": label, "date": ev_date,
                                    "url": url, "hits": hits})

            last = sw.last_meeting_snapshot(sheets, sheet_id, event_id)
            if last is not None:
                n_already_known += 1
                continue

            new_hash = snapshot_hash(snap)
            note = "historical backfill baseline (no alert)"
            if hits:
                kws = ", ".join(sorted({h["keyword"] for h in hits}))
                note = f"historical backfill baseline — KEYWORD MATCH: {kws}"
            sw.append_meeting_watch_row(
                sheets, sheet_id, today.isoformat(), gname, label, event_id, url,
                "baseline", new_hash, len(snap["files"]), note, _now(),
                json.dumps(snap, sort_keys=True, ensure_ascii=False))
            n_baselined += 1

        print(f"[backfill] category {category_id} ({gname}): done.")

    body = format_backfill_report(hit_events, n_events, n_baselined, n_already_known, months)
    sent = False
    try:
        sent = ea.send_email(
            f"[Arbor Hills] {months}-month CivicClerk keyword backfill — "
            f"{len(hit_events)} meeting(s) matched",
            body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — report is best-effort; baselines are already durable
        print(f"[backfill] report email FAILED: {e}")

    print(f"[backfill] done — {n_events} events scanned, {n_baselined} newly "
          f"baselined, {n_already_known} already known, {len(hit_events)} "
          f"meeting(s) with keyword hits, email sent={sent}.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
