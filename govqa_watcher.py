"""
govqa_watcher.py — Stream U: daily watch on EGLE's PUBLIC FOIA archive (GovQA) for
requests about Arbor Hills, and for the records EGLE released in answer to them.
See docs/decisions/059-govqa-archive-watch.md and govqa_client.py (the method).

WHY: EGLE answers other people's FOIA requests — the landfill operator's law firm,
neighbors, peer-landfill watchers — and posts what it released on a public archive.
E614007 (a 8/2026 request by defense counsel, released 8/20/2026) put ~45 RRD
documents in front of us we had never seen. Nothing watched for the next one.

WHAT IT DOES (daily):
  1. KEYWORD SWEEP  each configured keyword, ONE per search, newest first, stopping
     the moment a page holds a request already in the tab (incremental). A request
     whose number is new and that passes the keyword's `require` phrase(s) is
     recorded and ALERTED (`new`); a keyword's very first sweep records its rows
     silently (`baseline`) and then writes a `term:` marker (rows first, marker last:
     a crash re-baselines silently instead of alerting on history). A row that came
     back from the site's OR-of-tokens search but lacks the required phrase is
     recorded `nomatch` (so the incremental stop still works) with no excerpt.
  2. CSV DROP       an optional Drive folder (shared with the service account) where
     Trisha drops a gridView CSV exported in a real browser; new E-numbers matching
     the keywords are ingested silently. The fallback for a backfill too big to
     scrape — the run STOPS AND ASKS for one rather than loop on timeouts.
  3. RE-CHECK       every configured `watch_requests` number plus every recorded
     request that is not yet in a terminal status, by E-number over a plain cookie
     session (no browser): a status change alerts (so a law firm's release is caught
     the day it posts). Unknown statuses count as OPEN (fail-safe).
  4. FILES          for a new or status-changed request that says records were
     released, the attachment list is recorded (`file-listed`); if enabled and a
     private staging folder is configured, the files are downloaded, hashed
     (SHA-256 + MD5) and copied there (`file-staged`), skipping any whose MD5 matches
     a configured "already held" folder (`file-held`).

HARD RULES (ADR 059) — pinned by tests/test_govqa.py:
  - NEVER PUBLISHES. Rows go to the PRIVATE Sheet (GSHEET_ID_PRIVATE), never the
    public case-file Sheet (GSHEET_ID); attachments go only to a private staging
    folder that must not equal any other GOAUTH_*_FOLDER_ID; nothing here imports
    findings_feed / gen_findings_feed / any archiver. FOIA releases can contain
    residents' names and addresses, and items EGLE marked privileged. They route to
    Trisha's hand-curation (`dedupe-curate`) queue only.
  - Recipients scoped VERBATIM (Trisha only); EMPTY = display-only, never the
    coalition alert list. Fails CLOSED when GSHEET_ID_PRIVATE is unset or equals
    GSHEET_ID.

ACCURACY: alerts are source-labeled listing events ("EGLE's archive shows request X
with status Y"); they say nothing about what a release contains — file contents are
never read by this job.

GATED on govqa.enabled (a brand-new external source; ships false). `--probe` runs
one real lookup and one real keyword search regardless of the flag and writes nothing.
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
import govqa_client as gq
import sheet_writer as sw
from config_loader import load_config

STAGING_ENV_DEFAULT = "GOAUTH_GOVQA_STAGING_FOLDER_ID"
CSV_ENV_DEFAULT = "GOAUTH_GOVQA_CSV_FOLDER_ID"
_MAX_FILE_FAILS = 3
_EMAIL_LIST_CAP = 25
_EXCERPT_CHARS = 300

# Column indexes of sw.GOVQA_HEADERS.
(C_DATE, C_KEY, C_EVENT, C_CREATED, C_CLOSED, C_STATUS, C_TERMS, C_RID, C_EXCERPT,
 C_FILE, C_SIZE, C_SHA, C_MD5, C_LINK, C_NOTE, C_CHECKED) = range(16)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    if not (cfg.get("govqa") or {}).get("enabled"):
        return False, "govqa.enabled is false — skipping (no-op)."
    return True, ""


def _private_sheet_id() -> str | None:
    """The PRIVATE Sheet id, or None (this run must not write): unset -> None; equal
    to the public GSHEET_ID -> None, so a misconfigured secret can never route other
    people's request text onto the public Sheet."""
    priv = (os.environ.get("GSHEET_ID_PRIVATE") or "").strip()
    if not priv:
        return None
    if priv == (os.environ.get("GSHEET_ID") or "").strip():
        return None
    return priv


