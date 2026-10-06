"""Tests for gfl_feed_snapshot.py (ADR 063): monthly full snapshot of the GFL
perimeter feed + change detection. Hermetic: ArcGIS, Drive and SMTP are faked."""
import io
import json
import zipfile

import pytest

import gfl_feed_snapshot as gs

_BUILD = gs.build_snapshot   # the real one, before any monkeypatch

SVC = "https://example.test/FeatureServer"


def _row(oid, st="MS-1", date=None, h2s=0.0, ch4=2.0, **kw):
    # distinct hourly time per OBJECTID by default, like the real feed
    date = 1_789_660_800_000 + oid * 3_600_000 if date is None else date
    r = {"OBJECTID": oid, "LocName": st, "Date": date, "H2S": h2s, "CH4": ch4,
         "H2S_Text": "BDL", "CH4_Text": str(int(ch4)), "Speed": 1.5, "Direction": 200,
         "Direction_Text": "SSW", "Temp": 60.1, "Relative_Humidity": 70.0,
         "Barometric_Pressure": 29.1, "last_edited_date": 1}
    r.update(kw)
    return r


class FakeFeed:
    """A fake FeatureServer: layer 4 = readings, layer 0 = one station point."""

    def __init__(self, readings, *, server_count=None, page=2):
        self.readings = readings
        self.server_count = server_count
        self.page = page

    def __call__(self, url, params=None, **k):
        if url == SVC:
            return {"layers": [{"id": 0, "name": "Monitoring Locations"}],
                    "tables": [{"id": 4, "name": "Monitoring Data"}]}
        if url.endswith("/0") or url.endswith("/4"):
            names = ["OBJECTID", "Name"] if url.endswith("/0") else list(_row(1))
            return {"fields": [{"name": n} for n in names], "maxRecordCount": self.page}
        if url.endswith("/query"):
            lid = url.split("/")[-2]
            rows = ([{"OBJECTID": 1, "Name": "MS-1"}] if lid == "0" else self.readings)
            if params.get("returnCountOnly"):
                if lid == "4" and self.server_count is not None:
                    return {"count": self.server_count}
                w = params["where"]
                if w.startswith("OBJECTID <="):
                    lim = int(w.split("<=")[1])
                    return {"count": sum(1 for r in rows if r["OBJECTID"] <= lim)}
                return {"count": len(rows)}
            last = int(params["where"].split(">")[1])
            batch = [r for r in rows if r["OBJECTID"] > last][: int(params["resultRecordCount"])]
            feats = [{"attributes": dict(r)} for r in batch]
            if lid == "0":
                for f in feats:
                    f["geometry"] = {"x": -83.5, "y": 42.4}
            return {"features": feats}
        if "dashboard" in url:
            return {"widgets": []}
        raise AssertionError(url)


CFG = {"service_url": SVC, "page_size": 2, "dashboard_item_url": "https://example.test/dashboard"}


# ----- pure diff ---------------------------------------------------------------

def test_diff_finds_deleted_edited_and_added():
    prev = [_row(1), _row(2, h2s=3.1), _row(3)]
    cur = [_row(1), _row(2, h2s=0.0), _row(4)]
    d = gs.diff_readings(prev, cur)
    assert [r["OBJECTID"] for r in d["deleted"]] == [3]
    assert d["edited"] == [("2", {"H2S": (3.1, 0.0)})]
    assert d["added"] == 1


def test_diff_ignores_bookkeeping_fields():
    d = gs.diff_readings([_row(1, last_edited_date=1)], [_row(1, last_edited_date=999)])
    assert not gs.has_alertable_change(d) and d["added"] == 0


def test_diff_csv_strings_equal_fresh_json_values():
    # The previous snapshot is read back from CSV (all strings, '' for null); the
    # current one is fresh JSON. Equal readings must not show as edits.
    prev = [{k: ("" if v is None else str(v)) for k, v in _row(1, Temp=None).items()}]
    d = gs.diff_readings(prev, [_row(1, Temp=None)])
    assert not gs.has_alertable_change(d) and d["added"] == 0
    prev = [{k: str(v) for k, v in _row(1, ch4=5.0).items()}]
    prev[0]["CH4"] = "5"                                   # 5 vs 5.0 is the same value
    assert gs.diff_readings(prev, [_row(1, ch4=5.0)])["edited"] == []


