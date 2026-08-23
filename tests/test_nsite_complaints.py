"""nsite_client.fetch_site_complaints + nsite_complaints_watcher (Stream P,
ADR 031) — the activation gate, the fetch-error contract (a failure must
never look like "zero complaints"), the pure count+hash+windowed-recency
snapshot/diff helpers, the cell-size guard, and the full baseline/unchanged/
new/changed/fetch-fail flows driven through a fake Sheets service (no
network, no creds). Mirrors tests/test_nsite_violations.py's structure;
reuses FakeSheets from test_pfas_watcher.

THE volume test (test_the_n2688_scale_snapshot_...) is the one that matters
here: this profile's whole design exists because N2688 carries 6,396 real
complaint records — an order of magnitude past what forced Violations'/
Compliance Actions' digest degradation, and past what even THAT degradation
can absorb (measured live 2026-08-22: ~102,000 chars, still 2x the 50,000
cell cap, because complaints carry zero duplicate-tuple compression unlike
Violations' RA). Several fixtures below are shaped from that live spike."""
import copy
import json

import pytest

import nsite_client as nc
import nsite_complaints_watcher as cw
import nsite_submissions_watcher as sub_w
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# fetch_site_complaints — the fetch-error contract
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


# Shaped from the live N2688 profile, 2026-08-22.
_RAW_COMPLAINT = {
    "submRefProgramAreaDescr": "AQD - Air",
    "submRefFormTypeDescr": "Complaint",
    "submRcvdDate": "2026-08-17T00:00:00.0000000-04:00",
    "submSubmRefNum": "HQQ-WA43-BC4YM",
}


def test_fetch_normalizes_a_real_shaped_record():
    session = _Session(_Resp({"queryResults": [_RAW_COMPLAINT]}))
    rows = nc.fetch_site_complaints(session, "8094300008956198244")
    assert rows == [{
        "ref_num": "HQQ-WA43-BC4YM",
        "form_type": "Complaint",
        "program_area": "AQD - Air",
        "received_date": "2026-08-17",   # UTC offset dropped, date kept
    }]


def test_fetch_drops_a_keyless_record_rather_than_diffing_it():
    """Like Evaluations/Permits (and unlike Violations/Compliance Actions),
    this profile's diff key is required — a record without one cannot be
    placed in the watcher's ref-number-set fingerprint."""
    raw = dict(_RAW_COMPLAINT, submSubmRefNum="")
    session = _Session(_Resp({"queryResults": [raw, _RAW_COMPLAINT]}))
    rows = nc.fetch_site_complaints(session, "X")
    assert len(rows) == 1
    assert rows[0]["ref_num"] == "HQQ-WA43-BC4YM"


