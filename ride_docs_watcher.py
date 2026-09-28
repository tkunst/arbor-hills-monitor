"""
ride_docs_watcher.py — Stream T: daily watch on the DOCUMENTS RRD lists for the
Arbor-Hills-area Part 201 sites in EGLE's RIDE (Remediation Information Data
Exchange), via the anonymous file listing its public Inventory of Facilities page
exposes. See docs/decisions/058-rrd-documents-watch.md and ride_docs_client.py.

WHY: RRD is the fourth EGLE division with Arbor Hills records and the only one
the monitor watched for STATUS but never for DOCUMENTS (Stream J / ADR 019). The
E614007 FOIA release showed ~45 RRD documents we had never seen. RRD is also
still digitizing its backlog ("records may exist that are not displayed here"),
so files keep appearing — Salem Landfill's were added Jul-Oct 2025.

WHAT IT DOES, per watched location (Part 201 program number -> RIDE locationId):
  - lists every file RIDE shows for it; each record's durable key is its `uri`;
  - FIRST sighting of a location -> silent `baseline` rows + one `loc:` marker;
  - a uri never seen before -> `new` row + alert (a uri that had been `removed`
    and returns is `new` again);
  - a known uri whose canonical record changed -> `changed` row + alert;
  - a known uri that vanished from the listing -> `removed` row + alert (a public
    record disappearing is itself signal);
  - then (optional) mirrors not-yet-mirrored files to a PRIVATE Drive folder,
    recording SHA-256 + MD5 and the Drive link, bounded per run.

HARD RULES (ADR 058) — pinned by tests/test_ride_docs.py:
  - NEVER PUBLISHES. Rows go to the PRIVATE Sheet (GSHEET_ID_PRIVATE), never the
    public case-file Sheet (GSHEET_ID); files go to a private Drive folder that
    must not be any other configured mirror folder; nothing here imports or
    feeds findings_feed / gen_findings_feed. RIDE file titles can include
    residents' names and street addresses; publishing is Trisha's hand-curation
    decision, never this job's.
  - Recipients are scoped VERBATIM (Trisha only); an EMPTY list means display-only
    (rows, no email) — it NEVER falls back to the coalition alert list.
  - Fails CLOSED when GSHEET_ID_PRIVATE is missing or equals GSHEET_ID.

ACCURACY POSTURE: alerts are source-labeled listing events, not findings. They
show BOTH the document's own date and the date it was added to RIDE (backlog
digitization means an old document can be newly listed with no new activity) and
say the file has not been reviewed.

FAILURE MODES: a fetch failure (RideDocsFetchError, incl. the 405 a bot challenge
gives an unwarmed/blocked session) is skip-and-warn per location when that
location already has a baseline, LOUD (exit 1) when it doesn't; a structural
break (RideDocsParseError) is ALWAYS loud. `--probe` runs the client end to end
(session + resolve + list) regardless of the enabled flag and touches no Sheet,
Drive or email — the pre-activation check that the Azure runner isn't challenged.

GATED on ride_docs.enabled (a brand-new external source; ships false). Flipping
it on is a separate, later, human step.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import archive_client as ac
import drive_client as dc
import email_alerts as ea
import ride_docs_client as rdc
import sheet_writer as sw
from config_loader import load_config

FOLDER_ENV = "GOAUTH_RRD_FOLDER_ID"
_MAX_MIRROR_FAILS = 3          # then a file is recorded `mirror-skipped` until its record changes
_DEFAULT_STALE_SKIPS = 3       # consecutive skipped runs for a baselined location before ONE liveness alert
_EMAIL_LIST_CAP = 25           # lines per alert body; every file still gets its own Sheet row

# Column indexes of sw.RRD_DOCS_HEADERS (kept next to the only code that reads them).
C_DATE, C_KEY, C_EVENT, C_LOC, C_PROGRAM, C_TITLE, C_INDEX, C_EXT, C_SIZE = range(9)
C_DOCDATE, C_ADDED, C_FOLDER, C_DOCNO, C_HASH, C_LINK, C_SHA, C_MD5, C_NOTE, C_CHECKED = range(9, 19)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking."""
    if not (cfg.get("ride_docs") or {}).get("enabled"):
        return False, "ride_docs.enabled is false — skipping (no-op)."
    return True, ""


