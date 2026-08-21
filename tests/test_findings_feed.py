"""Findings feed render/format logic — pagination boundaries, blank-field
handling, sort order, link passthrough, archive-link resolution, title
date-stripping."""
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


# --- is_suspicious_shrink (the auto-commit trip-wire) ---------------------

def test_is_suspicious_shrink_flags_a_big_drop():
    assert ff.is_suspicious_shrink(previous_total=1720, new_total=5) is True


def test_is_suspicious_shrink_allows_normal_growth():
    assert ff.is_suspicious_shrink(previous_total=1720, new_total=1724) is False


def test_is_suspicious_shrink_allows_a_small_dip():
    # A handful of rows purged/re-dumped is real, expected variance, not a
    # bad-read signal — only a drop past SHRINK_GUARD_RATIO trips it.
    assert ff.is_suspicious_shrink(previous_total=1720, new_total=1715) is False


def test_is_suspicious_shrink_never_fires_on_first_run():
    assert ff.is_suspicious_shrink(previous_total=None, new_total=0) is False


def test_is_suspicious_shrink_never_fires_from_a_zero_baseline():
    assert ff.is_suspicious_shrink(previous_total=0, new_total=0) is False


# --- strip_embedded_date ---------------------------------------------------

def test_strip_embedded_date_removes_a_trailing_bare_date():
    # The real case that prompted this: title said 06/01/2025, the doc's
    # actual Date Filed was 2026-06-18 -- an EGLE-side mismatch, not the
    # monitor's error, and confusing next to the correct date shown above it.
    assert ff.strip_embedded_date("On-Site Inspection (06/01/2025)") == "On-Site Inspection"
    assert ff.strip_embedded_date("Air Other Evaluation (1/8/26)") == "Air Other Evaluation"


def test_strip_embedded_date_leaves_titles_without_a_trailing_date_alone():
    assert ff.strip_embedded_date("Submission PDF") == "Submission PDF"
    assert ff.strip_embedded_date("") == ""


def test_strip_embedded_date_leaves_non_date_parenthetical_content_alone():
    # A parenthetical that ISN'T a bare date is part of the title's own
    # meaning, not a redundant restatement of Date Filed -- real examples
    # from the Sheet.
    assert ff.strip_embedded_date("Conventional WQBEL (Updated 5/12/2022)") == \
        "Conventional WQBEL (Updated 5/12/2022)"
    assert ff.strip_embedded_date(
        "Additional information request omitted from previous email (due by 3/15/2021)"
    ) == "Additional information request omitted from previous email (due by 3/15/2021)"


def test_render_entry_uses_the_stripped_title():
    row = ff.parse_feed_rows([_row(name="On-Site Inspection (06/01/2025)")])[0]
    out = ff.render_entry(row)
    assert "<h3><a href=" in out and ">On-Site Inspection</a></h3>" in out
    assert "06/01/2025" not in out


# --- archive-link resolution -------------------------------------------

_NSITE_LINK = "https://mienviro.michigan.gov/ncore/downloadpdf/-3977391404391944171"
_DRIVE_LINK = "https://drive.google.com/file/d/1gCEo3sUapK72cI6PnCuNXdZVZpRBcT38/view"


def test_doc_id_from_nsite_link_extracts_the_trailing_id():
    assert ff.doc_id_from_nsite_link(_NSITE_LINK) == "-3977391404391944171"
    assert ff.doc_id_from_nsite_link("https://mienviro.michigan.gov/ncore/downloadpdf/8094300008956198244") == \
        "8094300008956198244"


def test_doc_id_from_nsite_link_returns_none_for_a_non_nsite_link():
    assert ff.doc_id_from_nsite_link(_DRIVE_LINK) is None
    assert ff.doc_id_from_nsite_link("") is None
    assert ff.doc_id_from_nsite_link(None) is None


def test_resolve_display_link_prefers_the_archive_mirror_when_present():
    archive_links = {"-3977391404391944171": _DRIVE_LINK}
    assert ff.resolve_display_link(_NSITE_LINK, archive_links) == _DRIVE_LINK


def test_resolve_display_link_falls_back_to_nsite_link_when_no_mirror_yet():
    assert ff.resolve_display_link(_NSITE_LINK, {}) == _NSITE_LINK


