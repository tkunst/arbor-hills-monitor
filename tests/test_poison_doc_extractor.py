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


def _make_docx_raw_body(body_xml: str) -> bytes:
    """Like _make_docx but takes the raw <w:body> inner XML directly, for
    tests that need explicit control over run/tab/br structure within a
    paragraph (multi-run paragraphs, <w:tab/>, <w:br/>)."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body>{body_xml}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _make_docx_with_document_xml(document_xml: str) -> bytes:
    """Like _make_docx but takes the ENTIRE word/document.xml (including any
    XML prolog / DOCTYPE), for the XXE tests that need a malicious DTD ahead of
    the root element — something the fixed-prolog helpers above can't express."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", document_xml)
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


def test_sniff_zip_without_document_xml_is_zipbundle():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("not_word/foo.xml", "<x/>")
    assert pde.sniff_format(buf.getvalue()) == "zipbundle"


def test_sniff_empty_zip_is_unsupported():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass  # zero entries — nothing for a zipbundle to extract
    assert pde.sniff_format(buf.getvalue()) is None


def test_sniff_random_bytes_is_unsupported():
    assert pde.sniff_format(b"MZ\x90\x00random garbage") is None


def test_sniff_malformed_zip_magic_is_unsupported():
    assert pde.sniff_format(pde.ZIP_MAGIC + b"not actually a zip") is None


def test_sniff_jpeg_is_image():
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
    pix.clear_with(90)
    assert pde.sniff_format(pix.tobytes("jpg")) == "image"


def test_sniff_png_is_image():
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
    pix.clear_with(90)
    assert pde.sniff_format(pix.tobytes("png")) == "image"


# ---------------------------------------------------------------------------
# .docx
# ---------------------------------------------------------------------------


def test_docx_body_text_extracts_paragraphs():
    data = _make_docx(["First paragraph.", "Second paragraph."])
    text = pde._docx_body_text(data)
    assert text == "First paragraph.\nSecond paragraph."


def test_docx_body_text_preserves_tabs_between_runs():
    # Bug found 2026-07-11 in code review, confirmed against the real
    # hand-pull specimens (up to 34 <w:tab/> elements in a single real
    # document): a tabular value split across separate <w:t> runs around a
    # <w:tab/> (Word's normal representation of "Well AHW272R4<TAB>180F")
    # previously glued into "Well AHW272R4180F" with no separator.
    data = _make_docx_raw_body(
        "<w:p><w:r><w:t>Well AHW272R4</w:t></w:r><w:r><w:tab/></w:r>"
        "<w:r><w:t>180F</w:t></w:r></w:p>"
    )
    assert pde._docx_body_text(data) == "Well AHW272R4\t180F"


def test_docx_body_text_preserves_line_breaks_within_a_paragraph():
    data = _make_docx_raw_body(
        "<w:p><w:r><w:t>Line one</w:t></w:r><w:r><w:br/></w:r>"
        "<w:r><w:t>Line two</w:t></w:r></w:p>"
    )
    assert pde._docx_body_text(data) == "Line one\nLine two"


def test_docx_body_text_does_not_duplicate_nested_textbox_paragraphs():
    # Bug found 2026-07-11 in code review (confirmed on a real specimen: raw
    # <w:t> had "LIESL EICHLER CLARK" twice, but the buggy extraction had it
    # 4 times). A <w:drawing> anchored inside a run can itself contain a
    # nested <w:p> (a letterhead/signature text box) — walking every
    # descendant of the OUTER paragraph captured that nested text once as
    # part of the outer paragraph, and again when root.iter() reached the
    # nested <w:p> as its own independent paragraph.
    data = _make_docx_raw_body(
        "<w:p><w:r><w:t>Outer text</w:t></w:r>"
        "<w:r><w:drawing><w:txbxContent>"
        "<w:p><w:r><w:t>Inner textbox text</w:t></w:r></w:p>"
        "</w:txbxContent></w:drawing></w:r></w:p>"
    )
    text = pde._docx_body_text(data)
    assert text == "Outer text\nInner textbox text"
    assert text.count("Inner textbox text") == 1


