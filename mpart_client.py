"""
mpart_client.py — fetch + canonicalize for the MPART PFAS open-data layers
(Stream V). See docs/decisions/060-mpart-data-watch.md.

MPART (the Michigan PFAS Action Response Team — a multi-agency team housed at EGLE,
not a division) publishes its PFAS results as KEYLESS ArcGIS open-data layers. The
monitor already watches MPART's Arbor Hills WEB PAGE (ADR 012) and the public-water-
supply PFAS layer (Stream R, ADR 042); this module reads the other three layers the
Johnson Drain / Johnson Creek / upper-Rouge story lives in:

  - SURFACE WATER  PfasOpenData/MapServer/0 "Pfas Surface Water" — one row per
    sample, ~30 PFAS analytes as (value, Flag, Mdl, Rl) column groups; located by
    Latitude/Longitude inside a bounding box. Key: LabSampleId.
  - FISH           FcmpOpenData/FeatureServer/1 "Fish Contaminant Monitoring
    Sampling Data" (a TABLE) — one row per fish, keyed by the unique SampleID;
    watched stations + the same bounding box.
  - SITES / AOIs   MPART's "SitesAoisMerge" layer (a separate ArcGIS Online
    service) — the PFAS sites and areas of interest; the Arbor Hills row + the
    bounding box.

NOT here: the Public Water Supply layer. It is already watched by the live
Stream R (`pfas_pws`), so re-watching it would double-alert.

Only what the watch needs is fetched: `outFields` is always an EXPLICIT list (never
`*`) and `returnGeometry` is always `false`. That keeps OBJECTID / GlobalID (a
republish renumbers or regenerates them — ADR 018's false-alert lesson), coordinates,
and the EGLE staff name / e-mail / phone the sites layer carries (`SiteLead*` — a
named employee, and reassignment churn) out of the canonical record entirely.

Each layer's canonical view is one small string dict; `row_hash` (16 hex chars) is
its change detector, and the watcher snapshots {key: row_hash} per layer.

Values are printed VERBATIM in alerts. Flag vocabulary (observed 2026-09-28 on the
surface-water layer): 'K' (non-detect; the value is the method detection limit),
'J' (estimated), 'Q', combinations such as 'J, Q', and stray padding (' J', ' ').
Fish PFOS is in ppb (fillet); the code 'I' means NO VALUE PUBLISHED and is never a
detection. Stdlib only (urllib), like the sibling ArcGIS clients.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

_EGLE = "https://gisagoegle.state.mi.us/arcgis/rest/services/EGLE"
DEFAULT_SURFACE_WATER_URL = f"{_EGLE}/PfasOpenData/MapServer/0/query"
DEFAULT_FISH_URL = f"{_EGLE}/FcmpOpenData/FeatureServer/1/query"
DEFAULT_SITES_URL = ("https://services1.arcgis.com/FNjlrOFR0aGJ71Tg/arcgis/rest/services/"
                     "Michigan_PFAS_Sites_and_Areas_of_Interest_PUBLIC_view/FeatureServer/1/query")

# The Johnson Drain / Johnson Creek / upper Rouge box (lon_min, lat_min, lon_max, lat_max).
DEFAULT_BBOX = (-83.66, 42.34, -83.40, 42.46)
# The two Johnson Drain fish stations (1484 Fish Hatchery Park; 1507 "6-mile").
DEFAULT_FISH_STATIONS = (1484, 1507)

# The four analytes with a Rule 57 non-drink value (ng/L). (The layer carries ~30.)
ANALYTES = {
    "PFOS": "CAS1763231_PFOS",
    "PFOA": "CAS335671_PFOA",
    "PFHxS": "CAS355464_PFHxS",
    "PFNA": "CAS375951_PFNA",
}

SW_ID_FIELDS = ("LabSampleId", "SiteCode", "Waterbody", "Description", "CollectionDate",
                "SampleType", "Unit", "Duplicate")
SW_OUT_FIELDS = SW_ID_FIELDS + tuple(f"{c}{s}" for c in ANALYTES.values() for s in ("", "Flag", "Mdl"))
FISH_OUT_FIELDS = ("SampleID", "FishID", "StationID", "WaterBody", "SamplingLocation", "CollectionDate",
                   "Species", "PFOSppb", "PFOScode")
SITES_OUT_FIELDS = ("Name", "SiteOrAoi", "Address", "City", "County", "Type", "ResidentialWellsSampled",
                    "WebpageSite", "FacilityDate")

# Canonical (hashed + displayed) view of each layer's record.
SW_VIEW = ("key", "site", "waterbody", "description", "date", "sample_type", "unit") + tuple(
    f"{a}{s}" for a in ANALYTES for s in ("", "_flag", "_mdl"))
FISH_VIEW = ("key", "fish_id", "station", "waterbody", "location", "date", "species", "pfos_ppb", "pfos_code")
SITES_VIEW = ("key", "name", "kind", "address", "city", "county", "type", "residential_wells",
              "webpage", "facility_date")

MAX_SNAPSHOT_CHARS = 45_000          # a Sheets cell caps at 50,000; over this is a loud error, never a truncation


class MpartFetchError(RuntimeError):
    """Transient: network error, non-200, a body that isn't JSON, or ArcGIS's
    200-with-{"error": ...} idiom. The watcher skips-and-warns for an already-baselined
    layer (and counts it toward the liveness alert); loud for a layer with no baseline."""


class MpartParseError(RuntimeError):
    """Structural: no `features`, a truncated result (exceededTransferLimit), a layer
    schema missing a field we read, or a duplicate row key. ALWAYS loud, never gated on
    baseline status (a reorganized service persists across runs — the ADR 014 silent-
    stall class). Same split as ride_client / mmd_client."""


def _opener():
    op = urllib.request.build_opener()
    op.addheaders = [("User-Agent", _UA), ("Accept", "application/json")]
    return op


def quote_sql(value) -> str:
    """A single-quoted SQL string literal for an ArcGIS `where` (quotes doubled)."""
    return "'{}'".format(str(value).replace("'", "''"))


def bbox_where(bbox, lon: str, lat: str) -> str:
    """`lon >= x0 AND lon <= x1 AND lat >= y0 AND lat <= y1` from four numbers. Each is
    coerced to float, so nothing but a number can reach the query."""
    x0, y0, x1, y1 = (float(v) for v in bbox)
    return f"{lon} >= {x0} AND {lon} <= {x1} AND {lat} >= {y0} AND {lat} <= {y1}"


def _fetch(url: str, where: str, fields: tuple[str, ...], timeout: int) -> list[dict]:
    params = urllib.parse.urlencode({"where": where, "outFields": ",".join(fields),
                                     "returnGeometry": "false", "f": "json"})
    try:
        r = _opener().open(f"{url}?{params}", timeout=timeout)  # nosec B310 — https constant + escaped params
        status = getattr(r, "status", None) or r.getcode()
        body = r.read()
    except Exception as e:  # noqa: BLE001 — network / HTTP -> transient
        raise MpartFetchError(f"GET {url} failed: {type(e).__name__}") from e
    if status != 200:
        raise MpartFetchError(f"GET {url} returned HTTP {status}")
    try:
        payload = json.loads(body)
    except Exception as e:  # noqa: BLE001
        raise MpartFetchError(f"GET {url} did not return JSON ({len(body)} bytes)") from e
    if not isinstance(payload, dict):
        raise MpartFetchError(f"GET {url} returned non-object JSON")
    if "error" in payload:
        raise MpartFetchError(f"ArcGIS error from {url}: {str(payload['error'])[:200]}")
    if "features" not in payload:
        raise MpartParseError(f"query response from {url} has no 'features' — the service may have changed")
    if payload.get("exceededTransferLimit"):
        raise MpartParseError(f"query response from {url} exceededTransferLimit — result truncated")
    names = {f.get("name") for f in payload.get("fields", [])}
    missing = [f for f in fields if f not in names]
    if missing:
        raise MpartParseError(f"layer schema at {url} is missing expected field(s) {missing}")
    return [f.get("attributes", {}) for f in payload["features"]]


def fetch_surface_water(bbox=DEFAULT_BBOX, url: str = DEFAULT_SURFACE_WATER_URL, timeout: int = 60) -> list[dict]:
    return _fetch(url, bbox_where(bbox, "Longitude", "Latitude"), SW_OUT_FIELDS, timeout)


def fetch_fish(stations=DEFAULT_FISH_STATIONS, bbox=DEFAULT_BBOX, url: str = DEFAULT_FISH_URL,
               timeout: int = 60) -> list[dict]:
    ids = ",".join(str(int(s)) for s in stations)                    # int(): only digits reach the query
    where = f"StationID IN ({ids}) OR ({bbox_where(bbox, 'Long', 'Lat')})" if ids else bbox_where(bbox, "Long", "Lat")
    return _fetch(url, where, FISH_OUT_FIELDS, timeout)


def fetch_sites(name_like: str = "Arbor Hills", bbox=DEFAULT_BBOX, url: str = DEFAULT_SITES_URL,
                timeout: int = 60) -> list[dict]:
    like = quote_sql("%" + str(name_like).replace("%", "").replace("_", "") + "%")
    where = f"Name LIKE {like} OR ({bbox_where(bbox, 'Longitude', 'Latitude')})"
    return _fetch(url, where, SITES_OUT_FIELDS, timeout)


# ---------------------------------------------------------------------------
# Canonical views (pure)
# ---------------------------------------------------------------------------


def epoch_ms_to_date(value) -> str:
    """ArcGIS epoch-ms date -> 'YYYY-MM-DD' (UTC). '' for None/empty; a non-numeric
    value falls back to str() so a service-side type change is a visible diff."""
    if value in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return str(value)


def _s(v) -> str:
    return "" if v is None else re.sub(r"\s+", " ", str(v)).strip()


def _num(v) -> str:
    """A number as text without float noise; '' for None/blank; anything else verbatim."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return ""
    try:
        return format(float(v), ".10g")
    except (TypeError, ValueError):
        return _s(v)


