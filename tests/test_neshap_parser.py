"""NESHAP table parser: Appendix A (exceedance rosters) + Appendix F
(enhanced-monitoring / visual-inspection logs) line state-machines, report-
metadata regexes, and the divider-page finder. Hermetic — operates on
synthetic linearized page text, no PDF.

The synthetic fixtures below deliberately encode structural quirks found by
testing the two real 2025 semi-annual reports before this module was written
(per docs/overnight-coder.md's spike step + ADR 011's own precedent that real
specimens catch bugs synthetic fixtures miss): the report's own table of
contents lists "APPENDIX A" / "APPENDIX B" back-to-back, which would
false-match a naive per-line divider search; Appendix F's date format is NOT
consistent between the two real reports (H1: bare M/D/YYYY, H2: MM/DD/YY
HH:MM); and AHW263R5's temperature readings genuinely include values below
its own printed exceedance episodes (illustrating why `exceeded` must be
computed per row, not assumed True for every row in an "exceedance" table).
"""
import neshap_table_parser as n
from neshap_table_parser import (
    EnhancedMonitoringReading,
    ExceedanceReading,
)


def L(*items):
    """Build a [(text, page)] list from bare strings (all on page 1)."""
    return [(s, 1) for s in items]


# --- Appendix A: pressure + temperature exceedance rosters -----------------

def test_pressure_reading_parsed_within_pressure_mode():
    lines = L("(Pressure)", "AHEW0012", "08/14/25 11:28", "1.63")
    rs = n._parse_appendix_a_lines(lines)
    assert len(rs) == 1
    r = rs[0]
    assert r.well_id == "AHEW0012" and r.parameter == "pressure"
    assert r.value == 1.63 and r.limit == 0.0
    assert r.exceeded is True   # positive pressure -> exceeds
    assert r.duration is None   # no duration token followed


def test_temperature_reading_parsed_within_temperature_mode():
    lines = L("(Temperature)", "AHW263R5", "07/01/25 10:11", "160")
    rs = n._parse_appendix_a_lines(lines)
    assert len(rs) == 1
    r = rs[0]
    assert r.parameter == "temperature" and r.limit == 145.0
    assert r.value == 160.0 and r.exceeded is True


def test_exceeded_is_computed_per_row_not_assumed_true_for_the_table():
    # A real exceedance episode: the start reading exceeds (positive), the
    # closing/return reading (with Duration filled in) does not. Both rows
    # belong to the same "exceedance roster" table, but only one exceeded.
    lines = L("(Pressure)",
              "AHEW0012", "08/14/25 11:28", "1.63",
              "AHEW0012", "08/14/25 11:29", "-39.31", "<1")
    rs = n._parse_appendix_a_lines(lines)
    assert len(rs) == 2
    assert rs[0].exceeded is True and rs[0].duration is None
    assert rs[1].exceeded is False and rs[1].duration == "<1"


def test_duration_token_variants_including_footnote_asterisk():
    lines = L("(Temperature)",
              "AHW263R5", "07/23/25 10:13", "157.7", "*64",
              "AHWW328R", "09/19/25 10:38", "140.6", "7")
    rs = n._parse_appendix_a_lines(lines)
    assert [r.duration for r in rs] == ["*64", "7"]


def test_reading_before_any_mode_marker_is_skipped():
    # A well/date/value triple with no preceding (Pressure)/(Temperature)
    # marker can't be assigned a parameter -- skip rather than guess.
    lines = L("AHEW0012", "08/14/25 11:28", "1.63")
    assert n._parse_appendix_a_lines(lines) == []


def test_mode_switches_from_pressure_to_temperature_mid_stream():
    lines = L("(Pressure)",
              "AHEW0012", "08/14/25 11:28", "1.63",
              "(Temperature)",
              "AHW263R5", "07/23/25 10:13", "157.7")
    rs = n._parse_appendix_a_lines(lines)
    assert [r.parameter for r in rs] == ["pressure", "temperature"]


def test_non_well_and_date_only_lines_are_skipped():
    # Footnote text and stray lines must not be consumed as data.
    lines = L("(Temperature)",
              "*Higher operating value (HOV) requested for well 263R5",
              "AHW263R5", "07/23/25 10:13", "157.7")
    rs = n._parse_appendix_a_lines(lines)
    assert len(rs) == 1 and rs[0].well_id == "AHW263R5"


# --- Appendix F: enhanced monitoring + visual inspection --------------------