def test_docx_billion_laughs_is_refused_as_poison_not_expanded(tmp_path):
    # The .docx bytes are an untrusted nSITE download. A document.xml carrying
    # internal entity definitions (the classic billion-laughs XML bomb) must be
    # REFUSED by the defusedxml parser (EntitiesForbidden) and surface at the
    # public boundary as an ExtractionError — i.e. a poison strike, never an
    # entity expansion or an uncaught crash. defusedxml raises on the entity
    # *definition* itself, so no deep nesting is needed to trip the guard.
    malicious = _make_docx_with_document_xml(
        '<?xml version="1.0"?>'
        '<!DOCTYPE w:document ['
        '<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        ']>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p><w:r>'
        '<w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>'
    )
    dest = str(tmp_path / "out.pdf")
    with pytest.raises(pde.ExtractionError):
        pde.synthesize_pdf(malicious, dest)


def test_docx_external_entity_xxe_is_refused_as_poison(tmp_path):
    # The XXE that Bandit B314 flags: an external SYSTEM entity that would, with
    # a naive stdlib parser, exfiltrate a local file. defusedxml forbids
    # external entities; the doc must become an ExtractionError (poison strike),
    # and — the point of the test — must NOT read the referenced path.
    malicious = _make_docx_with_document_xml(
        '<?xml version="1.0"?>'
        '<!DOCTYPE w:document ['
        '<!ENTITY xxe SYSTEM "file:///etc/passwd">'
        ']>'
        f'<w:document xmlns:w="{WORD_NS}"><w:body><w:p><w:r>'
        '<w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>'
    )
    dest = str(tmp_path / "out.pdf")
    with pytest.raises(pde.ExtractionError):
        pde.synthesize_pdf(malicious, dest)


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
# bare raster image — a top-level download that's just a photo (2026-07-23
# Arbor Hills Remediation Area gap-doc audit: nSITE's downloadpdf render
# endpoint 400s on some image-sourced records even though the native
# downloadfile endpoint serves the raw JPEG fine).
# ---------------------------------------------------------------------------


def test_image_to_pdf_produces_one_page_and_always_needs_ocr():
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.clear_with(90)
    doc, needs_ocr = pde._image_to_pdf(pix.tobytes("jpg"))
    assert needs_ocr is True
    assert len(doc) == 1
    doc.close()


def test_image_to_pdf_bad_bytes_raises_extraction_error():
    with pytest.raises(pde.ExtractionError, match="image insert failed"):
        pde._image_to_pdf(b"not actually image bytes")


def test_synthesize_pdf_image_invokes_ocr(monkeypatch, tmp_path):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.clear_with(90)

    calls = []
    import egle_doc_parser as edp
    monkeypatch.setattr(edp, "ocr_in_place", lambda path, **kw: calls.append(path) or True)

    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(pix.tobytes("jpg"), dest)
    assert calls == [dest]


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
# generic zip bundle — an nSITE record whose native source is a zip of files
# (e.g. an nForm submission: a confirmation PDF plus attached SDS sheets),
# found 2026-07-23 in the Arbor Hills Remediation Area gap-doc audit.
# ---------------------------------------------------------------------------


def _make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zipbundle_merges_multiple_real_pdfs():
    zbytes = _make_zip({
        "SubmissionDownload.pdf": _make_pdf_bytes("Confirmation of submission HN4-X6HG-53BVQ."),
        "Nitrafix SDS.pdf": _make_pdf_bytes("Nitrafix safety data sheet."),
        "Vitastim 6000 SDS.pdf": _make_pdf_bytes("Vitastim 6000 safety data sheet."),
    })
    doc, needs_ocr = pde._zipbundle_to_pdf(zbytes)
    assert len(doc) == 3
    full_text = "\n".join(p.get_text() for p in doc)
    assert "Confirmation of submission HN4-X6HG-53BVQ." in full_text
    assert "Nitrafix safety data sheet." in full_text
    assert "Vitastim 6000 safety data sheet." in full_text
    assert needs_ocr is False  # all-text PDFs, no image-only pages
    doc.close()


