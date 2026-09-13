"""
Stream C (WDS) tests — the diff engine, the WDS-specific classifier, and the
detail-span parser. All pure: no network, no Sheets, no SMTP.

The parser fixtures deliberately reproduce the real WDS markup shape, INCLUDING
attribute-order variation (id-before-class vs class-before-id, title in different
positions) — the exact thing that made a stricter single-regex return 0 rows
during the 2026-07-09 crawl. Empty fields are included to prove no field bleed.
"""
import wds_client as wc
import wds_watcher as ww
import sheet_writer as sw


# ---------------------------------------------------------------------------
# Parser: detail-span extraction, attribute-order tolerant, no field bleed
# ---------------------------------------------------------------------------

def _span(row, title, value, order="id_first"):
    """A WDS-style detailControl value span for grid 'QMRReportList', row N."""
    idattr = f'id="ctl00_Body_QMRReportList_R_ctl{row:02d}_D_x_detailLabel"'
    classattr = 'class="detailControl plainText2ca"'
    titleattr = f'title="{title}:"'
    if order == "id_first":
        attrs = f"{idattr} {titleattr} {classattr}"
    else:  # class before id, title last — the order that broke the strict regex
        attrs = f"{classattr} {idattr} {titleattr}"
    return f"<span {attrs}>{value}</span>"


def _qmr_html(rows):
    """rows: list of dicts -> a minimal QMRReportList page fragment."""
    parts = []
    for i, r in enumerate(rows):
        order = "id_first" if i % 2 == 0 else "class_first"
        for title in ("Due Date", "Date Received", "Statistical Exceedence?", "Review Notes"):
            parts.append(_span(i, title, r.get(title, ""), order))
    return "<html><body>" + "".join(parts) + "</body></html>"


def test_detail_rows_extracts_and_tolerates_attr_order():
    html = _qmr_html([
        {"Due Date": "4/30/2025", "Date Received": "4/28/2025",
         "Statistical Exceedence?": "Yes", "Review Notes": "Boron trend."},
        {"Due Date": "1/31/2025", "Date Received": "1/30/2025",
         "Statistical Exceedence?": "No", "Review Notes": ""},
    ])
    rows = wc._detail_rows(html, "QMRReportList")
    assert len(rows) == 2
    assert rows[0]["Due Date"] == "4/30/2025"
    assert rows[0]["Statistical Exceedence?"] == "Yes"
    # Row 1 used the class-first attribute order and MUST still parse.
    assert rows[1]["Due Date"] == "1/31/2025"
    assert rows[1]["Statistical Exceedence?"] == "No"
    # Empty Review Notes stays empty — no bleed from the next row.
    assert rows[1]["Review Notes"] == ""


# ---------------------------------------------------------------------------
# Date normalization — WDS's unpadded M/D/YYYY must become ISO YYYY-MM-DD so it
# sorts correctly both in the Sheet UI and in rebuild_risk_register_tab()'s
# string '>' comparison for "most recent evidence" (nsite_client.py already
# does this via .isoformat(); WDS scraped the raw string verbatim until now).
# ---------------------------------------------------------------------------

def test_iso_date_pads_single_digit_month_and_day():
    assert ww._iso_date("4/30/2025") == "2025-04-30"
    assert ww._iso_date("1/1/2000") == "2000-01-01"
    assert ww._iso_date("12/9/2026") == "2026-12-09"


def test_iso_date_passes_through_blank_and_unrecognized():
    assert ww._iso_date("") == ""
    assert ww._iso_date("2025") == "2025"  # annual report's bare Year


def test_diff_collection_emits_iso_dates_for_mdy_collections():
    r1 = {"Due Date": "4/30/2025", "Date Received": "4/28/2025",
          "Statistical Exceedence?": "No", "Review Notes": ""}
    _e, entry, _n = ww.diff_collection("qmr", [r1], _empty(), {})
    r2 = {"Due Date": "7/30/2025", "Date Received": "7/28/2025",
          "Statistical Exceedence?": "Yes", "Review Notes": "y"}
    events, _entry2, _n2 = ww.diff_collection("qmr", [r1, r2], entry, {})
    assert events[0]["date"] == "2025-07-28"


def test_historical_events_emits_iso_dates_for_mdy_collections():
    rows = [{"Application Type": "Construction Permit", "Receipt Date": "6/1/2020",
             "Closure Type": "Issued", "Closure Date": "9/1/2020"}]
    events = ww.historical_events("applications", rows, {})
    assert events[0]["date"] == "2020-06-01"


def test_annual_date_stays_bare_year_unchanged():
    rows = [{"Year": "2025", "Yrs Remaining End": "4.0"}]
    events = ww.historical_events("annual", rows, {"years_remaining_floor": 3.0})
    assert events[0]["date"] == "2025"


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------

def _empty():
    return {"records": {}, "last_count": 0}


def test_baseline_records_all_alerts_none():
    rows = [{"Due Date": "4/30/2025", "Date Received": "4/28/2025",
             "Statistical Exceedence?": "Yes", "Review Notes": "x"}]
    events, entry, _note = ww.diff_collection("qmr", rows, _empty(), {})
    assert events == []                      # first sight -> no alerts
    assert entry["last_count"] == 1
    assert len(entry["records"]) == 1


