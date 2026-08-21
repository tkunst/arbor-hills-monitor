"""_route_urgent_or_digest: the if/else that decides whether a newly processed
document becomes a same-day [URGENT] email (recapped the next Sunday) or a
routine pending_digest entry. See docs/overnight-coder-handoffs/digest-urgent-recap.md.
"""
import watcher as w
from egle_doc_parser import ParsedDoc


def _parsed(severity="routine"):
    return ParsedDoc(
        summary="s", key_data_point="k", doc_type="evidence", risks=["R8"],
        severity=severity, full_text="", ocr_applied=False, page_count=1,
        measurements=[],
    )


def _doc_meta():
    return {"date_filed": "2026-08-15", "document_name": "Some Filing"}


def _fresh_state():
    return {"pending_digest": [], "pending_urgent_recap": []}


def test_urgent_success_appends_to_recap_only(monkeypatch):
    monkeypatch.setattr(w.ea, "is_urgent", lambda parsed, cfg: True)
    monkeypatch.setattr(w.ea, "send_urgent_alert", lambda parsed, d, link, cfg: None)
    writes = []
    monkeypatch.setattr(w.sw, "write_meta", lambda sheets, sheet_id, state: writes.append(dict(state)))

    state = _fresh_state()
    w._route_urgent_or_digest(_parsed("urgent"), _doc_meta(), "http://x", {}, state, object(), "SID")

    assert len(state["pending_urgent_recap"]) == 1
    assert state["pending_digest"] == []
    rec = state["pending_urgent_recap"][0]
    assert rec["document_name"] == "Some Filing"
    assert rec["urgent_sent_at"]  # non-empty timestamp
    assert len(writes) == 1  # write_meta called exactly once, on the success path


def test_routine_doc_appends_to_digest_only(monkeypatch):
    monkeypatch.setattr(w.ea, "is_urgent", lambda parsed, cfg: False)
    writes = []
    monkeypatch.setattr(w.sw, "write_meta", lambda sheets, sheet_id, state: writes.append(dict(state)))

    state = _fresh_state()
    w._route_urgent_or_digest(_parsed("routine"), _doc_meta(), "http://x", {}, state, object(), "SID")

    assert len(state["pending_digest"]) == 1
    assert state["pending_urgent_recap"] == []
    assert len(writes) == 1


def test_failed_urgent_send_queues_neither(monkeypatch):
    # Matches today's existing behavior: an urgent send that never went out
    # doesn't get queued to pending_digest either — nothing to recap.
    monkeypatch.setattr(w.ea, "is_urgent", lambda parsed, cfg: True)

    def _boom(parsed, d, link, cfg):
        raise RuntimeError("SMTP down")
    monkeypatch.setattr(w.ea, "send_urgent_alert", _boom)
    writes = []
    monkeypatch.setattr(w.sw, "write_meta", lambda sheets, sheet_id, state: writes.append(dict(state)))

    state = _fresh_state()
    w._route_urgent_or_digest(_parsed("urgent"), _doc_meta(), "http://x", {}, state, object(), "SID")

    assert state["pending_digest"] == []
    assert state["pending_urgent_recap"] == []
    assert writes == []  # write_meta never reached on the failure path
