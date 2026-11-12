"""Homepage GlanceCard + "Newest filing" card (home_glance.py, design handoff
2026-11-12 section 2d): count and newest filing come from glance.json, never a
hand-typed number; "Last checked" shows only for a known check 48 hours old or
less; the committed homepage carries the fill markers exactly once."""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

import home_glance as hg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "site", "index.html")
GLANCE = os.path.join(ROOT, "site", "public-records", "glance.json")

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
GLANCE_DATA = {"count": 2207, "newest": {
    "title": "Staff Report <b>&", "facility": "Arbor Hills Energy",
    "date_filed": "2026-11-10", "link": "https://drive.google.com/file/d/x/view"}}


def _page():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def test_format_filed_is_ap_style():
    assert hg.format_filed("2026-11-10") == "Nov. 10, 2026"
    assert hg.format_filed("2026-09-05") == "Sept. 5, 2026"
    assert hg.format_filed("2026-06-16") == "June 16, 2026"
    assert hg.format_filed("bad") == ""


def test_format_checked_is_eastern_time_both_sides_of_dst():
    assert hg.format_checked(datetime(2026, 11, 12, 11, 23, tzinfo=timezone.utc)) == "Nov 12, 6:23 AM ET"
    assert hg.format_checked(datetime(2026, 7, 1, 16, 5, tzinfo=timezone.utc)) == "Jul 1, 12:05 PM ET"


@pytest.mark.parametrize("hours, shown", [(0, True), (47.9, True), (48, True), (49, False), (-1, False)])
def test_checked_is_fresh_48_hour_window(hours, shown):
    assert hg.checked_is_fresh(NOW - timedelta(hours=hours), NOW) is shown


def test_checked_is_fresh_unknown_time_never_shows():
    assert hg.checked_is_fresh(None, NOW) is False
    for bad in ("", None, "yesterday", "2026-11-12T10:00:00"):  # naive time is rejected
        assert hg.parse_utc(bad) is None
    assert hg.parse_utc("2026-11-12T11:23:45Z") == datetime(2026, 11, 12, 11, 23, 45, tzinfo=timezone.utc)


def test_render_card_escapes_and_shows_count_and_newest():
    card = hg.render_card(GLANCE_DATA, None, NOW)
    assert '<p class="big">2,207</p>' in card
    assert hg.COUNT_LABEL in card
    assert "Staff Report &lt;b&gt;&amp;" in card
    assert "Last checked" not in card


def test_render_card_last_checked_only_when_fresh():
    fresh = hg.render_card(GLANCE_DATA, NOW - timedelta(hours=3), NOW)
    assert 'data-checked="2026-11-12T12:00:00Z"' in fresh
    assert "Last checked <strong>Nov 12, 7:00 AM ET</strong>" in fresh
    stale = hg.render_card(GLANCE_DATA, NOW - timedelta(hours=49), NOW)
    assert "Last checked" not in stale and "data-checked" not in stale


def test_render_drops_non_https_links():
    bad = {"count": 5, "newest": dict(GLANCE_DATA["newest"], link="javascript:alert(1)")}
    assert "Newest filing" not in hg.render_card(bad, None, NOW)
    feature = hg.render_feature(bad)
    assert 'href="public-records/"' in feature and "javascript" not in feature


def test_render_feature_meta_line():
    feature = hg.render_feature(GLANCE_DATA)
    assert 'href="https://drive.google.com/file/d/x/view"' in feature
    assert "Arbor Hills Energy &middot; filed Nov. 10, 2026." in feature


def test_fill_is_idempotent_and_touches_only_the_markers():
    page = _page()
    once = hg.fill(page, GLANCE_DATA, NOW - timedelta(hours=1), NOW)
    assert hg.fill(once, GLANCE_DATA, NOW - timedelta(hours=1), NOW) == once
    strip = lambda s: re.sub(r"<!-- glance:(card|feature) -->.*?<!-- /glance:\1 -->", "", s, flags=re.S)
    assert strip(once) == strip(page)


def test_fill_requires_exactly_one_of_each_marker():
    with pytest.raises(ValueError):
        hg.fill("<p>no markers</p>", GLANCE_DATA, None, NOW)


def test_committed_homepage_has_markers_and_no_committed_last_checked():
    page = _page()
    for marker in (hg.CARD_START, hg.CARD_END, hg.FEATURE_START, hg.FEATURE_END):
        assert page.count(marker) == 1, marker
    # The committed page never carries a timestamp: only a deploy adds one.
    assert "data-checked=" not in page.split("<script>")[0]


def test_committed_glance_json_is_valid():
    with open(GLANCE, encoding="utf-8") as f:
        data = hg.load_glance(f.read())
    assert isinstance(data["count"], int) and data["count"] > 0
    assert data["newest"]["link"].startswith("https://")


def test_homepage_structure():
    page = _page()
    assert page.count("<h1") == 1
    assert "—" not in page  # no em-dashes
    # The cross-site link lives in the IndependenceBar, not the nav pills.
    nav = re.search(r'<nav class="jump" aria-label="Site sections">(.*?)</nav>', page, re.S).group(1)
    anchors = re.findall(r'href="#([^"]+)"', nav)
    assert anchors == ["records", "maps-explore", "on-record", "qa", "data"]
    assert "policy.arborhillsmonitor.org" not in nav
    for a in anchors:
        assert f'id="{a}"' in page
    # Decorative marks are hidden from assistive tech.
    for svg in re.findall(r"<svg[^>]*>", page):
        assert 'aria-hidden="true"' in svg
    # Arrows in link text are aria-hidden.
    assert "&rarr;</a>" not in page
    assert 'name="referrer" content="no-referrer"' in page
    assert 'name="viewport"' in page
