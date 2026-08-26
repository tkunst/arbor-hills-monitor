"""nsite_client.fetch_site_public_notices + nsite_public_notices_watcher
(Stream Q, ADR 032) — the activation gate, the fetch-error contract (a
failure must never look like "zero notices"), the pure ref-number-keyed
snapshot/diff helpers (keyed on a URL-embedded id, NOT
publicNotifExtrnlPublNoticeNum — see why in nsite_client._normalize_public_
notice), the comments-field cap, and the full baseline/unchanged/new/changed/
removed/fetch-fail flows driven through a fake Sheets service (no network, no
creds). Mirrors tests/test_nsite_evaluations.py's structure; reuses FakeSheets
from test_pfas_watcher.

Fixtures below are mostly SYNTHETIC (per the handoff's own instruction — this
profile's live volume has never exceeded ONE record across all 19 sites), but
the one real record found IS included verbatim: the N1504 ROP renewal notice
live-fetched 2026-08-25 during this build's feasibility spike, the second
consecutive sampling (after 2026-07-24's P1488 record) to land on a ROP
renewal notice — the finding that drove this profile's ROP-overlap design
question (see ADR 032)."""
import copy
import json

import pytest

import nsite_client as nc
import nsite_public_notices_watcher as pnw
import nsite_submissions_watcher as sub_w
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# fetch_site_public_notices — the fetch-error contract
# ==============================================================================


class _Resp:
    def __init__(self, body=None, status=200, json_ok=True):
        self._body = body if body is not None else {"queryResults": []}
        self._status = status
        self._json_ok = json_ok

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        if not self._json_ok:
            raise ValueError("not JSON")
        return self._body


class _Session:
    """Returns a scripted response per call. A list value scripts a retry
    sequence (one item consumed per call)."""

    def __init__(self, script):
        self.script = list(script) if isinstance(script, list) else script
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if isinstance(self.script, list):
            return self.script[min(self.calls - 1, len(self.script) - 1)]
        return self.script


# Verbatim from the live N1504 site, 2026-08-25 (this build's feasibility
# spike) — a ROP renewal comment-window notice, the SAME event type the
# 2026-07-24 handoff sample (a different site, P1488) also found. Two
# consecutive live samples landing on a ROP notice is what makes the
# ROP-overlap question real rather than hypothetical (see ADR 032).
_RAW_NOTICE_N1504 = {
    "publicNotifPnurl": (
        "https://mienviro.michigan.gov/ncore//external/publicnotice/info/"
        "-1797947627965436698/details"
    ),
    "publicNotifExtrnlPublNoticeNum": None,
    "publicNotifRefPublicNotifCovrg": "Facility Location",
    "publicNotifStartDate": "2026-08-10T00:00:00.0000000-04:00",
    "publicNotifEndDate": "2026-09-09T00:00:00.0000000-04:00",
    "publicNotifComments": (
        "The Michigan Department of Environment, Great Lakes, and Energy "
        "(EGLE), Air Quality Division (AQD) has opened a public comment "
        "period on the renewal of a draft Renewable Operating Permit (ROP) "
        "for Arbor Hills Energy, LLC from August 10, 2026 to September 9, "
        "2026.  A public hearing may also be scheduled if a request is "
        "received in writing by September 9, 2026.  The AQD uses public "
        "comment periods and hearings to allow the public the opportunity "
        "to comment on the proposed conditional approval of the renewal of "
        "an ROP."
    ),
}

# A synthetic non-ROP notice, distinct id, so tests exercising two records
# don't rely on two copies of the same real one.
_RAW_NOTICE_OTHER = {
    "publicNotifPnurl": (
        "https://mienviro.michigan.gov/ncore//external/publicnotice/info/"
        "1234567890123456789/details"
    ),
    "publicNotifExtrnlPublNoticeNum": None,
    "publicNotifRefPublicNotifCovrg": "Facility Location",
    "publicNotifStartDate": "2026-01-05T00:00:00.0000000-05:00",
    "publicNotifEndDate": "2026-02-04T00:00:00.0000000-05:00",
    "publicNotifComments": "A wetland permit application public comment period.",
}


def test_fetch_normalizes_the_real_live_n1504_specimen():
    session = _Session(_Resp({"queryResults": [_RAW_NOTICE_N1504]}))
    rows = nc.fetch_site_public_notices(session, "-4937599654678851055")
    assert rows == [{
        "notice_id": "-1797947627965436698",
        "ext_num": "",
        "coverage": "Facility Location",
        "start_date": "2026-08-10",       # UTC offset dropped, date kept
        "end_date": "2026-09-09",
        "comments": _RAW_NOTICE_N1504["publicNotifComments"],
    }]


def test_fetch_extracts_the_url_embedded_id_including_a_positive_one():
    """The regex must handle both the negative id seen on the N1504 specimen
    and a positive one (the schema declares a signed integer, and nothing
    guarantees EGLE only ever emits negative ids)."""
    session = _Session(_Resp({"queryResults": [_RAW_NOTICE_OTHER]}))
    rows = nc.fetch_site_public_notices(session, "X")
    assert rows[0]["notice_id"] == "1234567890123456789"


