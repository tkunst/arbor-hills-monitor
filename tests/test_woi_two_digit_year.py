"""A row timestamp printed with a two-digit year ("10/1/25 7:56", and the
one-line "10/1/25 15:55 ADJ"; ADR 005 addendum 2026-10-21, two-digit years)
starts a row exactly as the four-digit form does, and the row stores the
four-digit form ("10/1/2025 7:56"). Hermetic: synthetic lines and in-process
PDFs."""
import datetime

import pytest

import woi_table_parser as w
from tests.test_woi_parser import HDR_ALIAS, HDR_FULL, HDR_FULL_NO_ADJ, L
from tests.test_woi_split_join import build, row

CELLS = ["47.8", "36", "1.8", "14.4", "0", "59.1", "1096", "0.56", "0.01"]


def _key(r):
    return (r.well_id, r.raw_well_id, r.dt, r.adj, r.page, r.ch4, r.co2, r.o2, r.balance,
            r.diff_press, r.temp, r.flow, r.well_pres, r.header_pres, r.alias_as_printed)


def _read(*rows, hdr=HDR_FULL):
    return w._parse_table_lines(L(*hdr, *[x for r in rows for x in r])).readings


def test_two_digit_year_reads_like_the_four_digit_form():
    two = _read(["AHW1", "10/1/25 7:56", *CELLS], ["AHW1", "10/1/25 15:55", "ADJ", *CELLS])
    four = _read(["AHW1", "10/1/2025 7:56", *CELLS], ["AHW1", "10/1/2025 15:55", "ADJ", *CELLS])
    assert [_key(r) for r in two] == [_key(r) for r in four]
    assert [(r.dt, r.adj) for r in two] == [("10/1/2025 7:56", False), ("10/1/2025 15:55", True)]
    assert all(r.valid for r in two)


def test_one_line_adj_with_a_two_digit_year():
    one = _read(["AHW1", "10/1/25 15:55 ADJ", *CELLS])
    two = _read(["AHW1", "10/1/2025 15:55", "ADJ", *CELLS])
    assert [_key(r) for r in one] == [_key(r) for r in two]
    assert [(r.dt, r.adj, r.temp) for r in one] == [("10/1/2025 15:55", True, 59.1)]


@pytest.mark.parametrize("printed, stored", [
    ("05/01/26 09:53", "5/1/2026 9:53"),     # zero-padded
    ("10/1/25  7:56", "10/1/2025 7:56"),     # two spaces
    ("1/1/00 0:00", "1/1/2000 0:00"),
    ("12/31/25 23:59", "12/31/2025 23:59"),
])
def test_two_digit_year_is_stored_in_the_unpadded_four_digit_form(printed, stored):
    assert w._dt_line(printed) == (stored, False)
    assert w._dt_line(printed + " ADJ") == (stored, True)
    rs = _read(["AHW1", printed, *CELLS])
    assert [r.dt for r in rs] == [stored]
    assert w._to_iso(rs[0].dt) == w._to_iso(stored.split()[0] + " 0:00")


def test_four_digit_form_is_kept_exactly_as_printed():
    # main's rule: a four-digit timestamp is never rewritten (padding, spacing)
    for t in ["10/1/2025 7:56", "05/01/2026 09:53", "10/21/2022  11:46"]:
        assert w._dt_line(t) == (t, False)


@pytest.mark.parametrize("line", [
    "10/1/25",              # date only (the CO tables' form)
    "10/1/202 7:56",        # three-digit year
    "10/1/2 7:56",          # one-digit year
    "10/1/20255 7:56",      # five-digit year
    "10/1/25 7:56 AM",
    "10/1/25 7:56 PM",
    "10/1/25, 7:56 AM",
    "10/1/25 7:56ADJ",
    "10/1/25 7:56 adj",
    "10/1/25 7:56 ADJ 47.8",
    "10/1/25 7",
    "10/1/25 7:5",
    "2/30/25 7:56",         # not a date
    "13/1/25 7:56",
    "1/0/00 0:00",          # a spreadsheet's zero date, printed in one report
    "10/1/25 24:00",        # not a time
    "10/1/25 7:60",
    "1/1/99 7:56",          # 2099: after today
])
def test_near_misses_start_no_row(line):
    assert w._dt_line(line) is None
    assert _read(["AHW1", line, *CELLS]) == []


