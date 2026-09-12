"""
gfl_info_site_client.py — fetch + normalize for the GFL "informational website"
change-watch (arborhillslandfill.com, Stream S / ADR 041).

GFL launched a professionally-produced narrative site on 2026-09-10 to address
community questions about the proposed Arbor Hills expansion (six pages: Home,
What We Do, Landfill Improvements, Community Relations, FAQ, Location & Hours).
It is a WordPress site whose FAQ answers are COLLAPSED ACCORDIONS — but the
answers are SERVER-RENDERED in the HTML (only CSS-collapsed), so a plain HTTP
GET captures the substance; no headless browser is needed (verified live, spike
2026-09-11: the FAQ setback/PFAS/school-monitor claims are all in the raw DOM).

This is a NEW EXTERNAL (non-EGLE) source — it NEVER routes through
egle_doc_parser (the EGLE-document parsing base). It is the operator's own
marketing narrative, not a regulatory filing. Like pfas_client.py (the EGLE PFAS
page-watch), this module only FETCHES + NORMALIZES; the diff / alerting / state
lives in gfl_info_site_watcher.py.

CLOUDFLARE. The site is fronted by Cloudflare. Two things follow:

  1. FETCH PATH. `requests` with a browser User-Agent + normal headers renders
     the real content (verified from BOTH a residential IP and a datacenter IP,
     2026-09-11). Cloudflare's "Attention Required" managed challenge is an
     IP/ASN-reputation block, not a UA block — so it can still bite a specific
     runner IP even though it passes here. We do NOT try to defeat a challenge:
     a challenge page has no <main id=SiteContent> content region and little
     text, so normalize_content() raises GFLInfoSiteContentError on it, and the
     watcher treats that like any other bad fetch (skip-and-warn once a baseline
     exists; loud on the activation run). See gfl_info_site_watcher.py.

  2. PER-REQUEST NOISE. Every fetch carries rotating per-request tokens —
     Cloudflare's /cdn-cgi/challenge-platform beacon + __cf* tokens, WordPress
     `nonce=` attributes, and `?ver=`/cache-buster query strings on assets —
     none of which are content. Hashing the raw page would fire a false
     "the site changed!" alert on every single run. So we normalize:
       a. isolate the single <main> content region (drops nav/header/footer/the
          Cloudflare beacon, which live outside <main>),
       b. decode any Cloudflare-obfuscated (data-cfemail / __cf_email__) or
          mailto: email address FIRST (so a future "email contact" GFL adds is
          recovered in cleartext and surfaces in the diff — the phone-only site
          exposes no email today; the launch baseline flags this),
       c. drop <script>/<style>/comments,
       d. reduce to visible text (block-per-line, so the change email's diff
          reads line-by-line) PLUS the sorted set of link/asset target PATHS
          with the query string stripped (a rotating cache-buster is invisible;
          a genuinely NEW document/page link still trips the hash),
       e. hash that.
     PROVEN LIVE (spike 2026-09-11): fetching each of the six pages twice yields
     an IDENTICAL normalized hash while the raw HTML differs every time. That is
     the whole test spec (test_gfl_info_site reproduces it on a synthetic
     fixture carrying the same per-request noise — the real page can't be
     committed, per this repo's data-file rule).

DISCOVERY. The launch page set can grow/shrink, and the watch must alert on a
NEW or REMOVED page — so the page list is discovered each run, not hardcoded:
  - /sitemap.xml is a WP/Rank-Math sitemap INDEX (<sitemapindex>) pointing at
    child sitemaps (page-sitemap.xml, ...); discover_page_urls() follows the
    index one level to collect the actual page <loc>s,
  - falls back to a nav-crawl of the home page (same-host <a href> links) if the
    sitemap can't be read,
  - always unions in the configured seed_paths (the six launch pages) as a
    FLOOR, so the core pages are checked even if discovery degrades,
  - filters out ignore_path_patterns (the derived WP index pages /sitemap and
    /sitemap/blog, .kml/.xsl assets — noise that isn't substantive content).
REMOVAL is confirmed by the watcher via an HTTP 404/410 on the page itself, NOT
by mere absence from a (possibly incomplete or transiently-failed) sitemap — so
a flaky sitemap fetch can never fire a false "page removed" alert.

Stdlib + requests only (requests is already a dep — nsite_client uses it). No
new dependency, no Anthropic, no Drive/OAuth: the deliverable is the ALERT, and
the Sheet tab row carries the full normalized text as both the diff basis and a
durable dated snapshot (the pfas_watcher idiom).
"""
from __future__ import annotations

import hashlib
import html as _html
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import requests

