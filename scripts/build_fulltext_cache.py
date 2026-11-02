#!/usr/bin/env python3
"""scripts/build_fulltext_cache.py -- fill the full-text cache for the public
records search (ADR 062 addendum, 2026-11-02).

Reads the PUBLISHED site/public-records/search-index.json (never the Sheet),
and for every eligible entry (fulltext_search.is_eligible: not hand-curated, a
Drive-mirror or nSITE link) that is not cached yet: downloads the PDF, extracts
its text with PyMuPDF, redacts the name_check denylist, caps it, and writes
<cache>/<key>.txt plus a line in <cache>/manifest.json. Incremental and
resumable: at most --max-downloads new documents per run, so the first fill
spreads over several days instead of one long job. A document that fails is
retried on later runs, up to MAX_ATTEMPTS.

The cache is NOT committed (data files never are). In CI it lives in the
Actions cache (fulltext-index.yml); locally it is any directory you pass.

stdout is public in Actions: only counts and exception class names are printed,
never a title, URL or file name (SEC-001).

Usage:
  python3 scripts/build_fulltext_cache.py --cache DIR [--index PATH]
      [--max-downloads N] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fulltext_search as fts  # noqa: E402

DEFAULT_INDEX = "site/public-records/search-index.json"
MANIFEST = "manifest.json"
MAX_ATTEMPTS = 3
# Cached text is capped more generously than the index cap, so the index cap can
# be raised later without re-downloading everything.
CACHE_CAP_CHARS = 300_000


def load_manifest(cache_dir: str) -> dict:
    path = os.path.join(cache_dir, MANIFEST)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(cache_dir: str, manifest: dict) -> None:
    path = os.path.join(cache_dir, MANIFEST)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, sort_keys=True, indent=0)
    os.replace(tmp, path)


# A document cached with pages that had no text (a scan, or a partly scanned
# compilation) is fetched again after this many days: backfill_mirror_ocr.py /
# ADR 069 add a text layer IN PLACE (same Drive file id, so the same cache key),
# and without a refresh that document would stay unsearchable forever.
REFRESH_SCANNED_AFTER_DAYS = 30


def _needs_refresh(m: dict, today: date) -> bool:
    if m.get("status") not in ("ok", "no_text"):
        return False
    if m.get("status") == "ok" and m.get("text_pages", 0) >= m.get("pages", 0):
        return False
    try:
        fetched = date.fromisoformat(m.get("fetched", ""))
    except ValueError:
        return True
    return (today - fetched).days >= REFRESH_SCANNED_AFTER_DAYS


def pending(entries: list[dict], manifest: dict, today: date | None = None) -> list[tuple[str, dict]]:
    """(key, entry) for every eligible entry that needs a fetch, NEW work first:
    never tried, or failed fewer than MAX_ATTEMPTS times; then documents cached
    with textless pages that are due a refresh (_needs_refresh). Deduplicated by
    key (two feed rows can share one Mirror copy). Pure, for the tests."""
    today = today or date.today()
    new, refresh, seen = [], [], set()
    for e in entries:
        if not fts.is_eligible(e):
            continue
        key = fts.doc_key(e["link"])
        if key in seen:
            continue
        seen.add(key)
        m = manifest.get(key)
        if m is None or (m.get("status", "").startswith("error") and m.get("attempts", 0) < MAX_ATTEMPTS):
            new.append((key, e))
        elif _needs_refresh(m, today):
            refresh.append((key, e))
    return new + refresh


# Stop reading a document's pages once this much raw text is in hand: the cache
# keeps at most CACHE_CAP_CHARS of prose plus distinct words, so reading every
# page of a 6,500-page compilation only costs CI time (seen in the real Mirror).
MAX_RAW_CHARS = 3_000_000


# A malformed PDF can make PyMuPDF spin at 100% CPU inside fitz.open() (seen on
# a real 7.5 MB Mirror copy, 2026-11-02), so every extraction runs in a child
# process that is killed after this many seconds; the document is then recorded
# as an error (retried, then skipped) instead of stalling the whole run.
EXTRACT_TIMEOUT_S = 180


def extract_pages_isolated(pdf_path: str, timeout: int = EXTRACT_TIMEOUT_S) -> list[str]:
    import subprocess

    out_path = pdf_path + ".pages.json"
    subprocess.run([sys.executable, os.path.abspath(__file__), "--extract-one", pdf_path, out_path],
                   check=True, timeout=timeout, capture_output=True)
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)


def extract_pages(pdf_path: str, max_chars: int = MAX_RAW_CHARS) -> list[str | None]:
    """Text of each page, in order; pages past the raw-text budget are counted
    but not read (returned as None, so they never look like textless scans to
    the refresh rule)."""
    import fitz  # PyMuPDF

    out, total = [], 0
    with fitz.open(pdf_path) as doc:
        for page in doc:
            if total >= max_chars:
                out.append(None)
                continue
            t = page.get_text()
            total += len(t)
            out.append(t)
    return out


PUBLIC_DRIVE_DOWNLOAD = "https://drive.usercontent.google.com/download"
MAX_PDF_BYTES = 200 * 1024 * 1024


def public_drive_download(file_id: str, dest: str, timeout: int = 120) -> None:
    """Fetch a PUBLIC ("anyone with the link") Drive file without credentials.
    The Mirror copies the feed links to are public by design, so this needs no
    secret. Fixed https host, streamed with a size cap, and the body must be a
    PDF (an HTML interstitial or error page raises instead of being cached)."""
    import requests

    with requests.get(PUBLIC_DRIVE_DOWNLOAD,
                      params={"id": file_id, "export": "download", "confirm": "t"},
                      stream=True, timeout=timeout) as r:
        r.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ValueError("file over size cap")
                f.write(chunk)
    with open(dest, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError("not a PDF")


class Fetcher:
    """Downloads one entry's PDF. Drive mirror copies use the archiver's OAuth
    client when it is configured and working (drive.file scope: the app created
    them), else the public no-credential download; nSITE links use the same
    session/referer handling as the watcher."""

    def __init__(self):
        self._drive = None
        self._drive_ok = None
        self._nsite = None

    def fetch(self, key: str, entry: dict, dest: str) -> None:
        if key.startswith("drive_"):
            file_id = key[len("drive_"):]
            if self._drive_ok is not False:
                import archive_client as ac

                try:
                    if self._drive is None:
                        if not ac.is_configured():
                            raise RuntimeError("archive OAuth not configured")
                        self._drive = ac.oauth_drive_service()
                    ac.download_file(self._drive, file_id, dest)
                    self._drive_ok = True
                    return
                except Exception:  # noqa: BLE001 -- fall back to the public link
                    if self._drive_ok is None:
                        self._drive_ok = False  # creds broken: stop retrying OAuth this run
            public_drive_download(file_id, dest)
        else:
            import nsite_client as nc

            if self._nsite is None:
                self._nsite = nc.make_session()
            nc.download_pdf(self._nsite, {"doc_id": key[len("nsite_"):], "doc_url": entry["link"]}, dest)


def run(index_path: str, cache_dir: str, max_downloads: int, fetcher=None,
        extract=extract_pages_isolated) -> dict:
    os.makedirs(cache_dir, exist_ok=True)
    with open(index_path, encoding="utf-8") as f:
        entries = json.load(f)
    manifest = load_manifest(cache_dir)
    todo = pending(entries, manifest)
    fetcher = fetcher or Fetcher()
    counts = {"eligible": sum(1 for e in entries if fts.is_eligible(e)), "pending": len(todo),
              "fetched": 0, "no_text": 0, "errors": 0, "redactions": 0}
    today = date.today().isoformat()
    for key, entry in todo[:max_downloads]:
        prior = manifest.get(key, {})
        try:
            with tempfile.TemporaryDirectory() as td:
                pdf = os.path.join(td, "doc.pdf")
                fetcher.fetch(key, entry, pdf)
                pages = extract(pdf)
            text_pages = sum(1 for p in pages if p is None or p.strip())
            raw = "\n".join(p for p in pages if p)
            _, n_red = fts.name_check.redact_denylist(fts.normalize(raw))
            content = fts.prepare_content(raw, cap=CACHE_CAP_CHARS)
            with open(os.path.join(cache_dir, key + ".txt"), "w", encoding="utf-8") as f:
                f.write(content)
            status = "ok" if content else "no_text"
            manifest[key] = {"status": status, "pages": len(pages), "text_pages": text_pages,
                             "chars": len(content), "fetched": today}
            counts["fetched"] += 1
            counts["redactions"] += n_red
            if status == "no_text":
                counts["no_text"] += 1
        except Exception as e:  # noqa: BLE001 -- one bad document must not stop the run
            manifest[key] = {"status": f"error:{type(e).__name__}", "fetched": today,
                             "attempts": prior.get("attempts", 0) + 1}
            counts["errors"] += 1
        if (counts["fetched"] + counts["errors"]) % 25 == 0:
            save_manifest(cache_dir, manifest)  # resumable if the job is killed
    save_manifest(cache_dir, manifest)
    counts["cached_ok"] = sum(1 for m in manifest.values() if m.get("status") == "ok")
    return counts


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--extract-one"]:  # child process for extract_pages_isolated
        with open(argv[2], "w", encoding="utf-8") as f:
            json.dump(extract_pages(argv[1]), f)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--max-downloads", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="run even if the config flag is off")
    args = ap.parse_args(argv)
    import config_loader

    cfg = (config_loader.load_config().get("fulltext_search") or {})
    if not cfg.get("enabled") and not args.force:
        print("fulltext_search.enabled is false; nothing to do")
        return 0
    max_dl = args.max_downloads if args.max_downloads is not None else int(cfg.get("max_downloads_per_run", 300))
    counts = run(args.index, args.cache, max_dl)
    print("fulltext cache:", " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
