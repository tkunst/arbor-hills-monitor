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
     (GOAUTH_GFL_AIR_FOLDER_ID), immutable and named by fetch time. The data is saved
     FIRST, whatever happens to the comparison.
  3. Compare the readings with the newest earlier snapshot that was itself fully
     compared (it has a `.compared` marker file). Readings that existed then but are
     gone now (DELETED, after matching renumbered rows on station + time), or whose
     measurement fields changed (EDITED), or a change in the feed's field set, email
     Trisha ONLY (the owner list). The new snapshot gets its `.compared` marker only
     after that succeeds, so a failed comparison or email is retried against the same
     baseline next run instead of being hidden.

Gated on `gfl_feed_snapshot.enabled` (ships false: a new job against a live
external system is switched on by a human, per docs/overnight-coder.md). Every
failure exits 1 so the GitHub failure email surfaces it; a pull whose row count
disagrees with the server's own count is never uploaded. Standalone and
self-terminating, like pfas_watcher.py.
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
_MARKER = ".compared"
# Fields whose change on an existing reading counts as an EDIT of a past reading.
# (System bookkeeping such as last_edited_date is not compared.)
MEASUREMENT_FIELDS = ("LocName", "Date", "H2S", "CH4", "H2S_Text", "CH4_Text",
                      "Speed", "Direction", "Direction_Text", "Temp",
                      "Relative_Humidity", "Barometric_Pressure")
_UA = "arbor-hills-monitor (independent public-records archive)"


class SnapshotError(RuntimeError):
    """The snapshot could not be completed faithfully."""


# ---------------------------------------------------------------------------
# HTTP (stdlib only)
# ---------------------------------------------------------------------------

def _get_json(url: str, params: dict | None = None, *, tries: int = 5,
              timeout: int = 180, sleep=time.sleep) -> dict:
    """GET JSON. Network/parse errors and ArcGIS server-side errors (code >= 500, which
    the server returns under load) retry with backoff; any other ArcGIS `error` body
    (e.g. 400 invalid query) is deterministic and raises at once."""
    if params is not None:
        url = url + "?" + urllib.parse.urlencode({**params, "f": "json"})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # nosec B310 — https URL from trusted config, not user input
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — transient network/server errors retry
            last = e
            if i < tries - 1:
                sleep(10 * (i + 1))
            continue
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            code = err.get("code") if isinstance(err, dict) else None
            if isinstance(code, int) and code >= 500 and i < tries - 1:
                last = SnapshotError(f"ArcGIS {code}: {err}")    # server under load: retry
                sleep(10 * (i + 1))
                continue
            raise SnapshotError(f"ArcGIS error from {url}: {err}")
        return data
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

def csv_columns(rows: list[dict], meta_fields: list[str]) -> list[str]:
    """Metadata field order first, then any attribute actually returned that the
    metadata didn't list (nothing returned is ever dropped). OBJECTID is required."""
    cols = list(meta_fields)
    seen = set(cols)
    for r in rows:
        for k in r:
            if k not in seen:
                cols.append(k)
                seen.add(k)
    if rows and "OBJECTID" not in seen:
        raise SnapshotError("rows have no OBJECTID column")
    return cols


