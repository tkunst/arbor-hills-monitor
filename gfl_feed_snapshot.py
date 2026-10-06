"""
gfl_feed_snapshot.py — monthly FULL snapshot of GFL's public perimeter-air feed,
with change detection against the previous snapshot (ADR 063).

The daily Stream E capture (gfl_air_watcher, ADR 026, capture.mode "all") saves every
NEW reading as it arrives. It cannot see readings it missed (an over-cap re-baseline, a
failed upload) or the source deleting or editing PAST readings. This job closes both:

  1. Pull every row of every layer/table of the FeatureServer (readings layer 4 =
     ~226k hourly H2S/CH4 rows, all fields verbatim), plus the service/layer metadata
     and the public dashboard's config, into one zip with a manifest (source URLs,
     fetch time, our row count vs the server's own count, SHA-256 per file).
  2. Upload the zip to the app-only GFL Air Exhibit Drive folder
     (GOAUTH_GFL_AIR_FOLDER_ID), immutable and named by fetch time.
  3. Download the previous snapshot from that folder and diff the readings by
     OBJECTID: rows that existed then but are gone now (DELETED), or whose
     measurement fields changed (EDITED). Any of either emails Trisha ONLY (the
     owner list), never the public recipient lists.

Gated on `gfl_feed_snapshot.enabled` (ships false: a new job against a live
external system is switched on by a human, per docs/overnight-coder.md). A fetch
that cannot be completed, or whose row count disagrees with the server's own
count, exits 1 so the GitHub failure email surfaces it; nothing partial is
uploaded. Standalone and self-terminating, like pfas_watcher.py.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

import archive_client as ac
import email_alerts as ea
from config_loader import load_config

_FOLDER_ENV = "GOAUTH_GFL_AIR_FOLDER_ID"
_PREFIX = "gfl-feed-snapshot-"
_READINGS_LAYER_NAME = "Monitoring Data"
# Fields whose change on an existing OBJECTID counts as an EDIT of a past reading.
# (System bookkeeping such as last_edited_date is reported separately, not alerted.)
MEASUREMENT_FIELDS = ("LocName", "Date", "H2S", "CH4", "H2S_Text", "CH4_Text",
                      "Speed", "Direction", "Direction_Text", "Temp",
                      "Relative_Humidity", "Barometric_Pressure")
_UA = "arbor-hills-monitor (independent public-records archive)"


class SnapshotError(RuntimeError):
    """The snapshot could not be completed faithfully; nothing is uploaded."""


# ---------------------------------------------------------------------------
# HTTP (stdlib only)
# ---------------------------------------------------------------------------

def _get_json(url: str, params: dict | None = None, *, tries: int = 5,
              timeout: int = 180, sleep=time.sleep) -> dict:
    if params is not None:
        url = url + "?" + urllib.parse.urlencode({**params, "f": "json"})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # nosec B310 — https URL from trusted config, not user input
                data = json.loads(r.read().decode("utf-8", "ignore"))
            if isinstance(data, dict) and data.get("error"):
                raise SnapshotError(f"ArcGIS error from {url}: {data['error']}")
            return data
        except Exception as e:  # noqa: BLE001 — transient network/server errors retry
            last = e
            if i < tries - 1:
                sleep(10 * (i + 1))
    raise SnapshotError(f"GET {url} failed after {tries} tries: {last}")


def fetch_layer(service_url: str, layer_id: int, max_rc: int, get=_get_json) -> list[dict]:
    """Every row of one layer, all fields, via OBJECTID keyset paging (robust to
    server-side offset limits). Geometry (if any) is kept as JSON in `_geometry`."""
    rows: list[dict] = []
    last = -1
    while True:
        d = get(f"{service_url}/{layer_id}/query", {
            "where": f"OBJECTID > {last}", "outFields": "*",
            "orderByFields": "OBJECTID ASC", "returnGeometry": "true",
            "outSR": 4326, "resultRecordCount": max_rc})
        feats = d.get("features") or []
        if not feats:
            break
        for f in feats:
            a = dict(f.get("attributes") or {})
            if f.get("geometry"):
                a["_geometry"] = json.dumps(f["geometry"], sort_keys=True)
            rows.append(a)
        nxt = feats[-1].get("attributes", {}).get("OBJECTID")
        if nxt is None or nxt <= last:
            raise SnapshotError(f"layer {layer_id}: OBJECTID paging did not advance")
        last = nxt
        if len(feats) < max_rc and not d.get("exceededTransferLimit"):
            break
    return rows


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def rows_to_csv(rows: list[dict], fields: list[str]) -> str:
    cols = list(fields) + (["_geometry"] if any("_geometry" in r for r in rows) else [])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def diff_readings(prev: list[dict], cur: list[dict], fields=MEASUREMENT_FIELDS) -> dict:
    """Compare two snapshots of the readings layer by OBJECTID. Values are compared
    as strings, so a CSV-loaded previous snapshot compares cleanly with a fresh JSON
    pull (None and '' are the same: an empty CSV cell). Returns
    {'deleted': [prev rows gone now], 'edited': [(oid, {field: (old, new)})],
     'added': n new OBJECTIDs}. Pure."""
    def norm(v):
        if v is None or v == "":
            return ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        s = str(v)
        try:
            f = float(s)
            return str(int(f)) if f.is_integer() else repr(f)
        except ValueError:
            return s

    cur_by = {str(norm(r.get("OBJECTID"))): r for r in cur}
    prev_ids = set()
    deleted, edited = [], []
    for p in prev:
        oid = str(norm(p.get("OBJECTID")))
        prev_ids.add(oid)
        c = cur_by.get(oid)
        if c is None:
            deleted.append(p)
            continue
        changes = {f: (p.get(f), c.get(f)) for f in fields
                   if norm(p.get(f)) != norm(c.get(f))}
        if changes:
            edited.append((oid, changes))
    added = sum(1 for oid in cur_by if oid not in prev_ids)
    return {"deleted": deleted, "edited": edited, "added": added}


def previous_snapshot_name(names: list[str], current: str | None = None) -> str | None:
    """Newest snapshot zip name, excluding `current`. Names embed a sortable UTC
    stamp (gfl-feed-snapshot-YYYY-MM-DDTHHMMZ.zip), so max() is the newest."""
    cands = [n for n in names if n.startswith(_PREFIX) and n.endswith(".zip") and n != current]
    return max(cands) if cands else None


def format_change_email(diff: dict, prev_name: str, cur_name: str, *,
                        max_lines: int = 40) -> tuple[str, str]:
    nd, ne = len(diff["deleted"]), len(diff["edited"])
    subject = (f"[Arbor Hills Monitor] GFL perimeter feed: {nd} past reading(s) deleted, "
               f"{ne} edited since the last snapshot")
    lines = [
        "The monthly full snapshot of GFL's public perimeter-air feed differs from the",
        "previous snapshot for readings that already existed then.",
        "",
        f"Previous snapshot: {prev_name}",
        f"This snapshot:     {cur_name}",
        f"Deleted readings:  {nd}",
        f"Edited readings:   {ne}",
        f"New readings:      {diff['added']} (expected; not a change)",
        "",
        "Both snapshots are in the GFL Air Exhibit Drive folder, unchanged.",
        "",
    ]
    if nd:
        lines.append("DELETED (first %d):" % min(nd, max_lines))
        for r in diff["deleted"][:max_lines]:
            lines.append(f"  OBJECTID {r.get('OBJECTID')}  {r.get('LocName')}  Date={r.get('Date')}  "
                         f"H2S={r.get('H2S')}  CH4={r.get('CH4')}")
        lines.append("")
    if ne:
        lines.append("EDITED (first %d):" % min(ne, max_lines))
        for oid, ch in diff["edited"][:max_lines]:
            parts = ", ".join(f"{f}: {o!r} -> {n!r}" for f, (o, n) in sorted(ch.items()))
            lines.append(f"  OBJECTID {oid}  {parts}")
        lines.append("")
    lines.append("This email goes only to the monitor's owner list.")
    return subject, "\n".join(lines)


# ---------------------------------------------------------------------------
# Snapshot build
# ---------------------------------------------------------------------------

def build_snapshot(cfg: dict, stamp: str, get=_get_json) -> tuple[bytes, list[dict], dict]:
    """Fetch everything and return (zip bytes, readings rows, manifest). Raises
    SnapshotError if any layer's row count disagrees with the server's own count."""
    svc_url = cfg["service_url"].rstrip("/")
    files: dict[str, bytes] = {}
    svc = get(svc_url, {})
    files["service.json"] = json.dumps(svc, indent=1, sort_keys=True).encode()
    manifest = {"source": svc_url, "fetched_at": stamp, "layers": [], "files": {}}
    readings: list[dict] = []
    for lyr in (svc.get("layers") or []) + (svc.get("tables") or []):
        lid = lyr["id"]
        meta = get(f"{svc_url}/{lid}", {})
        files[f"layer{lid}-meta.json"] = json.dumps(meta, indent=1, sort_keys=True).encode()
        fields = [f["name"] for f in meta.get("fields") or []]
        max_rc = min(int(meta.get("maxRecordCount") or 2000), int(cfg.get("page_size", 5000)))
        rows = fetch_layer(svc_url, lid, max_rc, get=get)
        server = get(f"{svc_url}/{lid}/query", {"where": "1=1", "returnCountOnly": "true"}).get("count")
        if server is not None and server != len(rows):
            raise SnapshotError(f"layer {lid} ({lyr.get('name')}): fetched {len(rows)} rows, "
                                f"server reports {server}")
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(lyr.get("name", lid)))
        files[f"layer{lid}-{safe}.csv"] = rows_to_csv(rows, fields).encode()
        manifest["layers"].append({"id": lid, "name": lyr.get("name"), "rows": len(rows),
                                   "server_count": server})
        if lyr.get("name") == _READINGS_LAYER_NAME:
            readings = rows
    for name, url in (("dashboard-item.json", cfg.get("dashboard_item_url")),
                      ("dashboard-data.json", (cfg.get("dashboard_item_url") or "") + "/data")):
        if cfg.get("dashboard_item_url"):
            try:
                files[name] = json.dumps(get(url, {}), indent=1, sort_keys=True).encode()
            except SnapshotError as e:   # the dashboard config is context, not the record
                manifest.setdefault("warnings", []).append(f"{name}: {e}")
    if not readings:
        raise SnapshotError(f"no '{_READINGS_LAYER_NAME}' rows fetched")
    manifest["files"] = {n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in sorted(files.items()):
            z.writestr(n, b)
        z.writestr("manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
    return buf.getvalue(), readings, manifest


def readings_from_zip(blob: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next((n for n in z.namelist()
                     if n.startswith("layer") and n.endswith(".csv")
                     and _READINGS_LAYER_NAME.replace(" ", "_") in n), None)
        if name is None:
            raise SnapshotError("previous snapshot has no readings CSV")
        return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8"))))


# ---------------------------------------------------------------------------
# Drive I/O (app-only folder, drive.file scope: lists/reads only this app's files)
# ---------------------------------------------------------------------------

def _list_snapshot_names(drive, folder_id: str) -> dict[str, str]:
    out, token = {}, None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and name contains '{_PREFIX}' and trashed = false",
            fields="nextPageToken, files(id, name)", pageSize=100,
            pageToken=token).execute(num_retries=ac.GOOGLE_API_NUM_RETRIES)
        for f in resp.get("files", []):
            out[f["name"]] = f["id"]
        token = resp.get("nextPageToken")
        if not token:
            return out


def _download(drive, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = dl.next_chunk(num_retries=ac.GOOGLE_API_NUM_RETRIES)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> int:
    cfg = load_config()
    snap_cfg = cfg.get("gfl_feed_snapshot") or {}
    if not snap_cfg.get("enabled"):
        print("[gfl-snapshot] disabled (gfl_feed_snapshot.enabled is false) — nothing to do.")
        return 0
    if not ac.is_configured(_FOLDER_ENV):
        print(f"[gfl-snapshot] Drive creds/folder ({_FOLDER_ENV}) not configured.")
        return 1
    gcfg = {**(cfg.get("gfl_air") or {}), **snap_cfg}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    cur_name = f"{_PREFIX}{stamp}.zip"
    try:
        blob, readings, manifest = build_snapshot(gcfg, stamp)
    except SnapshotError as e:
        print(f"[gfl-snapshot] FAILED, nothing uploaded: {e}")
        return 1
    for lyr in manifest["layers"]:
        print(f"[gfl-snapshot]   layer {lyr['id']} {lyr['name']}: {lyr['rows']} rows "
              f"(server {lyr['server_count']})")

    drive = ac.oauth_drive_service()
    folder = ac.folder_id(_FOLDER_ENV)
    existing = _list_snapshot_names(drive, folder)
    prev_name = previous_snapshot_name(list(existing), current=cur_name)

    tmp = None
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=".zip", delete=False) as fh:
            fh.write(blob)
            tmp = fh.name
        ac.upload_file(drive, tmp, cur_name, "application/zip", folder)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    print(f"[gfl-snapshot] uploaded {cur_name} ({len(blob):,} bytes).")

    if not prev_name:
        print("[gfl-snapshot] first snapshot in the folder — baseline, nothing to compare.")
        return 0
    try:
        prev_rows = readings_from_zip(_download(drive, existing[prev_name]))
    except Exception as e:  # noqa: BLE001
        print(f"[gfl-snapshot] could not read previous snapshot {prev_name}: {e}")
        return 1
    diff = diff_readings(prev_rows, readings)
    print(f"[gfl-snapshot] vs {prev_name}: {len(diff['deleted'])} deleted, "
          f"{len(diff['edited'])} edited, {diff['added']} new.")
    if diff["deleted"] or diff["edited"]:
        owners = sorted(ea.load_owner_emails(cfg))
        if not owners:
            print("[gfl-snapshot] CHANGES FOUND but the owner list is empty — failing loudly.")
            return 1
        subject, body = format_change_email(diff, prev_name, cur_name)
        if not ea.send_email(subject, body, cfg, recipients=owners):
            print("[gfl-snapshot] change email FAILED to send — failing loudly.")
            return 1
        print(f"[gfl-snapshot] change email sent to the owner list ({len(owners)}).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