def _staging_folder_id(env_name: str) -> str | None:
    """The private staging folder id, or None when staging isn't configured. Raises
    RuntimeError if it equals ANY other GOAUTH_*_FOLDER_ID — several of those are
    public-facing mirrors."""
    if not ac.is_configured(env_name):
        return None
    mine = ac.folder_id(env_name)
    for name, value in os.environ.items():
        if name != env_name and re.fullmatch(r"GOAUTH_.*_FOLDER_ID", name) and value == mine:
            raise RuntimeError(f"{env_name} equals {name} — refusing to stage GovQA files into another "
                               "mirror's folder (staging must be its own PRIVATE folder)")
    return mine


def _keywords(cfg: dict) -> list[dict]:
    """[{term, require:[phrases]}] from config; a bare string is a term with no
    `require`. Blank terms are dropped."""
    out = []
    for k in (cfg.get("govqa") or {}).get("keywords") or []:
        if isinstance(k, str):
            k = {"term": k}
        term = str(k.get("term", "")).strip()
        if term:
            out.append({"term": term, "require": [str(r) for r in (k.get("require") or [])]})
    return out


def keyword_matches(kw: dict, summary: str) -> bool:
    """A row the site returned for `kw` counts as a match when every `require`
    phrase appears in its summary (no `require` = trust the site's own search,
    which also matches text this grid doesn't show)."""
    return all(gq.phrase_in(summary, r) for r in kw["require"])


def bare_term(term: str) -> str:
    """The search term without its surrounding double quotes: the site treats a quoted
    term as an exact phrase, but the request text never contains the quote marks."""
    return term.strip().strip('"').strip()


def csv_row_matches(keywords: list[dict], summary: str) -> bool:
    """A CSV row (no site search behind it) matches when ANY keyword's bare term appears
    in the summary and that keyword's `require` holds."""
    return any(gq.phrase_in(summary, bare_term(kw["term"])) and keyword_matches(kw, summary) for kw in keywords)


# ---------------------------------------------------------------------------
# State (pure)
# ---------------------------------------------------------------------------


def _cell(row: list, idx: int) -> str:
    return row[idx] if idx < len(row) else ""


def build_state(rows: list[list]) -> dict:
    """Fold the append-only tab into state (the LAST row for a key wins):
      requests[E]  {status, created, closed, rid, matched, terms, released_seen}
      files[key]   {request, name, state: listed|staged|held|skipped, fails}
      terms        set of keywords with a first-sweep baseline marker
      csvs         set of CSV Drive file ids already ingested
    """
    st = {"requests": {}, "files": {}, "terms": set(), "csvs": set()}
    for r in rows:
        key, event = _cell(r, C_KEY), _cell(r, C_EVENT)
        if key.startswith("term:"):
            st["terms"].add(key[5:])
        elif key.startswith("csv:"):
            st["csvs"].add(key[4:])
        elif key.startswith("file:"):
            _, request, name = key.split(":", 2) if key.count(":") >= 2 else ("file", "", key[5:])
            f = st["files"].setdefault(key, {"request": request, "name": name, "state": "listed", "fails": 0})
            if event == "file-listed":
                f["state"] = "listed" if f["state"] not in ("staged", "held") else f["state"]
            elif event == "file-staged":
                f["state"], f["fails"] = "staged", 0
            elif event == "file-held":
                f["state"] = "held"
            elif event == "file-skipped":
                f["state"] = "skipped"
            elif event == "file-failed":
                f["fails"] += 1
        elif re.fullmatch(r"E\d{6}-\d{6}", key):
            rq = st["requests"].setdefault(key, {"status": "", "created": "", "closed": "", "rid": "",
                                                  "matched": False, "terms": ""})
            if event == "nomatch":
                continue          # keeps matched=False unless an earlier row already matched it
            if event in ("baseline", "new"):
                rq.update(status=_cell(r, C_STATUS), created=_cell(r, C_CREATED), closed=_cell(r, C_CLOSED),
                          rid=_cell(r, C_RID) or rq["rid"], matched=True, terms=_cell(r, C_TERMS))
            elif event == "status":
                rq.update(status=_cell(r, C_STATUS), closed=_cell(r, C_CLOSED) or rq["closed"],
                          rid=_cell(r, C_RID) or rq["rid"])
    return st