def test_new_record_after_baseline_alerts():
    r1 = {"Due Date": "4/30/2025", "Date Received": "4/28/2025",
          "Statistical Exceedence?": "No", "Review Notes": ""}
    _e, entry, _n = ww.diff_collection("qmr", [r1], _empty(), {})
    r2 = {"Due Date": "7/30/2025", "Date Received": "7/28/2025",
          "Statistical Exceedence?": "Yes", "Review Notes": "Chloride."}
    events, entry2, _n2 = ww.diff_collection("qmr", [r1, r2], entry, {})
    assert len(events) == 1
    assert events[0]["kind"] == "new"
    assert events[0]["severity"] == "notable"   # exceedance = Yes
    assert entry2["last_count"] == 2


def test_changed_content_re_alerts_exceedance_backfill():
    # A QMR appears first with a blank exceedance flag, then WDS back-fills Yes.
    r_blank = {"Due Date": "4/30/2025", "Date Received": "4/28/2025",
               "Statistical Exceedence?": "", "Review Notes": ""}
    _e, entry, _n = ww.diff_collection("qmr", [r_blank], _empty(), {})
    r_yes = dict(r_blank, **{"Statistical Exceedence?": "Yes",
                             "Review Notes": "Boron, chloride increasing."})
    events, _entry2, _n2 = ww.diff_collection("qmr", [r_yes], entry, {})
    assert len(events) == 1
    assert events[0]["kind"] == "changed"
    assert events[0]["severity"] == "notable"


def test_application_closure_transition_is_the_expansion_signal():
    # A Construction Permit application pending -> Issued must re-alert (urgent).
    pending = {"Application Type": "Construction Permit", "Receipt Date": "6/1/2026",
               "Closure Type": "", "Closure Date": ""}
    _e, entry, _n = ww.diff_collection("applications", [pending], _empty(), {})
    issued = dict(pending, **{"Closure Type": "Issued", "Closure Date": "9/1/2026"})
    events, _entry2, _n2 = ww.diff_collection("applications", [issued], entry, {})
    assert len(events) == 1
    assert events[0]["kind"] == "changed"
    assert events[0]["severity"] == "urgent"
    assert events[0]["risks"] == ["R1"]


def test_unchanged_rows_produce_no_events():
    r = {"Year": "2025", "Yrs Remaining End": "4.0", "Waste_Total": "3,662,137.11",
         "Total Permitted Capacity": "63560000"}
    _e, entry, _n = ww.diff_collection("annual", [r], _empty(), {})
    events, _entry2, _n2 = ww.diff_collection("annual", [r], entry, {})
    assert events == []


def test_bad_fetch_zero_rows_is_skipped_not_diffed():
    rows = [{"Due Date": f"q{i}", "Date Received": f"r{i}",
             "Statistical Exceedence?": "No", "Review Notes": ""} for i in range(10)]
    _e, entry, _n = ww.diff_collection("qmr", rows, _empty(), {})
    # A later run reads 0 rows (transient). Must NOT emit 10 "deletion"/"new"
    # events, and must leave the seen-set intact.
    events, entry2, note = ww.diff_collection("qmr", [], entry, {})
    assert events == []
    assert entry2 == entry                 # unchanged
    assert "bad fetch" in note.lower() or "suspected" in note.lower()


def test_bad_fetch_collapse_is_skipped():
    rows = [{"Due Date": f"q{i}", "Date Received": f"r{i}",
             "Statistical Exceedence?": "No", "Review Notes": ""} for i in range(10)]
    _e, entry, _n = ww.diff_collection("qmr", rows, _empty(), {})
    events, entry2, _n = ww.diff_collection("qmr", rows[:2], entry, {})  # 10 -> 2
    assert events == []
    assert entry2 == entry


def test_over_cap_silently_rebaselines():
    # Simulate an already-seeded collection that suddenly shows many new rows
    # (first-enable-without-seed / anomaly): cap=3, present 5 brand-new rows.
    seed = [{"Compliance Action Type": "x", "Compliance Action Date": "1/1/2000",
             "Corrective Action Component": "", "Company Response Date": ""}]
    _e, entry, _n = ww.diff_collection("compliance_actions", seed, _empty(),
                                       {"max_new_wds_alerts_per_run": 3})
    many = seed + [{"Compliance Action Type": "115 - VIOLATION NOTICE",
                    "Compliance Action Date": f"2/{i}/2026",
                    "Corrective Action Component": "", "Company Response Date": ""}
                   for i in range(1, 6)]
    events, entry2, note = ww.diff_collection("compliance_actions", many, entry,
                                              {"max_new_wds_alerts_per_run": 3})
    assert events == []                        # blast suppressed
    assert entry2["last_count"] == len(many)   # but all recorded (re-baselined)
    assert "cap" in note.lower()


# ---------------------------------------------------------------------------
# Classifier (D): WDS-specific, never the temperature scan
# ---------------------------------------------------------------------------

def test_compliance_action_paid_resolved_is_not_urgent():
    sev, _dt, _r = ww._classify_compliance_action(
        {"Compliance Action Type": "315 - STIPULATED PENALTY PAID/RESOLVED"}, False)
    assert sev == "watch"


