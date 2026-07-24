"""ride_client.py / ride_watcher.py — the activation gate, the pure record
canonicalization + snapshot/diff helpers, and the full baseline/unchanged/
changed/fetch-fail flows driven through a fake Sheets service (no network, no
creds). Reuses FakeSheets from test_pfas_watcher, same idiom as test_mmd/
test_rop. The attribute fixtures below are VERBATIM copies of the real
Layer-0/Layer-1 query responses (live-verified 2026-07-23, the overnight-coder
feasibility-gate re-check) — not fabricated data — so the canonicalization,
whitespace-stripping, and epoch-date assumptions are pinned against the real
service shape."""
import copy
import json

import pytest

import ride_client as rc
import ride_watcher as rw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# Fixtures — verbatim Layer-0/Layer-1 attributes (live-verified 2026-07-23)
# ==============================================================================

_SITE_ATTRS = [
    {"SiteID": "81000004", "SiteName": "Arbor Hills - East",
     "RiskCondition": "Risks Present and Require Action in Short-term",
     "Contaminants": ("Chlorinated Volatile and Semi Volatile Organic Compounds, "
                      "Petroleum Volatile and Semi Volatile Organic Compounds"),
     "LastUpdated": 1738540800000},
    {"SiteID": "81000033", "SiteName": "Salem Landfill",
     "RiskCondition": "Risks Present and Require Action in Short-term",
     "Contaminants": ("Elements/Metals/Other Inorganics, Lead, PCB, Petroleum "
                      "Volatile and Semi Volatile Organic Compounds"),
     "LastUpdated": 1760918400000},
    {"SiteID": "81000835", "SiteName": "7667 Chubb Rd ",  # trailing space — real service shape
     "RiskCondition": "Risks Present and Require Action in Short-term",
     "Contaminants": "Carcinogenic PAHs, Elements/Metals/Other Inorganics",
     "LastUpdated": 1689638400000},
    {"SiteID": "81000840", "SiteName": "7941 Salem Road  ",  # trailing double space
     "RiskCondition": "Risks Present and Require Action in Long-term",
     "Contaminants": "Carcinogenic PAHs, Elements/Metals/Other Inorganics, Mercury",
     "LastUpdated": 1689724800000},
    {"SiteID": "82008712", "SiteName": "MITC Corridor",
     "RiskCondition": "Risks Controlled-Interim",
     "Contaminants": ("Chlorinated Volatile and Semi Volatile Organic Compounds, "
                      "Elements/Metals/Other Inorganics, Lead"),
     "LastUpdated": 1747958400000},
]

_UST_ATTRS = [
    {"FacilityID": "00040223", "FacilityName": "GFL Environmental USA, LLC",
     "RiskCondition": "No Known Risks", "Open_Release": 0, "LastUpdated": 1738108800000},
]


def site_records() -> list[dict]:
    return copy.deepcopy(_SITE_ATTRS)


def ust_records() -> list[dict]:
    return copy.deepcopy(_UST_ATTRS)


