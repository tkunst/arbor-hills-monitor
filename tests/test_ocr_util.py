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
diverging silently from CI. subprocess.Popen() is mocked instead for most
tests (coder:ocr-hang-fix, 2026-10-14: ocr_util moved from subprocess.run()
to subprocess.Popen()+communicate() so a timeout can kill ocrmypdf's WHOLE
process group, not just its own pid -- see ocr_util._run_ocrmypdf()'s own
docstring). The two "_real_subprocess" tests near the bottom are the
deliberate exception: they invoke a tiny FAKE python script (never the real
ocrmypdf binary) to prove the process-group kill against an actual fork()'d
grandchild, which a Popen mock structurally cannot exercise."""
import logging
import os
import subprocess
import sys
import time

import fitz
import pytest

import ocr_util


# ---------------------------------------------------------------------------
# FakePopen -- stands in for subprocess.Popen so tests can control what
# ocr_util._run_ocrmypdf()'s Popen(...).communicate() calls "return" without
# ever invoking a real binary. `steps` is consumed one entry per
# communicate() call (the last entry repeats if called more times than
# there are steps -- mirrors the real post-kill drain call, which always
# succeeds once the whole group is dead). Each entry is either:
#   - a callable `f(cmd) -> (stdout, stderr, returncode)` -- do the
#     "ocrmypdf ran" side effect (e.g. write the output file) and report
#     the result, or
#   - an exception INSTANCE to raise instead (e.g. a real TimeoutExpired).
# ---------------------------------------------------------------------------


def fake_popen_factory(steps, instances=None):
    calls = {"n": 0}

    class _FakePopen:
        def __init__(self, cmd, *, env=None, stdout=None, stderr=None,
                     text=None, start_new_session=None):
            self.cmd = cmd
            self.env = env
            self.stdout_dest = stdout
            self.stderr_dest = stderr
            self.text = text
            self.start_new_session = start_new_session
            self.pid = 4242424
            self.returncode = None
            if instances is not None:
                instances.append(self)

        def communicate(self, timeout=None):
            i = min(calls["n"], len(steps) - 1)
            calls["n"] += 1
            step = steps[i]
            if isinstance(step, BaseException):
                raise step
            stdout, stderr, rc = step(self.cmd)
            self.returncode = rc
            return stdout, stderr

        def wait(self, timeout=None):
            return self.returncode

    return _FakePopen


def _writes_text(text: str):
    """A `steps` entry: stands in for a successful ocrmypdf run — writes a
    real text-bearing PDF to the command's output path (the last argv
    element) and reports rc=0."""
    def step(cmd):
        out_path = cmd[-1]
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(out_path)
        doc.close()
        return "", "", 0
    return step


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


def test_needs_ocr_false_for_captioned_image_page(captioned_image_pdf):
    # ocrmypdf --skip-text skips a page the moment it has ANY text at all
    # (confirmed empirically against the real binary, 2026-10-13 spike — see
    # the fixture's own docstring). A page captioned "Photo 1" plus an image
    # must NOT be flagged: ocrmypdf would just skip it, so flagging it means
    # the file never converges to "no longer needs OCR" after a real run.
    assert ocr_util.needs_ocr(captioned_image_pdf) is False


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
# image_only_pages(): the richer set form — tells PARTIAL improvement from
# none, which needs_ocr()'s plain bool can't (found by independent review,
# 2026-10-13 — see scripts/backfill_mirror_ocr.py's process_candidate()).
# ---------------------------------------------------------------------------


def test_image_only_pages_is_the_set_needs_ocr_is_bool_of(mixed_pdf):
    pages = ocr_util.image_only_pages(mixed_pdf)
    assert pages == {1}   # page 0 is real text, page 1 is image-only
    assert ocr_util.needs_ocr(mixed_pdf) == bool(pages)


def test_image_only_pages_empty_for_text_pdf(text_pdf):
    assert ocr_util.image_only_pages(text_pdf) == set()


def test_image_only_pages_multiple_pages_flagged(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "real text page 0")
    for i in (1, 2):
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
        pix.clear_with(100 + i)
        page.insert_image(fitz.Rect(5, 5, 45, 45), pixmap=pix)
    doc.new_page().insert_text((72, 72), "real text page 3")
    path = str(tmp_path / "multi.pdf")
    doc.save(path)
    doc.close()
    assert ocr_util.image_only_pages(path) == {1, 2}


# ---------------------------------------------------------------------------
# ensure_searchable(): fail-open, never raises
# ---------------------------------------------------------------------------


def test_ensure_searchable_passthrough_for_text_pdf(text_pdf, monkeypatch):
    calls = []
    monkeypatch.setattr(ocr_util.subprocess, "Popen", lambda *a, **k: calls.append(1))
    out = ocr_util.ensure_searchable(text_pdf)
    assert out == text_pdf          # unchanged, no copy
    assert calls == []              # the fast path: no subprocess call at all


def test_ensure_searchable_ocrs_scan_only_fixture(image_pdf, monkeypatch):
    monkeypatch.setattr(ocr_util.subprocess, "Popen",
                        fake_popen_factory([_writes_text("OCR'd content here. " * 10)]))
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

    def step(cmd):
        seen_cmds.append(cmd)
        out_path = cmd[-1]
        # simulate --skip-text: copy the original (page 0's real text
        # untouched, page 1 would gain a layer in a real run).
        doc = fitz.open(mixed_pdf)
        doc.save(out_path)
        doc.close()
        return "", "", 0
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([step]))

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


def test_ensure_searchable_passes_a_minimal_env_not_the_full_environment(image_pdf, monkeypatch):
    # Security finding (round-2 review, Low): the full os.environ would hand
    # every workflow secret (GOAUTH_*, GDRIVE_SA_KEY) to ocrmypdf and its
    # child processes, which parse untrusted PDFs. Only PATH (plus a small
    # allowlist) should reach the subprocess call.
    monkeypatch.setenv("GOAUTH_CLIENT_SECRET", "super-secret-value")
    instances = []
    monkeypatch.setattr(ocr_util.subprocess, "Popen",
                        fake_popen_factory([_writes_text("text")], instances=instances))

    out = ocr_util.ensure_searchable(image_pdf)
    try:
        assert len(instances) == 1
        assert "GOAUTH_CLIENT_SECRET" not in instances[0].env
        assert "PATH" in instances[0].env
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_ensure_searchable_skips_rerun_when_already_ocrmypdf_stamped(tmp_path, monkeypatch):
    # Found by independent review + the advisor (2026-10-13): a document
    # already run through ocrmypdf upstream (e.g. parse_document()'s
    # classify()-triggered pass) that STILL has an image-only page (a photo
    # with nothing recoverable in it) must not be re-OCR'd — the remaining
    # page is unrecoverable no matter how many times OCR runs, and a second
    # attempt just spends up to `timeout` seconds for zero gain.
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100))
    pix.clear_with(150)
    page.insert_image(fitz.Rect(10, 10, 90, 90), pixmap=pix)
    doc.set_metadata({"creator": "OCRmyPDF 17.13.0 / OCRmyPDF fpdf2 + Tesseract OCR 5.5.3"})
    path = str(tmp_path / "already_ocrd.pdf")
    doc.save(path)
    doc.close()
    assert ocr_util.needs_ocr(path) is True   # still genuinely flagged

    calls = []
    monkeypatch.setattr(ocr_util.subprocess, "Popen", lambda *a, **k: calls.append(1))
    out = ocr_util.ensure_searchable(path)
    assert out == path
    assert calls == []   # never even tried -- the subprocess call was skipped


def test_ensure_searchable_rejects_empty_output(image_pdf, monkeypatch):
    def step(cmd):
        open(cmd[-1], "wb").close()   # ocrmypdf claims success but writes nothing
        return "", "", 0
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([step]))
    assert ocr_util.ensure_searchable(image_pdf) == image_pdf


def test_ensure_searchable_rejects_unreadable_output(image_pdf, monkeypatch):
    def step(cmd):
        with open(cmd[-1], "wb") as fh:
            fh.write(b"not a real pdf, but not empty either")
        return "", "", 0
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([step]))
    assert ocr_util.ensure_searchable(image_pdf) == image_pdf


def test_ensure_searchable_rejects_page_count_mismatch(mixed_pdf, monkeypatch):
    # mixed_pdf has 2 pages; a fake "successful" ocrmypdf run that drops a
    # page must be rejected, not trusted over the original.
    def step(cmd):
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "only one page now")
        doc.save(cmd[-1])
        doc.close()
        return "", "", 0
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([step]))
    assert ocr_util.ensure_searchable(mixed_pdf) == mixed_pdf


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
    def step(cmd):
        return ("", "some ocrmypdf error naming a document titled Secret "
                "Enforcement Letter\nlast line", 1)
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([step]))
    with caplog.at_level(logging.WARNING):
        out = ocr_util.ensure_searchable(image_pdf)
    assert out == image_pdf
    assert "Secret Enforcement Letter" not in caplog.text   # SEC-001: no stderr/title text
    assert "nonzero_exit" in caplog.text


def test_ensure_searchable_fails_open_on_timeout(image_pdf, monkeypatch, caplog):
    first = subprocess.TimeoutExpired(cmd=["ocrmypdf"], timeout=300)
    drain = lambda cmd: ("", "", -9)  # noqa: E731 -- the post-kill drain call
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([first, drain]))
    with caplog.at_level(logging.WARNING):
        out = ocr_util.ensure_searchable(image_pdf)
    assert out == image_pdf
    assert "timeout" in caplog.text


def test_ensure_searchable_kills_whole_group_on_timeout(image_pdf, monkeypatch):
    # The actual fix (coder:ocr-hang-fix, 2026-10-14): a timeout must kill
    # ocrmypdf's WHOLE process group, not just subprocess.run()'s own one
    # pid. Unit-tested here against a controlled fake (see the real,
    # end-to-end fork()-based proof further down this file); this test
    # just confirms the WIRING -- os.getpgid()/os.killpg() are called with
    # the right pid/signal before the exception propagates.
    killpg_calls = []
    monkeypatch.setattr(ocr_util.os, "getpgid",
                        lambda pid: pytest.fail("must not re-look-up the pgid (TOCTOU)"))
    monkeypatch.setattr(ocr_util.os, "killpg",
                        lambda pgid, sig: killpg_calls.append((pgid, sig)))
    first = subprocess.TimeoutExpired(cmd=["ocrmypdf"], timeout=1)
    drain = lambda cmd: ("", "", -9)  # noqa: E731
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([first, drain]))

    with pytest.raises(subprocess.TimeoutExpired):
        ocr_util._run_ocrmypdf(["ocrmypdf", "x"], env={}, timeout=1, capture=False)

    # start_new_session=True: pgid == pid, so the group is killed by pid directly
    assert killpg_calls == [(4242424, ocr_util.signal.SIGKILL)]


def test_run_ocrmypdf_post_kill_drain_is_bounded(monkeypatch):
    # Security review 2026-10-14 (PR #143 Step 6): if the post-kill drain
    # ITSELF times out (a descendant escaped the group and holds the pipes),
    # _run_ocrmypdf must still raise TimeoutExpired rather than hang, and it
    # must pass a finite timeout to the second communicate() and the reap.
    seen = []
    monkeypatch.setattr(ocr_util.os, "killpg", lambda pgid, sig: None)

    class _P:
        pid = 4242424
        returncode = None
        stdout = None
        stderr = None

        def __init__(self, *a, **k):
            pass

        def communicate(self, timeout=None):
            seen.append(("communicate", timeout))
            raise subprocess.TimeoutExpired(cmd=["ocrmypdf"], timeout=timeout)

        def wait(self, timeout=None):
            seen.append(("wait", timeout))
            return -9

    monkeypatch.setattr(ocr_util.subprocess, "Popen", _P)
    with pytest.raises(subprocess.TimeoutExpired):
        ocr_util._run_ocrmypdf(["ocrmypdf"], env={}, timeout=1, capture=True)
    assert seen[0] == ("communicate", 1)
    assert seen[1] == ("communicate", ocr_util._POST_KILL_TIMEOUT_S)
    assert ("wait", ocr_util._POST_KILL_TIMEOUT_S) in seen
    assert all(t is not None for _, t in seen)


def test_run_ocrmypdf_kills_group_on_other_exceptions_too(monkeypatch):
    # Mirrors subprocess.run()'s own `except: process.kill(); raise` branch
    # (e.g. KeyboardInterrupt during communicate()) -- extended to the
    # whole group, same as the timeout path.
    killpg_calls = []
    monkeypatch.setattr(ocr_util.os, "killpg",
                        lambda pgid, sig: killpg_calls.append((pgid, sig)))
    monkeypatch.setattr(ocr_util.subprocess, "Popen",
                        fake_popen_factory([KeyboardInterrupt()]))

    with pytest.raises(KeyboardInterrupt):
        ocr_util._run_ocrmypdf(["ocrmypdf"], env={}, timeout=5, capture=False)

    assert killpg_calls == [(4242424, ocr_util.signal.SIGKILL)]


def test_ocr_in_place_raises_timeout_expired_and_kills_group(tmp_path, monkeypatch):
    # ocr_in_place()'s raise-on-failure contract (ADR 069: "moved verbatim,
    # unchanged") must survive this refactor exactly -- a timeout still
    # propagates as TimeoutExpired, and its own tmp file is still cleaned up
    # by its `finally` block, now with the process GROUP killed too.
    pdf_path = str(tmp_path / "doc.pdf")
    with open(pdf_path, "wb") as fh:
        fh.write(b"x")
    killpg_calls = []
    monkeypatch.setattr(ocr_util.os, "killpg",
                        lambda pgid, sig: killpg_calls.append((pgid, sig)))
    first = subprocess.TimeoutExpired(cmd=["ocrmypdf"], timeout=1)
    drain = lambda cmd: ("", "", -9)  # noqa: E731
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([first, drain]))

    with pytest.raises(subprocess.TimeoutExpired):
        ocr_util.ocr_in_place(pdf_path, timeout=1)

    assert killpg_calls == [(4242424, ocr_util.signal.SIGKILL)]
    assert not os.path.exists(pdf_path + ".ocr_tmp.pdf")


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

    def step(cmd):
        return "", "boom", 1
    monkeypatch.setattr(ocr_util.subprocess, "Popen", fake_popen_factory([step]))

    ocr_util.ensure_searchable(image_pdf)
    assert len(created_paths) == 1
    assert not os.path.exists(created_paths[0])


# ---------------------------------------------------------------------------
# Real, end-to-end proof (no Popen mocking): a fake "ocrmypdf" that forks a
# grandchild which does NOT call setsid() itself (matching ocrmypdf's own
# real worker processes, which inherit the parent's process group) and
# sleeps well past the configured timeout. Proves two things a mock
# structurally can't: (1) ensure_searchable() returns promptly, at roughly
# the configured timeout -- subprocess's own timeout already does this
# correctly even with a grandchild holding the pipes open (confirmed by a
# direct repro before writing this fix; see ocr_util's module docstring
# addendum); (2) this fix's actual improvement -- the grandchild is
# genuinely KILLED, not merely orphaned and left running to completion.
# ---------------------------------------------------------------------------


def test_ensure_searchable_real_subprocess_kills_grandchild_on_timeout(
        image_pdf, tmp_path, monkeypatch):
    started = tmp_path / "grandchild_started"
    finished = tmp_path / "grandchild_finished"
    script = tmp_path / "fake_ocrmypdf"
    script.write_text(
        "#!{python}\n"
        "import os, sys, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    open({started!r}, 'w').close()\n"
        "    time.sleep(5)\n"
        "    open({finished!r}, 'w').close()\n"  # only reached if NOT killed in time
        "    sys.exit(0)\n"
        "else:\n"
        "    time.sleep(5)\n".format(python=sys.executable, started=str(started),
                                     finished=str(finished))
    )
    script.chmod(0o755)
    monkeypatch.setenv("OCRMYPDF_BIN", str(script))

    t0 = time.monotonic()
    out = ocr_util.ensure_searchable(image_pdf, timeout=1)
    elapsed = time.monotonic() - t0

    assert out == image_pdf     # fail-open: original returned unchanged on timeout
    assert elapsed < 4          # promptly -- nowhere near the grandchild's 5s sleep

    deadline = time.monotonic() + 3
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started.exists(), "test setup is broken -- the grandchild never even started"

    # Wait past when the grandchild WOULD have finished its sleep(5) if it
    # had survived, unkilled, as an orphan.
    time.sleep(4.5)
    assert not finished.exists(), (
        "the grandchild ran to completion -- it was orphaned, not killed. "
        "This is exactly the bug this fix closes: subprocess.run()'s own "
        "timeout only kills the DIRECT child; the process-group kill below "
        "it must reach ocrmypdf's own worker processes too."
    )


def test_run_ocrmypdf_real_escaped_grandchild_capture_does_not_hang(tmp_path):
    # The Step 6 scenario end to end, no mocking: capture=True (the
    # ocr_in_place() path) and a grandchild that calls setsid() -- escaping
    # the killed group -- while keeping the stdout/stderr pipes open for 30 s.
    # Before the fix the post-kill communicate() blocked until it exited
    # (mutation-checked: 30.2 s). The sentinel proves the grandchild really
    # escaped before the kill; the lower bound proves the bounded drain was
    # actually exercised (not a fast path that skipped it).
    escaped = tmp_path / "grandchild_escaped"
    script = tmp_path / "fake_ocrmypdf_escape"
    script.write_text(
        "#!{python}\n"
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    open({escaped!r}, 'w').close()\n"
        "    time.sleep(30)\n"
        "    os._exit(0)\n"
        "time.sleep(30)\n".format(python=sys.executable, escaped=str(escaped))
    )
    script.chmod(0o755)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        ocr_util._run_ocrmypdf([str(script)], env=dict(os.environ), timeout=2, capture=True)
    elapsed = time.monotonic() - t0
    assert escaped.exists(), "test setup broken: the grandchild never escaped the group"
    assert elapsed >= 2 + ocr_util._POST_KILL_TIMEOUT_S - 0.5, f"drain not exercised ({elapsed:.1f}s)"
    assert elapsed < 2 + 2 * ocr_util._POST_KILL_TIMEOUT_S + 3, f"took {elapsed:.1f}s"


def test_ocr_in_place_passes_a_minimal_env_not_the_full_environment(image_pdf, monkeypatch):
    # SEC-008: ocr_in_place() (the parse_document OCR path) now uses the same
    # minimal environment as ensure_searchable(): no workflow secrets reach
    # ocrmypdf and the PDF toolchain it runs on untrusted filings.
    monkeypatch.setenv("GOAUTH_CLIENT_SECRET", "super-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    instances = []
    monkeypatch.setattr(ocr_util.subprocess, "Popen",
                        fake_popen_factory([_writes_text("text")], instances=instances))
    assert ocr_util.ocr_in_place(image_pdf) is True
    assert len(instances) == 1
    assert "GOAUTH_CLIENT_SECRET" not in instances[0].env
    assert "ANTHROPIC_API_KEY" not in instances[0].env
    assert "PATH" in instances[0].env