def test_compliance_action_violation_is_urgent():
    sev, _dt, _r = ww._classify_compliance_action(
        {"Compliance Action Type": "115 - VIOLATION NOTICE"}, False)
    assert sev == "urgent"


def test_compliance_action_changed_violation_is_not_repeat_urgent():
    # Type is part of the record identity, so a `changed` adverse action is a
    # backfill (e.g. Company Response Date filled in) on a case we already
    # alerted urgent — it must NOT re-fire urgent.
    v = {"Compliance Action Type": "115 - VIOLATION NOTICE",
         "Compliance Action Date": "3/1/2026",
         "Corrective Action Component": "", "Company Response Date": ""}
    _e, entry, _n = ww.diff_collection("compliance_actions", [v], _empty(), {})
    v2 = dict(v, **{"Company Response Date": "4/1/2026"})   # WDS backfills a field
    events, _e2, _n2 = ww.diff_collection("compliance_actions", [v2], entry, {})
    assert len(events) == 1
    assert events[0]["kind"] == "changed"
    assert events[0]["severity"] == "notable"    # downgraded, not a duplicate urgent


def test_compliance_action_distinct_records_sharing_date_and_type_dont_collide():
    # EGLE's WDS grid can list multiple distinct compliance-action records
    # under the same (Compliance Action Date, Compliance Action Type) — e.g.
    # two separate corrective-action components of one violation notice (real
    # example: Arbor Hills' 11/3/2023 notice). Before the identity fix these
    # collapsed into one tracked slot and re-fired a false 'changed' event
    # every single run forever, even though nothing on EGLE's side changed.
    a = {"Compliance Action Type": "120 - VIOLATION NOTICE/LETTER OF WARNING",
         "Compliance Action Date": "11/3/2023", "Corrective Action Component": "Yes",
         "Company Response Due Date": "11/6/2023", "Company Response Date": "11/6/2023"}
    b = {"Compliance Action Type": "120 - VIOLATION NOTICE/LETTER OF WARNING",
         "Compliance Action Date": "11/3/2023", "Corrective Action Component": "Yes",
         "Company Response Due Date": "1/6/2024", "Company Response Date": "1/6/2024"}
    _e, entry, _n = ww.diff_collection("compliance_actions", [a, b], _empty(), {})
    assert len(entry["records"]) == 2          # tracked as two distinct records
    # Re-polling the SAME two rows must be a no-op forever, not flap.
    events, entry2, _n = ww.diff_collection("compliance_actions", [a, b], entry, {})
    assert events == []
    assert entry2 == entry


def test_operating_license_is_notable_construction_permit_urgent():
    assert ww._classify_application({"Application Type": "Operating License"}, False)[0] == "notable"
    assert ww._classify_application({"Application Type": "Construction Permit"}, False)[0] == "urgent"


def test_annual_below_floor_is_notable():
    below = ww._classify_annual({"Yrs Remaining End": "2.5"}, False, floor=3.0)
    above = ww._classify_annual({"Yrs Remaining End": "6.0"}, False, floor=3.0)
    assert below[0] == "notable"
    assert above[0] == "watch"


def test_annual_zero_years_is_notable():
    """0.0 years remaining = airspace exhausted, the single most R1-critical
    signal. `0.0` is falsy in Python, so the old `yrs and yrs < floor` guard
    short-circuited it to watch. It must classify as notable/evidence/R1."""
    exhausted = ww._classify_annual({"Yrs Remaining End": "0.0"}, False, floor=3.0)
    assert exhausted == ("notable", "evidence", ["R1"])
    # nearby regression cases: just-below floor -> notable, at/above -> watch
    assert ww._classify_annual({"Yrs Remaining End": "2.4"}, False, floor=3.0)[0] == "notable"
    assert ww._classify_annual({"Yrs Remaining End": "5.0"}, False, floor=3.0)[0] == "watch"
    # a blank/absent field is a valid-data absence, not zero -> watch
    assert ww._classify_annual({"Yrs Remaining End": ""}, False, floor=3.0)[0] == "watch"
    assert ww._classify_annual({}, False, floor=3.0)[0] == "watch"


# ---------------------------------------------------------------------------
# Sheet row shape
# ---------------------------------------------------------------------------

def test_wds_event_row_shape():
    ev = {"date": "4/30/2025", "kind": "new", "name": "qmr", "severity": "notable",
          "risks": ["R5"], "label": "QMR groundwater report", "detail": "Yes; boron."}
    row = sw.wds_event_row(ev)
    assert len(row) == len(sw.WDS_HEADERS)
    assert row[0] == "4/30/2025"
    assert row[3] == "notable"
    assert row[4] == "R5"


def test_wds_event_row_uses_site_aware_link():
    ev = {"date": "6/1/2026", "kind": "new", "name": "applications", "severity": "urgent",
          "risks": ["R1"], "label": "Construction Permit", "detail": "pending",
          "link": "https://www.egle.state.mi.us/wdspi/Dashboard.aspx?w=999999"}
    assert sw.wds_event_row(ev)[7] == "https://www.egle.state.mi.us/wdspi/Dashboard.aspx?w=999999"


