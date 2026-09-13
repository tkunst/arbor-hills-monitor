"""Tests for the EGLE Public Water Supply PFAS sampling watch (Stream R, ADR 042).

Hermetic: synthetic ArcGIS payloads (never committed as data files), the opener
mocked for client fetch-guard tests, and FakeSheets (reused from
test_pfas_watcher) + a canned fetch + a captured mailer for the watcher run()
flows. Same idiom as test_mmd / test_ride. No network, no creds.
"""
import copy
import json
from datetime import datetime, timezone

import pytest

import pfas_pws_client as pc
import pfas_pws_watcher as pw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets


# ==============================================================================
# Fixtures — Salem Elementary (WSSN 2001381), the 6 documented all-<2 rounds
# ==============================================================================

def _ms(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def _row(wssn, code, date_ms, loc, system="SALEM ELEMENTARY SCHOOL", **analytes):
    """One raw layer-1 attribute dict; all seven analytes default to '<2'."""
    a = {name: "<2" for name in pc.ANALYTES}
    a.update(analytes)
    return {"OBJECTID": 1, "HexID": 9, "GlobalID": "g",
            "WSSN": wssn, "SystemName": system, "LocName": loc,
            "SysSampleCode": code, "SampleDate": date_ms, **a}


def baseline_records() -> list[dict]:
    """Salem's six clean rounds Dec-2020 -> Feb-2025 (all regulated PFAS <2)."""
    return [
        _row(2001381, "871103.01", _ms(2025, 2, 4), "EP01"),
        _row(2001381, "S68826.01", _ms(2024, 11, 19), "EP01"),
        _row(2001381, "MEC03142350170", _ms(2023, 3, 14), "EP01"),
        _row(2001381, "MEC03102225816", _ms(2022, 3, 10), "EP01"),
        _row(2001381, "MEC09212184474", _ms(2021, 9, 21), "002"),
        _row(2001381, "LJ36361", _ms(2020, 12, 8), "020", system="SALEM SCHOOL"),
    ]


def _arcgis_payload(records: list[dict]) -> bytes:
    return json.dumps({
        "fields": [{"name": n} for n in
                   ("OBJECTID", "HexID", "GlobalID") + pc.RECORD_FIELDS],
        "features": [{"attributes": r} for r in records],
    }).encode()


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def getcode(self):
        return self.status


def _wire_opener(monkeypatch, body: bytes, status: int = 200):
    class _Op:
        def open(self, url, timeout=None):
            return _FakeResp(body, status)
    monkeypatch.setattr(pc, "_opener", lambda: _Op())


# ==============================================================================
# Client — fetch guards (opener mocked)
# ==============================================================================

def test_fetch_returns_attribute_dicts(monkeypatch):
    _wire_opener(monkeypatch, _arcgis_payload(baseline_records()))
    recs = pc.fetch_results((2001381,))
    assert len(recs) == 6 and recs[0]["WSSN"] == 2001381


def test_fetch_http_error_is_fetch_error(monkeypatch):
    _wire_opener(monkeypatch, b"gone", status=503)
    with pytest.raises(pc.PwsFetchError):
        pc.fetch_results()


def test_fetch_non_json_is_fetch_error(monkeypatch):
    _wire_opener(monkeypatch, b"<html>bot wall</html>")
    with pytest.raises(pc.PwsFetchError):
        pc.fetch_results()


def test_fetch_arcgis_error_payload_is_fetch_error(monkeypatch):
    _wire_opener(monkeypatch, json.dumps({"error": {"code": 400}}).encode())
    with pytest.raises(pc.PwsFetchError):
        pc.fetch_results()


def test_fetch_missing_features_is_parse_error(monkeypatch):
    _wire_opener(monkeypatch, json.dumps({"fields": []}).encode())
    with pytest.raises(pc.PwsParseError):
        pc.fetch_results()


def test_fetch_exceeded_transfer_limit_is_parse_error(monkeypatch):
    payload = json.loads(_arcgis_payload(baseline_records()))
    payload["exceededTransferLimit"] = True
    _wire_opener(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(pc.PwsParseError):
        pc.fetch_results()


def test_fetch_schema_missing_field_is_parse_error(monkeypatch):
    payload = json.loads(_arcgis_payload(baseline_records()))
    payload["fields"] = [{"name": "WSSN"}]  # PFOS etc. gone
    _wire_opener(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(pc.PwsParseError):
        pc.fetch_results()


def test_fetch_non_numeric_wssn_crashes_loudly():
    with pytest.raises(ValueError):
        pc.fetch_results(("2001381; DROP TABLE",))


# ==============================================================================
# Client — classification + canonicalization (pure)
# ==============================================================================

@pytest.mark.parametrize("raw,expected_state", [
    ("<2", "nondetect"), ("<4", "nondetect"), ("ND", "nondetect"),
    ("NOT DETECTED", "nondetect"), ("BDL", "nondetect"),
    ("2", "detection"), ("1.9", "detection"), ("3.1", "detection"),
    ("2.1 J", "detection"),                       # trailing qualifier stripped
    ("", "nodata"), (None, "nodata"),
    ("junk", "unrecognized"),                     # fail-safe: never silently clean
])
def test_classify_value(raw, expected_state):
    assert pc.classify_value(raw)[0] == expected_state


def test_classify_value_returns_float_on_detection():
    assert pc.classify_value("2.1 J") == ("detection", 2.1)
    assert pc.classify_value("<2") == ("nondetect", None)


def test_record_view_shape_and_date():
    v = pc.record_view(baseline_records()[0])
    assert set(v) == {"wssn", "system", "loc", "sample_code", "sample_date", "analytes"}
    assert v["sample_date"] == "2025-02-04"
    assert set(v["analytes"]) == set(pc.ANALYTES)


def test_round_key_prefers_sample_code_then_composite():
    v = pc.record_view(baseline_records()[0])
    assert pc.round_key(v) == "871103.01"
    v2 = pc.record_view(_row(2001381, "", _ms(2025, 2, 4), "EP01"))
    assert pc.round_key(v2) == "2025-02-04|EP01"   # composite fallback on blank code


def test_round_detections_clean_vs_hit():
    clean = pc.record_view(baseline_records()[0])
    assert pc.round_detections(clean) == []
    hit = pc.record_view(_row(2001381, "X1", _ms(2026, 1, 1), "EP01", PFOS="5.2"))
    dets = pc.round_detections(hit)
    assert len(dets) == 1 and dets[0]["analyte"] == "PFOS" and dets[0]["value"] == 5.2


def test_epoch_ms_to_date_garbage_falls_back():
    assert pc.epoch_ms_to_date("nope") == "nope"
    assert pc.epoch_ms_to_date(None) == ""


# ==============================================================================
# Watcher — snapshot + diff (pure)
# ==============================================================================

def test_snapshot_filters_by_wssn_and_is_order_stable():
    a = pw.wssn_snapshot(baseline_records(), 2001381)
    b = pw.wssn_snapshot(list(reversed(baseline_records())), 2001381)
    assert pw.snapshot_hash(a) == pw.snapshot_hash(b)
    assert len(a["rounds"]) == 6


def test_snapshot_empty_for_absent_wssn():
    snap = pw.wssn_snapshot(baseline_records(), 9999999)
    assert snap == {"wssn": "9999999", "rounds": {}}


def test_diff_detects_new_round():
    old = pw.wssn_snapshot(baseline_records(), 2001381)
    plus = baseline_records() + [_row(2001381, "NEW999", _ms(2026, 2, 1), "EP01")]
    new = pw.wssn_snapshot(plus, 2001381)
    diff = pw.diff_rounds(old, new)
    assert len(diff["new"]) == 1 and diff["new"][0]["sample_code"] == "NEW999"


def test_summarize_new_clean_round_note():
    old = pw.wssn_snapshot(baseline_records(), 2001381)
    plus = baseline_records() + [_row(2001381, "NEW999", _ms(2026, 2, 1), "EP01")]
    new = pw.wssn_snapshot(plus, 2001381)
    note, body, is_det = pw.summarize_change(pw.diff_rounds(old, new))
    assert is_det is False
    assert "non-detect" in body and "new sampling round" in note


def test_summarize_detection_round():
    old = pw.wssn_snapshot(baseline_records(), 2001381)
    plus = baseline_records() + [_row(2001381, "HIT1", _ms(2026, 2, 1), "EP01", PFOA="6")]
    new = pw.wssn_snapshot(plus, 2001381)
    note, body, is_det = pw.summarize_change(pw.diff_rounds(old, new))
    assert is_det is True
    assert "DETECT" in note.upper() and "PFOA 6 ppt" in body


def test_measurement_dicts_shape():
    dets = pw.all_detections([pc.record_view(
        _row(2001381, "HIT1", _ms(2026, 2, 1), "EP01", PFOS="18"))])
    m = pw.measurement_dicts(dets)[0]
    assert m["metric"] == "pfas_pfos" and m["value"] == "18"
    assert m["unit"] == "ppt" and m["basis"] == "measured"


def test_format_change_body_mentions_label_note_body():
    b = pw.format_change_body("Public Water Supply PFAS — WSSN 2001381", "note-x", "line-1")
    assert "WSSN 2001381" in b and "note-x" in b and "line-1" in b


# ==============================================================================
# Watcher — run() flows (fake Sheets, canned fetch, captured mailer)
# ==============================================================================

PWS_CFG = {"pfas_pws": {"enabled": True, "wssns": [2001381],
                        "recipients": ["trisha@example.org"]}}


def _wire(monkeypatch, cfg=PWS_CFG, records=None, fetch_error=None):
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(pw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(pw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(
        pw.ea, "send_email",
        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(wssns=pc.DEFAULT_WSSNS, url=None, timeout=60):
        if fetch_error is not None:
            raise fetch_error
        return copy.deepcopy(records if records is not None else baseline_records())
    monkeypatch.setattr(pw.pc, "fetch_results", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_PFAS_PWS, [])[1:]  # drop header


def _meas_rows(fake):
    return fake._values._tabs.get(sw.TAB_MEASUREMENTS, [])[1:]


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    monkeypatch.setattr(pw, "load_config", lambda: {"pfas_pws": {"enabled": False}})
    def boom(*a, **k):
        raise AssertionError("must not be called while disabled")
    monkeypatch.setattr(pw.dc, "sheets_service", boom)
    monkeypatch.setattr(pw.pc, "fetch_results", boom)
    assert pw.run() == 0


def test_first_run_baselines_silently(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert pw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 1 and rows[0][1] == "pws:2001381" and rows[0][3] == "baseline"
    assert sent == []                              # baseline never alerts


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert pw.run() == 0
    assert pw.run() == 0
    assert len(_rows(fake)) == 1
    assert sent == []


def test_new_round_clean_alerts_scoped(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert pw.run() == 0
    plus = baseline_records() + [_row(2001381, "NEW999", _ms(2026, 2, 1), "EP01")]
    monkeypatch.setattr(pw.pc, "fetch_results",
                        lambda wssns=None, url=None, timeout=60: copy.deepcopy(plus))
    assert pw.run() == 0
    changed = [r for r in _rows(fake) if r[3] == "changed"]
    assert len(changed) == 1
    assert len(sent) == 1
    subj, body, recips = sent[0]
    assert "non-detect" in body and recips == ["trisha@example.org"]
    assert _meas_rows(fake) == []                  # no detection -> no Measurements row


def test_detection_elevates_and_writes_measurement(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert pw.run() == 0
    plus = baseline_records() + [_row(2001381, "HIT1", _ms(2026, 2, 1), "EP01", PFOS="18")]
    monkeypatch.setattr(pw.pc, "fetch_results",
                        lambda wssns=None, url=None, timeout=60: copy.deepcopy(plus))
    assert pw.run() == 0
    det_rows = [r for r in _rows(fake) if r[3] == "detection"]
    assert len(det_rows) == 1
    subj, body, _ = sent[0]
    assert "DETECTION" in subj and "PFOS 18 ppt" in body
    meas = _meas_rows(fake)
    assert len(meas) == 1
    # Measurements headers: [As-Of, Well ID, Metric, Value, Unit, Basis, ...]
    assert meas[0][2] == "pfas_pfos" and meas[0][3] == "18" and meas[0][5] == "measured"


def test_baseline_with_historical_detection_is_silent_but_noted(monkeypatch):
    recs = baseline_records() + [_row(2222222, "HX", _ms(2019, 5, 1), "EP01",
                                      system="OTHER PWS", PFOA="9")]
    cfg = {"pfas_pws": {"enabled": True, "wssns": [2222222],
                        "recipients": ["t@example.org"]}}
    fake, sent = _wire(monkeypatch, cfg=cfg, records=recs)
    assert pw.run() == 0
    rows = _rows(fake)
    assert rows[0][3] == "baseline" and "HISTORICAL detection" in rows[0][5]
    assert sent == []                              # forward-looking: no alert at baseline


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert pw.run() == 0
    monkeypatch.setattr(pw.pc, "fetch_results",
                        lambda wssns=None, url=None, timeout=60:
                        (_ for _ in ()).throw(pc.PwsFetchError("blip")))
    assert pw.run() == 0
    assert len(_rows(fake)) == 1 and sent == []


def test_fetch_failure_without_baseline_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, fetch_error=pc.PwsFetchError("bot wall"))
    assert pw.run() == 1
    assert sent == []


def test_parse_error_is_always_loud_even_with_baseline(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert pw.run() == 0
    monkeypatch.setattr(pw.pc, "fetch_results",
                        lambda wssns=None, url=None, timeout=60:
                        (_ for _ in ()).throw(pc.PwsParseError("schema drift")))
    assert pw.run() == 1
    assert sent == []


def test_alert_email_failure_still_records_row(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert pw.run() == 0
    plus = baseline_records() + [_row(2001381, "NEW999", _ms(2026, 2, 1), "EP01")]
    monkeypatch.setattr(pw.pc, "fetch_results",
                        lambda wssns=None, url=None, timeout=60: copy.deepcopy(plus))
    monkeypatch.setattr(pw.ea, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))
    assert pw.run() == 0
    assert any(r[3] == "changed" for r in _rows(fake))
