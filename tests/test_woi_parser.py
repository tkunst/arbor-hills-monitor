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


# --- gas-sum window (2026-10-17) ---
# The exact row shape of the 2026-10-08 report's AHWW0279 5/31/2024 readings
# (p. 31 and p. 103): gases sum to 97.5 and 97.8, each row followed by its
# comment and an ADJ repeat. The old 100 +/- 1.5 gate dropped all of them.
AHWW0279_531 = [
    "AHWW0279", "5/31/2024 10:43", "58.7", "17.9", "0.3", "20.6", "1.91", "154.2", "36.4",
    "-1.87", "-37.94", "No Change: :",
    "AHWW0279", "5/31/2024 10:43", "ADJ", "58.7", "17.9", "0.3", "20.6", "1.91", "154.2",
    "36.4", "-1.87", "-37.94",
    "AHWW0279", "5/31/2024 10:45", "60.9", "18.1", "0.2", "18.6", "1.81", "154.4", "35.4",
    "-1.85", "-38.48", "No Change,Second Reading: :",
    "AHWW0279", "5/31/2024 10:45", "ADJ", "60.9", "18.1", "0.2", "18.6", "1.81", "154.4",
    "35.4", "-1.85", "-38.48",
]


def test_rows_summing_short_of_100_are_valid():
    rs = w._parse_lines(L(*AHWW0279_531))
    assert [(r.dt, r.adj, r.temp) for r in rs] == [
        ("5/31/2024 10:43", False, 154.2), ("5/31/2024 10:43", True, 154.2),
        ("5/31/2024 10:45", False, 154.4), ("5/31/2024 10:45", True, 154.4)]
    assert all(r.valid for r in rs)
    summ = w.per_well_summary(rs)
    assert summ[0]["well"] == "AHWW0279" and summ[0]["max_temp_f"] == 154.4


def test_short_sum_rows_survive_the_pdf_path(tmp_path):
    import fitz
    path = tmp_path / "r.pdf"
    doc = fitz.open()
    hdr = ("Well ID Date/Time ADJ CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. "
           "Temp. Flow Well Pres. Header Pres. Comments")
    doc.new_page().insert_text((20, 20), "Gas Extraction Report\n" + hdr + "\n"
                               + "\n".join(AHWW0279_531), fontsize=5)
    doc.save(str(path))
    doc.close()
    g = w.parse_gas_extraction_report(str(path))
    assert g.rejected_tables == []
    valid = [r for r in g.readings if r.valid]
    assert len(valid) == 4 and max(r.temp for r in valid if not r.adj) == 154.4


def test_gas_sum_window_still_rejects_shifted_and_overfull_rows():
    # Diff. Press. read into the Balance slot: 58.7+17.9+0.3+1.91 = 78.8.
    shifted = ["AHWW0279", "5/31/2024 10:43", "58.7", "17.9", "0.3", "1.91", "154.2",
               "36.4", "-1.87", "-37.94", "0"]
    assert w._parse_lines(L(*shifted))[0].valid is False
    over = ["AHW1", "1/1/2025 10:00", "50", "40", "2", "10", "0.5", "120", "5", "-1", "-2"]
    assert w._parse_lines(L(*over))[0].valid is False        # 102.0 > GAS_SUM_MAX
    edge_lo = ["AHW1", "1/1/2025 10:00", "50", "40", "0", "4.9", "0.5", "120", "5", "-1", "-2"]
    assert w._parse_lines(L(*edge_lo))[0].valid is False     # 94.9 < GAS_SUM_MIN


# --- unlabeled ADJ rows (2026-10-17) ---
# Attachment 1 of the 2026-10-08 N2688 report (pp8-80) prints no ADJ column:
# the as-found row, a comment, then the after-adjustment row under the SAME
# well + timestamp. The p43 rows below are AHWW0533's: the as-found read is
# 132.7F, the after-adjustment one 133.8F, which used to count as as-found.
HDR_FULL_NO_ADJ = [h for h in HDR_FULL if h != "ADJ"]
AHWW0533_501 = [
    "AHWW0533", "5/1/2024 15:23", "54.8", "44.7", "0", "0.5", "0.17", "132.7", "28.9",
    "0.27", "-4.23", "Opened Valve 1/2 turn to 1 turn:", "95607:",
    "AHWW0533", "5/1/2024 15:23", "54.8", "44.7", "0", "0.5", "0.21", "133.8", "32.1",
    "-0.04", "-2.59",
    "AHWW0533", "5/1/2024 15:26", "54.7", "45.3", "0", "0", "0.12", "133.7", "23.9",
    "-0.32", "-3.3", "Second Reading,Reduced", "Vacuum: :",
    "AHWW0533", "5/1/2024 15:26", "54.7", "45.3", "0", "0", "0.12", "133.7", "23.9",
    "-0.32", "-3.3",
]


