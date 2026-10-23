"""ADR 071 — report_tracker.py, report_tracker_seed.py and the briefing
summary. Hermetic: the Sheets API is a dict-backed fake."""
import importlib.util
import os
import re
from datetime import date

import pytest

import report_tracker as rt
import report_tracker_seed as seed
import sheet_writer as sw

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "report_tracker_summary", os.path.join(_REPO, "scripts", "report_tracker_summary.py"))
rts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rts)

TODAY = date(2026, 10, 23)


# ---------------------------------------------------------------------------
# Periods + due rules (the ADR's edge cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("s,kind,bounds", [
    ("2025-10", "M", (date(2025, 10, 1), date(2025, 10, 31))),
    ("2022B1", "B", (date(2022, 1, 1), date(2022, 2, 28))),
    ("2025Q3", "Q", (date(2025, 7, 1), date(2025, 9, 30))),
    ("2025H2", "H", (date(2025, 7, 1), date(2025, 12, 31))),
    ("2024", "Y", (date(2024, 1, 1), date(2024, 12, 31))),
    ("2023-06-12", "D", (date(2023, 6, 12), date(2023, 6, 12))),
])
def test_period_round_trip_and_bounds(s, kind, bounds):
    p = rt.parse_period(s)
    assert p.kind == kind and str(p) == s and p.bounds() == bounds


@pytest.mark.parametrize("bad", ["2025Q5", "2022B7", "2025H3", "25Q1", "2025-13", "Q1 2025", ""])
def test_bad_period_rejected(bad):
    with pytest.raises(ValueError):
        rt.parse_period(bad)


def test_period_next_rolls_the_year():
    assert str(rt.parse_period("2025H2").next()) == "2026H1"
    assert str(rt.parse_period("2025Q4").next()) == "2026Q1"
    assert str(rt.parse_period("2022B6").next()) == "2023B1"
    assert str(rt.parse_period("2025-12").next()) == "2026-01"
    assert str(rt.parse_period("2024").next()) == "2025"


def test_period_ending_before():
    assert str(rt.period_ending_before("B", date(2022, 3, 15))) == "2022B1"
    assert str(rt.period_ending_before("Q", date(2024, 1, 15))) == "2023Q4"
    assert str(rt.period_ending_before("Q", date(2024, 7, 30))) == "2024Q2"
    assert str(rt.period_ending_before("H", date(2026, 1, 15))) == "2025H2"
    assert str(rt.period_ending_before("M", date(2025, 11, 13))) == "2025-10"
    assert str(rt.period_ending_before("Y", date(2025, 3, 10))) == "2024"


def test_due_rule_fixed_semiannual_federal_dates():
    """fixed:03-15,09-15 — H1 (ends 6/30) is due 9/15 the same year, H2
    (ends 12/31) rolls to 3/15 the next year."""
    rule = rt.parse_due_rule("fixed:03-15,09-15")
    assert rt.due_date(rule, rt.parse_period("2025H1")) == date(2025, 9, 15)
    assert rt.due_date(rule, rt.parse_period("2025H2")) == date(2026, 3, 15)
    # Annual certification: fixed:03-15 for CY2024 -> 2025-03-15.
    assert rt.due_date(rt.parse_due_rule("fixed:03-15"), rt.parse_period("2024")) == date(2025, 3, 15)
    # WOI semi-annual: fixed:01-15,07-15 -> H1 due 7/15, H2 due 1/15 next year.
    woi = rt.parse_due_rule("fixed:01-15,07-15")
    assert rt.due_date(woi, rt.parse_period("2024H2")) == date(2025, 1, 15)
    assert rt.due_date(woi, rt.parse_period("2025H1")) == date(2025, 7, 15)


def test_due_rule_period_end_plus_days():
    rule = rt.parse_due_rule("period_end+45d")
    assert rt.due_date(rule, rt.parse_period("2023H2")) == date(2024, 2, 14)
    assert rt.due_date(rule, rt.parse_period("2026H1")) == date(2026, 8, 14)
    assert rt.due_date(rt.parse_due_rule("period_end+30d"), rt.parse_period("2025Q4")) == date(2026, 1, 30)


def test_due_rule_next_month_day():
    rule = rt.parse_due_rule("next_month_day:15")
    assert rt.due_date(rule, rt.parse_period("2025-10")) == date(2025, 11, 15)
    assert rt.due_date(rule, rt.parse_period("2025-12")) == date(2026, 1, 15)   # year roll
    assert rt.due_date(rule, rt.parse_period("2022B1")) == date(2022, 3, 15)    # bi-month
    assert rt.due_date(rule, rt.parse_period("2023Q4")) == date(2024, 1, 15)
    # Day clamped to the month's length.
    assert rt.due_date(rt.parse_due_rule("next_month_day:31"), rt.parse_period("2025-01")) == date(2025, 2, 28)


def test_due_rule_on_and_within():
    assert rt.due_date(rt.parse_due_rule("on:2023-06-12"), rt.parse_period("2023-06-12")) == date(2023, 6, 12)
    w = rt.parse_due_rule("within:24h")
    assert w.days == 1 and w.hours == 24
    assert rt.due_date(w, rt.parse_period("2025"), trigger=date(2025, 3, 3)) == date(2025, 3, 4)
    assert rt.due_date(rt.parse_due_rule("within:7d"), rt.parse_period("2025"), trigger=date(2025, 3, 3)) == date(2025, 3, 10)
    with pytest.raises(ValueError):
        rt.due_date(w, rt.parse_period("2025"))  # within: needs a trigger


@pytest.mark.parametrize("bad", ["period_end+45", "fixed:13-01", "fixed:02-30", "next_month_day:0",
                                 "on:2023/06/12", "within:2w", "monthly", ""])
def test_bad_due_rule_rejected(bad):
    with pytest.raises(ValueError):
        rt.parse_due_rule(bad)


# ---------------------------------------------------------------------------
# Match rules + period extraction: the ADR fixtures and real Sheet titles
# ---------------------------------------------------------------------------

def _schedules():
    facs, bad = rt.load_facilities(seed.FACILITIES)
    assert not bad
    scheds, bad = rt.load_schedules(seed.EXPECTED, {f.facility_id for f in facs})
    assert not bad
    return facs, scheds


def _rec(title, stream="nsite", facility="Arbor Hills Landfill", **kw):
    return rt.Record(stream, kw.pop("ref", "x"), title, facility, **kw)


