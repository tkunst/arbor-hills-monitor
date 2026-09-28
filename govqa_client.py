"""
govqa_client.py — fetch + parse for EGLE's PUBLIC FOIA archive on GovQA (Stream U).
See docs/decisions/059-govqa-archive-watch.md.

EGLE posts the FOIA requests it has answered — and the records it released — on
its public archive (michiganegle.govqa.us, "Open Records Summary"). Anyone can
read it with no login. It is how the E614007 release (about 45 RRD documents the
monitor had never seen) came to light. This module is the pure-ish client for it;
watching, alerting, staging and the Sheet are govqa_watcher.py.

The site is an ASP.NET WebForms page with a DevExpress grid tied to a live
session, and the working method (docs/overnight-coder-handoffs/rrd-mpart-records.md
§1C-bis, learned the hard way in 9/2026) is followed exactly:

  KEYWORD LIST  headless Playwright, ONE term per search, newest-first, paged with
                ASPx.GVPagerOnClick('gridView','PBN'), stopping as soon as a page
                holds an E-number we already know (incremental daily run). Never
                the UI's CSV Export headless (returns nothing within 10 minutes),
                never the date filter (ignored), never in parallel. Any timeout:
                fresh browser context, retry with backoff (30 s, 2 min, ...),
                after `max_attempts` failures raise GovqaStructuralError and let
                the caller move on — never hang the run.
  ONE REQUEST   a plain cookie-jar HTTP session, no browser (`requests` here; the
                prior-art scripts shell out to curl, same protocol): GET the
                summary (302 -> session URL), scrape the form inputs, POST them
                back with txtRefsearch=<E-number>, read the internal `rid`, GET
                RequestArchiveDetails.aspx?rid=<rid>, and download each attachment
                by re-POSTing that page's form with __EVENTTARGET=<its link> and
                following the 302 to a time-limited Azure blob URL. A text/html
                answer means the session expired -> GovqaFetchError.
  BULK BACKFILL a gridView CSV that Trisha exports in a real browser and drops in
                a configured folder (`parse_gridview_csv`); the run stops and asks
                for one rather than loop on timeouts (see the watcher).

Requests are paced at >= MIN_INTERVAL seconds and run serially.

Nothing here writes anywhere: no Sheet, no Drive, no email. Staging downloads are
written to a caller-supplied local path only.
"""
from __future__ import annotations

import csv
import hashlib
import html as htmllib
import io
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests

SUMMARY_URL = "https://michiganegle.govqa.us/WEBAPP/_rs/OpenRecordsSummary.aspx?view=1"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

MIN_INTERVAL = 1.0                       # seconds between HTTP requests (tests zero it)
BACKOFF_SECONDS = (30, 120, 600)         # waits after failure 1, 2, 3 (1C-bis: 30 s, 2 min, 10 min)
DEFAULT_MAX_ATTEMPTS = 3                 # "after 3 failures, log a structural error and move on"

E_NUMBER = re.compile(r"^E\d{6}-\d{6}$")
_E_NUMBER_IN_TEXT = re.compile(r"E\d{6}-\d{6}")

# Redirect targets a download may follow: the archive itself and its Azure blob store.
_ALLOWED_HOST_SUFFIXES = ("michiganegle.govqa.us", ".blob.core.usgovcloudapi.net", ".blob.core.windows.net")

# Request statuses that mean EGLE is done with the request. Anything else — including
# statuses this code has never seen — is treated as OPEN and re-checked (fail-safe).
_TERMINAL_PREFIXES = ("GRANTED", "DENIED", "CANCELLED", "ABANDONED")
_RELEASED_PREFIXES = ("GRANTED", "PARTIAL")


class GovqaFetchError(RuntimeError):
    """A transient failure: timeout, non-200, a session that expired mid-download
    (an HTML answer where file bytes were expected), an unreachable page."""


class GovqaStructuralError(RuntimeError):
    """The client gave up (max attempts exhausted), or the page no longer has the
    shape this parser needs, or the run needs a human (a backfill too large to
    scrape: 'export a CSV'). Loud — the watcher exits non-zero — but per term: the
    run moves on to the next one."""


_URL_IN_TEXT = re.compile(r"https?://\S+")


