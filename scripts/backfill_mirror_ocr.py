"""
backfill_mirror_ocr.py — ADR 069 one-time OCR backfill for the scan-only PDFs
the 2026-10-08 audit found already in the Drive mirror (392 / 2,103, worst in
N2688 at 36%).

The live upload path (`archive_client.upload_file()`, wired 2026-10-13) now
OCRs every new PDF going forward. This script fixes the backlog that was
mirrored BEFORE that fix existed.

  python scripts/backfill_mirror_ocr.py                  # DRY-RUN (default)
  python scripts/backfill_mirror_ocr.py --apply           # Trisha only, locally
  python scripts/backfill_mirror_ocr.py --apply --limit 50

**Source of truth: the Sheet's own archive tabs** (Archived PDFs, MMPC
Archived Files, CivicClerk Archived Files) — so this only ever touches files
THIS APP created (`archive_client`'s `drive.file` scope can't see anything
else). Hand-Curated Files is excluded on purpose (not app-created —
`drive.file` can't touch it); WDS Page Snapshots is excluded (HTML, not
PDF). **Ridge Wood Reports / Ridge Wood Revisions are ALSO excluded**
(decided 2026-10-13, after the first draft of this script): every one of
the 66 Ridge Wood reports is verified text-layer (ADR 016's own doc), so
there's nothing to fix there, and overwriting one would break the Content
Hash <-> Archive Link pairing ADR 066's provenance manifest relies on.

**DRY-RUN MAKES ZERO DRIVE API CALLS.** It reads the Sheet tabs (service-
account Sheets access only — not a Drive call) and reports candidate-row
counts per tab, cross-referenced against the resumable state file for how
many are already resolved. It canNOT report how many files actually need
OCR — that requires downloading and inspecting each one, which is exactly the
Drive call dry-run is built to never make. This is a narrower, more literal
reading than "counts only" might first suggest; see ADR 069 decision 9.

**--apply does the real work:**
  - Enumerates each tab's rows in a FIXED tab order (Archived PDFs first —
    the nSITE mirror, dominated by the N2688 WOI/HOV reports the audit
    flagged worst — then MMPC, then CivicClerk). This is a documented
    APPROXIMATION of the handoff's "multi-page N2688 first, then multi-page
    other groups, then 1-page" priority ask: true page-count / per-SRN
    sorting would need a download of the whole backlog just to order it,
    which conflicts with keeping every run's Drive-call volume bounded. See
    ADR 069 for the full reasoning.
  - For each not-yet-resolved candidate (per the state file): downloads the
    mirrored PDF and runs `ocr_util.image_only_pages()`. Already-searchable
    (empty set) is recorded `skip_has_text` and does NOT count against
    `--limit` — only an actual OCR attempt does.
  - For a file with image-only pages: `ocr_util.ensure_searchable()` on a
    temp copy (never raises — fail-open; on failure records `ocr_failed` and
    leaves the file untouched in Drive). The set of image-only pages is
    re-checked on the result: if it DIDN'T shrink at all (some filings carry
    a photo-log page with nothing printed on it — unrecoverable no matter
    how many times OCR runs), no Drive write happens (`ocr_incomplete`) —
    but if it shrank PARTIALLY (other pages gained real text even though one
    stays unreadable) or fully, the file IS written back (`done_partial` /
    `done`): a partial win is still a real win, and the first version of
    this script threw those away. On success: pins the CURRENT head revision
    (`archive_client.pin_head_revision()` — Drive purges a non-head binary-
    file revision ~30 days after new content lands unless pinned, so without
    this the "old version stays in revision history" guarantee would quietly
    expire), overwrites the file's content IN PLACE
    (`archive_client.update_file_content()`, SAME file id — never
    `files().create`), and re-downloads it to verify the file id didn't
    change and record whether it's now fully fixed or still partial.
  - `--limit` (default 100) caps ACTUAL OCR attempts per run, not rows
    scanned. `_MAX_SCAN_PER_RUN` separately bounds how many already-clean
    rows get downloaded-and-skipped in one run, so a run can't spiral into
    re-scanning the whole backlog when most of it is already fine.
  - **Resumable:** `local-state/backfill-mirror-ocr-state.json` (gitignored
    by the repo's blanket `*.json` rule — no extra .gitignore entry needed;
    anchored to the repo root, not the invoking shell's cwd), keyed by
    `"{tab}:{id}"`. Most statuses are TERMINAL (never auto-retried) —
    `error_download` and `error_upload` are the exceptions, retried on the
    next run, UNLESS the exact same file fails the exact same way AGAIN, in
    which case it's recorded `"<status>_persistent"` (now terminal; a
    one-off network blip doesn't repeat itself twice on the same file the
    same way, so this is file-specific, not transient). A human can force a
    retry of any terminal status by deleting that row's entry from the state
    file. A malformed PDF (fitz can't open it — the audit found 10) is
    recorded `error_malformed` and reported separately at the end; this
    script does not try to fix them, per the handoff.

**Never run with `--apply` by the overnight coder loop** — this is Trisha's,
locally, after reading the dry-run report. Credentials: GSHEET_ID +
GDRIVE_SA_KEY for the dry-run's Sheet read; `--apply` additionally needs the
GOAUTH_* trio (see `archive_client.py` / `scripts/oauth_setup.py`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive_client as ac  # noqa: E402
import ocr_util  # noqa: E402
import sheet_writer as sw  # noqa: E402

# Anchored to the repo root (not a bare relative path) -- found by the
# advisor, 2026-10-13: a relative "local-state/..." resolves against whatever
# directory the script happens to be INVOKED from, not the repo. Running it
# from anywhere else starts a fresh state file, silently losing resume.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(_REPO_ROOT, "local-state", "backfill-mirror-ocr-state.json")

# Per-run safety cap on how many candidates get DOWNLOADED just to check their
# text layer (separate from --limit, which caps actual OCR+reupload attempts)
# -- bounds one run's Drive-call volume even when most of a tab is clean.
_MAX_SCAN_PER_RUN = 500

# Outcomes that represent an ACTUAL OCR attempt (image_only_pages() was
# non-empty) -- these, and only these, count against --limit. skip_has_text,
# error_bad_link, error_download, and error_malformed are "free": cheap to
# discover, no ocrmypdf subprocess run. done_partial (added 2026-10-13, see
# process_candidate()) is a REAL attempt that improved the file, just not
# all the way -- it counts too.
_COUNTS_AGAINST_LIMIT = {"done", "done_partial", "done_unverified", "ocr_incomplete",
                        "ocr_failed", "error_upload", "error_id_changed"}

# Every status is TERMINAL (never automatically retried on a later run) EXCEPT
# these two -- a transient network/API blip on a single file shouldn't
# permanently give up on it the way a genuinely malformed PDF or a real OCR
# failure should. (A human can also force a retry of any terminal status by
# deleting its entry from the state file.) Note: a "_persistent" suffixed
# status (see run_apply()) is deliberately NOT in this set -- it's terminal
# by construction, the same way "error_malformed" is.
_RETRYABLE_STATUSES = {"error_download", "error_upload"}

# A status that signals a SYSTEMIC problem (bad credentials/scope, a dead
# OAuth token) rather than a one-off bad file -- found 2026-10-13 by
# independent review: without this, a systemic failure would burn through up
# to _MAX_SCAN_PER_RUN OCR attempts in one run, each recorded as a permanent
# failure, before the run ever noticed something was fundamentally wrong.
# run_apply() aborts the REST of the run (not just this candidate) the FIRST
# time either of these fires for a given file. If the SAME file fails the
# SAME way AGAIN on a later retry (found 2026-10-13, round-2 independent
# review: without this, a single file-specific failure -- not a systemic one
# -- would abort every future run forever, since it's always the first thing
# re-attempted), that's treated as file-specific, not systemic: recorded
# "<status>_persistent" (terminal, not retried again) and the run continues
# instead of aborting a second time over the same file.
_ABORT_RUN_STATUSES = {"error_upload", "error_id_changed"}

# (tab constant, id-column header, link-column header) -- the archive tabs,
# in the FIXED priority order this script processes them. See module
# docstring for why this is a tab-level approximation of the handoff's
# per-document priority ask, not a true page-count sort. Ridge Wood Reports /
# Ridge Wood Revisions are DELIBERATELY EXCLUDED (found 2026-10-13 by the
# advisor): every one of the 66 Ridge Wood reports verified text-layer per
# ADR 016's own doc, so there's nothing there to fix, and overwriting one
# would break the Content Hash <-> Archive Link pairing ADR 066's provenance
# manifest relies on (the hash is of the ORIGINAL bytes; an OCR'd derivative
# would no longer match it). See ADR 069 for the full reasoning.
SOURCE_TABS = [
    (sw.TAB_ARCHIVE, "Doc ID", "Archive Link"),
    (sw.TAB_MMPC_ARCHIVE, "File ID", "Archive Link"),
    (sw.TAB_CIVICCLERK_ARCHIVE, "File ID", "Archive Link"),
]

_FILE_ID_PATTERNS = (
    re.compile(r"/file/d/([a-zA-Z0-9_-]+)"),   # https://drive.google.com/file/d/<id>/view...
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),     # https://drive.google.com/open?id=<id> / uc?id=<id>
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without credentials)
# ---------------------------------------------------------------------------


def extract_file_id(link: str) -> str | None:
    """Parse a Drive file id out of a webViewLink, in either form this
    repo's own links can take."""
    if not link:
        return None
    for pat in _FILE_ID_PATTERNS:
        m = pat.search(link)
        if m:
            return m.group(1)
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(state: dict, key: str, status: str, **extra) -> None:
    state[key] = {"status": status, "updated_at": _now_iso(), **extra}