# A real browser UA — the fetch path that passes Cloudflare (spike 2026-09-11).
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# A real content page is tens of KB; a body much smaller than this is a bot wall
# / error page / partial read, not the page — refuse it (see fetch_page).
_MIN_BODY_BYTES = 500
# The smallest real page (/location-hours) normalizes to ~600 chars of <main>
# visible text; 150 is a safe floor that still rejects a challenge interstitial.
_DEFAULT_MIN_CHARS = 150

_MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.S | re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TARGET_RE = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"', re.I)
_URLFUNC_RE = re.compile(r"url\(\s*['\"]?([^)'\"]+?)['\"]?\s*\)", re.I)
# Closing/opening block tags + <br> + accordion <button>/<dt>/<dd> become
# newlines so the normalized text keeps one logical block per line (readable
# unified diff); every other tag is dropped.
_BLOCK_RE = re.compile(
    r"</?(?:p|div|li|ul|ol|h[1-6]|section|article|tr|table|br|main|dt|dd|button)\b[^>]*>",
    re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_INTRALINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_CFEMAIL_RE = re.compile(r'(?:data-cfemail|data-cfemail=)["\']?([0-9a-fA-F]{6,})', re.I)
_MAILTO_RE = re.compile(r'href\s*=\s*["\']mailto:([^"\'?>]+)', re.I)
_A_HREF_RE = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\']', re.I)
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S | re.I)

# Separators between the three halves of a normalized content string. PRINTABLE
# on purpose: the whole string is stored verbatim in a Google Sheets cell (the
# diff basis for next run), and the Sheets API rejects NULL/control characters.
# They only need to never occur in real page prose / a URL / an email, which
# these don't. (Same reasoning as pfas_client._LINK_SENTINEL.)
_LINK_SENTINEL = "\n[gfl-info-site:links]\n"
_EMAIL_SENTINEL = "\n[gfl-info-site:emails]\n"


class GFLInfoSiteFetchError(RuntimeError):
    """The page couldn't be fetched cleanly (network error, timeout, non-2xx, or
    a body too short to be the real page). TRANSIENT — the watcher skips-and-warns
    rather than diffing it, so a blip or a Cloudflare wall never fires a spurious
    'page changed' alert. (A first-ever run with no baseline treats it as loud
    instead — a persistent block must surface on activation, not no-op forever.)
    Carries `.status` (the HTTP status, or None) so the watcher can special-case
    a 404/410 as a removed page."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GFLInfoSiteGone(GFLInfoSiteFetchError):
    """The page returned HTTP 404/410 — a CONFIRMED removal, not a transient
    blip. Raised as its own subclass so the watcher can fire a 'page removed'
    alert on it while still treating every other fetch failure as skip-and-warn.
    Removal is decided by this real HTTP status on the page itself, never by mere
    absence from the sitemap (which can be incomplete or transiently fail)."""


class GFLInfoSiteContentError(RuntimeError):
    """The body fetched but had no usable <main> content region (or its visible
    text was too short). Same skip-not-diff treatment as a transient fetch error:
    a missing <main>/short body is far more likely a Cloudflare challenge
    interstitial or a served error page than a real content edit — this IS the
    challenge detector (gate on positive content PRESENCE, never on the presence
    of an artifact string, which false-positives on the ambient CF beacon)."""


@dataclass
class DiscoveryResult:
    """The discovered page-URL set for one run, plus how it was obtained (for the
    log). `urls` is always non-empty in practice because seed_paths is a floor;
    `source` records whether the sitemap, a nav crawl, or only the seed floor
    supplied them, so a degraded discovery is visible in the run log."""
    urls: list[str]
    source: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_page(url: str, timeout: int = 45) -> str:
    """GET `url` and return its body text. Raises GFLInfoSiteGone on HTTP 404/410
    (a confirmed removal), GFLInfoSiteFetchError on any other network/HTTP
    failure or a suspiciously short body — never returns a partial/error body for
    the caller to mistake for changed content."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — network / timeout → transient
        raise GFLInfoSiteFetchError(f"GET {url} failed: {e}") from e
    status = r.status_code
    if status in (404, 410):
        raise GFLInfoSiteGone(f"GET {url} returned HTTP {status}", status=status)
    if status != 200:
        raise GFLInfoSiteFetchError(f"GET {url} returned HTTP {status}", status=status)
    body = r.text
    if len(body) < _MIN_BODY_BYTES:
        raise GFLInfoSiteFetchError(
            f"GET {url} body too short ({len(body)} bytes) — bad fetch?", status=status)
    return body


