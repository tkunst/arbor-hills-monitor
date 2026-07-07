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
# Native-file endpoint: serves the document's ORIGINAL bytes (legacy .doc, zips,
# images) where downloadpdf returns HTTP 400 "PDF content could not be found"
# for any non-PDF source. Used for stub links to docs the parser can't ingest.
DOWNLOAD_FILE_BASE = f"{NSITE_BASE}/ncore/downloadfile"


def native_download_url(doc_id: str) -> str:
    """The downloadfile URL for a doc_id — the original bytes, not a PDF render."""
    return f"{DOWNLOAD_FILE_BASE}/{doc_id}"


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


def _looks_like_pdf(body: bytes) -> bool:
    """A cheap 'is this a PDF' check. Readers tolerate junk before the %PDF
    header, so scan the first 1 KB rather than requiring it at byte 0."""
    return b"%PDF" in body[:1024]


def download_pdf(session: requests.Session, doc: dict, dest_path: str, timeout: int = 120) -> str:
    """Download one document to dest_path as a PDF the parser can open. Returns
    dest_path; raises on HTTP error, empty body, or if no source yields a PDF.

    nSITE's per-record link (`doc_url`) usually points at a PDF, but for some
    documents it points at the ORIGINAL file — an Outlook .msg, an image, an
    nForm submission — which PyMuPDF cannot open. When the record's own link is
    not a PDF, fall back to nSITE's `downloadpdf/<id>` render endpoint, which
    rasterizes the source into a PDF. (Legacy Word .doc has no render — that
    endpoint 400s — so those still fail here and accrue a poison strike, which is
    correct: the monitor can't read them without a .doc converter. See ADR / the
    2026-07-07 handoff.)"""
    doc_id = doc["doc_id"]
    primary = doc["doc_url"]
    render = f"{DOWNLOAD_BASE}/{doc_id}"
    # The record's own link first, then the render endpoint (unless identical).
    urls = [primary] + ([render] if render != primary else [])
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results"
    last_exc: Optional[Exception] = None
    for url in urls:
        for attempt in range(3):
            try:
                r = session.get(url, headers={"Referer": referer}, timeout=timeout)
                r.raise_for_status()
                if not r.content:
                    raise RuntimeError("empty response body")
            except Exception as e:  # noqa: BLE001 — transient HTTP/network: retry this url
                last_exc = e
                time.sleep(2 ** attempt)
                continue
            if _looks_like_pdf(r.content):
                with open(dest_path, "wb") as f:
                    f.write(r.content)
                return dest_path
            # A valid response that isn't a PDF (the .msg / image / nForm case).
            # Retrying the same URL won't help — fall through to the next source.
            last_exc = RuntimeError(f"non-PDF response from {url} (starts {r.content[:8]!r})")
            break
    raise RuntimeError(f"download failed for doc {doc_id}: {last_exc}")