def test_unlabeled_header_marks_second_same_time_row_adj():
    rs = w._parse_table_lines(L(*HDR_FULL_NO_ADJ, *AHWW0533_501)).readings
    assert [(r.dt, r.adj, r.temp) for r in rs] == [
        ("5/1/2024 15:23", False, 132.7), ("5/1/2024 15:23", True, 133.8),
        ("5/1/2024 15:26", False, 133.7), ("5/1/2024 15:26", True, 133.7)]
    assert w.per_well_summary(rs)[0]["max_temp_f"] == 133.7


def test_unlabeled_header_alternates_and_leaves_singletons_as_found():
    a = ["AHW1", "1/1/2025 10:00", "50", "40", "0", "10", "0.5", "120", "5", "-1", "-2"]
    b = ["AHW2", "1/1/2025 10:00", "50", "40", "0", "10", "0.5", "121", "5", "-1", "-2"]
    # A pair printed twice reads as-found, ADJ, as-found, ADJ (how the labeled
    # table prints it); a different well at the same time is not a pair.
    rs = w._parse_table_lines(L(*HDR_FULL_NO_ADJ, *a, *a, *a, *a, *b, *a)).readings
    assert [r.adj for r in rs] == [False, True, False, True, False, False]


def test_unlabeled_pair_straddles_a_page_break():
    lines = [(s, 1) for s in HDR_FULL_NO_ADJ + AHWW0533_501[:13]]
    lines += [(s, 2) for s in AHWW0533_501[13:24]]
    rs = w._parse_table_lines(lines).readings
    assert [(r.page, r.adj) for r in rs] == [(1, False), (2, True)]


def test_labeled_header_never_infers_adj():
    rows = [x for x in AHWW0533_501]  # no ADJ tokens, under a header that has ADJ
    rs = w._parse_table_lines(L(*HDR_FULL, *rows)).readings
    assert [r.adj for r in rs] == [False, False, False, False]
    # and the header-less fragment path only trusts the literal token
    assert [r.adj for r in w._parse_lines(L(*rows))] == [False, False, False, False]


def test_adj_rule_resets_on_header_change():
    a = ["AHW1", "1/1/2025 10:00", "50", "40", "0", "10", "0.5", "120", "5", "-1", "-2"]
    # labeled table then unlabeled: the unlabeled table's first row has no
    # predecessor in its own table, so it is as-found.
    rs = w._parse_table_lines(L(*HDR_FULL, *a, *HDR_FULL_NO_ADJ, *a, *a)).readings
    assert [r.adj for r in rs] == [False, False, True]


def test_unlabeled_adj_lets_cross_table_twins_merge():
    # Attachment 1 (no ADJ column) and the labeled back table print the same
    # pair; with the second Attachment-1 row read as ADJ each row merges with
    # its labeled twin instead of both copies reaching Measurements.
    pair_labeled = AHWW0533_501[:13] + AHWW0533_501[13:15] + ["ADJ"] + AHWW0533_501[15:24]
    lines = [(s, 1) for s in HDR_FULL_NO_ADJ + AHWW0533_501[:24]]
    lines += [(s, 2) for s in HDR_FULL + pair_labeled]
    rs = [r for r in w._parse_table_lines(lines).readings if r.valid]
    assert [(r.page, r.adj) for r in rs] == [(1, False), (1, True), (2, False), (2, True)]
    merged = w.merge_cross_table_duplicates(rs)
    assert [(r.adj, r.temp) for r in merged] == [(False, 132.7), (True, 133.8)]


def test_unlabeled_adj_through_the_pdf_path(tmp_path):
    import fitz
    path = tmp_path / "r.pdf"
    doc = fitz.open()
    hdr = ("Well ID Date/Time CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. "
           "Temp. Flow Well Pres. Header Pres. Comments")
    doc.new_page().insert_text((20, 20), "Gas Extraction Report\n" + hdr + "\n"
                               + "\n".join(AHWW0533_501), fontsize=5)
    doc.save(str(path))
    doc.close()
    g = w.parse_gas_extraction_report(str(path))
    assert g.rejected_tables == []
    assert [(r.adj, r.temp) for r in g.readings] == [
        (False, 132.7), (True, 133.8), (False, 133.7), (True, 133.7)]
    assert w.per_well_summary(g.readings)[0]["max_temp_f"] == 133.7