def rows_to_csv(rows: list[dict], meta_fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=csv_columns(rows, meta_fields), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _norm(v) -> str:
    """Compare-form of a value: None and '' are the same (an empty CSV cell), and a
    number compares by value whether it came from JSON (3, 3.0) or CSV ('3', '3.0')."""
    if v is None or v == "":
        return ""
    s = str(v)
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f.is_integer() else repr(f)


def _present_fields(rows: list[dict], fields) -> set:
    """Fields that carry a key in at least one row (a field GFL dropped is absent)."""
    return {f for f in fields if any(f in r for r in rows)}


def diff_readings(prev: list[dict], cur: list[dict], fields=MEASUREMENT_FIELDS) -> dict:
    """Compare two snapshots of the readings layer. Rows match by OBJECTID; a previous
    row whose OBJECTID is gone is then matched on the natural key (station + time), so
    a source-side reinsert that renumbers OBJECTIDs reads as RENUMBERED, not deleted.
    Only fields present in BOTH snapshots are compared; a change in the field set is
    reported once as `schema`, not as an edit on every row. Pure. Returns
    {'deleted': [prev rows], 'edited': [(oid, {field: (old, new)})], 'renumbered': n,
     'added': n, 'schema': {'dropped': [...], 'added': [...]}}."""
    pf, cf = _present_fields(prev, fields), _present_fields(cur, fields)
    compare = [f for f in fields if f in pf and f in cf]
    schema = {"dropped": sorted(pf - cf), "added": sorted(cf - pf)}

    def key(r):
        return (_norm(r.get("LocName")), _norm(r.get("Date")))

    cur_by_oid = {_norm(r.get("OBJECTID")): r for r in cur}
    prev_key_by_oid = {_norm(r.get("OBJECTID")): key(r) for r in prev}
    used: set = set()
    matched: dict[int, dict] = {}        # index in prev -> matching current row
    # Pass 1: same OBJECTID and same station + time (the normal case).
    for i, p in enumerate(prev):
        c = cur_by_oid.get(_norm(p.get("OBJECTID")))
        if c is not None and key(c) == key(p):
            matched[i] = c
            used.add(id(c))
    # Pass 2: a previous row not matched above takes an UNUSED current row with the
    # same station + time whose OBJECTID is either new or was held by a different
    # reading before (renumbered / reused), so one current row is never counted twice
    # and a deletion among duplicate-time rows is still a deletion. Blank station/time
    # never matches this way.
    cur_by_key: dict[tuple, list] = {}
    for r in cur:
        oid = _norm(r.get("OBJECTID"))
        if (id(r) not in used and all(key(r))
                and (oid not in prev_key_by_oid or prev_key_by_oid[oid] != key(r))):
            cur_by_key.setdefault(key(r), []).append(r)
    renumbered = 0
    for i, p in enumerate(prev):
        if i in matched or not all(key(p)):
            continue
        cands = [r for r in cur_by_key.get(key(p), []) if id(r) not in used]
        if cands:
            matched[i] = cands[0]
            used.add(id(cands[0]))
            renumbered += 1
    # Pass 3: same OBJECTID but a different station/time and no renumbered copy:
    # the reading itself was edited (its station or time changed).
    for i, p in enumerate(prev):
        if i in matched:
            continue
        c = cur_by_oid.get(_norm(p.get("OBJECTID")))
        if c is not None and id(c) not in used:
            matched[i] = c
            used.add(id(c))

    deleted, edited = [], []
    for i, p in enumerate(prev):
        c = matched.get(i)
        if c is None:
            deleted.append(p)
            continue
        changes = {f: (p.get(f), c.get(f)) for f in compare if _norm(p.get(f)) != _norm(c.get(f))}
        if changes:
            edited.append((_norm(p.get("OBJECTID")), changes))
    added = sum(1 for r in cur if id(r) not in used)
    return {"deleted": deleted, "edited": edited, "renumbered": renumbered,
            "added": added, "schema": schema}


RENUMBER_ALERT_MIN = 100   # a mass renumbering (source-side reinsert) is worth knowing


def has_alertable_change(diff: dict) -> bool:
    return bool(diff["deleted"] or diff["edited"]
                or diff["schema"]["dropped"] or diff["schema"]["added"]
                or diff["renumbered"] >= RENUMBER_ALERT_MIN)


def snapshot_names(names) -> list[str]:
    return sorted(n for n in names if n.startswith(_PREFIX) and n.endswith(".zip"))


def baseline_candidates(names, current: str | None = None) -> list[str]:
    """Baselines to try, best first: the compared snapshots newest-first, or (if none
    was ever compared) every earlier snapshot oldest-first, so an unreadable oldest one
    falls through to the next instead of failing every run."""
    names = set(names)
    snaps = [n for n in snapshot_names(names) if n != current]
    marked = sorted((n for n in snaps if n + _MARKER in names), reverse=True)
    return marked or sorted(snaps)


def baseline_snapshot_name(names, current: str | None = None) -> str | None:
    """The preferred baseline (first of baseline_candidates), or None."""
    cands = baseline_candidates(names, current)
    return cands[0] if cands else None


def format_change_email(diff: dict, base_name: str, cur_name: str, *,
                        max_lines: int = 40) -> tuple[str, str]:
    nd, ne = len(diff["deleted"]), len(diff["edited"])
    sch = diff["schema"]
    parts = []
    if nd:
        parts.append(f"{nd} past reading(s) deleted")
    if ne:
        parts.append(f"{ne} edited")
    if sch["dropped"] or sch["added"]:
        parts.append("field list changed")
    if diff["renumbered"] >= RENUMBER_ALERT_MIN:
        parts.append(f"{diff['renumbered']} renumbered (not deleted)")
    if not parts:
        parts.append("no reading changes, but a baseline snapshot was unreadable")
    subject = ("[Arbor Hills Monitor] GFL perimeter feed: " + ", ".join(parts)
               + " since the last snapshot")
    lines = [
        "The monthly full snapshot of GFL's public perimeter-air feed differs from the",
        "previous snapshot for readings that already existed then.",
        "",
        f"Previous snapshot: {base_name}",
        f"This snapshot:     {cur_name}",
        f"Deleted readings:  {nd}",
        f"Edited readings:   {ne}",
        f"Renumbered:        {diff['renumbered']} (same station + time, new OBJECTID; values compared, not counted as deleted)",
        f"New readings:      {diff['added']} (expected; not a change)",
        "",
        "Both snapshots are in the GFL Air Exhibit Drive folder, unchanged.",
        "",
    ]
    if diff.get("skipped_baselines"):
        lines += ["NOTE: newer baseline snapshot(s) could not be read and were skipped: "
                  + ", ".join(diff["skipped_baselines"]), ""]
    if sch["dropped"] or sch["added"]:
        lines += ["FIELD LIST CHANGED (these fields were not compared):",
                  f"  no longer in the feed: {', '.join(sch['dropped']) or 'none'}",
                  f"  new in the feed:       {', '.join(sch['added']) or 'none'}", ""]
    if nd:
        lines.append("DELETED (first %d):" % min(nd, max_lines))
        for r in diff["deleted"][:max_lines]:
            lines.append(f"  OBJECTID {r.get('OBJECTID')}  {r.get('LocName')}  Date={r.get('Date')}  "
                         f"H2S={r.get('H2S')}  CH4={r.get('CH4')}")
        lines.append("")
    if ne:
        lines.append("EDITED (first %d):" % min(ne, max_lines))
        for oid, ch in diff["edited"][:max_lines]:
            changes = ", ".join(f"{f}: {o!r} -> {n!r}" for f, (o, n) in sorted(ch.items()))
            lines.append(f"  OBJECTID {oid}  {changes}")
        lines.append("")
    lines.append("This email goes only to the monitor's owner list.")
    return subject, "\n".join(lines)


# ---------------------------------------------------------------------------
# Snapshot build
# ---------------------------------------------------------------------------

def _csv_name(lid, name) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(name if name is not None else lid))
    return f"layer{lid}-{safe}.csv"