@pytest.mark.parametrize("title,stream,facility,rtype,period", [
    # The ADR's fixtures, verbatim.
    ("2025 Third Quarter Report - CJ No. 2020-0593-CE", "nsite", "Arbor Hills Landfill", "cj-quarterly-probe", "2025Q3"),
    ("2023 Second Quarter Report - CJ No. 2020-0593-CE", "nsite", "Arbor Hills Landfill", "cj-quarterly-probe", "2023Q2"),
    ("2026 First Semi-Annual WOI Status Report", "nsite", "Arbor Hills Landfill", "woi-status", "2026H1"),
    ("AAAA Semi-Annual NESHAP 070125 through 123125", "hand-curated", "N2688", "nsps-neshap-semiannual", "2025H2"),
    ("Carleton Farms Landfill (CFL) HOV Traditional Well Monthly Status Report (October 2025)", "hand-curated", "",
     "hov-traditional-monthly", "2025-10"),
    # Real display titles from the live case-file Sheet (read 2026-10-23).
    ("Fourth Quarter Report CJ 2024", "nsite", "Arbor Hills Landfill", "cj-quarterly-probe", "2024Q4"),
    ("GFL Consent Judgment report: 2026 Second Quarter (CJ No. 2020-0593-CE)", "nsite", "Arbor Hills Landfill",
     "cj-quarterly-probe", "2026Q2"),
    ("Arbor Hills Landfill Q3 2025 Consent Judgment compliance report to EGLE, including SCS Engineers VGC well "
     "liquid monitoring report, dated 10/30/2025", "nsite", "Arbor Hills Landfill", "cj-quarterly-probe", "2025Q3"),
    ("Arbor Hills Landfill Inc. 2024 Q3 Consent Judgment quarterly report to EGLE Air Quality Division, with "
     "leachate volume summary and lab results, dated 10/30/2024", "nsite", "Arbor Hills Landfill", "cj-quarterly-probe", "2024Q3"),
    ("Arbor Hills Landfill, Inc. quarterly Consent Judgment compliance report to EGLE Air Quality Division with "
     "SCS Engineers VGC well liquid monitoring report, dated 7/30/2024", "nsite", "Arbor Hills Landfill",
     "cj-quarterly-probe", "2024Q2"),
    ("Consent Judgment Q2 2025 Quarterly Compliance Report (CJ No. 2020-0593-CE), Arbor Hills", "hand-curated", "N2688",
     "cj-quarterly-probe", "2025Q2"),
    ("GFL/Arbor Hills Landfill semi-annual WOI Status Report to EGLE on wellhead temperature and gas data, "
     "covering July–December 2025, dated 1/15/2026", "nsite", "Arbor Hills Landfill", "woi-status", "2025H2"),
    ("GFL Arbor Hills Landfill semi-annual WOI Status Report to EGLE on elevated-temperature wells, dated 7/15/2025",
     "nsite", "Arbor Hills Landfill", "woi-status", "2025H1"),
    ("GFL Arbor Hills Landfill HOV Wells-of-Interest Status Report to EGLE for Second Semi-Annual 2024, dated 1/6/2025",
     "nsite", "Arbor Hills Landfill", "woi-status", "2024H2"),
    ("GFL Arbor Hills Landfill HOV WOI Quarterly Status Report to EGLE on Q1 2024 wellfield temperature and gas data, "
     "dated 4/15/2024", "nsite", "Arbor Hills Landfill", "woi-status", "2024Q1"),
    ("GFL Arbor Hills Landfill HOV Wells-of-Interest quarterly status report to EGLE, Q2 2023, dated 7/14/2023",
     "nsite", "Arbor Hills Landfill", "woi-status", "2023Q2"),
    ("GFL Arbor Hills Landfill quarterly HOV Wells-of-Interest status report to EGLE, dated 4/15/2024",
     "nsite", "Arbor Hills Landfill", "woi-status", "2024Q1"),
    ("Arbor Hills Landfill gas-well and Wells-of-Interest (WOI) status report covering July–September 2023 "
     "temperature, CH4/CO2/O2, CO and hydrogen readings", "nsite", "Arbor Hills Landfill", "woi-status", "2023Q3"),
    ("GFL Arbor Hills Landfill bi-monthly HOV Wells-of-Interest status report to EGLE for March–April 2022, "
     "dated 5/12/2022", "nsite", "Arbor Hills Landfill", "woi-status", "2022B2"),
    ("GFL Arbor Hills Landfill HOV Wells-of-Interest Bi-Monthly Status Report to EGLE for March-April 2023, "
     "dated 5/11/2023", "nsite", "Arbor Hills Landfill", "woi-status", "2023B2"),
    ("Arbor Hills Landfill HOV WOI Bi-Monthly Status Report -- May and June 2022", "hand-curated", "N2688",
     "woi-status", "2022B3"),
    ("Arbor Hills Landfill HOV WOI Status Report -- 2026 First Semi-Annual (Jan-Jun 2026)", "hand-curated", "N2688",
     "woi-status", "2026H1"),
    ("Arbor Hills Landfill HOV WOI Quarterly Status Report -- First Quarter 2024", "hand-curated", "N2688",
     "woi-status", "2024Q1"),
    ("Arbor Hills Landfill, Inc. 1st Semi-Annual 2026 NESHAP (40 CFR 63 Subpart AAAA) compliance report to EGLE "
     "Jackson District Office, dated 9/15/2026", "nsite", "Arbor Hills Landfill", "nsps-neshap-semiannual", "2026H1"),
    ("Arbor Hills Landfill Semi-Annual NESHAP Report -- 2025 First Semi-Annual (Jan 1 - Jun 30 2025); GFL transmittal",
     "hand-curated", "N2688", "nsps-neshap-semiannual", "2025H1"),
    ("Ridge Wood Elementary H2S 24-hr averages — May 2026", "ridgewood", "", "ridgewood-monthly", "2026-05"),
])
def test_fixture_titles_match_high(title, stream, facility, rtype, period):
    facs, scheds = _schedules()
    rec = _rec(title, stream, facility, period_hint="2026-05" if stream == "ridgewood" else "")
    m = rt.match_record(rec, scheds, facs)
    assert m is not None, title
    assert (m.confidence, m.report_type, m.period) == ("high", rtype, period), (m.detail, title)


def test_fixture_plan_document_is_not_a_periodic_report():
    """`N5986 CFL HOV Plan rev. February 2025` is the plan text (one-time),
    never the monthly report, and never an 'unmatched periodic' title."""
    facs, scheds = _schedules()
    periodic = re.compile(r"\bReport\b|Semi-?Annual|Quarter|Monthly", re.I)
    rec = _rec("N5986 CFL HOV Plan rev. February 2025", "hand-curated", "N5986")
    m = rt.match_record(rec, scheds, facs)
    assert m is None or m.report_type != "hov-traditional-monthly"
    assert not rt.looks_periodic(rec.title, periodic)


@pytest.mark.parametrize("title", [
    "On-Site Inspection (08/19/2026)",             # EGLE's own evaluation
    "N5986 CFL HOV Map June 2026",                 # an attachment, not the AOI report
    "Schedule - CJ-2020-0593-CE General Report",   # nSITE schedule placeholder
    "Email follow-up on CJ report review comments/clarification. 2025 Annual CJ report",
])
def test_negative_fixtures_never_become_arrivals(title):
    facs, scheds = _schedules()
    periodic = re.compile(r"\bReport\b|Semi-?Annual|Quarter|Monthly", re.I)
    agency = re.compile(r"On-Site Inspection|Inspection \(|\bEvaluation\b|^Schedule - |\bEmail\b", re.I)
    rec = _rec(title, "hand-curated", "N5986" if "N5986" in title else "N2688")
    if agency.search(title):
        return  # skipped before any rule is tried (Decision 4)
    m = rt.match_record(rec, scheds, facs)
    assert m is None or m.confidence != "high", (m.report_type, m.period, title)
    if title.startswith("N5986 CFL HOV Map"):
        assert not rt.looks_periodic(title, periodic)


def test_agency_regex_from_config_blocks_inspections_but_not_hov_approvals():
    from config_loader import load_config
    cfg = load_config()["report_tracking"]
    agency = re.compile(cfg["agency_title_regex"], re.I)
    assert agency.search("On-Site Inspection (08/19/2026)")
    assert agency.search("Schedule - CJ-2020-0593-CE General Report")
    assert agency.search("Violation Notice VN-011821")
    assert not agency.search("EGLE AQD approval of HOV temperature waiver - well AHW272R4 at 180 deg F")
    assert not agency.search("GFL Consent Judgment report: 2026 Second Quarter (CJ No. 2020-0593-CE)")


