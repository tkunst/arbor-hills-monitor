"""nsite_client.fetch_site_compliance_actions + nsite_compliance_actions_watcher
(Stream M, ADR 028) — the activation gate, the fetch-error contract (a failure
must never look like "zero compliance actions"), the pure counted-multiset
snapshot/diff helpers, the cell-size guard, and the full baseline/unchanged/new/
changed/fetch-fail flows driven through a fake Sheets service (no network, no
creds). Mirrors tests/test_nsite_violations.py's structure; reuses FakeSheets
from test_pfas_watcher.

Several fixtures below are REAL records copied verbatim from the 2026-08-08 live
spike across the 5 known sites — including the `5:21-cv-12098-S` federal case
number that N2688 files on TWO records, which is the finding that ruled out a
ref-number-keyed diff and forced the multiset (see ADR 028)."""
import copy
import json

import pytest

import nsite_client as nc
import nsite_compliance_actions_watcher as caw
import nsite_submissions_watcher as sub_w
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# fetch_site_compliance_actions — the fetch-error contract
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


# Verbatim from the live N2688 profile, 2026-08-08 — the most recent CA, an
# open ("Issued") Violation Notice.
_RAW_CA = {
    "cmplActnRefProgramAreaDescr": "AQD - Air",
    "cmplActnRefCmplActnCatgDescr": "Administrative",
    "cmplActnRefCmplActnTypeDescr": "Violation Notice",
    "cmplActnCmplActnNum": "VN-019436",
    "cmplActnActnDate": "2026-07-15T00:00:00.0000000-04:00",
    "cmplActnRefCmplActnStatDescr": "Issued",
}

# The two records N2688 files under ONE number (`5:21-cv-12098-S`, a federal
# case) — a Consent Order "Entered" on two dates. This is the finding that ruled
# out a ref-number key. Verbatim from the live spike.
_RAW_CA_DUP_A = {
    "cmplActnRefProgramAreaDescr": "AQD - Air",
    "cmplActnRefCmplActnCatgDescr": "Administrative - Formal",
    "cmplActnRefCmplActnTypeDescr": "Administrative Consent Order",
    "cmplActnCmplActnNum": "5:21-cv-12098-S",
    "cmplActnActnDate": "2022-08-22T00:00:00.0000000-04:00",
    "cmplActnRefCmplActnStatDescr": "Entered",
}
_RAW_CA_DUP_B = {
    **_RAW_CA_DUP_A,
    "cmplActnActnDate": "2021-12-15T00:00:00.0000000-05:00",   # note: EST offset
}


def test_fetch_normalizes_a_real_shaped_record():
    session = _Session(_Resp({"queryResults": [_RAW_CA]}))
    rows = nc.fetch_site_compliance_actions(session, "8094300008956198244")
    assert rows == [{
        "num": "VN-019436",
        "type": "Violation Notice",
        "status": "Issued",
        "action_date": "2026-07-15",          # UTC offset dropped, date kept
        "category": "Administrative",
        "program": "AQD - Air",
    }]


def test_fetch_keeps_both_records_that_share_a_number():
    """`cmplActnCmplActnNum` is NOT unique — N2688 files `5:21-cv-12098-S` on two
    records. A ref-number-keyed diff would silently drop one; the client must
    keep both."""
    session = _Session(_Resp({"queryResults": [_RAW_CA_DUP_A, _RAW_CA_DUP_B]}))
    rows = nc.fetch_site_compliance_actions(session, "X")
    assert len(rows) == 2
    assert {r["num"] for r in rows} == {"5:21-cv-12098-S"}
    assert {r["action_date"] for r in rows} == {"2022-08-22", "2021-12-15"}


def test_fetch_normalizes_the_est_offset_the_same_as_edt():
    """The raw value carries -05:00 in EST and -04:00 in EDT; the calendar date
    is the signal. If the offset survived, the same action would fire a false
    "changed" alert every fall when the offset flips."""
    session = _Session(_Resp({"queryResults": [_RAW_CA_DUP_B]}))
    assert nc.fetch_site_compliance_actions(session, "X")[0]["action_date"] == "2021-12-15"


def test_fetch_normalizes_a_null_field_to_empty_string():
    """`.get(f, "")` returns None for a present-but-null key, so the day EGLE
    serves "" instead the hash would flip for no real reason. `or ""` collapses
    both to "". (No nulls were seen in the spike, but the guard is free and
    matches _normalize_violation.)"""
    raw = dict(_RAW_CA, cmplActnRefProgramAreaDescr=None)
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_compliance_actions(session, "X")
    assert rows[0]["program"] == ""
    assert all(v is not None for v in rows[0].values())


