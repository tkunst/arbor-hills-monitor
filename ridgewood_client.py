"""
ridgewood_client.py — Stream G: fetch + parse Barr Engineering's monthly Ridge
Wood Elementary H2S data reports. See docs/decisions/016-ridgewood-h2s-stream-g.md.

GFL runs an H2S air monitor at Ridge Wood Elementary School, installed December
2020 under a U.S. EPA agreement and operated by Barr
Engineering (the same consultant behind the GFL perimeter ArcGIS feed, Stream E).
Barr posts a monthly, QA'd, born-digital PDF data report per month to a public
page. This is a DOCUMENT-archive source (the "new PDF appears -> mirror + extract"
shape of Mirror D / mmpc_archiver), NOT a live numeric feed like Stream E.

This module only FETCHES + PARSES (pure where it can be); diffing against the
already-archived month-set, mirroring to Drive, writing Measurements, and alerting
are ridgewood_archiver.py. Stdlib for the page/PDF fetch (same posture as
pfas_client) + fitz for PDF text extraction (the repo's existing PDF dependency;
egle_doc_parser.py is deliberately left untouched so the Decode base stays
domain-agnostic — we route the bytes through fitz here, not through parse_document).

TWO load-bearing design choices (both from the feasibility spike, 2026-07-14, and
the fail-safe ruling in ADR 016):

  1. SCRAPE the report links off the page; NEVER construct a URL. The filename
     carries an unpredictable `_NNNN` cache-buster (some months have none) and
     spaces (URL-encode). The reliable key is the `YYYY-MM` prefix.

  2. The exceedance classifier is FAIL-SAFE and FOOTNOTE-SAFE. Every published
     report to date is an all-clear, so we have no specimen of an exceedance
     report's wording — and the action-level FOOTNOTES literally contain the
     strings "exceeds 750 ppb" / "exceeds 72 ppb" as definitions. So we never
     positive-match exceedance wording. Instead:
       - 24-hr / 72 ppb: alert iff any NUMERIC daily table value >= threshold
         (wording-independent; the daily table only ever holds 24-hr averages).
       - 15-min / 750 ppb: only appears in prose. We positively detect the known
         all-clear phrase ("No notifications required to be sent ...") in the
         footnote-stripped body; its ABSENCE -> alert for human review.
       - A parse that yields zero daily values -> alert (a broken parse / scanned
         image must never read as "clean month").
     Default to alerting when the recognizable all-clear is absent. A false alert
     is cheap (Trisha reads it); a missed exceedance defeats the monitor's job.
"""
from __future__ import annotations

import hashlib
import html as _html
import http.cookiejar
import re
import urllib.parse
import urllib.request

import fitz  # pymupdf — the repo's existing PDF text-layer dependency

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# The public report list is ~40 KB; a much smaller body is a bot wall / error page.
_MIN_PAGE_BYTES = 2000

DEFAULT_PAGE_URL = "https://arborhillsmonitoring.com/Home/Ridgewood"
# Relative /Files/... links are absolutized against this host (the PDFs live on the
# www. host; the report list page is served from the bare host).
DEFAULT_BASE_URL = "https://www.arborhillsmonitoring.com"

# Published action levels (also the config defaults; see ADR 016 + config.yml).
DEFAULT_H2S_24H_PPB = 72     # 24-hour average (Michigan EGLE ITSL; Barr's own level)
DEFAULT_H2S_15MIN_PPB = 750  # 15-minute average (USEPA Acute Exposure Guideline)

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# A Files/*.pdf link whose filename starts with the canonical `YYYY-MM_` prefix.
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})[_ ]")
_PDF_LINK_RE = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"', re.I)

# Daily-table row: a bare `M/D/YY` (or `M/D/YYYY`) date line; the immediately
# following non-empty line is its 24-hr average value token. M/D/YY dates appear
# ONLY in the table (the narrative uses "May 4th", the header "May 1 - 31, 2026"),
# so this anchor scopes the numeric parse to the table without a fragile header match.
_DATE_LINE_RE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*$")
# A value token: "<1" / "< 1" (below reporting resolution) or a number.
_VALUE_TOKEN_RE = re.compile(r"^\s*(<\s*1|\d{1,4}(?:\.\d+)?)\s*$")