def is_open(status: str) -> bool:
    return not gq.is_terminal(status)


def excerpt(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= _EXCERPT_CHARS else t[:_EXCERPT_CHARS - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Rows + alert copy (pure)
# ---------------------------------------------------------------------------


def _row(today: str, key: str, event: str, *, created="", closed="", status="", terms="", rid="",
         text="", file_name="", size="", sha="", md5="", link="", note="") -> list:
    return [today, key, event, created, closed, status, terms, rid, text, file_name, size, sha, md5,
            link, note, _now()]


def _describe(rq: dict) -> str:
    files = f" | {rq['n_files']} file(s) listed" if rq.get("n_files") else ""
    return (f"- {rq['request_no']} — {rq['status'] or 'status unknown'} | created {rq['created'] or '?'}"
            f"{files} | matched: {rq['terms'] or 'watch list'}\n    “{excerpt(rq['summary'])}”")


_NOTE = ("These are source-labeled listing events from EGLE's public FOIA archive "
         "(michiganegle.govqa.us). The archive shows a request and its status; this alert says "
         "nothing about what a release contains — file contents are not read by the monitor.\n\n"
         "PRIVATE: request text and attachment names can name residents and street addresses, and "
         "EGLE marks some items privileged. Nothing here is published; any files are staged only in "
         "your private folder. Publishing anything is a hand-curation decision (dedupe-curate).")


def format_report(new: list[dict], changed: list[tuple[dict, str]], staged: list[dict],
                  problems: list[str]) -> str:
    """The one email per run. Sections appear only when non-empty. Pure."""
    parts = []
    if new:
        shown = new[:_EMAIL_LIST_CAP]
        more = f"\n(+ {len(new) - len(shown)} more — see the GovQA Archive Watch tab.)" if len(new) > len(shown) else ""
        parts.append(f"NEW requests matching your keywords ({len(new)}):\n\n" + "\n".join(_describe(r) for r in shown) + more)
    if changed:
        lines = [f"- {r['request_no']}: {old or '(none)'} → {r['status']}"
                 + (f" (closed {r['closed']})" if r.get("closed") else "")
                 + (f" | {r['n_files']} file(s) listed" if r.get("n_files") else "")
                 + f"\n    “{excerpt(r['summary'])}”" for r, old in changed[:_EMAIL_LIST_CAP]]
        parts.append(f"STATUS CHANGES ({len(changed)}):\n\n" + "\n".join(lines))
    if staged:
        parts.append(f"FILES STAGED to the private folder ({len(staged)}):\n\n" +
                     "\n".join(f"- {s['request']}: {s['name']} ({s['size']:,} bytes; SHA-256 {s['sha256'][:16]}…)"
                               for s in staged[:_EMAIL_LIST_CAP]))
    if problems:
        parts.append("NEEDS ATTENTION:\n\n" + "\n".join(f"- {p}" for p in problems))
    return "\n\n".join(parts) + "\n\n" + _NOTE + "\n"


def _send(recipients: list[str], subject: str, body: str, cfg: dict) -> None:
    """Best-effort alert. EMPTY recipients = display-only: send_email would fall back
    to the whole coalition list on an empty list, so it is never called."""
    if not recipients:
        print(f"[govqa] display-only (no recipients): {subject}")
        return
    try:
        ea.send_email(subject, body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — rows are already recorded
        print(f"[govqa] alert email FAILED (rows are recorded): {subject}: {gq.scrub(e)}")


# ---------------------------------------------------------------------------
# Drive helpers (SA read for CSV drop + held MD5s; OAuth write for staging)
# ---------------------------------------------------------------------------


def _held_md5s(drive, folder_ids: list[str]) -> set[str]:
    """MD5 checksums of every file in the configured 'already held' folders (READ only,
    service account)."""
    out: set[str] = set()
    for fid in folder_ids:
        token = None
        while True:
            resp = drive.files().list(
                q=f"'{fid}' in parents and trashed = false", fields="nextPageToken, files(md5Checksum)",
                pageSize=1000, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute(num_retries=dc.GOOGLE_API_NUM_RETRIES)
            out.update(f["md5Checksum"] for f in resp.get("files", []) if f.get("md5Checksum"))
            token = resp.get("nextPageToken")
            if not token:
                break
    return out


def _held_folder_ids(gcfg: dict) -> list[str]:
    return [os.environ[e] for e in (gcfg.get("held_folder_envs") or []) if os.environ.get(e)]


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def run_probe(cfg: dict, make_grid=None, make_session=None) -> int:
    """`--probe`: one real by-E-number lookup + details, and one real keyword search
    with the browser. Reads only; runs regardless of the enabled flag."""
    make_grid = make_grid or gq.PlaywrightGrid
    make_session = make_session or gq.ArchiveSession
    gcfg = cfg.get("govqa") or {}
    known = (gcfg.get("watch_requests") or ["E614007-080526"])[0]
    try:
        s = make_session()
        row = s.lookup(known)
        if row is None:
            print(f"[govqa] PROBE: lookup of {known} found nothing")
            return 1
        det = s.details(row["rid"]) if row.get("rid") else {"files": []}
        print(f"[govqa] probe: lookup {known} -> status {row['status']!r}, {len(det['files'])} file(s) (no browser)")
        kws = _keywords(cfg)
        term = kws[0]["term"] if kws else "Holloway"
        with make_grid() as grid:
            res = gq.sweep_term(grid, term, lambda n: False, max_pages=1)
        print(f"[govqa] probe: keyword {term!r} -> {res.total_items} item(s) over {res.total_pages} page(s) "
              f"(browser); newest {res.rows[0]['request_no'] if res.rows else 'none'}")
    except (gq.GovqaFetchError, gq.GovqaStructuralError) as e:
        print(f"[govqa] PROBE FAILED: {gq.scrub(e)}")
        return 1
    print("[govqa] PROBE OK — the archive is reachable by number (session) and by keyword (browser).")
    return 0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None, make_grid=None, make_session=None, sleep=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    if "--probe" in argv:
        return run_probe(cfg, make_grid, make_session)
    ok, reason = _should_run(cfg)
    if not ok:
        print(f"[govqa] {reason}")
        return 0
    priv_id = _private_sheet_id()
    if priv_id is None:
        print("[govqa] GSHEET_ID_PRIVATE is unset (or equals the public Sheet id) — refusing to run: "
              "other people's FOIA request text belongs ONLY on the private Sheet.")
        return 1

    make_grid = make_grid or gq.PlaywrightGrid
    make_session = make_session or gq.ArchiveSession
    sleep = sleep or __import__("time").sleep

    gcfg = cfg.get("govqa") or {}
    keywords = _keywords(cfg)
    recipients = list(gcfg.get("recipients") or [])
    max_pages = int(gcfg.get("max_pages_per_term", 12))
    max_rechecks = int(gcfg.get("max_open_rechecks", 25))
    max_attempts = int(gcfg.get("max_attempts", gq.DEFAULT_MAX_ATTEMPTS))
    watch_requests = [str(x) for x in (gcfg.get("watch_requests") or [])]
    do_download = bool(gcfg.get("download_attachments", False))
    max_dl = int(gcfg.get("max_downloads_per_run", 10))
    max_bytes = int(float(gcfg.get("max_file_mb", 200)) * 1024 * 1024)

    sheets = dc.sheets_service()
    sw.ensure_govqa_tabs(sheets, priv_id)
    state = build_state(sw.read_govqa_rows(sheets, priv_id))
    today = _today()
    exit_code = 0
    problems: list[str] = []
    counts = defaultdict(int)

    def write(rows):
        if rows:
            sw.append_govqa_rows(sheets, priv_id, rows)

    new_reqs: list[dict] = []          # for the alert (matched, non-baseline)
    changed: list[tuple[dict, str]] = []
    swept_ok_terms: list[str] = []

    # ---- 1. CSV drop (optional) -------------------------------------------------
    csv_env = gcfg.get("csv_folder_env", CSV_ENV_DEFAULT)
    csv_folder = os.environ.get(csv_env or "")
    if csv_folder:
        try:
            drive = dc.drive_service()
            with tempfile.TemporaryDirectory() as tmp:
                for f in dc.list_files(drive, csv_folder):
                    if not f["name"].lower().endswith(".csv") or f["id"] in state["csvs"]:
                        continue
                    path = dc.download_file(drive, f["id"], os.path.join(tmp, "in.csv"))
                    with open(path, "rb") as fh:
                        parsed = gq.parse_gridview_csv(fh.read())
                    rows = []
                    for r in parsed:
                        if r["request_no"] in state["requests"] or not csv_row_matches(keywords, r["summary"]):
                            continue
                        rows.append(_row(today, r["request_no"], "baseline", created=r["created"], status=r["status"],
                                         terms="csv", text=excerpt(r["summary"]), note=f"ingested from CSV {f['name']}"))
                        state["requests"][r["request_no"]] = {"status": r["status"], "created": r["created"], "closed": "",
                                                              "rid": "", "matched": True, "terms": "csv"}
                    rows.append(_row(today, f"csv:{f['id']}", "ingested", note=f"{f['name']}: {len(rows)} new request(s)"))
                    write(rows)                       # requests first, the csv marker last (same order as terms)
                    state["csvs"].add(f["id"])
                    counts["csv_ingested"] += len(rows) - 1
                    print(f"[govqa] CSV drop: ingested {len(rows) - 1} new request(s) silently.")
        except Exception as e:  # noqa: BLE001 — the CSV drop is a fallback; never sink the run
            print(f"[govqa] CSV drop skipped: {gq.scrub(e)}")
            problems.append(f"CSV drop folder unreadable: {gq.scrub(e, 150)}")
            exit_code = 1

    # ---- 2. keyword sweeps (one browser, one term at a time) ----------------------
    found: dict[str, dict] = {}        # request_no -> {row, terms:[...], first_time: bool}
    partial_terms: list[str] = []      # first sweep hit the page cap: history beyond it was NOT read
    if keywords:
        try:
            grid_cm = make_grid()
            grid = grid_cm.__enter__()
        except (gq.GovqaStructuralError, gq.GovqaFetchError) as e:
            print(f"[govqa] keyword sweep unavailable: {gq.scrub(e)}")
            problems.append(f"keyword sweep unavailable: {gq.scrub(e, 150)}")
            grid_cm = grid = None
            exit_code = 1
        if grid is not None:
            try:
                for kw in keywords:
                    term = kw["term"]
                    first = term not in state["terms"]
                    is_known = lambda n: n in state["requests"]  # noqa: E731
                    try:
                        res = gq.with_backoff(
                            lambda: gq.sweep_term(grid, term, is_known, max_pages), f"sweep {term!r}",
                            max_attempts=max_attempts, sleep=sleep, on_retry=grid.restart)
                    except gq.GovqaStructuralError as e:
                        print(f"[govqa] STRUCTURAL: {gq.scrub(e)}")
                        problems.append(f"keyword {term!r}: {gq.scrub(e)}")
                        exit_code = 1
                        continue
                    print(f"[govqa] keyword {term!r}: {len(res.rows)} row(s) over {res.pages_read}/{res.total_pages} "
                          f"page(s) of {res.total_items} item(s){' — stopped at a known request' if res.stopped_on_known else ''}.")
                    for r in res.rows:
                        entry = found.setdefault(r["request_no"], {"row": r, "terms": [], "matched_by": [], "first": True})
                        entry["terms"].append(term)
                        if keyword_matches(kw, r["summary"]):
                            entry["matched_by"].append(term)
                        if not first:
                            entry["first"] = False         # any already-baselined term makes it alertable
                    if res.overflow:
                        msg = (f"keyword {term!r} has {res.total_items} items across {res.total_pages} pages — "
                               f"more than max_pages_per_term={max_pages} without reaching a known request. Export the "
                               "grid to CSV in a real browser and drop it in the CSV folder, then rerun.")
                        print(f"[govqa] NEEDS CSV EXPORT: keyword {term!r} ({res.total_items} items, {res.total_pages} pages).")
                        problems.append(msg)
                        exit_code = 1
                        if first:
                            partial_terms.append(term)     # ask ONCE; the newest max_pages*10 rows are recorded
                    else:
                        swept_ok_terms.append(term)
            finally:
                try:
                    grid_cm.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass

    # record newly discovered requests (rows first; term markers only after)
    rows = []
    fresh: set[str] = set()                # discovered THIS run: their status was just read, nothing to re-check
    for no, entry in found.items():
        if no in state["requests"]:
            continue
        fresh.add(no)
        r = entry["row"]
        if not entry["matched_by"]:
            rows.append(_row(today, no, "nomatch", created=r["created"], status=r["status"], rid=r["rid"] or "",
                             terms=";".join(entry["terms"]), note="site search hit without the required phrase"))
            state["requests"][no] = {"status": r["status"], "created": r["created"], "closed": "", "rid": r["rid"] or "",
                                     "matched": False, "terms": ""}
            counts["nomatch"] += 1
            continue
        event = "baseline" if entry["first"] else "new"
        terms = ";".join(entry["matched_by"])
        rows.append(_row(today, no, event, created=r["created"], status=r["status"], terms=terms, rid=r["rid"] or "",
                         text=excerpt(r["summary"]),
                         note="first sweep of this keyword (no alert)" if event == "baseline" else "new request"))
        state["requests"][no] = {"status": r["status"], "created": r["created"], "closed": "", "rid": r["rid"] or "",
                                 "matched": True, "terms": terms}
        counts[event] += 1
        if event == "new":
            new_reqs.append({"request_no": no, "status": r["status"], "created": r["created"], "summary": r["summary"],
                             "terms": terms, "rid": r["rid"]})
    write(rows)
    write([_row(today, f"term:{t}", "baseline", note="keyword baselined") for t in swept_ok_terms
           if t not in state["terms"]]
          + [_row(today, f"term:{t}", "partial",
                  note=f"first sweep stopped at max_pages_per_term={max_pages}: older history NOT read — "
                       "export a CSV to backfill it") for t in partial_terms])
    state["terms"].update(swept_ok_terms)
    state["terms"].update(partial_terms)

    # ---- 3. re-check open + watched requests by number (no browser) -----------------
    session = None
    open_recorded = sorted((n for n, v in state["requests"].items()
                           if v["matched"] and is_open(v["status"]) and n not in fresh), reverse=True)
    targets = list(dict.fromkeys(watch_requests + open_recorded[:max_rechecks]))
    to_list_files: list[dict] = []     # requests whose released-file list we should record now
    if targets:
        session = make_session()
    for no in targets:
        try:
            row = gq.with_backoff(lambda: session.lookup(no), f"lookup {no}", max_attempts=max_attempts,
                                  sleep=sleep, on_retry=lambda: None)
        except gq.GovqaStructuralError as e:
            print(f"[govqa] STRUCTURAL: {gq.scrub(e)}")
            problems.append(f"re-check {no}: {gq.scrub(e)}")
            exit_code = 1
            continue
        if row is None:
            print(f"[govqa] {no}: not shown by the archive (skipped).")
            continue
        prev = state["requests"].get(no)
        if prev is None:                                            # a configured watch request, first sighting
            write([_row(today, no, "baseline", created=row["created"], status=row["status"], terms="watch", rid=row["rid"] or "",
                        text=excerpt(row["summary"]), note="watch-list request (no alert)")])
            state["requests"][no] = {"status": row["status"], "created": row["created"], "closed": "", "rid": row["rid"] or "",
                                     "matched": True, "terms": "watch"}
            counts["baseline"] += 1
            continue
        if not prev["matched"]:
            continue
        if row["status"] != prev["status"]:
            rq = {"request_no": no, "status": row["status"], "created": row["created"], "summary": row["summary"],
                  "terms": prev["terms"], "rid": row["rid"] or prev["rid"], "closed": ""}
            old = prev["status"]
            write([_row(today, no, "status", created=row["created"], status=row["status"], terms=prev["terms"],
                        rid=rq["rid"], text=excerpt(row["summary"]), note=f"status {old!r} -> {row['status']!r}")])
            prev.update(status=row["status"], rid=rq["rid"])
            changed.append((rq, old))
            counts["status"] += 1
            if gq.is_released(row["status"]):
                to_list_files.append(rq)
    for r in new_reqs:
        if gq.is_released(r["status"]):
            to_list_files.append(r)

    # ---- 4. list the released files (and record them) ---------------------------------
    for rq in to_list_files:
        if not rq.get("rid"):
            continue
        session = session or make_session()
        try:
            det = gq.with_backoff(lambda: session.details(rq["rid"]), f"details {rq['request_no']}",
                                  max_attempts=max_attempts, sleep=sleep, on_retry=lambda: None)
        except gq.GovqaStructuralError as e:
            print(f"[govqa] STRUCTURAL: {gq.scrub(e)}")
            problems.append(f"details {rq['request_no']}: {gq.scrub(e)}")
            exit_code = 1
            continue
        rq["closed"] = det.get("closed", "")
        rq["n_files"] = len(det["files"])
        rows = []
        for f in det["files"]:
            key = f"file:{rq['request_no']}:{f['name']}"
            if key not in state["files"]:
                rows.append(_row(today, key, "file-listed", status=rq["status"], file_name=f["name"],
                                 note="attachment listed on the request's detail page"))
                state["files"][key] = {"request": rq["request_no"], "name": f["name"], "state": "listed", "fails": 0}
        write(rows)
        counts["files_listed"] += len(rows)

    # ---- 5. stage the attachments (private folder), if configured -------------------------
    staged: list[dict] = []
    if do_download:
        staging_env = gcfg.get("staging_folder_env", STAGING_ENV_DEFAULT)
        try:
            folder = _staging_folder_id(staging_env)
        except RuntimeError as e:
            print(f"[govqa] STAGING REFUSED: {gq.scrub(e)}")
            problems.append(gq.scrub(e))
            exit_code = 1
            folder = None
        pending = [(k, f) for k, f in state["files"].items() if f["state"] == "listed" and f["fails"] < _MAX_FILE_FAILS]
        if folder is None:
            if pending and exit_code == 0:
                print(f"[govqa] staging not configured ({staging_env}/OAuth unset) — {len(pending)} listed file(s) not downloaded.")
        elif pending:
            pending.sort(key=lambda kf: (kf[1]["fails"], kf[0]), reverse=False)
            held = set()
            held_ids = _held_folder_ids(gcfg)
            if held_ids:
                try:
                    held = _held_md5s(dc.drive_service(), held_ids)
                except Exception as e:  # noqa: BLE001 — the hold check is an optimisation
                    print(f"[govqa] held-folder listing failed (continuing without it): {gq.scrub(e)}")
            drive_up = ac.oauth_drive_service()
            session = session or make_session()
            by_request: dict[str, list] = defaultdict(list)
            for k, f in pending[:max_dl]:
                by_request[f["request"]].append((k, f))
            with tempfile.TemporaryDirectory() as tmp:
                for request_no, items in by_request.items():
                    rid = (state["requests"].get(request_no) or {}).get("rid")
                    if not rid:
                        continue
                    try:
                        det = gq.with_backoff(lambda: session.details(rid), f"details {request_no}", max_attempts=max_attempts,
                                              sleep=sleep, on_retry=lambda: None)
                    except gq.GovqaStructuralError as e:
                        problems.append(f"details {request_no}: {gq.scrub(e)}")
                        exit_code = 1
                        continue
                    targets_by_name = {f["name"]: f["target"] for f in det["files"]}
                    for key, f in items:
                        target = targets_by_name.get(f["name"])
                        if target is None:
                            write([_row(today, key, "file-failed", file_name=f["name"], note="no longer listed on the detail page")])
                            f["fails"] += 1
                            continue
                        local = os.path.join(tmp, gq.safe_filename(request_no, f["name"]))
                        try:
                            info = session.download(target, local, max_bytes)
                            if info["md5"] in held:
                                write([_row(today, key, "file-held", file_name=f["name"], size=str(info["size"]),
                                            sha=info["sha256"], md5=info["md5"], note="identical (MD5) to a file in a configured held folder")])
                                f["state"] = "held"
                                counts["held"] += 1
                                continue
                            link = ac.upload_file(drive_up, local, gq.safe_filename(request_no, f["name"]),
                                                  "application/pdf" if f["name"].lower().endswith(".pdf") else "application/octet-stream",
                                                  folder)
                            write([_row(today, key, "file-staged", file_name=f["name"], size=str(info["size"]),
                                        sha=info["sha256"], md5=info["md5"], link=link, note="staged to the private folder")])
                            f["state"] = "staged"
                            staged.append({"request": request_no, "name": f["name"], "size": info["size"], "sha256": info["sha256"]})
                            counts["staged"] += 1
                        except ValueError as e:
                            write([_row(today, key, "file-skipped", file_name=f["name"], note=f"not staged: {gq.scrub(e)}")])
                            f["state"] = "skipped"
                        except Exception as e:  # noqa: BLE001 — one bad file must not stop the rest
                            f["fails"] += 1
                            event = "file-skipped" if f["fails"] >= _MAX_FILE_FAILS else "file-failed"
                            write([_row(today, key, event, file_name=f["name"], note=f"attempt {f['fails']} failed: {gq.scrub(e)}")])
                            # NOT the file name or the raw error: this line goes to a public Actions log.
                            print(f"[govqa] {request_no}: a file failed to stage ({type(e).__name__}, attempt {f['fails']}).")

    # ---- 6. one report per run ----------------------------------------------------------------
    if new_reqs or changed or staged or problems:
        subject_bits = []
        if new_reqs:
            subject_bits.append(f"{len(new_reqs)} new request(s)")
        if changed:
            subject_bits.append(f"{len(changed)} status change(s)")
        if staged:
            subject_bits.append(f"{len(staged)} file(s) staged")
        if problems and not subject_bits:
            subject_bits.append("needs attention")
        _send(recipients, "[GovQA] " + ", ".join(subject_bits) + " — EGLE FOIA archive",
              format_report(new_reqs, changed, staged, problems), cfg)

    print(f"[govqa] done — {counts['baseline']} baselined, {counts['new']} new, {counts['nomatch']} no-match, "
          f"{counts['status']} status change(s), {counts['files_listed']} file(s) listed, {counts['staged']} staged, "
          f"{counts['csv_ingested']} from CSV.")
    return exit_code


def main() -> int:
    """Entry point. An unhandled exception is reported as its class + scrubbed message
    only: a raw traceback can carry a URL (Azure blob links hold attachment file names;
    Drive API errors embed the query, which holds file names) into a WORLD-READABLE
    Actions log. Exit 1 either way, so a real failure is still red."""
    try:
        return run()
    except Exception as e:  # noqa: BLE001
        print(f"[govqa] FAILED: {type(e).__name__}: {gq.scrub(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
