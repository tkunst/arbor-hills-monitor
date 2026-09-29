"""Tests for well_watch (ADR 058): named single-well temperature trip-wires."""
from types import SimpleNamespace

import well_watch

CFG = {"well_watch": {
    "recipients": ["arbor-hills@trishakunst.com"],
    "wells": [{"label": "263R5", "pattern": r"^AHW{1,2}0*263R\d*$",
               "threshold_f": 145, "since": "2026-05-01", "note": "context"}],
}}


def _r(well, temp, dt="7/14/2026 09:10", adj=False, valid=True, raw=None):
    return SimpleNamespace(well_id=well, raw_well_id=raw or well, dt=dt, adj=adj,
                           temp=temp, page=12, valid=valid)


def test_exactly_145_fires():
    hits = well_watch.find_hits([_r("AHW263R5", 145.0)], CFG)
    assert len(hits) == 1 and hits[0][1][0].temp == 145.0


def test_144_9_does_not_fire():
    assert well_watch.find_hits([_r("AHW263R5", 144.9)], CFG) == []


def test_redrill_and_prefix_aliases_fire():
    rs = [_r("AHWW263R6", 150.0), _r("AHW0263R5", 146.0)]
    assert len(well_watch.find_hits(rs, CFG)[0][1]) == 2


def test_other_wells_ignored():
    rs = [_r("AHWW0279", 154.6), _r("AHW2263R5", 160.0), _r("AHW263", 160.0)]
    assert well_watch.find_hits(rs, CFG) == []


def test_adj_reading_counts_and_is_labeled():
    hits = well_watch.find_hits([_r("AHW263R5", 144.8), _r("AHW263R5", 145.1, adj=True)], CFG)
    assert [r.adj for r in hits[0][1]] == [True]
    subject, body = well_watch.compose(hits[0][0], hits[0][1], {"document_name": "X"}, "L")
    assert "145.1" in subject and "ADJ" in body


def test_since_drops_reprinted_history_but_keeps_undated():
    rs = [_r("AHW263R5", 160.0, dt="11/3/2025 10:00"), _r("AHW263R5", 146.0, dt="garbled")]
    hits = well_watch.find_hits(rs, CFG)[0][1]
    assert [r.temp for r in hits] == [146.0]


def test_invalid_and_missing_temp_skipped():
    rs = [_r("AHW263R5", 150.0, valid=False), _r("AHW263R5", None)]
    assert well_watch.find_hits(rs, CFG) == []


def test_no_config_is_noop(monkeypatch):
    sent = []
    monkeypatch.setattr(well_watch.email_alerts, "send_email", lambda *a, **k: sent.append(a))
    assert well_watch.check_and_alert([_r("AHW263R5", 150.0)], {}, "L", {}) == 0
    assert sent == []


def test_check_and_alert_sends_one_email_to_watch_recipients(monkeypatch):
    sent = []
    monkeypatch.setattr(well_watch.email_alerts, "send_email",
                        lambda subj, body, cfg, recipients=None: sent.append((subj, recipients)))
    n = well_watch.check_and_alert(
        [_r("AHW263R5", 145.2), _r("AHW263R5", 146.0, dt="7/20/2026 08:00")],
        {"document_name": "2026 Second Semi-Annual WOI Status Report", "date_filed": "2027-01-14"},
        "https://example/doc", CFG)
    assert n == 1 and len(sent) == 1
    assert "AHW263R5 at 146.0F" in sent[0][0]
    assert sent[0][1] == ["arbor-hills@trishakunst.com"]
