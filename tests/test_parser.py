"""Parser tests: text-layer detection, keyword windowing, and end-to-end
assembly with the Claude call mocked (no API key needed)."""
import fitz
import pytest

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
        lambda *a, **k: _fake_classification(),
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


def test_parse_document_threads_deadlines(monkeypatch, text_pdf):
    # ADR 025: the generic `deadlines` field flows from the classifier into ParsedDoc.
    dl = [{"item_description": "Submit corrective action plan",
           "due_date": "2025-03-15", "extension_due_date": None,
           "actual_completion_date": None, "compelled_by": "VN-011821 (2024-10-10)",
           "compliance_doc_effective_date": "2024-10-10"}]
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda *a, **k: _fake_classification(deadlines=dl),
    )
    parsed = p.parse_document(text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.deadlines == dl


def test_parse_document_defaults_deadlines_empty(monkeypatch, text_pdf):
    # A classification with no `deadlines` key (older shape) yields [] — never None.
    monkeypatch.setattr(
        p, "_classify_with_claude", lambda *a, **k: _fake_classification())
    parsed = p.parse_document(text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.deadlines == []


# ---------------------------------------------------------------------------
# ADR 065: deterministic document-date extractor (pure — no PDF needed)
# ---------------------------------------------------------------------------

def test_extract_document_date_plain_dateline():
    assert p.extract_document_date_from_text(
        ["Some header text.\nAugust 27, 2026\nDear David Seegert:"]
    ) == ("2026-08-27", "dateline")


def test_extract_document_date_weekday_prefixed_dateline():
    assert p.extract_document_date_from_text(
        ["Monday, December 23, 2019\nADS (5092.008) /5092.008"]
    ) == ("2019-12-23", "dateline")


def test_extract_document_date_date_label():
    assert p.extract_document_date_from_text(
        ["Report cover page\nDATE: 12/23/2019\nmore text"]
    ) == ("2019-12-23", "date_label")


def test_extract_document_date_email_sent_header():
    email = (
        "From: Diane Kavanaugh Vetort\n"
        "Sent: Monday, December 23, 2019 4:11 PM\n"
        "To: Mala Hettiarachchi\n"
        "Subject: FW: Compost Pond Samples\n"
    )
    assert p.extract_document_date_from_text([email]) == ("2019-12-23", "email_sent_header")


def test_extract_document_date_later_page_overrides_earlier_page():
    # A real EGLE letter had a mismatched page-1 cover date vs. its own page-2
    # running header -- the later page's date is the one treated as authoritative.
    page1 = "July 10, 2026\nVIA EMAIL\nDavid Seegert, General Manager"
    page2 = "VIOLATION NOTICE\nArbor Hills Landfill, Inc. (N2688)\nPage 2\nJuly 8, 2026"
    assert p.extract_document_date_from_text([page1, page2]) == ("2026-07-08", "dateline")


def test_extract_document_date_continuation_page_does_not_blank_candidate():
    page1 = "June 14, 2021\nViolation Notice No. VN-011821"
    page2 = "This body paragraph has no date-shaped text on it at all."
    assert p.extract_document_date_from_text([page1, page2]) == ("2021-06-14", "dateline")


def test_extract_document_date_never_scans_bare_table_dates():
    # Confirmed on a real RA NPDES lab-data table: "Date" column header +
    # bare M/D/YYYY sample dates, no month name, no label -- must NOT be
    # read as the document's own date (the exact trap the handoff flags).
    table_page = (
        "Date\nAmmonia (mg/L)\nArsenic (ug/L)\n"
        "1/6/2020\n-\n-\n-\n8.63\n1/24/2020\n0.66\n7.7\n"
    )
    assert p.extract_document_date_from_text([table_page]) == ("", "")


def test_extract_document_date_no_date_anywhere_is_blank():
    assert p.extract_document_date_from_text(["No date-shaped text here at all."]) == ("", "")


def test_is_plausible_iso_date():
    assert p._is_plausible_iso_date("2026-08-27") is True
    assert p._is_plausible_iso_date("2099-01-01") is False  # implausibly far future
    assert p._is_plausible_iso_date("1899-01-01") is False  # implausibly old
    assert p._is_plausible_iso_date("not a date") is False
    assert p._is_plausible_iso_date(None) is False
    assert p._is_plausible_iso_date("") is False


# ---------------------------------------------------------------------------
# ADR 065: parse_document wiring — document_date (deterministic-wins-over-LLM)
# and title_is_generic -> display_title
# ---------------------------------------------------------------------------

@pytest.fixture
def dated_pdf(tmp_path):
    """A single-page PDF whose own text carries a real dateline, so the
    deterministic pass finds something (unlike `text_pdf`, which has none)."""
    path = str(tmp_path / "dated.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "August 27, 2026\nDear David Seegert:\nSUBJECT: Extension approval.")
    doc.save(path)
    doc.close()
    return path


def test_parse_document_deterministic_date_wins_over_llm(monkeypatch, dated_pdf):
    # The model proposes a DIFFERENT date; the deterministic pass must win.
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda *a, **k: _fake_classification(document_date="2020-01-01"))
    parsed = p.parse_document(dated_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.document_date == "2026-08-27"
    assert parsed.document_date_method == "dateline"


def test_parse_document_llm_date_is_fallback_when_deterministic_finds_nothing(monkeypatch, text_pdf):
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda *a, **k: _fake_classification(document_date="2026-08-27"))
    parsed = p.parse_document(text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.document_date == "2026-08-27"
    assert parsed.document_date_method == "llm"


def test_parse_document_implausible_llm_date_is_rejected(monkeypatch, text_pdf):
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda *a, **k: _fake_classification(document_date="2099-12-31"))
    parsed = p.parse_document(text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.document_date == ""
    assert parsed.document_date_method == ""


def test_parse_document_no_date_anywhere_is_blank(monkeypatch, text_pdf):
    monkeypatch.setattr(
        p, "_classify_with_claude", lambda *a, **k: _fake_classification())
    parsed = p.parse_document(text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.document_date == ""
    assert parsed.document_date_method == ""


def test_parse_document_title_is_generic_true_surfaces_display_title(monkeypatch, text_pdf):
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda *a, **k: _fake_classification(display_title="A real descriptive title"))
    parsed = p.parse_document(
        text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS, title_is_generic=True)
    assert parsed.display_title == "A real descriptive title"


def test_parse_document_title_is_generic_false_never_surfaces_display_title(monkeypatch, text_pdf):
    # Defense in depth: even if the model returns a display_title when it
    # wasn't asked for one, parse_document must not surface it.
    monkeypatch.setattr(
        p, "_classify_with_claude",
        lambda *a, **k: _fake_classification(display_title="Should never appear"))
    parsed = p.parse_document(
        text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS, title_is_generic=False)
    assert parsed.display_title == ""


def test_classify_with_claude_meta_line_carries_title_is_generic_flag(monkeypatch):
    # _classify_with_claude builds the per-document user message itself (the
    # system prompt is cached/shared across docs, so the flag can't live
    # there) -- pin that the flag actually reaches the API call.
    captured = {}

    class _FakeResponse:
        parsed_output = None
        stop_reason = "end_turn"

    class _FakeMessages:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeClient:
        messages = _FakeMessages()

    with pytest.raises(RuntimeError):
        p._classify_with_claude(
            "doc text", {"document_name": "Site"}, RISK_REGISTER, "model",
            client=_FakeClient(), title_is_generic=True)
    user_content = captured["messages"][0]["content"]
    assert "Title is generic (propose a display_title): yes" in user_content
