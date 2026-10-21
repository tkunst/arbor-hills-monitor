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


def build(path, left_pages, mode="none", right_labels=None, extra_right=0, extra_lines=()):
    """left_pages: [[row, ...], ...]; the right half follows on as many
    pages. right_labels: indexes of the right pages that print labels (all
    by default). extra_right: more right pages after them. extra_lines:
    (right page index, along, across, text) printed as well."""
    doc = fitz.open()
    n = len(left_pages)
    right_labels = set(range(n)) if right_labels is None else set(right_labels)
    for rows in left_pages:
        p = doc.new_page(width=612, height=H)
        _put(p, mode, 20, 20, "Gas Extraction Report")
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
                    _put(p, mode, x, y, v)
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