def test_dual_period_letter_is_an_arrival_outside_the_window():
    """The 7/9/2024 letter is filed as the 'first half 2024' semi-annual
    report but the semi-annual series starts 2024H2 (the quarterly series
    ends 2024Q2). Its title names the period, so it IS a WOI arrival (keyed
    2024H1, noted as outside the schedule's window, never an expected
    instance); the seed pins the same document to 2024Q2 by hand."""
    facs, scheds = _schedules()
    m = rt.match_record(_rec("GFL Arbor Hills Landfill letter to EGLE: Semi-annual Wells-of-Interest HOV status "
                             "report for first half 2024, dated 7/9/2024"), scheds, facs)
    assert m is not None and (m.confidence, m.report_type, m.period) == ("high", "woi-status", "2024H1")
    assert "outside" in m.detail


def test_date_fallback_outside_window_is_proposed():
    """An inactive 2019-2021 WOI row must not claim a 2025 title through its
    date-based fallback extractor: without a title period, outside the
    window, the answer is PROPOSED."""
    facs, scheds = _schedules()
    only_old = [s for s in scheds if s.schedule_id == "ah-woi-2019-2021"]
    m = rt.match_record(_rec("GFL Arbor Hills Landfill WOI Status Report to EGLE", doc_date="2025-12-20"), only_old, facs)
    assert m is not None and m.confidence == "proposed" and "window" in m.detail


def test_title_period_outside_window_is_a_plain_arrival_with_note():
    """A 2004 QMR read off the WDS title is a QMR: HIGH, noted as outside
    the schedule's window, never a hand decision."""
    facs, scheds = _schedules()
    m = rt.match_record(rt.Record("wds", "w", "QMR groundwater report (due 10/30/2004) — Statistical Exceedence: Yes"),
                        scheds, facs)
    assert m is not None and (m.confidence, m.report_type, m.period) == ("high", "hmp-quarterly", "2004Q3")
    assert "outside" in m.detail
    m = rt.match_record(rt.Record("wds", "w", "Annual Landfill Report — FY1999 — Total waste 4,071,959.00 CYDS"),
                        scheds, facs)
    assert m is not None and (m.confidence, m.report_type, m.period) == ("high", "annual-landfill-report", "1999")


def test_hov_letters_become_dated_arrivals():
    facs, scheds = _schedules()
    m = rt.match_record(_rec("EGLE AQD approval of HOV temperature waiver - well AHW272R4 at 180 deg F, February 19, 2025",
                             "hand-curated", "N2688"), scheds, facs)
    assert m is not None and (m.confidence, m.report_type, m.period) == ("high", "hov-approval", "2025-02-19")
    m = rt.match_record(_rec("EGLE AQD grant -- two-year renewal of HOV temperature waivers for 15 WOI wells/sumps",
                             "hand-curated", "N2688", doc_date="2024-04-30"), scheds, facs)
    assert m is not None and (m.confidence, m.report_type, m.period) == ("high", "hov-approval", "2024-04-30")
    # The statewide landfilled-waste report is not the facility's annual report.
    m = rt.match_record(_rec("EGLE Report of Solid Waste Landfilled in Michigan - Fiscal Year 2024 (Part 115 Sec. 11512(3)) annual report",
                             "hand-curated", "N2688"), scheds, facs)
    assert m is None or m.report_type != "annual-landfill-report"


def test_period_not_found_is_proposed_never_high():
    facs, scheds = _schedules()
    m = rt.match_record(_rec("GFL Arbor Hills Landfill HOV Wells-of-Interest Quarterly Status Report (period not stated)"),
                        scheds, facs)
    assert m is not None and m.confidence == "proposed" and "period" in m.detail


def test_unresolved_facility_is_proposed():
    facs, scheds = _schedules()
    m = rt.match_record(rt.Record("govqa", "file:E1:x#1", "HOV Traditional Well Monthly Status Report (March 2024)"),
                        scheds, facs)
    assert m is not None and m.confidence == "proposed" and "facility" in m.detail


def test_quarterly_schedule_never_accepts_a_half_period():
    """A quarterly rule's regex may hit a semi-annual title; the kind check
    and the exclude_regex keep it from inventing a quarter."""
    facs, scheds = _schedules()
    q = next(s for s in scheds if s.schedule_id == "ah-woi-quarterly")
    rec = _rec("GFL Arbor Hills Landfill HOV Wells-of-Interest semi-annual status report to EGLE for Second Semi-Annual 2024")
    assert rt.period_from_rule(q.match_rule, rec, "Q") == (None, "")


@pytest.mark.parametrize("name,title,expected", [
    ("month_range", "covering July–December 2025, dated 1/15/2026", "2025H2"),
    ("month_range", "status report covering Jan–Mar 2024 well monitoring data", "2024Q1"),
    ("month_range", "Bi-Monthly Status Report -- November and December 2022", "2022B6"),
    ("month_range", "(Jan 1 - Jun 30 2025)", "2025H1"),
    ("month_range", "Sept-Oct 2022", "2022B5"),
    ("month_year", "Monthly Status Report (October 2025)", "2025-10"),
    ("date_range", "AAAA Semi-Annual NESHAP 010125 through 063025", "2025H1"),
    ("year_half_words", "REVISED AHE 1H26 Semi Annual Report", "2026H1"),
    ("year_half_words", "HOV status report for first half 2024", "2024H1"),
    ("year_quarter_words", "Fourth Quarter Report CJ 2024", "2024Q4"),
    ("year_in_title", "ROP Annual Compliance Certification CY2024", "2024"),
    ("year_in_title", "Annual Landfill Report — FY1999", "1999"),
    ("date_in_title", "approval letter, February 19, 2025", "2025-02-19"),
    ("date_in_title", "EGLE grant dated 4/30/2024", "2024-04-30"),
    ("prior_quarter_from_date", "QMR groundwater report (due 1/30/2003)", "2002Q4"),
])
def test_extractors(name, title, expected):
    assert str(rt.extract_period(name, rt.Record("nsite", "r", title))) == expected


def test_month_year_does_not_read_the_end_of_a_range():
    assert rt.extract_period("month_year", rt.Record("nsite", "r", "July–December 2025")) is None
    assert rt.extract_period("month_year", rt.Record("nsite", "r", "(Jan–Jun 2026)")) is None


def test_dated_in_title_beats_wrong_document_date():
    rec = rt.Record("nsite", "r", "quarterly Consent Judgment report, dated 7/30/2024", doc_date="2022-04-27")
    assert str(rt.extract_period("prior_quarter_from_date", rec)) == "2024Q2"


@pytest.mark.parametrize("bad", [
    "stream=nsite; period_from=auto",                       # no regex
    "stream=ftp; title_regex=x",                            # unknown stream
    "stream=nsite; title_regex=(unclosed",                  # bad regex
    "stream=nsite; title_regex=x; period_from=magic",       # unknown extractor
    "stream=nsite; title_regex=x; colour=blue",             # unknown key
    "",
])
def test_bad_match_rule_rejected(bad):
    with pytest.raises(ValueError):
        rt.parse_match_rule(bad)


# ---------------------------------------------------------------------------
# Hand-table loading: a bad row is skipped, never guessed
# ---------------------------------------------------------------------------

