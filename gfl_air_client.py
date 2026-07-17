"""
gfl_air_client.py — Stream E: GFL's public perimeter air-monitoring feed for the
Arbor Hills Landfill, read from Barr Engineering's ArcGIS FeatureServer.

This is the FIRST source that gives real fenceline READINGS (R3 odor / R4 air),
not documents: GFL self-reports hourly H2S (ppb) and CH4 (ppm) at six perimeter
stations (MS-1..MS-6), plus meteorology, on a public ArcGIS dashboard backed by a
keyless FeatureServer. See docs/decisions/014-gfl-perimeter-air-stream-e.md.

This module ONLY fetches + maps to the existing ADR-004 Measurement shape. Diff /
cursor / classification / alerting is gfl_air_watcher.py (mirrors the
wds_client/wds_watcher split). It is a STRUCTURED-API source, so it never touches
egle_doc_parser.py — the Decode document base stays domain-agnostic. All
ArcGIS/GFL specifics live here.

Key facts established by the feasibility spike (ADR 014), which shape this code:

  - Incremental cursor is OBJECTID, not a timestamp. The readings table is
    ~214k rows; a full pull is a non-starter, and a raw-epoch `Date >` filter is
    rejected by the server (it wants `Date > TIMESTAMP '...'`). OBJECTID is a
    server-assigned auto-increment: `OBJECTID > cursor` is monotonic with
    insertion, so it is skip-proof and dedup-free — no timezone skew, no boundary
    overlap, no de-dup pass. The newest rows always carry the highest OBJECTIDs
    (verified live).

  - In-place mutation does NOT touch the numerics. A freshly-inserted row is
    "preliminary" (its H2S_Text/CH4_Text are blank and Temp is briefly in Celsius),
    then finalized in place (~Text backfilled, Temp converted to Fahrenheit). The
    NUMERIC H2S/CH4 — the only alerting signal — is stable at insert (verified by
    re-querying the same OBJECTIDs). So walking past a row once (OBJECTID cursor)
    never misses an H2S/CH4 correction. Residual: if GFL ever starts correcting
    the numerics post-insert, the cursor would miss it — documented, low, and the
    stream ships disabled with Trisha reviewing (ADR 014).

  - Sentinels 999 (H2S) / 99999 (CH4) are ambiguous no-data vs off-scale-high.
    They are EXCLUDED from the measured series (recording 999 ppb as a real value
    would both corrupt the series and false-fire an exceedance) but SURFACED, not
    silently dropped: the watcher records them as anomalies. Fail-safe posture —
    don't infer "benign" when the external semantics are unknowable (ADR 014).

A failed fetch raises GflAirFetchError (never a silent []), so the watcher can
tell "no new readings" from "couldn't read it" — a short/failed fetch must never
be diffed into a false 'zero readings' or a re-baseline.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

_UA = "arbor-hills-monitor/gfl-air (+https://github.com/tkunst/arbor-hills-monitor)"

# The perimeter GAS stations are MS-1..MS-6. Layer 0 (current-per-station) also
# carries a stale "10-Meter MET Tower" row with mismapped text fields; the
# station_prefix filter keeps it (or any future non-gas station) out of readings.
# The readings table (layer 4) only ever contains MS-* rows, but the filter is
# belt-and-suspenders.
DEFAULT_STATION_PREFIX = "MS-"

# Sentinel / no-data values in the feed (see module docstring + ADR 014).
DEFAULT_SENTINEL_H2S = 999.0
DEFAULT_SENTINEL_CH4 = 99999.0

# ADR-004 Measurement metric names. CH4 maps to the existing doc-classifier
# "methane" metric; H2S has no metric in that Literal enum yet, so it takes an
# honest new name — NOT "other", which would erase the pollutant identity (the
# exact "dumping into other" the handoff warns against). Stored as text in the
# Measurements tab (no schema break); the metric-taxonomy roadmap will formalize
# it. See ADR 014.
METRIC_H2S = "hydrogen_sulfide"
METRIC_CH4 = "methane"

# Attribution the adversarial-review section requires be visible in the row (not
# just the ADR): every reading is GFL's OWN self-report, not an EGLE measurement.
SELF_REPORTED = "GFL self-reported perimeter air monitoring"

# The fields the readings table actually carries (spike-verified). Kept as one
# constant so fetch_readings and fetch_baseline request an identical projection.
_READING_FIELDS = "OBJECTID,LocName,Date,H2S,CH4,H2S_Text,CH4_Text,Speed,Direction,Temp"

# Pollutant spec: (result-key, ArcGIS field, unit, threshold/sentinel config key,
# metric name). One place both the mapping and the classifier read from.
# Marker substring present in every sentinel reason string (classify_reading), used to
# filter sentinel detail out of a real exceedance/watch line when alert_on_sentinel is off.
SENTINEL_REASON = "sentinel/no-data value"

_POLLUTANTS = (
    ("h2s", "H2S", "ppb", "h2s_ppb", METRIC_H2S),
    ("ch4", "CH4", "ppm", "ch4_ppm", METRIC_CH4),
)


class GflAirFetchError(RuntimeError):
    """A GFL/ArcGIS query could not be fetched, or the server returned an error
    payload. Treated as TRANSIENT by the watcher (skip-and-warn once a baseline
    exists), never as 'no readings' — mirrors wds_client.WDSFetchError."""


# ---------------------------------------------------------------------------
# HTTP (stdlib only) — GET the FeatureServer query endpoint
# ---------------------------------------------------------------------------

def _query(service_url: str, layer, params: dict, *, timeout: int = 60) -> dict:
    """GET `.../FeatureServer/<layer>/query?...` and return parsed JSON. Raises
    GflAirFetchError on a network/HTTP failure OR an ArcGIS `{"error": ...}` body
    (the server answers HTTP 200 with an error object for a bad query — e.g. a
    raw-epoch date filter — so a 200 is not enough to trust)."""
    if not service_url:
        raise GflAirFetchError("gfl_air.service_url is not configured")
    q = dict(params)
    q.setdefault("f", "json")
    url = f"{service_url.rstrip('/')}/{layer}/query?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — https GET
            raw = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001 — network / HTTP → transient
        raise GflAirFetchError(f"GET {url} failed: {e}") from e
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise GflAirFetchError(f"non-JSON response from {url}: {raw[:200]!r}") from e
    if isinstance(data, dict) and data.get("error"):
        raise GflAirFetchError(f"ArcGIS error from {url}: {data['error']}")
    return data


def _features(data: dict) -> list[dict]:
    return [f.get("attributes", {}) for f in (data.get("features") or [])]


def _svc(cfg_gfl: dict) -> dict:
    return {
        "service_url": cfg_gfl.get("service_url", ""),
        "readings_layer": cfg_gfl.get("readings_layer", 4),
        "max_record_count": int(cfg_gfl.get("max_record_count", 2000)),
    }


# ---------------------------------------------------------------------------
# Fetch API
# ---------------------------------------------------------------------------

def fetch_readings(cfg_gfl: dict, since_oid: int, *, limit: Optional[int] = None) -> list[dict]:
    """Raw reading rows with OBJECTID > since_oid, OLDEST-first (OBJECTID ASC —
    the monotone, skip-proof, dedup-free cursor; see module docstring). Paged by
    resultOffset up to the service maxRecordCount.

    If `limit` is given, the first page is capped at limit+1 rows so the watcher
    can detect an over-cap blast (e.g. a source-side full-table reinsert bumping
    every OBJECTID) in a SINGLE query, without ever pulling the ~214k-row table:
    a return longer than `limit` is the caller's cue to re-baseline instead of
    stampeding. Returns the raw ArcGIS attribute dicts; mapping is pure functions
    below."""
    c = _svc(cfg_gfl)
    page = c["max_record_count"] if limit is None else min(c["max_record_count"], int(limit) + 1)
    out: list[dict] = []
    offset = 0
    while True:
        data = _query(c["service_url"], c["readings_layer"], {
            "where": f"OBJECTID > {int(since_oid)}",
            "outFields": _READING_FIELDS,
            "orderByFields": "OBJECTID ASC",
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": page,
        })
        feats = _features(data)
        out.extend(feats)
        if limit is not None and len(out) > int(limit):
            return out[: int(limit) + 1]           # over-cap signal; caller re-baselines
        if not feats or not data.get("exceededTransferLimit"):
            break
        offset += len(feats)
    return out


def fetch_baseline(cfg_gfl: dict, *, station_prefix: str = DEFAULT_STATION_PREFIX) -> list[dict]:
    """The current latest reading per perimeter station (top rows by OBJECTID
    DESC, deduped to the newest per station). Used ONLY on the first-ever run to
    baseline the cursor to 'now' WITHOUT pulling the ~214k-row history — record
    the current readings, alert on none (WDS Rule B, self-protecting first run).
    A little headroom (60 rows) over the six stations covers a poll landing on an
    hour boundary where two hours' rows interleave."""
    c = _svc(cfg_gfl)
    data = _query(c["service_url"], c["readings_layer"], {
        "where": "1=1",
        "outFields": _READING_FIELDS,
        "orderByFields": "OBJECTID DESC",
        "returnGeometry": "false",
        "resultRecordCount": 60,
    })
    latest: dict[str, dict] = {}
    for a in _features(data):                       # OBJECTID DESC ⇒ first seen is newest
        st = (a.get("LocName") or "").strip()
        if station_prefix and not st.startswith(station_prefix):
            continue
        if st and st not in latest:
            latest[st] = a
    return list(latest.values())


