"""
rop_client.py — fetch + parse for the ROP (air Title V permit) watch. See
docs/decisions/017-rop-watch.md.

All three Arbor Hills air facilities have a Renewable Operating Permit (ROP)
renewal IN PROCESS: N2688 (landfill, ROP0000224 v3), N1504 (Energy, ROP0000656
v3), P1488 (Emerald RNG, ROP0000236 v3). A renewal opens a 30-day PUBLIC COMMENT
window once it reaches that stage — a second advocacy venue (air-permit
conditions) that is easy to miss. This module only FETCHES + EXTRACTS from three
public EGLE sources; snapshotting/diffing/alerting is rop_watcher.py.

THREE sources, three fetch functions (stdlib urllib, same posture as
pfas_client/ridgewood_client — egle.state.mi.us does not 403 like michigan.gov):

  1. The EPA ROP Monthly Report CSV — a facility-level workflow-task export,
     OVERWRITTEN in place every month under a fixed filename. `fetch_csv` also
     returns the HTTP Last-Modified header for provenance, but the load-bearing
     signal is the CONTENT of the extracted rows (rop_watcher hashes those), not
     the header or any filename — a fixed name can't tell you "changed" by itself.

  2. The N2688 folder listing — a plain Apache/IIS directory index. A NEW file
     here (a draft renewal ROP / staff report) is the signal. Verified live
     2026-07-15: the folder itself carries NO Last-Modified header, and its
     listed files are dated up to 2024 even though the folder's *page* can look
     freshly generated — so `parse_folder_listing` reads the listing's own
     per-file dates out of the HTML body; nothing here trusts folder-level mtime.

  3. The statewide ROP_Public_Notice.pdf — parsed for a mention of EACH target
     SRN (N2688, N1504, P1488), one per facility (the 30-day-comment-window
     notice). Originally N2688-only; broadened 2026-07-23 after Emerald RNG
     (P1488) reached public comment (window July 20 – Aug 1) and slipped through
     because the trip-wire only searched for "N2688". Uses fitz (the repo's
     existing PDF text-layer dependency, same as ridgewood_client) —
     egle_doc_parser.py is deliberately left untouched (Decode base stays
     domain-agnostic).

⚠️ M3333 ("Conway Products Corporation d/b/a Emerald Spa Corp") is an UNRELATED
facility that happens to also contain "Emerald" in its name (P1488 is "Emerald
RNG LLC") — a name-substring match would wrongly pull it in. `parse_csv_rows`
filters by exact SRN membership in `TARGET_SRNS`, never by name, so M3333 is
excluded by construction; see test_rop.py for the regression that pins this.
"""
from __future__ import annotations

import csv
import html as _html
import http.cookiejar
import io
import re
import urllib.parse
import urllib.request

import fitz  # pymupdf — the repo's existing PDF text-layer dependency

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

DEFAULT_CSV_URL = ("https://www.egle.state.mi.us/aps/downloads/ROP/pub_ntce/"
                    "1 - EPA ROP Monthly Report/EPA Monthly Report.csv")
DEFAULT_N2688_FOLDER_URL = "https://www.egle.state.mi.us/aps/downloads/ROP/pub_ntce/N2688/"
DEFAULT_NOTICE_URL = "https://www.egle.state.mi.us/aps/downloads/ROP/pub_ntce/ROP_Public_Notice.pdf"

# Priority order: the landfill first (N2688 is the primary advocacy concern).
TARGET_SRNS = ("N2688", "N1504", "P1488")

# The real CSV is ~1.8 MB; a bot-wall/error page would be far smaller.
_MIN_CSV_BYTES = 50_000
# The real folder listing is ~2 KB; the real notice PDF is ~340 KB.
_MIN_FOLDER_BYTES = 200
_MIN_PDF_BYTES = 2_000

# The CSV's real header (row 2; row 1 is a merged-cell group header) has 22
# columns. Column NAMES repeat ("Name" x3, "Status" x3 — ROP action / task /
# permit each have their own), so parsing is POSITIONAL, not name-keyed; this
# constant is a coarse structural trip-wire (see parse_csv_rows).
_EXPECTED_COLUMNS = 22

