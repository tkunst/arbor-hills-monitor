"""Unit tests for public_comment_feed.py (the pure render/bucket logic for the
Open Public Comment Periods page). No network, no Sheets -- mirrors
test_findings_feed.py's split between pure-render tests here and the I/O wrapper
elsewhere."""
from __future__ import annotations

import public_comment_feed as pcf


TODAY = "2026-09-01"


def _entry(**kw):
    base = {
        "facility": "Test Facility",
        "srn": "TEST",
        "kind": "EGLE public notice",
        "notice_id": "123",
        "coverage": "Facility Location",
        "start_date": "2026-08-01",
        "end_date": "2026-09-15",
        "link": pcf.comment_link("123"),
        "source": "notice",
    }
    base.update(kw)
    return base


# --- comment_link ----------------------------------------------------------

def test_comment_link_builds_details_url():
    assert pcf.comment_link("4041710289914188617") == (
        "https://mienviro.michigan.gov/ncore/external/publicnotice/info/"
        "4041710289914188617/details"
    )


def test_comment_link_preserves_negative_ids():
    # EGLE uses signed 64-bit ids; the details page takes the sign verbatim.
    link = pcf.comment_link("-1797947627965436698")
    assert link.endswith("/info/-1797947627965436698/details")


def test_comment_link_empty_when_no_id():
    assert pcf.comment_link("") == ""
    assert pcf.comment_link(None) == ""


# --- days_between ----------------------------------------------------------

def test_days_between_future_zero_and_past():
    assert pcf.days_between("2026-09-15", TODAY) == 14
    assert pcf.days_between("2026-09-01", TODAY) == 0
    assert pcf.days_between("2026-08-30", TODAY) == -2


def test_days_between_none_on_bad_or_missing_dates():
    assert pcf.days_between("", TODAY) is None
    assert pcf.days_between("not-a-date", TODAY) is None
    assert pcf.days_between("2026-09-15", "") is None


# --- bucket_entries --------------------------------------------------------

def test_bucket_splits_open_upcoming_closed():
    entries = [
        _entry(end_date="2026-09-15"),                       # open
        _entry(start_date="2026-09-20", end_date="2026-10-01"),  # upcoming
        _entry(start_date="2026-08-01", end_date="2026-08-20"),  # recently closed
    ]
    b = pcf.bucket_entries(entries, TODAY)
    assert len(b["open"]) == 1
    assert len(b["upcoming"]) == 1
    assert len(b["closed"]) == 1


def test_open_sorted_soonest_deadline_first_undated_last():
    entries = [
        _entry(notice_id="late", end_date="2026-09-30"),
        _entry(notice_id="none", end_date="", source="rop", link=pcf.ROP_NOTICE_URL),
        _entry(notice_id="soon", end_date="2026-09-05"),
    ]
    b = pcf.bucket_entries(entries, TODAY)
    order = [e["notice_id"] for e in b["open"]]
    assert order == ["soon", "late", "none"]


def test_undated_entry_counts_as_open():
    b = pcf.bucket_entries([_entry(end_date="", start_date="")], TODAY)
    assert len(b["open"]) == 1


def test_recently_closed_trims_old_windows():
    entries = [
        _entry(end_date="2026-08-25"),                       # 7 days ago -> shown
        _entry(end_date="2026-01-01"),                       # >90 days -> dropped
    ]
    b = pcf.bucket_entries(entries, TODAY)
    assert len(b["closed"]) == 1
    assert b["closed"][0]["end_date"] == "2026-08-25"


# --- deadline meta / render entry -----------------------------------------

def test_deadline_meta_static_has_absolute_date_not_countdown():
    # Static text carries ONLY the absolute date; "in N days" / "closing soon"
    # are added client-side, never baked into the committed HTML.
    meta = pcf._deadline_meta(_entry(end_date="2026-09-05"))
    assert meta == "Comment closes September 5, 2026"
    assert "(in " not in meta and "closing soon" not in meta


def test_deadline_meta_closed_and_undated():
    assert pcf._deadline_meta(_entry(end_date="2026-08-25"), closed=True) == \
        "Comment closed August 25, 2026"
    assert "see the notice" in pcf._deadline_meta(_entry(end_date=""))


def test_human_date():
    assert pcf._human_date("2026-09-15") == "September 15, 2026"
    assert pcf._human_date("") == ""


def test_render_entry_open_has_countdown_hook_but_no_baked_countdown():
    out = pcf.render_entry(_entry(end_date="2026-09-15"))
    assert 'data-close="2026-09-15"' in out
    assert '<span class="pc-countdown"></span>' in out
    assert "(in " not in out          # countdown is NOT baked into static HTML
    assert "closing soon" not in out


def test_render_entry_closed_has_no_countdown_hook():
    out = pcf.render_entry(_entry(end_date="2026-08-25"), closed=True)
    assert "data-close" not in out
    assert "pc-countdown" not in out
    assert "Comment closed August 25, 2026" in out


def test_render_entry_links_and_escapes():
    e = _entry(facility="A & B <Landfill>", end_date="2026-09-15",
               link="https://example.gov/notice")
    out = pcf.render_entry(e)
    assert '<a href="https://example.gov/notice">' in out
    assert "A &amp; B &lt;Landfill&gt;" in out


