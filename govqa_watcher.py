"""
govqa_watcher.py — Stream U: daily watch on EGLE's PUBLIC FOIA archive (GovQA) for
requests about Arbor Hills, and for the records EGLE released in answer to them.
See docs/decisions/059-govqa-archive-watch.md and govqa_client.py (the method).

WHY: EGLE answers other people's FOIA requests — the landfill operator's law firm,
neighbors, peer-landfill watchers — and posts what it released on a public archive.
E614007 (an 8/2026 request by outside counsel, closed 8/20/2026) put ~45 RRD
documents in front of us we had never seen. Nothing watched for the next one.

WHAT IT DOES (daily), in guarded phases — a failure in one phase is reported and the
next still runs, and the REPORT is always sent last (a `finally`), so an alert cannot
be lost to an exception after its rows were written:
  1. KEYWORD SWEEP  each configured keyword, ONE per search, newest first (Playwright,
     the handoff's §1C-bis method), stopping at the first page whose E-numbers are ALL
     already in the tab. A request that is new and passes the keyword's optional `require`
     phrase(s) is recorded and ALERTED (`new`); a keyword's very FIRST sweep reads every
     page up to `max_pages_per_term`, records what it finds silently (`baseline`) and then
     writes a `term:` marker (rows first, marker last: a crash re-baselines silently
     instead of alerting on history). Hitting the page cap on a first sweep writes a
     `partial` marker and asks for a CSV export ONCE. A `nomatch` request (returned by the
     site's OR search but failing `require`) is upgraded to matched if a term that matches
     it later finds it. A zero-row result is believed only with the grid's own "No data to
     display" marker; if EVERY keyword comes back empty while the tab holds requests, that
     is a structural error, not "no news".
  2. CSV DROP       an optional Drive folder (shared with the service account) where
     Trisha drops a gridView CSV exported in a real browser; runs AFTER the sweep, so
     anything genuinely new was already alerted by the sweep and the CSV only fills older
     history, silently. A file is identified by its Drive id AND content hash, so a file
     replaced in place is re-read; a CSV with no parseable rows is reported.
  3. RE-CHECK       every configured `watch_requests` number (an explicit watch also
     overrides a `nomatch`) plus every recorded matched request not yet in a terminal
     status, by E-number over a plain cookie session (no browser); a status change alerts.
     Unknown statuses count as OPEN. A truncated re-check list, or lookups that keep
     returning nothing, are reported.
  4. FILES          for every new or status-changed request the attachment list is recorded
     (`file-listed`, duplicate names keyed by occurrence) — not only for statuses this code
     recognises as "released": an unseen status must not hide a release. The request gets a
     `list-pending` marker in the SAME write as its `new`/`status` row and a `file-list-done`
     marker (carrying the rid) once its detail page was read, and every run retries the
     pending ones — so a failure, a spent time budget or a kill between the status row and
     the listing (the request is terminal and never re-checked) cannot lose the release. A
     listing that keeps failing gets `list-failed` strikes and is given up (`list-skipped`,
     reported once) after 5.
  5. STAGING        if `download_attachments` and a private staging folder are set, files are
     downloaded, hashed (SHA-256 + MD5) and uploaded under a CONTENT-ADDRESSED name
     (`<E-number>__<sha256[:16]>[.<ext>]` — the attachment's own name, which can name a
     resident, is kept only in the private Sheet and never goes into a Drive query or log),
     skipping any whose MD5 matches a configured "already held" folder.

HARD RULES (ADR 059) — pinned by tests/test_govqa.py:
  - NEVER PUBLISHES. Rows go to the PRIVATE Sheet (GSHEET_ID_PRIVATE), never the public
    case-file Sheet; attachments go only to a private staging folder that must not equal
    any other GOAUTH_*_FOLDER_ID; nothing here imports findings_feed / gen_findings_feed /
    any archiver. FOIA releases can contain residents' names and addresses, and items EGLE
    marked privileged. They route to Trisha's hand-curation (`dedupe-curate`) queue only.
  - Fails CLOSED: GSHEET_ID_PRIVATE unset -> refuse; equal to GSHEET_ID -> refuse; and —
    the check that works in CI, where GSHEET_ID is not even set — the target spreadsheet
    already holding a public case-file tab (New Documents / Evidence by Risk / Measurements)
    -> refuse, before any write.
  - Recipients scoped VERBATIM (Trisha only); EMPTY = display-only, never the coalition
    list. A configured recipient whose report could not be SENT makes the run red.
  - stdout is a PUBLIC log: nothing prints request text or attachment names; every
    exception message is URL-scrubbed (govqa_client.scrub); googleapiclient's retry logger
    (which prints request URLs) is silenced; `main()` reports an uncaught error as class +
    scrubbed message only.

RESIDUAL (accepted, ADR 059): the one report email is sent after staging, so a HARD kill
(SIGKILL / the job timeout) during a long staging phase could still lose it (staging ships
off and is time-budgeted; an exception never can); released files are listed when a request
is new or changes status; attachments posted later to an already-terminal request are not seen; requests
baselined by a first sweep or a CSV are history and are not listed.

ACCURACY: alerts are source-labeled listing events ("EGLE's archive shows request X with
status Y"); file contents are never read by this job.

GATED on govqa.enabled (a brand-new external source; ships false). `--probe` runs one real
lookup and one real keyword search regardless of the flag and writes nothing.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import tempfile
import time
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
_MAX_FILE_FAILS = 5
_MAX_LIST_FAILS = 5
_EMAIL_LIST_CAP = 25
_EXCERPT_CHARS = 300
_CIRCUIT_BREAKER = 3                # consecutive structural failures that abort a phase
_DEFAULT_TIME_BUDGET_MIN = 40
_PUBLIC_TABS = ("TAB_NEW", "TAB_EVIDENCE", "TAB_MEASUREMENTS")   # sheet_writer names of PUBLIC case-file tabs

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
    """The PRIVATE Sheet id, or None (this run must not write): unset -> None; equal to the
    public GSHEET_ID -> None (only meaningful locally — the workflow does not receive the
    public id; see `looks_like_public_sheet` for the check that works in CI)."""
    priv = (os.environ.get("GSHEET_ID_PRIVATE") or "").strip()
    if not priv:
        return None
    if priv == (os.environ.get("GSHEET_ID") or "").strip():
        return None
    return priv


def looks_like_public_sheet(service, sheet_id: str) -> bool:
    """True when the spreadsheet already holds any of the PUBLIC case-file's tabs (New
    Documents / Evidence by Risk / Measurements). A GSHEET_ID_PRIVATE secret that was
    copy-pasted from the public id would pass the env comparison in CI (the workflow never
    sets GSHEET_ID), but it cannot pass THIS: the public Sheet has those tabs, the private
    one never does. Runs before any write, so the tab helpers never touch a public Sheet."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute(num_retries=dc.GOOGLE_API_NUM_RETRIES)
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    return bool(titles & {getattr(sw, name) for name in _PUBLIC_TABS})


