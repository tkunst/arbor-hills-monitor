"""Split gas tables (ADR 005 addendum 2026-10-20): a gas table printed as a
left half (Well ID, Date/Time, ADJ, gas columns) on n pages and a right half
(Temp, Flow, pressures, no Well ID) on the n pages after it is joined row by
row. Hermetic: synthetic PDFs built in-process, never committed."""
import fitz
import pytest

import woi_table_parser as w

FS = 6
H = 800  # page height used to map the rotated layouts
LEFT_LABELS = [(20, "Well ID"), (70, "Date/Time"), (130, "ADJ"), (160, "CH4 (%)"),
               (190, "CO2 (%)"), (220, "O2 (%)"), (250, "Balance (%)")]
LEFT_X = [160, 190, 220, 250]
# Right-half labels, in INSERTION (text) order: "Temp." is extracted before
# the wrapped "Diff. / Press." label printed to its left, as in the filings.
RIGHT_LABELS = [(80, 40, "Temp."), (30, 34, "Diff."), (30, 40, "Press."), (120, 40, "Flow"),
                (160, 34, "Well"), (160, 40, "Pres."), (210, 34, "Header"), (210, 40, "Pres."),
                (270, 40, "Comments")]
RIGHT_X = [30, 80, 120, 160, 210]   # diff_press, temp, flow, well_pres, header_pres
GAS = ("50", "30", "0.5", "19.5")


def row(well, minute, right=("0.5", "120", "9", "-4", "-40"), adj=False, comment=None):
    return {"well": well, "dt": f"1/2/2025 10:{minute:02d}", "adj": adj, "gas": GAS,
            "right": right, "comment": comment}


def _put(page, mode, along, across, text):
    if mode == "text90":   # rotated text on an unrotated page: runs upward
        page.insert_text((across, H - along), text, fontsize=FS, rotate=90)
    else:
        page.insert_text((along, across), text, fontsize=FS)


def build(path, left_pages, mode="none", right_labels=None, extra_right=0, extra_lines=(),
          left_headers=None, shift=None, before=None):
    """left_pages: [[row, ...], ...]; the right half follows on as many
    pages. right_labels: indexes of the right pages that print labels (all
    by default). extra_right: more right pages after them. extra_lines:
    (right page index, along, across, text) printed as well. left_headers:
    indexes of the left pages that print the header (all by default).
    shift: {right page index: points} moves that page's cells along the
    text. before: lines for a page printed before the left half."""
    doc = fitz.open()
    if before is not None:
        p = doc.new_page(width=612, height=H)
        p.insert_text((20, 20), "\n".join(before), fontsize=FS)
    shift = shift or {}
    n = len(left_pages)
    right_labels = set(range(n)) if right_labels is None else set(right_labels)
    for li, rows in enumerate(left_pages):
        p = doc.new_page(width=612, height=H)
        _put(p, mode, 20, 20, "Gas Extraction Report")
        if left_headers is None or li in left_headers:
            for x, t in LEFT_LABELS:
                _put(p, mode, x, 40, t)
        for k, r in enumerate(rows):
            y = 52 + 12 * k
            _put(p, mode, 20, y, r["well"])
            _put(p, mode, 70, y, r["dt"])
            if r["adj"]:
                _put(p, mode, 130, y, "ADJ")
            for x, v in zip(LEFT_X, r["gas"]):
                _put(p, mode, x, y, v)
    for i in range(n + extra_right):
        p = doc.new_page(width=612, height=H)
        if i in right_labels or i >= n:
            for x, y, t in RIGHT_LABELS:
                _put(p, mode, x, y, t)
        rows = left_pages[i] if i < n else left_pages[-1]
        for k, r in enumerate(rows):
            y = 52 + 12 * k
            if r["right"] is None:
                continue
            for x, v in zip(RIGHT_X, r["right"]):
                if v is not None:
                    _put(p, mode, x + shift.get(i, 0), y, v)
            if r["comment"]:
                _put(p, mode, 270, y, r["comment"])
        for pi, x, y, t in extra_lines:
            if pi == i:
                _put(p, mode, x, y, t)
    if mode == "page":   # a /Rotate page
        for p in doc:
            p.set_rotation(90)
    doc.save(str(path))
    doc.close()
    return str(path)


def three(well, start=0, **kw):
    return [row(well, start + k, **kw) for k in range(3)]