def test_zipbundle_routes_entries_through_the_same_dispatch_as_msg_attachments():
    # A .docx entry (not just PDFs) should get real text extraction, same as
    # a .docx .msg attachment — proves entries share _add_content_by_name,
    # not a parallel/duplicated dispatch.
    docx_bytes = _make_docx(["Zip-bundled compliance letter text."])
    zbytes = _make_zip({"letter.docx": docx_bytes})
    doc, needs_ocr = pde._zipbundle_to_pdf(zbytes)
    full_text = "\n".join(p.get_text() for p in doc)
    assert "Zip-bundled compliance letter text." in full_text
    assert needs_ocr is False
    doc.close()


def test_zipbundle_image_entry_sets_needs_ocr():
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.clear_with(180)
    zbytes = _make_zip({"site_photo.jpg": pix.tobytes("jpg")})
    doc, needs_ocr = pde._zipbundle_to_pdf(zbytes)
    assert needs_ocr is True
    assert len(doc) == 1
    doc.close()


def test_zipbundle_one_bad_entry_does_not_sink_the_whole_bundle():
    zbytes = _make_zip({
        "good.pdf": _make_pdf_bytes("Readable entry."),
        "bad.jpg": b"not actually image bytes",
    })
    doc, needs_ocr = pde._zipbundle_to_pdf(zbytes)
    assert len(doc) == 1  # only the good entry landed
    assert "Readable entry." in doc[0].get_text()
    doc.close()


def test_zipbundle_all_entries_unsupported_raises_extraction_error():
    zbytes = _make_zip({"tiny.xml": "<x/>"})  # below the 20-char text threshold
    with pytest.raises(pde.ExtractionError, match="no pages"):
        pde._zipbundle_to_pdf(zbytes)


def test_synthesize_pdf_zipbundle_dispatches_correctly(tmp_path):
    zbytes = _make_zip({"doc.pdf": _make_pdf_bytes("Zip bundle via synthesize_pdf.")})
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(zbytes, dest)
    doc = fitz.open(dest)
    assert "Zip bundle via synthesize_pdf." in doc[0].get_text()
    doc.close()


# ---------------------------------------------------------------------------
# zip-of-forwarded-.msg (ADR 011 addendum, 2026-09-20): the real pathology
# behind nSITE doc_id 4008289922215821067 — a zip of 11 forwarded Outlook
# .msg emails synthesized a corrupt 1,087-page/16.7MB PDF that leaked raw
# OLE2 stream bytes as text. Root cause: a .msg zip ENTRY (as opposed to a
# .msg ATTACHMENT) was never recognized by _add_content_by_name, so it fell
# through to the raw-bytes-decoded-as-text fallback. These synthesize a
# multi-message zip in-process (no real .msg binary committed, per CLAUDE.md)
# by dispatching a fake extract_msg.openMsg per zip entry's own bytes.
# ---------------------------------------------------------------------------


def _patch_msg_multi(monkeypatch, by_data: dict):
    """Like _patch_msg, but for a zip bundle containing SEVERAL distinct
    .msg entries — extract_msg.openMsg is patched to look up the right
    _FakeMsg by the exact bytes it's called with, so each zip entry decodes
    to its own distinct envelope/attachments rather than one fixed fake."""
    import extract_msg

    def _fake_open_msg_multi(data, **kw):
        return _fake_open_msg(by_data[data])

    monkeypatch.setattr(extract_msg, "openMsg", _fake_open_msg_multi)


