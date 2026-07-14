"""
Hermetic tests for Stream E (GFL perimeter air). No network, no creds, no
committed data — the ArcGIS response is a SMALL synthetic fixture built in-process
(data-guard CI forbids committed *.json captures). Covers the pure client
(mapping / classify / select) and the full watcher run() flows (baseline /
incremental / skip-seen / over-cap / sentinel) driven through a fake Sheets service.
"""
import copy
import re

import gfl_air_client as gc
import gfl_air_watcher as gw
import sheet_writer as sw

# --- synthetic ArcGIS reading fixtures -----------------------------------------

STATIONS = ["MS-1", "MS-2", "MS-3", "MS-4", "MS-5", "MS-6"]
DAY0 = 1_783_900_800_000          # arbitrary fixed epoch-ms (a day boundary)
DAY1 = DAY0 + 86_400_000
THRESH = {"h2s_ppb": 72, "ch4_ppm": 12500}
SENT = {"h2s_ppb": 999, "ch4_ppm": 99999}


def _reading(oid, station, h2s, ch4, date_ms=DAY0, *, temp=75.0, h2s_text="BDL",
             ch4_text=None, speed=1.0, direction=200):
    """One raw ArcGIS 'Monitoring Data' attribute row, the shape _features() yields."""
    return {
        "OBJECTID": oid, "LocName": station, "Date": date_ms,
        "H2S": h2s, "CH4": ch4, "H2S_Text": h2s_text, "CH4_Text": ch4_text,
        "Speed": speed, "Direction": direction, "Temp": temp,
    }


# --- pure client: mapping ------------------------------------------------------

def test_reading_maps_to_measured_h2s_and_ch4():
    ms = gc.reading_to_measurements(_reading(1, "MS-1", 4.0, 12.0), sentinels=SENT)
    by_metric = {m["metric"]: m for m in ms}
    assert set(by_metric) == {"hydrogen_sulfide", "methane"}
    h2s = by_metric["hydrogen_sulfide"]
    assert h2s["basis"] == "measured"          # never a permitted ceiling (ADR-004)
    assert h2s["value"] == 4.0 and h2s["unit"] == "ppb"
    assert h2s["well_id"] == "MS-1"
    assert by_metric["methane"]["unit"] == "ppm" and by_metric["methane"]["value"] == 12.0


def test_note_carries_self_reported_attribution_and_location_type():
    ms = gc.reading_to_measurements(_reading(1, "MS-2", 0.0, 5.0), sentinels=SENT)
    note = ms[0]["note"]
    assert "GFL self-reported" in note                 # mandated attribution, in the row
    assert "location_type=perimeter_station" in note   # schema has no column yet (roadmap)


def test_bdl_flagged_in_note():
    ms = gc.reading_to_measurements(_reading(1, "MS-1", 0.0, 5.0, h2s_text="BDL"), sentinels=SENT)
    h2s = next(m for m in ms if m["metric"] == "hydrogen_sulfide")
    assert "below detection limit" in h2s["note"].lower()


def test_sentinel_values_excluded_from_measured_series():
    # H2S=999 (sentinel) must not become a measured value; CH4=5 still maps.
    ms = gc.reading_to_measurements(_reading(1, "MS-1", 999.0, 5.0), sentinels=SENT)
    assert [m["metric"] for m in ms] == ["methane"]


def test_non_perimeter_station_yields_no_measurements():
    ms = gc.reading_to_measurements(_reading(1, "10-Meter MET Tower", 79.0, 29.0), sentinels=SENT)
    assert ms == []


# --- pure client: classify -----------------------------------------------------

def test_classify_exceedance_is_urgent():
    c = gc.classify_reading(_reading(1, "MS-6", 80.0, 5.0), THRESH, SENT)
    assert c["severity"] == "urgent"
    assert c["h2s"] == (80.0, "exceedance")
    assert c["ch4"][1] == "ok"


def test_classify_below_threshold_is_ok():
    c = gc.classify_reading(_reading(1, "MS-1", 3.0, 20.0), THRESH, SENT)
    assert c["severity"] == "ok"


def test_classify_sentinel_is_anomaly_not_dropped():
    c = gc.classify_reading(_reading(1, "MS-1", 999.0, 5.0), THRESH, SENT)
    assert c["severity"] == "anomaly"
    assert c["h2s"][1] == "sentinel"
    assert c["reasons"]                          # surfaced with a reason, never silent


def test_classify_missing_value():
    c = gc.classify_reading(_reading(1, "MS-1", None, 5.0), THRESH, SENT)
    assert c["h2s"] == (None, "missing")


# --- pure client: select_measurements (digest vs all) --------------------------