def _cleanup(*paths: str) -> None:
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# State file I/O
# ---------------------------------------------------------------------------


def load_state(path: str = STATE_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def save_state(state: dict, path: str = STATE_PATH) -> None:
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=0, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Sheet enumeration -- service-account Sheets reads ONLY, never a Drive call.
# Safe to run in dry-run.
# ---------------------------------------------------------------------------


def enumerate_tab(sheets_svc, sheet_id: str, tab: str, id_header: str,
                  link_header: str) -> list[dict]:
    """[{key, id, link}] for every row in `tab` that has both an id and an
    archive link. A row with no link (the upload failed at the time, or this
    event type never carries one — e.g. a Ridge Wood Revisions "removed"
    row) has nothing to fix here and is skipped. Returns [] (never raises) if
    the tab doesn't exist yet — MMPC/CivicClerk/Ridge Wood tabs are created
    on demand by their own archivers, so a tab that's never run yet is a
    normal, not an error, state for this script."""
    try:
        resp = (
            sheets_svc.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"'{tab}'!A1:Z")
            .execute(num_retries=5)
        )
    except Exception as e:  # noqa: BLE001 — a missing/unready tab must not abort the whole run
        print(f"  [{tab}] could not read this tab ({type(e).__name__}) — skipping it this run.")
        return []
    rows = resp.get("values", [])
    if not rows:
        return []
    header = rows[0]
    if id_header not in header or link_header not in header:
        return []
    id_i = header.index(id_header)
    link_i = header.index(link_header)
    out = []
    for i, r in enumerate(rows[1:], start=2):
        row_id = r[id_i] if id_i < len(r) else ""
        link = r[link_i] if link_i < len(r) else ""
        if not row_id or not link:
            continue
        # Ridge Wood Revisions' "Month" is NOT unique per row (one row per
        # revision EVENT, possibly several per month) -- fold in the Sheet
        # row number so each event gets its own state key.
        key = (f"{tab}:{row_id}" if tab != sw.TAB_RIDGEWOOD_REVISIONS
               else f"{tab}:{row_id}:{i}")
        out.append({"key": key, "id": str(row_id), "link": link})
    return out