def _fetch_text_or_none(url: str, timeout: int) -> str | None:
    """Best-effort GET for discovery (sitemaps / nav) — returns None on any
    failure instead of raising, because a discovery miss degrades gracefully
    (the seed floor still covers the core pages) and must never abort a run."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        if r.status_code != 200 or len(r.text) < 20:
            return None
        return r.text
    except Exception:  # noqa: BLE001 — discovery is best-effort
        return None


# ---------------------------------------------------------------------------
# URL canonicalization
# ---------------------------------------------------------------------------


def canonical_url(url: str, base_url: str = "") -> str | None:
    """Canonicalize a URL for stable identity across runs: absolutize against
    base_url, lowercase scheme+host, drop query + fragment, strip a trailing
    slash EXCEPT on the root path. Returns None for a non-http(s) URL (mailto:,
    tel:, javascript:, #fragment-only) so those never enter the watch set."""
    if not url:
        return None
    absolute = urljoin(base_url + "/" if base_url and not base_url.endswith("/") else base_url, url) if base_url else url
    p = urlparse(absolute)
    if p.scheme not in ("http", "https"):
        return None
    if not p.netloc:
        return None
    path = re.sub(r"/{2,}", "/", p.path) or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))


def _same_host(url: str, base_url: str) -> bool:
    return urlparse(url).netloc.lower() == urlparse(base_url).netloc.lower()


def _matches_any(path: str, patterns: list[str]) -> bool:
    for pat in patterns or []:
        try:
            if re.search(pat, path):
                return True
        except re.error:
            # A malformed pattern is treated as a literal substring, never fatal.
            if pat in path:
                return True
    return False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _urls_from_sitemap(sitemap_url: str, base_url: str, timeout: int,
                       _depth: int = 0) -> list[str]:
    """Follow a sitemap ONE level: if it's a <sitemapindex>, fetch each child
    sitemap and collect their <loc>s; if it's a <urlset>, collect its <loc>s.
    _depth bounds recursion so a self-referential/looping index can't spin."""
    text = _fetch_text_or_none(sitemap_url, timeout)
    if not text:
        return []
    is_index = "<sitemapindex" in text.lower()
    locs = [m for m in _LOC_RE.findall(text)]
    if is_index and _depth < 2:
        out: list[str] = []
        for child in locs:
            child = _html.unescape(child.strip())
            # Only follow XML sitemaps; skip .kml / .xsl and the like.
            if child.lower().endswith((".kml", ".xsl")):
                continue
            # SSRF guard: NEVER fetch a child sitemap on a different host than the
            # site we're watching. A child <loc> comes from the fetched index's
            # own bytes, so a compromised/spoofed index could otherwise point the
            # runner at an internal host (e.g. a cloud metadata endpoint). Page
            # <loc>s are host-filtered downstream, but the child FETCH itself must
            # be constrained here — this is the only fetch not already host-bound.
            if not _same_host(child, base_url):
                continue
            out.extend(_urls_from_sitemap(child, base_url, timeout, _depth + 1))
        return out
    return [_html.unescape(l.strip()) for l in locs]


def _urls_from_nav(base_url: str, timeout: int) -> list[str]:
    """Fallback discovery: same-host <a href> links off the home page."""
    text = _fetch_text_or_none(base_url, timeout)
    if not text:
        return []
    out = []
    for href in _A_HREF_RE.findall(text):
        cu = canonical_url(_html.unescape(href), base_url)
        if cu and _same_host(cu, base_url):
            out.append(cu)
    return out


def discover_page_urls(base_url: str, sitemap_url: str, seed_paths: list[str],
                       ignore_patterns: list[str] | None = None,
                       timeout: int = 30) -> DiscoveryResult:
    """Return the canonical page-URL set to watch this run: the sitemap's pages
    (index followed one level) — or a nav crawl if the sitemap can't be read —
    UNIONED with the configured seed_paths floor, minus ignore_path_patterns and
    off-host URLs. Never returns empty: seed_paths guarantees the core pages."""
    ignore_patterns = ignore_patterns or []
    notes: list[str] = []

    raw = _urls_from_sitemap(sitemap_url, base_url, timeout)
    source = "sitemap"
    if not raw:
        raw = _urls_from_nav(base_url, timeout)
        source = "nav" if raw else "seed-only"
        if source == "seed-only":
            notes.append("sitemap AND nav discovery both failed — using seed floor only")
        else:
            notes.append("sitemap discovery failed — fell back to nav crawl")

    seeds = [canonical_url(p, base_url) for p in (seed_paths or [])]
    combined = list(raw) + [s for s in seeds if s]

    seen: set[str] = set()
    kept: list[str] = []
    for u in combined:
        cu = canonical_url(u, base_url)
        if not cu or not _same_host(cu, base_url):
            continue
        path = urlparse(cu).path or "/"
        if _matches_any(path, ignore_patterns):
            continue
        if cu in seen:
            continue
        seen.add(cu)
        kept.append(cu)
    kept.sort()
    return DiscoveryResult(urls=kept, source=source, notes=notes)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _decode_cfemail(hexstr: str) -> str | None:
    """Decode a Cloudflare-obfuscated email (data-cfemail hex): first byte is the
    XOR key, the rest is the email XOR'd with it. Deterministic per address (not
    per-request), so it never adds diff noise — but it recovers the cleartext so
    a future 'email contact' surfaces in the alert. Returns None on malformed
    input (fail-safe: the '[email protected]' visible text still surfaces that an
    email was added, even if this decode can't recover the address)."""
    try:
        b = bytes.fromhex(hexstr)
        if len(b) < 2:
            return None
        key = b[0]
        out = "".join(chr(c ^ key) for c in b[1:])
        return out if "@" in out and "." in out else None
    except Exception:  # noqa: BLE001 — malformed token → fall back to visible text
        return None


