"""WOI table parser: line state-machine, validation gate, canonicalization,
per-well aggregation, and measurement emission. Hermetic — operates on synthetic
linearized lines, no PDF."""
import woi_table_parser as w
from woi_table_parser import WOIReading


def L(*items):
    """Build a [(text, page)] list from bare strings (all on page 1)."""
    return [(s, 1) for s in items]


# CH4 CO2 O2 Bal DiffP Temp Flow WellP HeaderP  (8.1+40+7+44.9 = 100.0)
HOT = ["AHW272R4", "3/14/2025 15:40", "8.1", "40", "7.0", "44.9", "0.5", "177.0", "10", "-5", "-40"]


def test_parses_a_normal_reading_with_columns():
    rs = w._parse_lines(L(*HOT, "No Change: : "))
    assert len(rs) == 1
    r = rs[0]
    assert r.well_id == "AHW272R4" and r.adj is False
    assert r.ch4 == 8.1 and r.o2 == 7.0 and r.temp == 177.0
    assert r.valid is True


def test_adj_row_flagged():
    rs = w._parse_lines(L("AHW272R4", "3/14/2025 15:40", "ADJ",
                          "8.1", "40", "7.0", "44.9", "0.5", "177.0", "10", "-5", "-40"))
    assert len(rs) == 1 and rs[0].adj is True


def test_comment_with_numbers_not_consumed_as_columns():
    # 9 numeric columns, then a comment line that itself contains numbers, then
    # the next well. The comment numbers must NOT become extra columns.
    lines = L(*HOT, "581821: 0", "543934: 10 60 0",
              "AHEWL09R", "3/10/2025 11:37", "41.4", "28.4", "4.1", "26.1",
              "1.93", "68.1", "5.4", "-22.54", "-34.37")
    rs = w._parse_lines(lines)
    assert len(rs) == 2
    assert rs[0].header_pres == -40.0           # 9th column intact
    assert rs[1].well_id == "AHEWL09R" and rs[1].temp == 68.1


def test_validation_gate_catches_misalignment():
    bad = ["AHW999", "1/1/2025 10:00", "10", "10", "10", "10", "0", "120", "5", "-1", "-2"]
    r = w._parse_lines(L(*bad))[0]
    assert r.valid is False     # 10+10+10+10 = 40, not ~100


def test_date_only_rows_are_not_gas_readings():
    # CO-style rows (Attachment 2) have a date with NO time -> must be skipped.
    rs = w._parse_lines(L("AHWW258R", "1/29/2025", "0", "AHW259R5", "1/29/2025", "5"))
    assert rs == []


def test_canonicalize_strips_asterisks_and_applies_alias():
    assert w.canonicalize("AHW272R4**") == "AHW272R4"
    assert w.canonicalize("AHW259R5****") == "AHW259R5"
    assert w.canonicalize("AHW0177", {"AHW0177": "AHWW177R"}) == "AHWW177R"


def _r(well, temp, o2, ch4, adj=False, dt="3/14/2025 15:40"):
    return WOIReading(well_id=well, raw_well_id=well, dt=dt, adj=adj, page=1,
                      ch4=ch4, co2=40.0, o2=o2, balance=100 - ch4 - 40.0 - o2,
                      diff_press=0.5, temp=temp, flow=10.0, well_pres=-5.0, header_pres=-40.0)


def test_per_well_summary_picks_max_temp_and_concurrent_values():
    readings = [
        _r("AHW272R4", 150.0, 6.0, 9.0, dt="2/1/2025 10:00"),
        _r("AHW272R4", 177.0, 7.0, 8.1, dt="3/14/2025 15:40"),   # the hottest
        _r("AHW001", 90.0, 1.0, 55.0),
    ]
    summ = w.per_well_summary(readings, woi_set={"AHW272R4"})
    top = summ[0]
    assert top["well"] == "AHW272R4"
    assert top["max_temp_f"] == 177.0
    assert top["o2_at_max_temp"] == 7.0      # O2 read at the hottest moment
    assert top["ch4_at_max_temp"] == 8.1
    assert top["is_woi"] is True


def test_per_well_summary_excludes_adj_and_invalid():
    readings = [
        _r("AHW272R4", 177.0, 7.0, 8.1, adj=True),   # ADJ -> excluded
        _r("AHW272R4", 150.0, 6.0, 9.0),             # as-found -> used
    ]
    summ = w.per_well_summary(readings)
    assert summ[0]["max_temp_f"] == 150.0


