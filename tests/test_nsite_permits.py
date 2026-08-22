"""nsite_client.fetch_site_permits + nsite_permits_watcher (Stream O, ADR 030)
— the activation gate, the fetch-error contract (a failure must never look
like "zero permits"), the pure ref-number-keyed snapshot/diff helpers (and
their budget-guarded digest degradation, verified-inert at real Permits
volumes), the STATUS/DATE-change-on-an-existing-permit signal the handoff
requires explicit coverage for (Extended -> Expired; a termination date
populating), the ROP-overlap disambiguation in the alert copy, and the full
baseline/unchanged/new/changed/removed/fetch-fail flows driven through a fake
Sheets service (no network, no creds). Mirrors tests/test_nsite_evaluations.py's
structure; reuses FakeSheets from test_pfas_watcher.

Fixtures below are REAL records copied verbatim from the 2026-08-22 live spike
across the 7 sites that carry any permits, including the observation that
`prmtPrmtNum` IS a genuine unique key (9/9 at N2688) — the finding that put
this profile on the Submissions/Evaluations ref-keyed idiom rather than the
rop/mmd/ride/violations/compliance-actions multiset."""
import copy
import json

import pytest

import nsite_client as nc
import nsite_permits_watcher as pw
import nsite_submissions_watcher as sub_w
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# fetch_site_permits — the fetch-error contract
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


# Verbatim from the live N2688 profile, 2026-08-22 — the Air ROP this
# profile shares with Stream H's targeted ROP watch.
_RAW_PERMIT = {
    "prmtPrmtNum": "ROP0000224",
    "prmtRefPrmtCatgDescr": "Air Renewable Operating Permit",
    "prmtPrmtTypeDescr": None,
    "prmtRefPrmtStatDescr": "Extended",
    "prmtEfctvDate": "2018-03-28T00:00:00.0000000-04:00",
    "prmtExprDate": "2016-01-24T00:00:00.0000000-05:00",
    "prmtTermDate": None,
}

# Verbatim from the live RA profile — a populated permit_type, an EST offset.
_RAW_PERMIT_RA = {
    "prmtPrmtNum": "MIS210766",
    "prmtRefPrmtCatgDescr": "NPDES Certificate of Coverage under General Permit (COC)",
    "prmtPrmtTypeDescr": "SW-Industrial CY2",
    "prmtRefPrmtStatDescr": "In Effect",
    "prmtEfctvDate": "2023-06-20T00:00:00.0000000-04:00",
    "prmtExprDate": "2027-04-01T00:00:00.0000000-04:00",
    "prmtTermDate": None,
}

# Verbatim from the live N2688 profile — an already-Terminated permit, proof
# a termination date really does populate in this profile.
_RAW_PERMIT_TERMINATED = {
    "prmtPrmtNum": "19-17B",
    "prmtRefPrmtCatgDescr": "Air Permit to Install",
    "prmtPrmtTypeDescr": None,
    "prmtRefPrmtStatDescr": "Terminated",
    "prmtEfctvDate": "2018-12-26T00:00:00.0000000-05:00",
    "prmtExprDate": None,
    "prmtTermDate": "2019-03-12T00:00:00.0000000-04:00",
}


def test_fetch_normalizes_a_real_shaped_record():
    session = _Session(_Resp({"queryResults": [_RAW_PERMIT]}))
    rows = nc.fetch_site_permits(session, "8094300008956198244")
    assert rows == [{
        "prmt_num": "ROP0000224",
        "status": "Extended",
        "category": "Air Renewable Operating Permit",
        "permit_type": "",
        "effective_date": "2018-03-28",   # UTC offset dropped, date kept
        "expiration_date": "2016-01-24",
        "termination_date": "",
    }]


def test_fetch_keeps_a_populated_permit_type():
    session = _Session(_Resp({"queryResults": [_RAW_PERMIT_RA]}))
    rows = nc.fetch_site_permits(session, "X")
    assert rows[0]["permit_type"] == "SW-Industrial CY2"
    assert rows[0]["effective_date"] == "2023-06-20"   # EDT offset dropped too


def test_fetch_normalizes_a_populated_termination_date():
    """The handoff's headline signal: a null->populated termination date."""
    session = _Session(_Resp({"queryResults": [_RAW_PERMIT_TERMINATED]}))
    rows = nc.fetch_site_permits(session, "X")
    assert rows[0]["status"] == "Terminated"
    assert rows[0]["termination_date"] == "2019-03-12"


def test_fetch_permits_are_unique_by_prmt_num_unlike_violations():
    """The finding that put this profile on the ref-keyed idiom: two DIFFERENT
    permits never share a prmt_num (verified live 9/9 at N2688)."""
    session = _Session(_Resp({"queryResults": [_RAW_PERMIT, _RAW_PERMIT_RA]}))
    rows = nc.fetch_site_permits(session, "X")
    nums = [r["prmt_num"] for r in rows]
    assert len(nums) == len(set(nums)) == 2


def test_fetch_drops_a_record_with_no_prmt_num_rather_than_raising():
    """UNLIKE fetch_site_violations/fetch_site_compliance_actions (which keep
    every record because they have no key), this profile filters on
    prmtPrmtNum present — the same precedent fetch_site_evaluations sets for
    evalEvalNum — because a keyless record cannot be placed in a
    ref-number-keyed snapshot. No such record has been observed live."""
    keyless = dict(_RAW_PERMIT, prmtPrmtNum=None)
    session = _Session(_Resp({"queryResults": [keyless, _RAW_PERMIT_RA]}))
    rows = nc.fetch_site_permits(session, "X")
    assert len(rows) == 1
    assert rows[0]["prmt_num"] == "MIS210766"