def extract_emails(main_html: str) -> list[str]:
    """Every email address discoverable in a <main> fragment: mailto: links plus
    Cloudflare-obfuscated data-cfemail tokens, decoded. Sorted + de-duplicated,
    lowercased. Called BEFORE tags are stripped (the tokens live in attributes)."""
    found: set[str] = set()
    for m in _MAILTO_RE.finditer(main_html):
        addr = _html.unescape(m.group(1)).strip().lower()
        if "@" in addr:
            found.add(addr)
    for m in _CFEMAIL_RE.finditer(main_html):
        addr = _decode_cfemail(m.group(1))
        if addr:
            found.add(addr.strip().lower())
    return sorted(found)


def _strip_query(u: str) -> str:
    """Path only: drop ?query and #fragment so an asset's rotating cache-buster
    (?ver=, rev, hash, ...) is not mistaken for a content change; a genuinely new
    document/page still has a new PATH, which does register."""
    return u.split("#", 1)[0].split("?", 1)[0].strip()


def extract_content(html_text: str, min_chars: int = _DEFAULT_MIN_CHARS) -> str:
    """Normalize a fetched page to its stable, comparable content string:
    <main> visible text (block-per-line) + the sorted set of link/asset target
    paths (query stripped) + any discovered email addresses. Raises
    GFLInfoSiteContentError if there's no <main> or the text is too short (a
    Cloudflare challenge / error page — the caller must NOT diff it)."""
    m = _MAIN_RE.search(html_text)
    if not m:
        raise GFLInfoSiteContentError("no <main> content region found")
    main = m.group(1)

    emails = extract_emails(main)

    main = _SCRIPT_STYLE_RE.sub(" ", main)
    main = _COMMENT_RE.sub(" ", main)

    targets: set[str] = set()
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
        raise GFLInfoSiteContentError(
            f"<main> text too short ({len(txt)} chars) — bad fetch / challenge?")

    content = txt + _LINK_SENTINEL + "\n".join(sorted(targets))
    if emails:
        content += _EMAIL_SENTINEL + "\n".join(emails)
    return content


def hash_text(content: str) -> str:
    """16-char SHA-1 of a normalized content string. Short by design: a change
    token stored in a Sheet cell, never a security digest (same idiom as
    pfas_client.hash_text)."""
    return hashlib.sha1(
        content.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()[:16]


def visible_text(content: str) -> str:
    """The human-readable half of a normalized content string (everything before
    the links block) — what the change email diffs and displays."""
    return content.split(_LINK_SENTINEL, 1)[0]


def emails_in(content: str) -> list[str]:
    """The email addresses recorded in a normalized content string, if any."""
    if _EMAIL_SENTINEL not in content:
        return []
    tail = content.split(_EMAIL_SENTINEL, 1)[1]
    return [ln.strip() for ln in tail.splitlines() if ln.strip()]


# A few nicer labels for known slugs where title-casing reads wrong ("Faq").
# Anything not listed falls back to the derived title-case, so a newly-discovered
# page still gets a sensible label with no config change.
_KNOWN_LABELS = {
    "faq": "FAQ",
    "what-we-do": "What We Do",
    "landfill-improvements": "Landfill Improvements",
    "location-hours": "Location & Hours",
    "community": "Community Relations",
    "privacy-policy": "Privacy Policy",
}


def page_label(url: str) -> str:
    """A human page label derived from the URL path, for the alert subject/body.
    '/' -> 'Home'; '/what-we-do' -> 'What We Do'. Uses a small known-slug map for
    labels that title-case wrong (FAQ), falling back to derived title-case — so a
    newly-discovered page still gets a sensible label with no config change."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "Home"
    slug = path.split("/")[-1]
    if slug.lower() in _KNOWN_LABELS:
        return _KNOWN_LABELS[slug.lower()]
    words = re.split(r"[-_]+", slug)
    return " ".join(w.capitalize() for w in words if w) or slug
