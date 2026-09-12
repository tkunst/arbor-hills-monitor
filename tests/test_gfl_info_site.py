"""Tests for the GFL informational-website change-watch (ADR 041):

  - gfl_info_site_client: URL canonicalization, sitemap-index discovery (+ nav
    and seed-only fallbacks), <main> normalization that strips per-request
    Cloudflare/WP noise (hash-STABLE across rotated nonces/?ver=/cf tokens while
    a genuine text/link change trips the hash), Cloudflare-email decoding,
    challenge detection by content-PRESENCE, and HTTP-status handling
    (404/410 -> GFLInfoSiteGone).
  - gfl_info_site_watcher: the enabled gate, the pure diff/body/new-email
    helpers, and the full run() classify matrix driven through a fake Sheets
    service + a URL router (no network, no creds): initial atomic silent
    baseline, unchanged no-op, changed+email (with the diff), new-page alert,
    removed-page alert (and no re-alert), removed-then-returned, the
    anti-stampede re-baseline, fetch-fail skip-vs-loud, display-only recipients,
    and the "new email contact" surfacing.

Everything is hermetic: synthetic HTML is built here (never committed as a data
file — the repo rule), and requests.get is monkeypatched to a router.
"""
import re

import pytest

import gfl_info_site_client as gc
import gfl_info_site_watcher as gw
import sheet_writer as sw


# ---------------------------------------------------------------------------
# Synthetic HTML / sitemap builders (carry the same per-request noise the real
# site does: rotating nonces, ?ver= cache-busters, the Cloudflare beacon).
# ---------------------------------------------------------------------------


# A constant paragraph appended inside <main> so every synthetic page clears the
# min-content-chars floor (>150 chars) regardless of how short the CHANGING part
# (main_inner) is — the diff still keys off main_inner, this is just ballast.
_FILLER = ("Arbor Hills Landfill has served Washtenaw County for decades and is "
           "committed to safe, compliant, and transparent operations for the "
           "surrounding community and its neighbors across the region.")


def build_page(main_inner="Default body content for the page.", nonce="N1",
               ver="1", cfemail=None, mailto=None, extra_links=""):
    email_html = ""
    if cfemail:
        email_html += (f'<a href="/cdn-cgi/l/email-protection" '
                       f'data-cfemail="{cfemail}">[email&#160;protected]</a>')
    if mailto:
        email_html += f'<a href="mailto:{mailto}">Email us</a>'
    return (
        "<!doctype html><html><head>"
        f'<link rel="stylesheet" href="/wp-content/themes/x/style.css?ver={ver}">'
        f'<script nonce="{nonce}" src="/wp-includes/js/a.js?ver={ver}"></script>'
        "<title>GFL Arbor Hills</title></head><body>"
        '<header><nav><a href="/">Home</a><a href="/faq">FAQ</a>'
        '<a href="/what-we-do">What We Do</a></nav></header>'
        '<main id="SiteContent" class="site-content" role="main">'
        f"<h1>Page</h1><p>{main_inner}</p><p>{_FILLER}</p>{email_html}{extra_links}"
        f'<script nonce="{nonce}">window.__data={{v:{ver}}};</script>'
        "</main>"
        '<footer><p>&copy; 2026 GFL Environmental</p>'
        '<a href="/privacy-policy">Privacy Policy</a></footer>'
        f'<script src="/cdn-cgi/challenge-platform/h/g/scripts/jsd/{nonce}/main.js">'
        "</script></body></html>"
    )


def build_challenge():
    """A Cloudflare 'Just a moment' interstitial: no <main> content region, padded
    past the min-body-bytes floor so it reaches the no-<main> content check."""
    filler = "<p>Enable JavaScript and cookies to continue.</p>" * 20
    return (
        "<!doctype html><html><head><title>Just a moment...</title></head><body>"
        '<div class="cf-wrapper"><h1>Checking your browser</h1>'
        f"{filler}"
        '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1">'
        "</script></div></body></html>"
    )


def cfemail_hex(email, key=0x42):
    """Encode an email the way Cloudflare's data-cfemail does: first byte is the
    XOR key, the rest is the address XOR'd with it. Lets tests build a token
    without hardcoding a magic hex string."""
    out = bytes([key]) + bytes(ord(c) ^ key for c in email)
    return out.hex()


class FakeResp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text


