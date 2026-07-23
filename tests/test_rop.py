"""rop_client.py / rop_watcher.py — the activation gate, pure CSV/folder/notice
parsing, the pure snapshot/diff helpers, and the full baseline/unchanged/
changed/fetch-fail flows driven through a fake Sheets service (no network, no
creds). Reuses FakeSheets from test_pfas_watcher, same idiom as
test_civicclerk_watcher. The CSV fixture below is a TRIMMED, VERBATIM copy of
real rows from the 2026-07-15 EPA ROP Monthly Report (the handoff's baseline) —
not fabricated data — so the M3333-exclusion and column-layout assumptions are
pinned against the real export shape."""
import copy

import fitz
import pytest

import rop_client as rc
import rop_watcher as rw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# CSV fixture — trimmed, verbatim rows from the real 2026-07-15 export
# ==============================================================================

_HEADER_ROW1 = "Calculated,,,,,,,,Workflow Task,,,TASK,,,,,,,,,,"
_HEADER_ROW2 = (
    "AQD Unregistered ID / SRN,Name,Addr Line 1,Addr Line 2,City,County,District,"
    "SIC Codes,Name,Status,Created Date,Name,Status,Assigned,Due Date,"
    "Completed Date,Permit Number,Version,Status,Issue Date,Effective Date,"
    "Expiration Date"
)

_N2688_ROWS = [
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Initial,Complete,12/13/2024,Application shield and letter sent,Complete,,,5/28/2003,ROP0000224,1,Superseded,5/28/2003,5/28/2003,5/28/2008',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,12/18/2024,Application shield and letter sent,Complete,Dawn Hayslip,,5/11/2015,ROP0000224,3,In Process,,,',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,12/13/2024,Application shield and letter sent,Complete,,,1/24/2011,ROP0000224,2,Superseded,1/24/2011,1/24/2011,1/24/2016',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,12/18/2024,EPA Review (45 day) - Initiate,Unstarted,Kelly Orent,,,ROP0000224,3,In Process,,,',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,12/13/2024,EPA Review (45 day) - Initiate,Complete,,,1/24/2011,ROP0000224,2,Superseded,1/24/2011,1/24/2011,1/24/2016',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Minor Modification,Complete,12/13/2024,Send working draft conditions to applicant,Complete,,,1/24/2011,ROP0000224,2,Superseded,1/24/2011,1/24/2011,1/24/2016',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Minor Modification,Complete,12/13/2024,Send working draft conditions to applicant,Complete,,,3/28/2018,ROP0000224,2,Extended,3/28/2018,3/28/2018,1/24/2016',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Initial,Complete,12/13/2024,Send working draft conditions to applicant,Complete,,,5/28/2003,ROP0000224,1,Superseded,5/28/2003,5/28/2003,5/28/2008',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,12/18/2024,Send working draft conditions to applicant,Unstarted,Dawn Hayslip,,,ROP0000224,3,In Process,,,',
    'N2688,"Arbor Hills Landfill, Inc. (N2688)",10690 6 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,12/13/2024,Send working draft conditions to applicant,Complete,,,1/24/2011,ROP0000224,2,Superseded,1/24/2011,1/24/2011,1/24/2016',
]

_N1504_ROWS = [
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,10/22/2025,Application shield and letter sent,Complete,Diane Kavanaugh Vetort,,11/17/2025,ROP0000656,3,In Process,,,',
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,1/21/2025,Application shield and letter sent,Complete,,,1/21/2025,ROP0000656,2,Extended,3/28/2018,3/28/2018,3/28/2023',
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Initial,Complete,1/21/2025,Application shield and letter sent,Complete,Dawn Hayslip,,1/21/2025,ROP0000656,1,Superseded,5/28/2003,5/28/2003,5/28/2008',
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,10/22/2025,EPA Review (45 day) - Initiate,Pending,Kelly Orent,,,ROP0000656,3,In Process,,,',
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,1/21/2025,EPA Review (45 day) - Initiate,Complete,Kelly Orent,,1/21/2025,ROP0000656,2,Extended,3/28/2018,3/28/2018,3/28/2023',
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,10/22/2025,Send working draft conditions to applicant,Unstarted,Dawn Hayslip,,,ROP0000656,3,In Process,,,',
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,1/21/2025,Send working draft conditions to applicant,Complete,,,1/21/2025,ROP0000656,2,Extended,3/28/2018,3/28/2018,3/28/2023',
    'N1504,"Arbor Hills Energy, LLC (N1504)",10611 5 Mile Rd,,Northville,Washtenaw,Jackson,,ROP - Initial,Complete,1/21/2025,Send working draft conditions to applicant,Complete,,,1/21/2025,ROP0000656,1,Superseded,5/28/2003,5/28/2003,5/28/2008',
]