# ---------------------------------------------------------------------------
# --apply: the real work. Only called once GOAUTH_* creds are confirmed.
# ---------------------------------------------------------------------------


def process_candidate(drive, cand: dict, tmp_dir: str) -> dict:
    """Download, check, and (if needed) OCR + re-upload-in-place ONE
    candidate. Returns a state-entry dict (at least {"status": ...}) — NEVER
    RAISES; every failure mode is a recorded status, not an exception, so one
    bad file can't abort a run.

    coder:mirror-ocr fixes (2026-10-13, found by independent review + the
    advisor, before any --apply run against live data):
      - Tracks the SET of image-only pages (ocr_util.image_only_pages()), not
        just a bool, before and after OCR. A page with a genuinely
        unreadable image (a photo with nothing printed on it) stays flagged
        no matter how many times OCR runs — confirmed empirically — and
        WOI/inspection filings routinely carry such pages ALONGSIDE real
        tables. The first version of this function treated "still flagged
        anywhere" as "OCR failed, don't write Drive" — which would throw
        away a REAL improvement on every OTHER page of a mixed scan, for a
        potentially large share of the 392-file backlog. The rule now: write
        to Drive only if the image-only-page SET strictly shrank (`done` if
        it's now empty, `done_partial` if some genuinely unreadable pages
        remain); skip the Drive write only when OCR helped NOTHING at all
        (`ocr_incomplete`).
      - The pin+update step and the post-update verify step are two separate
        try/except blocks. A failure in the verify step (a transient
        re-download blip) no longer gets mislabeled `error_upload` when the
        update itself actually succeeded — that mislabeling would make a
        LATER run re-OCR and re-upload a file that's probably already fixed.
        See `done_unverified` below.
    """
    file_id = extract_file_id(cand["link"])
    if file_id is None:
        return {"status": "error_bad_link"}

    local = os.path.join(tmp_dir, f"{file_id}.pdf")
    try:
        ac.download_file(drive, file_id, local)
    except Exception as e:  # noqa: BLE001 — recorded, not raised; see module docstring
        return {"status": "error_download", "note": type(e).__name__}

    try:
        before_pages = ocr_util.image_only_pages(local)
    except Exception:
        _cleanup(local)
        return {"status": "error_malformed"}

    if not before_pages:
        _cleanup(local)
        return {"status": "skip_has_text"}

    searchable = ocr_util.ensure_searchable(local)
    if searchable == local:
        # ensure_searchable() is fail-open: returning the input path unchanged
        # means the OCR attempt itself failed (missing binary, timeout,
        # non-zero exit, or its own output failed validation). The original
        # scan-only file is untouched in Drive.
        _cleanup(local)
        return {"status": "ocr_failed"}

    try:
        try:
            after_pages = ocr_util.image_only_pages(searchable)
        except Exception:
            # ocrmypdf produced something fitz can't even open -- treat the
            # same as any other malformed-PDF finding (reported, not retried).
            return {"status": "error_malformed"}

        if not (after_pages < before_pages):
            # No STRICT improvement. ensure_searchable() already guarantees
            # after_pages can never contain a page that wasn't in
            # before_pages (no page loses text), so this means "unchanged" —
            # OCR ran but genuinely found nothing new anywhere. Don't touch
            # Drive for zero benefit.
            return {"status": "ocr_incomplete"}

        try:
            ac.pin_head_revision(drive, file_id)
            new_id = ac.update_file_content(drive, file_id, searchable)
        except Exception as e:  # noqa: BLE001 — recorded, not raised
            return {"status": "error_upload", "note": type(e).__name__}
        if new_id != file_id:
            # Structurally shouldn't happen (files().update always keeps the
            # id) -- defense in depth, never silently accept a changed id.
            return {"status": "error_id_changed", "note": f"{file_id}->{new_id}"}

        verify_path = local + ".verify.pdf"
        try:
            ac.download_file(drive, file_id, verify_path)
            verify_pages = ocr_util.image_only_pages(verify_path)
        except Exception as e:  # noqa: BLE001 — the UPDATE already succeeded; see docstring
            return {"status": "done_unverified", "note": type(e).__name__}
        finally:
            _cleanup(verify_path)

        return {"status": "done_partial" if verify_pages else "done"}
    finally:
        _cleanup(local, searchable)