def _arcgis_payload(records: list[dict], fields: tuple[str, ...]) -> bytes:
    """A structurally-real query response body (fields array + features)."""
    return json.dumps({
        "displayFieldName": fields[0],
        "fieldAliases": {},
        "fields": [{"name": n} for n in ("OID",) + fields],
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
    monkeypatch.setattr(rc, "_opener", lambda: _Op())


def test_fetch_site_records_returns_attribute_dicts(monkeypatch):
    _wire_opener(monkeypatch, _arcgis_payload(site_records(), rc.LAYER0_FIELDS))
    records = rc.fetch_site_records()
    assert len(records) == 5
    assert {r["SiteID"] for r in records} == {r["SiteID"] for r in _SITE_ATTRS}


def test_fetch_ust_records_returns_attribute_dicts(monkeypatch):
    _wire_opener(monkeypatch, _arcgis_payload(ust_records(), rc.LAYER1_FIELDS))
    records = rc.fetch_ust_records()
    assert len(records) == 1
    assert records[0]["FacilityID"] == "00040223"


def test_fetch_http_error_is_fetch_error(monkeypatch):
    _wire_opener(monkeypatch, b"gone", status=503)
    with pytest.raises(rc.RideFetchError):
        rc.fetch_site_records()


def test_fetch_non_json_body_is_fetch_error(monkeypatch):
    _wire_opener(monkeypatch, b"<html>bot wall</html>")
    with pytest.raises(rc.RideFetchError):
        rc.fetch_site_records()


def test_fetch_arcgis_error_payload_is_fetch_error(monkeypatch):
    # ArcGIS's 200-with-error idiom must not be mistaken for an empty result.
    _wire_opener(monkeypatch, json.dumps(
        {"error": {"code": 400, "message": "Failed to execute query."}}).encode())
    with pytest.raises(rc.RideFetchError):
        rc.fetch_ust_records()


def test_fetch_missing_features_is_parse_error(monkeypatch):
    _wire_opener(monkeypatch, json.dumps({"fields": []}).encode())
    with pytest.raises(rc.RideParseError):
        rc.fetch_site_records()


def test_fetch_exceeded_transfer_limit_is_parse_error(monkeypatch):
    payload = json.loads(_arcgis_payload(site_records(), rc.LAYER0_FIELDS))
    payload["exceededTransferLimit"] = True
    _wire_opener(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(rc.RideParseError):
        rc.fetch_site_records()


def test_fetch_schema_missing_field_is_parse_error(monkeypatch):
    # A canonical field vanishing from the layer schema = EGLE reorganized the
    # service; a positional/keyed parse must not be trusted silently.
    payload = json.loads(_arcgis_payload(site_records(), rc.LAYER0_FIELDS))
    payload["fields"] = [f for f in payload["fields"] if f["name"] != "RiskCondition"]
    _wire_opener(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(rc.RideParseError):
        rc.fetch_site_records()


def test_in_clause_escapes_single_quotes():
    # A config value containing a quote must not break out of its literal.
    where = rc._in_clause("SiteID", ["ABC'; DROP--"])
    assert where == "SiteID IN ('ABC''; DROP--')"


# ==============================================================================
# Client — canonicalization (pure)
# ==============================================================================


def test_site_record_view_excludes_oid_and_strips_whitespace():
    view = rc.site_record_view(_SITE_ATTRS[2])  # "7667 Chubb Rd "
    assert set(view) == set(rc.LAYER0_FIELDS)
    assert "OID" not in view
    assert view["SiteName"] == "7667 Chubb Rd"


def test_site_record_view_converts_epoch_dates():
    view = rc.site_record_view(_SITE_ATTRS[0])
    assert view["LastUpdated"] == "2025-02-03"


def test_ust_record_view_normalizes_int_field():
    view = rc.ust_record_view(_UST_ATTRS[0])
    assert view["Open_Release"] == "0"
    assert view["LastUpdated"] == "2025-01-29"


def test_epoch_ms_to_date_garbage_falls_back_to_str():
    # A service-side type change must surface as a visible diff, never a crash.
    assert rc.epoch_ms_to_date("not-a-date") == "not-a-date"
    assert rc.epoch_ms_to_date(None) == ""


# ==============================================================================
# Watcher — snapshot + diff (pure)
# ==============================================================================


def test_site_snapshot_filters_by_id_and_is_order_stable():
    a = rw.site_snapshot(site_records(), "81000033")
    b = rw.site_snapshot(list(reversed(site_records())), "81000033")
    assert a == b
    assert len(a["records"]) == 1
    assert rw.snapshot_hash(a) == rw.snapshot_hash(b)


def test_ust_snapshot_filters_by_facility_id():
    snap = rw.ust_snapshot(ust_records(), "00040223")
    assert snap["records"][0]["RiskCondition"] == "No Known Risks"


def test_site_snapshot_empty_for_absent_id():
    snap = rw.site_snapshot(site_records(), "99999999")
    assert snap == {"site_id": "99999999", "records": []}


def test_site_risk_condition_change_shows_removed_plus_added():
    old = rw.site_snapshot(site_records(), "82008712")
    changed = site_records()
    changed[4]["RiskCondition"] = "Risks Present and Require Action in Short-term"
    new = rw.site_snapshot(changed, "82008712")
    note, body = rw.summarize_site_change(old, new)
    assert "added" in note and "removed" in note
    assert "RiskCondition=Risks Controlled-Interim" in body
    assert "RiskCondition=Risks Present and Require Action in Short-term" in body


def test_ust_open_release_change_is_visible_in_diff():
    old = rw.ust_snapshot(ust_records(), "00040223")
    changed = ust_records()
    changed[0]["Open_Release"] = 1
    changed[0]["RiskCondition"] = "Risks Present and Require Action in Short-term"
    new = rw.ust_snapshot(changed, "00040223")
    assert rw.snapshot_hash(old) != rw.snapshot_hash(new)
    _, body = rw.summarize_ust_change(old, new)
    assert "Open_Release=0" in body and "Open_Release=1" in body


def test_site_appearance_is_the_trip_wire_note():
    old = rw.site_snapshot(site_records(), "99999999")  # empty
    appeared = site_records() + [dict(_SITE_ATTRS[0], SiteID="99999999")]
    new = rw.site_snapshot(appeared, "99999999")
    note, body = rw.summarize_site_change(old, new)
    assert "NOW APPEARS" in note
    assert "+ ADDED" in body


def test_site_disappearance_note():
    old = rw.site_snapshot(site_records(), "81000033")
    new = rw.site_snapshot([], "81000033")
    note, _ = rw.summarize_site_change(old, new)
    assert "NO LONGER LISTED" in note


def test_oid_churn_does_not_change_hash():
    # OID is never fetched at all (explicit outFields excludes it), so a
    # server-side republish renumbering it can't reach the canonical record.
    baseline = rw.site_snapshot(site_records(), "81000033")
    attrs_with_oid = dict(_SITE_ATTRS[1], OID=447)
    renumbered = rw.site_snapshot([attrs_with_oid], "81000033")
    assert rw.snapshot_hash(baseline) == rw.snapshot_hash(renumbered)


def test_format_change_body_mentions_label_and_note():
    body = rw.format_change_body("RIDE Part 201 — Site 81000033", "note-x", "line-1")
    assert "RIDE Part 201 — Site 81000033" in body and "note-x" in body and "line-1" in body


def test_site_label_and_ust_label_include_known_names():
    assert "Salem Landfill" in rw.site_label("81000033")
    assert "GFL" in rw.ust_label("00040223")
    assert rw.site_label("99999999") == "RIDE Part 201 — Site 99999999"  # unknown id: no crash


# ==============================================================================
# Watcher — run() flows (fake Sheets, canned fetches, captured mailer)
# ==============================================================================

RIDE_CFG = {
    "ride": {
        "enabled": True,
        "site_ids": ["81000033", "81000004", "81000835", "81000840", "82008712"],
        "facility_ids": ["00040223"],
        "recipients": ["trisha@example.org"],
    }
}


def _wire(monkeypatch, cfg=RIDE_CFG, sites=None, usts=None,
          site_error=None, ust_error=None):
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(rw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(
        rw.ea, "send_email",
        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch_sites(site_ids=rc.DEFAULT_SITE_IDS, url=None, timeout=60):
        if site_error is not None:
            raise site_error
        return copy.deepcopy(sites if sites is not None else site_records())
    monkeypatch.setattr(rw.rc, "fetch_site_records", _fetch_sites)

    def _fetch_usts(facility_ids=rc.DEFAULT_FACILITY_IDS, url=None, timeout=60):
        if ust_error is not None:
            raise ust_error
        return copy.deepcopy(usts if usts is not None else ust_records())
    monkeypatch.setattr(rw.rc, "fetch_ust_records", _fetch_usts)

    return fake, sent


def _data_rows(fake):
    return fake._values._tabs.get(sw.TAB_RIDE, [])[1:]  # drop the header row


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    monkeypatch.setattr(rw, "load_config", lambda: {"ride": {"enabled": False}})
    def boom(*a, **k):
        raise AssertionError("must not be called while disabled")
    monkeypatch.setattr(rw.dc, "sheets_service", boom)
    monkeypatch.setattr(rw.rc, "fetch_site_records", boom)
    monkeypatch.setattr(rw.rc, "fetch_ust_records", boom)
    assert rw.run() == 0


def test_first_run_baselines_all_six_items_silently(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert rw.run() == 0
    rows = _data_rows(fake)
    assert len(rows) == 6
    assert {r[1] for r in rows} == {
        "ride:81000033", "ride:81000004", "ride:81000835",
        "ride:81000840", "ride:82008712", "ride:00040223",
    }
    assert all(r[3] == "baseline" for r in rows)
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert rw.run() == 0
    assert rw.run() == 0
    assert len(_data_rows(fake)) == 6  # no new rows
    assert sent == []


def test_site_status_change_appends_row_then_alerts_scoped_recipients(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert rw.run() == 0
    changed = site_records()
    changed[4]["RiskCondition"] = "Risks Present and Require Action in Short-term"
    monkeypatch.setattr(rw.rc, "fetch_site_records",
                        lambda site_ids=None, url=None, timeout=60: copy.deepcopy(changed))
    assert rw.run() == 0
    rows = [r for r in _data_rows(fake) if r[3] == "changed"]
    assert len(rows) == 1 and rows[0][1] == "ride:82008712"
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "82008712" in subj and "Short-term" in body
    assert recipients == ["trisha@example.org"]


def test_ust_open_release_change_alerts(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert rw.run() == 0
    changed = ust_records()
    changed[0]["Open_Release"] = 1
    monkeypatch.setattr(rw.rc, "fetch_ust_records",
                        lambda facility_ids=None, url=None, timeout=60: copy.deepcopy(changed))
    assert rw.run() == 0
    rows = [r for r in _data_rows(fake) if r[3] == "changed"]
    assert len(rows) == 1 and rows[0][1] == "ride:00040223"
    assert len(sent) == 1
    assert "1" in sent[0][1]


def test_site_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert rw.run() == 0
    monkeypatch.setattr(rw.rc, "fetch_site_records",
                        lambda site_ids=None, url=None, timeout=60:
                        (_ for _ in ()).throw(rc.RideFetchError("blip")))
    assert rw.run() == 0                     # quiet skip — baselines exist
    assert len(_data_rows(fake)) == 6         # nothing appended (UST still ran)
    assert sent == []


def test_site_fetch_failure_without_baseline_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, site_error=rc.RideFetchError("bot wall"))
    assert rw.run() == 1
    assert sent == []


def test_ust_fetch_failure_without_baseline_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, ust_error=rc.RideFetchError("bot wall"))
    assert rw.run() == 1
    assert sent == []


def test_site_parse_error_is_always_loud_even_with_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert rw.run() == 0                     # baselines recorded
    monkeypatch.setattr(rw.rc, "fetch_site_records",
                        lambda site_ids=None, url=None, timeout=60:
                        (_ for _ in ()).throw(rc.RideParseError("schema drift")))
    assert rw.run() == 1                     # NOT gated on baseline status
    assert sent == []


def test_alert_email_failure_still_records_the_row(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert rw.run() == 0
    changed = ust_records()
    changed[0]["Open_Release"] = 1
    monkeypatch.setattr(rw.rc, "fetch_ust_records",
                        lambda facility_ids=None, url=None, timeout=60: copy.deepcopy(changed))
    def _mail_boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(rw.ea, "send_email", _mail_boom)
    assert rw.run() == 0                     # best-effort alert never fails the run
    assert any(r[3] == "changed" for r in _data_rows(fake))  # durable row landed


def test_should_run_false_when_disabled():
    ok, reason = rw._should_run({"ride": {"enabled": False}})
    assert ok is False
    assert "ride.enabled is false" in reason


def test_should_run_false_when_key_absent():
    ok, _ = rw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = rw._should_run({"ride": {"enabled": True}})
    assert ok is True and reason == ""