# --- Well ID / Alias tables and "---" cells (ADR 005 addendum 2026-10-19) ---
# Lines as PyMuPDF get_text() prints them in the cached CJ/VGC reports
# (doc-cache 0bbf365b1033... p196, 0f2abeab850a... p187).

HDR_ALIAS = ["Well ID", "Alias", "Date/Time", "ADJ", "CH4 (%)", "CO2 (%)", "O2 (%)", "Balance ",
             "(%)", "Diff. ", "Press. ", "(in. H2O) Temp.", "Flow ", "(scfm)", "Well ",
             "Pres. (in. ", "H2O)", "Header ", "Pres. (in. ", "H2O)", "Comments"]


def _alias(*rows):
    return w._parse_table_lines(L(*HDR_ALIAS, *[x for r in rows for x in r]))


def test_value_and_dash_on_one_line_fills_positions():
    # Temp shares its line with a blank Flow; Header Pres shares its line with
    # the comment. Main stopped at "27.6 ---" and lost Temp's neighbours.
    g = _alias(["AHWFLRIN", "AHWFLRIN", "2/18/2025 15:32", "48.9", "35.2", "1.6", "14.3",
                "0", "27.6 ---", "14.15", "-0.68 No Change: : "])
    r, = g.readings
    assert (r.diff_press, r.temp, r.flow, r.well_pres, r.header_pres) == (0, 27.6, None, 14.15, -0.68)


def test_dash_before_temp_still_reads_temp():
    g = _alias(["AHWFLROT", "AHWFLROT", "2/18/2025 15:36", "49", "35.9", "1.7", "13.4 ---",
                "23.6 ---", "---", "---", "No Change: : "])
    r, = g.readings
    assert (r.balance, r.diff_press, r.temp, r.flow, r.well_pres, r.header_pres) == (
        13.4, None, 23.6, None, None, None)


def test_value_then_comment_on_one_line_ends_the_row():
    nxt = ["AHW1", "AHW1", "1/1/2025 10:00", "50", "40", "0", "10", "0.5", "120", "5", "-1", "-2"]
    g = _alias(["AHW2", "AHW2", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "121", "5",
                "-1", "-2 Opened Valve 1/2 turn:", "7"], nxt)
    a, b = g.readings
    assert (a.flow, a.well_pres, a.header_pres) == (5, -1, -2)  # "7" is not read
    assert b.raw_well_id == "AHW1" and b.temp == 120


def test_comment_starting_with_a_number_cannot_supply_temp():
    # Temp is the last column (gas_temp layout) and isn't printed; the comment
    # "150 ft of water" would complete the row, but a mixed line may only
    # supply columns after Temp. The row ends before Temp: truncated.
    hdr = ["Well ID", "Date/Time", "CH4 (%)", "CO2 (%)", "O2 (%)", "Balance", "Temp.", "Comments"]
    g = w._parse_table_lines(L(*hdr, "AHW1", "1/1/2025 9:00", "50", "40", "0", "10",
                               "150 ft of water in well"))
    assert g.readings == [] and g.truncated_rows == 1


def test_comment_starting_with_a_number_after_a_short_row():
    # The row is short (no Well/Header Pres printed), then a comment that
    # starts with a number. Its cells don't complete the row, so it isn't read.
    # (Residual, see ADR 005 2026-10-19: a comment whose leading numbers
    # exactly complete the row after Temp is still read as those cells.)
    g = _alias(["AHW2", "AHW2", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "121", "5",
                "2 turns open"])
    r, = g.readings
    assert (r.flow, r.well_pres, r.header_pres) == (5, None, None)


def test_unreadable_temp_cell_is_not_a_gas_only_reading():
    # "177.0*" is not a cell the parser reads: the row ends before Temp. That
    # is not a blank Temp, so the row is counted, never emitted without a
    # temperature (main dropped it too).
    g = _alias(["AHW7", "AHW7", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "177.0*",
                "5", "-1", "-2"])
    assert g.readings == [] and g.truncated_rows == 1


def test_unreadable_temp_cell_leaves_unlabeled_adj_as_on_main():
    hdr = [h for h in HDR_ALIAS if h != "ADJ"]
    first = ["AHW7", "AHW7", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "177.0*", "5", "-1", "-2"]
    second = ["AHW7", "AHW7", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "176", "5", "-1", "-2"]
    rs = w._parse_table_lines(L(*hdr, *first, *second)).readings
    assert [(r.adj, r.temp) for r in rs] == [(False, 176.0)]