def test_fetch_drops_a_record_with_no_extractable_url_id_rather_than_raising():
    """UNLIKE fetch_site_violations/fetch_site_compliance_actions (which keep
    every record because they have no key), this profile filters on a
    regex-extractable notice_id — the same precedent fetch_site_evaluations/
    fetch_site_permits set — because a keyless record cannot be placed in a
    ref-number-keyed snapshot. No such record has been observed live."""
    keyless = dict(_RAW_NOTICE_N1504, publicNotifPnurl="not a url at all")
    session = _Session(_Resp({"queryResults": [keyless, _RAW_NOTICE_OTHER]}))
    rows = nc.fetch_site_public_notices(session, "X")
    assert len(rows) == 1
    assert rows[0]["notice_id"] == "1234567890123456789"


def test_fetch_normalizes_a_null_ext_num_to_empty_string():
    """ext_num is null on both live records seen so far. `or ""` collapses a
    present-but-null key to the same "" an absent key would give."""
    session = _Session(_Resp({"queryResults": [_RAW_NOTICE_N1504]}))
    rows = nc.fetch_site_public_notices(session, "X")
    assert rows[0]["ext_num"] == ""
    assert all(v is not None for v in rows[0].values())


def test_fetch_keeps_a_populated_ext_num_as_an_ordinary_field():
    raw = dict(_RAW_NOTICE_N1504, publicNotifExtrnlPublNoticeNum="PN-2026-00042")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_public_notices(session, "X")
    assert rows[0]["ext_num"] == "PN-2026-00042"
    assert rows[0]["notice_id"] == "-1797947627965436698"   # key still the URL id