def test_fetch_normalizes_a_null_permit_type_to_empty_string():
    """permit_type is null on 18/22 live records. `.get(f, "")` returns None
    for a present-but-null key, so `or ""` collapses both cases to ""."""
    session = _Session(_Resp({"queryResults": [_RAW_PERMIT]}))
    rows = nc.fetch_site_permits(session, "X")
    assert rows[0]["permit_type"] == ""
    assert all(v is not None for v in rows[0].values())


def test_fetch_survives_an_out_of_range_effective_date_instead_of_blinding_the_site():
    raw = dict(_RAW_PERMIT, prmtEfctvDate="2026-02-30T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_permits(session, "X")
    assert rows[0]["effective_date"] == ""
    assert rows[0]["prmt_num"] == "ROP0000224"
    assert session.calls == 1          # not retried — it was never an error


def test_fetch_survives_an_out_of_range_termination_date():
    raw = dict(_RAW_PERMIT, prmtTermDate="2026-13-40T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_permits(session, "X")
    assert rows[0]["termination_date"] == ""


def test_fetch_accepts_the_us_style_date_format_too():
    raw = dict(_RAW_PERMIT, prmtEfctvDate="03/28/2018")
    session = _Session(_Resp({"queryResults": [raw]}))
    assert nc.fetch_site_permits(session, "X")[0]["effective_date"] == "2018-03-28"


def test_fetch_raises_rather_than_diffing_a_partial_page():
    session = _Session(_Resp({"queryResults": [_RAW_PERMIT], "hasResultsRemaining": True}))
    with pytest.raises(nc.NsiteStructuralError, match="paging"):
        nc.fetch_site_permits(session, "X")


def test_fetch_accepts_the_null_hasresultsremaining_the_api_actually_sends():
    session = _Session(_Resp({"queryResults": [_RAW_PERMIT],
                              "hasResultsRemaining": None, "totalCount": None}))
    assert len(nc.fetch_site_permits(session, "X")) == 1


def test_fetch_empty_queryresults_is_a_valid_zero_result():
    """12 of the 19 watched sites have zero permits. A structurally-sound
    response listing none is NOT an error — it is the baseline."""
    session = _Session(_Resp({"queryResults": []}))
    assert nc.fetch_site_permits(session, "X") == []


def test_fetch_raises_never_returns_empty_on_http_error():
    """THE contract this feature depends on: an HTTP failure must raise, never
    silently degrade to []. Otherwise the watcher would misread a fetch outage
    as 'every permit withdrawn at once' and fire a false all-clear."""
    session = _Session(_Resp(status=500))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_permits(session, "X")


def test_fetch_raises_on_missing_queryresults_key():
    session = _Session(_Resp({"somethingElse": []}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_permits(session, "X")


def test_fetch_raises_on_non_json_body():
    session = _Session(_Resp(json_ok=False))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_permits(session, "X")


def test_fetch_raises_on_structurally_broken_record():
    session = _Session(_Resp({"queryResults": ["not-a-dict"]}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_permits(session, "X")


def test_fetch_retries_then_succeeds():
    session = _Session([_Resp(status=500), _Resp({"queryResults": [_RAW_PERMIT]})])
    rows = nc.fetch_site_permits(session, "X")
    assert len(rows) == 1
    assert session.calls == 2


def test_fetch_hits_the_permits_endpoint_with_the_site_filter():
    """Pin the URL construction so a copy-paste of the wrong profile path (e.g.
    reusing the Submissions endpoint, its own sibling under the same
    2-environmental-interests namespace) fails the suite instead of 404ing
    silently in production."""
    captured = {}

    class _CapturingSession:
        def get(self, url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _Resp({"queryResults": [_RAW_PERMIT]})

    nc.fetch_site_permits(_CapturingSession(), "8094300008956198244")
    assert "/profiles/2-environmental-interests/1-permits" in captured["url"]
    assert "/profiles/2-environmental-interests/2-submissions" not in captured["url"]  # not a sibling
    assert "responseContentType=application" in captured["url"]
    assert "8094300008956198244" in captured["url"]
    assert "detail/8094300008956198244" in captured["headers"].get("Referer", "")


def test_fetch_coerces_a_non_string_date_to_empty_instead_of_crashing():
    raw = dict(_RAW_PERMIT, prmtEfctvDate=1521864000000)   # epoch ms as an int
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_permits(session, "X")
    assert rows[0]["effective_date"] == ""
    assert rows[0]["prmt_num"] == "ROP0000224"
    assert session.calls == 1   # not retried — it was never an error


# ==============================================================================
# Pure snapshot / diff helpers
# ==============================================================================

FIELDS = nc.PERMIT_FIELDS


def _pm(prmt_num="ROP0000001", **kw):
    base = {
        "prmt_num": prmt_num, "status": "Extended",
        "category": "Air Renewable Operating Permit", "permit_type": "",
        "effective_date": "2018-03-28", "expiration_date": "2016-01-24",
        "termination_date": "",
    }
    base.update(kw)
    return base


def test_snapshot_hash_stable_across_row_order():
    a = pw.permits_snapshot([_pm(prmt_num="B"), _pm(prmt_num="A")], FIELDS)
    b = pw.permits_snapshot([_pm(prmt_num="A"), _pm(prmt_num="B")], FIELDS)
    assert pw.snapshot_hash(a) == pw.snapshot_hash(b)


def test_snapshot_hash_changes_when_any_field_changes():
    a = pw.permits_snapshot([_pm(status="Extended")], FIELDS)
    b = pw.permits_snapshot([_pm(status="Expired")], FIELDS)
    assert pw.snapshot_hash(a) != pw.snapshot_hash(b)


def test_snapshot_of_empty_list_is_a_valid_baseline():
    snap = pw.permits_snapshot([], FIELDS)
    assert snap["n"] == 0 and snap["rows"] == []
    assert pw.snapshot_hash(snap)   # hashes fine, does not raise


def test_snapshot_is_self_describing_about_its_field_order():
    snap = pw.permits_snapshot([_pm()], ("prmt_num", "status"))
    assert snap["fields"] == ["prmt_num", "status"]
    assert snap["rows"] == [["ROP0000001", "Extended"]]


def test_snapshot_keeps_two_permits_distinct_even_with_identical_detail():
    """Unlike Violations/CA, duplicate DETAIL doesn't collapse two permits —
    they are keyed by prmt_num, which is always distinct."""
    snap = pw.permits_snapshot([_pm(prmt_num="A"), _pm(prmt_num="B")], FIELDS)
    assert snap["n"] == 2 and len(snap["rows"]) == 2


def test_new_permit_at_a_zero_site_reads_with_detail():
    old = pw.permits_snapshot([], FIELDS)
    new = pw.permits_snapshot([_pm(prmt_num="NEW-1", status="In Effect")], FIELDS)
    note, body = pw.summarize_permits_change(old, new)
    assert "new permit recorded" in note
    assert "+ NEW PERMIT  NEW-1" in body
    assert "status=In Effect" in body


def test_a_status_flip_on_an_existing_permit_reads_as_changed():
    """The handoff's explicit, named test requirement: Extended -> Expired on
    an existing prmt_num, the primary signal this whole profile exists for."""
    old = pw.permits_snapshot([_pm(status="Extended")], FIELDS)
    new = pw.permits_snapshot([_pm(status="Expired")], FIELDS)
    note, body = pw.summarize_permits_change(old, new)
    assert "existing permit changed" in note
    assert "~ CHANGED  ROP0000001" in body
    assert "status: Extended -> Expired" in body


def test_a_termination_date_populating_on_an_existing_permit_reads_as_changed():
    """The handoff's second named test requirement: a null termination_date
    populating (the "this permit is now terminated" signal), not just a bare
    string-inequality check on an already-present value."""
    old = pw.permits_snapshot([_pm(status="In Effect", termination_date="")], FIELDS)
    new = pw.permits_snapshot(
        [_pm(status="Terminated", termination_date="2026-08-20")], FIELDS)
    note, body = pw.summarize_permits_change(old, new)
    assert "existing permit changed" in note
    assert "~ CHANGED  ROP0000001" in body
    assert "status: In Effect -> Terminated" in body
    assert "termination_date: — -> 2026-08-20" in body


def test_a_permit_no_longer_listed_reads_as_removed():
    old = pw.permits_snapshot([_pm(prmt_num="A"), _pm(prmt_num="B")], FIELDS)
    new = pw.permits_snapshot([_pm(prmt_num="A")], FIELDS)
    note, body = pw.summarize_permits_change(old, new)
    assert "permit no longer listed" in note
    assert "- REMOVED  B" in body


def test_new_and_changed_and_removed_can_all_appear_in_one_diff():
    old = pw.permits_snapshot(
        [_pm(prmt_num="A", status="Extended"), _pm(prmt_num="B")], FIELDS)
    new = pw.permits_snapshot(
        [_pm(prmt_num="A", status="Expired"), _pm(prmt_num="C")], FIELDS)
    note, body = pw.summarize_permits_change(old, new)
    assert "new permit recorded" in note
    assert "existing permit changed" in note
    assert "permit no longer listed" in note
    assert "+ NEW PERMIT  C" in body
    assert "~ CHANGED  A" in body
    assert "- REMOVED  B" in body


def test_field_set_change_is_labelled_configuration_not_an_egle_change():
    old = pw.permits_snapshot([_pm()], FIELDS)
    new = pw.permits_snapshot([_pm()], tuple(f for f in FIELDS if f != "permit_type"))
    note, body = pw.summarize_permits_change(old, new)
    assert "NOT an EGLE change" in note
    assert "configuration" in note or "exclude_fields" in body
    assert "REMOVED" not in body


def test_missing_previous_snapshot_re_baselines_without_claiming_the_site_was_clean():
    note, body = pw.summarize_permits_change({}, pw.permits_snapshot([_pm()], FIELDS))
    assert "missing or unreadable" in note
    assert "NOT mean the site previously had no permits" in note


def test_a_structurally_invalid_stored_snapshot_is_reported_not_crashed():
    bad = {"fields": list(FIELDS), "n": 1, "rows": [["not-enough-values"]]}
    note, body = pw.summarize_permits_change(bad, pw.permits_snapshot([_pm()], FIELDS))
    assert "structurally invalid" in note
    assert "re-baselines" in note


def test_a_new_record_sharing_an_existing_prmt_num_is_flagged_not_silently_misreported():
    """The load-bearing assumption behind this whole design: prmt_num is
    verified unique live, but not GUARANTEED unique by the API. If it were
    ever to stop holding, a naive by-key dict diff would silently collapse a
    genuinely NEW permit sharing an existing key into a bland "changed" line,
    understating what happened. It must instead be surfaced as a distinct,
    loud case."""
    old = pw.permits_snapshot([_pm(prmt_num="X", status="A")], FIELDS)
    new = pw.permits_snapshot(
        [_pm(prmt_num="X", status="A"), _pm(prmt_num="X", status="B")], FIELDS)
    note, body = pw.summarize_permits_change(old, new)
    assert "not unique" in note.lower()
    assert "1 -> 2" in note
    assert "existing permit changed" not in note   # must NOT misreport as a benign update
    assert "new permit recorded" not in note        # the collision itself is the headline


def test_a_key_collision_is_caught_even_in_the_degraded_truncated_form():
    """_digest_map's dict(...) collapse on read affects the truncated payload
    exactly like _rows_by_key does on the full one. Uses a synthetic 500-row
    snapshot so the OLD side lands in the mid-degradation digest form (real
    Permits volumes never degrade)."""
    rows = [_pm(prmt_num=f"P-{i:05d}") for i in range(500)]
    old_trunc = json.loads(pw._cell_payload(
        pw.permits_snapshot(rows, FIELDS), budget=pw.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    assert not old_trunc.get("digests_dropped")   # the fixture must land mid-degradation
    dup_rows = rows + [_pm(prmt_num="P-00000", status="DUPLICATE-KEY-NEW-RECORD")]
    note, _ = pw.summarize_permits_change(old_trunc, pw.permits_snapshot(dup_rows, FIELDS))
    assert "not unique" in note.lower()


def test_duplicate_key_count_is_zero_for_a_clean_snapshot_full_and_truncated():
    clean = pw.permits_snapshot([_pm(prmt_num="A"), _pm(prmt_num="B")], FIELDS)
    assert pw._duplicate_key_count(clean) == 0
    truncated = json.loads(pw._cell_payload(clean, budget=1))   # force degrade
    assert pw._duplicate_key_count(truncated) == 0


def test_no_ref_level_diff_when_snapshots_are_equal_but_hash_check_was_skipped():
    snap = pw.permits_snapshot([_pm()], FIELDS)
    note, body = pw.summarize_permits_change(snap, copy.deepcopy(snap))
    assert note == "changed (no ref-level diff — see snapshot)"


def test_alert_line_count_is_capped_and_says_what_it_dropped():
    many = [_pm(prmt_num=f"P-{i:05d}") for i in range(pw.MAX_ALERT_LINES + 25)]
    note, body = pw.summarize_permits_change(
        pw.permits_snapshot([], FIELDS), pw.permits_snapshot(many, FIELDS))
    lines = body.splitlines()
    assert len(lines) == pw.MAX_ALERT_LINES + 1     # + the "and N more" line
    assert "and 25 more change line(s) not shown" in lines[-1]


def test_format_change_body_has_essentials_and_no_severity_judgment():
    body = pw.format_change_body("nSITE Permits — Arbor Hills Landfill (N2688)",
                                 "existing permit changed", "~ CHANGED  ROP0000224")
    assert "Arbor Hills Landfill (N2688)" in body
    assert "existing permit changed" in body
    assert "ROP0000224" in body
    assert "no judgment" in body.lower()


def test_format_change_body_disambiguates_from_the_rop_watch():
    """The handoff's explicit adversarial-review requirement: the alert copy
    must say this is a DIFFERENT event than Stream H's public-comment
    trip-wire, in the email itself — not just in the ADR."""
    body = pw.format_change_body("nSITE Permits — Arbor Hills Landfill (N2688)",
                                 "existing permit changed", "~ CHANGED  ROP0000224")
    assert "ROP0000224" in body
    assert "ROP watch" in body or "Stream H" in body
    assert "public-comment" in body or "public comment" in body
    assert "does not mean" in body or "not duplicate" in body.lower() or "DIFFERENT event" in body


# ==============================================================================
# The Sheets cell-size guard — verified-INERT at real Permits volumes, unlike
# Evaluations (kept for structural parity with every sibling watch)
# ==============================================================================


def test_cell_payload_passes_through_under_budget():
    snap = pw.permits_snapshot([_pm()], FIELDS)
    payload = pw._cell_payload(snap, budget=45000)
    assert json.loads(payload)["rows"] == snap["rows"]
    assert "truncated" not in payload


def test_real_n2688_scale_never_triggers_the_degradation_guard():
    """UNLIKE Evaluations, N2688's real 9-permit volume stays far under the
    default budget even as plain per-record objects — the guard here is
    verified-inert insurance, not a live necessity."""
    rows = [_pm(prmt_num=f"P-{i:05d}") for i in range(9)]
    snap = pw.permits_snapshot(rows, FIELDS)
    full = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    assert len(full) < pw.DEFAULT_SNAPSHOT_CHAR_BUDGET
    payload = pw._cell_payload(snap, budget=pw.DEFAULT_SNAPSHOT_CHAR_BUDGET)
    assert "truncated" not in payload


def test_the_degraded_form_still_names_the_new_prmt_num_not_just_a_count():
    """The point of keeping [prmt_num, digest] pairs instead of an anonymous
    digest multiset (Violations/CA's design): even fully truncated, a diff
    against the degraded form can still say WHICH permit is new. Uses a
    synthetic 500-row snapshot (real Permits volumes never degrade) so the
    payload lands in the mid-degradation digest form, not the final clamp."""
    old_rows = [_pm(prmt_num=f"P-{i:05d}") for i in range(500)]
    old_payload = json.loads(pw._cell_payload(
        pw.permits_snapshot(old_rows, FIELDS), budget=pw.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    assert not old_payload.get("digests_dropped")   # the fixture must land mid-degradation
    new_snap = pw.permits_snapshot(old_rows + [_pm(prmt_num="P-BRANDNEW")], FIELDS)
    note, body = pw.summarize_permits_change(old_payload, new_snap)
    assert "1 new permit" in note
    assert "+ NEW PERMIT  P-BRANDNEW" in body


def test_the_degraded_form_reports_a_changed_prmt_num_without_field_detail():
    old_rows = [_pm(prmt_num=f"P-{i:05d}") for i in range(500)]
    old_payload = json.loads(pw._cell_payload(
        pw.permits_snapshot(old_rows, FIELDS), budget=pw.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    assert not old_payload.get("digests_dropped")   # the fixture must land mid-degradation
    changed_rows = list(old_rows)
    changed_rows[0] = _pm(prmt_num="P-00000", status="Expired")
    new_snap = pw.permits_snapshot(changed_rows, FIELDS)
    note, body = pw.summarize_permits_change(old_payload, new_snap)
    assert "1 permit(s) with changed status/detail" in note
    assert "~ CHANGED  P-00000" in body
    assert "snapshot too large for a field-level diff" in body


def test_the_truncated_fallback_is_itself_bounded():
    huge = pw.permits_snapshot(
        [_pm(prmt_num=f"P-{i:06d}", status=f"status {i}") for i in range(4000)], FIELDS)
    payload = pw._cell_payload(huge, budget=pw.DEFAULT_SNAPSHOT_CHAR_BUDGET)
    assert len(payload) <= pw.DEFAULT_SNAPSHOT_CHAR_BUDGET
    assert len(payload) < 50000
    body = json.loads(payload)
    assert body["truncated"] is True and body["digests_dropped"] is True
    assert body["n"] == 4000


def test_a_digest_dropped_snapshot_reports_count_only_not_a_crash():
    huge = pw.permits_snapshot([_pm(prmt_num=f"P-{i:06d}") for i in range(4000)], FIELDS)
    old = json.loads(pw._cell_payload(huge, budget=pw.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    new = pw.permits_snapshot([_pm(prmt_num=f"P-{i:06d}") for i in range(4001)], FIELDS)
    note, _ = pw.summarize_permits_change(old, new)
    assert "4000 -> 4001" in note
    assert "no ref-level diff available" in note


def test_snapshot_hash_ignores_the_cell_budget_entirely():
    snap = pw.permits_snapshot([_pm(prmt_num=f"P-{i:06d}") for i in range(600)], FIELDS)
    assert pw._cell_payload(snap, budget=10) != pw._cell_payload(snap, budget=999999)
    assert pw.snapshot_hash(snap) == pw.snapshot_hash(copy.deepcopy(snap))


def test_cell_payload_degrade_looks_up_prmt_num_by_field_name_not_position():
    """_cell_payload's own degrade step must key off the same field-name
    lookup _rows_by_key/_digest_map use when READING a stored snapshot — not
    a hardcoded row[0] — so a future reordering of PERMIT_FIELDS can't
    silently corrupt every degraded-mode digest."""
    rows = [_pm(prmt_num=f"P-{i:05d}") for i in range(600)]
    snap = pw.permits_snapshot(rows, ("status", "prmt_num") + tuple(
        f for f in FIELDS if f not in ("status", "prmt_num")))
    full = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    assert len(full) > 20000                       # the fixture really is oversized at this budget
    payload = pw._cell_payload(snap, budget=20000)
    body = json.loads(payload)
    assert body["truncated"] is True
    assert not body.get("digests_dropped")          # must land in the digest form, not the final clamp
    digest_keys = {k for k, _ in body["digests"]}
    assert digest_keys == {r["prmt_num"] for r in rows}


def test_digest_map_looks_up_prmt_num_by_field_name_not_position():
    """A hand-reordered stored cell (a human edits the Sheet) must still parse
    — _digest_map looks up prmt_num's column index rather than assuming 0."""
    snap = {"fields": ["status", "prmt_num"], "n": 1,
            "rows": [["Extended", "ROP0000224"]]}
    dm = pw._digest_map(snap)
    assert dm is not None and "ROP0000224" in dm


def test_a_json_scalar_in_the_snapshot_cell_does_not_wedge_the_site():
    for raw in ("0", "null", "true", "42", '"hello"', "[]", "not json at all"):
        assert pw._load_json(raw, {}) == {} or isinstance(pw._load_json(raw, {}), dict)
    note, _ = pw.summarize_permits_change(
        pw._load_json("0", {}), pw.permits_snapshot([_pm()], FIELDS))
    assert "missing or unreadable" in note


# ==============================================================================
# Config gate + cadence wiring
# ==============================================================================


def test_should_run_false_when_disabled():
    ok, reason = pw._should_run({"nsite_permits": {"enabled": False}})
    assert ok is False and "false" in reason.lower()


def test_should_run_false_when_key_absent():
    ok, _ = pw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = pw._should_run({"nsite_permits": {"enabled": True}})
    assert ok is True and reason == ""


def test_is_due_is_imported_not_reimplemented():
    """The handoff is explicit: reuse nsite_submissions_watcher._is_due rather
    than copy-pasting it. Identity, not equivalence — a copy would drift."""
    assert pw._is_due is sub_w._is_due


def test_diff_fields_defaults_to_every_field():
    assert pw.diff_fields({}) == nc.PERMIT_FIELDS


def test_diff_fields_honors_the_exclude_lever():
    fields = pw.diff_fields({"nsite_permits": {"exclude_fields": ["permit_type"]}})
    assert "permit_type" not in fields
    assert len(fields) == len(nc.PERMIT_FIELDS) - 1


def test_diff_fields_never_excludes_prmt_num_even_if_configured_to():
    """UNLIKE Violations/CA's exclude_fields (which can drop even the display
    headline), prmt_num is the structural diff KEY — excluding it would break
    every snapshot/diff helper, not just degrade a line's readability."""
    fields = pw.diff_fields({"nsite_permits": {"exclude_fields": ["prmt_num", "permit_type"]}})
    assert "prmt_num" in fields
    assert "permit_type" not in fields


def test_alerting_is_configured_detects_each_way_delivery_can_be_impossible(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(var, "x")
    ok, reason = pw.alerting_is_configured({}, ["a@example.com"])
    assert ok is True and reason == ""

    monkeypatch.delenv("SMTP_PASSWORD")
    ok, reason = pw.alerting_is_configured({}, ["a@example.com"])
    assert ok is False and "SMTP_PASSWORD" in reason

    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(pw.ea, "resolve_recipients", lambda cfg: [])
    ok, reason = pw.alerting_is_configured({}, None)
    assert ok is False and "recipients" in reason


def test_a_structural_break_fails_loudly_instead_of_going_quiet(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm()], "WRD": []})
    pw.run()                                          # baseline both
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteStructuralError("hasResultsRemaining")))
    assert pw.run() == 1
    assert sent == []
    # And the plain transient case, on the same baselined state, stays quiet:
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteFetchError("connection reset")))
    assert pw.run() == 0


def test_structural_error_is_a_subclass_so_existing_handlers_still_catch_it():
    assert issubclass(nc.NsiteStructuralError, nc.NsiteFetchError)


def test_snapshot_char_budget_is_clamped_below_the_hard_sheets_cap(monkeypatch):
    seen = {}
    real = pw._diff_and_record

    def _capture(*a, **kw):
        seen["budget"] = a[9] if len(a) > 9 else kw.get("budget")
        return real(*a, **kw)
    cfg = copy.deepcopy(PERMIT_CFG)
    cfg["nsite_permits"]["snapshot_char_budget"] = 60000
    _wire(monkeypatch, cfg, {"N2688": [_pm()], "WRD": []})
    monkeypatch.setattr(pw, "_diff_and_record", _capture)
    assert pw.run() == 0
    assert seen["budget"] < pw.HARD_SHEETS_CELL_LIMIT


def test_the_workflow_is_scheduled_directly_not_parked():
    """This build's SSH key authenticated non-interactively, so — like Stream
    N — the workflow was landed straight into .github/workflows/ rather than
    parked. Still tolerant of a future re-park (e.g. a rotated,
    passphrase-locked key) so this test doesn't need to change if that
    changes, matching the enforcing pattern test_nsite_compliance_actions.py
    established."""
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "config.yml") as f:
        cfg = yaml.safe_load(f)
    scheduled = (root / ".github" / "workflows" / "nsite-permits-watch.yml").exists()
    parked = (root / "docs" / "pending-workflows" / "nsite-permits-watch.yml").exists()
    assert scheduled or parked, "the workflow file has gone missing entirely"
    assert cfg["nsite_permits"]["enabled"] is False or scheduled, (
        "nsite_permits.enabled is true but the workflow is still parked "
        "at docs/pending-workflows/ — the watch would never be scheduled. "
        "git mv docs/pending-workflows/nsite-permits-watch.yml .github/workflows/"
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
    tiers = cfg["nsite_permits"]["tiers"]
    registry = {s["srn"] for s in cfg["nsite_sites"]}
    assert set(tiers) == registry
    assert set(tiers.values()) <= {"daily", "biweekly", "quarterly"}
    assert tiers != cfg["nsite_violations"]["tiers"]
    assert tiers != cfg["nsite_compliance_actions"]["tiers"]
    assert tiers != cfg["nsite_submissions"]["tiers"]
    assert tiers != cfg["nsite_evaluations"]["tiers"]


def test_shipped_config_ships_disabled():
    """New source, unattended overnight build — enabled: false is Trisha's
    activation step, not this build's."""
    import pathlib

    import yaml
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["nsite_permits"]["enabled"] is False


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

SITES = [
    {"srn": "N2688", "name": "Arbor Hills Landfill", "id": "8094300008956198244"},
    {"srn": "WRD", "name": "GFL-Arbor Hills Landfill-Washtenaw Co", "id": "306291952280313698"},
]

PERMIT_CFG = {
    "nsite_sites": SITES,
    "nsite_permits": {"enabled": True, "tiers": {"N2688": "daily", "WRD": "daily"}},
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
    monkeypatch.setattr(pw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(pw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(pw.nc, "make_session", lambda: object())
    monkeypatch.setattr(pw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(session, nsite_id):
        srn = next(s["srn"] for s in sites if s["id"] == nsite_id)
        result = fetch_by_srn(srn) if callable(fetch_by_srn) else fetch_by_srn[srn]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(pw.nc, "fetch_site_permits", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_PERMITS, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    """The shipped state: enabled is false, so the scheduled job must not even
    build a Sheets client."""
    monkeypatch.setattr(pw, "load_config",
                        lambda: {"nsite_permits": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(pw.dc, "sheets_service", boom)
    assert pw.run() == 0


def test_tiers_srn_missing_from_registry_raises_keyerror(monkeypatch):
    cfg = {
        "nsite_sites": [{"srn": "N2688", "name": "AH", "id": _N2688_ID}],
        "nsite_permits": {"enabled": True, "tiers": {"TYPO_SRN": "daily"}},
    }
    monkeypatch.setattr(pw, "load_config", lambda: cfg)
    with pytest.raises(KeyError, match="TYPO_SRN"):
        pw.run()


def test_run_skips_a_site_that_is_not_due_today_no_fetch_no_row(monkeypatch):
    sites = [
        {"srn": "N2688", "name": "Arbor Hills Landfill", "id": _N2688_ID},
        {"srn": "COMP", "name": "Arbor Hills Composting Faciltiy", "id": "-2164784335333909072"},
    ]
    cfg = {
        "nsite_sites": sites,
        "nsite_permits": {"enabled": True,
                          "tiers": {"N2688": "daily", "COMP": "quarterly"}},
        "alert_recipients": ["a@example.com"],
    }
    fake, sent = _wire(monkeypatch, cfg, {
        "N2688": [_pm()],
        "COMP": AssertionError("a not-due site must never be fetched"),
    })
    monkeypatch.setattr(pw, "_is_due", lambda cadence, srn, today: srn != "COMP")
    assert pw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"prmt:N2688"}
    assert sent == []


def test_first_run_baselines_every_site_silently_including_zero_permit_sites(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {
        "N2688": [_pm() for _ in range(3)], "WRD": [],
    })
    assert pw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert {r[1] for r in rows} == {"prmt:N2688", "prmt:WRD"}
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm()], "WRD": []})
    pw.run()
    assert pw.run() == 0
    assert len(_rows(fake)) == 2
    assert sent == []


def test_new_permit_emails_an_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm(prmt_num="P-1")], "WRD": []})
    pw.run()   # baseline
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="P-1"), _pm(prmt_num="P-2", status="In Effect")]
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 0
    matches = [s for s in sent if "N2688" in s[0] or "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "+ NEW PERMIT" in matches[0][1]
    assert "P-2" in matches[0][1]
    assert matches[0][2] is None   # None -> send_email resolves full alert_recipients


def test_status_change_on_an_existing_permit_emails_an_alert(monkeypatch):
    """End-to-end version of the handoff's named requirement: a real Extended
    -> Expired flip on ROP0000224 must actually reach the alert email."""
    fake, sent = _wire(monkeypatch, PERMIT_CFG,
                       {"N2688": [_pm(prmt_num="ROP0000224", status="Extended")], "WRD": []})
    pw.run()   # baseline
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="ROP0000224", status="Expired")]
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "ROP0000224" in matches[0][1]
    assert "status: Extended -> Expired" in matches[0][1]


def test_first_permit_at_a_zero_site_alerts_as_new(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm()], "WRD": []})
    pw.run()   # baseline: WRD at zero
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm()] if nsite_id == _N2688_ID
                            else [_pm(prmt_num="WRP-NEW", category="Resources Minor Project")]))
    assert pw.run() == 0
    matches = [s for s in sent if "WRD" in s[0] or "Washtenaw" in s[0]]
    assert len(matches) == 1
    assert "new permit recorded" in matches[0][1]
    assert "WRP-NEW" in matches[0][1]


def test_no_egle_derived_text_reaches_the_email_subject(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm(prmt_num="P-1")], "WRD": []})
    pw.run()
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="P-1"),
                             _pm(prmt_num="INJECTED\nSubject: evil",
                                 status="also injected\r\nX-Header: bad")]
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 0
    subjects = [s[0] for s in sent]
    assert subjects == [
        "[Permits watch] nSITE Permits — Arbor Hills Landfill (N2688) changed"]
    assert all("\n" not in s and "INJECTED" not in s for s in subjects)


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm()], "WRD": []})
    pw.run()   # baseline
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("blip"))
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 0   # not loud
    assert len(_rows(fake)) == 2   # unchanged, nothing new appended
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud_but_other_site_still_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {
        "N2688": nc.NsiteFetchError("bot wall on the runner"), "WRD": [_pm()],
    })
    assert pw.run() == 1
    assert {r[1] for r in _rows(fake)} == {"prmt:WRD"}   # partial, not all-or-nothing
    assert sent == []