def test_select_all_returns_every_reading():
    readings = [_reading(i, "MS-1", float(i), 5.0) for i in range(1, 6)]
    ms = gc.select_measurements(readings, "all", THRESH, sentinels=SENT)
    assert len(ms) == 5 * 2                      # H2S + CH4 for each


def test_select_digest_keeps_daily_peak_plus_exceedances():
    # Five MS-1 readings same day; H2S peak 10, plus one exceedance at 80.
    readings = [_reading(i, "MS-1", float(i), 5.0) for i in range(1, 6)]        # H2S 1..5
    readings.append(_reading(6, "MS-1", 80.0, 5.0))                            # exceedance
    ms = gc.select_measurements(readings, "digest", THRESH, sentinels=SENT)
    h2s = [m for m in ms if m["metric"] == "hydrogen_sulfide"]
    vals = sorted(m["value"] for m in h2s)
    # digest keeps the day's peak (80, which is also the exceedance) — deduped to one
    assert vals == [80.0]
    ch4 = [m for m in ms if m["metric"] == "methane"]
    assert len(ch4) == 1                          # one per-station-per-day CH4 peak


def test_max_oid_ignores_unparseable():
    rows = [_reading(9, "MS-1", 0, 5), {"OBJECTID": "x"}, _reading(17614325, "MS-2", 0, 5)]
    assert gc.max_oid(rows) == 17614325           # numeric, not lexicographic


# --- fake Sheets service (adds clear() to the pfas-test idiom) ------------------

def _tab(rng):
    return re.match(r"'([^']+)'", rng).group(1)


def _start_row(rng):
    m = re.search(r"![A-Z]+(\d+)", rng)
    return int(m.group(1)) if m else 1


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Values:
    def __init__(self, tabs):
        self._tabs = tabs

    def get(self, spreadsheetId, range):
        rows = self._tabs.get(_tab(range))
        if rows is None:
            raise KeyError("no such tab")         # real API raises; gfl_air_cursor propagates
        return _Req({"values": [list(r) for r in rows[_start_row(range) - 1:]]})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._tabs.setdefault(_tab(range), []).extend(list(r) for r in body["values"])
        return _Req({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        rows = self._tabs.setdefault(_tab(range), [])
        start = _start_row(range) - 1
        for i, row in enumerate(body["values"]):
            idx = start + i
            while len(rows) <= idx:
                rows.append([])
            rows[idx] = list(row)
        return _Req({})

    def clear(self, spreadsheetId, range, body):
        rows = self._tabs.get(_tab(range))
        if rows:                                  # keep the header (row 0), drop the body
            del rows[1:]
        return _Req({})


class FakeSheets:
    def __init__(self):
        self._values = _Values({})

    def spreadsheets(self):
        return self

    def get(self, spreadsheetId):
        return _Req({"sheets": [{"properties": {"title": t}} for t in self._values._tabs]})

    def batchUpdate(self, spreadsheetId, body):
        for req in body["requests"]:
            self._values._tabs.setdefault(req["addSheet"]["properties"]["title"], [])
        return _Req({})

    def values(self):
        return self._values


CFG = {"gfl_air": {
    "enabled": True,
    "service_url": "http://svc/FeatureServer",
    "dashboard_url": "http://dash",
    "readings_layer": 4,
    "station_prefix": "MS-",
    "measurements_mode": "digest",
    "thresholds": THRESH,
    "sentinels": SENT,
    "alert_on_sentinel": True,
    "max_new_readings_per_run": 1000,
}}


def _wire(monkeypatch, cfg=CFG):
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(gw.ea, "send_email", lambda subj, body, c: sent.append((subj, body)))
    return fake, sent


def _summary(fake):
    return fake._values._tabs.get(sw.TAB_GFL_AIR, [])[1:]     # drop header


def _measurements(fake):
    return fake._values._tabs.get(sw.TAB_MEASUREMENTS, [])[1:]


# --- gate ----------------------------------------------------------------------

def test_should_run_false_when_disabled():
    ok, _ = gw._should_run({"gfl_air": {"enabled": False}})
    assert ok is False


def test_should_run_false_when_key_absent():
    ok, _ = gw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, _ = gw._should_run({"gfl_air": {"enabled": True}})
    assert ok is True


def test_disabled_is_noop(monkeypatch):
    cfg = {"gfl_air": {"enabled": False}}
    fake, sent = _wire(monkeypatch, cfg)
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert gw.run() == 0
    assert sw.TAB_GFL_AIR not in fake._values._tabs   # never created the tab
    assert sent == []


# --- run() flows ---------------------------------------------------------------

def test_first_run_baselines_no_alert_and_sets_cursor(monkeypatch):
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    base[5]["H2S"] = 90.0                          # a current exceedance on activation day...
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda cfg, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch on baseline")))
    assert gw.run() == 0
    assert len(_summary(fake)) == 6
    assert _measurements(fake) == []               # baseline writes no measurement backlog
    assert sent == []                              # ...but never alerts on the baseline (WDS Rule B)
    assert sw.gfl_air_cursor(fake, "SID") == 105    # cursor = max OBJECTID


def test_incremental_writes_measurements_and_emails_on_exceedance(monkeypatch):
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda cfg, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda cfg, since, limit=None: [])
    gw.run()                                        # baseline -> cursor 105
    assert sw.gfl_air_cursor(fake, "SID") == 105

    new = [_reading(106 + i, s, 0.0, 6.0, DAY1) for i, s in enumerate(STATIONS)]
    new[5]["H2S"] = 80.0                            # MS-6 exceedance (>= 72)

    def fetch(cfg, since, limit=None):
        return [r for r in new if r["OBJECTID"] > since]   # honors the cursor (skip-seen)
    monkeypatch.setattr(gw.gc, "fetch_readings", fetch)

    assert gw.run() == 0
    meas = _measurements(fake)
    assert meas and any(row[2] == "hydrogen_sulfide" and row[5] == "measured" for row in meas)
    assert any(row[10] == "Arbor Hills Landfill" for row in meas)    # facility attribution in row
    assert len(sent) == 1 and "URGENT" in sent[0][0]
    assert sw.gfl_air_cursor(fake, "SID") == 111    # advanced past the batch


def test_second_incremental_with_no_new_readings_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda cfg, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda cfg, since, limit=None: [])
    gw.run()                                        # baseline
    before = len(_measurements(fake))
    assert gw.run() == 0                            # nothing new
    assert len(_measurements(fake)) == before
    assert sent == []


