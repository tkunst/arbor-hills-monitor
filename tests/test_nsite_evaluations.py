"""nsite_client.fetch_site_evaluations + nsite_evaluations_watcher (Stream N,
ADR 029) — the activation gate, the fetch-error contract (a failure must never
look like "zero evaluations"), the pure ref-number-keyed snapshot/diff helpers
(and their budget-guarded digest degradation, which — unlike Violations/
Compliance Actions — actually fires at real N2688 volume), and the full
baseline/unchanged/new/changed/removed/fetch-fail flows driven through a fake
Sheets service (no network, no creds). Mirrors tests/test_nsite_compliance_
actions.py's structure; reuses FakeSheets from test_pfas_watcher.

Fixtures below are REAL records copied verbatim from the 2026-08-08 live spike
across the 5 known non-dormant sites, including the observation that
`evalEvalNum` IS a genuine unique key (477/477 at N2688) — the finding that
put this profile on the Submissions ref-keyed idiom rather than the rop/mmd/
ride/violations/compliance-actions multiset."""
import copy
import json

import pytest

import nsite_client as nc
import nsite_evaluations_watcher as ew
import nsite_submissions_watcher as sub_w
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# fetch_site_evaluations — the fetch-error contract
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


# Verbatim from the live N2688 profile, 2026-08-08/2026-08-21 — the most
# recent evaluation, a Records Review two weeks old at build time.
_RAW_EVAL = {
    "evalRefProgramAreaDescr": "AQD - Air",
    "evalRefEvalTypeDescr": "Records Review",
    "evalEvalNum": "E-AHZX-TW7C-8EN5",
    "evalPrmtNum": None,
    "evalRefEvalCatgDescr": "On-Site Inspection",
    "evalStartDate": "2026-08-07T00:00:00.0000000-04:00",
    "evalSmplTransmtDate": None,
    "siteName": "Arbor Hills Landfill, Inc. (N2688)",
}

# Verbatim from the live RA profile — a populated permit_num, an EST offset.
_RAW_EVAL_RA = {
    "evalRefProgramAreaDescr": "WRD - NPDES",
    "evalRefEvalTypeDescr": "Stormwater Industrial/Commercial Inspection",
    "evalEvalNum": "E-PP528996434548",
    "evalPrmtNum": "MIS210766",
    "evalRefEvalCatgDescr": "On-Site Inspection",
    "evalStartDate": "2024-10-17T00:00:00.0000000-04:00",
    "evalSmplTransmtDate": None,
    "siteName": "Arbor Hills Remediation Area",
}


def test_fetch_normalizes_a_real_shaped_record():
    session = _Session(_Resp({"queryResults": [_RAW_EVAL]}))
    rows = nc.fetch_site_evaluations(session, "8094300008956198244")
    assert rows == [{
        "eval_num": "E-AHZX-TW7C-8EN5",
        "program_area": "AQD - Air",
        "eval_type": "Records Review",
        "eval_category": "On-Site Inspection",
        "permit_num": "",
        "start_date": "2026-08-07",           # UTC offset dropped, date kept
        "sample_transmit_date": "",
        "site_name": "Arbor Hills Landfill, Inc. (N2688)",
    }]


def test_fetch_keeps_a_populated_permit_num():
    session = _Session(_Resp({"queryResults": [_RAW_EVAL_RA]}))
    rows = nc.fetch_site_evaluations(session, "X")
    assert rows[0]["permit_num"] == "MIS210766"
    assert rows[0]["start_date"] == "2024-10-17"   # EST offset dropped too


def test_fetch_evaluations_are_unique_by_eval_num_unlike_violations():
    """The finding that put this profile on the ref-keyed idiom: two DIFFERENT
    evaluations never share an eval_num (verified live 477/477 at N2688)."""
    session = _Session(_Resp({"queryResults": [_RAW_EVAL, _RAW_EVAL_RA]}))
    rows = nc.fetch_site_evaluations(session, "X")
    nums = [r["eval_num"] for r in rows]
    assert len(nums) == len(set(nums)) == 2


def test_fetch_drops_a_record_with_no_eval_num_rather_than_raising():
    """UNLIKE fetch_site_violations/fetch_site_compliance_actions (which keep
    every record because they have no key), this profile filters on
    evalEvalNum present — the same precedent fetch_site_submissions sets for
    submSubmRefNum — because a keyless record cannot be placed in a
    ref-number-keyed snapshot. No such record has been observed live."""
    keyless = dict(_RAW_EVAL, evalEvalNum=None)
    session = _Session(_Resp({"queryResults": [keyless, _RAW_EVAL_RA]}))
    rows = nc.fetch_site_evaluations(session, "X")
    assert len(rows) == 1
    assert rows[0]["eval_num"] == "E-PP528996434548"


def test_fetch_normalizes_a_null_permit_num_to_empty_string():
    """permit_num is null on 470/477 live N2688 records. `.get(f, "")` returns
    None for a present-but-null key, so `or ""` collapses both cases to ""."""
    session = _Session(_Resp({"queryResults": [_RAW_EVAL]}))
    rows = nc.fetch_site_evaluations(session, "X")
    assert rows[0]["permit_num"] == ""
    assert all(v is not None for v in rows[0].values())


