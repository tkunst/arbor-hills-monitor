"""Behavioral tests for the Public Records search matcher (coder:public-
records-search-words). search.js is a static file with no JS runtime under
pytest (see tests/test_search_js.py's docstring), so this module carries a
hand-ported, pure-Python MIRROR of its tokenizer/matcher functions
(tokenizeWords/searchableText/tokenMatches/matchesText) -- there is no shared
import between a Python test and a static JS file, the same constraint
search.js's own top comment already documents for SEARCH_INDEX_FILENAME. Any
future change to the JS matching rule must be mirrored here by hand, and
tests/test_search_js.py's structural pins exist so that drift doesn't go
unnoticed.

Why Python instead of running the real search.js under node: node is
available on this machine, but this repo's CI (`tests.yml`) only provisions
`actions/setup-python` -- there is no `actions/setup-node` step, so relying
on a node binary in CI would depend on an undeclared detail of the GitHub
Actions image rather than anything this repo actually sets up. That also
matches this repo's existing, deliberate choice (test_search_js.py's
docstring) to keep this JS file's tests text-level/hermetic under pytest.

Two kinds of coverage below:
  1. Hermetic unit tests on small synthetic entries -- pin the matching RULES
     themselves (tokenization, AND/any-order semantics, the substring vs.
     stem-prefix conditions, the 6-char stem floor, which fields are
     searched).
  2. Real-index tests against the live `site/public-records/search-index.json`
     -- the handoff's Step 3 "verify against the real index" requirement,
     kept in the suite (not just a one-off script) so it's re-checked on
     every run. These intentionally assert PRESENCE/SUBSET/relative facts,
     never hardcoded absolute counts: this is a live, append-only archive
     that grows most nights, so a hardcoded "historic == 35" would start
     failing on its own as soon as new hand-curated rows land. The actual
     before/after counts observed at build time are reported in the PR
     description instead.
"""
import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEARCH_INDEX_PATH = os.path.join(REPO_ROOT, "site", "public-records", "search-index.json")

# --- hand-ported mirror of site/public-records/search.js --------------------

_WORD_EDGE_PUNCTUATION = "#,.;:()\"'"
STEM_MIN_WORD_LENGTH = 6


def tokenize_words(s):
    if not s:
        return []
    words = []
    for piece in str(s).split():
        w = piece.strip(_WORD_EDGE_PUNCTUATION).lower()
        if w:
            words.append(w)
    return words


def searchable_text(entry):
    parts = []
    for field in ("title", "facility", "excerpt"):
        v = entry.get(field)
        if v:
            parts.append(str(v))
    if "source" in entry and entry.get("source"):
        parts.append(str(entry["source"]))
    if entry.get("date"):
        parts.append(str(entry["date"]))
    return " ".join(parts)


def token_matches(token, text_lower, text_words):
    if token in text_lower:
        return True
    for w in text_words:
        if len(w) >= STEM_MIN_WORD_LENGTH and token.startswith(w):
            return True
    return False


def matches_query(entry, query_tokens):
    if not query_tokens:
        return True
    text = searchable_text(entry)
    text_lower = text.lower()
    text_words = tokenize_words(text)
    return all(token_matches(tok, text_lower, text_words) for tok in query_tokens)


def search(entries, query):
    tokens = tokenize_words(query)
    return [e for e in entries if matches_query(e, tokens)]


# --- hermetic unit tests on the rule itself ---------------------------------


def test_tokenize_strips_surrounding_punctuation_and_lowercases():
    assert tokenize_words('PEAS #24917, (please check).') == ["peas", "24917", "please", "check"]


def test_tokenize_ignores_empty_tokens():
    assert tokenize_words("  ##  ...  ") == []
    assert tokenize_words("") == []
    assert tokenize_words(None) == []


def test_empty_query_matches_everything():
    entries = [{"title": "A"}, {"title": "B"}, {}]
    assert search(entries, "") == entries


def test_and_semantics_any_order():
    entries = [{"title": "Alpha Beta Gamma"}]
    assert search(entries, "gamma alpha") == entries
    assert search(entries, "alpha delta") == []


def test_substring_match_keeps_partial_word_behavior():
    entries = [{"title": "Revised Hydrogeologic Monitoring Plan"}]
    assert search(entries, "hydrogeolog") == entries
    assert search(entries, "24917") == []


def test_punctuation_in_query_does_not_block_a_match():
    entries = [{"excerpt": "leachate spill PEAS #24917 reported"}]
    assert search(entries, "PEAS #24917") == entries
    assert search(entries, "24917") == entries


def test_stem_rule_lets_a_longer_query_find_a_shorter_text_word():
    entries = [{"title": "Hydrogeologic Monitoring Plan"}]
    assert search(entries, "hydrogeological") == entries


