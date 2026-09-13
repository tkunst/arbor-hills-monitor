"""
pfas_pws_client.py — fetch + canonicalize for the EGLE Public Water Supply PFAS
sampling watch (Stream R). See docs/decisions/042-pfas-pws-watch.md.

EGLE publishes every Public Water Supply's PFAS sampling result behind the
statewide MPART PFAS map as a KEYLESS ArcGIS FeatureServer table (the same
ArcGIS query idiom as Stream E's Barr feed and Stream I's MMD registry, but a
lab-result table, not a live numeric feed). Layer 1
("PublicWaterSupplySamplingResults") is one row per sampling event at one entry
point: WSSN + SampleDate + SysSampleCode identify the round, and the seven
Michigan-regulated PFAS analytes (HFPO-DA/GenX, PFBS, PFHxA, PFHxS, PFNA, PFOA,
PFOS) are seven String columns, each a result in ng/L (ppt).

WHY THIS SOURCE: Salem Elementary (WSSN 2001381), whose supply sits in the
landfill's capture zone, is sampled ~annually and has come back all-non-detect
(<2 ppt) across six rounds Dec-2020→Feb-2025 (the human baseline in Lotext
`documents/arbor-hills/source-docs/salem-elementary-pfas-egle-pws/NOTES.md`).
The watch exists to catch (a) a NEW sampling round appearing and (b) any first
DETECTION — the drinking-water half of the PFAS story the landfill's own
leachate/groundwater PFAS record (Stream, RIDE) can't see.

VALUE VOCABULARY (verified live across the full 23,951-row table, 2026-09-12):
every analyte value is EITHER the single non-detect token `<2` OR a plain
number; there are NO qualifier flags (`J`), no `ND`, no mixed reporting limits
today. But labs DO use those forms elsewhere, and for a drinking-water watch a
missed detection is the worst outcome — so `classify_value` is FAIL-SAFE: it
calls a value a non-detect ONLY on a recognized non-detect pattern (`<`-prefix,
`ND`/`NOT DETECTED`/`BDL`/`U`), a detection when it parses as a number (after
stripping a trailing qualifier token like `J`), "no data" on null/blank, and
"unrecognized" on anything else — and the watcher treats UNRECOGNIZED as a
POSSIBLE detection (alert + log), never silently dropping it (the
fail-safe-when-external-semantics-unknowable rule).

This module only FETCHES + CANONICALIZES; snapshotting/diffing/alerting is
pfas_pws_watcher.py. ONE query fetches every watched WSSN at once (`WSSN IN
(...)`), the per-WSSN split happens in the watcher — the same one-fetch-many-
items shape as mmd_client / rop_client. Stdlib only (urllib), like the sibling
ArcGIS clients; NEVER routes through egle_doc_parser (not an EGLE document).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# Layer 1 = "PublicWaterSupplySamplingResults" — one row per (WSSN, sampling
# event, entry point), all seven regulated PFAS analytes as columns.
DEFAULT_QUERY_URL = ("https://gisagoegle.state.mi.us/arcgis/rest/services/EGLE/"
                     "PublicWaterSupplySamplingOpenData/FeatureServer/1/query")

# Salem Elementary (in the landfill's capture zone) is the confirmed default;
# more capture-zone WSSNs are added via config, not here.
DEFAULT_WSSNS = (2001381,)

# Michigan's seven regulated PFAS (the compounds with state drinking-water MCLs).
# The API field name for GenX/HFPO-DA is "HFPODA" (no hyphen).
ANALYTES = ("HFPODA", "PFBS", "PFHxA", "PFHxS", "PFNA", "PFOA", "PFOS")

# The canonical record fields we fetch + hash/diff (explicit outFields, NEVER
# "*" — keeps OBJECTID/HexID/GlobalID churn out of the snapshot the way
# ride_client/mmd_client exclude server-assigned ids). Adding a field here
# changes every snapshot hash (one one-time re-baseline per WSSN, reviewable).
RECORD_FIELDS = ("WSSN", "SystemName", "LocName", "SysSampleCode", "SampleDate",
                 *ANALYTES)
_OUTFIELDS = ",".join(RECORD_FIELDS)

# Recognized NON-DETECT tokens (besides a `<`-prefixed reporting limit like
# "<2"/"<4"). Uppercased before matching. Anything not matched here and not a
# number is treated as a POSSIBLE detection, never as clean.
_NONDETECT_TOKENS = frozenset(("ND", "NOT DETECTED", "BDL", "U", "<RL"))


class PwsFetchError(RuntimeError):
    """The service couldn't be fetched cleanly (network error, non-200, a body
    that isn't JSON, or ArcGIS's 200-with-{"error": ...} idiom). TRANSIENT — the
    watcher skips-and-warns rather than diffing, so a blip never fires a spurious
    change alert. (A first-ever run with no baseline treats it as loud instead —
    an activation-time block must surface, not silently no-op forever.) Same
    split as mmd_client's MmdFetchError/MmdParseError."""


