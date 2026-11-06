#!/usr/bin/env python3
"""scripts/build_fulltext_cache.py -- fill the full-text cache for the public
records search (ADR 062 addendum, 2026-11-02).

Reads the PUBLISHED site/public-records/search-index.json (never the Sheet),
and for every eligible entry (fulltext_search.is_eligible: not hand-curated, a
Drive-mirror or nSITE link) that is not cached yet: downloads the PDF, extracts
its text with PyMuPDF, redacts both name lists (full text, never capped -- see below), and writes
<cache>/<key>.txt plus a line in <cache>/manifest.json. Incremental and
resumable: at most --max-downloads new documents per run, so the first fill
spreads over several days instead of one long job. A document that fails is
retried on later runs, up to MAX_ATTEMPTS.

Content changes (CODER-PLAN step 5, 2026-11-06): every fetch records the
sha256 of the downloaded bytes in the manifest. When Drive's LIVE
sha256Checksum for a cached Mirror copy can be read (the archiver's OAuth
client), a document is fetched again exactly when that live sha differs from
the recorded one, and the 30-day timer no longer applies to it. A document
with no recorded sha (everything cached before this change), an nSITE link,
or a lookup that failed keeps the old timer rule: skip work only on a live
sha match (ADR 073's fail-safe rule). This job has no Sheet credentials, so it
does not read the Mirror Index tab; the live Drive check is what the rule
requires anyway.

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
import hashlib
import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fulltext_search as fts  # noqa: E402
import retry_policy  # noqa: E402

DEFAULT_INDEX = "site/public-records/search-index.json"
MANIFEST = "manifest.json"
MAX_ATTEMPTS = 3
# Stop the run after this many transient failures in a row (an outage).
MAX_CONSECUTIVE_TRANSIENT = 5
# Drive's HTML page instead of a file: stop the run once this many DIFFERENT
# documents get it (real throttling), and treat it as the document's own failure
# once one document has had it this many times (not shared / own quota).
MAX_THROTTLED_DOCS_PER_RUN = 3
MAX_THROTTLED_PER_DOC = 3
# The cache keeps each document's FULL normalized + redacted text (up to
# MAX_RAW_CHARS), never the capped/de-duplicated form: de-duplication breaks the
# adjacency of a full name's words, so it must only ever happen AFTER redaction
# (at index time, in prepare_content), where a name listed after the document was
# cached is still a matchable phrase. (Review rounds 3 and 5 on PR #203.)


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


MAX_REFRESHES = 3
# A cached copy whose Drive content changed is fetched again on every run until
# it succeeds, but a NON-transient failure counts a strike, and after this many
# the document goes back to the old timer rule (so one broken file can never be
# retried forever).
MAX_CHANGE_ERRORS = 3


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_changed(m: dict | None, live_sha: str | None) -> bool | None:
    """True / False when both the recorded sha (of the cached bytes) and
    Drive's live sha are known; None when either is missing (then the old
    timer rule decides). Pure."""
    if not m or m.get("status") not in ("ok", "no_text"):
        return None
    recorded = m.get("sha256") or ""
    if not recorded or not live_sha:
        return None
    return recorded != live_sha


def _needs_refresh(m: dict, today: date) -> bool:
    """Due a re-fetch: no text at all, or a material share of textless pages
    (more than one page AND more than 10%: a blank separator or back page is
    not a scan), at most MAX_REFRESHES times, every REFRESH_SCANNED_AFTER_DAYS."""
    if m.get("status") not in ("ok", "no_text"):
        return False
    if m.get("refreshes", 0) >= MAX_REFRESHES:
        return False
    if m.get("status") == "ok":
        textless = m.get("pages", 0) - m.get("text_pages", 0)
        if textless <= 1 or textless <= 0.10 * m.get("pages", 0):
            return False
    try:
        fetched = date.fromisoformat(m.get("fetched", ""))
    except ValueError:
        return True
    return (today - fetched).days >= REFRESH_SCANNED_AFTER_DAYS


def pending(entries: list[dict], manifest: dict, today: date | None = None,
            live: dict[str, str] | None = None) -> list[tuple[str, dict]]:
    """(key, entry) for every eligible entry that needs a fetch, NEW work first:
    never tried, or failed fewer than MAX_ATTEMPTS times; then cached Mirror
    copies whose Drive content changed (`live`: {key: Drive's live sha256});
    then documents cached with textless pages that are due a refresh
    (_needs_refresh) -- the timer applies only when the live-sha comparison
    can't be made. Deduplicated by key (two feed rows can share one Mirror
    copy). Pure, for the tests."""
    today = today or date.today()
    live = live or {}
    new, changed, refresh, seen = [], [], [], set()
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
            continue
        verdict = content_changed(m, live.get(key))
        if verdict is True and m.get("change_errors", 0) < MAX_CHANGE_ERRORS:
            changed.append((key, e))
        elif verdict is False:
            continue  # live sha equals the cached bytes' sha: nothing to refetch
        elif _needs_refresh(m, today):
            refresh.append((key, e))
    return new + changed + refresh


def live_lookup_keys(entries: list[dict], manifest: dict) -> list[str]:
    """Cached Mirror copies (drive_ keys) with a recorded sha: the only ones a
    live Drive sha can decide for. Pure."""
    out, seen = [], set()
    for e in entries:
        if not fts.is_eligible(e):
            continue
        key = fts.doc_key(e["link"])
        if key in seen or not key.startswith("drive_"):
            continue
        seen.add(key)
        m = manifest.get(key) or {}
        if m.get("status") in ("ok", "no_text") and m.get("sha256"):
            out.append(key)
    return out


# Stop reading a document's pages once this much raw text is in hand: the cache
# only needs so much text to make the document findable, so reading every
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


def check_pdf(dest: str) -> None:
    """Both download paths: under the size cap, and actually a PDF."""
    if os.path.getsize(dest) > MAX_PDF_BYTES:
        raise ValueError("file over size cap")
    with open(dest, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError("not a PDF")


class ThrottledError(Exception):
    """Drive answered a download with an HTML page instead of the file (its
    quota / interstitial page, served with status 200). Carries status_code 503
    so retry_policy treats it as transient: never the document's fault."""
    status_code = 503


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
        ctype = (r.headers.get("Content-Type") or "").lower()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ValueError("file over size cap")
                f.write(chunk)
    if "text/html" in ctype:
        raise ThrottledError("Drive returned an HTML page instead of the file")
    check_pdf(dest)


def _status_in_chain(exc: BaseException):
    """First HTTP status found on the exception or anything it was raised from
    (nsite_client.download_pdf wraps its last failure in a RuntimeError)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        for status in (getattr(exc, "status_code", None),
                       getattr(getattr(exc, "response", None), "status_code", None)):
            if isinstance(status, int):
                return status
        exc = exc.__cause__ or exc.__context__
    return None


class Fetcher:
    """Downloads one entry's PDF. Drive mirror copies use the archiver's OAuth
    client when it is configured and working (drive.file scope: the app created
    them), else the public no-credential download; nSITE links use the same
    session/referer handling as the watcher."""

    def __init__(self):
        self._drive = None
        self._drive_ok = None
        self._nsite = None

    def _oauth(self):
        """The archiver's OAuth Drive client, or None (not configured, or it
        already failed this run)."""
        if self._drive_ok is False:
            return None
        if self._drive is None:
            import archive_client as ac

            try:
                if not ac.is_configured():
                    raise RuntimeError("archive OAuth not configured")
                self._drive = ac.oauth_drive_service()
            except Exception:  # noqa: BLE001 -- no usable creds: public link all run
                self._drive_ok = False
        return self._drive

    def live_sha256(self, keys: list[str]) -> dict[str, str]:
        """{key: Drive's live sha256Checksum} for drive_ keys, through the
        OAuth client (metadata only, batched). {} when there is no usable
        client; a key whose lookup failed is absent (old rule). Never raises."""
        ids = [k[len("drive_"):] for k in keys if k.startswith("drive_")]
        service = self._oauth() if ids else None
        if service is None:
            return {}
        import mirror_index

        return {"drive_" + fid: sha for fid, sha in mirror_index.live_sha256(service, ids).items()}

    def fetch(self, key: str, entry: dict, dest: str) -> None:
        if key.startswith("drive_"):
            file_id = key[len("drive_"):]
            if self._drive_ok is not False:
                import archive_client as ac

                self._oauth()
                if self._drive is not None:
                    try:
                        ac.download_file(self._drive, file_id, dest)
                        check_pdf(dest)
                        self._drive_ok = True
                        return
                    except Exception as e:  # noqa: BLE001 -- this file only: public link
                        # A refresh-token failure means the creds are dead for
                        # the whole run; any other error is about this one file.
                        if type(e).__name__ == "RefreshError":
                            self._drive_ok = False
            public_drive_download(file_id, dest)
        else:
            import nsite_client as nc

            if self._nsite is None:
                self._nsite = nc.make_session()
            nc.download_pdf(self._nsite, {"doc_id": key[len("nsite_"):], "doc_url": entry["link"]}, dest)


def run(index_path: str, cache_dir: str, max_downloads: int, fetcher=None,
        extract=extract_pages_isolated, max_minutes: float | None = None, clock=None) -> dict:
    """Fetch up to max_downloads pending documents, stopping early once
    max_minutes have passed (so the CI job always finishes and saves its cache
    instead of hitting its own timeout mid-download)."""
    import time

    clock = clock or time.monotonic
    started = clock()
    os.makedirs(cache_dir, exist_ok=True)
    with open(index_path, encoding="utf-8") as f:
        entries = json.load(f)
    manifest = load_manifest(cache_dir)
    fetcher = fetcher or Fetcher()
    lookup = live_lookup_keys(entries, manifest)
    live_fn = getattr(fetcher, "live_sha256", None)
    live = {}
    if lookup and live_fn is not None:
        try:
            live = live_fn(lookup) or {}
        except Exception:  # noqa: BLE001 -- no live shas: the old timer rule decides
            live = {}
    todo = pending(entries, manifest, live=live)
    counts = {"eligible": sum(1 for e in entries if fts.is_eligible(e)), "pending": len(todo),
              "fetched": 0, "no_text": 0, "errors": 0, "redactions": 0, "stopped_on_time": 0,
              "transient": 0, "stopped_transient": 0,
              "live_lookups": len(lookup), "live_found": len(live),
              "live_changed": sum(1 for k, v in live.items() if content_changed(manifest.get(k), v))}
    consecutive_transient = 0
    throttled_docs = 0
    today = date.today().isoformat()
    for key, entry in todo[:max_downloads]:
        if max_minutes is not None and (clock() - started) / 60 >= max_minutes:
            counts["stopped_on_time"] = 1
            break
        prior = manifest.get(key, {})
        changed = content_changed(prior, live.get(key)) is True
        try:
            with tempfile.TemporaryDirectory() as td:
                pdf = os.path.join(td, "doc.pdf")
                fetcher.fetch(key, entry, pdf)
                sha = _file_sha256(pdf)
                pages = extract(pdf)
            text_pages = sum(1 for p in pages if p is None or p.strip())
            raw = "\n".join(p for p in pages if p)
            _, n_red = fts.name_check.redact_denylist(fts.normalize(raw))
            content = fts.redact(fts.normalize(raw))
            with open(os.path.join(cache_dir, key + ".txt"), "w", encoding="utf-8") as f:
                f.write(content)
            status = "ok" if content else "no_text"
            # A fetch because the content changed is new content, not a retry of
            # the old one: it starts the textless-page refresh count over.
            was_cached = prior.get("status") in ("ok", "no_text")
            manifest[key] = {"status": status, "pages": len(pages), "text_pages": text_pages,
                             "chars": len(content), "fetched": today, "sha256": sha,
                             "refreshes": prior.get("refreshes", 0) + 1 if was_cached and not changed else 0}
            counts["fetched"] += 1
            counts["redactions"] += n_red
            if status == "no_text":
                counts["no_text"] += 1
        except Exception as e:  # noqa: BLE001 -- one bad document must not stop the run
            # An outage, a 429 or a dropped connection is never the document's
            # fault (retry_policy, same rule as the document pipeline): no strike,
            # no refresh used up. Only a real per-document failure counts.
            # Plus the builtin socket-level errors the Drive client can raise
            # (subprocess.TimeoutExpired, a hung PDF, is NOT one of these).
            transient = retry_policy.is_transient(e) or isinstance(e, (ConnectionError, TimeoutError))
            # An HTML answer instead of the file is usually Drive throttling, but
            # a file that isn't publicly shared (or has its own quota used up)
            # answers the same way every time: after MAX_THROTTLED_PER_DOC such
            # answers it counts as the document's own failure, so one bad file
            # can never stall the fill on every run.
            throttled = isinstance(e, ThrottledError)
            if throttled and prior.get("throttled", 0) + 1 >= MAX_THROTTLED_PER_DOC:
                transient = False
            if prior.get("status") in ("ok", "no_text"):
                # A failed REFRESH keeps the good cached copy and its counts; the
                # new fetched date puts the next refresh 30 days out.
                # A failed refetch after a CONTENT CHANGE strikes change_errors
                # instead (the recorded sha stays the old one, so it is retried
                # next run until MAX_CHANGE_ERRORS).
                manifest[key] = {**prior, "fetched": today, "last_refresh_error": type(e).__name__}
                if changed:
                    manifest[key]["change_errors"] = prior.get("change_errors", 0) + (0 if transient else 1)
                else:
                    manifest[key]["refreshes"] = prior.get("refreshes", 0) + (0 if transient else 1)
            else:
                manifest[key] = {"status": f"error:{type(e).__name__}", "fetched": today,
                                 "attempts": prior.get("attempts", 0) + (0 if transient else 1)}
                if throttled:
                    manifest[key]["throttled"] = prior.get("throttled", 0) + 1
            counts["errors"] += 1
            if transient:
                counts["transient"] += 1
                consecutive_transient += 1
                # Back off for the whole run on the first rate limit, or after a
                # run of transient failures (an outage): the rest wait for the
                # next run instead of failing one after another.
                if throttled:
                    throttled_docs += 1
                if _status_in_chain(e) == 429 or throttled_docs >= MAX_THROTTLED_DOCS_PER_RUN or \
                        consecutive_transient >= MAX_CONSECUTIVE_TRANSIENT:
                    counts["stopped_transient"] = 1
                    break
            else:
                consecutive_transient = 0
        else:
            consecutive_transient = 0
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
    max_min = cfg.get("max_fetch_minutes")
    counts = run(args.index, args.cache, max_dl, max_minutes=float(max_min) if max_min else None)
    print("fulltext cache:", " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