def _staging_folder_id(env_name: str) -> str | None:
    """The private staging folder id, or None when staging isn't configured. Raises
    RuntimeError if it equals ANY other GOAUTH_*_FOLDER_ID (several are public-facing
    mirrors) or the public PDF archive's GDRIVE_FOLDER_ID."""
    if not ac.is_configured(env_name):
        return None
    mine = ac.folder_id(env_name)
    for name, value in os.environ.items():
        if (name != env_name and (re.fullmatch(r"GOAUTH_.*_FOLDER_ID", name) or name == "GDRIVE_FOLDER_ID")
                and value == mine):
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
    """A row the site returned for `kw` counts as a match when every `require` phrase
    appears in its summary (no `require` = trust the site's own search, which also matches
    text this grid doesn't show)."""
    return all(gq.phrase_in(summary, r) for r in kw["require"])


def bare_term(term: str) -> str:
    """The search term without its surrounding double quotes: the site treats a quoted term
    as an exact phrase, but the request text never contains the quote marks."""
    return term.strip().strip('"').strip()


def csv_row_matches(keywords: list[dict], summary: str) -> bool:
    """A CSV row (no site search behind it) matches when ANY keyword's bare term appears in
    the summary and that keyword's `require` holds."""
    return any(gq.phrase_in(summary, bare_term(kw["term"])) and keyword_matches(kw, summary) for kw in keywords)


# ---------------------------------------------------------------------------
# State (pure)
# ---------------------------------------------------------------------------


def _cell(row: list, idx: int) -> str:
    return row[idx] if idx < len(row) else ""


_FILE_KEY = re.compile(r"^file:(E\d{6}-\d{6}):(.*)#(\d+)$", re.S)


def file_key(request_no: str, name: str, occurrence: int = 1) -> str:
    """`file:<E>:<name>#<n>` — n is the occurrence of that name on the request (a request can
    carry several files called `image001.png`). ALWAYS suffixed, so a name that itself ends in
    `#3` can never be mistaken for an occurrence marker."""
    return f"file:{request_no}:{name}#{occurrence}"


def build_state(rows: list[list]) -> dict:
    """Fold the append-only tab into state (the LAST row for a key wins):
      requests[E]  {status, created, closed, rid, matched, terms}
      files[key]   {request, name, occ, state: listed|staged|held|skipped, fails}
      list_pending requests with a `list-pending` marker and no `file-list-done`/`list-skipped` since
      list_fails   {E: consecutive `list-failed` strikes since its last `list-pending`}
      terms        keywords with a first-sweep marker (baseline OR partial)
      csvs         '<Drive id>:<content hash>' of CSV files already ingested
    Tolerates rows Sheets returned with trailing empty cells stripped."""
    st = {"requests": {}, "files": {}, "terms": set(), "csvs": set(), "list_pending": set(), "list_fails": {}}
    for r in rows:
        key, event = _cell(r, C_KEY), _cell(r, C_EVENT)
        if key.startswith("term:"):
            st["terms"].add(key[5:])
        elif key.startswith("csv:"):
            st["csvs"].add(key[4:])
        elif key.startswith("list:"):
            no = key[5:]
            if event == "list-pending":
                st["list_pending"].add(no)
                st["list_fails"].pop(no, None)                       # a fresh release event starts a fresh count
            elif event == "list-failed":
                st["list_fails"][no] = st["list_fails"].get(no, 0) + 1
            elif event in ("file-list-done", "list-skipped"):
                st["list_pending"].discard(no)
                st["list_fails"].pop(no, None)
            if _cell(r, C_RID) and no in st["requests"] and not st["requests"][no]["rid"]:
                st["requests"][no]["rid"] = _cell(r, C_RID)          # a rid found by number is kept for next time
        elif key.startswith("file:"):
            m = _FILE_KEY.match(key)
            if not m:
                continue
            f = st["files"].setdefault(key, {"request": m.group(1), "name": m.group(2), "occ": int(m.group(3)),
                                             "state": "listed", "fails": 0})
            if event == "file-listed":
                if f["state"] not in ("staged", "held"):
                    f["state"] = "listed"
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