def test_a_sheet_write_failure_isolates_to_one_site_and_never_alerts(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm()], "WRD": [_pm()]})
    real_append = sw.append_permits_watch_row

    def _boom(service, sheet_id, date, item_key, *a, **kw):
        if item_key == "prmt:N2688":
            raise RuntimeError("cell exceeded 50000 characters")
        return real_append(service, sheet_id, date, item_key, *a, **kw)
    monkeypatch.setattr(pw.sw, "append_permits_watch_row", _boom)

    assert pw.run() == 1                                   # loud
    assert {r[1] for r in _rows(fake)} == {"prmt:WRD"}     # the later site still ran
    assert sent == []                                      # no alert for the failed row


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(PERMIT_CFG)
    cfg["nsite_permits"]["recipients"] = ["trisha@example.com"]
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_pm(prmt_num="P-1")], "WRD": []})
    pw.run()   # baseline
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="P-1"), _pm(prmt_num="P-2")]
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert matches[0][2] == ["trisha@example.com"]


def test_alert_email_failure_keeps_the_durable_row_AND_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm(prmt_num="P-1")], "WRD": []})
    pw.run()   # baseline
    monkeypatch.setattr(pw.ea, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP down")))
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="P-1"), _pm(prmt_num="P-2")]
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 1
    n2688_rows = [r for r in _rows(fake) if r[1] == "prmt:N2688"]
    assert len(n2688_rows) == 2 and n2688_rows[1][3] == "changed"