def test_fetch_survives_an_out_of_range_date_instead_of_blinding_the_site():
    """A single "2026-02-30" would otherwise raise ValueError out of date(),
    escape into the retry loop, and surface as a permanent NsiteFetchError —
    blinding the ENTIRE site over one bad record."""
    raw = dict(_RAW_CA, cmplActnActnDate="2026-02-30T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_compliance_actions(session, "X")
    assert rows[0]["action_date"] == ""
    assert rows[0]["num"] == "VN-019436"
    assert session.calls == 1          # not retried — it was never an error


def test_fetch_accepts_the_us_style_date_format_too():
    raw = dict(_RAW_CA, cmplActnActnDate="07/15/2026")
    session = _Session(_Resp({"queryResults": [raw]}))
    assert nc.fetch_site_compliance_actions(session, "X")[0]["action_date"] == "2026-07-15"


def test_fetch_raises_rather_than_diffing_a_partial_page():
    session = _Session(_Resp({"queryResults": [_RAW_CA], "hasResultsRemaining": True}))
    with pytest.raises(nc.NsiteStructuralError, match="paging"):
        nc.fetch_site_compliance_actions(session, "X")


def test_fetch_accepts_the_null_hasresultsremaining_the_api_actually_sends():
    session = _Session(_Resp({"queryResults": [_RAW_CA],
                              "hasResultsRemaining": None, "totalCount": None}))
    assert len(nc.fetch_site_compliance_actions(session, "X")) == 1


def test_fetch_empty_queryresults_is_a_valid_zero_result():
    """Most watched sites have zero compliance actions. A structurally-sound
    response listing none is NOT an error — it is the baseline."""
    session = _Session(_Resp({"queryResults": []}))
    assert nc.fetch_site_compliance_actions(session, "X") == []


def test_fetch_raises_never_returns_empty_on_http_error():
    """THE contract this feature depends on: an HTTP failure must raise, never
    silently degrade to []. Otherwise the watcher would misread a fetch outage
    as 'every compliance action closed at once' and fire a false all-clear."""
    session = _Session(_Resp(status=500))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_compliance_actions(session, "X")


def test_fetch_raises_on_missing_queryresults_key():
    session = _Session(_Resp({"somethingElse": []}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_compliance_actions(session, "X")


def test_fetch_raises_on_non_json_body():
    session = _Session(_Resp(json_ok=False))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_compliance_actions(session, "X")


def test_fetch_raises_on_structurally_broken_record():
    session = _Session(_Resp({"queryResults": ["not-a-dict"]}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_compliance_actions(session, "X")


def test_fetch_retries_then_succeeds():
    session = _Session([_Resp(status=500), _Resp({"queryResults": [_RAW_CA]})])
    rows = nc.fetch_site_compliance_actions(session, "X")
    assert len(rows) == 1
    assert session.calls == 2


def test_fetch_hits_the_compliance_actions_endpoint_with_the_site_filter():
    """The endpoint path / filter param / Referer are otherwise only verifiable
    against the live API (the _Session fake ignores the URL). A copy-paste of
    the wrong profile path — e.g. reusing the Violations endpoint — is a
    plausible bug that would 404 in production, and the safe failure mode
    (loud NsiteFetchError at activation) means it would not surface until then.
    Pin the URL construction so a wrong constant fails the suite instead."""
    captured = {}

    class _CapturingSession:
        def get(self, url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _Resp({"queryResults": [_RAW_CA]})

    nc.fetch_site_compliance_actions(_CapturingSession(), "8094300008956198244")
    assert "/profiles/3-compliance/3-compliance-actions" in captured["url"]
    assert "/profiles/3-compliance/2-violations" not in captured["url"]   # not the sibling
    assert "responseContentType=application" in captured["url"]
    assert "8094300008956198244" in captured["url"]                       # site id in queryParams
    assert "detail/8094300008956198244" in captured["headers"].get("Referer", "")


def test_fetch_coerces_a_non_string_date_to_empty_instead_of_crashing():
    """A sibling EGLE ArcGIS feed serves epoch-ms INTEGERS for dates. A non-str
    here would raise TypeError out of the slicing/regex (not the ValueError the
    parse paths catch), escape into the retry loop, and blind the whole site
    behind a permanent NsiteFetchError. It must fail soft to ""."""
    raw = dict(_RAW_CA, cmplActnActnDate=1723075200000)   # epoch ms as an int
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_compliance_actions(session, "X")
    assert rows[0]["action_date"] == ""
    assert rows[0]["num"] == "VN-019436"
    assert session.calls == 1   # not retried — it was never an error


# ==============================================================================
# Pure snapshot / diff helpers
# ==============================================================================

FIELDS = nc.COMPLIANCE_ACTION_FIELDS


def _ca(num="VN-019436", status="Issued", **kw):
    base = {
        "num": num, "type": "Violation Notice", "status": status,
        "action_date": "2026-07-15", "category": "Administrative", "program": "AQD - Air",
    }
    base.update(kw)
    return base


def test_snapshot_hash_stable_across_row_order():
    a = caw.compliance_actions_snapshot([_ca(num="B"), _ca(num="A")], FIELDS)
    b = caw.compliance_actions_snapshot([_ca(num="A"), _ca(num="B")], FIELDS)
    assert caw.snapshot_hash(a) == caw.snapshot_hash(b)


def test_snapshot_hash_changes_when_any_field_changes():
    a = caw.compliance_actions_snapshot([_ca(status="Issued")], FIELDS)
    b = caw.compliance_actions_snapshot([_ca(status="Closed")], FIELDS)
    assert caw.snapshot_hash(a) != caw.snapshot_hash(b)


def test_snapshot_preserves_duplicate_counts_a_set_would_destroy():
    """A byte-identical duplicate action must snapshot as count=2, and losing
    one of them must change the hash — the reason the multiset (not a set) is
    load-bearing even though today's full tuples happen to be distinct."""
    three = caw.compliance_actions_snapshot([_ca(), _ca(), _ca()], FIELDS)
    two = caw.compliance_actions_snapshot([_ca(), _ca()], FIELDS)
    assert three["n"] == 3 and len(three["counted_rows"]) == 1
    assert three["counted_rows"][0][0] == 3
    assert caw.snapshot_hash(three) != caw.snapshot_hash(two)


def test_snapshot_keeps_two_records_sharing_a_number():
    """The real N2688 case: two Consent-Order records share `5:21-cv-12098-S`
    but differ by date. The multiset keeps both as distinct rows; a ref-keyed
    diff would collapse them to one."""
    snap = caw.compliance_actions_snapshot(
        [_ca(num="5:21-cv-12098-S", action_date="2022-08-22"),
         _ca(num="5:21-cv-12098-S", action_date="2021-12-15")], FIELDS)
    assert snap["n"] == 2 and len(snap["counted_rows"]) == 2


def test_snapshot_of_empty_list_is_a_valid_baseline():
    snap = caw.compliance_actions_snapshot([], FIELDS)
    assert snap["n"] == 0 and snap["counted_rows"] == []
    assert caw.snapshot_hash(snap)   # hashes fine, does not raise


def test_snapshot_is_self_describing_about_its_field_order():
    snap = caw.compliance_actions_snapshot([_ca()], ("num", "status"))
    assert snap["fields"] == ["num", "status"]
    assert snap["counted_rows"] == [[1, "VN-019436", "Issued"]]


def test_first_action_at_a_zero_site_reads_as_a_headline_not_a_bland_add():
    old = caw.compliance_actions_snapshot([], FIELDS)
    new = caw.compliance_actions_snapshot([_ca(num="VN-020000")], FIELDS)
    note, body = caw.summarize_compliance_actions_change(old, new)
    assert "FIRST COMPLIANCE ACTION(S) RECORDED" in note
    assert "VN-020000" in body
    assert "+ ADDED" in body


def test_all_actions_disappearing_reads_as_its_own_headline():
    old = caw.compliance_actions_snapshot([_ca(), _ca(num="X")], FIELDS)
    new = caw.compliance_actions_snapshot([], FIELDS)
    note, body = caw.summarize_compliance_actions_change(old, new)
    assert "ALL COMPLIANCE ACTIONS NO LONGER LISTED" in note
    assert body.count("- REMOVED") == 2


def test_status_advance_reads_as_removed_plus_added_leading_with_the_number():
    """A multiset diff has no "updated" verb — a status change (Issued -> Closed,
    the single highest-value CA event) shows as its old shape REMOVED plus its
    new shape ADDED. The line must lead with the ACTION NUMBER (not `category`,
    which is a bare "Administrative") or the two render unidentifiably, and every
    field must appear or they render identically (rop_watcher's lesson)."""
    old = caw.compliance_actions_snapshot([_ca(num="VN-019436", status="Issued")], FIELDS)
    new = caw.compliance_actions_snapshot([_ca(num="VN-019436", status="Closed")], FIELDS)
    note, body = caw.summarize_compliance_actions_change(old, new)
    assert "1 compliance-action record(s) added/updated" in note
    assert "1 compliance-action record(s) removed/superseded" in note
    assert "+ ADDED    VN-019436" in body
    assert "- REMOVED  VN-019436" in body
    assert "status=Closed" in body
    assert "status=Issued" in body


def test_diff_reports_count_changes_among_identical_records():
    old = caw.compliance_actions_snapshot([_ca(), _ca()], FIELDS)
    new = caw.compliance_actions_snapshot([_ca(), _ca(), _ca()], FIELDS)
    note, body = caw.summarize_compliance_actions_change(old, new)
    assert "1 compliance-action record(s) added/updated" in note
    assert body.count("+ ADDED") == 1


def test_the_headline_field_follows_the_configured_field_set():
    """_detail must not hardcode the headline field: with `num` excluded via
    config, every line's headline would degrade to a bare em-dash. It should
    degrade to the next field (`type`) instead."""
    fields = tuple(f for f in FIELDS if f != "num")
    old = caw.compliance_actions_snapshot([], fields)
    new = caw.compliance_actions_snapshot([_ca(type="Consent Judgment")], fields)
    _, body = caw.summarize_compliance_actions_change(old, new)
    assert "+ ADDED    Consent Judgment" in body
    assert "+ ADDED    —" not in body


def test_field_set_change_is_labelled_configuration_not_an_egle_change():
    old = caw.compliance_actions_snapshot([_ca(), _ca(num="X")], FIELDS)
    new = caw.compliance_actions_snapshot([_ca(), _ca(num="X")],
                                          tuple(f for f in FIELDS if f != "category"))
    note, body = caw.summarize_compliance_actions_change(old, new)
    assert "NOT an EGLE change" in note
    assert "configuration" in note or "exclude_fields" in body
    assert "REMOVED" not in body


def test_alert_line_count_is_capped_and_says_what_it_dropped():
    many = [_ca(num=f"VN-{i:05d}") for i in range(caw.MAX_ALERT_LINES + 25)]
    note, body = caw.summarize_compliance_actions_change(
        caw.compliance_actions_snapshot([], FIELDS),
        caw.compliance_actions_snapshot(many, FIELDS))
    lines = body.splitlines()
    assert len(lines) == caw.MAX_ALERT_LINES + 1     # + the "and N more" line
    assert "and 25 more change line(s) not shown" in lines[-1]


def test_format_change_body_has_essentials_and_no_severity_judgment():
    body = caw.format_change_body("nSITE Compliance Actions — Arbor Hills Landfill (N2688)",
                                  "FIRST COMPLIANCE ACTION(S) RECORDED", "+ ADDED  VN-019436")
    assert "Arbor Hills Landfill (N2688)" in body
    assert "FIRST COMPLIANCE ACTION(S) RECORDED" in body
    assert "VN-019436" in body
    assert "NO judgment about which status is good or bad" in body


# ==============================================================================
# The Sheets cell-size guard (inherited from ADR 023; never fires at CA volumes)
# ==============================================================================


def test_cell_payload_passes_through_under_budget():
    snap = caw.compliance_actions_snapshot([_ca()], FIELDS)
    payload = caw._cell_payload(snap, budget=45000)
    assert json.loads(payload)["counted_rows"] == snap["counted_rows"]
    assert "truncated" not in payload


def test_cell_payload_degrades_to_digests_over_budget():
    snap = caw.compliance_actions_snapshot([_ca(num=f"VN-{i:06d}") for i in range(600)], FIELDS)
    full = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    budget = 20000
    assert len(full) > budget                    # the fixture really is oversized
    payload = caw._cell_payload(snap, budget=budget)
    assert len(payload) <= budget
    body = json.loads(payload)
    assert body["truncated"] is True
    assert body["n"] == 600
    assert len(body["digests"]) == 600
    assert not body.get("digests_dropped")


def test_the_largest_real_ca_site_fits_a_sheets_cell_with_huge_margin():
    """N2688 (the largest, 39 records) serializes to ~4,800 chars counted —
    nowhere near the 50,000 cap, so the truncation guard is inherited insurance,
    not a live concern the way RA's 299 violations were (ADR 028)."""
    rows = [_ca(num=f"VN-{i:05d}", action_date=f"2026-{(i % 12) + 1:02d}-15") for i in range(39)]
    payload = caw._cell_payload(caw.compliance_actions_snapshot(rows, FIELDS))
    assert len(payload) < 10000
    assert "truncated" not in payload


def test_the_truncated_fallback_is_itself_bounded():
    huge = caw.compliance_actions_snapshot(
        [_ca(num=f"VN-{i:06d}", category=f"cat {i}") for i in range(4000)], FIELDS)
    payload = caw._cell_payload(huge, budget=caw.DEFAULT_SNAPSHOT_CHAR_BUDGET)
    assert len(payload) <= caw.DEFAULT_SNAPSHOT_CHAR_BUDGET
    assert len(payload) < 50000
    body = json.loads(payload)
    assert body["truncated"] is True and body["digests_dropped"] is True
    assert body["n"] == 4000


def test_a_digest_dropped_snapshot_still_diffs_at_the_count_level():
    huge = caw.compliance_actions_snapshot([_ca(num=f"VN-{i:06d}") for i in range(4000)], FIELDS)
    old = json.loads(caw._cell_payload(huge, budget=caw.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    new = caw.compliance_actions_snapshot([_ca(num=f"VN-{i:06d}") for i in range(4001)], FIELDS)
    note, _ = caw.summarize_compliance_actions_change(old, new)
    assert "4000 -> 4001" in note
    assert "no field-level diff available" in note


def test_a_structurally_invalid_stored_snapshot_is_reported_not_crashed():
    bad = {"fields": list(FIELDS), "n": 2, "counted_rows": [["not-an-int", "a"]]}
    note, body = caw.summarize_compliance_actions_change(
        bad, caw.compliance_actions_snapshot([_ca()], FIELDS))
    assert "structurally invalid" in note
    assert "re-baselines" in note


def test_snapshot_hash_ignores_the_cell_budget_entirely():
    snap = caw.compliance_actions_snapshot([_ca(num=f"VN-{i:06d}") for i in range(600)], FIELDS)
    assert caw._cell_payload(snap, budget=10) != caw._cell_payload(snap, budget=999999)
    assert caw.snapshot_hash(snap) == caw.snapshot_hash(copy.deepcopy(snap))


def test_truncated_diff_uses_digests_to_report_row_level_magnitude():
    """300 records wholly replaced by 300 different ones must not report the
    entire change as "300 -> 300" — describing nothing."""
    old_rows = [_ca(num=f"A{i:05d}") for i in range(300)]
    new_rows = [_ca(num=f"B{i:05d}") for i in range(300)]
    old = json.loads(caw._cell_payload(
        caw.compliance_actions_snapshot(old_rows, FIELDS), budget=20000))
    new = caw.compliance_actions_snapshot(new_rows, FIELDS)
    note, _ = caw.summarize_compliance_actions_change(old, new)
    assert "300 -> 300" in note
    assert "300 row(s) appeared, 300 row(s) disappeared" in note


# ==============================================================================
# Config gate + cadence wiring
# ==============================================================================


def test_should_run_false_when_disabled():
    ok, reason = caw._should_run({"nsite_compliance_actions": {"enabled": False}})
    assert ok is False and "false" in reason.lower()


def test_should_run_false_when_key_absent():
    ok, _ = caw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = caw._should_run({"nsite_compliance_actions": {"enabled": True}})
    assert ok is True and reason == ""


def test_is_due_is_imported_not_reimplemented():
    """The handoff is explicit: reuse nsite_submissions_watcher._is_due rather
    than copy-pasting it. Identity, not equivalence — a copy would drift."""
    assert caw._is_due is sub_w._is_due


def test_diff_fields_defaults_to_every_field():
    assert caw.diff_fields({}) == nc.COMPLIANCE_ACTION_FIELDS


def test_diff_fields_honors_the_exclude_lever():
    fields = caw.diff_fields({"nsite_compliance_actions": {"exclude_fields": ["category"]}})
    assert "category" not in fields
    assert len(fields) == len(nc.COMPLIANCE_ACTION_FIELDS) - 1


def test_alerting_is_configured_detects_each_way_delivery_can_be_impossible(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(var, "x")
    ok, reason = caw.alerting_is_configured({}, ["a@example.com"])
    assert ok is True and reason == ""

    monkeypatch.delenv("SMTP_PASSWORD")
    ok, reason = caw.alerting_is_configured({}, ["a@example.com"])
    assert ok is False and "SMTP_PASSWORD" in reason

    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(caw.ea, "resolve_recipients", lambda cfg: [])
    ok, reason = caw.alerting_is_configured({}, None)
    assert ok is False and "recipients" in reason


def test_a_structural_break_fails_loudly_instead_of_going_quiet(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()                                          # baseline both
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteStructuralError("hasResultsRemaining")))
    assert caw.run() == 1
    assert sent == []
    # And the plain transient case, on the same baselined state, stays quiet:
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteFetchError("connection reset")))
    assert caw.run() == 0


def test_structural_error_is_a_subclass_so_existing_handlers_still_catch_it():
    assert issubclass(nc.NsiteStructuralError, nc.NsiteFetchError)


def test_snapshot_char_budget_is_clamped_below_the_hard_sheets_cap(monkeypatch):
    seen = {}
    real = caw._diff_and_record

    def _capture(*a, **kw):
        seen["budget"] = a[9] if len(a) > 9 else kw.get("budget")
        return real(*a, **kw)
    cfg = copy.deepcopy(CA_CFG)
    cfg["nsite_compliance_actions"]["snapshot_char_budget"] = 60000
    _wire(monkeypatch, cfg, {"N2688": [_ca()], "WRD": []})
    monkeypatch.setattr(caw, "_diff_and_record", _capture)
    assert caw.run() == 0
    assert seen["budget"] < caw.HARD_SHEETS_CELL_LIMIT


def test_a_json_scalar_in_the_snapshot_cell_does_not_wedge_the_site():
    for raw in ("0", "null", "true", "42", '"hello"', "[]", "not json at all"):
        assert caw._load_json(raw, {}) == {} or isinstance(caw._load_json(raw, {}), dict)
    note, _ = caw.summarize_compliance_actions_change(
        caw._load_json("0", {}), caw.compliance_actions_snapshot([_ca()], FIELDS))
    assert "missing or unreadable" in note


def test_the_parked_workflow_must_be_in_place_before_the_stream_is_enabled():
    """The durable form of the parked-workflow hazard. Nothing in code or CI
    fails if `enabled: true` ships while the .yml is still parked outside
    .github/workflows/ — the watch would simply never run, with zero signal.
    Asserting `enabled is False` alone is not enough: that assertion is the
    first line a human deletes at activation, taking the only guard with it.
    This invariant survives activation instead."""
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "config.yml") as f:
        cfg = yaml.safe_load(f)
    scheduled = (root / ".github" / "workflows" / "nsite-compliance-actions-watch.yml").exists()
    parked = (root / "docs" / "pending-workflows" / "nsite-compliance-actions-watch.yml").exists()
    assert scheduled or parked, "the workflow file has gone missing entirely"
    assert cfg["nsite_compliance_actions"]["enabled"] is False or scheduled, (
        "nsite_compliance_actions.enabled is true but the workflow is still parked "
        "at docs/pending-workflows/ — the watch would never be scheduled. "
        "git mv docs/pending-workflows/nsite-compliance-actions-watch.yml .github/workflows/"
    )


def test_shipped_config_tiers_cover_every_registry_site_and_differ_from_violations():
    """The tiers map must stay in sync with nsite_sites (a srn in one and not the
    other is either an unwatched site or a loud KeyError at runtime), ship
    disabled (new source), and — the handoff's explicit instruction — NOT be a
    copy of the Violations tiers."""
    import pathlib

    import yaml
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    tiers = cfg["nsite_compliance_actions"]["tiers"]
    registry = {s["srn"] for s in cfg["nsite_sites"]}
    assert set(tiers) == registry
    assert set(tiers.values()) <= {"daily", "biweekly", "quarterly"}
    assert cfg["nsite_compliance_actions"]["enabled"] is False   # new-source gate
    assert tiers != cfg["nsite_violations"]["tiers"]
    assert tiers != cfg["nsite_submissions"]["tiers"]


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

SITES = [
    {"srn": "N2688", "name": "Arbor Hills Landfill", "id": "8094300008956198244"},
    {"srn": "WRD", "name": "GFL-Arbor Hills Landfill-Washtenaw Co", "id": "306291952280313698"},
]

CA_CFG = {
    "nsite_sites": SITES,
    "nsite_compliance_actions": {"enabled": True, "tiers": {"N2688": "daily", "WRD": "daily"}},
    "alert_recipients": ["a@example.com"],
}

_N2688_ID = "8094300008956198244"


def _wire(monkeypatch, cfg, fetch_by_srn):
    """fetch_by_srn: dict of srn -> (list[dict] | Exception) OR a callable
    srn -> list[dict]. Looks srn up against whatever `nsite_sites` list is IN
    THE PASSED cfg, so tests that override it still resolve correctly."""
    fake = FakeSheets()
    sent = []
    sites = cfg["nsite_sites"]
    monkeypatch.setenv("GSHEET_ID", "SID")
    # A DELIVERABLE environment is the baseline these tests model: send_email is
    # captured below, but run()'s up-front alerting check reads the real env, and
    # an unconfigured one is (correctly) a non-zero exit.
    for _var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(_var, "test-value")
    monkeypatch.setattr(caw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(caw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(caw.nc, "make_session", lambda: object())
    monkeypatch.setattr(caw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(session, nsite_id):
        srn = next(s["srn"] for s in sites if s["id"] == nsite_id)
        result = fetch_by_srn(srn) if callable(fetch_by_srn) else fetch_by_srn[srn]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_COMPLIANCE_ACTIONS, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    """The shipped state: enabled is false, so the scheduled job must not even
    build a Sheets client."""
    monkeypatch.setattr(caw, "load_config",
                        lambda: {"nsite_compliance_actions": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(caw.dc, "sheets_service", boom)
    assert caw.run() == 0


def test_tiers_srn_missing_from_registry_raises_keyerror(monkeypatch):
    cfg = {
        "nsite_sites": [{"srn": "N2688", "name": "AH", "id": _N2688_ID}],
        "nsite_compliance_actions": {"enabled": True, "tiers": {"TYPO_SRN": "daily"}},
    }
    monkeypatch.setattr(caw, "load_config", lambda: cfg)
    with pytest.raises(KeyError, match="TYPO_SRN"):
        caw.run()


def test_run_skips_a_site_that_is_not_due_today_no_fetch_no_row(monkeypatch):
    sites = [
        {"srn": "N2688", "name": "Arbor Hills Landfill", "id": _N2688_ID},
        {"srn": "COMP", "name": "Arbor Hills Composting Faciltiy", "id": "-2164784335333909072"},
    ]
    cfg = {
        "nsite_sites": sites,
        "nsite_compliance_actions": {"enabled": True,
                                     "tiers": {"N2688": "daily", "COMP": "quarterly"}},
        "alert_recipients": ["a@example.com"],
    }
    fake, sent = _wire(monkeypatch, cfg, {
        "N2688": [_ca()],
        "COMP": AssertionError("a not-due site must never be fetched"),
    })
    monkeypatch.setattr(caw, "_is_due", lambda cadence, srn, today: srn != "COMP")
    assert caw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"ca:N2688"}
    assert sent == []


def test_first_run_baselines_every_site_silently_including_zero_action_sites(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {
        "N2688": [_ca() for _ in range(39)], "WRD": [],
    })
    assert caw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert {r[1] for r in rows} == {"ca:N2688", "ca:WRD"}
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()
    assert caw.run() == 0
    assert len(_rows(fake)) == 2
    assert sent == []


def test_new_action_emails_an_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()   # baseline
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(), _ca(num="VN-020000", type="Consent Judgment")]
                            if nsite_id == _N2688_ID else []))
    assert caw.run() == 0
    matches = [s for s in sent if "N2688" in s[0] or "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "+ ADDED" in matches[0][1]
    assert "VN-020000" in matches[0][1]
    assert matches[0][2] is None   # None -> send_email resolves full alert_recipients


def test_status_change_on_an_existing_action_emails_an_alert(monkeypatch):
    """The high-value case the handoff calls out: a CA closing. It renders as
    the same number REMOVED (Issued) + ADDED (Closed)."""
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca(status="Issued")], "WRD": []})
    caw.run()   # baseline
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(status="Closed")] if nsite_id == _N2688_ID else []))
    assert caw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "VN-019436" in matches[0][1]
    assert "status=Closed" in matches[0][1] and "status=Issued" in matches[0][1]


def test_first_action_at_a_zero_site_alerts_with_the_headline(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()   # baseline: WRD at zero
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca()] if nsite_id == _N2688_ID
                            else [_ca(num="VN-011821", program="WRD - NPDES")]))
    assert caw.run() == 0
    matches = [s for s in sent if "WRD" in s[0] or "Washtenaw" in s[0]]
    assert len(matches) == 1
    assert "FIRST COMPLIANCE ACTION(S) RECORDED" in matches[0][1]