def test_fetch_survives_an_out_of_range_start_date_instead_of_blinding_the_site():
    raw = dict(_RAW_NOTICE_N1504, publicNotifStartDate="2026-02-30T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_public_notices(session, "X")
    assert rows[0]["start_date"] == ""
    assert rows[0]["notice_id"] == "-1797947627965436698"
    assert session.calls == 1          # not retried — it was never an error


def test_fetch_coerces_a_non_string_date_to_empty_instead_of_crashing():
    raw = dict(_RAW_NOTICE_N1504, publicNotifStartDate=1723075200000)   # epoch ms as an int
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_public_notices(session, "X")
    assert rows[0]["start_date"] == ""
    assert session.calls == 1


def test_fetch_raises_rather_than_diffing_a_partial_page():
    session = _Session(_Resp({"queryResults": [_RAW_NOTICE_N1504], "hasResultsRemaining": True}))
    with pytest.raises(nc.NsiteStructuralError, match="paging"):
        nc.fetch_site_public_notices(session, "X")


def test_fetch_accepts_the_null_hasresultsremaining_the_api_actually_sends():
    session = _Session(_Resp({"queryResults": [_RAW_NOTICE_N1504],
                              "hasResultsRemaining": None, "totalCount": None}))
    assert len(nc.fetch_site_public_notices(session, "X")) == 1


def test_fetch_empty_queryresults_is_a_valid_zero_result():
    """18 of the 19 watched sites have zero active public notices as of the
    2026-08-25 live sample. A structurally-sound response listing none is NOT
    an error — it is the baseline."""
    session = _Session(_Resp({"queryResults": []}))
    assert nc.fetch_site_public_notices(session, "X") == []


def test_fetch_raises_never_returns_empty_on_http_error():
    """THE contract this feature depends on: an HTTP failure must raise, never
    silently degrade to []. Otherwise the watcher would misread a fetch outage
    as 'every notice withdrawn at once' and fire a false all-clear."""
    session = _Session(_Resp(status=500))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_public_notices(session, "X")


def test_fetch_raises_on_missing_queryresults_key():
    session = _Session(_Resp({"somethingElse": []}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_public_notices(session, "X")


def test_fetch_raises_on_non_json_body():
    session = _Session(_Resp(json_ok=False))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_public_notices(session, "X")


def test_fetch_raises_on_structurally_broken_record():
    session = _Session(_Resp({"queryResults": ["not-a-dict"]}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_public_notices(session, "X")


def test_fetch_retries_then_succeeds():
    session = _Session([_Resp(status=500), _Resp({"queryResults": [_RAW_NOTICE_N1504]})])
    rows = nc.fetch_site_public_notices(session, "X")
    assert len(rows) == 1
    assert session.calls == 2


def test_fetch_hits_the_public_notices_endpoint_with_the_site_filter():
    """Pin the URL construction so a copy-paste of the wrong profile path
    fails the suite instead of 404ing silently in production."""
    captured = {}

    class _CapturingSession:
        def get(self, url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _Resp({"queryResults": [_RAW_NOTICE_N1504]})

    nc.fetch_site_public_notices(_CapturingSession(), "-4937599654678851055")
    assert "/profiles/1-profile/2-active-public-notices" in captured["url"]
    assert "/profiles/3-compliance/1-evaluations" not in captured["url"]   # not a sibling
    assert "responseContentType=application" in captured["url"]
    assert "-4937599654678851055" in captured["url"]
    assert "detail/-4937599654678851055" in captured["headers"].get("Referer", "")


# ==============================================================================
# Pure snapshot / diff helpers
# ==============================================================================

FIELDS = nc.PUBLIC_NOTICE_FIELDS


def _pn(notice_id="N-00001", **kw):
    base = {
        "notice_id": notice_id, "ext_num": "", "coverage": "Facility Location",
        "start_date": "2026-08-10", "end_date": "2026-09-09",
        "comments": "A public comment period notice.",
    }
    base.update(kw)
    return base


def test_snapshot_hash_stable_across_row_order():
    a = pnw.public_notices_snapshot([_pn(notice_id="B"), _pn(notice_id="A")], FIELDS)
    b = pnw.public_notices_snapshot([_pn(notice_id="A"), _pn(notice_id="B")], FIELDS)
    assert pnw.snapshot_hash(a) == pnw.snapshot_hash(b)


def test_snapshot_hash_changes_when_any_field_changes():
    a = pnw.public_notices_snapshot([_pn(end_date="2026-09-09")], FIELDS)
    b = pnw.public_notices_snapshot([_pn(end_date="2026-10-09")], FIELDS)
    assert pnw.snapshot_hash(a) != pnw.snapshot_hash(b)


def test_snapshot_of_empty_list_is_a_valid_baseline():
    snap = pnw.public_notices_snapshot([], FIELDS)
    assert snap["n"] == 0 and snap["rows"] == []
    assert pnw.snapshot_hash(snap)   # hashes fine, does not raise


def test_snapshot_is_self_describing_about_its_field_order():
    snap = pnw.public_notices_snapshot([_pn()], ("notice_id", "coverage"))
    assert snap["fields"] == ["notice_id", "coverage"]
    assert snap["rows"] == [["N-00001", "Facility Location"]]


def test_snapshot_keeps_two_notices_distinct_even_with_identical_detail():
    snap = pnw.public_notices_snapshot([_pn(notice_id="A"), _pn(notice_id="B")], FIELDS)
    assert snap["n"] == 2 and len(snap["rows"]) == 2


def test_new_notice_at_a_zero_site_reads_with_detail():
    old = pnw.public_notices_snapshot([], FIELDS)
    new = pnw.public_notices_snapshot([_pn(notice_id="N-NEW", coverage="Statewide")], FIELDS)
    note, body = pnw.summarize_public_notices_change(old, new)
    assert "new public notice recorded" in note
    assert "+ NEW NOTICE  N-NEW" in body
    assert "Statewide" in body


def test_a_field_advancing_on_an_existing_notice_id_reads_as_changed():
    """The high-value secondary signal: the comment window's end date
    (the actionable deadline) moving."""
    old = pnw.public_notices_snapshot([_pn(end_date="2026-09-09")], FIELDS)
    new = pnw.public_notices_snapshot([_pn(end_date="2026-10-09")], FIELDS)
    note, body = pnw.summarize_public_notices_change(old, new)
    assert "existing public notice changed" in note
    assert "~ CHANGED  N-00001" in body
    assert "end_date: 2026-09-09 -> 2026-10-09" in body


def test_ext_num_populating_on_an_existing_notice_is_a_benign_change_not_a_key_flip():
    """The exact false-positive this profile's design deliberately avoids: if
    notice_id were keyed conditionally on ext_num, a notice first seen with
    ext_num null (keyed by its URL id) that later has ext_num populated would
    flip its computed key, reporting a false REMOVE + false ADD for one
    unchanged notice. Keying unconditionally on the URL id means this instead
    reads as one ordinary field-level change."""
    old = pnw.public_notices_snapshot([_pn(ext_num="")], FIELDS)
    new = pnw.public_notices_snapshot([_pn(ext_num="PN-2026-00042")], FIELDS)
    note, body = pnw.summarize_public_notices_change(old, new)
    assert "existing public notice changed" in note
    assert "~ CHANGED  N-00001" in body
    assert "ext_num: — -> PN-2026-00042" in body
    assert "REMOVED" not in body
    assert "NEW NOTICE" not in body


def test_a_notice_no_longer_listed_reads_as_removed():
    old = pnw.public_notices_snapshot([_pn(notice_id="A"), _pn(notice_id="B")], FIELDS)
    new = pnw.public_notices_snapshot([_pn(notice_id="A")], FIELDS)
    note, body = pnw.summarize_public_notices_change(old, new)
    assert "public notice no longer listed" in note
    assert "- REMOVED  B" in body


def test_new_and_changed_and_removed_can_all_appear_in_one_diff():
    old = pnw.public_notices_snapshot(
        [_pn(notice_id="A", coverage="Facility Location"), _pn(notice_id="B")], FIELDS)
    new = pnw.public_notices_snapshot(
        [_pn(notice_id="A", coverage="Statewide"), _pn(notice_id="C")], FIELDS)
    note, body = pnw.summarize_public_notices_change(old, new)
    assert "new public notice recorded" in note
    assert "existing public notice changed" in note
    assert "public notice no longer listed" in note
    assert "+ NEW NOTICE  C" in body
    assert "~ CHANGED  A" in body
    assert "- REMOVED  B" in body


def test_field_set_change_is_labelled_configuration_not_an_egle_change():
    old = pnw.public_notices_snapshot([_pn()], FIELDS)
    new = pnw.public_notices_snapshot([_pn()], tuple(f for f in FIELDS if f != "coverage"))
    note, body = pnw.summarize_public_notices_change(old, new)
    assert "NOT an EGLE change" in note
    assert "configuration" in note or "exclude_fields" in body
    assert "REMOVED" not in body


def test_missing_previous_snapshot_re_baselines_without_claiming_the_site_was_clean():
    note, body = pnw.summarize_public_notices_change(
        {}, pnw.public_notices_snapshot([_pn()], FIELDS))
    assert "missing or unreadable" in note
    assert "NOT mean the site previously had no active public notices" in note


def test_a_structurally_invalid_stored_snapshot_is_reported_not_crashed():
    bad = {"fields": list(FIELDS), "n": 1, "rows": [["not-enough-values"]]}
    note, body = pnw.summarize_public_notices_change(bad, pnw.public_notices_snapshot([_pn()], FIELDS))
    assert "structurally invalid" in note
    assert "re-baselines" in note


def test_a_new_record_sharing_an_existing_notice_id_is_flagged_not_silently_misreported():
    """The load-bearing assumption behind this whole design: notice_id (the
    URL-embedded id) has been unique on every live record seen — but UNLIKE
    Evaluations/Permits (verified against hundreds of records), this profile
    has had exactly one live record per sampling date, so the assumption is
    far less tested. If it were ever to stop holding, a naive by-key dict
    diff would silently collapse a genuinely NEW notice sharing an existing
    key into a bland "changed" line. It must instead be surfaced as a
    distinct, loud case."""
    old = pnw.public_notices_snapshot([_pn(notice_id="X", coverage="A")], FIELDS)
    new = pnw.public_notices_snapshot(
        [_pn(notice_id="X", coverage="A"), _pn(notice_id="X", coverage="B")], FIELDS)
    note, body = pnw.summarize_public_notices_change(old, new)
    assert "not unique" in note.lower()
    assert "1 -> 2" in note
    assert "existing public notice changed" not in note
    assert "new public notice recorded" not in note


def test_duplicate_key_count_is_zero_for_a_clean_snapshot_full_and_truncated():
    clean = pnw.public_notices_snapshot([_pn(notice_id="A"), _pn(notice_id="B")], FIELDS)
    assert pnw._duplicate_key_count(clean) == 0
    truncated = json.loads(pnw._cell_payload(clean, budget=1))   # force degrade
    assert pnw._duplicate_key_count(truncated) == 0


def test_no_ref_level_diff_when_snapshots_are_equal_but_hash_check_was_skipped():
    snap = pnw.public_notices_snapshot([_pn()], FIELDS)
    note, body = pnw.summarize_public_notices_change(snap, copy.deepcopy(snap))
    assert note == "changed (no ref-level diff — see snapshot)"


def test_alert_line_count_is_capped_and_says_what_it_dropped():
    many = [_pn(notice_id=f"N-{i:05d}") for i in range(pnw.MAX_ALERT_LINES + 25)]
    note, body = pnw.summarize_public_notices_change(
        pnw.public_notices_snapshot([], FIELDS), pnw.public_notices_snapshot(many, FIELDS))
    # Each NEW NOTICE line here is two physical lines (headline + indented
    # comments line) — count logical entries, not raw text lines.
    assert body.count("+ NEW NOTICE") == pnw.MAX_ALERT_LINES
    assert "and 25 more change line(s) not shown" in body.splitlines()[-1]


def test_format_change_body_has_essentials_and_no_severity_judgment():
    body = pnw.format_change_body("nSITE Active Public Notices — Arbor Hills Energy (N1504)",
                                  "new public notice recorded", "+ NEW NOTICE  N-1")
    assert "Arbor Hills Energy (N1504)" in body
    assert "new public notice recorded" in body
    assert "N-1" in body
    assert "no severity judgment" in body


def test_format_change_body_discloses_the_rop_overlap():
    """The core mitigation ADR 032 asks for regardless of which of the three
    ROP-overlap options Trisha eventually picks: every alert this profile
    sends must say plainly that it may duplicate Stream H's ROP watch."""
    body = pnw.format_change_body("nSITE Active Public Notices — Arbor Hills Energy (N1504)",
                                  "new public notice recorded", "+ NEW NOTICE  N-1")
    assert "ROP" in body
    assert "known, disclosed overlap" in body


# ==============================================================================
# The publicNotifComments cap — a per-record guard, distinct from the general
# multi-record Sheets cell budget every sibling nSITE watch also carries
# ==============================================================================


def test_comments_under_the_cap_pass_through_unchanged():
    short = "A short public notice."
    assert pnw._capped_comments(short) == short


def test_comments_over_the_cap_are_truncated_with_a_content_hash():
    long_comments = "x" * (pnw.COMMENTS_STORED_CHARS + 500)
    capped = pnw._capped_comments(long_comments)
    assert len(capped) < len(long_comments)
    assert capped.startswith("x" * pnw.COMMENTS_STORED_CHARS)
    assert "truncated" in capped and "sha256=" in capped


def test_a_change_past_the_truncation_point_still_changes_the_stored_value():
    """The whole point of appending a hash: two DIFFERENT long comments that
    share the same first COMMENTS_STORED_CHARS must still produce different
    stored values, so the change is still detected (and still alerts)."""
    base = "x" * pnw.COMMENTS_STORED_CHARS
    a = pnw._capped_comments(base + "AAA")
    b = pnw._capped_comments(base + "BBB")
    assert a != b


def test_snapshot_caps_comments_before_they_ever_reach_the_stored_row():
    long_comments = "y" * (pnw.COMMENTS_STORED_CHARS + 1000)
    snap = pnw.public_notices_snapshot([_pn(comments=long_comments)], FIELDS)
    stored_comments = snap["rows"][0][FIELDS.index("comments")]
    assert len(stored_comments) < len(long_comments)


def test_the_real_n1504_comments_are_well_under_the_cap():
    """Sanity check against the actual live specimen: real notices are nowhere
    near COMMENTS_STORED_CHARS, so the cap is documented insurance, not a
    live-triggering path today."""
    assert len(_RAW_NOTICE_N1504["publicNotifComments"]) < pnw.COMMENTS_STORED_CHARS


# ==============================================================================
# The Sheets cell-size guard — theoretical insurance at this profile's
# observed volume (0-1 records per site), inherited for structural parity
# ==============================================================================


def test_cell_payload_passes_through_under_budget():
    snap = pnw.public_notices_snapshot([_pn()], FIELDS)
    payload = pnw._cell_payload(snap, budget=45000)
    assert json.loads(payload)["rows"] == snap["rows"]
    assert "truncated" not in payload


def test_cell_payload_degrades_past_budget_and_keeps_notice_id_visible():
    rows = [_pn(notice_id=f"N-{i:05d}") for i in range(400)]
    snap = pnw.public_notices_snapshot(rows, FIELDS)
    full = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    assert len(full) > 20000
    payload = pnw._cell_payload(snap, budget=20000)
    assert len(payload) <= 20000
    body = json.loads(payload)
    assert body["truncated"] is True
    assert body["n"] == 400
    assert len(body["digests"]) == 400


def test_the_degraded_form_still_names_the_new_notice_id_not_just_a_count():
    old_rows = [_pn(notice_id=f"N-{i:05d}") for i in range(200)]
    old_payload = json.loads(pnw._cell_payload(
        pnw.public_notices_snapshot(old_rows, FIELDS), budget=8000))
    new_snap = pnw.public_notices_snapshot(old_rows + [_pn(notice_id="N-BRANDNEW")], FIELDS)
    note, body = pnw.summarize_public_notices_change(old_payload, new_snap)
    assert "1 new notice" in note
    assert "+ NEW NOTICE  N-BRANDNEW" in body


def test_the_truncated_fallback_is_itself_bounded():
    huge = pnw.public_notices_snapshot(
        [_pn(notice_id=f"N-{i:06d}", coverage=f"detail {i}") for i in range(4000)], FIELDS)
    payload = pnw._cell_payload(huge, budget=pnw.DEFAULT_SNAPSHOT_CHAR_BUDGET)
    assert len(payload) <= pnw.DEFAULT_SNAPSHOT_CHAR_BUDGET
    body = json.loads(payload)
    assert body.get("digests_dropped") in (True, None)   # may or may not clamp; must not crash
    assert body["n"] == 4000


def test_snapshot_hash_ignores_the_cell_budget_entirely():
    snap = pnw.public_notices_snapshot([_pn(notice_id=f"N-{i:06d}") for i in range(600)], FIELDS)
    assert pnw._cell_payload(snap, budget=10) != pnw._cell_payload(snap, budget=999999)
    assert pnw.snapshot_hash(snap) == pnw.snapshot_hash(copy.deepcopy(snap))


def test_a_json_scalar_in_the_snapshot_cell_does_not_wedge_the_site():
    for raw in ("0", "null", "true", "42", '"hello"', "[]", "not json at all"):
        assert pnw._load_json(raw, {}) == {} or isinstance(pnw._load_json(raw, {}), dict)
    note, _ = pnw.summarize_public_notices_change(
        pnw._load_json("0", {}), pnw.public_notices_snapshot([_pn()], FIELDS))
    assert "missing or unreadable" in note


# ==============================================================================
# Config gate + cadence wiring
# ==============================================================================


def test_should_run_false_when_disabled():
    ok, reason = pnw._should_run({"nsite_public_notices": {"enabled": False}})
    assert ok is False and "false" in reason.lower()


def test_should_run_false_when_key_absent():
    ok, _ = pnw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = pnw._should_run({"nsite_public_notices": {"enabled": True}})
    assert ok is True and reason == ""


def test_is_due_is_imported_not_reimplemented():
    assert pnw._is_due is sub_w._is_due


def test_diff_fields_defaults_to_every_field():
    assert pnw.diff_fields({}) == nc.PUBLIC_NOTICE_FIELDS


def test_diff_fields_honors_the_exclude_lever():
    fields = pnw.diff_fields({"nsite_public_notices": {"exclude_fields": ["coverage"]}})
    assert "coverage" not in fields
    assert len(fields) == len(nc.PUBLIC_NOTICE_FIELDS) - 1


def test_diff_fields_never_excludes_notice_id_even_if_configured_to():
    fields = pnw.diff_fields(
        {"nsite_public_notices": {"exclude_fields": ["notice_id", "coverage"]}})
    assert "notice_id" in fields
    assert "coverage" not in fields


def test_alerting_is_configured_detects_each_way_delivery_can_be_impossible(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(var, "x")
    ok, reason = pnw.alerting_is_configured({}, ["a@example.com"])
    assert ok is True and reason == ""

    monkeypatch.delenv("SMTP_PASSWORD")
    ok, reason = pnw.alerting_is_configured({}, ["a@example.com"])
    assert ok is False and "SMTP_PASSWORD" in reason

    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(pnw.ea, "resolve_recipients", lambda cfg: [])
    ok, reason = pnw.alerting_is_configured({}, None)
    assert ok is False and "recipients" in reason


def test_structural_error_is_a_subclass_so_existing_handlers_still_catch_it():
    assert issubclass(nc.NsiteStructuralError, nc.NsiteFetchError)


def test_the_workflow_is_scheduled_directly_not_parked():
    """This build's SSH key authenticated non-interactively, so the workflow
    was landed straight into .github/workflows/ rather than parked. Still
    tolerant of a future re-park, matching the enforcing pattern every
    sibling watch's test suite established."""
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "config.yml") as f:
        cfg = yaml.safe_load(f)
    scheduled = (root / ".github" / "workflows" / "nsite-active-public-notices-watch.yml").exists()
    parked = (root / "docs" / "pending-workflows" / "nsite-active-public-notices-watch.yml").exists()
    assert scheduled or parked, "the workflow file has gone missing entirely"
    assert cfg["nsite_public_notices"]["enabled"] is False or scheduled, (
        "nsite_public_notices.enabled is true but the workflow is still "
        "parked at docs/pending-workflows/ — the watch would never be "
        "scheduled."
    )


def test_shipped_config_tiers_cover_every_registry_site_and_differ_from_siblings():
    import pathlib

    import yaml
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    tiers = cfg["nsite_public_notices"]["tiers"]
    registry = {s["srn"] for s in cfg["nsite_sites"]}
    assert set(tiers) == registry
    assert set(tiers.values()) <= {"daily", "biweekly", "quarterly"}
    assert tiers != cfg["nsite_evaluations"]["tiers"]
    assert tiers != cfg["nsite_permits"]["tiers"]
    assert tiers != cfg["nsite_complaints"]["tiers"]


def test_shipped_config_ships_disabled_and_stays_disabled():
    """UNLIKE every sibling new-poller gate, this one is not merely "not yet
    activated" — it is deliberately held pending Trisha's answer to the
    ROP-overlap design question (ADR 032). This asserts the flag itself,
    not just that a workflow exists to run it, because turning it on
    unmodified silently picks the "standalone" option by default."""
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "config.yml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["nsite_public_notices"]["enabled"] is False


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

SITES = [
    {"srn": "N1504", "name": "Arbor Hills Energy", "id": "-4937599654678851055"},
    {"srn": "WRD", "name": "GFL-Arbor Hills Landfill-Washtenaw Co", "id": "306291952280313698"},
]

PN_CFG = {
    "nsite_sites": SITES,
    "nsite_public_notices": {"enabled": True, "tiers": {"N1504": "daily", "WRD": "daily"}},
    "alert_recipients": ["a@example.com"],
}

_N1504_ID = "-4937599654678851055"


def _wire(monkeypatch, cfg, fetch_by_srn):
    """fetch_by_srn: dict of srn -> (list[dict] | Exception) OR a callable
    srn -> list[dict]. Looks srn up against whatever `nsite_sites` list is IN
    THE PASSED cfg, so tests that override it still resolve correctly."""
    fake = FakeSheets()
    sent = []
    sites = cfg["nsite_sites"]
    monkeypatch.setenv("GSHEET_ID", "SID")
    for _var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(_var, "test-value")
    monkeypatch.setattr(pnw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(pnw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(pnw.nc, "make_session", lambda: object())
    monkeypatch.setattr(pnw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(session, nsite_id):
        srn = next(s["srn"] for s in sites if s["id"] == nsite_id)
        result = fetch_by_srn(srn) if callable(fetch_by_srn) else fetch_by_srn[srn]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_PUBLIC_NOTICES, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    """The shipped state: enabled is false, so the scheduled job must not even
    build a Sheets client."""
    monkeypatch.setattr(pnw, "load_config",
                        lambda: {"nsite_public_notices": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(pnw.dc, "sheets_service", boom)
    assert pnw.run() == 0


def test_tiers_srn_missing_from_registry_raises_keyerror(monkeypatch):
    cfg = {
        "nsite_sites": [{"srn": "N1504", "name": "AH", "id": _N1504_ID}],
        "nsite_public_notices": {"enabled": True, "tiers": {"TYPO_SRN": "daily"}},
    }
    monkeypatch.setattr(pnw, "load_config", lambda: cfg)
    with pytest.raises(KeyError, match="TYPO_SRN"):
        pnw.run()


def test_run_skips_a_site_that_is_not_due_today_no_fetch_no_row(monkeypatch):
    sites = [
        {"srn": "N1504", "name": "Arbor Hills Energy", "id": _N1504_ID},
        {"srn": "COMP", "name": "Arbor Hills Composting Faciltiy", "id": "-2164784335333909072"},
    ]
    cfg = {
        "nsite_sites": sites,
        "nsite_public_notices": {"enabled": True,
                                 "tiers": {"N1504": "daily", "COMP": "quarterly"}},
        "alert_recipients": ["a@example.com"],
    }
    fake, sent = _wire(monkeypatch, cfg, {
        "N1504": [_pn()],
        "COMP": AssertionError("a not-due site must never be fetched"),
    })
    monkeypatch.setattr(pnw, "_is_due", lambda cadence, srn, today: srn != "COMP")
    assert pnw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"pubntc:N1504"}
    assert sent == []


def test_first_run_baselines_every_site_silently_including_zero_notice_sites(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {
        "N1504": [_pn() for _ in range(2)], "WRD": [],
    })
    assert pnw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert {r[1] for r in rows} == {"pubntc:N1504", "pubntc:WRD"}
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn()], "WRD": []})
    pnw.run()
    assert pnw.run() == 0
    assert len(_rows(fake)) == 2
    assert sent == []


def test_new_notice_emails_an_alert_with_the_rop_disclosure(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn(notice_id="N-1")], "WRD": []})
    pnw.run()   # baseline
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1"), _pn(notice_id="N-2", coverage="Statewide")]
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 0
    matches = [s for s in sent if "N1504" in s[0] or "Arbor Hills Energy" in s[0]]
    assert len(matches) == 1
    assert "+ NEW NOTICE" in matches[0][1]
    assert "N-2" in matches[0][1]
    assert "known, disclosed overlap" in matches[0][1]
    assert matches[0][2] is None   # None -> send_email resolves full alert_recipients


def test_detail_change_on_an_existing_notice_emails_an_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG,
                       {"N1504": [_pn(notice_id="N-1", end_date="2026-09-09")], "WRD": []})
    pnw.run()   # baseline
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1", end_date="2026-10-09")]
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 0
    matches = [s for s in sent if "Arbor Hills Energy" in s[0]]
    assert len(matches) == 1
    assert "N-1" in matches[0][1]
    assert "end_date: 2026-09-09 -> 2026-10-09" in matches[0][1]


def test_first_notice_at_a_zero_site_alerts_as_new(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn()], "WRD": []})
    pnw.run()   # baseline: WRD at zero
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn()] if nsite_id == _N1504_ID
                            else [_pn(notice_id="N-WRD-1", coverage="Wetland")]))
    assert pnw.run() == 0
    matches = [s for s in sent if "WRD" in s[0] or "Washtenaw" in s[0]]
    assert len(matches) == 1
    assert "new public notice recorded" in matches[0][1]
    assert "N-WRD-1" in matches[0][1]


