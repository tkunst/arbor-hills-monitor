"""civicclerk_watcher.py — the activation gate, the pure cadence / snapshot /
diff helpers, and the full baseline / unchanged / changed / vanish / fetch-fail
flows driven through a fake Sheets service (no network, no creds). Mirrors
test_pfas_watcher; reuses its FakeSheets. The fetch-fail-without-baseline test
pins the loud-exit-1 activation behaviour; the vanish test pins the
200-empty-is-a-real-change rule (ADR 015)."""
from datetime import date

import pytest

import civicclerk_watcher as cw
import mmpc_client as mc
import sheet_writer as sw
from test_pfas_watcher import FakeSheets


# --- gate (pure) ----------------------------------------------------------------

def test_should_run_false_when_disabled():
    ok, reason = cw._should_run({"civicclerk_watch": {"enabled": False}})
    assert ok is False and "civicclerk_watch.enabled" in reason


def test_should_run_false_when_key_absent():
    ok, reason = cw._should_run({})
    assert ok is False and "civicclerk_watch.enabled" in reason


def test_should_run_true_when_enabled():
    ok, reason = cw._should_run({"civicclerk_watch": {"enabled": True}})
    assert ok is True and reason == ""


# --- cadence (pure) -------------------------------------------------------------

def test_every_run_always_due():
    assert cw.is_due_today("every_run", None, date(2026, 7, 14)) is True
    assert cw.is_due_today(None, date(2026, 8, 19), date(2026, 7, 14)) is True


def test_unknown_scalar_cadence_fails_safe_to_due():
    assert cw.is_due_today("weekly-ish", None, date(2026, 7, 14)) is True


def test_weekly_due_only_on_the_weekly_weekday():
    cad = {"weekly_weekday": 0, "daily_before_days": 3}  # Monday
    far = date(2027, 1, 1)  # meeting far outside the 3-day window
    assert cw.is_due_today(cad, far, date(2026, 7, 20)) is True   # 2026-07-20 = Monday
    assert cw.is_due_today(cad, far, date(2026, 7, 21)) is False  # Tuesday, not near


def test_daily_in_the_window_before_the_meeting():
    cad = {"weekly_weekday": 0, "daily_before_days": 3}
    meeting = date(2026, 8, 5)  # a Wednesday
    assert cw.is_due_today(cad, meeting, date(2026, 8, 2)) is True   # 3 days before
    assert cw.is_due_today(cad, meeting, date(2026, 8, 5)) is True   # meeting day
    assert cw.is_due_today(cad, meeting, date(2026, 8, 1)) is False  # 4 days before
    assert cw.is_due_today(cad, meeting, date(2026, 8, 6)) is False  # day after


# --- snapshot + hash (pure) -----------------------------------------------------

def _ev(**over):
    base = {"id": 4005, "eventName": "MMPC", "eventDate": "2026-08-19T10:00:00Z",
            "isPublished": "Published", "eventNotice": "", "isDeleted": False,
            "publishedFiles": []}
    base.update(over)
    return base


def _file(fid, **over):
    f = {"fileId": fid, "type": "Agenda", "name": f"doc {fid}",
         "publishOn": "2026-08-01T00:00:00Z", "sort": 1, "url": f"stream/{fid}.pdf"}
    f.update(over)
    return f


def test_snapshot_ignores_volatile_sort_and_url():
    a = cw.event_snapshot(_ev(publishedFiles=[_file(1, sort=1, url="stream/a.pdf")]))
    b = cw.event_snapshot(_ev(publishedFiles=[_file(1, sort=9, url="stream/ROTATED.pdf")]))
    assert cw.snapshot_hash(a) == cw.snapshot_hash(b)


def test_snapshot_file_order_is_canonical():
    a = cw.event_snapshot(_ev(publishedFiles=[_file(1), _file(2)]))
    b = cw.event_snapshot(_ev(publishedFiles=[_file(2), _file(1)]))
    assert cw.snapshot_hash(a) == cw.snapshot_hash(b)


@pytest.mark.parametrize("over", [
    {"publishedFiles": [_file(1)]},                 # a doc added
    {"eventDate": "2026-08-20T10:00:00Z"},          # meeting moved
    {"eventName": "CANCELLED — MMPC"},              # cancellation rename
    {"isPublished": "Unpublished"},                 # unpublished
    {"eventNotice": "Meeting cancelled"},           # notice banner
])
def test_meaningful_changes_change_the_hash(over):
    base = cw.event_snapshot(_ev())
    changed = cw.event_snapshot(_ev(**over))
    assert cw.snapshot_hash(base) != cw.snapshot_hash(changed)


# --- summarize_change (pure) ----------------------------------------------------

def test_summarize_added_and_removed_documents():
    old = cw.event_snapshot(_ev(publishedFiles=[_file(1, type="Agenda")]))
    new = cw.event_snapshot(_ev(publishedFiles=[_file(2, type="Minutes")]))
    note, body = cw.summarize_change(old, new)
    assert "document added" in note and "document removed" in note
    assert "ADDED" in body and "REMOVED" in body


def test_summarize_date_change():
    old = cw.event_snapshot(_ev())
    new = cw.event_snapshot(_ev(eventDate="2026-08-26T10:00:00Z"))
    note, body = cw.summarize_change(old, new)
    assert "date/time changed" in note and "date/time" in body


def test_summarize_vanish():
    old = cw.event_snapshot(_ev())
    note, body = cw.summarize_change(old, dict(cw.GONE_SNAPSHOT))
    assert "no longer on the portal" in note
    assert "cancelled" in body.lower()