def test_no_egle_derived_text_reaches_the_email_subject(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(), _ca(num="INJECTED\nSubject: evil",
                                       type="also injected\r\nX-Header: bad")]
                            if nsite_id == _N2688_ID else []))
    assert caw.run() == 0
    subjects = [s[0] for s in sent]
    assert subjects == [
        "[Compliance Actions watch] nSITE Compliance Actions — Arbor Hills Landfill (N2688) changed"]
    assert all("\n" not in s and "INJECTED" not in s for s in subjects)


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()   # baseline
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("blip"))
                            if nsite_id == _N2688_ID else []))
    assert caw.run() == 0   # not loud
    assert len(_rows(fake)) == 2   # unchanged, nothing new appended
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud_but_other_site_still_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {
        "N2688": nc.NsiteFetchError("bot wall on the runner"), "WRD": [_ca()],
    })
    assert caw.run() == 1
    assert {r[1] for r in _rows(fake)} == {"ca:WRD"}   # partial, not all-or-nothing
    assert sent == []


def test_a_sheet_write_failure_isolates_to_one_site_and_never_alerts(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": [_ca()]})
    real_append = sw.append_compliance_actions_watch_row

    def _boom(service, sheet_id, date, item_key, *a, **kw):
        if item_key == "ca:N2688":
            raise RuntimeError("cell exceeded 50000 characters")
        return real_append(service, sheet_id, date, item_key, *a, **kw)
    monkeypatch.setattr(caw.sw, "append_compliance_actions_watch_row", _boom)

    assert caw.run() == 1                                   # loud
    assert {r[1] for r in _rows(fake)} == {"ca:WRD"}        # the later site still ran
    assert sent == []                                      # no alert for the failed row


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(CA_CFG)
    cfg["nsite_compliance_actions"]["recipients"] = ["trisha@example.com"]
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_ca()], "WRD": []})
    caw.run()   # baseline
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(), _ca(num="X")] if nsite_id == _N2688_ID else []))
    assert caw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert matches[0][2] == ["trisha@example.com"]


