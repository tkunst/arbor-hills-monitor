"""Parser tests: text-layer detection, keyword windowing, and end-to-end
assembly with the Claude call mocked (no API key needed)."""
import fitz

import egle_doc_parser as p
from risk_register import SIGNAL_KEYWORDS, RISK_REGISTER


def test_classify_text_pdf(text_pdf):
    verdict, npages, cpp = p.classify(text_pdf)
    assert verdict == "has_text"
    assert npages == 2
    assert cpp > 40


def test_classify_image_pdf(image_pdf):
    verdict, npages, _ = p.classify(image_pdf)
    assert verdict == "needs_ocr"
    assert npages == 1


def test_extract_small_doc_is_full_text(text_pdf):
    doc = fitz.open(text_pdf)
    text, windowed = p.extract_text_for_classification(doc, SIGNAL_KEYWORDS, 30, 10)
    doc.close()
    assert windowed is False
    assert "compliance report" in text


def test_extract_large_doc_windows(large_pdf):
    path, keyword_pages = large_pdf
    doc = fitz.open(path)
    text, windowed = p.extract_text_for_classification(doc, SIGNAL_KEYWORDS, 30, 10)
    doc.close()
    assert windowed is True
    assert "LARGE DOCUMENT: 50 pages" in text
    assert "cover and summary page" in text          # page 0 always included
    assert "measured temperature 152 F" in text       # keyword pages included
    # A filler page that has no keyword must not be pulled in.
    assert "Filler page 2 " not in text


def test_extract_large_doc_caps_keyword_pages(large_pdf):
    path, _ = large_pdf
    doc = fitz.open(path)
    # Force every page to "match" by using a keyword present on all pages, with a
    # cap of 4 — should select cover + 4 = 5 page markers, no more.
    text, windowed = p.extract_text_for_classification(doc, ["page"], 30, 4)
    doc.close()
    assert windowed is True
    assert text.count("--- page ") <= 5


def _fake_classification(**over):
    base = {
        "summary": "A summary.",
        "key_data_point": "180F permitted ceiling for AHW263.",
        "doc_type": "evidence",
        "risks": ["R8", "R99"],  # R99 is invalid and must be filtered out
        "severity": "notable",
        "measurements": [
            {"metric": "temperature", "value": 180, "unit": "F",
             "basis": "permitted_limit", "well_id": "AHW263"},
        ],
    }
    base.update(over)
    return base


def test_parse_document_assembles_full_dataclass(monkeypatch, text_pdf):
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda text, metadata, rr, model, client=None: _fake_classification(),
    )
    meta = {"document_name": "Test Doc", "date_filed": "2025-02-05", "type_name": "VN"}
    parsed = p.parse_document(text_pdf, meta, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)

    assert parsed.doc_type == "evidence"
    assert parsed.risks == ["R8"]            # R99 filtered against the register
    assert parsed.ocr_applied is False        # text pdf — no OCR
    assert parsed.page_count == 2
    assert parsed.full_text                    # populated locally, not by the model
    assert parsed.measurements[0]["basis"] == "permitted_limit"


def test_parse_document_filters_unknown_risks(monkeypatch, text_pdf):
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda *a, **k: _fake_classification(risks=["R1", "ZZ", "R4"]),
    )
    parsed = p.parse_document(text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.risks == ["R1", "R4"]