class Router:
    """Serves the sitemap (index -> child urlset, generated from the current page
    set) and each page; a missing URL is a 404. Tests mutate `pages` to simulate
    a page changing / being added / being removed between run() calls."""

    def __init__(self, base="http://site.test"):
        self.base = base
        self.pages: dict[str, str] = {}
        self.sitemap_ok = True
        self.nav_html = ""

    def set_page(self, path, html):
        self.pages[gc.canonical_url(path, self.base)] = html

    def remove_page(self, path):
        self.pages.pop(gc.canonical_url(path, self.base), None)

    def _child_sitemap(self):
        locs = "".join(f"<url><loc>{u}</loc></url>" for u in sorted(self.pages))
        # Also advertise a couple of noise entries the watch must ignore.
        locs += (f"<url><loc>{self.base}/sitemap</loc></url>"
                 f"<url><loc>{self.base}/locations.kml</loc></url>")
        return f'<?xml version="1.0"?><urlset>{locs}</urlset>'

    def _index(self):
        return (f'<?xml version="1.0"?><sitemapindex><sitemap>'
                f'<loc>{self.base}/page-sitemap.xml</loc></sitemap></sitemapindex>')

    def get(self, url, headers=None, timeout=None):
        if url == f"{self.base}/sitemap.xml":
            return FakeResp(200 if self.sitemap_ok else 503,
                            self._index() if self.sitemap_ok else "err")
        if url == f"{self.base}/page-sitemap.xml":
            return FakeResp(200, self._child_sitemap())
        if url in (self.base, f"{self.base}/") and self.nav_html:
            return FakeResp(200, self.nav_html)
        if url in self.pages:
            return FakeResp(200, self.pages[url])
        return FakeResp(404, "<html><body>Not found</body></html>")


# ===========================================================================
# CLIENT — canonicalization
# ===========================================================================

def test_canonical_url_strips_trailing_slash_query_fragment():
    b = "http://site.test"
    assert gc.canonical_url("/faq/", b) == "http://site.test/faq"
    assert gc.canonical_url("/faq?x=1#top", b) == "http://site.test/faq"
    assert gc.canonical_url("/", b) == "http://site.test/"          # root keeps slash
    assert gc.canonical_url("http://SITE.test/FAQ") == "http://site.test/FAQ"  # host lowercased, path kept


def test_canonical_url_rejects_non_http():
    assert gc.canonical_url("mailto:x@y.com") is None
    assert gc.canonical_url("tel:123") is None
    assert gc.canonical_url("javascript:void(0)", "http://site.test") is None


# ===========================================================================
# CLIENT — discovery
# ===========================================================================

def _router_with_pages(*paths):
    r = Router()
    for p in paths:
        r.set_page(p, build_page())
    return r


def test_discovery_follows_sitemap_index_and_filters_noise(monkeypatch):
    r = _router_with_pages("/", "/faq", "/what-we-do")
    monkeypatch.setattr(gc.requests, "get", r.get)
    dr = gc.discover_page_urls(r.base, f"{r.base}/sitemap.xml",
                               ["/", "/faq"], gw._DEFAULT_IGNORE)
    assert dr.source == "sitemap"
    assert "http://site.test/faq" in dr.urls
    assert "http://site.test/what-we-do" in dr.urls
    # the /sitemap HTML index and the .kml asset are filtered out
    assert "http://site.test/sitemap" not in dr.urls
    assert not any(u.endswith(".kml") for u in dr.urls)


def test_discovery_never_fetches_cross_host_child_sitemap(monkeypatch):
    # SSRF guard: a hostile/compromised sitemap INDEX points at an internal host.
    # The runner must NOT fetch it, and its urls must not appear.
    fetched = []
    base = "http://site.test"
    index = ('<?xml version="1.0"?><sitemapindex>'
             '<sitemap><loc>http://site.test/page-sitemap.xml</loc></sitemap>'
             '<sitemap><loc>http://169.254.169.254/latest/meta-data/</loc></sitemap>'
             '</sitemapindex>')
    child = ('<?xml version="1.0"?><urlset>'
             '<url><loc>http://site.test/faq</loc></url></urlset>')

    def fake_get(url, headers=None, timeout=None):
        fetched.append(url)
        if url == f"{base}/sitemap.xml":
            return FakeResp(200, index)
        if url == f"{base}/page-sitemap.xml":
            return FakeResp(200, child)
        return FakeResp(200, "<urlset></urlset>")

    monkeypatch.setattr(gc.requests, "get", fake_get)
    dr = gc.discover_page_urls(base, f"{base}/sitemap.xml", ["/"], gw._DEFAULT_IGNORE)
    assert not any("169.254.169.254" in u for u in fetched)   # never fetched
    assert not any("169.254.169.254" in u for u in dr.urls)
    assert "http://site.test/faq" in dr.urls                  # same-host child still followed


