"""Sheet routing: feed row, evidence fan-out (one row per risk), measurements."""
import nsite_client as nc
import sheet_writer as sw
from egle_doc_parser import ParsedDoc
from risk_register import RISK_NAMES


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