# ---------------------------------------------------------------------------
# Pure helpers — accessors, sentinel/threshold classification, ADR-004 mapping
# (no network; unit-tested directly)
# ---------------------------------------------------------------------------

def oid_of(row: dict) -> Optional[int]:
    v = row.get("OBJECTID")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def max_oid(rows) -> Optional[int]:
    """Highest OBJECTID among rows, or None. This is the advanced cursor value.
    (The Sheet stores it as text; callers that read it back MUST int() before
    max() — "9" > "17614325" as strings, which would silently rewind the cursor.
    See sheet_writer.gfl_air_cursor.)"""
    oids = [o for o in (oid_of(r) for r in rows) if o is not None]
    return max(oids) if oids else None


def station_of(row: dict) -> str:
    return (row.get("LocName") or "").strip()


def reading_iso(row: dict) -> str:
    """The reading's timestamp as ISO-8601 UTC 'YYYY-MM-DDTHH:MMZ'. The feed
    stores Date as epoch-milliseconds. Blank (not a raise) on a missing/garbage
    value — a parse miss must never abort a poll."""
    ms = row.get("Date")
    if ms is None:
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _as_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_sentinel(value: Optional[float], sentinel) -> bool:
    return value is not None and sentinel is not None and value == float(sentinel)


def classify_reading(row: dict, thresholds: dict, sentinels: Optional[dict] = None,
                     watch_thresholds: Optional[dict] = None) -> dict:
    """Per-reading severity from the config action levels — GFL's OWN classifier.
    It deliberately does NOT reuse email_alerts.is_urgent, which thresholds on
    Fahrenheit temperature and would never fire on H2S/CH4 (WDS Rule D: a new
    stream brings its own classifier).

    Returns:
      {'severity': 'urgent' | 'watch' | 'anomaly' | 'ok',
       'h2s': (value|None, status), 'ch4': (value|None, status),
       'reasons': [str, ...]}
    where status is 'exceedance' | 'watch' | 'sentinel' | 'ok' | 'missing'.

    Precedence (highest wins): a real reading >= its action level is 'urgent'; a
    real reading >= its (lower) WATCH level but below the action level is 'watch'
    (early-warning); a sentinel with no real exceedance/watch is 'anomaly' (surfaced,
    never silently dropped — the sentinel is ambiguous no-data vs off-scale-high, and
    the fail-safe default is to flag it); otherwise 'ok'. `watch_thresholds` is an
    OPTIONAL lower tier (partial dict — only listed pollutants get a watch level);
    omit it (None) for the original single-tier behavior. Config-driven (ADR 014)."""
    thresholds = thresholds or {}
    sent = sentinels or {}
    watch = watch_thresholds or {}
    result: dict = {"reasons": []}
    _RANK = {"ok": 0, "anomaly": 1, "watch": 2, "urgent": 3}
    severity = "ok"

    def _bump(level: str) -> None:
        nonlocal severity
        if _RANK[level] > _RANK[severity]:
            severity = level

    for key, field, _unit, cfgkey, _metric in _POLLUTANTS:
        val = _as_float(row.get(field))
        if val is None:
            result[key] = (None, "missing")
            continue
        if _is_sentinel(val, sent.get(cfgkey)):
            result[key] = (val, "sentinel")
            result["reasons"].append(
                f"{field}={val:g} is a {SENTINEL_REASON} (ambiguous — surfaced as anomaly)")
            _bump("anomaly")
            continue
        thr = thresholds.get(cfgkey)
        wthr = watch.get(cfgkey)
        if thr is not None and val >= float(thr):
            result[key] = (val, "exceedance")
            result["reasons"].append(f"{field}={val:g} >= action level {float(thr):g}")
            _bump("urgent")
        elif wthr is not None and val >= float(wthr):
            result[key] = (val, "watch")
            _below = f" (below the {float(thr):g} action level)" if thr is not None else ""
            result["reasons"].append(
                f"{field}={val:g} >= watch level {float(wthr):g}, early-warning{_below}")
            _bump("watch")
        else:
            result[key] = (val, "ok")
    result["severity"] = severity
    return result


