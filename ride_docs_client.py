"""
ride_docs_client.py — fetch + canonicalize for the EGLE RIDE anonymous DOCUMENT
listing (Stream T). See docs/decisions/061-rrd-documents-watch.md.

RIDE is EGLE's Remediation Information Data Exchange. Its public "Inventory of
Facilities" page is an Angular SPA that gives every anonymous visitor a
"Public User" session on its own (no credentials, no login: GET the shell page,
then GET /RIDE/Home/GetAppSettings, which returns userName "Public1"). The
page's own front end then calls three JSON endpoints that this module replays
with plain `requests`:

  - POST api/Location/GetFacilitiesTable            program number -> locationId
  - POST api/ContentManagerFile/GetContentManagerFilesForLocationFilesTable
                                                    the location's file list,
                                                    each record keyed by `uri`
  - POST api/ContentManagerFile/GetFileContents     {"uri": N} -> the file bytes

A bare POST with no cookie warm-up returns HTTP 405, so the warm-up is part of
`open_session()`. The session also sets F5 (`TS*`, `BIGip*`) and Cloudflare
(`__cf_bm`) bot-defense cookies, so a datacenter runner IP may be challenged where a residential one is not — hence `probe()`, which the watcher exposes as `--probe` (Stream S's
pre-activation runner check).

This module only FETCHES + CANONICALIZES. Snapshotting, alerting and the private
Drive mirror are ride_docs_watcher.py. It never logs in, never touches a
credential, and never goes through `egle_doc_parser` (a structured-API source,
same posture as Streams E/F/H/I/J).

Pacing: one request at a time, at least `MIN_INTERVAL` seconds apart (EGLE's
public app; be a polite client). Tests set MIN_INTERVAL = 0.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime

import requests

BASE = "https://www.egle.state.mi.us/RIDE"
SHELL_URL = f"{BASE}/inventory-of-facilities/facilities"
SETTINGS_URL = f"{BASE}/Home/GetAppSettings"
FACILITIES_URL = f"{BASE}/api/Location/GetFacilitiesTable"
FILES_URL = f"{BASE}/api/ContentManagerFile/GetContentManagerFilesForLocationFilesTable"
CONTENT_URL = f"{BASE}/api/ContentManagerFile/GetFileContents"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

MIN_INTERVAL = 1.0          # seconds between requests (module-level so tests can zero it)
PAGE_ROWS = 500             # the API honors up to at least 1000; 500 is ample per location
MAX_PAGES = 20              # a location with > PAGE_ROWS * MAX_PAGES files is a structural surprise
_DOWNLOAD_CHUNK = 1 << 20

# RIDE stores an unknown document date as 1900-01-31 (seen on 5 real records).
# That is a placeholder, not a date — canonicalized to "" so it is never shown
# or hashed as if it were a real 1900 filing.
_PLACEHOLDER_YEAR = "1900"

# The canonical per-file record (every field the watcher diffs and displays).
FILE_FIELDS = ("uri", "title", "file_name", "index_type", "extension", "size",
               "date_of_document", "created_on", "folder_number", "doc_number")


class RideDocsFetchError(RuntimeError):
    """The service couldn't be fetched cleanly (network error, non-200 incl. the
    405 a missing cookie warm-up or a bot challenge returns, a body that isn't
    JSON). TRANSIENT — the watcher skips-and-warns for an already-baselined
    location rather than diffing, so a blip never fires a spurious alert. (A
    location with no baseline yet treats it as loud — an activation-time block
    must surface, not silently no-op.)"""


class RideDocsParseError(RuntimeError):
    """The response fetched and parsed as JSON but its STRUCTURE is wrong — a
    missing `data`/`totalRows`, a record with no `uri`, a duplicate `uri` (the
    diff key would be unsound), or more files than the page cap allows. Almost
    certainly EGLE reorganized RIDE. ALWAYS loud, never gated on baseline
    status (a structural break persists across runs; the ADR 014 silent-stall
    class). Same split as ride_client/mmd_client."""


class RideDocsTooLargeError(RuntimeError):
    """A file exceeds the configured download cap. NOT transient and not a
    structural break — the watcher records it as skipped so it is never retried."""


_last_request = 0.0


def _pace() -> None:
    """Sleep so consecutive requests are >= MIN_INTERVAL apart."""
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _request(session: requests.Session, method: str, url: str, timeout: int, **kw):
    _pace()
    try:
        return session.request(method, url, timeout=timeout, **kw)
    except requests.RequestException as e:
        raise RideDocsFetchError(f"{method} {url} failed: {e}") from e


def _json_payload(r, what: str) -> dict:
    if r.status_code != 200:
        raise RideDocsFetchError(
            f"{what} returned HTTP {r.status_code} (405 = missing cookie warm-up or a "
            "bot challenge on this IP)")
    try:
        payload = r.json()
    except ValueError as e:
        raise RideDocsFetchError(f"{what} did not return JSON ({len(r.content)} bytes)") from e
    if not isinstance(payload, dict):
        raise RideDocsFetchError(f"{what} returned non-object JSON")
    return payload


def open_session(timeout: int = 60) -> requests.Session:
    """A warmed anonymous session. GET the SPA shell, then GetAppSettings — the
    latter must return the app's own JSON with an `applicationUser`, else the
    warm-up did not take (bot wall / redirect) and we raise RideDocsFetchError.
    No credentials of any kind are sent."""
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json, text/plain, */*"})
    r = _request(s, "GET", SHELL_URL, timeout)
    if r.status_code != 200:
        raise RideDocsFetchError(f"GET {SHELL_URL} returned HTTP {r.status_code}")
    settings = _json_payload(_request(s, "GET", SETTINGS_URL, timeout), "GetAppSettings")
    if not isinstance(settings.get("applicationUser"), dict):
        raise RideDocsFetchError("GetAppSettings has no applicationUser — warm-up did not take")
    return s


def _post_json(session: requests.Session, url: str, body: dict, what: str,
               timeout: int) -> dict:
    r = _request(session, "POST", url, timeout, json=body,
                 headers={"Content-Type": "application/json"})
    return _json_payload(r, what)


def resolve_location(session: requests.Session, program_num: str,
                     timeout: int = 60) -> dict | None:
    """Program number (e.g. Part 201 site '81000004') -> {location_id,
    program_num, name, location_type}, or None if RIDE's inventory has no exact
    match. Exact match on `programNum` (the server filter is a contains-search)."""
    payload = _post_json(session, FACILITIES_URL, {
        "rowCount": 50, "pageNumber": 1, "sortColumnName": "programNum", "sortDirection": 2,
        "filter": [{"columnName": "myFacilitiesOnly", "filterMode": 1, "text": "false"},
                   {"columnName": "programNum", "filterMode": 0, "text": str(program_num)}],
    }, "GetFacilitiesTable", timeout)
    data = payload.get("data")
    if not isinstance(data, list):
        raise RideDocsParseError("GetFacilitiesTable response has no 'data' list — RIDE may have changed")
    for rec in data:
        if not isinstance(rec, dict):
            raise RideDocsParseError("GetFacilitiesTable record is not an object")
        if str(rec.get("programNum", "")).strip() == str(program_num):
            try:
                location_id = int(rec["locationId"])
            except (KeyError, TypeError, ValueError) as e:
                raise RideDocsParseError("GetFacilitiesTable record has no integer locationId") from e
            return {
                "location_id": location_id,
                "program_num": str(rec["programNum"]).strip(),
                "name": str(rec.get("name") or rec.get("displayName") or "").strip(),
                "location_type": str((rec.get("locationType") or {}).get("name", "")).strip(),
            }
    return None


def fetch_location_files(session: requests.Session, location_id: int,
                         timeout: int = 60) -> list[dict]:
    """Every file record RIDE lists for a location, as raw dicts. Sorted by the
    unique `uri` server-side, so paging is stable (a sort on dateOfDocument has
    ties and can repeat/skip rows across pages). Raises RideDocsParseError on a
    missing `data`/`totalRows`, a record without `uri`, a duplicate `uri`, or a
    location bigger than the page cap."""
    records: list[dict] = []
    total = None
    for page in range(1, MAX_PAGES + 1):
        payload = _post_json(session, FILES_URL, {
            "rowCount": PAGE_ROWS, "pageNumber": page,
            "sortColumnName": "uri", "sortDirection": 1,
            "filter": [{"columnName": "locationId", "filterMode": 0, "text": str(location_id)}],
        }, "GetContentManagerFilesForLocationFilesTable", timeout)
        data, total = payload.get("data"), payload.get("totalRows")
        if not isinstance(data, list) or not isinstance(total, int):
            raise RideDocsParseError(
                "file-list response lacks 'data' list / integer 'totalRows' — RIDE may have changed")
        records.extend(data)
        if len(records) >= total or not data:
            break
    else:
        raise RideDocsParseError(
            f"location {location_id} lists more than {PAGE_ROWS * MAX_PAGES} files — page cap hit")
    if len(records) != total:
        raise RideDocsParseError(
            f"location {location_id}: fetched {len(records)} record(s) but totalRows={total}")
    uris = []
    for rec in records:
        if not isinstance(rec, dict):
            raise RideDocsParseError(f"location {location_id}: a file record is not an object")
        if rec.get("uri") in (None, ""):
            raise RideDocsParseError(f"location {location_id}: a file record has no 'uri'")
        if not re.fullmatch(r"[0-9]+", str(rec["uri"]).strip()):
            raise RideDocsParseError(f"location {location_id}: a file 'uri' is not numeric")
        uris.append(str(rec["uri"]))
    if len(set(uris)) != len(uris):
        raise RideDocsParseError(
            f"location {location_id}: duplicate file 'uri' values — the diff key is unsound")
    return records


def _iso_date(value) -> str:
    """'2016-07-11T00:00:00.000' -> '2016-07-11'; the 1900 placeholder or an
    unparseable value -> '' (an unparseable NON-empty value falls back to its
    str so a service-side type change shows up as a visible diff, never a crash)."""
    if value in (None, ""):
        return ""
    s = str(value)
    try:
        d = datetime.fromisoformat(s[:10]).date().isoformat()
    except ValueError:
        return s
    return "" if d.startswith(_PLACEHOLDER_YEAR) else d


def file_view(rec: dict) -> dict:
    """The canonical, hash-stable view of one raw file record: exactly
    FILE_FIELDS, every value a normalized string. `uri` is the durable key.
    `created_on` is the date the file was ADDED to RIDE (contentManagerCreatedOn),
    which is different from `date_of_document` — RRD is digitizing a backlog, so
    a 1981-2016 document can be newly added in 2025 without any new activity."""
    folder = rec.get("contentManagerFolderUriNavigation") or {}
    try:
        size = int(rec.get("documentSize") or 0)
    except (TypeError, ValueError):
        size = 0
    return {
        "uri": str(rec.get("uri", "")).strip(),
        "title": str(rec.get("title") or "").strip(),
        "file_name": str(rec.get("fileName") or "").strip(),
        "index_type": str(rec.get("indexType") or "").strip(),
        "extension": str(rec.get("extension") or "").strip().upper(),
        "size": str(size),
        "date_of_document": _iso_date(rec.get("dateOfDocument")),
        "created_on": _iso_date(rec.get("contentManagerCreatedOn")),
        "folder_number": str(folder.get("contentManagerNumber") or "").strip(),
        "doc_number": str(rec.get("contentManagerNumber") or "").strip(),
    }


def record_hash(view: dict) -> str:
    """A stable short hash of one canonical file view."""
    blob = "\x1f".join(view[f] for f in FILE_FIELDS).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def safe_filename(uri: str, extension: str, hash8: str = "") -> str:
    """A Drive-safe, traversal-safe, TITLE-FREE mirror filename: '<uri>[_<hash8>].<ext>'.
    Deliberately carries no part of the RIDE title: the name is embedded in Drive
    `files().list` queries, and googleapiclient prints the request URL (query
    included) in its errors and retry warnings — which would put a resident's name
    or address into the world-readable Actions log. The title lives only in the
    private Sheet row, keyed by the same uri. Only digits, hex and a short
    alphanumeric extension survive, so nothing can traverse or start with a dot."""
    head = re.sub(r"[^0-9]", "", str(uri)) or "0"
    hex8 = re.sub(r"[^0-9a-f]", "", hash8 or "")
    mid = f"_{hex8}" if hex8 else ""
    ext = re.sub(r"[^A-Za-z0-9]", "", extension).lower()[:8] or "bin"
    return f"{head}{mid}.{ext}"


def download_file(session: requests.Session, uri: str, dest_path: str,
                  max_bytes: int, expect_pdf: bool = True,
                  timeout: int = 300, deadline_s: float = 900) -> dict:
    """Stream one file to `dest_path`; returns {size, sha256, md5, content_type}.
    Raises RideDocsTooLargeError (over `max_bytes`, by header or while streaming),
    RideDocsFetchError (non-200, a JSON/HTML error body, an empty body, or — for
    a PDF — a body that isn't %PDF-). A failed download never leaves a partial
    file behind. The response is never buffered whole (files reach 200+ MB).
    `timeout` is per read; `deadline_s` bounds this ONE download against a server
    that trickles bytes (the watcher also caps its whole mirror pass)."""
    if not re.fullmatch(r"[0-9]+", str(uri).strip()):          # ASCII digits only
        raise RideDocsFetchError(f"refusing non-numeric file uri {uri!r}")
    _pace()
    started = time.monotonic()
    try:
        r = session.post(CONTENT_URL, json={"uri": int(uri)}, stream=True, timeout=timeout,
                         headers={"Content-Type": "application/json"})
    except requests.RequestException as e:
        raise RideDocsFetchError(f"POST {CONTENT_URL} failed: {e}") from e
    try:
        if r.status_code != 200:
            raise RideDocsFetchError(f"GetFileContents uri={uri} returned HTTP {r.status_code}")
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype in ("application/json", "text/html", "text/plain"):
            raise RideDocsFetchError(
                f"GetFileContents uri={uri} returned {ctype}, not file bytes (session expired?)")
        declared = r.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise RideDocsTooLargeError(f"uri {uri}: {int(declared)} bytes > cap {max_bytes}")
        sha, md5, size, head = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0, b""
        try:
            with open(dest_path, "wb") as fh:
                for chunk in r.iter_content(_DOWNLOAD_CHUNK):
                    if not chunk:
                        continue
                    if not head:
                        head = chunk[:8]
                    size += len(chunk)
                    if time.monotonic() - started > deadline_s:
                        raise RideDocsFetchError(f"uri {uri}: download exceeded the {deadline_s:.0f} s deadline")
                    if size > max_bytes:
                        raise RideDocsTooLargeError(f"uri {uri}: exceeded cap {max_bytes} bytes while streaming")
                    sha.update(chunk)
                    md5.update(chunk)
                    fh.write(chunk)
            if size == 0:
                raise RideDocsFetchError(f"GetFileContents uri={uri} returned an empty body")
            if expect_pdf and not head.startswith(b"%PDF-"):
                # No body bytes in the message: it can reach the public Actions log.
                raise RideDocsFetchError(f"uri {uri}: body is not a PDF (no %PDF- signature)")
        except (requests.RequestException, OSError) as e:
            raise RideDocsFetchError(f"streaming uri={uri} failed: {e}") from e
    except BaseException:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise
    finally:
        r.close()
    return {"size": size, "sha256": sha.hexdigest(), "md5": md5.hexdigest(), "content_type": ctype}


def probe(program_nums, timeout: int = 60) -> list[dict]:
    """The pre-activation runner check: open a session, resolve each program
    number, list its files. Returns [{program_num, location_id, name, n_files}]
    (location_id None when the inventory has no such program). Reads only — the
    watcher's `--probe` runs this regardless of the enabled flag and writes
    nothing (no Sheet, no Drive, no email)."""
    session = open_session(timeout)
    out = []
    for pn in program_nums:
        loc = resolve_location(session, pn, timeout)
        if loc is None:
            out.append({"program_num": str(pn), "location_id": None, "name": "", "n_files": 0})
            continue
        files = fetch_location_files(session, loc["location_id"], timeout)
        out.append({"program_num": str(pn), "location_id": loc["location_id"],
                    "name": loc["name"], "n_files": len(files)})
    return out