def test_bad_schedule_rows_are_reported_not_guessed():
    rows = [
        ["ok", "arbor-hills", "woi-status", "n", "", "secondary", "AQD", "quarterly", "calendar",
         "next_month_day:15", "", "2023Q2", "", "", "stream=nsite; title_regex=WOI", "Y"],
        ["bad-due", "arbor-hills", "woi-status", "n", "", "", "", "quarterly", "", "every 3 months", "", "2023Q2"],
        ["bad-type", "arbor-hills", "woi-reports", "n", "", "", "", "quarterly", "", "next_month_day:15", "", "2023Q2"],
        ["bad-period", "arbor-hills", "woi-status", "n", "", "", "", "quarterly", "", "next_month_day:15", "", "2023H2"],
        ["bad-fac", "mars", "woi-status", "n", "", "", "", "quarterly", "", "next_month_day:15", "", "2023Q2"],
        ["bad-within", "arbor-hills", "woi-status", "n", "", "", "", "quarterly", "", "within:7d", "", "2023Q2"],
        ["ok", "arbor-hills", "woi-status", "dup", "", "", "", "quarterly", "", "next_month_day:15", "", "2023Q2"],
    ]
    scheds, bad = rt.load_schedules(rows, {"arbor-hills"})
    assert [s.schedule_id for s in scheds] == ["ok"]
    assert sorted(b[0] for b in bad) == ["bad-due", "bad-fac", "bad-period", "bad-type", "bad-within", "ok"]


def test_trigger_condition_grammar():
    terms = rt.parse_condition("wellhead_temp_f >= 170 AND co_ppmv >= 1000 AND no_hov(well)")
    assert terms == (("wellhead_temp_f", ">=", 170.0), ("co_ppmv", ">=", 1000.0), ("no_hov",))
    with pytest.raises(ValueError):
        rt.parse_condition("probe_lel_pct > 100")   # not in v1
    with pytest.raises(ValueError):
        rt.parse_condition("no_hov(well)")          # needs a temperature term


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

def _sched(**kw):
    base = dict(schedule_id="s", facility_id="f", report_type="woi-status", report_name="WOI",
                cadence="quarterly", due_rule=rt.parse_due_rule("next_month_day:15"), grace_days=None,
                first_period=rt.parse_period("2023Q2"), last_period=None, match_rule=None, active=True)
    base.update(kw)
    return rt.Schedule(**base)


def test_expand_semiannual_fixed_dates_through_today_plus_lookahead():
    s = _sched(cadence="semiannual", due_rule=rt.parse_due_rule("fixed:03-15,09-15"),
               first_period=rt.parse_period("2019H1"))
    inst = rt.expand_schedule(s, TODAY, 7, 30)
    # 2019H1 (due 2019-09-15) ... 2026H1 (due 2026-09-15); 2026H2 is due 2027-03-15, beyond the horizon.
    assert [i.period for i in inst][:2] == ["2019H1", "2019H2"]
    assert inst[-1].period == "2026H1" and inst[-1].due == date(2026, 9, 15)
    assert len(inst) == 15
    assert all(i.grace == 7 for i in inst)


def test_expand_respects_last_period_and_row_grace_override():
    s = _sched(last_period=rt.parse_period("2024Q2"), grace_days=0)
    inst = rt.expand_schedule(s, TODAY, 7, 30)
    assert [i.period for i in inst] == ["2023Q2", "2023Q3", "2023Q4", "2024Q1", "2024Q2"]
    assert all(i.grace == 0 for i in inst)