def test_format_change_body_has_essentials():
    body = cw.format_change_body("MMPC — Aug 19", "http://x", "document added", "+ ADDED foo")
    assert "MMPC — Aug 19" in body and "http://x" in body
    assert "document added" in body and "+ ADDED foo" in body


# --- flows through a fake Sheet -------------------------------------------------

MONDAY = date(2026, 7, 20)  # a Monday, so a weekly BOC group would be due

MMPC_CFG = {
    "civicclerk_watch": {
        "enabled": True,
        "recipients": ["arbor-hills@trishakunst.com"],
        "groups": [
            {"name": "MMPC", "cadence": "every_run",
             "events": [{"id": 4005, "event_date": "2026-08-19", "label": "MMPC — Aug 19"}]},
        ],
    }
}


def _seq_fetch(responses):
    """A fake mmpc_client.fetch_event returning/raising successive `responses`.
    Records the event_ids it was asked for (to assert not-due events are skipped)."""
    it = iter(responses)

    def _f(session, event_id):
        _f.ids.append(event_id)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    _f.ids = []
    return _f


def _wire(monkeypatch, cfg, fetch, today=MONDAY):
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(cw, "load_config", lambda: cfg)
    monkeypatch.setattr(cw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(cw.mc, "fetch_event", fetch)
    monkeypatch.setattr(cw, "_today_date", lambda: today)
    monkeypatch.setattr(cw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_MEETING_WATCH, [])[1:]  # drop header


def test_disabled_is_noop(monkeypatch):
    cfg = {"civicclerk_watch": {"enabled": False, "groups": MMPC_CFG["civicclerk_watch"]["groups"]}}
    fake, sent = _wire(monkeypatch, cfg, _seq_fetch([_ev()]))
    assert cw.run() == 0
    assert sent == []
    assert sw.TAB_MEETING_WATCH not in fake._values._tabs  # never created the tab


def test_first_run_baselines_silently(monkeypatch):
    fake, sent = _wire(monkeypatch, MMPC_CFG, _seq_fetch([_ev()]))
    assert cw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 1
    assert rows[0][5] == "baseline"   # Change column
    assert sent == []                 # baseline never alerts


def test_unchanged_second_run_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, MMPC_CFG, _seq_fetch([_ev(), _ev()]))
    cw.run()               # baseline
    assert cw.run() == 0   # same event again
    assert len(_rows(fake)) == 1
    assert sent == []


def test_changed_records_row_and_emails_only_the_override(monkeypatch):
    old, new = _ev(), _ev(publishedFiles=[_file(9, type="Agenda", name="Agenda Aug 19")])
    fake, sent = _wire(monkeypatch, MMPC_CFG, _seq_fetch([old, new]))
    cw.run()               # baseline
    assert cw.run() == 0   # changed (agenda posted)
    rows = _rows(fake)
    assert len(rows) == 2 and rows[1][5] == "changed"
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "changed" in subj.lower() and "Agenda Aug 19" in body
    assert recipients == ["arbor-hills@trishakunst.com"]  # NOT the shared list


def test_vanish_after_baseline_is_a_change(monkeypatch):
    # A successful 200 that returns no event (None) for a previously-seen meeting
    # is a real change (cancelled/removed), not a transient error.
    fake, sent = _wire(monkeypatch, MMPC_CFG, _seq_fetch([_ev(), None]))
    cw.run()               # baseline
    assert cw.run() == 0   # vanished
    rows = _rows(fake)
    assert len(rows) == 2 and rows[1][5] == "changed"
    assert len(sent) == 1
    assert "no longer on the portal" in sent[0][1]


def test_empty_on_first_sight_is_not_baselined(monkeypatch):
    fake, sent = _wire(monkeypatch, MMPC_CFG, _seq_fetch([None]))
    assert cw.run() == 0
    assert _rows(fake) == []   # phantom not baselined
    assert sent == []


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fetch = _seq_fetch([_ev(), mc.MMPCFetchError("transient blip")])
    fake, sent = _wire(monkeypatch, MMPC_CFG, fetch)
    cw.run()               # baseline
    assert cw.run() == 0   # blip — not loud
    assert len(_rows(fake)) == 1   # baseline preserved, not diffed
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud(monkeypatch):
    fetch = _seq_fetch([mc.MMPCFetchError("bot wall on the runner")])
    fake, sent = _wire(monkeypatch, MMPC_CFG, fetch)
    assert cw.run() == 1           # activation-time block surfaces loudly
    assert _rows(fake) == []
    assert sent == []


def test_not_due_event_is_not_fetched(monkeypatch):
    # A BOC event with a weekly cadence, checked on a NON-weekly day with the
    # meeting far outside the 3-day window, must be skipped (never fetched).
    cfg = {
        "civicclerk_watch": {
            "enabled": True,
            "recipients": ["arbor-hills@trishakunst.com"],
            "groups": [
                {"name": "BOC",
                 "cadence": {"weekly_weekday": 0, "daily_before_days": 3},  # Monday
                 "events": [{"id": 3940, "event_date": "2026-12-02", "label": "BOC Dec 2"}]},
            ],
        }
    }
    tuesday = date(2026, 7, 21)  # not Monday, and Dec 2 is far away
    fetch = _seq_fetch([_ev()])
    fake, sent = _wire(monkeypatch, cfg, fetch, today=tuesday)
    assert cw.run() == 0
    assert fetch.ids == []          # the not-due event was never fetched
    assert _rows(fake) == []
    assert sent == []