_P1488_ROWS = [
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Initial,Complete,12/13/2024,Application shield and letter sent,Complete,,,5/28/2003,ROP0000236,1,Superseded,5/28/2003,5/28/2003,5/28/2008",
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,12/13/2024,Application shield and letter sent,Complete,,,1/24/2011,ROP0000236,2,Extended,1/24/2011,1/24/2011,1/24/2016",
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,1/22/2025,Application shield and letter sent,Complete,Dawn Hayslip,,11/17/2025,ROP0000236,3,In Process,,,",
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,12/13/2024,EPA Review (45 day) - Initiate,Complete,,,1/24/2011,ROP0000236,2,Extended,1/24/2011,1/24/2011,1/24/2016",
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,1/22/2025,EPA Review (45 day) - Initiate,Pending,Kelly Orent,,,ROP0000236,3,In Process,,,",
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Initial,Complete,12/13/2024,Send working draft conditions to applicant,Complete,,,5/28/2003,ROP0000236,1,Superseded,5/28/2003,5/28/2003,5/28/2008",
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,Complete,12/13/2024,Send working draft conditions to applicant,Complete,,,1/24/2011,ROP0000236,2,Extended,1/24/2011,1/24/2011,1/24/2016",
    "P1488,Emerald RNG LLC (P1488),10719 West 5 Mile Road,,Northville,Washtenaw,Jackson,,ROP - Renewal,In Process,1/22/2025,Send working draft conditions to applicant,Complete,Dawn Hayslip,,6/4/2026,ROP0000236,3,In Process,,,",
]

# The "Emerald" name-collision (Conway Products d/b/a Emerald Spa Corp) — an
# UNRELATED facility that must never be pulled in by a name-substring match.
_M3333_ROWS = [
    "M3333,Conway Products Corporation d/b/a Emerald Spa Corp (M3333),4150 EAST PARIS AVE SE,,GRAND RAPIDS,Kent,Grand Rapids,,ROP - Initial,Complete,12/13/2024,Application shield and letter sent,Complete,,,5/12/1999,ROP0000154,1,Terminated,5/12/1999,5/12/1999,5/12/2004",
    "M3333,Conway Products Corporation d/b/a Emerald Spa Corp (M3333),4150 EAST PARIS AVE SE,,GRAND RAPIDS,Kent,Grand Rapids,,ROP - Renewal,Complete,12/13/2024,Application shield and letter sent,Complete,,,8/30/2006,ROP0000154,2,Terminated,8/30/2006,8/30/2006,8/30/2011",
    "M3333,Conway Products Corporation d/b/a Emerald Spa Corp (M3333),4150 EAST PARIS AVE SE,,GRAND RAPIDS,Kent,Grand Rapids,,ROP - Renewal,Complete,12/13/2024,Application shield and letter sent,Complete,,,8/22/2011,ROP0000154,3,Terminated,8/22/2011,8/22/2011,8/22/2016",
]

# An unrelated facility (Ada Cogeneration, N1784) — pads the fixture with a row
# that must ALSO be excluded, distinct from the M3333 name-collision case.
_OTHER_ROW = (
    "N1784,Ada Cogeneration LLC (N1784),7575 Fulton Sreet East,,Ada,Kent,"
    "Grand Rapids,Electric and Other Services Combined (4931),ROP - Renewal,"
    "Complete,12/13/2024,Application shield and letter sent,Complete,,,"
    "12/17/2024,ROP0000211,6,In Effect,6/24/2025,6/24/2025,6/24/2030"
)


def baseline_csv() -> str:
    rows = [_HEADER_ROW1, _HEADER_ROW2, _OTHER_ROW]
    rows += _N2688_ROWS + _N1504_ROWS + _P1488_ROWS + _M3333_ROWS
    return "\n".join(rows) + "\n"


def csv_with(replacements: dict[str, str]) -> str:
    """baseline_csv() with each `old substring -> new substring` applied once —
    for building a 'the same CSV, but one field changed' fixture."""
    text = baseline_csv()
    for old, new in replacements.items():
        assert old in text, f"fixture substring not found: {old!r}"
        text = text.replace(old, new, 1)
    return text


# ==============================================================================
# CSV parsing (pure)
# ==============================================================================


def test_parse_extracts_only_target_srns():
    rows = rc.parse_csv_rows(baseline_csv())
    srns = {r["srn"] for r in rows}
    assert srns == {"N2688", "N1504", "P1488"}


def test_parse_excludes_m3333_emerald_name_collision():
    rows = rc.parse_csv_rows(baseline_csv())
    assert all(r["srn"] != "M3333" for r in rows)
    assert not any("Conway" in r["name"] for r in rows)
    # The REAL Emerald (P1488) rows must still be present.
    assert any(r["srn"] == "P1488" and "Emerald RNG" in r["name"] for r in rows)


def test_parse_excludes_unrelated_facility():
    rows = rc.parse_csv_rows(baseline_csv())
    assert all(r["srn"] != "N1784" for r in rows)


def test_parse_row_counts_match_fixture():
    rows = rc.parse_csv_rows(baseline_csv())
    assert sum(1 for r in rows if r["srn"] == "N2688") == len(_N2688_ROWS)
    assert sum(1 for r in rows if r["srn"] == "N1504") == len(_N1504_ROWS)
    assert sum(1 for r in rows if r["srn"] == "P1488") == len(_P1488_ROWS)