def test_baseline_is_newest_compared_snapshot_else_oldest():
    a, b, c = (f"gfl-feed-snapshot-2026-0{m}-02T1417Z.zip" for m in (7, 8, 9))
    names = [a, a + ".compared", b, b + ".compared", c, "gfl-air-capture-x.json"]
    assert gs.baseline_snapshot_name(names, current="gfl-feed-snapshot-2026-10-02T1417Z.zip") == b
    # c was uploaded but its comparison failed (no marker): still compare against b
    assert gs.baseline_snapshot_name(names + ["new.zip"], current=c) == b
    # nothing ever compared: compare against the OLDEST, so no change is missed
    assert gs.baseline_snapshot_name([b, c], current="z") == b
    assert gs.baseline_snapshot_name(["gfl-air-capture-x.json"]) is None


def test_renumbered_rows_are_not_reported_deleted():
    prev = [_row(1, date=10), _row(2, date=20)]
    cur = [_row(501, date=10), _row(502, date=20, h2s=9.9), _row(503, date=30)]
    d = gs.diff_readings(prev, cur)
    assert d["deleted"] == [] and d["renumbered"] == 2 and d["added"] == 1
    assert d["edited"] == [("2", {"H2S": (0.0, 9.9)})]       # values still compared


def test_reused_objectid_for_another_reading_is_not_an_edit():
    prev = [_row(1, date=10), _row(2, date=20)]
    cur = [_row(1, date=20), _row(7, date=10)]             # OID 1 now holds the date-20 row
    d = gs.diff_readings(prev, cur)
    assert d["deleted"] == [] and d["edited"] == []


def test_field_set_change_is_one_schema_note_not_every_row_edited():
    prev = [_row(i) for i in range(1, 4)]
    cur = []
    for i in range(1, 4):
        r = _row(i)
        r.pop("Relative_Humidity")
        cur.append(r)
    d = gs.diff_readings(prev, cur)
    assert d["edited"] == [] and d["schema"] == {"dropped": ["Relative_Humidity"], "added": []}
    assert gs.has_alertable_change(d)
    subj, body = gs.format_change_email(d, "a", "b")
    assert "field list changed" in subj and "no longer in the feed: Relative_Humidity" in body


def test_csv_columns_keep_unlisted_fields_and_require_objectid():
    assert gs.csv_columns([{"OBJECTID": 1, "X": 2}], ["OBJECTID"]) == ["OBJECTID", "X"]
    with pytest.raises(gs.SnapshotError):
        gs.csv_columns([{"A": 1}], [])


# ----- fetch + build -------------------------------------------------------------

def test_fetch_layer_pages_by_objectid_until_done():
    feed = FakeFeed([_row(i) for i in range(1, 6)], page=2)
    rows = gs.fetch_layer(SVC, 4, 2, get=feed)
    assert [r["OBJECTID"] for r in rows] == [1, 2, 3, 4, 5]


def test_fetch_layer_raises_if_paging_stalls():
    def stuck(url, params=None, **k):
        return {"features": [{"attributes": {"OBJECTID": -1}}], "exceededTransferLimit": True}
    with pytest.raises(gs.SnapshotError):
        gs.fetch_layer(SVC, 4, 1, get=stuck)


def test_build_snapshot_zip_manifest_and_roundtrip():
    readings = [_row(i, h2s=0.1 * i) for i in range(1, 6)]
    blob, got, manifest = gs.build_snapshot(CFG, "2026-10-06T1829Z", get=FakeFeed(readings))
    assert [r["OBJECTID"] for r in got] == [1, 2, 3, 4, 5]
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = set(z.namelist())
    assert {"service.json", "manifest.json", "layer4-Monitoring_Data.csv",
            "layer0-Monitoring_Locations.csv", "dashboard-data.json"} <= names
    m = json.loads(z.read("manifest.json"))
    assert m["fetched_at"] == "2026-10-06T1829Z"
    assert {"id": 4, "name": "Monitoring Data", "csv": "layer4-Monitoring_Data.csv",
            "rows": 5, "server_count": 5} in m["layers"]
    import hashlib
    assert m["files"]["layer4-Monitoring_Data.csv"] == hashlib.sha256(
        z.read("layer4-Monitoring_Data.csv")).hexdigest()
    assert "_geometry" in z.read("layer0-Monitoring_Locations.csv").decode()
    # what we upload, read back, compares clean against the same live pull
    back = gs.readings_from_zip(blob)
    d = gs.diff_readings(back, got)
    assert not gs.has_alertable_change(d) and d["added"] == 0 and d["renumbered"] == 0