def test_no_egle_derived_text_reaches_the_email_subject(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn(notice_id="N-1")], "WRD": []})
    pnw.run()
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1"),
                             _pn(notice_id="INJECTED\nSubject: evil",
                                 coverage="also injected\r\nX-Header: bad")]
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 0
    subjects = [s[0] for s in sent]
    assert subjects == [
        "[Public Notices watch] nSITE Active Public Notices — "
        "Arbor Hills Energy (N1504) changed"]
    assert all("\n" not in s and "INJECTED" not in s for s in subjects)


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn()], "WRD": []})
    pnw.run()   # baseline
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("blip"))
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 0   # not loud
    assert len(_rows(fake)) == 2   # unchanged, nothing new appended
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud_but_other_site_still_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {
        "N1504": nc.NsiteFetchError("bot wall on the runner"), "WRD": [_pn()],
    })
    assert pnw.run() == 1
    assert {r[1] for r in _rows(fake)} == {"pubntc:WRD"}   # partial, not all-or-nothing
    assert sent == []


def test_a_structural_break_fails_loudly_instead_of_going_quiet(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn()], "WRD": []})
    pnw.run()                                          # baseline both
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteStructuralError("hasResultsRemaining")))
    assert pnw.run() == 1
    assert sent == []
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteFetchError("connection reset")))
    assert pnw.run() == 0