@pytest.mark.parametrize("mode", ["none", "text90", "page"])
def test_split_table_is_joined(tmp_path, mode):
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf",
                                            [three("AHW1"), three("AHW2", 10)], mode=mode))
    assert g.split_joined_rows == 6 and g.split_unjoined_rows == 0
    assert g.rejected_tables == []
    r = g.readings[0]
    assert (r.well_id, r.dt, r.page) == ("AHW1", "1/2/2025 10:00", 1)
    assert (r.ch4, r.co2, r.o2, r.balance) == (50.0, 30.0, 0.5, 19.5)
    # the right half's labels are extracted "Temp." first, but Diff. Press is
    # printed first: the order comes from the label positions
    assert (r.diff_press, r.temp, r.flow, r.well_pres, r.header_pres) == (0.5, 120.0, 9.0, -4.0, -40.0)
    assert r.valid and [x.well_id for x in g.readings] == ["AHW1"] * 3 + ["AHW2"] * 3


def test_without_its_right_half_the_table_stays_rejected_as_before(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=H)
    for x, t in LEFT_LABELS:
        p.insert_text((x, 40), t, fontsize=FS)
    for k in range(3):
        for x, t in zip([20, 70] + LEFT_X, ["AHW1", f"1/2/2025 10:0{k}", *GAS]):
            p.insert_text((x, 52 + 12 * k), t, fontsize=FS)
    path = tmp_path / "r.pdf"
    doc.save(str(path))
    g = w.parse_gas_extraction_report(str(path))
    assert g.readings == [] and g.split_joined_rows == 0
    assert [(t.n_rows, t.reason) for t in g.rejected_tables] == [(3, "column layout not recognized")]


def test_page_count_mismatch_stays_rejected(tmp_path):
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1"), three("AHW2", 10)],
                                            extra_right=1))
    assert g.readings == [] and g.split_joined_rows == 0 and g.split_unjoined_rows == 0
    assert [(t.first_page, t.last_page, t.n_rows, t.reason) for t in g.rejected_tables] == [
        (1, 2, 6, "column layout not recognized")]


def test_left_row_without_a_right_row_leaves_its_page_unjoined(tmp_path):
    p1 = three("AHW1")
    p1[1]["right"] = None   # the right half prints nothing at this row
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [p1, three("AHW2", 10)]))
    assert [r.well_id for r in g.readings] == ["AHW2"] * 3      # page 2 still joins
    assert g.split_joined_rows == 3 and g.split_unjoined_rows == 3
    assert [(t.first_page, t.last_page, t.n_rows, t.reason) for t in g.rejected_tables] == [
        (1, 1, 3, w.SPLIT_UNJOINED_REASON)]


def test_right_row_missing_a_cell_is_counted_not_joined(tmp_path):
    p1 = three("AHW1")
    p1[2]["right"] = ("0.5", "120", None, "-4", "-40")   # Flow printed nothing
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [p1, three("AHW2", 10)]))
    assert g.split_joined_rows == 5 and g.split_unjoined_rows == 1
    assert "1/2/2025 10:02" not in [r.dt for r in g.readings]


def test_blank_cell_keeps_its_column(tmp_path):
    p1 = three("AHW1")
    p1[0]["right"] = ("0.5", "---", "9", "-4", "-40")
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [p1, three("AHW2", 10)]))
    r = g.readings[0]
    assert (r.diff_press, r.temp, r.flow, r.header_pres) == (0.5, None, 9.0, -40.0)
    assert g.split_joined_rows == 6     # a gas-only reading, kept (ADR 005 2026-10-19)


def test_value_and_comment_on_one_line(tmp_path):
    p1 = three("AHW1")
    p1[0]["right"] = ("0.5", "120", "9", "-4", "-34.83 No Change,Second Reading: :")
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [p1, three("AHW2", 10)]))
    assert g.readings[0].header_pres == -34.83 and g.split_joined_rows == 6


def test_comment_starting_with_a_number_fills_no_column(tmp_path):
    # Header Pres printed nothing; a comment "2 turns open" sits in that
    # column's band but doesn't line up with its cells: the row isn't joined.
    p1 = three("AHW1")
    p1[0]["right"] = ("0.5", "120", "9", "-4", None)
    g = w.parse_gas_extraction_report(build(
        tmp_path / "r.pdf", [p1, three("AHW2", 10)], extra_lines=[(0, 240, 52, "2 turns open")]))
    assert g.split_unjoined_rows == 1 and g.split_joined_rows == 5
    assert all(r.header_pres == -40.0 for r in g.readings)


def test_wrapped_comment_line_between_rows_is_not_a_row(tmp_path):
    # An extra right-page line that starts with a number, at its own
    # baseline in the Comments column (a wrapped comment): not a row.
    p1 = three("AHW1", comment="Opened Valve 1/2 turn")
    g = w.parse_gas_extraction_report(build(
        tmp_path / "r.pdf", [p1, three("AHW2", 10)], extra_lines=[(0, 270, 58, "150 ft of water")]))
    assert g.split_joined_rows == 6 and g.split_unjoined_rows == 0


