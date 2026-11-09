"""Every page on arborhillsmonitor.org tells the browser not to send a Referer.

MiEnviro (mienviro.michigan.gov, EGLE's document system) answers 401 to a
download request that carries `Referer: https://arborhillsmonitor.org/...` and
200 to the same request with no Referer (checked by curl 2026-11-08 on
/ncore/downloadpdf/-3909858558664182473). The browser default policy
(strict-origin-when-cross-origin) still sends the origin cross-site, so the
public-records links to MiEnviro failed for every visitor. Only `no-referrer`
fixes it.

Two halves: every built page under site/ (hand-written pages + the committed
generator output), and the two generators themselves (findings_feed,
public_comment_feed), so a regenerated page can't drop the tag between CI runs.
"""
import glob
import os
import re

import pytest

import findings_feed as ff
import public_comment_feed as pcf

_SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
_META = re.compile(r'<meta\s+name="referrer"\s+content="([^"]*)"\s*/?>', re.I)


def _assert_no_referrer(html, where):
    head = re.search(r"<head\b[^>]*>(.*?)</head>", html, re.S | re.I)
    assert head, f"{where}: no <head>"
    found = _META.findall(head.group(1))
    assert found == ["no-referrer"], (
        f"{where}: expected exactly one <meta name=\"referrer\" content=\"no-referrer\"> "
        f"in <head>, found {found}")
    # Browsers also honour a referrer meta in <body> (the last one wins), so a
    # second one anywhere would silently override this.
    everywhere = re.findall(r'<meta\b[^>]*name=["\']?referrer', html, re.I)
    assert len(everywhere) == 1, f"{where}: {len(everywhere)} referrer metas in the page"
    # The policy only governs requests issued after it is parsed, so it must come
    # before any stylesheet, script or link-bearing markup in the head.
    first_fetch = re.search(r"<(link|script|style|img|iframe)\b", head.group(1), re.I)
    meta_at = _META.search(head.group(1)).start()
    assert first_fetch is None or meta_at < first_fetch.start(), (
        f"{where}: referrer meta comes after <{first_fetch.group(1)}>")


PAGES = sorted(os.path.relpath(p, _SITE)
               for p in glob.glob(os.path.join(_SITE, "**", "*.html"), recursive=True))


def test_found_pages():
    assert len(PAGES) > 20


@pytest.mark.parametrize("rel", PAGES)
def test_every_site_page_sets_no_referrer(rel):
    with open(os.path.join(_SITE, rel), encoding="utf-8") as fh:
        _assert_no_referrer(fh.read(), rel)


def test_findings_feed_pages_set_no_referrer():
    row = ["2026-08-01", "Doc", "evidence", "R5", "notable", "A summary.",
           "180F at AHW272.", "https://mienviro.michigan.gov/ncore/downloadpdf/1",
           "Arbor Hills Remediation Area", "", ""]
    rows = ff.parse_feed_rows([row] * 7)
    pages = ff.build_pages(rows, generated_at="2026-11-08 12:00 UTC", page_size=5)
    assert len(pages) == 2
    for name, html in pages.items():
        _assert_no_referrer(html, f"findings_feed {name}")


def test_public_comment_feed_page_sets_no_referrer():
    _assert_no_referrer(pcf.render_page([], [], "2026-11-08 12:00 UTC"),
                        "public_comment_feed")


_OSM_LAYER = re.compile(r"L\.tileLayer\(OSM,\s*\{[^}]*\}\)", re.S)


def _read(rel):
    with open(os.path.join(_SITE, rel), encoding="utf-8") as fh:
        return fh.read()


# Pages that load OSM tiles (a page-content scan, not URL sanitization).
_OSM_TILES = re.compile(r"https://tile\.openstreetmap\.org/")


@pytest.mark.parametrize("rel", [p for p in PAGES if _OSM_TILES.search(_read(p))])
def test_osm_tiles_still_send_a_referer(rel):
    """OSM's tile usage policy asks browser apps to send a Referer, so the OSM
    layer overrides the page-wide no-referrer for its tile images (Leaflet 1.9's
    per-layer `referrerPolicy`). Without this the street basemap could be blocked
    if OSM starts enforcing that policy."""
    layers = _OSM_LAYER.findall(_read(rel))
    assert layers, f"{rel}: loads OSM tiles but no L.tileLayer(OSM, {{...}}) found"
    for layer in layers:
        assert 'referrerPolicy:"strict-origin-when-cross-origin"' in layer, (
            f"{rel}: OSM tile layer lacks referrerPolicy")