def _private_sheet_id() -> str | None:
    """The PRIVATE Sheet id, or None if this run must not write. Fails closed:
    unset -> None; equal to the public GSHEET_ID -> None (a misconfigured secret
    must never route resident-address-bearing rows onto the public Sheet)."""
    priv = (os.environ.get("GSHEET_ID_PRIVATE") or "").strip()
    if not priv:
        return None
    if priv == (os.environ.get("GSHEET_ID") or "").strip():
        return None
    return priv


def _mirror_folder_id() -> str | None:
    """The private RRD mirror folder id, or None when mirroring isn't configured.
    Raises RuntimeError if it equals ANY other GOAUTH_*_FOLDER_ID (those mirrors
    include public-facing folders) — a copy-pasted secret must never send RRD
    files to another mirror's folder."""
    if not ac.is_configured(FOLDER_ENV):
        return None
    mine = ac.folder_id(FOLDER_ENV)
    for name, value in os.environ.items():
        if name != FOLDER_ENV and re.fullmatch(r"GOAUTH_.*_FOLDER_ID", name) and value == mine:
            raise RuntimeError(
                f"{FOLDER_ENV} equals {name} — refusing to mirror RRD files into another "
                "mirror's folder (the RRD mirror must be its own PRIVATE folder)")
    return mine


# ---------------------------------------------------------------------------
# State (pure): fold the append-only tab into per-key state
# ---------------------------------------------------------------------------


def _cell(row: list, idx: int) -> str:
    return row[idx] if idx < len(row) else ""


def build_state(rows: list[list]) -> tuple[dict, dict]:
    """(files, locs). files[`rrd:<uri>`] = {location_id, hash, removed, mirror_link,
    skipped, fails, title}; locs[`loc:<lid>`] = {program, skips} where `skips` is the
    number of CONSECUTIVE `fetch-skipped` rows since the last baseline/`fetch-ok`. The LAST row for a key wins,
    with mirror bookkeeping reset whenever the record itself (re)appears/changes."""
    files: dict[str, dict] = {}
    locs: dict[str, dict] = {}
    for r in rows:
        key, event = _cell(r, C_KEY), _cell(r, C_EVENT)
        if key.startswith("loc:"):
            prev = locs.get(key, {"skips": 0})
            skips = prev["skips"] + 1 if event == "fetch-skipped" else 0   # baseline / fetch-ok reset it
            locs[key] = {"program": _cell(r, C_PROGRAM), "skips": skips}
            continue
        if not key.startswith("rrd:"):
            continue
        st = files.get(key)
        if event in ("baseline", "new", "changed"):
            files[key] = {"location_id": _cell(r, C_LOC), "hash": _cell(r, C_HASH),
                          "title": _cell(r, C_TITLE), "removed": False,
                          "mirror_link": "", "skipped": False, "fails": 0}
        elif st is None:
            continue            # a mirror/removed event for a key we never saw: ignore
        elif event == "removed":
            st["removed"] = True
        elif event == "mirrored":
            st["mirror_link"], st["skipped"], st["fails"] = _cell(r, C_LINK), False, 0
        elif event == "mirror-skipped":
            st["skipped"] = True
        elif event == "mirror-failed":
            st["fails"] += 1
    return files, locs


def diff_location(location_id: int, views: dict[str, dict], files_state: dict,
                  baselined: bool) -> dict:
    """Classify one location's current files against stored state. Pure.
    Returns {baseline, new, changed, removed}; the latter three are empty on a
    first sighting (everything is `baseline`)."""
    out = {"baseline": [], "new": [], "changed": [], "removed": []}
    if not baselined:
        out["baseline"] = list(views.values())
        return out
    for uri, view in views.items():
        st = files_state.get(f"rrd:{uri}")
        if st is None or st["removed"]:
            out["new"].append(view)
        elif st["hash"] != rdc.record_hash(view):
            out["changed"].append((st, view))
    lid = str(location_id)
    for key, st in files_state.items():
        if st["location_id"] == lid and not st["removed"] and key[4:] not in views:
            out["removed"].append((key[4:], st))      # (uri, last stored state)
    return out


# ---------------------------------------------------------------------------
# Rows + alert copy (pure)
# ---------------------------------------------------------------------------


