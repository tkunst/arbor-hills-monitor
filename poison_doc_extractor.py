"""
poison_doc_extractor.py — text extraction + PDF synthesis for nSITE documents
whose sources aren't PDFs (.msg, .docx) — the gap nsite_client.download_pdf()
alone can't close (ADR 011).

Wired in as nsite_client.download_pdf()'s last-resort fallback: when neither
the record's own link nor the downloadpdf render endpoint yields a PDF, the
native bytes (ncore/downloadfile, gzip-decoded) are sniffed for format and, if
supported here, synthesized into a PDF containing every extractable piece of
content — so egle_doc_parser.parse_document()'s existing classify/OCR/extract
pipeline ingests it completely unchanged downstream.

Supported: .msg (Outlook email, via extract-msg) and .docx (Word, via stdlib
zip/XML — no extra dependency). Legacy binary .doc shares .msg's OLE2 magic
bytes but isn't a real .msg — extract-msg raises on it (verified against a
real OLE2-but-not-msg specimen during design), which this module treats as
"unsupported" (still a poison strike), the same outcome as before this module
existed.

.msg attachments are recursed into: a PDF attachment has its actual pages
merged in (fitz.insert_pdf) rather than re-extracted as text — and if those
merged pages are themselves a scan (no text layer), that's detected too, so
the doc still gets OCR'd proactively; a .docx attachment is routed through
the same _docx_body_text() extraction as a top-level .docx; .xls/.xlsx
attachments become a text table (xlrd / openpyxl); image attachments are
placed as a raster page and OCR'd proactively by synthesize_pdf() itself
(see its docstring for why — a mixed text+image doc can't safely rely on
parse_document()'s own whole-document classify() gate). Anything else (e.g.
the tiny .txt sidecar files Outlook sometimes attaches for inline-image
content-IDs) is decoded best-effort as text and included only if it carries
real content.
"""
from __future__ import annotations

import io
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional

import fitz  # pymupdf

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Raster formats fitz.Page.insert_image can place directly as a page image.
# synthesize_pdf() OCRs these proactively — see its docstring.
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif")

# Manual line-wrap geometry for synthesized text pages (see _add_text_page).
_LINE_WIDTH_CHARS = 90
_LINES_PER_PAGE = 65
_LINE_HEIGHT = 11
_TOP_MARGIN = 60
_LEFT_MARGIN = 50


class ExtractionError(RuntimeError):
    """Content isn't a format this module can synthesize a PDF from, or the
    real extraction failed. Callers should treat this exactly like any other
    download failure: a poison strike, not a crash."""