def test_over_cap_rebaselines_without_blasting(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["max_new_readings_per_run"] = 3
    fake, sent = _wire(monkeypatch, cfg)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()                                        # baseline -> 105
    before = len(_measurements(fake))

    # Simulate a source full-table reinsert: far more rows than the cap.
    reinsert = [_reading(500 + i, STATIONS[i % 6], 0.0, 5.0, DAY1) for i in range(10)]
    newbase = [_reading(600 + i, s, 0.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: list(reinsert)[: (limit or 0) + 1])
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(newbase))

    assert gw.run() == 0
    assert len(_measurements(fake)) == before       # no blast into the case file
    assert sent == []                               # re-baseline alerts on none
    assert sw.gfl_air_cursor(fake, "SID") == 605     # cursor jumped to the new baseline max


def test_sentinel_reading_surfaced_not_measured(monkeypatch):
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()
    new = [_reading(106, "MS-1", 999.0, 5.0, DAY1)]  # H2S sentinel
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0
    meas = _measurements(fake)
    # the sentinel H2S is NOT a measured value; the CH4=5 still lands
    assert all("hydrogen_sulfide" not in row[2] or row[3] != 999.0 for row in meas)
    assert len(sent) == 1 and "anomaly" in sent[0][0].lower()   # surfaced as an anomaly


def test_read_error_skips_run_without_rebaselining(monkeypatch):
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()                                        # establish a cursor
    # Now make the cursor read raise (a transient API blip, not "first run").
    def boom(service, sheet_id):
        raise RuntimeError("transient read error")
    monkeypatch.setattr(gw.sw, "gfl_air_cursor", boom)
    called = []
    monkeypatch.setattr(gw.gc, "fetch_baseline",
                        lambda *a, **k: called.append(1) or list(base))
    assert gw.run() == 0
    assert called == []                             # did NOT re-baseline on a read blip


# --- cursor int-parse (the string-max trap) ------------------------------------

def test_cursor_parses_int_not_string_max():
    fake = FakeSheets()
    sw.ensure_gfl_air_tabs(fake, "SID")
    hdr = sw.GFL_AIR_SUMMARY_HEADERS
    j = hdr.index("OBJECTID")
    def row(oid):
        r = ["MS-x", "t", 0, "ok", 5, "ok", 1, 200, 75, oid, "note", "link"]
        return r
    # OBJECTID stored as TEXT ("9" vs "100"): lexicographic max is "9", numeric is 100.
    fake._values._tabs[sw.TAB_GFL_AIR] = [hdr, row("9"), row("100")]
    assert fake._values._tabs[sw.TAB_GFL_AIR][1][j] == "9"
    assert sw.gfl_air_cursor(fake, "SID") == 100
