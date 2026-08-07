"""Upcoming Activities digest section (upcoming.py). `render_upcoming` is a pure
function tested against a fixed `today`; `fetch_upcoming` is tested against a fake
Sheets values().get() chain (no network, no private-Sheet secret)."""
from datetime import date, datetime

import upcoming as up

TODAY = date(2026, 7, 26)   # a Sunday; today+14 = 2026-08-09


def _e(d, title, end=None):
    return {
        "date": datetime.strptime(d, "%Y-%m-%d").date(),
        "end_date": datetime.strptime(end, "%Y-%m-%d").date() if end else None,
        "title": title,
    }


# --- render_upcoming (pure) ----------------------------------------------------

def test_render_empty_when_no_entries():
    assert up.render_upcoming([], TODAY) == ""


def test_render_includes_in_window_and_sorts_by_date():
    out = up.render_upcoming([_e("2026-08-05", "Later"), _e("2026-07-29", "Sooner")], TODAY)
    assert "UPCOMING ACTIVITIES (next 14 days):" in out
    assert out.index("Sooner") < out.index("Later")
    assert "2026-07-29  Sooner" in out


def test_render_drops_past_and_beyond_horizon():
    out = up.render_upcoming(
        [_e("2026-07-20", "Past"), _e("2026-08-20", "Beyond14"), _e("2026-07-30", "Keep")], TODAY)
    assert "Keep" in out
    assert "Past" not in out and "Beyond14" not in out


def test_render_omits_section_when_all_out_of_window():
    assert up.render_upcoming([_e("2026-01-01", "Old")], TODAY) == ""


def test_render_horizon_boundary_is_inclusive():
    assert "Edge" in up.render_upcoming([_e("2026-08-09", "Edge")], TODAY)   # today + 14


def test_render_range_intersecting_window_shows_as_range():
    out = up.render_upcoming([_e("2026-07-24", "Multi", end="2026-07-28")], TODAY)  # starts before today
    assert "2026-07-24 to 2026-07-28  Multi" in out


def test_render_range_entirely_in_past_is_dropped():
    assert up.render_upcoming([_e("2026-07-10", "Done", end="2026-07-12")], TODAY) == ""


# --- fetch_upcoming (mocked Sheets) --------------------------------------------

class _FakeValues:
    def __init__(self, rows, raise_it):
        self._rows, self._raise = rows, raise_it

    def get(self, spreadsheetId, range):
        return self

    def execute(self):
        if self._raise:
            raise RuntimeError("no such tab")
        return {"values": self._rows}


class _FakeSheets:
    def __init__(self, rows=None, raise_on_get=False):
        self._v = _FakeValues(rows or [], raise_on_get)

    def spreadsheets(self):
        return self

    def values(self):
        return self._v


def test_fetch_parses_rows_and_skips_bad_ones():
    svc = _FakeSheets(rows=[
        ["2026-07-29", "", "Public comment closes"],   # ok
        ["not-a-date", "", "bad date -> skip"],         # skip
        ["2026-08-01", "", ""],                          # blank title -> skip
        ["2026-07-30", "2026-07-31", "Ranged event"],   # ok + end_date
    ])
    out = up.fetch_upcoming(svc, "PRIVID")
    assert [e["title"] for e in out] == ["Public comment closes", "Ranged event"]
    assert out[0]["date"] == date(2026, 7, 29)
    assert out[1]["end_date"] == date(2026, 7, 31)


def test_fetch_returns_empty_on_missing_tab_or_error():
    assert up.fetch_upcoming(_FakeSheets(raise_on_get=True), "PRIVID") == []


# --- email_alerts wiring: the upcoming section is a SEPARATE, scoped email -----

def test_send_upcoming_scopes_to_configured_recipients(monkeypatch):
    import email_alerts as ea
    captured = {}

    def _fake_send_email(subject, body, cfg, recipients=None):
        captured.update(subject=subject, body=body, recipients=recipients)

    monkeypatch.setattr(ea, "send_email", _fake_send_email)
    cfg = {"upcoming": {"recipients": ["trisha@example.com"]},
           "alert_recipients": ["coalition@example.com", "member@example.com"]}
    ea.send_upcoming("UPCOMING ACTIVITIES (next 14 days):\n  - x\n", cfg)
    # Sent to the scoped list VERBATIM — never the full alert_recipients list.
    assert captured["recipients"] == ["trisha@example.com"]
    assert "coalition@example.com" not in (captured["recipients"] or [])
    assert captured["body"].startswith("UPCOMING ACTIVITIES")


def test_send_upcoming_not_sent_when_recipients_unset(monkeypatch):
    """Fail-safe: an unset/empty upcoming.recipients must NOT broadcast the private
    section to the full list — it sends nothing at all."""
    import email_alerts as ea
    calls = []
    monkeypatch.setattr(ea, "send_email", lambda *a, **k: calls.append((a, k)))
    ea.send_upcoming("UPCOMING ACTIVITIES:\n  - x\n", {"alert_recipients": ["coalition@example.com"]})
    ea.send_upcoming("UPCOMING ACTIVITIES:\n  - x\n", {"upcoming": {"recipients": []}})
    assert calls == []


def test_send_upcoming_noop_on_empty_block(monkeypatch):
    import email_alerts as ea
    calls = []
    monkeypatch.setattr(ea, "send_email", lambda *a, **k: calls.append(1))
    ea.send_upcoming("", {"upcoming": {"recipients": ["trisha@example.com"]}})
    assert calls == []


def test_document_digest_is_decoupled_from_upcoming():
    """The document digest must not carry the private upcoming content — its
    signature no longer accepts an upcoming block."""
    import inspect

    import email_alerts as ea
    assert "upcoming_block" not in inspect.signature(ea.format_digest_body).parameters
    assert "upcoming_block" not in inspect.signature(ea.send_digest).parameters