def test_to_measurements_emits_temp_o2_methane_and_skips_adj():
    r = _r("AHW272R4", 177.0, 7.0, 8.1)
    ms = w.to_measurements(r)
    metrics = {m["metric"] for m in ms}
    assert metrics == {"temperature", "oxygen", "methane"}   # methane is first-class now
    temp = next(m for m in ms if m["metric"] == "temperature")
    assert temp["value"] == 177.0 and temp["basis"] == "measured"
    assert temp["well_id"] == "AHW272R4" and temp["as_of_date"] == "2025-03-14"
    methane = next(m for m in ms if m["metric"] == "methane")
    assert methane["value"] == 8.1 and methane["basis"] == "measured"
    assert w.to_measurements(_r("AHW272R4", 177.0, 7.0, 8.1, adj=True)) == []


# --- CO (Attachment 2) ---

CO_PAGE = [
    "Wells of Interest Per May 6, 2019 HOV Approval Letter",
    "Attachment 2 - June 2025 CO Data",
    "Well ID", "Date", "ppm",
    "AHWW0279", "6/24/2025", "110",
    "AHW272R4**", "6/23/2025", "70",
    "AHWW258R", "6/24/2025", "70",
]


def test_co_page_parses_triples_and_canonicalizes():
    rs = w._parse_co_page(CO_PAGE, page=152)
    assert len(rs) == 3
    assert rs[0].well_id == "AHWW0279" and rs[0].ppm == 110.0 and rs[0].month == "June 2025"
    assert rs[1].well_id == "AHW272R4"   # asterisks stripped


def test_co_page_skips_malformed_percent_table():
    # The '%' double-tables (no standalone 'ppm' header) must yield nothing.
    messy = ["Attachment 2 - March 2025 CO Data", "Well ID", "Date", "%",
             "AHWW258R", "3/24/2025", "0", "45658.00", "AHWW258R", "NA"]
    assert w._parse_co_page(messy, page=156) == []


def test_co_implausible_serial_value_dropped():
    page = ["Attachment 2 - March 2025 CO Data", "Well ID", "Date", "ppm",
            "AHWW258R", "3/24/2025", "45658.00",     # Excel serial -> dropped
            "AHWW0279", "3/24/2025", "5"]            # real -> kept
    rs = w._parse_co_page(page, page=149)
    assert len(rs) == 1 and rs[0].well_id == "AHWW0279"


def test_co_dedupe_keeps_first_per_well_month():
    a = w.COReading("AHW1", "AHW1", "6/1/2025", "June 2025", 110, 152)
    b = w.COReading("AHW1", "AHW1", "6/1/2025", "June 2025", 0, 159)  # messy dup
    assert [r.ppm for r in w._dedupe_co([a, b])] == [110]


def test_per_well_co_summary_orders_and_computes_rise():
    rs = [
        w.COReading("AHWW0279", "AHWW0279", "1/1/2025", "January 2025", 5, 147),
        w.COReading("AHWW0279", "AHWW0279", "6/1/2025", "June 2025", 110, 152),
    ]
    summ = w.per_well_co_summary(rs)
    assert summ[0]["well"] == "AHWW0279"
    assert summ[0]["max_ppm"] == 110
    assert summ[0]["first_ppm"] == 5 and summ[0]["last_ppm"] == 110
    assert summ[0]["rise"] == 105
    assert summ[0]["series"][0][0] == "January 2025"   # chronological


def test_co_to_measurements():
    r = w.COReading("AHW272R4", "AHW272R4**", "6/23/2025", "June 2025", 70, 152)
    ms = w.co_to_measurements(r)
    assert len(ms) == 1
    assert ms[0]["metric"] == "carbon_monoxide" and ms[0]["value"] == 70
    assert ms[0]["unit"] == "ppm" and ms[0]["basis"] == "measured"
    assert ms[0]["as_of_date"] == "2025-06-23"


# --- per-table column layout (coder:woi-layout-guard) ---

HDR_FULL = ["Well ID", "Date/Time", "ADJ", "CH4 (%)", "CO2 (%)", "O2 (%)", "Balance", "(%)",
            "Diff.", "Press. (in. H2O)", "Temp.", "Flow", "(scfm)", "Well", "Pres. (in. H2O)",
            "Header", "Pres. (in. H2O)", "Comments"]
