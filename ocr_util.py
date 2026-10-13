"""
ocr_util.py — the single OCR module shared by the classification pipeline AND
the Drive-mirror upload path (coder:mirror-ocr, 2026-10-13).

Two call shapes live here, for two different callers with different
tolerance for failure:

  1. classify() / ocr_in_place() — moved HERE, UNCHANGED, from egle_doc_parser.py.
     poison_doc_extractor.py calls ocr_in_place() proactively for synthesized
     PDFs and relies on it RAISING on failure (a logged-and-skipped best-
     effort, never a silent pass) — that behavior is preserved exactly.
     classify()'s verdict is a WHOLE-DOCUMENT judgment over the first
     `min(n, 12)` pages — the function ITSELF is unchanged. What DID change
     (2026-10-13, mid-build, after a real 1,006-page specimen was found):
     egle_doc_parser.parse_document() no longer uses classify()'s verdict as
     its sole gate for whether to call ocr_in_place() — see needs_ocr() below
     and parse_document()'s own docstring for why, and for how its raise-on-
     failure contract is preserved for exactly the cases classify() would
     already have triggered on `main`, with a new fail-open path for the
     cases only needs_ocr() catches.

  2. needs_ocr() / ensure_searchable() — NEW, for archive_client.upload_file()
     (and now also read by parse_document(), see above). needs_ocr() is
     PAGE-BY-PAGE (every page, not a 12-page sample), because a mixed EGLE
     filing (pages of real text plus a handful of image-only cover letters or
     signature pages) reads as "has_text" overall under classify()'s
     aggregate judgment — exactly the gap the 2026-10-08 OCR audit found
     (392/2,103 mirrored PDFs with no text layer; the worst group, N2688
     WOI/HOV reports, at 36%). A page needs OCR here if it has LITERALLY NO
     extractable text AND carries at least one image — matching exactly what
     `ocrmypdf --skip-text` itself treats as "already has text" (confirmed
     empirically: it skips a page with even a single stray character, not
     just a page past some character-count threshold — see needs_ocr()'s own
     docstring for why a looser threshold doesn't converge). A genuinely
     blank separator page — no text, no image — does NOT need OCR either:
     there is nothing on it to OCR, and flagging it would make
     ensure_searchable() shell out to ocrmypdf on every single upload of a
     document that has a blank page, defeating the "already-searchable files
     pass straight through" requirement from the other direction.

     ensure_searchable() is FAIL-OPEN: it never raises. Any failure (corrupt
     PDF, missing ocrmypdf binary, non-zero exit, timeout) logs a count-only
     warning and returns the ORIGINAL path unchanged, so a document is never
     dropped because OCR failed. SEC-001: this module's log lines never carry
     a filename, title, or exception text — this repo's GitHub Actions logs
     are public.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

import fitz  # pymupdf


log = logging.getLogger(__name__)

# classify()'s OWN per-page imageonly threshold (its aggregate verdict over
# the first min(n,12) pages — unchanged, see classify() below). NOT shared
# with image_only_pages()/needs_ocr() below, which deliberately use a
# different threshold (exactly 0, not 20 — see image_only_pages()'s own
# docstring for why): classify()'s whole-document verdict and this module's
# NEW page-by-page check answer different questions and are allowed to use
# different thresholds for their own different purposes.
_IMAGEONLY_CHAR_THRESHOLD = 20


# ---------------------------------------------------------------------------
# Moved verbatim from egle_doc_parser.py — the classification pipeline's gate.
# Behavior UNCHANGED. egle_doc_parser.py keeps both names importable (see its
# own comment at the top of its OCR section) so existing callers/tests that
# reach them as `egle_doc_parser.classify` / `egle_doc_parser.ocr_in_place`
# keep working.
# ---------------------------------------------------------------------------


def classify(path: str):
    """Return (verdict, npages, chars_per_page). verdict in
    needs_ocr/likely/has_text/empty/error."""
    try:
        doc = fitz.open(path)
    except Exception:
        return ("error", 0, 0)
    n = len(doc)
    if n == 0:
        doc.close()
        return ("empty", 0, 0)
    check = min(n, 12)
    total_chars = 0
    text_pages = 0
    imageonly_pages = 0
    for i in range(check):
        pg = doc[i]
        t = len(pg.get_text().strip())
        imgs = len(pg.get_images(full=False))
        total_chars += t
        if t >= 100:
            text_pages += 1
        if t < _IMAGEONLY_CHAR_THRESHOLD and imgs >= 1:
            imageonly_pages += 1
    doc.close()
    cpp = total_chars / check
    if text_pages == 0 and imageonly_pages >= 1:
        return ("needs_ocr", n, cpp)  # no text anywhere, pages are images
    if cpp < 40 and imageonly_pages >= check * 0.5:
        return ("likely", n, cpp)  # mostly image pages, scraps of text
    if text_pages == 0 and total_chars < 30:
        return ("empty", n, cpp)  # no text, no images -> blank/odd
    return ("has_text", n, cpp)


def _ocrmypdf_bin() -> str:
    """Resolve the ocrmypdf binary. Override with OCRMYPDF_BIN."""
    return (
        os.environ.get("OCRMYPDF_BIN")
        or shutil.which("ocrmypdf")
        or "/opt/homebrew/bin/ocrmypdf"
    )


def _minimal_ocr_env() -> dict:
    """The environment for the ensure_searchable() subprocess call ONLY —
    found by security review, 2026-10-13: passing the full os.environ (the
    original behavior, still used by ocr_in_place() above, UNCHANGED per
    this module's own docstring) hands every workflow secret (GOAUTH_*,
    GDRIVE_SA_KEY, etc.) to ocrmypdf and its child processes (Tesseract,
    ghostscript, qpdf), which parse PDFs submitted by regulated third
    parties (nSITE filings, CivicClerk, GovQA, RRD) — this is now the
    upload path for SEVEN workflows, several of which hold those secrets.
    Low severity (would need a parser bug in that toolchain plus a
    weaponized filing), but cheap to close: only PATH/HOME/TMPDIR/LANG are
    needed to run ocrmypdf at all."""
    keep = ("HOME", "TMPDIR", "LANG", "LC_ALL")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")
    return env


def ocr_in_place(pdf_path: str, timeout: int = 600) -> bool:
    """Run `ocrmypdf --skip-text` and replace pdf_path with the searchable
    version. Returns True on success. Raises on failure so the caller can
    decide whether to proceed with the un-OCR'd text."""
    binpath = _ocrmypdf_bin()
    env = dict(os.environ, PATH="/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    tmp = pdf_path + ".ocr_tmp.pdf"
    try:
        r = subprocess.run(
            [binpath, "--skip-text", "-l", "eng", "--output-type", "pdf", pdf_path, tmp],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        if r.returncode != 0 or not os.path.exists(tmp):
            last = (r.stderr or "").strip().split("\n")[-1][:200]
            raise RuntimeError(f"ocrmypdf failed (rc={r.returncode}): {last}")
        os.replace(tmp, pdf_path)
        return True
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------
# NEW (coder:mirror-ocr, 2026-10-13) — the Drive-mirror upload path.
# ---------------------------------------------------------------------------


def image_only_pages(path: str, char_threshold: int = 0) -> set[int]:
    """Return the 0-indexed set of pages that are image-only (LITERALLY NO
    extractable text AND at least one image) — checked page by page, every
    page, not a sampled aggregate. This is what catches the mixed-document
    gap: a 300-page WOI report that is almost entirely real text still needs
    this check to find the handful of image-only cover/signature pages
    classify()'s 12-page, whole-document verdict can miss.

    Returns a SET, not a bool (needs_ocr() below is just `bool(...)` of this),
    because a caller that OCRs a document and wants to know whether it helped
    needs more than yes/no: a photo-log page with nothing printed on it stays
    in this set FOREVER, no matter how many times OCR runs (Tesseract finds
    nothing because there is nothing to find) — confirmed empirically
    (2026-10-13 spike: a synthetic solid-color "photo" page stayed at 0
    characters even after a real, successful ocrmypdf pass). Treating that as
    "OCR failed, discard everything" would throw away a real improvement on
    every OTHER page of a mixed real-world scan (WOI/inspection filings
    routinely carry photo pages alongside printed tables) — see
    scripts/backfill_mirror_ocr.py's `process_candidate()` for how the set
    (not just its truthiness) is used to tell "no benefit" from "partial
    benefit" from "fully fixed."

    The threshold is EXACTLY ZERO characters, matching `ocrmypdf --skip-text`'s
    own real behavior, confirmed empirically (2026-10-13 spike against the
    actual binary): ocrmypdf skips a page entirely — no OCR attempt at all —
    the moment it has ANY text, even a single stray character or a short
    caption like "Photo 1". A looser threshold (the first version of this
    function used <20 chars) flags such a page as needing OCR, but ocrmypdf
    then skips it anyway, so the page reads needs_ocr()==True again on the
    very next check — the file never converges to "no longer needs OCR",
    `archive_client.upload_file()`'s "already-searchable files pass straight
    through" promise silently breaks for any document with such a page (every
    future upload re-invokes ocrmypdf for no gain), and the backfill script
    would mislabel a genuinely successful OCR run as incomplete forever.
    Matching ocrmypdf's own zero-text threshold makes a single OCR pass
    actually converge for THIS reason; the photo-log-page reason above is a
    separate, unavoidable limit (there's genuinely nothing to recover).

    A page with no text AND no image (a genuine blank separator page) is NOT
    flagged — there's nothing for ocrmypdf to add, and flagging it would
    re-trigger an OCR subprocess on every upload of any document that
    happens to have a blank page, breaking the same "pass straight through"
    requirement from the other direction.

    Raises on a read error (corrupt file, not a real PDF) — ensure_searchable()
    is the fail-open layer; this function stays a plain, honest predicate."""
    doc = fitz.open(path)
    try:
        if len(doc) == 0:
            return set()  # nothing to OCR
        pages = set()
        for i, page in enumerate(doc):
            text_len = len(page.get_text().strip())
            has_image = len(page.get_images(full=False)) >= 1
            if text_len <= char_threshold and has_image:
                pages.add(i)
        return pages
    finally:
        doc.close()


def needs_ocr(path: str, char_threshold: int = 0) -> bool:
    """True if ANY page in the document is image-only. See
    image_only_pages()'s docstring — this is just `bool()` of that set; use
    image_only_pages() directly when you need to tell a PARTIAL improvement
    from no improvement (the backfill script does)."""
    return bool(image_only_pages(path, char_threshold))


def _cleanup(path: str) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


_OCRMYPDF_CREATOR_PREFIX = "OCRmyPDF"


def ensure_searchable(local_path: str, *, timeout: int = 300, language: str = "eng") -> str:
    """Return a path to a searchable PDF, for the caller to upload to Drive.

    If every page already has text (needs_ocr() is False — the common case:
    the watcher/backfill path already ran classify()+ocr_in_place() before
    this is ever reached), returns `local_path` UNCHANGED. No copy, no
    ocrmypdf call — this is what makes "already-searchable files pass
    straight through" fast rather than a second OCR pass.

    If any page is image-only AND the document hasn't already been through
    ocrmypdf (checked via its own `/Creator` metadata — confirmed empirically
    that ocrmypdf stamps this on EVERY page of its output, whether that page
    was actually OCR'd or skipped for already having text), runs
    `ocrmypdf --skip-text` on a TEMP COPY and returns that path. If the
    document HAS already been through ocrmypdf and still has an image-only
    page, running it again would just repeat the same failed recognition
    attempt — a photo-log page with nothing printed on it stays unreadable no
    matter how many times OCR runs — so this returns `local_path` unchanged
    instead of spending up to `timeout` seconds for zero gain. (This only
    skips the SUBPROCESS CALL; it does not affect needs_ocr()'s OWN verdict,
    so a caller checking needs_ocr() again afterward still correctly sees
    True for the unrecoverable page — see scripts/backfill_mirror_ocr.py's
    `process_candidate()` for how that's used to decide whether a PARTIAL
    improvement elsewhere in the same document is still worth uploading.)

    `local_path` itself is NEVER modified — some callers (Ridge Wood's
    content hash, the ADR 066 provenance manifest) depend on the original
    bytes staying untouched. The caller owns the returned path's lifecycle:
    if it differs from `local_path`, the caller must delete it once it's
    done (archive_client.upload_file() does this itself).

    NEVER RAISES — the entire function body, including temp-file creation
    and binary resolution, runs inside one try/except. Any failure — corrupt
    PDF, missing ocrmypdf binary, non-zero exit, timeout, or an output file
    that's empty/unreadable/has the wrong page count (ocrmypdf claimed
    success but produced something broken) — logs one count-only warning and
    returns `local_path` unchanged: upload the document as-is rather than
    drop it, or upload something ocrmypdf silently corrupted. SEC-001: never
    a filename, title, or exception string in the log line (this repo's
    Actions logs are public) — only a class name / exit code.

    `timeout` defaults to 300s (half of ocr_in_place()'s 600s) — this runs
    inline on every live upload, not as a dedicated backfill batch job, so a
    shorter per-document ceiling bounds how long one slow scan can hold up a
    workflow run. Still generous for a typical EGLE filing (the 2026-10-08
    OCR audit measured 2-10s for a 1-9pp doc, 60-120s for 200pp)."""
    tmp_out = None
    try:
        before_pages = image_only_pages(local_path)
    except Exception:
        log.warning("ocr_util.ensure_searchable: text-layer check failed (class=read_error)")
        return local_path
    if not before_pages:
        return local_path

    try:
        doc = fitz.open(local_path)
        try:
            page_count = len(doc)
            creator = (doc.metadata or {}).get("creator") or ""
        finally:
            doc.close()
        if creator.startswith(_OCRMYPDF_CREATOR_PREFIX):
            # Already ocrmypdf's own output and STILL flagged -- the
            # remaining image-only page(s) are unrecoverable; don't repeat
            # the same failed recognition attempt.
            return local_path

        fd, tmp_out = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        binpath = _ocrmypdf_bin()
        env = _minimal_ocr_env()
        try:
            r = subprocess.run(
                [binpath, "--skip-text", "-l", language, "--output-type", "pdf", local_path, tmp_out],
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            log.warning("ocr_util.ensure_searchable: ocrmypdf timed out (class=timeout timeout_s=%s)", timeout)
            return local_path
        except FileNotFoundError:
            log.warning("ocr_util.ensure_searchable: ocrmypdf binary not found (class=missing_binary)")
            return local_path

        if r.returncode != 0:
            log.warning("ocr_util.ensure_searchable: ocrmypdf exited non-zero (class=nonzero_exit rc=%s)", r.returncode)
            return local_path

        # ocrmypdf claimed success (rc=0) -- verify the output is actually
        # usable before trusting it over the original: non-empty, openable,
        # the same page count (ocrmypdf should never add/remove pages), and
        # no page that HAD text lost it (--skip-text should make this
        # structurally impossible, but this is the live upload path -- worth
        # the cheap re-check rather than trusting that invariant blindly).
        if not os.path.exists(tmp_out) or os.path.getsize(tmp_out) == 0:
            log.warning("ocr_util.ensure_searchable: ocrmypdf produced an empty output (class=empty_output)")
            return local_path
        try:
            out_doc = fitz.open(tmp_out)
            try:
                out_page_count = len(out_doc)
            finally:
                out_doc.close()
            after_pages = image_only_pages(tmp_out)
        except Exception:
            log.warning("ocr_util.ensure_searchable: ocrmypdf output is not a readable PDF (class=unreadable_output)")
            return local_path
        if out_page_count != page_count:
            log.warning("ocr_util.ensure_searchable: ocrmypdf output page count changed (class=page_count_mismatch)")
            return local_path
        if after_pages - before_pages:
            log.warning("ocr_util.ensure_searchable: a page that had text lost it (class=lost_text)")
            return local_path

        result = tmp_out
        tmp_out = None  # ownership transfers to the caller; don't clean it up below
        return result
    except Exception:
        log.warning("ocr_util.ensure_searchable: unexpected failure (class=unexpected_error)")
        return local_path
    finally:
        _cleanup(tmp_out)