class PwsParseError(RuntimeError):
    """The response fetched and parsed as JSON but its STRUCTURE is wrong — no
    "features" array, a truncated result (exceededTransferLimit), or the layer
    schema no longer carries every RECORD_FIELDS name. Almost certainly EGLE
    reorganized the service. UNLIKE PwsFetchError this is ALWAYS loud in the
    watcher, never gated on baseline status — a structural break persists across
    runs, and letting it go quiet behind a baseline would hide a real service
    change (and a possible missed detection) forever."""


def _opener():
    op = urllib.request.build_opener()
    op.addheaders = [("User-Agent", _UA), ("Accept", "application/json")]
    return op


def fetch_results(wssns=DEFAULT_WSSNS, url: str = DEFAULT_QUERY_URL,
                  timeout: int = 60) -> list[dict]:
    """ONE query for every watched WSSN's sampling rows; returns the raw
    attribute dicts (canonicalization is record_view's job). Raises PwsFetchError
    on network/HTTP/non-JSON/ArcGIS-error failures and PwsParseError on
    structural drift. `wssns` values are int()-coerced into the where clause — a
    non-numeric config value raises ValueError loudly (a config bug should crash,
    not be smoothed over)."""
    where = f"WSSN IN ({','.join(str(int(w)) for w in wssns)})"
    params = urllib.parse.urlencode({
        "where": where,
        "outFields": _OUTFIELDS,
        "returnGeometry": "false",
        "orderByFields": "SampleDate DESC",
        "f": "json",
    })
    full_url = f"{url}?{params}"
    try:
        r = _opener().open(full_url, timeout=timeout)  # nosec B310 — https constant + int-coerced params
        status = getattr(r, "status", None) or r.getcode()
        body = r.read()
    except Exception as e:  # noqa: BLE001 — network / HTTP -> transient
        raise PwsFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise PwsFetchError(f"GET {url} returned HTTP {status}")
    try:
        payload = json.loads(body)
    except Exception as e:  # noqa: BLE001 — an HTML bot wall / error page
        raise PwsFetchError(f"GET {url} did not return JSON ({len(body)} bytes)") from e
    if not isinstance(payload, dict):
        raise PwsFetchError(f"GET {url} returned non-object JSON")
    if "error" in payload:
        # ArcGIS reports failures as HTTP 200 + {"error": {...}}.
        raise PwsFetchError(f"ArcGIS error from {url}: {payload['error']}")
    if "features" not in payload:
        raise PwsParseError(
            f"query response from {url} has no 'features' — EGLE may have "
            "reorganized the PWS PFAS service; review before trusting this parse.")
    if payload.get("exceededTransferLimit"):
        # A handful of WSSNs, each sampled ~annually, can't legitimately exceed
        # the transfer limit — a truncated diff would silently drop rounds (and
        # possibly a detection). Loud, so it's paginated rather than trusted.
        raise PwsParseError("query response exceededTransferLimit — result truncated")
    field_names = {f.get("name") for f in payload.get("fields", [])}
    missing = [f for f in RECORD_FIELDS if f not in field_names]
    if missing:
        raise PwsParseError(
            f"layer schema is missing expected field(s) {missing} — EGLE may "
            "have changed the PWS PFAS layer; review before trusting this parse.")
    return [f.get("attributes", {}) for f in payload["features"]]


