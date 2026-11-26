"""
site_pages.py — "New at Arbor Hills Monitor" for the Sunday digest.

Each Sunday the digest lists pages that appeared on the two sites since the
last digest that went out: arborhillsmonitor.org and
policy.arborhillsmonitor.org (config `digest_new_pages.sites`). A page counts
only if it is in the site's sitemap, so drafts never show. Each listed page
shows its main heading (<h1>) and its meta description (Trisha, 2026-11-26).

State: `_meta.digest_site_urls` = {site key: [sitemap URLs]} as of the last
digest that was SENT. The watcher saves the new baseline only after the digest
goes out, so a skipped or failed digest never loses a page.

Fail-safe, never costs the digest:
  * a site whose sitemap can't be fetched or parsed keeps its old baseline and
    lists nothing this week;
  * a site with no baseline yet (first run) saves one and lists nothing;
  * a page whose own HTML can't be fetched still lists, titled by its URL.
`find_new_pages` does the network; `diff_sites` and `parse_*` are pure.
"""
from __future__ import annotations

import html as _html
import re

import requests

TIMEOUT = 10
_UA = {"User-Agent": "ArborHillsMonitor/1.0 (+https://arborhillsmonitor.org/)"}


def parse_sitemap(xml: str) -> list[str]:
    """Every <loc> URL in a sitemap, in order, de-duplicated."""
    out: list[str] = []
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "", re.IGNORECASE):
        u = _html.unescape(loc)
        if u not in out:
            out.append(u)
    return out


def _text(fragment: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_heading(page_html: str) -> tuple[str, str]:
    """(main heading, meta description) from a page; the heading falls back to
    the <title> (site suffix after ' · ' dropped), either may be ""."""
    h = page_html or ""
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", h, re.IGNORECASE | re.DOTALL)
    heading = _text(m.group(1)) if m else ""
    if not heading:
        t = re.search(r"<title\b[^>]*>(.*?)</title>", h, re.IGNORECASE | re.DOTALL)
        heading = _text(t.group(1)).split(" · ")[0].strip() if t else ""
    d = (re.search(r'<meta\s+name="description"\s+content="([^"]*)"', h, re.IGNORECASE)
         or re.search(r"<meta\s+content=\"([^\"]*)\"\s+name=\"description\"", h, re.IGNORECASE))
    desc = " ".join(_html.unescape(d.group(1)).split()) if d else ""
    return heading, desc


def diff_sites(current: dict, baseline: dict) -> tuple[dict, dict]:
    """Pure. current: {site: [urls] or None (fetch failed)}; baseline: {site:
    [urls]}. Returns ({site: [new urls]}, new_baseline). A failed site keeps its
    old baseline; a site with no baseline gets one and lists nothing."""
    new, out_base = {}, dict(baseline or {})
    for site, urls in current.items():
        if urls is None:
            continue
        old = (baseline or {}).get(site)
        if old is not None:
            seen = set(old)
            fresh = [u for u in urls if u not in seen]
            if fresh:
                new[site] = fresh
        out_base[site] = list(urls)
    return new, out_base


def _get(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers=_UA)
    r.raise_for_status()
    return r.text


def find_new_pages(cfg: dict, baseline: dict, get=_get) -> tuple[list[dict], dict]:
    """([{site, url, title, desc}], new_baseline). Never raises."""
    block = cfg.get("digest_new_pages") or {}
    if not block.get("enabled"):
        return [], dict(baseline or {})
    sites = block.get("sites") or []
    current = {}
    for s in sites:
        key, sm = s.get("key"), s.get("sitemap")
        if not key or not sm:
            continue
        try:
            current[key] = parse_sitemap(get(sm)) or None
        except Exception as e:  # noqa: BLE001 — a site outage never costs the digest
            print(f"[site_pages] {key} sitemap unavailable: {type(e).__name__}")
            current[key] = None
    new, new_base = diff_sites(current, baseline or {})
    pages = []
    for s in sites:
        for url in new.get(s.get("key"), []):
            try:
                title, desc = parse_heading(get(url))
            except Exception as e:  # noqa: BLE001
                print(f"[site_pages] page unavailable: {type(e).__name__}")
                title, desc = "", ""
            pages.append({"site": s["key"], "url": url, "title": title or url, "desc": desc})
    return pages, new_base


def site_labels(cfg: dict) -> dict:
    """{site key: (host label, disclosure sentence)} from config."""
    return {s["key"]: (s.get("label") or s["key"], s.get("disclosure") or "")
            for s in (cfg.get("digest_new_pages") or {}).get("sites") or [] if s.get("key")}
