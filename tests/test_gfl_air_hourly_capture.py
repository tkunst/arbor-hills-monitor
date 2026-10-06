"""Tests for gfl_air_hourly_capture.py (ADR 026 addendum, hourly). Hermetic."""
import copy
from datetime import datetime, timezone

import gfl_air_hourly_capture as hc

NOW = datetime(2026, 10, 7, 9, 23, tzinfo=timezone.utc)
CFG = {"gfl_air": {"service_url": "U", "station_prefix": "MS-",
                   "capture": {"enabled": True, "mode": "all"},
                   "hourly_capture": {"enabled": True, "max_readings_per_run": 3}}}


def _r(oid, st="MS-1", h2s=0.0, ch4=2.0):
    return {"OBJECTID": oid, "LocName": st, "Date": 1_791_000_000_000 + oid,
            "H2S": h2s, "CH4": ch4, "H2S_Text": "BDL", "CH4_Text": str(int(ch4))}


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self, **k):
        return self.v


class FakeDrive:
    def __init__(self, names, pages=2):
        self.names = list(names)
        self.pages = pages
        self.queries = []

    def files(self):
        return self

    def list(self, q=None, fields=None, pageSize=None, pageToken=None):
        self.queries.append(q)
        assert "in parents" in q and "createdTime >" in q
        start = int(pageToken or 0)
        page = self.names[start:start + self.pages]
        nxt = str(start + self.pages) if start + self.pages < len(self.names) else None
        return _Exec({"files": [{"name": n} for n in page], "nextPageToken": nxt})


def test_max_captured_oid_reads_capture_names_only():
    names = ["gfl-air-capture-2026-10-06-oid18389120.json",
             "gfl-air-capture-2026-10-07-oid18390001.json",
             "gfl-feed-snapshot-2026-10-06T2254Z.zip", "gfl-air-capture-bad.json"]
    assert hc.max_captured_oid(names) == 18390001
    assert hc.max_captured_oid(["x.zip"]) is None


def test_find_cursor_pages_and_widens_lookback(monkeypatch):
    calls = []
    monkeypatch.setattr(hc, "_recent_capture_names",
                        lambda d, f, now, days: calls.append(days) or
                        (["gfl-air-capture-2026-09-01-oid5.json"] if days == 30 else []))
    assert hc.find_cursor(None, "F", NOW) == 5 and calls == [3, 30]


def test_recent_names_listing_pages():
    d = FakeDrive([f"gfl-air-capture-2026-10-0{i}-oid{i}.json" for i in range(1, 6)])
    assert len(hc._recent_capture_names(d, "F", NOW, 3)) == 5
    assert "createdTime > '2026-10-04T09:23:00'" in d.queries[0]