# ---------------------------------------------------------------------------
# Annual parser: attribute-order tolerant, reads capacity/years (the strict
# single-regex used to blank these on class-first rows and kill the R1 alert)
# ---------------------------------------------------------------------------

def test_parse_annual_reads_capacity_and_sums_waste_inputs():
    # Real WDS markup shapes, reproduced: capacity/years render as detailControl
    # spans in CLASS-FIRST attribute order (the order that returned '' from the old
    # strict regex), and per-stream waste figures render as editable-grid <input>
    # values (WasteVolumeCYTextBox / WasteAmtTonsTextBox) — NOT <td> text, which is
    # why the old <td>-cell reader produced no tonnage at all.
    def span(title, val):  # class BEFORE title
        return (f'<span class="detailControl plainText2ca" '
                f'id="ctl00_Body_ReportList_R_ctl00_x" title="{title}:">{val}</span>')

    def stream(cyds, tons):  # one waste row, value-before-name to prove tolerance
        return (f'<input value="{cyds}" type="text" name="ctl00$Body$WasteVolumeCYTextBox" />'
                f'<input type="text" name="ctl00$Body$WasteAmtTonsTextBox" value="{tons}" />')

    h = ("<html><body>"
         '<span id="ctl00_Body_ReportList_R_ctl00_container"></span>'   # row anchor
         + stream("1000.00", "500.00") + stream("234.50", "120.25")
         + stream("", "")              # empty add-a-row template must not break the sum
         + span("Total Permitted Capacity", "63560000")
         + span("Estimated years of capacity remaining at end of year", "2.5")
         + "<span>2025</span>"          # the year, for the >(\\d{4})< pick-up
         + "</body></html>")
    rows = wc._parse_annual(h)
    assert len(rows) == 1
    assert rows[0]["Year"] == "2025"
    # Capacity/years must survive class-first attribute order (the R1 signal).
    assert rows[0]["Total Permitted Capacity"] == "63560000"
    assert rows[0]["Yrs Remaining End"] == "2.5"
    # Waste totals are summed from the <input> values (thousands-formatted).
    assert rows[0]["Waste_Total"] == "1,234.50"    # 1000.00 + 234.50
    assert rows[0]["Waste_Tons"] == "620.25"       # 500.00 + 120.25
    # And the parsed years-remaining drives the R1 floor classifier.
    assert ww._classify_annual(rows[0], False, floor=3.0)[0] == "notable"


# ---------------------------------------------------------------------------
# Orchestration: a failed urgent email must NOT bury the signal
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# historical_events(): the one-off bulk-dump path — same classification as
# diff_collection(), but no seen-state, no alerts, kind='historical' always.
# ---------------------------------------------------------------------------

def test_historical_events_one_per_row_matches_live_classification():
    rows = [
        {"Due Date": "4/30/2025", "Date Received": "4/28/2025",
         "Statistical Exceedence?": "Yes", "Review Notes": "Boron trend."},
        {"Due Date": "1/31/2025", "Date Received": "1/30/2025",
         "Statistical Exceedence?": "No", "Review Notes": ""},
    ]
    events = ww.historical_events("qmr", rows, {})
    assert len(events) == 2
    assert all(e["kind"] == "historical" for e in events)
    # Severity matches what the live classifier independently produces for the
    # same rows — historical_events reuses _classify_qmr, doesn't reimplement it.
    assert events[0]["severity"] == "notable"   # exceedance = Yes
    assert events[1]["severity"] == "watch"


# ===========================================================================
# ADR 043 — the last coverage-matrix gap: penalties + composting/utilization.
# ===========================================================================

# --- Registry consistency: fetchers and collections stay in lockstep -------

def test_fetchers_and_collections_cover_the_same_names():
    # A new collection must be wired in BOTH the fetch layer and the classify
    # layer; check_wds/wds_archiver iterate one against the other, so a name in
    # only one would silently go unfetched or unclassified.
    assert set(wc.FETCHERS) == set(ww.COLLECTIONS)


def test_new_collections_are_registered():
    for name in ("penalties", "composting_registrations", "composting_reports"):
        assert name in ww.COLLECTIONS
        assert name in wc.FETCHERS


# --- Penalties: parser (nested penalty->payment sub-grid) -------------------

def _pen_row(parent, idx, ptype, amt, doc, pay_id):
    # A penalty-CONTAINER SummaryRow: ..._U_R_ctlN_SummaryRow (no _C_R_ segment),
    # matching the real 475946 id shape.
    return (f'<tr id="ctl00_Body_ComplianceActionsL_R_{parent}_T_{parent}_U_R_ctl{idx:02d}_SummaryRow">'
            f'<td></td><td>{ptype}</td><td>{amt}</td><td>{doc}</td>'
            f'<td>{pay_id}</td><td></td></tr>')


def _pay_row(parent, idx, sd, sa, pd, pa):
    # A payment CHILD SummaryRow: ..._U_R_ctlN_C_R_ctlK_SummaryRow (the _C_R_
    # segment is what marks it a payment, independent of the scheduled-date cell).
    return (f'<tr id="ctl00_Body_ComplianceActionsL_R_{parent}_T_{parent}_U_R_ctl{idx:02d}_C_R_ctl00_SummaryRow">'
            f'<td>{sd}</td><td>{sa}</td><td>{pd}</td><td>{pa}</td></tr>')