def test_a_two_digit_date_after_today_starts_no_row():
    today = datetime.date.today()
    if today.year >= 2099:  # pragma: no cover - the two-digit window ends
        pytest.skip("20YY no longer covers tomorrow")
    tomorrow = today + datetime.timedelta(days=1)
    t = f"{today.month}/{today.day}/{today.year % 100:02d} 7:56"
    n = f"{tomorrow.month}/{tomorrow.day}/{tomorrow.year % 100:02d} 7:56"
    assert w._dt_line(t) == (f"{today.month}/{today.day}/{today.year} 7:56", False)
    assert w._dt_line(n) is None


def test_alias_table_with_two_digit_years():
    # the shape of the report that prints them: Well ID, Alias, Date/Time
    rs = _read(["AHCANFLR", "AHCANFLR", "10/1/25 7:56", *CELLS],
               ["AH147AR4", "EW-147", "10/1/25 15:55 ADJ", *CELLS], hdr=HDR_ALIAS)
    assert [(r.well_id, r.alias_as_printed, r.dt, r.adj) for r in rs] == [
        ("AHCANFLR", "AHCANFLR", "10/1/2025 7:56", False),
        ("AH147AR4", "EW-147", "10/1/2025 15:55", True)]


def test_the_header_ends_at_a_two_digit_first_row():
    g = w._parse_table_lines(L(*HDR_FULL, "AHW1", "10/1/25 7:56", *CELLS))
    assert [r.dt for r in g.readings] == ["10/1/2025 7:56"]
    assert g.rejected_tables == []


def test_unlabeled_adj_partner_matches_across_the_two_digit_form():
    # No ADJ label: the second copy of a row is its ADJ twin, compared on the
    # stored (four-digit) timestamp.
    a = ["AHW1", "10/1/25 7:56", *CELLS]
    rs = _read(a, a, hdr=HDR_FULL_NO_ADJ)
    assert [(r.dt, r.adj) for r in rs] == [("10/1/2025 7:56", False), ("10/1/2025 7:56", True)]


def test_measurements_carry_the_four_digit_date():
    r = _read(["AHW1", "10/1/25 7:56", *CELLS])[0]
    ms = w.to_measurements(r)
    assert ms and {m["as_of_date"] for m in ms} == {"2025-10-01"}


def test_header_less_fragment_reads_the_two_digit_form():
    rs = w._parse_lines(L("AHW1", "10/1/25 7:56", *CELLS))
    assert [(r.dt, r.temp) for r in rs] == [("10/1/2025 7:56", 59.1)]


def test_two_digit_rows_through_the_pdf_path(tmp_path):
    import fitz
    path = tmp_path / "r.pdf"
    doc = fitz.open()
    hdr = ("Well ID Alias Date/Time ADJ CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. "
           "Temp. Flow Well Pres. Header Pres. Comments")
    rows = ["AHCANFLR", "AHCANFLR", "10/1/25 7:56", *CELLS[:-1], "0.01 ---",
            "AHCANFLR", "AHCANFLR", "10/1/25 15:55 ADJ", *CELLS[:-1], "0.01 ---"]
    doc.new_page().insert_text((20, 20), "Gas Extraction Report\n" + hdr + "\n" + "\n".join(rows),
                               fontsize=5)
    doc.save(str(path))
    doc.close()
    g = w.parse_gas_extraction_report(str(path))
    assert g.rejected_tables == []
    assert [(r.well_id, r.dt, r.adj, r.temp, r.flow) for r in g.readings] == [
        ("AHCANFLR", "10/1/2025 7:56", False, 59.1, 1096.0),
        ("AHCANFLR", "10/1/2025 15:55", True, 59.1, 1096.0)]


def test_split_table_with_two_digit_years_joins(tmp_path):
    rows = [dict(row("AHW1", k), dt=f"1/2/25 10:{k:02d}") for k in range(3)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    assert g.split_joined_rows == 3 and g.rejected_tables == []
    assert [r.dt for r in g.readings] == ["1/2/2025 10:00", "1/2/2025 10:01", "1/2/2025 10:02"]


def test_a_two_digit_timestamp_on_a_right_page_refuses_the_join(tmp_path):
    # A right half prints no timestamp in any year form.
    rows = [dict(row("AHW1", k), dt=f"1/2/2025 10:{k:02d}") for k in range(3)]
    ok = w.parse_gas_extraction_report(build(tmp_path / "a.pdf", [rows]))
    bad = w.parse_gas_extraction_report(build(tmp_path / "b.pdf", [rows],
                                              extra_lines=[(0, 300, 52, "1/2/25 10:00")]))
    assert ok.split_joined_rows == 3
    assert bad.split_joined_rows == 0
