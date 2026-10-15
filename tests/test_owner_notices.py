"""owner_notices: the owner-only weekly email and same-day owner alerts
(coder:owner-weekly-email). Hermetic: Sheets and SMTP are faked."""
from datetime import date
from unittest import mock

import pytest

import email_alerts as ea
import owner_notices as on
import poison_stub
import review_flags as rf
import sheet_writer as sw

OWNERS = {"owner@example.org"}
CFG_ON = {"owner_weekly": {"enabled": True, "enforcement_alert_enabled": True,
                           "hand_curated_since": "2026-10-15"},
          "alert_recipients": ["list@example.org"],
          "temperature_thresholds": {"max_plausible_wellhead_f": 250}}
CFG_OFF = {"owner_weekly": {"enabled": False, "enforcement_alert_enabled": False}}


@pytest.fixture
def owners(monkeypatch):
    monkeypatch.setattr(ea, "load_owner_emails", lambda cfg=None: set(OWNERS))


@pytest.fixture
def sends(monkeypatch):
    calls = []

    def fake_send(subject, body, cfg, recipients=None, log_subject=True):
        calls.append({"subject": subject, "body": body, "recipients": recipients,
                      "log_subject": log_subject})
        return True
    monkeypatch.setattr(ea, "send_email", fake_send)
    return calls


@pytest.fixture
def notices(monkeypatch):
    rows = []
    monkeypatch.setattr(rf, "private_sheet_for_write", lambda svc: ("PRIV", ""))
    monkeypatch.setattr(on.sw, "ensure_tab", lambda *a: None)
    monkeypatch.setattr(on.sw, "append_rows",
                        lambda svc, sid, tab, r: rows.extend((sid, tab, list(x)) for x in r))
    return rows


# --- recipients -----------------------------------------------------------

def test_owner_email_goes_to_owner_addresses_only(owners, sends):
    assert on.send_owner_email("s", "b", CFG_ON) is True
    assert sends[0]["recipients"] == sorted(OWNERS)
    assert "list@example.org" not in sends[0]["recipients"]
    assert sends[0]["log_subject"] is False


def test_empty_owner_set_sends_nothing(monkeypatch, sends):
    monkeypatch.setattr(ea, "load_owner_emails", lambda cfg=None: set())
    assert on.send_owner_email("s", "b", CFG_ON) is False
    assert sends == []


def test_send_email_can_keep_subject_out_of_the_log(monkeypatch, capsys):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    ea.send_email("Secret Title", "b", {}, recipients=["x@example.org"], log_subject=False)
    out = capsys.readouterr().out
    assert "Secret Title" not in out and "owner-only email" in out


# --- section C: unreadable enforcement notice ------------------------------

META = {"doc_id": "123", "document_name": "Violation Notice", "egle_title": "Violation Notice",
        "date_filed": "2026-10-14", "facility_name": "Arbor Hills Landfill"}


def test_enforcement_alert_fires_only_on_an_enforcement_title(owners, sends, notices):
    assert on.alert_unreadable_enforcement(None, META, "L", CFG_ON, "t", 3) is True
    assert sends[0]["subject"].startswith("[URGENT, Trisha only] Enforcement notice filed")
    assert "Violation Notice" in sends[0]["subject"] and "after 3 attempts" in sends[0]["body"]
    assert [(r[1], r[2][1]) for r in notices] == [(on.TAB_OWNER_NOTICES, on.KIND_ENFORCEMENT_ALERT)]

    other = dict(META, egle_title="Semi-Annual Report", document_name="Semi-Annual Report")
    assert on.alert_unreadable_enforcement(None, other, "L", CFG_ON, "t", 3) is None
    assert len(sends) == 1


def test_enforcement_alert_gate_off(owners, sends, notices):
    assert on.alert_unreadable_enforcement(None, META, "L", CFG_OFF, "t", 3) is None
    assert on.alert_unreadable_enforcement(None, META, "L", None, "t", 3) is None
    assert sends == [] and notices == []


def test_failed_enforcement_send_is_recorded_not_raised(owners, monkeypatch, notices):
    monkeypatch.setattr(ea, "send_email", mock.Mock(side_effect=ea.EmailSendError("x")))
    assert on.alert_unreadable_enforcement(None, META, "L", CFG_ON, "t", 3) is False
    assert notices[0][2][1] == on.KIND_SEND_FAILED
    assert "enforcement alert: raised" in notices[0][2][4]


