"""The public site names its publishing entity consistently.

Publisher = Landfill Records Project LLC (the entity formed to publish this
project; confirmed 2026-10-08). The editorial byline (Trisha Kunst) is a
different relationship and is deliberately NOT touched: schema.org `founder`,
the homepage paragraph describing the separate advocacy site, and the two
thermal-map copyright-holder comments all stay as they were.

Three sources emit a `site-footer`: the committed HTML under site/, the two
Python renderers that regenerate site/public-records/ and site/public-comment/
on a schedule (findings_feed.py, public_comment_feed.py), and the data-notes
YAML that scripts/sync_data_notes.py copies into the perimeter-air footer. The
first two are pinned here directly; the YAML is covered transitively, since the
perimeter-air page's footer is asserted here and tests/test_sync_data_notes.py
fails if that page and the YAML ever disagree.

Pages whose footer is not a `site-footer` (the thermal-map pages' CC BY-4.0
`license-foot`, the wellfield explorer's bare <footer>) carry a source credit,
not a publisher statement, and are outside this file's scope on purpose.

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

# The types this site puts `publisher` on. schema.org defines `publisher` on
# CreativeWork (and Event); these four are the CreativeWork subtypes in use.
# An Organization is deliberately absent: a `publisher` nested in one is valid
# JSON that validators flag and consumers drop.
PUBLISHER_HOLDER_TYPES = {"WebSite", "DataCatalog", "Dataset", "FAQPage"}

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


def _types(node: dict) -> list[str]:
    """@type as a list (JSON-LD allows a single string or a list)."""
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def _nodes_by_id(data) -> dict:
    """Full nodes keyed by @id. A reference stub (bare {"@id"} or {"@id", "@type"}
    only) carries no properties of its own and never shadows the node it names."""
    return {d["@id"]: d for d in _walk(data) if "@id" in d and set(d) - {"@id", "@type"}}


def _resolve(value, by_id: dict, where: str) -> dict:
    """The node a `publisher` value denotes: a plain-text name, an inline node,
    or an @id reference. Any @id (typed stub or bare) must resolve in-document."""
    if isinstance(value, str):  # schema.org allows Text for publisher
        return {"name": value}
    assert isinstance(value, dict), f"{where}: unexpected publisher value {value!r}"
    if "@id" in value:
        assert value["@id"] in by_id, f"{where}: dangling @id reference {value!r}"
        return by_id[value["@id"]]
    return value


def _publishers(data, where: str) -> list[tuple[list[str], dict]]:
    """(holder @types, resolved publisher node) for every `publisher`, list-valued too."""
    by_id = _nodes_by_id(data)
    out = []
    for d in _walk(data):
        pub = d.get("publisher")
        if pub is None:
            continue
        for p in (pub if isinstance(pub, list) else [pub]):
            out.append((_types(d), _resolve(p, by_id, where)))
    return out


def test_jsonld_page_list_is_current():
    found = sorted(
        os.path.relpath(p, SITE) for p in _site_pages() if _JSONLD_RE.search(_read(p)))
    assert found == sorted(JSONLD_PAGES), "update JSONLD_PAGES when a page gains or loses JSON-LD"


def test_every_jsonld_page_names_the_llc_as_publisher_on_a_creative_work():
    for rel in JSONLD_PAGES:
        blocks = _JSONLD_RE.findall(_read(os.path.join(SITE, rel)))
        assert blocks, f"{rel}: no JSON-LD block"
        for block in blocks:
            data = json.loads(block)  # a syntax error here fails silently in browsers
            pubs = _publishers(data, rel)
            assert any(p.get("name") == LLC for _, p in pubs), f"{rel}: no publisher named {LLC!r}"
            for holder_types, _ in pubs:
                assert set(holder_types) & PUBLISHER_HOLDER_TYPES, (
                    f"{rel}: `publisher` sits on {holder_types!r}, not one of the "
                    f"types this site puts it on ({sorted(PUBLISHER_HOLDER_TYPES)})")
            # Every @id reference in the graph (parentOrganization, ...) resolves too.
            by_id = _nodes_by_id(data)
            for d in _walk(data):
                if "@id" in d:
                    assert d["@id"] in by_id, f"{rel}: dangling @id reference {d['@id']!r}"


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
    assert "public-records/index.html" in pages_with_footer, "public-records index lost its footer"


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
