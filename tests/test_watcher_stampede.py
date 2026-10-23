"""watcher.run's anti-stampede cap (stall-hardening part (c),
docs/overnight-coder-handoffs/stall-hardening.md): an over-cap backlog that
belongs to facilities with NO processing history is a new-facility backlog —
deferred to backfill as before — but it must never again freeze live
monitoring of every already-established facility (2026-07-24: two new
facilities' 73 historical filings deferred N2688's own new filings for two
weeks). Under the cap nothing changes; over the cap, the established
facilities' documents are still processed when they alone fit under it.
"""
from __future__ import annotations

from datetime import datetime

import watcher as w


def _doc(i, srn):
    return {"doc_id": f"{srn}-{i}", "document_name": f"{srn} filing {i}",
            "date_filed": "2026-10-20", "facility_srn": srn, "facility_name": srn,
            "facility_scope": "core"}


# --- the pure split -----------------------------------------------------------

def test_split_by_history_uses_processed_and_skipped_docs_as_history():
    docs = [_doc(0, "N2688"), _doc(1, "N2688"), _doc(0, "RA"), _doc(0, "WRD"), _doc(1, "WRD")]
    state = {"processed": {"N2688-0": {}}, "skipped": {"RA-0": {}}}
    new = [docs[1], docs[3], docs[4]]
    established, fresh = w._split_by_history(new, docs, state)
    assert [d["doc_id"] for d in established] == ["N2688-1"]
    assert [d["doc_id"] for d in fresh] == ["WRD-0", "WRD-1"]


def test_split_by_history_preserves_order_and_tolerates_missing_srn():
    docs = [{"doc_id": "a"}, {"doc_id": "b", "facility_srn": "X"}]
    established, fresh = w._split_by_history(docs, docs, {"processed": {}, "skipped": {}})
    assert established == [] and fresh == docs


# --- run() flow ----------------------------------------------------------------

def _wire(monkeypatch, docs, state, cap=25):
    cfg = {"anthropic_model": "m", "large_doc_page_threshold": 1,
           "large_doc_max_keyword_pages": 1, "classification_max_tokens": 1,
           "backfill_batch_size": 50,
           "watcher": {"max_new_docs_per_run": cap, "max_consecutive_deferrals": 3,
                       "max_stale_days": 3}}
    state.setdefault("errors", {}); state.setdefault("skipped", {})
    state.setdefault("pending_digest", []); state.setdefault("pending_urgent_recap", [])
    state.setdefault("doc_pipeline_health", {})
    calls, sent = [], []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(w, "load_config", lambda: cfg)
    monkeypatch.setattr(w, "_today", lambda: datetime(2026, 10, 21).date())
    monkeypatch.setattr(w.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(w.sw, "ensure_tabs", lambda *a: None)
    monkeypatch.setattr(w.sw, "read_state", lambda *a: state)
    monkeypatch.setattr(w.sw, "write_meta", lambda *a: None)
    monkeypatch.setattr(w.sw, "rebuild_risk_register_tab", lambda *a: None)
    monkeypatch.setattr(w.nc, "make_session", lambda: None)
    monkeypatch.setattr(w.nc, "fetch_all_documents", lambda s, c: [dict(d) for d in docs])
    monkeypatch.setattr(w.well_aliases, "load_alias_map", lambda *a, **k: {})
    monkeypatch.setattr(w.well_aliases, "load_registry", lambda *a, **k: None)
    monkeypatch.setattr(w.well_aliases, "check_alias_map_matches", lambda *a, **k: None)

    def fake_process(ctx, st, d, job, local, purge_first=False):
        calls.append(d["doc_id"])
        st["processed"][d["doc_id"]] = {}
        return "processed"
    monkeypatch.setattr(w.dp, "process_one_doc", fake_process)
    monkeypatch.setattr(w.on, "send_owner_email",
                        lambda subject, body, cfg: sent.append((subject, body)) or True)
    return calls, sent


def test_mixed_backlog_processes_established_facility_and_defers_the_new_one(monkeypatch, capsys):
    # The 2026-07-24 shape: N2688 is established (one processed doc on record)
    # and has 3 genuinely new filings; WRD was just added with 69 never-seen
    # historical docs. 72 > cap 25.
    docs = [_doc(i, "N2688") for i in range(4)] + [_doc(i, "WRD") for i in range(69)]
    state = {"processed": {"N2688-0": {}}}
    calls, sent = _wire(monkeypatch, docs, state)
    assert w.run() == 0
    assert calls == ["N2688-1", "N2688-2", "N2688-3"]           # live monitoring continues
    assert not any(c.startswith("WRD") for c in calls)           # the backlog waits for backfill
    out = capsys.readouterr().out
    assert "no processing history" in out and "WRD" in out
    h = state["doc_pipeline_health"]
    assert h["consecutive_deferrals"] == 1 and h["last_activity_at"]  # deferral still counted


def test_all_new_facility_backlog_is_deferred_as_before(monkeypatch):
    docs = [_doc(i, "WRD") for i in range(30)]
    calls, sent = _wire(monkeypatch, docs, {"processed": {}})
    assert w.run() == 0
    assert calls == []


def test_established_facility_burst_over_cap_is_still_deferred(monkeypatch, capsys):
    # A genuine burst from a monitored facility keeps today's conservative
    # behavior (deferred, and now alarmed after max_consecutive_deferrals).
    docs = [_doc(i, "N2688") for i in range(31)]
    calls, sent = _wire(monkeypatch, docs, {"processed": {"N2688-0": {}}})
    assert w.run() == 0
    assert calls == []
    assert "deferring doc processing to the backfill job" in capsys.readouterr().out


def test_mixed_backlog_whose_established_share_exceeds_the_cap_defers_everything(monkeypatch):
    docs = [_doc(i, "N2688") for i in range(27)] + [_doc(i, "WRD") for i in range(10)]
    calls, sent = _wire(monkeypatch, docs, {"processed": {"N2688-0": {}}})
    assert w.run() == 0
    assert calls == []


def test_under_cap_mixed_docs_are_all_processed_unchanged(monkeypatch):
    docs = [_doc(i, "N2688") for i in range(3)] + [_doc(i, "AHLI") for i in range(4)]
    calls, sent = _wire(monkeypatch, docs, {"processed": {"N2688-0": {}}})
    assert w.run() == 0
    assert calls == ["N2688-1", "N2688-2", "AHLI-0", "AHLI-1", "AHLI-2", "AHLI-3"]


def test_the_2026_07_24_incident_shape_would_not_have_frozen_n2688(monkeypatch):
    # Facilities on 2026-07-24: RA (740 processed), N1504 (54), P1488 (16),
    # N2688 (all processed) established; WRD (69 never seen) + AHLI (4 never
    # seen) brand new. The established facilities' handful of new filings must
    # process; the 73 new-facility docs defer.
    docs = []
    for srn, n_hist in (("RA", 5), ("N1504", 5), ("P1488", 3), ("N2688", 10)):
        docs += [_doc(i, srn) for i in range(n_hist)]
    processed = {d["doc_id"]: {} for d in docs}
    docs += [_doc(100, "N2688"), _doc(101, "N2688"), _doc(100, "RA")]   # 3 new filings
    docs += [_doc(i, "WRD") for i in range(69)] + [_doc(i, "AHLI") for i in range(4)]
    calls, sent = _wire(monkeypatch, docs, {"processed": processed})
    assert w.run() == 0
    assert sorted(calls) == ["N2688-100", "N2688-101", "RA-100"]
