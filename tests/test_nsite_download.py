"""download_pdf source-fallback (added 2026-07-07): when a record's own link
serves a non-PDF (Outlook .msg, image, nForm), fall back to nSITE's downloadpdf
render endpoint. Hermetic — a fake session returns scripted bodies, no network.

ADR 011 (added 2026-07-11) extends the fallback chain one more step: if every
source is still non-PDF, poison_doc_extractor.synthesize_pdf() gets one last
shot at building a real PDF from .msg/.docx content. These tests treat
poison_doc_extractor as a black box (it has its own dedicated test file,
tests/test_poison_doc_extractor.py) — mocked here except for one real,
unmocked .docx round-trip proving the full integration actually works."""
import gzip
import io
import zipfile

import pytest

import nsite_client as nc
import poison_doc_extractor as pde

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
MSG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE / Outlook .msg magic — not a PDF


class _Resp:
    def __init__(self, content=b"", status=200):
        self.content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")


class _Session:
    """Returns a scripted response per URL substring. A list value scripts a
    retry sequence (one item consumed per call)."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for key, val in self.script.items():
            if key in url:
                return val.pop(0) if isinstance(val, list) else val
        return _Resp(b"", 404)


def _doc(doc_url):
    return {"doc_id": "12345", "doc_url": doc_url}


def test_primary_pdf_is_saved_without_fallback(tmp_path):
    dest = str(tmp_path / "out.pdf")
    sess = _Session({"downloadpdf/12345": _Resp(PDF)})
    nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_BASE}/12345"), dest)
    assert open(dest, "rb").read() == PDF
    assert sess.calls == [f"{nc.DOWNLOAD_BASE}/12345"]  # never touched the fallback


def test_non_pdf_primary_falls_back_to_render(tmp_path):
    dest = str(tmp_path / "out.pdf")
    # doc_url is the downloadfile source (an Outlook .msg); the render returns a PDF.
    sess = _Session({"downloadfile/12345": _Resp(MSG), "downloadpdf/12345": _Resp(PDF)})
    nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_FILE_BASE}/12345"), dest)
    assert open(dest, "rb").read() == PDF
    # Primary tried once (non-PDF -> no retry), then the render endpoint.
    assert sess.calls == [f"{nc.DOWNLOAD_FILE_BASE}/12345", f"{nc.DOWNLOAD_BASE}/12345"]


def test_both_non_pdf_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    dest = str(tmp_path / "out.pdf")
    # The legacy-.doc case: the source is non-PDF AND the render endpoint 400s.
    sess = _Session(
        {"downloadfile/12345": _Resp(b"MZ\x90\x00"), "downloadpdf/12345": _Resp(b'{"errorCode":400}', 400)}
    )
    with pytest.raises(RuntimeError, match="download failed"):
        nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_FILE_BASE}/12345"), dest)


def test_transient_then_pdf_retries_same_url(tmp_path, monkeypatch):
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    dest = str(tmp_path / "out.pdf")
    # First fetch 500s (transient), the retry returns the PDF — same URL.
    sess = _Session({"downloadpdf/12345": [_Resp(b"", 500), _Resp(PDF)]})
    nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_BASE}/12345"), dest)
    assert open(dest, "rb").read() == PDF


def test_empty_body_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    dest = str(tmp_path / "out.pdf")
    sess = _Session({"downloadpdf/12345": _Resp(b"", 200)})
    with pytest.raises(RuntimeError):
        nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_BASE}/12345"), dest)


def test_pdf_header_within_first_kb_is_accepted(tmp_path):
    dest = str(tmp_path / "out.pdf")
    body = b"\xef\xbb\xbf   \n" + PDF  # BOM + whitespace before %PDF (tolerated)
    sess = _Session({"downloadpdf/12345": _Resp(body)})
    nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_BASE}/12345"), dest)
    assert open(dest, "rb").read() == body


# ---------------------------------------------------------------------------
# ADR 011: extraction fallback when every source is non-PDF
# ---------------------------------------------------------------------------


def _make_docx(text: str) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{ns}"><w:body><w:p><w:r><w:t>{text}</w:t>'
        f'</w:r></w:p></w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_msg_source_falls_through_to_extractor(tmp_path, monkeypatch):
    # Both the primary link (already the native/downloadfile URL, matching
    # real nSITE .msg records) and the render endpoint fail as before —
    # ADR 011's new last resort is the extractor, mocked here as a black box.
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    dest = str(tmp_path / "out.pdf")
    sess = _Session({"downloadfile/12345": _Resp(MSG), "downloadpdf/12345": _Resp(b"", 400)})

    calls = []

    def _fake_synthesize(content, dest_path):
        calls.append(content)
        with open(dest_path, "wb") as f:
            f.write(b"fake synthesized pdf bytes")
        return dest_path

    monkeypatch.setattr(pde, "synthesize_pdf", _fake_synthesize)
    result = nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_FILE_BASE}/12345"), dest)
    assert result == dest
    assert calls == [MSG]  # extractor received exactly the non-PDF body fetched


def test_extraction_fallback_receives_gunzipped_content(tmp_path, monkeypatch):
    # nSITE's native-file endpoint sometimes serves gzip bytes without
    # declaring Content-Encoding — download_pdf must decode before both the
    # PDF-sniff and the extraction handoff (2026-07-07 hand-pull finding).
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    docx_bytes = _make_docx("Un-permitted discharge finding.")
    gzipped = gzip.compress(docx_bytes)
    dest = str(tmp_path / "out.pdf")
    sess = _Session({"downloadfile/12345": _Resp(gzipped), "downloadpdf/12345": _Resp(b"", 400)})

    calls = []

    def _fake_synthesize(content, dest_path):
        calls.append(content)
        with open(dest_path, "wb") as f:
            f.write(b"fake synthesized pdf bytes")
        return dest_path

    monkeypatch.setattr(pde, "synthesize_pdf", _fake_synthesize)
    nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_FILE_BASE}/12345"), dest)
    assert calls == [docx_bytes]  # gunzipped before reaching the extractor


def test_extraction_fallback_failure_still_raises_download_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    dest = str(tmp_path / "out.pdf")
    sess = _Session({"downloadfile/12345": _Resp(MSG), "downloadpdf/12345": _Resp(b"", 400)})

    def _fake_synthesize(content, dest_path):
        raise pde.ExtractionError("simulated: not actually extractable")

    monkeypatch.setattr(pde, "synthesize_pdf", _fake_synthesize)
    with pytest.raises(RuntimeError, match="download failed"):
        nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_FILE_BASE}/12345"), dest)


def test_real_docx_source_extracts_end_to_end_unmocked(tmp_path, monkeypatch):
    # No mocking of poison_doc_extractor here — a genuine .docx round-trips
    # through the full download_pdf() -> synthesize_pdf() path.
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    docx_bytes = _make_docx("EGLE Compliance Communication CC-001168.")
    dest = str(tmp_path / "out.pdf")
    sess = _Session({"downloadfile/12345": _Resp(docx_bytes), "downloadpdf/12345": _Resp(b"", 400)})
    result = nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_FILE_BASE}/12345"), dest)
    assert result == dest
    with open(dest, "rb") as f:
        assert f.read().startswith(b"%PDF")


def test_genuinely_unsupported_content_still_poisons(tmp_path, monkeypatch):
    # test_both_non_pdf_raises (above) already covers this for real, unmocked
    # — this variant just makes the ADR 011 angle explicit: MZ magic isn't
    # OLE2 or ZIP, so sniff_format legitimately returns None and the doc
    # still poisons exactly as it did before this module existed.
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    dest = str(tmp_path / "out.pdf")
    sess = _Session(
        {"downloadfile/12345": _Resp(b"MZ\x90\x00legacy .doc-shaped bytes"),
         "downloadpdf/12345": _Resp(b'{"errorCode":400}', 400)}
    )
    with pytest.raises(RuntimeError, match="download failed"):
        nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_FILE_BASE}/12345"), dest)


def test_empty_doc_url_still_reaches_native_extraction_fallback(tmp_path, monkeypatch):
    # Bug found 2026-07-11: _normalize() falls back to the RENDER endpoint
    # (not the native one) when a record's own docMgmtDocurl is empty, so
    # primary == render and the old two-URL list never even fetched a
    # non-PDF body to hand to the extractor. native_download_url(doc_id) is
    # now tried explicitly as a third source.
    monkeypatch.setattr(nc.time, "sleep", lambda *_: None)
    docx_bytes = _make_docx("Un-permitted discharge finding.")
    dest = str(tmp_path / "out.pdf")
    # doc_url == the render endpoint itself (the empty-docMgmtDocurl case);
    # downloadpdf 400s (as it does for .msg/.docx); the native downloadfile
    # endpoint — never explicitly requested before this fix — has the goods.
    sess = _Session({
        "downloadpdf/12345": _Resp(b"", 400),
        "downloadfile/12345": _Resp(docx_bytes),
    })
    result = nc.download_pdf(sess, _doc(f"{nc.DOWNLOAD_BASE}/12345"), dest)
    assert result == dest
    # downloadpdf 400s (retried 3x before moving on), then the native
    # downloadfile endpoint — previously never reached — gets called.
    assert f"{nc.DOWNLOAD_FILE_BASE}/12345" in sess.calls
    with open(dest, "rb") as f:
        assert f.read().startswith(b"%PDF")
