"""Unit tests for public_comment_feed.py -- pure render + state-transition logic
for the Public Comment Periods page. No network, no filesystem (the I/O wrapper
scripts/gen_public_comment_feed.py handles fetching + the JSON state file)."""
from __future__ import annotations

import public_comment_feed as pcf


# --- comment_link ----------------------------------------------------------

def test_comment_link_builds_details_url():
    assert pcf.comment_link("4041710289914188617") == (
        "https://mienviro.michigan.gov/ncore/external/publicnotice/info/"
        "4041710289914188617/details"
    )


def test_comment_link_preserves_negative_ids():
    assert pcf.comment_link("-1797947627965436698").endswith(
        "/info/-1797947627965436698/details")


def test_comment_link_empty_when_no_id():
    assert pcf.comment_link("") == ""
    assert pcf.comment_link(None) == ""


# --- statewide ROP notice parsing -----------------------------------------

# Mirrors the real EGLE statewide ROP notice: a TOC (dot-leaders + page numbers),
# then a 30-DAY PUBLIC COMMENT section (open), a 45-DAY EPA REVIEW section
# (comment ENDED), and a FINAL section (issued). The en-dash before "SRN" is the
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
Waste Facility LLC – SRN: Z9999
"""


def test_rop_parse_returns_only_30day_facilities():
    got = pcf.parse_rop_30day_comment(ROP_NOTICE_SAMPLE, ("N2688", "N1504", "P1488"))
    # N1504 open (30-day); P1488 in EPA-review (ended) so excluded; N2688 absent.
    assert got == {"N1504": "2026-09-09"}


def test_rop_parse_handles_until_and_to_phrasing():
    assert pcf.parse_rop_30day_comment(ROP_NOTICE_SAMPLE, ("B4942",)) == {"B4942": "2026-09-23"}
    assert pcf.parse_rop_30day_comment(ROP_NOTICE_SAMPLE, ("A3934",)) == {"A3934": "2026-09-10"}


def test_rop_section_excludes_other_sections():
    sec = pcf.rop_30day_section(ROP_NOTICE_SAMPLE)
    assert "N1504" in sec
    assert "P1488" not in sec   # 45-DAY section
    assert "Z9999" not in sec   # FINAL section


def test_rop_final_reports_only_issued_section():
    got = pcf.parse_rop_final(ROP_NOTICE_SAMPLE, ("N1504", "P1488", "Z9999"))
    assert got == {"Z9999"}     # only the FINAL/issued facility


def test_rop_parse_empty_when_no_section():
    assert pcf.parse_rop_30day_comment("no headers at all here", ("N1504",)) == {}


def test_rop_parse_undated_when_no_from_until_phrase():
    text = ("30-DAY PUBLIC COMMENT\nFoo LLC – SRN: Q1234\n"
            "The comment period is ending soon.\n45-DAY EPA REVIEW\n")
    assert pcf.parse_rop_30day_comment(text, ("Q1234",)) == {"Q1234": None}


def test_us_date_to_iso():
    assert pcf._us_date_to_iso("September 9, 2026") == "2026-09-09"
    assert pcf._us_date_to_iso("not a date") is None


def test_human_date():
    assert pcf._human_date("2026-09-15") == "September 15, 2026"
    assert pcf._human_date("") == ""


# --- sorting + link cell ---------------------------------------------------

def test_sort_open_soonest_first_undated_last():
    entries = [{"closes": "2026-09-30", "id": "late"},
               {"closes": "", "id": "none"},
               {"closes": "2026-09-05", "id": "soon"}]
    assert [e["id"] for e in pcf.sort_open(entries)] == ["soon", "late", "none"]


def test_sort_closed_most_recent_first():
    entries = [{"closes": "2026-07-01", "id": "old"},
               {"closes": "2026-08-30", "id": "new"}]
    assert [e["id"] for e in pcf.sort_closed(entries)] == ["new", "old"]


def test_link_cell():
    assert 'href="https://x.gov/n"' in pcf._link_cell("https://x.gov/n")
    assert pcf._link_cell("javascript:alert(1)") == "n/a"
    assert pcf._link_cell("") == "n/a"


# --- open table ------------------------------------------------------------

def _open(**kw):
    base = {"facility": "Arbor Hills Energy", "what": "Air ROP renewal",
            "opened": "2026-08-10", "closes": "2026-09-09",
            "link": "https://mienviro.michigan.gov/x"}
    base.update(kw)
    return base


def test_open_table_empty_state():
    assert "No comment periods are open right now." in pcf.render_open_table([])


def test_open_table_columns_and_dated_row():
    out = pcf.render_open_table([_open()])
    for col in ("Facility", "What", "Public comment opened",
                "Public comment closes", "Link"):
        assert f"<th>{col}</th>" in out
    assert 'data-close="2026-09-09"' in out            # countdown hook
    assert '<span class="pc-countdown"></span>' in out
    assert "September 10, 2026" in out or "August 10, 2026" in out
    assert "September 9, 2026" in out                  # absolute close date shown
    assert "(in " not in out and "closing soon" not in out   # not baked in
    assert '<a href="https://mienviro.michigan.gov/x">View notice</a>' in out


def test_open_table_undated_and_blank_opened():
    out = pcf.render_open_table([_open(opened="", closes="", link=pcf.ROP_NOTICE_URL)])
    assert "See notice" in out
    assert "data-close" not in out   # no countdown hook without a date
    assert "n/a" in out              # blank opened -> n/a


def test_open_table_escapes_facility():
    out = pcf.render_open_table([_open(facility="A & B <L>")])
    assert "A &amp; B &lt;L&gt;" in out


# --- closed table ----------------------------------------------------------

def test_closed_table_empty_state():
    assert "None yet." in pcf.render_closed_table([])


def test_closed_table_columns_and_outcome_default():
    out = pcf.render_closed_table([
        {"facility": "F1", "what": "ROP renewal", "link": "https://x", "outcome": ""},
        {"facility": "F2", "what": "JPA", "link": "https://y",
         "outcome": "Renewable Operating Permit renewal issued"},
    ])
    for col in ("Facility", "What", "Link", "Outcome"):
        assert f"<th>{col}</th>" in out
    assert pcf.PENDING_OUTCOME in out                                  # empty -> pending
    assert "Renewable Operating Permit renewal issued" in out          # set outcome shown


# --- update_state (pure state machine) ------------------------------------

def _entry(key="notice:1", srn="WRD", closes="2026-09-15", source="notice",
           what="EGLE public notice"):
    return {"key": key, "facility": "F", "srn": srn, "what": what,
            "opened": "2026-08-26", "closes": closes,
            "link": "https://mienviro/x", "source": source}


def test_new_open_period_added_not_closed():
    state = {}
    o, c = pcf.update_state(state, [_entry()], {"WRD"}, True, set())
    assert len(o) == 1 and c == []
    assert state["notice:1"]["closed"] is False


def test_period_marked_closed_when_it_drops_out_and_was_checked():
    state = {}
    pcf.update_state(state, [_entry()], {"WRD"}, True, set())     # seen open
    o, c = pcf.update_state(state, [], {"WRD"}, True, set())      # gone; WRD checked
    assert o == [] and len(c) == 1
    assert state["notice:1"]["closed"] is True


def test_transient_fetch_miss_does_not_false_close():
    state = {}
    pcf.update_state(state, [_entry()], {"WRD"}, True, set())
    _, c = pcf.update_state(state, [], set(), True, set())        # WRD NOT fetched
    assert c == [] and state["notice:1"]["closed"] is False


def test_rop_outcome_autofilled_when_in_final():
    state = {}
    r = _entry(key="rop:P1488", srn="P1488", source="rop", closes="2026-08-19",
               what="Air ROP renewal")
    pcf.update_state(state, [r], set(), True, set())             # seen open (rop)
    pcf.update_state(state, [], set(), True, {"P1488"})          # closed + now issued
    assert state["rop:P1488"]["closed"] is True
    assert state["rop:P1488"]["outcome"] == pcf.ISSUED_OUTCOME


def test_update_state_idempotent_on_unchanged_run():
    # A run where nothing changed must leave each record identical, so the
    # serialized state file is byte-stable and the diff-quiet guard stays quiet.
    state = {}
    pcf.update_state(state, [_entry()], {"WRD"}, True, set())
    snapshot = dict(state["notice:1"])
    pcf.update_state(state, [_entry()], {"WRD"}, True, set())
    assert state["notice:1"] == snapshot


def test_curated_what_and_outcome_preserved_across_runs():
    state = {"notice:1": {"key": "notice:1", "facility": "F", "srn": "WRD",
                          "what": "Wetland 1 PFAS JPA", "opened": "x",
                          "closes": "2026-09-15", "link": "https://x",
                          "source": "notice", "outcome": "Permit denied",
                          "closed": False}}
    pcf.update_state(state, [_entry()], {"WRD"}, True, set())
    assert state["notice:1"]["what"] == "Wetland 1 PFAS JPA"   # not clobbered by default
    assert state["notice:1"]["outcome"] == "Permit denied"


# --- embedded self-persisting state ---------------------------------------

def test_state_block_roundtrip():
    records = [
        {"key": "notice:1", "facility": "F1", "srn": "WRD",
         "what": "EGLE public notice", "opened": "2026-08-26",
         "closes": "2026-09-15", "link": "https://x", "source": "notice",
         "outcome": "", "closed": False},
        {"key": "rop:P1488", "facility": "Emerald", "srn": "P1488",
         "what": "Air ROP renewal", "opened": "", "closes": "2026-08-19",
         "link": pcf.ROP_NOTICE_URL, "source": "rop",
         "outcome": pcf.ISSUED_OUTCOME, "closed": True},
    ]
    block = pcf.render_state_block(records)
    assert 'type="application/json"' in block and 'id="pc-state"' in block
    recovered = pcf.parse_state_block(f"<html>{block}</html>")
    assert set(recovered) == {"notice:1", "rop:P1488"}
    assert recovered["rop:P1488"]["closed"] is True
    assert recovered["rop:P1488"]["outcome"] == pcf.ISSUED_OUTCOME


def test_parse_state_block_empty_when_absent_or_malformed():
    assert pcf.parse_state_block("<html>no block here</html>") == {}
    assert pcf.parse_state_block(
        '<script type="application/json" id="pc-state">{bad json</script>') == {}


def test_state_block_is_order_independent():
    r1 = {"key": "a:1", "facility": "A", "srn": "A", "what": "x", "opened": "",
          "closes": "2026-09-01", "link": "", "source": "notice",
          "outcome": "", "closed": False}
    r2 = {"key": "b:2", "facility": "B", "srn": "B", "what": "y", "opened": "",
          "closes": "2026-09-02", "link": "", "source": "notice",
          "outcome": "", "closed": True}
    assert pcf.render_state_block([r1, r2]) == pcf.render_state_block([r2, r1])


def test_render_page_embeds_recoverable_state():
    open_e = [{"key": "notice:1", "facility": "F", "srn": "WRD", "what": "w",
               "opened": "2026-08-26", "closes": "2026-09-15", "link": "https://x",
               "source": "notice", "outcome": "", "closed": False}]
    html = pcf.render_page(open_e, [], "2026-09-01 12:00 UTC")
    recovered = pcf.parse_state_block(html)
    assert "notice:1" in recovered   # the next run can read state back


# --- render_page -----------------------------------------------------------

def test_render_page_structure():
    open_e = [{"facility": "Arbor Hills Energy", "what": "Air ROP renewal",
               "opened": "2026-08-10", "closes": "2026-09-09", "link": "https://x"}]
    html = pcf.render_page(open_e, [], "2026-09-01 12:00 UTC")
    assert "1 open for comment now" in html
    assert "<h2>Open for comment</h2>" in html
    assert "<h2>Closed public comments</h2>" in html
    assert "pc-table" in html and "<script>" in html
    assert "not a legal notice" in html
    assert 'href="../style.css"' in html and "/favicon.svg" in html


def test_render_page_surfaces_errors():
    html = pcf.render_page([], [], "2026-09-01 12:00 UTC",
                           errors=["Could not check Foo (FOO): boom"])
    assert "0 open for comment now" in html
    assert "may be incomplete" in html
    assert "Could not check Foo (FOO): boom" in html


def test_render_page_deterministic_same_inputs():
    # Same inputs -> identical bytes (no hidden per-call variation baked in). The
    # no-baked-countdown guarantee is checked at the table level
    # (test_open_table_columns_and_dated_row); here the page legitimately embeds
    # the countdown SCRIPT, whose literal contains "(in ", so that check belongs
    # on the table output, not the whole page.
    open_e = [{"facility": "F", "what": "w", "opened": "2026-08-01",
               "closes": "2026-09-30", "link": "https://x"}]
    a = pcf.render_page(open_e, [], "2026-09-01 12:00 UTC")
    b = pcf.render_page(open_e, [], "2026-09-01 12:00 UTC")
    assert a == b