def test_zip_of_msg_entries_are_recursed_not_decoded_as_raw_bytes(monkeypatch):
    # The exact old failure mode: a .msg zip entry used to fall through to
    # the generic utf-8-decode-raw-bytes fallback, leaking OLE2 stream
    # structure (Root Entry / __substg1.0_.../ __nameid_version...) as text.
    msg1 = pde.OLE2_MAGIC + b"...email-one..."
    msg2 = pde.OLE2_MAGIC + b"...email-two..."
    _patch_msg_multi(monkeypatch, {
        msg1: _FakeMsg(subject="First email", body="Please review the attached letter."),
        msg2: _FakeMsg(subject="RE: First email", body="Acknowledged, thanks."),
    })
    zbytes = _make_zip({
        "correspondence/First email.msg": msg1,
        "correspondence/RE First email.msg": msg2,
    })
    doc, needs_ocr = pde._zipbundle_to_pdf(zbytes)
    full_text = "\n".join(p.get_text() for p in doc)
    assert "Please review the attached letter." in full_text
    assert "Acknowledged, thanks." in full_text
    assert "First email" in full_text  # real subject line, not garbage
    # The old failure mode: raw OLE2 bytes decoded as text.
    for marker in pde._OLE2_LEAK_MARKERS:
        assert marker not in full_text
    assert "PK\x03\x04" not in full_text
    doc.close()


def test_zip_of_msg_skips_inline_signature_images_but_keeps_real_attachments(monkeypatch):
    msg_with_signature_and_letter = pde.OLE2_MAGIC + b"...msg-with-attachments..."
    letter_pdf = _make_pdf_bytes("Wetland monitoring letter body text.")
    logo_png = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
    logo_png.clear_with(90)

    fake = _FakeMsg(attachments=[
        _FakeAttachment("image001.png", logo_png.tobytes("png")),
        _FakeAttachment("image002.jpg", logo_png.tobytes("jpg")),
        _FakeAttachment("Wetland Monitoring Letter.pdf", letter_pdf),
    ])
    _patch_msg_multi(monkeypatch, {msg_with_signature_and_letter: fake})
    zbytes = _make_zip({"chain/email.msg": msg_with_signature_and_letter})
    doc, needs_ocr = pde._zipbundle_to_pdf(zbytes)

    full_text = "\n".join(p.get_text() for p in doc)
    assert "Wetland monitoring letter body text." in full_text
    # 1 envelope page + 1 merged letter page — the 2 imageNNN signature
    # graphics contributed ZERO pages.
    assert len(doc) == 2
    assert needs_ocr is False
    doc.close()


def test_zip_of_msg_dedupes_repeated_attachment_across_forwarded_chain(monkeypatch):
    # The real specimen repeats the same substantive PDF letter 3x and the
    # same signature logos 4-6x across an 11-message forwarded chain — only
    # the FIRST occurrence of identical content should land in the output.
    letter_pdf = _make_pdf_bytes("Wetland monitoring letter, forwarded 3 times.")
    msg_a = pde.OLE2_MAGIC + b"...forward-one..."
    msg_b = pde.OLE2_MAGIC + b"...forward-two..."
    msg_c = pde.OLE2_MAGIC + b"...forward-three..."

    by_data = {
        m: _FakeMsg(subject=s, attachments=[_FakeAttachment("letter.pdf", letter_pdf)])
        for m, s in ((msg_a, "Original"), (msg_b, "FW: Original"), (msg_c, "FW: FW: Original"))
    }
    _patch_msg_multi(monkeypatch, by_data)
    zbytes = _make_zip({
        "chain/1 Original.msg": msg_a,
        "chain/2 FW Original.msg": msg_b,
        "chain/3 FW FW Original.msg": msg_c,
    })
    doc, needs_ocr = pde._zipbundle_to_pdf(zbytes)

    full_text = "\n".join(p.get_text() for p in doc)
    assert full_text.count("Wetland monitoring letter, forwarded 3 times.") == 1
    # 3 envelope pages (one per message) + 1 merged letter page (deduped).
    assert len(doc) == 4
    doc.close()