def scrub(text, limit: int = 200) -> str:
    """Exception/log text with every URL replaced by '<url>' and truncated. GitHub
    Actions logs are WORLD-READABLE on this public repo, and URLs here leak: an Azure
    blob download link carries the attachment's FILE NAME (which can name a resident)
    and a signed token, a Google Drive API error embeds the query it sent (which
    contains the file name), and a session URL carries the session id. Every message
    that can reach a print(), an email or a Sheet note goes through this."""
    return _URL_IN_TEXT.sub("<url>", str(text))[:limit]


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------


def normalize_status(status: str) -> str:
    """Canonical request status. The site shows 'GRANTED – Records' with an en dash;
    a human-exported CSV comes out as 'GRANTED � Records'. Any dash-like or
    replacement separator becomes ' – ' so the same status never diffs against
    itself between the grid and a CSV."""
    s = htmllib.unescape(str(status or "")).strip()
    s = re.sub(r"\s*[–—\-�]\s*", " – ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_terminal(status: str) -> bool:
    return normalize_status(status).upper().startswith(_TERMINAL_PREFIXES)


def is_released(status: str) -> bool:
    """True when the status says records were (at least partly) released."""
    return normalize_status(status).upper().startswith(_RELEASED_PREFIXES)


def _clean(cell_html: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", cell_html))).strip()


def parse_rows(page_html: str) -> list[dict]:
    """Every request row on a grid page, newest first as shown:
    [{request_no, created, summary, status, rid}]. `rid` is the internal id
    (redirectInfo('714629')); None if the row has no details link. Rows whose first
    cell isn't an E-number are ignored."""
    rows = []
    for tr in re.findall(r"<tr[^>]*dxgvDataRow[^>]*>(.*?)</tr>", page_html, re.S):
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        cells = [c for c in cells if c]
        if not cells or not E_NUMBER.match(cells[0]):
            continue
        rid = re.search(r"(?:redirectInfo|OnMoreInfoClick)\((?:this,\s*)?(?:&#39;|')?(\d+)", tr)
        rows.append({
            "request_no": cells[0],
            "created": cells[1] if len(cells) > 1 else "",
            "summary": cells[2] if len(cells) > 2 else "",
            "status": normalize_status(cells[3]) if len(cells) > 3 else "",
            "rid": rid.group(1) if rid else None,
        })
    return rows


def parse_pager(text: str) -> tuple[int, int, int] | None:
    """(page, pages, items) from 'Page 1 of 10 (97 items)'; None when the grid has
    no pager (10 or fewer rows)."""
    m = re.search(r"Page (\d+) of (\d+) \((\d+) items\)", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _label_value(page_html: str, label: str) -> str:
    m = re.search(re.escape(label) + r"\s*</span>.*?<p[^>]*>(.*?)</p>", page_html, re.S)
    return _clean(m.group(1)) if m else ""


def parse_detail(page_html: str) -> dict:
    """A request's detail page: {reference, created, description, closed, files}
    where files = [{target, name}] (`target` is the __doPostBack event target that
    downloads it). Raises GovqaStructuralError if the reference number isn't there
    (an error page / a logged-out shell)."""
    ref = _label_value(page_html, "Reference No:")
    if not E_NUMBER.match(ref):
        raise GovqaStructuralError("detail page has no Reference No — page shape changed or an error page")
    files = []
    for target, name in re.findall(
            r"__doPostBack\((?:&#39;|')(rptAttachments\$ctl\d+\$lnkStreamCloud)(?:&#39;|'),(?:&#39;|')(?:&#39;|')\)"
            r"[^>]*>([^<]+)</a>", page_html):
        files.append({"target": target, "name": htmllib.unescape(name).strip()})
    return {
        "reference": ref,
        "created": _label_value(page_html, "Create Date:"),
        "description": _label_value(page_html, "Public Record Desired:"),
        "closed": _label_value(page_html, "Close Date:"),
        "files": files,
    }


def hidden_inputs(page_html: str, keep_buttons: bool = False) -> dict:
    """Every <input> name/value on a WebForms page (the __VIEWSTATE family etc.),
    minus main-nav/viewport, and minus submit/button inputs unless asked."""
    out = {}
    for tag in re.findall(r"<input[^>]*>", page_html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        if not keep_buttons and re.search(r'type="(?:submit|button)"', tag):
            continue
        value = re.search(r'value="([^"]*)"', tag)
        out[name.group(1)] = htmllib.unescape(value.group(1)) if value else ""
    for k in ("main-nav", "viewport"):
        out.pop(k, None)
    return out


def parse_gridview_csv(data: bytes | str) -> list[dict]:
    """Rows from a gridView CSV exported by a human in a real browser (columns
    'Request Number, Create Date, Summary, Request Status'). Tolerates a UTF-8 BOM
    or a cp1252 file, multi-line quoted summaries, and the U+FFFD the export leaves
    where the site shows an en dash. Rows whose number isn't an E-number are skipped."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            data = data.decode("cp1252", errors="replace")
    reader = csv.DictReader(io.StringIO(data))
    rows = []
    for rec in reader:
        no = (rec.get("Request Number") or "").strip()
        if not E_NUMBER.match(no):
            continue
        rows.append({"request_no": no,
                     "created": (rec.get("Create Date") or "").strip(),
                     "summary": re.sub(r"\s+", " ", rec.get("Summary") or "").strip(),
                     "status": normalize_status(rec.get("Request Status") or ""),
                     "rid": None})
    return rows


def phrase_in(text: str, phrase: str) -> bool:
    """Case-insensitive, whitespace-normalized WHOLE-WORD phrase test ('arbor hills'
    is not found in 'Ann Arbor Hillsdale'; '10690' is not found in '106900')."""
    norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()  # noqa: E731
    needle = norm(phrase)
    if not needle:
        return False
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", norm(text)) is not None


_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_filename(request_no: str, name: str) -> str:
    """A traversal-safe local/Drive filename: '<E-number>__<sanitized name>'. Only
    [A-Za-z0-9._ -] survive from the attachment's own name; no path separators,
    no leading dot."""
    stem, dot, ext = str(name).rpartition(".")
    stem, ext = (stem, ext) if dot else (str(name), "")
    stem = _UNSAFE.sub("_", stem).strip(" ._")[:100] or "file"
    ext = _UNSAFE.sub("", ext).strip(".")[:8]
    no = request_no if E_NUMBER.match(request_no or "") else "E000000-000000"
    return f"{no}__{stem}" + (f".{ext.lower()}" if ext else "")


# ---------------------------------------------------------------------------
# Backoff (1C-bis: fresh session, retry, then give up loudly and move on)
# ---------------------------------------------------------------------------


def with_backoff(fn, what: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 delays=BACKOFF_SECONDS, sleep=time.sleep, on_retry=None):
    """Call fn() up to `max_attempts` times. Between attempts: run `on_retry()`
    (the caller's 'close the context, start a fresh one') then sleep delays[i].
    GovqaFetchError is retried; anything else propagates. After the last failure
    raise GovqaStructuralError — the caller logs it and moves on to the next item."""
    last = None
    for i in range(max_attempts):
        try:
            return fn()
        except GovqaFetchError as e:
            last = e
            print(f"[govqa] {what}: attempt {i + 1}/{max_attempts} failed: {scrub(e)}")
            if i + 1 < max_attempts:
                if on_retry is not None:
                    on_retry()
                sleep(delays[min(i, len(delays) - 1)])
    raise GovqaStructuralError(f"{what}: gave up after {max_attempts} failed attempts: {scrub(last)}")


# ---------------------------------------------------------------------------
# One request by E-number: a plain cookie-jar session (no browser)
# ---------------------------------------------------------------------------

_last_request = 0.0


def _pace() -> None:
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _host_allowed(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == s.lstrip(".") or host.endswith(s if s.startswith(".") else "." + s)
                                       for s in _ALLOWED_HOST_SUFFIXES)


class ArchiveSession:
    """A cookie-jar session against the public archive. Serial, paced, no browser."""

    def __init__(self, summary_url: str = SUMMARY_URL, session: requests.Session | None = None):
        self.summary_url = summary_url
        self.http = session or requests.Session()
        self.http.headers.update({"User-Agent": _UA})
        self._summary_page_url = ""
        self._summary_html = ""
        self._detail_url = ""
        self._detail_html = ""

    # -- plumbing -----------------------------------------------------------

    def _req(self, method: str, url: str, timeout: int = 90, **kw):
        _pace()
        try:
            return self.http.request(method, url, timeout=timeout, **kw)
        except requests.RequestException as e:
            raise GovqaFetchError(f"{method} request failed: {scrub(e)}") from e

    def _open_summary(self) -> None:
        r = self._req("GET", self.summary_url, timeout=180)
        if r.status_code != 200 or "txtSearch" not in r.text:
            raise GovqaFetchError(f"summary page returned HTTP {r.status_code} without the search form")
        self._summary_page_url, self._summary_html = r.url, r.text   # r.url carries the (S(...)) session id

    # -- public API ---------------------------------------------------------

    def lookup(self, request_no: str) -> dict | None:
        """The grid row for one E-number (status, created, summary, rid), or None if
        the archive doesn't show it. Uses txtRefsearch — no browser."""
        if not E_NUMBER.match(request_no or ""):
            raise ValueError(f"not an E-number: {request_no!r}")
        self._open_summary()
        form = hidden_inputs(self._summary_html)
        form.update(txtSearch="", txtRefsearch=request_no, filterButton="FILTER")
        r = self._req("POST", self._summary_page_url, timeout=180, data=form,
                      headers={"Referer": self._summary_page_url})
        if r.status_code != 200:
            raise GovqaFetchError(f"lookup POST returned HTTP {r.status_code}")
        for row in parse_rows(r.text):
            if row["request_no"] == request_no:
                return row
        return None

    def details(self, rid: str) -> dict:
        """Detail page for an internal rid: see parse_detail. Remembers the page for
        download()."""
        if not str(rid).isdigit():
            raise ValueError(f"not a numeric rid: {rid!r}")
        base = self._summary_page_url.rsplit("/", 1)[0] if self._summary_page_url else self.summary_url.rsplit("/", 1)[0]
        url = f"{base}/RequestArchiveDetails.aspx?rid={rid}&view=1"
        r = self._req("GET", url, timeout=180)
        if r.status_code != 200:
            raise GovqaFetchError(f"details GET returned HTTP {r.status_code}")
        self._detail_url, self._detail_html = url, r.text
        return parse_detail(r.text)

    def download(self, target: str, dest_path: str, max_bytes: int) -> dict:
        """Download the attachment whose postback target is `target` on the most
        recently loaded detail page. Streams to `dest_path`; returns {size, sha256,
        md5, content_type}. Follows the 302 to the Azure blob by hand, allowlisting
        the host. Raises GovqaFetchError on a non-file (text/html = expired session)
        answer, an empty body, or a transport error; ValueError if the file is over
        `max_bytes` (the caller records it as skipped); never leaves a partial file."""
        if not self._detail_html:
            raise GovqaFetchError("download() before details()")
        if not re.fullmatch(r"rptAttachments\$ctl\d+\$lnkStreamCloud", target):
            raise ValueError(f"unexpected postback target {target!r}")
        form = hidden_inputs(self._detail_html)
        form.update(__EVENTTARGET=target, __EVENTARGUMENT="")
        url, method, data = self._detail_url, "POST", form
        try:
            for _hop in range(4):
                _pace()
                r = self.http.request(method, url, data=data, stream=True, timeout=300,
                                      allow_redirects=False, headers={"Referer": self._detail_url})
                if r.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, r.headers.get("location", ""))
                    r.close()
                    if not _host_allowed(url):
                        raise GovqaFetchError(f"refusing redirect to non-allowlisted host: {scrub(urlparse(url).hostname)}")
                    method, data = "GET", None
                    continue
                break
            else:
                raise GovqaFetchError("too many redirects")
        except requests.RequestException as e:
            raise GovqaFetchError(f"download failed: {scrub(e)}") from e
        return _stream_to_file(r, dest_path, max_bytes)


def _stream_to_file(r, dest_path: str, max_bytes: int) -> dict:
    """Stream a response to disk, hashing as it goes. Shared by download() and tested
    directly. Deletes the partial file on any failure."""
    try:
        if r.status_code != 200:
            raise GovqaFetchError(f"download returned HTTP {r.status_code}")
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype == "text/html":
            raise GovqaFetchError("download answered text/html — session expired or blocked")
        declared = r.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(f"{int(declared)} bytes > cap {max_bytes}")
        sha, md5, size = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
        try:
            with open(dest_path, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"exceeded cap {max_bytes} bytes while streaming")
                    sha.update(chunk)
                    md5.update(chunk)
                    fh.write(chunk)
        except (requests.RequestException, OSError) as e:
            raise GovqaFetchError(f"streaming failed: {scrub(e)}") from e
        if size == 0:
            raise GovqaFetchError("download returned an empty body")
        return {"size": size, "sha256": sha.hexdigest(), "md5": md5.hexdigest(), "content_type": ctype}
    except BaseException:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise
    finally:
        r.close()


# ---------------------------------------------------------------------------
# Keyword list: one term per search, newest first, incremental paging
# ---------------------------------------------------------------------------


class SweepResult:
    """What one term's sweep saw. `rows` are every raw row read (newest first)."""

    def __init__(self):
        self.rows: list[dict] = []
        self.pages_read = 0
        self.total_pages = 1
        self.total_items = 0
        self.stopped_on_known = False
        self.overflow = False       # hit max_pages with unread pages left and no known row


def sweep_term(grid, term: str, is_known, max_pages: int) -> SweepResult:
    """Read `term`'s results newest-first, one page at a time, and STOP as soon as a
    page contains an E-number `is_known` (everything older was seen on a previous
    run) — the incremental daily run of 1C-bis. `grid` needs .search(term) and
    .next_page(), each returning (rows, pager). Marks `overflow` when max_pages
    pages were read, none held a known row, and more pages remain (the caller treats
    that as 'a backfill too large to scrape — ask for a CSV export')."""
    out = SweepResult()
    rows, pager = grid.search(term)
    while True:
        out.pages_read += 1
        out.rows.extend(rows)
        if pager:
            out.total_pages, out.total_items = pager[1], pager[2]
        else:
            out.total_pages, out.total_items = 1, len(rows)
        if any(is_known(r["request_no"]) for r in rows):
            out.stopped_on_known = True
            return out
        if not pager or pager[0] >= pager[1]:
            return out                                   # last page
        if out.pages_read >= max_pages:
            out.overflow = True
            return out
        rows, pager = grid.next_page()


class PlaywrightGrid:
    """The real grid driver (pw_search.py's pattern). One headless browser context at
    a time; `restart()` closes it and opens a fresh one (the 1C-bis timeout remedy).
    Playwright is imported LAZILY — it is not a requirement of this repo's test suite
    or of any other workflow; the govqa workflow installs it only when the stream is
    enabled."""

    def __init__(self, url: str = SUMMARY_URL, headless: bool = True):
        self.url, self.headless = url, headless
        self._pw = self._browser = self._ctx = self.page = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise GovqaStructuralError(
                "playwright is not installed (pip install playwright && playwright install chromium)") from e
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(channel="chrome", headless=self.headless)
        except Exception:  # noqa: BLE001 — no system Chrome: the bundled chromium
            self._browser = self._pw.chromium.launch(headless=self.headless)
        self.restart()
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for obj in (self._ctx, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = self._browser = self._ctx = self.page = None

    def restart(self):
        """Close the context and start a fresh one (a fresh ASP.NET session)."""
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:  # noqa: BLE001
            pass
        self._ctx = self._browser.new_context()
        self.page = self._ctx.new_page()

    def _read(self):
        html = self.page.content()
        try:
            text = self.page.inner_text("body")
        except Exception:  # noqa: BLE001
            text = html
        return parse_rows(html), parse_pager(text)

    def search(self, term: str):
        try:
            pg = self.page
            pg.goto(self.url, wait_until="domcontentloaded", timeout=180000)
            pg.wait_for_selector("#txtSearch_I", timeout=180000)
            pg.fill("#txtSearch_I", term)
            with pg.expect_navigation(timeout=300000):
                pg.click("#filterButton")
            pg.wait_for_selector("#txtSearch_I", timeout=180000)
            return self._read()
        except Exception as e:  # noqa: BLE001 — Playwright timeouts / navigation errors
            raise GovqaFetchError(f"search {term!r}: {scrub(e)}") from e

    def next_page(self):
        try:
            pg = self.page
            first = (self._read()[0] or [{"request_no": ""}])[0]["request_no"]
            pg.evaluate("ASPx.GVPagerOnClick('gridView','PBN')")
            for _ in range(120):                                  # up to 60 s
                pg.wait_for_timeout(500)
                rows, pager = self._read()
                if rows and rows[0]["request_no"] != first:
                    return rows, pager
            raise GovqaFetchError("pager did not advance within 60 s")
        except GovqaFetchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise GovqaFetchError(f"next page: {scrub(e)}") from e