# The report's own all-clear determination (section 7a). Its ABSENCE is the
# fail-safe trigger for the 15-minute / prose path.
_ALL_CLEAR_RE = re.compile(r"no\s+notifications?\s+required\s+to\s+be\s+sent", re.I)
# The footnote block (definitions containing "exceeds 750/72 ppb") starts here;
# stripped before the all-clear search so footnote prose can never influence it.
_FOOTNOTE_START_RE = re.compile(r"(?m)^\s*1\s+Notifications\b")


class RidgewoodFetchError(RuntimeError):
    """A page or PDF fetch failed / returned something unusable. TRANSIENT: the
    archiver aborts THIS run rather than diffing against a partial/empty list, so a
    blip or a bot wall is never mistaken for 'no reports were ever published'
    (mirrors PFASFetchError / MMPCFetchError)."""


# ---------------------------------------------------------------------------
# Fetch (network I/O — stdlib, same posture as pfas_client)
# ---------------------------------------------------------------------------

def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA), ("Accept", "text/html,application/pdf")]
    return op


def fetch_page(url: str = DEFAULT_PAGE_URL, timeout: int = 60) -> str:
    """GET the Ridgewood report-list page, return its HTML. Raises
    RidgewoodFetchError on any network/HTTP failure or a suspiciously short body —
    never returns a partial/error body for the caller to mistake for 'no reports'."""
    try:
        r = _opener().open(url, timeout=timeout)
        status = getattr(r, "status", None) or r.getcode()
        body = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001 — network / HTTP -> transient
        raise RidgewoodFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise RidgewoodFetchError(f"GET {url} returned HTTP {status}")
    if len(body) < _MIN_PAGE_BYTES:
        raise RidgewoodFetchError(f"GET {url} body too short ({len(body)} bytes) — bad fetch?")
    return body


def download_report(url: str, dest_path: str, timeout: int = 60) -> str:
    """Download one monthly report's PDF bytes to dest_path. Returns dest_path;
    raises RidgewoodFetchError on HTTP error or an empty/non-PDF body. The URL is
    percent-encoded here (report filenames contain spaces)."""
    safe = urllib.parse.quote(url, safe=":/?&=%_.-")
    try:
        r = _opener().open(safe, timeout=timeout)
        status = getattr(r, "status", None) or r.getcode()
        content = r.read()
    except Exception as e:  # noqa: BLE001
        raise RidgewoodFetchError(f"download {url} failed: {e}") from e
    if status != 200:
        raise RidgewoodFetchError(f"download {url} -> HTTP {status}")
    if not content or not content.startswith(b"%PDF"):
        raise RidgewoodFetchError(f"download {url} -> not a PDF ({len(content)} bytes)")
    with open(dest_path, "wb") as out:
        out.write(content)
    return dest_path


# ---------------------------------------------------------------------------
# Link scrape + month parse (pure)
# ---------------------------------------------------------------------------

def _absolutize(link: str, base_url: str) -> str:
    if link.startswith("http://") or link.startswith("https://"):
        return link
    if link.startswith("//"):
        return "https:" + link
    return base_url.rstrip("/") + "/" + link.lstrip("/")


