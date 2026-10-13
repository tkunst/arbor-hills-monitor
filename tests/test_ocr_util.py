"""Tests for ocr_util.py (coder:mirror-ocr, 2026-10-13): the NEW page-by-page
needs_ocr() check and the fail-open ensure_searchable() helper used by
archive_client.upload_file(). classify()/ocr_in_place() themselves moved here
VERBATIM from egle_doc_parser.py and are unchanged — their existing coverage
(test_parser.py, test_poison_doc_extractor.py) still exercises them via
egle_doc_parser's re-exported names; a couple of equivalent checks are
repeated here directly against ocr_util for completeness.

No test in this file invokes the real ocrmypdf binary — same convention this
repo already follows everywhere else OCR is touched (test_poison_doc_extractor.py
monkeypatches ocr_in_place entirely rather than ever shelling out); tests.yml
doesn't install ocrmypdf, so a real-binary test would only ever run locally,
diverging silently from CI. subprocess.run() is mocked instead."""
import logging
import os
import subprocess

import fitz
import pytest

import ocr_util


# ---------------------------------------------------------------------------
# needs_ocr(): page-by-page, every page — not a 12-page sample
# ---------------------------------------------------------------------------


def test_needs_ocr_false_for_text_pdf(text_pdf):
    assert ocr_util.needs_ocr(text_pdf) is False


def test_needs_ocr_true_for_image_only_pdf(image_pdf):
    assert ocr_util.needs_ocr(image_pdf) is True


def test_needs_ocr_true_for_mixed_pdf_classify_misses_it(mixed_pdf):
    # page 0 real text, page 1 image-only. classify()'s whole-document
    # verdict reads this as "has_text" (page 0 clears the threshold) — this
    # is exactly the gap needs_ocr() exists to close.
    assert ocr_util.classify(mixed_pdf)[0] == "has_text"
    assert ocr_util.needs_ocr(mixed_pdf) is True


def test_needs_ocr_false_for_blank_separator_page(blank_page_pdf):
    # A genuinely blank page (no text, no image) must NOT be flagged — there
    # is nothing for ocrmypdf to add. Flagging it would re-shell to ocrmypdf
    # on every upload of any document with a blank page, breaking the
    # "already-searchable files pass straight through" requirement.
    assert ocr_util.needs_ocr(blank_page_pdf) is False


def test_needs_ocr_true_for_scan_past_page_12(large_mixed_pdf_scan_after_page_12):
    # The regression found 2026-10-13: classify()'s first-12-pages sample
    # misses a scanned page further into a large (real-world: 1,006-page)
    # document. needs_ocr() checks every page.
    path = large_mixed_pdf_scan_after_page_12
    assert ocr_util.classify(path)[0] == "has_text"
    assert ocr_util.needs_ocr(path) is True


def test_needs_ocr_raises_on_corrupt_pdf(tmp_path):
    # needs_ocr() is a plain, honest predicate — ensure_searchable() is the
    # fail-open layer, not this function.
    bad = str(tmp_path / "corrupt.pdf")
    with open(bad, "wb") as fh:
        fh.write(b"this is not a pdf")
    with pytest.raises(Exception):
        ocr_util.needs_ocr(bad)


# ---------------------------------------------------------------------------
# ensure_searchable(): fail-open, never raises
# ---------------------------------------------------------------------------


def test_ensure_searchable_passthrough_for_text_pdf(text_pdf, monkeypatch):
    calls = []
    monkeypatch.setattr(ocr_util.subprocess, "run", lambda *a, **k: calls.append(1))
    out = ocr_util.ensure_searchable(text_pdf)
    assert out == text_pdf          # unchanged, no copy
    assert calls == []              # the fast path: no subprocess call at all


def _fake_ocrmypdf_writes_text(text: str):
    """Stands in for a successful ocrmypdf run: writes a real text-bearing
    PDF to the command's output path (the last argv element) and returns
    rc=0 — simulates what ocrmypdf would produce without the real binary."""
    def _run(cmd, **kwargs):
        out_path = cmd[-1]
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(out_path)
        doc.close()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return _run


