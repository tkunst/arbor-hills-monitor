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
NEG = ["47.8", "36", "1.8", "14.4", "0", "59.1", "-1096", "0.56", "0.01"]
TODAY = datetime.date(2026, 10, 21)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(w, "_today", lambda: TODAY)


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
    ("1/1/10 0:00", "1/1/2010 0:00"),        # the first year read
    ("12/31/25 23:59", "12/31/2025 23:59"),
    ("10/21/26 7:56", "10/21/2026 7:56"),    # today
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
    "1/0/00 0:00",          # a spreadsheet's day 0, printed in one report
    "1/1/00 0:00",          # and its day 1
    "12/31/09 7:56",        # before TWO_DIGIT_YEAR_MIN
    "10/22/26 7:56",        # tomorrow
    "10/1/25 24:00",        # not a time
    "10/1/25 7:60",
    "1/1/99 7:56",          # 2099: after today
])
def test_near_misses_start_no_row(line):
    assert w._dt_line(line) is None
    assert _read(["AHW1", line, *CELLS]) == []


def test_the_date_bound_is_the_parse_day(monkeypatch):
    assert w._dt_line("10/22/26 7:56") is None
    monkeypatch.setattr(w, "_today", lambda: datetime.date(2026, 10, 22))
    assert w._dt_line("10/22/26 7:56") == ("10/22/2026 7:56", False)


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


def test_unlabeled_adj_partner_within_two_digit_rows():
    # No ADJ label: the second copy of a row is its ADJ twin, compared on the
    # stored (four-digit) timestamp, among two-digit-year rows.
    a = ["AHW1", "10/1/25 7:56", *CELLS]
    rs = _read(a, a, hdr=HDR_FULL_NO_ADJ)
    assert [(r.dt, r.adj) for r in rs] == [("10/1/2025 7:56", False), ("10/1/2025 7:56", True)]


def test_two_digit_and_four_digit_rows_never_pair():
    # The same reading in both forms is not an ADJ twin of the other.
    two = ["AHW1", "10/1/25 7:56", *CELLS]
    four = ["AHW1", "10/1/2025 7:56", *CELLS]
    assert [r.adj for r in _read(four, two, hdr=HDR_FULL_NO_ADJ)] == [False, False]
    assert [r.adj for r in _read(two, four, hdr=HDR_FULL_NO_ADJ)] == [False, False]


# A table that mixes the two forms: every reading main reads from the
# four-digit rows is unchanged, and the two-digit rows are only added.

def test_a_two_digit_row_never_moves_a_main_rows_unlabeled_adj_flag():
    a = ["AHW1", "10/1/2025 7:56", *CELLS]
    mid = ["AHW9", "1/2/25 3:00", *CELLS]
    rs = _read(a, mid, a, hdr=HDR_FULL_NO_ADJ)
    assert [(r.well_id, r.adj) for r in rs] == [("AHW1", False), ("AHW9", False), ("AHW1", True)]


def test_two_digit_rows_cannot_reject_main_rows_by_negative_flow():
    mains = [x for k in range(15) for x in (f"AHW{k}", f"10/1/2025 7:{k:02d}", *CELLS)]
    added = [x for k in range(20) for x in (f"AHX{k}", f"1/2/25 3:{k:02d}", *NEG)]
    base = w._parse_table_lines(L(*HDR_FULL, *mains))
    g = w._parse_table_lines(L(*HDR_FULL, *mains, *added))
    assert [_key(r) for r in g.readings] == [_key(r) for r in base.readings]
    assert [(t.n_rows, t.reason) for t in g.rejected_tables] == [
        (20, "column-shift signature in rows started by a two-digit-year timestamp: "
             "100% have a negative flow")]


def test_main_rows_cannot_reject_two_digit_rows_by_negative_flow():
    mains = [x for k in range(20) for x in (f"AHW{k}", f"10/1/2025 7:{k:02d}", *NEG)]
    added = [x for k in range(3) for x in (f"AHX{k}", f"1/2/25 3:{k:02d}", *CELLS)]
    g = w._parse_table_lines(L(*HDR_FULL, *mains, *added))
    assert [(t.n_rows, t.reason[:22]) for t in g.rejected_tables] == [
        (20, "column-shift signature"), (3, "rows started by a two-")]
    assert g.readings == []