def test_expand_monthly_period_end_45d_and_one_time():
    s = _sched(cadence="semiannual", due_rule=rt.parse_due_rule("period_end+45d"),
               first_period=rt.parse_period("2023H2"))
    inst = rt.expand_schedule(s, TODAY, 7, 30)
    assert [i.period for i in inst] == ["2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]
    assert inst[0].due == date(2024, 2, 14)
    one = _sched(cadence="one-time", due_rule=rt.parse_due_rule("on:2023-06-12"),
                 first_period=rt.parse_period("2023-06-12"))
    inst = rt.expand_schedule(one, TODAY, 7, 30)
    assert len(inst) == 1 and inst[0].period == "2023-06-12" and inst[0].due == date(2023, 6, 12)


def test_inactive_and_event_rows_do_not_expand():
    assert rt.expand_schedule(_sched(active=False), TODAY, 7, 30) == []
    assert rt.expand_schedule(_sched(cadence="event", due_rule=None, first_period=None), TODAY, 7, 30) == []


def test_lookahead_includes_coming_due_only_within_window():
    s = _sched(cadence="monthly", due_rule=rt.parse_due_rule("next_month_day:15"),
               first_period=rt.parse_period("2026-09"))
    inst = rt.expand_schedule(s, TODAY, 7, 30)
    # 2026-09 due 10/15 (past), 2026-10 due 11/15 (within 30d), 2026-11 due 12/15 (beyond).
    assert [i.period for i in inst] == ["2026-09", "2026-10"]


# ---------------------------------------------------------------------------
# Missing / completion / never-duplicate (pure)
# ---------------------------------------------------------------------------

def test_missing_uses_due_plus_grace_and_skips_arrived_and_logged():
    inst = [rt.Instance("f", "t", "T", "2026Q2", date(2026, 7, 30), 7, "s"),     # 8/6 < today -> missing
            rt.Instance("f", "t", "T", "2026Q3", date(2026, 10, 30), 7, "s"),    # not yet due
            rt.Instance("f", "t", "T", "2026Q1", date(2026, 4, 30), 7, "s"),     # arrived
            rt.Instance("f", "t", "T", "2025Q4", date(2026, 1, 30), 7, "s")]     # already logged
    out = rt.missing_instances(inst, {"f|t|2026Q1"}, {"f|t|2025Q4"}, TODAY)
    assert [i.period for i in out] == ["2026Q2"]
    # Grace boundary: due + grace == today is NOT missing yet.
    edge = [rt.Instance("f", "t", "T", "p", TODAY - rt.timedelta(days=7), 7, "s")]
    assert rt.missing_instances(edge, set(), set(), TODAY) == []
    edge = [rt.Instance("f", "t", "T", "p", TODAY - rt.timedelta(days=8), 7, "s")]
    assert len(rt.missing_instances(edge, set(), set(), TODAY)) == 1


def test_completion_requires_a_real_date_before_today():
    assert rt.is_complete({"date_received": "2026-10-22"}, TODAY)
    assert not rt.is_complete({"date_received": "2026-10-23"}, TODAY)     # today: not yet
    assert not rt.is_complete({"date_received": "2026-11-01"}, TODAY)     # future
    assert not rt.is_complete({"date_received": ""}, TODAY)
    assert not rt.is_complete({"date_received": "pending"}, TODAY)


def test_instance_key_shapes():
    assert rt.instance_key("arbor-hills", "cj-quarterly-probe", "2024Q1") == "arbor-hills|cj-quarterly-probe|2024Q1"
    assert rt.instance_key("arbor-hills", "downhole-profile", "2025", "AHW272R4") == "arbor-hills|downhole-profile|AHW272R4|2025"


def test_evaluate_triggers_once_per_well_and_annual():
    rules, bad = rt.load_trigger_rules([
        ["r-annual", "*", "wellhead_temp_f >= 165", "Measurements", "downhole-profile", "period_end+0d",
         "annual while condition holds", "", "63.1961(a)(6)", "primary", "", "Y"],
        ["r-24h", "*", "wellhead_temp_f >= 170 AND co_ppmv >= 1000 AND no_hov(well)", "Measurements",
         "neshap-24h-170f-co", "within:24h", "once per well", "", "63.1981(k)", "primary", "", "Y"],
    ], {"arbor-hills"})
    assert not bad
    facs, _ = rt.load_facilities(seed.FACILITIES)
    H = sw.MEASUREMENTS_HEADERS

    def m(d, well, metric, val):
        r = [""] * len(H)
        r[H.index("As-Of Date")], r[H.index("Well ID")], r[H.index("Metric")] = d, well, metric
        r[H.index("Value")], r[H.index("Basis")], r[H.index("Facility")] = val, "measured", "Arbor Hills Landfill"
        return r
    rows = [m("2025-03-14", "AHW272R4", "temperature", "177.0"), m("2025-06-01", "AHW272R4", "temperature", "171.0"),
            m("2026-02-01", "AHW272R4", "temperature", "166.0"), m("2025-03-20", "AHW272R4", "carbon_monoxide", "1200"),
            m("2025-05-05", "AHWW999", "temperature", "172.0"), m("2025-05-09", "AHWW999", "carbon_monoxide", "1500"),
            m("2025-05-05", "AHWW999", "temperature", "150.0"),
            m("2025-07-01", "COLD", "temperature", "120.0")]
    inst = rt.evaluate_triggers(rules, rows, facs, hov_wells={"AHW272R4"}, today=TODAY, default_grace=7)
    keys = sorted(i.key for i in inst)
    assert keys == ["arbor-hills|downhole-profile|AHW272R4|2025", "arbor-hills|downhole-profile|AHW272R4|2026",
                    "arbor-hills|downhole-profile|AHWW999|2025", "arbor-hills|neshap-24h-170f-co|AHWW999|2025"]
    h24 = next(i for i in inst if i.report_type == "neshap-24h-170f-co")
    assert h24.due == date(2025, 5, 6)        # trigger 5/5 + 24h; AHW272R4 excluded by no_hov
    annual = next(i for i in inst if i.key.endswith("AHW272R4|2025"))
    assert annual.due == date(2025, 12, 31)   # period_end+0d on the trigger year


# ---------------------------------------------------------------------------
# Round-1 review regressions
# ---------------------------------------------------------------------------

def test_hov_roster_reads_the_config_list_and_normalizes_ids():
    """config.yml's hov_ceilings is a LIST of {well_id,...}; the roster must
    not read as empty (no_hov(well) would then be true for every well)."""
    from config_loader import load_config
    roster = rt.hov_wells(load_config())
    assert len(roster) >= 15 and "AHW272R4" in roster
    assert rt.hov_wells({"temperature_thresholds": {"hov_ceilings": [{"well_id": "ahw263r5*"}]}}) == {"AHW263R5"}
    assert rt.hov_wells({"temperature_thresholds": {"hov_ceilings": {"AHWW312R2": 160}}}) == {"AHWW312R2"}
    assert rt.hov_wells({}) == set()


def test_missing_never_emits_one_key_twice_in_a_run():
    a = rt.Instance("f", "downhole-profile", "D", "2025", date(2025, 12, 31), 7, "rule-a", subject="W1")
    b = rt.Instance("f", "downhole-profile", "D", "2025", date(2025, 3, 10), 7, "rule-b", subject="W1")
    out = rt.missing_instances([a, b], set(), set(), TODAY)
    assert [i.key for i in out] == ["f|downhole-profile|W1|2025"]


def test_blank_facility_reading_belongs_only_to_the_nsite_facility():
    rules, bad = rt.load_trigger_rules([
        ["r", "*", "wellhead_temp_f >= 165", "Measurements", "downhole-profile", "period_end+0d",
         "annual while condition holds", "", "c", "primary", "", "Y"]], {"arbor-hills", "carleton-farms"})
    assert not bad
    facs, _ = rt.load_facilities(seed.FACILITIES)
    H = sw.MEASUREMENTS_HEADERS
    r = [""] * len(H)
    r[H.index("As-Of Date")], r[H.index("Well ID")], r[H.index("Metric")] = "2025-05-05", "AHW888", "temperature"
    r[H.index("Value")], r[H.index("Basis")] = "172.0", "measured"          # Facility blank
    inst = rt.evaluate_triggers(rules, [r], facs, set(), TODAY, 7)
    assert [i.key for i in inst] == ["arbor-hills|downhole-profile|AHW888|2025"]


def test_open_log_row_is_reconciled_against_a_hand_arrival(world):
    fake = world
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    # Trisha adds a hand Report Arrivals row for a missing Carleton AOI report (received by email).
    fake.books["PRIV"].tabs[sw.TAB_REPORT_ARRIVALS].append(
        ["Ahand000001", "carleton-farms", "carleton-farms|hov-aoi-semiannual|2024H1", "hov-aoi-semiannual", "2024H1",
         "email-hand", "EGLE FOIA release 2026-10-24", "2024-08-10", "", "2026-10-24", "hand", "high", "by email"])
    assert rt.run(today=date(2026, 10, 25), cfg=_cfg()) == 0
    log = {r["instance_key"]: r for r in _rows(fake, sw.TAB_MISSING_OVERDUE)}
    row = log["carleton-farms|hov-aoi-semiannual|2024H1"]
    assert row["date_received"] == "2026-10-24" and row["received_via"] == "Ahand000001"   # first_seen_at, never doc_date
    assert _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)[-1]["completed_today"] == "1"


def test_typed_us_date_counts_and_unreadable_date_is_flagged(world):
    fake = world
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    tab = fake.books["PRIV"].tabs[sw.TAB_MISSING_OVERDUE]
    rows = [r for r in tab[1:] if r and r[0].startswith("carleton-farms|nsps-neshap-semiannual|")]
    rows[0][8], rows[0][9] = "10/24/2026", "no-record"       # Sheets' own rendering of a typed date
    rows[1][8] = "pending"                                     # not a date at all
    assert rt.run(today=date(2026, 10, 25), cfg=_cfg()) == 0
    runs = _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)[-1]
    assert runs["completed_today"] == "1" and runs["bad_rows"] == "1"
    review = _rows(fake, sw.TAB_REPORT_TRACKER_REVIEW)
    assert any(r["kind"] == rt.KIND_BAD_ROW and "date_received" in r["detail"] for r in review)
    assert rt.is_complete({"date_received": "10/24/2026"}, date(2026, 10, 25))
    assert str(rt.parse_period("6/12/2023")) == "2023-06-12"


def test_year_in_title_ignores_a_case_number_and_prefers_a_labeled_year():
    rec = lambda t: rt.Record("nsite", "r", t)  # noqa: E731
    assert str(rt.extract_period("year_in_title", rec("Annual Compliance Certification per CJ No. 2020-0593-CE for calendar year 2025"))) == "2025"
    assert str(rt.extract_period("year_in_title", rec("CJ No. 2020-0593-CE Annual Certification 2024"))) == "2024"
    assert rt.extract_period("year_in_title", rec("CJ No. 2020-0593-CE")) is None


def test_short_alias_needs_word_boundaries():
    facs, _ = rt.load_facilities(seed.FACILITIES)
    ah = next(f for f in facs if f.facility_id == "arbor-hills")
    assert not ah.matches_text("Letter from J. Stahl re permit")
    assert ah.matches_text("AHL request letter") and ah.matches_text("re: N2688 filing")