def test_alert_email_failure_keeps_the_durable_row_AND_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()   # baseline
    monkeypatch.setattr(caw.ea, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP down")))
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(), _ca(num="X")] if nsite_id == _N2688_ID else []))
    assert caw.run() == 1
    n2688_rows = [r for r in _rows(fake) if r[1] == "ca:N2688"]
    assert len(n2688_rows) == 2 and n2688_rows[1][3] == "changed"


def test_known_undeliverable_alerting_defers_the_change_instead_of_consuming_it(monkeypatch):
    """send_email PRINTS AND RETURNS when SMTP is unconfigured, so nothing
    raises. If the run proceeded anyway it would write the row, ADVANCE the
    stored hash, and the next run would say 'unchanged' and never retry — the
    notification gone permanently even after the secret was fixed. Same
    principle as the tab-read abort."""
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()                                          # healthy baseline first
    before = [list(r) for r in _rows(fake)]
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(), _ca(num="X")] if nsite_id == _N2688_ID else []))
    assert caw.run() == 1                              # loud
    assert [list(r) for r in _rows(fake)] == before    # nothing consumed
    assert sent == []

    monkeypatch.setenv("SMTP_PASSWORD", "test-value")
    assert caw.run() == 0
    assert len([s for s in sent if "Arbor Hills Landfill" in s[0]]) == 1