def test_a_two_digit_first_row_never_ends_mains_header():
    hdr = ["Well ID", "Date/Time", "AHW5", "1/2/25 3:00", "ADJ", "CH4 (%)", "CO2 (%)",
           "O2 (%)", "Balance", "(%)", "Diff. Press.", "Temp.", "Flow", "Well Pres.",
           "Header Pres."]
    main = ["AHW1", "10/1/2025 7:56", *CELLS]
    g = w._parse_table_lines(L(*hdr, *main))
    assert [(r.well_id, r.dt) for r in g.readings] == [("AHW1", "10/1/2025 7:56")]
    assert g.rejected_tables == []
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


def test_a_split_table_of_two_digit_rows_stays_rejected(tmp_path):
    # The split planner keeps main's rule: a left page that prints only
    # two-digit-year rows does not count as a page of rows, so the table
    # stays rejected exactly as on main (none is printed in the corpus).
    rows = [dict(row("AHW1", k), dt=f"1/2/25 10:{k:02d}") for k in range(3)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    assert g.split_joined_rows == 0 and g.readings == []
    assert [t.reason for t in g.rejected_tables] == ["column layout not recognized"]


def test_two_digit_text_on_a_right_page_changes_no_join(tmp_path):
    rows = [dict(row("AHW1", k), dt=f"1/2/2025 10:{k:02d}") for k in range(3)]
    for name, extra in (("a.pdf", []), ("b.pdf", [(0, 300, 52, "Printed 1/2/25 10:00")])):
        g = w.parse_gas_extraction_report(build(tmp_path / name, [rows], extra_lines=extra))
        assert g.split_joined_rows == 3 and len(g.readings) == 3


# Review round 2: a table printed only in two-digit years gets main's own
# bookkeeping (trusted Flow cells vs cells read past a blank, the last-column
# edge, the unlabeled-ADJ partner), so it reads exactly like its four-digit
# twin.

def _four(t):
    return t.replace("/25 ", "/2025 ")


def _twin_case(rows):
    two = w._parse_table_lines(L(*HDR_FULL, *[x for r in rows for x in r]))
    four = w._parse_table_lines(L(*HDR_FULL, *[_four(x) for r in rows for x in r]))
    return two, four


@pytest.mark.parametrize("n_neg, n_blank, n_clean", [(6, 10, 14), (0, 12, 30), (8, 0, 20),
                                                     (3, 12, 17)])
def test_a_two_digit_table_is_column_shift_checked_like_its_four_digit_twin(
        n_neg, n_blank, n_clean):
    past_blank = ["47.8", "36", "1.8", "14.4", "---", "59.1", "-1096", "0.56", "0.01"]
    rows = ([[f"AHW{k}", f"10/1/25 7:{k:02d}", *NEG] for k in range(n_neg)]
            + [[f"AHB{k}", f"10/2/25 7:{k:02d}", *past_blank] for k in range(n_blank)]
            + [[f"AHC{k}", f"10/3/25 7:{k:02d}", *CELLS] for k in range(n_clean)])
    two, four = _twin_case(rows)
    assert [_key(r) for r in two.readings] == [_key(r) for r in four.readings]
    assert [(t.first_page, t.last_page, t.n_rows) for t in two.rejected_tables] == [
        (t.first_page, t.last_page, t.n_rows) for t in four.rejected_tables]


def test_one_line_two_digit_adj_is_never_the_partner():
    # as a four-digit one-line ADJ row never is: the third row is the first
    # row's unlabeled ADJ twin in both forms
    rows = [["AHW1", "10/1/25 7:56", *CELLS], ["AHW1", "10/1/25 7:56 ADJ", *CELLS],
            ["AHW1", "10/1/25 7:56", *CELLS]]
    two = _read(*rows, hdr=HDR_FULL_NO_ADJ)
    four = _read(*[[_four(x) for x in r] for r in rows], hdr=HDR_FULL_NO_ADJ)
    assert [r.adj for r in two] == [r.adj for r in four] == [False, True, True]


def _positioned(rows):
    from tests.test_woi_parser import H9
    lines, pos = [(H9, 1)], [None]
    for well, dt, last, edge in rows:
        cells = [well, dt, "50", "30", "0.5", "19.5", "-1", "120", "9", "-4", last]
        for k, t in enumerate(cells):
            lines.append((t, 1))
            pos.append((20.0, edge, 3.0) if k == len(cells) - 1 else (20.0, None, None))
    return w._parse_table_lines(lines, positions=pos)


def test_comment_line_cell_in_a_two_digit_table_lines_up_with_its_own_edge():
    rows = [("AHW1", "1/2/25 10:00", "-5", 230.0), ("AHW2", "1/2/25 10:01", "-6", 230.0),
            ("AHW3", "1/2/25 10:02", "-0.68 No Change: :", 230.0)]
    g = _positioned(rows)
    assert [r.header_pres for r in g.readings] == [-5.0, -6.0, -0.68]
    assert g.unaligned_cells == 0


def test_two_digit_rows_never_move_mains_last_column_edge():
    # Main's rows end at 230; three two-digit rows end at 260. Main's
    # comment-line cell still lines up with main's own edge.
    mains = [("AHW1", "1/2/2025 10:00", "-5", 230.0), ("AHW2", "1/2/2025 10:01", "-6", 230.0),
             ("AHW3", "1/2/2025 10:02", "-0.68 No Change: :", 230.0)]
    added = [(f"AHX{k}", f"1/3/25 10:0{k}", "-7", 260.0) for k in range(3)]
    base = _positioned(mains)
    g = _positioned(mains + added)
    assert [_key(r) for r in g.readings[:3]] == [_key(r) for r in base.readings]
    assert g.readings[2].header_pres == -0.68


def test_two_digit_tables_read_like_their_four_digit_twins():
    # Seeded random tables (headers with and without ADJ/Alias labels, ADJ
    # lines and one-line "timestamp ADJ" rows, "---" cells, negative flows,
    # comments, short rows; up to three pages, the header repeated on each,
    # under labeled headers): printed with two-digit years they give exactly
    # the readings and reject ranges of the same table printed with
    # four-digit years. (A multi-page table under an unlabeled header is
    # left out: see the ADR addendum's open item.)
    import random
    rnd = random.Random(20261021)
    cells = [CELLS, NEG, CELLS[:4] + ["---"] + CELLS[5:], CELLS[:5], ["---"] * 9]
    for _ in range(600):
        hdr = rnd.choice([HDR_FULL, HDR_FULL_NO_ADJ, HDR_ALIAS])
        n_pages = 1 if hdr is HDR_FULL_NO_ADJ else rnd.randint(1, 3)
        seq = []
        for page in range(1, n_pages + 1):
            seq += [(h, page) for h in hdr]
            for k in range(rnd.randint(1, 30)):
                ts = (f"{rnd.randint(1, 12)}/{rnd.randint(1, 28)}/25 {rnd.randint(0, 23)}:"
                      f"{rnd.randint(0, 59):02d}")
                adj = rnd.random()
                row = [rnd.choice(["AHW1", "AHW2", "AHWW312R*"])]
                row += ["EW-7"] if hdr is HDR_ALIAS else []
                row += [ts + " ADJ"] if adj < 0.2 else [ts, "ADJ"] if adj < 0.35 else [ts]
                row += list(rnd.choice(cells))
                if rnd.random() < 0.3:
                    row.append(rnd.choice(["No Change: :", "Opened Valve 1/2 turn:", "581821: 0"]))
                seq += [(x, page) for x in row]
        two = w._parse_table_lines(seq)
        four = w._parse_table_lines([(_four(x), pg) for x, pg in seq])
        assert [_key(r) for r in two.readings] == [_key(r) for r in four.readings], seq
        assert [(t.first_page, t.last_page, t.n_rows) for t in two.rejected_tables] == [
            (t.first_page, t.last_page, t.n_rows) for t in four.rejected_tables], seq


@pytest.mark.parametrize("n_clean, n_neg_oneline", [(14, 8), (70, 20)])
def test_one_line_two_digit_rows_are_checked_in_their_own_pool(n_clean, n_neg_oneline):
    # As the four-digit one-line rows are: 8 negative one-line rows can't
    # reject 14 clean rows, and 20 of them are rejected on their own even
    # among 70 clean rows.
    def ts(k):
        return f"10/3/25 {7 + k // 60}:{k % 60:02d}"
    rows = ([[f"AHC{k}", ts(k), *CELLS] for k in range(n_clean)]
            + [[f"AHC{k}", ts(k) + " ADJ", *NEG] for k in range(n_neg_oneline)])
    two, four = _twin_case(rows)
    assert [_key(r) for r in two.readings] == [_key(r) for r in four.readings]
    assert len(two.readings) == n_clean + (n_neg_oneline if n_neg_oneline < 20 else 0)
