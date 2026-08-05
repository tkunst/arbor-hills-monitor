"""nsite_client.fetch_site_violations + nsite_violations_watcher.py (Stream L,
ADR 023) — the activation gate, the fetch-error contract (a failure must never
look like "zero violations"), the pure counted-multiset snapshot/diff helpers,
the cell-size guard, and the full baseline/unchanged/new/changed/fetch-fail
flows driven through a fake Sheets service (no network, no creds). Mirrors
tests/test_nsite_submissions.py's structure; reuses FakeSheets from
test_pfas_watcher, same idiom as test_rop.py.

Several fixtures below are REAL records copied verbatim from the 2026-08-04
live spike across all 19 sites — including RA's `violDescr: null`, which is
the case a `.get(f, "")` normalizer would silently pass through as None."""
import copy
import json

import pytest

import nsite_client as nc
import nsite_violations_watcher as vw
import nsite_submissions_watcher as sub_w
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# fetch_site_violations — the fetch-error contract
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


# Verbatim from the live N2688 profile, 2026-08-04.
_RAW_VIOLATION = {
    "violRefViolCatgDescr": "Rule 1001: Performance tests by owner",
    "violRefViolTypeDescr": "Testing/Sampling",
    "violRefViolStatDescr": "Active – Compliance Action Taken",
    "violNonCmplStartDate": "2026-07-08T00:00:00.0000000-04:00",
    "violViolNotifCmnts": "PTI No. 79-17, FGENCLOSEDFLARES-S2 Condition V. "
                          "Testing/Sampling 2., 6., and 10.",
    "violDescr": "AQD - Air",
    "evalEvalNum": "E-1DEM-T431-WSP2",
    "evalRefEvalTypeDescr": "On-Site Inspection",
}

# Verbatim from the live N2688 profile — note the real "\r\n" in the comment.
_RAW_VIOLATION_CRLF = {
    "violRefViolCatgDescr": "40 CFR Part 60, Subpart GG: Stationary Gas Turbines",
    "violRefViolTypeDescr": "2nd VN Notice",
    "violRefViolStatDescr": "Active - Addressed not Resolved",
    "violNonCmplStartDate": "2024-05-10T00:00:00.0000000-04:00",
    "violViolNotifCmnts": "EUTURBINE2, EUTURBINE3 ALSO. \r\nFailed to test "
                          "according to GG. VN response not acceptable test plan.",
    "violDescr": "AQD - Air",
    "evalEvalNum": "N268871769",
    "evalRefEvalTypeDescr": "Odor Evaluation",
}


def test_fetch_normalizes_a_real_shaped_record():
    session = _Session(_Resp({"queryResults": [_RAW_VIOLATION]}))
    rows = nc.fetch_site_violations(session, "8094300008956198244")
    assert rows == [{
        "category": "Rule 1001: Performance tests by owner",
        "viol_type": "Testing/Sampling",
        "status": "Active – Compliance Action Taken",
        "start_date": "2026-07-08",          # UTC offset dropped, date kept
        "comments": "PTI No. 79-17, FGENCLOSEDFLARES-S2 Condition V. "
                    "Testing/Sampling 2., 6., and 10.",
        "program": "AQD - Air",
        "eval_num": "E-1DEM-T431-WSP2",
        "eval_type": "On-Site Inspection",
    }]


def test_fetch_normalizes_crlf_in_comments_to_lf():
    """A server-side line-ending change is not an enforcement event — if CRLF
    survived into the tuple, EGLE re-serving the same record with LF would fire
    a false "changed" alert."""
    session = _Session(_Resp({"queryResults": [_RAW_VIOLATION_CRLF]}))
    rows = nc.fetch_site_violations(session, "X")
    assert "\r" not in rows[0]["comments"]
    assert rows[0]["comments"].startswith("EUTURBINE2, EUTURBINE3 ALSO. \nFailed")


def test_fetch_normalizes_null_program_to_empty_string():
    """17 of RA's 299 live records carry `violDescr: null`. `.get(f, "")`
    returns None for a present-but-null key, so the day EGLE serves "" instead
    the hash would flip for no real reason. `or ""` collapses both to ""."""
    raw = dict(_RAW_VIOLATION, violDescr=None)
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_violations(session, "X")
    assert rows[0]["program"] == ""
    assert all(v is not None for v in rows[0].values())