def test_a_transient_tab_read_failure_aborts_before_any_write(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    caw.run()                                   # baseline both sites
    before = [list(r) for r in _rows(fake)]

    def _boom(service, sheet_id, item_keys):
        raise RuntimeError("HTTP 429 rate limited")
    monkeypatch.setattr(caw.sw, "last_compliance_actions_snapshots", _boom)
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(), _ca(num="VN-REAL-NEW")]
                            if nsite_id == _N2688_ID else []))

    assert caw.run() == 1
    assert [list(r) for r in _rows(fake)] == before   # nothing written at all
    assert sent == []

    # ...and once the read recovers, the change is still detected, not lost.
    monkeypatch.undo()
    fake2, sent2 = _wire(monkeypatch, CA_CFG, {
        "N2688": [_ca(), _ca(num="VN-REAL-NEW")], "WRD": []})
    fake2._values._tabs = fake._values._tabs
    assert caw.run() == 0
    assert len([s for s in sent2 if "Arbor Hills Landfill" in s[0]]) == 1


def test_a_cleared_snapshot_cell_does_not_masquerade_as_a_clean_site(monkeypatch):
    fake, sent = _wire(monkeypatch, CA_CFG, {"N2688": [_ca(), _ca(num="B")], "WRD": []})
    caw.run()   # baseline
    for r in fake._values._tabs[sw.TAB_COMPLIANCE_ACTIONS]:
        if len(r) > 7 and r[1] == "ca:N2688":
            r[7] = ""            # a human clears the big JSON cell
    monkeypatch.setattr(caw.nc, "fetch_site_compliance_actions",
                        lambda session, nsite_id: (
                            [_ca(), _ca(num="B"), _ca(num="C")]
                            if nsite_id == _N2688_ID else []))
    assert caw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "FIRST COMPLIANCE ACTION" not in matches[0][1]
    assert "missing or unreadable" in matches[0][1]


