"""home_glance.py -- pure render logic for the homepage GlanceCard and the
"Newest filing" feature card (site/index.html).

Standard library only: scripts/fill_home_glance.py runs it inside pages.yml,
which installs no Python dependencies. Its inputs are two facts, never a
hand-typed number:

- site/public-records/glance.json, written by gen_findings_feed.py from the
  same rows as the public feed (findings_feed.build_home_glance): the published
  document count and the newest filing by EGLE's Date Filed;
- the time of the last SUCCESSFUL scheduled run of daily.yml (the watcher that
  checks EGLE's filing systems), passed in by pages.yml.

"Last checked" is fail-safe: it renders only when that time is known and at
most CHECKED_MAX_AGE_HOURS old at build time, and an inline script on the page
removes it once it ages past the same limit in the reader's browser. No stale
timestamp, no error text (design handoff 2026-11-12, section 2d).
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# The count's label, kept in ONE place. No start year (Trisha 11/12): the count
# is the Public Records feed's own total, which reaches back to 1967. "on file",
# not "read" (refutation gate 11/12): the total includes a few stub rows for
# documents the monitor could not open.
COUNT_LABEL = "documents on file"

CHECKED_MAX_AGE_HOURS = 48
EASTERN = ZoneInfo("America/Detroit")

CARD_START, CARD_END = "<!-- glance:card -->", "<!-- /glance:card -->"
FEATURE_START, FEATURE_END = "<!-- glance:feature -->", "<!-- /glance:feature -->"

# AP style, matching the Monitor's other dates ("Oct. 30, 2020", "June 16, 2026").
_AP_MONTHS = ("Jan.", "Feb.", "March", "April", "May", "June", "July",
              "Aug.", "Sept.", "Oct.", "Nov.", "Dec.")


def format_filed(iso_day: str) -> str:
    """'2026-11-09' -> 'Nov. 9, 2026'. '' for anything unparseable."""
    try:
        d = date.fromisoformat((iso_day or "")[:10])
    except ValueError:
        return ""
    return f"{_AP_MONTHS[d.month - 1]} {d.day}, {d.year}"


def parse_utc(value: str | None) -> datetime | None:
    """An ISO-8601 timestamp (GitHub's '2026-11-12T11:23:45Z') -> aware UTC
    datetime, or None if blank/unparseable/naive."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def checked_is_fresh(checked: datetime | None, now: datetime) -> bool:
    """True only for a known check time 0..CHECKED_MAX_AGE_HOURS before now."""
    if checked is None:
        return False
    age = now - checked
    return timedelta(0) <= age <= timedelta(hours=CHECKED_MAX_AGE_HOURS)


def format_checked(checked: datetime) -> str:
    """'Nov 12, 6:00 AM ET' in America/Detroit -- the same text the page's
    design (toLocaleString en-US, month short, day, hour, 2-digit minute)
    produces."""
    local = checked.astimezone(EASTERN)
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%b} {local.day}, {hour}:{local:%M} {ampm} ET"


def _safe_link(link) -> str:
    link = link if isinstance(link, str) else ""
    return link if link.startswith("https://") else ""


def render_card(glance: dict, checked: datetime | None, now: datetime) -> str:
    """The GlanceCard's data lines (between CARD_START and CARD_END)."""
    lines = []
    count = glance.get("count")
    if isinstance(count, int) and count > 0:
        lines.append(f'<p class="big">{count:,}</p>')
        lines.append(f'<p class="bigl">{html.escape(COUNT_LABEL)}</p>')
    if checked_is_fresh(checked, now):
        iso = checked.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f'<p class="cline glance-checked" data-checked="{iso}">'
                     f"Last check of EGLE document listings <strong>{html.escape(format_checked(checked))}</strong></p>")
    newest = glance.get("newest") or {}
    link, title = _safe_link(newest.get("link")), newest.get("title") or ""
    if link and title:
        lines.append(f'<p class="cline">Newest filing: <a href="{html.escape(link)}">'
                     f"{html.escape(title)}</a></p>")
    return "\n".join(lines)


def render_feature(glance: dict) -> str:
    """The first feature card. Falls back to the Public Records page (live
    homepage wording) when no newest filing is known."""
    newest = glance.get("newest") or {}
    link, title = _safe_link(newest.get("link")), newest.get("title") or ""
    if not (link and title):
        return ('<a class="feat" href="public-records/"><span class="lab">Newest filing</span>'
                '<span class="ft">Public Records <span aria-hidden="true">&rarr;</span></span>'
                '<span class="fs">Every document the monitor has read, from the earliest '
                "record to the most recent filing.</span></a>")
    meta = " &middot; ".join(html.escape(p) for p in (
        newest.get("facility") or "",
        f"filed {format_filed(newest.get('date_filed'))}" if format_filed(newest.get("date_filed")) else "",
    ) if p)
    return (f'<a class="feat" href="{html.escape(link)}"><span class="lab">Newest filing</span>'
            f'<span class="ft">{html.escape(title)} <span aria-hidden="true">&rarr;</span></span>'
            + (f'<span class="fs">{meta}.</span>' if meta else "") + "</a>")


def _replace_block(page: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if len(pattern.findall(page)) != 1:
        raise ValueError(f"expected exactly one {start} ... {end} block")
    return pattern.sub(lambda _m: f"{start}\n{body}\n{end}", page)


def fill(page: str, glance: dict, checked: datetime | None, now: datetime) -> str:
    """site/index.html with both glance blocks re-rendered. Idempotent."""
    page = _replace_block(page, CARD_START, CARD_END, render_card(glance, checked, now))
    return _replace_block(page, FEATURE_START, FEATURE_END, render_feature(glance))


def load_glance(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("glance.json is not an object")
    return data
