"""The public site names its publishing entity consistently.

Publisher = Landfill Records Project LLC (the entity formed to publish this
project; confirmed 2026-10-08). The editorial byline (Trisha Kunst) is a
different relationship and is deliberately NOT touched: schema.org `founder`,
the homepage paragraph describing the separate advocacy site, and the two
thermal-map copyright-holder comments all stay as they were.

Three sources emit a `site-footer`: the committed HTML under site/, the two
Python renderers that regenerate site/public-records/ and site/public-comment/
on a schedule (findings_feed.py, public_comment_feed.py), and the data-notes
YAML that scripts/sync_data_notes.py copies into the perimeter-air footer. All
three are pinned here so a regeneration can never silently drop the entity.
"""
import glob
import json
import os
import re

import findings_feed as ff
import public_comment_feed as pcf

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(_REPO, "site")
LLC = "Landfill Records Project LLC"
FOOTER_LEAD = f"An independent project of {LLC}."

_FOOTER_RE = re.compile(r'<footer class="site-footer">(.*?)</footer>', re.S)
_JSONLD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def _site_pages() -> list[str]:
    pages = sorted(glob.glob(os.path.join(SITE, "**", "*.html"), recursive=True))
    assert pages, "no site pages found"
    return pages


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _names_llc_as_publisher(node) -> bool:
    """True if any `publisher` anywhere in the (nested) JSON-LD names the LLC."""
    if isinstance(node, dict):
        pub = node.get("publisher")
        if isinstance(pub, dict) and pub.get("name") == LLC:
            return True
        return any(_names_llc_as_publisher(v) for v in node.values())
    if isinstance(node, list):
        return any(_names_llc_as_publisher(v) for v in node)
    return False


def test_every_jsonld_block_parses_and_names_the_llc_as_publisher():
    seen = 0
    for page in _site_pages():
        for block in _JSONLD_RE.findall(_read(page)):
            data = json.loads(block)  # a syntax error here fails silently in browsers
            assert _names_llc_as_publisher(data), f"{page}: JSON-LD has no publisher named {LLC!r}"
            seen += 1
    assert seen >= 7, f"expected the seven known JSON-LD pages, found {seen}"


def test_every_site_footer_names_the_llc():
    footers = 0
    for page in _site_pages():
        for body in _FOOTER_RE.findall(_read(page)):
            assert FOOTER_LEAD in body, f"{page}: footer does not name {LLC!r}"
            footers += 1
    assert footers >= 55, f"expected every page footer, found {footers}"


def test_findings_feed_renderer_footer_names_the_llc():
    html = ff.render_page([], 1, 1, 0, "2026-10-23 00:00 UTC")
    footer = _FOOTER_RE.search(html)
    assert footer and FOOTER_LEAD in footer.group(1)


def test_public_comment_feed_renderer_footer_names_the_llc():
    html = pcf.render_page([], [], "2026-10-23 00:00 UTC")
    footer = _FOOTER_RE.search(html)
    assert footer and FOOTER_LEAD in footer.group(1)


def test_founder_byline_and_copyright_holder_are_untouched():
    home = _read(os.path.join(SITE, "index.html"))
    assert '"founder":{"@type":"Person","name":"Trisha Kunst"}' in home
    assert "Trisha Kunst, the resident who maintains this project, also publishes" in home
    for page in ("thermal-map", "thermal-map-embed"):
        src = _read(os.path.join(SITE, page, "index.html"))
        # The code/content copyright holder is a separate decision from who
        # publishes the site (see TASKS: code license undecided).
        header_comment = src.split("-->", 1)[0]
        assert "Copyright (c) 2026 Trisha Kunst" in header_comment
        assert LLC not in header_comment
