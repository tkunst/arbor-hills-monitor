"""
gfl_air_liveness.py — Stream E liveness / silent-stall guard (ADR 014
addendum): the pure decision and the alert body. The orchestration wrapper
(_check_liveness) stays in gfl_air_watcher.py. Split out as a pure move;
gfl_air_watcher re-exports every name.
"""
from __future__ import annotations

from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None



# ---------------------------------------------------------------------------
# Liveness / silent-stall guard (ADR 014 residual — the OBJECTID-reset stall).
# Pure decision + body; the orchestration wrapper (_check_liveness) is below.
# ---------------------------------------------------------------------------

_AS_OF_FMT = "%Y-%m-%dT%H:%MZ"       # the format sheet_writer stores As-Of in


def _parse_as_of(s: str):
    """Parse a stored 'As-Of (UTC)' string back to an aware UTC datetime, or None
    on blank/garbage (never raises — a parse miss must not fire a misleading alert
    or break the poll)."""
    if not s or not str(s).strip():
        return None
    try:
        return datetime.strptime(str(s).strip(), _AS_OF_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def liveness_decision(newest_as_of: str, now_utc: datetime, max_stale_days: int,
                      warned_as_of) -> tuple[bool, int | None]:
    """Pure. Returns (should_warn, stale_days).

    should_warn is True iff the newest reading we've ingested is at least
    max_stale_days old AND we have not already warned for this exact As-Of (the
    once-per-episode gate — self-resetting because As-Of is monotonic, so a later
    stall carries a newer As-Of that differs from `warned_as_of`). stale_days is the
    integer age in whole days, or None if `newest_as_of` is unparseable (in which
    case should_warn is False — we do not fire a 'stale' alert we can't quantify;
    the caller logs that case loudly instead). `now_utc` is injected so this is
    fully unit-testable."""
    dt = _parse_as_of(newest_as_of)
    if dt is None:
        return False, None
    stale_days = int((now_utc - dt).total_seconds() // 86400)
    if stale_days < max_stale_days:
        return False, stale_days
    if warned_as_of and str(warned_as_of).strip() == str(newest_as_of).strip():
        return False, stale_days                 # already warned for this episode
    return True, stale_days


def format_liveness_body(newest_as_of: str, stale_days: int, max_stale_days: int,
                         link: str) -> str:
    """The stale-feed email body — deliberately NOT the exceedance formatter and
    clearly labeled a feed-health notice, never an exceedance."""
    return "\n".join([
        "GFL Arbor Hills perimeter air monitoring — LIVENESS / feed-health notice.\n",
        "This is NOT an exceedance and NOT a reading. The poller has seen NO new "
        f"perimeter readings for {stale_days} day(s) (alert threshold: "
        f"{max_stale_days}).\n",
        f"Newest reading on record: {newest_as_of} (UTC).\n",
        "A healthy feed updates roughly hourly, so a multi-day silence means it may "
        "have stalled — e.g. the ArcGIS service was rebuilt and the OBJECTID cursor "
        "no longer advances (ADR 014's OBJECTID-reset residual), or the source went "
        "offline. The rest of the pipeline is intact; it simply isn't seeing "
        "anything new.\n",
        "Check: open the dashboard and see whether new readings are appearing there. "
        "If they are but this monitor is not, the stored cursor likely needs a "
        "reset.\n",
        f"Live dashboard:\n  {link}\n",
        "(You will not get another liveness alert for this same stall — it fires "
        "once per stale episode, re-arming only after the feed recovers.)",
    ])
