"""
mmpc_watcher.py — Washtenaw County MMPC meeting calculator + minutes poller.

The MMPC schedule is a deterministic rule, not a page to scrape: second
Wednesday of every month, 10:00am, Washtenaw County Learning Resource Center,
Superior Room. We calculate meeting dates mathematically, then poll for minutes
starting `poll_start_days_after` days after each meeting, daily, up to
`poll_window_days` days, stopping when notes appear.

The minutes URL is NOT confirmed programmatically — verify with the Conservancy
(they attend every meeting) and hard-code it in config.yml before first deploy.

The date math (nth_weekday_of_month, meeting_date_for_month,
active_polling_meeting) is pure and unit-tested across month boundaries and a
year roll.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the date of the n-th `weekday` in (year, month).
    weekday: 0=Mon ... 6=Sun. n: 1-based (2 = second)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7  # days from the 1st to first weekday
    day = 1 + offset + (n - 1) * 7
    return date(year, month, day)


def meeting_date_for_month(year: int, month: int, cfg: dict) -> date:
    """The MMPC meeting date for a given month per config."""
    return nth_weekday_of_month(
        year, month, cfg["meeting_weekday"], cfg["meeting_week_of_month"]
    )


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def active_polling_meeting(today: date, cfg: dict) -> Optional[date]:
    """Return the meeting date whose minutes-polling window contains `today`,
    or None. Checks this month's and last month's meetings (a late-month meeting
    + a multi-day window can roll into the next month, and an early-month `today`
    can still be polling last month's meeting)."""
    candidates = [
        meeting_date_for_month(today.year, today.month, cfg),
        meeting_date_for_month(*_prev_month(today.year, today.month), cfg),
    ]
    start_after = cfg["poll_start_days_after"]
    window = cfg["poll_window_days"]
    for mtg in candidates:
        start = mtg + timedelta(days=start_after)
        end = start + timedelta(days=window)
        if start <= today <= end:
            return mtg
    return None


def check_minutes_posted(session, url: str) -> tuple[bool, str]:
    """Best-effort check that meeting minutes are posted at `url`. Returns
    (found, note). Conservative: only reports found on a clean 200 with a
    non-trivial body. Returns (False, ...) if the URL is the placeholder."""
    if not url or url == "VERIFY_BEFORE_DEPLOY":
        return (False, "minutes_url not configured (VERIFY_BEFORE_DEPLOY)")
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 200 and len(r.text) > 500:
            return (True, f"minutes page reachable ({len(r.text)} bytes)")
        return (False, f"HTTP {r.status_code}, {len(r.text)} bytes")
    except Exception as e:  # noqa: BLE001
        return (False, f"error fetching minutes: {e}")