def run_apply(sheets_svc, sheet_id: str, drive, state: dict, limit: int,
              state_path: str = STATE_PATH) -> dict:
    """Mutates `state` in place, saving it incrementally after EVERY
    candidate (a crash or Ctrl-C mid-run must never lose the record of a
    file whose Drive content was already overwritten). Returns
    {"scanned", "fixed", "by_status", "aborted"} for THIS RUN only (not
    cumulative across all runs)."""
    scanned = 0
    fixed = 0
    by_status: Counter = Counter()
    aborted = False
    with tempfile.TemporaryDirectory() as tmp_dir:
        for tab, id_header, link_header in SOURCE_TABS:
            if aborted or scanned >= _MAX_SCAN_PER_RUN or fixed >= limit:
                break
            for cand in enumerate_tab(sheets_svc, sheet_id, tab, id_header, link_header):
                already = state.get(cand["key"])
                already_status = already.get("status") if already else None
                if already is not None and already_status not in _RETRYABLE_STATUSES:
                    continue  # resolved by a prior run (and not a retryable transient failure)
                if scanned >= _MAX_SCAN_PER_RUN or fixed >= limit:
                    break
                scanned += 1
                result = process_candidate(drive, cand, tmp_dir)
                status = result["status"]
                extra = {k: v for k, v in result.items() if k != "status"}

                # If this exact failure already happened once before for this
                # SAME file (a retryable status, retried, and failed the
                # SAME way again), it's file-specific, not systemic -- make
                # it terminal so the run can move past it, instead of
                # aborting a second time over the same file every run.
                is_repeat_failure = status in _ABORT_RUN_STATUSES and already_status == status
                recorded_status = f"{status}_persistent" if is_repeat_failure else status

                _record(state, cand["key"], recorded_status, link=cand["link"], tab=tab, **extra)
                save_state(state, state_path)
                by_status[recorded_status] += 1
                if status in _COUNTS_AGAINST_LIMIT:
                    fixed += 1
                if status in _ABORT_RUN_STATUSES:
                    if is_repeat_failure:
                        print(f"[backfill] {status} on {cand['key']} failed the SAME way "
                              f"twice -- treating it as file-specific, not systemic. "
                              f"Recorded {recorded_status} (terminal); continuing this run.")
                    else:
                        print(f"[backfill] {status} on {cand['key']} looks SYSTEMIC "
                              f"(credentials/scope/token), not a one-off bad file -- "
                              f"aborting the rest of this run rather than repeating the "
                              f"same failure. This file will retry on the next run.")
                        aborted = True
                        break
            if aborted:
                break
    return {"scanned": scanned, "fixed": fixed, "by_status": by_status, "aborted": aborted}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def dry_run_report(sheets_svc, sheet_id: str, state: dict) -> str:
    lines = ["DRY-RUN -- this makes ZERO Drive API calls. Sheet-tab candidate "
             "counts only (see the module docstring for why this can't say how "
             "many files actually need OCR):", ""]
    total = resolved_total = 0
    for tab, id_header, link_header in SOURCE_TABS:
        candidates = enumerate_tab(sheets_svc, sheet_id, tab, id_header, link_header)
        resolved = sum(1 for c in candidates
                      if c["key"] in state
                      and state[c["key"]].get("status") not in _RETRYABLE_STATUSES)
        lines.append(f"  {tab:<28} {len(candidates):>5} candidate row(s), "
                     f"{resolved:>5} already resolved by a prior --apply run")
        total += len(candidates)
        resolved_total += resolved
    lines.append("")
    lines.append(f"  TOTAL: {total} candidate row(s) across {len(SOURCE_TABS)} tab(s), "
                 f"{resolved_total} already resolved, {total - resolved_total} not yet checked.")
    lines.append("")
    lines.append("Re-run with --apply to actually download, check, and (if needed) OCR + "
                 "re-upload in place, up to --limit (default 100) per run.")
    return "\n".join(lines)


