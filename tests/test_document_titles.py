"""document_titles.py (ADR 065): generic-title detection, display-title
sanitization against name_check, and the doc_id override precedence."""
import document_titles as dt


GENERIC_EXACT = ["nForm Document", "Site", "Submission PDF",
                  "Submital Attachments", "Correspondence"]
GENERIC_PREFIXES = ["Schedule - "]


# --- title_is_generic -----------------------------------------------------

def test_title_is_generic_exact_match_case_insensitive():
    assert dt.title_is_generic("site", GENERIC_EXACT, GENERIC_PREFIXES) is True
    assert dt.title_is_generic("SITE", GENERIC_EXACT, GENERIC_PREFIXES) is True
    assert dt.title_is_generic("nForm Document", GENERIC_EXACT, GENERIC_PREFIXES) is True


def test_title_is_generic_prefix_match():
    assert dt.title_is_generic(
        "Schedule - Air General Compliance Report", GENERIC_EXACT, GENERIC_PREFIXES) is True
    assert dt.title_is_generic(
        "schedule - dmr", GENERIC_EXACT, GENERIC_PREFIXES) is True


def test_title_is_generic_false_for_real_title():
    assert dt.title_is_generic("Violation Notice", GENERIC_EXACT, GENERIC_PREFIXES) is False
    assert dt.title_is_generic(
        "On-Site Inspection (06/01/2025)", GENERIC_EXACT, GENERIC_PREFIXES) is False


def test_title_is_generic_blank_title_is_never_generic():
    assert dt.title_is_generic("", GENERIC_EXACT, GENERIC_PREFIXES) is False
    assert dt.title_is_generic(None, GENERIC_EXACT, GENERIC_PREFIXES) is False


def test_title_is_generic_no_lists_always_false():
    # The domain-agnostic default (egle_doc_parser's reuse story): an empty/
    # missing config never flags anything as generic.
    assert dt.title_is_generic("Site") is False


# --- sanitize_display_title ------------------------------------------------

def test_sanitize_display_title_passes_clean_text_through():
    clean = "Fibertec lab report to ERG for Advanced Disposal compost-pond samples, dated 12/23/2019"
    assert dt.sanitize_display_title(clean) == clean


def test_sanitize_display_title_strips_a_known_name():
    # "Anthony Testa" is in name_check.KNOWN_NAMES.
    dirty = "EGLE letter to Anthony Testa approving a 120-day extension"
    cleaned = dt.sanitize_display_title(dirty)
    assert "Testa" not in cleaned
    assert "Anthony" not in cleaned
    assert cleaned  # not blanked entirely -- the rest of the title survives


def test_sanitize_display_title_blank_input_is_blank():
    assert dt.sanitize_display_title("") == ""
    assert dt.sanitize_display_title(None) == ""


def test_sanitize_display_title_falls_back_empty_when_unstrippable(monkeypatch):
    # If stripping can't converge (every pass still finds a hit), return ""
    # so the caller falls back to the plain nSITE title rather than publish
    # anything questionable.
    monkeypatch.setattr(
        dt.name_check, "find_denylist_hits",
        lambda text: [{"kind": "known_name", "match": "ZZZ_NOT_IN_TEXT"}])
    assert dt.sanitize_display_title("Some title") == ""


# --- resolve_display_name ---------------------------------------------------

def test_resolve_display_name_override_wins():
    overrides = {"123": "EGLE letter: extension approval"}
    assert dt.resolve_display_name("123", "Schedule - Air General Compliance Report",
                                   "", overrides) == "EGLE letter: extension approval"


def test_resolve_display_name_display_title_when_no_override():
    assert dt.resolve_display_name("999", "Site", "A real descriptive title", {}) == \
        "A real descriptive title"


def test_resolve_display_name_falls_back_to_nsite_title():
    assert dt.resolve_display_name("999", "Violation Notice", "", {}) == "Violation Notice"


def test_resolve_display_name_override_beats_display_title_too():
    overrides = {"7": "Curated name"}
    assert dt.resolve_display_name("7", "Site", "Classifier-proposed name", overrides) == \
        "Curated name"