def test_discovery_unions_seed_floor(monkeypatch):
    r = _router_with_pages("/faq")               # sitemap lists only /faq
    monkeypatch.setattr(gc.requests, "get", r.get)
    dr = gc.discover_page_urls(r.base, f"{r.base}/sitemap.xml",
                               ["/", "/faq", "/location-hours"], gw._DEFAULT_IGNORE)
    # seeds appear even though only /faq is in the sitemap (exact set — also
    # avoids a URL-substring membership pattern that trips CodeQL)
    assert set(dr.urls) == {"http://site.test/", "http://site.test/faq",
                            "http://site.test/location-hours"}


def test_discovery_falls_back_to_nav_then_seed_only(monkeypatch):
    r = Router()
    r.sitemap_ok = False
    r.nav_html = ('<html><body><a href="/faq">FAQ</a>'
                  '<a href="https://other.example/x">off</a></body></html>')
    monkeypatch.setattr(gc.requests, "get", r.get)
    dr = gc.discover_page_urls(r.base, f"{r.base}/sitemap.xml", ["/"], gw._DEFAULT_IGNORE)
    assert dr.source == "nav"
    assert "http://site.test/faq" in dr.urls
    assert not any("other.example" in u for u in dr.urls)   # off-host dropped

    r.nav_html = ""                                          # now nav fails too
    dr2 = gc.discover_page_urls(r.base, f"{r.base}/sitemap.xml", ["/"], gw._DEFAULT_IGNORE)
    assert dr2.source == "seed-only"
    assert dr2.urls == ["http://site.test/"]                 # seed floor still covers


# ===========================================================================
# CLIENT — normalization / hashing
# ===========================================================================

def test_normalization_hash_stable_across_per_request_noise():
    # Same visible content, DIFFERENT nonce + ?ver= + cf beacon path each fetch.
    a = gc.extract_content(build_page("Setback is 1000 feet.", nonce="AAA", ver="7"))
    b = gc.extract_content(build_page("Setback is 1000 feet.", nonce="ZZZ", ver="99"))
    assert gc.hash_text(a) == gc.hash_text(b)
    assert "1000 feet" in gc.visible_text(a)


def test_normalization_detects_real_text_change():
    a = gc.extract_content(build_page("Setback is 1000 feet."))
    b = gc.extract_content(build_page("Setback is 500 feet."))
    assert gc.hash_text(a) != gc.hash_text(b)


def test_normalization_detects_new_link_path():
    a = gc.extract_content(build_page("Body."))
    b = gc.extract_content(build_page("Body.", extra_links='<a href="/new-permit.pdf">doc</a>'))
    assert gc.hash_text(a) != gc.hash_text(b)


def test_normalization_ignores_query_only_link_change():
    a = gc.extract_content(build_page("Body.", extra_links='<a href="/doc.pdf?ver=1">d</a>'))
    b = gc.extract_content(build_page("Body.", extra_links='<a href="/doc.pdf?ver=2">d</a>'))
    assert gc.hash_text(a) == gc.hash_text(b)


def test_extract_content_raises_without_main():
    with pytest.raises(gc.GFLInfoSiteContentError):
        gc.extract_content(build_challenge())


def test_extract_content_raises_on_too_short():
    with pytest.raises(gc.GFLInfoSiteContentError):
        gc.extract_content('<main id="SiteContent">hi</main>', min_chars=50)


# ===========================================================================
# CLIENT — email extraction / decode
# ===========================================================================

def test_cfemail_decode_and_mailto_extraction():
    hexed = cfemail_hex("info@arborhills.example")
    content = gc.extract_content(build_page("Body.", cfemail=hexed,
                                            mailto="hello@arborhills.example"))
    emails = gc.emails_in(content)
    assert "info@arborhills.example" in emails
    assert "hello@arborhills.example" in emails


def test_cfemail_bad_token_falls_back_gracefully():
    # A malformed token must not crash normalization; the visible "[email
    # protected]" text still records that an email is present.
    content = gc.extract_content(build_page("Body.", cfemail="zzzz"))
    assert isinstance(gc.emails_in(content), list)   # no crash


