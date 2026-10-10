"""backfill_document_dates_titles.py (ADR 065 dry-run review): the two pure
helpers. The script's run() orchestration (Sheets + nSITE + PDF I/O) is a
dry-run-only one-off tool exercised manually against real data per the
handoff -- the logic it reuses (extract_document_date_from_text,
parse_document, document_titles.*) is already covered in
tests/test_parser.py and tests/test_document_titles.py."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "backfill_document_dates_titles",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "backfill_document_dates_titles.py"),
)
bdt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bdt)


def test_local_pdf_path_none_when_no_pdf_dir():
    assert bdt._local_pdf_path(None, "N2688", "123") is None


def test_local_pdf_path_none_when_file_missing(tmp_path):
    assert bdt._local_pdf_path(str(tmp_path), "N2688", "123") is None


def test_local_pdf_path_found(tmp_path):
    (tmp_path / "N2688_123.pdf").write_bytes(b"%PDF-1.4 fake")
    assert bdt._local_pdf_path(str(tmp_path), "N2688", "123") == \
        str(tmp_path / "N2688_123.pdf")


def test_name_check_result_no_display_title():
    assert bdt._name_check_result("") == "n/a (no display_title proposed)"


def test_name_check_result_clean():
    assert bdt._name_check_result("A clean descriptive title") == "clean"


def test_name_check_result_cleaned():
    result = bdt._name_check_result("Letter to Anthony Testa about an extension")
    assert result.startswith("cleaned: ")
    assert "Testa" not in result


def test_name_check_result_blocked(monkeypatch):
    monkeypatch.setattr(bdt.dt, "sanitize_display_title", lambda text: "")
    assert bdt._name_check_result("Some title") == \
        "BLOCKED — could not be made publish-clean, falls back to nSITE title"


def test_csv_fields_match_spec_order():
    assert bdt.CSV_FIELDS == [
        "doc_id", "site", "date_filed", "document_date", "method",
        "nsite_title", "proposed_display_title", "name_check_result",
    ]
