"""nsite_watch_common.py: identity tests pinning that every one of the six
nSITE profile watchers re-exports the SAME shared helper objects (not a
copy), plus direct unit tests of should_run()/run_profile_watch() itself —
the generic skeleton all six watchers' own run() now delegates to. The
per-watcher integration behavior (fetch error handling, diff design, alert
wording) is already covered end-to-end by each watcher's own test file
(test_nsite_violations.py etc.); these tests target the shared
implementation directly, the way test_sheet_writer.py's existing
`_is_due`-identity test targets nsite_submissions_watcher._is_due."""
import pytest

import nsite_watch_common as nwc
import nsite_submissions_watcher as sub_w
import nsite_violations_watcher as vw
import nsite_compliance_actions_watcher as caw
import nsite_evaluations_watcher as ew
import nsite_permits_watcher as pw
import nsite_complaints_watcher as cw
import nsite_public_notices_watcher as pnw

ALL_WATCHERS = [vw, caw, ew, pw, cw, pnw]
# complaints never had its own _row_digest (its fingerprint snapshot keeps
# no per-row digests at all -- see its own module docstring), so it is the
# one watcher excluded from the _row_digest identity check below.
ROW_DIGEST_WATCHERS = [vw, caw, ew, pw, pnw]


# ---------------------------------------------------------------------------
# Identity: every watcher's re-exported helper IS the shared object, not a
# copy -- a future accidental re-implementation would break these, not just
# silently drift.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("watcher", ALL_WATCHERS)
def test_now_is_the_shared_implementation(watcher):
    assert watcher._now is nwc._now


@pytest.mark.parametrize("watcher", ALL_WATCHERS)
def test_today_date_is_the_shared_implementation(watcher):
    assert watcher._today_date is nwc._today_date


@pytest.mark.parametrize("watcher", ALL_WATCHERS)
def test_load_json_is_the_shared_implementation(watcher):
    assert watcher._load_json is nwc._load_json


@pytest.mark.parametrize("watcher", ALL_WATCHERS)
def test_alerting_is_configured_is_the_shared_implementation(watcher):
    assert watcher.alerting_is_configured is nwc.alerting_is_configured


@pytest.mark.parametrize("watcher", ROW_DIGEST_WATCHERS)
def test_row_digest_is_the_shared_implementation(watcher):
    assert watcher._row_digest is nwc._row_digest


def test_complaints_deliberately_has_no_row_digest():
    assert not hasattr(cw, "_row_digest")


@pytest.mark.parametrize("watcher", ALL_WATCHERS)
def test_is_due_is_imported_from_submissions_watcher_not_copied(watcher):
    """Each watcher still imports _is_due directly from
    nsite_submissions_watcher (not forwarded through nsite_watch_common) so
    a monkeypatch.setattr(<watcher>, "_is_due", ...) in an existing test
    still reaches run()'s behavior -- see nsite_watch_common.py's module
    docstring for why that matters."""
    assert watcher._is_due is sub_w._is_due


# ---------------------------------------------------------------------------
# _should_run wrapper: each watcher's thin wrapper delegates to
# nwc.should_run with its OWN config_key.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("watcher,key", [
    (vw, "nsite_violations"),
    (caw, "nsite_compliance_actions"),
    (ew, "nsite_evaluations"),
    (pw, "nsite_permits"),
    (cw, "nsite_complaints"),
    (pnw, "nsite_public_notices"),
])
def test_should_run_wrapper_delegates_with_the_right_config_key(watcher, key):
    assert watcher._should_run({key: {"enabled": True}}) == (True, "")
    assert watcher._should_run({key: {"enabled": False}}) == (
        False, f"{key}.enabled is false — skipping (no-op).")
    assert watcher._should_run({}) == (
        False, f"{key}.enabled is false — skipping (no-op).")


# ---------------------------------------------------------------------------
# nwc.should_run() itself
# ---------------------------------------------------------------------------

def test_should_run_true_when_enabled():
    assert nwc.should_run({"x": {"enabled": True}}, "x") == (True, "")