def test_fetch_normalizes_the_est_offset_the_same_as_edt():
    raw = dict(_RAW_COMPLAINT, submRcvdDate="2021-12-15T00:00:00.0000000-05:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    assert nc.fetch_site_complaints(session, "X")[0]["received_date"] == "2021-12-15"


def test_fetch_normalizes_a_null_field_to_empty_string():
    raw = dict(_RAW_COMPLAINT, submRefProgramAreaDescr=None)
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_complaints(session, "X")
    assert rows[0]["program_area"] == ""
    assert all(v is not None for v in rows[0].values())


def test_fetch_survives_an_out_of_range_date_instead_of_blinding_the_site():
    raw = dict(_RAW_COMPLAINT, submRcvdDate="2026-02-30T00:00:00.0000000-04:00")
    session = _Session(_Resp({"queryResults": [raw]}))
    rows = nc.fetch_site_complaints(session, "X")
    assert rows[0]["received_date"] == ""
    assert rows[0]["ref_num"] == "HQQ-WA43-BC4YM"


def test_fetch_retries_then_raises_on_persistent_network_failure():
    session = _Session([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])

    class _BoomSession:
        calls = 0

        def get(self, *a, **kw):
            self.calls += 1
            raise RuntimeError("connection reset")
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_complaints(_BoomSession(), "X")


def test_fetch_raises_never_returns_empty_on_a_missing_query_results_key():
    session = _Session(_Resp({"somethingElse": []}))
    with pytest.raises(nc.NsiteFetchError, match="queryResults"):
        nc.fetch_site_complaints(session, "X")


def test_fetch_raises_structural_error_on_has_results_remaining():
    """The single most likely profile to actually trip this — 6,396 records
    at N2688 alone, far past every sibling's volume."""
    session = _Session(_Resp({"queryResults": [], "hasResultsRemaining": True}))
    with pytest.raises(nc.NsiteStructuralError):
        nc.fetch_site_complaints(session, "X")


# ==============================================================================
# complaints_snapshot / snapshot_hash / _cell_payload — pure, no network
# ==============================================================================


def _c(ref="R1", received="2026-08-01", **kw):
    base = {"ref_num": ref, "form_type": "Complaint", "program_area": "AQD - Air",
            "received_date": received}
    base.update(kw)
    return base


def test_snapshot_hash_stable_across_row_order():
    a = cw.complaints_snapshot([_c(ref="B"), _c(ref="A")])
    b = cw.complaints_snapshot([_c(ref="A"), _c(ref="B")])
    assert cw.snapshot_hash(a) == cw.snapshot_hash(b)


def test_snapshot_hash_changes_when_the_ref_set_changes():
    a = cw.complaints_snapshot([_c(ref="A")])
    b = cw.complaints_snapshot([_c(ref="A"), _c(ref="B")])
    assert cw.snapshot_hash(a) != cw.snapshot_hash(b)


def test_snapshot_hash_ignores_non_ref_fields_by_design():
    """Deliberate: the hash is over the sorted ref-number SET only, not full
    field tuples — immune to the EDT/EST offset flip that would otherwise
    re-string every `received_date` and false-fire a changed alert on all of
    N2688's 6,396 records twice a year. A same-ref record with a different
    program_area/received_date must NOT change the hash."""
    a = cw.complaints_snapshot([_c(ref="A", received="2026-01-01", program_area="AQD - Air")])
    b = cw.complaints_snapshot([_c(ref="A", received="2026-06-01", program_area="WRD - NPDES")])
    assert cw.snapshot_hash(a) == cw.snapshot_hash(b)


def test_snapshot_n_is_the_exact_record_count():
    snap = cw.complaints_snapshot([_c(ref=f"R{i}") for i in range(7)])
    assert snap["n"] == 7


def test_snapshot_latest_window_is_sorted_most_recent_first_and_bounded():
    rows = [_c(ref=f"R{i}", received=f"2026-01-{i + 1:02d}") for i in range(10)]
    snap = cw.complaints_snapshot(rows, latest_window=3)
    assert [r for r, _ in snap["latest"]] == ["R9", "R8", "R7"]
    assert snap["latest_window"] == 3


def test_snapshot_of_empty_record_set_is_valid():
    snap = cw.complaints_snapshot([])
    assert snap["n"] == 0
    assert snap["latest"] == []
    assert cw.snapshot_hash(snap) == cw.snapshot_hash(cw.complaints_snapshot([]))


def test_cell_payload_fits_comfortably_under_budget_at_realistic_volumes():
    snap = cw.complaints_snapshot([_c(ref=f"R{i:06d}", received="2026-01-01") for i in range(6396)])
    payload = cw._cell_payload(snap)
    assert len(payload) < 5000   # small BY CONSTRUCTION, not degraded
    assert "latest_truncated" not in payload


def test_cell_payload_truncates_the_window_under_a_pathological_budget():
    snap = cw.complaints_snapshot([_c(ref=f"R{i:06d}") for i in range(500)], latest_window=500)
    payload = cw._cell_payload(snap, budget=300)
    assert len(payload) <= 300
    body = json.loads(payload)
    assert body["latest_truncated"] is True
    assert body["n"] == 500   # n is NEVER trimmed


def test_cell_payload_never_trims_n_or_hash_even_when_latest_is_emptied():
    snap = cw.complaints_snapshot([_c(ref=f"R{i:06d}") for i in range(500)], latest_window=500)
    payload = cw._cell_payload(snap, budget=40)   # too small for even one latest entry
    body = json.loads(payload)
    assert body["n"] == 500
    assert "hash" in body and body["hash"]


# ==============================================================================
# summarize_complaints_change — pure, the alert-copy decision logic
# ==============================================================================


def test_missing_old_snapshot_is_reported_not_misread_as_zero_history():
    note, body = cw.summarize_complaints_change({}, cw.complaints_snapshot([_c()]))
    assert "missing or unreadable" in note
    assert "NOT mean" in note


def test_a_json_scalar_in_the_snapshot_cell_does_not_wedge_the_site():
    for raw in ("0", "null", "true", "42", '"hello"', "[]", "not json at all"):
        loaded = cw._load_json(raw, {})
        assert loaded == {} or isinstance(loaded, dict)
    note, _ = cw.summarize_complaints_change(
        cw._load_json("0", {}), cw.complaints_snapshot([_c()]))
    assert "missing or unreadable" in note


def test_zero_to_some_is_the_headline_first_complaint_case():
    old = cw.complaints_snapshot([])
    new = cw.complaints_snapshot([_c(ref="R1", received="2026-08-01")])
    note, body = cw.summarize_complaints_change(old, new)
    assert "FIRST COMPLAINT(S) RECORDED" in note
    assert "R1" in body


def test_some_to_zero_is_the_headline_all_gone_case():
    old = cw.complaints_snapshot([_c(ref="R1")])
    new = cw.complaints_snapshot([])
    note, _ = cw.summarize_complaints_change(old, new)
    assert "ALL COMPLAINTS NO LONGER LISTED" in note


def test_growth_is_reported_as_an_honest_count_change_never_named():
    """The shipped design: a nonzero-baseline count increase is ALWAYS a
    count+context note, never a claimed diff — see the module docstring's
    "WHY THE WINDOWED latest FIELD IS CONTEXT ONLY" section for why naming
    was tried, defeated by three independent review rounds, and reverted."""
    old = cw.complaints_snapshot([_c(ref="R1", received="2026-01-01")], latest_window=50)
    new = cw.complaints_snapshot(
        [_c(ref="R1", received="2026-01-01"), _c(ref="R2", received="2026-08-01")],
        latest_window=50)
    note, body = cw.summarize_complaints_change(old, new)
    assert "1 -> 2" in note
    assert "0 -> " not in note   # not the zero->some case
    assert "+ NEW" not in body
    assert "R2" in body          # shown as context...
    assert "R1" in body          # ...alongside every other item in the window


def test_growth_beyond_the_window_still_gives_honest_count_and_context():
    """N2688's own history has a real burst (246 complaints in a single day,
    2019-11-18) — even at that scale the note is just the count change plus
    whatever fits in the context window, never a false completeness claim."""
    old_rows = [_c(ref=f"OLD{i}", received=f"2026-01-{(i % 28) + 1:02d}") for i in range(5)]
    old = cw.complaints_snapshot(old_rows, latest_window=3)
    new_rows = old_rows + [_c(ref=f"NEW{i}", received="2026-08-15") for i in range(10)]
    new = cw.complaints_snapshot(new_rows, latest_window=3)
    note, body = cw.summarize_complaints_change(old, new)
    assert "5 -> 15" in note
    assert "can't safely name" in note
    assert "context only" in body


def test_a_removal_coinciding_with_growth_never_produces_a_false_new_claim():
    """Regression coverage for the Step 5 review's round-1/round-2 findings:
    an EARLIER version of this module tried to name specific new complaints,
    and a removal coinciding with new arrivals could promote an old,
    previously-invisible survivor (D) into the window and misname it as new
    whenever the counts happened to coincide. The shipped design makes this
    impossible by construction — nothing is ever named — but this test stays
    as a tripwire against reintroducing a named-diff path without also
    reintroducing that bug class."""
    old_rows = [_c(ref="A", received="2026-01-10"), _c(ref="B", received="2026-01-09"),
                _c(ref="C", received="2026-01-08"), _c(ref="D", received="2026-01-07")]
    old = cw.complaints_snapshot(old_rows, latest_window=3)
    new_rows = [_c(ref="B", received="2026-01-09"), _c(ref="C", received="2026-01-08"),
                _c(ref="D", received="2026-01-07"), _c(ref="E", received="2026-01-06"),
                _c(ref="F", received="2026-01-05")]
    new = cw.complaints_snapshot(new_rows, latest_window=3)
    assert new["n"] - old["n"] == 1   # the exact coincidence that used to slip through
    note, body = cw.summarize_complaints_change(old, new)
    assert "+ NEW      D" not in body
    assert "4 -> 5" in note


def test_a_date_correction_on_an_existing_complaint_never_produces_a_false_new_claim():
    """Regression coverage for the Step 5 review's round-3 finding: an
    EXISTING complaint's received_date being corrected by EGLE (e.g. blank ->
    populated) can re-sort it into the window alongside a genuinely new
    arrival, with no removal involved at all — a mechanism a purely
    removal-based check (rounds 1/2's fix) could never catch. This is the
    specific gap that proved the windowed-diff approach unfixable and
    triggered dropping the named-diff path entirely: previously, a naive
    diff could mislabel BOTH the genuinely-new ref and the merely-promoted
    one as new. The shipped design never labels either."""
    old_rows = [_c(ref="A", received="2026-01-10"), _c(ref="B", received="2026-01-09"),
                _c(ref="C", received="")]  # C's date is unknown/blank at first, so it
    old = cw.complaints_snapshot(old_rows, latest_window=2)   # sorts oldest; window is [A, B]
    # C's date gets corrected (now ranks newest of all) AND D genuinely
    # arrives — the ref-number set changes (D is new), so hash changes and
    # an alert does fire, with C promoted into view for a reason that has
    # nothing to do with being new.
    new_rows = [_c(ref="A", received="2026-01-10"), _c(ref="B", received="2026-01-09"),
                _c(ref="C", received="2026-01-11"), _c(ref="D", received="2026-01-12")]
    new = cw.complaints_snapshot(new_rows, latest_window=2)   # window is now [D, C]
    assert new["n"] - old["n"] == 1   # only D is genuinely new
    note, body = cw.summarize_complaints_change(old, new)
    assert "3 -> 4" in note
    assert "+ NEW      D" not in body
    assert "+ NEW      C" not in body


def test_first_sighting_exceeding_the_window_caveats_the_partial_list():
    """A zero-baseline site whose very first sighting already exceeds the
    window size must not silently imply the shown list is exhaustive —
    mirrors the caveat the >K-growth fallback already carries."""
    old = cw.complaints_snapshot([])
    rows = [_c(ref=f"R{i}", received=f"2026-01-{(i % 28) + 1:02d}") for i in range(5)]
    new = cw.complaints_snapshot(rows, latest_window=3)
    note, body = cw.summarize_complaints_change(old, new)
    assert "FIRST COMPLAINT(S) RECORDED" in note
    assert "2 more not shown" in body


def test_simultaneous_add_and_remove_reports_the_honest_net_count():
    """A count delta that doesn't match how many refs changed (a removal
    coinciding with arrivals) is exactly the case where naming an arrival
    count would be wrong — net delta != arrival count. The note reports the
    NET count change only, never an implied arrival count."""
    old = cw.complaints_snapshot([_c(ref="R1", received="2026-01-01"),
                                  _c(ref="R2", received="2026-01-02")], latest_window=50)
    new = cw.complaints_snapshot([_c(ref="R1", received="2026-01-01"),
                                  _c(ref="R3", received="2026-08-01"),
                                  _c(ref="R4", received="2026-08-02")], latest_window=50)
    # n went 2 -> 3 even though TWO refs arrived (R3, R4) and one (R2) left.
    note, _ = cw.summarize_complaints_change(old, new)
    assert "2 -> 3" in note
    assert "2 new" not in note   # never implies an arrival count from the net delta


def test_decrease_is_always_a_removal_never_misread_as_new():
    old = cw.complaints_snapshot([_c(ref="R1"), _c(ref="R2")])
    new = cw.complaints_snapshot([_c(ref="R1")])
    note, _ = cw.summarize_complaints_change(old, new)
    assert "no longer listed" in note
    assert "not a new complaint" in note
    assert "new complaint(s)" not in note or "no longer" in note


def test_same_count_but_changed_ref_set_is_reported_as_no_net_change():
    old = cw.complaints_snapshot([_c(ref="R1")])
    new = cw.complaints_snapshot([_c(ref="R2")])
    note, _ = cw.summarize_complaints_change(old, new)
    assert "no net count change" in note


# ==============================================================================
# THE large-volume test — the one that matters (per the handoff, Step 7)
# ==============================================================================


def test_the_n2688_scale_snapshot_fits_under_the_sheets_cell_cap():
    """6,396 is N2688's real live count (2026-08-22 spike); pad past it to be
    safe against future growth. Every per-record encoding this repo has used
    for a sibling profile fails at this volume (measured: full dicts
    870,156 chars, ref-keyed minimal 422,436 chars, even Violations'/CA's own
    digest-multiset degradation ~102,336 chars) — this design must not."""
    rows = [_c(ref=f"HQQ-{i:06d}", received=f"2026-{(i % 12) + 1:02d}-01") for i in range(6400)]
    snap = cw.complaints_snapshot(rows)
    payload = cw._cell_payload(snap)
    assert len(payload) < cw.HARD_SHEETS_CELL_LIMIT
    assert len(payload) < 5000   # small by construction, not merely under the cap
    assert snap["n"] == 6400


def test_baseline_at_n2688_scale_does_not_flood_an_alert(monkeypatch):
    """Even at 6,400 records, the first sighting must record silently."""
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {
        "N2688": [_c(ref=f"HQQ-{i:06d}") for i in range(6400)], "WRD": [],
    })
    assert cw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert sent == []


