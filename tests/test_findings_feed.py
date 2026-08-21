"""Findings feed render/format logic — pagination boundaries, blank-field
handling, sort order, link passthrough (no Drive links introduced)."""
import findings_feed as ff


def _row(date="2026-08-01", name="Doc", risks="R5", link="https://x/1",
         facility="Arbor Hills Remediation Area", summary="A summary.",
         kdp="180F at AHW272.", doc_type="evidence", severity="notable"):
    # Same column order as sheet_writer.FEED_HEADERS.
    return [date, name, doc_type, risks, severity, summary, kdp, link, facility]


# --- parse_feed_rows -----------------------------------------------------

def test_parse_feed_rows_pads_short_rows():
    short = ["2026-08-01", "Doc", "evidence"]  # only 3 of 9 columns
    rows = ff.parse_feed_rows([short])
    assert rows == [{
        "date_filed": "2026-08-01", "document_name": "Doc", "type": "evidence",
        "risks": "", "severity": "", "summary": "", "key_data_point": "",
        "link": "", "facility": "",
    }]


def test_parse_feed_rows_skips_blank_rows():
    assert ff.parse_feed_rows([[], _row()]) == ff.parse_feed_rows([_row()])


# --- merge_and_sort -------------------------------------------------------

def test_merge_and_sort_orders_newest_first():
    new = [_row(date="2026-08-10", name="Newest")]
    historical = [_row(date="1996-06-14", name="Oldest"), _row(date="2026-01-01", name="Middle")]
    rows = ff.merge_and_sort(new, historical)
    assert [r["document_name"] for r in rows] == ["Newest", "Middle", "Oldest"]


def test_merge_and_sort_stable_for_same_date_new_before_historical():
    new = [_row(date="2026-08-10", name="FromNew")]
    historical = [_row(date="2026-08-10", name="FromHistorical")]
    rows = ff.merge_and_sort(new, historical)
    assert [r["document_name"] for r in rows] == ["FromNew", "FromHistorical"]


# --- paginate ---------------------------------------------------------------

def test_paginate_exact_multiple():
    rows = [{"i": i} for i in range(10)]
    pages = ff.paginate(rows, page_size=5)
    assert len(pages) == 2
    assert [p["i"] for p in pages[0]] == list(range(5))
    assert [p["i"] for p in pages[1]] == list(range(5, 10))


def test_paginate_remainder():
    rows = [{"i": i} for i in range(11)]
    pages = ff.paginate(rows, page_size=5)
    assert len(pages) == 3
    assert len(pages[-1]) == 1


def test_paginate_empty_returns_one_empty_page():
    assert ff.paginate([], page_size=5) == [[]]


# --- facility_display ---------------------------------------------------

def test_facility_display_aliases_wrd():
    assert ff.facility_display("GFL-Arbor Hills Landfill-Washtenaw Co") == \
        "Arbor Hills Landfill (Land & Water Interface)"


def test_facility_display_passes_through_unmapped_names():
    assert ff.facility_display("Arbor Hills Energy") == "Arbor Hills Energy"
    assert ff.facility_display("") == ""


# --- render_entry ---------------------------------------------------------

def test_render_entry_omits_blank_optional_fields():
    row = ff.parse_feed_rows([_row(risks="", summary="", kdp="")])[0]
    out = ff.render_entry(row)
    assert "None" not in out
    assert "finding-kdp" not in out
    assert "finding-risks" not in out
    assert "<p></p>" not in out


def test_render_entry_includes_populated_fields():
    row = ff.parse_feed_rows([_row()])[0]
    out = ff.render_entry(row)
    assert "A summary." in out
    assert "Key data point:" in out and "180F at AHW272." in out
    assert "Risks: R5" in out


def test_render_entry_escapes_html_in_untrusted_fields():
    row = ff.parse_feed_rows([_row(name='<script>alert(1)</script>', summary="x & y")])[0]
    out = ff.render_entry(row)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "x &amp; y" in out


def test_render_entry_link_passthrough_no_drive_link_introduced():
    nsite_link = "https://mienviro.michigan.gov/ncore/downloadpdf/-3977391404391944171"
    row = ff.parse_feed_rows([_row(link=nsite_link)])[0]
    out = ff.render_entry(row)
    assert f'href="{nsite_link}"' in out
    assert "drive.google.com" not in out


def test_render_entry_handles_untitled_document():
    row = ff.parse_feed_rows([_row(name="")])[0]
    out = ff.render_entry(row)
    assert "(untitled document)" in out


# --- build_pages ------------------------------------------------------------

def test_build_pages_filenames_and_page_count():
    rows = ff.parse_feed_rows([_row(date=f"2026-01-{i:02d}") for i in range(1, 12)])
    pages = ff.build_pages(rows, generated_at="2026-08-21 12:00 UTC", page_size=5)
    assert set(pages.keys()) == {"index.html", "page-2.html", "page-3.html"}


def test_build_pages_nav_links_correct_on_each_page():
    rows = ff.parse_feed_rows([_row(date=f"2026-01-{i:02d}") for i in range(1, 12)])
    pages = ff.build_pages(rows, generated_at="2026-08-21 12:00 UTC", page_size=5)

    # First page: only "Older", no "Newer".
    assert "Newer" not in pages["index.html"]
    assert 'href="page-2.html"' in pages["index.html"]

    # Middle page: both directions, pointing at the right neighbors.
    assert 'href="index.html"' in pages["page-2.html"]
    assert 'href="page-3.html"' in pages["page-2.html"]

    # Last page: only "Newer", no "Older".
    assert "Older" not in pages["page-3.html"]
    assert 'href="page-2.html"' in pages["page-3.html"]


def test_build_pages_total_count_and_no_drive_links():
    rows = ff.parse_feed_rows([_row(date="2026-08-01", link="https://mienviro.michigan.gov/x")])
    pages = ff.build_pages(rows, generated_at="2026-08-21 12:00 UTC")
    assert "1 documents" in pages["index.html"]
    assert "drive.google.com" not in pages["index.html"]


def test_build_pages_empty_feed_renders_single_page():
    pages = ff.build_pages([], generated_at="2026-08-21 12:00 UTC")
    assert set(pages.keys()) == {"index.html"}
    assert "No documents found." in pages["index.html"]
