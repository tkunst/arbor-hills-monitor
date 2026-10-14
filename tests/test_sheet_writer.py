"""Sheet routing: feed row, evidence fan-out (one row per risk), measurements."""
import json
import re

import httplib2
import pytest
from googleapiclient.errors import HttpError

import nsite_client as nc
import sheet_writer as sw
from egle_doc_parser import ParsedDoc
from risk_register import RISK_NAMES, RISK_REGISTER


def _doc(doc_type="evidence", risks=("R4", "R8"), measurements=None):
    return ParsedDoc(
        summary="A summary.", key_data_point="180F at AHW272.", doc_type=doc_type,
        risks=list(risks), severity="urgent", full_text="t", ocr_applied=True,
        page_count=3, measurements=measurements or [],
    )


META = {"date_filed": "2025-02-05", "document_name": "WOI Status Report",
        "facility_name": "Arbor Hills Landfill"}
LINK = "https://drive.google.com/file/d/abc/view"


def test_feed_row_shape():
    row = sw.feed_row(_doc(), META, LINK)
    assert len(row) == len(sw.FEED_HEADERS)
    assert row[0] == "2025-02-05"
    assert row[2] == "evidence"
    assert row[3] == "R4, R8"
    assert row[-4] == LINK                       # Link, before Facility/Document Date/EGLE Title
    assert row[-3] == "Arbor Hills Landfill"     # Facility
    assert row[-2] == ""                         # Document Date (ADR 065) -- META carries none
    # EGLE Title (ADR 065) falls back to Document Name when META carries no egle_title
    assert row[-1] == "WOI Status Report"


def test_feed_row_carries_document_date_and_egle_title():
    meta = dict(META, document_name="EGLE letter: HOV renewal",
                egle_title="Schedule - Air General Compliance Report",
                document_date="2026-08-27")
    row = sw.feed_row(_doc(), meta, LINK)
    assert row[1] == "EGLE letter: HOV renewal"           # resolved display name
    assert row[-2] == "2026-08-27"                        # Document Date
    assert row[-1] == "Schedule - Air General Compliance Report"  # raw EGLE Title


def test_evidence_rows_fan_out_one_per_risk():
    rows = sw.evidence_rows(_doc(risks=("R4", "R8")), META, LINK, RISK_NAMES)
    assert len(rows) == 2
    assert len(rows[0]) == len(sw.EVIDENCE_HEADERS)
    assert rows[0][0] == "R4"
    assert rows[1][0] == "R8"
    assert rows[1][1] == RISK_NAMES["R8"]
    assert rows[0][-3] == "Arbor Hills Landfill"  # Facility
    assert rows[0][-2] == ""                      # Document Date (ADR 065)
    assert rows[0][-1] == "WOI Status Report"     # EGLE Title fallback


def test_evidence_rows_empty_for_non_evidence():
    assert sw.evidence_rows(_doc(doc_type="opinion"), META, LINK, RISK_NAMES) == []


def test_evidence_rows_empty_when_no_risks():
    assert sw.evidence_rows(_doc(risks=()), META, LINK, RISK_NAMES) == []


def test_measurement_rows_fallback_date_and_shape():
    m = [{"metric": "temperature", "value": 152, "unit": "F", "basis": "measured",
          "well_id": "AHW272"}]  # no as_of_date -> falls back to date_filed
    rows = sw.measurement_rows(_doc(measurements=m), META, LINK)
    assert len(rows) == 1
    assert len(rows[0]) == len(sw.MEASUREMENTS_HEADERS)
    assert rows[0][0] == "2025-02-05"     # as-of-date fell back to date_filed
    assert rows[0][1] == "AHW272"
    assert rows[0][5] == "measured"
    assert rows[0][sw.MEASUREMENTS_HEADERS.index("Facility")] == "Arbor Hills Landfill"
    assert rows[0][-1] == ""   # Well ID As Filed: blank unless the alias map relabeled (ADR 068)


def test_measurement_rows_empty_when_none():
    assert sw.measurement_rows(_doc(measurements=[]), META, LINK) == []


# ---------------------------------------------------------------------------
# WDS (Stream C) tab-parity: wds_evidence_rows fan-out — the WDS analog of
# evidence_rows, tested the same way.
# ---------------------------------------------------------------------------

def _wds_ev(doc_type="evidence", risks=("R5",), **overrides):
    ev = {
        "date": "4/30/2025", "kind": "new", "name": "qmr", "severity": "notable",
        "doc_type": doc_type, "risks": list(risks),
        "label": "QMR groundwater report", "detail": "Yes; boron.",
        "link": "https://www.egle.state.mi.us/wdspi/Dashboard.aspx?w=475946",
    }
    ev.update(overrides)
    return ev


def test_wds_evidence_rows_fan_out_one_per_risk():
    rows = sw.wds_evidence_rows(_wds_ev(risks=("R1", "R5")), RISK_NAMES)
    assert len(rows) == 2
    assert len(rows[0]) == len(sw.WDS_EVIDENCE_HEADERS)
    assert rows[0][0] == "R1"
    assert rows[1][0] == "R5"
    assert rows[1][1] == RISK_NAMES["R5"]


def test_wds_evidence_rows_empty_for_non_evidence():
    assert sw.wds_evidence_rows(_wds_ev(doc_type="procedural"), RISK_NAMES) == []


def test_wds_evidence_rows_empty_when_no_risks():
    assert sw.wds_evidence_rows(_wds_ev(risks=()), RISK_NAMES) == []


# ---------------------------------------------------------------------------
# all_evidence_rows(): merges nSITE + WDS Evidence-by-Risk rows into one
# common schema (ALL_EVIDENCE_HEADERS), tagged with Source for provenance.
# ---------------------------------------------------------------------------

