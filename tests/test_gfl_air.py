"""
Hermetic tests for Stream E (GFL perimeter air). No network, no creds, no
committed data — the ArcGIS response is a SMALL synthetic fixture built in-process
(data-guard CI forbids committed *.json captures). Covers the pure client
(mapping / classify / select) and the full watcher run() flows (baseline /
incremental / skip-seen / over-cap / sentinel) driven through a fake Sheets service.
"""
import copy
import re
from datetime import datetime, timedelta, timezone

import gfl_air_client as gc
import gfl_air_watcher as gw
import sheet_writer as sw

# --- synthetic ArcGIS reading fixtures -----------------------------------------

STATIONS = ["MS-1", "MS-2", "MS-3", "MS-4", "MS-5", "MS-6"]
DAY0 = 1_783_900_800_000          # arbitrary fixed epoch-ms (a day boundary)
DAY1 = DAY0 + 86_400_000
THRESH = {"h2s_ppb": 72, "ch4_ppm": 12500}
SENT = {"h2s_ppb": 999, "ch4_ppm": 99999}
WATCH = {"ch4_ppm": 40}          # early-warning tier: 40 <= CH4 < 12500 (action) => 'watch'


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


# --- pure client: classify — WATCH tier (coder:gfl-air-thresholds) --------------

def test_classify_watch_below_action_is_watch():
    # CH4 100 is >= watch 40 but < action 12500 -> 'watch' (early-warning), not urgent.
    c = gc.classify_reading(_reading(1, "MS-1", 3.0, 100.0), THRESH, SENT, WATCH)
    assert c["severity"] == "watch"
    assert c["ch4"] == (100.0, "watch")
    assert c["h2s"][1] == "ok"
    assert any("watch level" in r for r in c["reasons"])


def test_classify_below_watch_is_ok():
    c = gc.classify_reading(_reading(1, "MS-1", 3.0, 20.0), THRESH, SENT, WATCH)
    assert c["severity"] == "ok"                 # CH4 20 < watch 40
    assert c["ch4"][1] == "ok"


def test_classify_exceedance_outranks_watch():
    # H2S exceedance (urgent) + CH4 watch on the same reading -> urgent wins.
    c = gc.classify_reading(_reading(1, "MS-1", 80.0, 100.0), THRESH, SENT, WATCH)
    assert c["severity"] == "urgent"
    assert c["h2s"][1] == "exceedance" and c["ch4"][1] == "watch"


def test_classify_watch_outranks_anomaly():
    # H2S sentinel (anomaly) + CH4 watch -> watch outranks anomaly.
    c = gc.classify_reading(_reading(1, "MS-1", 999.0, 100.0), THRESH, SENT, WATCH)
    assert c["severity"] == "watch"
    assert c["h2s"][1] == "sentinel" and c["ch4"][1] == "watch"


def test_classify_no_watch_thresholds_is_single_tier():
    # Omitting watch_thresholds preserves the original single-tier behavior (CH4 100 -> ok).
    c = gc.classify_reading(_reading(1, "MS-1", 3.0, 100.0), THRESH, SENT)
    assert c["severity"] == "ok"
    assert c["ch4"][1] == "ok"


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