_KNOWN_EXTS = frozenset("pdf doc docx xls xlsx csv txt rtf ppt pptx png jpg jpeg gif tif tiff zip msg eml htm html xml json".split())


def staged_name(request_no: str, sha256: str, file_name: str) -> str:
    """The Drive name for a staged file: '<E-number>__<sha256[:16]>[.<ext>]'. Content-
    addressed, so two files can never collide, identical content dedupes, and the
    attachment's own name (which can name a resident) never enters a Drive query or a log."""
    ext = re.sub(r"[^A-Za-z0-9]", "", file_name.rpartition(".")[2]).lower() if "." in file_name else ""
    ext = ext if ext in _KNOWN_EXTS else ""          # never a slice of the attachment's own (resident-derived) name
    no = request_no if gq.E_NUMBER.match(request_no or "") else "E000000-000000"
    return f"{no}__{sha256[:16]}" + (f".{ext}" if ext else "")


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


def _capped(items: list, render, what: str) -> str:
    shown = items[:_EMAIL_LIST_CAP]
    more = f"\n(+ {len(items) - len(shown)} more {what} — see the GovQA Archive Watch tab.)" if len(items) > len(shown) else ""
    return "\n".join(render(x) for x in shown) + more


def format_report(new: list[dict], changed: list[tuple[dict, str]], staged: list[dict],
                  problems: list[str]) -> str:
    """The one email per run. Sections appear only when non-empty. Pure."""
    parts = []
    if new:
        parts.append(f"NEW requests matching your keywords ({len(new)}):\n\n" + _capped(new, _describe, "request(s)"))
    if changed:
        def line(pair):
            r, old = pair
            return (f"- {r['request_no']}: {old or '(none)'} → {r['status']}"
                    + (f" (closed {r['closed']})" if r.get("closed") else "")
                    + (f" | {r['n_files']} file(s) listed" if r.get("n_files") else "")
                    + f"\n    “{excerpt(r['summary'])}”")
        parts.append(f"STATUS CHANGES ({len(changed)}):\n\n" + _capped(changed, line, "change(s)"))
    if staged:
        parts.append(f"FILES STAGED to the private folder ({len(staged)}):\n\n" + _capped(
            staged, lambda s: f"- {s['request']}: {s['size']:,} bytes; SHA-256 {s['sha256'][:16]}… "
                              "(file names are in the Sheet)", "file(s)"))
    if problems:
        parts.append("NEEDS ATTENTION:\n\n" + "\n".join(f"- {p}" for p in problems))
    return "\n\n".join(parts) + "\n\n" + _NOTE + "\n"


def _send(recipients: list[str], subject: str, body_fn, cfg: dict) -> str:
    """Best-effort alert -> 'sent' | 'display-only' | 'failed'. `body_fn` runs INSIDE the
    guard so a formatting bug cannot escape. EMPTY recipients = display-only: send_email
    would fall back to the whole coalition list, so it is never called."""
    if not recipients:
        print(f"[govqa] display-only (no recipients): {subject}")
        return "display-only"
    try:
        body = body_fn()
        return "sent" if ea.send_email(subject, body, cfg, recipients=recipients) else "failed"
    except Exception as e:  # noqa: BLE001 — rows are already recorded
        print(f"[govqa] alert could not be prepared/sent (rows are recorded): {type(e).__name__}: {gq.scrub(e, 120)}")
        return "failed"


# ---------------------------------------------------------------------------
# Drive helpers (SA read for CSV drop + held MD5s)
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
    """`--probe`: one real by-E-number lookup + details, and one real keyword search with the
    browser. Reads only; runs regardless of the enabled flag."""
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
        status, n_files = row["status"], len(det["files"])
        print(f"[govqa] probe: lookup {known} -> status {status!r}, {n_files} file(s) (no browser)")
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