def test_render_entry_no_link_when_not_http():
    out = pcf.render_entry(_entry(link="javascript:alert(1)"))
    assert "<a href" not in out
    assert "javascript:alert(1)" not in out


# --- render_page -----------------------------------------------------------

def test_render_page_counts_open_and_has_sections():
    entries = [
        _entry(end_date="2026-09-09"),
        _entry(start_date="2026-08-01", end_date="2026-08-25"),
    ]
    b = pcf.bucket_entries(entries, TODAY)
    html = pcf.render_page(b, "2026-09-01 15:00 UTC")
    assert "1 open for comment now" in html
    assert "Open for comment" in html
    assert "Recently closed" in html
    assert "not a legal notice" in html
    assert 'href="../style.css"' in html
    assert "pc-countdown" in html   # the client-side countdown hook
    assert "<script>" in html       # progressive-enhancement script present


def test_render_page_empty_state():
    b = pcf.bucket_entries([], TODAY)
    html = pcf.render_page(b, "2026-09-01 15:00 UTC")
    assert "0 open for comment now" in html
    assert "No comment periods are open right now." in html


def test_render_page_surfaces_errors():
    b = pcf.bucket_entries([_entry()], TODAY)
    html = pcf.render_page(b, "2026-09-01 15:00 UTC",
                           errors=["Could not check Foo (FOO): boom"])
    assert "may be incomplete" in html
    assert "Could not check Foo (FOO): boom" in html


def test_page_is_byte_stable_across_days_when_nothing_changes():
    # THE anti-churn guarantee: with a fixed generated_at, the committed HTML for
    # an unchanged, still-open notice must be identical on different days -- else
    # the workflow's diff-quiet guard would commit + redeploy every single day as
    # the countdown ticked. The countdown is client-side precisely to hold this.
    entries = [_entry(end_date="2026-09-30")]  # far out; still open on both days
    gen = "2026-09-01 12:00 UTC"
    h1 = pcf.render_page(pcf.bucket_entries(entries, "2026-09-01"), gen)
    h2 = pcf.render_page(pcf.bucket_entries(entries, "2026-09-08"), gen)
    assert h1 == h2


# --- statewide ROP notice parsing -----------------------------------------

# Mirrors the real EGLE statewide ROP notice: a TOC (with dot-leaders + page
# numbers), then a 30-DAY PUBLIC COMMENT section (open), a 45-DAY EPA REVIEW
# section (comment ENDED), and a FINAL section. The en-dash before "SRN" is the
# character EGLE actually uses.
ROP_NOTICE_SAMPLE = """Title V Renewable Operating Permit (ROP)
Public Notice Documents

Contents
30-DAY PUBLIC COMMENT ...................... 2
45-DAY EPA REVIEW .......................... 4
FINAL TITLE V / ROP PERMITS ................ 7

30-DAY PUBLIC COMMENT
EGLE is seeking comment on the following ROP actions:
Corteva LLC – SRN: B4942
EGLE is holding a public comment period from August 24, 2026 until September 23, 2026
on a draft ROP renewal.
Arbor Hills Energy,  LLC – SRN: N1504
EGLE is holding a public comment period from August 10, 2026 until September 9, 2026
on a draft ROP renewal.
Great Lakes Castings LLC – SRN: A3934
The Department is holding a public comment period from August 6, 2026 to September 10, 2026
on both a New source review and a draft ROP.

45-DAY EPA REVIEW
The comment period for initial or renewal ROPs has ended and EGLE has proposed the
following ROPs to the EPA for 45-day review:
Emerald RNG LLC – SRN: P1488
A proposed ROP renewal (EPA review began 08-24-2026 and ends 10-08-2026).

FINAL TITLE V / ROP PERMITS
Information is included in chronological order.
Some Other LLC – SRN: Z9999
"""


def test_rop_parse_returns_only_30day_facilities():
    got = pcf.parse_rop_30day_comment(ROP_NOTICE_SAMPLE, ("N2688", "N1504", "P1488"))
    # N1504 is open (30-day); P1488 is in EPA-review (comment ended) so excluded;
    # N2688 is absent entirely.
    assert got == {"N1504": "2026-09-09"}


def test_rop_parse_handles_until_and_to_phrasing():
    assert pcf.parse_rop_30day_comment(ROP_NOTICE_SAMPLE, ("B4942",)) == {"B4942": "2026-09-23"}
    assert pcf.parse_rop_30day_comment(ROP_NOTICE_SAMPLE, ("A3934",)) == {"A3934": "2026-09-10"}


def test_rop_section_excludes_other_sections():
    sec = pcf.rop_30day_section(ROP_NOTICE_SAMPLE)
    assert "N1504" in sec
    assert "P1488" not in sec   # 45-DAY section
    assert "Z9999" not in sec   # FINAL section


def test_rop_parse_empty_when_no_section():
    assert pcf.parse_rop_30day_comment("no headers at all here", ("N1504",)) == {}


def test_rop_parse_undated_when_no_from_until_phrase():
    text = ("30-DAY PUBLIC COMMENT\nFoo LLC – SRN: Q1234\n"
            "The comment period is ending soon.\n45-DAY EPA REVIEW\n")
    assert pcf.parse_rop_30day_comment(text, ("Q1234",)) == {"Q1234": None}


def test_us_date_to_iso():
    assert pcf._us_date_to_iso("September 9, 2026") == "2026-09-09"
    assert pcf._us_date_to_iso("not a date") is None
