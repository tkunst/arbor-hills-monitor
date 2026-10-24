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


def test_raw_text_phase1_returns_one_entry_per_page(tmp_path):
    # Code review finding (round 2): _raw_text_phase1 used to join the pages
    # into ONE string before extract_document_date_from_text saw them,
    # silently defeating its "a later page's match overrides an earlier
    # page's" rule -- the exact real-world trap (a mismatched page-1/page-2
    # EGLE letter date) this whole feature exists to handle correctly.
    import fitz

    pdf_path = tmp_path / "N2688_123.pdf"
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "July 10, 2026\nVIA EMAIL")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "VIOLATION NOTICE\nPage 2\nJuly 8, 2026")
    doc.save(str(pdf_path))
    doc.close()

    pages = bdt._raw_text_phase1(
        session=None, doc={"doc_id": "123", "facility_srn": "N2688"},
        pdf_dir=str(tmp_path), tmp_dir=str(tmp_path))
    assert isinstance(pages, list)
    assert len(pages) == 2

    det_date, det_method = bdt.extract_document_date_from_text(pages)
    assert det_date == "2026-07-08"  # the LATER page's date wins
    assert det_method == "dateline"


def test_csv_fields_match_spec_order():
    assert bdt.CSV_FIELDS == [
        "doc_id", "site", "date_filed", "document_date", "method",
        "nsite_title", "proposed_display_title", "name_check_result",
    ]


# --- CSV formula injection (security review round 2, CWE-1236) ------------

def test_neutralize_csv_formulas_prefixes_each_trigger_char():
    for trigger in ("=", "+", "-", "@"):
        row = {"nsite_title": f"{trigger}HYPERLINK(\"http://evil\")"}
        out = bdt._neutralize_csv_formulas(row)
        assert out["nsite_title"].startswith("'" + trigger)


def test_neutralize_csv_formulas_leaves_plain_text_unchanged():
    row = {"nsite_title": "Violation Notice", "doc_id": "123"}
    assert bdt._neutralize_csv_formulas(row) == row


def test_neutralize_csv_formulas_handles_non_string_values():
    row = {"doc_id": 123, "method": None}
    assert bdt._neutralize_csv_formulas(row) == row


# --- Path safety (security review round 2) ---------------------------------

def test_safe_pdf_filename_accepts_normal_values():
    assert bdt._safe_pdf_filename("N2688", "-2622199756316422732") == \
        "N2688_-2622199756316422732.pdf"


def test_safe_pdf_filename_rejects_path_traversal_doc_id():
    assert bdt._safe_pdf_filename("N2688", "../../etc/passwd") is None


def test_safe_pdf_filename_rejects_unsafe_srn():
    assert bdt._safe_pdf_filename("../escape", "123") is None


def test_local_pdf_path_rejects_unsafe_doc_id_even_if_file_exists(tmp_path):
    # Even if an attacker-controlled doc_id happened to collide with a real
    # filename on disk, the shape check must reject it before any path join.
    bad = "../escape"
    assert bdt._local_pdf_path(str(tmp_path), "N2688", bad) is None
