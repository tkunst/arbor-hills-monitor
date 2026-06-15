"""MMPC date math: nth-weekday, meeting date, polling window, year roll."""
from datetime import date

import mmpc_watcher as mw

CFG = {
    "meeting_weekday": 2,        # Wednesday
    "meeting_week_of_month": 2,  # 2nd
    "poll_start_days_after": 3,
    "poll_window_days": 10,
}


def test_nth_weekday_second_wednesday_june_2026():
    # June 1 2026 is a Monday -> first Wed June 3 -> second Wed June 10.
    assert mw.nth_weekday_of_month(2026, 6, 2, 2) == date(2026, 6, 10)


def test_nth_weekday_first_and_third():
    assert mw.nth_weekday_of_month(2026, 6, 2, 1) == date(2026, 6, 3)
    assert mw.nth_weekday_of_month(2026, 6, 2, 3) == date(2026, 6, 17)


def test_meeting_date_for_month():
    assert mw.meeting_date_for_month(2026, 6, CFG) == date(2026, 6, 10)


def test_prev_month_year_roll():
    assert mw._prev_month(2027, 1) == (2026, 12)
    assert mw._prev_month(2026, 6) == (2026, 5)


def test_polling_window_active_on_first_poll_day():
    # Meeting June 10 + 3 = start June 13; window through June 23.
    assert mw.active_polling_meeting(date(2026, 6, 13), CFG) == date(2026, 6, 10)
    assert mw.active_polling_meeting(date(2026, 6, 23), CFG) == date(2026, 6, 10)


def test_polling_window_inactive_before_start_and_after_end():
    assert mw.active_polling_meeting(date(2026, 6, 12), CFG) is None  # before start
    assert mw.active_polling_meeting(date(2026, 6, 24), CFG) is None  # after window


def test_check_minutes_unconfigured_url_returns_false():
    found, note = mw.check_minutes_posted(None, "VERIFY_BEFORE_DEPLOY")
    assert found is False
    assert "VERIFY_BEFORE_DEPLOY" in note


# --- Explicit published calendar overrides the 2nd-Wednesday rule (2026) ---

EXPLICIT_CFG = {
    "meeting_dates": ["2026-06-10", "2026-08-19"],  # Aug 19 is a 3rd Wednesday
    "poll_start_days_after": 3,
    "poll_window_days": 10,
    # Rule params present but MUST be ignored when meeting_dates is set:
    "meeting_weekday": 2,
    "meeting_week_of_month": 2,
}


def test_explicit_dates_override_rule_in_window():
    # June 10 + 3 = June 13 start; window through June 23.
    assert mw.active_polling_meeting(date(2026, 6, 15), EXPLICIT_CFG) == date(2026, 6, 10)


def test_explicit_dates_use_moved_august_meeting():
    # Real Aug meeting is the 19th (3rd Wed), not the computed 2nd-Wed Aug 12.
    # Aug 19 + 3 = Aug 22 start, window through Sep 1.
    assert mw.active_polling_meeting(date(2026, 8, 25), EXPLICIT_CFG) == date(2026, 8, 19)
    # Aug 15 sits in the OLD rule's Aug-12 window but no real meeting is there now.
    assert mw.active_polling_meeting(date(2026, 8, 15), EXPLICIT_CFG) is None


def test_explicit_dates_no_phantom_for_cancelled_or_skipped_months():
    # April + May are cancelled and November is skipped -> absent from the list,
    # so no reminder fires in their would-be windows (the rule WOULD have fired).
    assert mw.active_polling_meeting(date(2026, 5, 16), EXPLICIT_CFG) is None  # rule's May-13 window
    assert mw.active_polling_meeting(date(2026, 4, 12), EXPLICIT_CFG) is None  # rule's Apr-8 window
    assert mw.active_polling_meeting(date(2026, 11, 14), EXPLICIT_CFG) is None  # rule's Nov-11 window


def test_explicit_dates_accept_yaml_date_objects():
    # PyYAML parses an unquoted YAML date as datetime.date — accept those too.
    cfg = dict(EXPLICIT_CFG, meeting_dates=[date(2026, 6, 10)])
    assert mw.active_polling_meeting(date(2026, 6, 13), cfg) == date(2026, 6, 10)


def test_rule_still_used_when_no_explicit_dates():
    # No meeting_dates key -> fall back to the computed 2nd-Wednesday rule.
    assert mw.active_polling_meeting(date(2026, 6, 13), CFG) == date(2026, 6, 10)