# ===========================================================================
# CLIENT — fetch status handling
# ===========================================================================

def test_fetch_page_status_handling(monkeypatch):
    r = Router()
    r.set_page("/faq", build_page())
    monkeypatch.setattr(gc.requests, "get", r.get)
    assert "<main" in gc.fetch_page("http://site.test/faq")

    with pytest.raises(gc.GFLInfoSiteGone) as ei:
        gc.fetch_page("http://site.test/missing")
    assert ei.value.status == 404


def test_fetch_page_short_body_is_fetch_error(monkeypatch):
    monkeypatch.setattr(gc.requests, "get", lambda url, **k: FakeResp(200, "tiny"))
    with pytest.raises(gc.GFLInfoSiteFetchError):
        gc.fetch_page("http://site.test/x")


def test_fetch_page_network_exception_is_fetch_error(monkeypatch):
    def boom(url, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(gc.requests, "get", boom)
    with pytest.raises(gc.GFLInfoSiteFetchError):
        gc.fetch_page("http://site.test/x")


def test_page_label():
    assert gc.page_label("http://site.test/") == "Home"
    assert gc.page_label("http://site.test/faq") == "FAQ"
    assert gc.page_label("http://site.test/what-we-do") == "What We Do"
    assert gc.page_label("http://site.test/some-new-page") == "Some New Page"


# ===========================================================================
# WATCHER — pure helpers
# ===========================================================================

def test_should_run_gate():
    assert gw._should_run({"gfl_info_site": {"enabled": False}})[0] is False
    assert gw._should_run({})[0] is False
    assert gw._should_run({"gfl_info_site": {"enabled": True}})[0] is True


def test_summarize_diff():
    note, body = gw.summarize_diff("a\nb", "a\nB")
    assert re.match(r"\+\d+/-\d+ lines", note)
    assert "B" in body
    note2, body2 = gw.summarize_diff("same", "same")
    assert note2 == "link/structure change (no visible-text diff)"
    assert body2 == ""


def test_new_emails_detects_only_additions():
    old = gc.extract_content(build_page("Body."))
    new = gc.extract_content(build_page("Body.", mailto="info@x.example"))
    assert gw.new_emails(old, new) == ["info@x.example"]
    assert gw.new_emails(new, new) == []


def test_format_change_body_flags_new_email():
    body = gw.format_change_body("FAQ", "http://x/faq", "+1/-0 lines", "+ new line",
                                 added_emails=["info@x.example"])
    assert "NEW EMAIL CONTACT" in body
    assert "info@x.example" in body


# ===========================================================================
# WATCHER — full run() flows (fake Sheets + URL router)
# ===========================================================================

# Reuse the PFAS test's FakeSheets — same append-only tab semantics.
from test_pfas_watcher import FakeSheets


def _cfg(recipients=("watch@example.com",), max_new=3):
    return {"gfl_info_site": {
        "enabled": True,
        "base_url": "http://site.test",
        "sitemap_url": "http://site.test/sitemap.xml",
        "seed_paths": ["/", "/faq"],
        "ignore_path_patterns": ["^/sitemap($|/)", r"\.kml$"],
        "min_content_chars": 20,
        "max_diff_lines": 80,
        "max_new_pages_per_run": max_new,
        "recipients": list(recipients),
    }}


def _wire(monkeypatch, router, cfg=None):
    cfg = cfg or _cfg()
    fake = FakeSheets()
    sent = []
    appends = {"n": 0}
    real_append = sw.append_rows

    def counting_append(service, sheet_id, tab, rows):
        appends["n"] += 1
        return real_append(service, sheet_id, tab, rows)

    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(gw, "load_config", lambda: cfg)
    monkeypatch.setattr(gw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(gw.gc.requests, "get", router.get)
    monkeypatch.setattr(sw, "append_rows", counting_append)
    monkeypatch.setattr(
        gw.ea, "send_email",
        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))
    return fake, sent, appends


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_GFL_INFO_SITE, [])[1:]  # drop header


def test_disabled_is_noop(monkeypatch):
    r = _router_with_pages("/", "/faq")
    fake, sent, _ = _wire(monkeypatch, r, cfg={"gfl_info_site": {"enabled": False}})
    assert gw.run() == 0
    assert sent == []
    assert sw.TAB_GFL_INFO_SITE not in fake._values._tabs   # tab never created


