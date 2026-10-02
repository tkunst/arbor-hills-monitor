"""egle_newsroom_watcher.py — parsing + relevance against fixtures trimmed from
the real 2026-10-01 newsroom (staff contacts scrubbed), and the run() flows
through the fake Sheets service from test_pfas_watcher: gate, silent first-run
baseline, alert on a relevant new item, no alert on irrelevant ones, gap notice,
liveness exit, scoped recipients, and read-error safety."""
import os

import pytest
import yaml

import egle_newsroom_watcher as w
import pfas_client as pc
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "egle_newsroom")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = "https://www.michigan.gov/egle/newsroom/"
WATER = B + "press-releases/2026/10/01/water-monitoring-recommendations"
EV = B + "mi-environment/2026/09/29/ev-battery-recycling"
BUG = B + "mi-environment/2026/09/28/spotted-lanternfly"
DECARB = B + "press-releases/2026/09/28/industrial-decarb-funding"
PAGES = {
    w.BASE + "/egle/newsroom": "newsroom.html",
    WATER: "water-monitoring-recommendations.html",
    EV: "ev-battery-recycling.html",
    BUG: "spotted-lanternfly.html",
    DECARB: "industrial-decarb-funding.html",
}


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def _real_cfg():
    with open(os.path.join(ROOT, "config.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


KW = _real_cfg()["egle_newsroom"]["keywords"]


# --- pure helpers ----------------------------------------------------------------

def test_gate():
    assert w._should_run({})[0] is False
    assert w._should_run({"egle_newsroom": {"enabled": False}})[0] is False
    assert w._should_run({"egle_newsroom": {"enabled": True}}) == (True, "")


def test_parse_listing_real_landing_page():
    items = w.parse_listing(_read("newsroom.html"))
    assert [u for u, _ in items] == [WATER, EV, BUG, DECARB]
    assert items[0][1] == "EGLE seeks suggestions for water quality monitoring locations"


def test_parse_listing_dedupes_and_fills_title_from_later_anchor():
    page = (f'<a href="/egle/newsroom/press-releases/2026/10/01/x"><img src="a.png"></a>'
            f'<a href="/egle/newsroom/press-releases/2026/10/01/x">Real title</a>')
    assert w.parse_listing(page) == [(B + "press-releases/2026/10/01/x", "Real title")]


def test_article_body_excludes_related_news():
    # Every fixture carries a synthetic Related News block naming PFAS, landfill,
    # Washtenaw and Arbor Hills; none of it may reach the scored body.
    for name in PAGES.values():
        if name == "newsroom.html":
            continue
        body = w.article_parts(_read(name))["body"]
        assert body and "Four ways" not in body and "Arbor Hills" not in body, name


@pytest.mark.parametrize("name,expected", [
    ("water-monitoring-recommendations.html", True),
    ("ev-battery-recycling.html", False),
    ("spotted-lanternfly.html", False),   # names Washtenaw in a county list
    ("industrial-decarb-funding.html", False),
])
def test_relevance_on_real_items(name, expected):
    p = w.article_parts(_read(name))
    relevant, matched = w.score(p["title"], p["body"], KW)
    assert relevant is expected, (name, matched)


def test_area_term_needs_a_topic_term():
    assert w.score("Pest found in Washtenaw", "Counties: Washtenaw, Wayne.", KW)[0] is False
    assert w.score("Washtenaw landfill hearing", "", KW)[0] is True


def test_specific_term_alone_is_enough():
    assert w.score("Grant awarded", "includes work near Arbor Hills", KW)[0] is True


def test_topic_needs_title_or_two_body_hits():
    assert w.score("News", "mentions a landfill once", KW)[0] is False
    assert w.score("News", "landfill leachate and groundwater", KW)[0] is True


def test_word_boundaries():
    assert w.score("News", "the GFLOW model and Salemite", KW)[1] == []


# --- run() flows -------------------------------------------------------------------

CFG = {"egle_newsroom": {"enabled": True, "recipients": ["me@example.com"], "keywords": KW}}


def _wire(monkeypatch, cfg=CFG, pages=None, listing=None):
    fake = FakeSheets()
    sent = []
    pages = dict(PAGES if pages is None else pages)

    def fetch(url, **k):
        if url == w.BASE + "/egle/newsroom" and listing is not None:
            return listing
        if url not in pages:
            raise pc.PFASFetchError(f"404 {url}")
        return _read(pages[url])

    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(w, "load_config", lambda: cfg)
    monkeypatch.setattr(w.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(w.pc, "fetch_page", fetch)
    monkeypatch.setattr(w.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_EGLE_NEWS, [])[1:]


def _seed(fake, urls):
    fake._values._tabs[sw.TAB_EGLE_NEWS] = [sw.EGLE_NEWS_HEADERS] + [
        ["2026-09-30", u, "old", "", "", "", "no", "", ""] for u in urls]


def test_disabled_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, cfg={"egle_newsroom": {"enabled": False}})
    assert w.run() == 0 and sent == [] and sw.TAB_EGLE_NEWS not in fake._values._tabs


def test_first_run_baselines_silently(monkeypatch):
    fake, sent = _wire(monkeypatch)
    assert w.run() == 0
    assert sent == []
    assert [r[1] for r in _rows(fake)] == [WATER, EV, BUG, DECARB]
    assert all(r[6] == "no" for r in _rows(fake))


def test_alerts_only_relevant_new_item_with_scoped_recipients(monkeypatch):
    fake, sent = _wire(monkeypatch)
    _seed(fake, [DECARB])  # steady state; three items are new
    assert w.run() == 0
    new_rows = _rows(fake)[1:]
    assert [r[1] for r in new_rows] == [WATER, EV, BUG]
    assert [r[4] for r in new_rows] == ["yes", "no", "no"]
    assert len(sent) == 1
    subj, body, recips = sent[0]
    assert "water quality monitoring" in subj and WATER in body
    assert recips == ["me@example.com"]


def test_seen_items_do_nothing(monkeypatch):
    fake, sent = _wire(monkeypatch)
    _seed(fake, [WATER, EV, BUG, DECARB])
    assert w.run() == 0 and sent == [] and len(_rows(fake)) == 4


def test_gap_notice_when_every_listed_item_is_new(monkeypatch):
    fake, sent = _wire(monkeypatch)
    _seed(fake, [B + "press-releases/2026/09/01/older"])
    assert w.run() == 0
    subjects = [s for s, _, _ in sent]
    assert any("may have been missed" in s for s in subjects)
    assert any("water quality monitoring" in s for s in subjects)


def test_zero_links_fails_loudly(monkeypatch):
    fake, sent = _wire(monkeypatch, listing="<html>" + "x" * 600 + "</html>")
    assert w.run() == 1 and sent == []


def test_listing_fetch_failure_fails_loudly(monkeypatch):
    fake, sent = _wire(monkeypatch, pages={})
    assert w.run() == 1


def test_empty_recipients_never_sends(monkeypatch):
    cfg = {"egle_newsroom": {"enabled": True, "recipients": [], "keywords": KW}}
    fake, sent = _wire(monkeypatch, cfg=cfg)
    _seed(fake, [DECARB])
    assert w.run() == 0
    assert sent == []  # no fall-through to the shared alert_recipients list
    assert _rows(fake)[1][4] == "yes" and _rows(fake)[1][6] == "no"


def test_article_fetch_failure_scores_title_and_records(monkeypatch):
    pages = {k: v for k, v in PAGES.items() if k != WATER}
    fake, sent = _wire(monkeypatch, pages=pages)
    _seed(fake, [DECARB])
    assert w.run() == 0
    water = [r for r in _rows(fake) if r[1] == WATER][0]
    assert water[4] == "yes" and "fetch failed" in water[7]
    assert len([s for s in sent if "water quality" in s[0]]) == 1


def test_state_read_error_raises_not_rebaselines(monkeypatch):
    fake, sent = _wire(monkeypatch)
    _seed(fake, [DECARB])

    def boom(*a, **k):
        raise RuntimeError("Sheets 503")
    monkeypatch.setattr(w.sw, "egle_news_seen_urls", boom)
    with pytest.raises(RuntimeError):
        w.run()
    assert sent == [] and len(_rows(fake)) == 1


def test_config_recipients_are_scoped_owner_alias():
    nc = _real_cfg()["egle_newsroom"]
    assert nc["recipients"] == ["arbor-hills@trishakunst.com"]


def test_text_strips_script_with_spaced_end_tag():
    assert w._text('<p>keep</p><script>bad landfill</script >tail<STYLE x>y</style\n>') == "keep tail"