def test_parse_field_positions_are_correct():
    rows = rc.parse_csv_rows(baseline_csv())
    n2688_renewal_epa = next(
        r for r in rows if r["srn"] == "N2688"
        and r["task_name"] == "EPA Review (45 day) - Initiate"
        and r["version"] == "3")
    assert n2688_renewal_epa["rop_action"] == "ROP - Renewal"
    assert n2688_renewal_epa["rop_action_status"] == "In Process"
    assert n2688_renewal_epa["task_status"] == "Unstarted"
    assert n2688_renewal_epa["permit_number"] == "ROP0000224"
    assert n2688_renewal_epa["permit_status"] == "In Process"


def test_parse_raises_on_bad_header_column_count():
    bad = _HEADER_ROW1 + "\n" + "A,B,C\n" + _N2688_ROWS[0]
    with pytest.raises(rc.RopParseError):
        rc.parse_csv_rows(bad)


def test_parse_raises_on_too_few_lines():
    with pytest.raises(rc.RopParseError):
        rc.parse_csv_rows(_HEADER_ROW1)


def test_parse_srns_argument_narrows_selection():
    rows = rc.parse_csv_rows(baseline_csv(), srns=("N2688",))
    assert {r["srn"] for r in rows} == {"N2688"}


# ==============================================================================
# N2688 folder listing (pure)
# ==============================================================================


def folder_html(entries, *, include_parent=True):
    """entries: list of (date, time, size_or_none_for_dir, href, name)."""
    lines = []
    if include_parent:
        lines.append('<A HREF="/aps/downloads/ROP/pub_ntce/">[To Parent Directory]</A><br><br>')
    for date, time, size, href, name in entries:
        token = "&lt;dir&gt;" if size is None else str(size)
        lines.append(f'{date}  {time}       {token} <A HREF="{href}">{name}</A><br>')
    pad = "<!-- " + "x" * 250 + " -->"
    return "<html><head><title>listing</title></head><body><pre>" + "".join(lines) + pad + "</pre></body></html>"


_REAL_FOLDER_ENTRIES = [
    ("3/28/2018", "9:51 AM", 955852,
     "/aps/downloads/ROP/pub_ntce/N2688/N2688%20FINAL%2003-28-18.docx",
     "N2688 FINAL 03-28-18.docx"),
    ("3/28/2018", "8:24 AM", 2145041,
     "/aps/downloads/ROP/pub_ntce/N2688/N2688%20FINAL%2003-28-18.pdf",
     "N2688 FINAL 03-28-18.pdf"),
    ("12/19/2022", "10:47 AM", None,
     "/aps/downloads/ROP/pub_ntce/N2688/Plans/", "Plans"),
]


def test_parse_folder_listing_skips_parent_directory_link():
    html = folder_html(_REAL_FOLDER_ENTRIES)
    entries = rc.parse_folder_listing(html)
    assert all("Parent Directory" not in e["name"] for e in entries)
    assert len(entries) == len(_REAL_FOLDER_ENTRIES)


def test_parse_folder_listing_flags_directories():
    html = folder_html(_REAL_FOLDER_ENTRIES)
    entries = rc.parse_folder_listing(html)
    plans = next(e for e in entries if e["name"] == "Plans")
    assert plans["is_dir"] is True
    pdf = next(e for e in entries if e["name"].endswith(".pdf"))
    assert pdf["is_dir"] is False


def test_parse_folder_listing_unescapes_and_sorts_by_name():
    html = folder_html(_REAL_FOLDER_ENTRIES)
    entries = rc.parse_folder_listing(html)
    names = [e["name"] for e in entries]
    assert names == sorted(names)
    assert "N2688 FINAL 03-28-18.docx" in names


def test_parse_folder_listing_empty_when_no_entries():
    html = folder_html([], include_parent=True)
    assert rc.parse_folder_listing(html) == []


# ==============================================================================
# Statewide public-notice PDF (pure, over a synthetic in-memory PDF)
# ==============================================================================


def make_notice_pdf(body_text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), body_text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def test_notice_mentions_srn_true_with_context():
    pdf = make_notice_pdf("Public notice for permit ROP0000224.\nN2688 renewal comment period open.")
    mentioned, context = rc.notice_mentions_srn(pdf, "N2688")
    assert mentioned is True
    assert "N2688" in context


def test_notice_mentions_srn_false_when_absent():
    pdf = make_notice_pdf("Public notice for an unrelated facility N9999.")
    mentioned, context = rc.notice_mentions_srn(pdf, "N2688")
    assert mentioned is False and context == ""


def test_notice_mentions_srn_is_whole_word_only():
    # A longer token containing N2688 as a substring must NOT match.
    pdf = make_notice_pdf("Reference code XN2688Z appears here, not the real SRN.")
    mentioned, _ = rc.notice_mentions_srn(pdf, "N2688")
    assert mentioned is False