def test_build_snapshot_refuses_short_pull():
    feed = FakeFeed([_row(1), _row(2)], server_count=3)
    with pytest.raises(gs.SnapshotError, match="server reports 3"):
        gs.build_snapshot(CFG, "s", get=feed)


def test_change_email_lists_deleted_and_edited():
    d = gs.diff_readings([_row(1), _row(2, h2s=3.1)], [_row(2, h2s=0.0)])
    subj, body = gs.format_change_email(d, "prev.zip", "cur.zip")
    assert "1 past reading(s) deleted, 1 edited" in subj
    assert "OBJECTID 1" in body and "H2S: 3.1 -> 0.0" in body
    assert "only to the monitor's owner list" in body


# ----- run() ---------------------------------------------------------------------

class FakeDrive:
    def __init__(self, files=None):
        self.files_by_name = dict(files or {})       # name -> bytes

    def files(self):
        return self

    def list(self, q=None, fields=None, pageSize=None, pageToken=None):
        assert "in parents" in q and "name contains" not in q   # filter in Python
        names = sorted(self.files_by_name)
        start = int(pageToken or 0)
        page = names[start:start + 2]                            # force pagination
        nxt = str(start + 2) if start + 2 < len(names) else None
        return _Exec({"files": [{"id": n, "name": n} for n in page], "nextPageToken": nxt})


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self, **k):
        return self.v


def _wire(monkeypatch, *, enabled=True, configured=True, drive=None, readings=None,
          owners=("owner@example.test",), send_ok=True):
    cfg = {"gfl_air": {"service_url": SVC},
           "gfl_feed_snapshot": {**CFG, "enabled": enabled}}
    monkeypatch.setattr(gs, "load_config", lambda: cfg)
    monkeypatch.setattr(gs.ac, "is_configured", lambda *a, **k: configured)
    drive = drive or FakeDrive()
    monkeypatch.setattr(gs.ac, "oauth_drive_service", lambda: drive)
    monkeypatch.setattr(gs.ac, "folder_id", lambda *a, **k: "FOLDER")

    def upload(service, path, name, mimetype, folder_id):
        with open(path, "rb") as fh:
            drive.files_by_name[name] = fh.read()
        return "link"
    monkeypatch.setattr(gs.ac, "upload_file", upload)
    monkeypatch.setattr(gs, "_download", lambda d, fid: drive.files_by_name[fid])
    feed = FakeFeed(readings if readings is not None else [_row(1), _row(2)])
    monkeypatch.setattr(gs, "build_snapshot", lambda c, stamp: _BUILD(c, stamp, get=feed))
    sent = []
    monkeypatch.setattr(gs.ea, "load_owner_emails", lambda cfg=None: set(owners))
    monkeypatch.setattr(gs.ea, "send_email",
                        lambda s, b, c, recipients=None: sent.append((s, b, recipients)) or send_ok)
    return drive, sent


def test_run_disabled_is_a_quiet_noop(monkeypatch):
    drive, sent = _wire(monkeypatch, enabled=False)
    assert gs.run() == 0 and drive.files_by_name == {} and sent == []


def test_run_enabled_but_unconfigured_fails_loudly(monkeypatch):
    _wire(monkeypatch, configured=False)
    assert gs.run() == 1


def test_run_first_snapshot_is_a_silent_baseline(monkeypatch):
    drive, sent = _wire(monkeypatch)
    assert gs.run() == 0
    zips = [n for n in drive.files_by_name if n.endswith(".zip")]
    assert len(zips) == 1 and zips[0] + ".compared" in drive.files_by_name and sent == []