def test_fetch_survives_an_out_of_range_start_date_instead_of_blinding_the_site():
    raw = dict(_RAW_EVAL, evalStartDate="2026-02-30T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_evaluations(session, "X")
    assert rows[0]["start_date"] == ""
    assert rows[0]["eval_num"] == "E-AHZX-TW7C-8EN5"
    assert session.calls == 1          # not retried — it was never an error


def test_fetch_survives_an_out_of_range_sample_transmit_date():
    raw = dict(_RAW_EVAL, evalSmplTransmtDate="2026-13-40T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_evaluations(session, "X")
    assert rows[0]["sample_transmit_date"] == ""


def test_fetch_accepts_the_us_style_date_format_too():
    raw = dict(_RAW_EVAL, evalStartDate="08/07/2026")
    session = _Session(_Resp({"queryResults": [raw]}))
    assert nc.fetch_site_evaluations(session, "X")[0]["start_date"] == "2026-08-07"


def test_fetch_normalizes_a_populated_sample_transmit_date():
    """Null in 525/525 live records, but the schema promises it and a future
    evaluation could populate it — the normalization must still work."""
    raw = dict(_RAW_EVAL, evalSmplTransmtDate="2026-08-15T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    assert nc.fetch_site_evaluations(session, "X")[0]["sample_transmit_date"] == "2026-08-15"


def test_fetch_raises_rather_than_diffing_a_partial_page():
    session = _Session(_Resp({"queryResults": [_RAW_EVAL], "hasResultsRemaining": True}))
    with pytest.raises(nc.NsiteStructuralError, match="paging"):
        nc.fetch_site_evaluations(session, "X")


def test_fetch_accepts_the_null_hasresultsremaining_the_api_actually_sends():
    session = _Session(_Resp({"queryResults": [_RAW_EVAL],
                              "hasResultsRemaining": None, "totalCount": None}))
    assert len(nc.fetch_site_evaluations(session, "X")) == 1


def test_fetch_empty_queryresults_is_a_valid_zero_result():
    """14 of the 19 watched sites have zero evaluations. A structurally-sound
    response listing none is NOT an error — it is the baseline."""
    session = _Session(_Resp({"queryResults": []}))
    assert nc.fetch_site_evaluations(session, "X") == []


def test_fetch_raises_never_returns_empty_on_http_error():
    """THE contract this feature depends on: an HTTP failure must raise, never
    silently degrade to []. Otherwise the watcher would misread a fetch outage
    as 'every evaluation withdrawn at once' and fire a false all-clear."""
    session = _Session(_Resp(status=500))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_evaluations(session, "X")


def test_fetch_raises_on_missing_queryresults_key():
    session = _Session(_Resp({"somethingElse": []}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_evaluations(session, "X")


def test_fetch_raises_on_non_json_body():
    session = _Session(_Resp(json_ok=False))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_evaluations(session, "X")


def test_fetch_raises_on_structurally_broken_record():
    session = _Session(_Resp({"queryResults": ["not-a-dict"]}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_evaluations(session, "X")


def test_fetch_retries_then_succeeds():
    session = _Session([_Resp(status=500), _Resp({"queryResults": [_RAW_EVAL]})])
    rows = nc.fetch_site_evaluations(session, "X")
    assert len(rows) == 1
    assert session.calls == 2


def test_fetch_hits_the_evaluations_endpoint_with_the_site_filter():
    """Pin the URL construction so a copy-paste of the wrong profile path (e.g.
    reusing the Violations or Compliance Actions endpoint) fails the suite
    instead of 404ing silently in production."""
    captured = {}

    class _CapturingSession:
        def get(self, url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _Resp({"queryResults": [_RAW_EVAL]})

    nc.fetch_site_evaluations(_CapturingSession(), "8094300008956198244")
    assert "/profiles/3-compliance/1-evaluations" in captured["url"]
    assert "/profiles/3-compliance/2-violations" not in captured["url"]        # not a sibling
    assert "/profiles/3-compliance/3-compliance-actions" not in captured["url"]  # not a sibling
    assert "responseContentType=application" in captured["url"]
    assert "8094300008956198244" in captured["url"]
    assert "detail/8094300008956198244" in captured["headers"].get("Referer", "")


def test_fetch_coerces_a_non_string_date_to_empty_instead_of_crashing():
    raw = dict(_RAW_EVAL, evalStartDate=1723075200000)   # epoch ms as an int
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_evaluations(session, "X")
    assert rows[0]["start_date"] == ""
    assert rows[0]["eval_num"] == "E-AHZX-TW7C-8EN5"
    assert session.calls == 1   # not retried — it was never an error


# ==============================================================================
# Pure snapshot / diff helpers
# ==============================================================================

FIELDS = nc.EVALUATION_FIELDS


def _ev(eval_num="E-00001", **kw):
    base = {
        "eval_num": eval_num, "program_area": "AQD - Air", "eval_type": "Records Review",
        "eval_category": "On-Site Inspection", "permit_num": "", "start_date": "2026-08-07",
        "sample_transmit_date": "", "site_name": "Arbor Hills Landfill, Inc. (N2688)",
    }
    base.update(kw)
    return base


def test_snapshot_hash_stable_across_row_order():
    a = ew.evaluations_snapshot([_ev(eval_num="B"), _ev(eval_num="A")], FIELDS)
    b = ew.evaluations_snapshot([_ev(eval_num="A"), _ev(eval_num="B")], FIELDS)
    assert ew.snapshot_hash(a) == ew.snapshot_hash(b)


def test_snapshot_hash_changes_when_any_field_changes():
    a = ew.evaluations_snapshot([_ev(sample_transmit_date="")], FIELDS)
    b = ew.evaluations_snapshot([_ev(sample_transmit_date="2026-08-20")], FIELDS)
    assert ew.snapshot_hash(a) != ew.snapshot_hash(b)


def test_snapshot_of_empty_list_is_a_valid_baseline():
    snap = ew.evaluations_snapshot([], FIELDS)
    assert snap["n"] == 0 and snap["rows"] == []
    assert ew.snapshot_hash(snap)   # hashes fine, does not raise


def test_snapshot_is_self_describing_about_its_field_order():
    snap = ew.evaluations_snapshot([_ev()], ("eval_num", "eval_type"))
    assert snap["fields"] == ["eval_num", "eval_type"]
    assert snap["rows"] == [["E-00001", "Records Review"]]


def test_snapshot_keeps_two_evaluations_distinct_even_with_identical_detail():
    """Unlike Violations/CA, duplicate DETAIL doesn't collapse two evaluations
    — they are keyed by eval_num, which is always distinct."""
    snap = ew.evaluations_snapshot([_ev(eval_num="A"), _ev(eval_num="B")], FIELDS)
    assert snap["n"] == 2 and len(snap["rows"]) == 2


def test_new_evaluation_at_a_zero_site_reads_with_detail():
    old = ew.evaluations_snapshot([], FIELDS)
    new = ew.evaluations_snapshot([_ev(eval_num="E-NEW", eval_type="Complaint Investigation")], FIELDS)
    note, body = ew.summarize_evaluations_change(old, new)
    assert "new evaluation recorded" in note
    assert "+ NEW EVALUATION  E-NEW" in body
    assert "Complaint Investigation" in body


def test_a_field_advancing_on_an_existing_eval_num_reads_as_changed():
    """The high-value secondary signal the handoff calls out: sample_transmit_
    date being filled in after the fact."""
    old = ew.evaluations_snapshot([_ev(sample_transmit_date="")], FIELDS)
    new = ew.evaluations_snapshot([_ev(sample_transmit_date="2026-08-20")], FIELDS)
    note, body = ew.summarize_evaluations_change(old, new)
    assert "existing evaluation changed" in note
    assert "~ CHANGED  E-00001" in body
    assert "sample_transmit_date: — -> 2026-08-20" in body


def test_an_evaluation_no_longer_listed_reads_as_removed():
    old = ew.evaluations_snapshot([_ev(eval_num="A"), _ev(eval_num="B")], FIELDS)
    new = ew.evaluations_snapshot([_ev(eval_num="A")], FIELDS)
    note, body = ew.summarize_evaluations_change(old, new)
    assert "evaluation no longer listed" in note
    assert "- REMOVED  B" in body


def test_new_and_changed_and_removed_can_all_appear_in_one_diff():
    old = ew.evaluations_snapshot(
        [_ev(eval_num="A", eval_type="Records Review"), _ev(eval_num="B")], FIELDS)
    new = ew.evaluations_snapshot(
        [_ev(eval_num="A", eval_type="Complaint Investigation"), _ev(eval_num="C")], FIELDS)
    note, body = ew.summarize_evaluations_change(old, new)
    assert "new evaluation recorded" in note
    assert "existing evaluation changed" in note
    assert "evaluation no longer listed" in note
    assert "+ NEW EVALUATION  C" in body
    assert "~ CHANGED  A" in body
    assert "- REMOVED  B" in body


def test_field_set_change_is_labelled_configuration_not_an_egle_change():
    old = ew.evaluations_snapshot([_ev()], FIELDS)
    new = ew.evaluations_snapshot([_ev()], tuple(f for f in FIELDS if f != "site_name"))
    note, body = ew.summarize_evaluations_change(old, new)
    assert "NOT an EGLE change" in note
    assert "configuration" in note or "exclude_fields" in body
    assert "REMOVED" not in body


def test_missing_previous_snapshot_re_baselines_without_claiming_the_site_was_clean():
    note, body = ew.summarize_evaluations_change({}, ew.evaluations_snapshot([_ev()], FIELDS))
    assert "missing or unreadable" in note
    assert "NOT mean the site previously had no evaluations" in note


def test_a_structurally_invalid_stored_snapshot_is_reported_not_crashed():
    bad = {"fields": list(FIELDS), "n": 1, "rows": [["not-enough-values"]]}
    note, body = ew.summarize_evaluations_change(bad, ew.evaluations_snapshot([_ev()], FIELDS))
    assert "structurally invalid" in note
    assert "re-baselines" in note


def test_no_ref_level_diff_when_snapshots_are_equal_but_hash_check_was_skipped():
    snap = ew.evaluations_snapshot([_ev()], FIELDS)
    note, body = ew.summarize_evaluations_change(snap, copy.deepcopy(snap))
    assert note == "changed (no ref-level diff — see snapshot)"


def test_alert_line_count_is_capped_and_says_what_it_dropped():
    many = [_ev(eval_num=f"E-{i:05d}") for i in range(ew.MAX_ALERT_LINES + 25)]
    note, body = ew.summarize_evaluations_change(
        ew.evaluations_snapshot([], FIELDS), ew.evaluations_snapshot(many, FIELDS))
    lines = body.splitlines()
    assert len(lines) == ew.MAX_ALERT_LINES + 1     # + the "and N more" line
    assert "and 25 more change line(s) not shown" in lines[-1]


def test_format_change_body_has_essentials_and_no_severity_judgment():
    body = ew.format_change_body("nSITE Evaluations — Arbor Hills Landfill (N2688)",
                                 "new evaluation recorded", "+ NEW EVALUATION  E-1")
    assert "Arbor Hills Landfill (N2688)" in body
    assert "new evaluation recorded" in body
    assert "E-1" in body
    assert "no severity judgment" in body or "no status field" in body


# ==============================================================================
# The Sheets cell-size guard — NOT theoretical insurance here (N2688 exceeds
# the default budget in the compact positional form on every real run)
# ==============================================================================


def test_cell_payload_passes_through_under_budget():
    snap = ew.evaluations_snapshot([_ev()], FIELDS)
    payload = ew._cell_payload(snap, budget=45000)
    assert json.loads(payload)["rows"] == snap["rows"]
    assert "truncated" not in payload


def test_the_real_n2688_scale_requires_the_digest_form_at_the_default_budget():
    """N2688's 477 records serialize to 75,494 chars even positionally — this
    is the expected, permanent steady state for N2688, not an edge case."""
    rows = [_ev(eval_num=f"E-{i:05d}") for i in range(477)]
    snap = ew.evaluations_snapshot(rows, FIELDS)
    full = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    assert len(full) > ew.DEFAULT_SNAPSHOT_CHAR_BUDGET      # the fixture really is oversized
    payload = ew._cell_payload(snap, budget=ew.DEFAULT_SNAPSHOT_CHAR_BUDGET)
    assert len(payload) <= ew.DEFAULT_SNAPSHOT_CHAR_BUDGET
    body = json.loads(payload)
    assert body["truncated"] is True
    assert body["n"] == 477
    assert len(body["digests"]) == 477
    assert not body.get("digests_dropped")


def test_the_degraded_form_still_names_the_new_eval_num_not_just_a_count():
    """The point of keeping [eval_num, digest] pairs instead of an anonymous
    digest multiset (Violations/CA's design): even fully truncated, a diff
    against the degraded form can still say WHICH evaluation is new."""
    old_rows = [_ev(eval_num=f"E-{i:05d}") for i in range(477)]
    old_payload = json.loads(ew._cell_payload(
        ew.evaluations_snapshot(old_rows, FIELDS), budget=ew.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    new_snap = ew.evaluations_snapshot(old_rows + [_ev(eval_num="E-BRANDNEW")], FIELDS)
    note, body = ew.summarize_evaluations_change(old_payload, new_snap)
    assert "1 new evaluation" in note
    assert "+ NEW EVALUATION  E-BRANDNEW" in body


def test_the_degraded_form_reports_a_changed_eval_num_without_field_detail():
    old_rows = [_ev(eval_num=f"E-{i:05d}") for i in range(477)]
    old_payload = json.loads(ew._cell_payload(
        ew.evaluations_snapshot(old_rows, FIELDS), budget=ew.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    changed_rows = list(old_rows)
    changed_rows[0] = _ev(eval_num="E-00000", sample_transmit_date="2026-08-20")
    new_snap = ew.evaluations_snapshot(changed_rows, FIELDS)
    note, body = ew.summarize_evaluations_change(old_payload, new_snap)
    assert "1 evaluation(s) with changed detail" in note
    assert "~ CHANGED  E-00000" in body
    assert "snapshot too large for a field-level diff" in body


def test_the_truncated_fallback_is_itself_bounded():
    huge = ew.evaluations_snapshot(
        [_ev(eval_num=f"E-{i:06d}", eval_type=f"type {i}") for i in range(4000)], FIELDS)
    payload = ew._cell_payload(huge, budget=ew.DEFAULT_SNAPSHOT_CHAR_BUDGET)
    assert len(payload) <= ew.DEFAULT_SNAPSHOT_CHAR_BUDGET
    assert len(payload) < 50000
    body = json.loads(payload)
    assert body["truncated"] is True and body["digests_dropped"] is True
    assert body["n"] == 4000


def test_a_digest_dropped_snapshot_reports_count_only_not_a_crash():
    huge = ew.evaluations_snapshot([_ev(eval_num=f"E-{i:06d}") for i in range(4000)], FIELDS)
    old = json.loads(ew._cell_payload(huge, budget=ew.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    new = ew.evaluations_snapshot([_ev(eval_num=f"E-{i:06d}") for i in range(4001)], FIELDS)
    note, _ = ew.summarize_evaluations_change(old, new)
    assert "4000 -> 4001" in note
    assert "no ref-level diff available" in note


def test_snapshot_hash_ignores_the_cell_budget_entirely():
    snap = ew.evaluations_snapshot([_ev(eval_num=f"E-{i:06d}") for i in range(600)], FIELDS)
    assert ew._cell_payload(snap, budget=10) != ew._cell_payload(snap, budget=999999)
    assert ew.snapshot_hash(snap) == ew.snapshot_hash(copy.deepcopy(snap))


def test_digest_map_looks_up_eval_num_by_field_name_not_position():
    """A hand-reordered stored cell (a human edits the Sheet) must still parse
    — _digest_map looks up eval_num's column index rather than assuming 0."""
    snap = {"fields": ["site_name", "eval_num"], "n": 1,
            "rows": [["Arbor Hills Landfill, Inc. (N2688)", "E-1"]]}
    dm = ew._digest_map(snap)
    assert dm is not None and "E-1" in dm


def test_a_json_scalar_in_the_snapshot_cell_does_not_wedge_the_site():
    for raw in ("0", "null", "true", "42", '"hello"', "[]", "not json at all"):
        assert ew._load_json(raw, {}) == {} or isinstance(ew._load_json(raw, {}), dict)
    note, _ = ew.summarize_evaluations_change(
        ew._load_json("0", {}), ew.evaluations_snapshot([_ev()], FIELDS))
    assert "missing or unreadable" in note


# ==============================================================================
# Config gate + cadence wiring
# ==============================================================================


def test_should_run_false_when_disabled():
    ok, reason = ew._should_run({"nsite_evaluations": {"enabled": False}})
    assert ok is False and "false" in reason.lower()


def test_should_run_false_when_key_absent():
    ok, _ = ew._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = ew._should_run({"nsite_evaluations": {"enabled": True}})
    assert ok is True and reason == ""


def test_is_due_is_imported_not_reimplemented():
    """The handoff is explicit: reuse nsite_submissions_watcher._is_due rather
    than copy-pasting it. Identity, not equivalence — a copy would drift."""
    assert ew._is_due is sub_w._is_due


def test_diff_fields_defaults_to_every_field():
    assert ew.diff_fields({}) == nc.EVALUATION_FIELDS


def test_diff_fields_honors_the_exclude_lever():
    fields = ew.diff_fields({"nsite_evaluations": {"exclude_fields": ["site_name"]}})
    assert "site_name" not in fields
    assert len(fields) == len(nc.EVALUATION_FIELDS) - 1


def test_diff_fields_never_excludes_eval_num_even_if_configured_to():
    """UNLIKE Violations/CA's exclude_fields (which can drop even the display
    headline), eval_num is the structural diff KEY — excluding it would break
    every snapshot/diff helper, not just degrade a line's readability."""
    fields = ew.diff_fields({"nsite_evaluations": {"exclude_fields": ["eval_num", "site_name"]}})
    assert "eval_num" in fields
    assert "site_name" not in fields


def test_alerting_is_configured_detects_each_way_delivery_can_be_impossible(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(var, "x")
    ok, reason = ew.alerting_is_configured({}, ["a@example.com"])
    assert ok is True and reason == ""

    monkeypatch.delenv("SMTP_PASSWORD")
    ok, reason = ew.alerting_is_configured({}, ["a@example.com"])
    assert ok is False and "SMTP_PASSWORD" in reason

    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(ew.ea, "resolve_recipients", lambda cfg: [])
    ok, reason = ew.alerting_is_configured({}, None)
    assert ok is False and "recipients" in reason


def test_a_structural_break_fails_loudly_instead_of_going_quiet(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev()], "WRD": []})
    ew.run()                                          # baseline both
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteStructuralError("hasResultsRemaining")))
    assert ew.run() == 1
    assert sent == []
    # And the plain transient case, on the same baselined state, stays quiet:
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteFetchError("connection reset")))
    assert ew.run() == 0


def test_structural_error_is_a_subclass_so_existing_handlers_still_catch_it():
    assert issubclass(nc.NsiteStructuralError, nc.NsiteFetchError)


def test_snapshot_char_budget_is_clamped_below_the_hard_sheets_cap(monkeypatch):
    seen = {}
    real = ew._diff_and_record

    def _capture(*a, **kw):
        seen["budget"] = a[9] if len(a) > 9 else kw.get("budget")
        return real(*a, **kw)
    cfg = copy.deepcopy(EVAL_CFG)
    cfg["nsite_evaluations"]["snapshot_char_budget"] = 60000
    _wire(monkeypatch, cfg, {"N2688": [_ev()], "WRD": []})
    monkeypatch.setattr(ew, "_diff_and_record", _capture)
    assert ew.run() == 0
    assert seen["budget"] < ew.HARD_SHEETS_CELL_LIMIT


def test_the_workflow_is_scheduled_directly_not_parked():
    """This build's SSH key authenticated non-interactively, so — unlike
    Violations/CA — the workflow was landed straight into .github/workflows/
    rather than parked. Still tolerant of a future re-park (e.g. a rotated,
    passphrase-locked key) so this test doesn't need to change if that
    changes, matching the enforcing pattern test_nsite_compliance_actions.py
    established."""
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "config.yml") as f:
        cfg = yaml.safe_load(f)
    scheduled = (root / ".github" / "workflows" / "nsite-evaluations-watch.yml").exists()
    parked = (root / "docs" / "pending-workflows" / "nsite-evaluations-watch.yml").exists()
    assert scheduled or parked, "the workflow file has gone missing entirely"
    assert cfg["nsite_evaluations"]["enabled"] is False or scheduled, (
        "nsite_evaluations.enabled is true but the workflow is still parked "
        "at docs/pending-workflows/ — the watch would never be scheduled. "
        "git mv docs/pending-workflows/nsite-evaluations-watch.yml .github/workflows/"
    )


def test_shipped_config_tiers_cover_every_registry_site_and_differ_from_siblings():
    """The tiers map must stay in sync with nsite_sites (a srn in one and not
    the other is either an unwatched site or a loud KeyError at runtime) and —
    the handoff's explicit instruction — NOT be a copy of any sibling
    profile's tiers."""
    import pathlib

    import yaml
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    tiers = cfg["nsite_evaluations"]["tiers"]
    registry = {s["srn"] for s in cfg["nsite_sites"]}
    assert set(tiers) == registry
    assert set(tiers.values()) <= {"daily", "biweekly", "quarterly"}
    assert tiers != cfg["nsite_violations"]["tiers"]
    assert tiers != cfg["nsite_compliance_actions"]["tiers"]
    assert tiers != cfg["nsite_submissions"]["tiers"]


def test_shipped_config_ships_disabled():
    """New source, unattended overnight build — enabled: false is Trisha's
    activation step, not this build's."""
    import pathlib

    import yaml
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["nsite_evaluations"]["enabled"] is False


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

SITES = [
    {"srn": "N2688", "name": "Arbor Hills Landfill", "id": "8094300008956198244"},
    {"srn": "WRD", "name": "GFL-Arbor Hills Landfill-Washtenaw Co", "id": "306291952280313698"},
]

EVAL_CFG = {
    "nsite_sites": SITES,
    "nsite_evaluations": {"enabled": True, "tiers": {"N2688": "daily", "WRD": "daily"}},
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
    for _var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(_var, "test-value")
    monkeypatch.setattr(ew, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(ew.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(ew.nc, "make_session", lambda: object())
    monkeypatch.setattr(ew.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(session, nsite_id):
        srn = next(s["srn"] for s in sites if s["id"] == nsite_id)
        result = fetch_by_srn(srn) if callable(fetch_by_srn) else fetch_by_srn[srn]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_EVALUATIONS, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    """The shipped state: enabled is false, so the scheduled job must not even
    build a Sheets client."""
    monkeypatch.setattr(ew, "load_config",
                        lambda: {"nsite_evaluations": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(ew.dc, "sheets_service", boom)
    assert ew.run() == 0


def test_tiers_srn_missing_from_registry_raises_keyerror(monkeypatch):
    cfg = {
        "nsite_sites": [{"srn": "N2688", "name": "AH", "id": _N2688_ID}],
        "nsite_evaluations": {"enabled": True, "tiers": {"TYPO_SRN": "daily"}},
    }
    monkeypatch.setattr(ew, "load_config", lambda: cfg)
    with pytest.raises(KeyError, match="TYPO_SRN"):
        ew.run()


def test_run_skips_a_site_that_is_not_due_today_no_fetch_no_row(monkeypatch):
    sites = [
        {"srn": "N2688", "name": "Arbor Hills Landfill", "id": _N2688_ID},
        {"srn": "COMP", "name": "Arbor Hills Composting Faciltiy", "id": "-2164784335333909072"},
    ]
    cfg = {
        "nsite_sites": sites,
        "nsite_evaluations": {"enabled": True,
                              "tiers": {"N2688": "daily", "COMP": "quarterly"}},
        "alert_recipients": ["a@example.com"],
    }
    fake, sent = _wire(monkeypatch, cfg, {
        "N2688": [_ev()],
        "COMP": AssertionError("a not-due site must never be fetched"),
    })
    monkeypatch.setattr(ew, "_is_due", lambda cadence, srn, today: srn != "COMP")
    assert ew.run() == 0
    assert {r[1] for r in _rows(fake)} == {"eval:N2688"}
    assert sent == []


def test_first_run_baselines_every_site_silently_including_zero_eval_sites(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {
        "N2688": [_ev() for _ in range(3)], "WRD": [],
    })
    assert ew.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert {r[1] for r in rows} == {"eval:N2688", "eval:WRD"}
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev()], "WRD": []})
    ew.run()
    assert ew.run() == 0
    assert len(_rows(fake)) == 2
    assert sent == []


def test_new_evaluation_emails_an_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev(eval_num="E-1")], "WRD": []})
    ew.run()   # baseline
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1"), _ev(eval_num="E-2", eval_type="Complaint Investigation")]
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 0
    matches = [s for s in sent if "N2688" in s[0] or "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "+ NEW EVALUATION" in matches[0][1]
    assert "E-2" in matches[0][1]
    assert matches[0][2] is None   # None -> send_email resolves full alert_recipients


def test_detail_change_on_an_existing_evaluation_emails_an_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG,
                       {"N2688": [_ev(eval_num="E-1", sample_transmit_date="")], "WRD": []})
    ew.run()   # baseline
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1", sample_transmit_date="2026-08-20")]
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "E-1" in matches[0][1]
    assert "sample_transmit_date: — -> 2026-08-20" in matches[0][1]


def test_first_evaluation_at_a_zero_site_alerts_as_new(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev()], "WRD": []})
    ew.run()   # baseline: WRD at zero
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev()] if nsite_id == _N2688_ID
                            else [_ev(eval_num="E-WRD-1", program_area="WRD - Resources")]))
    assert ew.run() == 0
    matches = [s for s in sent if "WRD" in s[0] or "Washtenaw" in s[0]]
    assert len(matches) == 1
    assert "new evaluation recorded" in matches[0][1]
    assert "E-WRD-1" in matches[0][1]


def test_no_egle_derived_text_reaches_the_email_subject(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev(eval_num="E-1")], "WRD": []})
    ew.run()
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1"),
                             _ev(eval_num="INJECTED\nSubject: evil",
                                 eval_type="also injected\r\nX-Header: bad")]
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 0
    subjects = [s[0] for s in sent]
    assert subjects == [
        "[Evaluations watch] nSITE Evaluations — Arbor Hills Landfill (N2688) changed"]
    assert all("\n" not in s and "INJECTED" not in s for s in subjects)


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev()], "WRD": []})
    ew.run()   # baseline
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("blip"))
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 0   # not loud
    assert len(_rows(fake)) == 2   # unchanged, nothing new appended
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud_but_other_site_still_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {
        "N2688": nc.NsiteFetchError("bot wall on the runner"), "WRD": [_ev()],
    })
    assert ew.run() == 1
    assert {r[1] for r in _rows(fake)} == {"eval:WRD"}   # partial, not all-or-nothing
    assert sent == []