HDR_NO_DIFF = ["Well ID", "Date/Time", "ADJ", "CH4 (%)", "CO2 (%)", "O2 (%)", "Balance", "(%)",
               "Temp.", "Flow", "(scfm)", "Well", "Pres. (in. H2O)", "Header",
               "Pres. (in. H2O)", "Comments"]
# The real no-Diff.Press row from the Q3 2023 HOV WOI report (p19): Temp 110.5,
# Flow 526.8. Read as the 9-number layout it reported 526.8F.
NO_DIFF_ROW = ["AHWW0402", "8/3/2023 11:18", "41.9", "38.4", "0.6", "19.1", "110.5",
               "526.8", "-11.43", "-51.62"]


def test_detect_layout_from_header_text():
    assert w.detect_layout(" ".join(HDR_FULL)) == w.LAYOUTS["full"]
    assert w.detect_layout(" ".join(HDR_NO_DIFF)) == w.LAYOUTS["no_diff_press"]
    assert w.detect_layout("Well ID Date/Time CH4 CO2 O2 Balance Diff. Press.") is None  # no Temp


def test_no_diff_press_table_reads_temp_not_flow():
    g = w._parse_table_lines(L(*HDR_NO_DIFF, *NO_DIFF_ROW))
    assert len(g.readings) == 1
    r = g.readings[0]
    assert r.temp == 110.5 and r.flow == 526.8 and r.diff_press is None
    assert r.well_pres == -11.43 and r.header_pres == -51.62


def test_layout_carries_forward_until_next_header():
    lines = L(*HDR_NO_DIFF, *NO_DIFF_ROW, *HDR_FULL, *HOT)
    rs = w._parse_table_lines(lines).readings
    assert [r.temp for r in rs] == [110.5, 177.0]
    assert rs[1].diff_press == 0.5 and rs[1].header_pres == -40.0


def test_rows_before_any_header_are_rejected_not_guessed():
    g = w._parse_table_lines(L(*NO_DIFF_ROW))
    assert g.readings == []
    assert len(g.rejected_tables) == 1
    assert g.rejected_tables[0].reason == "rows before any recognized header"


def test_unknown_gas_layout_rejects_whole_table():
    hdr = ["Well ID", "Date/Time", "ADJ", "CH4 (%)", "CO2 (%)", "O2 (%)", "Balance", "Diff. Press."]
    rows = ["AH147AR4", "5/19/2023 12:46", "59.9", "40", "0", "0.1", "5", "3", "2"]
    g = w._parse_table_lines(L(*hdr, *rows, *rows))
    assert g.readings == []
    assert g.rejected_tables[0].reason == "column layout not recognized"
    assert g.rejected_tables[0].n_rows == 2


def test_non_gas_table_header_ends_the_gas_table():
    co_hdr = ["Well ID", "Date", "ppm"]
    g = w._parse_table_lines(L(*HDR_FULL, *HOT, *co_hdr, *HOT))
    assert len(g.readings) == 1
    assert g.rejected_tables[0].reason == "rows under a non-gas table header"


def _row(i, flow):
    return [f"AHW{100 + i}", "3/14/2025 15:40", "50", "40", "0", "10", "0.5", "120",
            str(flow), "-5", "-40"]


def test_negative_flow_signature_rejects_table():
    lines = list(HDR_FULL)
    for i in range(30):
        lines += _row(i, -3.2 if i < 20 else 4.0)   # 67% negative "flow"
    g = w._parse_table_lines(L(*lines))
    assert g.readings == []
    assert "column-shift signature" in g.rejected_tables[0].reason


def test_occasional_negative_flow_is_kept():
    lines = list(HDR_FULL)
    for i in range(30):
        lines += _row(i, -0.1 if i < 2 else 4.0)    # 7% negative: a real table
    g = w._parse_table_lines(L(*lines))
    assert len(g.readings) == 30 and g.rejected_tables == []


def test_page_header_columns_reads_label_x_order():
    # Wrapped labels ("Balance/(%)", "Diff./Press.") come out of the text
    # extractor AFTER "Temp.", but their x-positions put them first.
    def wd(x, y, t):
        return (x, y, x + 10, y + 5, t)
    words = [wd(50, 100, "Well"), wd(62, 100, "ID"), wd(100, 100, "CH4"), wd(130, 100, "CO2"),
             wd(160, 100, "O2"), wd(190, 89, "Balance"), wd(225, 78, "Diff."),
             wd(260, 100, "Temp."), wd(290, 89, "Flow")]
    assert w._page_header_columns(words) == [["ch4", "co2", "o2", "balance", "diff_press",
                                              "temp", "flow"]]