def test_a_single_new_complaint_on_top_of_n2688_scale_still_alerts_honestly(monkeypatch):
    """The scenario the whole design exists to serve: one real new filing
    arrives on top of thousands of historical ones and must still trigger a
    visible alert — reporting the honest count change plus context, not a
    claimed name (see the module docstring for why naming was tried and
    reverted), and NOT silently swallowed by the scale."""
    baseline_rows = [_c(ref=f"HQQ-{i:06d}", received="2020-01-01") for i in range(6400)]
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": baseline_rows, "WRD": []})
    cw.run()   # baseline
    monkeypatch.setattr(
        cw.nc, "fetch_site_complaints",
        lambda session, nsite_id: (
            baseline_rows + [_c(ref="NEW-COMPLAINT-1", received="2026-08-20")]
            if nsite_id == _N2688_ID else []))
    assert cw.run() == 0
    matches = [s for s in sent if "N2688" in s[0]]
    assert len(matches) == 1
    assert "6400 -> 6401" in matches[0][1]
    assert "NEW-COMPLAINT-1" in matches[0][1]   # newest-received, so it's in the context window
    assert "+ NEW      NEW-COMPLAINT-1" not in matches[0][1]


# ==============================================================================
# Config gate + cadence wiring
# ==============================================================================


def test_should_run_false_when_disabled():
    ok, reason = cw._should_run({"nsite_complaints": {"enabled": False}})
    assert ok is False and "false" in reason.lower()