def test_an_unrecognized_cadence_polls_rather_than_silently_skipping(monkeypatch):
    cfg = copy.deepcopy(CA_CFG)
    cfg["nsite_compliance_actions"]["tiers"] = {"N2688": "dayly", "WRD": "daily"}
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_ca()], "WRD": []})
    assert caw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"ca:N2688", "ca:WRD"}


def test_last_compliance_actions_snapshots_batches_into_one_tab_read(monkeypatch):
    fake = FakeSheets()
    calls = []
    orig = fake.values
    monkeypatch.setattr(fake, "values", lambda: (calls.append(1) or orig()))
    sw.ensure_compliance_actions_tabs(fake, "SID")
    sw.append_compliance_actions_watch_row(fake, "SID", "2026-08-09", "ca:A", "A", "baseline",
                                           "hash1", "note", "now", "{}")
    sw.append_compliance_actions_watch_row(fake, "SID", "2026-08-09", "ca:B", "B", "baseline",
                                           "hash2", "note", "now", "{}")
    calls.clear()
    result = sw.last_compliance_actions_snapshots(fake, "SID", ["ca:A", "ca:B", "ca:C"])
    assert result["ca:A"] == ("hash1", "{}")
    assert result["ca:B"] == ("hash2", "{}")
    assert result["ca:C"] is None
    assert len(calls) == 1   # one values() call for all three keys, not three