def test_poison_stub_order_is_stub_state_then_email(monkeypatch, owners):
    order = []
    monkeypatch.setattr(sw, "write_stub_row", lambda *a, **k: order.append("stub"))
    monkeypatch.setattr(sw, "mark_skipped", lambda *a, **k: order.append("state"))
    monkeypatch.setattr(ea, "send_email", lambda *a, **k: order.append("email") or True)
    monkeypatch.setattr(on, "record_notice", lambda *a, **k: True)
    state = {"skipped": {}, "errors": {"123": 3}}
    d = dict(META)
    d.pop("egle_title")
    assert poison_stub.stub_if_poisoned(None, "SID", state, d, 3, ValueError("bad"), "t",
                                        cfg=CFG_ON) is True
    assert order == ["stub", "state", "email"]
    assert "123" in state["skipped"]


def test_failed_send_never_blocks_stubbing(monkeypatch, owners):
    monkeypatch.setattr(sw, "write_stub_row", lambda *a, **k: None)
    payloads = []
    monkeypatch.setattr(sw, "mark_skipped", lambda svc, sid, did, p, now: payloads.append(p))

    def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(ea, "send_email", boom)
    monkeypatch.setattr(on, "record_notice", lambda *a, **k: True)
    state = {"skipped": {}, "errors": {}}
    assert poison_stub.stub_if_poisoned(None, "SID", state, dict(META), 3, KeyError("k"), "t",
                                        cfg=CFG_ON) is True
    assert "123" in state["skipped"]
    assert payloads[0]["error_class"] == "KeyError"
    assert payloads[0]["egle_title"] == "Violation Notice"


def test_stub_without_cfg_sends_nothing(monkeypatch, sends):
    monkeypatch.setattr(sw, "write_stub_row", lambda *a, **k: None)
    monkeypatch.setattr(sw, "mark_skipped", lambda *a, **k: None)
    poison_stub.stub_if_poisoned(None, "SID", {"skipped": {}, "errors": {}}, dict(META), 3,
                                 ValueError("x"), "t")
    assert sends == []


# --- review-flag same-day alert (woi-layout-guard flags) --------------------

def test_review_flag_alert_only_for_temperature_flags(owners, sends, notices):
    flags = [rf.flag(rf.KIND_WOI_TEMP, value_f=526.8, well_id="AHWW0402",
                     reading="8/3/2023 11:29", pages="19", detail="d"),
             rf.flag(rf.KIND_REJECTED_TABLE, pages="5-9", detail="r")]
    assert on.alert_review_flags(None, flags, META, "L", CFG_ON, "t") is True
    assert "526.8F" in sends[0]["body"] and "AHWW0402" in sends[0]["body"]
    assert "5-9" not in sends[0]["body"]
    assert sends[0]["subject"].startswith("[Trisha only] Needs review: value above 250F")
    assert on.alert_review_flags(None, flags[1:], META, "L", CFG_ON, "t") is None
    assert on.alert_review_flags(None, flags, META, "L", CFG_OFF, "t") is None
    assert len(sends) == 1


# --- weekly: content -----------------------------------------------------------

def _hc(title, added, link="https://drive.google.com/file/d/X/view", origin="https://src"):
    row = [""] * 13
    row[0], row[1], row[7], row[9], row[10] = f"{title}.pdf", title, origin, link, added
    return row


def test_unrouted_respects_cutoff_annotation_and_routing_log():
    rows = [_hc("Old", "2026-10-01T00:00:00"),                       # before cutoff
            _hc("Routed", "2026-10-16T00:00:00", link="https://drive.google.com/file/d/R/view"),
            _hc("Annot", "2026-10-16T00:00:00", link="https://egle.example/x"),  # annotation
            _hc("Same", "2026-10-16T00:00:00", link="https://s", origin="https://s"),
            _hc("ByName", "2026-10-16T00:00:00", link="https://drive.google.com/file/d/N/view"),
            _hc("Open", "2026-10-17 09:00")]
    routing = [["https://drive.google.com/file/d/R/view", "Routed", "routine", "t"],
               ["https://elsewhere", "byname", "routine", "t"]]
    out = on.unrouted_hand_curated(rows, routing, "2026-10-15")
    assert [r[1] for r in out] == ["Open"]