def test_enhanced_reading_h2_style_datetime_with_co():
    # 2025 H2's real format: MM/DD/YY HH:MM, with an optional trailing CO ppm.
    lines = L("Methane",
              "AHW263R5", "07/01/25 10:11", "50.3", "0", "160", "120")
    enh, vis = n._parse_appendix_f_lines(lines)
    assert len(enh) == 1 and vis == []
    r = enh[0]
    assert r.well_id == "AHW263R5" and r.reading_date == "07/01/25 10:11"
    assert r.methane_pct == 50.3 and r.oxygen_pct == 0.0 and r.gas_temp_f == 160.0
    assert r.co_ppm == 120.0


def test_enhanced_reading_h1_style_date_only_no_co():
    # 2025 H1's real format: bare M/D/YYYY, no time at all, and CO often absent.
    lines = L("Methane",
              "AHW263R5", "5/20/2025", "47.2", "0", "161.6")
    enh, vis = n._parse_appendix_f_lines(lines)
    assert len(enh) == 1
    r = enh[0]
    assert r.reading_date == "5/20/2025"
    assert r.methane_pct == 47.2 and r.co_ppm is None


def test_enhanced_row_with_fewer_than_three_numbers_is_dropped():
    lines = L("Methane", "AHW263R5", "07/01/25 10:11", "50.3")
    enh, vis = n._parse_appendix_f_lines(lines)
    assert enh == []


def test_visual_inspection_parses_multiword_staff_name_and_yn():
    lines = L("Staff Person",
              "AHW263R5", "07/01/25 10:11", "Philip Trela", "N", "N", "N")
    enh, vis = n._parse_appendix_f_lines(lines)
    assert enh == [] and len(vis) == 1
    v = vis[0]
    assert v.staff_name == "Philip Trela"
    assert v.smoke_observed is False and v.ash_observed is False and v.damage_observed is False


def test_visual_inspection_yn_maps_y_to_true_not_hardcoded_false():
    # Both real reports happen to show all-"N" results; prove the mapping
    # itself isn't just always returning False regardless of the source.
    lines = L("Staff Person",
              "AHW263R5", "07/01/25 10:11", "A Name", "Y", "N", "Y")
    enh, vis = n._parse_appendix_f_lines(lines)
    v = vis[0]
    assert v.smoke_observed is True and v.ash_observed is False and v.damage_observed is True


def test_visual_row_with_fewer_than_three_yn_tokens_is_dropped():
    lines = L("Staff Person", "AHW263R5", "07/01/25 10:11", "A Name", "N")
    enh, vis = n._parse_appendix_f_lines(lines)
    assert vis == []


def test_mode_switches_from_enhanced_to_visual_mid_stream():
    lines = L("Methane",
              "AHW263R5", "07/01/25 10:11", "50.3", "0", "160",
              "Staff Person",
              "AHW263R5", "07/01/25 10:11", "Philip Trela", "N", "N", "N")
    enh, vis = n._parse_appendix_f_lines(lines)
    assert len(enh) == 1 and len(vis) == 1


# --- Divider-page finder: the real reports' own TOC is a trap ---------------

TOC_PAGE = (
    "CONTENTS\n"
    "1 INTRODUCTION ... 1\n"
    "APPENDIX A\n"
    "Wellfield Exceedance Tables\n"
    "APPENDIX B\n"
    "Control Device / Treatment System Downtime\n"
    "APPENDIX C\n"
)
REAL_DIVIDER_A = "  \n \nAPPENDIX A \nWELLFIELD EXCEEDANCE REPORTS \n \n"
REAL_DIVIDER_B = " \nAPPENDIX B  \nCONTROL DEVICE / TREATMENT SYSTEM DOWNTIME \n"


def test_find_divider_page_skips_toc_and_finds_the_real_divider():
    # This exact false-positive (a naive whole-document line scan grabbing
    # the TOC's "APPENDIX A" / immediately-following "APPENDIX B" as the
    # section bounds) was found and fixed against the real 2025 H2 report.
    pages = [TOC_PAGE, "some narrative page", REAL_DIVIDER_A, "table page", REAL_DIVIDER_B]
    a = n._find_divider_page(pages, "A")
    assert a == 2
    b = n._find_divider_page(pages, "B", start_from=a + 1)
    assert b == 4


def test_find_divider_page_returns_none_when_appendix_absent():
    pages = [TOC_PAGE, "narrative"]
    assert n._find_divider_page(pages, "F") is None


def test_lines_for_pages_respects_page_range_bounds():
    pages = ["AHEW0012\n08/14/25 11:28\n1.63", "AHW263R5\n07/01/25 10:11\n160"]
    lines = n._lines_for_pages(pages, 0, 1)
    assert lines == [("AHEW0012", 1), ("08/14/25 11:28", 1), ("1.63", 1)]