def test_labels_only_on_a_later_right_page(tmp_path):
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1"), three("AHW2", 10)],
                                            right_labels={1}))
    assert g.split_joined_rows == 6
    assert (g.readings[0].diff_press, g.readings[0].temp) == (0.5, 120.0)


def test_joined_rows_get_the_negative_flow_check(tmp_path):
    rows = [row("AHW1", k, right=("0.5", "120", "-9", "-4", "-40")) for k in range(22)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    assert g.readings == []
    assert [t.reason for t in g.rejected_tables] == [
        "column-shift signature: 100% of rows have a negative flow"]


def test_is_split_left():
    assert w._is_split_left(["ch4", "co2", "o2", "balance"])
    assert w._is_split_left(["ch4", "co2", "o2", "balance", "diff_press"])
    assert not w._is_split_left(["ch4", "co2", "o2", "balance", "temp"])
    assert not w._is_split_left(["ch4", "co2", "o2"])
    assert not w._is_split_left(None)


def test_unlabeled_right_page_drawn_elsewhere_is_not_joined(tmp_path):
    # Right page 2 prints no labels and its columns sit 40 pt to the left,
    # with a pump count where Header Pres would be: it holds no row in the
    # labeled page's columns, so the table is not joined (never Flow as Temp).
    p2 = three("AHW2", 10)
    g = w.parse_gas_extraction_report(build(
        tmp_path / "r.pdf", [three("AHW1"), p2], right_labels={0}, shift={1: -40},
        extra_lines=[(1, 230, 52 + 12 * k, "3") for k in range(3)]))
    assert g.readings == [] and g.split_joined_rows == 0
    assert [t.reason for t in g.rejected_tables] == ["column layout not recognized"]


def test_unlabeled_right_page_in_place_is_joined(tmp_path):
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1"), three("AHW2", 10)],
                                            right_labels={0}))
    assert g.split_joined_rows == 6


def test_header_only_on_the_first_left_page(tmp_path):
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1"), three("AHW2", 10)],
                                            left_headers={0}))
    assert g.split_joined_rows == 6


def test_left_range_not_starting_at_its_header_is_not_joined(tmp_path):
    # The first left page's rows are all unreadable ("---" gas cells), so the
    # rejected rows start on page 2, which prints no header: n would be one
    # short and every page would pair one page off. Nothing is joined.
    p1 = [dict(r, gas=("---", "---", "---", "---")) for r in three("AHW1")]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [p1, three("AHW2", 10),
                                                                 three("AHW3", 20)],
                                            left_headers={0}))
    assert g.split_joined_rows == 0 and g.readings == []


def test_unclaimed_right_row_leaves_the_page_unjoined(tmp_path):
    # A complete right row at a baseline where the left page prints nothing:
    # the halves disagree, so the page is not joined.
    g = w.parse_gas_extraction_report(build(
        tmp_path / "r.pdf", [three("AHW1"), three("AHW2", 10)],
        extra_lines=[(0, x, 88, v) for x, v in zip(RIGHT_X, ("1", "100", "5", "-1", "-9"))]))
    assert [r.well_id for r in g.readings] == ["AHW2"] * 3
    assert g.split_unjoined_rows == 3


def test_adj_rows_are_joined_with_their_flag(tmp_path):
    rows = [row("AHW1", 0), row("AHW1", 0, adj=True, right=("0.4", "121", "8", "-4", "-41")),
            row("AHW1", 5)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    assert [(r.adj, r.temp) for r in g.readings] == [(False, 120.0), (True, 121.0), (False, 120.0)]


def test_temp_in_a_comment_below_the_rows_is_not_a_label(tmp_path):
    p1 = three("AHW1")
    p1[2]["comment"] = "High Temp. reading"
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [p1, three("AHW2", 10)]))
    assert g.split_joined_rows == 6


def test_a_normal_table_before_the_split_one_reads_as_before(tmp_path):
    before = ["Gas Extraction Report",
              "Well ID Date/Time ADJ CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. "
              "Temp. Flow Well Pres. Header Pres.",
              "AHW9", "3/14/2025 15:40", "8.1", "40", "7.0", "44.9", "0.5", "177.0", "10", "-5", "-40"]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1")], before=before))
    assert [(r.well_id, r.temp, r.page) for r in g.readings] == [
        ("AHW9", 177.0, 1)] + [("AHW1", 120.0, 2)] * 3
    assert g.split_joined_rows == 3


