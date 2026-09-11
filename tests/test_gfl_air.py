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

import pytest

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


def test_watch_config_warnings_flags_inverted_and_passes_sane():
    # watch >= action => the watch tier is dead; warn on both equal and greater.
    assert gc.watch_config_warnings({"ch4_ppm": 500}, {"ch4_ppm": 500})
    warns = gc.watch_config_warnings({"ch4_ppm": 500}, {"ch4_ppm": 600})
    assert warns and "can never fire" in warns[0]
    # sane (watch < action), missing action, and empty watch => no warnings.
    assert gc.watch_config_warnings({"ch4_ppm": 500}, {"ch4_ppm": 40}) == []
    assert gc.watch_config_warnings({}, {"ch4_ppm": 40}) == []
    assert gc.watch_config_warnings({"ch4_ppm": 500}, {}) == []


def test_watch_line_suppresses_sentinel_detail_when_sentinels_silent():
    # A watch reading with a co-located sentinel: alert_on_sentinel False must keep the
    # sentinel detail OUT of the watch line; True lets it ride along.
    r = _reading(1, "MS-1", 999.0, 100.0)          # H2S sentinel + CH4 watch
    lines, has_exc, has_watch = gw.alert_lines([r], THRESH, SENT, False, WATCH)
    assert has_watch and not has_exc and len(lines) == 1
    assert "watch level" in lines[0] and "sentinel" not in lines[0]
    lines_on, _, _ = gw.alert_lines([r], THRESH, SENT, True, WATCH)
    assert "sentinel" in lines_on[0]


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

    def execute(self, num_retries=0):
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
    monkeypatch.delenv("GFL_AIR_WATCH_RECIPIENTS_EXTRA", raising=False)
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(gw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body)))
    # Default: the H2S 24-hr average query returns no stations (no average exceedance),
    # so an incremental poll stays hermetic (no real ArcGIS call). Tests that exercise
    # the average alert override this with their own {station: {avg, n}} mapping.
    monkeypatch.setattr(gw.gc, "fetch_h2s_window_avg", lambda *a, **k: {})
    # The consolidated action-level screening path (ADR 039) fetches best-effort
    # station coords + ±2h context; stub both so tests stay hermetic (they already
    # degrade gracefully on a live-network failure, but the timeout is slow + noisy).
    monkeypatch.setattr(gw.gc, "fetch_station_coords", lambda *a, **k: {})
    monkeypatch.setattr(gw.gc, "fetch_station_window", lambda *a, **k: [])
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


def test_incremental_writes_measurements_and_emails_on_ch4_exceedance(monkeypatch):
    # Under H2S averaging (the default), an instantaneous H2S=80 still LANDS in
    # Measurements (evidence unchanged) but no longer drives the email — the URGENT
    # here is a CH4 instantaneous exceedance. The average query is _wire's default {}.
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda cfg, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda cfg, since, limit=None: [])
    gw.run()                                        # baseline -> cursor 105
    assert sw.gfl_air_cursor(fake, "SID") == 105

    new = [_reading(106 + i, s, 0.0, 6.0, DAY1) for i, s in enumerate(STATIONS)]
    new[5]["H2S"] = 80.0                            # MS-6 instantaneous H2S >= 72 (recorded, NOT alerted)
    new[2]["CH4"] = 13000.0                         # MS-3 instantaneous CH4 >= 12500 action -> URGENT

    def fetch(cfg, since, limit=None):
        return [r for r in new if r["OBJECTID"] > since]   # honors the cursor (skip-seen)
    monkeypatch.setattr(gw.gc, "fetch_readings", fetch)

    assert gw.run() == 0
    meas = _measurements(fake)
    assert meas and any(row[2] == "hydrogen_sulfide" and row[3] == 80.0 and row[5] == "measured"
                        for row in meas)            # the hot H2S hour still archived as evidence
    assert any(row[10] == "Arbor Hills Landfill" for row in meas)    # facility attribution in row
    assert len(sent) == 1 and "URGENT" in sent[0][0]
    _, body = sent[0]
    assert "13000" in body                          # CH4 instantaneous exceedance drove the alert
    assert "H2S=80" not in body                     # instantaneous H2S no longer alerts (averaged)
    assert sw.gfl_air_cursor(fake, "SID") == 111    # advanced past the batch


def test_watch_below_exceedance_is_display_only_without_recipients(monkeypatch):
    # watch_thresholds set but NO watch_alert_recipients => DISPLAY-ONLY (ADR 039):
    # a CH4 reading in [watch, exceedance) sends NO email (the episode engine is
    # skipped AND the watch tier is never folded into the full-list exceedance email),
    # but the snapshot tab still shows 'watch' status.
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["watch_thresholds"] = {"h2s_ppb": 30, "ch4_ppm": 40}
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
    assert sent == []                               # display-only: no email anywhere
    ms3 = next(r for r in _summary(fake) if r[0] == "MS-3")
    assert ms3[5] == "watch"                        # CH4 Status column still shows 'watch'


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


# --- H2S 24-hr rolling average (coder:gfl-air-24h-average) ---------------------
#
# The 72 ppb H2S action level is a 24-HOUR-AVERAGE level, so the H2S exceedance
# alert fires on a rolling per-station AVERAGE (server-side), not one hot hour. CH4
# stays instantaneous. Individual readings still land in Measurements unchanged.

# pure client: the window-cutoff literal + the grouped-stats query/parse -----------

