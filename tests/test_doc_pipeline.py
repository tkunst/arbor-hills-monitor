"""doc_pipeline: the one per-document pipeline behind watcher.run and
backfill.run (consolidation PR 3). The end-to-end behavior of both jobs is
pinned by their existing tests (test_watcher, test_backfill, test_facility_scope,
test_poison_stub); these cover the Job switches themselves."""
from types import SimpleNamespace

import pytest

import doc_pipeline as dp
import sheet_writer as sw


def _parsed():
    return SimpleNamespace(doc_type="evidence", severity="routine", risks=["R8"], display_title="",
                           document_date="", measurements=[], deadlines=[{"due_date": "x"}],
                           key_data_point="", summary="", full_text="")


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(dp.nc, "download_pdf", lambda *a: None)
    monkeypatch.setattr(dp.av, "mirror_one_now", lambda *a, **k: "LINK")
    monkeypatch.setattr(dp.woi_router, "route_measurements", lambda *a, **k: None)
    monkeypatch.setattr(dp.sw, "write_document", lambda *a, **k: log.append(("doc", k["feed_tab"])))
    monkeypatch.setattr(dp.sw, "write_compliance_deadlines", lambda *a, **k: log.append("deadlines"))
    monkeypatch.setattr(dp.sw, "mark_processed", lambda *a, **k: log.append("state"))
    monkeypatch.setattr(dp.rf, "log_for_document", lambda *a, **k: log.append("flags"))
    monkeypatch.setattr(dp.on, "alert_review_flags", lambda *a, **k: log.append("flag-alert"))
    return log


def _ctx():
    return dp.RunContext(cfg={"large_doc_page_threshold": 30, "large_doc_max_keyword_pages": 10,
                              "classification_max_tokens": 10},
                         sheets=None, sheet_id="SID", session=None, model="m", well_alias_map=None,
                         parse=lambda *a, **k: _parsed(), now=lambda: "T")


def _doc():
    return {"doc_id": "1", "document_name": "Doc", "date_filed": "2026-10-16", "facility_scope": "core"}


def test_watcher_job_runs_its_extra_steps_in_order(calls, tmp_path):
    alerts = []
    job = dp.Job(name="watcher", feed_tab=sw.TAB_NEW, payload=lambda p, d: {}, write_deadlines=True,
                 alert=lambda p, d, link: alerts.append(link) or calls.append("alert"),
                 review_flag_alert=True)
    state = {"processed": {}, "errors": {}, "skipped": {}}
    assert dp.process_one_doc(_ctx(), state, _doc(), job, str(tmp_path / "x.pdf")) == "processed"
    assert calls == [("doc", sw.TAB_NEW), "deadlines", "alert", "flags", "flag-alert", "state"]


def test_backfill_job_never_alerts(calls, tmp_path):
    job = dp.Job(name="backfill", feed_tab=sw.TAB_HISTORICAL, payload=lambda p, d: {},
                 clear_errors_on_success=True)
    state = {"processed": {}, "errors": {"1": 2}, "skipped": {}}
    assert dp.process_one_doc(_ctx(), state, _doc(), job, str(tmp_path / "x.pdf")) == "processed"
    assert calls == [("doc", sw.TAB_HISTORICAL), "flags", "state"]
    assert "1" not in state["errors"]


def test_local_file_removed_even_on_failure(monkeypatch, tmp_path):
    local = tmp_path / "x.pdf"
    local.write_bytes(b"%PDF")
    monkeypatch.setattr(dp.nc, "download_pdf", lambda *a: (_ for _ in ()).throw(ConnectionError("x")))
    monkeypatch.setattr(dp.rp, "is_transient", lambda e: True)
    job = dp.Job(name="backfill", feed_tab=sw.TAB_HISTORICAL, payload=lambda p, d: {})
    state = {"processed": {}, "errors": {}, "skipped": {}}
    assert dp.process_one_doc(_ctx(), state, _doc(), job, str(local)) == "retry"
    assert not local.exists() and state["errors"] == {}