def test_weekly_body_sections_and_omission():
    skipped = [("123", "2026-10-16T01:00:00Z",
                {"egle_title": "Violation Notice", "date_filed": "2026-10-10",
                 "facility": "Arbor Hills Landfill", "error_class": "FileDataError"})]
    body = on.build_weekly_body(skipped, [], [], [], "2026-10-11")
    assert "1. COULD NOT BE READ (1)" in body and "FileDataError" in body
    assert "downloadfile" in body or "/123" in body
    assert "2. HAND-CURATED" not in body and "3. ALERTS" not in body and "4. PARSER" not in body
    unrouted = [_hc("Open", "2026-10-17")]
    body = on.build_weekly_body([], unrouted, None, None, "2026-10-11")
    assert "2. HAND-CURATED ROWS NOT YET ROUTED (1)" in body
    assert "gh workflow run route-hand-curated.yml -f docs_json=" in body
    failed = [{"Logged At": "2026-10-16T02:00:00Z", "Detail": "urgent alert: raised SMTPException",
               "Document Name": "X"}]
    flags = [{"Kind": "woi_temp_above_ceiling", "Value (F)": "526.8", "Well ID": "AHWW0402",
              "Pages": "19", "Document Name": "Q3", "Detail": "d", "Link": "L"}]
    body = on.build_weekly_body([], [], failed, flags, "2026-10-11")
    assert "3. ALERTS THAT FAILED TO SEND (1)" in body and "4. PARSER REVIEW FLAGS (1)" in body


def test_no_email_when_every_section_is_empty():
    assert on.build_weekly_body([], [], [], [], "2026-10-11") is None
    assert on.build_weekly_body([], [], None, None, "2026-10-11") is None


# --- weekly: send + window -----------------------------------------------------

def _wire_weekly(monkeypatch, skipped=(), hc=(), routing=(), failed=(), flags=()):
    monkeypatch.setattr(sw, "state_events_since", lambda svc, sid, st, since: list(skipped))
    monkeypatch.setattr(sw, "_tab_rows_strict",
                        lambda svc, sid, tab, a1: list(hc) if tab == sw.TAB_HANDCURATED
                        else list(routing))
    monkeypatch.setattr(rf, "private_sheet_for_write", lambda svc: ("PRIV", ""))
    monkeypatch.setattr(on, "notices_since", lambda *a, **k: list(failed))
    monkeypatch.setattr(rf, "flags_since", lambda *a, **k: list(flags))
    metas = []
    monkeypatch.setattr(sw, "write_meta", lambda svc, sid, st: metas.append(dict(st)))
    return metas


SKIP = [("123", "2026-10-16T01:00:00Z", {"document_name": "VN", "date_filed": "2026-10-10"})]


def test_weekly_sends_and_advances_window(monkeypatch, owners, sends):
    metas = _wire_weekly(monkeypatch, skipped=SKIP)
    state = {"last_owner_weekly": "2026-10-11T00:00:00Z"}
    assert on.send_weekly(None, "SID", state, CFG_ON, date(2026, 10, 18), "NOW") == "sent"
    assert sends[0]["subject"] == on.WEEKLY_SUBJECT and sends[0]["recipients"] == sorted(OWNERS)
    assert state["last_owner_weekly"] == "NOW" and metas


def test_weekly_quiet_week_sends_nothing_but_advances(monkeypatch, owners, sends):
    _wire_weekly(monkeypatch)
    state = {}
    assert on.send_weekly(None, "SID", state, CFG_ON, date(2026, 10, 18), "NOW") == "nothing to send"
    assert sends == [] and state["last_owner_weekly"] == "NOW"


def test_weekly_failed_send_keeps_window_and_records(monkeypatch, owners, notices):
    _wire_weekly(monkeypatch, skipped=SKIP)
    monkeypatch.setattr(ea, "send_email", mock.Mock(side_effect=RuntimeError("down")))
    state = {"last_owner_weekly": "2026-10-11T00:00:00Z"}
    out = on.send_weekly(None, "SID", state, CFG_ON, date(2026, 10, 18), "NOW")
    assert out.startswith("failed")
    assert state["last_owner_weekly"] == "2026-10-11T00:00:00Z"
    assert notices[0][2][1] == on.KIND_SEND_FAILED


def test_weekly_disabled_reads_nothing(monkeypatch, sends):
    monkeypatch.setattr(sw, "state_events_since", mock.Mock(side_effect=AssertionError))
    assert on.send_weekly(None, "SID", {}, CFG_OFF, date(2026, 10, 18), "NOW") == "disabled"


def test_weekly_read_failure_raises_not_all_clear(monkeypatch, owners, sends):
    _wire_weekly(monkeypatch)
    monkeypatch.setattr(sw, "state_events_since", mock.Mock(side_effect=OSError("429")))
    with pytest.raises(OSError):
        on.send_weekly(None, "SID", {}, CFG_ON, date(2026, 10, 18), "NOW")
    assert sends == []