def test_all_evidence_rows_maps_nsite_row():
    nsite_row = ["R4", "Groundwater", "2025-02-05", "WOI Status Report",
                 "180F at AHW272.", "A summary.", LINK, "Arbor Hills Landfill"]
    rows = sw.all_evidence_rows([nsite_row], [])
    assert len(rows) == 1
    assert len(rows[0]) == len(sw.ALL_EVIDENCE_HEADERS)
    assert rows[0][0] == "R4"
    assert rows[0][3] == "nSITE"
    assert rows[0][4] == "WOI Status Report"
    assert rows[0][5] == "180F at AHW272. — A summary."  # Key Data Point + Summary
    assert rows[0][6] == "Arbor Hills Landfill"           # Facility
    assert rows[0][7] == LINK


def test_all_evidence_rows_maps_wds_row():
    wds_row = ["R5", "Groundwater", "2025-04-28", "new", "qmr", "notable",
               "QMR groundwater report", "Statistical Exceedence: Yes.", "link"]
    rows = sw.all_evidence_rows([], [wds_row])
    assert len(rows) == 1
    assert rows[0][3] == "WDS"
    assert rows[0][4] == "QMR groundwater report"          # Item
    assert rows[0][5] == "Statistical Exceedence: Yes."     # Detail
    assert rows[0][6] == "qmr"                              # Collection
    assert rows[0][7] == "link"


def test_all_evidence_rows_preserves_order_nsite_then_wds():
    nsite_row = ["R1", "n", "2025-01-01", "d", "k", "s", "l", "f"]
    wds_row = ["R1", "n", "2026-01-01", "new", "annual", "watch", "i", "de", "li"]
    rows = sw.all_evidence_rows([nsite_row], [wds_row])
    assert [r[3] for r in rows] == ["nSITE", "WDS"]


def test_all_evidence_rows_empty_when_both_empty():
    assert sw.all_evidence_rows([], []) == []


def test_all_evidence_rows_carries_document_date_and_egle_title_for_nsite():
    nsite_row = ["R4", "Groundwater", "2025-02-05", "WOI Status Report",
                 "180F at AHW272.", "A summary.", LINK, "Arbor Hills Landfill",
                 "2026-08-27", "Schedule - Air General Compliance Report"]
    rows = sw.all_evidence_rows([nsite_row], [])
    assert len(rows[0]) == len(sw.ALL_EVIDENCE_HEADERS)
    assert rows[0][-2] == "2026-08-27"
    assert rows[0][-1] == "Schedule - Air General Compliance Report"


def test_all_evidence_rows_wds_row_has_blank_document_date_and_egle_title():
    # A WDS record isn't a filed nSITE document -- no guess, blank.
    wds_row = ["R5", "Groundwater", "2025-04-28", "new", "qmr", "notable",
               "QMR groundwater report", "Statistical Exceedence: Yes.", "link"]
    rows = sw.all_evidence_rows([], [wds_row])
    assert len(rows[0]) == len(sw.ALL_EVIDENCE_HEADERS)
    assert rows[0][-2] == ""
    assert rows[0][-1] == ""


# ---------------------------------------------------------------------------
# WDS tab-parity: Sheets-API-dependent helpers, against a tiny fake service
# (modeled on tests/test_archive.py's FakeSheets, extended with .update()).
# ---------------------------------------------------------------------------

