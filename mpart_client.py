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
    Latitude/Longitude inside a bounding box. Key: LabSampleId | SiteCode |
    collection date | Duplicate (LabSampleId alone repeats — see `surface_water_view`).
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

Values are printed AS PUBLISHED in alerts (whitespace collapsed; numbers to 10
significant digits) — flags and codes are never re-sorted or re-cased. What EGLE
itself says about them (the layers' ArcGIS item descriptions, saved in Lotext
`documents/arbor-hills/source-docs/egle-mpart-pfas-gis-app-2026-09-26/`):

  - Both layers are STATIC PULLS: surface water "last pulled 2/2025"; fish "a static
    pull … on 01/14/2026 … updated annually". A new row therefore means EGLE
    republished the layer, not that sampling just happened — the collection date says
    when.
  - Surface-water flags are "a note from the analytical laboratory"; a flag of "Not
    Measured" means that analyte was not part of the analysis. Qualifier definitions
    include K (amount detected is below the method detection limit — Vista), J
    (below the reporting limit / LOQ), Q (ion-transition ratio outside acceptance
    criteria), B (also in the method blank), E (above the calibration range), I
    (chemical interference; EMPC for Eurofins) and IDA01 (estimated/suspect); "the
    definition varies by report". Observed K rows carry value == MDL.
  - Fish: "K" = not detected, the method detection limit is displayed; "J" = an
    estimated concentration; "I" = analytical interference was present, so a
    concentration could not be determined; "QNS" = not enough sample remained. Only
    edible-portion data are shown. PFOS is in ppb.

