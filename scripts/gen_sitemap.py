#!/usr/bin/env python3
"""gen_sitemap.py — regenerate site/sitemap.xml from the pages actually in site/.

A page is listed iff it declares a `<link rel="canonical">` on
https://arborhillsmonitor.org/ and is not marked `noindex`. The listed URL IS
that canonical href, so the sitemap, the canonical tags, and (by the link
convention) the internal links all name one URL form per page by construction —
there is no second list to drift. A page with no canonical (e.g. the
thermal-map-embed iframe target) stays out of the sitemap.

Run by findings-feed.yml right after the Public Records feed regenerates, so
every new page-N.html lands in the sitemap the same day it's published.
tests/test_sitemap.py fails CI if the committed sitemap.xml is stale, so a new
hand-authored page can't ship without its sitemap entry either.

Usage: python3 scripts/gen_sitemap.py   (stdlib only, no credentials)
"""
from __future__ import annotations

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_DIR = os.path.join(REPO_ROOT, "site")
SITE_ORIGIN = "https://arborhillsmonitor.org/"

_CANONICAL_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="([^"]+)"', re.IGNORECASE)
_NOINDEX_RE = re.compile(
    r'<meta\s+name="robots"\s+content="[^"]*noindex', re.IGNORECASE)


def canonical_url(page_html: str) -> str | None:
    """The page's own canonical URL if it should be in the sitemap, else None."""
    if _NOINDEX_RE.search(page_html):
        return None
    m = _CANONICAL_RE.search(page_html)
    if not m or not m.group(1).startswith(SITE_ORIGIN):
        return None
    return m.group(1)


def _natural_key(url: str) -> list:
    # page-2.html before page-10.html; the homepage sorts first (shortest path).
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", url)]


def build_sitemap(urls) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in sorted(set(urls), key=_natural_key):
        lines.append(f"  <url><loc>{url}</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def collect_urls(site_dir: str) -> list[str]:
    urls = []
    for root, _dirs, files in os.walk(site_dir):
        for name in files:
            if not name.endswith(".html"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as f:
                url = canonical_url(f.read())
            if url:
                urls.append(url)
    return urls


def main() -> None:
    site_dir = sys.argv[1] if len(sys.argv) > 1 else SITE_DIR
    urls = collect_urls(site_dir)
    with open(os.path.join(site_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(build_sitemap(urls))
    print(f"Wrote sitemap.xml with {len(set(urls))} URL(s)")


if __name__ == "__main__":
    main()