def test_fetch_survives_an_out_of_range_date_instead_of_blinding_the_site():
    """A single "2026-02-30" would otherwise raise ValueError out of date(),
    escape into the retry loop, and surface as a permanent NsiteFetchError —
    blinding the ENTIRE site (skip-and-warn, exit 0, green build) over one bad
    record. Every other parse path is fail-soft; this one must be too."""
    raw = dict(_RAW_VIOLATION, violNonCmplStartDate="2026-02-30T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_violations(session, "X")
    assert rows[0]["start_date"] == ""
    assert rows[0]["category"] == "Rule 1001: Performance tests by owner"
    assert session.calls == 1          # not retried — it was never an error


def test_fetch_accepts_the_us_style_date_format_too():
    """Parity with _normalize's three accepted formats. Without it, an EGLE
    switch to MM/DD/YYYY would collapse every start_date to "" and merge
    distinct records into one multiset entry."""
    raw = dict(_RAW_VIOLATION, violNonCmplStartDate="07/08/2026")
    session = _Session(_Resp({"queryResults": [raw]}))
    assert nc.fetch_site_violations(session, "X")[0]["start_date"] == "2026-07-08"


def test_fetch_raises_rather_than_diffing_a_partial_page():
    """`hasResultsRemaining` is null on every site today, but if nSITE ever
    starts paging this profile a partial page is INDISTINGUISHABLE from a
    shrunken record set: the caller's multiset diff would read the first 100 of
    RA's 299 records as '199 violation records removed' and email that as
    fact."""
    session = _Session(_Resp({"queryResults": [_RAW_VIOLATION], "hasResultsRemaining": True}))
    with pytest.raises(nc.NsiteFetchError, match="paging"):
        nc.fetch_site_violations(session, "X")


def test_fetch_accepts_the_null_hasresultsremaining_the_api_actually_sends():
    session = _Session(_Resp({"queryResults": [_RAW_VIOLATION],
                              "hasResultsRemaining": None, "totalCount": None}))
    assert len(nc.fetch_site_violations(session, "X")) == 1


def test_fetch_empty_queryresults_is_a_valid_zero_result():
    """16 of the 19 watched sites have zero violations. A structurally-sound
    response listing none is NOT an error — it is the baseline."""
    session = _Session(_Resp({"queryResults": []}))
    assert nc.fetch_site_violations(session, "X") == []


def test_fetch_raises_never_returns_empty_on_http_error():
    """THE contract this whole feature depends on: an HTTP failure must raise,
    never silently degrade to []. If this regressed to fetch_site_documents'
    swallow-and-return-[] behavior, the watcher would misread a fetch outage as
    'every violation resolved at once' and fire a false all-clear."""
    session = _Session(_Resp(status=500))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_violations(session, "X")


def test_fetch_raises_on_missing_queryresults_key():
    session = _Session(_Resp({"somethingElse": []}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_violations(session, "X")


def test_fetch_raises_on_non_json_body():
    session = _Session(_Resp(json_ok=False))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_violations(session, "X")


def test_fetch_raises_on_structurally_broken_record():
    """Violations has no unique-key field to filter on, so a non-dict element
    must surface loudly rather than be quietly skipped into a partial list."""
    session = _Session(_Resp({"queryResults": ["not-a-dict"]}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_violations(session, "X")


def test_fetch_retries_then_succeeds():
    session = _Session([_Resp(status=500), _Resp({"queryResults": [_RAW_VIOLATION]})])
    rows = nc.fetch_site_violations(session, "X")
    assert len(rows) == 1
    assert session.calls == 2


def test_fetch_keeps_every_record_including_duplicates():
    """RA's 299 live records collapse to only 108 distinct tuples — EGLE
    genuinely files repeated identical rows, so the client must NOT dedupe or
    filter. A filter here would silently drop real enforcement records."""
    session = _Session(_Resp({"queryResults": [_RAW_VIOLATION] * 3}))
    assert len(nc.fetch_site_violations(session, "X")) == 3


# ==============================================================================
# Pure snapshot / diff helpers
# ==============================================================================

FIELDS = nc.VIOLATION_FIELDS


def _v(category="Rule 1001: Performance tests by owner", status="Active - Addressed not Resolved",
       **kw):
    base = {
        "category": category, "viol_type": "Testing/Sampling", "status": status,
        "start_date": "2026-07-08", "comments": "PTI No. 79-17", "program": "AQD - Air",
        "eval_num": "E-1DEM-T431-WSP2", "eval_type": "On-Site Inspection",
    }
    base.update(kw)
    return base


def test_snapshot_hash_stable_across_row_order():
    a = vw.violations_snapshot([_v(category="B"), _v(category="A")], FIELDS)
    b = vw.violations_snapshot([_v(category="A"), _v(category="B")], FIELDS)
    assert vw.snapshot_hash(a) == vw.snapshot_hash(b)


def test_snapshot_hash_changes_when_any_field_changes():
    a = vw.violations_snapshot([_v(status="Active - Addressed not Resolved")], FIELDS)
    b = vw.violations_snapshot([_v(status="Inactive - Resolved")], FIELDS)
    assert vw.snapshot_hash(a) != vw.snapshot_hash(b)


def test_snapshot_preserves_duplicate_counts_a_set_would_destroy():
    """The finding that forced a multiset: RA has 299 records but only 108
    distinct tuples. Three identical records must snapshot as count=3, and
    losing one of them must change the hash."""
    three = vw.violations_snapshot([_v(), _v(), _v()], FIELDS)
    two = vw.violations_snapshot([_v(), _v()], FIELDS)
    assert three["n"] == 3 and len(three["counted_rows"]) == 1
    assert three["counted_rows"][0][0] == 3
    assert vw.snapshot_hash(three) != vw.snapshot_hash(two)


def test_snapshot_of_empty_list_is_a_valid_baseline():
    snap = vw.violations_snapshot([], FIELDS)
    assert snap["n"] == 0 and snap["counted_rows"] == []
    assert vw.snapshot_hash(snap)   # hashes fine, does not raise


def test_snapshot_is_self_describing_about_its_field_order():
    snap = vw.violations_snapshot([_v()], ("category", "status"))
    assert snap["fields"] == ["category", "status"]
    assert snap["counted_rows"] == [[1, "Rule 1001: Performance tests by owner",
                                     "Active - Addressed not Resolved"]]


def test_first_violation_at_a_zero_site_reads_as_a_headline_not_a_bland_add():
    """The single highest-value alert this stream can produce: 16 of 19 sites
    baseline at zero. "1 record added" would bury it."""
    old = vw.violations_snapshot([], FIELDS)
    new = vw.violations_snapshot([_v(category="Rule 201: Permits to Install")], FIELDS)
    note, body = vw.summarize_violations_change(old, new)
    assert "FIRST VIOLATION(S) RECORDED" in note
    assert "Rule 201: Permits to Install" in body
    assert "+ ADDED" in body


def test_all_violations_disappearing_reads_as_its_own_headline():
    old = vw.violations_snapshot([_v(), _v()], FIELDS)
    new = vw.violations_snapshot([], FIELDS)
    note, body = vw.summarize_violations_change(old, new)
    assert "ALL VIOLATIONS NO LONGER LISTED" in note
    assert body.count("- REMOVED") == 2


def test_status_advance_reads_as_removed_plus_added_with_every_field_shown():
    """A multiset diff has no "updated" verb — a changed record shows as its
    old shape REMOVED plus its new shape ADDED. Every diffed field must appear
    on those lines or the two render identically (rop_watcher's lesson)."""
    old = vw.violations_snapshot([_v(status="Active - Addressed not Resolved")], FIELDS)
    new = vw.violations_snapshot([_v(status="Inactive - Resolved")], FIELDS)
    note, body = vw.summarize_violations_change(old, new)
    assert "1 violation record(s) added/updated" in note
    assert "1 violation record(s) removed/superseded" in note
    assert "status=Inactive - Resolved" in body
    assert "status=Active - Addressed not Resolved" in body


def test_diff_reports_count_changes_among_identical_records():
    """Two identical records becoming three is a real EGLE event and must not
    be invisible just because the tuples match."""
    old = vw.violations_snapshot([_v(), _v()], FIELDS)
    new = vw.violations_snapshot([_v(), _v(), _v()], FIELDS)
    note, body = vw.summarize_violations_change(old, new)
    assert "1 violation record(s) added/updated" in note
    assert body.count("+ ADDED") == 1


def test_the_headline_field_follows_the_configured_field_set():
    """_detail must not hardcode the headline field: with `category` excluded
    via config, every line's headline would degrade to a bare em-dash."""
    fields = tuple(f for f in FIELDS if f != "category")
    old = vw.violations_snapshot([], fields)
    new = vw.violations_snapshot([_v(viol_type="Testing/Sampling")], fields)
    _, body = vw.summarize_violations_change(old, new)
    assert "+ ADDED    Testing/Sampling" in body
    assert "+ ADDED    —" not in body


def test_field_set_change_is_labelled_configuration_not_an_egle_change():
    """Flipping nsite_violations.exclude_fields changes the hash basis. Without
    this branch the diff would render every record as REMOVED + re-ADDED and
    read as a catastrophic enforcement event."""
    old = vw.violations_snapshot([_v(), _v()], FIELDS)
    new = vw.violations_snapshot([_v(), _v()], tuple(f for f in FIELDS if f != "comments"))
    note, body = vw.summarize_violations_change(old, new)
    assert "NOT an EGLE change" in note
    assert "configuration" in note or "exclude_fields" in body
    assert "REMOVED" not in body


def test_alert_line_count_is_capped_and_says_what_it_dropped():
    """A wholesale EGLE re-import could otherwise emit ~600 lines. Whatever is
    dropped is stated — never a silent truncation."""
    many = [_v(category=f"Cat {i:04d}") for i in range(vw.MAX_ALERT_LINES + 25)]
    note, body = vw.summarize_violations_change(vw.violations_snapshot([], FIELDS),
                                                vw.violations_snapshot(many, FIELDS))
    lines = body.splitlines()
    assert len(lines) == vw.MAX_ALERT_LINES + 1     # + the "and N more" line
    assert "and 25 more change line(s) not shown" in lines[-1]


def test_format_change_body_has_essentials_and_no_severity_judgment():
    body = vw.format_change_body("nSITE Violations — Arbor Hills Landfill (N2688)",
                                 "FIRST VIOLATION(S) RECORDED", "+ ADDED  Rule 201")
    assert "Arbor Hills Landfill (N2688)" in body
    assert "FIRST VIOLATION(S) RECORDED" in body
    assert "Rule 201" in body
    assert "NO judgment about which status is good or bad" in body


# ==============================================================================
# The Sheets cell-size guard
# ==============================================================================


def test_cell_payload_passes_through_under_budget():
    snap = vw.violations_snapshot([_v()], FIELDS)
    payload = vw._cell_payload(snap, budget=45000)
    assert json.loads(payload)["counted_rows"] == snap["counted_rows"]
    assert "truncated" not in payload


def test_cell_payload_degrades_to_digests_over_budget():
    snap = vw.violations_snapshot([_v(category=f"Cat {i}") for i in range(400)], FIELDS)
    full = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    # A budget between the digest form and the full form: the full one must be
    # rejected, the digest one kept intact (the clamp beyond it is its own test).
    budget = 20000
    assert len(full) > budget                    # the fixture really is oversized
    payload = vw._cell_payload(snap, budget=budget)
    assert len(payload) <= budget
    body = json.loads(payload)
    assert body["truncated"] is True
    assert body["n"] == 400
    assert len(body["digests"]) == 400
    assert not body.get("digests_dropped")


def test_a_realistic_ra_sized_snapshot_fits_a_sheets_cell():
    """The measurement that drove the counted encoding: RA's 299 records
    serialize to 130,188 chars as one object per record, 2.6x past a Sheets
    cell's 50,000 cap. The counted form must stay well under it."""
    rows = [_v(category=f"Category number {i % 108}",
               comments=f"PTI No. 79-17, FGENCLOSEDFLARES-S2 Condition V. Item {i % 108}")
            for i in range(299)]
    payload = vw._cell_payload(vw.violations_snapshot(rows, FIELDS))
    assert len(payload) < 50000
    assert "truncated" not in payload


def test_the_truncated_fallback_is_itself_bounded():
    """The digest form is ~140 chars per DISTINCT row, so past ~2,000 distinct
    rows it outgrows the very budget it exists to respect — and an over-cap
    write is rejected outright, which is exactly the bulk-re-import failure the
    guard is for. A final clamp drops the digests so the payload is bounded by
    a constant."""
    huge = vw.violations_snapshot(
        [_v(category=f"Category number {i}", comments=f"citation {i}") for i in range(4000)],
        FIELDS)
    payload = vw._cell_payload(huge, budget=vw.DEFAULT_SNAPSHOT_CHAR_BUDGET)
    assert len(payload) <= vw.DEFAULT_SNAPSHOT_CHAR_BUDGET
    assert len(payload) < 50000                       # the hard Sheets cell cap
    body = json.loads(payload)
    assert body["truncated"] is True and body["digests_dropped"] is True
    assert body["n"] == 4000                          # the count still survives


def test_a_digest_dropped_snapshot_still_diffs_at_the_count_level():
    huge = vw.violations_snapshot([_v(category=f"C{i}") for i in range(4000)], FIELDS)
    old = json.loads(vw._cell_payload(huge, budget=vw.DEFAULT_SNAPSHOT_CHAR_BUDGET))
    new = vw.violations_snapshot([_v(category=f"C{i}") for i in range(4001)], FIELDS)
    note, _ = vw.summarize_violations_change(old, new)
    assert "4000 -> 4001" in note
    assert "no field-level diff available" in note


def test_a_structurally_invalid_stored_snapshot_is_reported_not_crashed():
    bad = {"fields": list(FIELDS), "n": 2, "counted_rows": [["not-an-int", "a"]]}
    note, body = vw.summarize_violations_change(bad, vw.violations_snapshot([_v()], FIELDS))
    assert "structurally invalid" in note
    assert "re-baselines" in note


def test_snapshot_hash_ignores_the_cell_budget_entirely():
    """The hash must be computed over the FULL snapshot, never the truncated
    payload — otherwise editing snapshot_char_budget silently re-baselines
    every site and fires a change alert for each."""
    snap = vw.violations_snapshot([_v(category=f"Cat {i}") for i in range(400)], FIELDS)
    assert vw._cell_payload(snap, budget=10) != vw._cell_payload(snap, budget=999999)
    assert vw.snapshot_hash(snap) == vw.snapshot_hash(copy.deepcopy(snap))


def test_truncated_snapshot_diff_reports_counts_and_admits_it_cannot_detail():
    old = json.loads(vw._cell_payload(
        vw.violations_snapshot([_v(category=f"C{i}") for i in range(300)], FIELDS), budget=100))
    new = vw.violations_snapshot([_v(category=f"C{i}") for i in range(301)], FIELDS)
    note, body = vw.summarize_violations_change(old, new)
    assert "300 -> 301" in note
    assert "no field-level diff available" in note
    assert "review MiEnviro" in body.lower() or "MiEnviro" in body


# ==============================================================================
# Config gate + cadence wiring
# ==============================================================================


def test_should_run_false_when_disabled():
    ok, reason = vw._should_run({"nsite_violations": {"enabled": False}})
    assert ok is False and "false" in reason.lower()


def test_should_run_false_when_key_absent():
    ok, _ = vw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = vw._should_run({"nsite_violations": {"enabled": True}})
    assert ok is True and reason == ""


def test_is_due_is_imported_not_reimplemented():
    """The handoff is explicit: reuse nsite_submissions_watcher._is_due rather
    than copy-pasting it. Identity, not equivalence — a copy would drift."""
    assert vw._is_due is sub_w._is_due


def test_diff_fields_defaults_to_every_field_including_comments():
    assert vw.diff_fields({}) == nc.VIOLATION_FIELDS
    assert "comments" in vw.diff_fields({})


def test_diff_fields_honors_the_exclude_lever():
    fields = vw.diff_fields({"nsite_violations": {"exclude_fields": ["comments"]}})
    assert "comments" not in fields
    assert len(fields) == len(nc.VIOLATION_FIELDS) - 1


def test_alerting_is_configured_detects_each_way_delivery_can_be_impossible(monkeypatch):
    """send_email PRINTS AND RETURNS (no exception) when SMTP is unconfigured
    or recipients resolve empty, so catching exceptions alone would let a
    missing/rotated GitHub secret silently swallow a violation alert."""
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(var, "x")
    ok, reason = vw.alerting_is_configured({}, ["a@example.com"])
    assert ok is True and reason == ""

    monkeypatch.delenv("SMTP_PASSWORD")
    ok, reason = vw.alerting_is_configured({}, ["a@example.com"])
    assert ok is False and "SMTP_PASSWORD" in reason

    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(vw.ea, "resolve_recipients", lambda cfg: [])
    ok, reason = vw.alerting_is_configured({}, None)
    assert ok is False and "recipients" in reason


def test_an_undeliverable_alert_exits_loud_even_though_send_email_never_raises(monkeypatch):
    """The whole failure chain in one test: SMTP unset -> send_email no-ops ->
    the row still lands and advances the hash -> the next run says 'unchanged'
    and never retries. That must not be a green build."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    # AFTER _wire, which sets a deliverable environment by default.
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(vw.ea, "send_email", ea_noop := (lambda *a, **k: None))
    assert vw.run() == 0          # baseline run: nothing to deliver, so not loud
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(), _v(category="Rule 201")] if nsite_id == _N2688_ID else []))
    assert vw.run() == 1          # change recorded, provably not delivered
    n2688 = [r for r in _rows(fake) if r[1] == "viol:N2688"]
    assert len(n2688) == 2 and n2688[1][3] == "changed"   # durable row survived
    assert ea_noop is not None


def test_snapshot_char_budget_is_clamped_below_the_hard_sheets_cap(monkeypatch):
    """`snapshot_char_budget` sits in config.yml right under a comment naming
    the 50,000 cap, so "raise it a bit" is a plausible edit — and any value at
    or above the cap would disable the guard and hand the site a permanently
    rejected write."""
    seen = {}
    real = vw._diff_and_record

    def _capture(*a, **kw):
        seen["budget"] = a[9] if len(a) > 9 else kw.get("budget")
        return real(*a, **kw)
    cfg = copy.deepcopy(VIOL_CFG)
    cfg["nsite_violations"]["snapshot_char_budget"] = 60000
    _wire(monkeypatch, cfg, {"N2688": [_v()], "WRD": []})
    monkeypatch.setattr(vw, "_diff_and_record", _capture)
    assert vw.run() == 0
    assert seen["budget"] < vw.HARD_SHEETS_CELL_LIMIT


def test_a_json_scalar_in_the_snapshot_cell_does_not_wedge_the_site():
    """`0`, `null` and `true` all parse as valid JSON but are not mappings —
    `"fields" not in 0` raises TypeError, which would leave the site with no
    row and no alert on every run, defeating the unreadable-snapshot branch."""
    for raw in ("0", "null", "true", "42", '"hello"', "[]", "not json at all"):
        assert vw._load_json(raw, {}) == {} or isinstance(vw._load_json(raw, {}), dict)
    note, _ = vw.summarize_violations_change(
        vw._load_json("0", {}), vw.violations_snapshot([_v()], FIELDS))
    assert "missing or unreadable" in note


def test_truncated_diff_uses_digests_to_report_row_level_magnitude():
    """Without this, 300 records wholly replaced by 300 different ones reports
    the entire change as "300 -> 300" — describing nothing."""
    old_rows = [_v(category=f"C{i}") for i in range(300)]
    new_rows = [_v(category=f"D{i}") for i in range(300)]
    # A budget the digest form fits but the full form doesn't — past the final
    # clamp the digests are gone and no row-level magnitude is recoverable.
    old = json.loads(vw._cell_payload(vw.violations_snapshot(old_rows, FIELDS), budget=20000))
    new = vw.violations_snapshot(new_rows, FIELDS)
    note, _ = vw.summarize_violations_change(old, new)
    assert "300 -> 300" in note
    assert "300 row(s) appeared, 300 row(s) disappeared" in note


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
    scheduled = (root / ".github" / "workflows" / "nsite-violations-watch.yml").exists()
    parked = (root / "docs" / "pending-workflows" / "nsite-violations-watch.yml").exists()
    assert scheduled or parked, "the workflow file has gone missing entirely"
    assert cfg["nsite_violations"]["enabled"] is False or scheduled, (
        "nsite_violations.enabled is true but the workflow is still parked at "
        "docs/pending-workflows/ — the watch would never be scheduled. "
        "git mv docs/pending-workflows/nsite-violations-watch.yml .github/workflows/"
    )


def test_shipped_config_is_disabled_and_covers_every_registry_site():
    """Guards two things at once: the new-source gate really ships off, and the
    tiers map stays in sync with nsite_sites (a srn in one and not the other is
    either an unwatched site or a loud KeyError at runtime)."""
    import pathlib

    import yaml
    # Resolved from this file, not the cwd — every other test in the suite is
    # location-independent and this one should be too.
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["nsite_violations"]["enabled"] is False
    tiers = cfg["nsite_violations"]["tiers"]
    registry = {s["srn"] for s in cfg["nsite_sites"]}
    assert set(tiers) == registry
    assert set(tiers.values()) <= {"daily", "biweekly", "quarterly"}
    # The handoff's explicit instruction: these are NOT a copy of Submissions'.
    assert tiers != cfg["nsite_submissions"]["tiers"]


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

SITES = [
    {"srn": "N2688", "name": "Arbor Hills Landfill", "id": "8094300008956198244"},
    {"srn": "WRD", "name": "GFL-Arbor Hills Landfill-Washtenaw Co", "id": "306291952280313698"},
]

VIOL_CFG = {
    "nsite_sites": SITES,
    "nsite_violations": {"enabled": True, "tiers": {"N2688": "daily", "WRD": "daily"}},
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
    # an unconfigured one is (correctly) a non-zero exit. The tests that assert
    # the undeliverable path unset these deliberately.
    for _var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(_var, "test-value")
    monkeypatch.setattr(vw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(vw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(vw.nc, "make_session", lambda: object())
    monkeypatch.setattr(vw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(session, nsite_id):
        srn = next(s["srn"] for s in sites if s["id"] == nsite_id)
        result = fetch_by_srn(srn) if callable(fetch_by_srn) else fetch_by_srn[srn]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(vw.nc, "fetch_site_violations", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_VIOLATIONS, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    """The shipped state: enabled is false, so the scheduled job must not even
    build a Sheets client."""
    monkeypatch.setattr(vw, "load_config", lambda: {"nsite_violations": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(vw.dc, "sheets_service", boom)
    assert vw.run() == 0


def test_tiers_srn_missing_from_registry_raises_keyerror(monkeypatch):
    """A `tiers` srn absent from `nsite_sites` is a config error and must raise
    loudly (ADR 022) — not silently produce an unwatched site."""
    cfg = {
        "nsite_sites": [{"srn": "N2688", "name": "AH", "id": _N2688_ID}],
        "nsite_violations": {"enabled": True, "tiers": {"TYPO_SRN": "daily"}},
    }
    monkeypatch.setattr(vw, "load_config", lambda: cfg)
    with pytest.raises(KeyError):
        vw.run()


def test_run_skips_a_site_that_is_not_due_today_no_fetch_no_row(monkeypatch):
    sites = [
        {"srn": "N2688", "name": "Arbor Hills Landfill", "id": _N2688_ID},
        {"srn": "COMP", "name": "Arbor Hills Composting Faciltiy", "id": "-2164784335333909072"},
    ]
    cfg = {
        "nsite_sites": sites,
        "nsite_violations": {"enabled": True, "tiers": {"N2688": "daily", "COMP": "quarterly"}},
        "alert_recipients": ["a@example.com"],
    }
    fake, sent = _wire(monkeypatch, cfg, {
        "N2688": [_v()],
        "COMP": AssertionError("a not-due site must never be fetched"),
    })
    monkeypatch.setattr(vw, "_is_due", lambda cadence, srn, today: srn != "COMP")
    assert vw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"viol:N2688"}
    assert sent == []


def test_first_run_baselines_every_site_silently_including_zero_violation_sites(monkeypatch):
    """RA's 299-record first run must not alert, and neither must a site whose
    baseline is an empty list."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {
        "N2688": [_v() for _ in range(299)], "WRD": [],
    })
    assert vw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert {r[1] for r in rows} == {"viol:N2688", "viol:WRD"}
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    vw.run()
    assert vw.run() == 0
    assert len(_rows(fake)) == 2
    assert sent == []


def test_new_violation_emails_an_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    vw.run()   # baseline
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(), _v(category="Rule 201: Permits to Install")]
                            if nsite_id == _N2688_ID else []))
    assert vw.run() == 0
    matches = [s for s in sent if "N2688" in s[0] or "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "+ ADDED" in matches[0][1]
    assert "Rule 201: Permits to Install" in matches[0][1]
    assert matches[0][2] is None   # None -> send_email resolves full alert_recipients


def test_first_violation_at_a_zero_site_alerts_with_the_headline(monkeypatch):
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    vw.run()   # baseline: WRD at zero
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v()] if nsite_id == _N2688_ID
                            else [_v(category="Rule 2196: Wetland fill")]))
    assert vw.run() == 0
    matches = [s for s in sent if "WRD" in s[0] or "Washtenaw" in s[0]]
    assert len(matches) == 1
    assert "FIRST VIOLATION(S) RECORDED" in matches[0][1]