def test_should_run_false_when_key_absent():
    ok, _ = cw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = cw._should_run({"nsite_complaints": {"enabled": True}})
    assert ok is True and reason == ""


def test_is_due_is_imported_not_reimplemented():
    assert cw._is_due is sub_w._is_due


def test_alerting_is_configured_detects_each_way_delivery_can_be_impossible(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(var, "x")
    ok, reason = cw.alerting_is_configured({}, ["a@example.com"])
    assert ok is True and reason == ""

    monkeypatch.delenv("SMTP_PASSWORD")
    ok, reason = cw.alerting_is_configured({}, ["a@example.com"])
    assert ok is False and "SMTP_PASSWORD" in reason

    monkeypatch.setenv("SMTP_PASSWORD", "x")
    monkeypatch.setattr(cw.ea, "resolve_recipients", lambda cfg: [])
    ok, reason = cw.alerting_is_configured({}, None)
    assert ok is False and "recipients" in reason


def test_a_structural_break_fails_loudly_instead_of_going_quiet(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})
    cw.run()   # baseline both
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteStructuralError("hasResultsRemaining")))
    assert cw.run() == 1
    assert sent == []
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (_ for _ in ()).throw(
                            nc.NsiteFetchError("connection reset")))
    assert cw.run() == 0


def test_structural_error_is_a_subclass_so_existing_handlers_still_catch_it():
    assert issubclass(nc.NsiteStructuralError, nc.NsiteFetchError)


