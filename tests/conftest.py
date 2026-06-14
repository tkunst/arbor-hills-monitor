"""Shared fixtures. Synthetic PDFs are built in-process with PyMuPDF — no PDFs
are ever committed (the data-guard CI check blocks *.pdf)."""
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