class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self, num_retries=0):
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs  # {tab: [header_row, data_row, ...]}

    @staticmethod
    def _tab(rng):
        return re.match(r"'([^']+)'", rng).group(1)

    @staticmethod
    def _ncols(rng):
        m = re.search(r"![A-Z](\d+):([A-Z])", rng)
        return (ord(m.group(2)) - ord("A") + 1) if m else None

    def get(self, spreadsheetId, range):
        rows = self._tabs.get(self._tab(range), [])
        n = self._ncols(range)
        out = [list(r) for r in rows[1:]]
        if n:
            out = [r[:n] for r in out]
        return _Req({"values": out})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._tabs.setdefault(self._tab(range), [["hdr"]]).extend(body["values"])
        return _Req({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        tab = self._tab(range)
        header = self._tabs.get(tab, [["hdr"]])[:1]
        self._tabs[tab] = header + body["values"]
        return _Req({})

    def clear(self, spreadsheetId, range, body):
        tab = self._tab(range)
        header = self._tabs.get(tab, [["hdr"]])[:1]
        self._tabs[tab] = header
        return _Req({})


class FakeSheets:
    def __init__(self, tabs=None):
        self._values = _Values(tabs or {})
        self._created = []  # titles created via batchUpdate(addSheet)
        self._gid = {}      # title -> a stable numeric sheetId
        for t in self._values._tabs:
            self._ensure_gid(t)

    def _ensure_gid(self, title):
        if title not in self._gid:
            self._gid[title] = len(self._gid)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values

    # spreadsheet-level metadata + tab creation/deletion, for ensure_*_tabs() and
    # purge_doc_rows(). get() exposes each tab's numeric sheetId (needed for
    # deleteDimension); batchUpdate handles both addSheet and deleteDimension.
    def get(self, spreadsheetId):
        for t in self._values._tabs:
            self._ensure_gid(t)
        return _Req({"sheets": [{"properties": {"title": t, "sheetId": self._gid[t]}}
                                for t in self._values._tabs]})

    def batchUpdate(self, spreadsheetId, body):
        for r in body.get("requests", []):
            if "addSheet" in r:
                title = r["addSheet"]["properties"]["title"]
                self._created.append(title)
                self._values._tabs.setdefault(title, [["hdr"]])
                self._ensure_gid(title)
            elif "deleteDimension" in r:
                rng = r["deleteDimension"]["range"]
                title = next(t for t, g in self._gid.items() if g == rng["sheetId"])
                del self._values._tabs[title][rng["startIndex"]:rng["endIndex"]]
        return _Req({})


def test_wds_historical_collections_dumped_empty_when_tab_absent():
    assert sw.wds_historical_collections_dumped(FakeSheets(), "SID") == set()


def test_wds_historical_collections_dumped_reads_collection_column():
    tabs = {sw.TAB_WDS_HISTORICAL: [sw.WDS_HEADERS, [
        "4/30/2025", "historical", "qmr", "notable", "R5", "label", "detail", "link",
    ]]}
    assert sw.wds_historical_collections_dumped(FakeSheets(tabs), "SID") == {"qmr"}


def test_write_wds_event_evidence_writes_feed_and_evidence_tabs():
    svc = FakeSheets()
    sw.write_wds_event(svc, "SID", _wds_ev(), RISK_NAMES)
    assert len(svc._values._tabs[sw.TAB_WDS_NEW]) == 2       # header + 1 row
    assert len(svc._values._tabs[sw.TAB_WDS_EVIDENCE]) == 2  # header + 1 fan-out row


def test_write_wds_event_procedural_only_writes_feed_tab():
    svc = FakeSheets()
    sw.write_wds_event(svc, "SID", _wds_ev(doc_type="procedural"), RISK_NAMES)
    assert len(svc._values._tabs[sw.TAB_WDS_NEW]) == 2
    assert sw.TAB_WDS_EVIDENCE not in svc._values._tabs      # append_rows no-ops on []


def test_write_wds_event_respects_feed_tab_override():
    svc = FakeSheets()
    sw.write_wds_event(svc, "SID", _wds_ev(kind="historical"), RISK_NAMES,
                       feed_tab=sw.TAB_WDS_HISTORICAL)
    assert sw.TAB_WDS_HISTORICAL in svc._values._tabs
    assert sw.TAB_WDS_NEW not in svc._values._tabs


def test_rebuild_risk_register_tab_unions_nsite_and_wds_evidence():
    tabs = {
        sw.TAB_EVIDENCE: [sw.EVIDENCE_HEADERS, [
            "R1", "Expansion eligibility", "2025-01-01", "doc", "kdp", "summary", "link", "fac",
        ]],
        sw.TAB_WDS_EVIDENCE: [sw.WDS_EVIDENCE_HEADERS, [
            "R1", "Expansion eligibility", "2026-06-01", "new", "applications",
            "urgent", "item", "detail", "link",
        ]],
        sw.TAB_REGISTER: [sw.REGISTER_HEADERS],
    }
    svc = FakeSheets(tabs)
    sw.rebuild_risk_register_tab(svc, "SID", RISK_REGISTER)
    body = svc._values._tabs[sw.TAB_REGISTER][1:]
    r1_row = next(r for r in body if r[0] == "R1")
    assert r1_row[3] == 2                # counted from BOTH tabs
    assert r1_row[4] == "2026-06-01"     # most recent date wins across both


def test_rebuild_all_evidence_tab_merges_both_sources():
    tabs = {
        sw.TAB_EVIDENCE: [sw.EVIDENCE_HEADERS, [
            "R1", "Expansion eligibility", "2025-01-01", "doc", "kdp", "summary", "link", "fac",
        ]],
        sw.TAB_WDS_EVIDENCE: [sw.WDS_EVIDENCE_HEADERS, [
            "R1", "Expansion eligibility", "2026-06-01", "new", "applications",
            "urgent", "item", "detail", "link",
        ]],
        sw.TAB_ALL_EVIDENCE: [sw.ALL_EVIDENCE_HEADERS],
    }
    svc = FakeSheets(tabs)
    sw.rebuild_all_evidence_tab(svc, "SID")
    body = svc._values._tabs[sw.TAB_ALL_EVIDENCE][1:]
    assert len(body) == 2
    assert {r[3] for r in body} == {"nSITE", "WDS"}


def test_rebuild_all_evidence_tab_carries_document_date_and_egle_title():
    # Code review finding (ADR 065): the source/dest ranges here used to be
    # hardcoded at the OLD 8-column width -- a real 10-column Evidence row
    # (with Document Date/EGLE Title) would get silently clipped back down
    # to 8 columns on every rebuild.
    tabs = {
        sw.TAB_EVIDENCE: [sw.EVIDENCE_HEADERS, [
            "R1", "Expansion eligibility", "2025-01-01", "doc", "kdp", "summary",
            "link", "fac", "2026-08-27", "Schedule - Air General Compliance Report",
        ]],
        sw.TAB_WDS_EVIDENCE: [sw.WDS_EVIDENCE_HEADERS],
        sw.TAB_ALL_EVIDENCE: [sw.ALL_EVIDENCE_HEADERS],
    }
    svc = FakeSheets(tabs)
    sw.rebuild_all_evidence_tab(svc, "SID")
    body = svc._values._tabs[sw.TAB_ALL_EVIDENCE][1:]
    assert len(body) == 1
    assert len(body[0]) == len(sw.ALL_EVIDENCE_HEADERS)
    assert body[0][-2] == "2026-08-27"
    assert body[0][-1] == "Schedule - Air General Compliance Report"


def test_rebuild_all_evidence_tab_clears_stale_rows_on_shrink():
    # A prior, larger run's leftover row must not survive once the source
    # tabs no longer back it (e.g. after a manual re-dump shrinks WDS Evidence).
    tabs = {
        sw.TAB_EVIDENCE: [sw.EVIDENCE_HEADERS],
        sw.TAB_WDS_EVIDENCE: [sw.WDS_EVIDENCE_HEADERS],
        sw.TAB_ALL_EVIDENCE: [sw.ALL_EVIDENCE_HEADERS,
                              ["R1", "n", "2020-01-01", "nSITE", "i", "d", "f", "l"]],
    }
    svc = FakeSheets(tabs)
    sw.rebuild_all_evidence_tab(svc, "SID")
    assert svc._values._tabs[sw.TAB_ALL_EVIDENCE] == [sw.ALL_EVIDENCE_HEADERS]


def test_fetch_all_documents_tags_facility(monkeypatch):
    """The multi-facility loop concatenates per-facility docs in config order and
    tags each with facility_srn / facility_name (ADR 008)."""
    cfg = {"facilities": [
        {"srn": "N2688", "name": "Arbor Hills Landfill", "id": "111"},
        {"srn": "P1488", "name": "Emerald RNG", "id": "222"},
    ]}
    fake = {"111": [{"doc_id": "a"}], "222": [{"doc_id": "b"}, {"doc_id": "c"}]}
    monkeypatch.setattr(nc, "fetch_site_documents", lambda session, fid: fake[fid])

    docs = nc.fetch_all_documents(session=None, cfg=cfg)

    assert [d["doc_id"] for d in docs] == ["a", "b", "c"]
    assert docs[0]["facility_srn"] == "N2688"
    assert docs[0]["facility_name"] == "Arbor Hills Landfill"
    assert docs[2]["facility_srn"] == "P1488"
    assert docs[2]["facility_name"] == "Emerald RNG"


# ---------------------------------------------------------------------------
# WOI Well Summary (ADR 005 integration) — per-well summary rows + tab write.
# ---------------------------------------------------------------------------

_WOI_SUMMARY = [
    {"well": "AHW272R4", "is_woi": True, "max_temp_f": 177.0,
     "max_temp_date": "3/14/2025 15:40", "o2_at_max_temp": 7.0,
     "ch4_at_max_temp": 8.1, "max_o2_pct": 12.0, "n_readings": 30},
    {"well": "AHW999", "is_woi": False, "max_temp_f": None, "max_temp_date": None,
     "o2_at_max_temp": None, "ch4_at_max_temp": None, "max_o2_pct": None,
     "n_readings": 0},
]


def test_woi_summary_rows_shape_and_none_blanks():
    rows = sw.woi_summary_rows(_WOI_SUMMARY, META, LINK)
    assert len(rows) == 2
    assert len(rows[0]) == len(sw.WOI_SUMMARY_HEADERS)
    assert rows[0][0] == "2025-02-05"              # report date = doc date_filed
    assert rows[0][1] == "AHW272R4"
    assert rows[0][2] == "yes"                     # is_woi -> yes
    assert rows[0][3] == 177.0
    assert rows[0][-2] == "WOI Status Report"      # document_name
    assert rows[0][-1] == LINK
    assert rows[1][2] == "no"
    assert rows[1][3] == "" and rows[1][5] == ""   # None numerics render blank


def test_write_woi_summary_appends_to_summary_tab():
    svc = FakeSheets()
    sw.write_woi_summary(svc, "SID", _WOI_SUMMARY, META, LINK)
    rows = svc._values._tabs[sw.TAB_WOI_SUMMARY]
    assert any(len(r) > 1 and r[1] == "AHW272R4" for r in rows)


def test_write_woi_summary_empty_summary_writes_nothing():
    svc = FakeSheets()
    sw.write_woi_summary(svc, "SID", [], META, LINK)
    assert sw.TAB_WOI_SUMMARY not in svc._values._tabs


def test_ensure_woi_tabs_creates_when_absent():
    svc = FakeSheets()
    sw.ensure_woi_tabs(svc, "SID")
    assert sw.TAB_WOI_SUMMARY in svc._created            # create-if-absent fired
    assert sw.TAB_WOI_SUMMARY in svc._values._tabs


def test_ensure_woi_tabs_idempotent_when_present():
    svc = FakeSheets({sw.TAB_WOI_SUMMARY: [sw.WOI_SUMMARY_HEADERS]})
    sw.ensure_woi_tabs(svc, "SID")
    assert svc._created == []                            # already present -> not re-created


# ---------------------------------------------------------------------------
# purge_doc_rows(): clean re-extract support (backfill FORCE_REPROCESS_DOC_IDS).
# ---------------------------------------------------------------------------

_DID_A = "111"
_DID_B = "-222"   # a negative doc_id, to prove the final-path-segment match


def _link(did):
    return f"https://mienviro.michigan.gov/ncore/downloadpdf/{did}"


def _purge_fixture():
    """Historical + Measurements tabs holding rows for two docs (A twice, B once),
    matched by the doc_id in the Link column (index 7 in FEED, 9 in MEASUREMENTS)."""
    def hist_row(did, name):
        r = [""] * len(sw.FEED_HEADERS)
        r[0], r[1], r[7] = "2025-07-08", name, _link(did)
        return r

    def meas_row(did, well):
        r = [""] * len(sw.MEASUREMENTS_HEADERS)
        r[1], r[9] = well, _link(did)
        return r

    tabs = {
        sw.TAB_HISTORICAL: [sw.FEED_HEADERS, hist_row(_DID_A, "A"),
                            hist_row(_DID_B, "B"), hist_row(_DID_A, "A-dup")],
        sw.TAB_MEASUREMENTS: [sw.MEASUREMENTS_HEADERS, meas_row(_DID_A, "W1"),
                              meas_row(_DID_A, "W2"), meas_row(_DID_B, "W3")],
    }
    return FakeSheets(tabs)


def test_link_doc_id_final_path_segment():
    assert sw._link_doc_id(_link("111")) == "111"
    assert sw._link_doc_id(_link("-222")) == "-222"          # negative id survives
    assert sw._link_doc_id(_link("111") + "?x=1") == "111"   # query stripped


def test_purge_doc_rows_removes_only_matching_doc():
    svc = _purge_fixture()
    counts = sw.purge_doc_rows(svc, "SID", _DID_A)
    assert counts[sw.TAB_HISTORICAL] == 2 and counts[sw.TAB_MEASUREMENTS] == 2
    hist = svc._values._tabs[sw.TAB_HISTORICAL]
    meas = svc._values._tabs[sw.TAB_MEASUREMENTS]
    assert hist[0] == sw.FEED_HEADERS                        # header intact
    assert [sw._link_doc_id(r[7]) for r in hist[1:]] == [_DID_B]   # only B remains
    assert [sw._link_doc_id(r[9]) for r in meas[1:]] == [_DID_B]


def test_purge_doc_rows_dry_run_mutates_nothing():
    svc = _purge_fixture()
    before = [list(r) for r in svc._values._tabs[sw.TAB_HISTORICAL]]
    counts = sw.purge_doc_rows(svc, "SID", _DID_A, dry_run=True)
    assert counts[sw.TAB_HISTORICAL] == 2                    # still reports the matches
    assert svc._values._tabs[sw.TAB_HISTORICAL] == before    # but deleted nothing


def test_purge_doc_rows_skips_absent_tabs():
    # WOI Summary / New / Evidence not created here -> purge skips them silently.
    svc = _purge_fixture()
    counts = sw.purge_doc_rows(svc, "SID", _DID_A)
    assert sw.TAB_WOI_SUMMARY not in counts and sw.TAB_EVIDENCE not in counts


def test_purge_doc_rows_matches_a_drive_mirrored_row_via_archive_index():
    """Regression (2026-09-20): once a doc is Drive-mirrored, its row's Link is
    the Drive archive URL (…/view), which shares NO substring with the nSITE
    doc_id -- `_link_doc_id` alone can never match it, so purge silently found 0
    rows and a real FORCE_REPROCESS_APPLY would have appended a duplicate instead
    of replacing the stale row. Fix: resolve doc_id -> archive_link via the
    Archived PDFs index and match by exact equality too."""
    drive_link = "https://drive.google.com/file/d/1abcXYZ/view?usp=drivesdk"
    row = [""] * len(sw.FEED_HEADERS)
    row[0], row[1], row[7] = "2026-04-30", "nForm Document", drive_link
    archive_row = [""] * len(sw.ARCHIVE_HEADERS)
    archive_row[0], archive_row[5] = _DID_A, drive_link
    svc = FakeSheets({
        sw.TAB_HISTORICAL: [sw.FEED_HEADERS, row],
        sw.TAB_ARCHIVE: [sw.ARCHIVE_HEADERS, archive_row],
    })

    preview = sw.purge_doc_rows(svc, "SID", _DID_A, dry_run=True)
    assert preview[sw.TAB_HISTORICAL] == 1        # was 0 before the fix

    counts = sw.purge_doc_rows(svc, "SID", _DID_A)
    assert counts[sw.TAB_HISTORICAL] == 1
    hist = svc._values._tabs[sw.TAB_HISTORICAL]
    assert hist == [sw.FEED_HEADERS]              # the mirrored row is gone, not duplicated


def test_purge_doc_rows_unmirrored_doc_still_matches_by_nsite_link():
    # No Archived PDFs tab at all -> archive_link resolves to None; the original
    # nSITE-link match path (a doc that never got mirrored) must still work.
    svc = _purge_fixture()
    counts = sw.purge_doc_rows(svc, "SID", _DID_A)
    assert counts[sw.TAB_HISTORICAL] == 2


# ---------------------------------------------------------------------------
# _wds_seen append-only backup + recovery log (ADR 024, Gap G1-C-1).
# The live diff cursor stays in _meta.wds_seen (unchanged); this log makes it
# recoverable, so a _meta wipe no longer re-fires every WDS record as "new".
# ---------------------------------------------------------------------------

def _seen(**collections):
    """A _meta.wds_seen-shaped dict: {collection: {records, last_count}}."""
    return {name: {"records": recs, "last_count": len(recs)}
            for name, recs in collections.items()}


def test_wds_seen_hash_stable_and_change_sensitive():
    a = {"records": {"x": "h1"}, "last_count": 1}
    assert sw._wds_seen_hash(a) == sw._wds_seen_hash(
        {"records": {"x": "h1"}, "last_count": 1})
    assert sw._wds_seen_hash(a) != sw._wds_seen_hash(
        {"records": {"x": "h2"}, "last_count": 1})


def test_read_wds_seen_log_latest_empty_when_absent():
    assert sw.read_wds_seen_log_latest(FakeSheets(), "SID") == {}


def test_append_wds_seen_log_seed_round_trips_exactly():
    # The migration crux: the first append seeds the log from the current
    # _meta.wds_seen; reading it back must reproduce the cursor EXACTLY, so the
    # transition run diffs against the same state and re-fires nothing.
    svc = FakeSheets({sw.TAB_WDS_SEEN: [sw.WDS_SEEN_HEADERS]})
    seen = _seen(qmr={"q1": "ha"}, annual={"a1": "hb", "a2": "hc"})
    sw.append_wds_seen_log(svc, "SID", seen, "2026-07-26T00:00:00")
    assert sw.read_wds_seen_log_latest(svc, "SID") == seen


def test_append_wds_seen_log_is_append_on_change():
    svc = FakeSheets({sw.TAB_WDS_SEEN: [sw.WDS_SEEN_HEADERS]})
    seen = _seen(qmr={"q1": "ha"}, annual={"a1": "hb"})
    sw.append_wds_seen_log(svc, "SID", seen, "t1")
    n = len(svc._values._tabs[sw.TAB_WDS_SEEN])          # header + 2 collections
    # An identical snapshot appends nothing (append-on-change dedup).
    sw.append_wds_seen_log(svc, "SID", seen, "t2")
    assert len(svc._values._tabs[sw.TAB_WDS_SEEN]) == n
    # Changing ONE collection appends exactly one row; the unchanged one is skipped.
    seen2 = _seen(qmr={"q1": "ha", "q2": "hd"}, annual={"a1": "hb"})
    sw.append_wds_seen_log(svc, "SID", seen2, "t3")
    assert len(svc._values._tabs[sw.TAB_WDS_SEEN]) == n + 1
    assert sw.read_wds_seen_log_latest(svc, "SID") == seen2


def test_read_wds_seen_log_latest_takes_newest_row_and_history_is_preserved():
    svc = FakeSheets({sw.TAB_WDS_SEEN: [sw.WDS_SEEN_HEADERS]})
    sw.append_wds_seen_log(svc, "SID", _seen(qmr={"q1": "old"}), "t1")
    sw.append_wds_seen_log(svc, "SID", _seen(qmr={"q1": "old", "q2": "new"}), "t2")
    got = sw.read_wds_seen_log_latest(svc, "SID")
    assert got["qmr"] == {"records": {"q1": "old", "q2": "new"}, "last_count": 2}
    # Both rows remain on disk (append-only — the old snapshot is never overwritten).
    assert len(svc._values._tabs[sw.TAB_WDS_SEEN]) == 3   # header + 2 rows


def test_append_wds_seen_log_noop_on_empty_seen():
    svc = FakeSheets({sw.TAB_WDS_SEEN: [sw.WDS_SEEN_HEADERS]})
    sw.append_wds_seen_log(svc, "SID", {}, "t1")
    assert len(svc._values._tabs[sw.TAB_WDS_SEEN]) == 1   # header only


# ---------------------------------------------------------------------------
# Compliance Deadlines tab (ADR 025) — structured deadline capture from both
# the nSITE classifier and WDS compliance_actions.
# ---------------------------------------------------------------------------

def test_compliance_deadline_row_maps_six_fields_and_provenance():
    d = {"due_date": "2025-03-15", "extension_due_date": "2025-04-15",
         "actual_completion_date": "2025-04-10", "item_description": "Submit CAP",
         "compelled_by": "VN-011821", "compliance_doc_effective_date": "2024-10-10"}
    row = sw.compliance_deadline_row(d, "nSITE", "Arbor Hills Landfill", "http://x", "2026-07-26")
    assert len(row) == len(sw.COMPLIANCE_DEADLINE_HEADERS)
    assert row[0] == "2025-03-15"          # Due Date
    assert row[1] == "2025-04-15"          # Extension Due Date
    assert row[2] == "2025-04-10"          # Actual Completion Date
    assert row[3] == "Submit CAP"          # Item Description
    assert row[4] == "VN-011821"           # Compelled By
    assert row[5] == "2024-10-10"          # Compliance Doc Effective Date
    assert row[6] == "nSITE"               # Source Stream
    assert row[-1] == "2026-07-26"         # Extracted At


def test_compliance_deadline_row_blanks_missing_fields():
    row = sw.compliance_deadline_row({"item_description": "Do X"}, "WDS", "", "", "t")
    assert row[0] == "" and row[1] == "" and row[2] == "" and row[4] == "" and row[5] == ""
    assert row[3] == "Do X"


def test_compliance_deadline_rows_skips_entries_without_item_description():
    ds = [{"item_description": "Keep"}, {"due_date": "2025-01-01"}, {}]
    rows = sw.compliance_deadline_rows(ds, "nSITE", "F", "ref", "t")
    assert len(rows) == 1 and rows[0][3] == "Keep"


def test_write_compliance_deadlines_appends_and_noops_when_empty():
    svc = FakeSheets({sw.TAB_COMPLIANCE_DEADLINES: [sw.COMPLIANCE_DEADLINE_HEADERS]})
    sw.write_compliance_deadlines(
        svc, "SID", [{"item_description": "A"}, {"item_description": "B"}],
        "nSITE", "F", "ref", "t")
    assert len(svc._values._tabs[sw.TAB_COMPLIANCE_DEADLINES]) == 3   # header + 2
    sw.write_compliance_deadlines(svc, "SID", [], "nSITE", "F", "ref", "t")
    assert len(svc._values._tabs[sw.TAB_COMPLIANCE_DEADLINES]) == 3   # unchanged


# ---------------------------------------------------------------------------
# state-reads-raise: _tab_rows_strict distinguishes a genuinely ABSENT tab
# (Sheets' own 400 "Unable to parse range...") from every other read failure,
# which must RAISE instead of masquerading as an empty read (ADR 018/023's
# "spurious baseline becomes the state" failure). This block covers (1) the
# helper itself against the REAL googleapiclient exception shape, and (2) one
# "raises rather than swallowing" test per converted reader — the same style
# as test_nsite_violations.py's test_last_violations_snapshots_raises_
# rather_than_swallowing_a_read_error, which this generalizes to every other
# caller that now shares its contract.
# ---------------------------------------------------------------------------


def _http_error(status: int, message: str) -> HttpError:
    """A real googleapiclient HttpError carrying `status`/`message`, exactly
    as `.resp.status`/`.reason` would read it from a live Sheets 400/503 —
    see tests/test_ride_docs.py's `_http_error` for the same construction."""
    body = json.dumps({"error": {"code": status, "message": message}}).encode()
    return HttpError(httplib2.Response({"status": status}), body)


class _Exploding:
    """A Sheets service whose values().get() always raises — the fake used by
    test_nsite_violations.py's raises-rather-than-swallowing test, generalized
    here so every converted reader in this file can reuse one definition."""

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):
        raise RuntimeError("HTTP 503")


class _ExplodingOneTab:
    """Like _Exploding, but raises ONLY for one named tab and returns an
    ordinary empty read for any other — needed for rebuild_risk_register_tab/
    rebuild_all_evidence_tab, whose FIRST read (nSITE Evidence by Risk) is a
    raw, unguarded call that must succeed so the test actually exercises the
    SECOND (WDS Evidence by Risk) read, which is the one now going through
    _tab_rows_strict."""

    def __init__(self, boom_tab: str):
        self._boom_tab = boom_tab

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):
        tab = re.match(r"'([^']+)'", range).group(1)
        if tab == self._boom_tab:
            raise RuntimeError("HTTP 503")
        return _Req({"values": []})