def test_blank_cells_under_a_non_gas_header_are_not_a_rejected_table():
    hdr = ["Well ID", "Date/Time", "Depth to Liquid", "Depth to Bottom"]
    g = w._parse_table_lines(L(*hdr, "AHW5", "1/1/2025 9:00", "---", "---", "---", "---",
                               "---", "---"))
    assert g.readings == [] and g.rejected_tables == []


def test_blank_alias_cell():
    g = _alias(["AHW8", "---", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "120", "5", "-1", "-2"])
    r, = g.readings
    assert (r.well_id, r.alias_as_printed, r.temp) == ("AHW8", None, 120)


def test_alias_label_change_starts_a_new_table():
    row = ["AHWTR022", "AHWTRO22", "3/2/2023 11:07", "54.6", "40.4", "0", "5", "0", "90",
           "10", "-1", "-2"]
    # Same columns without the Alias label: that is a different table, read the
    # old way (the line next to the timestamp is the well).
    rs = w._parse_table_lines(L(*HDR_ALIAS, *row, *HDR_FULL, *row)).readings
    assert [(r.well_id, r.alias_as_printed) for r in rs] == [
        ("AHWTR022", "AHWTRO22"), ("AHWTRO22", None)]


def test_gas_only_reading_is_kept_with_no_temperature():
    g = _alias(["AHW3", "AHW3", "1/1/2025 9:00", "50", "40", "0", "10", "---", "---", "---",
                "---", "---"])
    r, = g.readings
    assert r.valid and r.temp is None and r.o2 == 0
    assert [m["metric"] for m in w.to_measurements(r)] == ["oxygen", "methane"]
    assert w.per_well_summary(g.readings)[0]["max_temp_f"] is None


def test_all_dash_row_is_counted_not_emitted():
    g = _alias(["AHW264R3", "AHW264R3", "10/25/2023 8:38", "---", "---", "---", "---"],
               ["AHW1", "AHW1", "1/1/2025 10:00", "50", "40", "0", "10", "0.5", "120", "5", "-1", "-2"])
    assert [r.raw_well_id for r in g.readings] == ["AHW1"]
    assert g.blank_rows == 1 and g.partial_rows == 0


def test_partial_gas_row_is_counted_not_emitted():
    g = _alias(["AHW4", "AHW4", "1/1/2025 9:00", "50", "---", "0", "10", "0.5", "120"])
    assert g.readings == [] and g.partial_rows == 1


def test_non_ah_alias_row_is_read_under_its_well_id():
    g = _alias(["AHEW0008", "EW-008", "3/2/2023 11:07", "54.6", "40.4", "0", "5", "0", "90",
                "10", "-1", "-2"])
    r, = g.readings
    assert (r.well_id, r.raw_well_id, r.alias_as_printed, r.temp) == ("AHEW0008", "AHEW0008", "EW-008", 90)


def test_letter_o_alias_does_not_become_the_well():
    g = _alias(["AHWTR022", "AHWTRO22", "3/2/2023 11:07", "54.6", "40.4", "0", "5", "0", "90",
                "10", "-1", "-2"])
    r, = g.readings
    assert (r.well_id, r.alias_as_printed) == ("AHWTR022", "AHWTRO22")


def test_first_row_after_an_alias_header_keeps_its_well_id():
    # The header ends at the Well ID line, not at the alias (which is also
    # followed by the timestamp), so the first row is not read under its alias.
    hdr_text, j = w._read_header(L(*HDR_ALIAS, "AHWTR022", "AHWTRO22", "3/2/2023 11:07"), 0)
    assert "AHWTR022" not in hdr_text and j == len(HDR_ALIAS)


def test_alias_row_unlabeled_adj_keys_on_well_id():
    hdr = [h for h in HDR_ALIAS if h != "ADJ"]
    row = ["AHWTR022", "AHWTRO22", "3/2/2023 11:07", "54.6", "40.4", "0", "5", "0", "90",
           "10", "-1", "-2"]
    rs = w._parse_table_lines(L(*hdr, *row, *row)).readings
    assert [(r.well_id, r.adj) for r in rs] == [("AHWTR022", False), ("AHWTR022", True)]


