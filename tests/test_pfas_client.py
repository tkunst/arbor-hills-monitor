"""pfas_client.py — the content-hash normalization that the whole page-watch
rests on. These reproduce, on a SYNTHETIC fixture, the exact four assertions that
were proven against the live michigan.gov page during the build spike (the real
page can't be committed — no-data-files rule). The fixture deliberately mirrors
the real page's structure: Sitecore theme cache-busters OUTSIDE <main>, and a
content link + a "posted" date INSIDE <main>. If those aren't reproduced, the
test proves nothing.

The load-bearing property: rotating a cache-buster (theme OR content-asset) must
NOT change the hash; a real content edit (text OR a new/changed document link)
MUST change it.
"""
import pytest

import pfas_client as pc

# ~500 chars of <main> visible text (comfortably over the 200-char floor), with a
# content-asset link + background image carrying rotating cache-busters, a
# "Content posted" date, and inline chrome — the shapes the real page has.
BODY_MAIN = """
  <h1>Arbor Hills Landfill, Inc. PFAS investigation</h1>
  <p>Disclaimer: Web content may not be routinely updated on this page.</p>
  <p>EGLE site lead Jane Roe, RoeJ@Michigan.gov or 517-555-0100.</p>
  <p>Background: Arbor Hills is an active landfill. Samples were collected in
     response to an EGLE letter requesting PFAS sampling of groundwater at a
     subset of site monitoring wells, as well as leachate sampling. Groundwater
     flows south-southeast. Content posted January 2021.</p>
  <p><a href="/pfasresponse/-/media/Arbor-Hills/Map.pdf?rev=CONTENT_REV_1&amp;hash=CONTENTHASH1">Sampling Map</a></p>
  <div style="background-image:url('/pfasresponse/-/media/Arbor-Hills/Map-Preview.png?mw=768&amp;rev=CONTENT_REV_2')"></div>
  <p>Drinking water: PFAS results for the seven residential wells and one Type II
     well were below the Part 201 criteria for drinking water.</p>
  <script>console.log('inline chrome inside main should be dropped');</script>
"""


def build_page(main=BODY_MAIN, theme_rev="THEME_REV_1", theme_hash="THEMEHASH1",
               extra_head="", extra_main=""):
    return (
        "<!doctype html><html><head>\n"
        f'<link href="/theme/core.css?rev={theme_rev}&hash={theme_hash}" rel="stylesheet"/>\n'
        '<script src="/theme/lib.js?rev=THEME_REV_2&hash=THEMEHASH2"></script>\n'
        f"{extra_head}</head><body>\n"
        '<nav>Site navigation <a href="/pfasresponse?rev=n1">Home</a></nav>\n'
        f"<main>{main}{extra_main}</main>\n"
        '<footer>Footer chrome <a href="/about?rev=f1">About</a></footer>\n'
        "</body></html>"
    )


def h(page):
    return pc.hash_text(pc.extract_content(page))


BASE = build_page()
BASE_HASH = h(BASE)


# --- the four proven assertions -------------------------------------------------

def test_theme_cachebuster_rotation_does_not_change_hash():
    # A state-wide Sitecore theme redeploy rotates every theme rev/hash — all
    # OUTSIDE <main>, so the content hash must be identical.
    rotated = build_page(theme_rev="ZZZ9999", theme_hash="YYYY0000")
    assert h(rotated) == BASE_HASH


def test_content_asset_cachebuster_rotation_does_not_change_hash():
    # The Map.pdf / Map-Preview.png cache-busters live INSIDE <main>; stripping
    # the query means their rotation (a media republish) is invisible.
    rotated = BASE.replace("CONTENT_REV_1", "XXXXXXXX").replace("CONTENT_REV_2", "WWWWWWWW")
    assert rotated != BASE  # the bytes really did change
    assert h(rotated) == BASE_HASH


def test_visible_text_change_changes_hash():
    edited = BASE.replace("Content posted January 2021", "Content posted August 2026")
    assert h(edited) != BASE_HASH


def test_new_document_link_changes_hash():
    added = build_page(extra_main='<p><a href="/media/2026-Sampling-Report.pdf">New report</a></p>')
    assert h(added) != BASE_HASH


# --- corollaries ----------------------------------------------------------------

def test_link_path_change_with_identical_anchor_text_changes_hash():
    # Same anchor text ("Sampling Map"), different target path — a swapped
    # document. Visible text is unchanged, but the link-path set is in the hash,
    # so it must still trip. (This is the case a text-only hash would miss.)
    swapped = BASE.replace("Map.pdf", "Map-v2.pdf")
    assert h(swapped) != BASE_HASH


def test_noise_outside_main_does_not_change_hash():
    noisy = build_page(extra_head="<!-- deploy 2026-07-12 -->\n   \n")
    assert h(noisy) == BASE_HASH


def test_missing_main_raises_content_error():
    with pytest.raises(pc.PFASContentError):
        pc.extract_content("<html><body><p>no main here</p></body></html>")


def test_too_short_main_raises_content_error():
    with pytest.raises(pc.PFASContentError):
        pc.extract_content("<main>hi</main>", min_chars=200)


def test_hash_text_is_stable_and_16_chars():
    content = pc.extract_content(BASE)
    assert pc.hash_text(content) == pc.hash_text(content)
    assert len(pc.hash_text(content)) == 16


def test_visible_text_excludes_the_links_block():
    content = pc.extract_content(BASE)
    vis = pc.visible_text(content)
    assert "Part 201 criteria" in vis            # real content survives
    assert "/pfasresponse/-/media" not in vis    # the LINKS half is stripped off


# --- fetch_page guards (network mocked) -----------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._b = body

    def read(self):
        return self._b.encode("utf-8")


class _Opener:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc

    def open(self, url, timeout=60):
        if self._exc:
            raise self._exc
        return self._resp


def test_fetch_page_ok(monkeypatch):
    monkeypatch.setattr(pc, "_opener", lambda: _Opener(_Resp(200, "x" * 600)))
    assert pc.fetch_page("http://example") == "x" * 600


def test_fetch_page_rejects_non_200(monkeypatch):
    monkeypatch.setattr(pc, "_opener", lambda: _Opener(_Resp(404, "x" * 600)))
    with pytest.raises(pc.PFASFetchError):
        pc.fetch_page("http://example")


def test_fetch_page_rejects_short_body(monkeypatch):
    # A bot wall / partial read: 200 but tiny — must not be handed back as content.
    monkeypatch.setattr(pc, "_opener", lambda: _Opener(_Resp(200, "blocked")))
    with pytest.raises(pc.PFASFetchError):
        pc.fetch_page("http://example")


def test_fetch_page_wraps_network_error(monkeypatch):
    monkeypatch.setattr(pc, "_opener", lambda: _Opener(exc=OSError("connection reset")))
    with pytest.raises(pc.PFASFetchError):
        pc.fetch_page("http://example")