def test_is_absent_tab_error_matches_only_the_real_400_unable_to_parse_range():
    assert sw._is_absent_tab_error(
        _http_error(400, "Unable to parse range: 'Nonexistent Tab'!A2:H")) is True


def test_is_absent_tab_error_false_for_a_different_400():
    assert sw._is_absent_tab_error(_http_error(400, "Invalid value")) is False


def test_is_absent_tab_error_false_for_5xx_and_429():
    assert sw._is_absent_tab_error(_http_error(503, "Backend Error")) is False
    assert sw._is_absent_tab_error(_http_error(429, "Quota exceeded")) is False


def test_is_absent_tab_error_false_for_a_non_http_error():
    assert sw._is_absent_tab_error(RuntimeError("boom")) is False


def test_tab_rows_strict_absent_tab_reads_empty():
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, "Unable to parse range: 'Ghost'!A2:H"))
    assert sw._tab_rows_strict(svc, "SID", "Ghost", "A2:H") == []


def test_tab_rows_strict_raises_on_5xx():
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(503, "Backend Error"))
    with pytest.raises(HttpError):
        sw._tab_rows_strict(svc, "SID", "Tab", "A2:H")


def test_tab_rows_strict_raises_on_a_non_http_error():
    with pytest.raises(RuntimeError):
        sw._tab_rows_strict(_Exploding(), "SID", "Tab", "A2:H")


