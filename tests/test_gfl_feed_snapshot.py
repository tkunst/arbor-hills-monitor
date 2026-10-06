"""Tests for gfl_feed_snapshot.py (ADR 063): monthly full snapshot of the GFL
perimeter feed + change detection. Hermetic: ArcGIS, Drive and SMTP are faked."""
import io
import json
import zipfile

import pytest

import gfl_feed_snapshot as gs

_BUILD = gs.build_snapshot   # the real one, before any monkeypatch

SVC = "https://example.test/FeatureServer"


def _row(oid, st="MS-1", date=1_789_660_800_000, h2s=0.0, ch4=2.0, **kw):
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
                n = self.server_count if (lid == "4" and self.server_count is not None) else len(rows)
                return {"count": n}
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
    assert d == {"deleted": [], "edited": [], "added": 0}


def test_diff_csv_strings_equal_fresh_json_values():
    # The previous snapshot is read back from CSV (all strings, '' for null); the
    # current one is fresh JSON. Equal readings must not show as edits.
    prev = [{k: ("" if v is None else str(v)) for k, v in _row(1, Temp=None).items()}]
    assert gs.diff_readings(prev, [_row(1, Temp=None)]) == {"deleted": [], "edited": [], "added": 0}
    prev = [{k: str(v) for k, v in _row(1, ch4=5.0).items()}]
    prev[0]["CH4"] = "5"                                   # 5 vs 5.0 is the same value
    assert gs.diff_readings(prev, [_row(1, ch4=5.0)])["edited"] == []


def test_previous_snapshot_name_picks_newest_other():
    names = ["gfl-feed-snapshot-2026-09-02T1400Z.zip", "gfl-feed-snapshot-2026-10-02T1400Z.zip",
             "gfl-air-capture-2026-10-01-oid5.json", "gfl-feed-snapshot-2026-08-02T1400Z.zip"]
    assert gs.previous_snapshot_name(names, current="gfl-feed-snapshot-2026-10-02T1400Z.zip") \
        == "gfl-feed-snapshot-2026-09-02T1400Z.zip"
    assert gs.previous_snapshot_name(["gfl-air-capture-x.json"]) is None


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
    assert {"id": 4, "name": "Monitoring Data", "rows": 5, "server_count": 5} in m["layers"]
    import hashlib
    assert m["files"]["layer4-Monitoring_Data.csv"] == hashlib.sha256(
        z.read("layer4-Monitoring_Data.csv")).hexdigest()
    assert "_geometry" in z.read("layer0-Monitoring_Locations.csv").decode()
    # what we upload, read back, compares clean against the same live pull
    back = gs.readings_from_zip(blob)
    assert gs.diff_readings(back, got) == {"deleted": [], "edited": [], "added": 0}


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
        out = [{"id": n, "name": n} for n in self.files_by_name if gs._PREFIX in n]
        return _Exec({"files": out})


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
    assert len(drive.files_by_name) == 1 and sent == []


def test_run_emails_owner_only_when_past_readings_change(monkeypatch):
    old_blob, _, _ = gs.build_snapshot(CFG, "old", get=FakeFeed([_row(1), _row(2, h2s=3.1)]))
    drive = FakeDrive({"gfl-feed-snapshot-2026-09-02T1400Z.zip": old_blob})
    drive, sent = _wire(monkeypatch, drive=drive, readings=[_row(2, h2s=0.0), _row(3)])
    assert gs.run() == 0
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert recipients == ["owner@example.test"]
    assert "1 past reading(s) deleted, 1 edited" in subj


def test_run_unchanged_history_sends_nothing(monkeypatch):
    old_blob, _, _ = gs.build_snapshot(CFG, "old", get=FakeFeed([_row(1), _row(2)]))
    drive = FakeDrive({"gfl-feed-snapshot-2026-09-02T1400Z.zip": old_blob})
    drive, sent = _wire(monkeypatch, drive=drive, readings=[_row(1), _row(2), _row(3)])
    assert gs.run() == 0 and sent == []


def test_run_changes_with_no_owner_list_fails_loudly(monkeypatch):
    old_blob, _, _ = gs.build_snapshot(CFG, "old", get=FakeFeed([_row(1), _row(2)]))
    drive = FakeDrive({"gfl-feed-snapshot-2026-09-02T1400Z.zip": old_blob})
    _, sent = _wire(monkeypatch, drive=drive, readings=[_row(2)], owners=())
    assert gs.run() == 1 and sent == []


def test_run_short_pull_uploads_nothing(monkeypatch):
    drive, sent = _wire(monkeypatch)
    feed = FakeFeed([_row(1)], server_count=2)
    monkeypatch.setattr(gs, "build_snapshot", lambda c, stamp: _BUILD(c, stamp, get=feed))
    assert gs.run() == 1 and drive.files_by_name == {} and sent == []


def test_live_config_ships_disabled():
    import config_loader
    c = config_loader.load_config()["gfl_feed_snapshot"]
    assert c["enabled"] is False