def epoch_ms_to_date(value) -> str:
    """An ArcGIS epoch-milliseconds date -> 'YYYY-MM-DD' (UTC). Empty string for
    None/empty; a non-numeric value falls back to str(value) so a service-side
    type change shows up as a visible diff, never a crash (mmd_client idiom)."""
    if value is None or value == "":
        return ""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return str(value)


def classify_value(raw) -> tuple[str, float | None]:
    """Classify one analyte result, FAIL-SAFE. Returns (state, value):
      - ("nodata", None)      null / blank  (analyte not in this round's panel)
      - ("nondetect", None)   a recognized non-detect: `<`-prefixed limit ("<2",
                              "<4") or a known token (ND / NOT DETECTED / BDL / U)
      - ("detection", float)  parses as a number (after stripping a trailing
                              qualifier token like "J"), i.e. the compound was
                              quantified — INCLUDING a value below the usual 2 ppt
                              RL (e.g. "1.9"), because a quantified value is a
                              detection, not a non-detect
      - ("unrecognized", None) anything else — the watcher treats this as a
                              POSSIBLE detection (alert + log), never as clean
    """
    if raw is None:
        return ("nodata", None)
    s = str(raw).strip()
    if s == "":
        return ("nodata", None)
    if s.startswith("<") or s.upper() in _NONDETECT_TOKENS:
        return ("nondetect", None)
    try:
        return ("detection", float(s))
    except ValueError:
        # Strip a trailing qualifier token, e.g. "2.1 J" -> "2.1".
        head = s.split()[0]
        try:
            return ("detection", float(head))
        except ValueError:
            return ("unrecognized", None)


def record_view(attrs: dict) -> dict:
    """The canonical, hash-stable view of one raw attribute dict: WSSN / system /
    location / sample code / ISO sample date + the seven analyte results as
    strings (None -> ""), everything str()-normalized so the snapshot JSON is
    stable and the Sheet cell readable (mmd_client.record_view idiom)."""
    def norm(v):
        return "" if v is None else str(v).strip()

    return {
        "wssn": norm(attrs.get("WSSN")),
        "system": norm(attrs.get("SystemName")),
        "loc": norm(attrs.get("LocName")),
        "sample_code": norm(attrs.get("SysSampleCode")),
        "sample_date": epoch_ms_to_date(attrs.get("SampleDate")),
        "analytes": {a: norm(attrs.get(a)) for a in ANALYTES},
    }


def round_key(view: dict) -> str:
    """A stable per-WSSN key for a sampling round. SysSampleCode is unique and
    never null across the whole table (verified 2026-09-12), so it is the primary
    key; a composite (date|loc) is the fallback if a future row ever has a blank
    code. A single WSSN can legitimately have two rows on the same date (multiple
    entry points), which SysSampleCode distinguishes correctly."""
    code = view.get("sample_code", "")
    if code:
        return code
    return f"{view.get('sample_date','')}|{view.get('loc','')}"


def round_detections(view: dict) -> list[dict]:
    """Every analyte in a round that is a DETECTION or UNRECOGNIZED (never a
    clean non-detect / no-data). Each entry: {analyte, raw, value, state}. This
    is what the watcher elevates a 'new round' alert on and routes to
    Measurements. Sorted by analyte for stable output."""
    out = []
    for a in ANALYTES:
        raw = view["analytes"].get(a, "")
        state, value = classify_value(raw)
        if state in ("detection", "unrecognized"):
            out.append({"analyte": a, "raw": raw, "value": value, "state": state})
    return out
