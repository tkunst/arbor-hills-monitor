"""Every site page names a canonical URL: its own address, except the embed copy
of the thermal map (points at /thermal-map/) and the moved proposed-protections
stub (points at the policy subdomain). Search Console flags pages without one as
"Duplicate without user-selected canonical" (2026-10-26)."""
import os
import re

SITE = os.path.join(os.path.dirname(__file__), "..", "site")
BASE = "https://arborhillsmonitor.org/"
EXCEPTIONS = {
    "thermal-map-embed/index.html": BASE + "thermal-map/",
    "proposed-protections/index.html": "https://policy.arborhillsmonitor.org/proposed-protections/",
}
CANON = re.compile(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', re.I)


def _pages():
    for root, _dirs, files in os.walk(SITE):
        for f in files:
            if f.endswith(".html"):
                yield os.path.relpath(os.path.join(root, f), SITE).replace(os.sep, "/")


def test_every_page_has_exactly_one_correct_canonical():
    pages = list(_pages())
    assert pages
    for rel in pages:
        with open(os.path.join(SITE, rel), encoding="utf-8") as fh:
            found = CANON.findall(fh.read())
        expected = EXCEPTIONS.get(rel, BASE + rel[: -len("index.html")] if rel.endswith("index.html") else BASE + rel)
        assert found == [expected], f"{rel}: canonical {found}, expected [{expected}]"
