"""
nsite_client.py — minimal client for EGLE's nSITE document API.

Ported from michigan-egle-database-auto-scraper/EGLE-AQD-database-autoscraper.py,
stripped to the three things this monitor needs:
  - make_session()        — session with the required cookie
  - fetch_site_documents()— full document list for one facility
  - download_pdf()        — fetch one document's PDF bytes to disk

Deliberately DOES NOT inherit the scraper's `doc_date == today` filter — that was
for a daily all-facilities sweep. Backfill needs the full history; the watcher
filters by checkpoint date itself. We also dropped the pandas / CSV-merge
machinery. fetch_all_documents() loops the facilities configured in config.yml
and tags each doc with its facility (the multi-facility design, ADR 008).
"""
from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime, date
from typing import Optional

import requests

NSITE_BASE = "https://mienviro.michigan.gov"
SETTINGS_URL = f"{NSITE_BASE}/nsite/api/settings/getWslSettings"
DOCS_ENDPOINT = (
    f"{NSITE_BASE}/nsite/ss/api/nsite-explorer/default-mode"
    "/profiles/4-documents/1-documents"
)
DOWNLOAD_BASE = f"{NSITE_BASE}/ncore/downloadpdf"


def make_session() -> requests.Session:
    """Return a requests.Session primed with a valid nSITE cookie."""
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "Mozilla/5.0 (compatible; arbor-hills-monitor/1.0)"}
    )
    s.get(SETTINGS_URL, timeout=30)
    return s


def _parse_doc_url(html_anchor: str) -> str:
    """Extract href from '<a href="URL">Download</a>' string."""
    m = re.search(r'href="([^"]+)"', html_anchor or "")
    return m.group(1) if m else (html_anchor or "")


def _normalize(raw: dict) -> dict:
    """Convert a raw nSITE document dict into the fields the pipeline uses."""
    descr = raw.get("docMgmtDocDescr", "")
    srctype = raw.get("docMgmtSourcetype", descr)
    date_str = raw.get("docMgmtDocRvcdCreatedDate", "")
    doc_id = str(raw.get("docMgmtDocMgmtId", ""))

    parsed_date: Optional[date] = None
    if date_str:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(date_str[: len(fmt) + 2], fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            # Last resort: take the leading YYYY-MM-DD if present.
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
            if m:
                parsed_date = date(int(m[1]), int(m[2]), int(m[3]))

    raw_url = _parse_doc_url(raw.get("docMgmtDocurl", ""))
    doc_url = raw_url or f"{DOWNLOAD_BASE}/{doc_id}"
    if doc_url.startswith("/"):
        doc_url = NSITE_BASE + doc_url

    return {
        "doc_id": doc_id,
        "date_filed": parsed_date.isoformat() if parsed_date else "",
        "date_obj": parsed_date,
        "document_name": descr or srctype or f"doc-{doc_id}",
        "type_name": descr or srctype,
        "doc_url": doc_url,
        "category": raw.get("docMgmtCategory", ""),
    }


def fetch_site_documents(session: requests.Session, nsite_id: str) -> list[dict]:
    """Return the full list of normalized documents for one facility (no date
    filter). Returns [] on persistent error."""
    query_params = urllib.parse.quote('{"filter":[{"id":"' + str(nsite_id) + '"}]}')
    url = (
        f"{DOCS_ENDPOINT}"
        f"?responseContentType=application/json"
        f"&includeMetadataInResponse=true"
        f"&loadChildren=true"
        f"&queryParams={query_params}"
        f"&filterString="
    )
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results/detail/{nsite_id}/Documents"

    for attempt in range(3):
        try:
            r = session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json"},
                timeout=30,
            )
            data = r.json()
            raw_docs = data.get("queryResults", [])
            return [_normalize(d) for d in raw_docs if d.get("docMgmtDocMgmtId")]
        except Exception:
            if attempt == 2:
                return []
            time.sleep(2 ** attempt)
    return []


def fetch_all_documents(session: requests.Session, cfg: dict) -> list[dict]:
    """Fetch and concatenate the document lists for every facility in
    cfg["facilities"], tagging each doc with facility_srn / facility_name.

    nSITE doc_ids are globally unique across these facilities (verified 0 pairwise
    overlap), so the combined list safely shares one Sheet + one _state tab with
    no composite key (ADR 008). A facility that returns [] (transient error /
    empty record) simply contributes nothing; it never aborts the others.
    """
    docs: list[dict] = []
    for f in cfg["facilities"]:
        for d in fetch_site_documents(session, f["id"]):
            d["facility_srn"] = f["srn"]
            d["facility_name"] = f["name"]
            docs.append(d)
    return docs


def download_pdf(session: requests.Session, doc: dict, dest_path: str, timeout: int = 120) -> str:
    """Download one document's PDF to dest_path. Returns dest_path. Raises on
    HTTP error or empty body."""
    url = doc["doc_url"]
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results"
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = session.get(url, headers={"Referer": referer}, timeout=timeout)
            r.raise_for_status()
            if not r.content:
                raise RuntimeError("empty response body")
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return dest_path
        except Exception as e:  # noqa: BLE001
            last_exc = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed for {url}: {last_exc}")