def test_ensure_searchable_ocrs_scan_only_fixture(image_pdf, monkeypatch):
    monkeypatch.setattr(ocr_util.subprocess, "run",
                        _fake_ocrmypdf_writes_text("OCR'd content here. " * 10))
    out = ocr_util.ensure_searchable(image_pdf)
    try:
        assert out != image_pdf     # a new temp path, original untouched
        doc = fitz.open(out)
        chars = sum(len(doc[i].get_text()) for i in range(len(doc)))
        doc.close()
        assert chars > 50
    finally:
        if os.path.exists(out):
            os.remove(out)
    # The original file is NEVER modified.
    doc = fitz.open(image_pdf)
    assert doc[0].get_text().strip() == ""
    doc.close()


def test_ensure_searchable_mixed_fixture_calls_skip_text(mixed_pdf, monkeypatch):
    seen_cmds = []

    def _run(cmd, **kwargs):
        seen_cmds.append(cmd)
        out_path = cmd[-1]
        # simulate --skip-text: copy the original (page 0's real text
        # untouched, page 1 would gain a layer in a real run).
        doc = fitz.open(mixed_pdf)
        doc.save(out_path)
        doc.close()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(ocr_util.subprocess, "run", _run)

    out = ocr_util.ensure_searchable(mixed_pdf)
    try:
        assert out != mixed_pdf
        assert "--skip-text" in seen_cmds[0]
        doc = fitz.open(out)
        assert "Arbor Hills" in doc[0].get_text()   # text page untouched
        doc.close()
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_ensure_searchable_fails_open_on_corrupt_pdf(tmp_path, caplog):
    bad = str(tmp_path / "broken-document-name.pdf")
    with open(bad, "wb") as fh:
        fh.write(b"not a pdf at all")
    with caplog.at_level(logging.WARNING):
        out = ocr_util.ensure_searchable(bad)
    assert out == bad                                   # returned original, no raise
    assert "broken-document-name" not in caplog.text     # SEC-001: no filename
    assert "read_error" in caplog.text


def test_ensure_searchable_fails_open_on_missing_binary(image_pdf, monkeypatch, caplog):
    monkeypatch.setenv("OCRMYPDF_BIN", "/nonexistent/ocrmypdf-binary-that-does-not-exist")
    with caplog.at_level(logging.WARNING):
        out = ocr_util.ensure_searchable(image_pdf)
    assert out == image_pdf
    assert "missing_binary" in caplog.text


def test_ensure_searchable_fails_open_on_nonzero_exit(image_pdf, monkeypatch, caplog):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="",
            stderr="some ocrmypdf error naming a document titled Secret Enforcement Letter\nlast line")
    monkeypatch.setattr(ocr_util.subprocess, "run", _run)
    with caplog.at_level(logging.WARNING):
        out = ocr_util.ensure_searchable(image_pdf)
    assert out == image_pdf
    assert "Secret Enforcement Letter" not in caplog.text   # SEC-001: no stderr/title text
    assert "nonzero_exit" in caplog.text


def test_ensure_searchable_fails_open_on_timeout(image_pdf, monkeypatch, caplog):
    def _run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 300))
    monkeypatch.setattr(ocr_util.subprocess, "run", _run)
    with caplog.at_level(logging.WARNING):
        out = ocr_util.ensure_searchable(image_pdf)
    assert out == image_pdf
    assert "timeout" in caplog.text


def test_ensure_searchable_cleans_up_its_temp_file_on_failure(image_pdf, monkeypatch):
    # On any failure path, the temp output file ensure_searchable() itself
    # created must not survive.
    created_paths = []
    real_mkstemp = ocr_util.tempfile.mkstemp

    def spy_mkstemp(*a, **kw):
        fd, path = real_mkstemp(*a, **kw)
        created_paths.append(path)
        return fd, path
    monkeypatch.setattr(ocr_util.tempfile, "mkstemp", spy_mkstemp)

    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
    monkeypatch.setattr(ocr_util.subprocess, "run", _run)

    ocr_util.ensure_searchable(image_pdf)
    assert len(created_paths) == 1
    assert not os.path.exists(created_paths[0])