class Run:
    """One watcher run: config, the Sheet, state, and the things the final report needs."""

    def __init__(self, cfg, sheets, priv_id, state, make_grid, make_session, sleep, clock):
        g = cfg.get("govqa") or {}
        self.cfg, self.gcfg, self.sheets, self.priv_id, self.state = cfg, g, sheets, priv_id, state
        self.make_grid, self.make_session, self.sleep = make_grid, make_session, sleep
        self.keywords = _keywords(cfg)
        self.recipients = [r for r in (g.get("recipients") or []) if str(r or "").strip()]
        self.max_pages = int(g.get("max_pages_per_term", 12))
        self.max_rechecks = int(g.get("max_open_rechecks", 25))
        self.max_attempts = int(g.get("max_attempts", gq.DEFAULT_MAX_ATTEMPTS))
        self.do_download = bool(g.get("download_attachments", False))
        self.max_dl = int(g.get("max_downloads_per_run", 10))
        self.max_bytes = int(float(g.get("max_file_mb", 200)) * 1024 * 1024)
        self.deadline = clock() + float(g.get("time_budget_minutes", _DEFAULT_TIME_BUDGET_MIN)) * 60
        self.clock = clock
        self.today = _today()
        self.exit_code = 0
        self.problems: list[str] = []
        self.counts: dict[str, int] = defaultdict(int)
        self.new_reqs: list[dict] = []
        self.changed: list[tuple[dict, str]] = []
        self.staged: list[dict] = []
        self.fresh: set[str] = set()                 # requests discovered THIS run
        self.session = None
        # watch_requests: validated once; a malformed entry is a reported problem, never a crash
        self.watch = []
        for w in (str(x).strip() for x in (g.get("watch_requests") or [])):
            if gq.E_NUMBER.match(w):
                self.watch.append(w)
            else:
                self.problem(f"watch_requests entry {w!r} is not a full E-number (E######-MMDDYY) — ignored")

    # -- plumbing -----------------------------------------------------------
    def write(self, rows):
        if rows:
            sw.append_govqa_rows(self.sheets, self.priv_id, rows)

    def problem(self, msg: str, fatal: bool = True):
        self.problems.append(msg)
        if fatal:
            self.exit_code = 1

    def out_of_time(self) -> bool:
        return self.clock() > self.deadline

    def get_session(self):
        if self.session is None:
            self.session = self.make_session()
        return self.session

    def guard(self, name: str, fn):
        """Run one phase; any exception becomes a reported problem (class + scrubbed text) and
        the run moves on — the final report is still sent."""
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"[govqa] {name} phase failed: {type(e).__name__}: {gq.scrub(e, 120)}")
            self.problem(f"{name} phase failed: {type(e).__name__}: {gq.scrub(e, 150)}")

    def backoff(self, fn, what, on_retry=None):
        return gq.with_backoff(fn, what, max_attempts=self.max_attempts, sleep=self.sleep, on_retry=on_retry,
                               should_stop=self.out_of_time)

    def list_pending_row(self, no: str) -> list:
        """The marker that says this request's released files still have to be listed. Written in
        the SAME append as the row that made the request 'released', so no crash can separate them."""
        self.state["list_pending"].add(no)
        return _row(self.today, f"list:{no}", "list-pending", note="released — attachment list to be read")

    # -- phase 1: keyword sweep ---------------------------------------------
    def phase_sweep(self):
        if not self.keywords:
            return
        try:
            grid_cm = self.make_grid()
            grid = grid_cm.__enter__()
        except Exception as e:  # noqa: BLE001 — a missing/failed browser: loud, but the by-number phases still run
            print(f"[govqa] keyword sweep unavailable: {type(e).__name__}: {gq.scrub(e, 120)}")
            self.problem(f"keyword sweep unavailable: {type(e).__name__}: {gq.scrub(e, 150)}")
            return
        found: dict[str, dict] = {}
        partial_terms, swept_ok, empties, structural_in_a_row = [], [], 0, 0
        partial_why: dict[str, str] = {}
        known_terms = {t for rq in self.state["requests"].values() if rq["matched"] for t in rq["terms"].split(";")}
        try:
            for kw in self.keywords:
                if self.out_of_time():
                    self.problem("time budget reached during the keyword sweep — remaining keywords skipped")
                    break
                term = kw["term"]
                first = term not in self.state["terms"]
                known = self.state["requests"]
                is_known = (lambda n: False) if first else (lambda n: n in known)   # a first sweep reads pages, up to the cap
                try:
                    res = self.backoff(lambda: gq.sweep_term(grid, term, is_known, self.max_pages),
                                       f"sweep {term!r}", on_retry=grid.restart)
                except Exception as e:  # noqa: BLE001 — one keyword must never cost the others' finds
                    print(f"[govqa] STRUCTURAL: {type(e).__name__}: {gq.scrub(e)}")
                    self.problem(f"keyword {term!r}: {type(e).__name__}: {gq.scrub(e)}")
                    structural_in_a_row += 1
                    if structural_in_a_row >= _CIRCUIT_BREAKER:
                        self.problem(f"circuit breaker: {structural_in_a_row} keywords in a row failed — sweep aborted")
                        break
                    continue
                structural_in_a_row = 0
                print(f"[govqa] keyword {term!r}: {len(res.rows)} row(s) over {res.pages_read}/{res.total_pages} "
                      f"page(s) of {res.total_items} item(s){' — stopped at a fully-known page' if res.stopped_on_known else ''}.")
                if res.total_items == 0:
                    empties += 1
                    if term in known_terms:
                        self.problem(f"keyword {term!r} returned zero items although it matched recorded requests before — "
                                     "the site may have stopped honouring the query (e.g. quoted phrases)", fatal=False)
                for r in res.rows:
                    entry = found.setdefault(r["request_no"], {"row": r, "matched_by": [], "first": True})
                    if keyword_matches(kw, r["summary"]):
                        entry["matched_by"].append(term)
                    if not first:
                        entry["first"] = False          # any already-baselined term makes it alertable
                if res.overflow:
                    self.problem(f"keyword {term!r} has {res.total_items} items across {res.total_pages} pages — more than "
                                 f"max_pages_per_term={self.max_pages} without reaching a fully-known page. Export the grid to "
                                 "CSV in a real browser and drop it in the CSV folder, then rerun.")
                    print(f"[govqa] NEEDS CSV EXPORT: keyword {term!r} ({res.total_items} items, {res.total_pages} pages).")
                    if first:
                        partial_terms.append(term)      # ask ONCE; what was read is recorded below
                        partial_why[term] = (f"first sweep stopped at max_pages_per_term={self.max_pages}: older history NOT read "
                                             "— export a CSV to backfill it")
                elif first and res.pager_missing:
                    # a full page of rows and no pager text: older pages may exist that were never read
                    self.problem(f"keyword {term!r} returned a full page of {gq.GRID_PAGE_SIZE} rows with no pager text, so "
                                 "the first sweep cannot tell whether older pages exist. If it really has exactly that many "
                                 "results, ignore this; otherwise export the grid to CSV in a real browser and drop it in the "
                                 "CSV folder.", fatal=False)
                    partial_terms.append(term)
                    partial_why[term] = ("first sweep read a full page of rows with no pager text, so whether older pages exist "
                                         "is unknown — export a CSV if the term has more than that many results")
                else:
                    swept_ok.append(term)
        finally:
            try:
                grid_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

        # Sanity: EVERY keyword empty is a broken read, not "no news" — with requests on record, and also on the
        # activation run (writing every `term:` marker with no requests would make the next real run alert the
        # whole history as new).
        if self.keywords and empties == len(self.keywords):
            self.problem("every keyword returned zero rows — the archive, its markup or the query syntax may have changed "
                         "(run the workflow with probe=true); nothing was recorded and no keyword marker was written")
            return

        rows = []
        for no, entry in found.items():
            prev = self.state["requests"].get(no)
            if prev is not None and (prev["matched"] or not entry["matched_by"]):
                continue                                 # known, and nothing to upgrade
            r = entry["row"]
            if prev is None:
                self.fresh.add(no)
            if not entry["matched_by"]:
                rows.append(_row(self.today, no, "nomatch", created=r["created"], status=r["status"], rid=r["rid"] or "",
                                 note="site search hit without the required phrase"))
                self.state["requests"][no] = {"status": r["status"], "created": r["created"], "closed": "",
                                              "rid": r["rid"] or "", "matched": False, "terms": ""}
                self.counts["nomatch"] += 1
                continue
            event = "baseline" if entry["first"] else "new"
            terms = ";".join(entry["matched_by"])
            rows.append(_row(self.today, no, event, created=r["created"], status=r["status"], terms=terms, rid=r["rid"] or "",
                             text=excerpt(r["summary"]),
                             note=("upgraded from no-match; " if prev is not None else "")
                                  + ("first sweep of this keyword (no alert)" if event == "baseline" else "new request")))
            self.state["requests"][no] = {"status": r["status"], "created": r["created"], "closed": "",
                                          "rid": r["rid"] or "", "matched": True, "terms": terms}
            self.counts[event] += 1
            if event == "new":
                self.new_reqs.append({"request_no": no, "status": r["status"], "created": r["created"],
                                      "summary": r["summary"], "terms": terms, "rid": r["rid"]})
                rows.append(self.list_pending_row(no))            # ANY new request: an unseen status must not hide a release
        self.write(rows)
        markers = ([_row(self.today, f"term:{t}", "baseline", note="keyword baselined")
                    for t in swept_ok if t not in self.state["terms"]]
                   + [_row(self.today, f"term:{t}", "partial", note=partial_why[t]) for t in partial_terms])
        self.write(markers)                              # markers LAST: a crash re-baselines silently
        self.state["terms"].update(swept_ok)
        self.state["terms"].update(partial_terms)

    # -- phase 2: CSV drop (after the sweep) ---------------------------------
    def phase_csv(self):
        csv_env = self.gcfg.get("csv_folder_env", CSV_ENV_DEFAULT)
        folder = os.environ.get(csv_env or "")
        if not folder:
            return
        drive = dc.drive_service()
        with tempfile.TemporaryDirectory() as tmp:
            for f in dc.list_files(drive, folder):
                if not f["name"].lower().endswith(".csv"):
                    continue
                path = dc.download_file(drive, f["id"], os.path.join(tmp, "in.csv"))
                with open(path, "rb") as fh:
                    data = fh.read()
                marker = f"{f['id']}:{hashlib.sha256(data).hexdigest()[:16]}"
                if marker in self.state["csvs"]:
                    continue
                parsed = gq.parse_gridview_csv(data)
                if not parsed:
                    self.problem("a CSV in the drop folder has no parseable rows (expected the gridView export's "
                                 "'Request Number, Create Date, Summary, Request Status' columns) — not ingested")
                    continue
                rows, n = [], 0
                for r in parsed:
                    if r["request_no"] in self.state["requests"] or not csv_row_matches(self.keywords, r["summary"]):
                        continue
                    rows.append(_row(self.today, r["request_no"], "baseline", created=r["created"], status=r["status"],
                                     terms="csv", text=excerpt(r["summary"]), note="ingested from a CSV export (history; no alert)"))
                    self.state["requests"][r["request_no"]] = {"status": r["status"], "created": r["created"], "closed": "",
                                                                "rid": "", "matched": True, "terms": "csv"}
                    self.fresh.add(r["request_no"])       # history: not re-checked (and so not alerted) in this same run
                    n += 1
                rows.append(_row(self.today, f"csv:{marker}", "ingested", note=f"{n} new request(s) of {len(parsed)} parsed"))
                self.write(rows)                         # requests first, the csv marker last
                self.state["csvs"].add(marker)
                self.counts["csv_ingested"] += n
                print(f"[govqa] CSV drop: ingested {n} new request(s) of {len(parsed)} silently.")

    # -- phase 3: re-check open + watched requests by number -----------------
    def phase_recheck(self):
        g_state = self.state["requests"]
        open_recorded = sorted((n for n, v in g_state.items() if v["matched"] and is_open(v["status"]) and n not in self.fresh
                                and n not in self.watch), reverse=True)
        chosen = open_recorded[:self.max_rechecks]
        if len(open_recorded) > len(chosen):
            self.problem(f"{len(open_recorded) - len(chosen)} open request(s) were not re-checked this run "
                         f"(max_open_rechecks={self.max_rechecks}); raise the cap if this persists", fatal=False)
        targets = list(dict.fromkeys(self.watch + chosen))
        if not targets:
            return
        session = self.get_session()
        nothing, structural_in_a_row = 0, 0
        for no in targets:
            if self.out_of_time():
                self.problem("time budget reached during the re-check — remaining requests skipped")
                break
            try:
                row = self.backoff(lambda: session.lookup(no), f"lookup {no}")
            except gq.GovqaStructuralError as e:
                print(f"[govqa] STRUCTURAL: {gq.scrub(e)}")
                self.problem(f"re-check {no}: {gq.scrub(e)}")
                structural_in_a_row += 1
                if structural_in_a_row >= _CIRCUIT_BREAKER:
                    self.problem(f"circuit breaker: {structural_in_a_row} lookups in a row failed — re-check aborted")
                    break
                continue
            structural_in_a_row = 0
            if row is None:
                nothing += 1
                if no in self.watch and no not in g_state:
                    self.problem(f"watch_requests entry {no} was not found in the archive — check the E-number and its "
                                 "date suffix (take it from the archive), or remove it until EGLE posts the request", fatal=False)
                continue
            prev = g_state.get(no)
            if prev is None or (not prev["matched"] and no in self.watch):
                self.write([_row(self.today, no, "baseline", created=row["created"], status=row["status"], terms="watch",
                                 rid=row["rid"] or "", text=excerpt(row["summary"]), note="watch-list request (no alert)")])
                g_state[no] = {"status": row["status"], "created": row["created"], "closed": "", "rid": row["rid"] or "",
                               "matched": True, "terms": "watch"}
                self.counts["baseline"] += 1
                continue
            if not prev["matched"]:
                continue
            if row["status"] != prev["status"]:
                rq = {"request_no": no, "status": row["status"], "created": row["created"], "summary": row["summary"],
                      "terms": prev["terms"], "rid": row["rid"] or prev["rid"], "closed": ""}
                old = prev["status"]
                out = [_row(self.today, no, "status", created=row["created"], status=row["status"], terms=prev["terms"],
                            rid=rq["rid"], text=excerpt(row["summary"]), note=f"status {old!r} -> {row['status']!r}")]
                out.append(self.list_pending_row(no))
                self.write(out)                                     # the status row and the pending marker: ONE append
                prev.update(status=row["status"], rid=rq["rid"])
                self.changed.append((rq, old))
                self.counts["status"] += 1
        if nothing >= 3 and nothing * 2 >= len(targets):
            self.problem(f"the archive returned nothing for {nothing} of {len(targets)} recorded/watched requests — "
                         "an outage or a markup change?")

    # -- phase 4: list the released files ------------------------------------
    def list_strike(self, no: str, why: str):
        """One failed listing attempt: a `list-failed` row (fatal problem this run); the 5th gives up
        (`list-skipped`, the request leaves the pending set) and says so once, non-fatally."""
        n = self.state["list_fails"].get(no, 0) + 1
        self.state["list_fails"][no] = n
        gave_up = n >= _MAX_LIST_FAILS
        self.write([_row(self.today, f"list:{no}", "list-skipped" if gave_up else "list-failed", note=f"attempt {n}: {why}"[:250])])
        if gave_up:
            self.state["list_pending"].discard(no)
            self.problem(f"{no}: gave up listing its released files after {n} failed attempts (list-skipped in the Sheet) — "
                         "look at the request by hand", fatal=False)
        else:
            self.problem(f"{no}: its released files could not be listed (attempt {n} of {_MAX_LIST_FAILS}; retried next run): {why}")

    def phase_list_files(self):
        """Read the detail page of every request with a `list-pending` marker — this run's new/
        changed requests AND any left over from an earlier run — and record its attachments."""
        in_report = {r["request_no"]: r for r in self.new_reqs}
        in_report.update({r["request_no"]: r for r, _old in self.changed})
        for no in sorted(self.state["list_pending"], reverse=True):
            if self.out_of_time():
                self.problem("time budget reached while listing files — remaining requests kept for the next run")
                break
            known = self.state["requests"].get(no) or {}
            rq = in_report.get(no) or {"request_no": no, "status": known.get("status", ""), "closed": known.get("closed", ""),
                                       "rid": known.get("rid", "")}
            session = self.get_session()
            rid = rq.get("rid") or known.get("rid")
            if not rid:                                       # the grid row had no details link: ask by number
                try:
                    row = self.backoff(lambda: session.lookup(no), f"lookup {no}")
                except gq.GovqaStructuralError as e:
                    print(f"[govqa] STRUCTURAL: {gq.scrub(e)}")
                    self.list_strike(no, f"lookup: {gq.scrub(e, 120)}")
                    continue
                rid = row["rid"] if row else None
                if rid:
                    rq["rid"] = rid
                    known["rid"] = rid
            if not rid:
                self.list_strike(no, "the archive shows no details link for it")
                continue
            try:
                det = self.backoff(lambda: session.details(rid, expect_reference=no), f"details {no}", on_retry=session.reset)
            except gq.GovqaStructuralError as e:
                print(f"[govqa] STRUCTURAL: {gq.scrub(e)}")
                self.list_strike(no, gq.scrub(e, 150))
                continue
            rq["closed"] = det.get("closed", "")
            rq["n_files"] = len(det["files"])
            seen: dict[str, int] = defaultdict(int)
            rows = []
            for f in det["files"]:
                seen[f["name"]] += 1
                key = file_key(no, f["name"], seen[f["name"]])
                if key not in self.state["files"]:
                    rows.append(_row(self.today, key, "file-listed", status=rq["status"], closed=rq["closed"],
                                     file_name=f["name"], note="attachment listed on the request's detail page"))
                    self.state["files"][key] = {"request": no, "name": f["name"], "occ": seen[f["name"]],
                                                "state": "listed", "fails": 0}
            n_listed = len(rows)
            rows.append(_row(self.today, f"list:{no}", "file-list-done", rid=rid,
                             note=f"{len(det['files'])} attachment(s) on the page"))
            self.write(rows)                                  # the files first, the done marker LAST (it also keeps the rid)
            self.state["list_pending"].discard(no)
            self.state["list_fails"].pop(no, None)
            self.counts["files_listed"] += n_listed

    # -- phase 5: stage the attachments (private folder) ----------------------
    def phase_stage(self):
        if not self.do_download:
            return
        staging_env = self.gcfg.get("staging_folder_env", STAGING_ENV_DEFAULT)
        try:
            folder = _staging_folder_id(staging_env)
        except RuntimeError as e:
            print(f"[govqa] STAGING REFUSED: {gq.scrub(e)}")
            self.problem(gq.scrub(e))
            return
        pending = [(k, f) for k, f in self.state["files"].items()
                   if f["state"] == "listed" and f["fails"] < _MAX_FILE_FAILS
                   and (self.state["requests"].get(f["request"]) or {}).get("rid")]      # a rid-less request cannot hog the batch
        if folder is None:
            n_pending = len(pending)
            if n_pending:
                print(f"[govqa] staging not configured ({staging_env}/OAuth unset) — {n_pending} listed file(s) not downloaded.")
            return
        if not pending:
            return
        pending.sort(key=lambda kf: (kf[1]["fails"], kf[0]))
        held: set[str] = set()
        held_ids = _held_folder_ids(self.gcfg)
        if held_ids:
            try:
                held = _held_md5s(dc.drive_service(), held_ids)
            except Exception as e:  # noqa: BLE001 — the hold check is an optimisation
                print(f"[govqa] held-folder listing failed (continuing without it): {type(e).__name__}: {gq.scrub(e, 100)}")
        drive_up = ac.oauth_drive_service()
        session = self.get_session()
        by_request: dict[str, list] = defaultdict(list)
        for k, f in pending[:self.max_dl]:
            by_request[f["request"]].append((k, f))
        with tempfile.TemporaryDirectory() as tmp:
            for request_no, items in by_request.items():
                rid = (self.state["requests"].get(request_no) or {}).get("rid")
                if not rid:
                    continue
                if self.out_of_time():
                    self.problem("time budget reached while staging files — remaining files left for the next run")
                    return
                try:
                    det = self.backoff(lambda: session.details(rid, expect_reference=request_no), f"details {request_no}",
                                       on_retry=session.reset)
                except gq.GovqaStructuralError as e:
                    self.problem(f"details {request_no}: {gq.scrub(e)}")
                    continue
                targets: dict[str, list[str]] = defaultdict(list)
                for f in det["files"]:
                    targets[f["name"]].append(f["target"])
                for key, f in items:
                    tl = targets.get(f["name"], [])
                    if f["occ"] > len(tl):
                        self._strike(key, f, "no longer listed on the detail page")
                        continue
                    self._stage_one(session, drive_up, folder, held, tmp, request_no, rid, key, f, tl[f["occ"] - 1], targets)

    def _stage_one(self, session, drive_up, folder, held, tmp, request_no, rid, key, f, target, targets):
        local = os.path.join(tmp, f"dl-{hashlib.sha256(key.encode()).hexdigest()[:12]}.bin")
        info = None
        try:
            for attempt in (1, 2):
                try:
                    info = session.download(target, local, self.max_bytes)
                    break
                except gq.GovqaTooLargeError:
                    raise
                except gq.GovqaFetchError:
                    if attempt == 2:
                        raise
                    # an expired session / transient answer: a FRESH session, reload the detail page, retry in-run
                    session.reset()
                    det = session.details(rid, expect_reference=request_no)
                    tl = [x["target"] for x in det["files"] if x["name"] == f["name"]]
                    if f["occ"] > len(tl):
                        raise
                    target = tl[f["occ"] - 1]
            if info is None:                              # unreachable (attempt 2 raises), but explicit
                raise gq.GovqaFetchError("download did not complete")
            if info["md5"] in held:
                self.write([_row(self.today, key, "file-held", file_name=f["name"], size=str(info["size"]), sha=info["sha256"],
                                 md5=info["md5"], note="identical (MD5) to a file in a configured held folder")])
                f["state"] = "held"
                self.counts["held"] += 1
                return
            name = staged_name(request_no, info["sha256"], f["name"])
            link = ac.upload_file(drive_up, local, name,
                                  "application/pdf" if f["name"].lower().endswith(".pdf") else "application/octet-stream", folder)
            self.write([_row(self.today, key, "file-staged", file_name=f["name"], size=str(info["size"]), sha=info["sha256"],
                             md5=info["md5"], link=link, note=f"staged to the private folder as {name}")])
            f["state"] = "staged"
            self.staged.append({"request": request_no, "size": info["size"], "sha256": info["sha256"]})
            self.counts["staged"] += 1
        except gq.GovqaTooLargeError as e:
            self.write([_row(self.today, key, "file-skipped", file_name=f["name"], note=f"not staged: {gq.scrub(e)}")])
            f["state"] = "skipped"
        except Exception as e:  # noqa: BLE001 — one bad file must not stop the rest
            # NOT the file name or the raw error: this line goes to a public Actions log.
            attempt_no = f["fails"] + 1
            print(f"[govqa] {request_no}: a file failed to stage ({type(e).__name__}, attempt {attempt_no}).")
            self._strike(key, f, f"attempt {attempt_no} failed: {type(e).__name__}: {gq.scrub(e)}")
        finally:
            try:
                os.remove(local)                          # never hold up to max_dl x max_file_mb on disk
            except OSError:
                pass

    def _strike(self, key: str, f: dict, note: str):
        f["fails"] += 1
        gave_up = f["fails"] >= _MAX_FILE_FAILS
        self.write([_row(self.today, key, "file-skipped" if gave_up else "file-failed", file_name=f["name"], note=note)])
        if gave_up:
            f["state"] = "skipped"
            self.problem(f"a file on {f['request']} gave up after {_MAX_FILE_FAILS} failed staging attempts "
                         "(recorded as file-skipped in the Sheet)", fatal=False)

    # -- report --------------------------------------------------------------
    def report(self):
        if not (self.new_reqs or self.changed or self.staged or self.problems):
            return
        bits = []
        if self.new_reqs:
            bits.append(f"{len(self.new_reqs)} new request(s)")
        if self.changed:
            bits.append(f"{len(self.changed)} status change(s)")
        if self.staged:
            bits.append(f"{len(self.staged)} file(s) staged")
        if self.problems and not bits:
            bits.append("needs attention")
        result = _send(self.recipients, "[GovQA] " + ", ".join(bits) + " — EGLE FOIA archive",
                       lambda: format_report(self.new_reqs, self.changed, self.staged, self.problems), self.cfg)
        if result == "failed":
            print("[govqa] the report could not be sent — failing the run so it is not silent")
            self.exit_code = 1


