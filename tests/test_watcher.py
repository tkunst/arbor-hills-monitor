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
    monkeypatch.setattr(w.ea, "send_urgent_alert", lambda parsed, d, link, cfg: True)
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


def test_urgent_send_skipped_smtp_not_configured_queues_neither(monkeypatch):
    # send_urgent_alert can return False (no exception) when SMTP is
    # unconfigured or has no recipients -- send_email() silently no-ops rather
    # than raising. That must NOT be recorded as "sent": nothing actually went
    # out, so there is nothing to recap, same as the exception path above.
    monkeypatch.setattr(w.ea, "is_urgent", lambda parsed, cfg: True)
    monkeypatch.setattr(w.ea, "send_urgent_alert", lambda parsed, d, link, cfg: False)
    writes = []
    monkeypatch.setattr(w.sw, "write_meta", lambda sheets, sheet_id, state: writes.append(dict(state)))

    state = _fresh_state()
    w._route_urgent_or_digest(_parsed("urgent"), _doc_meta(), "http://x", {}, state, object(), "SID")

    assert state["pending_digest"] == []
    assert state["pending_urgent_recap"] == []
    assert writes == []  # write_meta never reached on the skipped-send path


# --- ADR 065: _digest_record's is_correspondence computation ---------------

def _parsed_with_text(full_text, severity="routine"):
    return ParsedDoc(
        summary="s", key_data_point="k", doc_type="evidence", risks=["R8"],
        severity=severity, full_text=full_text, ocr_applied=False, page_count=1,
        measurements=[],
    )


_EGLE_LETTER_TEXT = (
    "Michigan.gov/EGLE • 800-662-9278\nAugust 27, 2026\n"
    "We approve the requested 120-day extension."
)


def test_digest_record_is_correspondence_true_when_generic_and_letter():
    d = dict(_doc_meta(), title_was_generic=True)
    rec = w._digest_record(_parsed_with_text(_EGLE_LETTER_TEXT), d, "http://x")
    assert rec["is_correspondence"] is True


def test_digest_record_is_correspondence_false_when_not_generic():
    # The text reads as an EGLE letter, but the title wasn't flagged generic
    # -- real, specific titles don't need the rescue.
    d = dict(_doc_meta(), title_was_generic=False)
    rec = w._digest_record(_parsed_with_text(_EGLE_LETTER_TEXT), d, "http://x")
    assert rec["is_correspondence"] is False


def test_digest_record_is_correspondence_false_when_generic_but_not_a_letter():
    d = dict(_doc_meta(), title_was_generic=True)
    rec = w._digest_record(_parsed_with_text("Just a routine stormwater table."), d, "http://x")
    assert rec["is_correspondence"] is False


def test_digest_record_missing_title_was_generic_key_defaults_false():
    rec = w._digest_record(_parsed_with_text(_EGLE_LETTER_TEXT), _doc_meta(), "http://x")
    assert rec["is_correspondence"] is False


def test_record_to_item_carries_is_correspondence_through():
    rec = {"doc_type": "evidence", "severity": "routine", "risks": [],
           "key_data_point": "k", "date_filed": "2026-08-27",
           "document_name": "EGLE letter", "link": "http://x",
           "is_correspondence": True}
    item = w._record_to_item(rec)
    assert item["parsed"].is_correspondence is True


# --- ADR 065: _processed_payload carries document_date/egle_title ----------

def test_processed_payload_carries_document_date_and_egle_title():
    d = {"document_name": "EGLE letter: HOV renewal",
         "egle_title": "Schedule - Air General Compliance Report",
         "document_date": "2026-08-27", "date_filed": "2026-08-26"}
    payload = w._processed_payload(_parsed(), d)
    assert payload["document_date"] == "2026-08-27"
    assert payload["egle_title"] == "Schedule - Air General Compliance Report"


def test_processed_payload_egle_title_falls_back_to_document_name():
    d = {"document_name": "Violation Notice", "date_filed": "2026-07-08"}
    payload = w._processed_payload(_parsed(), d)
    assert payload["document_date"] == ""
    assert payload["egle_title"] == "Violation Notice"


def test_write_meta_failure_after_successful_send_propagates_not_swallowed(monkeypatch):
    # A write_meta failure AFTER a successful send must NOT be caught by the
    # same except that guards send_urgent_alert — that would mislabel a real
    # send as "FAILED to send" and let the doc reach mark_processed with the
    # recap entry only ever having existed in memory. It must propagate to
    # the caller's outer per-doc try/except instead, same as the routine
    # branch's write_meta call already does today.
    monkeypatch.setattr(w.ea, "is_urgent", lambda parsed, cfg: True)
    monkeypatch.setattr(w.ea, "send_urgent_alert", lambda parsed, d, link, cfg: True)

    def _write_meta_boom(sheets, sheet_id, state):
        raise RuntimeError("Sheets API 500")
    monkeypatch.setattr(w.sw, "write_meta", _write_meta_boom)

    state = _fresh_state()
    try:
        w._route_urgent_or_digest(_parsed("urgent"), _doc_meta(), "http://x", {}, state, object(), "SID")
        raised = False
    except RuntimeError:
        raised = True

    assert raised, "write_meta failure after a successful send must propagate, not be swallowed"
    # The append already happened in memory before write_meta was called —
    # a later successful write_meta in the same run (e.g. the next doc, or
    # the unconditional end-of-run write) can still persist it.
    assert len(state["pending_urgent_recap"]) == 1
