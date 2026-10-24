"""Static checks on site/public-records/search.js (ADR 062 Phase 3) -- this is
a plain static JS file with no runtime/browser under pytest, so these are
text-level drift and discipline checks, not behavior tests. Manual
in-browser verification of the actual filtering/facet/date-range behavior is
described in the PR."""
import ast
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


def test_search_js_never_interpolates_markup():
    # The data is already curated/redacted by the time it reaches this file
    # (see findings_feed._public_view), but every field is still rendered via
    # textContent/createElement -- never raw markup interpolation of field
    # text -- matching the Python side's own blanket _esc() discipline as
    # defense in depth. Bans the bare tokens (not just a `.innerHTML =`
    # assignment) so a `+=`, an `.outerHTML` write, or an
    # `.insertAdjacentHTML(...)` call would also fail this test; comments in
    # this file must describe the rule without naming these APIs, or this
    # test would flag its own comment.
    js = _search_js_text()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert forbidden not in js, f"found {forbidden!r} in search.js"


def test_search_js_fetches_without_credentials():
    # search-index.json is a public, same-origin static file -- never sent
    # with cookies/credentials, and never a cross-origin request.
    js = _search_js_text()
    assert re.search(r'credentials:\s*"omit"', js)


def test_search_js_checks_link_scheme_before_using_it():
    # A hand-curated `link` is human-typed free text (see
    # findings_feed._public_view); search.js must re-check the http(s)-only
    # scheme itself rather than trusting the JSON blindly. Matches the actual
    # regex literal, not just the substring "https?" (which a stray comment
    # could satisfy without a real check behind it).
    js = _search_js_text()
    assert re.search(r"/\^https\?:\\/\\//i", js)


def test_search_js_loads_facets_on_opening_filters_not_only_on_typing():
    # A <select> holding only its default "All ..." option never fires
    # "change" by itself, so a visitor who opens Filters and clicks straight
    # into a facet dropdown -- without first typing search text or picking a
    # date -- must still get the fetch that populates it. Pins the fix for
    # the Step 5 review finding: facets used to load only from the text/date
    # listeners, leaving them permanently empty on that path.
    js = _search_js_text()
    assert re.search(r'addEventListener\(\s*"toggle"', js)


# --- coder:public-records-search-words: structural pins on the tokenized
# multi-word matcher. These are drift guards (so a future edit can't quietly
# revert to a whole-query, three-field, substring-only matcher without this
# file noticing) -- the actual matching RULES are tested behaviorally via the
# Python reference port in tests/test_search_matcher.py (no JS runtime under
# pytest, per this file's own docstring).


def test_search_js_tokenizes_the_query_instead_of_matching_it_whole():
    # Checks the actual WIRING, not just that a function with this name
    # exists somewhere in the file (a dead, unused tokenizeWords would pass a
    # bare function-declaration check while currentFilters()/matchesText()
    # quietly still did whole-query matching).
    js = _search_js_text()
    assert re.search(r"function tokenizeWords\(", js)
    assert re.search(r"qTokens:\s*tokenizeWords\(", js)
    assert re.search(r"matchesText\(entry,\s*f\.qTokens\)", js)


def test_search_js_stem_floor_is_six_characters():
    # The 6-char floor on a text word before it can stem-match a longer query
    # token (see search.js's own comment) is what stops a short word like
    # "pfas" or "well" from matching every longer query that starts with it.
    js = _search_js_text()
    assert re.search(r"STEM_MIN_WORD_LENGTH\s*=\s*6", js)


def test_search_js_searches_source_and_date_fields_too():
    # Was title/facility/excerpt only; the handoff's whole point #3/#4 was
    # that the Source line and the date were never searched at all. Checks
    # the literal push calls inside searchableText specifically -- "entry.
    # source"/"entry.date" alone already appear elsewhere in this file
    # (renderCard, parseDateBounds) even on the OLD three-field matcher, so a
    # bare substring check wouldn't actually prove these two fields feed the
    # search text.
    js = _search_js_text()
    assert re.search(r"function searchableText\(entry\)", js)
    assert re.search(r"parts\.push\(entry\.source\)", js)
    assert re.search(r"parts\.push\(entry\.date\)", js)


def test_search_js_and_python_port_agree_on_edge_punctuation_set():
    # tests/test_search_matcher.py hand-ports this exact character set (its
    # own docstring says so) -- nothing else enforces the two stay in sync,
    # so a future edit to one side alone would silently diverge.
    js = _search_js_text()
    js_match = re.search(r"WORD_EDGE_PUNCTUATION\s*=\s*/\^\[([^\]]+)\]", js)
    assert js_match, "could not find the WORD_EDGE_PUNCTUATION regex literal in search.js"
    js_chars = set(js_match.group(1))

    matcher_path = os.path.join(REPO_ROOT, "tests", "test_search_matcher.py")
    with open(matcher_path, encoding="utf-8") as f:
        py_src = f.read()
    py_match = re.search(r'_WORD_EDGE_PUNCTUATION\s*=\s*("(?:[^"\\]|\\.)*")', py_src)
    assert py_match, "could not find the _WORD_EDGE_PUNCTUATION literal in test_search_matcher.py"
    py_chars = set(ast.literal_eval(py_match.group(1)))

    assert js_chars == py_chars