def test_notice_mentions_srn_raises_ropfetcherror_on_corrupt_pdf():
    # fetch_notice_pdf's "%PDF" magic-byte check only confirms the HEADER — a
    # truncated download can still start with "%PDF" while the rest is
    # unparseable. That must surface as RopFetchError (routed through the
    # normal skip-and-warn/loud fail-safe), never a raw, uncaught fitz/mupdf
    # exception that would crash the whole run.
    corrupt = b"%PDF-1.7\n" + b"\x00" * 50  # valid magic bytes, garbage body
    with pytest.raises(rc.RopFetchError):
        rc.notice_mentions_srn(corrupt, "N2688")


def test_notice_mentions_srn_does_not_mask_a_genuine_bug_as_ropfetcherror(monkeypatch):
    # notice_mentions_srn must catch ONLY fitz.FileDataError (corrupt/truncated
    # body), never a bare `except Exception`. A genuine bug elsewhere in this
    # function (or a future pymupdf API change) has to surface as an uncaught,
    # loud crash — not get relabeled as a routine transient RopFetchError and
    # skip-and-warned forever once notice:N2688 has a baseline.
    def _boom(*a, **k):
        raise ValueError("some unrelated programming bug, not a PDF problem")
    monkeypatch.setattr(rc.fitz, "open", _boom)
    with pytest.raises(ValueError):
        rc.notice_mentions_srn(b"%PDF-1.7\nirrelevant", "N2688")


# ==============================================================================
# Snapshot / hash / diff (pure)
# ==============================================================================


def test_facility_snapshot_hash_stable_across_row_order():
    rows = rc.parse_csv_rows(baseline_csv())
    a = rw.facility_snapshot(rows, "N2688")
    rows_shuffled = list(reversed(rows))
    b = rw.facility_snapshot(rows_shuffled, "N2688")
    assert rw.snapshot_hash(a) == rw.snapshot_hash(b)


def test_facility_snapshot_changes_when_task_status_advances():
    rows_a = rc.parse_csv_rows(baseline_csv())
    rows_b = rc.parse_csv_rows(csv_with({
        "EPA Review (45 day) - Initiate,Unstarted,Kelly Orent,,,ROP0000224,3,In Process,,,":
        "EPA Review (45 day) - Initiate,Started,Kelly Orent,,,ROP0000224,3,In Process,,,",
    }))
    a = rw.facility_snapshot(rows_a, "N2688")
    b = rw.facility_snapshot(rows_b, "N2688")
    assert rw.snapshot_hash(a) != rw.snapshot_hash(b)
    note, body = rw.summarize_facility_change(a, b)
    assert "new task/version row" in note and "task/version row removed" in note
    assert "REMOVED" in body and "ADDED" in body
    assert "EPA Review (45 day) - Initiate" in body
    # The OLD and NEW task_status must both be visible in the body — a reader
    # must be able to tell what changed, not see two identical-looking lines
    # differing only by the +/- marker (the bug this fixes).
    assert "task_status=Unstarted" in body
    assert "task_status=Started" in body


def test_facility_snapshot_changes_when_new_version_row_appears():
    rows_a = rc.parse_csv_rows(baseline_csv())
    new_row = _N2688_ROWS[3].replace("ROP0000224,3,", "ROP0000224,4,")
    rows_b = rc.parse_csv_rows(baseline_csv() + new_row + "\n")
    a = rw.facility_snapshot(rows_a, "N2688")
    b = rw.facility_snapshot(rows_b, "N2688")
    note, body = rw.summarize_facility_change(a, b)
    assert "new task/version row" in note
    assert "ADDED" in body


def test_summarize_handles_partial_key_collision_without_dropping_a_row():
    # Two rows can share (permit_number, version, task_name) but differ in other
    # fields — a real shape in the baseline fixture (_N2688_ROWS[5]/[6]: same v2
    # "Send working draft..." task, one Superseded, one Extended). Diffing by
    # that partial key alone (a plain dict) would silently drop one of them;
    # summarize_facility_change must account for both.
    common = {
        "permit_number": "ROP0000224", "version": "2",
        "task_name": "Send working draft conditions to applicant",
        "rop_action": "ROP - Minor Modification", "rop_action_status": "Complete",
        "task_status": "Complete", "task_due": "", "task_completed": "1/24/2011",
        "issue_date": "1/24/2011", "effective_date": "1/24/2011", "expiration_date": "1/24/2016",
    }
    row1 = {**common, "srn": "N2688", "permit_status": "Superseded"}
    row2 = {**common, "srn": "N2688", "permit_status": "Extended", "task_completed": "3/28/2018"}
    old = rw.facility_snapshot([row1, row2], "N2688")
    row2_changed = {**row2, "permit_status": "In Effect"}
    new = rw.facility_snapshot([row1, row2_changed], "N2688")

    assert rw.snapshot_hash(old) != rw.snapshot_hash(new)
    note, body = rw.summarize_facility_change(old, new)
    assert "new task/version row" in note and "task/version row removed" in note
    # Both the vanished Extended-shape row and the new In-Effect-shape row appear.
    assert body.count("Send working draft conditions to applicant") == 2
    # And both permit_status values are visible — this facility change is
    # driven entirely by permit_status, not task_status, so the fix that
    # surfaces permit_status (not just task_status) is what makes this readable.
    assert "permit_status=Extended" in body


