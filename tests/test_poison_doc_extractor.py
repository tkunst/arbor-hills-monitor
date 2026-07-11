"""poison_doc_extractor.py — .msg / .docx extraction + PDF synthesis (ADR 011).

Hermetic: .msg parsing is mocked (extract_msg's real interface is a complex
OLE2 binary format, not something to hand-construct in a test file — a fake
Message double matching the attributes we actually use is the standard
approach here, same spirit as _Session fakes elsewhere). .docx and .xlsx
fixtures ARE synthesized in-process for real (both are just zip+XML, cheap
and correctness-meaningful to build for real) — no real .msg/.docx/.xls
binaries are ever committed (same rule as PDFs, see CLAUDE.md)."""
import io
import zipfile
from contextlib import contextmanager

import fitz
import pytest

import poison_doc_extractor as pde
from egle_doc_parser import classify

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------
# fixtures / doubles
# ---------------------------------------------------------------------------


def _make_docx(paragraphs: list) -> bytes:
    """A minimal real .docx: a zip with just enough word/document.xml to be
    parseable by _docx_body_text. Not a full OOXML package (no
    [Content_Types].xml etc.) — _docx_body_text only ever reads
    word/document.xml, so that's all this needs to provide."""
    body = "".join(
        f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body>{body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _make_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


class _FakeAttachment:
    def __init__(self, name: str, data: bytes):
        self.longFilename = name
        self.shortFilename = name
        self.data = data


class _FakeMsg:
    def __init__(self, sender="a@b.com", to="c@d.com", date="2020-01-01",
                 subject="Subject", body="Body text.", attachments=None):
        self.sender = sender
        self.to = to
        self.date = date
        self.subject = subject
        self.body = body
        self.attachments = attachments or []

    def close(self):
        pass


@contextmanager
def _fake_open_msg(fake: _FakeMsg):
    yield fake


def _patch_msg(monkeypatch, fake: _FakeMsg):
    """extract_msg is imported lazily inside _msg_to_pdf, so patch the real
    module's attribute — the local `import extract_msg` picks up the patched
    version at call time."""
    import extract_msg

    monkeypatch.setattr(extract_msg, "openMsg", lambda data, **kw: _fake_open_msg(fake))


# ---------------------------------------------------------------------------
# sniff_format
# ---------------------------------------------------------------------------


def test_sniff_ole2_is_msg():
    assert pde.sniff_format(pde.OLE2_MAGIC + b"...") == "msg"


def test_sniff_docx_zip_with_document_xml():
    data = _make_docx(["hello"])
    assert pde.sniff_format(data) == "docx"


def test_sniff_zip_without_document_xml_is_unsupported():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("not_word/foo.xml", "<x/>")
    assert pde.sniff_format(buf.getvalue()) is None


def test_sniff_random_bytes_is_unsupported():
    assert pde.sniff_format(b"MZ\x90\x00random garbage") is None


def test_sniff_malformed_zip_magic_is_unsupported():
    assert pde.sniff_format(pde.ZIP_MAGIC + b"not actually a zip") is None


# ---------------------------------------------------------------------------
# .docx
# ---------------------------------------------------------------------------


def test_docx_body_text_extracts_paragraphs():
    data = _make_docx(["First paragraph.", "Second paragraph."])
    text = pde._docx_body_text(data)
    assert text == "First paragraph.\nSecond paragraph."


def test_docx_to_pdf_produces_classifiable_text(tmp_path):
    data = _make_docx(["Compliance finding: un-permitted discharge to groundwater."] * 5)
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(data, dest)
    verdict, n, cpp = classify(dest)
    assert verdict == "has_text"
    assert n >= 1
    assert cpp > 0


def test_docx_empty_body_raises_extraction_error():
    data = _make_docx([])
    with pytest.raises(pde.ExtractionError, match="empty"):
        pde.synthesize_pdf(data, "/dev/null")


# ---------------------------------------------------------------------------
# .msg — envelope
# ---------------------------------------------------------------------------


def test_msg_envelope_only_produces_has_text_pdf(monkeypatch, tmp_path):
    _patch_msg(monkeypatch, _FakeMsg(body="EGLE found an un-permitted discharge. " * 20))
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)
    verdict, n, cpp = classify(dest)
    assert verdict == "has_text"
    assert n >= 1


def test_msg_envelope_includes_sender_subject_body(monkeypatch, tmp_path):
    fake = _FakeMsg(sender="rburns@michigan.gov", subject="Groundwater Recon", body="See attached.")
    _patch_msg(monkeypatch, fake)
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)
    doc = fitz.open(dest)
    full_text = doc[0].get_text()
    doc.close()
    assert "rburns@michigan.gov" in full_text
    assert "Groundwater Recon" in full_text
    assert "See attached." in full_text


# ---------------------------------------------------------------------------
# .msg — attachments
# ---------------------------------------------------------------------------