def test_initial_run_baselines_all_silently_in_one_write(monkeypatch):
    r = _router_with_pages("/", "/faq", "/what-we-do")
    fake, sent, appends = _wire(monkeypatch, r)
    assert gw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 3
    assert all(row[3] == "baseline" for row in rows)
    assert sent == []                        # no alert on the initial baseline
    assert appends["n"] == 1                 # ATOMIC: one append for all baselines


def test_unchanged_second_run_is_noop(monkeypatch):
    r = _router_with_pages("/", "/faq")
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()
    gw.run()
    assert len(_rows(fake)) == 2             # no new rows
    assert sent == []


def test_changed_page_records_row_and_emails_with_diff(monkeypatch):
    r = Router()
    r.set_page("/", build_page("Home."))
    r.set_page("/faq", build_page("Setback is 1000 feet."))
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline
    r.set_page("/faq", build_page("Setback is 500 feet."))
    assert gw.run() == 0
    rows = _rows(fake)
    changed = [row for row in rows if row[3] == "changed"]
    assert len(changed) == 1
    assert changed[0][2] == "http://site.test/faq"
    assert len(sent) == 1
    subj, body, recips = sent[0]
    assert "changed" in subj.lower()
    assert "500 feet" in body                 # the diff shows the edit
    assert recips == ["watch@example.com"]    # scoped verbatim


def test_new_page_alerts_in_steady_state(monkeypatch):
    r = _router_with_pages("/", "/faq")
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline the two pages
    r.set_page("/community", build_page("New community page."))
    assert gw.run() == 0
    new_rows = [row for row in _rows(fake) if row[3] == "new-page"]
    assert len(new_rows) == 1
    assert new_rows[0][2] == "http://site.test/community"
    assert len(sent) == 1
    assert "ADDED" in sent[0][0]


def test_removal_is_debounced_over_two_runs(monkeypatch):
    # A transient site-wide 404 must NOT fire a "REMOVED" email on the first run;
    # only a SECOND consecutive 404 confirms the removal.
    r = _router_with_pages("/", "/faq", "/community")
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline all three
    r.remove_page("/community")               # now 404s
    # First run after removal: pending, SILENT (debounce).
    assert gw.run() == 0
    assert sent == []
    pend = [row for row in _rows(fake) if row[3] == gw._CHANGE_PENDING]
    assert len(pend) == 1 and pend[0][2] == "http://site.test/community"
    # Second consecutive 404: confirmed removal + one alert.
    assert gw.run() == 0
    removed = [row for row in _rows(fake) if row[3] == gw._CHANGE_REMOVED]
    assert len(removed) == 1
    assert removed[0][4] == gw._REMOVED_HASH
    assert len(sent) == 1 and "REMOVED" in sent[0][0]
    # A third run: still 404 — must NOT re-alert.
    sent.clear()
    assert gw.run() == 0
    assert sent == []


def test_transient_404_then_recover_identical_no_alert(monkeypatch):
    # A single 404 that clears next run (identical content) is a blip — no alert
    # at all, and the state resets so future real changes are still detected.
    r = _router_with_pages("/", "/community")
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline
    r.remove_page("/community")
    gw.run()                                  # pending (silent)
    r.set_page("/community", build_page())    # back, identical content
    assert gw.run() == 0
    assert sent == []                          # transient blip — zero emails
    assert any("recovered" in row[6] for row in _rows(fake))


def test_transient_404_then_recover_changed_diffs(monkeypatch):
    # A page that 404s once and comes back CHANGED diffs against its pre-404
    # content (preserved on the pending row), not against the removed sentinel.
    r = Router()
    r.set_page("/", build_page("Home."))
    r.set_page("/community", build_page("Original claim: setback 1000 feet."))
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline
    r.remove_page("/community")
    gw.run()                                  # pending (silent)
    r.set_page("/community", build_page("Revised claim: setback 500 feet."))
    assert gw.run() == 0
    changed = [row for row in _rows(fake)
               if row[3] == gw._CHANGE_CHANGED and row[2] == "http://site.test/community"]
    assert changed
    assert len(sent) == 1 and "changed" in sent[0][0].lower()
    assert "500 feet" in sent[0][1]           # diffed against the pre-404 content


