"""
pfas_client.py — content-hash page-watch for EGLE's PFAS-investigation pages.

The state's PFAS site pages (michigan.gov/pfasresponse/...) are the human-facing
status write-up for a site's PFAS investigation — no structured feed, no
per-record PDFs, just prose EGLE edits in place when the investigation moves.
There's nothing to parse or classify; the signal is simply "the page changed."
So this is a lightweight content-hash watch (the pattern wds_archiver.py uses for
WDS pages), except its job is to ALERT on a change, not to mirror the page.

WHY NOT HASH THE RAW PAGE (the load-bearing decision, verified against the live
page 2026-07-12): michigan.gov runs on Sitecore, whose every theme JS/CSS asset
carries a `?rev=...&hash=...` cache-buster that rotates on any *state-wide* theme
redeploy — 44 of them on this page, none of which have anything to do with the
Arbor Hills content. Hashing the whole page would fire a false "the PFAS page
changed!" alert every time Michigan pushes an unrelated site-wide asset. So we:

  1. isolate the single <main> content region (the substantive content; ~20% of
     the page — everything outside it is nav/theme/footer chrome),
  2. drop <script>/<style>/comments,
  3. reduce to visible text (block boundaries -> newlines, so the change email's
     diff reads line-by-line) PLUS the sorted set of link/asset target PATHS with
     the query string stripped — so an asset's cache-buster rotating is invisible,
     but a genuinely NEW document link (a new path) still trips the hash,
  4. hash that.

Proven against the real page: rotating either the theme OR the content-asset
cache-busters leaves the hash IDENTICAL; changing a word of visible text or
adding a document link CHANGES it. Those four are the test spec (test_pfas_client
reproduces them on a synthetic fixture — the real page can't be committed).

This module only FETCHES + NORMALIZES. Diff / alerting / state is pfas_watcher.py.
Stdlib-only (urllib/re/hashlib) — no new dependency, same as wds_client.py.
"""
from __future__ import annotations

import hashlib
import html as _html
import http.cookiejar
import re
import urllib.request

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# A real michigan.gov page is ~190 KB; anything much smaller is a bot wall / error
# page / partial read, not the content — refuse to hash it (see fetch_page).
_MIN_BODY_BYTES = 500
# The extracted <main> visible text is ~8 KB; a floor guards against a page whose
# <main> is present but empty (another bad-fetch shape).
_DEFAULT_MIN_CHARS = 200

_MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.S | re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TARGET_RE = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"', re.I)
_URLFUNC_RE = re.compile(r"url\(\s*['\"]?([^)'\"]+?)['\"]?\s*\)", re.I)
# Closing/opening block tags + <br> become newlines so the normalized text keeps
# one logical block per line (readable unified diff); every other tag is dropped.
_BLOCK_RE = re.compile(
    r"</?(?:p|div|li|ul|ol|h[1-6]|section|article|tr|table|br|main)\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_INTRALINE_WS_RE = re.compile(r"[ \t\r\f\v]+")

# Separates the visible-text half from the link-paths half of a normalized
# content string. PRINTABLE on purpose: this string is stored verbatim in a
# Google Sheets cell (the "Normalized Text" column, the diff basis for next run),
# and the Sheets API rejects NULL/control characters — a control-char sentinel
# would 400 the very first baseline write (a bug the mocked tests can't see). It
# only needs to never occur in real page prose or a URL, which this doesn't.
_LINK_SENTINEL = "\n[pfas-watch:links]\n"


class PFASFetchError(RuntimeError):
    """The page couldn't be fetched cleanly (network error, non-200, or a body too
    short to be the real page). TRANSIENT — the watcher skips-and-warns rather than
    diffing it, so a blip or bot wall never fires a spurious 'page changed' alert.
    (A first-ever run with no baseline treats it as loud instead — see
    pfas_watcher: a persistent block must surface on activation, not no-op.)"""


class PFASContentError(RuntimeError):
    """The body fetched but had no usable <main> content region (or its text was
    too short). Same skip-not-diff treatment as PFASFetchError: a missing <main>
    is far more likely a served error page or a redesign we can't safely diff than
    a real content edit, so we never turn it into a change alert."""


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA), ("Accept", "text/html")]
    return op


def fetch_page(url: str, timeout: int = 60) -> str:
    """GET the page and return its HTML. Raises PFASFetchError on any network/HTTP
    failure, a non-200 status, or a suspiciously short body — never returns a
    partial/error body for the caller to mistake for changed content."""
    try:
        r = _opener().open(url, timeout=timeout)
        status = getattr(r, "status", None) or r.getcode()
        body = r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001 — network / HTTP → transient
        raise PFASFetchError(f"GET {url} failed: {e}") from e
    if status != 200:
        raise PFASFetchError(f"GET {url} returned HTTP {status}")
    if len(body) < _MIN_BODY_BYTES:
        raise PFASFetchError(f"GET {url} body too short ({len(body)} bytes) — bad fetch?")
    return body


def _strip_query(u: str) -> str:
    """Path only: drop ?query and #fragment so an asset's rotating cache-buster
    (rev/hash/mw/v, gclid, ...) is not mistaken for a content change; a genuinely
    new document still has a new PATH, which does register."""
    return u.split("#", 1)[0].split("?", 1)[0].strip()


def extract_content(html_text: str, min_chars: int = _DEFAULT_MIN_CHARS) -> str:
    """Normalize a fetched page to its stable, comparable content string:
    <main> visible text (block-per-line) + the sorted set of link/asset target
    paths (query stripped). Raises PFASContentError if there's no <main> or the
    text is too short (a bad fetch / error page — the caller must not diff it)."""
    m = _MAIN_RE.search(html_text)
    if not m:
        raise PFASContentError("no <main> content region found")
    main = m.group(1)
    main = _SCRIPT_STYLE_RE.sub(" ", main)
    main = _COMMENT_RE.sub(" ", main)

    targets = set()
    for rx in (_TARGET_RE, _URLFUNC_RE):
        for mm in rx.finditer(main):
            path = _strip_query(_html.unescape(mm.group(1)))
            if path:
                targets.add(path)

    txt = _BLOCK_RE.sub("\n", main)
    txt = _TAG_RE.sub(" ", txt)
    txt = _html.unescape(txt)
    lines = [_INTRALINE_WS_RE.sub(" ", ln).strip() for ln in txt.split("\n")]
    txt = "\n".join(ln for ln in lines if ln)

    if len(txt) < min_chars:
        raise PFASContentError(f"<main> text too short ({len(txt)} chars) — bad fetch?")
    return txt + _LINK_SENTINEL + "\n".join(sorted(targets))


def hash_text(content: str) -> str:
    """12→16-char SHA-1 of a normalized content string (from extract_content).
    Short by design: it's a change token stored in a Sheet cell, never a security
    digest. Same idiom as wds_archiver._hash."""
    return hashlib.sha1(content.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()[:16]


def visible_text(content: str) -> str:
    """The human-readable half of a normalized content string (everything before
    the LINKS block) — what the change email diffs and displays."""
    return content.split(_LINK_SENTINEL, 1)[0]