def test_month_year_ignores_a_spaced_range_and_periodic_regex_word_boundary():
    assert rt.extract_period("month_year", rt.Record("nsite", "r", "(October - November 2025)")) is None
    assert str(rt.extract_period("month_range", rt.Record("nsite", "r", "(September - October 2025)"))) == "2025B5"
    from config_loader import load_config
    periodic = re.compile(load_config()["report_tracking"]["periodic_title_regex"], re.I)
    assert not periodic.search("Letter to Headquarters") and periodic.search("Third Quarter Report")


def test_pathological_regex_is_rejected_at_load():
    with pytest.raises(ValueError, match="too slow"):
        rt.parse_match_rule(r"stream=nsite; title_regex=(\w+\s?)+Report$; period_from=auto")


def test_seed_text_passes_the_name_denylist():
    import name_check
    for table, rows in seed.TABLES.items():
        for row in rows:
            hits = [h for h in name_check.find_denylist_hits(" ".join(str(c) for c in row))
                    if h.get("kind") != "internal_marker"]   # names only; "hand-curated" is a marker
            assert not hits, (table, row[0], hits)
    # No FOIA request id of any shape in the public seed (they live only in the
    # private Sheet's foia_request_id column).
    assert not re.search(r"\bE\d{6}-\d{6}\b", open(seed.__file__, encoding="utf-8").read())


def test_blank_facility_nsite_row_resolves_to_the_nsite_facility():
    facs, scheds = _schedules()
    rec = rt.Record("nsite", "123", "Fourth Quarter Report CJ 2024", facility_text="")
    assert rt.resolve_facility(rec, facs) == ["arbor-hills"]
    m = rt.match_record(rec, scheds, facs)
    assert m is not None and (m.confidence, m.period) == ("high", "2024Q4")
    # A hand-curated row with a blank facility still needs its text.
    assert rt.resolve_facility(rt.Record("hand-curated", "x", "Fourth Quarter Report CJ 2024"), facs) == []


def test_agency_skipped_periodic_title_is_reviewed_once(world):
    fake = world
    fake.books["PUB"].tabs[sw.TAB_NEW].append(_feed_row(
        "Compliance Communication re Second Semi-Annual 2025 NESHAP report", "2026-10-01",
        "https://mienviro.michigan.gov/ncore/downloadpdf/222222222222222222"))
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    review = [r for r in _rows(fake, sw.TAB_REPORT_TRACKER_REVIEW) if r["kind"] == rt.KIND_AGENCY_SKIPPED]
    assert len(review) == 1 and "Compliance Communication" in review[0]["title"]
    assert "222222222222222222" not in {a["source_ref"] for a in _rows(fake, sw.TAB_REPORT_ARRIVALS)}


def test_agency_regex_is_anchored_for_common_operator_words():
    from config_loader import load_config
    agency = re.compile(load_config()["report_tracking"]["agency_title_regex"], re.I)
    assert not agency.search("GFL email transmitting the 2025 Second Semi-Annual NESHAP report")
    assert not agency.search("Quarterly Consent Judgment report (evaluation of probes)")
    assert agency.search("Email follow-up on CJ report review comments") and agency.search("Consent Order 115-05-2023")


def test_regex_probe_restores_a_callers_timer():
    import signal
    signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        assert rt._regex_is_fast(re.compile(r"Status Report"))
        remaining = signal.getitimer(signal.ITIMER_REAL)[0]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    assert 20 < remaining <= 30


def test_month_year_ignores_through():
    assert rt.extract_period("month_year", rt.Record("nsite", "r", "October through December 2025")) is None


def test_scrub_strips_urls_and_long_ids():
    msg = "HttpError 429 https://sheets.googleapis.com/v4/spreadsheets/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/values"
    out = rt.scrub(msg)
    assert "googleapis" not in out and "1AbCdEfGh" not in out and "429" in out


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def test_seed_validates_and_marks_unverified_cadences_inactive():
    assert seed.validate() == []
    by_id = {r[0]: dict(zip(sw.EXPECTED_REPORT_HEADERS, r)) for r in seed.EXPECTED}
    for sid in ("ah-woi-2019-2021", "ah-gas-extraction-exceedance", "cf-hov-letters-pre-plan", "ah-downhole-profile"):
        assert by_id[sid]["active"] == "N", sid
    assert all(dict(zip(sw.TRIGGER_RULE_HEADERS, r))["active"] == "N" for r in seed.TRIGGERS)
    # Decision 3: Carleton AOI from 2023-H2, due 45 days after each half.
    aoi = by_id["cf-hov-aoi-semiannual"]
    assert aoi["first_period"] == "2023H2" and aoi["due_rule"] == "period_end+45d" and aoi["active"] == "Y"
    # Every seed row names a confidence honestly.
    assert all(r[sw.EXPECTED_REPORT_HEADERS.index("citation_confidence")] in rt.CONFIDENCE for r in seed.EXPECTED)