def test_a_sheet_write_failure_isolates_to_one_site_and_never_alerts(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn()], "WRD": [_pn()]})
    real_append = sw.append_public_notices_watch_row

    def _boom(service, sheet_id, date, item_key, *a, **kw):
        if item_key == "pubntc:N1504":
            raise RuntimeError("cell exceeded 50000 characters")
        return real_append(service, sheet_id, date, item_key, *a, **kw)
    monkeypatch.setattr(pnw.sw, "append_public_notices_watch_row", _boom)

    assert pnw.run() == 1                                   # loud
    assert {r[1] for r in _rows(fake)} == {"pubntc:WRD"}     # the later site still ran
    assert sent == []                                        # no alert for the failed row


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(PN_CFG)
    cfg["nsite_public_notices"]["recipients"] = ["trisha@example.com"]
    fake, sent = _wire(monkeypatch, cfg, {"N1504": [_pn(notice_id="N-1")], "WRD": []})
    pnw.run()   # baseline
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1"), _pn(notice_id="N-2")]
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 0
    matches = [s for s in sent if "Arbor Hills Energy" in s[0]]
    assert len(matches) == 1
    assert matches[0][2] == ["trisha@example.com"]


def test_alert_email_failure_keeps_the_durable_row_and_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn(notice_id="N-1")], "WRD": []})
    pnw.run()   # baseline
    monkeypatch.setattr(pnw.ea, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP down")))
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1"), _pn(notice_id="N-2")]
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 1
    n1504_rows = [r for r in _rows(fake) if r[1] == "pubntc:N1504"]
    assert len(n1504_rows) == 2 and n1504_rows[1][3] == "changed"


def test_known_undeliverable_alerting_defers_the_change_instead_of_consuming_it(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn(notice_id="N-1")], "WRD": []})
    pnw.run()                                          # healthy baseline first
    before = [list(r) for r in _rows(fake)]
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1"), _pn(notice_id="N-2")]
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 1                              # loud
    assert [list(r) for r in _rows(fake)] == before    # nothing consumed
    assert sent == []

    monkeypatch.setenv("SMTP_PASSWORD", "test-value")
    assert pnw.run() == 0
    assert len([s for s in sent if "Arbor Hills Energy" in s[0]]) == 1