def test_stem_floor_blocks_short_words_from_matching_everything():
    # "well" is only 4 characters -- below STEM_MIN_WORD_LENGTH -- so it must
    # NOT let "wellington" match text that merely contains the word "well".
    entries = [{"title": "Well monitoring report"}]
    assert search(entries, "wellington") == []
    # A real 6+-char word, by contrast, does enable the stem match -- with no
    # upper bound on how much longer the query can run past that word.
    entries2 = [{"title": "Hydrogeologic Monitoring Plan"}]
    assert search(entries2, "hydrogeological") == entries2
    assert search(entries2, "hydrogeologicalxyz") == entries2


def test_source_field_is_searched_only_when_present():
    with_source = {"title": "Permit renewal", "source": "County historic file"}
    without_source = {"title": "Permit renewal"}
    assert search([with_source], "historic") == [with_source]
    assert search([without_source], "historic") == []


def test_date_field_is_searched():
    entries = [{"title": "PFAS sampling results", "date": "2023-07-13"}]
    assert search(entries, "pfas 2023") == entries
    assert search(entries, "pfas 2024") == []


def test_facility_field_is_searched():
    entries = [{"title": "Quarterly report", "facility": "Arbor Hills Landfill"}]
    assert search(entries, "arbor") == entries


# --- frozen acceptance fixtures (handoff's acceptance table) ---------------
#
# Snapshots of real rows named in the handoff's acceptance table, captured
# 2026-10-10 from the live site/public-records/search-index.json (2074 rows
# at the time). The live, mutable file was ALSO run directly -- against both
# this reference port and the actual search.js under node -- as the one-time
# Step 3 real-specimen verification; those before/after counts are reported
# in the PR description, not re-asserted here on every run.
#
# These are frozen dict literals rather than a live read of search-index.json
# on purpose: this is an append-only archive that keeps growing, and rows do
# occasionally get removed or retitled by unrelated later work (e.g. the
# 2026-10-07 facility-scope migration removed 440 rows; a pending
# display-title backfill will retitle more). A required CI check that reads
# today's live index and asserts specific rows/counts would go red on some
# future, unrelated data change -- the opposite of this repo's "hermetic
# tests" rule (CLAUDE.md). Freezing the specimens here keeps the acceptance
# RULES pinned forever without coupling this check to live data.

GOLDER_2018_SEP = {
    "date": "2018-09-21",
    "title": (
        "Revised Hydrogeologic Monitoring Plan, Arbor Hills West Expanded "
        "Sanitary Landfill -- September 2018 Revision (Golder Associates, "
        "Sep 21 2018)"
    ),
    "facility": "Arbor Hills Landfill",
    "type": "procedural",
    "source": "Golder Associates for Advanced Disposal Services -> EGLE WMRPD Jackson District",
    "link": "https://drive.google.com/file/d/1BU_Gj43AKmpPLV_DL12g8ipj8hwWZupr/view",
}

GOLDER_2018_FEB = {
    "date": "2018-02-28",
    "title": (
        "Revised Hydrogeologic Monitoring Plan, Arbor Hills West Expanded "
        "Sanitary Landfill (Golder Associates, Feb 28 2018)"
    ),
    "facility": "Arbor Hills Landfill",
    "type": "procedural",
    "source": "Golder Associates for Advanced Disposal Services -> EGLE WMRPD Jackson District",
    "link": "https://drive.google.com/file/d/198ETBqxECd5hYOpOzPNeURFBXpR-SvLE/view",
}

HYDROGEOLOGICAL_1993 = {
    "date": "1993-04-14",
    "title": (
        "Report on Hydrogeological Investigation, Salem Landfill closure "
        "(Hennessey Engineers, 4/14/1993), Part 201 site 81000033"
    ),
    "facility": "Salem Landfill (closed), Part 201 site 81000033",
    "type": "evidence",
    "source": "EGLE RRD, RIDE public file listing",
    "link": "https://drive.google.com/file/d/1FlnnShMztu7IEGKx6A6eWR9EYm01UKyS/view",
}

CEC_24917 = {
    "date": "2023-07-13",
    "title": (
        "CEC Field Summary Report for the March-2023 Workplan (leachate "
        "spill PEAS #24917) + HMP-revision recommendations, submitted under "
        "2023 MMD Consent Order Sec 2.3 (CEC, July 13 2023)"
    ),
    "facility": "Arbor Hills Landfill",
    "type": "evidence",
    "source": "Civil & Environmental Consultants (CEC) for GFL/Arbor Hills -> EGLE",
    "link": "https://drive.google.com/file/d/1gu3TSX89FIiVaboBvCrd4g4h9UU8Plg4/view",
}

VN_24917 = {
    "date": "2023-03-16",
    "title": "Violation Notice Correspondence",
    "facility": "Arbor Hills Remediation Area",
    "type": "evidence",
    "severity": "notable",
    "excerpt": (
        "GFL Environmental's response to an EGLE violation notice regarding "
        "a leachate spill incident (PEAS #24917) that occurred on August 4, "
        "2022, at Arbor Hills Landfill due to a power outage from an..."
    ),
    "link": "https://drive.google.com/file/d/1Mliz_ItPSC-BXFiv9E18erveax9VpDjD/view?usp=drivesdk",
}