def test_a_sheet_write_failure_isolates_to_one_site_and_never_alerts(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev()], "WRD": [_ev()]})
    real_append = sw.append_evaluations_watch_row

    def _boom(service, sheet_id, date, item_key, *a, **kw):
        if item_key == "eval:N2688":
            raise RuntimeError("cell exceeded 50000 characters")
        return real_append(service, sheet_id, date, item_key, *a, **kw)
    monkeypatch.setattr(ew.sw, "append_evaluations_watch_row", _boom)

    assert ew.run() == 1                                   # loud
    assert {r[1] for r in _rows(fake)} == {"eval:WRD"}     # the later site still ran
    assert sent == []                                      # no alert for the failed row


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(EVAL_CFG)
    cfg["nsite_evaluations"]["recipients"] = ["trisha@example.com"]
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_ev(eval_num="E-1")], "WRD": []})
    ew.run()   # baseline
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1"), _ev(eval_num="E-2")]
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert matches[0][2] == ["trisha@example.com"]


def test_alert_email_failure_keeps_the_durable_row_AND_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev(eval_num="E-1")], "WRD": []})
    ew.run()   # baseline
    monkeypatch.setattr(ew.ea, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP down")))
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1"), _ev(eval_num="E-2")]
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 1
    n2688_rows = [r for r in _rows(fake) if r[1] == "eval:N2688"]
    assert len(n2688_rows) == 2 and n2688_rows[1][3] == "changed"


def test_known_undeliverable_alerting_defers_the_change_instead_of_consuming_it(monkeypatch):
    """send_email PRINTS AND RETURNS when SMTP is unconfigured, so nothing
    raises. If the run proceeded anyway it would write the row, ADVANCE the
    stored hash, and the next run would say 'unchanged' and never retry — the
    notification gone permanently even after the secret was fixed."""
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev(eval_num="E-1")], "WRD": []})
    ew.run()                                          # healthy baseline first
    before = [list(r) for r in _rows(fake)]
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1"), _ev(eval_num="E-2")]
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 1                              # loud
    assert [list(r) for r in _rows(fake)] == before    # nothing consumed
    assert sent == []

    monkeypatch.setenv("SMTP_PASSWORD", "test-value")
    assert ew.run() == 0
    assert len([s for s in sent if "Arbor Hills Landfill" in s[0]]) == 1