def test_known_undeliverable_alerting_defers_the_change_instead_of_consuming_it(monkeypatch):
    """send_email PRINTS AND RETURNS when SMTP is unconfigured, so nothing
    raises. If the run proceeded anyway it would write the row, ADVANCE the
    stored hash, and the next run would say 'unchanged' and never retry — the
    notification gone permanently even after the secret was fixed."""
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm(prmt_num="P-1")], "WRD": []})
    pw.run()                                          # healthy baseline first
    before = [list(r) for r in _rows(fake)]
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="P-1"), _pm(prmt_num="P-2")]
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 1                              # loud
    assert [list(r) for r in _rows(fake)] == before    # nothing consumed
    assert sent == []

    monkeypatch.setenv("SMTP_PASSWORD", "test-value")
    assert pw.run() == 0
    assert len([s for s in sent if "Arbor Hills Landfill" in s[0]]) == 1


def test_a_transient_tab_read_failure_aborts_before_any_write(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm(prmt_num="P-1")], "WRD": []})
    pw.run()                                   # baseline both sites
    before = [list(r) for r in _rows(fake)]

    def _boom(service, sheet_id, item_keys):
        raise RuntimeError("HTTP 429 rate limited")
    monkeypatch.setattr(pw.sw, "last_permits_snapshots", _boom)
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="P-1"), _pm(prmt_num="P-REAL-NEW")]
                            if nsite_id == _N2688_ID else []))

    assert pw.run() == 1
    assert [list(r) for r in _rows(fake)] == before   # nothing written at all
    assert sent == []

    # ...and once the read recovers, the change is still detected, not lost.
    monkeypatch.undo()
    fake2, sent2 = _wire(monkeypatch, PERMIT_CFG, {
        "N2688": [_pm(prmt_num="P-1"), _pm(prmt_num="P-REAL-NEW")], "WRD": []})
    fake2._values._tabs = fake._values._tabs
    assert pw.run() == 0
    assert len([s for s in sent2 if "Arbor Hills Landfill" in s[0]]) == 1


