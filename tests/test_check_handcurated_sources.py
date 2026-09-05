"""Hermetic tests for the pure part of scripts/check_handcurated_sources.py
(the blank-source detector) -- no network, no Sheet."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "check_handcurated_sources",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "check_handcurated_sources.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
chs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chs)


def _hc(source_public, title="Doc", doc_date="2026-09-05"):
    # Hand-Curated column order (sheet_writer.TAB_HANDCURATED), 13 cols. The
    # PUBLISHED source is col M (source_public) -- what this check inspects; the
    # internal source (col C) is irrelevant here and never published.
    return ["f.pdf", title, "internal-source", doc_date, "N2688", "procedural",
            "", "", "note", "https://drive.google.com/x", "2026-09-05T00:00:00",
            "no", source_public]


def test_flags_blank_and_whitespace_source_only():
    rows = [
        _hc("EGLE / nSITE", title="Has Source"),
        _hc("", title="Blank Source"),
        _hc("   ", title="Whitespace Source"),
    ]
    blanks = chs.find_blank_source_rows(rows)
    names = sorted(r["document_name"] for r in blanks)
    assert names == ["Blank Source", "Whitespace Source"]


def test_no_blanks_returns_empty():
    rows = [_hc("EGLE / nSITE"), _hc("Salem Township")]
    assert chs.find_blank_source_rows(rows) == []


def test_short_row_missing_source_column_is_flagged():
    # A row that does not even reach the source column (col C) -- parse pads it
    # to a blank source, so it must be flagged, not crash.
    short = ["f.pdf", "Title only"]
    assert len(chs.find_blank_source_rows([short])) == 1