def _row(today: str, key: str, event: str, loc: dict, program: str, view: dict | None,
         note: str = "", link: str = "", sha: str = "", md5: str = "",
         record_hash: str = "") -> list:
    v = view or {}
    return [
        today, key, event, str(loc["location_id"]), program,
        v.get("title") or v.get("file_name", ""), v.get("index_type", ""), v.get("extension", ""),
        v.get("size", ""), v.get("date_of_document", ""), v.get("created_on", ""),
        v.get("folder_number", ""), v.get("doc_number", ""),
        record_hash or (rdc.record_hash(v) if v else ""), link, sha, md5, note, _now(),
    ]


def location_label(loc: dict) -> str:
    name = f" {loc['name']}" if loc.get("name") else ""
    return f"RIDE location {loc['location_id']} — Part 201 site {loc['program_num']}{name}"


def _kb(size: str) -> str:
    try:
        return f"{int(size) / 1024:,.0f} KB"
    except (TypeError, ValueError):
        return "size unknown"


def _describe(v: dict) -> str:
    when = v["date_of_document"] or "unknown (RIDE lists no date)"
    return (f"- {v['title'] or v['file_name'] or '(untitled)'}\n"
            f"    {v['index_type'] or 'no index type'} | document date {when} | "
            f"added to RIDE {v['created_on'] or 'unknown'} | {_kb(v['size'])} | "
            f"folder {v['folder_number'] or '—'} | uri {v['uri']}")


_ACCURACY_NOTE = (
    "Reading this alert: \"document date\" is the date on the record itself; \"added to "
    "RIDE\" is when RRD uploaded it. RRD is digitizing its backlog, so an old document can "
    "be newly listed without any new activity at the site. This is an automated listing "
    "watch — the file contents have NOT been reviewed by the monitor.\n\n"
    "PRIVATE: titles in RIDE can include residents' names and street addresses. Nothing here "
    "is published; the files are mirrored only to your private Drive folder (when configured). "
    "Publishing anything is a hand-curation decision."
)


def format_new_body(label: str, views: list[dict]) -> str:
    """Alert body for newly-listed files at one location. Pure."""
    shown = views[:_EMAIL_LIST_CAP]
    more = f"\n\n(+ {len(views) - len(shown)} more — see the RRD Documents tab.)" if len(views) > len(shown) else ""
    return (f"{len(views)} file(s) newly appeared in EGLE's RIDE public file listing for "
            f"{label}.\n\n" + "\n".join(_describe(v) for v in shown) + more + "\n\n" + _ACCURACY_NOTE + "\n")


def format_changed_body(label: str, changes: list[tuple[dict, dict]]) -> str:
    """Alert body for files whose canonical record changed. `changes` is a list of
    (stored_state, new_view). Pure."""
    shown = changes[:_EMAIL_LIST_CAP]
    lines = [_describe(v) for _, v in shown]
    more = f"\n\n(+ {len(changes) - len(shown)} more — see the RRD Documents tab.)" if len(changes) > len(shown) else ""
    return (f"{len(changes)} file record(s) CHANGED in EGLE's RIDE public file listing for "
            f"{label} (title, date, size, index type or folder differs from what was last "
            "recorded; the previous values are in the RRD Documents tab).\n\n"
            + "\n".join(lines) + more + "\n\n" + _ACCURACY_NOTE + "\n")


def format_removed_body(label: str, removed: list[tuple[str, dict]]) -> str:
    """Alert body for files no longer listed; `removed` is [(uri, last stored
    state)] so each line carries the last recorded title. Pure."""
    shown = removed[:_EMAIL_LIST_CAP]
    more = f"\n(+ {len(removed) - len(shown)} more)" if len(removed) > len(shown) else ""
    return (f"{len(removed)} file(s) that RIDE previously listed for {label} are NO LONGER LISTED "
            "(RRD may have withdrawn, redacted or re-indexed them).\n\n"
            + "\n".join(f"- {st.get('title') or '(untitled)'} — uri {u}" for u, st in shown) + more
            + "\n\nThe RRD Documents tab keeps the last recorded details for each.\n")