_FOLDER_ENTRY_RE = re.compile(
    r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+(&lt;dir&gt;|\d+)\s*'
    r'<A\s+HREF="([^"]+)"[^>]*>([^<]*)</A>', re.I)


class RopFetchError(RuntimeError):
    """A source couldn't be fetched cleanly (network error, non-200, a body too
    short/wrong-typed to be the real thing). TRANSIENT — the watcher skips-and-
    warns rather than diffing it, so a blip never fires a spurious change alert.
    (A first-ever run with no baseline treats it as loud instead — see
    rop_watcher: an activation-time block must surface, not silently no-op.)"""


class RopParseError(RuntimeError):
    """The CSV fetched but its header doesn't match the expected column layout —
    almost certainly EGLE changed the export format. Never trust a positional
    parse against an unrecognized layout. UNLIKE RopFetchError, this is treated
    as ALWAYS loud in rop_watcher.run(), never gated on baseline status — a
    structural break is far more likely to persist across runs than a network
    blip, so letting it go quiet once every item has a baseline would hide a
    real format change forever instead of surfacing it."""


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    # Accept: */* — NOT a specific type list. EGLE's server content-negotiates the
    # CSV endpoint and returns HTTP 406 Not Acceptable for the previous
    # "text/html,application/pdf,text/csv" header (verified 2026-07-23: that header
    # 406'd the CSV on every scheduled run from activation 2026-07-16 onward, which
    # silently blinded the csv:* items behind the already-baselined skip-and-warn
    # path; `*/*` returns the full ~1.8 MB CSV, and all three sources — CSV, the
    # N2688 folder index, the notice PDF — return 200 under it). `*/*` is strictly
    # more permissive than the old list, so it cannot regress the folder/PDF fetches.
    op.addheaders = [("User-Agent", _UA), ("Accept", "*/*")]
    return op


# ---------------------------------------------------------------------------
# 1. EPA ROP Monthly Report CSV
# ---------------------------------------------------------------------------


def fetch_csv(url: str = DEFAULT_CSV_URL, timeout: int = 60) -> tuple[str, str | None]:
    """GET the ROP monthly CSV. Returns (text, last_modified_header_or_None).
    Raises RopFetchError on network/HTTP failure or a suspiciously small body —
    never returns a partial/error body for the caller to mistake for real data."""
    safe_url = urllib.parse.quote(url, safe=":/")
    try:
        r = _opener().open(safe_url, timeout=timeout)
        status = getattr(r, "status", None) or r.getcode()
        body = r.read()
        last_modified = r.headers.get("Last-Modified")
    except Exception as e:  # noqa: BLE001 — network / HTTP -> transient
        raise RopFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise RopFetchError(f"GET {url} returned HTTP {status}")
    if len(body) < _MIN_CSV_BYTES:
        raise RopFetchError(f"GET {url} body too short ({len(body)} bytes) — bad fetch?")
    return body.decode("utf-8-sig", "ignore"), last_modified


def parse_csv_rows(csv_text: str, srns: tuple[str, ...] = TARGET_SRNS) -> list[dict]:
    """Extract one dict per (facility, ROP action, task) row for `srns` only —
    filtered by EXACT SRN match, never by facility name (see the M3333 warning in
    the module docstring). Raises RopParseError if the header's column count
    doesn't match the expected layout (a structural change we must not silently
    misparse)."""
    lines = list(csv.reader(io.StringIO(csv_text)))
    if len(lines) < 2:
        raise RopParseError("CSV has no header rows — empty or bad fetch?")
    header = lines[1]
    if len(header) != _EXPECTED_COLUMNS:
        raise RopParseError(
            f"CSV header has {len(header)} columns, expected {_EXPECTED_COLUMNS} — "
            "EGLE may have changed the export format; review before trusting this parse.")

    wanted = set(srns)
    rows: list[dict] = []
    for row in lines[2:]:
        if len(row) < _EXPECTED_COLUMNS:
            continue
        srn = row[0].strip()
        if srn not in wanted:
            continue
        rows.append({
            "srn": srn,
            "name": row[1].strip(),
            "rop_action": row[8].strip(),
            "rop_action_status": row[9].strip(),
            "rop_action_created": row[10].strip(),
            "task_name": row[11].strip(),
            "task_status": row[12].strip(),
            "task_assigned": row[13].strip(),
            "task_due": row[14].strip(),
            "task_completed": row[15].strip(),
            "permit_number": row[16].strip(),
            "version": row[17].strip(),
            "permit_status": row[18].strip(),
            "issue_date": row[19].strip(),
            "effective_date": row[20].strip(),
            "expiration_date": row[21].strip(),
        })
    return rows