def test_utc_date_literal_is_utc_and_offsets_by_hours():
    now = datetime(2026, 7, 18, 5, 34, 13, tzinfo=timezone.utc)
    assert gc._utc_date_literal(now, 24) == "2026-07-17 05:34:13"
    assert gc._utc_date_literal(now, 0) == "2026-07-18 05:34:13"    # window 0 = no look-back


def test_fetch_h2s_window_avg_query_shape_and_parse(monkeypatch):
    captured = {}

    def fake_query(service_url, layer, params, *, timeout=60):
        captured.update(service_url=service_url, layer=layer, params=params)
        return {"features": [
            {"attributes": {"LocName": "MS-1", "avgH2S": 3.5, "n": 20}},
            {"attributes": {"LocName": "MS-2", "avgH2S": None, "n": 0}},
            {"attributes": {"LocName": "10-Meter MET Tower", "avgH2S": 5.0, "n": 3}},
        ]}
    monkeypatch.setattr(gc, "_query", fake_query)
    cfg_gfl = {"service_url": "http://svc/FeatureServer", "readings_layer": 4,
               "sentinels": {"h2s_ppb": 999}}
    now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)
    out = gc.fetch_h2s_window_avg(cfg_gfl, 24, now=now)

    where = captured["params"]["where"]
    # UTC standardized `date` literal — NOT `TIMESTAMP` (layer date fields are EST-fixed,
    # so a TIMESTAMP cutoff would silently shift the window ~5h; spike 2026-07-18).
    assert "date '2026-07-17 05:00:00'" in where and "TIMESTAMP" not in where
    assert "H2S <> 999" in where                            # sentinel excluded server-side
    assert captured["params"]["groupByFieldsForStatistics"] == "LocName"
    assert '"avg"' in captured["params"]["outStatistics"]   # avg + count grouped stats
    assert '"count"' in captured["params"]["outStatistics"]
    assert captured["layer"] == 4
    # MET tower filtered out by station_prefix; None avg preserved, n coerced to int.
    assert out == {"MS-1": {"avg": 3.5, "n": 20}, "MS-2": {"avg": None, "n": 0}}


def test_fetch_h2s_window_avg_raises_on_fetch_error_never_zero(monkeypatch):
    # A failed average query must PROPAGATE (GflAirFetchError), never be read as 0 ppb.
    monkeypatch.setattr(gc, "_query",
                        lambda *a, **k: (_ for _ in ()).throw(gc.GflAirFetchError("down")))
    with pytest.raises(gc.GflAirFetchError):
        gc.fetch_h2s_window_avg({"service_url": "http://svc", "readings_layer": 4}, 24,
                                now=datetime(2026, 7, 18, tzinfo=timezone.utc))


def test_fetch_h2s_window_avg_omits_sentinel_clause_when_unconfigured(monkeypatch):
    captured = {}
    monkeypatch.setattr(gc, "_query",
                        lambda su, ly, params, **k: captured.update(params=params) or {"features": []})
    gc.fetch_h2s_window_avg({"service_url": "http://svc", "readings_layer": 4,
                             "sentinels": {"h2s_ppb": None}}, 24,
                            now=datetime(2026, 7, 18, tzinfo=timezone.utc))
    assert "H2S <>" not in captured["params"]["where"]      # no sentinel key -> no exclusion clause


# pure watcher: the average -> alert-line decision --------------------------------

def test_h2s_average_alert_lines_fires_when_avg_ge_thr_and_enough_n():
    avgs = {"MS-2": {"avg": 78.0, "n": 20}, "MS-1": {"avg": 3.0, "n": 22}}
    lines, has_exc, notes = gw.h2s_average_alert_lines(avgs, 72, 12, 24)
    assert has_exc and len(lines) == 1 and notes == []
    assert "MS-2" in lines[0] and "24-hr avg" in lines[0] and "78" in lines[0]


def test_h2s_average_alert_lines_suppresses_sparse_window():
    avgs = {"MS-2": {"avg": 78.0, "n": 5}}          # >= 72 but only 5 readings in the window
    lines, has_exc, notes = gw.h2s_average_alert_lines(avgs, 72, 12, 24)
    assert lines == [] and has_exc is False
    assert len(notes) == 1 and "SUPPRESSED" in notes[0] and "MS-2" in notes[0]


def test_h2s_average_alert_lines_silent_below_threshold_or_no_data():
    avgs = {"MS-2": {"avg": 10.0, "n": 24}, "MS-1": {"avg": None, "n": 0}}
    lines, has_exc, notes = gw.h2s_average_alert_lines(avgs, 72, 12, 24)
    assert lines == [] and has_exc is False and notes == []


def test_h2s_average_alert_lines_no_threshold_is_noop():
    avgs = {"MS-2": {"avg": 999.0, "n": 24}}
    lines, has_exc, notes = gw.h2s_average_alert_lines(avgs, None, 12, 24)
    assert lines == [] and has_exc is False and notes == []


# run() flow ----------------------------------------------------------------------

def test_h2s_instantaneous_no_longer_alerts_under_averaging(monkeypatch):
    # A single hot H2S hour (>= 72) with a benign 24-hr average must NOT email — the
    # whole point of the change — but the reading is still archived to Measurements.
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()                                         # baseline
    new = [_reading(106, "MS-6", 80.0, 5.0, DAY1)]   # one hot H2S hour, CH4 benign
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0                             # _wire's default avg {} => benign average
    assert sent == []                                # instantaneous H2S suppressed
    meas = _measurements(fake)
    assert any(row[2] == "hydrogen_sulfide" and row[3] == 80.0 for row in meas)   # still archived