def test_seed_expected_first_run_gaps():
    """With the seeded tables and arrivals and NO stream records, the first
    run's missing list is the ADR's expected set (the job's count wins; this
    pins what the seed alone implies on 2026-10-23)."""
    facs, scheds = _schedules()
    inst = []
    for s in scheds:
        inst += rt.expand_schedule(s, TODAY, 7, 30)
    arrived = {dict(zip(sw.REPORT_ARRIVAL_HEADERS, r))["instance_key"] for r in seed.ARRIVALS}
    missing = rt.missing_instances(inst, arrived, set(), TODAY)
    by_type = {}
    for i in missing:
        by_type.setdefault((i.facility_id, i.report_type), []).append(i.period)
    assert by_type[("arbor-hills", "cj-semiannual")] == ["2025H2", "2026H1"]
    assert by_type[("arbor-hills", "cj-quarterly-probe")] == ["2025Q4"]
    assert by_type[("arbor-hills", "nsps-neshap-semiannual")] == [f"{y}H{h}" for y in range(2019, 2024) for h in (1, 2)] + ["2024H1"]
    assert by_type[("arbor-hills", "rop-annual-certification")] == [str(y) for y in range(2019, 2026)]
    assert ("arbor-hills", "woi-status") not in by_type
    assert len(by_type[("carleton-farms", "hov-traditional-monthly")]) == 38
    assert by_type[("carleton-farms", "hov-aoi-semiannual")] == ["2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]
    assert len(by_type[("carleton-farms", "nsps-neshap-semiannual")]) == 15
    assert len(by_type[("carleton-farms", "rop-annual-certification")]) == 7
    assert by_type[("carleton-farms", "hov-plan-approval")] == ["2023-06-12", "2025-02-03"]
    # Ridge Wood: every month through Sep 2026 is on the Sheet; with no stream
    # records in this test they all read missing, so only check the shape.
    assert by_type[("arbor-hills", "ridgewood-monthly")][0] == "2020-12"


# ---------------------------------------------------------------------------
# End to end against a fake Sheets API: never-duplicate on re-run,
# date_received filled when an item arrives, Runs row written
# ---------------------------------------------------------------------------

class _Req:
    def __init__(self, payload):
        self._p = payload

    def execute(self, num_retries=0):
        return self._p


class _Values:
    def __init__(self, tabs):
        self.tabs = tabs
        self.updates = []

    @staticmethod
    def _tab(rng):
        return re.match(r"'([^']+)'", rng).group(1)

    def get(self, spreadsheetId, range):
        tab = self._tab(range)
        if tab not in self.tabs:
            e = Exception("Unable to parse range")
            e.status_code, e.reason = 400, "Unable to parse range: " + range
            raise e
        rows = [list(r) for r in self.tabs[tab][1:]]
        return _Req({"values": rows})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.tabs.setdefault(self._tab(range), [["hdr"]]).extend([list(r) for r in body["values"]])
        return _Req({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        tab = self._tab(range)
        m = re.search(r"!([A-Z])(\d+):([A-Z])(\d+)$", range)
        if m:  # a cell update (I{row}:J{row})
            row = int(m.group(2)) - 1
            col = ord(m.group(1)) - ord("A")
            r = self.tabs[tab][row]
            while len(r) < col + len(body["values"][0]):
                r.append("")
            for i, v in enumerate(body["values"][0]):
                r[col + i] = v
        else:  # header write A1
            self.tabs.setdefault(tab, [[]])[0] = list(body["values"][0])
        self.updates.append((tab, range))
        return _Req({})


class FakeSheets:
    def __init__(self, public: dict, private: dict):
        self.books = {"PUB": _Values(public), "PRIV": _Values(private)}

    def spreadsheets(self):
        return self

    def values(self):
        return _Multi(self)

    def get(self, spreadsheetId):
        return _Req({"sheets": [{"properties": {"title": t}} for t in self.books[spreadsheetId].tabs]})

    def batchUpdate(self, spreadsheetId, body):
        for req in body["requests"]:
            self.books[spreadsheetId].tabs.setdefault(req["addSheet"]["properties"]["title"], [[]])
        return _Req({})


class _Multi:
    def __init__(self, fake):
        self.fake = fake

    def get(self, spreadsheetId, range):
        return self.fake.books[spreadsheetId].get(spreadsheetId, range)

    def append(self, spreadsheetId, **kw):
        return self.fake.books[spreadsheetId].append(spreadsheetId, **kw)

    def update(self, spreadsheetId, **kw):
        return self.fake.books[spreadsheetId].update(spreadsheetId, **kw)


def _cfg(**over):
    from config_loader import load_config
    cfg = load_config()
    # shadow_until blank = live mode, whatever the committed config says.
    cfg["report_tracking"] = {**cfg["report_tracking"], "enabled": True, "shadow_until": "", **over}
    return cfg


def _feed_row(title, filed, link, facility="Arbor Hills Landfill"):
    r = [""] * len(sw.FEED_HEADERS)
    r[sw.FEED_HEADERS.index("Date Filed")], r[sw.FEED_HEADERS.index("Document Name")] = filed, title
    r[sw.FEED_HEADERS.index("Link")], r[sw.FEED_HEADERS.index("Facility")] = link, facility
    r[sw.FEED_HEADERS.index("Type")] = "evidence"
    return r


@pytest.fixture
def world(monkeypatch):
    public = {
        sw.TAB_NEW: [sw.FEED_HEADERS,
                     _feed_row("GFL Consent Judgment report: 2026 Second Quarter (CJ No. 2020-0593-CE)", "2026-07-29",
                               "https://mienviro.michigan.gov/ncore/downloadpdf/7045536706099143566"),
                     _feed_row("On-Site Inspection (08/19/2026)", "2026-09-27",
                               "https://mienviro.michigan.gov/ncore/downloadpdf/667071183042199580"),
                     _feed_row("GFL Arbor Hills Landfill monthly flare report", "2026-09-01",
                               "https://mienviro.michigan.gov/ncore/downloadpdf/111111111111111111")],
        sw.TAB_HISTORICAL: [sw.FEED_HEADERS],
        sw.TAB_ARCHIVE: [sw.ARCHIVE_HEADERS],
        sw.TAB_WDS_NEW: [sw.WDS_HEADERS], sw.TAB_WDS_HISTORICAL: [sw.WDS_HEADERS],
        sw.TAB_HANDCURATED: [list(rt._HC_COLS)],
        sw.TAB_RIDGEWOOD: [sw.RIDGEWOOD_REPORT_HEADERS],
        sw.TAB_MEASUREMENTS: [sw.MEASUREMENTS_HEADERS],
        sw.TAB_EVIDENCE: [sw.EVIDENCE_HEADERS],  # marks PUB as the public Sheet
    }
    private = {sw.TAB_GOVQA: [sw.GOVQA_HEADERS]}
    fake = FakeSheets(public, private)
    monkeypatch.setenv("GSHEET_ID", "PUB")
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PRIV")
    import review_flags as rf
    rf._public_sheet_cache.clear()
    import drive_client as dc
    monkeypatch.setattr(dc, "sheets_service", lambda: fake)
    # Seed the hand tables + arrivals through the seed module's own writer.
    seed.seed(fake, "PRIV", apply=True)
    return fake


def _rows(fake, tab):
    return [dict(zip(sw.REPORT_TRACKER_TABS[tab], r + [""] * (len(sw.REPORT_TRACKER_TABS[tab]) - len(r))))
            for r in fake.books["PRIV"].tabs[tab][1:]]


def test_end_to_end_first_run_then_rerun_never_duplicates(world):
    fake = world
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    log = _rows(fake, sw.TAB_MISSING_OVERDUE)
    keys = [r["instance_key"] for r in log]
    assert len(keys) == len(set(keys))                       # one row per instance_key
    assert "arbor-hills|cj-semiannual|2025H2" in keys
    assert "arbor-hills|cj-quarterly-probe|2025Q4" in keys
    assert "arbor-hills|cj-quarterly-probe|2026Q2" not in keys   # seeded + matched arrival
    assert "carleton-farms|hov-aoi-semiannual|2023H2" in keys
    assert all(r["date_added"] == TODAY.isoformat() and r["date_received"] == "" for r in log)
    arrivals = _rows(fake, sw.TAB_REPORT_ARRIVALS)
    # The 2026 Q2 nSITE record was already a seeded arrival (same stream+ref): not added twice.
    refs = [(a["source_stream"], a["source_ref"]) for a in arrivals]
    assert len(refs) == len(set(refs))
    # The inspection never became an arrival; the flare report is not periodic-looking enough to review? It says
    # "monthly" -> unmatched periodic review row, once.
    review = _rows(fake, sw.TAB_REPORT_TRACKER_REVIEW)
    assert [r["kind"] for r in review] == [rt.KIND_UNMATCHED]
    assert "flare" in review[0]["title"]
    runs = _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)
    assert len(runs) == 1 and runs[0]["mode"] == "live"
    assert int(runs[0]["open_count"]) == len(log) and int(runs[0]["added_today"]) == len(log)
    n_log, n_arr, n_rev = len(log), len(arrivals), len(review)

    # Re-run the same day: nothing is duplicated, one more Runs row.
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    assert len(_rows(fake, sw.TAB_MISSING_OVERDUE)) == n_log
    assert len(_rows(fake, sw.TAB_REPORT_ARRIVALS)) == n_arr
    assert len(_rows(fake, sw.TAB_REPORT_TRACKER_REVIEW)) == n_rev
    runs = _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)
    assert len(runs) == 2 and runs[1]["added_today"] == "0" and runs[1]["open_count"] == str(n_log)


def test_arrival_of_a_logged_item_fills_date_received_and_counts_completed(world):
    fake = world
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    # The missing 2025 Q4 CJ quarterly report now shows up on nSITE.
    fake.books["PUB"].tabs[sw.TAB_NEW].append(_feed_row(
        "Arbor Hills Landfill Q4 2025 Consent Judgment quarterly report to EGLE, dated 1/30/2026", "2026-10-24",
        "https://mienviro.michigan.gov/ncore/downloadpdf/-6434168316378583072"))
    assert rt.run(today=TODAY.replace(day=25), cfg=_cfg()) == 0
    log = {r["instance_key"]: r for r in _rows(fake, sw.TAB_MISSING_OVERDUE)}
    row = log["arbor-hills|cj-quarterly-probe|2025Q4"]
    assert row["date_received"] == "2026-10-24"               # filed_date, not today
    assert row["received_via"] == rt.arrival_id("nsite", "-6434168316378583072")
    runs = _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)
    assert runs[-1]["completed_today"] == "1"
    assert int(runs[-1]["open_count"]) == int(runs[0]["open_count"]) - 1
    assert len(_rows(fake, sw.TAB_MISSING_OVERDUE)) == int(runs[0]["open_count"])   # never deleted