def test_should_run_false_when_disabled():
    assert nwc.should_run({"x": {"enabled": False}}, "x") == (
        False, "x.enabled is false — skipping (no-op).")


def test_should_run_false_when_key_absent():
    assert nwc.should_run({}, "x") == (
        False, "x.enabled is false — skipping (no-op).")


# ---------------------------------------------------------------------------
# nwc.run_profile_watch() itself -- pure unit tests against plain callables,
# no Sheets/network mocking needed, since run_profile_watch never imports
# sheet_writer/nsite_client directly; every I/O boundary is a parameter.
# ---------------------------------------------------------------------------

def _base_kwargs(**overrides):
    kwargs = dict(
        cfg={
            "nsite_sites": [{"srn": "N2688", "name": "Arbor Hills", "id": "ID1"}],
            "x": {"enabled": True, "tiers": {"N2688": "daily"},
                  "recipients": ["test@example.com"]},
        },
        log_prefix="[test-watch]",
        config_key="x",
        key_prefix="k",
        label_prefix="Test Profile",
        unit_noun="thing(s)",
        tab_name="Test Watch",
        ensure_tabs_fn=lambda sheets, sheet_id: None,
        last_snapshots_fn=lambda sheets, sheet_id, keys: {k: None for k in keys},
        is_due_fn=lambda cadence, srn, today: True,
        fetch_fn=lambda session, nsite_id: [{"a": 1}],
        build_snapshot_fn=lambda rows: {"n": len(rows)},
        diff_and_record_fn=lambda *a: ("baseline", None),
    )
    kwargs.update(overrides)
    return kwargs


