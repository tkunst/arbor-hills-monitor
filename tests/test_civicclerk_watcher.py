"""civicclerk_watcher.py — the activation gate, the pure cadence / snapshot /
diff helpers, and the full baseline / unchanged / changed / vanish / fetch-fail
flows driven through a fake Sheets service (no network, no creds). Mirrors
test_pfas_watcher; reuses its FakeSheets. The fetch-fail-without-baseline test
pins the loud-exit-1 activation behaviour; the vanish test pins the
200-empty-is-a-real-change rule (ADR 015)."""
from datetime import date

import fitz
import pytest

import civicclerk_watcher as cw
import mmpc_client as mc
import sheet_writer as sw
from test_pfas_watcher import FakeSheets


def _pdf_bytes(text: str) -> bytes:
    """A real, in-memory, born-digital PDF's bytes (PyMuPDF) — used to exercise
    extract_pdf_text/scan_files_for_keywords for real, not through a mock, so the
    keyword-match regression tests below are genuine (fitz.open(stream=...) really
    parses this), matching tests/conftest.py's text_pdf fixture idiom."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


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


# --- keyword-match primitives (pure) ---------------------------------------------

def test_find_keyword_hits_matches_and_excerpts():
    text = "The board reviewed a 251-acre landfill expansion request from GFL."
    hits = cw.find_keyword_hits(text, ["251 acre", "landfill expansion", "GFL", "Arbor Hills"])
    matched = {k for k, _ in hits}
    assert matched == {"251 acre", "landfill expansion", "GFL"}  # hyphen-tolerant; no Arbor Hills
    excerpt = dict(hits)["GFL"]
    assert "GFL" in excerpt


def test_find_keyword_hits_word_boundary_not_substring():
    assert cw.find_keyword_hits("we engulfed the topic entirely", ["GFL"]) == []
    assert cw.find_keyword_hits("GFL's proposal was denied", ["GFL"]) != []


def test_find_keyword_hits_case_insensitive_and_line_wrap_tolerant():
    assert cw.find_keyword_hits("a Good Neighbor PLAN was discussed",
                                 ["good neighbor plan"]) != []
    assert cw.find_keyword_hits("the plan\namendment was filed", ["plan amendment"]) != []


def test_find_keyword_hits_short_keywords_are_intentionally_broad():
    # "consistency"/"siting" are directed to stay in the list as-is (fail open) —
    # this pins the KNOWN false-positive risk so it's visible, not a silent trap.
    assert cw.find_keyword_hits(
        "staff recommended consistency in the purchasing policy", ["consistency"]) != []
    assert cw.find_keyword_hits(
        "the working session covered siting of a new county garage", ["siting"]) != []


def test_is_scannable_file_type():
    assert cw._is_scannable_file_type("Agenda") is True
    assert cw._is_scannable_file_type("Agenda Packet") is True
    assert cw._is_scannable_file_type("Minutes") is True
    assert cw._is_scannable_file_type("Notice") is False
    assert cw._is_scannable_file_type(None) is False


def _sfile(fid, **over):
    """A canonical snapshot-shaped file dict (event_snapshot()'s output shape —
    file_id/type/name/publish_on), distinct from _file() above (the RAW
    publishedFiles shape with fileId/publishOn). files_to_scan operates on the
    canonical shape."""
    base = {"file_id": fid, "type": "Agenda", "name": f"doc {fid}",
            "publish_on": "2026-08-01T00:00:00Z"}
    base.update(over)
    return base


def test_files_to_scan_new_changed_unchanged_removed_and_wrong_type():
    old = [_sfile(1), _sfile(2, type="Minutes")]
    new = [_sfile(1),                                    # unchanged -> excluded
           _sfile(2, type="Minutes", name="replaced"),    # changed -> included
           _sfile(3),                                     # new -> included
           _sfile(4, type="Notice")]                      # new, wrong type -> excluded
    scan = cw.files_to_scan(old, new)
    assert {f["file_id"] for f in scan} == {2, 3}


def test_files_to_scan_first_sighting_scans_everything_present():
    new = [_sfile(1), _sfile(2, type="Minutes"), _sfile(3, type="Notice")]
    scan = cw.files_to_scan([], new)
    assert {f["file_id"] for f in scan} == {1, 2}  # Notice excluded, rest included


def test_format_keyword_hits_empty_and_content():
    assert cw.format_keyword_hits([]) == ""
    hits = [{"file": {"type": "Agenda", "name": "Agenda Aug 19"},
             "keyword": "GFL", "excerpt": "...GFL proposal..."}]
    body = cw.format_keyword_hits(hits)
    assert "KEYWORD MATCH" in body and "GFL" in body and "Agenda Aug 19" in body


def test_discoverable_events_filters_by_cutoff_and_sorts():
    events = [_ev(id=1, eventDate="2024-01-01T00:00:00Z"),
              _ev(id=2, eventDate="2026-09-10T00:00:00Z"),
              _ev(id=3, eventDate="2026-08-01T00:00:00Z"),
              _ev(id=4, eventDate="")]  # no parseable date -> excluded, fail-safe
    out = cw.discoverable_events(events, date(2026, 7, 1))
    assert [e["id"] for e in out] == [3, 2]  # sorted oldest-first, id 1/4 excluded


def test_extract_pdf_text_round_trips_real_pdf():
    data = _pdf_bytes("Arbor Hills plan amendment discussion item.")
    assert "Arbor Hills" in cw.extract_pdf_text(data)


def test_extract_pdf_text_raises_on_corrupt_bytes():
    with pytest.raises(mc.MMPCFetchError):
        cw.extract_pdf_text(b"not a pdf at all, just garbage")


def test_scan_files_for_keywords_real_pdf_match_and_miss(monkeypatch):
    bodies = {
        101: _pdf_bytes("The board discussed a GFL landfill plan amendment."),
        102: _pdf_bytes("The board approved routine minutes and budget items."),
    }
    monkeypatch.setattr(mc, "download_file_bytes",
                        lambda session, fid, timeout=60: bodies[fid])
    files = [{"file_id": 101, "type": "Agenda", "name": "Agenda A"},
             {"file_id": 102, "type": "Minutes", "name": "Minutes B"}]
    hits = cw.scan_files_for_keywords(None, files, ["GFL", "plan amendment", "Arbor Hills"])
    assert {h["file"]["file_id"] for h in hits} == {101}
    assert {h["keyword"] for h in hits} == {"GFL", "plan amendment"}


def test_scan_files_for_keywords_skips_unreadable_file_without_raising(monkeypatch):
    monkeypatch.setattr(mc, "download_file_bytes",
                        lambda session, fid, timeout=60: (_ for _ in ()).throw(
                            mc.MMPCFetchError("bad download")))
    hits = cw.scan_files_for_keywords(None, [{"file_id": 1, "type": "Agenda", "name": "x"}], ["GFL"])
    assert hits == []


def test_format_backfill_report_clean_and_with_hits():
    clean = cw.format_backfill_report([], 10, 3, 7, 12)
    assert "No keyword matches" in clean
    assert "FOIA gap" in clean

    hit_events = [{"group": "MMPC", "label": "MMPC Jan", "date": "2026-01-15",
                   "url": "http://x", "hits": [{"file": {"type": "Agenda", "name": "A"},
                                                  "keyword": "GFL", "excerpt": "...GFL..."}]}]
    report = cw.format_backfill_report(hit_events, 10, 3, 7, 12)
    assert "1 meeting(s) matched" in report
    assert "MMPC Jan" in report and "GFL" in report
    assert report.index("MMPC Jan") < report.index("Scanned")  # hits listed FIRST


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


def _wire(monkeypatch, cfg, fetch=None, today=MONDAY, fetch_category=None, download=None):
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(cw, "load_config", lambda: cfg)
    monkeypatch.setattr(cw.dc, "sheets_service", lambda: fake)
    if fetch is not None:
        monkeypatch.setattr(cw.mc, "fetch_event", fetch)
    if fetch_category is not None:
        monkeypatch.setattr(cw.mc, "fetch_category_events", fetch_category)
    if download is not None:
        monkeypatch.setattr(cw.mc, "download_file_bytes", download)
    monkeypatch.setattr(cw, "_today_date", lambda: today)
    monkeypatch.setattr(cw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)) or True)
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


# --- category_id auto-discover group mode (ADR 036) ------------------------------

DPA_CFG = {
    "civicclerk_watch": {
        "enabled": True,
        "recipients": ["arbor-hills@trishakunst.com"],
        "groups": [
            {"name": "DPA", "cadence": "every_run",
             "category_id": 68, "discover_since_days": 400},
        ],
    }
}


def test_auto_discover_baselines_events_in_window_and_skips_outside_it(monkeypatch):
    raw = [_ev(id=100, eventDate="2026-08-20T00:00:00Z", eventName="DPA Aug"),
           _ev(id=101, eventDate="2020-01-01T00:00:00Z", eventName="DPA old")]
    fake, sent = _wire(monkeypatch, DPA_CFG, fetch_category=lambda s, cid: raw)
    assert cw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 1 and rows[0][3] == 100   # only the in-window event
    assert sent == []


def test_auto_discover_no_per_event_fetch_call_needed(monkeypatch):
    # The category fetch already returns full events — fetch_event must never be
    # called for an auto-discover group (that would defeat the "one call for the
    # whole category" design this mode exists for).
    raw = [_ev(id=100, eventDate="2026-08-20T00:00:00Z")]
    called = []
    monkeypatch.setattr(cw.mc, "fetch_event", lambda s, eid: called.append(eid))
    fake, sent = _wire(monkeypatch, DPA_CFG, fetch_category=lambda s, cid: raw)
    cw.run()
    assert called == []


def test_auto_discover_second_run_unchanged_is_noop(monkeypatch):
    raw = [_ev(id=100, eventDate="2026-08-20T00:00:00Z")]
    fake, sent = _wire(monkeypatch, DPA_CFG, fetch_category=lambda s, cid: raw)
    cw.run()
    assert cw.run() == 0
    assert len(_rows(fake)) == 1
    assert sent == []


def test_auto_discover_change_is_detected_next_run(monkeypatch):
    old = _ev(id=100, eventDate="2026-08-20T00:00:00Z")
    new = _ev(id=100, eventDate="2026-08-20T00:00:00Z",
               publishedFiles=[_file(9100, type="Agenda", name="Agenda posted")])
    calls = {"n": 0}

    def _cat(s, cid):
        calls["n"] += 1
        return [old] if calls["n"] == 1 else [new]

    fake, sent = _wire(monkeypatch, DPA_CFG, fetch_category=_cat)
    cw.run()
    assert cw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2 and rows[1][5] == "changed"
    assert len(sent) == 1


def test_auto_discover_category_fetch_failure_no_rows_is_loud(monkeypatch):
    def _fail(s, cid):
        raise mc.MMPCFetchError("bot wall on the runner")
    fake, sent = _wire(monkeypatch, DPA_CFG, fetch_category=_fail)
    assert cw.run() == 1
    assert _rows(fake) == []
    assert sent == []


def test_auto_discover_category_fetch_failure_with_rows_is_skip_warn(monkeypatch):
    raw = [_ev(id=100, eventDate="2026-08-20T00:00:00Z")]
    calls = {"n": 0}

    def _cat(s, cid):
        calls["n"] += 1
        if calls["n"] == 1:
            return raw
        raise mc.MMPCFetchError("transient blip")

    fake, sent = _wire(monkeypatch, DPA_CFG, fetch_category=_cat)
    cw.run()                      # baseline
    assert cw.run() == 0          # blip on the 2nd run — not loud, baseline preserved
    assert len(_rows(fake)) == 1


# --- keyword scan wired into run() (ADR 036) --------------------------------------

def _kw_cfg(groups, keywords=("Arbor Hills", "GFL", "plan amendment")):
    return {
        "civicclerk_watch": {
            "enabled": True,
            "recipients": ["arbor-hills@trishakunst.com"],
            "keyword_scan": {"enabled": True, "keywords": list(keywords)},
            "groups": groups,
        }
    }


def test_keyword_match_on_first_sighting_overrides_silent_baseline(monkeypatch):
    ev = _ev(id=200, eventDate="2026-08-20T00:00:00Z",
              publishedFiles=[_file(9001, type="Agenda", name="Agenda")])
    pdf = _pdf_bytes("Item 5: GFL plan amendment for the landfill expansion.")
    cfg = _kw_cfg(DPA_CFG["civicclerk_watch"]["groups"])
    fake, sent = _wire(monkeypatch, cfg, fetch_category=lambda s, cid: [ev],
                        download=lambda s, fid, timeout=60: pdf)
    assert cw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 1 and rows[0][5] == "baseline"   # still a baseline row...
    assert "KEYWORD MATCH" in rows[0][8]                 # ...but the Note says why
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "ALERT" in subj
    assert "GFL" in body and "plan amendment" in body
    assert recipients == ["arbor-hills@trishakunst.com"]


def test_routine_first_sighting_stays_silent(monkeypatch):
    ev = _ev(id=201, eventDate="2026-08-20T00:00:00Z",
              publishedFiles=[_file(9002, type="Agenda", name="Agenda")])
    pdf = _pdf_bytes("Item 5: approve routine budget transfer for road salt.")
    cfg = _kw_cfg(DPA_CFG["civicclerk_watch"]["groups"])
    fake, sent = _wire(monkeypatch, cfg, fetch_category=lambda s, cid: [ev],
                        download=lambda s, fid, timeout=60: pdf)
    assert cw.run() == 0
    rows = _rows(fake)
    assert rows[0][5] == "baseline" and "KEYWORD MATCH" not in rows[0][8]
    assert sent == []


def test_keyword_match_on_changed_event_elevates_the_alert(monkeypatch):
    old, new = _ev(), _ev(publishedFiles=[_file(9003, type="Agenda", name="Agenda Sep 9")])
    pdf = _pdf_bytes("Arbor Hills plan amendment discussion item.")
    cfg = _kw_cfg(MMPC_CFG["civicclerk_watch"]["groups"])
    fetch = _seq_fetch([old, new])
    fake, sent = _wire(monkeypatch, cfg, fetch, download=lambda s, fid, timeout=60: pdf)
    cw.run()                # baseline (no files yet)
    assert cw.run() == 0    # changed: agenda posted
    rows = _rows(fake)
    assert len(rows) == 2 and rows[1][5] == "changed"
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert subj.startswith("[Arbor Hills ALERT]")
    assert "Arbor Hills" in body and "plan amendment" in body


def test_routine_changed_event_keeps_the_ordinary_subject(monkeypatch):
    old, new = _ev(), _ev(publishedFiles=[_file(9004, type="Agenda", name="Agenda Sep 9")])
    pdf = _pdf_bytes("Approve routine minutes from the last session.")
    cfg = _kw_cfg(MMPC_CFG["civicclerk_watch"]["groups"])
    fetch = _seq_fetch([old, new])
    fake, sent = _wire(monkeypatch, cfg, fetch, download=lambda s, fid, timeout=60: pdf)
    cw.run()
    assert cw.run() == 0
    assert len(sent) == 1
    assert sent[0][0].startswith("[Meeting watch]")   # NOT elevated


def test_keyword_scan_disabled_never_downloads(monkeypatch):
    old, new = _ev(), _ev(publishedFiles=[_file(9005, type="Agenda", name="Agenda")])
    calls = []

    def _dl(s, fid, timeout=60):
        calls.append(fid)
        return _pdf_bytes("Arbor Hills plan amendment")

    cfg = {
        "civicclerk_watch": {
            "enabled": True,
            "recipients": ["arbor-hills@trishakunst.com"],
            "keyword_scan": {"enabled": False, "keywords": ["Arbor Hills"]},
            "groups": MMPC_CFG["civicclerk_watch"]["groups"],
        }
    }
    fetch = _seq_fetch([old, new])
    fake, sent = _wire(monkeypatch, cfg, fetch, download=_dl)
    cw.run()
    cw.run()
    assert calls == []
    assert sent[0][0].startswith("[Meeting watch]")


def test_keyword_scan_with_no_keywords_configured_never_downloads(monkeypatch):
    old, new = _ev(), _ev(publishedFiles=[_file(9006, type="Agenda", name="Agenda")])
    calls = []

    def _dl(s, fid, timeout=60):
        calls.append(fid)
        return _pdf_bytes("Arbor Hills plan amendment")

    cfg = {
        "civicclerk_watch": {
            "enabled": True,
            "recipients": ["arbor-hills@trishakunst.com"],
            "keyword_scan": {"enabled": True, "keywords": []},
            "groups": MMPC_CFG["civicclerk_watch"]["groups"],
        }
    }
    fetch = _seq_fetch([old, new])
    fake, sent = _wire(monkeypatch, cfg, fetch, download=_dl)
    cw.run()
    cw.run()
    assert calls == []


# --- one-time 12-month historical backfill (ADR 036) ------------------------------

def _wire_backfill(monkeypatch, keywords=("GFL", "Arbor Hills"), category_events=None):
    fake = FakeSheets()
    sent = []
    cfg = {
        "civicclerk_watch": {
            "enabled": True,
            "recipients": ["arbor-hills@trishakunst.com"],
            "keyword_scan": {"enabled": True, "keywords": list(keywords)},
            "groups": [],
        }
    }
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(cw, "load_config", lambda: cfg)
    monkeypatch.setattr(cw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(cw, "_today_date", lambda: MONDAY)
    if category_events is not None:
        monkeypatch.setattr(cw.mc, "fetch_category_events",
                            lambda s, cid: category_events(cid))
    monkeypatch.setattr(cw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)) or True)
    return fake, sent


def test_backfill_baselines_in_window_events_and_reports_hits(monkeypatch):
    hit_ev = _ev(id=500, eventDate="2026-07-01T00:00:00Z", eventName="DPA July",
                 publishedFiles=[_file(7001, type="Agenda", name="Agenda")])
    clean_ev = _ev(id=501, eventDate="2026-06-01T00:00:00Z", eventName="DPA June",
                   publishedFiles=[_file(7002, type="Minutes", name="Minutes")])
    old_ev = _ev(id=502, eventDate="2023-01-01T00:00:00Z", eventName="DPA ancient")

    def _cat(cid):
        return [hit_ev, clean_ev, old_ev] if cid == 68 else []

    pdfs = {7001: _pdf_bytes("Item: GFL landfill Arbor Hills expansion."),
            7002: _pdf_bytes("Routine minutes approval.")}
    fake, sent = _wire_backfill(monkeypatch, category_events=_cat)
    monkeypatch.setattr(cw.mc, "download_file_bytes",
                        lambda s, fid, timeout=60: pdfs[fid])

    rc = cw.run_historical_backfill(months=12)
    assert rc == 0

    rows = _rows(fake)
    assert {r[3] for r in rows} == {500, 501}     # ancient event excluded (outside window)
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert "1 meeting(s) matched" in subj
    assert "DPA July" in body and "GFL" in body


def test_backfill_skips_baseline_for_already_known_event_but_still_reports(monkeypatch):
    fake, sent = _wire_backfill(monkeypatch)
    sw.ensure_meeting_watch_tabs(fake, "SID")
    sw.append_meeting_watch_row(
        fake, "SID", "2026-01-01", "MMPC", "MMPC Jan", 600,
        "http://x", "baseline", "somehash", 1, "initial snapshot (no alert)",
        "2026-01-01T00:00:00", "{}")

    ev = _ev(id=600, eventDate="2026-01-15T00:00:00Z", eventName="MMPC Jan",
              publishedFiles=[_file(8001, type="Agenda", name="Agenda")])

    def _cat(cid):
        return [ev] if cid == 72 else []

    monkeypatch.setattr(cw.mc, "fetch_category_events", lambda s, cid: _cat(cid))
    monkeypatch.setattr(cw.mc, "download_file_bytes",
                        lambda s, fid, timeout=60: _pdf_bytes("GFL landfill expansion item"))

    cw.run_historical_backfill(months=12)
    rows = _rows(fake)
    assert len(rows) == 1                 # no new baseline row — already known
    assert len(sent) == 1
    assert "MMPC Jan" in sent[0][1]        # still reported as a hit


def test_backfill_one_category_fetch_failure_does_not_block_the_others(monkeypatch):
    ev = _ev(id=700, eventDate="2026-07-01T00:00:00Z", eventName="MMPC event")

    def _cat(cid):
        if cid == 68:
            raise mc.MMPCFetchError("cat 68 down")
        return [ev] if cid == 72 else []

    fake, sent = _wire_backfill(monkeypatch, category_events=_cat)
    monkeypatch.setattr(cw.mc, "download_file_bytes",
                        lambda s, fid, timeout=60: _pdf_bytes("nothing relevant"))
    rc = cw.run_historical_backfill(months=12)
    assert rc == 0
    rows = _rows(fake)
    assert {r[3] for r in rows} == {700}   # MMPC's event still got baselined
    assert len(sent) == 1                  # report still sent