def _ca_page(*, parent="ctl01", date="5/25/2023",
             atype="311 - STATE COMPLIANCE ORDER 3008(A)", penalties=()):
    """A minimal ComplianceActions page: one parent action DetailEditRow plus its
    nested penalty/payment SummaryRow rows in the REAL id structure the live parser
    keys on. Each `penalties` entry is (ptype, amount, doc, pay_id, sched_date,
    sched_amt, paid_date, paid_amt); a payment child row is emitted only when any
    payment field is given."""
    parts = [
        f'<tr id="ctl00_Body_ComplianceActionsL_R_{parent}_DetailEditRow"><td>'
        f'Compliance Action Date: {date} Compliance Action Type: {atype} '
        f'Determined By: EGLE</td></tr>'
    ]
    for i, (ptype, amt, doc, pay_id, sd, sa, pd, pa) in enumerate(penalties):
        parts.append(_pen_row(parent, i, ptype, amt, doc, pay_id))
        if sd or sa or pd or pa:
            parts.append(_pay_row(parent, i, sd, sa, pd, pa))
    return "<html><body><table>" + "".join(parts) + "</table></body></html>"


def test_parse_penalties_pairs_penalty_with_its_payment_and_reads_parent():
    h = _ca_page(penalties=[
        ("FA - FINAL MONETARY PENALTY", "$15,300.00", "115-05-2023", "RMD60021",
         "6/26/2023", "$15,300.00", "6/2/2023", "$15,300.00"),
        ("AC - FINAL ASSESSED COSTS", "$1,424.46", "115-05-2023", "RMD60021",
         "6/26/2023", "$1,424.46", "6/2/2023", "$1,424.46"),
    ])
    rows = wc._parse_penalties_page(h)
    assert len(rows) == 2                       # two penalties under one action
    fa, ac = rows
    # Parent action fields are joined onto each penalty.
    assert fa["Action Date"] == "5/25/2023"
    assert fa["Action Type"] == "311 - STATE COMPLIANCE ORDER 3008(A)"
    assert fa["Penalty Type"] == "FA - FINAL MONETARY PENALTY"
    assert fa["Assessment Amount"] == "$15,300.00"
    assert fa["Document #"] == "115-05-2023"
    # The nested payment child is paired onto the right penalty (document order).
    assert fa["Date Paid"] == "6/2/2023"
    assert fa["Amount Paid"] == "$15,300.00"
    assert ac["Penalty Type"] == "AC - FINAL ASSESSED COSTS"
    assert ac["Amount Paid"] == "$1,424.46"


def test_new_unpaid_penalty_parses_with_empty_payment_fields():
    # The headline signal: a newly assessed penalty with NO payment child yet.
    # It must parse (notable) with blank payment fields, not be dropped.
    h = _ca_page(penalties=[
        ("FA - FINAL MONETARY PENALTY", "$355,109.00", "2020-0593-CE", "MUL40006",
         "", "", "", ""),
    ])
    rows = wc._parse_penalties_page(h)
    assert len(rows) == 1
    assert rows[0]["Assessment Amount"] == "$355,109.00"
    assert rows[0]["Date Paid"] == "" and rows[0]["Amount Paid"] == ""
    assert ww._classify_penalty(rows[0], False)[0] == "notable"


def test_payment_with_blank_scheduled_date_still_pairs():
    # A payment child is recognized by its _C_R_ id + a dollar amount, NOT by the
    # scheduled-date cell — which is sometimes blank (paid but never scheduled).
    # Keying on the date cell would silently drop the "paid" backfill signal.
    h = _ca_page(penalties=[
        ("FA - FINAL MONETARY PENALTY", "$355,109.00", "2020-0593-CE", "MUL40006",
         "", "", "5/1/2022", "$355,109.00"),   # blank scheduled date, real payment
    ])
    rows = wc._parse_penalties_page(h)
    assert len(rows) == 1
    assert rows[0]["Scheduled Date"] == ""
    assert rows[0]["Date Paid"] == "5/1/2022"
    assert rows[0]["Amount Paid"] == "$355,109.00"


def test_non_penalty_container_row_is_not_a_bogus_penalty():
    # Most _U_R_ container rows are EMPTY (an action with no penalty still renders
    # one). A container whose cells don't carry a penalty-type code + document # +
    # dollar amount must NOT parse as a penalty — the guard against a stray
    # two-letter-coded summary row (e.g. a status code) becoming a bogus $ penalty.
    empty_container = _pen_row("ctl02", 0, "", "", "", "")
    stray = _pen_row("ctl02", 1, "MI - Some Status", "not-a-dollar-amount", "", "")
    h = "<html><body><table>" + empty_container + stray + "</table></body></html>"
    assert wc._parse_penalties_page(h) == []


