"""`import nsite_client` must NOT load the PDF stack.

The seven nSITE profile watchers (Streams K-Q: submissions / violations /
compliance actions / evaluations / permits / complaints / public notices) only
poll EGLE's nSITE JSON profiles, yet every one of them imports `nsite_client`.
Until 2026-10-23 that module imported `poison_doc_extractor` at module level,
which in turn imports `fitz` (PyMuPDF) and `defusedxml` at ITS module level —
so merely importing the client dragged the PDF-parsing stack into every
watcher process. The import is now deferred into `download_pdf()`, the one
function that uses it.

Each check runs in a FRESH interpreter subprocess rather than poking at
`sys.modules` in-process: pytest's own collection has long since imported
`poison_doc_extractor` (tests/test_nsite_download.py does so directly), and
evicting modules from `sys.modules` mid-session is both fragile and not what
the watchers experience. A clean `python -c` is exactly the condition under
which the seven workflows start.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The modules that must stay out of a bare `import nsite_client`. `fitz` and
# `defusedxml` are the heavy third-party halves `poison_doc_extractor` pulls in
# at its own top level; `egle_doc_parser` is the LLM/OCR half, which
# `poison_doc_extractor` itself only imports lazily — included so a future
# module-level import of it anywhere in the chain is caught here too.
PDF_STACK = ("poison_doc_extractor", "egle_doc_parser", "fitz", "defusedxml")


def _run(snippet: str) -> dict:
    """Run `snippet` in a clean interpreter from the repo root and return the
    JSON object it prints on its last line."""
    proc = subprocess.run(  # noqa: S603 — fixed interpreter, fixed snippet, no user input
        [sys.executable, "-c", textwrap.dedent(snippet)],
        cwd=REPO, capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_bare_import_of_nsite_client_does_not_load_the_pdf_stack():
    loaded = _run("""
        import json, sys
        import nsite_client
        print(json.dumps({"loaded": sorted(m for m in sys.modules
                                           if m.split(".")[0] in %r)}))
    """ % (PDF_STACK,))["loaded"]
    assert loaded == [], f"`import nsite_client` pulled in: {loaded}"


@pytest.mark.parametrize("watcher", [
    "nsite_submissions_watcher",
    "nsite_violations_watcher",
    "nsite_compliance_actions_watcher",
    "nsite_evaluations_watcher",
    "nsite_permits_watcher",
    "nsite_complaints_watcher",
    "nsite_public_notices_watcher",
])
def test_profile_watchers_do_not_load_the_pdf_stack(watcher):
    """The actual beneficiaries: each Stream K-Q watcher, imported the way its
    workflow starts it, no longer carries fitz/defusedxml/the parser."""
    loaded = _run("""
        import json, sys
        import %s
        print(json.dumps({"loaded": sorted(m for m in sys.modules
                                           if m.split(".")[0] in %r)}))
    """ % (watcher, PDF_STACK))["loaded"]
    assert loaded == [], f"`import {watcher}` pulled in: {loaded}"


def test_download_pdf_fallback_path_loads_the_extractor_on_demand(tmp_path):
    """The other half of the contract: the deferred import still happens, and
    exactly where it is needed. A scripted session serves a non-PDF body from
    every source, so `download_pdf()` reaches its ADR 011 extractor fallback;
    `poison_doc_extractor` (and the `fitz` it imports) must be present AFTER
    that call having been absent BEFORE it. The body is an OLE magic prefix
    with nothing behind it, so synthesis fails and `download_pdf()` raises
    its usual RuntimeError — that exception is expected and swallowed; only
    the import-graph side effect is under test."""
    dest = str(tmp_path / "out.pdf")
    result = _run("""
        import json, sys
        import nsite_client as nc
        before = sorted(m for m in sys.modules if m.split(".")[0] in %r)

        class _Resp:
            content = b"\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1" + b"\\x00" * 64
            def raise_for_status(self): pass

        class _Session:
            def get(self, url, headers=None, timeout=None):
                return _Resp()

        doc = {"doc_id": "1", "doc_url": "https://example.invalid/primary"}
        raised = None
        try:
            nc.download_pdf(_Session(), doc, %r)
        except RuntimeError as e:  # the expected synthesis failure
            raised = type(e).__name__
        after = sorted(m for m in sys.modules if m.split(".")[0] in %r)
        print(json.dumps({"before": before, "after": after, "raised": raised}))
    """ % (PDF_STACK, dest, PDF_STACK))
    assert result["before"] == []
    assert "poison_doc_extractor" in result["after"]
    assert "fitz" in result["after"]
    assert result["raised"] is not None, "download_pdf should have raised on the unextractable body"