def parse_month(filename: str) -> str | None:
    """The `YYYY-MM` month from a report filename's canonical prefix, or None.
    None is meaningful: the very first (Dec 2020) report is ALSO linked under an
    old-format name (`Report_...202012...`) that has no `YYYY-MM_` prefix — that
    link is a duplicate of the canonical `2020-12_...` one, so a None simply drops
    a duplicate, never a real month. The archiver logs the None count so a genuine
    format change (every link going None) is visible, not silent."""
    name = urllib.parse.unquote(filename)
    m = _MONTH_RE.match(name)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def scrape_report_links(html_text: str, base_url: str = DEFAULT_BASE_URL) -> tuple[list[dict], list[str]]:
    """Return (reports, unparsed). `reports` is one dict per DISTINCT month
    {month, url, filename}, sorted by month ascending; `unparsed` is the list of
    Files/*.pdf link URLs whose month didn't parse (logged by the caller so a
    drop-to-zero is visible). Pure — no network. First link wins per month (the
    canonical `YYYY-MM_` link precedes the old-format duplicate on the page)."""
    by_month: dict[str, dict] = {}
    unparsed: list[str] = []
    seen_links: set[str] = set()
    for raw in _PDF_LINK_RE.findall(html_text):
        link = _html.unescape(raw)
        if not re.search(r"/Files/.*\.pdf", link, re.I):
            continue
        if link in seen_links:
            continue
        seen_links.add(link)
        filename = link.split("/Files/", 1)[-1]
        month = parse_month(filename)
        url = _absolutize(link, base_url)
        if month is None:
            unparsed.append(url)
            continue
        if month not in by_month:
            by_month[month] = {"month": month, "url": url, "filename": urllib.parse.unquote(filename)}
    reports = [by_month[m] for m in sorted(by_month)]
    return reports, unparsed