# ---------------------------------------------------------------------------
# 2. N2688 folder listing
# ---------------------------------------------------------------------------


def fetch_folder_listing(url: str = DEFAULT_N2688_FOLDER_URL, timeout: int = 60) -> str:
    """GET the N2688 folder's directory-index HTML. Raises RopFetchError on any
    network/HTTP failure or a suspiciously short body."""
    try:
        r = _opener().open(url, timeout=timeout)
        status = getattr(r, "status", None) or r.getcode()
        body = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        raise RopFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise RopFetchError(f"GET {url} returned HTTP {status}")
    if len(body) < _MIN_FOLDER_BYTES:
        raise RopFetchError(f"GET {url} body too short ({len(body)} bytes) — bad fetch?")
    return body


def parse_folder_listing(html_text: str) -> list[dict]:
    """Return one dict per listed entry {name, href, date, time, is_dir}, sorted
    by name. Pure regex over the directory-index `<pre>` block: each real entry
    is a `M/D/YYYY  H:MM AM/PM  <size-or-&lt;dir&gt;>  <A HREF=...>name</A>` line;
    the "[To Parent Directory]" link has no leading date and so never matches —
    excluded by construction, not a special-cased skip."""
    out = []
    for date_s, time_s, size_s, href, name in _FOLDER_ENTRY_RE.findall(html_text):
        out.append({
            "name": _html.unescape(name).strip(),
            "href": _html.unescape(href).strip(),
            "date": date_s,
            "time": time_s.strip(),
            "is_dir": size_s.lower() == "&lt;dir&gt;",
        })
    out.sort(key=lambda e: e["name"])
    return out


# ---------------------------------------------------------------------------
# 3. Statewide ROP public-notice PDF
# ---------------------------------------------------------------------------


def fetch_notice_pdf(url: str = DEFAULT_NOTICE_URL, timeout: int = 60) -> bytes:
    """Download the statewide notice PDF's bytes. Raises RopFetchError on HTTP
    error or a body that isn't a PDF / is suspiciously small."""
    try:
        r = _opener().open(url, timeout=timeout)
        status = getattr(r, "status", None) or r.getcode()
        body = r.read()
    except Exception as e:  # noqa: BLE001
        raise RopFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise RopFetchError(f"GET {url} returned HTTP {status}")
    if not body.startswith(b"%PDF"):
        raise RopFetchError(f"GET {url} did not return a PDF ({len(body)} bytes)")
    if len(body) < _MIN_PDF_BYTES:
        raise RopFetchError(f"GET {url} body too short ({len(body)} bytes) — bad fetch?")
    return body


# Each facility's own entry in the statewide notice is a self-delimiting block:
# a "<Name> - SRN: <code>" header line (en-dash/em-dash/hyphen all seen live),
# immediately followed by that facility's own notice text, immediately followed
# by the NEXT facility's header line (or a page break, or end of document).
# Verified live against the real ~22,000-char / 24-page PDF: this pattern
# matches all 68 entries present, cleanly, with no false matches.
_ENTRY_START_RE = re.compile(r"^(.*?)\s*[-–—]\s*SRN:\s*([A-Z0-9]+)\s*$", re.MULTILINE)
# The repeating page header/footer ("Title V Renewable Operating Permit (ROP)
# / Public Notice Documents / Page N") that PDF text extraction splices into
# the middle of an entry's own text whenever that entry straddles a page
# break — stripped so it never pollutes one SRN's context with another page's
# boilerplate, and so a page break landing a character or two differently
# (itself a product of EARLIER content's length) can't perturb the hash.
_PAGE_BOILERPLATE_RE = re.compile(
    r"Title V Renewable Operating Permit \(ROP\)\s*\nPublic Notice Documents\s*\nPage\s*\d+\s*\n?"
)
# Generous enough to hold a real single-entry block in full (observed ~300-350
# chars collapsed) with headroom, while still bounding the one structural edge
# case this can't fully clean up on its own: an SRN that happens to be the
# LAST entry in a notice-type section, where the "next entry" lookup instead
# lands on a different section's long explanatory header paragraph.
_CONTEXT_MAX_CHARS = 400


