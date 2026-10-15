"""egle_doc_parser: a page MuPDF cannot read must not make the whole document
unparseable (coder:woi-layout-guard). Hermetic: the damaged page is simulated."""

import egle_doc_parser as e


class _Doc:
    """A fitz-like document whose page `bad` raises on access."""
    def __init__(self, texts, bad):
        self.texts, self.bad = texts, bad

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, i):
        if i == self.bad:
            raise RuntimeError("cannot find page 71 in page tree")
        t = self.texts[i]
        return type("P", (), {"get_text": lambda self_: t})()


def test_page_text_returns_empty_for_a_damaged_page():
    d = _Doc(["a", "b", "c"], bad=1)
    assert [e._page_text(d, i) for i in range(3)] == ["a", "", "c"]


def test_extract_text_survives_a_damaged_page():
    d = _Doc(["cover", "temperature exceedance", "x"], bad=2)
    text, windowed = e.extract_text_for_classification(d, ["temperature"], page_threshold=30)
    assert windowed is False and "cover" in text and "temperature exceedance" in text
    big = _Doc(["cover"] + ["filler"] * 40 + ["temperature here"], bad=5)
    text, windowed = e.extract_text_for_classification(big, ["temperature"], page_threshold=30)
    assert windowed is True and "temperature here" in text


def test_needs_ocr_gate_fails_open_on_read_error(monkeypatch):
    def boom(path):
        raise RuntimeError("FzErrorFormat")
    monkeypatch.setattr(e, "needs_ocr", boom)
    assert e._needs_ocr_fail_open("x.pdf") is False


def test_extract_document_date_survives_damaged_first_page():
    d = _Doc(["", "October 1, 2026"], bad=0)
    assert e.extract_document_date(d, max_pages=2) == ("2026-10-01", "dateline")
