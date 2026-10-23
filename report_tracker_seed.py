"""
report_tracker_seed.py — the one-time seed of ADR 071's hand tables (Report
Facilities, Expected Reports, Trigger Rules) and the Report Arrivals
backfill for everything the project already holds, so the FIRST run's
Missing & Overdue Log lists only real gaps.

Seeded from the Lotext inventories named in the ADR (2026-10-20):
`SET-report/C6-peer-carleton-farms-N5986.md` §7/§7b,
`analysis/.../carleton-hov-timeline-2026-10-20.md`,
`arbor-hills-HOV-ETLF-CO-index.md` §1/§2/§2d/§7a-c,
`analysis/gas-probe-history-2026-10-03/reports.txt` (the 15 CJ quarterly
nSITE ids), `arbor-hills-monitoring-obligations-inventory.md`. Every row's
`citation_confidence` is marked honestly; a row whose cadence is unverified
ships `active = N` (kept visible, never expanded). Match rules were checked
against the real titles on the live case-file Sheet on 2026-10-23 (see
tests/test_report_tracker.py, the "real titles" block).

Dry run by default (prints what would be written). `--apply` writes to the
PRIVATE Sheet only, and only into a tab that has NO data rows yet — it never
overwrites or appends to a hand-edited table. `--apply` is Trisha's to run.
Data lives here as Python literals because the repo bans committed *.csv /
*.json data files.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import report_tracker as rt
import sheet_writer as sw

SEEDED_BY = "report_tracker_seed.py"
SEEDED_AT = "2026-10-23"

# ---------------------------------------------------------------------------
# Report Facilities
# ---------------------------------------------------------------------------

FACILITIES = [
    # facility_id, name, operator, air_srn, wds_id, nsite_site_id, county, role,
    # arrival_streams, active, notes, name_aliases
    ["arbor-hills", "Arbor Hills Landfill", "GFL Environmental (Arbor Hills Landfill, Inc.)",
     "N2688", "475946", "8094300008956198244", "Washtenaw", "primary",
     "nsite;wds;hand-curated;govqa;ridgewood", "Y",
     "The monitor's core facility (config.yml facilities: N2688). Ridge Wood is the EPA-agreement "
     "monitor next to the school, operated by Barr.",
     "Arbor Hills Landfill;Arbor Hills;AHL;N2688"],
    ["carleton-farms", "Carleton Farms Landfill", "Republic Services",
     "N5986", "390701", "", "Wayne", "peer",
     "hand-curated;govqa", "Y",
     "Peer landfill (New Boston). Not polled by any monitor stream; arrivals come from hand rows "
     "and GovQA until a stream covers N5986.",
     "Carleton Farms;Carleton;CFL;N5986"],
]

# ---------------------------------------------------------------------------
# Expected Reports
# ---------------------------------------------------------------------------

_E = sw.EXPECTED_REPORT_HEADERS


def _exp(**kw) -> list:
    """Build one Expected Reports row by column name (blank for the rest)."""
    kw.setdefault("period_basis", "calendar")
    kw.setdefault("edited_by", SEEDED_BY)
    kw.setdefault("edited_at", SEEDED_AT)
    unknown = set(kw) - set(_E)
    assert not unknown, unknown
    return [str(kw.get(h, "")) for h in _E]


_WOI_NSITE_HC = "stream=nsite|hand-curated|email-hand"
_CJ_Q = r"(Consent Judg?e?ment|CJ No\.?|CJ \d{4}|Report CJ|2020-0593-CE).*(Quarter|\bQ[1-4]\b)|(Quarter|\bQ[1-4]\b).*(Consent Judg?e?ment|\bCJ\b|2020-0593-CE)"

EXPECTED = [
    # ---- Arbor Hills: WOI Status Reports (the HOV letters' reporting condition) ----
    _exp(schedule_id="ah-woi-2019-2021", facility_id="arbor-hills", report_type="woi-status",
         report_name="WOI Status Report (2019-2021, cadence unconfirmed)",
         obligation_source="EGLE HOV approval letters 2019-2023 (program onset: request 3/28/2019, approved 5/6/2019)",
         citation_confidence="secondary", recipient="AQD", cadence="monthly", due_rule="next_month_day:15",
         first_period="2019-06", last_period="2021-12", active="N",
         match_rule=_WOI_NSITE_HC + r"; title_regex=(WOI|Wells.of.Interest).*(Status|Report)|(Status|Report).*(WOI|Wells.of.Interest); exclude_regex=waiver|approv|grant|request|Exceedance; period_from=month_range,month_year,prior_month_from_date",
         notes="HOV index §7c: 2019-2021 reports' cadence unconfirmed (2021 data reaches us only via rolling "
               "tables in 2022 reports). Set active=Y and the right cadence once a letter states it."),
    _exp(schedule_id="ah-woi-bimonthly", facility_id="arbor-hills", report_type="woi-status",
         report_name="WOI Bi-Monthly Status Report",
         obligation_source="EGLE HOV approval letters (reporting condition); filing pattern 3/15, 5/15, 7/15, 9/15, 11/15, 1/15",
         citation_confidence="secondary", recipient="AQD", cadence="bimonthly", due_rule="next_month_day:15",
         first_period="2022B1", last_period="2023B2", active="Y",
         match_rule=_WOI_NSITE_HC + r"; title_regex=Bi-?Mont\w*.*(WOI|Wells.of.Interest)|(WOI|Wells.of.Interest).*(Bi-?Mont|Jan\w*.{1,3}Feb\w*|Mar\w*.{1,3}Apr\w*|May.{1,3}June? 20|Jul\w*.{1,3}Aug\w*|Sep\w*.{1,3}Oct\w*|Nov\w*.{1,3}Dec\w*); exclude_regex=Semi-?Ann|Quarter|Exceedance; period_from=month_range,prior_bimonth_from_date",
         notes="Jan-Feb 2022 through Mar-Apr 2023 (8 reports, all held; HOV index §7c)."),
    _exp(schedule_id="ah-woi-quarterly", facility_id="arbor-hills", report_type="woi-status",
         report_name="WOI Quarterly Status Report",
         obligation_source="EGLE HOV approval letters (reporting condition); filing pattern 15th of the month after the quarter",
         citation_confidence="secondary", recipient="AQD", cadence="quarterly", due_rule="next_month_day:15",
         first_period="2023Q2", last_period="2024Q2", active="Y",
         match_rule=_WOI_NSITE_HC + r"; title_regex=(Quarter|\bQ[1-4]\b|Jan\w*.{1,3}Mar\w*|Apr\w*.{1,3}Jun\w*|Jul\w*.{1,3}Sep\w*|Oct\w*.{1,3}Dec\w*|May.{1,3}June? 2023).*(WOI|Wells.of.Interest)|(WOI|Wells.of.Interest).*(Quarter|\bQ[1-4]\b|Jan\w*.{1,3}Mar\w*|Apr\w*.{1,3}Jun\w*|Jul\w*.{1,3}Sep\w*|Oct\w*.{1,3}Dec\w*|January 15,? 2024); exclude_regex=Semi-?Ann|Bi-?Mont|first half|second half; period_from=year_quarter_words,month_range,prior_quarter_from_date",
         notes="Q2 2023 through Q2 2024 (5 reports, all held). The Q2 2024 report was filed 7/9/2024 as the "
               "'first half 2024' semi-annual letter (nSITE -6975595422894935530); its hand Arrival row below "
               "pins it to 2024Q2."),
    _exp(schedule_id="ah-woi-semiannual", facility_id="arbor-hills", report_type="woi-status",
         report_name="WOI Semi-Annual Status Report",
         obligation_source="EGLE HOV approval letters (4/30/2024 grant; 4/7/2026 renewal to 5/1/2028); H1 due 7/15, H2 due 1/15",
         citation_confidence="secondary", recipient="AQD", cadence="semiannual", due_rule="fixed:01-15,07-15",
         first_period="2024H2", end_condition="HOV renewal expires 2028-05-01 unless renewed again", active="Y",
         match_rule=_WOI_NSITE_HC + r"; title_regex=(Semi-?Ann\w*|first half|second half).*(WOI|Wells.of.Interest)|(WOI|Wells.of.Interest).*(Semi-?Ann\w*|first half|second half|Jul\w*.{1,3}Dec\w*|Jan\w*.{1,3}Jun\w*); period_from=year_half_words,month_range,prior_half_from_date",
         notes="2024 H2 onward. 2026 H2 is due 2027-01-15 (not yet)."),
    # ---- Arbor Hills: Consent Judgment 2020-0593-CE ----
    _exp(schedule_id="ah-cj-semiannual", facility_id="arbor-hills", report_type="cj-semiannual",
         report_name="Consent Judgment §6.3 semi-annual compliance report",
         obligation_source="CJ No. 2020-0593-CE §6.3 (due Mar 15 / Sep 15; perimeter monitoring, data completeness, action-level log + RCAs)",
         citation_confidence="secondary", recipient="AQD", cadence="semiannual", due_rule="fixed:03-15,09-15",
         first_period="2022H1", end_condition="¶5.5 obligations run to ~2032-03-07 (CJ ¶17.2); §6.3 tracks each obligation's tier", active="Y",
         match_rule=r"stream=nsite|hand-curated|govqa|email-hand; title_regex=(Semi-?Ann\w*|first half|second half).*(Consent Judg?e?ment|\bCJ\b|2020-0593-CE)|(Consent Judg?e?ment|\bCJ\b|2020-0593-CE).*(Semi-?Ann\w*|first half|second half); exclude_regex=WOI|Wells.of.Interest|NESHAP; period_from=year_half_words,month_range,prior_half_from_date",
         notes="Held 2022 H1-2025 H1 in Lotext source-docs/egle-documents/ (hand Arrival rows below); "
               "2025 H2 and 2026 H1 missing as of 2026-10-20 (HOV index §7c)."),
    _exp(schedule_id="ah-cj-quarterly-probe", facility_id="arbor-hills", report_type="cj-quarterly-probe",
         report_name="Consent Judgment quarterly report (¶5.12(D) perimeter gas probes / VGC liquid monitoring)",
         obligation_source="CJ No. 2020-0593-CE ¶5.12(D) + Attachment 3; filed ~30 days after each quarter (observed 4/30, 7/30, 10/30, 1/30)",
         citation_confidence="secondary", recipient="AQD", cadence="quarterly", due_rule="period_end+30d",
         first_period="2022Q2", end_condition="¶5.12 is in the CJ's 5-year tier, eligible to terminate ~2027-03-07 (CJ ¶17.1)", active="Y",
         match_rule=r"stream=nsite|hand-curated|govqa|email-hand; title_regex=" + _CJ_Q + r"|quarterly Consent Judg?e?ment|Consent Judg?e?ment quarterly|(VGC|Vertical Gas Collection) Well Liquid[- ]Monitoring; exclude_regex=Semi-?Ann|WOI|Wells.of.Interest; period_from=year_quarter_words,month_range,prior_quarter_from_date",
         notes="15 of 17 held on nSITE as of 2026-10-20 (gas-probe-history reports.txt); 2024 Q1 arrived "
               "2026-10-07 (nSITE -1415046732814678757); 2025 Q4 cover letter is on nSITE (-6434168316378583072)."),
    # ---- Arbor Hills: federal + ROP ----
    _exp(schedule_id="ah-nsps-neshap-semiannual", facility_id="arbor-hills", report_type="nsps-neshap-semiannual",
         report_name="NSPS WWW / NESHAP AAAA semi-annual report",
         obligation_source="40 CFR 63 Subpart AAAA (NSPS WWW before 9/27/2021) via ROP MI-ROP-N2688; due 3/15 (Jul-Dec) and 9/15 (Jan-Jun)",
         citation_confidence="secondary", recipient="AQD", cadence="semiannual", due_rule="fixed:03-15,09-15",
         first_period="2019H1", active="Y",
         match_rule=r"stream=nsite|hand-curated|govqa|email-hand; title_regex=NESHAP|Subpart AAAA|AAAA Semi|NSPS.*Semi-?Ann|Semi-?Ann\w*.*NSPS; period_from=date_range,month_range,year_half_words,prior_half_from_date",
         notes="Held 2024 H2, 2025 H1, 2025 H2 (hand-curated); 2026 H1 filed on nSITE 2026-09-10 "
               "(483748944487292930). 2019 H1-2024 H1 (12) not requested yet as of 2026-10-20."),
    _exp(schedule_id="ah-rop-annual-certification", facility_id="arbor-hills", report_type="rop-annual-certification",
         report_name="ROP annual compliance certification",
         obligation_source="ROP MI-ROP-N2688 general conditions (annual certification due 3/15 for the prior calendar year)",
         citation_confidence="secondary", recipient="AQD", cadence="annual", due_rule="fixed:03-15",
         first_period="2019", active="Y",
         match_rule=r"stream=nsite|hand-curated|govqa|email-hand; title_regex=Annual.{0,30}Certif|Certif.{0,30}Annual|Annual Compliance Cert; period_from=year_in_title,prior_year_from_date",
         notes="CY2019-CY2025: not checked as of 2026-10-20 (HOV index §7c)."),
    _exp(schedule_id="ah-rop-deviation-semiannual", facility_id="arbor-hills", report_type="rop-deviation-semiannual",
         report_name="ROP semi-annual deviation report",
         obligation_source="ROP MI-ROP-N2688 general conditions (semi-annual deviation reports, 3/15 and 9/15)",
         citation_confidence="secondary", recipient="AQD", cadence="semiannual", due_rule="fixed:03-15,09-15",
         first_period="2019H1", active="N",
         match_rule=r"stream=nsite|hand-curated|govqa|email-hand; title_regex=Deviation.*Report|ROP.*Semi-?Ann|Semi-?Ann\w*.*ROP; exclude_regex=NESHAP|WOI|Consent; period_from=year_half_words,month_range,date_range,prior_half_from_date",
         notes="May arrive bundled with the NESHAP report (one or two documents per half). Inactive until "
               "the first specimen shows whether it is a separate filing here."),
    _exp(schedule_id="ah-gas-extraction-exceedance", facility_id="arbor-hills", report_type="gas-extraction-exceedance",
         report_name="Gas Extraction Exceedance report (General / Detailed xlsx)",
         obligation_source="HOV letters / EGLE request (start date unconfirmed)",
         citation_confidence="secondary", recipient="AQD", cadence="monthly", due_rule="next_month_day:15",
         first_period="2022-11", active="N",
         match_rule=_WOI_NSITE_HC + r"; title_regex=Gas Extraction Exceedance; period_from=month_year,month_range,prior_month_from_date",
         notes="Held: Nov 2022, Apr 2024 (+ detailed), Mar-Sep 2025, Apr-May 2026. Monthly start date "
               "unconfirmed (HOV index §7c), so not expanded."),
    _exp(schedule_id="ah-hmp-quarterly", facility_id="arbor-hills", report_type="hmp-quarterly",
         report_name="Hydrogeologic Monitoring Plan quarterly report (QMR)",
         obligation_source="Part 115 HMP (Golder 2018, approved 10/4/2018); quarterly HM report to MMD due 4/30, 7/30, 10/30, 1/30",
         citation_confidence="secondary", recipient="MMD", cadence="quarterly", due_rule="fixed:01-30,04-30,07-30,10-30",
         first_period="2026Q1", active="N",
         match_rule=r"stream=wds|hand-curated|govqa|email-hand; title_regex=\bQMR\b|Quarterly Monitoring Report|Hydrogeologic Monitoring Report|Hydrogeologic Monitoring Plan quarterly; exclude_regex=Revised Hydrogeologic Monitoring Plan|\bPlan\b(?!.*Report); period_from=year_quarter_words,month_range,prior_quarter_from_date",
         notes="Q1 2026 held (hand-curated). WDS Historical Documents carries 'QMR groundwater report (due "
               "M/D/YYYY)' rows (2002 onward; checked 2026-10-23), which match. Inactive until the WDS "
               "coverage of recent quarters is confirmed, so a WDS gap is not read as a missing report."),
    _exp(schedule_id="ah-annual-landfill-report", facility_id="arbor-hills", report_type="annual-landfill-report",
         report_name="Part 115 annual landfill report (WDS)",
         obligation_source="Part 115 / R 299.4XXX annual report to MMD (WDS `annual` collection)",
         citation_confidence="secondary", recipient="MMD", cadence="annual", due_rule="fixed:01-31",
         first_period="2024", active="N",
         match_rule=r"stream=wds|hand-curated|govqa|email-hand; title_regex=Annual Landfill Report|Landfill Facility Capacity Information|Sec\. 11512 annual report; exclude_regex=Solid Waste Landfilled in Michigan; period_from=year_in_title,prior_year_from_date",
         notes="WDS Historical Documents carries 'Annual Landfill Report — FY2024 — ...' rows (FY1996 onward; "
               "checked 2026-10-23), which match. Inactive until the due date is verified (fixed:01-31 is a "
               "placeholder) and WDS coverage of the latest fiscal year is confirmed."),
    _exp(schedule_id="ah-ridgewood-monthly", facility_id="arbor-hills", report_type="ridgewood-monthly",
         report_name="Ridge Wood Elementary monthly H2S report (Barr)",
         obligation_source="Dec 2020 agreement with U.S. EPA (not an EGLE permit condition); posted ~15-20 days after month end",
         citation_confidence="secondary", recipient="EPA", cadence="monthly", due_rule="next_month_day:20",
         first_period="2020-12", active="Y",
         match_rule=r"stream=ridgewood; title_regex=Ridge Wood; period_from=record_period",
         notes="Stream G archives every month; the Report Title's month is the period."),
    # ---- Arbor Hills: event-driven / letters (documented; not expanded) ----
    _exp(schedule_id="ah-hov-letters-2019-2023", facility_id="arbor-hills", report_type="hov-approval",
         report_name="HOV temperature-waiver requests and grants, 2019-2023",
         obligation_source="EGLE AQD HOV letters (~15 listed on the face of the 2024-25 letters)",
         citation_confidence="secondary", recipient="AQD", cadence="event", active="N",
         match_rule=_WOI_NSITE_HC + r"; title_regex=(HOV|Higher Operating Value).*(approv|grant|request|waiver)|temperature.waiver; period_from=date_in_title,record_date",
         notes="0 of ~15 held. Event-driven; not expanded. Candidates for the FOIA wishlist."),
    _exp(schedule_id="ah-hov-letters-2024-2026", facility_id="arbor-hills", report_type="hov-approval",
         report_name="HOV temperature-waiver requests and grants, 2024-2026",
         obligation_source="Requests 4/22/2024, 2/11/2025, 7/15/2025, Apr 2026; grants 4/30/2024, 7/10/2024, 2/19/2025, 7/23/2025, 4/7/2026",
         citation_confidence="primary", recipient="AQD", cadence="event", active="N",
         match_rule=_WOI_NSITE_HC + r"; title_regex=(HOV|Higher Operating Value).*(approv|grant|request|waiver)|temperature.waiver; exclude_regex=Plan; period_from=date_in_title,record_date",
         notes="8 of 9 held (the 2/11/2025 request is referenced only). Event-driven; not expanded."),
    _exp(schedule_id="ah-downhole-profile", facility_id="arbor-hills", report_type="downhole-profile",
         report_name="Downhole temperature profile (WOI Att 7)",
         obligation_source="AH HOV approval letters: quarterly for one year after a WOI well exceeds 165°F (verify against the 4/30/2024 grant letter before activating); EGLE confirmed 8/23/2026 the trigger still applies",
         citation_confidence="secondary", recipient="AQD", cadence="event", active="N",
         notes="Trigger-driven (Trigger Rules ah-165f-downhole). Only ever filed 2022-2023 inside WOI reports."),
    _exp(schedule_id="ah-neshap-24h-170f-co", facility_id="arbor-hills", report_type="neshap-24h-170f-co",
         report_name="24-hour report: wellhead >= 170°F and CO >= 1,000 ppmv, no HOV",
         obligation_source="40 CFR 63.1981(k)", citation_confidence="primary", recipient="AQD",
         cadence="event", due_rule="within:24h", active="N",
         notes="Trigger-driven (Trigger Rules fed-170f-co-24h). None known (CO max 650 ppm in held data)."),
    _exp(schedule_id="ah-rule-433-notification", facility_id="arbor-hills", report_type="rule-433-notification",
         report_name="R 299.4433(4) boundary-methane notification to MMD",
         obligation_source="Part 115 Rule 433(4) (verify notification timing when activating)",
         citation_confidence="secondary", recipient="MMD", cadence="event", active="N",
         notes="An open FOIA request (granted 10/15/2026, deposit pending) covers these notifications; "
               "pre-fill foia_request_id by hand on any instance that is later logged (the id lives "
               "only on the private Sheet)."),
    # ---- Carleton Farms ----
    _exp(schedule_id="cf-hov-traditional-monthly", facility_id="carleton-farms", report_type="hov-traditional-monthly",
         report_name="HOV Traditional Well Monthly Status Report",
         obligation_source="N5986 HOV Plan (approved 6/12/2023; rev. Feb 2025 approved 2/3/2025): wellhead data submitted monthly to AQD; observed filed ~13th-16th of the next month",
         citation_confidence="primary", recipient="AQD", cadence="monthly", due_rule="next_month_day:15",
         first_period="2023-06", active="Y",
         match_rule=r"stream=hand-curated|govqa|email-hand|nsite; title_regex=HOV Traditional Well Monthly|Traditional Well.*Monthly Status; period_from=month_year,month_range,prior_month_from_date",
         notes="40 due Jun 2023-Sep 2026 (39 if the first was July 2023; an 'as needed' month may have no "
               "report). Held: Oct + Nov 2025 only (hand Arrival rows below)."),
    _exp(schedule_id="cf-hov-aoi-semiannual", facility_id="carleton-farms", report_type="hov-aoi-semiannual",
         report_name="HOV Plan semi-annual Area of Interest (AOI) report",
         obligation_source="N5986 HOV Plan rev. Feb 2025: AOI report twice a year, due 45 days after each period",
         citation_confidence="primary", recipient="AQD", cadence="semiannual", due_rule="period_end+45d",
         first_period="2023H2", active="Y",
         match_rule=r"stream=hand-curated|govqa|email-hand|nsite; title_regex=(Area of Interest|\bAOI\b).*Report|Report.*(Area of Interest|\bAOI\b); exclude_regex=\bMap\b; period_from=year_half_words,month_range,date_range,prior_half_from_date",
         notes="Seeded as 6 (2023-H2 through 2026-H1; Trisha's decision 3, 2026-10-20). AOI tier may have "
               "begun with the Feb 2025 revision; if EGLE confirms, set 2023-H2 to 2024-H2 to no-record."),
    _exp(schedule_id="cf-nsps-neshap-semiannual", facility_id="carleton-farms", report_type="nsps-neshap-semiannual",
         report_name="NSPS WWW / NESHAP AAAA semi-annual monitoring + deviation report",
         obligation_source="ROP MI-ROP-N5986-2023 GC 19-23; 40 CFR 63 Subpart AAAA; due 3/15 (Jul-Dec) and 9/15 (Jan-Jun)",
         citation_confidence="primary", recipient="AQD", cadence="semiannual", due_rule="fixed:03-15,09-15",
         first_period="2019H1", active="Y",
         match_rule=r"stream=hand-curated|govqa|email-hand|nsite; title_regex=NESHAP|Subpart AAAA|AAAA Semi|NSPS.*Semi-?Ann|Semi-?Ann\w*.*NSPS; period_from=date_range,month_range,year_half_words,prior_half_from_date",
         notes="15 due 9/15/2019 through 9/15/2026, 0 held (C6 §7). May arrive as two documents per half."),
    _exp(schedule_id="cf-rop-annual-certification", facility_id="carleton-farms", report_type="rop-annual-certification",
         report_name="ROP annual compliance certification",
         obligation_source="ROP MI-ROP-N5986-2023 general conditions; annual certification by 3/15 for the prior year",
         citation_confidence="primary", recipient="AQD", cadence="annual", due_rule="fixed:03-15",
         first_period="2019", active="Y",
         match_rule=r"stream=hand-curated|govqa|email-hand|nsite; title_regex=Annual.{0,30}Certif|Certif.{0,30}Annual|Annual Compliance Cert; period_from=year_in_title,prior_year_from_date",
         notes="CY2019-CY2025 (7), 0 held."),
    _exp(schedule_id="cf-hov-plan-approval-2023", facility_id="carleton-farms", report_type="hov-plan-approval",
         report_name="AQD approval of the Carleton HOV Plan (6/12/2023)",
         obligation_source="Operator cover letters: 'The plan was approved by [EGLE] on June 12, 2023'",
         citation_confidence="primary", recipient="AQD", cadence="one-time", due_rule="on:2023-06-12",
         first_period="2023-06-12", active="Y",
         match_rule=r"stream=hand-curated|govqa|email-hand|nsite; title_regex=HOV Plan.*(approv|2023)|approv.*HOV Plan; period_from=record_period",
         notes="Approval letter not held; only the Feb 2025 plan text is public."),
    _exp(schedule_id="cf-hov-plan-approval-2025", facility_id="carleton-farms", report_type="hov-plan-approval",
         report_name="AQD approval of the Carleton HOV Plan revision (2/3/2025)",
         obligation_source="Operator cover letters: 'A revision of this plan was approved on February 3, 2025'",
         citation_confidence="primary", recipient="AQD", cadence="one-time", due_rule="on:2025-02-03",
         first_period="2025-02-03", active="Y",
         match_rule=r"stream=hand-curated|govqa|email-hand|nsite; title_regex=HOV Plan.*(approv|2025)|approv.*HOV Plan; exclude_regex=rev\. February 2025$; period_from=record_period",
         notes="Approval letter not held. 'N5986 CFL HOV Plan rev. February 2025' is the plan text, not the letter."),
    _exp(schedule_id="cf-hov-letters-pre-plan", facility_id="carleton-farms", report_type="hov-approval",
         report_name="Individual HOV requests + AQD approvals/denials, 8/2019-6/2023",
         obligation_source="AQD inspections 8/19/2019 pp. 8-9 (no HOV above 150°F approved from 12/2018)",
         citation_confidence="secondary", recipient="AQD", cadence="event", active="N",
         notes="Unknown count; 0 held. Event-driven; not expanded."),
    _exp(schedule_id="cf-neshap-24h-170f-co", facility_id="carleton-farms", report_type="neshap-24h-170f-co",
         report_name="24-hour report: wellhead >= 170°F and CO >= 1,000 ppmv (9/27/2021-6/12/2023, pre-plan)",
         obligation_source="40 CFR 63.1981(k); ROP p.45 (required since 9/27/2021; not applicable under the plan)",
         citation_confidence="primary", recipient="AQD", cadence="event", due_rule="within:24h", active="N",
         notes="Event-driven, 0 or more; 0 held. Trigger-driven in principle but no Carleton Measurements exist."),
    _exp(schedule_id="cf-rule-433-notification", facility_id="carleton-farms", report_type="rule-433-notification",
         report_name="R 299.4433(4) boundary-methane notification to MMD",
         obligation_source="Part 115 Rule 433(4)", citation_confidence="secondary", recipient="MMD",
         cadence="event", active="N", notes="Unknown; 0 held. Event-driven; not expanded."),
]

# ---------------------------------------------------------------------------
# Trigger Rules — all ship active = N: enable one at a time after reviewing
# what it would create against Measurements (ADR 071 "verify before seeding").
# ---------------------------------------------------------------------------

_T = sw.TRIGGER_RULE_HEADERS


def _trig(**kw) -> list:
    unknown = set(kw) - set(_T)
    assert not unknown, unknown
    return [str(kw.get(h, "")) for h in _T]


TRIGGERS = [
    _trig(rule_id="fed-165f-downhole-annual", facility_id="*",
          condition="wellhead_temp_f >= 165", input="Measurements", creates="downhole-profile",
          due_rule="period_end+0d", repeat="annual while condition holds",
          stop_condition="well below 165°F for the calendar year",
          citation="40 CFR 63.1961(a)(6); Carleton ROP SC VI.5", citation_confidence="primary",
          notes="Temperature every 10 vertical feet, annually, for a well at or above 165°F.", active="N"),
    _trig(rule_id="fed-145f-enhanced-weekly", facility_id="*",
          condition="wellhead_temp_f > 145 AND no_hov(well)", input="Measurements", creates="downhole-profile",
          due_rule="within:7d", repeat="once per well",
          stop_condition="an HOV is approved for the well, or it returns to 145°F or below",
          citation="40 CFR 63.1961(a)(5)(i)-(ix); Carleton ROP SC VI.4", citation_confidence="primary",
          notes="Enhanced monitoring starts 7 days after the first reading over 145°F without an HOV "
                "(weekly; may drop to monthly after four weekly CO readings under 100 ppmv). v1 creates one "
                "instance per well for the first weekly set; the weekly series itself is not expanded.", active="N"),
    _trig(rule_id="fed-170f-co-24h", facility_id="*",
          condition="wellhead_temp_f >= 170 AND co_ppmv >= 1000 AND no_hov(well)", input="Measurements",
          creates="neshap-24h-170f-co", due_rule="within:24h", repeat="once per well",
          stop_condition="n/a (one report per event)",
          citation="40 CFR 63.1981(k); Carleton ROP p.45", citation_confidence="primary",
          notes="Email report to EGLE within 24 hours.", active="N"),
    _trig(rule_id="ah-165f-downhole-quarterly", facility_id="arbor-hills",
          condition="wellhead_temp_f > 165", input="Measurements", creates="downhole-profile",
          due_rule="next_month_day:15", repeat="annual while condition holds",
          stop_condition="one year of quarterly profiles after the exceedance",
          citation="AH HOV approval letters (HOV index); verify against the 4/30/2024 grant letter",
          citation_confidence="secondary",
          notes="HOV condition: quarterly downhole profile for one year after a WOI well exceeds 165°F. "
                "EGLE confirmed 8/23/2026 the trigger still applies though it no longer collects the "
                "profiles. v1 creates one instance per well-year.", active="N"),
]

# ---------------------------------------------------------------------------
# Report Arrivals backfill — what the project already holds (match_method =
# hand). nSITE ids where known; otherwise the Lotext holding named in the
# HOV index / C6. A matched stream record for the same instance later adds a
# second row with its own source_ref; that is fine (one row per RECORD).
# ---------------------------------------------------------------------------

_A = sw.REPORT_ARRIVAL_HEADERS


def _arr(facility_id: str, report_type: str, period: str, stream: str, ref: str,
         doc_date: str = "", filed_date: str = "", notes: str = "") -> list:
    return [rt.arrival_id(stream, ref), facility_id, rt.instance_key(facility_id, report_type, period),
            report_type, period, stream, ref, doc_date, filed_date, SEEDED_AT, "hand", "high", notes]


_LX = "Lotext documents/arbor-hills/"
_AH = "arbor-hills"

ARRIVALS = [
    # WOI status reports: all 17 from Jan-Feb 2022 through 2026 H1 (HOV index §2 / §7c).
    _arr(_AH, "woi-status", "2022B1", "email-hand", _LX + "source-docs/egle-aqd-records-2026-10-08/WOI-status-report-2022-Jan-Feb.pdf", "2022-03-15", "", "March 15, 2022 Bi-Monthly WOI Status Report (EGLE records 10/8/2026)"),
    _arr(_AH, "woi-status", "2022B2", "nsite", "603180310030693456", "2022-05-12", "2026-10-08", "bi-monthly, March-April 2022"),
    _arr(_AH, "woi-status", "2022B3", "email-hand", _LX + "source-docs/egle-woi-downhole-2026-08-20/ (May-June 2022)", "2022-07-20", "", "EGLE AQD records obtained 2026-08-20; also Hand-Curated"),
    _arr(_AH, "woi-status", "2022B4", "email-hand", _LX + "source-docs/egle-aqd-records-2026-10-08/WOI-status-report-2022-Jul-Aug.pdf", "2022-09-15", "", "filed 9/15/2022"),
    _arr(_AH, "woi-status", "2022B5", "nsite", "6318109379845527354", "2022-11-15", "2026-02-05", "bi-monthly, September-October 2022"),
    _arr(_AH, "woi-status", "2022B6", "email-hand", _LX + "source-docs/egle-woi-downhole-2026-08-20/ (Nov-Dec 2022)", "2023-01-15", "", "EGLE AQD records obtained 2026-08-20; also Hand-Curated"),
    _arr(_AH, "woi-status", "2023B1", "email-hand", _LX + "source-docs/egle-aqd-records-2026-10-08/WOI-status-report-2023-Jan-Feb.pdf", "2023-03-15", "", "March 15, 2023 Bi-Monthly WOI Report"),
    _arr(_AH, "woi-status", "2023B2", "nsite", "6899614001871945975", "2023-05-11", "2026-02-05", "bi-monthly, March-April 2023"),
    _arr(_AH, "woi-status", "2023Q2", "nsite", "-2136493721019571731", "2023-07-14", "2026-02-05", "quarterly, Q2 2023"),
    _arr(_AH, "woi-status", "2023Q3", "nsite", "-8885291794816390696", "2023-10-15", "2026-02-05", "quarterly, Q3 2023"),
    _arr(_AH, "woi-status", "2023Q4", "email-hand", _LX + "source-docs/egle-aqd-records-2026-10-08/WOI-status-report-2023-Q4.pdf", "2024-01-15", "", "January 15 2024 WOI Status Report"),
    _arr(_AH, "woi-status", "2024Q1", "nsite", "-4046818116875037513", "2024-04-15", "2026-02-05", "quarterly, Q1 2024"),
    _arr(_AH, "woi-status", "2024Q2", "nsite", "-6975595422894935530", "2024-07-09", "2026-10-07", "filed as the 'first half 2024' semi-annual letter; covers Apr-Jun 2024 (the Q2 2024 report)"),
    _arr(_AH, "woi-status", "2024H2", "nsite", "7894782240984169987", "2025-01-06", "2026-02-05", "Second Semi-Annual 2024"),
    _arr(_AH, "woi-status", "2025H1", "nsite", "-7583536641080008007", "2025-07-15", "2026-02-05", "2025 First Semi-Annual"),
    _arr(_AH, "woi-status", "2025H2", "nsite", "-2083857881305271673", "2026-01-15", "2026-01-13", "July-December 2025"),
    _arr(_AH, "woi-status", "2026H1", "email-hand", _LX + "source-docs/hov-etlf/WOI-status-report-2026-1H.pdf", "2026-07-13", "", "2026 First Semi-Annual; also Hand-Curated (added 2026-08-28)"),
    # Consent Judgment §6.3 semi-annual compliance reports: 2022 H1 through 2025 H1 held (HOV index §7c).
    *[_arr(_AH, "cj-semiannual", p, "email-hand", _LX + f"source-docs/egle-documents/ (CJ semi-annual {p})", "", "", "held per HOV index §7c (checked on disk 2026-10-02)")
      for p in ("2022H1", "2022H2", "2023H1", "2023H2", "2024H1", "2024H2", "2025H1")],
    # Consent Judgment quarterly reports: the 15 nSITE ids in gas-probe-history reports.txt.
    *[_arr(_AH, "cj-quarterly-probe", p, "nsite", did, "", "", "gas-probe-history-2026-10-03/reports.txt")
      for p, did in (("2022Q2", "-9008889600254614867"), ("2022Q3", "1432089853875180836"),
                     ("2022Q4", "-7883632392835307833"), ("2023Q1", "-572615758652377778"),
                     ("2023Q2", "-3651238607132417036"), ("2023Q3", "-4763013407521422881"),
                     ("2023Q4", "-4573140138154359966"), ("2024Q2", "-6565316200689395285"),
                     ("2024Q3", "-4243771068861522262"), ("2024Q4", "2219362145116106552"),
                     ("2025Q1", "-3054672390421616919"), ("2025Q2", "1952192436773246630"),
                     ("2025Q3", "-3415946546048626918"), ("2026Q1", "1091529476934128600"),
                     ("2026Q2", "7045536706099143566"))],
    _arr(_AH, "cj-quarterly-probe", "2024Q1", "nsite", "-1415046732814678757", "2024-04-30", "2026-10-07", "arrived 2026-10-07 (was 'missing' in the 10/20 inventory)"),
    # NESHAP semi-annual: 2024 H2, 2025 H1, 2025 H2 held; 2026 H1 on nSITE.
    _arr(_AH, "nsps-neshap-semiannual", "2024H2", "email-hand", _LX + "source-docs/neshap-semiannual-2024H2-2025H1/", "2025-03-15", "", "HOV index §7c"),
    _arr(_AH, "nsps-neshap-semiannual", "2025H1", "email-hand", _LX + "source-docs/fce-records-request-2026-zip/AAAA Semi-Annual NESHAP 010125 through 063025.pdf", "2025-09-10", "", "also Hand-Curated (2026-08-28)"),
    _arr(_AH, "nsps-neshap-semiannual", "2025H2", "email-hand", _LX + "source-docs/fce-records-request-2026-zip/AAAA Semi-Annual NESHAP 070125 through 123125.pdf", "2026-03-13", "", "also Hand-Curated (2026-08-28)"),
    _arr(_AH, "nsps-neshap-semiannual", "2026H1", "nsite", "483748944487292930", "2026-09-15", "2026-09-10", "1st Semi-Annual 2026 NESHAP report"),
    # Carleton: the two monthly reports public on nSITE (N5986 is not a polled facility).
    _arr("carleton-farms", "hov-traditional-monthly", "2025-10", "email-hand", "N5986 nSITE submission 2025-11-13 (HOV Traditional Well Monthly Status Report, October 2025)", "2025-11-13", "", "carleton-hov-timeline-2026-10-20.md"),
    _arr("carleton-farms", "hov-traditional-monthly", "2025-11", "email-hand", "N5986 nSITE submission 2025-12-16 (HOV Traditional Well Monthly Status Report, November 2025)", "2025-12-16", "", "carleton-hov-timeline-2026-10-20.md"),
]

TABLES = {
    sw.TAB_REPORT_FACILITIES: FACILITIES,
    sw.TAB_EXPECTED_REPORTS: EXPECTED,
    sw.TAB_TRIGGER_RULES: TRIGGERS,
    sw.TAB_REPORT_ARRIVALS: ARRIVALS,
}


def validate() -> list:
    """Every seed row must pass the tracker's own loaders. Returns the list
    of (table, row_id, reason) problems — empty when the seed is sound."""
    facs, bad_f = rt.load_facilities(FACILITIES)
    fids = {f.facility_id for f in facs}
    _, bad_s = rt.load_schedules(EXPECTED, fids)
    _, bad_t = rt.load_trigger_rules(TRIGGERS, fids)
    problems = [("Report Facilities", *b) for b in bad_f] + [("Expected Reports", *b) for b in bad_s] \
        + [("Trigger Rules", *b) for b in bad_t]
    seen = set()
    for row in ARRIVALS:
        d = dict(zip(sw.REPORT_ARRIVAL_HEADERS, row))
        if d["facility_id"] not in fids:
            problems.append(("Report Arrivals", d["arrival_id"], "unknown facility"))
        if d["report_type"] not in rt.REPORT_TYPES:
            problems.append(("Report Arrivals", d["arrival_id"], "unknown report_type"))
        try:
            rt.parse_period(d["period"])
        except ValueError as e:
            problems.append(("Report Arrivals", d["arrival_id"], str(e)))
        if (d["source_stream"], d["source_ref"]) in seen:
            problems.append(("Report Arrivals", d["arrival_id"], "duplicate source"))
        seen.add((d["source_stream"], d["source_ref"]))
    return problems


def seed(service, sheet_id: str, apply: bool = False) -> dict:
    """Write each seed table into its PRIVATE-Sheet tab, only when that tab
    has no data rows yet. Returns {tab: 'written N' | 'skipped (N rows present)'
    | 'would write N'}."""
    problems = validate()
    if problems:
        raise ValueError(f"seed does not validate: {problems}")
    out = {}
    if apply:
        sw.ensure_report_tracker_tabs(service, sheet_id)
    for tab, rows in TABLES.items():
        existing = sw._tab_rows_strict(service, sheet_id, tab, "A2:B") if apply else []
        if existing:
            out[tab] = f"skipped ({len(existing)} rows present)"
            continue
        if apply:
            sw.append_rows(service, sheet_id, tab, rows)
            out[tab] = f"written {len(rows)}"
        else:
            out[tab] = f"would write {len(rows)}"
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Seed ADR 071's hand tables on the PRIVATE Sheet (dry run by default).")
    ap.add_argument("--apply", action="store_true", help="actually write (only into empty tabs)")
    args = ap.parse_args(argv)
    problems = validate()
    if problems:
        for p in problems:
            print("INVALID:", p)
        return 1
    print(f"seed validates: {len(FACILITIES)} facilities, {len(EXPECTED)} expected-report rows "
          f"({sum(1 for r in EXPECTED if r[_E.index('active')] == 'Y')} active), {len(TRIGGERS)} trigger rules "
          f"(all inactive), {len(ARRIVALS)} arrival rows")
    if not args.apply:
        for tab, rows in TABLES.items():
            print(f"  would write {len(rows):3d} rows -> {tab}")
        print("dry run; pass --apply to write (empty private tabs only)")
        return 0
    if os.path.exists(".env"):
        from backfill_metric_taxonomy import _load_dotenv
        _load_dotenv()
    import drive_client as dc
    import review_flags as rf
    service = dc.sheets_service()
    sheet_id, why = rf.private_sheet_for_write(service)
    if sheet_id is None:
        print(f"refusing: {why}")
        return 1
    for tab, outcome in seed(service, sheet_id, apply=True).items():
        print(f"  {outcome} -> {tab}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