def run(argv: list[str] | None = None, make_grid=None, make_session=None, sleep=None, clock=None) -> int:
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

    sheets = dc.sheets_service()
    if looks_like_public_sheet(sheets, priv_id):
        print("[govqa] the GSHEET_ID_PRIVATE spreadsheet holds public case-file tabs — refusing to run "
              "(check the secret: it looks like the PUBLIC Sheet).")
        return 1
    sw.ensure_govqa_tabs(sheets, priv_id)
    state = build_state(sw.read_govqa_rows(sheets, priv_id))
    r = Run(cfg, sheets, priv_id, state, make_grid or gq.PlaywrightGrid, make_session or gq.ArchiveSession,
            sleep or time.sleep, clock or time.monotonic)
    try:
        r.guard("keyword sweep", r.phase_sweep)          # newest requests first, so genuinely new ones alert...
        r.guard("CSV drop", r.phase_csv)                 # ...then the CSV fills OLDER history silently
        r.guard("re-check", r.phase_recheck)
        r.guard("file listing", r.phase_list_files)
        r.guard("staging", r.phase_stage)
    finally:
        r.report()                                       # ALWAYS: rows are written, so the alert must go out
    c = r.counts
    print(f"[govqa] done — {c['baseline']} baselined, {c['new']} new, {c['nomatch']} no-match, {c['status']} status "
          f"change(s), {c['files_listed']} file(s) listed, {c['staged']} staged, {c['csv_ingested']} from CSV.")
    return r.exit_code


def main() -> int:
    """Entry point. An unhandled exception is reported as its class + scrubbed message only
    (a raw traceback can carry a URL into a WORLD-READABLE Actions log), and googleapiclient's
    retry logger — which prints the full request URL, Drive query included — is silenced.
    Exit 1 either way, so a real failure is still red."""
    logging.getLogger("googleapiclient").setLevel(logging.CRITICAL)
    logging.getLogger("googleapiclient.http").setLevel(logging.CRITICAL)
    try:
        return run()
    except Exception as e:  # noqa: BLE001
        print(f"[govqa] FAILED: {type(e).__name__}: {gq.scrub(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