def render_apply_summary(result: dict) -> str:
    lines = [f"Scanned {result['scanned']} candidate row(s) this run; "
             f"{result['fixed']} counted against the OCR limit."]
    for status, n in sorted(result["by_status"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {status:<18} {n}")
    malformed = result["by_status"].get("error_malformed", 0)
    if malformed:
        lines.append(f"\n{malformed} malformed PDF(s) found this run — NOT retried "
                     f"(per the handoff). See {STATE_PATH} for which file ids.")
    if result.get("aborted"):
        lines.append("\nThis run ABORTED EARLY on a systemic-looking failure "
                     "(credentials/scope/token) -- see the [backfill] line above. "
                     "error_download/error_upload retry automatically on the next "
                     "run; error_id_changed does not (it's always terminal) -- check "
                     f"{STATE_PATH} for which file id and investigate by hand.")
    return "\n".join(lines)


def _ocrmypdf_smoke_test(timeout: int = 60) -> bool:
    """A REAL functional check, not just 'does a file exist at this path' —
    found by the advisor, 2026-10-13: `os.path.isfile()` + `os.access(X_OK)`
    passes even when ocrmypdf is installed but its OWN dependency
    (Tesseract) is missing, in which case every real candidate this run
    touches would get recorded `ocr_failed` (terminal) for a machine-config
    reason, not a per-file one. Builds a tiny in-memory image-only PDF and
    confirms `ensure_searchable()` actually produces a different, validated
    output for it — exercising the real subprocess call, not just a path
    check. Never raises; returns False on any failure (the caller decides
    what to do, same fail-safe posture as everywhere else in this script)."""
    import fitz

    fd, tmp_in = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    out = None
    try:
        doc = fitz.open()
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
        pix.clear_with(128)
        page.insert_image(fitz.Rect(5, 5, 45, 45), pixmap=pix)
        doc.save(tmp_in)
        doc.close()
        out = ocr_util.ensure_searchable(tmp_in, timeout=timeout)
        return out != tmp_in
    except Exception:  # noqa: BLE001 — a smoke test that itself crashes just means "not working"
        return False
    finally:
        _cleanup(tmp_in)
        if out and out != tmp_in:
            _cleanup(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="ADR 069 one-time OCR backfill for already-mirrored scan-only PDFs.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually download, OCR, and re-upload in place (default: dry-run).")
    ap.add_argument("--limit", type=int, default=100,
                    help="Max ACTUAL OCR+reupload attempts this run (default 100).")
    args = ap.parse_args(argv)

    from backfill_metric_taxonomy import _load_dotenv
    _load_dotenv()
    import drive_client

    sheet_id = os.environ["GSHEET_ID"]
    sheets_svc = drive_client.sheets_service()
    state = load_state()

    if not args.apply:
        print(dry_run_report(sheets_svc, sheet_id, state))
        return 0

    if not all(os.environ.get(k) for k in ac.CREDENTIAL_ENV):
        print("ERROR: --apply needs GOAUTH_CLIENT_ID / GOAUTH_CLIENT_SECRET / "
              "GOAUTH_REFRESH_TOKEN set (see scripts/oauth_setup.py). Aborting -- no state written.")
        return 1

    # Machine-level precheck (found by independent review, hardened by the
    # advisor to a REAL smoke test, not just a path check): if ocrmypdf isn't
    # actually working here -- missing entirely, or installed but missing
    # ITS OWN dependency (Tesseract) -- EVERY candidate that needs OCR would
    # otherwise get recorded `ocr_failed` -- a config problem on THIS
    # machine, not a per-file problem, so it shouldn't burn through
    # candidates or write any state at all. ensure_searchable() itself
    # already fails open per-file; this is a whole-run guard in front of it.
    if not _ocrmypdf_smoke_test():
        binpath = ocr_util._ocrmypdf_bin()
        print(f"ERROR: ocrmypdf smoke test failed (binary resolved to {binpath!r} -- "
              f"it may be missing entirely, or installed without its own Tesseract "
              f"dependency). Install/fix it (e.g. `brew install ocrmypdf`) before "
              f"running --apply. Aborting -- no state written.")
        return 1
    drive = ac.oauth_drive_service()

    result = run_apply(sheets_svc, sheet_id, drive, state, args.limit)
    save_state(state)
    print(render_apply_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