def test_snapshot_char_budget_is_clamped_below_the_hard_sheets_cap(monkeypatch):
    seen = {}
    real = cw._diff_and_record

    def _capture(*a, **kw):
        seen["budget"] = a[9] if len(a) > 9 else kw.get("budget")
        return real(*a, **kw)
    cfg = copy.deepcopy(COMPLAINTS_CFG)
    cfg["nsite_complaints"]["snapshot_char_budget"] = 60000
    _wire(monkeypatch, cfg, {"N2688": [_c()], "WRD": []})
    monkeypatch.setattr(cw, "_diff_and_record", _capture)
    assert cw.run() == 0
    assert seen["budget"] < cw.HARD_SHEETS_CELL_LIMIT


def test_latest_window_config_lever_is_clamped_to_at_least_one(monkeypatch):
    cfg = copy.deepcopy(COMPLAINTS_CFG)
    cfg["nsite_complaints"]["latest_window"] = 0
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_c()], "WRD": []})
    assert cw.run() == 0   # must not crash / disable the named-diff path entirely


def test_the_parked_workflow_must_be_in_place_before_the_stream_is_enabled():
    """The durable form of the parked-workflow hazard (see the sibling
    Violations/Compliance Actions tests this mirrors). This build's SSH check
    succeeded, so the workflow is expected to land directly in
    .github/workflows/ — but the invariant is written the same defensive way
    regardless of which path a given session took."""
    import pathlib

    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    with open(root / "config.yml") as f:
        cfg = yaml.safe_load(f)
    scheduled = (root / ".github" / "workflows" / "nsite-complaints-watch.yml").exists()
    parked = (root / "docs" / "pending-workflows" / "nsite-complaints-watch.yml").exists()
    assert scheduled or parked, "the workflow file has gone missing entirely"
    assert cfg["nsite_complaints"]["enabled"] is False or scheduled, (
        "nsite_complaints.enabled is true but the workflow is still parked "
        "at docs/pending-workflows/ — the watch would never be scheduled. "
        "git mv docs/pending-workflows/nsite-complaints-watch.yml .github/workflows/"
    )