def test_last_compliance_actions_snapshots_raises_rather_than_swallowing_a_read_error():
    class _Exploding:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId, range):
            raise RuntimeError("HTTP 503")
    with pytest.raises(RuntimeError):
        sw.last_compliance_actions_snapshots(_Exploding(), "SID", ["ca:A"])


def test_run_issues_exactly_one_tab_read_for_all_sites(monkeypatch):
    reads = []
    real = sw.last_compliance_actions_snapshots

    def _counting(service, sheet_id, item_keys):
        reads.append(list(item_keys))
        return real(service, sheet_id, item_keys)
    monkeypatch.setattr(caw.sw, "last_compliance_actions_snapshots", _counting)
    _wire(monkeypatch, CA_CFG, {"N2688": [_ca()], "WRD": []})
    assert caw.run() == 0
    assert len(reads) == 1
    assert sorted(reads[0]) == ["ca:N2688", "ca:WRD"]


def test_compliance_actions_tab_is_separate_state_from_violations():
    """Two watches, two tabs, two item-key namespaces — a ca:* row must never be
    read as a viol:* row or vice versa."""
    assert sw.TAB_COMPLIANCE_ACTIONS != sw.TAB_VIOLATIONS
    fake = FakeSheets()
    sw.ensure_compliance_actions_tabs(fake, "SID")
    sw.ensure_violations_tabs(fake, "SID")
    sw.append_compliance_actions_watch_row(fake, "SID", "2026-08-09", "ca:N2688", "M", "baseline",
                                           "chash", "n", "now", "{}")
    assert sw.last_violations_snapshots(fake, "SID", ["ca:N2688"])["ca:N2688"] is None
    assert sw.last_compliance_actions_snapshots(
        fake, "SID", ["ca:N2688"])["ca:N2688"] == ("chash", "{}")