def test_tab_rows_strict_returns_rows_on_a_normal_read():
    tabs = {"Tab": [["hdr"], ["a", "b"], ["c", "d"]]}
    svc = FakeSheets(tabs)
    assert sw._tab_rows_strict(svc, "SID", "Tab", "A2:B") == [["a", "b"], ["c", "d"]]


# --- core _state/_meta --------------------------------------------------------

def test_read_state_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.read_state(_Exploding(), "SID")


def test_read_meta_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.read_meta(_Exploding(), "SID")


def test_read_state_still_reads_empty_for_a_genuinely_absent_tab():
    """The real 400 "Unable to parse range" signature, not just a fake that
    quietly returns []: a fresh Sheet before ensure_tabs() has ever run must
    still reduce to the empty default state, never raise."""
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, f"Unable to parse range: {range}"))
    state = sw.read_state(svc, "SID")
    assert state["processed"] == {} and state["errors"] == {}
    assert state["pending_digest"] == []


# --- the 8 enabled watchers' snapshot/first-run readers -----------------------

def test_last_submissions_snapshots_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_submissions_snapshots(_Exploding(), "SID", ["subm:N2688"])


def test_last_rop_snapshots_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_rop_snapshots(_Exploding(), "SID", ["csv:N2688"])


def test_last_mmd_snapshots_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_mmd_snapshots(_Exploding(), "SID", ["mmd:475946"])