def flag_tokens(raw) -> tuple[str, ...]:
    """A flag cell as a sorted tuple of upper-case tokens: ' J' -> ('J',), 'J, Q' ->
    ('J','Q'), ' ' or None -> ()."""
    return tuple(sorted(t for t in re.split(r"[\s,;]+", _s(raw).upper()) if t))


def surface_water_view(attrs: dict) -> dict:
    """Canonical view of one surface-water row (SW_VIEW). The key is the lab sample id
    (unique across the layer, and stable across republishes — unlike GlobalID);
    a row with no id falls back to site|date|duplicate."""
    date = epoch_ms_to_date(attrs.get("CollectionDate"))
    key = _s(attrs.get("LabSampleId")) or f"{_s(attrs.get('SiteCode'))}|{date}|{_s(attrs.get('Duplicate'))}"
    v = {"key": key, "site": _s(attrs.get("SiteCode")), "waterbody": _s(attrs.get("Waterbody")),
         "description": _s(attrs.get("Description")), "date": date,
         "sample_type": _s(attrs.get("SampleType")), "unit": _s(attrs.get("Unit"))}
    for name, col in ANALYTES.items():
        v[name] = _num(attrs.get(col))
        v[f"{name}_flag"] = ",".join(flag_tokens(attrs.get(f"{col}Flag")))
        v[f"{name}_mdl"] = _num(attrs.get(f"{col}Mdl"))
    return v