def test_a_transient_tab_read_failure_aborts_before_any_write(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG, {"N1504": [_pn(notice_id="N-1")], "WRD": []})
    pnw.run()                                   # baseline both sites
    before = [list(r) for r in _rows(fake)]

    def _boom(service, sheet_id, item_keys):
        raise RuntimeError("HTTP 429 rate limited")
    monkeypatch.setattr(pnw.sw, "last_public_notices_snapshots", _boom)
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1"), _pn(notice_id="N-REAL-NEW")]
                            if nsite_id == _N1504_ID else []))

    assert pnw.run() == 1
    assert [list(r) for r in _rows(fake)] == before   # nothing written at all
    assert sent == []


def test_a_cleared_snapshot_cell_does_not_masquerade_as_a_clean_site(monkeypatch):
    fake, sent = _wire(monkeypatch, PN_CFG,
                       {"N1504": [_pn(notice_id="N-1"), _pn(notice_id="N-2")], "WRD": []})
    pnw.run()   # baseline
    for r in fake._values._tabs[sw.TAB_PUBLIC_NOTICES]:
        if len(r) > 7 and r[1] == "pubntc:N1504":
            r[7] = ""            # a human clears the big JSON cell
    monkeypatch.setattr(pnw.nc, "fetch_site_public_notices",
                        lambda session, nsite_id: (
                            [_pn(notice_id="N-1"), _pn(notice_id="N-2"), _pn(notice_id="N-3")]
                            if nsite_id == _N1504_ID else []))
    assert pnw.run() == 0
    matches = [s for s in sent if "Arbor Hills Energy" in s[0]]
    assert len(matches) == 1
    assert "new public notice recorded" not in matches[0][1]
    assert "missing or unreadable" in matches[0][1]