def test_a_cleared_snapshot_cell_does_not_masquerade_as_a_clean_site(monkeypatch):
    fake, sent = _wire(monkeypatch, PERMIT_CFG,
                       {"N2688": [_pm(prmt_num="P-1"), _pm(prmt_num="P-2")], "WRD": []})
    pw.run()   # baseline
    for r in fake._values._tabs[sw.TAB_PERMITS]:
        if len(r) > 7 and r[1] == "prmt:N2688":
            r[7] = ""            # a human clears the big JSON cell
    monkeypatch.setattr(pw.nc, "fetch_site_permits",
                        lambda session, nsite_id: (
                            [_pm(prmt_num="P-1"), _pm(prmt_num="P-2"), _pm(prmt_num="P-3")]
                            if nsite_id == _N2688_ID else []))
    assert pw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "new permit recorded" not in matches[0][1]
    assert "missing or unreadable" in matches[0][1]


def test_an_unrecognized_cadence_polls_rather_than_silently_skipping(monkeypatch):
    cfg = copy.deepcopy(PERMIT_CFG)
    cfg["nsite_permits"]["tiers"] = {"N2688": "dayly", "WRD": "daily"}
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_pm()], "WRD": []})
    assert pw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"prmt:N2688", "prmt:WRD"}


def test_last_permits_snapshots_batches_into_one_tab_read(monkeypatch):
    fake = FakeSheets()
    calls = []
    orig = fake.values
    monkeypatch.setattr(fake, "values", lambda: (calls.append(1) or orig()))
    sw.ensure_permits_tabs(fake, "SID")
    sw.append_permits_watch_row(fake, "SID", "2026-08-22", "prmt:A", "A", "baseline",
                                "hash1", "note", "now", "{}")
    sw.append_permits_watch_row(fake, "SID", "2026-08-22", "prmt:B", "B", "baseline",
                                "hash2", "note", "now", "{}")
    calls.clear()
    result = sw.last_permits_snapshots(fake, "SID", ["prmt:A", "prmt:B", "prmt:C"])
    assert result["prmt:A"] == ("hash1", "{}")
    assert result["prmt:B"] == ("hash2", "{}")
    assert result["prmt:C"] is None
    assert len(calls) == 1   # one values() call for all three keys, not three