def test_resolve_display_link_falls_back_for_a_non_nsite_link():
    # Nothing to look up a mirror by -- pass the link through unchanged rather
    # than crash or drop it.
    assert ff.resolve_display_link(_DRIVE_LINK, {"-3977391404391944171": "https://drive.google.com/other"}) == _DRIVE_LINK


def test_resolve_display_links_applies_per_row_and_preserves_everything_else():
    rows = ff.parse_feed_rows([_row(name="Doc A", link=_NSITE_LINK), _row(name="Doc B", link="")])
    resolved = ff.resolve_display_links(rows, {"-3977391404391944171": _DRIVE_LINK})
    assert resolved[0]["link"] == _DRIVE_LINK
    assert resolved[0]["document_name"] == "Doc A"
    assert resolved[1]["link"] == ""  # no doc_id extractable from a blank link -- passthrough


# --- facility_display ---------------------------------------------------

def test_facility_display_aliases_wrd():
    assert ff.facility_display("GFL-Arbor Hills Landfill-Washtenaw Co") == \
        "Arbor Hills Landfill (Land & Water Interface)"


def test_facility_display_passes_through_unmapped_names():
    assert ff.facility_display("Arbor Hills Energy") == "Arbor Hills Energy"
    assert ff.facility_display("") == ""


# --- render_entry ---------------------------------------------------------

def test_render_entry_omits_blank_optional_fields():
    row = ff.parse_feed_rows([_row(summary="", kdp="")])[0]
    out = ff.render_entry(row)
    assert "None" not in out
    assert "finding-kdp" not in out
    assert "<p></p>" not in out


def test_render_entry_includes_populated_fields():
    row = ff.parse_feed_rows([_row()])[0]
    out = ff.render_entry(row)
    assert "A summary." in out
    assert "Key data point:" in out and "180F at AHW272." in out


def test_render_entry_never_renders_risks():
    # Risks (R1-R8) are this project's own internal taxonomy -- meaningless to
    # a public reader with no legend, so never rendered regardless of value.
    row = ff.parse_feed_rows([_row(risks="R1, R2, R5")])[0]
    out = ff.render_entry(row)
    assert "R1" not in out
    assert "Risks" not in out
    assert "finding-risks" not in out


def test_render_entry_escapes_html_in_untrusted_fields():
    row = ff.parse_feed_rows([_row(name='<script>alert(1)</script>', summary="x & y")])[0]
    out = ff.render_entry(row)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "x &amp; y" in out


def test_render_entry_link_passthrough_unmodified():
    # render_entry doesn't resolve archive links itself (resolve_display_links
    # does that upstream, before a row ever reaches here) -- it renders
    # whatever URL is on the row, verbatim, whether that's the original nSITE
    # link or an already-resolved Drive mirror link.
    nsite_link = "https://mienviro.michigan.gov/ncore/downloadpdf/-3977391404391944171"
    row = ff.parse_feed_rows([_row(link=nsite_link)])[0]
    assert f'href="{nsite_link}"' in ff.render_entry(row)

    drive_link = "https://drive.google.com/file/d/1gCEo3sUapK72cI6PnCuNXdZVZpRBcT38/view?usp=drivesdk"
    row2 = ff.parse_feed_rows([_row(link=drive_link)])[0]
    assert f'href="{drive_link}"' in ff.render_entry(row2)


def test_render_entry_rejects_non_http_link_scheme():
    row = ff.parse_feed_rows([_row(name="Doc", link="javascript:alert(1)")])[0]
    out = ff.render_entry(row)
    assert "javascript:" not in out
    assert "<h3>Doc</h3>" in out  # falls back to a plain (non-linked) title


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


def test_build_pages_total_count():
    rows = ff.parse_feed_rows([_row(date="2026-08-01", link="https://mienviro.michigan.gov/x")])
    pages = ff.build_pages(rows, generated_at="2026-08-21 12:00 UTC")
    assert "1 documents" in pages["index.html"]


def test_build_pages_empty_feed_renders_single_page():
    pages = ff.build_pages([], generated_at="2026-08-21 12:00 UTC")
    assert set(pages.keys()) == {"index.html"}
    assert "No documents found." in pages["index.html"]
