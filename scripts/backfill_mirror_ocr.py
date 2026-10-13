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
Archived Files, CivicClerk Archived Files, Ridge Wood Reports, Ridge Wood
Revisions) — so this only ever touches files THIS APP created
(`archive_client`'s `drive.file` scope can't see anything else). Hand-Curated
Files is excluded on purpose (not app-created — `drive.file` can't touch it);
WDS Page Snapshots is excluded (HTML, not PDF).

**DRY-RUN MAKES ZERO DRIVE API CALLS.** It reads the Sheet tabs (service-
account Sheets access only — not a Drive call) and reports candidate-row
counts per tab, cross-referenced against the resumable state file for how
many are already resolved. It canNOT report how many files actually need
OCR — that requires downloading and inspecting each one, which is exactly the
Drive call dry-run is built to never make. This is a narrower, more literal
reading than "counts only" might first suggest; see ADR 069 decision 8.

**--apply does the real work:**
  - Enumerates each tab's rows in a FIXED tab order (Archived PDFs first —
    the nSITE mirror, dominated by the N2688 WOI/HOV reports the audit
    flagged worst — then MMPC, CivicClerk, Ridge Wood Reports, Ridge Wood
    Revisions last). This is a documented APPROXIMATION of the handoff's
    "multi-page N2688 first, then multi-page other groups, then 1-page"
    priority ask: true page-count / per-SRN sorting would need a download of
    the whole backlog just to order it, which conflicts with keeping every
    run's Drive-call volume bounded. See ADR 069 for the full reasoning.
  - For each not-yet-resolved candidate (per the state file): downloads the
    mirrored PDF and runs `ocr_util.needs_ocr()`. Already-searchable is
    recorded `skip_has_text` and does NOT count against `--limit` — only an
    actual OCR attempt does.
  - For a file that needs OCR: `ocr_util.ensure_searchable()` on a temp copy
    (never raises — fail-open; on failure records `ocr_failed` and leaves the
    file untouched in Drive). On success: pins the CURRENT head revision
    (`archive_client.pin_head_revision()` — Drive purges a non-head binary-
    file revision ~30 days after new content lands unless pinned, so without
    this the "old version stays in revision history" guarantee would quietly
    expire), overwrites the file's content IN PLACE
    (`archive_client.update_file_content()`, SAME file id — never
    `files().create`), re-downloads it, and verifies: the file id didn't
    change, and `ocr_util.needs_ocr()` is now False. Still-flagged-after-OCR
    is recorded `ocr_incomplete`, never silently treated as fixed.
  - `--limit` (default 100) caps ACTUAL OCR attempts per run, not rows
    scanned. `_MAX_SCAN_PER_RUN` separately bounds how many already-clean
    rows get downloaded-and-skipped in one run, so a run can't spiral into
    re-scanning the whole backlog when most of it is already fine.
  - **Resumable:** `local-state/backfill-mirror-ocr-state.json` (gitignored
    by the repo's blanket `*.json` rule — no extra .gitignore entry needed),
    keyed by `"{tab}:{id}"`. Every recorded status is TERMINAL — a row is
    never automatically retried on a later run, including a transient
    download/upload error. (A human can force a retry by deleting that row's
    entry from the state file.) A malformed PDF (fitz can't open it — the
    audit found 10) is recorded `error_malformed` and reported separately at
    the end; this script does not try to fix them, per the handoff.

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

STATE_PATH = os.path.join("local-state", "backfill-mirror-ocr-state.json")

# Per-run safety cap on how many candidates get DOWNLOADED just to check their
# text layer (separate from --limit, which caps actual OCR+reupload attempts)
# -- bounds one run's Drive-call volume even when most of a tab is clean.
_MAX_SCAN_PER_RUN = 500

# Outcomes that represent an ACTUAL OCR attempt (needs_ocr() was True) --
# these, and only these, count against --limit. skip_has_text and every
# error_* outcome below this line are "free": cheap to discover, no
# ocrmypdf subprocess run.
_COUNTS_AGAINST_LIMIT = {"done", "ocr_incomplete", "ocr_failed"}

# (tab constant, id-column header, link-column header) -- the 5 archive tabs,
# in the FIXED priority order this script processes them. See module
# docstring for why this is a tab-level approximation of the handoff's
# per-document priority ask, not a true page-count sort.
SOURCE_TABS = [
    (sw.TAB_ARCHIVE, "Doc ID", "Archive Link"),
    (sw.TAB_MMPC_ARCHIVE, "File ID", "Archive Link"),
    (sw.TAB_CIVICCLERK_ARCHIVE, "File ID", "Archive Link"),
    (sw.TAB_RIDGEWOOD, "Month", "Archive Link"),
    (sw.TAB_RIDGEWOOD_REVISIONS, "Month", "New Archive Link"),
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
    bad file can't abort a run."""
    file_id = extract_file_id(cand["link"])
    if file_id is None:
        return {"status": "error_bad_link"}

    local = os.path.join(tmp_dir, f"{file_id}.pdf")
    try:
        ac.download_file(drive, file_id, local)
    except Exception as e:  # noqa: BLE001 — recorded, not raised; see module docstring
        return {"status": "error_download", "note": type(e).__name__}

    try:
        needs = ocr_util.needs_ocr(local)
    except Exception:
        _cleanup(local)
        return {"status": "error_malformed"}

    if not needs:
        _cleanup(local)
        return {"status": "skip_has_text"}

    searchable = ocr_util.ensure_searchable(local)
    if searchable == local:
        # ensure_searchable() is fail-open: returning the input path unchanged
        # means the OCR attempt itself failed (missing binary, timeout,
        # non-zero exit). The original scan-only file is untouched in Drive.
        _cleanup(local)
        return {"status": "ocr_failed"}

    try:
        ac.pin_head_revision(drive, file_id)
        new_id = ac.update_file_content(drive, file_id, searchable)
        if new_id != file_id:
            # Structurally shouldn't happen (files().update always keeps the
            # id) -- defense in depth, never silently accept a changed id.
            return {"status": "error_id_changed", "note": f"{file_id}->{new_id}"}
        verify_path = local + ".verify.pdf"
        ac.download_file(drive, file_id, verify_path)
        still_needs = ocr_util.needs_ocr(verify_path)
        _cleanup(verify_path)
        return {"status": "ocr_incomplete"} if still_needs else {"status": "done"}
    except Exception as e:  # noqa: BLE001 — recorded, not raised
        return {"status": "error_upload", "note": type(e).__name__}
    finally:
        _cleanup(local, searchable)


def run_apply(sheets_svc, sheet_id: str, drive, state: dict, limit: int) -> dict:
    """Mutates `state` in place. Returns {"scanned", "fixed", "by_status"}
    for THIS RUN only (not cumulative across all runs)."""
    scanned = 0
    fixed = 0
    by_status: Counter = Counter()
    with tempfile.TemporaryDirectory() as tmp_dir:
        for tab, id_header, link_header in SOURCE_TABS:
            if scanned >= _MAX_SCAN_PER_RUN or fixed >= limit:
                break
            for cand in enumerate_tab(sheets_svc, sheet_id, tab, id_header, link_header):
                if cand["key"] in state:
                    continue  # already resolved by a prior run -- terminal, never re-attempted
                if scanned >= _MAX_SCAN_PER_RUN or fixed >= limit:
                    break
                scanned += 1
                result = process_candidate(drive, cand, tmp_dir)
                status = result["status"]
                extra = {k: v for k, v in result.items() if k != "status"}
                _record(state, cand["key"], status, link=cand["link"], tab=tab, **extra)
                by_status[status] += 1
                if status in _COUNTS_AGAINST_LIMIT:
                    fixed += 1
    return {"scanned": scanned, "fixed": fixed, "by_status": by_status}


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
        resolved = sum(1 for c in candidates if c["key"] in state)
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
    return "\n".join(lines)


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
    drive = ac.oauth_drive_service()

    result = run_apply(sheets_svc, sheet_id, drive, state, args.limit)
    save_state(state)
    print(render_apply_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
