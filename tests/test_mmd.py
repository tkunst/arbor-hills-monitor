"""mmd_client.py / mmd_watcher.py — the activation gate, the pure record
canonicalization + snapshot/diff helpers, and the full baseline/unchanged/
changed/fetch-fail flows driven through a fake Sheets service (no network, no
creds). Reuses FakeSheets from test_pfas_watcher, same idiom as test_rop. The
attribute fixtures below are VERBATIM copies of the real layer-0 query
responses for wdsid 475946 (fetched 2026-07-21) — not fabricated data — so the
canonicalization and hidden-record (show=0) assumptions are pinned against the
real service shape."""
import copy
import json

import pytest

import mmd_client as mc
import mmd_watcher as mw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# Fixtures — verbatim layer-0 attributes for wdsid 475946 (2026-07-21)
# ==============================================================================

# The map-hidden compost registration (show=0) — Utilization/CMPST module.
_COMPOST_ATTRS = {
    "OID": 447, "show": 0, "module": "Utilization", "wdsid": 475946,
    "legalsitename": "ARBOR HILLS LANDFILL, INC",
    "specificsitename": "ARBOR HILLS LANDFILL, INC",
    "actcode": "CMPST", "facilitytype": "Compost Facility",
    "addrline1": "10699 W  6 MILE RD", "addrline2": None,
    "city": "NORTHVILLE", "state": "MI", "zip": "48168",
    "countyname": "WASHTENAW", "districtname": "JACKSON",
    "latdeccord": "42.40928", "longdeccord": "-83.55444",
    "p115status": None, "p115authexprdate": None,
    "compoststatus": "Accepting from public",
    "compostregexprdate": 1912118400000,  # 2030-08-05
    "ewasteregexprdate": None, "scraptirestatus": None,
    "scraptireexprdate": None, "endusercmpltype": None, "endusercmpldate": None,
    "disposalareastatus": None, "landfilllink": None,
}

# The landfill record — SolidWaste module, "Active - Accepting".
_LANDFILL_ATTRS = {
    "OID": 448, "show": 1, "module": "SolidWaste", "wdsid": 475946,
    "legalsitename": "ARBOR HILLS LANDFILL, INC",
    "specificsitename": "ARBOR HILLS LANDFILL, INC",
    "actcode": "II", "facilitytype": "Type II MSW Landfill",
    "addrline1": "10699 W  6 MILE RD", "addrline2": None,
    "city": "NORTHVILLE", "state": "MI", "zip": "48168",
    "countyname": "WASHTENAW", "districtname": "JACKSON",
    "latdeccord": "42.40928", "longdeccord": "-83.55444",
    "p115status": None, "p115authexprdate": None,
    "compoststatus": None, "compostregexprdate": None,
    "ewasteregexprdate": None, "scraptirestatus": None,
    "scraptireexprdate": None, "endusercmpltype": None, "endusercmpldate": None,
    "disposalareastatus": "Active - Accepting",
    "landfilllink": ("https://www.michigan.gov/egle/about/organization/"
                     "Materials-Management/solid-waste/Solid-Waste-Disposal-Areas/"
                     "landfill/475946"),
}


def baseline_records() -> list[dict]:
    """The real 2026-07-21 fetch result: both 475946 records, nothing for
    465941 (absent from the service — its empty set IS the baseline)."""
    return [copy.deepcopy(_COMPOST_ATTRS), copy.deepcopy(_LANDFILL_ATTRS)]


def _arcgis_payload(records: list[dict]) -> bytes:
    """A structurally-real query response body (fields array + features)."""
    return json.dumps({
        "displayFieldName": "legalsitename",
        "fieldAliases": {},
        "fields": [{"name": n} for n in
                   ("OID", "latdeccord", "longdeccord") + mc.RECORD_FIELDS],
        "features": [{"attributes": r} for r in records],
    }).encode()


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def getcode(self):
        return self.status


# ==============================================================================
# Client — fetch guards (pure, opener mocked)
# ==============================================================================


def _wire_opener(monkeypatch, body: bytes, status: int = 200):
    class _Op:
        def open(self, url, timeout=60):
            return _FakeResp(body, status)
    monkeypatch.setattr(mc, "_opener", lambda: _Op())


def test_fetch_returns_attribute_dicts(monkeypatch):
    _wire_opener(monkeypatch, _arcgis_payload(baseline_records()))
    records = mc.fetch_records((475946, 465941))
    assert len(records) == 2
    assert {r["wdsid"] for r in records} == {475946}


def test_fetch_http_error_is_fetch_error(monkeypatch):
    _wire_opener(monkeypatch, b"gone", status=503)
    with pytest.raises(mc.MmdFetchError):
        mc.fetch_records()


def test_fetch_non_json_body_is_fetch_error(monkeypatch):
    _wire_opener(monkeypatch, b"<html>bot wall</html>")
    with pytest.raises(mc.MmdFetchError):
        mc.fetch_records()