def test_no_egle_derived_text_reaches_the_email_subject(monkeypatch):
    """The subject carries only the maintainer-authored label from nsite_sites.
    Free-text EGLE content (comments, category) stays in the body."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    vw.run()
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(), _v(category="INJECTED\nSubject: evil",
                                      comments="also injected\r\nX-Header: bad")]
                            if nsite_id == _N2688_ID else []))
    assert vw.run() == 0
    subjects = [s[0] for s in sent]
    assert subjects == ["[Violations watch] nSITE Violations — Arbor Hills Landfill (N2688) changed"]
    assert all("\n" not in s and "INJECTED" not in s for s in subjects)


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    vw.run()   # baseline
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("blip"))
                            if nsite_id == _N2688_ID else []))
    assert vw.run() == 0   # not loud
    assert len(_rows(fake)) == 2   # unchanged, nothing new appended
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud_but_other_site_still_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch, VIOL_CFG, {
        "N2688": nc.NsiteFetchError("bot wall on the runner"), "WRD": [_v()],
    })
    assert vw.run() == 1
    assert {r[1] for r in _rows(fake)} == {"viol:WRD"}   # partial, not all-or-nothing
    assert sent == []


def test_a_sheet_write_failure_isolates_to_one_site_and_never_alerts(monkeypatch):
    """The hole deliberately NOT mirrored from the Submissions watcher: there,
    append_*_row is called outside the try blocks, so one rejected write aborts
    run() and silently drops every site queued after it. And a site whose row
    failed must not send an alert describing a row that never landed."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": [_v()]})
    real_append = sw.append_violations_watch_row

    def _boom(service, sheet_id, date, item_key, *a, **kw):
        if item_key == "viol:N2688":
            raise RuntimeError("cell exceeded 50000 characters")
        return real_append(service, sheet_id, date, item_key, *a, **kw)
    monkeypatch.setattr(vw.sw, "append_violations_watch_row", _boom)

    assert vw.run() == 1                                   # loud
    assert {r[1] for r in _rows(fake)} == {"viol:WRD"}     # the later site still ran
    assert sent == []                                      # no alert for the failed row


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(VIOL_CFG)
    cfg["nsite_violations"]["recipients"] = ["trisha@example.com"]
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_v()], "WRD": []})
    vw.run()   # baseline
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(), _v(category="X")] if nsite_id == _N2688_ID else []))
    assert vw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert matches[0][2] == ["trisha@example.com"]