def test_removed_then_returned_alerts_as_new(monkeypatch):
    r = _router_with_pages("/", "/community")
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline
    r.remove_page("/community")
    gw.run()                                  # pending (silent)
    gw.run()                                  # confirmed removed + alert
    sent.clear()
    r.set_page("/community", build_page("Back again."))
    assert gw.run() == 0
    new_rows = [row for row in _rows(fake) if row[3] == gw._CHANGE_NEW]
    assert new_rows                            # recorded as a (returned) new page
    assert len(sent) == 1
    assert "returned" in sent[0][0].lower() or "returned" in sent[0][1].lower()


def test_seed_first_success_is_silent_baseline_not_new(monkeypatch):
    # A seed page that failed on the activation run must baseline SILENTLY when it
    # later succeeds — never a false "Page ADDED" (a launch page isn't "new").
    r = Router()
    r.set_page("/", build_page("Home."))
    r.set_page("/faq", build_challenge())      # seed fails on the initial run
    fake, sent, _ = _wire(monkeypatch, r)
    assert gw.run() == 1                        # loud activation block on /faq
    sent.clear()
    r.set_page("/faq", build_page("FAQ now loads."))
    assert gw.run() == 0
    faq_rows = [row for row in _rows(fake) if row[2] == "http://site.test/faq"]
    assert faq_rows and faq_rows[-1][3] == gw._CHANGE_BASELINE
    assert sent == []                          # NOT a new-page alert


def test_total_blindness_exits_loud(monkeypatch):
    # Every page failing post-baseline (e.g. Cloudflare walls the runner) must
    # exit 1 so the workflow-failure email surfaces it — not a silent green no-op.
    r = _router_with_pages("/", "/faq")
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline

    def dead(url, headers=None, timeout=None):
        raise ConnectionError("walled")

    monkeypatch.setattr(gw.gc.requests, "get", dead)
    assert gw.run() == 1
    assert sent == []                          # no false change/removal alerts


def test_bulk_removal_sends_one_consolidated_email(monkeypatch):
    # A mass removal confirming at once (> the cap) sends ONE consolidated notice,
    # not N — but still records every removed-page row.
    r = _router_with_pages("/", "/faq", "/a", "/b", "/c", "/d")
    fake, sent, _ = _wire(monkeypatch, r, cfg=_cfg(max_new=1))
    gw.run()                                  # baseline six
    for p in ("/a", "/b", "/c", "/d"):
        r.remove_page(p)
    gw.run()                                  # four pending (silent)
    assert sent == []
    assert gw.run() == 0                        # four confirmed at once (> cap=1)
    assert len(sent) == 1                       # ONE consolidated email
    assert "4 pages REMOVED" in sent[0][0]
    assert len([row for row in _rows(fake) if row[3] == gw._CHANGE_REMOVED]) == 4


def test_unchanged_run_with_rotated_noise_is_noop(monkeypatch):
    # The cardinal-sin path end-to-end: a second real run() with rotated
    # per-request noise (as a live Cloudflare/WP server serves) must NOT fire a
    # false "changed", through the full Sheet round-trip.
    r = Router()
    r.set_page("/", build_page("Home.", nonce="AAA", ver="1"))
    r.set_page("/faq", build_page("FAQ.", nonce="BBB", ver="2"))
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline
    r.set_page("/", build_page("Home.", nonce="XXX", ver="9"))
    r.set_page("/faq", build_page("FAQ.", nonce="YYY", ver="8"))
    assert gw.run() == 0
    assert sent == []
    assert not [row for row in _rows(fake) if row[3] == gw._CHANGE_CHANGED]


def test_anti_stampede_rebaselines_silently(monkeypatch):
    r = _router_with_pages("/", "/faq")
    fake, sent, _ = _wire(monkeypatch, r, cfg=_cfg(max_new=1))
    gw.run()                                  # baseline the two seeds
    # Add 3 new pages at once — exceeds max_new_pages_per_run=1.
    for p in ("/a", "/b", "/c"):
        r.set_page(p, build_page(f"Page {p}."))
    assert gw.run() == 0
    assert sent == []                          # no new-page blast
    rebaselined = [row for row in _rows(fake)
                   if row[3] == "baseline" and "re-baseline" in row[6]]
    assert len(rebaselined) == 3


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    r = Router()
    r.set_page("/", build_page("Home."))
    r.set_page("/faq", build_page("FAQ."))
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                  # baseline

    real_get = r.get

    def flaky(url, **k):
        if url == "http://site.test/faq":
            raise ConnectionError("blip")
        return real_get(url, **k)

    monkeypatch.setattr(gw.gc.requests, "get", flaky)
    assert gw.run() == 0                       # a blip must not fail the job
    assert len([row for row in _rows(fake) if row[2] == "http://site.test/faq"]) == 1
    assert sent == []                          # not diffed into a false alert