def test_synthesize_pdf_synthetic_zip_of_forwarded_msg_dedupes_attachments_and_images(monkeypatch, tmp_path):
    # End-to-end MECHANISM test — NOT a page-count prediction for a real
    # zip-of-forwarded-.msg. It replicates the real specimen's (nSITE doc_id
    # 4008289922215821067) ATTACHMENT-side pathology: several forwarded .msg
    # entries in one zip, each re-attaching the same substantive letter +
    # several imageNNN signature logos — and proves dedup + signature-skip
    # keep that side small. It does NOT replicate the real specimen's BODY-
    # TEXT side: a real forwarded chain's msg.body carries growing quoted
    # history (verified against the real 11 bodies: the two largest are
    # 82-83% line-overlap with content already emitted by an earlier
    # message), which content-hash dedup can't catch (restated text, not
    # byte-identical). This fixture's bodies are one short distinct line each
    # by design, so its own page count says nothing about real body-text
    # volume — the real doc synthesizes to 42 pages (13 attachments + 29
    # body text, much of the 29 redundant), not the ≤15 this test asserts.
    # See docs/decisions/011's 2026-09-20 addendum for the real numbers and
    # the open question of whether that redundancy is worth trimming.
    # Real .msg binaries aren't committed (CLAUDE.md) — this builds the
    # attachment-side SHAPE of the pathology in-process instead of shipping
    # the actual zip.
    letter_pdf = _make_pdf_bytes("The real substantive attachment.")
    logo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
    logo.clear_with(200)

    def _msg(n):
        return pde.OLE2_MAGIC + f"...email-{n}...".encode()

    by_data = {}
    entries = {}
    for n in range(1, 8):
        data = _msg(n)
        by_data[data] = _FakeMsg(
            subject=f"Correspondence #{n}",
            body=f"Message body #{n} in the thread.",
            attachments=[
                _FakeAttachment(f"image{n:03d}.png", logo.tobytes("png")),
                _FakeAttachment("Substantive Letter.pdf", letter_pdf),
            ],
        )
        entries[f"correspondence/email-{n}.msg"] = data
    _patch_msg_multi(monkeypatch, by_data)
    zbytes = _make_zip(entries)
    dest = str(tmp_path / "out.pdf")
    pde.synthesize_pdf(zbytes, dest)

    doc = fitz.open(dest)
    # 7 envelope pages + 1 merged letter page (deduped across all 7 forwards),
    # zero pages from the 7 imageNNN signature logos — nowhere near the old
    # 1,087-page pathology, comfortably under the sane ceiling.
    assert doc.page_count <= 15
    full_text = "\n".join(p.get_text() for p in doc).replace("\x00", "")
    for marker in pde._OLE2_LEAK_MARKERS:
        assert marker not in full_text
    doc.close()


# ---------------------------------------------------------------------------
# post-synthesis sanity guard (_reject_if_corrupt / MAX_SANE_PAGES /
# _OLE2_LEAK_MARKERS) — added 2026-09-20 so a future extraction defect of the
# same shape (ballooning, or leaking raw stream bytes as text) can never be
# saved and mirrored again, even if the dedup/filtering above has a gap.
# ---------------------------------------------------------------------------


def test_reject_if_corrupt_raises_on_too_many_pages():
    doc = fitz.open()
    for _ in range(pde.MAX_SANE_PAGES + 1):
        doc.new_page()
    with pytest.raises(pde.ExtractionError, match="pages"):
        pde._reject_if_corrupt(doc)
    doc.close()


def test_reject_if_corrupt_allows_exactly_the_page_ceiling():
    doc = fitz.open()
    for _ in range(pde.MAX_SANE_PAGES):
        doc.new_page()
    pde._reject_if_corrupt(doc)  # must not raise
    doc.close()


def test_reject_if_corrupt_raises_on_leaked_ole2_marker():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Root Entry")
    with pytest.raises(pde.ExtractionError, match="leaked"):
        pde._reject_if_corrupt(doc)
    doc.close()