def test_last_pfas_pws_snapshots_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_pfas_pws_snapshots(_Exploding(), "SID", ["pws:2001381"])


def test_last_ride_snapshots_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_ride_snapshots(_Exploding(), "SID", ["ride:81000033"])


def test_last_pfas_snapshot_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_pfas_snapshot(_Exploding(), "SID", "http://example/arbor-hills")


def test_last_gfl_info_site_row_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_gfl_info_site_row(_Exploding(), "SID", "http://example/page")


def test_all_gfl_info_site_urls_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.all_gfl_info_site_urls(_Exploding(), "SID")


def test_gfl_info_site_tab_is_empty_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.gfl_info_site_tab_is_empty(_Exploding(), "SID")


def test_last_meeting_snapshot_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.last_meeting_snapshot(_Exploding(), "SID", 12345)


def test_meeting_watch_group_has_rows_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.meeting_watch_group_has_rows(_Exploding(), "SID", "DPW")


# --- the scope-expansion readers (same destructive-write-on-false-empty-read
# pattern; live/enabled, their only callers already isolate the failure) ------

def test_rebuild_risk_register_tab_raises_rather_than_swallowing_a_wds_read_error():
    with pytest.raises(RuntimeError):
        sw.rebuild_risk_register_tab(
            _ExplodingOneTab(sw.TAB_WDS_EVIDENCE), "SID", RISK_REGISTER)