def test_activation_block_is_loud_and_all_or_nothing(monkeypatch):
    # Initial run, a SEED page serves a challenge (no <main>). The run must exit
    # loud AND baseline NOTHING (all-or-nothing) — not a partial baseline.
    r = Router()
    r.set_page("/", build_page("Home."))
    r.set_page("/faq", build_challenge())
    fake, sent, _ = _wire(monkeypatch, r)
    assert gw.run() == 1                        # loud: surfaces the block on activation
    assert _rows(fake) == []                    # nothing baselined (all-or-nothing)
    assert sent == []


def test_initial_baseline_is_all_or_nothing_no_false_added(monkeypatch):
    # A NON-seed discovered page (e.g. /privacy-policy) failing on the initial run
    # must NOT leave a partial baseline that later false-fires "Page ADDED" for it.
    r = Router()
    r.set_page("/", build_page("Home."))            # seed, ok
    r.set_page("/faq", build_page("FAQ."))           # seed, ok
    r.set_page("/privacy-policy", build_challenge()) # non-seed discovered, fails
    fake, sent, _ = _wire(monkeypatch, r)
    assert gw.run() == 1                              # loud on the initial failure
    assert _rows(fake) == []                          # NOTHING baselined
    assert sent == []
    # Fix the failing page; a clean run now baselines all three silently.
    r.set_page("/privacy-policy", build_page("Privacy."))
    assert gw.run() == 0
    assert sent == []                                 # no false "Page ADDED"
    rows = _rows(fake)
    assert len(rows) == 3 and all(row[3] == gw._CHANGE_BASELINE for row in rows)


def test_site_wide_404_debounces_not_liveness(monkeypatch):
    # Every page 404'ing at once must go through the removal DEBOUNCE (silent
    # pending), NOT false-fire the liveness exit-1 (which is for fetch/parse
    # failures — a 404 is GFLInfoSiteGone, handled separately and not counted).
    r = _router_with_pages("/", "/faq")
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                          # baseline
    r.remove_page("/")
    r.remove_page("/faq")
    assert gw.run() == 0                               # NOT exit 1
    assert sent == []
    assert len([row for row in _rows(fake) if row[3] == gw._CHANGE_PENDING]) == 2


def test_new_pages_exactly_at_cap_alert_individually(monkeypatch):
    # Exactly max_new_pages_per_run new pages (== cap, not > cap) alert
    # individually — pins the boundary so a future '>' -> '>=' flip is caught.
    r = _router_with_pages("/", "/faq")
    fake, sent, _ = _wire(monkeypatch, r, cfg=_cfg(max_new=3))
    gw.run()                                          # baseline the two seeds
    for p in ("/a", "/b", "/c"):                       # exactly 3 = cap
        r.set_page(p, build_page(f"Page {p}."))
    assert gw.run() == 0
    assert len([row for row in _rows(fake) if row[3] == gw._CHANGE_NEW]) == 3
    assert len(sent) == 3                              # individual, not consolidated


def test_display_only_when_no_recipients(monkeypatch):
    r = Router()
    r.set_page("/", build_page("Home."))
    r.set_page("/faq", build_page("Setback 1000 feet."))
    fake, sent, _ = _wire(monkeypatch, r, cfg=_cfg(recipients=[]))
    gw.run()                                   # baseline
    r.set_page("/faq", build_page("Setback 500 feet."))
    assert gw.run() == 0
    assert [row for row in _rows(fake) if row[3] == "changed"]  # row still written
    assert sent == []                          # DISPLAY-ONLY: no email, no broadcast


def test_new_email_contact_surfaced_in_change(monkeypatch):
    r = Router()
    r.set_page("/", build_page("Home."))
    r.set_page("/faq", build_page("Call us."))
    fake, sent, _ = _wire(monkeypatch, r)
    gw.run()                                   # baseline (phone only)
    r.set_page("/faq", build_page("Email us.", mailto="info@gfl.example"))
    assert gw.run() == 0
    assert len(sent) == 1
    body = sent[0][1]
    assert "NEW EMAIL CONTACT" in body
    assert "info@gfl.example" in body