def test_a_transient_tab_read_failure_aborts_before_any_write(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev(eval_num="E-1")], "WRD": []})
    ew.run()                                   # baseline both sites
    before = [list(r) for r in _rows(fake)]

    def _boom(service, sheet_id, item_keys):
        raise RuntimeError("HTTP 429 rate limited")
    monkeypatch.setattr(ew.sw, "last_evaluations_snapshots", _boom)
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1"), _ev(eval_num="E-REAL-NEW")]
                            if nsite_id == _N2688_ID else []))

    assert ew.run() == 1
    assert [list(r) for r in _rows(fake)] == before   # nothing written at all
    assert sent == []

    # ...and once the read recovers, the change is still detected, not lost.
    monkeypatch.undo()
    fake2, sent2 = _wire(monkeypatch, EVAL_CFG, {
        "N2688": [_ev(eval_num="E-1"), _ev(eval_num="E-REAL-NEW")], "WRD": []})
    fake2._values._tabs = fake._values._tabs
    assert ew.run() == 0
    assert len([s for s in sent2 if "Arbor Hills Landfill" in s[0]]) == 1


def test_a_cleared_snapshot_cell_does_not_masquerade_as_a_clean_site(monkeypatch):
    fake, sent = _wire(monkeypatch, EVAL_CFG,
                       {"N2688": [_ev(eval_num="E-1"), _ev(eval_num="E-2")], "WRD": []})
    ew.run()   # baseline
    for r in fake._values._tabs[sw.TAB_EVALUATIONS]:
        if len(r) > 7 and r[1] == "eval:N2688":
            r[7] = ""            # a human clears the big JSON cell
    monkeypatch.setattr(ew.nc, "fetch_site_evaluations",
                        lambda session, nsite_id: (
                            [_ev(eval_num="E-1"), _ev(eval_num="E-2"), _ev(eval_num="E-3")]
                            if nsite_id == _N2688_ID else []))
    assert ew.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "new evaluation recorded" not in matches[0][1]
    assert "missing or unreadable" in matches[0][1]


