"""doc_pipeline_liveness.py + watcher._check_liveness — the nSITE document
pipeline's silent-stall guard (docs/overnight-coder-handoffs/stall-hardening.md,
part (a); the 2026-08-02 digest-recovery lesson).

Pure tests mirror tests/test_gfl_air.py's liveness_decision block; the run()
tests drive watcher.run with every external call faked and a shared in-memory
state so the health singleton persists across runs the way _meta does.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import doc_pipeline_liveness as dl
import watcher as w


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


NOW = _dt("2026-10-23T11:00:00Z")


# --- update_health (pure) ------------------------------------------------------

def test_update_health_stamps_activity_when_nothing_was_unprocessed():
    h = dl.update_health({}, now_utc=NOW, unprocessed=0, processed=0, struck=0, deferred=0)
    assert h["last_activity_at"] == "2026-10-23T11:00:00Z"
    assert h["consecutive_deferrals"] == 0


def test_update_health_stamps_activity_on_a_processed_doc_and_on_a_strike():
    h1 = dl.update_health({}, now_utc=NOW, unprocessed=3, processed=1, struck=0, deferred=0)
    h2 = dl.update_health({}, now_utc=NOW, unprocessed=3, processed=0, struck=1, deferred=0)
    assert h1["last_activity_at"] == h2["last_activity_at"] == "2026-10-23T11:00:00Z"


def test_update_health_keeps_old_activity_when_queue_made_no_progress():
    prev = {"last_activity_at": "2026-10-20T11:00:00Z", "consecutive_deferrals": 0}
    # every doc a transient retry
    h = dl.update_health(prev, now_utc=NOW, unprocessed=3, processed=0, struck=0, deferred=0)
    assert h["last_activity_at"] == "2026-10-20T11:00:00Z"
    # every doc deferred
    h = dl.update_health(prev, now_utc=NOW, unprocessed=30, processed=0, struck=0, deferred=30)
    assert h["last_activity_at"] == "2026-10-20T11:00:00Z"
    assert prev["consecutive_deferrals"] == 0            # input never mutated


def test_update_health_counts_consecutive_deferrals_and_resets_on_a_clean_run():
    h = {}
    for _ in range(3):
        h = dl.update_health(h, now_utc=NOW, unprocessed=30, processed=0, struck=0, deferred=30)
    assert h["consecutive_deferrals"] == 3
    h["warned_deferrals"] = True
    h = dl.update_health(h, now_utc=NOW, unprocessed=2, processed=2, struck=0, deferred=0)
    assert h["consecutive_deferrals"] == 0
    assert "warned_deferrals" not in h                   # episode over: gate re-armed


def test_update_health_blank_activity_stays_blank_not_invented():
    h = dl.update_health({}, now_utc=NOW, unprocessed=5, processed=0, struck=0, deferred=5)
    assert h["last_activity_at"] == ""


def test_update_health_tolerates_garbage_counter():
    h = dl.update_health({"consecutive_deferrals": "x"}, now_utc=NOW, unprocessed=5,
                         processed=0, struck=0, deferred=5)
    assert h["consecutive_deferrals"] == 1


# --- liveness_decision (pure; now injected) -----------------------------------

def test_decision_stale_fires_when_old_and_unwarned():
    d = dl.liveness_decision({"last_activity_at": "2026-10-18T11:00:00Z"}, NOW, 3, 3)
    assert d.should_warn and d.stale and d.stale_days == 5 and not d.deferrals_tripped


def test_decision_silent_when_fresh():
    d = dl.liveness_decision({"last_activity_at": "2026-10-22T12:00:00Z"}, NOW, 3, 3)
    assert not d.should_warn and d.stale_days == 0


def test_decision_stale_boundary_is_inclusive():
    d = dl.liveness_decision({"last_activity_at": "2026-10-20T11:00:00Z"}, NOW, 3, 3)
    assert d.stale and d.stale_days == 3
    d = dl.liveness_decision({"last_activity_at": "2026-10-20T11:00:01Z"}, NOW, 3, 3)
    assert not d.stale and d.stale_days == 2


def test_decision_stale_silent_when_already_warned_same_episode():
    h = {"last_activity_at": "2026-10-18T11:00:00Z", "warned_stale_for": "2026-10-18T11:00:00Z"}
    d = dl.liveness_decision(h, NOW, 3, 3)
    assert d.stale and not d.should_warn


def test_decision_stale_rearms_after_activity_then_a_new_stall():
    h = {"last_activity_at": "2026-10-19T11:00:00Z", "warned_stale_for": "2026-10-10T11:00:00Z"}
    d = dl.liveness_decision(h, NOW, 3, 3)
    assert d.should_warn


@pytest.mark.parametrize("bad", ["", None, "not-a-timestamp", "2026-10-18"])
def test_decision_unparseable_activity_never_fires_a_quantified_stale(bad):
    d = dl.liveness_decision({"last_activity_at": bad}, NOW, 3, 3)
    assert not d.stale and d.stale_days is None and d.unquantifiable and not d.should_warn


def test_decision_deferrals_trip_at_threshold_once_per_episode():
    h = {"last_activity_at": "2026-10-22T11:00:00Z", "consecutive_deferrals": 2}
    assert not dl.liveness_decision(h, NOW, 3, 3).should_warn
    h["consecutive_deferrals"] = 3
    d = dl.liveness_decision(h, NOW, 3, 3)
    assert d.should_warn and d.deferrals_tripped and not d.stale
    h = dl.mark_warned(h, d)
    assert h["warned_deferrals"] is True and "warned_stale_for" not in h
    h["consecutive_deferrals"] = 4
    assert not dl.liveness_decision(h, NOW, 3, 3).should_warn   # same episode: silent


def test_decision_deferrals_fire_even_when_activity_is_unquantifiable():
    # First run with the guard in a stalled state: no timestamp yet, but the
    # counter needs none.
    h = {"consecutive_deferrals": 3}
    d = dl.liveness_decision(h, NOW, 3, 3)
    assert d.should_warn and d.deferrals_tripped and d.unquantifiable


def test_decision_both_signals_share_one_notice_and_both_get_marked():
    h = {"last_activity_at": "2026-10-18T11:00:00Z", "consecutive_deferrals": 5}
    d = dl.liveness_decision(h, NOW, 3, 3)
    assert d.should_warn and d.stale and d.deferrals_tripped
    h = dl.mark_warned(h, d)
    assert h["warned_stale_for"] == "2026-10-18T11:00:00Z" and h["warned_deferrals"] is True
    assert not dl.liveness_decision(h, NOW, 3, 3).should_warn


def test_decision_zero_threshold_disables_that_signal():
    h = {"last_activity_at": "2026-10-01T11:00:00Z", "consecutive_deferrals": 50}
    assert not dl.liveness_decision(h, NOW, 0, 0).should_warn
    assert dl.liveness_decision(h, NOW, 3, 0).stale
    assert not dl.liveness_decision(h, NOW, 3, 0).deferrals_tripped
    assert dl.liveness_decision(h, NOW, 0, 3).deferrals_tripped
    assert not dl.liveness_decision(h, NOW, 0, 3).stale


def test_decision_empty_health_is_silent():
    assert not dl.liveness_decision({}, NOW, 3, 3).should_warn
    assert not dl.liveness_decision(None, NOW, 3, 3).should_warn


# --- body / subject ------------------------------------------------------------

def test_body_and_subject_read_as_a_health_notice_never_an_alert():
    h = {"last_activity_at": "2026-10-18T11:00:00Z", "consecutive_deferrals": 3}
    d = dl.liveness_decision(h, NOW, 3, 3)
    body = dl.format_liveness_body(d, h, 3, 3, 25)
    subj = dl.liveness_subject(d)
    assert "health" in subj.lower() and "STALLED" in subj
    assert "URGENT" not in subj and "URGENT" not in body
    assert "NOT a document alert" in body
    assert "3 consecutive daily run(s)" in body and "5 day(s)" in body
    assert "max_new_docs_per_run = 25" in body
    assert "2026-10-18T11:00:00Z" in body
    assert "once per episode" in body


def test_body_names_only_the_signal_that_tripped():
    h = {"last_activity_at": "2026-10-22T11:00:00Z", "consecutive_deferrals": 3}
    d = dl.liveness_decision(h, NOW, 3, 3)
    body = dl.format_liveness_body(d, h, 3, 3, 25)
    assert "consecutive daily run(s)" in body and "No document-pipeline activity" not in body
    h = {"last_activity_at": "2026-10-10T11:00:00Z", "consecutive_deferrals": 0}
    d = dl.liveness_decision(h, NOW, 3, 3)
    body = dl.format_liveness_body(d, h, 3, 3, 25)
    assert "consecutive daily run(s)" not in body and "No document-pipeline activity" in body
    assert "never recorded" in dl.format_liveness_body(d, {}, 3, 3, 25)


# --- watcher.run() flow --------------------------------------------------------

def _docs(n, srn="N2688", prefix="d"):
    return [{"doc_id": f"{prefix}{i}", "document_name": f"Filing {i}", "date_filed": "2026-10-20",
             "facility_srn": srn, "facility_name": "x", "facility_scope": "core"}
            for i in range(n)]


def _wire(monkeypatch, docs, cap=25, outcome="processed", max_def=3, max_stale=3):
    """Fake every external call watcher.run() makes. Returns (state, sent,
    metas) — `state` is shared across runs like the real _meta cell."""
    state = {"processed": {}, "skipped": {}, "errors": {}, "pending_digest": [],
             "pending_urgent_recap": [], "doc_pipeline_health": {}}
    cfg = {"anthropic_model": "m", "large_doc_page_threshold": 1,
           "large_doc_max_keyword_pages": 1, "classification_max_tokens": 1,
           "backfill_batch_size": 50,
           "watcher": {"max_new_docs_per_run": cap, "max_consecutive_deferrals": max_def,
                       "max_stale_days": max_stale}}
    sent, metas, processed_calls = [], [], []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(w, "load_config", lambda: cfg)
    monkeypatch.setattr(w, "_today", lambda: datetime(2026, 10, 21).date())   # a Wednesday
    monkeypatch.setattr(w.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(w.sw, "ensure_tabs", lambda *a: None)
    monkeypatch.setattr(w.sw, "read_state", lambda *a: state)
    monkeypatch.setattr(w.sw, "write_meta", lambda svc, sid, st: metas.append(dict(st)))
    monkeypatch.setattr(w.sw, "rebuild_risk_register_tab", lambda *a: None)
    monkeypatch.setattr(w.nc, "make_session", lambda: None)
    monkeypatch.setattr(w.nc, "fetch_all_documents", lambda s, c: [dict(d) for d in docs])
    monkeypatch.setattr(w.well_aliases, "load_alias_map", lambda *a, **k: {})
    monkeypatch.setattr(w.well_aliases, "load_registry", lambda *a, **k: None)
    monkeypatch.setattr(w.well_aliases, "check_alias_map_matches", lambda *a, **k: None)

    def fake_process(ctx, st, d, job, local, purge_first=False):
        processed_calls.append(d["doc_id"])
        if outcome == "processed":
            st["processed"][d["doc_id"]] = {}
        return outcome
    monkeypatch.setattr(w.dp, "process_one_doc", fake_process)
    monkeypatch.setattr(w.on, "send_owner_email",
                        lambda subject, body, cfg: sent.append((subject, body)) or True)
    return state, sent, metas, processed_calls


def test_three_consecutive_over_cap_deferrals_trip_the_alarm_once(monkeypatch):
    state, sent, metas, calls = _wire(monkeypatch, _docs(30), cap=25)
    assert w.run() == 0 and w.run() == 0
    assert sent == [] and calls == []                       # deferred, under threshold
    assert state["doc_pipeline_health"]["consecutive_deferrals"] == 2
    assert w.run() == 0
    assert len(sent) == 1                                   # third straight deferral
    subj, body = sent[0]
    assert "health" in subj.lower() and "URGENT" not in subj
    assert "3 consecutive daily run(s)" in body
    assert state["doc_pipeline_health"]["warned_deferrals"] is True
    assert w.run() == 0 and w.run() == 0
    assert len(sent) == 1                                   # same episode: no re-alert
    assert state["doc_pipeline_health"]["consecutive_deferrals"] == 5
    assert metas and metas[-1]["doc_pipeline_health"] == state["doc_pipeline_health"]


def test_a_run_that_processes_docs_resets_the_deferral_counter(monkeypatch):
    docs = _docs(30)
    state, sent, metas, calls = _wire(monkeypatch, docs, cap=25)
    w.run(); w.run()
    assert state["doc_pipeline_health"]["consecutive_deferrals"] == 2
    # the backlog got drained (by backfill, say) down to a normal day's volume
    docs[:] = _docs(2)
    assert w.run() == 0
    assert calls == ["d0", "d1"]
    h = state["doc_pipeline_health"]
    assert h["consecutive_deferrals"] == 0 and h["last_activity_at"]
    assert sent == []
    w.run(); w.run(); w.run()
    assert sent == []                                       # the counter really reset


def test_alarm_rearms_for_a_second_deferral_episode(monkeypatch):
    docs = _docs(30)
    state, sent, metas, calls = _wire(monkeypatch, docs, cap=25)
    for _ in range(3):
        w.run()
    assert len(sent) == 1
    docs[:] = _docs(1)
    w.run()                                                 # recovery
    assert "warned_deferrals" not in state["doc_pipeline_health"]
    docs[:] = _docs(40)
    for _ in range(3):
        w.run()
    assert len(sent) == 2                                   # a new episode fires again


def test_a_quiet_day_counts_as_activity_and_never_trips_stale(monkeypatch):
    # Everything nSITE lists is already processed: nothing to do, pipeline fine.
    state, sent, metas, calls = _wire(monkeypatch, _docs(2), cap=25)
    state["processed"] = {"d0": {}, "d1": {}}
    state["doc_pipeline_health"] = {"last_activity_at": "2026-10-01T11:00:00Z"}
    assert w.run() == 0
    assert calls == []
    assert state["doc_pipeline_health"]["last_activity_at"] != "2026-10-01T11:00:00Z"
    assert sent == []


def test_transient_failures_every_run_trip_the_stale_alarm_once(monkeypatch):
    state, sent, metas, calls = _wire(monkeypatch, _docs(2), cap=25, outcome="retry")
    # seed: the pipeline was healthy 4 days ago
    state["doc_pipeline_health"] = {"last_activity_at": "2026-10-19T11:00:00Z",
                                    "consecutive_deferrals": 0}
    assert w.run() == 0
    assert calls == ["d0", "d1"]                            # attempted, all transient
    assert len(sent) == 1
    assert "No document-pipeline activity" in sent[0][1]
    assert state["doc_pipeline_health"]["warned_stale_for"] == "2026-10-19T11:00:00Z"
    w.run()
    assert len(sent) == 1                                   # once per episode


def test_a_poison_strike_counts_as_progress_not_a_stall(monkeypatch):
    state, sent, metas, calls = _wire(monkeypatch, _docs(1), cap=25, outcome="error")
    state["doc_pipeline_health"] = {"last_activity_at": "2026-10-10T11:00:00Z",
                                    "consecutive_deferrals": 0}
    assert w.run() == 0
    assert sent == []
    assert state["doc_pipeline_health"]["last_activity_at"] != "2026-10-10T11:00:00Z"


def test_failed_owner_send_leaves_the_episode_unmarked_so_it_retries(monkeypatch):
    state, sent, metas, calls = _wire(monkeypatch, _docs(30), cap=25)

    def boom(subject, body, cfg):
        raise RuntimeError("SMTP down")
    monkeypatch.setattr(w.on, "send_owner_email", boom)
    for _ in range(3):
        assert w.run() == 0                                 # never breaks the run
    assert "warned_deferrals" not in state["doc_pipeline_health"]
    monkeypatch.setattr(w.on, "send_owner_email",
                        lambda subject, body, cfg: sent.append((subject, body)) or True)
    w.run()
    assert len(sent) == 1 and state["doc_pipeline_health"]["warned_deferrals"] is True


def test_unsent_owner_notice_no_addresses_does_not_mark_the_episode(monkeypatch):
    state, sent, metas, calls = _wire(monkeypatch, _docs(30), cap=25)
    monkeypatch.setattr(w.on, "send_owner_email", lambda subject, body, cfg: False)
    for _ in range(4):
        assert w.run() == 0
    assert "warned_deferrals" not in state["doc_pipeline_health"]


def test_liveness_exception_never_breaks_the_run(monkeypatch):
    state, sent, metas, calls = _wire(monkeypatch, _docs(30), cap=25)
    monkeypatch.setattr(w.dl, "update_health",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert w.run() == 0


def test_health_meta_key_is_persisted_by_write_meta():
    import sheet_writer as sw
    assert dl.META_KEY in sw._META_DEFAULTS
    assert len(sw._META_DEFAULTS) <= sw._META_CELL_ROWS


def test_live_config_carries_the_two_thresholds():
    from config_loader import load_config
    wcfg = load_config()["watcher"]
    assert wcfg["max_consecutive_deferrals"] == 3
    assert wcfg["max_stale_days"] == 3