def test_shipped_config_ships_disabled():
    """New source built unattended (overnight-coder Step 3) — verified live
    against config.yml, not assumed from the handoff."""
    import pathlib

    import yaml
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["nsite_complaints"]["enabled"] is False


def test_shipped_config_tiers_cover_every_registry_site_and_differ_from_siblings():
    import pathlib

    import yaml
    with open(pathlib.Path(__file__).resolve().parent.parent / "config.yml") as f:
        cfg = yaml.safe_load(f)
    tiers = cfg["nsite_complaints"]["tiers"]
    registry = {s["srn"] for s in cfg["nsite_sites"]}
    assert set(tiers) == registry
    assert set(tiers.values()) <= {"daily", "biweekly", "quarterly"}
    for sibling in ("nsite_violations", "nsite_compliance_actions",
                    "nsite_evaluations", "nsite_permits", "nsite_submissions"):
        assert tiers != cfg[sibling]["tiers"]


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

SITES = [
    {"srn": "N2688", "name": "Arbor Hills Landfill", "id": "8094300008956198244"},
    {"srn": "WRD", "name": "GFL-Arbor Hills Landfill-Washtenaw Co", "id": "306291952280313698"},
]

COMPLAINTS_CFG = {
    "nsite_sites": SITES,
    "nsite_complaints": {"enabled": True, "tiers": {"N2688": "daily", "WRD": "daily"}},
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
    monkeypatch.setattr(cw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(cw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(cw.nc, "make_session", lambda: object())
    monkeypatch.setattr(cw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(session, nsite_id):
        srn = next(s["srn"] for s in sites if s["id"] == nsite_id)
        result = fetch_by_srn(srn) if callable(fetch_by_srn) else fetch_by_srn[srn]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(cw.nc, "fetch_site_complaints", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_COMPLAINTS, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    monkeypatch.setattr(cw, "load_config",
                        lambda: {"nsite_complaints": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(cw.dc, "sheets_service", boom)
    assert cw.run() == 0


def test_tiers_srn_missing_from_registry_raises_keyerror(monkeypatch):
    cfg = {
        "nsite_sites": [{"srn": "N2688", "name": "AH", "id": _N2688_ID}],
        "nsite_complaints": {"enabled": True, "tiers": {"TYPO_SRN": "daily"}},
    }
    monkeypatch.setattr(cw, "load_config", lambda: cfg)
    with pytest.raises(KeyError, match="TYPO_SRN"):
        cw.run()


def test_run_skips_a_site_that_is_not_due_today_no_fetch_no_row(monkeypatch):
    sites = [
        {"srn": "N2688", "name": "Arbor Hills Landfill", "id": _N2688_ID},
        {"srn": "COMP", "name": "Arbor Hills Composting Faciltiy", "id": "-2164784335333909072"},
    ]
    cfg = {
        "nsite_sites": sites,
        "nsite_complaints": {"enabled": True,
                             "tiers": {"N2688": "daily", "COMP": "quarterly"}},
        "alert_recipients": ["a@example.com"],
    }
    fake, sent = _wire(monkeypatch, cfg, {
        "N2688": [_c()],
        "COMP": AssertionError("a not-due site must never be fetched"),
    })
    monkeypatch.setattr(cw, "_is_due", lambda cadence, srn, today: srn != "COMP")
    assert cw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"cmplt:N2688"}
    assert sent == []


def test_first_run_baselines_every_site_silently_including_zero_complaint_sites(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})
    assert cw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert {r[1] for r in rows} == {"cmplt:N2688", "cmplt:WRD"}
    assert sent == []


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})
    cw.run()
    assert cw.run() == 0
    assert len(_rows(fake)) == 2
    assert sent == []


def test_new_complaint_emails_an_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c(ref="R1")], "WRD": []})
    cw.run()   # baseline
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (
                            [_c(ref="R1"), _c(ref="R2", received="2026-08-20")]
                            if nsite_id == _N2688_ID else []))
    assert cw.run() == 0
    matches = [s for s in sent if "N2688" in s[0] or "Arbor Hills Landfill" in s[0]]
    assert len(matches) == 1
    assert "1 -> 2" in matches[0][1]
    assert "R2" in matches[0][1]
    assert matches[0][2] is None   # None -> send_email resolves full alert_recipients


