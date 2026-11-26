"""scripts/send_scoped_test.py: one-address test sends, incl. the digest preview."""
import importlib.util
import os
from datetime import date

import pytest

import email_alerts as ea

_spec = importlib.util.spec_from_file_location(
    "send_scoped_test", os.path.join(os.path.dirname(__file__), "..", "scripts", "send_scoped_test.py"))
sst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sst)

CFG = {"unsubscribe": {"mailto": "unsub@x.org", "owner_addresses": ["owner@x.org"]},
       "digest_new_pages": {"enabled": True, "sites": [{"key": "monitor", "label": "m", "sitemap": "s"}]}}
STATE = {"pending_digest": [{
    "date_filed": "2026-11-20", "document_date": "2026-09-25", "document_name": "RCA",
    "doc_type": "evidence", "severity": "notable", "risks": ["R9"], "key_data_point": "k",
    "link": "https://drive.google.com/x"}], "pending_urgent_recap": []}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("MONITOR_OWNER_EMAILS", "UNSUBSCRIBED_EMAILS"):
        monkeypatch.delenv(k, raising=False)


def test_next_sunday():
    assert sst.next_sunday(date(2026, 11, 26)) == date(2026, 11, 29)   # Thursday
    assert sst.next_sunday(date(2026, 11, 29)) == date(2026, 11, 29)   # Sunday itself


def test_preview_for_owner_shows_subscriber_footer_once():
    subj, text, html = sst.build_digest_preview(STATE, CFG, "owner@x.org", [], date(2026, 11, 26))
    assert subj == "[TEST] Arbor Hills N2688 digest: 1 new document(s)"
    assert text.count("To unsubscribe") == 1 and html.count("To unsubscribe") == 1
    assert "Sunday, November 29, 2026" in text
    assert "R9" not in text and "R9" not in html


def test_preview_for_non_owner_leaves_footer_to_send_email():
    _s, text, html = sst.build_digest_preview(STATE, CFG, "ally@y.org", [], date(2026, 11, 26))
    assert "To unsubscribe" not in text and "To unsubscribe" not in html


def test_run_rejects_more_than_one_address(monkeypatch):
    monkeypatch.setenv("SCOPED_TEST_RECIPIENT", "a@x.org, b@x.org")
    monkeypatch.setattr(ea, "send_email", lambda *a, **k: pytest.fail("must not send"))
    assert sst.run() == 1


def test_digest_mode_is_read_only_and_sends_to_exactly_one(monkeypatch):
    import sheet_writer as sw
    import drive_client as dc
    import site_pages
    monkeypatch.setenv("SCOPED_TEST_RECIPIENT", "owner@x.org")
    monkeypatch.setenv("SCOPED_TEST_KIND", "digest")
    monkeypatch.setenv("GSHEET_ID", "sheet")
    monkeypatch.setattr(sst, "load_config", lambda: CFG)
    monkeypatch.setattr(dc, "sheets_service", lambda: object())
    monkeypatch.setattr(sw, "read_meta", lambda svc, sid: dict(STATE))
    monkeypatch.setattr(sw, "write_meta", lambda *a, **k: pytest.fail("preview must not write _meta"))
    monkeypatch.setattr(site_pages, "find_new_pages", lambda cfg, base: ([], {"monitor": ["new"]}))
    sent = []
    monkeypatch.setattr(ea, "send_email", lambda subj, body, cfg, recipients=None, **k: sent.append(
        (subj, recipients, k.get("html") is not None)) or True)
    assert sst.run() == 0
    assert sent == [("[TEST] Arbor Hills N2688 digest: 1 new document(s)", ["owner@x.org"], True)]
