"""A row whose timestamp and ADJ print on ONE line ("10/21/2022 11:46 ADJ";
ADR 005 addendum 2026-10-21) starts an ADJ row exactly as the two-line form
(timestamp, then "ADJ") does. Hermetic: synthetic lines and in-process PDFs."""
import pytest

import woi_table_parser as w
from tests.test_woi_parser import HDR_FULL, HDR_FULL_NO_ADJ, HDR_ALIAS, L
from tests.test_woi_split_join import build, row

CELLS = ["49.5", "39.1", "2.1", "9.3", "1.54", "77.9", "36.4", "-1.85", "-37.71"]
AS_FOUND = ["AH147AR4", "10/21/2022 11:44", "49.4", "39.2", "2.1", "9.3", "1.5", "77.5",
            "36", "-1.9", "-37.7", "Closed Valve 1/2 turn or less:", "768279:"]


def _key(r):
    return (r.well_id, r.raw_well_id, r.dt, r.adj, r.page, r.ch4, r.co2, r.o2, r.balance,
            r.diff_press, r.temp, r.flow, r.well_pres, r.header_pres, r.alias_as_printed)


def two_line(dt="10/21/2022 11:46"):
    return ["AH147AR4", dt, "ADJ", *CELLS]


def one_line(dt_line="10/21/2022 11:46 ADJ"):
    return ["AH147AR4", dt_line, *CELLS]


def test_one_line_timestamp_adj_reads_like_the_two_line_form():
    one = w._parse_table_lines(L(*HDR_FULL, *AS_FOUND, *one_line())).readings
    two = w._parse_table_lines(L(*HDR_FULL, *AS_FOUND, *two_line())).readings
    assert [_key(r) for r in one] == [_key(r) for r in two]
    r = one[1]
    assert (r.dt, r.adj, r.temp, r.header_pres) == ("10/21/2022 11:46", True, 77.9, -37.71)
    assert r.valid


@pytest.mark.parametrize("dt_line, dt", [
    ("10/21/2022 11:46  ADJ", "10/21/2022 11:46"),
    ("10/21/2022 11:46\tADJ", "10/21/2022 11:46"),
    ("10/21/2022  11:46   ADJ", "10/21/2022  11:46"),
])
def test_extra_spaces_before_adj(dt_line, dt):
    # The timestamp keeps its own spacing, as the two-line form does.
    rs = w._parse_table_lines(L(*HDR_FULL, *one_line(dt_line))).readings
    assert [(r.dt, r.adj, r.temp) for r in rs] == [(dt, True, 77.9)]
    two = w._parse_table_lines(L(*HDR_FULL, *two_line(dt))).readings
    assert [_key(r) for r in rs] == [_key(r) for r in two]


@pytest.mark.parametrize("dt_line", [
    "10/21/2022 11:46ADJ", "10/21/2022 11:46 ADJX", "10/21/2022 11:46 adj",
    "10/21/2022 11:46 AM", "10/21/2022 11:46 ADJ 49.5", "10/21/2022 ADJ", "x 10/21/2022 11:46 ADJ",
])
def test_other_text_after_a_timestamp_starts_no_row(dt_line):
    g = w._parse_table_lines(L(*HDR_FULL, *one_line(dt_line)))
    assert g.readings == []


def test_two_line_form_unchanged():
    rs = w._parse_table_lines(L(*HDR_FULL, *AS_FOUND, *two_line())).readings
    assert [(r.dt, r.adj) for r in rs] == [("10/21/2022 11:44", False), ("10/21/2022 11:46", True)]
    assert w._dt_line("10/21/2022 11:46") == ("10/21/2022 11:46", False)
    assert w._dt_line("10/21/2022 11:46 ADJ") == ("10/21/2022 11:46", True)
    assert w._dt_line("10/21/2022") is None


def test_header_less_fragment_reads_the_one_line_form():
    rs = w._parse_lines(L(*one_line()))
    assert [(r.dt, r.adj, r.temp) for r in rs] == [("10/21/2022 11:46", True, 77.9)]


def test_first_row_after_the_header_is_not_swallowed_by_it():
    g = w._parse_table_lines(L(*HDR_FULL, *one_line(), *AS_FOUND))
    assert [(r.dt, r.adj) for r in g.readings] == [("10/21/2022 11:46", True),
                                                   ("10/21/2022 11:44", False)]


def test_alias_table_one_line_form():
    hdr = HDR_ALIAS
    rows = ["AH147AR4", "EW-147", "10/21/2022 11:46 ADJ", *CELLS]
    rs = w._parse_table_lines(L(*hdr, *rows)).readings
    assert [(r.well_id, r.alias_as_printed, r.dt, r.adj) for r in rs] == [
        ("AH147AR4", "EW-147", "10/21/2022 11:46", True)]