def test_last_permits_snapshots_raises_rather_than_swallowing_a_read_error():
    class _Exploding:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId, range):
            raise RuntimeError("HTTP 503")
    with pytest.raises(RuntimeError):
        sw.last_permits_snapshots(_Exploding(), "SID", ["prmt:A"])


def test_run_issues_exactly_one_tab_read_for_all_sites(monkeypatch):
    reads = []
    real = sw.last_permits_snapshots

    def _counting(service, sheet_id, item_keys):
        reads.append(list(item_keys))
        return real(service, sheet_id, item_keys)
    monkeypatch.setattr(pw.sw, "last_permits_snapshots", _counting)
    _wire(monkeypatch, PERMIT_CFG, {"N2688": [_pm()], "WRD": []})
    assert pw.run() == 0
    assert len(reads) == 1
    assert sorted(reads[0]) == ["prmt:N2688", "prmt:WRD"]


def test_permits_tab_is_separate_state_from_its_sibling_watches():
    """Five watches, five tabs, five item-key namespaces — a prmt:* row must
    never be read as a viol:*/ca:*/subm:*/eval:* row or vice versa."""
    assert sw.TAB_PERMITS not in (
        sw.TAB_VIOLATIONS, sw.TAB_COMPLIANCE_ACTIONS, sw.TAB_SUBMISSIONS, sw.TAB_EVALUATIONS)
    fake = FakeSheets()
    sw.ensure_permits_tabs(fake, "SID")
    sw.ensure_evaluations_tabs(fake, "SID")
    sw.append_permits_watch_row(fake, "SID", "2026-08-22", "prmt:N2688", "M", "baseline",
                                "phash", "n", "now", "{}")
    assert sw.last_evaluations_snapshots(fake, "SID", ["prmt:N2688"])["prmt:N2688"] is None
    assert sw.last_permits_snapshots(
        fake, "SID", ["prmt:N2688"])["prmt:N2688"] == ("phash", "{}")