def test_removed_complaint_alerts_without_claiming_new(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG,
                       {"N2688": [_c(ref="R1"), _c(ref="R2")], "WRD": []})
    cw.run()
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (
                            [_c(ref="R1")] if nsite_id == _N2688_ID else []))
    assert cw.run() == 0
    matches = [s for s in sent if "N2688" in s[0]]
    assert len(matches) == 1
    assert "no longer listed" in matches[0][1]
    assert "not a new complaint" in matches[0][1]


def test_no_egle_derived_text_reaches_the_email_subject(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c(ref="R1")], "WRD": []})
    cw.run()
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (
                            [_c(ref="R1"), _c(ref="EGLE-SENSITIVE-TEXT")]
                            if nsite_id == _N2688_ID else []))
    assert cw.run() == 0
    matches = [s for s in sent if "N2688" in s[0]]
    assert "EGLE-SENSITIVE-TEXT" not in matches[0][0]


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})
    cw.run()
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("boom"))
                            if nsite_id == _N2688_ID else []))
    assert cw.run() == 0
    # "unchanged" never writes a row (only baseline/changed do) — 2 baseline
    # rows from run #1, nothing new from run #2's fetch-failed/unchanged pair.
    assert len(_rows(fake)) == 2
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud_but_other_site_still_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {
        "N2688": AssertionError("wired via lambda below"), "WRD": [],
    })
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("boom"))
                            if nsite_id == _N2688_ID else []))
    assert cw.run() == 1
    rows = _rows(fake)
    assert {r[1] for r in rows} == {"cmplt:WRD"}