def test_run_emails_owner_only_when_past_readings_change(monkeypatch):
    old_blob, _, _ = gs.build_snapshot(CFG, "old", get=FakeFeed([_row(1), _row(2, h2s=3.1)]))
    drive = FakeDrive({"gfl-feed-snapshot-2026-09-02T1400Z.zip": old_blob,
                       "gfl-feed-snapshot-2026-09-02T1400Z.zip.compared": b"{}",
                       "unrelated-a.json": b"", "unrelated-b.json": b""})
    drive, sent = _wire(monkeypatch, drive=drive, readings=[_row(2, h2s=0.0), _row(3)])
    assert gs.run() == 0
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert recipients == ["owner@example.test"]
    assert "1 past reading(s) deleted, 1 edited" in subj
    assert sum(n.endswith(".compared") for n in drive.files_by_name) == 2


def test_run_unchanged_history_sends_nothing(monkeypatch):
    old_blob, _, _ = gs.build_snapshot(CFG, "old", get=FakeFeed([_row(1), _row(2)]))
    drive = FakeDrive({"gfl-feed-snapshot-2026-09-02T1400Z.zip": old_blob,
                       "gfl-feed-snapshot-2026-09-02T1400Z.zip.compared": b"{}",
                       "unrelated-a.json": b"", "unrelated-b.json": b""})
    drive, sent = _wire(monkeypatch, drive=drive, readings=[_row(1), _row(2), _row(3)])
    assert gs.run() == 0 and sent == []


def test_run_changes_with_no_owner_list_fails_loudly(monkeypatch):
    old_blob, _, _ = gs.build_snapshot(CFG, "old", get=FakeFeed([_row(1), _row(2)]))
    drive = FakeDrive({"gfl-feed-snapshot-2026-09-02T1400Z.zip": old_blob,
                       "gfl-feed-snapshot-2026-09-02T1400Z.zip.compared": b"{}",
                       "unrelated-a.json": b"", "unrelated-b.json": b""})
    _, sent = _wire(monkeypatch, drive=drive, readings=[_row(2)], owners=())
    assert gs.run() == 1 and sent == []


def test_run_failed_email_keeps_data_and_retries_same_baseline(monkeypatch):
    old_blob, _, _ = _BUILD(CFG, "old", get=FakeFeed([_row(1), _row(2)]))
    base = "gfl-feed-snapshot-2026-09-02T1400Z.zip"
    drive = FakeDrive({base: old_blob, base + ".compared": b"{}"})
    drive, sent = _wire(monkeypatch, drive=drive, readings=[_row(2)], send_ok=False)
    assert gs.run() == 1
    new = [n for n in drive.files_by_name if n.endswith(".zip") and n != base]
    assert len(new) == 1                                   # the data was still saved
    assert new[0] + ".compared" not in drive.files_by_name  # but not marked compared
    assert gs.baseline_snapshot_name(drive.files_by_name, current="later.zip") == base


def test_run_unreadable_baseline_fails_loudly(monkeypatch):
    base = "gfl-feed-snapshot-2026-09-02T1400Z.zip"
    drive = FakeDrive({base: b"not a zip", base + ".compared": b"{}"})
    drive, sent = _wire(monkeypatch, drive=drive)
    assert gs.run() == 1 and sent == []


def test_count_check_ignores_rows_arriving_mid_pull():
    class Growing(FakeFeed):
        def __call__(self, url, params=None, **k):
            out = super().__call__(url, params, **k)
            if url.endswith("/4/query") and not params.get("returnCountOnly"):
                if not out["features"] and len(self.readings) == 3:
                    self.readings.append(_row(99))           # lands after the last page
            return out
    feed = Growing([_row(1), _row(2), _row(3)], page=2)
    _, got, m = _BUILD(CFG, "s", get=feed)
    assert len(got) == 3
    assert {"id": 4, "name": "Monitoring Data", "csv": "layer4-Monitoring_Data.csv",
            "rows": 3, "server_count": 3} in m["layers"]