def test_summarize_shows_every_hashed_field_not_just_a_hand_picked_subset():
    # _detail() must track _FACILITY_ROW_FIELDS in full, not enumerate a
    # hand-picked subset (the original alert-clarity fix only showed
    # task_status, then a later fix added permit_status/task_due/
    # task_completed — but rop_action, rop_action_status, issue_date,
    # effective_date, and expiration_date are ALSO part of the hashed/diffed
    # identity). A row that changes ONLY in one of those fields must still
    # render distinguishable ADDED/REMOVED lines, not two identical ones.
    common = {
        "permit_number": "ROP0000224", "version": "3",
        "task_name": "EPA Review (45 day) - Initiate",
        "rop_action": "ROP - Renewal", "task_status": "Unstarted",
        "task_due": "", "task_completed": "", "permit_status": "In Process",
        "issue_date": "", "effective_date": "",
    }
    row_old = {**common, "srn": "N2688", "rop_action_status": "In Process",
               "expiration_date": ""}
    row_new = {**common, "srn": "N2688", "rop_action_status": "Complete",
               "expiration_date": "5/28/2029"}
    old = rw.facility_snapshot([row_old], "N2688")
    new = rw.facility_snapshot([row_new], "N2688")
    assert rw.snapshot_hash(old) != rw.snapshot_hash(new)
    _, body = rw.summarize_facility_change(old, new)
    assert "rop_action_status=In Process" in body
    assert "rop_action_status=Complete" in body
    assert "expiration_date=5/28/2029" in body
    # The two rendered lines must not be identical (the bug this fixes).
    lines = [ln for ln in body.split("\n") if ln.strip()]
    assert len(lines) == 2 and lines[0] != lines[1]


def test_facility_snapshot_unaffected_by_other_facilities():
    rows_a = rc.parse_csv_rows(baseline_csv())
    rows_b = rc.parse_csv_rows(csv_with({
        "Send working draft conditions to applicant,Unstarted,Dawn Hayslip,,,ROP0000656,3":
        "Send working draft conditions to applicant,Complete,Dawn Hayslip,,,ROP0000656,3",
    }))
    a = rw.facility_snapshot(rows_a, "N2688")
    b = rw.facility_snapshot(rows_b, "N2688")
    assert rw.snapshot_hash(a) == rw.snapshot_hash(b)   # N1504 changed, not N2688


def test_folder_snapshot_hash_ignores_date_and_size():
    entries_a = [{"name": "a.pdf", "is_dir": False, "date": "1/1/2020", "time": "1:00 AM", "href": "x"}]
    entries_b = [{"name": "a.pdf", "is_dir": False, "date": "1/1/2026", "time": "9:00 PM", "href": "y"}]
    assert rw.snapshot_hash(rw.folder_snapshot(entries_a)) == rw.snapshot_hash(rw.folder_snapshot(entries_b))


def test_folder_snapshot_changes_on_new_file():
    old = rw.folder_snapshot([{"name": "a.pdf", "is_dir": False}])
    new = rw.folder_snapshot([{"name": "a.pdf", "is_dir": False}, {"name": "b.pdf", "is_dir": False}])
    assert rw.snapshot_hash(old) != rw.snapshot_hash(new)
    note, body = rw.summarize_folder_change(old, new)
    assert "1 new file" in note and "ADDED    b.pdf" in body


def test_notice_snapshot_context_ignored_when_not_mentioned():
    a = rw.notice_snapshot(False, "")
    b = rw.notice_snapshot(False, "irrelevant noise elsewhere in the pdf")
    assert rw.snapshot_hash(a) == rw.snapshot_hash(b)


def test_summarize_notice_change_flags_comment_window_opening():
    old = rw.notice_snapshot(False, "")
    new = rw.notice_snapshot(True, "N2688 30-day comment period")
    note, body = rw.summarize_notice_change(old, new)
    assert "public comment window has opened" in note
    assert "30-day comment period" in body


def test_summarize_notice_change_flags_closing():
    old = rw.notice_snapshot(True, "N2688 comment period")
    new = rw.notice_snapshot(False, "")
    note, _ = rw.summarize_notice_change(old, new)
    assert "no longer appears" in note


def test_summarize_notice_change_names_the_passed_srn():
    # The alert text must name whichever SRN the item is for (not a hardcoded
    # N2688), so a P1488/N1504 comment-window alert reads correctly.
    old = rw.notice_snapshot(False, "")
    new = rw.notice_snapshot(True, "Emerald RNG LLC - SRN: P1488 comment period")
    note, _ = rw.summarize_notice_change(old, new, "P1488")
    assert note.startswith("P1488 now appears")
    assert "N2688" not in note


