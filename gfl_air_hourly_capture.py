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
`gfl-air-capture-*.json` names in the app-only GFL Air Exhibit folder (the daily run
writes the same kind of file, same naming), so the two jobs never race over shared
state and a failed hour is simply picked up by the next one. Files reuse
`gfl_air_watcher._capture_row`/`_write_capture`, so each row has the same shape
(numeric values, the source's labels, and a verbatim `raw` copy).

Failure policy: nothing is lost by a failed hour. The source keeps its history, the
next hour retries from the same Drive-derived cursor, and the daily gfl-air run (its
own Sheet cursor) still captures every reading since its last poll and exits 1 on any
capture gap. So a failed hour logs and exits 0, except in one fixed UTC hour a day
(LOUD_HOUR_UTC), when it exits 1: a persistent failure surfaces as at most one GitHub
failure email a day instead of 24. A missing Drive configuration always exits 1.
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
# everything since its own Sheet cursor regardless), EXCEPT the run in this UTC hour,
# which exits 1 so a persistent failure surfaces as at most one GitHub email a day.
LOUD_HOUR_UTC = 15

_CAPTURE_RE = re.compile(r"^gfl-air-capture-\d{4}-\d{2}-\d{2}-oid(\d+)\.json$")


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
        cursor = find_cursor(drive, folder, now)
        if cursor is None:
            # Nothing captured yet anywhere: start from the source's current newest
            # rows (history is covered by the monthly full snapshot, ADR 063).
            base = gc.fetch_baseline(cfg_gfl, station_prefix=prefix)
            cursor = max((gc.oid_of(r) or 0) for r in base) if base else 0
            print(f"[gfl-hourly] no capture files yet; starting at OBJECTID {cursor}.")
        readings = gc.fetch_readings(cfg_gfl, cursor, limit=per_run)
        if len(readings) > per_run:
            readings = readings[:per_run]      # catch up over several runs, oldest first
        if not readings:
            print(f"[gfl-hourly] no new readings past OBJECTID {cursor}.")
            return 0
        rows = gw.select_capture_rows(readings, {}, None, None, 1, prefix, mode="all")
        when = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        n = gw._write_capture(cfg_gfl, rows, when)
        print(f"[gfl-hourly] captured {n} reading(s) past OBJECTID {cursor} -> Drive.")
        return 0
    except Exception as e:  # noqa: BLE001 — see the failure policy in the docstring
        print(f"[gfl-hourly] capture attempt failed: {type(e).__name__}: {e}")
        if now.hour == LOUD_HOUR_UTC:
            print("[gfl-hourly] failing loudly (the once-a-day report hour).")
            return 1
        print("[gfl-hourly] the next run retries; the daily gfl-air run still captures "
              "everything since its own cursor — exiting 0.")
        return 0


if __name__ == "__main__":
    sys.exit(run())