def test_one_line_row_never_moves_a_main_rows_unlabeled_adj_flag():
    # No ADJ label: main reads a, b (b is a's ADJ twin). A one-line ADJ row
    # printed between them is read as ADJ but is not the partner, so b stays
    # ADJ exactly as on main.
    a = ["AHW1", "1/1/2025 10:00", "50", "40", "0", "10", "0.5", "120", "5", "-1", "-2"]
    mid = ["AHW1", "1/1/2025 10:00 ADJ", "50", "40", "0", "10", "0.5", "121", "5", "-1", "-2"]
    rs = w._parse_table_lines(L(*HDR_FULL_NO_ADJ, *a, *mid, *a)).readings
    assert [(r.adj, r.temp) for r in rs] == [(False, 120.0), (True, 121.0), (True, 120.0)]
    main = w._parse_table_lines(L(*HDR_FULL_NO_ADJ, *a, *a)).readings
    assert [r.adj for r in main] == [False, True]


def test_one_line_rows_cannot_reject_main_rows_by_negative_flow():
    # Main's rows have positive flow; added one-line rows with negative flow
    # are checked on their own and never take main's rows with them.
    good = [["AHW%d" % k, "1/1/2025 10:%02d" % k, "50", "40", "0", "10", "0.5", "120", "5",
             "-1", "-2"] for k in range(25)]
    bad = [["AHX%d" % k, "1/1/2025 11:%02d ADJ" % k, "50", "40", "0", "10", "0.5", "120", "-5",
            "-1", "-2"] for k in range(25)]
    lines = [x for r in good for x in r] + [x for r in bad for x in r]
    g = w._parse_table_lines(L(*HDR_FULL, *lines))
    assert [r.well_id for r in g.readings] == ["AHW%d" % k for k in range(25)]
    assert [(t.n_rows, "one-line timestamp ADJ" in t.reason) for t in g.rejected_tables] == [
        (25, True)]


def test_split_table_one_line_adj_row_joins(tmp_path):
    rows = [row("AHW1", 0),
            dict(row("AHW1", 1, right=("0.4", "121", "8", "-4", "-41")), dt="1/2/2025 10:01 ADJ"),
            row("AHW1", 5)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    assert [(r.dt, r.adj, r.temp) for r in g.readings] == [
        ("1/2/2025 10:00", False, 120.0), ("1/2/2025 10:01", True, 121.0),
        ("1/2/2025 10:05", False, 120.0)]
    assert (g.split_joined_rows, g.split_unjoined_rows, g.split_unread_rows) == (3, 0, 0)


# --- review round 1: rows main reads are never moved by a one-line row ---

def test_split_page_still_joins_when_a_one_line_row_has_no_right_row(tmp_path):
    rows = [row("AHW1", 0), row("AHW1", 2),
            dict(row("AHW1", 1, right=None), dt="1/2/2025 10:01 ADJ"),
            row("AHW1", 5), row("AHW1", 6)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    assert [(r.dt[-5:], r.adj) for r in g.readings] == [
        ("10:00", False), ("10:02", False), ("10:05", False), ("10:06", False)]
    assert (g.split_joined_rows, g.split_unjoined_rows, g.split_unread_rows) == (4, 1, 0)
    assert [(t.n_rows, t.reason) for t in g.rejected_tables] == [(1, w.SPLIT_UNJOINED_REASON)]


def test_header_text_and_labels_stay_mains_when_the_first_row_is_one_line():
    # Main's header ran on through a first row printed "timestamp ADJ" and
    # took its "ADJ" as a label; the labels stay main's, so main's rows keep
    # their ADJ flags. The one-line row itself is read.
    first = ["AHW9", "1/1/2025 09:00 ADJ", "50", "40", "0", "10", "0.5", "121", "5", "-1", "-2"]
    a = ["AHW1", "1/1/2025 10:00", "50", "40", "0", "10", "0.5", "120", "5", "-1", "-2"]
    rs = w._parse_table_lines(L(*HDR_FULL_NO_ADJ, *first, *a, *a)).readings
    assert [(r.well_id, r.adj) for r in rs] == [("AHW9", True), ("AHW1", False), ("AHW1", False)]


def test_one_line_pool_never_takes_rows_read_past_a_blank_cell_with_it():
    good = [["AHW%d" % k, "1/1/2025 10:%02d" % k, "50", "40", "0", "10", "0.5", "120", "5",
             "-1", "-2"] for k in range(25)]
    dash = [["AHD%d" % k, "1/1/2025 12:%02d" % k, "50", "40", "0", "10", "---", "120", "5",
             "-1", "-2"] for k in range(20)]
    bad = [["AHX%d" % k, "1/1/2025 11:%02d ADJ" % k, "50", "40", "0", "10", "0.5", "120", "-5",
            "-1", "-2"] for k in range(20)]
    lines = [x for r in good + dash + bad for x in r]
    g = w._parse_table_lines(L(*HDR_FULL, *lines))
    assert [r.well_id for r in g.readings] == (["AHW%d" % k for k in range(25)]
                                               + ["AHD%d" % k for k in range(20)])
    assert [(t.n_rows, "one-line timestamp ADJ" in t.reason) for t in g.rejected_tables] == [
        (20, True)]


def test_a_literal_adj_line_after_a_one_line_adj_is_the_same_token():
    rs = w._parse_table_lines(L(*HDR_FULL, "AH147AR4", "10/21/2022 11:46 ADJ", "ADJ",
                                *CELLS)).readings
    assert [(r.dt, r.adj, r.temp) for r in rs] == [("10/21/2022 11:46", True, 77.9)]