def test_weekly_first_window_is_seven_days():
    assert on.weekly_since({}, date(2026, 10, 18)) == "2026-10-11"
    assert on.weekly_since({"last_owner_weekly": "X"}, date(2026, 10, 18)) == "X"


# --- state_events_since --------------------------------------------------------

def test_state_events_since_skipped_excludes_later_processed(monkeypatch):
    rows = [["a", "skipped", 0, "2026-10-16T00:00:00Z", '{"document_name": "A"}'],
            ["b", "skipped", 0, "2026-10-01T00:00:00Z", "{}"],
            ["c", "skipped", 0, "2026-10-17T00:00:00Z", "{}"],
            ["c", "processed", 0, "2026-10-18T00:00:00Z", "{}"]]
    monkeypatch.setattr(sw, "_tab_rows_strict", lambda *a: rows)
    got = sw.state_events_since(None, "SID", "skipped", "2026-10-11")
    assert [(d, p.get("document_name")) for d, _, p in got] == [("a", "A")]


# --- record_notice ------------------------------------------------------------

def test_record_notice_refuses_without_private_sheet(monkeypatch, capsys):
    monkeypatch.setattr(rf, "private_sheet_for_write", lambda svc: (None, "no private Sheet configured"))
    monkeypatch.setattr(on.sw, "append_rows", mock.Mock(side_effect=AssertionError))
    assert on.record_notice(None, on.KIND_SEND_FAILED, META, "x", "L", "t") is False
    assert "no private Sheet" in capsys.readouterr().out


def test_record_notice_never_raises(monkeypatch, capsys):
    monkeypatch.setattr(rf, "private_sheet_for_write", mock.Mock(side_effect=OSError("x https://s")))
    assert on.record_notice(None, on.KIND_SEND_FAILED, META, "x", "L", "t") is False
    out = capsys.readouterr().out
    assert "OSError" in out and "https" not in out


# --- route_hand_curated writes the routing log before write_meta --------------

def test_route_hand_curated_writes_routing_log_before_meta(monkeypatch):
    import json
    import importlib
    rhc = importlib.import_module("scripts.route_hand_curated")
    order = []
    monkeypatch.setenv("HAND_CURATED_ROUTE_JSON", json.dumps([{
        "document_name": "Doc", "date_filed": "2026-10-16", "doc_type": "evidence",
        "severity": "routine", "risks": [], "key_data_point": "k",
        "link": "https://drive.google.com/file/d/Z/view"}]))
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rhc, "load_config", lambda: {})
    monkeypatch.setattr(rhc.dc, "sheets_service", lambda: None)
    monkeypatch.setattr(rhc.sw, "read_meta", lambda svc, sid: {"pending_digest": [],
                                                              "pending_urgent_recap": []})
    monkeypatch.setattr(rhc.ea, "route_hand_curated_urgent_or_digest", lambda **k: False)
    monkeypatch.setattr(rhc.sw, "ensure_tab", lambda *a: order.append("ensure"))
    monkeypatch.setattr(rhc.sw, "append_rows",
                        lambda svc, sid, tab, rows: order.append(("row", tab, rows[0][0], rows[0][2])))
    monkeypatch.setattr(rhc.sw, "write_meta", lambda *a: order.append("meta"))
    assert rhc.run() == 0
    assert order == ["ensure", ("row", on.TAB_ROUTING_LOG,
                                "https://drive.google.com/file/d/Z/view", "routine"), "meta"]


# --- watcher records a failed urgent send for the weekly email -----------------

@pytest.mark.parametrize("send", [mock.Mock(side_effect=RuntimeError("smtp")),
                                  mock.Mock(return_value=False)])
def test_watcher_records_failed_urgent_send(monkeypatch, send):
    import watcher as w
    from types import SimpleNamespace
    recorded = []
    monkeypatch.setattr(w.ea, "is_urgent", lambda p, c: True)
    monkeypatch.setattr(w.ea, "send_urgent_alert", send)
    monkeypatch.setattr(w.on, "record_send_failure",
                        lambda svc, what, outcome, meta, link, now: recorded.append((what, outcome)))
    state = {"pending_digest": [], "pending_urgent_recap": []}
    w._route_urgent_or_digest(SimpleNamespace(), {"document_name": "D"}, "L", {}, state, None, "SID")
    assert recorded and recorded[0][0] == "urgent alert"
    assert state["pending_urgent_recap"] == []