def test_a_sheet_write_failure_isolates_to_one_site_and_never_alerts(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": [_c()]})

    def _boom(*a, **kw):
        raise RuntimeError("sheets rejected the write")
    real_append = sw.append_complaints_watch_row
    calls = {"n": 0}

    def _flaky(service, sheet_id, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("sheets rejected the write")
        return real_append(service, sheet_id, *a, **kw)
    monkeypatch.setattr(cw.sw, "append_complaints_watch_row", _flaky)
    assert cw.run() == 1
    assert len(_rows(fake)) == 1   # the second site's row still landed
    assert sent == []


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(COMPLAINTS_CFG)
    cfg["nsite_complaints"]["recipients"] = ["only@example.com"]
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_c(ref="R1")], "WRD": []})
    cw.run()
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (
                            [_c(ref="R1"), _c(ref="R2")] if nsite_id == _N2688_ID else []))
    assert cw.run() == 0
    matches = [s for s in sent if "N2688" in s[0]]
    assert matches[0][2] == ["only@example.com"]


def test_alert_email_failure_keeps_the_durable_row_and_exits_loud(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c(ref="R1")], "WRD": []})
    cw.run()
    monkeypatch.setattr(cw.ea, "send_email",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("smtp down")))
    monkeypatch.setattr(cw.nc, "fetch_site_complaints",
                        lambda session, nsite_id: (
                            [_c(ref="R1"), _c(ref="R2")] if nsite_id == _N2688_ID else []))
    assert cw.run() == 1
    rows = [r for r in _rows(fake) if r[1] == "cmplt:N2688"]
    assert rows[-1][3] == "changed"   # row is durable despite the send failure


def test_known_undeliverable_alerting_defers_the_change_instead_of_consuming_it(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert cw.run() == 1
    assert _rows(fake) == []   # nothing written — the change wasn't consumed
    monkeypatch.setenv("SMTP_PASSWORD", "test-value")
    assert cw.run() == 0
    assert len(_rows(fake)) == 2


def test_a_transient_tab_read_failure_aborts_before_any_write(monkeypatch):
    fake, sent = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})

    def _boom(*a, **kw):
        raise RuntimeError("throttled")
    monkeypatch.setattr(cw.sw, "last_complaints_snapshots", _boom)
    assert cw.run() == 1
    assert _rows(fake) == []
    monkeypatch.undo()
    fake2, sent2 = _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})
    assert cw.run() == 0
    assert len(_rows(fake2)) == 2


def test_an_unrecognized_cadence_polls_rather_than_silently_skipping(monkeypatch):
    cfg = copy.deepcopy(COMPLAINTS_CFG)
    cfg["nsite_complaints"]["tiers"]["N2688"] = "hourly"   # not a real cadence
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_c()], "WRD": []})
    assert cw.run() == 0
    assert {r[1] for r in _rows(fake)} == {"cmplt:N2688", "cmplt:WRD"}


def test_last_complaints_snapshots_batches_into_one_tab_read(monkeypatch):
    fake = FakeSheets()
    sw.ensure_complaints_tabs(fake, "SID")   # header write happens BEFORE we start counting
    calls = []
    orig = fake.values
    monkeypatch.setattr(fake, "values", lambda: (calls.append(1) or orig()))
    sw.last_complaints_snapshots(fake, "SID", ["cmplt:N2688", "cmplt:WRD", "cmplt:RA"])
    assert calls == [1]


def test_last_complaints_snapshots_raises_rather_than_swallowing_a_read_error():
    class _Boom:
        def spreadsheets(self):
            raise RuntimeError("throttled")
    with pytest.raises(RuntimeError):
        sw.last_complaints_snapshots(_Boom(), "SID", ["cmplt:N2688"])


def test_run_issues_exactly_one_tab_read_for_all_sites(monkeypatch):
    calls = {"n": 0}
    real = sw.last_complaints_snapshots

    def _counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)
    monkeypatch.setattr(cw.sw, "last_complaints_snapshots", _counting)
    _wire(monkeypatch, COMPLAINTS_CFG, {"N2688": [_c()], "WRD": []})
    cw.run()
    assert calls["n"] == 1


def test_complaints_tab_is_separate_state_from_every_sibling_profile():
    assert sw.TAB_COMPLAINTS not in (
        sw.TAB_VIOLATIONS, sw.TAB_COMPLIANCE_ACTIONS, sw.TAB_EVALUATIONS,
        sw.TAB_PERMITS, sw.TAB_SUBMISSIONS,
    )
    fake = FakeSheets()
    sw.ensure_complaints_tabs(fake, "SID")
    assert sw.TAB_COMPLAINTS in fake._values._tabs