def test_fetch_arcgis_error_payload_is_fetch_error(monkeypatch):
    # ArcGIS's 200-with-error idiom must not be mistaken for an empty result.
    _wire_opener(monkeypatch, json.dumps(
        {"error": {"code": 400, "message": "Failed to execute query."}}).encode())
    with pytest.raises(mc.MmdFetchError):
        mc.fetch_records()


def test_fetch_missing_features_is_parse_error(monkeypatch):
    _wire_opener(monkeypatch, json.dumps({"fields": []}).encode())
    with pytest.raises(mc.MmdParseError):
        mc.fetch_records()


def test_fetch_exceeded_transfer_limit_is_parse_error(monkeypatch):
    payload = json.loads(_arcgis_payload(baseline_records()))
    payload["exceededTransferLimit"] = True
    _wire_opener(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(mc.MmdParseError):
        mc.fetch_records()


def test_fetch_schema_missing_field_is_parse_error(monkeypatch):
    # A canonical field vanishing from the layer schema = EGLE reorganized the
    # service; a positional/keyed parse must not be trusted silently.
    payload = json.loads(_arcgis_payload(baseline_records()))
    payload["fields"] = [f for f in payload["fields"]
                         if f["name"] != "disposalareastatus"]
    _wire_opener(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(mc.MmdParseError):
        mc.fetch_records()


def test_fetch_non_numeric_wdsid_crashes_loudly():
    # A config typo must crash, not be smoothed into a bad where-clause.
    with pytest.raises(ValueError):
        mc.fetch_records(("475946; DROP TABLE",))


# ==============================================================================
# Client — canonicalization (pure)
# ==============================================================================


def test_record_view_excludes_oid_and_coords():
    view = mc.record_view(_LANDFILL_ATTRS)
    assert set(view) == set(mc.RECORD_FIELDS)
    assert "OID" not in view and "latdeccord" not in view


def test_record_view_converts_epoch_dates():
    view = mc.record_view(_COMPOST_ATTRS)
    assert view["compostregexprdate"] == "2030-08-05"
    assert view["p115authexprdate"] == ""  # None -> ""


def test_record_view_keeps_show_flag():
    assert mc.record_view(_COMPOST_ATTRS)["show"] == "0"
    assert mc.record_view(_LANDFILL_ATTRS)["show"] == "1"


def test_epoch_ms_to_date_garbage_falls_back_to_str():
    # A service-side type change must surface as a visible diff, not a crash.
    assert mc.epoch_ms_to_date("not-a-date") == "not-a-date"
    assert mc.epoch_ms_to_date(None) == ""


# ==============================================================================
# Watcher — snapshot + diff (pure)
# ==============================================================================


def test_snapshot_filters_by_wdsid_and_is_order_stable():
    a = mw.wdsid_snapshot(baseline_records(), 475946)
    b = mw.wdsid_snapshot(list(reversed(baseline_records())), 475946)
    assert a == b
    assert len(a["records"]) == 2
    assert mw.snapshot_hash(a) == mw.snapshot_hash(b)


def test_snapshot_empty_for_absent_wdsid():
    snap = mw.wdsid_snapshot(baseline_records(), 465941)
    assert snap == {"wdsid": "465941", "records": []}


def test_status_change_shows_removed_plus_added():
    old = mw.wdsid_snapshot(baseline_records(), 475946)
    changed = baseline_records()
    changed[1]["disposalareastatus"] = "Closed"
    new = mw.wdsid_snapshot(changed, 475946)
    note, body = mw.summarize_change(old, new)
    assert "added" in note and "removed" in note
    assert "disposalareastatus=Active - Accepting" in body
    assert "disposalareastatus=Closed" in body


def test_appearance_is_the_trip_wire_note():
    old = mw.wdsid_snapshot(baseline_records(), 465941)   # empty
    appeared = baseline_records() + [dict(_LANDFILL_ATTRS, wdsid=465941,
                                          legalsitename="ARBOR HILLS COMPOST AREA")]
    new = mw.wdsid_snapshot(appeared, 465941)
    note, body = mw.summarize_change(old, new)
    assert "NOW APPEARS" in note
    assert "+ ADDED" in body


def test_disappearance_note():
    old = mw.wdsid_snapshot(baseline_records(), 475946)
    new = mw.wdsid_snapshot([], 475946)
    note, _ = mw.summarize_change(old, new)
    assert "NO LONGER LISTED" in note


def test_hidden_record_show_flip_is_visible_in_diff():
    old = mw.wdsid_snapshot(baseline_records(), 475946)
    changed = baseline_records()
    changed[0]["show"] = 1  # the hidden compost record surfaces on the map
    new = mw.wdsid_snapshot(changed, 475946)
    assert mw.snapshot_hash(old) != mw.snapshot_hash(new)
    _, body = mw.summarize_change(old, new)
    assert "show=0" in body and "show=1" in body


def test_oid_churn_does_not_change_hash():
    # A service republish renumbering OIDs must never fire an alert.
    renumbered = baseline_records()
    for r in renumbered:
        r["OID"] += 1000
    assert (mw.snapshot_hash(mw.wdsid_snapshot(baseline_records(), 475946))
            == mw.snapshot_hash(mw.wdsid_snapshot(renumbered, 475946)))


def test_format_change_body_mentions_label_and_note():
    body = mw.format_change_body("MMD Open Data — WDS 475946", "note-x", "line-1")
    assert "MMD Open Data — WDS 475946" in body and "note-x" in body and "line-1" in body


# ==============================================================================
# Watcher — run() flows (fake Sheets, canned fetch, captured mailer)
# ==============================================================================

MMD_CFG = {
    "mmd": {
        "enabled": True,
        "wdsids": [475946, 465941],
        "recipients": ["trisha@example.org"],
    }
}


def _wire(monkeypatch, cfg=MMD_CFG, records=None, fetch_error=None):
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(mw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(mw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(
        mw.ea, "send_email",
        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(wdsids=mc.DEFAULT_WDSIDS, url=None, timeout=60):
        if fetch_error is not None:
            raise fetch_error
        return copy.deepcopy(records if records is not None else baseline_records())
    monkeypatch.setattr(mw.mc, "fetch_records", _fetch)
    return fake, sent


def _data_rows(fake):
    return fake._values._tabs.get(sw.TAB_MMD, [])[1:]  # drop the header row


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    monkeypatch.setattr(mw, "load_config", lambda: {"mmd": {"enabled": False}})
    def boom(*a, **k):
        raise AssertionError("must not be called while disabled")
    monkeypatch.setattr(mw.dc, "sheets_service", boom)
    monkeypatch.setattr(mw.mc, "fetch_records", boom)
    assert mw.run() == 0


def test_first_run_baselines_both_items_silently(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert mw.run() == 0
    rows = _data_rows(fake)
    assert len(rows) == 2
    assert {r[1] for r in rows} == {"mmd:475946", "mmd:465941"}
    assert all(r[3] == "baseline" for r in rows)
    assert sent == []  # baseline never alerts — including the EMPTY 465941 set


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert mw.run() == 0
    assert mw.run() == 0
    assert len(_data_rows(fake)) == 2  # no new rows
    assert sent == []


def test_status_change_appends_row_then_alerts_scoped_recipients(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert mw.run() == 0
    changed = baseline_records()
    changed[1]["disposalareastatus"] = "Closed"
    monkeypatch.setattr(mw.mc, "fetch_records",
                        lambda wdsids=None, url=None, timeout=60: copy.deepcopy(changed))
    assert mw.run() == 0
    rows = [r for r in _data_rows(fake) if r[3] == "changed"]
    assert len(rows) == 1 and rows[0][1] == "mmd:475946"
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "475946" in subj and "Closed" in body
    assert recipients == ["trisha@example.org"]


def test_compost_area_appearing_alerts(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert mw.run() == 0
    appeared = baseline_records() + [dict(_LANDFILL_ATTRS, OID=999, wdsid=465941,
                                          legalsitename="ARBOR HILLS COMPOST AREA")]
    monkeypatch.setattr(mw.mc, "fetch_records",
                        lambda wdsids=None, url=None, timeout=60: copy.deepcopy(appeared))
    assert mw.run() == 0
    assert len(sent) == 1
    assert "NOW APPEARS" in sent[0][1]


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert mw.run() == 0
    monkeypatch.setattr(mw.mc, "fetch_records",
                        lambda wdsids=None, url=None, timeout=60:
                        (_ for _ in ()).throw(mc.MmdFetchError("blip")))
    assert mw.run() == 0                     # quiet skip — baselines exist
    assert len(_data_rows(fake)) == 2        # nothing appended
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, fetch_error=mc.MmdFetchError("bot wall"))
    assert mw.run() == 1
    assert sent == []


def test_parse_error_is_always_loud_even_with_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert mw.run() == 0                     # baselines recorded
    monkeypatch.setattr(mw.mc, "fetch_records",
                        lambda wdsids=None, url=None, timeout=60:
                        (_ for _ in ()).throw(mc.MmdParseError("schema drift")))
    assert mw.run() == 1                     # NOT gated on baseline status
    assert sent == []


def test_alert_email_failure_still_records_the_row(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert mw.run() == 0
    changed = baseline_records()
    changed[1]["disposalareastatus"] = "Closed"
    monkeypatch.setattr(mw.mc, "fetch_records",
                        lambda wdsids=None, url=None, timeout=60: copy.deepcopy(changed))
    def _mail_boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(mw.ea, "send_email", _mail_boom)
    assert mw.run() == 0                     # best-effort alert never fails the run
    assert any(r[3] == "changed" for r in _data_rows(fake))  # durable row landed