def test_blank_log_row_does_not_shift_the_date_received_fill(world):
    """A cleared row / spacer in the hand-edited log must not move the
    in-place fill onto the wrong instance (the fill is keyed on the real
    sheet row number, not an index into the blank-filtered read)."""
    fake = world
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    tab = fake.books["PRIV"].tabs[sw.TAB_MISSING_OVERDUE]
    tab.insert(1, [])                      # a blank row right under the header
    fake.books["PUB"].tabs[sw.TAB_NEW].append(_feed_row(
        "Arbor Hills Landfill Q4 2025 Consent Judgment quarterly report to EGLE, dated 1/30/2026", "2026-10-24",
        "https://mienviro.michigan.gov/ncore/downloadpdf/-6434168316378583072"))
    assert rt.run(today=TODAY.replace(day=25), cfg=_cfg()) == 0
    filled = [r for r in tab[1:] if len(r) > 8 and r[8]]
    assert len(filled) == 1 and filled[0][0] == "arbor-hills|cj-quarterly-probe|2025Q4"
    assert tab[1] == []                    # the spacer is untouched


def test_hand_entered_date_received_completes_and_is_counted_once(world):
    fake = world
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    tab = fake.books["PRIV"].tabs[sw.TAB_MISSING_OVERDUE]
    target = next(r for r in tab[1:] if r[0] == "carleton-farms|hov-aoi-semiannual|2023H2")
    target[8], target[9] = "2026-10-24", "no-record"         # hand entry: EGLE says no record exists
    assert rt.run(today=date(2026, 10, 25), cfg=_cfg()) == 0
    runs = _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)
    assert runs[-1]["completed_today"] == "1"
    assert rt.run(today=date(2026, 10, 26), cfg=_cfg()) == 0
    runs = _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)
    assert runs[-1]["completed_today"] == "0"                 # counted on the run after entry only
    assert "carleton-farms:" in runs[-1]["per_facility"]


def test_shadow_mode_and_disabled_and_fail_closed(world, monkeypatch):
    fake = world
    assert rt.run(today=TODAY, cfg=_cfg(shadow_until="2026-11-06")) == 0
    assert _rows(fake, sw.TAB_REPORT_TRACKER_RUNS)[-1]["mode"] == "shadow"
    from config_loader import load_config
    assert rt.run(today=TODAY, cfg=load_config()) == 0       # enabled: false in the committed config -> no-op
    # No private Sheet -> refuse, write nothing.
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PUB")
    n = len(_rows(fake, sw.TAB_REPORT_TRACKER_RUNS))
    assert rt.run(today=TODAY, cfg=_cfg()) == 1
    assert len(_rows(fake, sw.TAB_REPORT_TRACKER_RUNS)) == n


def test_tracker_never_writes_a_public_tab(world):
    fake = world
    before = {t: [list(r) for r in rows] for t, rows in fake.books["PUB"].tabs.items()}
    assert rt.run(today=TODAY, cfg=_cfg()) == 0
    assert fake.books["PUB"].tabs == before
    assert not fake.books["PUB"].updates


def test_seed_apply_is_idempotent(world):
    out = seed.seed(world, "PRIV", apply=True)
    assert all(v.startswith("skipped") for v in out.values())


def test_config_block_and_stream_status_pick_up_the_flags():
    from config_loader import load_config
    rtc = load_config()["report_tracking"]
    # Activated 2026-10-23 (Trisha); the review email stays off until two clean weeks.
    assert rtc["enabled"] is True and rtc["default_grace_days"] == 7 and rtc["review_email_enabled"] is False
    assert rtc["shadow_until"] == "2026-11-06"
    assert set(sw.REPORT_TRACKER_TABS) & set(sw._TAB_HEADERS) == set()   # never on the public Sheet via ensure_tabs


def test_review_email_body():
    rows = [{"kind": rt.KIND_PROPOSED, "source_stream": "govqa", "source_ref": "file:E1:x#1", "title": "HOV report",
             "facility_id": "carleton-farms", "detail": "hov-traditional-monthly : facility not resolved", "logged_at": "2026-10-23"}]
    body = rt.build_review_email(rows, date(2026, 10, 16), {"run_date": "2026-10-23", "mode": "shadow", "open_count": "5",
                                                             "added_today": "1", "completed_today": "0", "per_facility": "x"})
    assert "PROPOSED MATCHES" in body and "HOV report" in body and "shadow" in body
    assert rt.build_review_email([], date(2026, 10, 16), None) is None


# ---------------------------------------------------------------------------
# Briefing summary (Check 16)
# ---------------------------------------------------------------------------

def test_summary_line_ok_and_stale_and_shadow():
    run = {"run_date": "2026-10-23", "mode": "live", "open_count": "24", "added_today": "2", "completed_today": "1",
           "per_facility": "arbor-hills: 18 open (+2/-1) · carleton-farms: 6 open (+0/-0)", "proposed_matches": "0",
           "unmatched_periodic": "3", "bad_rows": "0"}
    out = rts.summary_line(run, TODAY)
    assert out["status"] == "ok"
    assert out["line"].startswith("Missing/overdue reports: 24 open (+2 added, -1 completed since 2026-10-23)")
    assert "3 unmatched periodic title(s)" in out["line"]
    stale = rts.summary_line(dict(run, run_date="2026-10-19"), TODAY)
    assert stale["status"] == "stale" and stale["line"].startswith("⚠️ report tracker last ran 4 days ago")
    shadow = rts.summary_line(dict(run, mode="shadow"), TODAY)
    assert shadow["line"].startswith("Missing/overdue reports [shadow]:")


def test_summary_main_disabled_and_skipped(capsys, monkeypatch):
    import json
    import config_loader
    real = config_loader.load_config()
    monkeypatch.setattr(config_loader, "load_config",
                        lambda: {**real, "report_tracking": {**real["report_tracking"], "enabled": False}})
    assert rts.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "disabled"
    monkeypatch.setattr(config_loader, "load_config",
                        lambda: {**real, "report_tracking": {**real["report_tracking"], "enabled": True}})
    monkeypatch.delenv("GSHEET_ID_PRIVATE", raising=False)
    assert rts.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "skipped"


def test_workflow_file_and_flag_agree():
    """A disabled new source ships WITH its scheduled workflow in place (the
    job is a no-op until the flag flips); the file must exist and name the
    private Sheet secret the tracker requires."""
    path = os.path.join(_REPO, ".github", "workflows", "report-tracker.yml")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "GSHEET_ID_PRIVATE" in text and "python report_tracker.py" in text
    assert "group: report-tracker" in text