def fish_view(attrs: dict) -> dict:
    """Canonical view of one fish row (FISH_VIEW), keyed by the unique SampleID."""
    station = attrs.get("StationID")
    try:
        station = str(int(float(station)))
    except (TypeError, ValueError):
        station = _s(station)
    return {"key": _s(attrs.get("SampleID")), "fish_id": _s(attrs.get("FishID")), "station": station,
            "waterbody": _s(attrs.get("WaterBody")), "location": _s(attrs.get("SamplingLocation")),
            "date": epoch_ms_to_date(attrs.get("CollectionDate")), "species": _s(attrs.get("Species")),
            "pfos_ppb": _num(attrs.get("PFOSppb")), "pfos_code": _s(attrs.get("PFOScode"))}


def sites_view(attrs: dict) -> dict:
    """Canonical view of one MPART site/AOI (SITES_VIEW). Keyed by name + kind (the
    layer's OBJECTID/GlobalID churn on republish). EGLE staff contact fields are never
    fetched, so they cannot appear here."""
    name, kind = _s(attrs.get("Name")), _s(attrs.get("SiteOrAoi"))
    fd = attrs.get("FacilityDate")
    return {"key": f"{name.lower()}|{kind.lower()}", "name": name, "kind": kind,
            "address": _s(attrs.get("Address")), "city": _s(attrs.get("City")), "county": _s(attrs.get("County")),
            "type": _s(attrs.get("Type")), "residential_wells": _s(attrs.get("ResidentialWellsSampled")),
            "webpage": _s(attrs.get("WebpageSite")),
            "facility_date": epoch_ms_to_date(fd) if isinstance(fd, (int, float)) else _s(fd)}


def row_hash(view: dict, fields: tuple[str, ...]) -> str:
    """16-hex change detector over a canonical view's fields, in a fixed order."""
    blob = "\x1f".join(view.get(f, "") for f in fields).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def index_rows(views: list[dict], fields: tuple[str, ...], what: str) -> dict[str, str]:
    """{key: row_hash}. A blank or DUPLICATE key raises MpartParseError — the diff is
    keyed on it, so a collision would silently lose a row (the ADR 018 partial-key lesson)."""
    out: dict[str, str] = {}
    for v in views:
        k = v["key"]
        if not k or k in out:
            raise MpartParseError(f"{what}: blank or duplicate row key {k!r} — the diff key is unsound")
        out[k] = row_hash(v, fields)
    return out


def snapshot_json(index: dict[str, str]) -> str:
    """The stored snapshot: compact sorted JSON of {key: row_hash}. Raises
    MpartParseError above MAX_SNAPSHOT_CHARS (a Sheets cell truncates silently)."""
    s = json.dumps({"rows": index}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(s) > MAX_SNAPSHOT_CHARS:
        raise MpartParseError(f"snapshot is {len(s):,} chars (> {MAX_SNAPSHOT_CHARS:,}) — refusing to truncate")
    return s


def snapshot_hash(snapshot: str) -> str:
    return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:16]