def test_format_change_body_has_essentials():
    body = rw.format_change_body("ROP monthly report — N2688", "task/version row updated", "~ CHANGED foo")
    assert "N2688" in body and "task/version row updated" in body and "CHANGED foo" in body


# --- gate (pure) ------------------------------------------------------------


def test_should_run_false_when_disabled():
    ok, reason = rw._should_run({"rop": {"enabled": False}})
    assert ok is False and "rop.enabled" in reason


def test_should_run_false_when_key_absent():
    assert rw._should_run({})[0] is False


def test_should_run_true_when_enabled():
    ok, reason = rw._should_run({"rop": {"enabled": True}})
    assert ok is True and reason == ""


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

ROP_CFG = {
    "rop": {"enabled": True, "srns": ["N2688", "N1504", "P1488"]},
    "alert_recipients": ["a@example.com"],
}


def _folder_bytes_ok():
    return folder_html(_REAL_FOLDER_ENTRIES)


def _notice_bytes_ok(mentioned=False):
    text = "N2688 renewal notice" if mentioned else "unrelated statewide notice text"
    return make_notice_pdf(text)


def _wire(monkeypatch, cfg, *, csv_text=None, notice_pdf=None,
          csv_error=None, folder_error=None, notice_error=None):
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(rw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(rw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch_csv(url=None, timeout=60):
        if csv_error:
            raise csv_error
        return (csv_text if csv_text is not None else baseline_csv()), "Mon, 08 Jun 2026 16:59:20 GMT"
    monkeypatch.setattr(rw.rc, "fetch_csv", _fetch_csv)

    def _fetch_folder(url=None, timeout=60):
        if folder_error:
            raise folder_error
        return _folder_bytes_ok()
    monkeypatch.setattr(rw.rc, "fetch_folder_listing", _fetch_folder)

    def _fetch_notice(url=None, timeout=60):
        if notice_error:
            raise notice_error
        return notice_pdf if notice_pdf is not None else _notice_bytes_ok()
    monkeypatch.setattr(rw.rc, "fetch_notice_pdf", _fetch_notice)

    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_ROP, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    monkeypatch.setattr(rw, "load_config", lambda: {"rop": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(rw.dc, "sheets_service", boom)
    monkeypatch.setattr(rw.rc, "fetch_csv", boom)
    assert rw.run() == 0


def test_first_run_baselines_all_seven_items_silently(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG)
    assert rw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 7   # 3 CSV facilities + 1 folder + 3 per-SRN notice items
    assert all(r[3] == "baseline" for r in rows)   # Change column
    keys = {r[1] for r in rows}
    assert keys == {"csv:N2688", "csv:N1504", "csv:P1488", "folder:N2688",
                    "notice:N2688", "notice:N1504", "notice:P1488"}
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG)
    rw.run()
    assert rw.run() == 0
    assert len(_rows(fake)) == 7   # no new rows
    assert sent == []


def test_csv_task_status_change_emails_full_recipient_list(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG)
    rw.run()   # baseline
    changed_csv = csv_with({
        "EPA Review (45 day) - Initiate,Unstarted,Kelly Orent,,,ROP0000224,3,In Process,,,":
        "EPA Review (45 day) - Initiate,Started,Kelly Orent,,,ROP0000224,3,In Process,,,",
    })
    monkeypatch.setattr(rw.rc, "fetch_csv", lambda url=None, timeout=60: (changed_csv, "later"))
    assert rw.run() == 0
    rows = _rows(fake)
    n2688_rows = [r for r in rows if r[1] == "csv:N2688"]
    assert len(n2688_rows) == 2 and n2688_rows[1][3] == "changed"
    matches = [s for s in sent if "N2688" in s[0]]
    assert len(matches) == 1
    assert matches[0][2] is None   # None -> send_email resolves the full alert_recipients list


def test_folder_new_file_emails_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG)
    rw.run()   # baseline
    new_entries = _REAL_FOLDER_ENTRIES + [
        ("7/1/2026", "9:00 AM", 12345,
         "/aps/downloads/ROP/pub_ntce/N2688/N2688%20Draft%20Renewal.pdf",
         "N2688 Draft Renewal.pdf"),
    ]
    monkeypatch.setattr(rw.rc, "fetch_folder_listing",
                        lambda url=None, timeout=60: folder_html(new_entries))
    assert rw.run() == 0
    matches = [s for s in sent if "folder" in s[0].lower() or "N2688" in s[0]]
    assert len(matches) == 1
    assert "Draft Renewal" in matches[0][1]


def test_notice_mention_appearing_emails_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG, notice_pdf=_notice_bytes_ok(mentioned=False))
    rw.run()   # baseline: not mentioned
    monkeypatch.setattr(rw.rc, "fetch_notice_pdf",
                        lambda url=None, timeout=60: _notice_bytes_ok(mentioned=True))
    assert rw.run() == 0
    assert any("public comment window" in body for _, body, _ in sent)


def test_notice_p1488_appearing_emails_alert_the_exact_gap(monkeypatch):
    # THE regression for the bug this fix closes: Emerald RNG (P1488) reaching
    # public comment must fire an alert even though N2688 is NOT in the notice.
    # The old N2688-only trip-wire searched the statewide PDF for "N2688" and so
    # missed P1488's real window (opened 2026-07-20) entirely.
    fake, sent = _wire(monkeypatch, ROP_CFG, notice_pdf=_notice_bytes_ok(mentioned=False))
    rw.run()   # baseline: no target SRN mentioned
    p1488_pdf = make_notice_pdf(
        "EGLE is seeking comment on the following ROP actions: "
        "Emerald RNG LLC - SRN: P1488. Public comment period July 20, 2026 "
        "until August 1, 2026.")
    monkeypatch.setattr(rw.rc, "fetch_notice_pdf", lambda url=None, timeout=60: p1488_pdf)
    assert rw.run() == 0
    # Exactly one alert — for the P1488 notice item — describing the comment window.
    p1488_alerts = [s for s in sent if "notice" in s[0].lower() and "P1488" in s[0]]
    assert len(p1488_alerts) == 1
    assert "public comment window" in p1488_alerts[0][1]
    assert "P1488" in p1488_alerts[0][1]
    # N2688's notice item did NOT fire (it isn't in this notice) — proving the
    # alert came from the broadened per-SRN coverage, not the old N2688 check.
    assert not any("notice" in s[0].lower() and "N2688" in s[0] for s in sent)
    # ...and a durable "changed" row was recorded for notice:P1488.
    changed = [r for r in _rows(fake) if r[1] == "notice:P1488" and r[3] == "changed"]
    assert len(changed) == 1


def test_csv_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG)
    rw.run()   # baseline
    monkeypatch.setattr(rw.rc, "fetch_csv",
                        lambda url=None, timeout=60: (_ for _ in ()).throw(rc.RopFetchError("blip")))
    assert rw.run() == 0   # not loud
    assert len(_rows(fake)) == 7   # unchanged, nothing new appended
    assert sent == []


