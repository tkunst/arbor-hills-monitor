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