def iter_new_reports(reports: list[dict], already_archived: set) -> list[dict]:
    """Reports whose month isn't in already_archived (str-compared — Sheets
    round-trips cells as strings, same idiom as mmpc iter_new_files). Newest first,
    so a capped run always processes the most recent month before older backlog."""
    out = [r for r in reports if str(r["month"]) not in already_archived]
    out.sort(key=lambda r: r["month"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# PDF text extraction (fitz I/O) + provenance
# ---------------------------------------------------------------------------

def extract_text(pdf_path: str) -> tuple[str, int, bool]:
    """(full_text, page_count, has_text_layer) via fitz. has_text_layer is False
    for a scanned/image-only PDF (near-empty text) — the caller then mirrors the
    PDF but skips extraction and alerts for manual/OCR review (fail-safe: the month
    is never dropped). Mirrors egle_doc_parser.classify's text-layer heuristic
    without importing it (that module stays domain-agnostic)."""
    doc = fitz.open(pdf_path)
    try:
        n = len(doc)
        text = "\n".join(doc[i].get_text() for i in range(n))
    finally:
        doc.close()
    has_text = len(text.strip()) >= 100
    return text, n, has_text


def content_hash(pdf_bytes_or_path) -> str:
    """SHA-256 hex of the PDF bytes — provenance (this is evidence; ties into the
    data-sources item-9 provenance requirement). Accepts raw bytes or a path."""
    if isinstance(pdf_bytes_or_path, (bytes, bytearray)):
        data = bytes(pdf_bytes_or_path)
    else:
        with open(pdf_bytes_or_path, "rb") as fh:
            data = fh.read()
    return hashlib.sha256(data).hexdigest()


def report_title(month: str) -> str:
    """A stable, human-readable title for the Ridge Wood Reports tab, derived from
    the month (not the PDF text, so a cosmetic wording change upstream can't break
    it). e.g. '2026-05' -> 'Ridge Wood Elementary H2S 24-hr averages — May 2026'."""
    try:
        y, m = month.split("-")
        return f"Ridge Wood Elementary H2S 24-hr averages — {_MONTH_NAMES[int(m)]} {y}"
    except (ValueError, IndexError):
        return f"Ridge Wood Elementary H2S 24-hr averages — {month}"


# ---------------------------------------------------------------------------
# Daily-table parse + fail-safe classifier (pure — text in, verdict out)
# ---------------------------------------------------------------------------

def strip_footnotes(text: str) -> str:
    """The report body with the trailing action-level footnote block removed. The
    footnotes DEFINE the '750/72 ppb' action levels, so they're excluded before any
    prose check to keep the classifier footnote-safe."""
    m = _FOOTNOTE_START_RE.search(text)
    return text[:m.start()] if m else text


def parse_daily_values(text: str) -> list[tuple[str, str | None]]:
    """Pure. Return [(date, value_token_or_None)] for each daily row in the table.
    A row = a bare `M/D/YY` date line; its value is the immediately-following
    non-empty line IF that line is a value token (`<1` or a number), else None (a
    day with a missing reading). M/D/YY dates occur only in the table, so this
    scopes the numeric parse to the table and never captures a footnote/narrative
    number (72, 750, 9,999, the 0.000-9.999 range, the rounding examples)."""
    lines = text.splitlines()
    out: list[tuple[str, str | None]] = []
    for i, ln in enumerate(lines):
        dm = _DATE_LINE_RE.match(ln)
        if not dm:
            continue
        value: str | None = None
        for j in range(i + 1, min(i + 4, len(lines))):
            s = lines[j].strip()
            if not s:
                continue
            vm = _VALUE_TOKEN_RE.match(s)
            if vm:
                value = vm.group(1).replace(" ", "")  # "< 1" -> "<1"
            break  # only the immediate next non-empty line counts as the value
        out.append((dm.group(1), value))
    return out


def _fmt_num(x: float) -> str:
    return str(int(x)) if x == int(x) else str(x)


def classify_report(text: str, thresholds: dict | None = None) -> dict:
    """Pure fail-safe classifier for one report's text. Returns a verdict dict:

      n_days        int    — daily rows found in the table
      max_numeric   float|None — max numeric 24-hr average (ignores "<1")
      value_token   str    — what to store in Measurements.Value: the numeric max,
                             or "<1" when every reading is below resolution, or ""
                             when nothing parsed (no measurement is written then)
      all_days_below_1 bool
      all_clear     bool   — the report's own all-clear phrase is present
      exceed_24h    bool   — a numeric daily value >= the 24-hr action level
      parse_anomaly bool   — no daily values parsed (scanned image / format change)
      alert         bool   — ANY of the above fail-safe triggers fired
      severity      str    — "alert" | "routine"
      reasons       list[str] — human-readable alert reasons (empty if routine)

    See the module docstring for why this never positive-matches exceedance wording.
    """
    thr = thresholds or {}
    thr_24h = float(thr.get("h2s_24h_ppb", DEFAULT_H2S_24H_PPB))

    days = parse_daily_values(text)
    n_days = len(days)
    tokens = [v for _, v in days if v]
    numerics = [float(v) for v in tokens if not v.startswith("<")]
    below1 = [v for v in tokens if v.startswith("<")]
    max_numeric = max(numerics) if numerics else None

    if max_numeric is not None:
        value_token = _fmt_num(max_numeric)
    elif below1:
        value_token = "<1"
    else:
        value_token = ""

    all_days_below_1 = bool(below1) and not numerics
    exceed_24h = max_numeric is not None and max_numeric >= thr_24h
    parse_anomaly = not tokens  # no usable value parsed (covers n_days == 0)
    all_clear = _ALL_CLEAR_RE.search(strip_footnotes(text)) is not None

    reasons: list[str] = []
    if exceed_24h:
        reasons.append(
            f"24-hr average {value_token} ppb >= {_fmt_num(thr_24h)} ppb action level "
            f"(EGLE ITSL / Barr notification level)")
    if parse_anomaly:
        reasons.append(
            "no daily 24-hr average values parsed from the report — possible format "
            "change or a scanned (non-text) PDF; review the mirrored PDF manually")
    elif not all_clear:
        # Only meaningful when the table DID parse (else the anomaly reason covers it).
        reasons.append(
            "report does not state the standard all-clear ('No notifications required "
            "to be sent') — review for a 15-minute/750 ppb notification or other change")

    alert = bool(reasons)
    return {
        "n_days": n_days,
        "max_numeric": max_numeric,
        "value_token": value_token,
        "all_days_below_1": all_days_below_1,
        "all_clear": all_clear,
        "exceed_24h": exceed_24h,
        "parse_anomaly": parse_anomaly,
        "alert": alert,
        "severity": "alert" if alert else "routine",
        "reasons": reasons,
    }