def test_run_short_pull_uploads_nothing(monkeypatch):
    drive, sent = _wire(monkeypatch)
    feed = FakeFeed([_row(1)], server_count=2)
    monkeypatch.setattr(gs, "build_snapshot", lambda c, stamp: _BUILD(c, stamp, get=feed))
    assert gs.run() == 1 and drive.files_by_name == {} and sent == []


def test_live_config_ships_disabled():
    import config_loader
    c = config_loader.load_config()["gfl_feed_snapshot"]
    assert c["enabled"] is False


# ----- round-2 review cases --------------------------------------------------------

def test_deletion_among_duplicate_time_rows_is_still_a_deletion():
    a, b = _row(1, date=10), _row(2, date=10)            # same station + time
    d = gs.diff_readings([a, b], [_row(2, date=10)])
    assert [r["OBJECTID"] for r in d["deleted"]] == [1] and d["renumbered"] == 0
    assert gs.has_alertable_change(d)


def test_blank_station_or_time_never_matches_by_key():
    d = gs.diff_readings([_row(1, LocName=None, date="")], [_row(9, LocName=None, date="")])
    assert len(d["deleted"]) == 1 and d["renumbered"] == 0


def test_edited_time_on_same_objectid_is_an_edit():
    d = gs.diff_readings([_row(1, date=10)], [_row(1, date=11)])
    assert d["deleted"] == [] and d["edited"] == [("1", {"Date": (10, 11)})]


def test_mass_renumbering_is_reported_not_silent():
    prev = [_row(i) for i in range(1, gs.RENUMBER_ALERT_MIN + 1)]
    cur = [_row(i + 10_000, date=_row(i)["Date"]) for i in range(1, gs.RENUMBER_ALERT_MIN + 1)]
    d = gs.diff_readings(prev, cur)
    assert d["deleted"] == [] and d["renumbered"] == gs.RENUMBER_ALERT_MIN
    assert gs.has_alertable_change(d)
    subj, _ = gs.format_change_email(d, "a", "b")
    assert "renumbered (not deleted)" in subj


def test_get_json_retries_arcgis_5xx_but_not_4xx(monkeypatch):
    bodies = [{"error": {"code": 500, "message": "Unable to complete operation."}}, {"ok": 1}]

    class Resp:
        def __init__(self, b):
            self.b = json.dumps(b).encode()

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(gs.urllib.request, "urlopen", lambda req, timeout=None: Resp(bodies.pop(0)))
    assert gs._get_json("https://x.test", {}, sleep=lambda s: None) == {"ok": 1}
    bodies[:] = [{"error": {"code": 400, "message": "Invalid query"}}, {"ok": 1}]
    with pytest.raises(gs.SnapshotError, match="Invalid query"):
        gs._get_json("https://x.test", {}, sleep=lambda s: None)
    assert bodies == [{"ok": 1}]                         # 400 was not retried


def test_run_falls_back_to_older_compared_baseline(monkeypatch):
    good, _, _ = _BUILD(CFG, "old", get=FakeFeed([_row(1), _row(2)]))
    a, b = "gfl-feed-snapshot-2026-08-02T1417Z.zip", "gfl-feed-snapshot-2026-09-02T1417Z.zip"
    drive = FakeDrive({a: good, a + ".compared": b"{}", b: b"corrupt", b + ".compared": b"{}"})
    drive, sent = _wire(monkeypatch, drive=drive, readings=[_row(1), _row(2)])
    assert gs.run() == 0
    assert len(sent) == 1 and "baseline snapshot was unreadable" in sent[0][0]
    assert b in sent[0][1]                                 # the skipped one is named


def test_run_same_minute_rerun_does_not_recompare(monkeypatch):
    drive, sent = _wire(monkeypatch)
    assert gs.run() == 0
    n = len(drive.files_by_name)
    assert gs.run() == 0 and len(drive.files_by_name) == n and sent == []


def test_unmarked_baselines_are_tried_oldest_first():
    a, b = "gfl-feed-snapshot-2026-08-02T1417Z.zip", "gfl-feed-snapshot-2026-09-02T1417Z.zip"
    assert gs.baseline_candidates([b, a], current="z") == [a, b]