def watch_config_warnings(thresholds: dict, watch_thresholds: dict) -> list[str]:
    """Config-sanity: each watch level must sit BELOW its action level, or that
    pollutant's watch tier is DEAD — classify_reading checks the action level first,
    so a watch >= action can never be reached. Returns one human warning per inverted
    or degenerate watch level (empty when the config is sane). Pure; the caller logs."""
    thr = thresholds or {}
    watch = watch_thresholds or {}
    out: list[str] = []
    for cfgkey, wval in watch.items():
        if wval is None:
            continue
        aval = thr.get(cfgkey)
        if aval is not None and float(wval) >= float(aval):
            out.append(
                f"watch_thresholds.{cfgkey}={wval} >= action level {aval} — the watch "
                f"tier for {cfgkey} can never fire (the action level is checked first).")
    return out


def reading_note(row: dict, field: str) -> str:
    """The Measurement 'Note' for one pollutant of one reading. Carries (a) the
    mandated GFL-self-reported attribution, (b) location_type=perimeter_station —
    which the ADR-004 Measurement schema has no column for yet (roadmap), so it
    rides the note rather than being dropped — and (c) a BDL flag when the source
    marks the reading below the detection limit."""
    parts = [SELF_REPORTED, "location_type=perimeter_station"]
    txt = (row.get(f"{field}_Text") or "").strip()
    if txt.upper() == "BDL":
        parts.append("below detection limit (BDL)")
    return "; ".join(parts)