def test_alert_email_failure_keeps_the_durable_row_AND_exits_loud(monkeypatch):
    """Two things at once. The row must survive an SMTP failure (durable-first
    ordering), AND the run must exit non-zero — for a stream whose entire
    deliverable is the alert, a green check over a silently-undelivered
    violation notice is the worst possible outcome, and the advanced hash means
    the next run reports "unchanged" and never retries."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    vw.run()   # baseline
    monkeypatch.setattr(vw.ea, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP down")))
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(), _v(category="X")] if nsite_id == _N2688_ID else []))
    assert vw.run() == 1
    n2688_rows = [r for r in _rows(fake) if r[1] == "viol:N2688"]
    assert len(n2688_rows) == 2 and n2688_rows[1][3] == "changed"


def test_a_transient_tab_read_failure_aborts_before_any_write(monkeypatch):
    """The silent-data-loss bug this design's batched, RAISING read exists to
    prevent. If a throttled read were swallowed to "no rows", every site would
    look never-seen, a fresh `baseline` row would be written, and because the
    tab is append-only with last-write-wins that spurious baseline BECOMES the
    state — permanently erasing a real, un-alerted change instead of deferring
    it. So: abort, write nothing, exit 1, leave last run's state intact."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    vw.run()                                   # baseline both sites
    before = [list(r) for r in _rows(fake)]

    def _boom(service, sheet_id, item_keys):
        raise RuntimeError("HTTP 429 rate limited")
    monkeypatch.setattr(vw.sw, "last_violations_snapshots", _boom)
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(), _v(category="Rule 201: a REAL new violation")]
                            if nsite_id == _N2688_ID else []))

    assert vw.run() == 1
    assert [list(r) for r in _rows(fake)] == before   # nothing written at all
    assert sent == []

    # ...and once the read recovers, the change is still detected, not lost.
    monkeypatch.undo()
    fake2, sent2 = _wire(monkeypatch, VIOL_CFG, {
        "N2688": [_v(), _v(category="Rule 201: a REAL new violation")], "WRD": []})
    fake2._values._tabs = fake._values._tabs
    assert vw.run() == 0
    assert len([s for s in sent2 if "Arbor Hills Landfill" in s[0]]) == 1


