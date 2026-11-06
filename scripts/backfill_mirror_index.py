"""scripts/backfill_mirror_index.py — one-time fill of the `Mirror Index` tab
for PDFs mirrored BEFORE the tab existed (ADR 073 addendum, CODER-PLAN step 4).

Since 2026-11-05 every new nSITE / MMPC / CivicClerk upload writes its own
Mirror Index row (archive_client + mirror_index). This script covers the
~4,300 files already on Drive, plus the Hand-Curated files (no monitor upload
path), so later steps (full-text cache, OCR backfill, wellfield staging) can
read the index instead of downloading.

What it indexes: exactly the PDFs linked from the four public tabs that
index Mirror files: `Archived PDFs` (nsite), `MMPC Archived Files` (mmpc),
`CivicClerk Archived Files` (civicclerk), `Hand-Curated Files`
(hand_curated). One row per Drive id; when two tabs link the same file (some
Hand-Curated rows point at an nSITE Mirror copy), the first kind in KIND_ORDER
owns it. Non-PDF files and Mirror PDFs no tab links (WDS records, Ridge Wood,
loose folders) are counted, never indexed.

Where each column comes from:
  - mirror_sha256 / mirror_md5 / size / head_revision: Drive's own metadata,
    from listing the Mirror folders (`--root`, all pages, recursive), with a
    per-file files.get for linked ids no listing covered.
  - pages / image_only_pages / text_status / ocr_engine: ocr_util.page_stats()
    on the DOWNLOADED bytes, the same check the upload path runs. The
    download's own sha256 must equal Drive's listed sha (else Drive is asked
    again; still different = the file changed mid-run, skipped and counted).
  - original_sha256 / raw_revision_pinned: blank. The backfill can't know the
    bytes the source served (ADR 073 §1).

Why everything is downloaded: the Apple Vision state (`--vision-state`, the
Lotext Mac backfill's ledger) has page counts for most Mirror files but no
sha256, so the fail-safe rule ("stats must describe exactly these bytes")
can't be met from it alone, and its `skip_has_text` entries can't tell a
native PDF from an OCRmyPDF one (`native` vs `ocr_full`). The dry run reports
what that shortcut WOULD save and, with `--validate-sample N`, how often the
Vision numbers agree with a fresh page check, so Trisha can decide (Gate C).
The Vision state IS used for one thing on the download path: a file Vision
OCR'd (`done`/`done_partial`) whose fresh page count and image-only count
match the Vision entry gets ocr_engine `Apple Vision` (or `<OCRmyPDF creator>
+ Apple Vision`), because Vision keeps the input's /Creator and the file
would otherwise read as `native`.

Writes (`--apply` only):
  - Skips any id already in the index whose mirror_sha256 equals Drive's live
    sha (a row from the upload itself is better than one from here).
  - Appends in batches. Before each batch the index is read again (STRICT:
    a read failure raises and stops the run) and ids now present are dropped,
    so a rerun resumes. A row an upload wrote after the plan was made is
    left alone (it describes Drive's current bytes). A row that was already
    there with a DIFFERENT sha is replaced in place (mirror_index.upsert_row).
    A file Drive gives no sha256 gets no row (it could never be trusted).
    Two narrow races remain, both in the moment between a batch's re-read
    and its write: an upload appending a row there leaves two rows and the
    reader takes the last (ours); an upload rewriting a row we are about to
    replace is overwritten by ours. Either way our row's sha differs from
    Drive's live sha, so readers process the file the old way (fail-safe)
    and a rerun reindexes it.
  - `--apply` needs `--vision-state PATH` or an explicit `--no-vision-state`:
    without the Vision state, Vision-OCR'd files are indexed with engine
    `none`, and since their sha matches Drive's a rerun would never fix them.

Output: stdout carries counts only. Per-file detail (ids, names) goes ONLY to
`--detail-out` (TSV), which belongs outside this public repo.

DRY RUN BY DEFAULT: lists Drive, reads the Sheet, downloads nothing (unless
`--validate-sample`), writes nothing. `--apply` is Gate C (Trisha's go).

Credentials: GDRIVE_SA_KEY (service account; read-only on the Mirror folders
is enough, Sheets write needed for --apply) and GSHEET_ID, from `.env`.

Usage:
  python scripts/backfill_mirror_index.py --root <MIRROR_FOLDER_ID> \\
      [--root <HAND_CURATED_FOLDER_ID>] [--vision-state PATH] \\
      [--validate-sample 40] [--detail-out PATH] [--apply [--limit N]]
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mirror_index as mi  # noqa: E402

PDF = "application/pdf"
FOLDER = "application/vnd.google-apps.folder"
LIST_FIELDS = ("nextPageToken, files(id, name, mimeType, size, md5Checksum, "
               "sha256Checksum, headRevisionId, modifiedTime)")
GET_FIELDS = "id, name, mimeType, size, md5Checksum, sha256Checksum, headRevisionId, modifiedTime, trashed"

# (kind, tab, key column, link column). Order = precedence when two tabs link
# the same Drive file.
TABS = [
    ("nsite", "Archived PDFs", "Doc ID", "Archive Link"),
    ("mmpc", "MMPC Archived Files", "File ID", "Archive Link"),
    ("civicclerk", "CivicClerk Archived Files", "File ID", "Archive Link"),
    ("hand_curated", "Hand-Curated Files", "curated_filename", "drive_link"),
]
KIND_ORDER = [t[0] for t in TABS]

VISION_ENGINE = "Apple Vision"
# Every status the Vision backfill records AFTER writing its output over the
# Drive file (Lotext scripts/mirror-vision-ocr/backfill.py).
VISION_DONE = ("done", "done_partial", "done_id_unconfirmed", "done_partial_id_unconfirmed")
# The Vision backfill (Lotext scripts/mirror-vision-ocr/backfill.py) stamps
# `at` with time.strftime(): naive LOCAL time on Trisha's Mac.
VISION_TZ = "America/Detroit"
# Drive's modifiedTime for a file Vision wrote is within ~1 s of its `at`
# (Drive for desktop keeps the local mtime; measured on 527 done entries,
# 2026-11-05, max 0.99 s). Anything later means the file changed since.
VISION_WRITE_SLACK_S = 60

_SHA_COL = mi.HEADERS.index("mirror_sha256")

_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]{20,})|[?&]id=([A-Za-z0-9_-]{20,})")


# --- pure -------------------------------------------------------------------

def drive_id_from_link(link: str) -> str | None:
    m = _ID_RE.search(link or "")
    return (m.group(1) or m.group(2)) if m else None


def sheet_refs(tab_rows: dict[str, list[list]]) -> tuple[dict[str, tuple[str, str]], Counter]:
    """{drive_id: (kind, source_key)} from each tab's rows (header first), plus
    counts. First kind in KIND_ORDER wins a shared id; within a tab the first
    row wins."""
    refs: dict[str, tuple[str, str]] = {}
    c: Counter = Counter()
    for kind, tab, key_col, link_col in TABS:
        rows = tab_rows.get(tab) or []
        if not rows:
            continue
        hdr = [str(h).strip() for h in rows[0]]
        if key_col not in hdr or link_col not in hdr:
            raise ValueError(f"{tab}: header lacks {key_col!r}/{link_col!r}")
        ki, li = hdr.index(key_col), hdr.index(link_col)
        for r in rows[1:]:
            if not r or not any(str(x).strip() for x in r):
                continue
            c[f"{kind}:rows"] += 1
            did = drive_id_from_link(r[li] if len(r) > li else "")
            if not did:
                c[f"{kind}:no_link"] += 1
                continue
            if did in refs:
                c[f"{kind}:shared_with_{refs[did][0]}" if refs[did][0] != kind
                  else f"{kind}:duplicate_link"] += 1
                continue
            refs[did] = (kind, str(r[ki] if len(r) > ki else "").strip())
    return refs, c


def vision_at_utc(at: str) -> datetime | None:
    """The Vision state's naive local `at` as an aware UTC datetime."""
    from zoneinfo import ZoneInfo
    try:
        return datetime.fromisoformat(at).replace(tzinfo=ZoneInfo(VISION_TZ)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def drive_time(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def vision_shortcut(meta: dict, ventry: dict | None) -> str | None:
    """Which page stats the Vision state COULD supply without a download, for
    the dry-run report only (--apply never uses it; see module docstring):
    'searchable' (skip_has_text) or 'not_searchable' (ocr_nothing), when Drive
    says the file was last modified before Vision recorded it. Else None."""
    if not ventry or ventry.get("status") not in ("skip_has_text", "ocr_nothing"):
        return None
    if not isinstance(ventry.get("pages"), int):
        return None
    at, mod = vision_at_utc(ventry.get("at", "")), drive_time(meta.get("modifiedTime", ""))
    if at is None or mod is None or mod > at:
        return None
    return "searchable" if ventry["status"] == "skip_has_text" else "not_searchable"


def vision_key(path: str) -> str:
    """The Vision state's key for a file at `path` (relative to the Case File
    Mirror): the bare name at the top level, else `<parent folder>/<name>`
    (Vision ran one folder at a time, keyed by that folder's basename)."""
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        return ""
    return parts[-1] if len(parts) == 1 else f"{parts[-2]}/{parts[-1]}"


def engine_with_vision(stats, ventry: dict | None, meta: dict):
    """`stats` with ocr_engine naming Apple Vision when the Vision state says
    it wrote OCR output over THIS Drive file (its recorded item_id), the file
    hasn't been modified since (Drive modifiedTime within
    VISION_WRITE_SLACK_S of Vision's `at`, either side), AND the fresh check matches what
    Vision recorded (same page count, same image-only count after). Vision
    keeps the input's /Creator, so without this a Vision-OCR'd scan reads as
    `native`."""
    if stats is None or not ventry:
        return stats
    if not meta.get("id") or ventry.get("item_id") != meta.get("id"):
        return stats
    if ventry.get("status") == "converted_msg":
        # fix_msg_files.py rebuilt this file from an email (OCRmyPDF, then
        # Vision on leftover pages) and recorded no time and no page outcome:
        # who made which text is unknowable, so never publish `native`.
        return replace(stats, image_only_pages=None, engine="")
    if ventry.get("status") not in VISION_DONE or stats.image_only_pages is None:
        return stats
    at, mod = vision_at_utc(ventry.get("at", "")), drive_time(meta.get("modifiedTime", ""))
    if (at is None or mod is None or abs((mod - at).total_seconds()) > VISION_WRITE_SLACK_S
            or ventry.get("pages") != stats.pages
            or ventry.get("image_only_after") != len(stats.image_only_pages)):
        # Vision wrote over this exact Drive file but the current bytes don't
        # check out as its output: who made the text is unknowable, so don't
        # publish `native`. Unknown page set => text_status `unknown`
        # (readers process the file the old way).
        return replace(stats, image_only_pages=None, engine="")
    engine = VISION_ENGINE if stats.engine in ("", "none") else f"{stats.engine[:100]} + {VISION_ENGINE}"
    return replace(stats, engine=engine)


@dataclass
class Item:
    drive_id: str
    kind: str
    key: str
    meta: dict
    path: str = ""
    vision: dict | None = None
    action: str = ""  # index | skip_indexed | reindex | no_drive_sha | not_pdf | unreachable
    shortcut: str | None = None
    planned_row_sha: str = ""  # sha of the existing index row at plan time (reindex)


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)


def build_plan(refs: dict[str, tuple[str, str]], listed: dict[str, dict],
               fetched: dict[str, dict | None], index: dict[str, dict],
               vision: dict[str, dict], paths: dict[str, str]) -> Plan:
    """Decide, per linked Drive id, what --apply would do. Pure.

    listed  -- {id: Drive metadata} from the folder listing
    fetched -- {id: metadata or None (404/trashed)} for linked ids not listed
    index   -- mirror_index.read_index()
    vision  -- the Vision state ({path relative to the first root: entry})
    paths   -- {id: path relative to the first root}"""
    p = Plan()
    # Vision recorded the Drive id of every file it wrote: an exact match,
    # immune to renames and to which folder was passed first.
    by_item = {v["item_id"]: v for v in vision.values()
               if isinstance(v, dict) and v.get("item_id")}
    for did, (kind, key) in refs.items():
        meta = listed.get(did) or fetched.get(did)
        it = Item(did, kind, key, meta or {}, path=paths.get(did, ""))
        if not meta or meta.get("trashed"):
            it.action = "unreachable"
        elif meta.get("mimeType") != PDF:
            it.action = "not_pdf"
        else:
            row = index.get(did)
            live = (meta.get("sha256Checksum") or "").lower()
            if not live:
                # No Drive sha: a row could never be trusted (fail-safe), and
                # the download could never be checked against it.
                it.action = "no_drive_sha"
            elif row and str(row.get("mirror_sha256", "")).lower() == live:
                it.action = "skip_indexed"
            else:
                it.action = "reindex" if row else "index"
                it.planned_row_sha = str((row or {}).get("mirror_sha256", "")).lower()
                it.vision = by_item.get(did) or (vision.get(vision_key(it.path)) if it.path else None)
                it.shortcut = vision_shortcut(meta, it.vision)
        p.items.append(it)
        p.counts[f"{kind}:{it.action}"] += 1
        p.counts[it.action] += 1
    return p


def unreferenced_pdfs(listed: dict[str, dict], refs: dict) -> int:
    return sum(1 for d, m in listed.items() if m.get("mimeType") == PDF and d not in refs)


def human_gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


# --- Drive / Sheets ---------------------------------------------------------

def list_tree(drive, root_id: str, retries: int) -> tuple[dict[str, dict], dict[str, str]]:
    """({id: metadata}, {id: path relative to root}) for every non-folder
    file under root_id, all pages, recursive. A folder the service account
    can't see lists as empty, so the root itself is fetched first (404 raises)."""
    drive.files().get(fileId=root_id, fields="id", supportsAllDrives=True).execute(num_retries=retries)
    files, paths = {}, {}
    stack = [(root_id, "")]
    while stack:
        folder, prefix = stack.pop()
        token = None
        while True:
            resp = drive.files().list(
                q=f"'{folder}' in parents and trashed = false", fields=LIST_FIELDS,
                pageSize=1000, pageToken=token, supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute(num_retries=retries)
            for f in resp.get("files", []):
                path = prefix + f["name"]
                if f.get("mimeType") == FOLDER:
                    stack.append((f["id"], path + "/"))
                else:
                    files[f["id"]] = f
                    paths[f["id"]] = path
            token = resp.get("nextPageToken")
            if not token:
                break
    return files, paths


def fetch_missing(drive, ids, retries: int) -> dict[str, dict | None]:
    from googleapiclient.errors import HttpError
    out = {}
    for did in ids:
        try:
            out[did] = drive.files().get(fileId=did, fields=GET_FIELDS,
                                         supportsAllDrives=True).execute(num_retries=retries)
        except HttpError as e:
            if getattr(e, "resp", None) is not None and e.resp.status == 404:
                out[did] = None
            else:
                raise
    return out


def read_tabs(sheets, sheet_id: str) -> dict[str, list[list]]:
    import sheet_writer as sw
    # Strict: a failed read must stop the run, never look like "no files".
    return {tab: sw._tab_rows_strict(sheets, sheet_id, tab, "A:Z") for _k, tab, _c, _l in TABS}


def stats_for(drive, it: Item, tmp_dir: str, retries: int):
    """(stats or None, outcome). Downloads, checks the bytes are the ones
    Drive listed, runs the page check once. Never raises for one bad file."""
    import drive_client as dc
    import ocr_util
    dest = os.path.join(tmp_dir, "f.pdf")
    try:
        dc.download_file(drive, it.drive_id, dest)
        stats = None
        try:
            stats = ocr_util.page_stats(dest)
        except Exception:  # noqa: BLE001 — unreadable PDF: index it as `unknown`
            sha = ocr_util.file_sha256(dest)
            outcome = "unreadable"
        else:
            sha = stats.sha256
            outcome = "ok"
        if sha != (it.meta.get("sha256Checksum") or "").lower():
            fresh = drive.files().get(fileId=it.drive_id, fields=GET_FIELDS,
                                      supportsAllDrives=True).execute(num_retries=retries)
            if (fresh.get("sha256Checksum") or "").lower() != sha:
                return None, "changed_mid_run"
            it.meta = fresh
        return stats, outcome
    except Exception as e:  # noqa: BLE001
        return None, f"download_error:{type(e).__name__}"
    finally:
        if os.path.exists(dest):
            os.remove(dest)


def validate_sample(drive, plan: Plan, n: int, retries: int, seed: int = 0) -> Counter:
    """Download up to n random shortcut-eligible files and compare the Vision
    entry with a fresh page check. Read-only."""
    pool = [it for it in plan.items if it.shortcut]
    random.Random(seed).shuffle(pool)
    c: Counter = Counter()
    t0, nbytes = time.monotonic(), 0
    with tempfile.TemporaryDirectory() as td:
        for it in pool[:n]:
            stats, outcome = stats_for(drive, it, td, retries)
            c["sampled"] += 1
            nbytes += int(it.meta.get("size") or 0)
            if stats is None:
                c[f"sample_{outcome}"] += 1
                continue
            v = it.vision or {}
            want_img = 0 if v.get("status") == "skip_has_text" else v.get("image_only")
            if stats.pages == v.get("pages") and len(stats.image_only_pages or ()) == want_img:
                c["agree"] += 1
            else:
                c["disagree"] += 1
            c["engine_ocrmypdf" if stats.engine.startswith("OCRmyPDF") else "engine_none"] += 1
    c["_bytes"] = nbytes
    c["_seconds"] = int(time.monotonic() - t0)
    return c


def apply(drive, sheets, sheet_id: str, plan: Plan, *, batch: int, limit: int,
          retries: int, log=print) -> Counter:
    import sheet_writer as sw
    todo = [it for it in plan.items if it.action in ("index", "reindex")]
    if limit:
        todo = todo[:limit]
    c: Counter = Counter()
    pending: list[tuple[Item, list]] = []

    def flush():
        if not pending:
            return
        current = mi.read_index(sheets, sheet_id)  # strict: raises on failure
        appends = []
        for it, row in pending:
            have = current.get(it.drive_id)
            sha = row[_SHA_COL].lower()
            if have and sha and str(have.get("mirror_sha256", "")).lower() == sha:
                c["raced_already_indexed"] += 1
            elif have and (it.action != "reindex"
                           or str(have.get("mirror_sha256", "")).lower() != it.planned_row_sha):
                # Written since the plan was made: an upload's row describes
                # Drive's current bytes better than this download. Leave it.
                c["raced_newer_row"] += 1
            elif have:
                mi.upsert_row(sheets, sheet_id, row)
                c["replaced"] += 1
            else:
                appends.append(row)
        sw.append_rows(sheets, sheet_id, mi.TAB, appends)
        c["appended"] += len(appends)
        pending.clear()
        log(f"progress: {c['appended'] + c['replaced']} written")

    sw.ensure_tab(sheets, sheet_id, mi.TAB, mi.HEADERS)
    with tempfile.TemporaryDirectory() as td:
        for it in todo:
            stats, outcome = stats_for(drive, it, td, retries)
            c[outcome.split(":")[0]] += 1
            if outcome not in ("ok", "unreadable"):
                continue
            # it.meta, not the plan's: stats_for refreshes it when Drive
            # reports newer bytes, and then Vision's record no longer applies.
            before = stats
            stats = engine_with_vision(stats, it.vision, it.meta)
            if stats is not before:
                c["vision_credited" if stats.image_only_pages is not None
                  else "vision_unverified_unknown"] += 1
            row = mi.build_row(it.meta, stats, source_kind=it.kind, source_key=it.key)
            c[f"status:{row[mi.HEADERS.index('text_status')]}"] += 1
            pending.append((it, row))
            if len(pending) >= batch:
                flush()
        flush()
    return c


# --- report -----------------------------------------------------------------

def render_summary(plan: Plan, ref_counts: Counter, unref: int, sample: Counter | None) -> str:
    todo = [it for it in plan.items if it.action in ("index", "reindex")]
    size = sum(int(it.meta.get("size") or 0) for it in todo)
    sc = Counter(it.shortcut for it in todo if it.shortcut)
    sc_bytes = sum(int(it.meta.get("size") or 0) for it in todo if it.shortcut)
    vision_done = sum(1 for it in todo if (it.vision or {}).get("status") in VISION_DONE)
    out = ["Mirror Index backfill plan", ""]
    out.append("Sheet links (rows / no link / shared or duplicate):")
    for kind in KIND_ORDER:
        extra = sum(v for k, v in ref_counts.items() if k.startswith(f"{kind}:shared")
                    or k == f"{kind}:duplicate_link")
        out.append(f"  {kind}: {ref_counts[f'{kind}:rows']} rows, "
                   f"{ref_counts[f'{kind}:no_link']} no link, {extra} shared/duplicate")
    out.append("")
    out.append("Plan per kind (index = new row, reindex = row with a different sha, "
               "skip_indexed = row already matches Drive):")
    for kind in KIND_ORDER:
        parts = [f"{a} {plan.counts[f'{kind}:{a}']}" for a in
                 ("index", "reindex", "skip_indexed", "no_drive_sha", "not_pdf", "unreachable")
                 if plan.counts[f"{kind}:{a}"]]
        out.append(f"  {kind}: " + (", ".join(parts) or "nothing"))
    out.append(f"Mirror PDFs no tab links (not indexed): {unref}")
    out.append("")
    out.append(f"--apply would download {len(todo)} files, {human_gb(size)} "
               f"(every row's page stats from the downloaded bytes).")
    out.append(f"  of which Vision-OCR'd per the Vision state (engine attribution applies "
               f"if the fresh check matches): {vision_done}")
    out.append(f"Vision-state shortcut (NOT used by --apply; Gate C option): "
               f"{sc['searchable']} skip_has_text + {sc['not_searchable']} ocr_nothing files, "
               f"{human_gb(sc_bytes)} not downloaded, modified on Drive before Vision checked them.")
    out.append("  Caveat: no sha in the Vision state, and skip_has_text can't tell native "
               "from ocr_full (a schema change to ADR 073 if chosen).")
    if sample is not None:
        secs = max(sample["_seconds"], 1)
        rate = sample["_bytes"] / secs
        out.append("")
        out.append(f"Sample check ({sample['sampled']} shortcut-eligible files, downloaded): "
                   f"{sample['agree']} agree, {sample['disagree']} disagree, "
                   f"{sum(v for k, v in sample.items() if k.startswith('sample_'))} not checked; "
                   f"of the checked: {sample['engine_ocrmypdf']} carry an OCRmyPDF /Creator, "
                   f"{sample['engine_none']} none.")
        if rate > 0:
            hrs = size / rate / 3600
            hrs_sc = (size - sc_bytes) / rate / 3600
            out.append(f"Measured {rate / 1e6:.1f} MB/s incl. page checks -> --apply ~{hrs:.1f} h "
                       f"(with the shortcut ~{hrs_sc:.1f} h).")
    return "\n".join(out)


def vision_summary(vision: dict) -> str:
    """One line: how much Vision state was loaded and how recent it is, so a
    missing or stale file is visible before --apply writes anything."""
    if not vision:
        return "Vision state: none loaded (Vision-OCR'd files would read as engine `none`)."
    done = sum(1 for v in vision.values() if isinstance(v, dict) and v.get("status") in VISION_DONE)
    latest = max((str(v.get("at", "")) for v in vision.values() if isinstance(v, dict)), default="")
    return f"Vision state: {len(vision)} entries, {done} Vision-written, latest {latest[:10] or 'unknown'}."


def write_detail(path: str, plan: Plan) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("drive_id\tkind\tsource_key\taction\tsize\tvision_status\tshortcut\tpath\n")
        for it in plan.items:
            f.write("\t".join([it.drive_id, it.kind, it.key, it.action,
                               str(it.meta.get("size") or ""),
                               (it.vision or {}).get("status", ""), it.shortcut or "",
                               it.path]) + "\n")


def _inside(path: str, root: str) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r + os.sep)


def main(argv=None) -> int:
    """Errors print their class only: a traceback would carry request URIs
    (Drive/Sheet ids), and stdout is promised to be counts only."""
    try:
        return _main(argv)
    except Exception as e:  # noqa: BLE001
        print(f"backfill_mirror_index: stopped ({type(e).__name__}). Rows of the batch in "
              f"progress may be partly written; a rerun finishes them.")
        return 1


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill the Mirror Index tab (ADR 073).")
    ap.add_argument("--root", action="append", required=True,
                    help="Drive folder id to list (repeatable). Vision-state paths are "
                         "relative to the FIRST root (the Case File Mirror).")
    ap.add_argument("--vision-state", help="Lotext mirror-vision-ocr-state.json")
    ap.add_argument("--no-vision-state", action="store_true",
                    help="--apply without the Vision state (Vision-OCR'd files then "
                         "read as engine `none`, permanently)")
    ap.add_argument("--validate-sample", type=int, default=0,
                    help="dry run: download N shortcut-eligible files and compare")
    ap.add_argument("--detail-out", help="per-file TSV (keep it out of the repo)")
    ap.add_argument("--apply", action="store_true", help="write rows (Gate C)")
    ap.add_argument("--limit", type=int, default=0, help="--apply: at most N downloads")
    ap.add_argument("--batch", type=int, default=200)
    a = ap.parse_args(argv)
    # googleapiclient logs retried requests with their full URI (Drive and
    # Sheet ids); keep stdout/stderr to counts only.
    import logging
    logging.getLogger("googleapiclient").setLevel(logging.ERROR)
    if a.vision_state and a.no_vision_state:
        ap.error("--vision-state and --no-vision-state are exclusive")
    if a.apply and not (a.vision_state or a.no_vision_state):
        ap.error("--apply needs --vision-state PATH (or an explicit --no-vision-state)")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if a.detail_out and _inside(a.detail_out, repo):
        ap.error("--detail-out must be outside the repo (it lists file ids and names)")

    import json

    from config_loader import load_dotenv
    load_dotenv()
    import drive_client as dc
    retries = dc.GOOGLE_API_NUM_RETRIES
    sheet_id = os.environ["GSHEET_ID"]
    drive, sheets = dc.drive_service(), dc.sheets_service()

    listed, paths = {}, {}
    for i, root in enumerate(a.root):
        f, p = list_tree(drive, root, retries)
        for did, meta in f.items():
            listed.setdefault(did, meta)
        if i == 0:
            paths = p  # only first-root paths can match the Vision state
    refs, ref_counts = sheet_refs(read_tabs(sheets, sheet_id))
    fetched = fetch_missing(drive, [d for d in refs if d not in listed], retries)
    index = mi.read_index(sheets, sheet_id)
    vision = {}
    if a.vision_state:
        with open(a.vision_state, encoding="utf-8") as f:
            vision = json.load(f)
    plan = build_plan(refs, listed, fetched, index, vision, paths)
    sample = validate_sample(drive, plan, a.validate_sample, retries) if a.validate_sample else None
    print(render_summary(plan, ref_counts, unreferenced_pdfs(listed, refs), sample))
    print(vision_summary(vision))
    if a.detail_out:
        write_detail(a.detail_out, plan)
    if not a.apply:
        print("\nDry run: nothing written.")
        return 0
    c = apply(drive, sheets, sheet_id, plan, batch=a.batch, limit=a.limit, retries=retries)
    print("\nApplied: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