def test_page_header_columns_rotated_table():
    # A table printed rotated on an unrotated page: "Well ID" runs upward.
    def wd(x, y, t):
        return (x, y, x + 5, y + 10, t)
    words = [wd(100, 730, "Well"), wd(100, 718, "ID"), wd(100, 560, "CH4"), wd(100, 535, "CO2"),
             wd(100, 514, "O2"), wd(94, 477, "Balance"), wd(88, 462, "Diff."),
             wd(100, 432, "Temp."), wd(94, 411, "Flow"), wd(88, 388, "Well"),
             wd(88, 356, "Header")]
    assert w._page_header_columns(words) == [w.LAYOUTS["full"]]


def test_unreadable_page_is_skipped(tmp_path, monkeypatch):
    import fitz
    path = tmp_path / "r.pdf"
    doc = fitz.open()
    for text in ["Well ID Date/Time ADJ CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. "
                 "Temp. Flow Well Pres. Header Pres.", "filler", "AHW272R4\n3/14/2025 15:40\n"
                 + "\n".join(HOT[2:])]:
        doc.new_page().insert_text((20, 20), text, fontsize=6)
    doc.save(str(path))
    doc.close()

    real_getitem = fitz.Document.__getitem__

    def flaky(self, i):
        if i == 1:
            raise RuntimeError("cannot find page 2 in page tree")
        return real_getitem(self, i)
    monkeypatch.setattr(fitz.Document, "__getitem__", flaky)
    g = w.parse_gas_extraction_report(str(path))
    assert g.skipped_pages == [2]
    assert [r.temp for r in g.readings] == [177.0]
    assert w.parse_gas_extraction(str(path))[0].temp == 177.0    # list API unchanged


def test_page_ranges():
    assert w._page_ranges([71, 72, 73, 80]) == "71-73, 80"
    assert w._page_ranges([]) == ""


def test_header_text_must_agree_with_paired_word_columns():
    # A text header naming Diff. Press paired (by position) with words that
    # lack it must not be read with the words' columns.
    hdr = list(HDR_FULL)
    lines = [(t, 1) for t in hdr + NO_DIFF_ROW]
    g = w._parse_table_lines(lines, page_headers={1: [w.LAYOUTS["no_diff_press"]]})
    assert g.readings == [] and g.rejected_tables[0].reason == "column layout not recognized"


def test_page_header_columns_ignores_pages_with_too_many_id_words():
    words = [(10 * i, 0, 10 * i + 5, 5, "ID") for i in range(6)] + [(0, 0, 5, 5, "Well")]
    assert w._page_header_columns(words) == []


def test_header_count_mismatch_on_a_page_is_not_guessed():
    # Two "Well ID" text headers but only one found by position: pairing is
    # unknowable, so neither table is read with borrowed columns.
    lines = [(t, 1) for t in HDR_NO_DIFF + NO_DIFF_ROW + HDR_FULL + HOT]
    g = w._parse_table_lines(lines, page_headers={1: [w.LAYOUTS["full"]]})
    assert g.readings == []


# --- cross-table duplicates (2026-10-16) ---

def _rd(page, diff=None, flow=10.0, temp=120.0, dt="8/3/2023 11:29"):
    return WOIReading(well_id="AHW1", raw_well_id="AHW1", dt=dt, adj=False, page=page, ch4=50.0,
                      co2=40.0, o2=0.0, balance=10.0, diff_press=diff, temp=temp, flow=flow,
                      well_pres=-5.0, header_pres=-40.0)


def test_cross_table_duplicate_collapses_to_the_most_complete_copy():
    out = w.merge_cross_table_duplicates([_rd(15), _rd(51, diff=0.5)])
    assert len(out) == 1 and out[0].page == 51 and out[0].diff_press == 0.5


def test_same_page_repeats_and_conflicting_copies_are_all_kept():
    assert len(w.merge_cross_table_duplicates([_rd(15), _rd(15)])) == 2          # same page
    assert len(w.merge_cross_table_duplicates([_rd(15, flow=10.0), _rd(51, flow=12.0)])) == 2
    assert len(w.merge_cross_table_duplicates([_rd(15), _rd(51, temp=121.0)])) == 2
