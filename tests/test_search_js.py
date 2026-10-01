"""Static checks on site/public-records/search.js (ADR 062 Phase 3) -- this is
a plain static JS file with no runtime/browser under pytest, so these are
text-level drift and discipline checks, not behavior tests. Manual
in-browser verification of the actual filtering/facet/date-range behavior is
described in the PR."""
import importlib.util
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEARCH_JS_PATH = os.path.join(REPO_ROOT, "site", "public-records", "search.js")
_SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")


def _load_script_module(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SCRIPTS_DIR, f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _search_js_text():
    with open(SEARCH_JS_PATH, encoding="utf-8") as f:
        return f.read()


def test_search_js_exists():
    assert os.path.isfile(SEARCH_JS_PATH)


def test_search_js_filename_matches_the_generator_and_the_gate():
    # gen_findings_feed.py writes this filename, check_publish_safety.py
    # gates it, and search.js fetches it -- three independent copies, no
    # shared import between a Python script and this static JS file. If any
    # one drifts (a rename, a typo), the fetch 404s silently in production
    # (no CI signal) unless this stays pinned.
    gff = _load_script_module("gen_findings_feed")
    cps = _load_script_module("check_publish_safety")
    js = _search_js_text()
    assert gff.SEARCH_INDEX_FILENAME == cps.SEARCH_INDEX_FILENAME
    assert f'"{gff.SEARCH_INDEX_FILENAME}"' in js


def test_search_js_never_assigns_innerhtml():
    # The data is already curated/redacted by the time it reaches this file
    # (see findings_feed._public_view), but every field is still rendered via
    # textContent/createElement -- never raw innerHTML interpolation of field
    # text -- matching the Python side's own blanket _esc() discipline as
    # defense in depth. Checks for an actual assignment, not the bare word,
    # since a comment is allowed to mention innerHTML by name.
    js = _search_js_text()
    assert not re.search(r"\.innerHTML\s*=", js)


def test_search_js_fetches_without_credentials():
    # search-index.json is a public, same-origin static file -- never sent
    # with cookies/credentials, and never a cross-origin request.
    js = _search_js_text()
    assert re.search(r'credentials:\s*"omit"', js)


def test_search_js_checks_link_scheme_before_using_it():
    # A hand-curated `link` is human-typed free text (see
    # findings_feed._public_view); search.js must re-check the http(s)-only
    # scheme itself rather than trusting the JSON blindly.
    js = _search_js_text()
    assert "https?" in js