# --- Report-level metadata: narrative regex + derived well roster ----------

H1_RCA_SENTENCE = "During the reporting period all exceedances were corrected within 0 to 15 days.  Therefore, \nno root cause analysis forms were required."
H2_RCA_SENTENCE = "During the reporting period all exceedances were corrected within 0 to 60 days.  Therefore, \nno root cause analysis forms are required to be submitted."
H1_DOWNWELL_SENTENCE = "No down well monitoring was conducted during this reporting period."
H2_DOWNWELL_SENTENCE = "No down well monitoring was required during this reporting period."


def test_rca_required_false_matches_both_real_reports_phrasing():
    assert n._rca_required(H1_RCA_SENTENCE) is False
    assert n._rca_required(H2_RCA_SENTENCE) is False


def test_rca_required_none_when_phrasing_not_found():
    # No real report has ever required an RCA -- don't guess True either way
    # when the known "not required" phrasing simply isn't present.
    assert n._rca_required("Some unrelated narrative text.") is None


def test_downwell_conducted_false_matches_both_real_reports_phrasing():
    assert n._downwell_monitoring_conducted(H1_DOWNWELL_SENTENCE) is False
    assert n._downwell_monitoring_conducted(H2_DOWNWELL_SENTENCE) is False


def test_downwell_conducted_none_when_phrasing_not_found():
    assert n._downwell_monitoring_conducted("Some unrelated narrative text.") is None


def test_reporting_period_and_transmittal_date_extracted_from_pages():
    page1 = "March 13, 2026 \n \nMs. Diane Kavanaugh-Vetort"
    page3 = "NESHAP SEMI-ANNUAL REPORT \nFor the Reporting Period \nJuly 1, 2025 through December 31, 2025 \n"
    meta = n.parse_report_metadata([page1, page3], enhanced=[])
    assert meta.transmittal_date == "March 13, 2026"
    assert meta.reporting_period == "July 1, 2025 through December 31, 2025"


def test_wells_enhanced_monitored_is_derived_from_appendix_f_not_narrative_count():
    # The narrative here deliberately says "One (1) well" -- a stale/wrong
    # count relative to the two actual wells in the parsed Appendix F table --
    # to prove wells_enhanced_monitored is read from the roster, not the prose.
    page = "One (1) well was subject to weekly enhanced monitoring during the reporting period."
    enhanced = [
        EnhancedMonitoringReading("AHW263R5", "07/01/25 10:11", 50.3, 0.0, 160.0, None, page=79),
        EnhancedMonitoringReading("AHWW328R", "09/12/25 14:59", 24.0, 3.0, 150.4, None, page=79),
    ]
    meta = n.parse_report_metadata([page], enhanced=enhanced)
    assert meta.wells_enhanced_monitored == ["AHW263R5", "AHWW328R"]


def test_report_metadata_fields_are_none_not_false_when_undetermined():
    meta = n.parse_report_metadata(["no relevant narrative here"], enhanced=[])
    assert meta.rca_required is None
    assert meta.downwell_monitoring_conducted is None
    assert meta.transmittal_date is None
    assert meta.reporting_period is None
    assert meta.wells_enhanced_monitored == []


# --- End-to-end composition (still hermetic: fake "PDF" as page-text list) --

def test_end_to_end_composition_across_divider_finding_and_both_parsers():
    """Compose _find_divider_page + _lines_for_pages + both line
    state-machines the way parse_report() does, over a miniature synthetic
    report shaped like the real ones (TOC trap included), without opening a
    PDF. This is the closest hermetic proxy to the real-specimen shape."""
    pages = [
        TOC_PAGE,                                     # 0: TOC (the trap)
        "narrative pages ...",                        # 1
        REAL_DIVIDER_A,                                # 2: Appendix A divider
        "(Pressure)\nAHEW0012\n08/14/25 11:28\n1.63",  # 3: pressure table
        "(Temperature)\nAHW263R5\n07/23/25 10:13\n157.7",  # 4: temp table
        REAL_DIVIDER_B,                                # 5: Appendix B divider (ends A)
        "control device downtime table ...",           # 6
    ]
    a = n._find_divider_page(pages, "A")
    b = n._find_divider_page(pages, "B", start_from=a + 1)
    rows = n._parse_appendix_a_lines(n._lines_for_pages(pages, a, b))
    assert [(r.well_id, r.parameter, r.exceeded) for r in rows] == [
        ("AHEW0012", "pressure", True),
        ("AHW263R5", "temperature", True),
    ]
