"""scripts/gen_sitemap.py — sitemap is derived from canonical tags, and the
committed site/sitemap.xml must match what the generator produces."""
import importlib.util
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "gen_sitemap", os.path.join(_REPO, "scripts", "gen_sitemap.py"))
gs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gs)

SITE = os.path.join(_REPO, "site")


def test_canonical_url_rules():
    home = '<link rel="canonical" href="https://arborhillsmonitor.org/">'
    assert gs.canonical_url(home) == "https://arborhillsmonitor.org/"
    assert gs.canonical_url("<title>no canonical</title>") is None
    assert gs.canonical_url(
        '<link rel="canonical" href="https://example.com/x/">') is None
    assert gs.canonical_url(
        '<meta name="robots" content="noindex">' + home) is None


def test_build_sitemap_natural_order_and_dedupe():
    base = "https://arborhillsmonitor.org/public-records/"
    xml = gs.build_sitemap([base + "page-10.html", base + "page-2.html",
                            base, "https://arborhillsmonitor.org/", base])
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    assert locs == ["https://arborhillsmonitor.org/", base,
                    base + "page-2.html", base + "page-10.html"]


def test_committed_sitemap_is_current():
    """Adding/removing a page without re-running gen_sitemap.py fails here."""
    with open(os.path.join(SITE, "sitemap.xml"), encoding="utf-8") as f:
        committed = f.read()
    assert committed == gs.build_sitemap(gs.collect_urls(SITE)), (
        "site/sitemap.xml is stale -- run: python3 scripts/gen_sitemap.py")


# Editorial pages that are in the sitemap but deliberately NOT linked from the
# monitor's pages (policy material is routed to the separate advocacy site from
# the homepage). Linking them is an editorial call, not a crawl fix -- remove an
# entry here once a page links to it.
KNOWN_UNLINKED = {"unanswered-questions/", "proposed-protections/"}


def test_every_sitemap_page_is_linked_from_another_page():
    """No orphans: each listed page is reachable by an internal link."""
    linked = set()
    for root, _dirs, files in os.walk(SITE):
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(root, name)
            rel_dir = os.path.relpath(root, SITE)
            with open(path, encoding="utf-8") as f:
                hrefs = re.findall(r'href="([^"#?]+)', f.read())
            for h in hrefs:
                if h.startswith(gs.SITE_ORIGIN):
                    target = h[len(gs.SITE_ORIGIN):]
                elif h.startswith("/"):
                    target = h.lstrip("/")
                elif "://" in h or h.startswith("mailto:"):
                    continue
                else:
                    target = os.path.normpath(os.path.join(rel_dir, h))
                    target = "" if target == "." else target
                    if h.endswith("/") and target:
                        target += "/"
                linked.add((path, target.removesuffix("index.html")))
    for url in gs.collect_urls(SITE):
        rel = url[len(gs.SITE_ORIGIN):]
        if rel == "" or rel in KNOWN_UNLINKED:
            continue  # the homepage is the root, not a link target
        own = os.path.join(SITE, rel if rel.endswith(".html") else rel + "index.html")
        assert any(t == rel and p != own for p, t in linked), f"orphan page: {url}"
