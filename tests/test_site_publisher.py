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

schema.org defines `publisher` on CreativeWork (WebSite, Dataset, DataCatalog,
FAQPage), not on Organization: a `publisher` nested inside an Organization is
syntactically fine but validators flag it and consumers drop it, so the LLC
must be named on the CreativeWork node itself, never inside an Organization.
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

# Pages that must carry a site-footer: the hand-written ones plus the single
# public_comment_feed.py page. The 42 site/public-records/ pages are generated
# by findings_feed.py (their count moves with the case file), so they are
# checked wherever found but not listed here.
FOOTER_PAGES = (
    "index.html",
    "2022-event/index.html",
    "data/index.html",
    "data/flagged-wells/index.html",
    "data/perimeter-air/index.html",
    "data/wellfield/index.html",
    "data/wellhead-h2s/index.html",
    "faq/index.html",
    "glossary/index.html",
    "hydrogen-sulfide/index.html",
    "public-comment/index.html",
    "sem-inspection-2026-06-16/index.html",
    "unanswered-questions/index.html",
)
JSONLD_PAGES = (
    "index.html",
    "data/index.html",
    "data/flagged-wells/index.html",
    "data/perimeter-air/index.html",
    "data/wellfield/index.html",
    "data/wellhead-h2s/index.html",
    "faq/index.html",
)

# schema.org types on which `publisher` is defined (all CreativeWork subtypes).
CREATIVE_WORK_TYPES = {"WebSite", "DataCatalog", "Dataset", "FAQPage"}

_FOOTER_RE = re.compile(
    r'<footer[^>]*\bclass="[^"]*\bsite-footer\b[^"]*"[^>]*>(.*?)</footer>', re.DOTALL)
_JSONLD_RE = re.compile(
    r'<script[^>]*\btype=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL)


def _site_pages() -> list[str]:
    pages = sorted(glob.glob(os.path.join(SITE, "**", "*.html"), recursive=True))
    assert pages, "no site pages found"
    return pages


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _walk(node):
    """Every dict in a (nested) JSON-LD value."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _nodes_by_id(data) -> dict:
    """Real nodes keyed by @id; a bare {"@id": ...} reference never shadows one."""
    return {d["@id"]: d for d in _walk(data) if "@id" in d and "@type" in d}


def _resolve(value, by_id: dict, where: str) -> dict:
    """A node or an @id reference to one; a reference that resolves nowhere fails."""
    if "@type" in value:
        return value
    assert value.get("@id") in by_id, f"{where}: dangling @id reference {value!r}"
    return by_id[value["@id"]]


def _publishers(data, where: str) -> list[tuple[str, dict]]:
    """(holder @type, resolved publisher node) for every `publisher`, list-valued too."""
    by_id = _nodes_by_id(data)
    out = []
    for d in _walk(data):
        pub = d.get("publisher")
        if pub is None:
            continue
        for p in (pub if isinstance(pub, list) else [pub]):
            out.append((d.get("@type"), _resolve(p, by_id, where)))
    return out


def test_every_jsonld_page_names_the_llc_as_publisher_on_a_creative_work():
    for rel in JSONLD_PAGES:
        blocks = _JSONLD_RE.findall(_read(os.path.join(SITE, rel)))
        assert blocks, f"{rel}: no JSON-LD block"
        for block in blocks:
            data = json.loads(block)  # a syntax error here fails silently in browsers
            pubs = _publishers(data, rel)
            assert any(p.get("name") == LLC for _, p in pubs), f"{rel}: no publisher named {LLC!r}"
            for holder, _ in pubs:
                assert holder in CREATIVE_WORK_TYPES, (
                    f"{rel}: `publisher` is a CreativeWork property, not one of {holder!r}")
            # Every other @id reference in the graph (parentOrganization, ...) resolves too.
            by_id = _nodes_by_id(data)
            for d in _walk(data):
                if "@id" in d and "@type" not in d:
                    _resolve(d, by_id, rel)


def test_every_jsonld_block_anywhere_parses():
    for page in _site_pages():
        for block in _JSONLD_RE.findall(_read(page)):
            json.loads(block)


def test_every_site_footer_names_the_llc():
    pages_with_footer = set()
    for page in _site_pages():
        for body in _FOOTER_RE.findall(_read(page)):
            assert FOOTER_LEAD in body, f"{page}: footer does not name {LLC!r}"
            pages_with_footer.add(os.path.relpath(page, SITE))
    missing = set(FOOTER_PAGES) - pages_with_footer
    assert not missing, f"pages lost their site-footer: {sorted(missing)}"
    assert "public-records/index.html" in pages_with_footer


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
        comments = [c for c in re.findall(r"<!--(.*?)-->", src, re.DOTALL) if "Copyright (c)" in c]
        assert len(comments) == 1, f"{page}: expected exactly one copyright comment"
        assert "Copyright (c) 2026 Trisha Kunst" in comments[0]
        assert LLC not in comments[0]