@pytest.fixture(autouse=True)
def _env_and_sheets(monkeypatch):
    monkeypatch.setenv("GSHEET_ID", "SID")
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(var, "test-value")
    monkeypatch.setattr(nwc.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(nwc.nc, "make_session", lambda: object())


def test_disabled_is_a_noop(monkeypatch):
    boom = lambda: (_ for _ in ()).throw(AssertionError("must not build a Sheets client"))
    monkeypatch.setattr(nwc.dc, "sheets_service", boom)
    kwargs = _base_kwargs(cfg={"x": {"enabled": False}})
    assert nwc.run_profile_watch(**kwargs) == 0


def test_tiers_srn_missing_from_registry_raises_keyerror():
    kwargs = _base_kwargs(cfg={
        "nsite_sites": [],
        "x": {"enabled": True, "tiers": {"TYPO": "daily"}},
    })
    with pytest.raises(KeyError, match="TYPO"):
        nwc.run_profile_watch(**kwargs)


def test_alerting_not_configured_aborts_before_any_sheets_call(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    boom = lambda: (_ for _ in ()).throw(AssertionError("must not build a Sheets client"))
    monkeypatch.setattr(nwc.dc, "sheets_service", boom)
    assert nwc.run_profile_watch(**_base_kwargs()) == 1


def test_happy_path_baseline_returns_zero():
    assert nwc.run_profile_watch(**_base_kwargs()) == 0


def test_not_due_site_is_skipped_no_fetch():
    fetch_calls = []
    kwargs = _base_kwargs(
        is_due_fn=lambda cadence, srn, today: False,
        fetch_fn=lambda session, nsite_id: fetch_calls.append(1),
    )
    assert nwc.run_profile_watch(**kwargs) == 0
    assert fetch_calls == []


def test_is_due_fn_is_looked_up_fresh_not_captured_at_import_time():
    """The whole reason is_due_fn is a parameter rather than a shared
    nsite_watch_common-level import: each watcher's run() passes its OWN
    current module-global _is_due, so a test's monkeypatch on just that one
    watcher still changes behavior. Simulated here directly: two different
    is_due_fn values in two calls must produce different outcomes."""
    fetch_calls = []
    kwargs = _base_kwargs(fetch_fn=lambda session, nsite_id: fetch_calls.append(1) or [])
    nwc.run_profile_watch(**{**kwargs, "is_due_fn": lambda c, s, t: False})
    assert fetch_calls == []
    nwc.run_profile_watch(**{**kwargs, "is_due_fn": lambda c, s, t: True})
    assert fetch_calls == [1]


def test_structural_fetch_error_is_loud_and_not_skip_and_warn():
    def boom(session, nsite_id):
        raise __import__("nsite_client").NsiteStructuralError("shape changed")
    kwargs = _base_kwargs(fetch_fn=boom)
    assert nwc.run_profile_watch(**kwargs) == 1


def test_transient_fetch_error_with_no_baseline_is_loud():
    import nsite_client as nc

    def boom(session, nsite_id):
        raise nc.NsiteFetchError("network blip")
    kwargs = _base_kwargs(fetch_fn=boom)
    assert nwc.run_profile_watch(**kwargs) == 1


def test_transient_fetch_error_with_a_baseline_is_skip_and_warn():
    import nsite_client as nc

    def boom(session, nsite_id):
        raise nc.NsiteFetchError("network blip")
    kwargs = _base_kwargs(
        fetch_fn=boom,
        last_snapshots_fn=lambda sheets, sheet_id, keys: {k: ("h", "{}") for k in keys},
    )
    assert nwc.run_profile_watch(**kwargs) == 0


def test_a_site_level_exception_is_isolated_and_does_not_abort_the_run():
    kwargs = _base_kwargs(
        cfg={
            "nsite_sites": [
                {"srn": "BAD", "name": "Bad Site", "id": "ID-BAD"},
                {"srn": "N2688", "name": "Arbor Hills", "id": "ID1"},
            ],
            "x": {"enabled": True, "tiers": {"BAD": "daily", "N2688": "daily"},
                  "recipients": ["test@example.com"]},
        },
        fetch_fn=lambda session, nsite_id: (
            (_ for _ in ()).throw(RuntimeError("boom")) if nsite_id == "ID-BAD" else [{"a": 1}]
        ),
    )
    # one site is poisoned, but the run still processes the other and
    # reports a non-zero exit rather than dying outright
    assert nwc.run_profile_watch(**kwargs) == 1


def test_tab_read_failure_aborts_before_any_per_site_work():
    fetch_calls = []

    def boom_read(sheets, sheet_id, keys):
        raise RuntimeError("HTTP 503")
    kwargs = _base_kwargs(
        last_snapshots_fn=boom_read,
        fetch_fn=lambda session, nsite_id: fetch_calls.append(1),
        extra_abort_note=" (extra note)",
    )
    assert nwc.run_profile_watch(**kwargs) == 1
    assert fetch_calls == []


def test_extra_counts_and_summary_hook_round_trip():
    """The Public Notices extension point in isolation: a diff_and_record_fn
    that returns a result kind only present in extra_counts must not KeyError,
    and extra_summary_fn's return value must appear in the printed summary
    (captured via capsys in the caller test below) -- here just confirm no
    crash and a 0 exit."""
    kwargs = _base_kwargs(
        diff_and_record_fn=lambda *a: ("changed_suppressed", None),
        extra_counts=("changed_suppressed",),
        extra_summary_fn=lambda counts: f"{counts['changed_suppressed']} suppressed, ",
    )
    assert nwc.run_profile_watch(**kwargs) == 0


def test_extra_summary_fn_text_reaches_the_done_line(capsys):
    kwargs = _base_kwargs(
        diff_and_record_fn=lambda *a: ("changed_suppressed", None),
        extra_counts=("changed_suppressed",),
        extra_summary_fn=lambda counts: f"{counts['changed_suppressed']} suppressed, ",
    )
    nwc.run_profile_watch(**kwargs)
    out = capsys.readouterr().out
    assert "1 suppressed, " in out


def test_alert_failure_is_reported_as_a_non_zero_exit():
    kwargs = _base_kwargs(diff_and_record_fn=lambda *a: ("changed", "send failed"))
    assert nwc.run_profile_watch(**kwargs) == 1
