"""watcher.run's anti-stampede cap (stall-hardening part (c),
docs/overnight-coder-handoffs/stall-hardening.md): an over-cap backlog that
belongs to facilities with NO processing history is a new-facility backlog —
deferred to backfill as before — but it must never again freeze live
monitoring of every already-established facility (2026-07-24: two new
facilities' 73 historical filings deferred N2688's own new filings for two
weeks). Under the cap nothing changes; over the cap, an established facility's
RECENT filings (within watcher.live_filing_window_days) are still processed
when they alone fit under it. The recency half closes the hole the review
found: a facility whose backfill has only partly drained has history, and its
remaining years-old filings must not stampede into the live feed either.
"""
from __future__ import annotations

from datetime import date, datetime

import watcher as w

TODAY = date(2026, 10, 21)
RECENT = "2026-10-20"
OLD = "2023-05-04"


def _doc(i, srn, filed=RECENT):
    return {"doc_id": f"{srn}-{i}", "document_name": f"{srn} filing {i}",
            "date_filed": filed, "facility_srn": srn, "facility_name": srn,
            "facility_scope": "core"}


# --- the pure split -----------------------------------------------------------

def test_split_uses_processed_and_skipped_docs_as_history():
    docs = [_doc(0, "N2688"), _doc(1, "N2688"), _doc(0, "RA"), _doc(0, "WRD"), _doc(1, "WRD")]
    state = {"processed": {"N2688-0": {}}, "skipped": {"RA-0": {}}}
    new = [docs[1], docs[3], docs[4]]
    live, backlog = w._split_by_history(new, docs, state, TODAY, 30)
    assert [d["doc_id"] for d in live] == ["N2688-1"]
    assert [d["doc_id"] for d in backlog] == ["WRD-0", "WRD-1"]


def test_split_keeps_only_recent_filings_of_an_established_facility_live():
    docs = [_doc(0, "N2688", OLD), _doc(1, "N2688", OLD), _doc(2, "N2688", RECENT),
            _doc(3, "N2688", ""), _doc(4, "N2688", "2026-11-30")]     # blank / future
    state = {"processed": {"N2688-0": {}}, "skipped": {}}
    live, backlog = w._split_by_history(docs[1:], docs, state, TODAY, 30)
    assert [d["doc_id"] for d in live] == ["N2688-2"]
    assert [d["doc_id"] for d in backlog] == ["N2688-1", "N2688-3", "N2688-4"]


def test_split_window_boundary_is_inclusive():
    docs = [_doc(0, "N2688", OLD), _doc(1, "N2688", "2026-09-21"), _doc(2, "N2688", "2026-09-20")]
    state = {"processed": {"N2688-0": {}}, "skipped": {}}
    live, backlog = w._split_by_history(docs[1:], docs, state, TODAY, 30)
    assert [d["doc_id"] for d in live] == ["N2688-1"]          # exactly 30 days: live
    assert [d["doc_id"] for d in backlog] == ["N2688-2"]       # 31 days: backlog


def test_split_preserves_order_and_tolerates_missing_srn():
    docs = [{"doc_id": "a", "date_filed": RECENT}, {"doc_id": "b", "facility_srn": "X", "date_filed": RECENT}]
    live, backlog = w._split_by_history(docs, docs, {"processed": {}, "skipped": {}}, TODAY, 30)
    assert live == [] and backlog == docs


# --- run() flow ----------------------------------------------------------------

def _wire(monkeypatch, docs, state, cap=25):
    cfg = {"anthropic_model": "m", "large_doc_page_threshold": 1,
           "large_doc_max_keyword_pages": 1, "classification_max_tokens": 1,
           "backfill_batch_size": 50,
           "watcher": {"max_new_docs_per_run": cap, "live_filing_window_days": 30,
                       "max_consecutive_deferrals": 3, "max_stale_days": 3}}
    state.setdefault("errors", {}); state.setdefault("skipped", {})
    state.setdefault("pending_digest", []); state.setdefault("pending_urgent_recap", [])
    state.setdefault("doc_pipeline_health", {})
    calls, sent = [], []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(w, "load_config", lambda: cfg)
    monkeypatch.setattr(w, "_today", lambda: TODAY)
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
    docs = [_doc(0, "N2688", OLD)] + [_doc(i, "N2688") for i in range(1, 4)]
    docs += [_doc(i, "WRD", OLD) for i in range(69)]
    state = {"processed": {"N2688-0": {}}}
    calls, sent = _wire(monkeypatch, docs, state)
    assert w.run() == 0
    assert calls == ["N2688-1", "N2688-2", "N2688-3"]           # live monitoring continues
    assert not any(c.startswith("WRD") for c in calls)           # the backlog waits for backfill
    out = capsys.readouterr().out
    assert "are backlog" in out and "WRD" in out
    h = state["doc_pipeline_health"]
    assert h["consecutive_deferrals"] == 1 and h["last_activity_at"]  # deferral still counted


