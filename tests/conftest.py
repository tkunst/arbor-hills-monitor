"""Shared fixtures. Synthetic PDFs are built in-process with PyMuPDF — no PDFs
are ever committed (the data-guard CI check blocks *.pdf)."""
import hashlib
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RIDGEWOOD_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "provenance", "ridgewood-manifest.json")


def _file_digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _committed_ridgewood_manifest_untouched():
    """Guard: the test suite must never create, modify or delete the committed
    Ridge Wood provenance manifest (the monthly workflow owns it; its git history
    is the tamper-evidence). Fails the session at teardown if it changed."""
    before = _file_digest(_RIDGEWOOD_MANIFEST)
    yield
    after = _file_digest(_RIDGEWOOD_MANIFEST)
    leftover_tmp = os.path.exists(_RIDGEWOOD_MANIFEST + ".tmp")
    if after != before or leftover_tmp:
        pytest.fail(
            f"test suite modified the committed {_RIDGEWOOD_MANIFEST} "
            f"(sha256 {before} -> {after}, leftover .tmp={leftover_tmp}); "
            f"tests must use a tmp_path manifest", pytrace=False)


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
