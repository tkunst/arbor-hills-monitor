"""
ride_client.py — fetch + canonicalize for the EGLE RIDE / Part 201 + UST status
watch (Stream J). See docs/decisions/019-ride-part201-watch.md.

RIDE (EGLE's Remediation Information Data Exchange) itself is an auth-walled
Angular SPA with no anonymous document API (confirmed by worker #69's recon).
But EGLE separately publishes the underlying per-site STATUS as a KEYLESS
ArcGIS REST MapServer (gisagoegle.state.mi.us — the same host/idiom as Stream
I's MMD watch, just a different service and, here, two layers instead of one):

  - Layer 0 "RRD Sites" (Part 201 contaminated-site remediation), key field
    SiteID — the 5 Arbor Hills-area sites (Salem Landfill, Arbor Hills - East,
    7667 Chubb Rd, 7941 Salem Rd, MITC Corridor).
  - Layer 1 "USTs" (Part 211 underground storage tanks), key field
    FacilityID — the GFL Environmental USA UST at 7811 Chubb Rd.

This is a STATUS watch, not a measurement poller: the service returns per-site
strings (RiskCondition, Contaminants) and a per-facility open-release count,
never numeric readings, so it does NOT touch the Measurement schema or
egle_doc_parser.py (the Decode base stays domain-agnostic — same posture as
Streams E/F/H/I).

This module only FETCHES + CANONICALIZES; snapshotting/diffing/alerting is
ride_watcher.py. One query per layer fetches every watched item at once
(`SiteID IN (...)` / `FacilityID IN (...)`) — the per-item split happens in the
watcher, same one-fetch-many-items shape as mmd_client/rop_client.

`outFields` is always an EXPLICIT field list (never `*`) and `returnGeometry`
is always `false` — this is how OID and geometry stay out of the canonical
record without special-casing (ADR 018's OID/coordinate false-alert lesson: a
server-side republish renumbers OID and would false-alert every site at once).
`ProjectManaager` (the schema's own typo — preserved here only in this comment,
never watched) is deliberately excluded too: a project-manager reassignment is
admin churn, not remediation signal, and would false-alert the same way.

LastUpdated arrives as epoch MILLISECONDS; it's converted to UTC YYYY-MM-DD so
the snapshot JSON is human-readable in the Sheet and hash-stable (the ADR
012/018 date-normalization idiom).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

_BASE = "https://gisagoegle.state.mi.us/arcgis/rest/services/EGLE/RRDOpenData/MapServer"

# Layer 0 = Part 201 remediation sites (key field SiteID).
DEFAULT_LAYER0_URL = f"{_BASE}/0/query"
# Layer 1 = Part 211 underground storage tanks (key field FacilityID).
DEFAULT_LAYER1_URL = f"{_BASE}/1/query"

# The 5 Arbor Hills-area Part 201 sites (worker #69's recon).
DEFAULT_SITE_IDS = ("81000033", "81000004", "81000835", "81000840", "82008712")
# The GFL Part 211 UST at 7811 Chubb Rd.
DEFAULT_FACILITY_IDS = ("00040223",)

# The canonical Layer-0 record: every watched field EXCEPT OID/geometry (never
# fetched at all — see module docstring) and ProjectManaager (fetched only if
# ever needed for display; not part of the watched/hashed set).
LAYER0_FIELDS = ("SiteID", "SiteName", "RiskCondition", "Contaminants", "LastUpdated")
# The canonical Layer-1 record, same exclusions.
LAYER1_FIELDS = ("FacilityID", "FacilityName", "RiskCondition", "Open_Release", "LastUpdated")

_DATE_FIELDS = frozenset(("LastUpdated",))

# A real query response for a handful of sites is a couple KB of JSON; an
# HTML error page / bot wall would not parse as JSON at all, so the guard here
# is "parses as JSON and has the ArcGIS shape", not a byte-size floor.


class RideFetchError(RuntimeError):
    """The service couldn't be fetched cleanly (network error, non-200, a body
    that isn't JSON, or ArcGIS's 200-with-{"error": ...} idiom). TRANSIENT —
    the watcher skips-and-warns rather than diffing, so a blip never fires a
    spurious change alert. (A first-ever run with no baseline treats it as loud
    instead — an activation-time block must surface, not silently no-op.)"""


class RideParseError(RuntimeError):
    """The response fetched and parsed as JSON but its STRUCTURE is wrong — no
    "features" array, a truncated result (exceededTransferLimit), or the layer
    schema no longer carries every watched field name. Almost certainly EGLE
    reorganized RRDOpenData. UNLIKE RideFetchError this is ALWAYS loud in
    ride_watcher.run(), never gated on baseline status — a structural break
    persists across runs, and letting it go quiet behind a baseline would hide
    a real service change forever (the same silent-stall class ADR 014's
    liveness guard exists to catch). Same split as mmd_client/rop_client."""


def _opener():
    op = urllib.request.build_opener()
    op.addheaders = [("User-Agent", _UA), ("Accept", "application/json")]
    return op


def _in_clause(field: str, values) -> str:
    """A `field IN ('a','b',...)` where-clause fragment. SiteID/FacilityID are
    text fields (FacilityID carries a leading zero — '00040223' — so they
    can't be int-coerced the way mmd_client validates its numeric wdsids);
    single quotes are doubled per the standard SQL/ArcGIS string-literal
    escape so a value can never break out of its quoted literal."""
    quoted = ",".join("'{}'".format(str(v).replace("'", "''")) for v in values)
    return f"{field} IN ({quoted})"


def _fetch(url: str, where: str, fields: tuple[str, ...], timeout: int) -> list[dict]:
    """Shared GET + validate for one layer query. Raises RideFetchError on
    network/HTTP/non-JSON/ArcGIS-error failures and RideParseError on
    structural drift (see the two exception classes' docstrings)."""
    params = urllib.parse.urlencode({
        "where": where,
        "outFields": ",".join(fields),
        "returnGeometry": "false",
        "f": "json",
    })
    full_url = f"{url}?{params}"
    try:
        r = _opener().open(full_url, timeout=timeout)  # nosec B310 — https constant + quote-escaped params
        status = getattr(r, "status", None) or r.getcode()
        body = r.read()
    except Exception as e:  # noqa: BLE001 — network / HTTP -> transient
        raise RideFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise RideFetchError(f"GET {url} returned HTTP {status}")
    try:
        payload = json.loads(body)
    except Exception as e:  # noqa: BLE001 — an HTML bot wall / error page
        raise RideFetchError(f"GET {url} did not return JSON ({len(body)} bytes)") from e
    if not isinstance(payload, dict):
        raise RideFetchError(f"GET {url} returned non-object JSON")
    if "error" in payload:
        # ArcGIS reports failures as HTTP 200 + {"error": {...}} — treat it
        # exactly like any other failed fetch.
        raise RideFetchError(f"ArcGIS error from {url}: {payload['error']}")
    if "features" not in payload:
        raise RideParseError(
            f"query response from {url} has no 'features' — EGLE may have "
            "reorganized RRDOpenData; review before trusting this parse.")
    if payload.get("exceededTransferLimit"):
        # Can't happen for a handful of ids unless the service changed out
        # from under us — a truncated diff would silently drop records.
        raise RideParseError(f"query response from {url} exceededTransferLimit — result truncated")
    field_names = {f.get("name") for f in payload.get("fields", [])}
    missing = [f for f in fields if f not in field_names]
    if missing:
        raise RideParseError(
            f"layer schema at {url} is missing expected field(s) {missing} — "
            "EGLE may have changed the RRDOpenData layer; review before trusting this parse.")
    return [f.get("attributes", {}) for f in payload["features"]]


def fetch_site_records(site_ids=DEFAULT_SITE_IDS, url: str = DEFAULT_LAYER0_URL,
                       timeout: int = 60) -> list[dict]:
    """ONE query for every watched Part 201 site's Layer-0 record; returns the
    raw attribute dicts (canonicalization is site_record_view's job)."""
    return _fetch(url, _in_clause("SiteID", site_ids), LAYER0_FIELDS, timeout)


def fetch_ust_records(facility_ids=DEFAULT_FACILITY_IDS, url: str = DEFAULT_LAYER1_URL,
                      timeout: int = 60) -> list[dict]:
    """ONE query for every watched Part 211 UST's Layer-1 record; returns the
    raw attribute dicts (canonicalization is ust_record_view's job)."""
    return _fetch(url, _in_clause("FacilityID", facility_ids), LAYER1_FIELDS, timeout)


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


def _record_view(attrs: dict, fields: tuple[str, ...]) -> dict:
    """The canonical, hash-stable view of one raw attribute dict: exactly
    `fields`, None -> "", dates -> ISO, everything else str()-normalized and
    stripped (the real service pads some SiteName values with trailing
    whitespace — '7667 Chubb Rd ' — worker #69's recon)."""
    out = {}
    for f in fields:
        v = attrs.get(f)
        if f in _DATE_FIELDS:
            out[f] = epoch_ms_to_date(v)
        elif v is None:
            out[f] = ""
        else:
            out[f] = str(v).strip()
    return out


def site_record_view(attrs: dict) -> dict:
    """Canonical view of one Layer-0 (Part 201 site) record."""
    return _record_view(attrs, LAYER0_FIELDS)


def ust_record_view(attrs: dict) -> dict:
    """Canonical view of one Layer-1 (Part 211 UST) record."""
    return _record_view(attrs, LAYER1_FIELDS)