def reading_to_measurements(
    row: dict,
    *,
    sentinels: Optional[dict] = None,
    station_prefix: str = DEFAULT_STATION_PREFIX,
) -> list[dict]:
    """Zero-or-more ADR-004 Measurement dicts (H2S, CH4) for one raw reading row.

    basis is ALWAYS 'measured' — a real perimeter reading, never a permitted
    ceiling (the ADR-004 / CLAUDE.md invariant this whole project guards). Sentinel
    /no-data values are excluded from the measured series (surfaced as anomalies by
    the watcher instead). well_id is the station; the note carries the
    self-reported attribution + location_type. Rows for a non-perimeter station
    (station_prefix mismatch, e.g. the MET tower) yield nothing."""
    st = station_of(row)
    if station_prefix and not st.startswith(station_prefix):
        return []
    sent = sentinels or {}
    as_of = reading_iso(row)
    out: list[dict] = []
    for _key, field, unit, cfgkey, metric in _POLLUTANTS:
        val = _as_float(row.get(field))
        if val is None or _is_sentinel(val, sent.get(cfgkey)):
            continue
        out.append({
            "metric": metric,
            "value": val,
            "unit": unit,
            "basis": "measured",
            "well_id": st,
            "as_of_date": as_of,
            "note": reading_note(row, field),
        })
    return out


def select_measurements(
    readings: list[dict],
    mode: str,
    thresholds: dict,
    *,
    sentinels: Optional[dict] = None,
    station_prefix: str = DEFAULT_STATION_PREFIX,
) -> list[dict]:
    """Which readings become rows in the shared Measurements case-file tab.

    The feed is HOURLY × 6 stations × 2 pollutants — writing every one is ~288
    rows/day into a tab other rebuilds scan, and hourly `H2S=0.0 BDL` is noise. So
    the DEFAULT mode ('digest') keeps the evidence without the firehose; 'all' is
    the full-fidelity opt-in. Either way the SOURCE feed remains the system of
    record for the complete hourly history, so 'digest' loses nothing while the
    feed exists (ADR 014 flags the tradeoff + the Drive-archive fast-follow if GFL
    ever purges history).

      - 'all'    → every reading's H2S + CH4 measurement.
      - 'digest' → per (station, pollutant, DAY) the peak reading, PLUS every
                   exceedance reading individually (so no over-threshold reading is
                   ever dropped even if it isn't that day's peak). Deduped by
                   (well_id, metric, as_of_date). ~12 rows/day in normal operation.

    Pure — unit-tested. Sentinels are already excluded upstream by
    reading_to_measurements (never a measured value)."""
    all_m: list[dict] = []
    for r in readings:
        all_m.extend(reading_to_measurements(r, sentinels=sentinels, station_prefix=station_prefix))
    if mode == "all":
        return all_m

    thr_by_metric = {
        METRIC_H2S: (thresholds or {}).get("h2s_ppb"),
        METRIC_CH4: (thresholds or {}).get("ch4_ppm"),
    }
    keep: dict = {}

    def _add(m):
        keep[(m["well_id"], m["metric"], m["as_of_date"])] = m

    # Per (station, metric, day) peak.
    peak: dict = {}
    for m in all_m:
        day = (m["as_of_date"] or "")[:10]
        pk = (m["well_id"], m["metric"], day)
        if pk not in peak or m["value"] > peak[pk]["value"]:
            peak[pk] = m
    for m in peak.values():
        _add(m)

    # Every exceedance, individually (peak ⊇ exceedance only if it IS the peak).
    for m in all_m:
        thr = thr_by_metric.get(m["metric"])
        if thr is not None and m["value"] >= float(thr):
            _add(m)

    return sorted(keep.values(), key=lambda m: (m["as_of_date"], m["well_id"], m["metric"]))