def test_rebuild_all_evidence_tab_raises_rather_than_swallowing_a_wds_read_error():
    with pytest.raises(RuntimeError):
        sw.rebuild_all_evidence_tab(_ExplodingOneTab(sw.TAB_WDS_EVIDENCE), "SID")


def test_read_wds_seen_log_latest_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.read_wds_seen_log_latest(_Exploding(), "SID")


def test_ridgewood_archived_months_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.ridgewood_archived_months(_Exploding(), "SID")


def test_ridgewood_report_rows_raises_rather_than_swallowing_a_read_error():
    with pytest.raises(RuntimeError):
        sw.ridgewood_report_rows(_Exploding(), "SID")


# --- a representative absent-tab-still-empty check per profile shape ---------

def test_last_rop_snapshots_absent_tab_still_reads_empty():
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, f"Unable to parse range: {range}"))
    assert sw.last_rop_snapshots(svc, "SID", ["csv:N2688"]) == {"csv:N2688": None}


def test_gfl_info_site_tab_is_empty_true_for_a_genuinely_absent_tab():
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, f"Unable to parse range: {range}"))
    assert sw.gfl_info_site_tab_is_empty(svc, "SID") is True


# ---------------------------------------------------------------------------
# Generic Watch-tab helpers (sheet-writer-generic): ensure_tab(),
# append_watch_row(), last_snapshots()/last_snapshot(). Every ensure_*_tab(s)/
# append_*_watch_row/last_*_snapshot(s) function above is now a thin wrapper
# over exactly one of these four; the existing per-profile tests throughout
# this file (and tests/test_nsite_violations.py etc.) already pin the
# wrappers' observable behavior unchanged, so these tests target the shared
# implementations directly.
# ---------------------------------------------------------------------------