def _wire(monkeypatch, cfg=CFG, names=(), readings=(), write=None, fetch_raises=None):
    monkeypatch.setattr(hc, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(hc.ac, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(hc.ac, "oauth_drive_service", lambda: FakeDrive(names))
    monkeypatch.setattr(hc.ac, "folder_id", lambda *a, **k: "F")
    seen = {}

    def fetch(c, since, limit=None):
        if fetch_raises:
            raise fetch_raises
        seen["since"] = since
        out = [r for r in readings if r["OBJECTID"] > since]
        return out[: limit + 1] if limit is not None else out
    monkeypatch.setattr(hc.gc, "fetch_readings", fetch)
    monkeypatch.setattr(hc.gc, "fetch_baseline", lambda c, station_prefix="MS-": list(readings)[-1:])
    written = []
    monkeypatch.setattr(hc.gw, "_write_capture",
                        write or (lambda c, rows, when, suffix="": written.append((rows, suffix)) or len(rows)))
    return seen, written


def test_run_captures_everything_past_the_drive_cursor(monkeypatch):
    seen, written = _wire(monkeypatch, names=["gfl-air-capture-2026-10-06-oid2.json"],
                          readings=[_r(1), _r(2), _r(3), _r(4, st="MS-5")])
    assert hc.run(NOW) == 0
    assert seen["since"] == 2
    rows, suffix = written[0]
    assert [r["oid"] for r in rows] == [3, 4]                  # every reading, no sampling
    assert rows[0]["raw"]["H2S_Text"] == "BDL" and suffix == "-h"


def test_run_caps_a_large_backlog_and_catches_up_later(monkeypatch):
    seen, written = _wire(monkeypatch, names=["gfl-air-capture-2026-10-06-oid0.json"],
                          readings=[_r(i) for i in range(1, 10)])
    assert hc.run(NOW) == 0
    assert [r["oid"] for r in written[0][0]] == [1, 2, 3]     # oldest first, per-run cap


def test_run_with_no_captures_yet_saves_the_baseline_to_start_the_cursor(monkeypatch):
    seen, written = _wire(monkeypatch, names=[], readings=[_r(7), _r(8)])
    assert hc.run(NOW) == 0
    assert [r["oid"] for r in written[0][0]] == [8]          # saved, so a cursor now exists


def test_run_disabled_or_capture_off_is_a_noop(monkeypatch):
    off = copy.deepcopy(CFG)
    off["gfl_air"]["hourly_capture"]["enabled"] = False
    _, written = _wire(monkeypatch, cfg=off, readings=[_r(1)])
    assert hc.run(NOW) == 0 and written == []
    cap_off = copy.deepcopy(CFG)
    cap_off["gfl_air"]["capture"]["enabled"] = False
    _, written = _wire(monkeypatch, cfg=cap_off, readings=[_r(1)])
    assert hc.run(NOW) == 0 and written == []


def test_run_unconfigured_fails_loudly(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(hc.ac, "is_configured", lambda *a, **k: False)
    assert hc.run(NOW) == 1


def test_failed_hour_is_quiet_except_the_daily_report_hour(monkeypatch):
    _wire(monkeypatch, names=["gfl-air-capture-2026-10-06-oid1.json"],
          fetch_raises=hc.gc.GflAirFetchError("timed out"))
    quiet = NOW.replace(hour=10)
    assert quiet.hour not in hc.LOUD_HOURS_UTC and hc.run(quiet) == 0
    for h in hc.LOUD_HOURS_UTC:
        assert hc.run(NOW.replace(hour=h)) == 1
    assert len(hc.LOUD_HOURS_UTC) >= 3                        # a late run can't hide a day


def test_objectids_going_backwards_is_a_failure(monkeypatch):
    _wire(monkeypatch, names=["gfl-air-capture-2026-10-06-oid500.json"],
          readings=[_r(3), _r(4)])                            # source now tops out at 4
    assert hc.run(NOW.replace(hour=hc.LOUD_HOURS_UTC[0])) == 1


def test_batch_with_no_perimeter_rows_is_a_failure_not_success(monkeypatch):
    _, written = _wire(monkeypatch, names=["gfl-air-capture-2026-10-06-oid1.json"],
                       readings=[_r(2, st="10-Meter MET Tower")])
    assert hc.run(NOW.replace(hour=hc.LOUD_HOURS_UTC[0])) == 1 and written == []


def test_hourly_and_daily_names_never_collide_and_both_feed_the_cursor():
    import gfl_air_watcher as gw
    rows = [{"oid": 42}]
    daily = gw._capture_filename(rows, "2026-10-07T13:00:00Z")
    hourly = gw._capture_filename(rows, "2026-10-07T13:00:00Z", hc.HOURLY_SUFFIX)
    assert daily != hourly and hourly.endswith("-h.json")
    assert hc.max_captured_oid([daily]) == 42 and hc.max_captured_oid([hourly]) == 42


def test_real_write_capture_uploads_hourly_named_file(monkeypatch):
    import gfl_air_watcher as gw
    uploads = []
    monkeypatch.setattr(gw.ac, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(gw.ac, "oauth_drive_service", lambda: object())
    monkeypatch.setattr(gw.ac, "folder_id", lambda *a, **k: "F")
    monkeypatch.setattr(gw.ac, "upload_file",
                        lambda d, path, name, mt, fid: uploads.append((name, open(path).read())) or "l")
    rows = gw.select_capture_rows([_r(5), _r(6)], {}, None, None, 1, "MS-", mode="all")
    assert gw._write_capture({"capture": {"enabled": True}}, rows, "2026-10-07T09:23:00Z", "-h") == 2
    name, body = uploads[0]
    assert name == "gfl-air-capture-2026-10-07-oid6-h.json" and '"H2S_Text": "BDL"' in body


def test_widened_lookback_cursor_end_to_end(monkeypatch):
    class Bounded(FakeDrive):
        def list(self, q=None, **k):
            days_ok = "2026-09-07" in q or "2016" in q        # 30-day or 3650-day window
            self.names = ["gfl-air-capture-2026-09-20-oid77.json"] if days_ok else []
            return super().list(q=q, **k)
    assert hc.find_cursor(Bounded([]), "F", NOW) == 77


def test_live_config_enables_hourly_capture():
    import config_loader
    c = config_loader.load_config()["gfl_air"]
    assert c["hourly_capture"]["enabled"] is True and c["capture"]["mode"] == "all"
