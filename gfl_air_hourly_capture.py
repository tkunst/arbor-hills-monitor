"""
gfl_air_hourly_capture.py — hourly durable capture of GFL perimeter readings (ADR 026
addendum 2026-10-06, "hourly").

The daily Stream E run (gfl_air_watcher, 8am ET) captures every reading since its
last poll, so a reading can sit in GFL's feed for up to ~24 hours before we hold a
copy. If the source changed or removed a reading inside that window, we would never
have seen the original. This job shrinks the window to about an hour (GitHub's cron
often runs late, so in practice 1-2 hours).

It is deliberately separate and minimal: no alerts, no Sheet, no cursor in the case
file. Its cursor is DERIVED FROM DRIVE: the highest `oid<N>` in the existing
`gfl-air-capture-*.json` names in the app-only GFL Air Exhibit folder (the daily run's
files and this job's `-h` files both count), so the two jobs never race over shared
state and a failed hour is simply picked up by the next one. Files reuse
`gfl_air_watcher._capture_row`/`_write_capture`, so each row has the same shape
(numeric values, the source's labels, and a verbatim `raw` copy).

Failure policy: nothing is lost by a failed hour. The source keeps its history, the
next hour retries from the same Drive-derived cursor, and the daily gfl-air run (its
own Sheet cursor) still captures every reading since its last poll and exits 1 on any
capture gap. So a failed hour logs and exits 0, except runs starting in four fixed UTC
hours a day (LOUD_HOURS_UTC), which exit 1: a persistent failure surfaces as a few GitHub
failure emails a day instead of 24, and a late or dropped scheduled run cannot hide it
for a whole day. Source OBJECTIDs going backwards, or new rows that are not perimeter
readings, count as failures. A missing Drive configuration always exits 1.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

import archive_client as ac
import gfl_air_client as gc
import gfl_air_watcher as gw
from config_loader import load_config

# A failed hour exits 0 (the next hour retries, and the daily gfl-air run captures
# everything since its own Sheet cursor regardless), EXCEPT runs that start in these
# UTC hours, which exit 1: a persistent failure surfaces as a few GitHub emails a day,
# and four spread-out windows survive GitHub starting or dropping a scheduled run late.
LOUD_HOURS_UTC = (3, 9, 15, 21)
HOURLY_SUFFIX = "-h"

# Both the daily run's files and this job's ("-h") files count toward the cursor.
_CAPTURE_RE = re.compile(r"^gfl-air-capture-\d{4}-\d{2}-\d{2}-oid(\d+)(?:-h)?\.json$")


def max_captured_oid(names) -> int | None:
    """Highest OBJECTID already captured, from capture file names. Pure."""
    oids = [int(m.group(1)) for n in names if (m := _CAPTURE_RE.match(n))]
    return max(oids) if oids else None


def _recent_capture_names(drive, folder_id: str, now: datetime, days: int) -> list[str]:
    """Names of capture files created in the last `days` days (bounded listing;
    the prefix filter is applied in Python)."""
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    out, token = [], None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false and createdTime > '{since}'",
            fields="nextPageToken, files(name)", pageSize=1000,
            pageToken=token).execute(num_retries=ac.GOOGLE_API_NUM_RETRIES)
        out += [f["name"] for f in resp.get("files", [])]
        token = resp.get("nextPageToken")
        if not token:
            return out


def find_cursor(drive, folder_id: str, now: datetime, lookback_days=(3, 30, 3650)) -> int | None:
    """The capture cursor: max captured OBJECTID, looking back 3 days first and
    widening only if nothing is found (e.g. after a long outage)."""
    for days in lookback_days:
        oid = max_captured_oid(_recent_capture_names(drive, folder_id, now, days))
        if oid is not None:
            return oid
    return None


def run(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cfg = load_config()
    cfg_gfl = cfg.get("gfl_air") or {}
    hc = cfg_gfl.get("hourly_capture") or {}
    if not hc.get("enabled"):
        print("[gfl-hourly] disabled (gfl_air.hourly_capture.enabled is false) — nothing to do.")
        return 0
    if not (cfg_gfl.get("capture") or {}).get("enabled"):
        print("[gfl-hourly] gfl_air.capture.enabled is false — capture is switched off; nothing to do.")
        return 0
    if not ac.is_configured(gw._CAPTURE_FOLDER_ENV):
        print(f"[gfl-hourly] Drive creds/folder ({gw._CAPTURE_FOLDER_ENV}) not configured.")
        return 1
    prefix = cfg_gfl.get("station_prefix", gc.DEFAULT_STATION_PREFIX)
    per_run = int(hc.get("max_readings_per_run", 5000))

    try:
        drive = ac.oauth_drive_service()
        folder = ac.folder_id(gw._CAPTURE_FOLDER_ENV)
        when = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = find_cursor(drive, folder, now)
        if cursor is None:
            # Nothing captured yet that this app can see (new folder / rotated OAuth
            # client): save the source's newest rows NOW so a cursor exists from here
            # on (history is covered by the monthly full snapshot, ADR 063).
            base = gc.fetch_baseline(cfg_gfl, station_prefix=prefix)
            rows = gw.select_capture_rows(base, {}, None, None, 1, prefix, mode="all")
            if not rows:
                raise RuntimeError("no capture files and no baseline rows to start from")
            n = gw._write_capture(cfg_gfl, rows, when, HOURLY_SUFFIX)
            print(f"[gfl-hourly] no capture files yet; saved {n} baseline reading(s) to start the cursor.")
            return 0
        readings = gc.fetch_readings(cfg_gfl, cursor, limit=per_run)
        if len(readings) > per_run:
            readings = readings[:per_run]      # catch up over several runs, oldest first
        if not readings:
            base = gc.fetch_baseline(cfg_gfl, station_prefix=prefix)
            top = max((gc.oid_of(r) or 0) for r in base) if base else None
            if top is not None and top < cursor:
                raise RuntimeError(f"source OBJECTIDs went BACKWARDS (newest {top} < captured "
                                   f"{cursor}): table reset? new readings would be missed")
            print(f"[gfl-hourly] no new readings past OBJECTID {cursor}.")
            return 0
        rows = gw.select_capture_rows(readings, {}, None, None, 1, prefix, mode="all")
        if not rows:
            raise RuntimeError(f"{len(readings)} new row(s) past OBJECTID {cursor} but none "
                               "were perimeter readings; the cursor cannot advance")
        n = gw._write_capture(cfg_gfl, rows, when, HOURLY_SUFFIX)
        print(f"[gfl-hourly] captured {n} reading(s) past OBJECTID {cursor} -> Drive.")
        return 0
    except Exception as e:  # noqa: BLE001 — see the failure policy in the docstring
        print(f"[gfl-hourly] capture attempt failed: {type(e).__name__}: {e}")
        if now.hour in LOUD_HOURS_UTC:
            print("[gfl-hourly] failing loudly (a report hour).")
            return 1
        print("[gfl-hourly] the next run retries; the daily gfl-air run still captures "
              "everything since its own cursor — exiting 0.")
        return 0


if __name__ == "__main__":
    sys.exit(run())
