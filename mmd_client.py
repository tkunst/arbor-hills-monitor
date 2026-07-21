"""
mmd_client.py — fetch + canonicalize for the EGLE MMD Open Data watch
(Stream I). See docs/decisions/018-mmd-open-data-watch.md.

EGLE's Materials Management Division publishes its statewide facility layers as
a KEYLESS ArcGIS REST MapServer (gisagoegle.state.mi.us — the same ArcGIS query
idiom as Stream E's Barr FeatureServer, but a document-free status source, not
a numeric feed). Layer 0 "Materials Management Facilities" is the master table:
one record per (facility, module registration), so a single wdsid can carry
several records — Arbor Hills' 475946 carries BOTH its SolidWaste landfill
record (disposalareastatus "Active - Accepting") AND a Utilization/CMPST
compost record that is HIDDEN from the public map (show=0) yet present in the
data, with its own registration-expiration date. That hidden-but-present shape
is exactly why this watch queries the DATA, not the map.

The other watched wdsid, 465941 ("ARBOR HILLS COMPOST AREA" in WDS), is ABSENT
from this service today — an empty record set is a perfectly good baseline, and
the record APPEARING is the alert (the state starting to track the compost/
expansion parcel), mirroring Stream H's notice:N2688 mention trip-wire.

This module only FETCHES + CANONICALIZES; snapshotting/diffing/alerting is
mmd_watcher.py. One query fetches every watched wdsid at once (`wdsid IN
(...)`) — the per-wdsid split happens in the watcher, same one-fetch-many-items
shape as rop_client's CSV.

Two deliberate exclusions from the canonical record (both churn-prone display
data, the PFAS-cache-buster lesson of ADR 012):
  - OID: server-assigned, renumbers when EGLE republishes the service — a
    renumbering would false-alert every watched facility at once.
  - latdeccord/longdeccord: the coordinates are EGLE-side interpolations (WDS
    labels them "Lat/Long interpolation"), so a re-derivation could shift them
    without anything real changing.
`show` IS kept: a hidden record becoming visible (or vice versa) is signal.

Date fields arrive as epoch MILLISECONDS; they're converted to UTC YYYY-MM-DD
so the snapshot JSON is human-readable in the Sheet and hash-stable.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# Layer 0 = "Materials Management Facilities", the master (facility, module)
# table — it contains every module's record for a wdsid (SolidWaste,
# Utilization/CMPST, ...), so the per-module layers (1 Compost, 6 Part 115
# Landfills, ...) never need separate queries for this watch.
DEFAULT_QUERY_URL = ("https://gisagoegle.state.mi.us/arcgis/rest/services/EGLE/"
                     "MmdOpenData/MapServer/0/query")

# Priority order: the landfill first (the live facility), then the compost-area
# record whose APPEARANCE is the expansion-parcel trip-wire.
DEFAULT_WDSIDS = (475946, 465941)

# The canonical record: every schema field EXCEPT OID / lat / long / geometry
# (see module docstring). mmd_watcher hashes and diffs exactly these, so adding
# a field here changes every snapshot hash (one one-time "changed" alert per
# watched wdsid on the next run — visible, reviewable, then quiet).
RECORD_FIELDS = (
    "show", "module", "wdsid", "legalsitename", "specificsitename", "actcode",
    "facilitytype", "addrline1", "addrline2", "city", "state", "zip",
    "countyname", "districtname", "p115status", "p115authexprdate",
    "compoststatus", "compostregexprdate", "ewasteregexprdate",
    "scraptirestatus", "scraptireexprdate", "endusercmpltype",
    "endusercmpldate", "disposalareastatus", "landfilllink",
)

_DATE_FIELDS = frozenset((
    "p115authexprdate", "compostregexprdate", "ewasteregexprdate",
    "scraptireexprdate", "endusercmpldate",
))

# A real layer-0 query response for a couple of wdsids is ~1-3 KB of JSON; an
# HTML error page / bot wall would not parse as JSON at all, so the guard here
# is "parses as JSON and has the ArcGIS shape", not a byte-size floor.


class MmdFetchError(RuntimeError):
    """The service couldn't be fetched cleanly (network error, non-200, a body
    that isn't JSON, or ArcGIS's 200-with-{"error": ...} idiom). TRANSIENT —
    the watcher skips-and-warns rather than diffing, so a blip never fires a
    spurious change alert. (A first-ever run with no baseline treats it as loud
    instead — an activation-time block must surface, not silently no-op.)"""


class MmdParseError(RuntimeError):
    """The response fetched and parsed as JSON but its STRUCTURE is wrong — no
    "features" array, a truncated result (exceededTransferLimit), or the layer
    schema no longer carries every RECORD_FIELDS name. Almost certainly EGLE
    reorganized the service. UNLIKE MmdFetchError this is ALWAYS loud in
    mmd_watcher.run(), never gated on baseline status — a structural break
    persists across runs, and letting it go quiet behind a baseline would hide
    a real service change forever (the same silent-stall class ADR 014's
    liveness guard exists to catch). Same split as rop_client's
    RopFetchError/RopParseError."""


def _opener():
    op = urllib.request.build_opener()
    op.addheaders = [("User-Agent", _UA), ("Accept", "application/json")]
    return op


def fetch_records(wdsids=DEFAULT_WDSIDS, url: str = DEFAULT_QUERY_URL,
                  timeout: int = 60) -> list[dict]:
    """ONE query for every watched wdsid's layer-0 records; returns the raw
    attribute dicts (canonicalization is record_view's job). Raises
    MmdFetchError on network/HTTP/non-JSON/ArcGIS-error failures and
    MmdParseError on structural drift. `wdsids` values are int()-coerced into
    the where clause — a non-numeric config value raises ValueError loudly (a
    config bug should crash, not be smoothed over)."""
    where = f"wdsid IN ({','.join(str(int(w)) for w in wdsids)})"
    params = urllib.parse.urlencode({
        "where": where,
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    })
    full_url = f"{url}?{params}"
    try:
        r = _opener().open(full_url, timeout=timeout)  # nosec B310 — https constant + int-coerced params
        status = getattr(r, "status", None) or r.getcode()
        body = r.read()
    except Exception as e:  # noqa: BLE001 — network / HTTP -> transient
        raise MmdFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise MmdFetchError(f"GET {url} returned HTTP {status}")
    try:
        payload = json.loads(body)
    except Exception as e:  # noqa: BLE001 — an HTML bot wall / error page
        raise MmdFetchError(f"GET {url} did not return JSON ({len(body)} bytes)") from e
    if not isinstance(payload, dict):
        raise MmdFetchError(f"GET {url} returned non-object JSON")
    if "error" in payload:
        # ArcGIS reports failures as HTTP 200 + {"error": {...}} — treat it
        # exactly like any other failed fetch.
        raise MmdFetchError(f"ArcGIS error from {url}: {payload['error']}")
    if "features" not in payload:
        raise MmdParseError(
            f"query response from {url} has no 'features' — EGLE may have "
            "reorganized the MMD service; review before trusting this parse.")
    if payload.get("exceededTransferLimit"):
        # Can't happen for a handful of wdsids unless the service changed out
        # from under us — a truncated diff would silently drop records.
        raise MmdParseError("query response exceededTransferLimit — result truncated")
    field_names = {f.get("name") for f in payload.get("fields", [])}
    missing = [f for f in RECORD_FIELDS if f not in field_names]
    if missing:
        raise MmdParseError(
            f"layer schema is missing expected field(s) {missing} — EGLE may "
            "have changed the MMD layer; review before trusting this parse.")
    return [f.get("attributes", {}) for f in payload["features"]]


def epoch_ms_to_date(value) -> str:
    """An ArcGIS epoch-milliseconds date -> 'YYYY-MM-DD' (UTC). Empty string
    for None/empty; a non-numeric value falls back to str(value) so a service-
    side type change shows up as a visible diff, never a crash."""
    if value is None or value == "":
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return str(value)


def record_view(attrs: dict) -> dict:
    """The canonical, hash-stable view of one raw attribute dict: exactly
    RECORD_FIELDS, None -> "", dates -> ISO, everything else str()-normalized
    (ArcGIS mixes ints and strings across fields — 'show' is an int, 'wdsid' a
    number, statuses strings; stringifying makes the snapshot JSON stable and
    the Sheet cell readable)."""
    out = {}
    for f in RECORD_FIELDS:
        v = attrs.get(f)
        if f in _DATE_FIELDS:
            out[f] = epoch_ms_to_date(v)
        elif v is None:
            out[f] = ""
        else:
            out[f] = str(v).strip()
    return out