def _col_to_idx(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _start_col(rng):
    """0-based start column of a range ('A2'->0, 'A2:L'->0, 'N1'->13). The mock
    must honor columns so a write/read outside the A:L station span (the liveness
    stale-marker in column N) neither clobbers nor is clobbered by the station
    snapshot — exactly the real Sheets behavior."""
    m = re.search(r"!([A-Z]+)\d+", rng)
    return _col_to_idx(m.group(1)) if m else 0


def _end_col(rng):
    """0-based end column when the range names one ('A2:L'->11), else None (open)."""
    m = re.search(r":([A-Z]+)", rng)
    return _col_to_idx(m.group(1)) if m else None


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
        sc, ec = _start_col(range), _end_col(range)
        out = []
        for r in rows[_start_row(range) - 1:]:
            r = list(r)
            if ec is not None:
                r = r[: ec + 1]                   # 'A2:L' returns only cols A..L (real API)
            out.append(r[sc:] if sc else r)       # 'N2' returns col N onward
        return _Req({"values": out})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._tabs.setdefault(_tab(range), []).extend(list(r) for r in body["values"])
        return _Req({})

    def update(self, spreadsheetId, range, valueInputOption, body):
        # OVERLAY at [start_col:], preserving cells OUTSIDE the written span — the
        # real Sheets behavior (an A2:L station write leaves the column-N marker
        # intact, and vice versa). The old `rows[idx] = list(row)` wiped trailing
        # cells, which would have made the marker vanish on the next snapshot write.
        rows = self._tabs.setdefault(_tab(range), [])
        start_r, sc = _start_row(range) - 1, _start_col(range)
        for i, row in enumerate(body["values"]):
            idx = start_r + i
            while len(rows) <= idx:
                rows.append([])
            cur = list(rows[idx])
            while len(cur) < sc + len(row):
                cur.append("")
            for j, v in enumerate(row):
                cur[sc + j] = v
            rows[idx] = cur
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
    # non-blank station rows (the snapshot is written over a fixed padded span)
    return [r for r in fake._values._tabs.get(sw.TAB_GFL_AIR, [])[1:] if r and str(r[0]).strip()]


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


def test_incremental_emails_watch_not_urgent_on_watch_level(monkeypatch):
    # A CH4 reading in [watch, action) sends a LOWER-urgency [GFL air watch] email,
    # never [URGENT]. Wires a CFG that carries watch_thresholds.
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["watch_thresholds"] = {"ch4_ppm": 40}
    fake, sent = _wire(monkeypatch, cfg)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()                                        # baseline -> cursor 105

    new = [_reading(106 + i, s, 0.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    new[2]["CH4"] = 100.0                           # MS-3 CH4 100: >= watch 40, < action 12500

    def fetch(c, since, limit=None):
        return [r for r in new if r["OBJECTID"] > since]
    monkeypatch.setattr(gw.gc, "fetch_readings", fetch)

    assert gw.run() == 0
    assert len(sent) == 1
    subj, body = sent[0]
    assert "GFL air watch" in subj and "URGENT" not in subj
    assert "watch level" in body.lower()


def test_second_incremental_with_no_new_readings_is_noop(monkeypatch):
    # Baseline dated near real "now" (not the fixed DAY0) — this test's only new
    # reading is the baseline itself, so the newest-on-record timestamp never
    # advances; anchoring it to DAY0 would eventually cross max_stale_days as real
    # calendar time passes and spuriously trip the liveness guard (ADR 014),
    # which is a DIFFERENT behavior than what this test is pinning. See
    # test_liveness_silent_when_newest_reading_is_fresh for the same idiom.
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0, _ms_ago(hours=1)) for i, s in enumerate(STATIONS)]
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


def test_summary_is_single_update_and_blanks_a_shrunk_station_set(monkeypatch):
    # Written as one padded update (no clear+update crash window); a station going
    # dark must not leave an orphan row, and the cursor must stay correct.
    fake = FakeSheets()
    sw.ensure_gfl_air_tabs(fake, "SID")
    six = [{"station": s, "as_of": "t", "h2s": 0, "h2s_status": "ok", "ch4": 5,
            "ch4_status": "ok", "wind": 1, "direction": 200, "temp": 75,
            "oid": 100 + i, "note": "n"} for i, s in enumerate(STATIONS)]
    calls = []
    orig_clear = fake._values.clear
    fake._values.clear = lambda *a, **k: (calls.append("clear"), orig_clear(*a, **k))[1]
    sw.write_gfl_air_summary(fake, "SID", six, "link")
    assert calls == []                              # never calls clear()
    assert len(_summary(fake)) == 6
    assert sw.gfl_air_cursor(fake, "SID") == 105
    # A later poll sees only 5 stations (MS-6 dark): the 6th row must be blanked.
    sw.write_gfl_air_summary(fake, "SID", six[:5], "link")
    assert len(_summary(fake)) == 5
    assert sw.gfl_air_cursor(fake, "SID") == 104    # no stale 105 orphan


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


# --- liveness / silent-stall guard (ADR 014 residual) --------------------------
#
# The OBJECTID-reset stall makes `OBJECTID > cursor` return [] FOREVER, which is
# indistinguishable from a healthy quiet. These pin the guard that turns that
# silent zero into ONE same-day "feed appears stale" alert (its own message, not
# an exceedance), fired at most once per stale episode, and always a no-op while
# disabled and on the non-incremental paths.

def _dt(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def _ms_ago(**kw):
    """Epoch-ms for a real-now-relative time — the liveness check compares the
    stored As-Of against the real clock, so run()-level tests anchor to now."""
    when = datetime.now(timezone.utc) - timedelta(**kw)
    return int(when.timestamp() * 1000)


# pure decision (now injected — fully deterministic) ----------------------------

def test_liveness_decision_fires_when_stale_and_unwarned():
    warn, days = gw.liveness_decision("2026-07-10T12:00Z", _dt("2026-07-15T12:00Z"), 3, None)
    assert warn is True and days == 5


def test_liveness_decision_silent_when_fresh():
    warn, days = gw.liveness_decision("2026-07-14T13:00Z", _dt("2026-07-15T12:00Z"), 3, None)
    assert warn is False and days == 0


def test_liveness_decision_silent_when_already_warned_same_episode():
    # same As-Of we already alerted on -> once-per-episode gate holds
    warn, _ = gw.liveness_decision("2026-07-10T12:00Z", _dt("2026-07-15T12:00Z"), 3,
                                   "2026-07-10T12:00Z")
    assert warn is False


def test_liveness_decision_rearms_on_a_newer_as_of():
    # feed recovered to a newer reading then stalled again -> different As-Of -> re-warn
    warn, _ = gw.liveness_decision("2026-07-16T00:00Z", _dt("2026-07-20T12:00Z"), 3,
                                   "2026-07-10T12:00Z")
    assert warn is True


def test_liveness_decision_unparseable_as_of_does_not_fire():
    warn, days = gw.liveness_decision("not-a-timestamp", _dt("2026-07-15T12:00Z"), 3, None)
    assert warn is False and days is None


# run() flow --------------------------------------------------------------------

def _baseline_then_zero(monkeypatch, cfg, date_ms):
    """Baseline the tab with readings dated `date_ms`, then wire an empty
    incremental poll. Returns (fake, sent) positioned just before the stale poll."""
    fake, sent = _wire(monkeypatch, cfg)
    base = [_reading(100 + i, s, 0.0, 5.0, date_ms) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    assert gw.run() == 0                       # baseline: no alert, incl. no liveness
    assert sent == []
    return fake, sent


def test_liveness_fires_one_stale_alert_on_zero_readings(monkeypatch):
    fake, sent = _baseline_then_zero(monkeypatch, CFG, _ms_ago(days=10))
    assert gw.run() == 0                        # zero new readings + stale -> fire
    assert len(sent) == 1
    subj, body = sent[0]
    assert "liveness" in subj.lower() and "STALE" in subj
    assert "URGENT" not in subj                 # NOT an exceedance
    assert "not an exceedance" in body.lower()
    assert sw.gfl_air_stale_marker(fake, "SID") is not None   # episode marker recorded


def test_liveness_silent_when_newest_reading_is_fresh(monkeypatch):
    fake, sent = _baseline_then_zero(monkeypatch, CFG, _ms_ago(hours=1))
    assert gw.run() == 0                        # zero new readings but fresh -> silent
    assert sent == []
    assert sw.gfl_air_stale_marker(fake, "SID") is None


def test_liveness_alerts_at_most_once_per_episode(monkeypatch):
    fake, sent = _baseline_then_zero(monkeypatch, CFG, _ms_ago(days=10))
    gw.run()                                    # stale -> 1 alert
    gw.run()                                    # still stale, same As-Of -> no re-alert
    gw.run()
    assert len(sent) == 1


def test_liveness_is_noop_when_disabled(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["enabled"] = False
    fake, sent = _wire(monkeypatch, cfg)
    # disabled short-circuits before any poll — never even reaches the liveness path
    monkeypatch.setattr(gw.gc, "fetch_baseline",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no work when disabled")))
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no work when disabled")))
    assert gw.run() == 0
    assert sent == []


def test_liveness_not_consulted_on_baseline_path(monkeypatch):
    # First-ever run baselines even from a stale-dated feed and must NOT liveness-alert
    # (the baseline path returns before the zero-readings branch).
    fake, sent = _wire(monkeypatch, CFG)
    base = [_reading(100 + i, s, 0.0, 5.0, _ms_ago(days=30)) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch on baseline")))
    assert gw.run() == 0
    assert sent == []


def test_liveness_not_consulted_on_over_cap_rebaseline(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["max_new_readings_per_run"] = 3
    fake, sent = _baseline_then_zero(monkeypatch, cfg, _ms_ago(days=10))   # stale tab
    # A full-table reinsert takes the OVER-CAP branch, not the zero-readings branch,
    # so liveness must NOT fire even though the tab was stale beforehand.
    reinsert = [_reading(500 + i, STATIONS[i % 6], 0.0, 5.0, _ms_ago(days=10)) for i in range(10)]
    newbase = [_reading(600 + i, s, 0.0, 5.0, _ms_ago(hours=1)) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: list(reinsert)[: (limit or 0) + 1])
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(newbase))
    assert gw.run() == 0
    assert sent == []


def test_liveness_exception_never_breaks_the_poll(monkeypatch):
    fake, sent = _baseline_then_zero(monkeypatch, CFG, _ms_ago(days=10))
    monkeypatch.setattr(gw.sw, "gfl_air_latest_as_of",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gw.run() == 0                        # poll completes cleanly despite the error
    assert sent == []                           # error swallowed, no alert


def test_liveness_unparseable_as_of_no_false_alert(monkeypatch):
    fake, sent = _baseline_then_zero(monkeypatch, CFG, _ms_ago(days=10))
    # Blank out every stored As-Of: freshness can't be computed -> no misleading alert.
    asof = sw.GFL_AIR_SUMMARY_HEADERS.index("As-Of (UTC)")
    for r in fake._values._tabs[sw.TAB_GFL_AIR][1:]:
        if len(r) > asof:
            r[asof] = ""
    assert gw.run() == 0
    assert sent == []


# marker isolation (column N must not disturb the station snapshot) --------------

def test_gfl_air_stale_marker_roundtrips_and_survives_a_snapshot_write():
    fake = FakeSheets()
    sw.ensure_gfl_air_tabs(fake, "SID")
    six = [{"station": s, "as_of": "2026-07-10T12:00Z", "h2s": 0, "h2s_status": "ok",
            "ch4": 5, "ch4_status": "ok", "wind": 1, "direction": 200, "temp": 75,
            "oid": 100 + i, "note": "n"} for i, s in enumerate(STATIONS)]
    sw.write_gfl_air_summary(fake, "SID", six, "link")
    assert sw.gfl_air_stale_marker(fake, "SID") is None
    sw.set_gfl_air_stale_marker(fake, "SID", "2026-07-10T12:00Z")
    assert sw.gfl_air_stale_marker(fake, "SID") == "2026-07-10T12:00Z"
    # column-N marker leaves the snapshot / cursor / latest-As-Of untouched
    assert len(_summary(fake)) == 6
    assert sw.gfl_air_cursor(fake, "SID") == 105
    assert sw.gfl_air_latest_as_of(fake, "SID") == "2026-07-10T12:00Z"
    # and a fresh REPLACE snapshot write (over A:L) does NOT wipe the marker
    sw.write_gfl_air_summary(fake, "SID", six, "link")
    assert sw.gfl_air_stale_marker(fake, "SID") == "2026-07-10T12:00Z"


def test_gfl_air_latest_as_of_takes_the_newest_and_ignores_non_reading_rows():
    fake = FakeSheets()
    sw.ensure_gfl_air_tabs(fake, "SID")
    six = [{"station": s, "as_of": f"2026-07-1{i}T00:00Z", "h2s": 0, "h2s_status": "ok",
            "ch4": 5, "ch4_status": "ok", "wind": 1, "direction": 200, "temp": 75,
            "oid": 100 + i, "note": "n"} for i, s in enumerate(STATIONS)]
    sw.write_gfl_air_summary(fake, "SID", six, "link")
    assert sw.gfl_air_latest_as_of(fake, "SID") == "2026-07-15T00:00Z"   # max across stations
    assert sw.gfl_air_latest_as_of(fake, "SID") is not None
