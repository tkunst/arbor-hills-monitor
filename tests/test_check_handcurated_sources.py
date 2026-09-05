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


def _hc(source, title="Doc", doc_date="2026-09-05"):
    # Hand-Curated column order (sheet_writer.TAB_HANDCURATED): curated_filename,
    # title, source, doc_date, facility, doc_type, risks, origin_url, note,
    # drive_link, added_at, folded_into_public.
    return ["f.pdf", title, source, doc_date, "N2688", "procedural",
            "", "", "note", "https://drive.google.com/x", "2026-09-05T00:00:00", "no"]


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
