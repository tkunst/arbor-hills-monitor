"""Issue #82: a poison doc is stubbed + marked 'skipped' on its terminal
(MAX_ERRORS_PER_DOC-th) failure no matter which job — watcher.py or backfill.py —
caused it. Before the fix only backfill.py had the stub block, so a doc whose 3rd
strike came from the watcher was silently parked by both jobs' poison gates.

The runs below go through the REAL sw.read_state / mark_error / mark_skipped
reduction over a fake append-only _state log, so "later runs" see exactly the
state a real Sheet would hand back.
"""
from datetime import date

import pytest

import backfill as bf
import poison_stub
import sheet_writer as sw
import watcher as w

ME = poison_stub.MAX_ERRORS_PER_DOC
DID = "1320691490590903667"
DOC = {"doc_id": DID, "document_name": "Application - Digital EGLE/USACE JPA",
       "date_filed": "2026-08-24", "facility_srn": "N2688"}
CFG = {"anthropic_model": "m", "large_doc_page_threshold": 50,
       "large_doc_max_keyword_pages": 10, "classification_max_tokens": 100,
       "backfill_batch_size": 50}


class FakeSheet:
    """In-memory _state event log + a record of stub rows written per tab."""

    def __init__(self):
        self.state_rows = []
        self.stubs = []

    def install(self, mp):
        mp.setattr(sw, "_append_state_row",
                   lambda svc, sid, row: self.state_rows.append(list(row)))
        mp.setattr(sw, "_tab_rows", lambda svc, sid, tab, a1:
                   [list(r) for r in self.state_rows] if tab == sw.TAB_STATE else [])
        mp.setattr(sw, "read_meta", lambda svc, sid:
                   {"pending_digest": [], "pending_urgent_recap": []})
        mp.setattr(sw, "write_stub_row",
                   lambda svc, sid, d, link, reason, feed_tab=sw.TAB_HISTORICAL:
                   self.stubs.append({"doc_id": d["doc_id"], "link": link,
                                      "reason": reason, "tab": feed_tab}))

    def statuses(self):
        return [r[1] for r in self.state_rows if r[0] == DID]


@pytest.fixture
def sheet(monkeypatch):
    fake = FakeSheet()
    fake.install(monkeypatch)
    monkeypatch.setenv("GSHEET_ID", "SID")
    for var in ("RETRY_POISONED", "RETRY_DOC_IDS", "FORCE_REPROCESS_DOC_IDS",
                "FORCE_REPROCESS_APPLY"):
        monkeypatch.delenv(var, raising=False)
    downloads = []

    def _fail(session, d, local):
        downloads.append(d["doc_id"])
        raise ValueError("File does not contain a property stream")

    for mod in (w, bf):
        monkeypatch.setattr(mod, "load_config", lambda: dict(CFG))
        monkeypatch.setattr(mod.dc, "sheets_service", lambda: object())
        monkeypatch.setattr(mod.sw, "ensure_tabs", lambda svc, sid: None)
        monkeypatch.setattr(mod.sw, "rebuild_risk_register_tab", lambda *a, **k: None)
        monkeypatch.setattr(mod.nc, "make_session", lambda: object())
        monkeypatch.setattr(mod.nc, "fetch_all_documents", lambda s, c: [dict(DOC)])
        monkeypatch.setattr(mod.nc, "download_pdf", _fail)
    monkeypatch.setattr(w.sw, "write_meta", lambda svc, sid, st: None)
    monkeypatch.setattr(w, "_today", lambda: date(2026, 9, 21))  # a Monday: no digest
    fake.downloads = downloads
    return fake