def test_msg_pdf_attachment_pages_are_merged(monkeypatch, tmp_path):
    attach_pdf = _make_pdf_bytes("Lab report page one.")
    fake = _FakeMsg(attachments=[_FakeAttachment("report.pdf", attach_pdf)])
    _patch_msg(monkeypatch, fake)
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)
    doc = fitz.open(dest)
    # 1 envelope page + 1 merged attachment page.
    assert len(doc) == 2
    assert "Lab report page one." in doc[1].get_text()
    doc.close()


def _make_scanned_pdf_bytes() -> bytes:
    """A PDF page with an image and no text layer — a stand-in for a real
    scanned attachment (e.g. a photographed inspection form)."""
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60))
    pix.clear_with(120)
    page.insert_image(page.rect, pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def test_msg_scanned_pdf_attachment_sets_needs_ocr(monkeypatch):
    # Bug found 2026-07-11: the PDF-merge branch always returned False,
    # so a scanned (image-only) PDF attachment never triggered the proactive
    # OCR pass — its content could be silently lost in a doc that otherwise
    # reads has_text overall, the same failure class as a raw image
    # attachment, just not caught for merged PDF pages.
    fake = _FakeMsg(attachments=[_FakeAttachment("scan.pdf", _make_scanned_pdf_bytes())])
    _patch_msg(monkeypatch, fake)
    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    assert needs_ocr is True
    doc.close()


def test_msg_text_pdf_attachment_does_not_force_ocr(monkeypatch):
    # A real text PDF attachment (the common case) must not spuriously
    # trigger needs_ocr — ocrmypdf --skip-text would just be extra work.
    fake = _FakeMsg(attachments=[_FakeAttachment("report.pdf", _make_pdf_bytes("Real text content here."))])
    _patch_msg(monkeypatch, fake)
    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    assert needs_ocr is False
    doc.close()


def test_msg_image_attachment_sets_needs_ocr_and_is_ocrd(monkeypatch, tmp_path):
    # A tiny in-memory JPEG (solid color) via fitz — real image bytes, not a
    # stub, so insert_image + the real ocrmypdf binary both have something
    # legitimate to operate on.
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.clear_with(180)
    jpeg_bytes = pix.tobytes("jpg")

    fake = _FakeMsg(attachments=[_FakeAttachment("photo.jpg", jpeg_bytes)])
    _patch_msg(monkeypatch, fake)

    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    assert needs_ocr is True
    assert len(doc) == 2  # envelope + image page
    doc.close()


def test_synthesize_pdf_invokes_ocr_when_needed(monkeypatch, tmp_path):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.clear_with(180)
    fake = _FakeMsg(attachments=[_FakeAttachment("photo.jpg", pix.tobytes("jpg"))])
    _patch_msg(monkeypatch, fake)

    calls = []
    import egle_doc_parser as edp
    monkeypatch.setattr(edp, "ocr_in_place", lambda path, **kw: calls.append(path) or True)

    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)
    assert calls == [dest]


def test_synthesize_pdf_text_only_never_invokes_ocr(monkeypatch, tmp_path):
    _patch_msg(monkeypatch, _FakeMsg())
    calls = []
    import egle_doc_parser as edp
    monkeypatch.setattr(edp, "ocr_in_place", lambda path, **kw: calls.append(path) or True)

    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)
    assert calls == []


def test_synthesize_pdf_ocr_failure_does_not_raise(monkeypatch, tmp_path):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.clear_with(180)
    fake = _FakeMsg(attachments=[_FakeAttachment("photo.jpg", pix.tobytes("jpg"))])
    _patch_msg(monkeypatch, fake)

    import egle_doc_parser as edp

    def _boom(path, **kw):
        raise RuntimeError("ocrmypdf not installed")

    monkeypatch.setattr(edp, "ocr_in_place", _boom)
    dest = str(tmp_path / "out.pdf")
    result = pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)  # must not raise
    assert result == dest


def test_msg_docx_attachment_extracts_real_text(monkeypatch, tmp_path):
    # Bug found 2026-07-11: a .docx attachment previously fell through to the
    # generic UTF-8-decode-raw-bytes branch, producing binary noise ("PK\x03
    # \x04...word/document.xml...") instead of the actual letter text — and
    # either got inserted as garbage or silently dropped depending on length.
    # _docx_body_text() already existed for top-level .docx docs; attachments
    # just never routed through it.
    docx_bytes = _make_docx(["EGLE Compliance Communication CC-001168.",
                              "Un-permitted discharge finding at the compost pond."])
    fake = _FakeMsg(attachments=[_FakeAttachment("compliance_letter.docx", docx_bytes)])
    _patch_msg(monkeypatch, fake)
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)

    doc = fitz.open(dest)
    full_text = "\n".join(p.get_text() for p in doc)
    doc.close()
    assert "Un-permitted discharge finding at the compost pond." in full_text
    # The old failure mode: raw zip bytes decoded as text.
    assert "PK\x03\x04" not in full_text
    assert "word/document.xml" not in full_text