def test_empty_container_between_penalty_and_payment_blocks_mispairing():
    # An empty penalty container CLOSES the prior penalty's payment section, so a
    # later unrelated payment child cannot pair back onto an earlier penalty.
    h = ("<html><body><table>"
         + _pen_row("ctl01", 0, "FA - FINAL MONETARY PENALTY", "$750.00", "D1", "P1")
         + _pen_row("ctl02", 0, "", "", "", "")            # empty container resets
         + _pay_row("ctl02", 0, "1/1/2030", "$999.00", "1/1/2030", "$999.00")
         + "</table></body></html>")
    rows = wc._parse_penalties_page(h)
    assert len(rows) == 1
    assert rows[0]["Assessment Amount"] == "$750.00"
    assert rows[0]["Amount Paid"] == ""       # the $999 payment did NOT bleed onto it


def test_unrelated_summary_row_does_not_orphan_the_payment():
    # A penalty is positively identified by a `_U_R_` container id (not merely
    # "not a payment"). An unrelated summary row (neither `_U_R_` nor `_C_R_`)
    # sitting between a penalty and its payment child must be IGNORED — it must
    # not reset `last` and orphan the payment.
    other = ('<tr id="ctl00_Body_ComplianceActionsL_R_ctl01_T_ctl01_X_R_ctl00_SummaryRow">'
             '<td>unrelated</td><td>row</td></tr>')
    h = ("<html><body><table>"
         + _pen_row("ctl01", 0, "FA - FINAL MONETARY PENALTY", "$750.00", "D1", "P1")
         + other
         + _pay_row("ctl01", 0, "7/1/2024", "$750.00", "7/2/2024", "$750.00")
         + "</table></body></html>")
    rows = wc._parse_penalties_page(h)
    assert len(rows) == 1
    assert rows[0]["Amount Paid"] == "$750.00"     # payment still pairs


def test_penalty_and_payment_never_straddle_pages():
    # Each ComplianceActions page is parsed independently (_parse_penalties_page),
    # so a payment child with no penalty row ABOVE IT ON THE SAME PAGE is ignored —
    # a penalty and its payment can never be paired across a page boundary. (On the
    # live 475946 grid all six pairs sit on one page; this asserts the structural
    # guarantee rather than relying on that.)
    payment_only = _pay_row("ctl01", 0, "6/26/2023", "$15,300.00", "6/2/2023", "$15,300.00")
    assert wc._parse_penalties_page(payment_only) == []


# --- Penalties: classifier + diff (identity, collision, backfill) -----------

def _penalty(ptype, amt, doc, paid_date="", paid_amt="", sched_date="", sched_amt=""):
    return {"Action Date": "5/25/2023", "Action Type": "311 - STATE COMPLIANCE ORDER 3008(A)",
            "Penalty Type": ptype, "Assessment Amount": amt, "Document #": doc,
            "Penalty Payment ID": "RMD60021", "Scheduled Date": sched_date,
            "Scheduled Amount": sched_amt, "Date Paid": paid_date, "Amount Paid": paid_amt}


def test_new_penalty_is_notable_not_urgent():
    # Deliberately NOT urgent — compliance_actions already fires urgent on the
    # parent enforcement event on the same page; urgent here would double-fire.
    sev, dtype, risks = ww._classify_penalty(
        _penalty("FA - FINAL MONETARY PENALTY", "$355,109.00", "2020-0593-CE"), False)
    assert sev == "notable"
    assert risks == ["R2"]


def test_penalty_payment_backfill_is_a_single_changed_watch():
    unpaid = _penalty("FA - FINAL MONETARY PENALTY", "$355,109.00", "2020-0593-CE",
                      paid_amt="$0.00", sched_date="4/8/2022", sched_amt="$355,109.00")
    _e, entry, _n = ww.diff_collection("penalties", [unpaid], _empty(), {})
    paid = dict(unpaid, **{"Date Paid": "5/1/2022", "Amount Paid": "$355,109.00"})
    events, _e2, _n2 = ww.diff_collection("penalties", [paid], entry, {})
    assert len(events) == 1
    assert events[0]["kind"] == "changed"
    assert events[0]["severity"] == "watch"     # penalty getting paid = good news


def test_penalties_sharing_date_and_doc_dont_collide():
    # The real 5/25/2023 pair on doc 115-05-2023: an FA ($15,300) and an AC
    # ($1,424.46). They share Action Date AND Document # — Penalty Type +
    # Assessment Amount are in the identity to keep them two distinct records
    # (else they'd flap 'changed' against each other every run, the collision the
    # compliance_actions identity fix guards against).
    fa = _penalty("FA - FINAL MONETARY PENALTY", "$15,300.00", "115-05-2023",
                  paid_date="6/2/2023", paid_amt="$15,300.00")
    ac = _penalty("AC - FINAL ASSESSED COSTS", "$1,424.46", "115-05-2023",
                  paid_date="6/2/2023", paid_amt="$1,424.46")
    _e, entry, _n = ww.diff_collection("penalties", [fa, ac], _empty(), {})
    assert len(entry["records"]) == 2
    # Re-polling the identical two rows must be a permanent no-op, not flap.
    events, entry2, _n2 = ww.diff_collection("penalties", [fa, ac], entry, {})
    assert events == []
    assert entry2 == entry


