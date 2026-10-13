"""Shared fixtures. Synthetic PDFs are built in-process with PyMuPDF — no PDFs
are ever committed (the data-guard CI check blocks *.pdf)."""
import hashlib
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MANIFEST_REL = os.path.join("data", "provenance", "ridgewood-manifest.json")
_RIDGEWOOD_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _MANIFEST_REL)


def _read_bytes(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def _digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _committed_ridgewood_manifest_untouched():
    """Guard: the test suite must never create, modify or delete the committed
    Ridge Wood provenance manifest (the monthly workflow owns it; its git history
    is the tamper-evidence). ridgewood_reverify.MANIFEST_PATH is cwd-relative, so
    the cwd-relative copy is watched too when pytest runs from elsewhere. On a
    leak the original bytes are restored (so a mutated manifest can't be
    committed by accident) and the session fails at teardown."""
    paths = {_RIDGEWOOD_MANIFEST, os.path.abspath(_MANIFEST_REL)}
    watched = [p for path in sorted(paths) for p in (path, path + ".tmp")]
    before = {p: _read_bytes(p) for p in watched}
    yield
    changed = [p for p in watched if _read_bytes(p) != before[p]]
    if not changed:
        return
    for p in changed:
        if before[p] is None:
            os.remove(p)
        else:
            with open(p, "wb") as fh:
                fh.write(before[p])
    details = "; ".join(
        f"{p} (sha256 {_digest(before[p])} -> changed, restored)" for p in changed)
    pytest.fail(f"test suite modified the Ridge Wood manifest: {details}. "
                f"Tests must use a tmp_path manifest.", pytrace=False)


@pytest.fixture
def text_pdf(tmp_path):
    """A born-digital PDF with a real text layer (classify -> has_text)."""
    path = str(tmp_path / "text.pdf")
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text(
            (72, 72),
            "Arbor Hills Landfill compliance report. " * 12
            + f"\nPage {i} of routine monitoring narrative text.",
        )
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def image_pdf(tmp_path):
    """An image-only PDF, no text layer (classify -> needs_ocr)."""
    path = str(tmp_path / "image.pdf")
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 300))
    pix.clear_with(200)
    page.insert_image(fitz.Rect(50, 50, 250, 250), pixmap=pix)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def mixed_pdf(tmp_path):
    """A 2-page document: page 0 is real text, page 1 is image-only (no text
    layer at all). coder:mirror-ocr (2026-10-13) regression fixture: a
    whole-document, sampled verdict (classify()) reads this as "has_text"
    overall (page 0 clears the threshold), so the OLD gate never OCR'd page
    1 — ocr_util.needs_ocr() checks every page and catches it."""
    path = str(tmp_path / "mixed.pdf")
    doc = fitz.open()
    page0 = doc.new_page()
    page0.insert_text((72, 72), "Arbor Hills Landfill compliance report. " * 12)
    page1 = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 120))
    pix.clear_with(140)
    page1.insert_image(fitz.Rect(10, 10, 110, 110), pixmap=pix)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def blank_page_pdf(tmp_path):
    """A 2-page document: page 0 is real text, page 1 is a GENUINE blank
    separator page -- no text, no image, nothing on it. needs_ocr() must NOT
    flag this page (there's nothing for ocrmypdf to add), or every upload of
    a document with a blank page would re-trigger an OCR subprocess call,
    breaking the "already-searchable files pass straight through" promise."""
    path = str(tmp_path / "blank_page.pdf")
    doc = fitz.open()
    page0 = doc.new_page()
    page0.insert_text((72, 72), "Arbor Hills Landfill compliance report. " * 12)
    doc.new_page()  # page 1: truly blank
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def large_mixed_pdf_scan_after_page_12(tmp_path):
    """A 20-page document: pages 0-11 are real text (enough that
    classify()'s FIRST-12-PAGES sample reads "has_text" overall), and page
    15 -- past that 12-page sample window -- is image-only with NO text
    layer. Regression fixture for the 2026-10-13 finding: a real 1,006-page
    WOI/CJ compilation had 316 image-only pages, ALL past page 12, that
    classify()'s whole-document verdict missed entirely (ocr_applied stayed
    False, those 316 pages' content never reached the classifier).
    ocr_util.needs_ocr() checks every page and catches it; parse_document()
    now gates on needs_ocr(), not classify()."""
    path = str(tmp_path / "large_mixed_scan_after_12.pdf")
    doc = fitz.open()
    for i in range(20):
        page = doc.new_page()
        if i == 15:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100))
            pix.clear_with(150)
            page.insert_image(fitz.Rect(10, 10, 90, 90), pixmap=pix)
            # deliberately NO insert_text call -- this page has no text layer
        else:
            page.insert_text(
                (72, 72),
                f"Page {i} of a WOI/CJ compilation. " * 10 + "Routine narrative text here.",
            )
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def large_pdf(tmp_path):
    """A 50-page text PDF. The cover page (0) plus a handful of interior pages
    contain the 'temperature' signal keyword; the rest are filler. Used to test
    the >30-page keyword-windowing branch."""
    path = str(tmp_path / "large.pdf")
    doc = fitz.open()
    keyword_pages = {3, 7, 11, 19, 27}
    for i in range(50):
        page = doc.new_page()
        if i == 0:
            page.insert_text((72, 72), "WOI Status Report — cover and summary page.")
        elif i in keyword_pages:
            page.insert_text(
                (72, 72),
                f"Well AHW{200 + i} measured temperature 152 F on this page. "
                "Exceedance noted.",
            )
        else:
            page.insert_text((72, 72), f"Filler page {i} with no signal content here.")
    doc.save(path)
    doc.close()
    return path, sorted({0} | keyword_pages)