def test_watcher_third_strike_writes_stub_once_and_marks_skipped(sheet):
    for _ in range(ME - 1):
        assert w.run() == 0
    assert sheet.stubs == []  # below threshold: strikes only
    assert sheet.statuses() == ["error"] * (ME - 1)

    assert w.run() == 0  # the watcher causes the terminal strike
    assert len(sheet.stubs) == 1
    stub = sheet.stubs[0]
    assert stub["doc_id"] == DID
    assert stub["tab"] == sw.TAB_NEW  # the watcher's own feed tab
    assert stub["link"].endswith(f"/{DID}")  # native downloadfile link
    assert f"after {ME} attempts" in stub["reason"]
    assert sheet.statuses() == ["error"] * ME + ["skipped"]

    state = sw.read_state(object(), "SID")
    assert DID in state["skipped"]
    assert DID not in state["errors"]


def test_no_double_stub_on_later_watcher_or_backfill_runs(sheet):
    for _ in range(ME):
        w.run()
    assert len(sheet.stubs) == 1
    n_downloads = len(sheet.downloads)
    n_rows = len(sheet.state_rows)

    w.run()
    w.run()
    assert bf.run() == 0
    assert len(sheet.stubs) == 1  # still exactly one stub
    assert len(sheet.downloads) == n_downloads  # skipped: never re-attempted
    assert len(sheet.state_rows) == n_rows  # no new error/skipped events


def test_mixed_jobs_stub_whichever_causes_the_third_strike(sheet):
    # Two strikes from backfill, the terminal one from the watcher.
    for _ in range(ME - 1):
        bf.run()
    assert sheet.stubs == []
    w.run()
    assert [s["tab"] for s in sheet.stubs] == [sw.TAB_NEW]
    assert DID in sw.read_state(object(), "SID")["skipped"]


def test_backfill_third_strike_behavior_unchanged(sheet):
    for _ in range(ME):
        assert bf.run() == 0
    assert len(sheet.stubs) == 1
    stub = sheet.stubs[0]
    assert stub["tab"] == sw.TAB_HISTORICAL  # backfill's default feed tab, as before
    assert stub["reason"].startswith(f"Source not classifiable after {ME} attempts: ")
    assert sheet.statuses() == ["error"] * ME + ["skipped"]
    skipped_payload = sw.read_state(object(), "SID")["skipped"][DID]
    assert skipped_payload["document_name"] == DOC["document_name"]
    assert skipped_payload["date_filed"] == DOC["date_filed"]

    bf.run()
    assert len(sheet.stubs) == 1


def test_transient_error_never_strikes_or_stubs(sheet, monkeypatch):
    def _capped(session, d, local):
        raise RuntimeError("You have reached your specified workspace API usage limits")

    monkeypatch.setattr(w.nc, "download_pdf", _capped)
    for _ in range(ME + 1):
        w.run()
    assert sheet.stubs == []
    assert sheet.statuses() == []


def test_helper_below_threshold_or_already_skipped_is_noop(sheet):
    state = {"skipped": {}, "errors": {DID: ME - 1}}
    assert poison_stub.stub_if_poisoned(object(), "SID", state, DOC, ME - 1,
                                        ValueError("x"), "t") is False
    state = {"skipped": {DID: {}}, "errors": {}}
    assert poison_stub.stub_if_poisoned(object(), "SID", state, DOC, ME,
                                        ValueError("x"), "t") is False
    assert sheet.stubs == [] and sheet.state_rows == []


def test_helper_write_failure_is_swallowed_and_leaves_doc_unskipped(sheet, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("sheets down")

    monkeypatch.setattr(sw, "write_stub_row", _boom)
    state = {"skipped": {}, "errors": {DID: ME}}
    assert poison_stub.stub_if_poisoned(object(), "SID", state, DOC, ME,
                                        ValueError("x"), "t") is False
    assert state == {"skipped": {}, "errors": {DID: ME}}
    assert sheet.state_rows == []


def test_max_errors_constant_is_shared():
    assert w.MAX_ERRORS_PER_DOC == bf.MAX_ERRORS_PER_DOC == poison_stub.MAX_ERRORS_PER_DOC