def test_ensure_tab_creates_when_absent():
    svc = FakeSheets()
    sw.ensure_tab(svc, "SID", "A New Tab", ["H1", "H2"])
    assert "A New Tab" in svc._created                   # create-if-absent fired
    assert "A New Tab" in svc._values._tabs


def test_ensure_tab_idempotent_when_present():
    svc = FakeSheets({"Already There": [["H1", "H2"]]})
    sw.ensure_tab(svc, "SID", "Already There", ["H1", "H2"])
    assert svc._created == []                            # already present -> not re-created


def test_append_watch_row_writes_the_shared_eight_column_shape():
    svc = FakeSheets()
    sw.append_watch_row(svc, "SID", "Some Watch", "2026-10-14", "key:1", "label",
                         "new", "hash1", "note", "now", "{}")
    rows = svc._values._tabs["Some Watch"][1:]
    assert rows == [["2026-10-14", "key:1", "label", "new", "hash1", "note", "now", "{}"]]


def test_last_snapshots_batches_into_one_tab_read():
    reads = []
    tabs = {"W": [["d", "k", "l", "c", "h", "n", "t", "j"],
                  ["2026-01-01", "key:A", "L", "baseline", "ha", "", "t1", "{}"],
                  ["2026-01-02", "key:B", "L", "baseline", "hb", "", "t2", "{}"]]}
    svc = FakeSheets(tabs)
    real_get = svc._values.get

    def _counting(spreadsheetId, range):
        reads.append(range)
        return real_get(spreadsheetId, range)
    svc._values.get = _counting
    result = sw.last_snapshots(svc, "SID", "W", ["key:A", "key:B", "key:C"])
    assert len(reads) == 1
    assert result == {"key:A": ("ha", "{}"), "key:B": ("hb", "{}"), "key:C": None}


def test_last_snapshots_last_row_wins_for_a_repeated_key():
    tabs = {"W": [["d", "k", "l", "c", "h", "n", "t", "j"],
                  ["2026-01-01", "key:A", "L", "baseline", "h-old", "", "t1", "{}"],
                  ["2026-01-02", "key:A", "L", "changed", "h-new", "", "t2", "{}"]]}
    svc = FakeSheets(tabs)
    assert sw.last_snapshots(svc, "SID", "W", ["key:A"]) == {"key:A": ("h-new", "{}")}


def test_last_snapshot_singular_matches_batched_form():
    tabs = {"W": [["d", "k", "l", "c", "h", "n", "t", "j"],
                  ["2026-01-01", "key:A", "L", "baseline", "ha", "", "t1", "{}"]]}
    svc = FakeSheets(tabs)
    assert sw.last_snapshot(svc, "SID", "W", "key:A") == ("ha", "{}")
    assert sw.last_snapshot(svc, "SID", "W", "key:absent") is None


def test_last_snapshots_tolerates_absent_tab_by_default():
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, f"Unable to parse range: {range}"))
    assert sw.last_snapshots(svc, "SID", "Ghost", ["key:A"]) == {"key:A": None}


def test_last_snapshots_raises_on_absent_tab_when_not_tolerated():
    """tolerate_absent_tab=False is the contract every one of the six nSITE
    profile watchers (Violations/Compliance Actions/Evaluations/Permits/
    Complaints/Public Notices) is merged with: unlike ROP/MMD/PFAS-PWS/RIDE/
    Submissions, a genuinely absent tab is NOT a case their design
    distinguishes, so it raises like any other read failure."""
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, f"Unable to parse range: {range}"))
    with pytest.raises(HttpError):
        sw.last_snapshots(svc, "SID", "Ghost", ["key:A"], tolerate_absent_tab=False)


def test_last_snapshots_raises_on_a_non_absent_failure_either_way():
    with pytest.raises(RuntimeError):
        sw.last_snapshots(_Exploding(), "SID", "W", ["key:A"])
    with pytest.raises(RuntimeError):
        sw.last_snapshots(_Exploding(), "SID", "W", ["key:A"], tolerate_absent_tab=False)


# --- identity: each of the six strict (no-absent-tab-allowance) nSITE
# profile readers raises on a genuinely absent tab too, not just a generic
# failure (test_last_*_snapshots_raises_rather_than_swallowing_a_read_error
# above already covers the generic-failure case for all of them) ------------

@pytest.mark.parametrize("fn, key", [
    (sw.last_violations_snapshots, "viol:N2688"),
    (sw.last_compliance_actions_snapshots, "ca:N2688"),
    (sw.last_evaluations_snapshots, "eval:N2688"),
    (sw.last_permits_snapshots, "prmt:N2688"),
    (sw.last_complaints_snapshots, "cmplt:N2688"),
    (sw.last_public_notices_snapshots, "pubntc:N2688"),
])
def test_strict_nsite_profile_snapshots_raise_even_on_a_genuinely_absent_tab(fn, key):
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, f"Unable to parse range: {range}"))
    with pytest.raises(HttpError):
        fn(svc, "SID", [key])


# --- identity: the five tolerant watchers (ROP/MMD/PFAS-PWS/RIDE/Submissions)
# all still read a genuinely absent tab as "never snapshotted" (the ROP case
# is already covered above by test_last_rop_snapshots_absent_tab_still_reads_
# empty) -----------------------------------------------------------------

@pytest.mark.parametrize("fn, key", [
    (sw.last_mmd_snapshots, "mmd:475946"),
    (sw.last_pfas_pws_snapshots, "pws:2001381"),
    (sw.last_ride_snapshots, "ride:81000033"),
    (sw.last_submissions_snapshots, "subm:N2688"),
])
def test_tolerant_nsite_profile_snapshots_still_read_absent_tab_as_empty(fn, key):
    svc = _Exploding()
    svc.get = lambda spreadsheetId, range: (
        _ for _ in ()).throw(_http_error(400, f"Unable to parse range: {range}"))
    assert fn(svc, "SID", [key]) == {key: None}