def _notice_context(text: str, srn: str, fallback_match: re.Match) -> str:
    """The context text for one SRN's mention — that SRN's own per-entity
    notice block (`_ENTRY_START_RE`'s header line through to the next entry's
    header line), not a fixed character window around the match. The
    statewide PDF lists dozens of unrelated facilities; a fixed-offset window
    shifts whenever any EARLIER facility's entry changes length (one added or
    removed elsewhere in the document), producing a spurious "context text
    shifted" alert even though THIS SRN's own entry is unchanged — confirmed
    live on N1504 (three such false alerts, 2026-08-24/09-22/10-01, each
    showing the identical already-closed Aug 10 - Sep 9 comment period).
    Bounding by the document's own per-entity delimiter instead means the
    captured text only changes when the content actually about this SRN
    changes. Falls back to the old fixed ±150-char window if the header-line
    pattern isn't found for this SRN (an unexpected PDF layout) — `mentioned`
    is already decided by the caller's generic whole-word search, so this
    fallback only affects the informational context text, never detection."""
    entries = list(_ENTRY_START_RE.finditer(text))
    idx = next((i for i, e in enumerate(entries) if e.group(2) == srn), None)
    if idx is None:
        start, end = max(0, fallback_match.start() - 150), min(len(text), fallback_match.end() + 150)
        return " ".join(text[start:end].split())
    block_end = entries[idx + 1].start() if idx + 1 < len(entries) else len(text)
    block = _PAGE_BOILERPLATE_RE.sub(" ", text[entries[idx].start():block_end])
    cleaned = " ".join(block.split())
    if len(cleaned) <= _CONTEXT_MAX_CHARS:
        return cleaned
    return cleaned[:_CONTEXT_MAX_CHARS].rstrip() + "..."


def notice_mentions_srn(pdf_bytes: bytes, srn: str = "N2688") -> tuple[bool, str]:
    """(mentioned, context): whether `srn` appears in the notice's text (a
    whole-word match, so N2688 doesn't accidentally match inside a longer token),
    and a short, stable surrounding-text excerpt when it does (empty string
    otherwise) — see `_notice_context`.

    Raises RopFetchError if the bytes can't actually be parsed as a PDF — the
    `%PDF` magic-byte check in fetch_notice_pdf only confirms the HEADER; a
    truncated download (a network cut mid-transfer) can still start with `%PDF`
    while the rest of the document is corrupt, which raises a fitz/mupdf
    FileDataError here. Wrapping ONLY that (and its EmptyFileError subclass)
    as RopFetchError routes a corrupt body through the same skip-and-warn-or-
    loud fail-safe as any other fetch failure, instead of crashing the whole
    run uncaught. Deliberately NOT a bare `except Exception`: a genuine bug in
    this function (or a future pymupdf API change) must surface as an
    uncaught, loud crash rather than be silently relabeled as a routine
    transient fetch blip and skip-and-warned forever once notice:N2688 has a
    baseline."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            text = "\n".join(doc[i].get_text() for i in range(len(doc)))
        finally:
            doc.close()
    except fitz.FileDataError as e:  # corrupt/truncated body -> transient
        raise RopFetchError(f"notice PDF could not be parsed: {e}") from e
    m = re.search(rf"\b{re.escape(srn)}\b", text)
    if not m:
        return False, ""
    return True, _notice_context(text, srn, m)