def test_reject_if_corrupt_raises_on_the_actual_utf16_interleaved_form():
    # The real leak (nSITE doc_id 4008289922215821067) does NOT look like
    # plain ASCII "Root Entry" on the page — raw OLE2 property/stream names
    # are UTF-16LE internally, so decoding them via the generic
    # raw.decode("utf-8", errors="ignore") fallback (as happened before .msg
    # zip-entry recursion existed) produces every character NUL-interleaved:
    # "__substg1.0_".encode("utf-16-le") -> "_\x00_\x00s\x00u\x00b\x00...".
    # A naive `"__substg1.0_" in text` check does NOT match this form — only
    # the NUL-stripping in _reject_if_corrupt does. This test fails if that
    # one line is ever removed.
    interleaved = "__substg1.0_".encode("utf-16-le").decode("utf-8", errors="ignore")
    assert "__substg1.0_" not in interleaved  # sanity: the naive check really would miss it
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), interleaved, fontsize=9)
    with pytest.raises(pde.ExtractionError, match="leaked"):
        pde._reject_if_corrupt(doc)
    doc.close()


def test_synthesize_pdf_rejects_the_real_utf16_ole2_leak_shape_end_to_end():
    # End-to-end version of the above, through the public synthesize_pdf()
    # API: a zip entry of a type with no dedicated handler (so it hits the
    # generic decode-as-text fallback) whose raw bytes are the UTF-16LE
    # encoding of an OLE2 stream name — the actual shape the real specimen's
    # bug produced, not just an ASCII stand-in.
    raw = "__nameid_version".encode("utf-16-le")
    zbytes = _make_zip({"debug.dat": raw})
    with pytest.raises(pde.ExtractionError, match="leaked"):
        pde.synthesize_pdf(zbytes, "/dev/null")


def test_reject_if_corrupt_only_scans_the_first_five_pages():
    # A 40-page doc with the leak marker buried on page 39 is still corrupt in
    # principle, but the guard is a fast sanity sample, not an exhaustive
    # scan — documents the actual (sampled, not full) contract.
    doc = fitz.open()
    for _ in range(39):
        doc.new_page()
    page = doc.new_page()
    page.insert_text((50, 50), "Root Entry")
    pde._reject_if_corrupt(doc)  # must not raise — beyond the 5-page sample
    doc.close()


def test_synthesize_pdf_rejects_a_synthesis_that_balloons_past_the_page_ceiling(monkeypatch):
    huge_body = "word " * 200000  # far more than MAX_SANE_PAGES pages can hold
    _patch_msg(monkeypatch, _FakeMsg(body=huge_body))
    with pytest.raises(pde.ExtractionError, match="pages"):
        pde.synthesize_pdf(pde.OLE2_MAGIC + b"...", "/dev/null")


def test_synthesize_pdf_rejects_a_synthesis_that_leaks_an_ole2_marker():
    # A zip entry of a type this module has no dedicated handler for falls to
    # the generic best-effort-text fallback — exactly how raw OLE2 stream
    # bytes used to leak into a synthesized PDF before .msg recursion existed.
    zbytes = _make_zip({"debug.dat": b"Root Entry" + b" filler bytes " * 5})
    with pytest.raises(pde.ExtractionError, match="leaked"):
        pde.synthesize_pdf(zbytes, "/dev/null")


def test_synthesize_pdf_rejection_does_not_leave_a_saved_file(tmp_path):
    # A rejected synthesis must not save dest_path at all — a caller checking
    # os.path.exists(dest_path) after catching ExtractionError must see False.
    dest = str(tmp_path / "out.pdf")
    zbytes = _make_zip({"debug.dat": b"Root Entry" + b" filler bytes " * 5})
    with pytest.raises(pde.ExtractionError):
        pde.synthesize_pdf(zbytes, dest)
    import os
    assert not os.path.exists(dest)


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