def test_diff_press_on_both_halves_is_not_joined(tmp_path):
    # Left header with Diff. Press and a right half that also has it: the
    # columns don't combine into a known layout.
    global LEFT_LABELS
    saved = LEFT_LABELS
    try:
        LEFT_LABELS = saved + [(280, "Diff. Press.")]
        rows = [dict(r, gas=GAS + ("0.3",)) for r in three("AHW1")]
        orig = list(LEFT_X)
        LEFT_X.append(280)
        g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    finally:
        LEFT_LABELS = saved
        LEFT_X[:] = orig
    assert g.split_joined_rows == 0 and g.readings == []
    assert [t.reason for t in g.rejected_tables] == ["column layout not recognized"]


def test_unlabeled_column_inside_a_measurement_column_is_not_joined(tmp_path):
    # A column printed between Temp and Flow with no label of its own: two
    # numbers in the Temp column on every row, so no row is joined.
    extra = [(pi, 100, 52 + 12 * k, "526") for pi in range(2) for k in range(3)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1"), three("AHW2", 10)],
                                            extra_lines=extra))
    assert g.split_joined_rows == 0
    assert all(r.temp != 526.0 for r in g.readings)


def _two_tables(tmp_path, footers=0):
    a = build(tmp_path / "a.pdf", [three("AHW1")])
    b = build(tmp_path / "b.pdf", [three("AHW2", 10, right=("0.1", "150", "5", "-2", "-20"))])
    d = fitz.open(a)
    d.insert_pdf(fitz.open(b))
    for k in range(footers):
        d.new_page(width=612, height=H).insert_text((300, 780), str(k + 5), fontsize=FS)
    d.save(str(tmp_path / "ab.pdf"))
    return w.parse_gas_extraction_report(str(tmp_path / "ab.pdf"))


@pytest.mark.parametrize("footers", [0, 2])
def test_two_split_tables_in_a_row_never_borrow_each_others_halves(tmp_path, footers):
    # Pages: A left, A right, B left, B right (+ pages holding only a page
    # number). The run of rejected rows spans A left..B left; it is not a
    # left half (A right prints no rows), so nothing is joined.
    g = _two_tables(tmp_path, footers)
    assert g.split_joined_rows == 0 and g.readings == []


def test_page_shifted_one_column_with_a_leading_column_is_not_joined(tmp_path):
    # Evenly spaced columns plus a numeric pump-count column. The unlabeled
    # page prints an extra leading column and everything one column over:
    # two columns share the pump-count label, so that page is not joined.
    labels = [(80, 40, "Temp."), (40, 34, "Diff."), (40, 40, "Press."), (120, 40, "Flow"),
              (160, 34, "Well"), (160, 40, "Pres."), (200, 34, "Header"), (200, 40, "Pres."),
              (240, 40, "Pump"), (290, 40, "Comments")]
    global RIGHT_LABELS, RIGHT_X
    saved = RIGHT_LABELS, list(RIGHT_X)
    try:
        RIGHT_LABELS = labels
        RIGHT_X[:] = [40, 80, 120, 160, 200]
        pump = [(0, 240, 52 + 12 * k, "3") for k in range(3)]
        pump += [(1, 280, 52 + 12 * k, "3") for k in range(3)]
        pump += [(1, 40, 52 + 12 * k, "7") for k in range(3)]
        g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1"), three("AHW2", 10)],
                                                right_labels={0}, shift={1: 40}, extra_lines=pump))
    finally:
        RIGHT_LABELS, RIGHT_X[:] = saved[0], saved[1]
    assert [r.well_id for r in g.readings] == ["AHW1"] * 3
    assert g.split_unjoined_rows == 3


def test_unjoined_row_is_still_the_adj_partner(tmp_path, monkeypatch):
    # No ADJ label: an as-found row whose right half is incomplete is not
    # joined, but its after-adjustment twin must still be read as ADJ.
    monkeypatch.setitem(globals(), "LEFT_LABELS", [lab for lab in LEFT_LABELS if lab[1] != "ADJ"])
    rows = [row("AHW1", 0, right=("0.5", "120", None, "-4", "-40")),
            row("AHW1", 0, right=("0.4", "125", "8", "-4", "-41")), row("AHW1", 5),
            row("AHW1", 6), row("AHW1", 7)]
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [rows]))
    assert [(r.dt[-5:], r.adj, r.temp) for r in g.readings][:2] == [
        ("10:00", True, 125.0), ("10:05", False, 120.0)]
    assert g.split_unjoined_rows == 1


def test_a_failing_join_keeps_the_first_parse(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("geometry")
    monkeypatch.setattr(w, "_settle_split", boom)
    g = w.parse_gas_extraction_report(build(tmp_path / "r.pdf", [three("AHW1")]))
    assert g.readings == [] and g.split_joined_rows == 0
    assert [t.reason for t in g.rejected_tables] == ["column layout not recognized"]