def test_partly_backfilled_facility_keeps_its_historical_remainder_in_the_backlog(monkeypatch):
    # The hole the review found: facility A (69 historical docs) got ONE manual
    # backfill batch of 50, so it now has history, with 19 years-old filings
    # left; B (69) is untouched. 19 + 69 + N2688's 2 new > cap. A's remainder
    # must NOT be processed live (urgent alerts on years-old exceedances);
    # N2688's recent filings must.
    docs = [_doc(i, "A", OLD) for i in range(69)] + [_doc(i, "B", OLD) for i in range(69)]
    docs += [_doc(0, "N2688", OLD), _doc(1, "N2688"), _doc(2, "N2688")]
    processed = {f"A-{i}": {} for i in range(50)}
    processed["N2688-0"] = {}
    calls, sent = _wire(monkeypatch, docs, {"processed": processed})
    assert w.run() == 0
    assert calls == ["N2688-1", "N2688-2"]


def test_all_new_facility_backlog_is_deferred_as_before(monkeypatch):
    docs = [_doc(i, "WRD") for i in range(30)]
    calls, sent = _wire(monkeypatch, docs, {"processed": {}})
    assert w.run() == 0
    assert calls == []


def test_established_facility_burst_over_cap_is_still_deferred(monkeypatch, capsys):
    # A genuine burst from a monitored facility keeps today's conservative
    # behavior (deferred, and now alarmed after max_consecutive_deferrals).
    docs = [_doc(0, "N2688", OLD)] + [_doc(i, "N2688") for i in range(1, 31)]
    calls, sent = _wire(monkeypatch, docs, {"processed": {"N2688-0": {}}})
    assert w.run() == 0
    assert calls == []
    assert "deferring doc processing to the backfill job" in capsys.readouterr().out


def test_mixed_backlog_whose_live_share_exceeds_the_cap_defers_everything(monkeypatch):
    docs = [_doc(0, "N2688", OLD)] + [_doc(i, "N2688") for i in range(1, 28)]
    docs += [_doc(i, "WRD", OLD) for i in range(10)]
    calls, sent = _wire(monkeypatch, docs, {"processed": {"N2688-0": {}}})
    assert w.run() == 0
    assert calls == []


def test_under_cap_mixed_docs_are_all_processed_unchanged(monkeypatch):
    # Under the cap the split never runs: a brand-new small facility's docs and
    # an old-dated filing are processed by the watcher exactly as before.
    docs = [_doc(0, "N2688", OLD), _doc(1, "N2688"), _doc(2, "N2688", OLD)]
    docs += [_doc(i, "AHLI", OLD) for i in range(4)]
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
        docs += [_doc(i, srn, OLD) for i in range(n_hist)]
    processed = {d["doc_id"]: {} for d in docs}
    docs += [_doc(100, "N2688"), _doc(101, "N2688"), _doc(100, "RA")]   # 3 new filings
    docs += [_doc(i, "WRD", OLD) for i in range(69)] + [_doc(i, "AHLI", OLD) for i in range(4)]
    calls, sent = _wire(monkeypatch, docs, {"processed": processed})
    assert w.run() == 0
    assert sorted(calls) == ["N2688-100", "N2688-101", "RA-100"]


def test_live_config_carries_the_window():
    from config_loader import load_config
    assert load_config()["watcher"]["live_filing_window_days"] == 30
    assert w.DEFAULT_LIVE_FILING_WINDOW_DAYS == 30
    assert isinstance(datetime.now().date(), date)