def test_an_unrecognized_cadence_polls_rather_than_silently_skipping(monkeypatch):
    cfg = copy.deepcopy(PN_CFG)
    cfg["nsite_public_notices"]["tiers"] = {"N1504": "dayly", "WRD": "daily"}
    fake, sent = _wire(monkeypatch, cfg, {"N1504": [_pn()], "WRD": []})
    assert pnw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"pubntc:N1504", "pubntc:WRD"}


def test_last_public_notices_snapshots_batches_into_one_tab_read(monkeypatch):
    fake = FakeSheets()
    calls = []
    orig = fake.values
    monkeypatch.setattr(fake, "values", lambda: (calls.append(1) or orig()))
    sw.ensure_public_notices_tabs(fake, "SID")
    sw.append_public_notices_watch_row(fake, "SID", "2026-08-25", "pubntc:A", "A", "baseline",
                                       "hash1", "note", "now", "{}")
    sw.append_public_notices_watch_row(fake, "SID", "2026-08-25", "pubntc:B", "B", "baseline",
                                       "hash2", "note", "now", "{}")
    calls.clear()
    result = sw.last_public_notices_snapshots(fake, "SID", ["pubntc:A", "pubntc:B", "pubntc:C"])
    assert result["pubntc:A"] == ("hash1", "{}")
    assert result["pubntc:B"] == ("hash2", "{}")
    assert result["pubntc:C"] is None
    assert len(calls) == 1   # one values() call for all three keys, not three