def _send(recipients: list[str], subject: str, body: str, cfg: dict) -> None:
    """Best-effort alert. EMPTY recipients = display-only: send_email would fall
    back to the whole coalition list on an empty list, so it is never called."""
    if not recipients:
        print(f"[ride-docs] display-only (no recipients): {subject}")
        return
    try:
        ea.send_email(subject, body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert is best-effort; rows are already recorded
        print(f"[ride-docs] alert email FAILED (rows are recorded): {subject}: {e}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _mirror_pass(session, service, priv_id, today, todo, folder_id, max_bytes, cap) -> int:
    """Download + upload up to `cap` not-yet-mirrored files to the PRIVATE Drive
    folder, appending one row per outcome. `todo` = [(loc, program, view, hash)].
    Never raises for a single file: a failure becomes a `mirror-failed` row (after
    _MAX_MIRROR_FAILS the caller records `mirror-skipped`). Returns files mirrored."""
    done = 0
    drive = ac.oauth_drive_service()
    with tempfile.TemporaryDirectory() as tmp:
        for loc, program, view, rec_hash, fails in todo[:cap]:
            name = rdc.safe_filename(view["uri"], view["title"] or view["file_name"],
                                     view["extension"], rec_hash[:8])
            dest = os.path.join(tmp, name)
            key = f"rrd:{view['uri']}"
            try:
                info = rdc.download_file(session, view["uri"], dest, max_bytes,
                                         expect_pdf=view["extension"] == "PDF")
                link = ac.upload_file(drive, dest, name, "application/pdf"
                                      if view["extension"] == "PDF" else "application/octet-stream",
                                      folder_id)
                sw.append_rrd_docs_rows(service, priv_id, [_row(
                    today, key, "mirrored", loc, program, view, note="mirrored to private Drive folder",
                    link=link, sha=info["sha256"], md5=info["md5"], record_hash=rec_hash)])
                done += 1
                print(f"[ride-docs] mirrored uri {view['uri']} ({info['size']:,} B).")
            except rdc.RideDocsTooLargeError as e:
                sw.append_rrd_docs_rows(service, priv_id, [_row(
                    today, key, "mirror-skipped", loc, program, view,
                    note=f"not mirrored: {e}", record_hash=rec_hash)])
                print(f"[ride-docs] uri {view['uri']}: too large, skipped ({e}).")
            except Exception as e:  # noqa: BLE001 — one bad file must not stop the rest
                event = "mirror-skipped" if fails + 1 >= _MAX_MIRROR_FAILS else "mirror-failed"
                sw.append_rrd_docs_rows(service, priv_id, [_row(
                    today, key, event, loc, program, view,
                    note=f"mirror attempt {fails + 1} failed: {str(e)[:200]}", record_hash=rec_hash)])
                print(f"[ride-docs] uri {view['uri']}: mirror failed ({e}).")
    return done


def _note_skip(sheets, priv_id, today, loc_key, locs_state, err, recipients, cfg, threshold) -> None:
    """A baselined location was skipped this run (transient fetch failure). Records a
    `fetch-skipped` row and, on the run that makes it `threshold` CONSECUTIVE skips,
    sends ONE liveness alert — so a persistent outage (RIDE's bot defense starting
    to challenge the runner, an endpoint moved to a non-JSON error) can never go
    quiet forever. Fires exactly once per outage; a later `fetch-ok` row resets it."""
    st = locs_state[loc_key]
    lid = loc_key[4:]
    loc = {"location_id": lid, "program_num": st["program"], "name": ""}
    st["skips"] += 1
    sw.append_rrd_docs_rows(sheets, priv_id, [_row(
        today, loc_key, "fetch-skipped", loc, st["program"], None,
        note=f"skipped (consecutive: {st['skips']}): {str(err)[:200]}")])
    if st["skips"] == threshold:
        _send(recipients, f"[RIDE docs] RIDE file listing unreachable for {threshold} runs: location {lid}",
              f"The RRD document watch could not read RIDE's file listing for location {lid} "
              f"(Part 201 site {st['program']}) on {threshold} consecutive runs, so any new file "
              f"there is currently going UNSEEN.\n\nLast error: {str(err)[:300]}\n\n"
              "Likely causes: RIDE's bot defense is challenging the GitHub runner, or RIDE changed "
              "its endpoints. Run the workflow manually with probe=true to see the failure "
              "outside the Sheet. This alert fires once per outage; the RRD Documents tab records "
              "each skipped run and the recovery.\n", cfg)


def run_probe(cfg: dict) -> int:
    """`--probe`: session + resolve + list for every configured program, printing
    a summary. Reads only; runs regardless of the enabled flag."""
    programs = _program_nums(cfg)
    try:
        results = rdc.probe(programs)
    except (rdc.RideDocsFetchError, rdc.RideDocsParseError) as e:
        print(f"[ride-docs] PROBE FAILED: {e}")
        return 1
    for r in results:
        where = f"location {r['location_id']} ({r['name']}), {r['n_files']} file(s)" \
            if r["location_id"] is not None else "NOT in RIDE's inventory"
        print(f"[ride-docs] probe: program {r['program_num']} -> {where}")
    print("[ride-docs] PROBE OK — anonymous session, program lookup and file listing all worked.")
    return 0


def _program_nums(cfg: dict) -> list[str]:
    rcfg = cfg.get("ride_docs") or {}
    explicit = rcfg.get("program_nums")
    nums = explicit if explicit else (cfg.get("ride") or {}).get("site_ids") or []
    return [str(n) for n in nums]


def run(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    if "--probe" in argv:
        return run_probe(cfg)

    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[ride-docs] {reason}")
        return 0

    priv_id = _private_sheet_id()
    if priv_id is None:
        print("[ride-docs] GSHEET_ID_PRIVATE is unset (or equals the public Sheet id) — refusing to run: "
              "RRD document rows may carry residents' names/addresses and belong ONLY on the "
              "private Sheet.")
        return 1

    rcfg = cfg.get("ride_docs") or {}
    programs = _program_nums(cfg)
    recipients = list(rcfg.get("recipients") or [])          # empty => display-only
    do_mirror = bool(rcfg.get("mirror", True))
    max_mirror = int(rcfg.get("max_mirror_per_run", 8))
    max_bytes = int(float(rcfg.get("max_file_mb", 250)) * 1024 * 1024)
    stale_after = int(rcfg.get("stale_alert_after_skips", _DEFAULT_STALE_SKIPS))

    sheets = dc.sheets_service()
    sw.ensure_rrd_docs_tabs(sheets, priv_id)
    files_state, locs_state = build_state(sw.read_rrd_docs_rows(sheets, priv_id))
    baselined_programs = {v["program"] for v in locs_state.values()}
    today = _today()
    exit_code = 0
    counts = defaultdict(int)

    try:
        session = rdc.open_session()
    except rdc.RideDocsFetchError as e:
        if set(programs) <= baselined_programs:
            print(f"[ride-docs] session could not be opened, skipping this run "
                  f"(baselines preserved, not diffed): {e}")
            for loc_key in list(locs_state):
                _note_skip(sheets, priv_id, today, loc_key, locs_state, e, recipients, cfg, stale_after)
            return 0
        print(f"[ride-docs] NO BASELINE for at least one program and the session could not "
              f"be opened (failing loudly so activation surfaces it): {e}")
        return 1

    # program -> location, grouped so a location shared by two programs is listed once.
    by_loc: dict[int, dict] = {}
    for pn in programs:
        try:
            loc = rdc.resolve_location(session, pn)
        except rdc.RideDocsParseError as e:
            print(f"[ride-docs] program {pn}: STRUCTURAL failure (failing loudly): {e}")
            exit_code = 1
            continue
        except rdc.RideDocsFetchError as e:
            if pn in baselined_programs:
                print(f"[ride-docs] program {pn}: lookup failed, skipping (baseline preserved): {e}")
                for loc_key, st in list(locs_state.items()):
                    if st["program"] == pn:
                        _note_skip(sheets, priv_id, today, loc_key, locs_state, e, recipients, cfg, stale_after)
            else:
                print(f"[ride-docs] program {pn}: NO BASELINE and lookup failed (loud): {e}")
                exit_code = 1
            continue
        if loc is None:
            print(f"[ride-docs] program {pn}: not in RIDE's public inventory (nothing to watch).")
            continue
        by_loc.setdefault(loc["location_id"], loc)

    mirror_todo: list = []
    for lid, loc in by_loc.items():
        program = loc["program_num"]
        loc_key = f"loc:{lid}"
        try:
            records = rdc.fetch_location_files(session, lid)
        except rdc.RideDocsParseError as e:
            print(f"[ride-docs] {location_label(loc)}: STRUCTURAL failure (failing loudly): {e}")
            exit_code = 1
            continue
        except rdc.RideDocsFetchError as e:
            if loc_key in locs_state:
                print(f"[ride-docs] {location_label(loc)}: file list failed, skipping "
                      f"(baseline preserved): {e}")
                _note_skip(sheets, priv_id, today, loc_key, locs_state, e, recipients, cfg, stale_after)
            else:
                print(f"[ride-docs] {location_label(loc)}: NO BASELINE and file list failed (loud): {e}")
                exit_code = 1
            continue

        views = {v["uri"]: v for v in (rdc.file_view(r) for r in records)}
        baselined = loc_key in locs_state
        d = diff_location(lid, views, files_state, baselined)
        label = location_label(loc)

        # Durable rows FIRST (file rows, then the loc marker: a crash between them
        # re-baselines silently next run rather than alerting on the whole list).
        rows = []
        for v in d["baseline"]:
            rows.append(_row(today, f"rrd:{v['uri']}", "baseline", loc, program, v,
                             note="initial listing (no alert)"))
        for v in d["new"]:
            was_removed = files_state.get(f"rrd:{v['uri']}", {}).get("removed")
            rows.append(_row(today, f"rrd:{v['uri']}", "new", loc, program, v,
                             note="reappeared after removal" if was_removed else "newly listed"))
        for st, v in d["changed"]:
            rows.append(_row(today, f"rrd:{v['uri']}", "changed", loc, program, v,
                             note=f"record hash {st['hash']} -> {rdc.record_hash(v)}"))
        for uri, st in d["removed"]:
            rows.append(_row(today, f"rrd:{uri}", "removed", loc, program,
                             {"title": st.get("title", "")},
                             note="no longer in RIDE's listing", record_hash=st["hash"]))
        if not baselined:
            rows.append(_row(today, loc_key, "baseline", loc, program, None,
                             note=f"location baselined: {len(views)} file(s)"))
        elif locs_state[loc_key]["skips"]:
            rows.append(_row(today, loc_key, "fetch-ok", loc, program, None,
                             note=f"listing readable again after {locs_state[loc_key]['skips']} skipped run(s)"))
        if rows:
            sw.append_rrd_docs_rows(sheets, priv_id, rows)
        counts["baseline"] += len(d["baseline"])
        counts["new"] += len(d["new"])
        counts["changed"] += len(d["changed"])
        counts["removed"] += len(d["removed"])
        print(f"[ride-docs] {label}: {len(views)} listed — {len(d['baseline'])} baselined, "
              f"{len(d['new'])} new, {len(d['changed'])} changed, {len(d['removed'])} removed.")

        # Refresh in-memory state so the mirror pass sees this run's rows.
        new_state, new_locs = build_state(rows)
        for k, v in new_state.items():
            files_state[k] = v
        locs_state.update(new_locs)

        if d["new"]:
            _send(recipients, f"[RIDE docs] {len(d['new'])} new RRD file(s): {label}",
                  format_new_body(label, d["new"]), cfg)
        if d["changed"]:
            _send(recipients, f"[RIDE docs] RRD file record changed: {label}",
                  format_changed_body(label, d["changed"]), cfg)
        if d["removed"]:
            _send(recipients, f"[RIDE docs] RRD file(s) no longer listed: {label}",
                  format_removed_body(label, d["removed"]), cfg)

        for v in views.values():
            st = files_state.get(f"rrd:{v['uri']}")
            if st and not st["mirror_link"] and not st["skipped"] and not st["removed"]:
                mirror_todo.append((loc, program, v, st["hash"], st["fails"]))

    if do_mirror and mirror_todo:
        try:
            folder = _mirror_folder_id()
        except RuntimeError as e:
            print(f"[ride-docs] MIRROR REFUSED: {e}")
            exit_code = 1
            folder = None
        if folder is None:
            if exit_code == 0:
                print(f"[ride-docs] mirror not configured ({FOLDER_ENV}/OAuth unset) — "
                      f"{len(mirror_todo)} file(s) listed but not mirrored (alerts/rows unaffected).")
        else:
            mirror_todo.sort(key=lambda t: (t[4], -int(t[2]["uri"])))   # fewest failures, newest first
            counts["mirrored"] = _mirror_pass(session, sheets, priv_id, today, mirror_todo,
                                              folder, max_bytes, max_mirror)

    print(f"[ride-docs] done — {counts['baseline']} baselined, {counts['new']} new, "
          f"{counts['changed']} changed, {counts['removed']} removed, "
          f"{counts['mirrored']} mirrored.")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