def test_new_penalty_after_baseline_alerts_notable():
    fa = _penalty("FA - FINAL MONETARY PENALTY", "$15,300.00", "115-05-2023")
    _e, entry, _n = ww.diff_collection("penalties", [fa], _empty(), {})
    ac = _penalty("AC - FINAL ASSESSED COSTS", "$1,424.46", "115-05-2023")
    events, _e2, _n2 = ww.diff_collection("penalties", [fa, ac], entry, {})
    assert len(events) == 1
    assert events[0]["kind"] == "new"
    assert events[0]["severity"] == "notable"
    assert events[0]["risks"] == ["R2"]


# --- Composting registrations ----------------------------------------------

def _reg(receipt, status, expires="8/5/2030", complete="Yes"):
    return {"Application Receipt Date": receipt, "Registration Status": status,
            "Registration Expiration Date": expires, "Is Administratively Complete?": complete,
            "Admin Completeness Review Date": "8/5/2025", "Registration Types": ""}


def test_new_composting_registration_is_notable_status_change_is_watch():
    assert ww._classify_composting_registration(_reg("5/20/2025", "Accepting from public"), False)[0] == "notable"
    assert ww._classify_composting_registration(_reg("5/20/2025", "EXPIRED"), True)[0] == "watch"
    # R1 — the compost parcel next to the landfill.
    assert ww._classify_composting_registration(_reg("5/20/2025", "Accepting from public"), False)[2] == ["R1"]


def test_composting_registration_diff_new_then_status_flip():
    active = _reg("5/20/2025", "Accepting from public")
    _e, entry, _n = ww.diff_collection("composting_registrations", [active], _empty(), {})
    # A NEW registration (new receipt date) after baseline = notable.
    fresh = _reg("6/1/2027", "Accepting from public", expires="6/1/2032")
    events, entry2, _n2 = ww.diff_collection("composting_registrations", [active, fresh], entry, {})
    assert len(events) == 1 and events[0]["kind"] == "new" and events[0]["severity"] == "notable"
    # An in-place status flip on the existing registration = changed/watch.
    expired = dict(active, **{"Registration Status": "EXPIRED"})
    events2, _e3, _n3 = ww.diff_collection("composting_registrations", [expired, fresh], entry2, {})
    assert len(events2) == 1 and events2[0]["kind"] == "changed" and events2[0]["severity"] == "watch"


# --- Composting report-years (RptYr parser + classifier) --------------------

def _rptyr_span(row, title, value):
    idpart = f'id="ctl00_Body_RptYr_R_ctl{row:02d}_D_x"'
    cls = 'class="detailControl plainText2ca"'
    titleattr = f'title="{title}:"' if title else ''
    return f"<span {idpart} {titleattr} {cls}>{value}</span>"


def test_parse_composting_reports_keeps_all_tonnages_and_drops_template():
    # One real report-year row REPEATS Product Types / quantity spans; the fingerprint
    # must keep ALL of them (last-wins grouping would drop the finished-compost line).
    # The trailing Year "0" row is WDS's add-a-record template and must be filtered.
    h = "<html><body>" + "".join([
        _rptyr_span(0, "Year", "2025"),
        _rptyr_span(0, "Product Types", "YC - Yard clippings"),
        _rptyr_span(0, "", "35,053.0"),
        _rptyr_span(0, "", "YARD WASTE FROM 7 COUNTIES"),
        _rptyr_span(0, "Product Types", "FC - Finished compost"),
        _rptyr_span(0, "", "93,442.0"),
        _rptyr_span(1, "Year", "0"),          # add-a-record template
    ]) + "</body></html>"
    rows = wc._parse_composting_reports(h)
    assert len(rows) == 1                       # the Year "0" template row is dropped
    assert rows[0]["Year"] == "2025"
    detail = rows[0]["Report Detail"]
    # BOTH product types + BOTH tonnages survive (proving not last-wins).
    for token in ("YC - Yard clippings", "35,053.0", "FC - Finished compost", "93,442.0"):
        assert token in detail


def test_composting_report_new_year_is_watch_and_backfill_re_alerts():
    r2024 = {"Year": "2024", "Report Detail": "YC | 130,844.0"}
    _e, entry, _n = ww.diff_collection("composting_reports", [r2024], _empty(), {})
    # A new report year appearing = watch (R1 refresh), date is the bare year.
    r2025 = {"Year": "2025", "Report Detail": "YC | 35,053.0"}
    events, entry2, _n2 = ww.diff_collection("composting_reports", [r2024, r2025], entry, {})
    assert len(events) == 1
    assert events[0]["kind"] == "new"
    assert events[0]["severity"] == "watch"
    assert events[0]["risks"] == ["R1"]
    assert events[0]["date"] == "2025"          # bare year, like annual
    # A later tonnage backfill on an existing year re-alerts once (changed/watch).
    r2025b = {"Year": "2025", "Report Detail": "YC | 35,053.0 | FC | 2,000.0"}
    events2, _e3, _n3 = ww.diff_collection("composting_reports", [r2024, r2025b], entry2, {})
    assert len(events2) == 1 and events2[0]["kind"] == "changed" and events2[0]["severity"] == "watch"


# --- Archiver URL-dedup key -------------------------------------------------

