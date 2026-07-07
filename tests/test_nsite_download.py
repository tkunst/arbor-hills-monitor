"""download_pdf source-fallback (added 2026-07-07): when a record's own link
serves a non-PDF (Outlook .msg, image, nForm), fall back to nSITE's downloadpdf
render endpoint. Hermetic — a fake session returns scripted bodies, no network."""
import pytest

import nsite_client as nc

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