def test_an_unrecognized_cadence_polls_rather_than_silently_skipping(monkeypatch):
    cfg = copy.deepcopy(EVAL_CFG)
    cfg["nsite_evaluations"]["tiers"] = {"N2688": "dayly", "WRD": "daily"}
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_ev()], "WRD": []})
    assert ew.run() == 0
    assert {r[1] for r in _rows(fake)} == {"eval:N2688", "eval:WRD"}


def test_last_evaluations_snapshots_batches_into_one_tab_read(monkeypatch):
    fake = FakeSheets()
    calls = []
    orig = fake.values
    monkeypatch.setattr(fake, "values", lambda: (calls.append(1) or orig()))
    sw.ensure_evaluations_tabs(fake, "SID")
    sw.append_evaluations_watch_row(fake, "SID", "2026-08-21", "eval:A", "A", "baseline",
                                    "hash1", "note", "now", "{}")
    sw.append_evaluations_watch_row(fake, "SID", "2026-08-21", "eval:B", "B", "baseline",
                                    "hash2", "note", "now", "{}")
    calls.clear()
    result = sw.last_evaluations_snapshots(fake, "SID", ["eval:A", "eval:B", "eval:C"])
    assert result["eval:A"] == ("hash1", "{}")
    assert result["eval:B"] == ("hash2", "{}")
    assert result["eval:C"] is None
    assert len(calls) == 1   # one values() call for all three keys, not three