def sniff_format(data: bytes) -> Optional[str]:
    """Return 'msg', 'docx', or None (unsupported) based on magic bytes.
    ZIP-magic content is further checked for word/document.xml before being
    called 'docx' — other OOXML formats (xlsx, pptx) share the ZIP magic but
    aren't Word documents this module knows how to read."""
    if data.startswith(OLE2_MAGIC):
        return "msg"
    if data.startswith(ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
    return None


def synthesize_pdf(data: bytes, dest_path: str) -> str:
    """Extract everything this module knows how to extract from `data` and
    write a PDF to dest_path. Raises ExtractionError if the format isn't
    supported or extraction fails for any reason. Returns dest_path.

    If any raster image page was inserted (an image attachment on a .msg),
    OCR runs HERE, proactively — not left to egle_doc_parser.parse_document()'s
    own classify()-gated OCR step. Reason (found 2026-07-11 against a real
    WRD-Groundwater specimen: an email with a short text body plus several
    photo attachments): classify()'s needs_ocr/likely verdict is a WHOLE-
    DOCUMENT judgment. A synthesized PDF mixing a real text page with several
    image-only pages usually still reads as 'has_text' overall (the text page
    is enough to clear the threshold), so parse_document() would never OCR
    the image pages and their content would be silently lost. Running OCR
    here, before the file is handed off, means every page already has a real
    text layer by the time classify() sees it — which then correctly (and
    truthfully) reports has_text, so parse_document() doesn't redundantly
    OCR again."""
    fmt = sniff_format(data)
    try:
        if fmt == "msg":
            doc, needs_ocr = _msg_to_pdf(data)
        elif fmt == "docx":
            doc, needs_ocr = _docx_to_pdf(data)
        else:
            raise ExtractionError(f"unsupported format (first bytes {data[:8]!r})")
    except ExtractionError:
        raise
    except Exception as e:  # noqa: BLE001 — any extraction failure -> poison, not a crash
        raise ExtractionError(f"{fmt or 'unknown'} extraction failed: {e}") from e

    try:
        doc.save(dest_path)
    finally:
        doc.close()

    if needs_ocr:
        from egle_doc_parser import ocr_in_place

        try:
            ocr_in_place(dest_path)
        except Exception as e:  # noqa: BLE001 — OCR failing shouldn't poison a doc whose
            # text pages are still perfectly readable; the image pages just stay
            # un-OCR'd this run (same degrade-gracefully spirit as everywhere else).
            print(f"[poison-doc-extractor] OCR pass failed for {dest_path}: {e}")

    return dest_path


# ---------------------------------------------------------------------------
# .docx — stdlib zip/XML, no extra dependency
# ---------------------------------------------------------------------------


def _docx_body_text(data: bytes) -> str:
    """Extract paragraph text, preserving <w:tab/> and <w:br/> as literal tab/
    newline characters, and without duplicating text nested in a text box or
    drawing anchored inside a run.

    Two bugs found 2026-07-11 in code review, both confirmed against the real
    hand-pull specimens:
    1. Collecting only <w:t> text and joining with "" glued adjacent runs
       together with no separator whenever Word split a value across multiple
       runs or tab-separated columns — a tabular reading like
       "Well AHW272R4<tab>180F" (two separate <w:t> runs either side of a
       <w:tab/>) came out as the single glued token "AHW272R4180F". Up to 34
       <w:tab/> elements in a single real specimen were affected.
    2. Walking each paragraph's FULL descendant tree (rather than only its
       direct-child runs) also walks into any <w:txbxContent> nested inside a
       <w:drawing> inside one of its runs — a letterhead/signature text box,
       e.g. — double-extracting that text: once as part of the outer
       paragraph's own text (since a paragraph's .iter() doesn't stop at a
       nested <w:p> boundary), and again when root.iter() reaches the nested
       <w:p> as its own independent paragraph. Confirmed on a real specimen:
       "LIESL EICHLER CLARK" appeared twice in raw <w:t> elements but 4 times
       in the (buggy) extracted text.

    Fixed by only descending into each paragraph's DIRECT-child <w:r> runs
    (p.findall, not p.iter) and only their direct-child content elements (not
    a deep walk) — <w:drawing> is a sibling of <w:t>/<w:tab/>/<w:br/> within a
    run, never itself walked into, so a nested paragraph inside it is only
    ever captured once, on its own, when root.iter() reaches it independently."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml_bytes = z.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    run_tag = f"{_WORD_NS}r"
    text_tag = f"{_WORD_NS}t"
    tab_tag = f"{_WORD_NS}tab"
    br_tag = f"{_WORD_NS}br"
    paras = []
    for p in root.iter(f"{_WORD_NS}p"):
        parts = []
        for r in p.findall(run_tag):
            for el in r:
                if el.tag == text_tag:
                    parts.append(el.text or "")
                elif el.tag == tab_tag:
                    parts.append("\t")
                elif el.tag == br_tag:
                    parts.append("\n")
        paras.append("".join(parts))
    return "\n".join(paras)


def _docx_to_pdf(data: bytes) -> tuple[fitz.Document, bool]:
    text = _docx_body_text(data)
    if not text.strip():
        raise ExtractionError("docx body text is empty")
    doc = fitz.open()
    _add_text_page(doc, text)
    return doc, False  # .docx never inserts raw images — no OCR needed


# ---------------------------------------------------------------------------
# .msg — extract-msg, with attachment recursion
# ---------------------------------------------------------------------------


def _msg_to_pdf(data: bytes) -> tuple[fitz.Document, bool]:
    import extract_msg

    doc = fitz.open()
    needs_ocr = False
    with extract_msg.openMsg(data) as msg:
        envelope = (
            f"From: {msg.sender or ''}\n"
            f"To: {msg.to or ''}\n"
            f"Date: {msg.date or ''}\n"
            f"Subject: {msg.subject or ''}\n\n"
            f"{msg.body or ''}"
        )
        _add_text_page(doc, envelope)
        for att in msg.attachments:
            name = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None) or "?"
            try:
                if _add_attachment(doc, att):
                    needs_ocr = True
            except Exception as e:  # noqa: BLE001 — one bad attachment must not sink the whole doc
                print(f"[poison-doc-extractor] attachment {name!r} skipped: {e}")
                continue
    if len(doc) == 0:
        raise ExtractionError("msg produced no pages")
    return doc, needs_ocr


def _pdf_has_image_only_pages(attach: fitz.Document) -> bool:
    """True if any page has (near-)no text but does carry an image — the same
    imageonly_page heuristic egle_doc_parser.classify() uses, checked here so
    a merged PDF attachment that's itself a scan gets the same proactive-OCR
    treatment as a raw image attachment (found 2026-07-11: the PDF-merge
    branch previously always reported needs_ocr=False, so a scanned PDF
    attachment inside a doc that otherwise reads has_text overall could have
    its content silently skipped, same failure class as synthesize_pdf()'s
    main docstring describes for image attachments)."""
    for page in attach:
        if len(page.get_text().strip()) < 20 and len(page.get_images(full=False)) >= 1:
            return True
    return False


def _add_attachment(doc: fitz.Document, att) -> bool:
    """Add one .msg attachment to `doc`. Returns True if the attachment needs
    the proactive OCR pass synthesize_pdf() runs (a raster image page, or a
    merged PDF attachment that's itself image-only)."""
    name = (getattr(att, "longFilename", None) or getattr(att, "shortFilename", None) or "").lower()
    raw = getattr(att, "data", None)
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return False  # embedded-message / non-data attachments (MSG-in-MSG) — out of scope
    raw = bytes(raw)

    if name.endswith(".pdf") or raw[:4] == b"%PDF":
        with fitz.open(stream=raw, filetype="pdf") as attach:
            needs_ocr = _pdf_has_image_only_pages(attach)
            doc.insert_pdf(attach)
        return needs_ocr

    if name.endswith(_IMAGE_EXTS):
        page = doc.new_page()
        try:
            page.insert_image(page.rect, stream=raw)
        except Exception:
            doc.delete_page(page.number)  # don't leave an orphaned blank page
            raise
        return True

    if name.endswith(".docx") or sniff_format(raw) == "docx":
        text = _docx_body_text(raw)
        if text.strip():
            _add_text_page(doc, f"--- attachment: {name} ---\n{text}")
        return False

    if name.endswith((".xls", ".xlsx")):
        table = _spreadsheet_to_text(raw, name)
        if table.strip():
            _add_text_page(doc, f"--- attachment: {name} ---\n{table}")
        return False

    # Best-effort text for anything else (e.g. Outlook's tiny inline-image
    # content-ID .txt sidecars) — skip if it's not real content. Content-
    # sniffed as ZIP/OLE2 but not recognized as .docx/.msg above would decode
    # to binary noise here; the >=20-char threshold doesn't reliably filter
    # that out, but there's no further format this module understands to
    # route it to instead — same "unsupported, degrade rather than crash"
    # contract as the top-level sniff_format().
    text = raw.decode("utf-8", errors="ignore").strip()
    if len(text) >= 20:
        _add_text_page(doc, f"--- attachment: {name} ---\n{text}")
    return False


def _spreadsheet_to_text(raw: bytes, name: str) -> str:
    lines = []
    if name.endswith(".xlsx"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
        for ws in wb.worksheets:
            lines.append(f"--- sheet: {ws.title} ---")
            for row in ws.iter_rows(values_only=True):
                lines.append(" | ".join("" if c is None else str(c) for c in row))
    else:
        import xlrd

        book = xlrd.open_workbook(file_contents=raw)
        for sheet in book.sheets():
            lines.append(f"--- sheet: {sheet.name} ---")
            for r in range(sheet.nrows):
                row = [str(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
                lines.append(" | ".join(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------


def _add_text_page(doc: fitz.Document, text: str) -> None:
    """Add text as one or more pages, using manual line-wrap + per-line
    insert_text rather than insert_textbox's box-fitting layout engine.
    Deliberate: insert_textbox was found (2026-07-11, against a real WRD-
    Groundwater .docx specimen) to silently render NOTHING for certain
    real-world text shapes once its internal capacity is exceeded, with no
    reliable signal in its return value — manual line placement gives full,
    predictable control over pagination instead."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, width=_LINE_WIDTH_CHARS) or [""])

    for i in range(0, len(lines), _LINES_PER_PAGE):
        page = doc.new_page()
        y = _TOP_MARGIN
        for line in lines[i:i + _LINES_PER_PAGE]:
            if line:
                page.insert_text((_LEFT_MARGIN, y), line, fontsize=9)
            y += _LINE_HEIGHT
