"""Sheet routing: feed row, evidence fan-out (one row per risk), measurements."""
import re

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
    assert row[-2] == LINK                       # Link is now second-to-last
    assert row[-1] == "Arbor Hills Landfill"     # Facility is the trailing column


def test_evidence_rows_fan_out_one_per_risk():
    rows = sw.evidence_rows(_doc(risks=("R4", "R8")), META, LINK, RISK_NAMES)
    assert len(rows) == 2
    assert len(rows[0]) == len(sw.EVIDENCE_HEADERS)
    assert rows[0][0] == "R4"
    assert rows[1][0] == "R8"
    assert rows[1][1] == RISK_NAMES["R8"]
    assert rows[0][-1] == "Arbor Hills Landfill"  # trailing Facility column


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
    assert rows[0][-1] == "Arbor Hills Landfill"  # trailing Facility column


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


# ---------------------------------------------------------------------------
# WDS tab-parity: Sheets-API-dependent helpers, against a tiny fake service
# (modeled on tests/test_archive.py's FakeSheets, extended with .update()).
# ---------------------------------------------------------------------------

class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
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

    def spreadsheets(self):
        return self

    def values(self):
        return self._values


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