def test_last_evaluations_snapshots_raises_rather_than_swallowing_a_read_error():
    class _Exploding:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId, range):
            raise RuntimeError("HTTP 503")
    with pytest.raises(RuntimeError):
        sw.last_evaluations_snapshots(_Exploding(), "SID", ["eval:A"])


def test_run_issues_exactly_one_tab_read_for_all_sites(monkeypatch):
    reads = []
    real = sw.last_evaluations_snapshots

    def _counting(service, sheet_id, item_keys):
        reads.append(list(item_keys))
        return real(service, sheet_id, item_keys)
    monkeypatch.setattr(ew.sw, "last_evaluations_snapshots", _counting)
    _wire(monkeypatch, EVAL_CFG, {"N2688": [_ev()], "WRD": []})
    assert ew.run() == 0
    assert len(reads) == 1
    assert sorted(reads[0]) == ["eval:N2688", "eval:WRD"]


def test_evaluations_tab_is_separate_state_from_its_sibling_watches():
    """Four watches, four tabs, four item-key namespaces — an eval:* row must
    never be read as a viol:*/ca:*/subm:* row or vice versa."""
    assert sw.TAB_EVALUATIONS not in (sw.TAB_VIOLATIONS, sw.TAB_COMPLIANCE_ACTIONS, sw.TAB_SUBMISSIONS)
    fake = FakeSheets()
    sw.ensure_evaluations_tabs(fake, "SID")
    sw.ensure_violations_tabs(fake, "SID")
    sw.append_evaluations_watch_row(fake, "SID", "2026-08-21", "eval:N2688", "M", "baseline",
                                    "ehash", "n", "now", "{}")
    assert sw.last_violations_snapshots(fake, "SID", ["eval:N2688"])["eval:N2688"] is None
    assert sw.last_evaluations_snapshots(
        fake, "SID", ["eval:N2688"])["eval:N2688"] == ("ehash", "{}")