def test_a_cleared_snapshot_cell_does_not_masquerade_as_a_clean_site(monkeypatch):
    """The snapshot lives in a cell on the OPERATOR-VISIBLE case-file Sheet,
    where a 25 KB JSON blob is exactly what a human tidies away. If an empty or
    unreadable cell diffed as an empty multiset, a site with hundreds of
    existing violations would alert 'FIRST VIOLATION(S) RECORDED' — the
    loudest, most wrong message the stream can send."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v(), _v(category="B")], "WRD": []})
    vw.run()   # baseline
    for r in fake._values._tabs[sw.TAB_VIOLATIONS]:
        if len(r) > 7 and r[1] == "viol:N2688":
            r[7] = ""            # a human clears the big JSON cell
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(), _v(category="B"), _v(category="C")]
                            if nsite_id == _N2688_ID else []))
    assert vw.run() == 0
    matches = [s for s in sent if "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "FIRST VIOLATION" not in matches[0][1]
    assert "missing or unreadable" in matches[0][1]
    assert "does NOT mean the site was previously clean" in matches[0][1]


def test_an_unrecognized_cadence_polls_rather_than_silently_skipping(monkeypatch):
    """Fail-safe, and deliberately different from ADR 022's loud KeyError for
    an unknown srn: a bad srn means we don't know WHAT to poll, but a bad
    cadence only means we don't know HOW OFTEN — and polling every run is the
    complete answer. A typo must never blind a site, nor abort the run."""
    cfg = copy.deepcopy(VIOL_CFG)
    cfg["nsite_violations"]["tiers"] = {"N2688": "dayly", "WRD": "daily"}
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_v()], "WRD": []})
    assert vw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"viol:N2688", "viol:WRD"}


def test_exclude_fields_lever_changes_the_diffed_set_end_to_end(monkeypatch):
    """The rollback lever, exercised through run(): with `comments` excluded, a
    comment-only edit must produce no change at all."""
    cfg = copy.deepcopy(VIOL_CFG)
    cfg["nsite_violations"]["exclude_fields"] = ["comments"]
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_v(comments="original")], "WRD": []})
    vw.run()   # baseline
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(comments="reworded by a staffer")]
                            if nsite_id == _N2688_ID else []))
    assert vw.run() == 0
    assert len(_rows(fake)) == 2   # still just the two baselines
    assert sent == []


def test_comment_only_edit_DOES_alert_by_default(monkeypatch):
    """The shipped default is the fail-safe one: comments are diffed, so a
    reworded citation alerts. This is the documented residual — the test pins
    the behavior so a future change to it is deliberate."""
    fake, sent = _wire(monkeypatch, VIOL_CFG, {"N2688": [_v(comments="original")], "WRD": []})
    vw.run()
    monkeypatch.setattr(vw.nc, "fetch_site_violations",
                        lambda session, nsite_id: (
                            [_v(comments="reworded")] if nsite_id == _N2688_ID else []))
    assert vw.run() == 0
    assert len([s for s in sent if "Arbor Hills Landfill" in s[0]]) == 1


def test_last_violations_snapshots_batches_into_one_tab_read(monkeypatch):
    fake = FakeSheets()
    calls = []
    orig = fake.values
    monkeypatch.setattr(fake, "values", lambda: (calls.append(1) or orig()))
    sw.ensure_violations_tabs(fake, "SID")
    sw.append_violations_watch_row(fake, "SID", "2026-08-04", "viol:A", "A", "baseline",
                                   "hash1", "note", "now", "{}")
    sw.append_violations_watch_row(fake, "SID", "2026-08-04", "viol:B", "B", "baseline",
                                   "hash2", "note", "now", "{}")
    calls.clear()
    result = sw.last_violations_snapshots(fake, "SID", ["viol:A", "viol:B", "viol:C"])
    assert result["viol:A"] == ("hash1", "{}")
    assert result["viol:B"] == ("hash2", "{}")
    assert result["viol:C"] is None
    assert len(calls) == 1   # one values() call for all three keys, not three


def test_last_violations_snapshots_raises_rather_than_swallowing_a_read_error():
    """It must NOT go through sheet_writer._tab_rows, which returns [] for any
    read failure. For a watch that diffs, an indistinguishable [] is silent
    data loss, not a graceful degradation."""
    class _Exploding:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId, range):
            raise RuntimeError("HTTP 503")
    with pytest.raises(RuntimeError):
        sw.last_violations_snapshots(_Exploding(), "SID", ["viol:A"])


def test_run_issues_exactly_one_tab_read_for_all_sites(monkeypatch):
    """~19 reads/run is what makes a throttled response likely in the first
    place; the batched read is the mitigation, so pin it."""
    reads = []
    real = sw.last_violations_snapshots

    def _counting(service, sheet_id, item_keys):
        reads.append(list(item_keys))
        return real(service, sheet_id, item_keys)
    monkeypatch.setattr(vw.sw, "last_violations_snapshots", _counting)
    _wire(monkeypatch, VIOL_CFG, {"N2688": [_v()], "WRD": []})
    assert vw.run() == 0
    assert len(reads) == 1
    assert sorted(reads[0]) == ["viol:N2688", "viol:WRD"]


def test_violations_tab_is_separate_state_from_submissions():
    """Two watches, two tabs, two item-key namespaces — a viol:* row must never
    be read as a subm:* row or vice versa."""
    assert sw.TAB_VIOLATIONS != sw.TAB_SUBMISSIONS
    fake = FakeSheets()
    sw.ensure_violations_tabs(fake, "SID")
    sw.ensure_submissions_tabs(fake, "SID")
    sw.append_violations_watch_row(fake, "SID", "2026-08-04", "viol:N2688", "L", "baseline",
                                   "vhash", "n", "now", "{}")
    assert sw.last_submissions_snapshot(fake, "SID", "viol:N2688") is None
    assert sw.last_violations_snapshots(
        fake, "SID", ["viol:N2688"])["viol:N2688"] == ("vhash", "{}")