Stdlib only (urllib), like the sibling ArcGIS clients.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
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
    schema missing a field we read, a rejected query, a retired/moved layer (HTTP 404/410),
    a blank row key, or an oversized snapshot. ALWAYS loud, never gated on
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
    if not str(url).lower().startswith("https://"):
        raise MpartParseError("layer URL must be https:// (config error)")   # urllib would also open file:// / ftp://
    params = urllib.parse.urlencode({"where": where, "outFields": ",".join(fields),
                                     "returnGeometry": "false", "f": "json"})
    try:
        r = _opener().open(f"{url}?{params}", timeout=timeout)  # nosec B310 — https-only (checked above) + escaped params
        status = getattr(r, "status", None) or r.getcode()
        body = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            # a retired / moved layer persists across runs — structural, like ArcGIS's own 400
            raise MpartParseError(f"GET {url} returned HTTP {e.code} — the layer may have been retired or moved") from e
        raise MpartFetchError(f"GET {url} returned HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001 — network -> transient
        raise MpartFetchError(f"GET {url} failed: {type(e).__name__}") from e
    if status != 200:                # urllib raises for 4xx/5xx; this catches a 2xx that is not 200
        raise MpartFetchError(f"GET {url} returned HTTP {status}")
    try:
        payload = json.loads(body)
    except Exception as e:  # noqa: BLE001
        raise MpartFetchError(f"GET {url} did not return JSON ({len(body)} bytes)") from e
    if not isinstance(payload, dict):
        raise MpartFetchError(f"GET {url} returned non-object JSON")
    if "error" in payload:
        err = payload["error"] if isinstance(payload["error"], dict) else {}
        # upstream text is collapsed to ONE line before it reaches an exception (and so the public Actions
        # log): a newline followed by `::add-mask::` / `::error::` would otherwise be a runner command
        shown = re.sub(r"\s+", " ", str(payload["error"])).strip()[:200]
        if err.get("code") == 400:
            # ArcGIS answers a rejected query (a renamed field in the where clause, a bad layer id)
            # as 200 + {"error": {"code": 400}}. That persists across runs: structural, not a blip.
            raise MpartParseError(f"ArcGIS rejected the query at {url}: {shown} — the layer may have changed")
        raise MpartFetchError(f"ArcGIS error from {url}: {shown}")
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


def flag_text(raw) -> str:
    """A flag/code cell AS PUBLISHED, whitespace collapsed: ' J' -> 'J', 'J, Q' ->
    'J, Q', 'Not Measured' -> 'Not Measured', ' ' / None -> ''. Never re-cased or
    re-sorted, so what an alert prints is what EGLE published."""
    return _s(raw)


def flag_tokens(raw) -> tuple[str, ...]:
    """A flag cell as a sorted tuple of upper-case tokens, for LOGIC only (never for
    display): ' J' -> ('J',), 'J, Q' -> ('J','Q'), ' ' or None -> ()."""
    return tuple(sorted(t for t in re.split(r"[\s,;]+", _s(raw).upper()) if t))


def normalize_unit(unit: str) -> str:
    """'ng/L' / 'ppt' / 'parts per trillion' (any case, any spacing) -> 'ng/L'; anything
    else is returned unchanged. The layer's Unit is "provided by the analytical
    laboratory", so a screen against an ng/L value requires it to read as ng/L."""
    u = re.sub(r"\s+", "", str(unit or "")).lower()
    return "ng/L" if u in ("ng/l", "ppt", "partspertrillion", "ng/l.") else str(unit or "")


def surface_water_view(attrs: dict) -> dict:
    """Canonical view of one surface-water row (SW_VIEW). The key is COMPOSITE —
    LabSampleId|SiteCode|date|Duplicate — because the lab id alone is not globally
    unique: the live layer mixes Eurofins ids (`240-169452-6`) with short, site-derived
    Vista labels (`UT-0100`, `JD-0100`) that a later lab job at the same site could
    reuse. All four parts are business fields, stable across republishes (unlike
    GlobalID / OBJECTID)."""
    date = epoch_ms_to_date(attrs.get("CollectionDate"))
    key = "|".join((_s(attrs.get("LabSampleId")) or "no-lab-id", _s(attrs.get("SiteCode")), date,
                    _s(attrs.get("Duplicate"))))
    v = {"key": key, "site": _s(attrs.get("SiteCode")), "waterbody": _s(attrs.get("Waterbody")),
         "description": _s(attrs.get("Description")), "date": date,
         "sample_type": _s(attrs.get("SampleType")), "unit": _s(attrs.get("Unit"))}
    for name, col in ANALYTES.items():
        v[name] = _num(attrs.get(col))
        v[f"{name}_flag"] = flag_text(attrs.get(f"{col}Flag"))
        v[f"{name}_mdl"] = _num(attrs.get(f"{col}Mdl"))
    return v


def fish_view(attrs: dict) -> dict:
    """Canonical view of one fish row (FISH_VIEW), keyed by the unique SampleID."""
    station = attrs.get("StationID")
    try:
        station = str(int(float(station)))
    except (TypeError, ValueError):
        station = _s(station)
    date = epoch_ms_to_date(attrs.get("CollectionDate"))
    key = _s(attrs.get("SampleID")) or "|".join(("no-sample-id", _s(attrs.get("FishID")), station, date))
    return {"key": key, "fish_id": _s(attrs.get("FishID")), "station": station,
            "waterbody": _s(attrs.get("WaterBody")), "location": _s(attrs.get("SamplingLocation")),
            "date": date, "species": _s(attrs.get("Species")),
            "pfos_ppb": _num(attrs.get("PFOSppb")), "pfos_code": flag_text(attrs.get("PFOScode"))}


FISH_CODE_MEANING = {
    "K": "not detected — the value shown is the method detection limit",
    "J": "estimated concentration",
    "I": "analytical interference was present; a concentration could not be determined",
    "QNS": "not enough sample remained to analyze",
}


def fish_code_meaning(code: str) -> str:
    """EGLE's own wording for the fish result codes it defines (K, J, I, QNS); any other
    code (the layer also carries e.g. 'NA') is returned verbatim, uninterpreted."""
    toks = flag_tokens(code)
    known = [f"{t}: {FISH_CODE_MEANING[t]}" for t in toks if t in FISH_CODE_MEANING]
    other = [t for t in toks if t not in FISH_CODE_MEANING]
    return "; ".join(known + ([f"code {', '.join(other)} (not defined here)"] if other else []))


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


def index_rows(views: list[dict], fields: tuple[str, ...], what: str) -> tuple[dict[str, str], int]:
    """({key: row_hash}, n_disambiguated). A blank key raises MpartParseError (the diff
    is keyed on it). A DUPLICATE key does NOT fail the layer — that would leave the very
    sample that collided permanently unwatched — its rows are ordered by row hash and
    suffixed `#1`, `#2`, ... (deterministic whatever order the service returns them in);
    the count is returned so the watcher records it visibly."""
    groups: dict[str, list[str]] = {}
    for v in views:
        k = v["key"]
        if not k:
            raise MpartParseError(f"{what}: a row has a blank key — the diff key is unsound")
        groups.setdefault(k, []).append(row_hash(v, fields))
    out: dict[str, str] = {}
    dups = 0
    for k, hashes in groups.items():
        if len(hashes) == 1:
            out[k] = hashes[0]
            continue
        dups += len(hashes) - 1
        for n, h in enumerate(sorted(hashes), 1):
            out[f"{k}#{n}"] = h
    return out, dups


def views_by_index_key(views: list[dict], fields: tuple[str, ...]) -> dict[str, dict]:
    """{index key: canonical view}, in step with index_rows' disambiguation: a duplicated
    key `k` becomes `k#1`, `k#2`, … ordered by row hash (identical rows are interchangeable,
    so the assignment is deterministic whatever order the service returned them in)."""
    groups: dict[str, list[dict]] = {}
    for v in views:
        groups.setdefault(v["key"], []).append(v)
    out: dict[str, dict] = {}
    for k, vs in groups.items():
        if len(vs) == 1:
            out[k] = vs[0]
            continue
        for n, v in enumerate(sorted(vs, key=lambda x: row_hash(x, fields)), 1):
            out[f"{k}#{n}"] = v
    return out


SNAPSHOT_VERSION = 2


def snapshot_json(index: dict[str, str], hits: dict[str, str] | None = None) -> str:
    """The stored snapshot: compact sorted JSON {"v", "rows": {key: row_hash}, "hits":
    {hit id: value as published}}. `hits` are the (site|date|analyte) results already
    announced as above a screening value, so one is never re-announced as new — and, being
    keyed on the sample's site/date/analyte rather than on a row key, a re-issued row does
    not read as a new hit. Raises MpartParseError above MAX_SNAPSHOT_CHARS (a Sheets cell
    truncates silently)."""
    doc: dict = {"v": SNAPSHOT_VERSION, "rows": index}
    if hits:
        doc["hits"] = {str(k): str(v) for k, v in hits.items()}
    s = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(s) > MAX_SNAPSHOT_CHARS:
        raise MpartParseError(f"snapshot is {len(s):,} chars (> {MAX_SNAPSHOT_CHARS:,}) — refusing to truncate")
    return s


def parse_snapshot(snapshot: str):
    """(index, hits) from a stored snapshot, or None if it is unreadable OR was written
    by a different SNAPSHOT_VERSION (the caller re-baselines silently and says so —
    diffing against a differently-shaped snapshot would flag every row as changed)."""
    try:
        doc = json.loads(snapshot)
        if (not isinstance(doc, dict) or doc.get("v") != SNAPSHOT_VERSION
                or not isinstance(doc.get("rows"), dict) or not isinstance(doc.get("hits", {}), dict)):
            return None
        return ({str(k): str(v) for k, v in doc["rows"].items()},
                {str(k): str(v) for k, v in doc.get("hits", {}).items()})
    except (ValueError, TypeError):
        return None


def snapshot_hash(snapshot: str) -> str:
    return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:16]