def test_csv_fetch_failure_without_baseline_exits_loud(monkeypatch):
    # The broken source (CSV) surfaces loudly and stays unbaselined; the two
    # independent sources (folder, notice) still baseline normally — a partial
    # activation block, not an all-or-nothing one.
    fake, sent = _wire(monkeypatch, ROP_CFG,
                       csv_error=rc.RopFetchError("bot wall on the runner"))
    assert rw.run() == 1
    assert {r[1] for r in _rows(fake)} == {
        "folder:N2688", "notice:N2688", "notice:N1504", "notice:P1488"}
    assert sent == []


def test_folder_fetch_failure_without_baseline_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG, folder_error=rc.RopFetchError("bot wall"))
    assert rw.run() == 1
    # CSV + notice items still baseline fine even though the folder fetch failed.
    assert {r[1] for r in _rows(fake)} == {
        "csv:N2688", "csv:N1504", "csv:P1488",
        "notice:N2688", "notice:N1504", "notice:P1488"}


def test_notice_fetch_failure_without_baseline_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG, notice_error=rc.RopFetchError("bot wall"))
    assert rw.run() == 1
    # None of the per-SRN notice items baseline when the single PDF fetch fails.
    assert not {r[1] for r in _rows(fake)} & {
        "notice:N2688", "notice:N1504", "notice:P1488"}


def test_csv_parse_error_without_baseline_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, ROP_CFG, csv_text=_HEADER_ROW1)  # too few lines
    assert rw.run() == 1
    assert {r[1] for r in _rows(fake)} == {
        "folder:N2688", "notice:N2688", "notice:N1504", "notice:P1488"}


def test_csv_partial_baseline_still_exits_loud_on_fetch_failure(monkeypatch):
    # A CSV-derived item can lack a baseline while its siblings have one (e.g.
    # rop.srns edited later to add a facility). A subsequent fetch failure must
    # still surface loudly for the still-unbaselined item — gating the skip on
    # _all_baselined, not "any baseline exists among the three", is what this
    # pins (the bug: any() would wrongly treat the siblings' baselines as
    # enough to go quiet).
    fake, sent = _wire(monkeypatch, ROP_CFG)
    rw.run()   # baseline all 7 items
    tab = fake._values._tabs[sw.TAB_ROP]
    fake._values._tabs[sw.TAB_ROP] = [tab[0]] + [r for r in tab[1:] if r[1] != "csv:P1488"]
    monkeypatch.setattr(rw.rc, "fetch_csv",
                        lambda url=None, timeout=60: (_ for _ in ()).throw(rc.RopFetchError("blip")))
    assert rw.run() == 1   # loud — P1488 was never (re-)baselined, even though N2688/N1504 were
    assert sent == []