def test_table_without_alias_label_reads_as_before():
    # No Alias label: a three-line well/alias/date run is NOT read as Well ID +
    # Alias; the line next to the timestamp is the well, as before.
    rs = w._parse_table_lines(L(*HDR_FULL, "AHW9", *HOT)).readings
    assert [r.raw_well_id for r in rs] == ["AHW272R4"] and rs[0].alias_as_printed is None
    # ...including the first row right after the header.
    rs = w._parse_table_lines(L(*HDR_FULL, "AHW9", "AHW272R4", *HOT[1:])).readings
    assert [r.raw_well_id for r in rs] == ["AHW272R4"]


def test_alias_table_through_the_pdf_path(tmp_path):
    import fitz
    path = tmp_path / "r.pdf"
    doc = fitz.open()
    hdr = ("Well ID Alias Date/Time ADJ CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press. "
           "Temp. Flow Well Pres. Header Pres. Comments")
    rows = ["AHEW0008", "EW-008", "2/18/2025 15:32", "48.9", "35.2", "1.6", "14.3", "0",
            "27.6 ---", "14.15", "-0.68 No Change: :",
            "AHW3", "AHW3", "2/18/2025 15:40", "50", "40", "0", "10", "--- ---", "---", "---", "---",
            "AHW264R3", "AHW264R3", "10/25/2023 8:38", "---", "---", "---", "---"]
    doc.new_page().insert_text((20, 20), "Gas Extraction Report\n" + hdr + "\n" + "\n".join(rows),
                               fontsize=5)
    doc.save(str(path))
    doc.close()
    g = w.parse_gas_extraction_report(str(path))
    assert g.rejected_tables == [] and g.blank_rows == 1
    assert [(r.well_id, r.alias_as_printed, r.temp, r.header_pres) for r in g.readings] == [
        ("AHEW0008", "EW-008", 27.6, -0.68), ("AHW3", "AHW3", None, None)]


def test_gas_only_row_is_never_the_as_found_partner():
    hdr = [h for h in HDR_ALIAS if h != "ADJ"]
    gas_only = ["AHW1", "AHW1", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "---", "5", "-1",
                "-2 Opened valve:"]
    real = ["AHW1", "AHW1", "1/1/2025 9:00", "50", "40", "0", "10", "0.5", "178", "5", "-1", "-2"]
    rs = w._parse_table_lines(L(*hdr, *gas_only, *real)).readings
    assert [(r.adj, r.temp) for r in rs] == [(False, None), (False, 178.0)]
    assert w.per_well_summary(rs)[0]["max_temp_f"] == 178.0


def test_numbers_past_a_blank_cell_do_not_make_a_non_gas_table_look_gas():
    hdr = ["Well ID", "Date/Time", "Depth to Liquid", "Depth to Bottom"]
    g = w._parse_table_lines(L(*hdr, "AHW5", "1/1/2025 9:00", "12.5", "---", "202", "3", "---",
                               "4", "5", "6"))
    assert g.rejected_tables == []


# --- Round-3 review: rows main read keep main's behavior exactly -------------

H9 = "Well ID CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press Temp. Flow Well Pres Header Pres"
D = "5/31/2024 10:00"


def _row9(well, diff, temp, flow):
    return [well, D, "50.0", "30.0", "0.5", "19.5", diff, temp, flow, "-5.0", "-6.0"]


def test_recovered_rows_never_trip_the_negative_flow_check():
    # 20 rows main read (flow 9) + 8 rows main dropped (Diff Press "---",
    # flow -2). Only main's flows are checked, so the table is kept whole.
    lines = [H9]
    for k in range(20):
        lines += _row9(f"AHWW{k:03d}", "-1.0", "120.0", "9.0")
    for k in range(8):
        lines += _row9(f"AHWX{k:03d}", "---", "120.0", "-2.0")
    g = w._parse_table_lines(L(*lines))
    assert len(g.readings) == 28 and g.rejected_tables == []


def test_recovered_rows_never_dilute_a_shifted_table():
    # A shifted table (21 of 25 main-read flows negative) stays rejected even
    # when many recovered rows with a positive flow are added.
    lines = [H9]
    for k in range(25):
        lines += _row9(f"AHWW{k:03d}", "-1.0", "120.0", "-3.0" if k < 21 else "4.0")
    for k in range(60):
        lines += _row9(f"AHWX{k:03d}", "---", "120.0", "5.0")
    g = w._parse_table_lines(L(*lines))
    assert g.readings == [] and "column-shift" in g.rejected_tables[0].reason


