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