def test_csv_structural_parse_error_is_always_loud_even_with_existing_baseline(monkeypatch):
    # RopParseError (a structural column-layout break) is NOT transient the way
    # a network blip is — it's likely to persist across runs. Once every CSV
    # item already has a baseline, treating it the same as RopFetchError would
    # let a genuine EGLE format change go unnoticed behind a quiet log line
    # forever (the OBJECTID-reset silent-stall failure class, ADR 014). This
    # pins that RopParseError is ALWAYS loud, regardless of baseline status.
    fake, sent = _wire(monkeypatch, ROP_CFG)
    rw.run()   # baseline all 7 items — every CSV item now has a baseline
    bad_csv = _HEADER_ROW1 + "\nA,B,C\n" + _N2688_ROWS[0]  # wrong column count
    monkeypatch.setattr(rw.rc, "fetch_csv", lambda url=None, timeout=60: (bad_csv, "later"))
    assert rw.run() == 1
    assert len(_rows(fake)) == 7   # nothing new appended; CSV items stayed at their prior baseline
    assert sent == []


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(ROP_CFG)
    cfg["rop"]["recipients"] = ["trisha@example.test"]
    fake, sent = _wire(monkeypatch, cfg)
    rw.run()   # baseline (no alerts)
    changed_csv = csv_with({
        "EPA Review (45 day) - Initiate,Unstarted,Kelly Orent,,,ROP0000224,3,In Process,,,":
        "EPA Review (45 day) - Initiate,Started,Kelly Orent,,,ROP0000224,3,In Process,,,",
    })
    monkeypatch.setattr(rw.rc, "fetch_csv", lambda url=None, timeout=60: (changed_csv, "later"))
    rw.run()
    matches = [s for s in sent if "N2688" in s[0]]
    assert len(matches) == 1 and matches[0][2] == ["trisha@example.test"]


def test_alert_formatting_crash_on_one_item_does_not_abort_the_run(monkeypatch):
    # A bug in format_change_body (or a summarize_fn) for ONE item must be
    # isolated to that item — never escape _diff_and_record and abort run()'s
    # processing of every other independent item. ADR 017's "partial
    # activation block, not all-or-nothing" guarantee applies per ITEM, not
    # just per SOURCE.
    fake, sent = _wire(monkeypatch, ROP_CFG)
    rw.run()   # baseline all 7 items

    real_format = rw.format_change_body

    def _boom(label, note, body):
        if label == "ROP monthly report — N2688":
            raise RuntimeError("simulated formatting bug")
        return real_format(label, note, body)

    monkeypatch.setattr(rw, "format_change_body", _boom)

    # Change BOTH N2688 (crashes) and N1504 (must still succeed, same run).
    changed_csv = csv_with({
        "EPA Review (45 day) - Initiate,Unstarted,Kelly Orent,,,ROP0000224,3,In Process,,,":
        "EPA Review (45 day) - Initiate,Started,Kelly Orent,,,ROP0000224,3,In Process,,,",
        "EPA Review (45 day) - Initiate,Pending,Kelly Orent,,,ROP0000656,3,In Process,,,":
        "EPA Review (45 day) - Initiate,Started,Kelly Orent,,,ROP0000656,3,In Process,,,",
    })
    monkeypatch.setattr(rw.rc, "fetch_csv", lambda url=None, timeout=60: (changed_csv, "later"))

    exit_code = rw.run()   # must NOT raise
    assert exit_code == 0  # a formatting bug is best-effort, not a fetch fail-safe

    rows_by_key = {r[1]: r for r in _rows(fake) if r[3] == "changed"}
    # N2688's row is still durably recorded (written before formatting runs)...
    assert "csv:N2688" in rows_by_key
    # ...but got NO alert email, since format_change_body raised for it.
    assert not any(s[0] == "[ROP watch] ROP monthly report — N2688 changed" for s in sent)
    # N1504 — an independent item in the SAME run — was unaffected: recorded
    # AND alerted normally, proving the crash didn't abort the rest of run().
    assert "csv:N1504" in rows_by_key
    assert any(s[0] == "[ROP watch] ROP monthly report — N1504 changed" for s in sent)


def test_last_rop_snapshots_batches_into_one_tab_read(monkeypatch):
    # _all_baselined's any()->all() fix means every key must be checked (no
    # any() short-circuit) — last_rop_snapshots must satisfy that with ONE
    # tab read for however many keys are asked, not one full-tab read per key.
    fake = FakeSheets()
    sw.ensure_rop_tabs(fake, "SID")
    sw.append_rop_watch_row(fake, "SID", "2026-07-01", "csv:N2688", "l", "baseline",
                             "h1", "n", "t", "{}")
    sw.append_rop_watch_row(fake, "SID", "2026-07-01", "csv:N1504", "l", "baseline",
                             "h2", "n", "t", "{}")
    # csv:P1488 deliberately absent -> never baselined.

    calls = []
    real_get = fake._values.get

    def _counting_get(*a, **k):
        calls.append(1)
        return real_get(*a, **k)
    monkeypatch.setattr(fake._values, "get", _counting_get)

    result = sw.last_rop_snapshots(fake, "SID", ["csv:N2688", "csv:N1504", "csv:P1488"])
    assert result["csv:N2688"] == ("h1", "{}")
    assert result["csv:N1504"] == ("h2", "{}")
    assert result["csv:P1488"] is None
    assert len(calls) == 1  # one tab read for three keys, not three