HISTORIC_VIA_SOURCE = {
    "date": "1990-05-29",
    "title": (
        "Washtenaw County Environmental Health Bureau letter to Salem "
        "Township on a solid waste construction permit application / "
        "modification, May 29, 1990"
    ),
    "facility": "Arbor Hills Landfill",
    "type": "procedural",
    "source": "Washtenaw County Environmental Health (historic)",
    "link": "https://drive.google.com/file/d/1jv-T289UlvkXentU64Z1R6vSZnwlFIXa/view",
}

# Distractors -- NOT named in the handoff, included so a matcher that quietly
# degrades to OR semantics (or ignores the date field) gets caught rather
# than passing by accident because the fixture only contains true positives.
PFAS_2023_ROW = {
    "date": "2023-02-01",
    "title": "PFAS sampling results summary",
    "facility": "Arbor Hills Landfill",
    "type": "evidence",
    "link": "https://drive.google.com/file/d/0000000000000000000a/view",
}
PFAS_NOT_2023 = {
    "date": "2022-05-01",
    "title": "PFAS sampling results summary",
    "facility": "Arbor Hills Landfill",
    "type": "evidence",
    "link": "https://drive.google.com/file/d/0000000000000000000b/view",
}
UNRELATED_2024 = {
    "date": "2024-01-01",
    "title": "Unrelated annual compliance certification",
    "facility": "Arbor Hills Landfill",
    "type": "procedural",
    "link": "https://drive.google.com/file/d/0000000000000000000c/view",
}

ACCEPTANCE_FIXTURE = [
    GOLDER_2018_SEP,
    GOLDER_2018_FEB,
    HYDROGEOLOGICAL_1993,
    CEC_24917,
    VN_24917,
    HISTORIC_VIA_SOURCE,
    PFAS_2023_ROW,
    PFAS_NOT_2023,
    UNRELATED_2024,
]


def test_acceptance_hydrogeologic_monitoring_finds_both_golder_2018_rows():
    results = search(ACCEPTANCE_FIXTURE, "hydrogeologic monitoring")
    assert GOLDER_2018_SEP in results
    assert GOLDER_2018_FEB in results


def test_acceptance_word_order_does_not_matter():
    a = search(ACCEPTANCE_FIXTURE, "hydrogeologic monitoring")
    b = search(ACCEPTANCE_FIXTURE, "monitoring hydrogeologic")
    assert a == b
    assert a  # not two vacuously-equal empty results


def test_acceptance_longer_spelling_finds_shorter_text_word_and_itself():
    results = search(ACCEPTANCE_FIXTURE, "hydrogeological")
    assert HYDROGEOLOGICAL_1993 in results  # says "Hydrogeological" in full
    # These two only say "Hydrogeologic" (shorter spelling) -- found only via
    # the stem rule, never a literal substring match.
    assert GOLDER_2018_SEP in results
    assert GOLDER_2018_FEB in results


def test_acceptance_partial_word_is_a_superset():
    reordered = search(ACCEPTANCE_FIXTURE, "monitoring hydrogeologic")
    full_spelling = search(ACCEPTANCE_FIXTURE, "hydrogeological")
    partial = search(ACCEPTANCE_FIXTURE, "hydrogeolog")
    for row in full_spelling + reordered:
        assert row in partial


def test_acceptance_punctuation_insensitive_id_number():
    a = search(ACCEPTANCE_FIXTURE, "24917")
    b = search(ACCEPTANCE_FIXTURE, "PEAS #24917")
    assert a == b
    assert CEC_24917 in a
    assert VN_24917 in a


def test_acceptance_source_line_is_searched():
    results = search(ACCEPTANCE_FIXTURE, "historic")
    assert HISTORIC_VIA_SOURCE in results
    assert "historic" not in HISTORIC_VIA_SOURCE["title"].lower()


def test_acceptance_date_narrows_by_year():
    broad = search(ACCEPTANCE_FIXTURE, "PFAS")
    narrow = search(ACCEPTANCE_FIXTURE, "PFAS 2023")
    assert PFAS_2023_ROW in broad and PFAS_NOT_2023 in broad
    assert PFAS_2023_ROW in narrow
    assert PFAS_NOT_2023 not in narrow
    assert len(narrow) < len(broad)


def test_acceptance_empty_query_matches_everything():
    assert search(ACCEPTANCE_FIXTURE, "") == ACCEPTANCE_FIXTURE


def test_real_search_index_file_is_present_and_well_formed():
    # Lightweight sanity check only -- not row/count-dependent, so it can't
    # go red from ordinary curation of the archive.
    assert os.path.isfile(SEARCH_INDEX_PATH)
    with open(SEARCH_INDEX_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) > 0