def test_last_public_notices_snapshots_raises_rather_than_swallowing_a_read_error():
    class _Exploding:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId, range):
            raise RuntimeError("HTTP 503")
    with pytest.raises(RuntimeError):
        sw.last_public_notices_snapshots(_Exploding(), "SID", ["pubntc:A"])


def test_run_issues_exactly_one_tab_read_for_all_sites(monkeypatch):
    reads = []
    real = sw.last_public_notices_snapshots

    def _counting(service, sheet_id, item_keys):
        reads.append(list(item_keys))
        return real(service, sheet_id, item_keys)
    monkeypatch.setattr(pnw.sw, "last_public_notices_snapshots", _counting)
    _wire(monkeypatch, PN_CFG, {"N1504": [_pn()], "WRD": []})
    assert pnw.run() == 0
    assert len(reads) == 1
    assert sorted(reads[0]) == ["pubntc:N1504", "pubntc:WRD"]


def test_public_notices_tab_is_separate_state_from_its_sibling_watches():
    """Ref-number-keyed sibling tabs must never cross-read each other's rows."""
    assert sw.TAB_PUBLIC_NOTICES not in (sw.TAB_EVALUATIONS, sw.TAB_PERMITS, sw.TAB_COMPLAINTS)
    fake = FakeSheets()
    sw.ensure_public_notices_tabs(fake, "SID")
    sw.ensure_evaluations_tabs(fake, "SID")
    sw.append_public_notices_watch_row(fake, "SID", "2026-08-25", "pubntc:N1504", "M", "baseline",
                                       "phash", "n", "now", "{}")
    assert sw.last_evaluations_snapshots(fake, "SID", ["pubntc:N1504"])["pubntc:N1504"] is None
    assert sw.last_public_notices_snapshots(
        fake, "SID", ["pubntc:N1504"])["pubntc:N1504"] == ("phash", "{}")