def test_recovered_row_does_not_change_main_rows_adj_flags():
    hdr = H9  # no ADJ label: the unlabeled-ADJ rule applies
    # A recovered as-found row (Diff Press "---") before main's row: main's
    # 120 row stays as-found.
    rs = w._parse_table_lines(L(hdr, *_row9("AHWW001", "---", "150.0", "10.0"), "Opened valve",
                                *_row9("AHWW001", "-1.0", "120.0", "9.0"))).readings
    assert [(r.temp, r.adj) for r in rs] == [(150.0, False), (120.0, False)]
    # A gas-only row of another well between main's pair: 120 stays ADJ.
    rs = w._parse_table_lines(L(hdr, *_row9("AHWW001", "-1.0", "150.0", "10.0"),
                                *_row9("AHWW002", "-1.0", "---", "9.0"),
                                *_row9("AHWW001", "-1.0", "120.0", "9.0"))).readings
    assert [(r.raw_well_id, r.temp, r.adj) for r in rs] == [
        ("AHWW001", 150.0, False), ("AHWW002", None, False), ("AHWW001", 120.0, True)]


def test_alias_in_header_prose_is_not_an_alias_column():
    lines = [H9 + " (alias per EGLE letter)", *_row9("AHWW001", "-1.0", "120.0", "9.0"),
             "AHWW777", *_row9("AHWW002", "-1.0", "130.0", "9.0")]
    rs = w._parse_table_lines(L(*lines)).readings
    assert [(r.raw_well_id, r.temp) for r in rs] == [("AHWW001", 120.0), ("AHWW002", 130.0)]


def test_well_shaped_comment_outside_the_well_column_cannot_take_a_row():
    # Alias table; the second row's alias prints nothing, and a comment line
    # naming a well sits right before it. With positions, the comment (x=500)
    # is not in the Well ID column (x=20), so the row keeps its own Well ID.
    lines = L(*HDR_ALIAS, "AHWW001", "AHWW001", D, "50", "30", "0.5", "19.5", "-1", "120", "9",
              "-5", "-6 Redrilled as", "AHWW777", "AHWW002", D, "50", "30", "0.5", "19.5", "-1",
              "130", "9", "-5", "-6")
    pos = [None] * len(lines)
    for k, (t, _) in enumerate(lines):
        if t in ("AHWW001", "AHWW002"):
            pos[k] = 20.0
        elif t == "AHWW777":
            pos[k] = 500.0
    pos[len(HDR_ALIAS) + 1] = 60.0  # the first row's alias column
    rs = w._parse_table_lines(lines, positions=pos).readings
    assert [(r.raw_well_id, r.temp) for r in rs] == [("AHWW001", 120.0), ("AHWW002", 130.0)]


def test_comment_number_never_fills_flow():
    h7 = "Well ID CH4 (%) CO2 (%) O2 (%) Balance (%) Diff. Press Temp. Flow"
    rs = w._parse_table_lines(L(h7, "AHWW001", D, "50.0", "30.0", "0.5", "19.5", "-1.0", "120.0",
                                "150 ft of water")).readings
    assert rs[0].flow is None


def test_unlabeled_second_id_column_keeps_mains_header_end():
    # A header with a second id column NOT labeled Alias: the header ends
    # where main ended it, so the table's ADJ labeling is main's.
    hdr = ["Well ID", "Name", "Date/Time", "CH4 (%)", "CO2 (%)", "O2 (%)", "Balance (%)",
           "Diff. Press", "Temp.", "Flow", "Well Pres", "Header Pres"]
    row1 = ["AHWW001", "EW-8", D, "ADJ", "50", "30", "0.5", "19.5", "-1", "120", "9", "-5", "-6"]
    rest = [*_row9("AHWW002", "-1.0", "140.0", "9.0"), *_row9("AHWW002", "-1.0", "130.0", "9.0")]
    rs = w._parse_table_lines(L(*hdr, *row1, *rest)).readings
    assert [(r.temp, r.adj) for r in rs] == [(140.0, False), (130.0, False)]


def test_unprinted_cell_before_a_blank_never_shifts_into_temp():
    # Diff Press printed nothing; Temp prints "---". The row has 8 cells for 9
    # columns, so positions after the first blank are unknown: it is not read
    # as Temp = 12.0 (really the Flow value).
    g = w._parse_table_lines(L(H9, "AHWW001", D, "50.0", "30.0", "0.5", "19.5", "---", "12.0",
                               "-5.0", "-6.0"))
    assert g.readings == [] and g.truncated_rows == 1
