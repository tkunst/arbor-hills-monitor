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


def test_operating_license_is_notable_construction_permit_urgent():
    assert ww._classify_application({"Application Type": "Operating License"}, False)[0] == "notable"
    assert ww._classify_application({"Application Type": "Construction Permit"}, False)[0] == "urgent"


def test_annual_below_floor_is_notable():
    below = ww._classify_annual({"Yrs Remaining End": "2.5"}, False, floor=3.0)
    above = ww._classify_annual({"Yrs Remaining End": "6.0"}, False, floor=3.0)
    assert below[0] == "notable"
    assert above[0] == "watch"


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
