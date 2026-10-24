"""Every page on arborhillsmonitor.org carries the legal footer from
footer/canonical-footer.json: links to the five legal pages and the short
disclaimer. Catches pages that miss a footer update (e.g. the paginated
public-records pages, 2026-10-24).

The same canonical file is vendored by the policy site (tkunst/better-arbor-hills),
whose footer-drift workflow fails if its copy differs from this one.
"""
import glob
import html
import json
import os
import re

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SITE = os.path.join(_REPO, "site")
CANON = json.load(open(os.path.join(_REPO, "footer", "canonical-footer.json"), encoding="utf-8"))

# Pages with no footer by design: a meta-refresh redirect, and the iframe embed
# (links inside an embed would navigate within someone else's page).
NO_FOOTER = {"proposed-protections/index.html", "thermal-map-embed/index.html"}
# Full-screen map pages carry the legal links in their license bar, not a full footer.
LICENSE_BAR_ONLY = {"thermal-map/index.html", "sem-exceedance-map/index.html"}


def _norm(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = s.replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def _pages():
    out = []
    for p in sorted(glob.glob(os.path.join(_SITE, "**", "*.html"), recursive=True)):
        rel = os.path.relpath(p, _SITE)
        if rel in NO_FOOTER:
            continue
        txt = open(p, encoding="utf-8").read()
        if "<footer" not in txt:
            continue  # page fragments / non-site HTML
        out.append((rel, txt))
    return out


def _footer(txt):
    blocks = re.findall(r"<footer\b.*?</footer>", txt, re.S)
    return "\n".join(blocks)


def _has_link(footer, path):
    base = CANON["legal_links_base_url"]
    rel = path.strip("/") + "/"
    return bool(re.search(
        r'href="(?:%s)?(?:/|(?:\.\./)*)%s"' % (re.escape(base), re.escape(rel)), footer))


PAGES = _pages()


def test_found_pages():
    assert len(PAGES) > 20


@pytest.mark.parametrize("rel,txt", PAGES, ids=[p[0] for p in PAGES])
def test_footer_has_all_legal_links(rel, txt):
    f = _footer(txt)
    missing = [l["label"] for l in CANON["legal_links"] if not _has_link(f, l["path"])]
    assert not missing, f"{rel}: footer missing legal links {missing}"


@pytest.mark.parametrize("rel,txt", [p for p in PAGES if p[0] not in LICENSE_BAR_ONLY],
                         ids=[p[0] for p in PAGES if p[0] not in LICENSE_BAR_ONLY])
def test_footer_has_canonical_disclaimer(rel, txt):
    f = _norm(_footer(txt))
    assert _norm(CANON["monitor_disclaimer"]) in f, f"{rel}: footer disclaimer missing or differs from canon"
    assert f.count(_norm(CANON["monitor_disclaimer"])) == 1, f"{rel}: disclaimer appears more than once"


@pytest.mark.parametrize("rel,txt", PAGES, ids=[p[0] for p in PAGES])
def test_one_legal_nav(rel, txt):
    assert _footer(txt).count('class="legal-nav"') <= 1, f"{rel}: legal links appear more than once"