def test_h2s_average_exceedance_alerts_on_zero_instantaneous(monkeypatch):
    # Every instantaneous reading benign (< 72), but the rolling 24-hr per-station
    # average is >= 72 -> ONE URGENT email that names the AVERAGE, not a single reading.
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()
    new = [_reading(106 + i, s, 3.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]   # all < 72
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    monkeypatch.setattr(gw.gc, "fetch_h2s_window_avg",
                        lambda *a, **k: {"MS-4": {"avg": 78.0, "n": 20}})
    assert gw.run() == 0
    assert len(sent) == 1
    subj, body = sent[0]
    assert "URGENT" in subj
    assert "MS-4" in body and "78" in body and "24-hr avg" in body


def test_h2s_average_sparse_window_does_not_alert(monkeypatch):
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()
    new = [_reading(106 + i, s, 3.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    # avg >= 72 but n below min_readings (12) -> suppressed, no email.
    monkeypatch.setattr(gw.gc, "fetch_h2s_window_avg",
                        lambda *a, **k: {"MS-4": {"avg": 78.0, "n": 4}})
    assert gw.run() == 0
    assert sent == []


def test_h2s_average_query_failure_is_skip_and_warn_not_zero(monkeypatch):
    # A failed average query must not break the poll and must not be read as 0 ppb:
    # the run still completes (measurements recorded), just without an average alert.
    fake, sent = _wire(monkeypatch)
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()
    new = [_reading(106 + i, s, 3.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    monkeypatch.setattr(gw.gc, "fetch_h2s_window_avg",
                        lambda *a, **k: (_ for _ in ()).throw(gc.GflAirFetchError("down")))
    assert gw.run() == 0                             # poll completes despite the failed avg query
    assert sent == []                                # no average alert, and NOT a false 0-ppb alert
    assert len(_measurements(fake)) > 0              # readings still recorded


def test_h2s_avg_window_zero_restores_instantaneous_and_skips_avg_query(monkeypatch):
    # Rollback lever: h2s_avg_window_hours=0 => instantaneous H2S alerts again AND the
    # average query is never issued.
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["h2s_avg_window_hours"] = 0
    fake, sent = _wire(monkeypatch, cfg)
    called = []
    monkeypatch.setattr(gw.gc, "fetch_h2s_window_avg",
                        lambda *a, **k: called.append(1) or {})
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()
    new = [_reading(106, "MS-6", 80.0, 5.0, DAY1)]   # instantaneous H2S >= 72
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0
    assert len(sent) == 1 and "URGENT" in sent[0][0]
    _, body = sent[0]
    assert "H2S=80" in body                          # instantaneous H2S exceedance line restored
    assert called == []                              # average query never issued when window=0


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


# --- Consolidated ACTION-LEVEL SCREENING system (ADR 039) ----------------------
# Replaces the per-station CH4-40 watch. Per-(station,gas) open/close episodes on
# the CJ action levels (H2S 30 ppb / ¶def T, CH4 40 ppm / ¶def U), ONE consolidated
# SCREENING (open) + ONE CLOSEOUT (close) email per run, a durable episode log, and
# the mandatory screening legal framing. Pure engine + formatters + sheet-state +
# run() integration; the documented backtest anchors are encoded as hermetic fixtures.

WT = {"h2s_ppb": 30, "ch4_ppm": 40}          # the CJ action-level watch tier (ADR 039)


def _r(oid, station, h2s, ch4, ms, *, h2s_text="", ch4_text=""):
    return _reading(oid, station, h2s, ch4, ms, h2s_text=h2s_text, ch4_text=ch4_text)


def _elev(oid, station, h2s, ch4, ms):
    """A reading with realistic _Text (numeric string, or 'BDL' only for a true 0) —
    so an elevated value is never mislabeled BDL/no-data in the monitor table."""
    return _reading(oid, station, h2s, ch4, ms,
                    h2s_text=("BDL" if h2s == 0 else str(h2s)), ch4_text=str(ch4))


# ----- pure episode engine (process_episodes) --------------------------------

def test_episode_opens_on_strict_above_and_closes_on_return_below():
    rs = [_r(1, "MS-2", 10, 5, DAY0),
          _r(2, "MS-2", 45, 5, DAY0 + 3600_000),      # opens (H2S 45 > 30)
          _r(3, "MS-2", 154.8, 5, DAY0 + 7200_000),   # peak
          _r(4, "MS-2", 12, 5, DAY0 + 10800_000)]     # closes (12 <= 30)
    res = gw.process_episodes(rs, WT, SENT, {})
    assert len(res.opened) == 1
    assert res.opened[0]["station"] == "MS-2" and res.opened[0]["gas"] == "h2s"
    assert len(res.closed) == 1
    c = res.closed[0]
    assert c["peak_value"] == 154.8 and c["opened_value"] == 45 and c["cleared_value"] == 12
    assert c["n_over"] == 2 and c["duration_hours"] == 2.0 and c["source"] == "CJ ¶def T"
    assert res.state == {}                             # re-armed


def test_episode_strictly_above_not_at_the_benchmark():
    # EXACTLY at the benchmark is NOT flagged (¶5.5 "above"); just-above IS.
    assert gw.process_episodes([_r(1, "MS-1", 30.0, 5, DAY0)], WT, SENT, {}).opened == []
    assert gw.process_episodes([_r(1, "MS-1", 30.1, 5, DAY0)], WT, SENT, {}).opened != []
    assert gw.process_episodes([_r(1, "MS-1", 1, 40.0, DAY0)], WT, SENT, {}).opened == []
    assert gw.process_episodes([_r(1, "MS-1", 1, 40.1, DAY0)], WT, SENT, {}).opened != []


def test_episode_per_station_gas_independence():
    # MS-2 H2S and MS-2 CH4 open as two independent episodes (the (station,gas) key).
    res = gw.process_episodes([_r(1, "MS-2", 45, 100, DAY0)], WT, SENT, {})
    assert {(o["station"], o["gas"]) for o in res.opened} == {("MS-2", "h2s"), ("MS-2", "ch4")}
    # A PRIOR open CH4 episode must not stop a fresh H2S open for the same station.
    prev = {"MS-2|ch4": {"event_id": "X", "threshold": 40.0,
                         "opened_at": gc.reading_iso({"Date": DAY0}), "opened_value": 100,
                         "peak_value": 100, "peak_at": gc.reading_iso({"Date": DAY0}), "n_over": 1}}
    res2 = gw.process_episodes([_r(2, "MS-2", 45, 100, DAY0 + 3600_000)], WT, SENT, prev)
    assert [(o["station"], o["gas"]) for o in res2.opened] == [("MS-2", "h2s")]  # only H2S is new


def test_episode_no_data_never_opens_or_closes():
    # sentinel (999 H2S / 99999 CH4) and TEST-texted readings are no-data.
    assert gw.process_episodes([_r(1, "MS-1", 999.0, 5, DAY0)], WT, SENT, {}).opened == []
    assert gw.process_episodes([_r(1, "MS-1", 5, 99999.0, DAY0)], WT, SENT, {}).opened == []
    assert gw.process_episodes(
        [_r(1, "MS-1", 5, 999.0, DAY0, ch4_text="TEST")], WT, SENT, {}).opened == []
    # A no-data reading while OPEN does not close (recovery must be a real reading).
    prev = {"MS-1|h2s": {"event_id": "X", "threshold": 30.0,
                         "opened_at": gc.reading_iso({"Date": DAY0}), "opened_value": 45,
                         "peak_value": 45, "peak_at": gc.reading_iso({"Date": DAY0}), "n_over": 1}}
    res = gw.process_episodes([_r(2, "MS-1", 999.0, 5, DAY0 + 3600_000)], WT, SENT, prev)
    assert res.closed == [] and "MS-1|h2s" in res.state


def test_episode_spans_multiple_runs_tracks_cross_run_peak_closes_once():
    res1 = gw.process_episodes([_r(1, "MS-2", 45, 5, DAY0)], WT, SENT, {})
    assert len(res1.opened) == 1 and res1.closed == []
    res2 = gw.process_episodes([_r(2, "MS-2", 154.8, 5, DAY0 + 3600_000)], WT, SENT, res1.state)
    assert res2.opened == [] and res2.closed == []            # continuing: no re-alert
    assert res2.state["MS-2|h2s"]["peak_value"] == 154.8      # cross-run peak tracked
    res3 = gw.process_episodes([_r(3, "MS-2", 10, 5, DAY0 + 7200_000)], WT, SENT, res2.state)
    assert res3.opened == [] and len(res3.closed) == 1
    c = res3.closed[0]
    assert c["peak_value"] == 154.8 and c["duration_hours"] == 2.0 and c["n_over"] == 2


def test_episode_open_and_close_within_one_run_appears_in_both():
    rs = [_r(1, "MS-3", 326.9, 5, DAY0), _r(2, "MS-3", 1.0, 5, DAY0 + 3600_000)]
    res = gw.process_episodes(rs, WT, SENT, {})
    assert len(res.opened) == 1 and len(res.closed) == 1 and res.state == {}
    assert res.closed[0]["peak_value"] == 326.9 and res.closed[0]["duration_hours"] == 1.0


def test_episode_event_id_is_et_dated_and_seq_disambiguates_dual_gas():
    # The 2025-06-19T01:00Z MS-3 spike is 2025-06-18 in ET; the Event ID uses ET.
    ms = int(datetime(2025, 6, 19, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res = gw.process_episodes([_r(1, "MS-3", 326.9, 45, ms)], WT, SENT, {})
    assert sorted(o["event_id"] for o in res.opened) == [
        "AHL-2025-06-18-MS3-001", "AHL-2025-06-18-MS3-002"]


def test_episode_event_id_seq_is_monotonic_within_a_run_on_reopen():
    # open, close, reopen, close, reopen (same station + ET-day) -> 001, 002, 003 — a
    # freed number is never reused, so the Event IDs in one run's emails/log are all
    # distinct (a same-day reopen does not collide with the earlier episode's ID).
    ms = int(datetime(2024, 2, 22, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rs = [_r(1, "MS-2", 35, 5, ms), _r(2, "MS-2", 20, 5, ms + 3600_000),
          _r(3, "MS-2", 40, 5, ms + 7200_000), _r(4, "MS-2", 10, 5, ms + 10800_000),
          _r(5, "MS-2", 50, 5, ms + 14400_000)]
    res = gw.process_episodes(rs, WT, SENT, {})
    assert [o["event_id"] for o in res.opened] == [
        "AHL-2024-02-22-MS2-001", "AHL-2024-02-22-MS2-002", "AHL-2024-02-22-MS2-003"]


# ----- pure email formatters -------------------------------------------------

def _opened(station, gas, val, ms, *, peak=None, peak_ms=None):
    iso = gc.reading_iso({"Date": ms})
    return {"station": station, "gas": gas, "event_id": gw._event_id(station, iso, 1),
            "threshold": float(WT[gc.gas_cfgkey(gas)]), "opened_at": iso, "opened_value": val,
            "peak_value": peak if peak is not None else val,
            "peak_at": gc.reading_iso({"Date": peak_ms or ms}), "n_over": 1}


def test_screening_email_five_station_one_email_mandatory_wording():
    opened = [_opened(s, "h2s", v, DAY0) for s, v in
              [("MS-1", 77.1), ("MS-2", 78.0), ("MS-3", 73.8), ("MS-4", 73.5), ("MS-6", 74.5)]]
    mr = gw._monitor_rows(
        {o["station"]: _elev(i, o["station"], o["opened_value"], 5, DAY0)
         for i, o in enumerate(opened)}, WT, SENT)
    subj, body = gw.format_screening_email(opened, [], mr, WT, link="L",
                                           retrieved_iso="2026-09-10T13:00:00Z", historical=True)
    expected_date = gw.et_date(gc.reading_iso({"Date": DAY0}))   # ET date of the opening reading
    assert "SCREENING ALERT" in subj and "HISTORICAL" in subj and expected_date in subj
    for st in ("MS-1", "MS-2", "MS-3", "MS-4", "MS-6"):
        assert st in subj and st in body
    # every mandatory legal phrase (verbatim intent):
    assert "NOT a formal determination" in body
    assert 'Arbor Hills Landfill, Inc. ("AHL")' in body and "NOT the operator GFL" in body
    assert "48 hours" in body and "CORRECTION, not to completing the root-cause" in body
    assert "¶5.5(D) or ¶5.5(E)" in body
    assert ("publicly reported hourly value above the numerical benchmark" in body
            and "verification against the required 15-minute rolling-average data is necessary" in body)
    assert "AG STATUS: informational lead" in body
    assert "DAILY BATCH" in body and "▲" in body          # processing note + strictly-above mark
    assert "UNDERCOUNTS true CJ exceedances" in body           # honest-measurement caveat


def test_screening_email_methane_flammable_only_when_in_range():
    lo = [_opened("MS-2", "ch4", 500.0, DAY0)]
    _, body_lo = gw.format_screening_email(lo, [], [], WT, link="L",
                                           retrieved_iso="2026-09-10T13:00:00Z")
    # The actual flammability CLAIM sentence must be ABSENT at 0.05% (a static
    # explanatory note may mention the range in the abstract; the claim must not fire).
    assert "warrants immediate safety assessment" not in body_lo
    hi = [_opened("MS-2", "ch4", 89090.0, DAY0)]
    _, body_hi = gw.format_screening_email(hi, [], [], WT, link="L",
                                           retrieved_iso="2026-09-10T13:00:00Z")
    assert ("8.909% methane falls within methane's ~5-15% flammable range and warrants "
            "immediate safety assessment" in body_hi)
    assert "near-explosive" not in body_hi.lower()             # never the forbidden phrasing


def test_screening_email_subject_live_vs_historical_and_dual_gas():
    opened = [_opened("MS-2", "h2s", 45.0, DAY0), _opened("MS-2", "ch4", 45.0, DAY0)]
    subj_h, _ = gw.format_screening_email(opened, [], [], WT, link="L",
                                          retrieved_iso="2026-09-10T13:00:00Z", historical=True)
    subj_l, _ = gw.format_screening_email(opened, [], [], WT, link="L",
                                          retrieved_iso="2026-09-10T13:00:00Z", historical=False)
    assert subj_h.startswith("[HISTORICAL SCREENING ALERT — NOT A LIVE INCIDENT]")
    assert subj_l.startswith("[SCREENING ALERT]")
    assert "H2S + methane" in subj_h                           # dual-gas names both


def test_closeout_email_start_peak_return_duration():
    ep = gw._close_record(
        "MS-2", "h2s",
        {"event_id": "AHL-2026-07-13-MS2-001", "threshold": 30.0,
         "opened_at": gc.reading_iso({"Date": DAY0}), "opened_value": 45,
         "peak_value": 154.8, "peak_at": gc.reading_iso({"Date": DAY0 + 7200_000}), "n_over": 3},
        gc.reading_iso({"Date": DAY0 + 10800_000}), 12.0)
    subj, body = gw.format_closeout_email([ep], link="L",
                                          retrieved_iso="2026-09-10T13:00:00Z", historical=True)
    assert "returned below" in subj
    assert "first-above" in body and "highest public hourly value 154.8" in body
    assert "first-below-benchmark" in body and "elapsed 3 hr" in body
    assert "does NOT establish that AHL corrected anything" in body


def test_is_historical_pure():
    now = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)    # 2 PM ET, 2026-07-14
    assert gw.is_historical(["2026-06-19T01:00Z"], now) is True         # old data
    assert gw.is_historical(["2026-07-14T17:00Z"], now) is False        # 1 PM ET today
    assert gw.is_historical([], now) is False                          # unknown -> not historical


# ----- sheet_writer: episode-state store (column O) + episode log tab ---------

def test_gfl_air_episode_state_roundtrips_and_survives_a_snapshot_write():
    fake = FakeSheets()
    sw.ensure_gfl_air_tabs(fake, "SID")
    six = [{"station": s, "as_of": "2026-07-10T12:00Z", "h2s": 0, "h2s_status": "ok",
            "ch4": 5, "ch4_status": "ok", "wind": 1, "direction": 200, "temp": 75,
            "oid": 100 + i, "note": "n"} for i, s in enumerate(STATIONS)]
    sw.write_gfl_air_summary(fake, "SID", six, "link")
    assert sw.gfl_air_episode_state(fake, "SID") == {}
    state = {"MS-2|h2s": {"event_id": "AHL-2026-07-13-MS2-001", "threshold": 30.0,
                          "opened_at": "2026-07-13T05:00Z", "opened_value": 45.0,
                          "peak_value": 154.8, "peak_at": "2026-07-13T09:00Z", "n_over": 5}}
    sw.set_gfl_air_episode_state(fake, "SID", state)
    assert sw.gfl_air_episode_state(fake, "SID") == state
    # survives a REPLACE snapshot write and coexists with the column-N stale marker
    sw.set_gfl_air_stale_marker(fake, "SID", "2026-07-10T12:00Z")
    sw.write_gfl_air_summary(fake, "SID", six, "link")
    assert sw.gfl_air_episode_state(fake, "SID") == state
    assert sw.gfl_air_stale_marker(fake, "SID") == "2026-07-10T12:00Z"
    assert len(_summary(fake)) == 6


def test_gfl_air_episode_state_empty_on_blank_garbage_or_legacy_array():
    fake = FakeSheets()
    sw.ensure_gfl_air_tabs(fake, "SID")
    assert sw.gfl_air_episode_state(fake, "SID") == {}          # blank cell
    for junk in ("not json", '["MS-2"]'):                       # garbage + the LEGACY array
        fake.values().update(spreadsheetId="SID", range=f"'{sw.TAB_GFL_AIR}'!O2",
                             valueInputOption="RAW", body={"values": [[junk]]}).execute()
        assert sw.gfl_air_episode_state(fake, "SID") == {}      # fail-safe: re-derive


def test_perimeter_episode_rows_content():
    ep = gw._close_record(
        "MS-2", "h2s",
        {"event_id": "AHL-2026-07-13-MS2-001", "threshold": 30.0,
         "opened_at": "2026-07-13T09:00Z", "opened_value": 45.0,
         "peak_value": 154.8, "peak_at": "2026-07-13T13:00Z", "n_over": 3},
        "2026-07-13T15:00Z", 12.0)
    rows = gw.perimeter_episode_rows([ep], "2026-09-10T13:00:00Z")
    assert len(rows) == 1 and len(rows[0]) == len(sw.PERIMETER_EPISODE_HEADERS)
    r = rows[0]
    assert r[0] == "MS-2" and r[1] == "H2S" and r[2] == "30"
    assert r[9] == "6" and r[10] == 3 and r[11] == "CJ ¶def T"   # duration, n_over, source
    assert "ET" in r[3] and r[12] == "AHL-2026-07-13-MS2-001"


def test_ensure_perimeter_episodes_tab_and_append():
    fake = FakeSheets()
    sw.ensure_perimeter_episodes_tab(fake, "SID")
    assert sw.TAB_PERIMETER_EPISODES in fake._values._tabs
    sw.append_rows(fake, "SID", sw.TAB_PERIMETER_EPISODES,
                   [["MS-1", "H2S"] + [""] * (len(sw.PERIMETER_EPISODE_HEADERS) - 2)])
    assert len(fake._values._tabs[sw.TAB_PERIMETER_EPISODES]) == 2   # header + 1


# ----- run() integration -----------------------------------------------------

_WATCH_RECIPIENTS = ["arbor-hills@trishakunst.com"]


def _watch_cfg():
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["watch_thresholds"] = {"h2s_ppb": 30, "ch4_ppm": 40}
    cfg["gfl_air"]["watch_alert_recipients"] = list(_WATCH_RECIPIENTS)
    return cfg


def _wire_with_recipients(monkeypatch, cfg):
    fake = FakeSheets()
    sent = []                     # (subj, body, recipients)
    monkeypatch.delenv("GFL_AIR_WATCH_RECIPIENTS_EXTRA", raising=False)
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(gw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))
    monkeypatch.setattr(gw.gc, "fetch_h2s_window_avg", lambda *a, **k: {})
    monkeypatch.setattr(gw.gc, "fetch_station_coords", lambda *a, **k: {})
    monkeypatch.setattr(gw.gc, "fetch_station_window", lambda *a, **k: [])
    return fake, sent


def _baseline_then(monkeypatch):
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])
    gw.run()                                                    # baseline -> cursor 105


def test_run_empty_watch_recipients_is_display_only(monkeypatch):
    cfg = copy.deepcopy(CFG)
    cfg["gfl_air"]["watch_thresholds"] = {"h2s_ppb": 30, "ch4_ppm": 40}   # no recipients
    fake, sent = _wire(monkeypatch, cfg)
    _baseline_then(monkeypatch)
    new = [_elev(106 + i, s, 0.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    new[2] = _elev(108, "MS-3", 45.0, 5.0, DAY1)                 # MS-3 H2S 45 (>30 watch)
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0
    assert sent == []                                           # display-only: no email
    assert sw.gfl_air_episode_state(fake, "SID") == {}          # engine skipped entirely
    ms3 = next(r for r in _summary(fake) if r[0] == "MS-3")
    assert ms3[3] == "watch"                                    # H2S Status column shows 'watch'


def test_run_configured_sends_one_consolidated_screening_email(monkeypatch):
    fake, sent = _wire_with_recipients(monkeypatch, _watch_cfg())
    _baseline_then(monkeypatch)
    new = [_elev(106 + i, s, 0.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    new[2] = _elev(108, "MS-3", 0.0, 45.0, DAY1)                # MS-3 CH4 opens (>40)
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "SCREENING ALERT" in subj and "URGENT" not in subj
    assert recipients == _WATCH_RECIPIENTS
    assert "MS-3" in body and 'Arbor Hills Landfill, Inc. ("AHL")' in body
    assert "MS-3|ch4" in sw.gfl_air_episode_state(fake, "SID")


def test_run_five_stations_same_hour_open_is_one_email(monkeypatch):
    fake, sent = _wire_with_recipients(monkeypatch, _watch_cfg())
    _baseline_then(monkeypatch)
    new = [_elev(106 + i, s, 0.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    for i, v in [(0, 77.1), (1, 78.0), (2, 73.8), (3, 73.5), (5, 74.5)]:   # MS-1,2,3,4,6
        new[i] = _elev(106 + i, STATIONS[i], v, 5.0, DAY1)
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0
    assert len(sent) == 1                                       # ONE consolidated email
    subj, body, _ = sent[0]
    for st in ("MS-1", "MS-2", "MS-3", "MS-4", "MS-6"):
        assert st in subj
    assert "MS-5" not in subj                                   # MS-5 (5/0) not flagged
    st_state = sw.gfl_air_episode_state(fake, "SID")
    assert sum(1 for k in st_state if k.endswith("|h2s")) == 5


def test_run_extra_env_merges_and_dedupes_recipients(monkeypatch):
    fake, sent = _wire_with_recipients(monkeypatch, _watch_cfg())
    monkeypatch.setenv("GFL_AIR_WATCH_RECIPIENTS_EXTRA",
                       "extra@example.com, arbor-hills@trishakunst.com")   # 2nd dups config
    _baseline_then(monkeypatch)
    new = [_elev(106 + i, s, 0.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    new[2] = _elev(108, "MS-3", 0.0, 45.0, DAY1)
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0
    assert len(sent) == 1
    assert sent[0][2] == ["arbor-hills@trishakunst.com", "extra@example.com"]


def test_run_continuing_then_recovery_then_reenter(monkeypatch):
    fake, sent = _wire_with_recipients(monkeypatch, _watch_cfg())
    _baseline_then(monkeypatch)

    def poll(oid, ch4, ms):
        rows = [_elev(oid, "MS-3", 0.0, ch4, ms)]
        monkeypatch.setattr(gw.gc, "fetch_readings",
                            lambda c, since, limit=None: [r for r in rows if r["OBJECTID"] > since])
        return gw.run()

    assert poll(106, 45.0, DAY1) == 0                           # opens -> SCREENING (1)
    assert len(sent) == 1 and "SCREENING ALERT" in sent[0][0]
    assert "MS-3|ch4" in sw.gfl_air_episode_state(fake, "SID")

    assert poll(107, 46.0, DAY1 + 3600_000) == 0                # continues -> NO email
    assert len(sent) == 1

    assert poll(108, 10.0, DAY1 + 7200_000) == 0                # recovers -> CLOSEOUT (2)
    assert len(sent) == 2 and "returned below" in sent[1][0]
    assert sw.gfl_air_episode_state(fake, "SID") == {}
    assert len(fake._values._tabs.get(sw.TAB_PERIMETER_EPISODES, [])) == 2   # header + 1 closed row

    assert poll(109, 47.0, DAY1 + 10800_000) == 0               # re-enters -> fresh SCREENING (3)
    assert len(sent) == 3
    assert "MS-3|ch4" in sw.gfl_air_episode_state(fake, "SID")


def test_run_screening_email_best_effort_state_committed_on_send_failure(monkeypatch):
    # A failed SCREENING send must not crash and must not lose the episode STATE
    # (committed like the cursor; the email is best-effort, matching the exceedance
    # email). Because state is committed, a continuing reading does NOT re-alert (the
    # OPEN email is not retried — documented ADR-039 posture); the CLOSEOUT still fires.
    cfg = _watch_cfg()
    fake = FakeSheets()
    monkeypatch.delenv("GFL_AIR_WATCH_RECIPIENTS_EXTRA", raising=False)
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(gw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(gw.gc, "fetch_h2s_window_avg", lambda *a, **k: {})
    monkeypatch.setattr(gw.gc, "fetch_station_coords", lambda *a, **k: {})
    monkeypatch.setattr(gw.gc, "fetch_station_window", lambda *a, **k: [])
    base = [_reading(100 + i, s, 0.0, 5.0) for i, s in enumerate(STATIONS)]
    monkeypatch.setattr(gw.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(base))
    monkeypatch.setattr(gw.gc, "fetch_readings", lambda c, since, limit=None: [])

    def boom(*a, **k):
        raise RuntimeError("SMTP down")
    monkeypatch.setattr(gw.ea, "send_email", boom)
    gw.run()                                                    # baseline

    r1 = [_elev(106, "MS-3", 0.0, 45.0, DAY1)]
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in r1 if r["OBJECTID"] > since])
    assert gw.run() == 0                                        # best-effort: never crashes
    assert "MS-3|ch4" in sw.gfl_air_episode_state(fake, "SID")  # STATE committed despite send fail

    sent = []
    monkeypatch.setattr(gw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))
    r2 = [_elev(107, "MS-3", 0.0, 46.0, DAY1 + 3600_000)]        # continuing
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in r2 if r["OBJECTID"] > since])
    assert gw.run() == 0
    assert sent == []                                           # OPEN not retried (episode open)

    r3 = [_elev(108, "MS-3", 0.0, 10.0, DAY1 + 7200_000)]        # closes
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in r3 if r["OBJECTID"] > since])
    assert gw.run() == 0
    assert len(sent) == 1 and "returned below" in sent[0][0]    # CLOSEOUT still fires


def test_run_exceedance_tier_unchanged_and_independent_of_episode_engine(monkeypatch):
    # A CH4 exceedance (>= 12500 in the fixture) still emails [URGENT] to the DEFAULT
    # full list (recipients None), untouched. An H2S action-level crossing on another
    # station opens an episode -> a SEPARATE consolidated SCREENING email to the watch
    # list. The two tiers fire independently, to different audiences.
    fake, sent = _wire_with_recipients(monkeypatch, _watch_cfg())
    _baseline_then(monkeypatch)
    new = [_elev(106 + i, s, 0.0, 5.0, DAY1) for i, s in enumerate(STATIONS)]
    new[2] = _elev(108, "MS-3", 45.0, 5.0, DAY1)                # MS-3 H2S 45 (action-level watch)
    new[4] = _elev(110, "MS-5", 0.0, 20000.0, DAY1)            # MS-5 CH4 >= 12500 exceedance
    monkeypatch.setattr(gw.gc, "fetch_readings",
                        lambda c, since, limit=None: [r for r in new if r["OBJECTID"] > since])
    assert gw.run() == 0
    urgent = [s for s in sent if "URGENT" in s[0]]
    screening = [s for s in sent if "SCREENING ALERT" in s[0]]
    assert len(urgent) == 1 and urgent[0][2] is None            # exceedance -> full list, unchanged
    assert "20000" in urgent[0][1]
    assert len(screening) == 1 and screening[0][2] == _WATCH_RECIPIENTS
    assert "MS-3" in screening[0][1]                            # H2S action-level episode present


# --- durable air-readings exhibit (ADR 026): select_capture_rows + gating -------

def test_select_capture_keeps_every_elevated_reading():
    # A watch (CH4>=40) and an H2S exceedance are BOTH kept, even inside one
    # baseline window — the full elevated series is captured.
    rows = [
        _reading(1, "MS-1", 3.0, 20.0, DAY0),                 # ok  -> baseline (first, kept)
        _reading(2, "MS-1", 3.0, 100.0, DAY0 + 60_000),       # CH4 100 -> watch -> kept
        _reading(3, "MS-1", 80.0, 5.0, DAY0 + 120_000),       # H2S 80 -> exceedance -> kept
    ]
    kept = gw.select_capture_rows(rows, THRESH, SENT, WATCH, 8, "MS-")
    assert [k["oid"] for k in kept] == [1, 2, 3]
    assert kept[1]["ch4_ppm"] == 100.0 and kept[2]["h2s_ppb"] == 80.0


def test_select_capture_baseline_downsamples_calm_readings():
    # Three calm readings 1h apart -> only the first survives the 8h baseline window.
    rows = [_reading(i, "MS-1", 3.0, 20.0, DAY0 + i * 3_600_000) for i in range(3)]
    kept = gw.select_capture_rows(rows, THRESH, SENT, WATCH, 8, "MS-")
    assert len(kept) == 1 and kept[0]["oid"] == 0


def test_select_capture_baseline_keeps_after_window_elapses():
    rows = [_reading(1, "MS-1", 3.0, 20.0, DAY0),
            _reading(2, "MS-1", 3.0, 20.0, DAY0 + 9 * 3_600_000)]  # >8h later
    kept = gw.select_capture_rows(rows, THRESH, SENT, WATCH, 8, "MS-")
    assert len(kept) == 2


def test_select_capture_filters_non_perimeter_and_maps_fields():
    rows = [_reading(1, "10-Meter MET Tower", 3.0, 20.0, DAY0),   # non-MS- -> dropped
            _reading(2, "MS-2", 5.0, 30.0, DAY0, temp=61.0, speed=2.5, direction=310)]
    kept = gw.select_capture_rows(rows, THRESH, SENT, WATCH, 8, "MS-")
    assert len(kept) == 1
    row = kept[0]
    assert set(row) >= {"monitor_id", "timestamp", "h2s_ppb", "ch4_ppm",
                        "wind_direction", "wind_speed", "temp", "oid"}
    assert row["monitor_id"] == "MS-2" and row["temp"] == 61.0
    assert row["wind_speed"] == 2.5 and row["wind_direction"] == 310


def test_write_capture_is_a_safe_noop_when_disabled_or_unconfigured(monkeypatch):
    # Disabled -> no-op regardless of creds (the shipped default).
    assert gw._write_capture({"capture": {"enabled": False}}, [{"oid": 1}], "2026-07-26T00:00:00Z") == 0
    # Enabled but Drive not configured -> still a safe no-op (ships-safe guarantee).
    monkeypatch.setattr(gw.ac, "is_configured", lambda env: False)
    assert gw._write_capture({"capture": {"enabled": True}}, [{"oid": 1}], "2026-07-26T00:00:00Z") == 0
