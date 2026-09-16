"""
mmpc_client.py — Washtenaw County MMPC document fetch, via CivicClerk's public
OData v4 API (ADR 010, Stream D).

Discovered 2026-07-10 by inspecting the portal's own network traffic (the
public-facing `washtenawcomi.portal.civicclerk.com` site is a JS SPA with no
server-rendered document links — `mmpc_client` talks to the API it calls, not
the SPA). Two endpoints, both unauthenticated:

  - GET {_BASE}/Events?$filter=categoryId eq {category_id}
    Returns every MMPC event (categoryId 72 = Materials Management Planning
    Committee). Each event carries a `publishedFiles` array — one entry per
    Agenda/Minutes/Other document, e.g.:
      {"fileId": 9107, "type": "Minutes", "name": "...", "publishOn": "...",
       "sort": 3, "fileType": 4}
    This is the enumeration mechanism: no ID-guessing, no HTML scraping.
    OData paginates large result sets via "@odata.nextLink" — followed here.

  - GET {_BASE}/Meetings/GetMeetingFileStream(fileId={id},plainText=false)
    Returns the raw PDF bytes for that fileId directly (Content-Type:
    application/pdf). Verified live against fileId 9107 (Feb 11 2026 minutes).

This module only FETCHES + FLATTENS. Diffing against already-archived fileIds
and uploading to Drive is mmpc_archiver.py.
"""
from __future__ import annotations

import time
import urllib.parse
from typing import Iterator

import requests

_BASE = "https://washtenawcomi.api.civicclerk.com/v1"
_UA = "Mozilla/5.0 (compatible; arbor-hills-monitor/1.0)"


class MMPCFetchError(RuntimeError):
    """An MMPC API call failed or returned something unparseable. Treated as
    TRANSIENT by the archiver (skip-and-warn this run, retry next), never as
    'zero documents' — a failed fetch must not diff as if nothing were ever
    published (mirrors WDSFetchError's contract in wds_client.py)."""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    return s


# CivicClerk's API intermittently read-times-out mid-pagination: observed
# 2026-09-10 and 2026-09-14, a 30s timeout on a deep $skiptoken page failed the
# whole civicclerk-archive run even though the very next scheduled run succeeded
# with no change. Because a failed fetch RAISES (MMPCFetchError) rather than
# returning a partial list — the "never mistake a truncated fetch for zero
# documents" contract every caller here depends on — a single transient blip
# aborts an entire mirror/watch. Retrying the GET a few times with exponential
# backoff absorbs the momentary blip while a genuinely sustained outage still
# surfaces (loudly) once the retries are exhausted.
_GET_TIMEOUT = 30          # seconds per attempt (unchanged from the original)
_GET_RETRIES = 3           # retries AFTER the first attempt -> up to 4 attempts
_GET_BACKOFF_BASE = 1.0    # seconds; waits grow 1s, 2s, 4s between attempts


def _get_with_retry(session, url, *, timeout=_GET_TIMEOUT, retries=_GET_RETRIES,
                    sleep=time.sleep) -> requests.Response:
    """GET `url`, retrying on a TRANSIENT network error (requests.RequestException
    — read timeout, dropped connection) up to `retries` times with exponential
    backoff, then returning the requests.Response. If every attempt fails the
    LAST exception is re-raised unchanged, so each caller wraps it in
    MMPCFetchError exactly as before — the retry is invisible to the failure
    contract, and a genuinely sustained outage still raises.

    Deliberately narrow: only the network-level EXCEPTION is retried. A response
    that arrives carrying a 4xx/5xx STATUS is returned as-is for the caller's own
    `status_code != 200 -> MMPCFetchError` check (unchanged) — a 5xx is a
    server-side condition distinct from the read-timeout this addresses, and
    retrying it is out of scope. `sleep` is injectable purely so tests stay
    hermetic (a no-op); production always uses time.sleep."""
    for attempt in range(retries + 1):
        try:
            return session.get(url, timeout=timeout)
        except requests.RequestException:
            if attempt >= retries:
                raise  # exhausted — re-raise the last failure for the caller to wrap
            sleep(_GET_BACKOFF_BASE * (2 ** attempt))
    # Unreachable: retries >= 0, so the loop always returns or raises above.
    raise AssertionError("unreachable")  # pragma: no cover