def build_snapshot(cfg: dict, stamp: str, get=_get_json) -> tuple[bytes, list[dict], dict]:
    """Fetch everything and return (zip bytes, readings rows, manifest). Raises
    SnapshotError if any layer's row count disagrees with the server's own count of
    rows up to the last OBJECTID we fetched (so a row arriving mid-pull is not a
    mismatch), or if the readings layer is missing or empty."""
    svc_url = cfg["service_url"].rstrip("/")
    readings_id = int(cfg.get("readings_layer", 4))
    files: dict[str, bytes] = {}
    svc = get(svc_url, {})
    files["service.json"] = json.dumps(svc, indent=1, sort_keys=True).encode()
    manifest = {"source": svc_url, "fetched_at": stamp, "readings_layer": readings_id,
                "layers": [], "files": {}, "warnings": []}
    readings: list[dict] | None = None
    for lyr in (svc.get("layers") or []) + (svc.get("tables") or []):
        lid = lyr["id"]
        meta = get(f"{svc_url}/{lid}", {})
        files[f"layer{lid}-meta.json"] = json.dumps(meta, indent=1, sort_keys=True).encode()
        fields = [f["name"] for f in meta.get("fields") or []]
        max_rc = min(int(meta.get("maxRecordCount") or 2000), int(cfg.get("page_size", 5000)))
        rows = fetch_layer(svc_url, lid, max_rc, get=get)
        last_oid = rows[-1].get("OBJECTID") if rows else None
        where = f"OBJECTID <= {last_oid}" if last_oid is not None else "1=1"
        server = get(f"{svc_url}/{lid}/query", {"where": where, "returnCountOnly": "true"}).get("count")
        if server is None:
            manifest["warnings"].append(f"layer {lid}: server returned no count; not checked")
        elif server != len(rows):
            raise SnapshotError(f"layer {lid} ({lyr.get('name')}): fetched {len(rows)} rows, "
                                f"server reports {server}")
        files[_csv_name(lid, lyr.get("name"))] = rows_to_csv(rows, fields).encode("utf-8")
        manifest["layers"].append({"id": lid, "name": lyr.get("name"), "csv": _csv_name(lid, lyr.get("name")),
                                   "rows": len(rows), "server_count": server})
        if lid == readings_id:
            readings = rows
    if not readings:
        raise SnapshotError(f"readings layer {readings_id} missing or empty")
    item = cfg.get("dashboard_item_url")
    if item:
        for name, url in (("dashboard-item.json", item), ("dashboard-data.json", item + "/data")):
            try:
                files[name] = json.dumps(get(url, {}), indent=1, sort_keys=True).encode()
            except SnapshotError as e:   # the dashboard config is context, not the record
                manifest["warnings"].append(f"{name}: {e}")
    manifest["files"] = {n: hashlib.sha256(b).hexdigest() for n, b in sorted(files.items())}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in sorted(files.items()):
            z.writestr(n, b)
        z.writestr("manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
    return buf.getvalue(), readings, manifest


def readings_from_zip(blob: bytes) -> list[dict]:
    """The readings rows of a snapshot zip, located via its manifest."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        m = json.loads(z.read("manifest.json"))
        rid = m.get("readings_layer", 4)
        csv_name = next((lyr.get("csv") for lyr in m.get("layers", []) if lyr.get("id") == rid), None)
        if not csv_name or csv_name not in z.namelist():
            raise SnapshotError("previous snapshot has no readings CSV")
        return list(csv.DictReader(io.StringIO(z.read(csv_name).decode("utf-8"))))


# ---------------------------------------------------------------------------
# Drive I/O (app-only folder, drive.file scope: lists/reads only this app's files)
# ---------------------------------------------------------------------------

def _list_folder(drive, folder_id: str) -> dict[str, str]:
    """{name: id} of every (non-trashed) file this app can see in the folder. The
    prefix filter is applied in Python, not in the Drive query."""
    out, token = {}, None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name)", pageSize=1000,
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


def _upload_bytes(drive, data: bytes, name: str, mimetype: str, folder: str) -> None:
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
            fh.write(data)
            tmp = fh.name
        ac.upload_file(drive, tmp, name, mimetype, folder)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


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
    for w in manifest["warnings"]:
        print(f"[gfl-snapshot]   WARNING: {w}")

    drive = ac.oauth_drive_service()
    folder = ac.folder_id(_FOLDER_ENV)
    existing = _list_folder(drive, folder)

    # 1. Save the data first, whatever happens to the comparison.
    if cur_name in existing:
        print(f"[gfl-snapshot] {cur_name} already exists (a re-run in the same minute) — "
              "not re-uploaded or re-compared.")
        return 0
    _upload_bytes(drive, blob, cur_name, "application/zip", folder)
    print(f"[gfl-snapshot] uploaded {cur_name} ({len(blob):,} bytes).")

    # 2. Compare against the last fully compared snapshot; mark this one compared only
    #    once that succeeds, so a failure is retried next run rather than hidden.
    cands = baseline_candidates(existing, current=cur_name)
    base, prev_rows, skipped = None, None, []
    for name in cands:
        try:
            prev_rows = readings_from_zip(_download(drive, existing[name]))
            base = name
            break
        except Exception as e:  # noqa: BLE001 — try the next-older compared snapshot
            skipped.append(name)
            print(f"[gfl-snapshot] WARNING: could not read baseline {name}: {e}")
    if cands and base is None:
        print("[gfl-snapshot] no readable baseline snapshot — failing loudly.")
        return 1
    if base is None:
        print("[gfl-snapshot] first snapshot in the folder — baseline, nothing to compare.")
    else:
        diff = diff_readings(prev_rows, readings)
        diff["skipped_baselines"] = skipped
        print(f"[gfl-snapshot] vs {base}: {len(diff['deleted'])} deleted, "
              f"{len(diff['edited'])} edited, {diff['renumbered']} renumbered, "
              f"{diff['added']} new, field changes {diff['schema']}.")
        if has_alertable_change(diff) or skipped:
            owners = sorted(ea.load_owner_emails(cfg))
            if not owners:
                print("[gfl-snapshot] CHANGES FOUND but the owner list is empty — failing loudly.")
                return 1
            subject, body = format_change_email(diff, base, cur_name)
            try:
                ok = ea.send_email(subject, body, cfg, recipients=owners)
            except Exception as e:  # noqa: BLE001
                print(f"[gfl-snapshot] change email raised: {e}")
                ok = False
            if not ok:
                print("[gfl-snapshot] change email FAILED to send — failing loudly; "
                      "next run compares against the same baseline.")
                return 1
            print(f"[gfl-snapshot] change email sent to the owner list ({len(owners)}).")
    _upload_bytes(drive, json.dumps({"compared_at": stamp, "baseline": base}).encode(),
                  cur_name + _MARKER, "application/json", folder)
    return 0


if __name__ == "__main__":
    sys.exit(run())