def test_msg_xlsx_attachment_becomes_text_table(monkeypatch, tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ParameterName", "Result", "Units"])
    ws.append(["E. coli", 4700, "CFU/100mL"])
    buf = io.BytesIO()
    wb.save(buf)

    fake = _FakeMsg(attachments=[_FakeAttachment("lab.xlsx", buf.getvalue())])
    _patch_msg(monkeypatch, fake)
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", dest)

    doc = fitz.open(dest)
    full_text = "\n".join(p.get_text() for p in doc)
    doc.close()
    assert "E. coli" in full_text
    assert "4700" in full_text


def test_msg_tiny_text_attachment_is_skipped(monkeypatch, tmp_path):
    # Outlook's inline-image content-ID sidecars are a handful of bytes —
    # not real content, shouldn't add a page.
    fake = _FakeMsg(attachments=[_FakeAttachment("ATT00001.txt", b"abc123")])
    _patch_msg(monkeypatch, fake)
    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    assert len(doc) == 1  # envelope only
    assert needs_ocr is False
    doc.close()


def test_msg_attachment_with_no_data_is_skipped(monkeypatch):
    # Embedded-message attachments (MSG-in-MSG) expose no .data — must not crash.
    class _NoDataAttachment:
        longFilename = "embedded.msg"
        data = None

    fake = _FakeMsg(attachments=[_NoDataAttachment()])
    _patch_msg(monkeypatch, fake)
    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    assert len(doc) == 1  # envelope only, attachment silently skipped
    doc.close()


def test_msg_one_bad_attachment_does_not_sink_the_whole_doc(monkeypatch):
    class _ExplodingAttachment:
        longFilename = "bad.pdf"
        data = b"not actually a pdf despite the name"

    good = _FakeAttachment("note.xlsx", b"also not real, will fail spreadsheet parsing")
    fake = _FakeMsg(attachments=[_ExplodingAttachment(), good])
    _patch_msg(monkeypatch, fake)
    # Both attachments are malformed and will raise inside _add_attachment —
    # the envelope page must still come through.
    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    assert len(doc) == 1
    doc.close()


def test_msg_bad_attachment_failure_is_logged(monkeypatch, capsys):
    # A skipped attachment used to fail completely silently — no way to tell
    # from run logs which attachment failed or why.
    class _ExplodingAttachment:
        longFilename = "bad.pdf"
        data = b"not actually a pdf despite the name"

    fake = _FakeMsg(attachments=[_ExplodingAttachment()])
    _patch_msg(monkeypatch, fake)
    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    doc.close()
    out = capsys.readouterr().out
    assert "bad.pdf" in out
    assert "poison-doc-extractor" in out


def test_msg_image_insert_failure_leaves_no_orphaned_blank_page(monkeypatch):
    # A .jpg-named attachment whose bytes aren't actually a valid image must
    # not leave a blank page behind when insert_image raises.
    fake = _FakeMsg(attachments=[_FakeAttachment("photo.jpg", b"not actually image bytes")])
    _patch_msg(monkeypatch, fake)
    doc, needs_ocr = pde._msg_to_pdf(pde.OLE2_MAGIC + b"...")
    assert len(doc) == 1  # envelope only — no orphaned blank page from the failed insert
    doc.close()


# ---------------------------------------------------------------------------
# synthesize_pdf — dispatch / errors
# ---------------------------------------------------------------------------


def test_synthesize_pdf_unsupported_format_raises():
    with pytest.raises(pde.ExtractionError, match="unsupported"):
        pde.synthesize_pdf(b"random unsupported bytes", "/dev/null")


def test_synthesize_pdf_msg_extraction_failure_becomes_extraction_error(monkeypatch):
    import extract_msg

    def _boom(data, **kw):
        raise RuntimeError("StandardViolationError: not a real msg")

    monkeypatch.setattr(extract_msg, "openMsg", _boom)
    with pytest.raises(pde.ExtractionError, match="extraction failed"):
        pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", "/dev/null")


# ---------------------------------------------------------------------------
# _add_text_page — pagination correctness
# ---------------------------------------------------------------------------


def test_add_text_page_wraps_long_text_across_multiple_pages():
    doc = fitz.open()
    long_text = "word " * 3000  # far more than one page can hold
    pde._add_text_page(doc, long_text)
    assert len(doc) > 1
    # Every page actually has retrievable text — the exact bug this module's
    # manual line-wrap was written to avoid (insert_textbox silently
    # rendering nothing once its internal capacity was exceeded).
    for page in doc:
        assert len(page.get_text().strip()) > 0
    doc.close()


def test_add_text_page_short_text_fits_one_page():
    doc = fitz.open()
    pde._add_text_page(doc, "Short text.")
    assert len(doc) == 1
    assert "Short text." in doc[0].get_text()
    doc.close()