def test_page_url_shared_pages_collapse_for_archiver_dedup():
    # wds_archiver de-dups snapshots by page_url, so collections that read the same
    # page MUST resolve to the identical URL (penalties + compliance_actions on the
    # ComplianceActions page; the two composting grids on the Utilization page) and
    # genuinely distinct pages must not collide.
    w = "475946"
    assert wc.page_url("penalties", w) == wc.page_url("compliance_actions", w)
    assert wc.page_url("composting_registrations", w) == wc.page_url("composting_reports", w)
    assert wc.page_url("penalties", w) != wc.page_url("composting_reports", w)


def test_only_compliance_actions_still_defines_a_deadline_extractor():
    # The new collections carry no dated compliance obligation, so they must not
    # define a "deadline" extractor (ADR 025 invariant, re-asserted after the add).
    assert "deadline" in ww.COLLECTIONS["compliance_actions"]
    for name in ("penalties", "composting_registrations", "composting_reports"):
        assert "deadline" not in ww.COLLECTIONS[name]


def test_historical_events_drops_identity_less_rows():
    # The trailing blank add-a-record template row diff_collection() also drops.
    rows = [
        {"Application Type": "Construction Permit", "Receipt Date": "6/1/2020",
         "Closure Type": "Issued", "Closure Date": "9/1/2020"},
        {"Application Type": "", "Receipt Date": ""},
    ]
    events = ww.historical_events("applications", rows, {})
    assert len(events) == 1


def test_historical_events_annual_uses_floor():
    rows = [{"Year": "2025", "Yrs Remaining End": "2.5", "Waste_Total": "1000",
             "Total Permitted Capacity": "63560000"}]
    events = ww.historical_events("annual", rows, {"years_remaining_floor": 3.0})
    assert events[0]["severity"] == "notable"    # below the floor


def test_historical_events_ignores_seen_state_and_never_alerts():
    # No seen-state parameter at all -- calling it repeatedly on the same rows
    # must produce the same events every time (no diffing, no mutation).
    rows = [{"Due Date": "4/30/2025", "Date Received": "4/28/2025",
             "Statistical Exceedence?": "Yes", "Review Notes": "x"}]
    e1 = ww.historical_events("qmr", rows, {})
    e2 = ww.historical_events("qmr", rows, {})
    assert len(e1) == len(e2) == 1
    assert e1[0]["hash"] == e2[0]["hash"]
    assert e1[0]["prev_hash"] is None


def test_urgent_send_failure_reverts_seen_state_so_it_re_alerts():
    spec = ww.COLLECTIONS["applications"]
    old = {"Application Type": "Operating License", "Receipt Date": "1/1/2020",
           "Closure Type": "Issued", "Closure Date": "2/1/2020"}
    new_cp = {"Application Type": "Construction Permit", "Receipt Date": "6/1/2026",
              "Closure Type": "", "Closure Date": ""}
    # Pre-seed so `old` is already seen (records non-empty -> a real diff, not the
    # first-run baseline), then a new Construction Permit (urgent) shows up.
    seed = {"records": {ww._idkey(spec, old): ww._content_hash(spec, old)}, "last_count": 1}
    state = {"wds_seen": {"applications": seed}, "pending_digest": []}
    cfg = {"wds": {"collections": ["applications"], "site_id": "475946"}}
    fetch = {"applications": lambda w: [old, new_cp]}

    def boom(subject, body, cfg):
        raise RuntimeError("smtp down")

    ww.check_wds(state, cfg, boom, fetchers=fetch)
    recs = state["wds_seen"]["applications"]["records"]
    assert ww._idkey(spec, new_cp) not in recs      # NOT committed as seen
    assert ww._idkey(spec, old) in recs             # untouched record stays seen

    # Next run with a working mailer re-alerts and delivers it.
    sent = []
    ww.check_wds(state, cfg, lambda s, b, c: sent.append(s), fetchers=fetch)
    assert len(sent) == 1
    assert "Construction Permit" in sent[0]
    assert ww._idkey(spec, new_cp) in state["wds_seen"]["applications"]["records"]


# ---------------------------------------------------------------------------
# ADR 025: compliance_actions maps its structured fields onto the six-field
# deadline schema; no other collection defines a deadline extractor.
# ---------------------------------------------------------------------------

def test_compliance_action_event_maps_deadline_fields():
    r = {"Compliance Action Type": "Violation Notice",
         "Compliance Action Date": "10/10/2023",
         "Corrective Action Component": "Submit response",
         "Company Response Due Date": "11/9/2023",
         "Company Response Date": "11/3/2023",
         "Lead Program": "Solid Waste", "Determined By": "EGLE"}
    ev = ww._event_from_row("compliance_actions", r, "new", {})
    dl = ev["deadline"]
    assert dl["item_description"] == "Submit response"
    assert dl["due_date"] == "2023-11-09"
    assert dl["actual_completion_date"] == "2023-11-03"
    assert dl["compliance_doc_effective_date"] == "2023-10-10"
    assert "Violation Notice" in dl["compelled_by"]
    assert dl["extension_due_date"] == ""          # WDS has no extension field


def test_only_compliance_actions_defines_a_deadline_extractor():
    assert "deadline" in ww.COLLECTIONS["compliance_actions"]
    for name in ("qmr", "applications", "annual", "evaluations"):
        assert "deadline" not in ww.COLLECTIONS[name]
