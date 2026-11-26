"""site_pages: the digest's "New at Arbor Hills Monitor" box."""
from datetime import date
from types import SimpleNamespace

import digest_render as dr
import site_pages as sp

SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://arborhillsmonitor.org/</loc></url>
<url><loc> https://arborhillsmonitor.org/stream-s1/ </loc></url>
<url><loc>https://arborhillsmonitor.org/stream-s1/</loc></url>
</urlset>"""

CFG = {"digest_new_pages": {"enabled": True, "sites": [
    {"key": "monitor", "label": "arborhillsmonitor.org", "sitemap": "https://m/sitemap.xml", "disclosure": ""},
    {"key": "policy", "label": "policy.arborhillsmonitor.org", "sitemap": "https://p/sitemap.xml",
     "disclosure": "The policy site is a resident's views, separate from the automated monitor."},
]}}


def test_parse_sitemap_dedupes_and_trims():
    assert sp.parse_sitemap(SITEMAP) == ["https://arborhillsmonitor.org/",
                                         "https://arborhillsmonitor.org/stream-s1/"]


def test_parse_heading_prefers_h1_then_title():
    page = ('<title>Six Mile Records · Arbor Hills</title>'
            '<meta name="description" content="Public filings &amp; letters.">'
            '<h1>The proposed new landfill: <em>application</em> materials</h1>')
    assert sp.parse_heading(page) == ("The proposed new landfill: application materials",
                                      "Public filings & letters.")
    assert sp.parse_heading("<title>Write to EGLE · Arbor Hills</title>") == ("Write to EGLE", "")


def test_diff_sites_first_run_failure_and_new():
    new, base = sp.diff_sites({"monitor": ["a", "b"], "policy": None}, {"policy": ["x"]})
    assert new == {}                                    # monitor: first run, policy: fetch failed
    assert base == {"monitor": ["a", "b"], "policy": ["x"]}
    new, base = sp.diff_sites({"monitor": ["a", "b", "c"]}, base)
    assert new == {"monitor": ["c"]} and base["monitor"] == ["a", "b", "c"] and base["policy"] == ["x"]


def test_find_new_pages_end_to_end_and_never_raises():
    pages_html = {
        "https://m/sitemap.xml": "<loc>https://m/</loc><loc>https://m/new/</loc>",
        "https://p/sitemap.xml": "<loc>https://p/</loc>",
        "https://m/new/": '<h1>New page</h1><meta name="description" content="What it is.">',
    }

    def get(url):
        if url not in pages_html:
            raise OSError("down")
        return pages_html[url]
    pages, base = sp.find_new_pages(CFG, {"monitor": ["https://m/"], "policy": ["https://p/"]}, get=get)
    assert pages == [{"site": "monitor", "url": "https://m/new/", "title": "New page", "desc": "What it is."}]
    # a page that won't load still lists, titled by its URL
    pages_html["https://m/sitemap.xml"] += "<loc>https://m/broken/</loc>"
    pages, _ = sp.find_new_pages(CFG, base, get=get)
    assert pages == [{"site": "monitor", "url": "https://m/broken/", "title": "https://m/broken/", "desc": ""}]
    # sitemap outage: nothing listed, baseline kept
    pages, base2 = sp.find_new_pages(CFG, base, get=lambda u: (_ for _ in ()).throw(OSError()))
    assert pages == [] and base2 == base


def test_disabled_lists_nothing():
    assert sp.find_new_pages({"digest_new_pages": {"enabled": False}}, {"m": ["a"]}) == ([], {"m": ["a"]})


def _item():
    parsed = SimpleNamespace(doc_type="evidence", severity="routine", risks=[], key_data_point="k",
                             summary="k", is_correspondence=False)
    return {"parsed": parsed, "metadata": {"date_filed": "2026-11-20", "document_name": "d"},
            "link": "https://x/"}


def test_box_renders_pages_with_policy_disclosure_and_escaping():
    pages = [{"site": "policy", "url": "https://p/q.html", "title": "Open <questions>", "desc": "D & E"},
             {"site": "monitor", "url": "javascript:alert(1)", "title": "Stream", "desc": ""}]
    labels = sp.site_labels(CFG)
    html = dr.render_html([_item()], new_pages=pages, labels=labels, send_date=date(2026, 11, 29))
    assert "Announcement &middot; New at Arbor Hills Monitor" in html
    assert html.index("arborhillsmonitor.org</div>") < html.index("policy.arborhillsmonitor.org</div>")
    assert "a resident&#x27;s views, separate from the automated monitor" in html
    assert "Open &lt;questions&gt;" in html and "javascript:" not in html
    text = dr.render_text([_item()], new_pages=pages, labels=labels)
    assert "NEW AT ARBOR HILLS MONITOR:" in text and "  - Open <questions>" in text


def test_no_pages_no_announcement_no_box():
    html = dr.render_html([_item()])
    assert "New at Arbor Hills Monitor" not in html and ">Announcement<" not in html