def fetch_mmpc_files(session: requests.Session, category_id: int = 72) -> list[dict]:
    """Return one flat dict per published document across every MMPC event,
    newest first isn't guaranteed (API order is whatever the OData default is;
    callers sort if they care). Each dict:
      {file_id, type, name, publish_on, event_id, event_date, event_name}

    Raises MMPCFetchError on any HTTP/JSON failure — including partway through
    paging, so a truncated page list is never mistaken for 'that's all of them'
    (the archiver's diff would otherwise treat a genuinely-missing later page as
    'those files no longer exist', which is never true for a public archive)."""
    quoted = urllib.parse.quote(f"categoryId eq {category_id}")
    url = f"{_BASE}/Events?$filter={quoted}"
    out: list[dict] = []
    seen_urls = set()  # nextLink loop guard — a repeated link would spin forever
    while url:
        if url in seen_urls:
            raise MMPCFetchError(f"@odata.nextLink loop detected at {url}")
        seen_urls.add(url)
        try:
            r = _get_with_retry(session, url)
        except requests.RequestException as e:
            raise MMPCFetchError(f"GET {url} failed: {e}") from e
        if r.status_code != 200:
            raise MMPCFetchError(f"GET {url} -> HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError as e:
            raise MMPCFetchError(f"GET {url} -> unparseable JSON: {e}") from e

        for ev in payload.get("value", []):
            event_id = ev.get("id")
            event_date = ev.get("eventDate", "")
            event_name = ev.get("eventName", "")
            for f in ev.get("publishedFiles") or []:
                file_id = f.get("fileId")
                if file_id is None:
                    continue
                out.append({
                    "file_id": file_id,
                    "type": f.get("type", ""),
                    "name": f.get("name", ""),
                    "publish_on": f.get("publishOn", ""),
                    "event_id": event_id,
                    "event_date": event_date,
                    "event_name": event_name,
                })
        url = payload.get("@odata.nextLink") or None

    return out


def fetch_category_events(session: requests.Session, category_id: int) -> list[dict]:
    """Return every RAW event dict (unflattened — full `publishedFiles`, plus
    `eventName`/`eventDate`/`isPublished`/`eventNotice`/`isDeleted`, i.e. everything
    `civicclerk_watcher.event_snapshot()` needs) for one category. Same endpoint,
    pagination, and error handling as fetch_mmpc_files() above — deliberately a
    SEPARATE loop rather than a shared refactor, so this addition can't regress
    fetch_mmpc_files()'s tested behaviour (Mirror D / mmpc_archiver.py depend on it).

    Used by civicclerk_watcher.py's category_id AUTO-DISCOVER group mode (ADR 015's
    addendum) — unlike a hand-picked `events:` list, this lets a watch cover a
    category whose future event IDs don't exist yet on the portal (confirmed live
    for categoryId 68, the Board of Public Works/DPA: no event stub exists past the
    meeting that already happened, so a hand-picked list can never see next month's
    meeting until this enumerates the category fresh each run) — and by the one-time
    12-month historical keyword backfill, which needs full event objects across all
    four watched categories, not just fileIds.

    Raises MMPCFetchError on any HTTP/JSON failure, including partway through
    paging — same "never mistake a truncated fetch for an empty category" contract
    as fetch_mmpc_files()."""
    quoted = urllib.parse.quote(f"categoryId eq {category_id}")
    url = f"{_BASE}/Events?$filter={quoted}"
    out: list[dict] = []
    seen_urls = set()
    while url:
        if url in seen_urls:
            raise MMPCFetchError(f"@odata.nextLink loop detected at {url}")
        seen_urls.add(url)
        try:
            r = _get_with_retry(session, url)
        except requests.RequestException as e:
            raise MMPCFetchError(f"GET {url} failed: {e}") from e
        if r.status_code != 200:
            raise MMPCFetchError(f"GET {url} -> HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError as e:
            raise MMPCFetchError(f"GET {url} -> unparseable JSON: {e}") from e
        out.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink") or None
    return out


def fetch_event(session: requests.Session, event_id) -> dict | None:
    """Fetch ONE CivicClerk event by its numeric id, returning the raw event dict
    (including its `publishedFiles` array), or None when the API returns HTTP 200
    with an empty result set — i.e. the event genuinely isn't there (removed,
    cancelled, or a bad id).

    That None is a MEANINGFUL 'not found', deliberately distinct from an HTTP/JSON
    failure, which raises MMPCFetchError. A caller (civicclerk_watcher) treats the
    error as TRANSIENT (skip-and-warn, never 'the meeting vanished') but treats a
    200-empty for a previously-seen event as a real change worth alerting on.

    Same OData host + session as the Mirror-D enumeration above — this just asks
    for a single event by id instead of a whole category. Used to watch specific
    MMPC / Board-of-Commissioners meetings for changes (see ADR 015)."""
    quoted = urllib.parse.quote(f"id eq {event_id}")
    url = f"{_BASE}/Events?$filter={quoted}"
    try:
        r = _get_with_retry(session, url)
    except requests.RequestException as e:
        raise MMPCFetchError(f"GET event {event_id} failed: {e}") from e
    if r.status_code != 200:
        raise MMPCFetchError(f"GET event {event_id} -> HTTP {r.status_code}")
    try:
        payload = r.json()
    except ValueError as e:
        raise MMPCFetchError(f"GET event {event_id} -> unparseable JSON: {e}") from e
    values = payload.get("value") or []
    return values[0] if values else None


def download_file_bytes(session: requests.Session, file_id, timeout: int = 60) -> bytes:
    """Fetch one document's raw PDF bytes IN MEMORY (no disk write) via the same
    GetMeetingFileStream endpoint download_file() uses. Raises MMPCFetchError on
    HTTP error or an empty/non-PDF body — the %PDF magic-byte check only confirms
    the header; civicclerk_watcher.extract_pdf_text() catches a truncated/corrupt
    body downstream. Used by the keyword-scan feature (ADR 015 addendum), which
    only needs the text layer, never a Drive upload — mmpc_archiver.py's Mirror D
    keeps using download_file() below, unchanged."""
    url = f"{_BASE}/Meetings/GetMeetingFileStream(fileId={file_id},plainText=false)"
    try:
        r = session.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise MMPCFetchError(f"download fileId={file_id} failed: {e}") from e
    if r.status_code != 200:
        raise MMPCFetchError(f"download fileId={file_id} -> HTTP {r.status_code}")
    content = r.content
    if not content or not content.startswith(b"%PDF"):
        raise MMPCFetchError(f"download fileId={file_id} -> not a PDF ({len(content)} bytes)")
    return content


def download_file(session: requests.Session, file_id, dest_path: str, timeout: int = 60) -> str:
    """Download one document's PDF bytes to dest_path. Returns dest_path; raises
    MMPCFetchError on HTTP error or an empty/non-PDF body."""
    content = download_file_bytes(session, file_id, timeout=timeout)
    with open(dest_path, "wb") as out:
        out.write(content)
    return dest_path


def iter_new_files(files: list[dict], already_archived: set) -> Iterator[dict]:
    """Pure filter: files whose file_id isn't in already_archived. Compares as
    strings — file_id is an int from the JSON payload, but Sheets round-trips
    cell values as strings, so already_archived (from
    sheet_writer.mmpc_archived_file_ids()) is str-typed; normalize here rather
    than pushing int()/str() coercion onto every caller. Split out from
    fetch_mmpc_files() so the diff logic is unit-testable without any network
    call."""
    for f in files:
        if str(f["file_id"]) not in already_archived:
            yield f
