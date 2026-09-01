"""public_comment_feed.py -- pure render/bucket logic for the public
"Open Public Comment Periods" page (site/public-comment/).

Companion to findings_feed.py, and built the same way: this module reads
nothing and writes nothing (no Sheets API, no HTTP), so it is unit-tested
directly. scripts/gen_public_comment_feed.py is the thin I/O wrapper that
live-fetches the active EGLE public notices (and the statewide ROP notice),
turns them into the entry dicts this module renders, and writes the static
HTML.

Editorial note: this page is FACTUAL RECORDS -- it lists what comment periods
are open, when they close, and links to the official EGLE notice. It does NOT
tell anyone to comment. The "submit a comment" call to action lives on the
orange advocacy site. See docs/editorial-standards-two-sites.md.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime

# The MiEnviro public-notice details page. The notice_id is embedded in the
# URL path; it can be negative (EGLE uses signed 64-bit ids), and the details
# page accepts it verbatim, sign included -- confirmed live against both a
# positive id (the Wetland 1 JPA) and a negative one (the Arbor Hills Energy
# ROP notice), see docs/decisions/035-public-comment-tracker.md.
NOTICE_INFO_BASE = "https://mienviro.michigan.gov/ncore/external/publicnotice/info/"

# EGLE's monthly statewide Renewable Operating Permit public notice (a single
# PDF listing every facility currently open for ROP comment). Some facilities'
# ROP comment windows appear ONLY here, with no facility-specific nSITE notice
# (Emerald RNG's did in mid-2026), so the tracker surfaces this notice too.
ROP_NOTICE_URL = "https://www.egle.state.mi.us/aps/downloads/ROP/pub_ntce/ROP_Public_Notice.pdf"

# A comment window closing within this many days is flagged "closing soon".
CLOSING_SOON_DAYS = 7

# How far back a just-closed period stays listed (for transparency, so a reader
# can see a window they may have just missed rather than it vanishing silently).
RECENTLY_CLOSED_DAYS = 90


# --- Statewide ROP public-notice parsing -----------------------------------
#
# EGLE's statewide ROP notice PDF has three sections, in order: a "30-DAY PUBLIC
# COMMENT" section (facilities OPEN for comment), a "45-DAY EPA REVIEW" section
# (facilities whose comment period has ENDED and are now at EPA), and a "FINAL"
# section (issued). A whole-PDF text search for a facility is NOT enough: a
# facility can appear in the 45-DAY or FINAL section, where it is NOT open for
# comment. So we isolate the 30-DAY section and only read facilities from there
# (a real bug this guards against: Emerald RNG appeared in the 45-DAY section on
# 2026-09-01, i.e. comment ended, and a naive search would have shown it "open").
# The section also carries each facility's exact window ("... from <date> until
# <date>" or "... to <date>"), so we extract the close date too.

_ROP_DATE_RE = re.compile(r"\b(?:until|to)\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")


def _looks_like_toc_line(text: str, pos: int) -> bool:
    """A table-of-contents entry reads 'HEADER ......... 12' -- dots/spaces then a
    page number. The real section header does not. Used to tell the TOC copy of
    a header from the actual section header further down."""
    return bool(re.match(r"[ .]*\d", text[pos:pos + 40]))


def rop_30day_section(notice_text: str) -> str:
    """The text of the statewide ROP notice's '30-DAY PUBLIC COMMENT' section
    only (empty string if its header can't be found -- fail safe, so a PDF-format
    change yields zero ROP entries rather than wrong ones)."""
    start = None
    for m in re.finditer("30-DAY PUBLIC COMMENT", notice_text):
        if not _looks_like_toc_line(notice_text, m.end()):
            start = m.end()  # last non-TOC occurrence is the real header
    if start is None:
        return ""
    end = len(notice_text)
    for marker in ("45-DAY EPA REVIEW", "FINAL TITLE V"):
        for m in re.finditer(re.escape(marker), notice_text):
            if m.start() > start and not _looks_like_toc_line(notice_text, m.end()):
                end = min(end, m.start())
                break
    return notice_text[start:end]


def _us_date_to_iso(us_date: str) -> str | None:
    """'September 9, 2026' -> '2026-09-09'; None if unparseable."""
    try:
        return datetime.strptime(us_date.strip(), "%B %d, %Y").date().isoformat()
    except (ValueError, TypeError):
        return None


def parse_rop_30day_comment(notice_text: str, srns) -> dict[str, str | None]:
    """{srn: close_date_iso_or_None} for each of `srns` that is currently in the
    statewide ROP notice's 30-DAY PUBLIC COMMENT section. A SRN absent from that
    section is absent from the result (it is not open for comment, even if it
    appears elsewhere in the PDF). The value is the parsed close date, or None
    when the date phrasing can't be parsed (still open, just undated)."""
    section = rop_30day_section(notice_text)
    if not section:
        return {}
    out: dict[str, str | None] = {}
    for srn in srns:
        key = f"SRN: {srn}"
        i = section.find(key)
        if i < 0:
            continue
        nxt = section.find("SRN:", i + len(key))
        chunk = section[i:nxt if nxt != -1 else len(section)]
        m = _ROP_DATE_RE.search(chunk)
        out[srn] = _us_date_to_iso(m.group(1)) if m else None
    return out


def comment_link(notice_id: str) -> str:
    """The official MiEnviro details/comment URL for a facility notice, or ""
    when there is no notice_id (e.g. a statewide-ROP-only entry, which carries
    its own link instead)."""
    nid = (notice_id or "").strip()
    return f"{NOTICE_INFO_BASE}{nid}/details" if nid else ""


def _parse_iso(d: str) -> date | None:
    """Lenient YYYY-MM-DD parse; None on empty/malformed rather than raising,
    so one bad EGLE date never takes down the whole page render."""
    d = (d or "").strip()
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def days_between(end_date: str, today: str) -> int | None:
    """Whole days from `today` to `end_date` (positive = still in the future,
    0 = closes today, negative = already closed). None if either date is
    missing/unparseable."""
    e, t = _parse_iso(end_date), _parse_iso(today)
    if e is None or t is None:
        return None
    return (e - t).days


def bucket_entries(entries: list[dict], today: str) -> dict[str, list[dict]]:
    """Split entries into open / upcoming / recently-closed, each sorted for
    display. Open is sorted by soonest deadline first (undated entries, which
    can't be ordered by deadline, sort last). Recently-closed is most-recent
    first and trimmed to RECENTLY_CLOSED_DAYS."""
    t = _parse_iso(today)
    open_, upcoming, closed = [], [], []
    for e in entries:
        s = _parse_iso(e.get("start_date", ""))
        end = _parse_iso(e.get("end_date", ""))
        if t is not None and s is not None and s > t:
            upcoming.append(e)
        elif t is not None and end is not None and end < t:
            if (t - end).days <= RECENTLY_CLOSED_DAYS:
                closed.append(e)
        else:
            open_.append(e)

    # Soonest deadline first; undated (None) after all dated ones.
    open_.sort(key=lambda e: (_parse_iso(e.get("end_date", "")) is None,
                              _parse_iso(e.get("end_date", "")) or date.max))
    upcoming.sort(key=lambda e: _parse_iso(e.get("start_date", "")) or date.max)
    closed.sort(key=lambda e: _parse_iso(e.get("end_date", "")) or date.min,
                reverse=True)
    return {"open": open_, "upcoming": upcoming, "closed": closed}


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def _human_date(iso: str) -> str:
    """'2026-09-15' -> 'September 15, 2026'; the input returned unchanged if it
    can't be parsed. Built without strftime('%-d') so it's platform-portable."""
    d = _parse_iso(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}" if d else (iso or "")


def _deadline_meta(entry: dict, closed: bool = False) -> str:
    """The STATIC deadline text baked into the committed HTML. It carries ONLY
    the absolute date, never a "in N days" countdown or a "closing soon" flag --
    those are computed client-side (see the countdown script in render_page), so
    the committed bytes change only when a notice actually changes, not every day
    as a countdown ticks down. That is what keeps the workflow's diff-quiet guard
    meaningful (a daily-changing countdown would make every run a commit)."""
    end = (entry.get("end_date") or "").strip()
    if not end:
        return "Comment period open (see the notice for the exact deadline)"
    verb = "closed" if closed else "closes"
    return f"Comment {verb} {_esc(_human_date(end))}"


def render_entry(entry: dict, closed: bool = False) -> str:
    """One comment period as an HTML <article>, reusing the site's .finding
    styling so this page matches the Public Records feed. For an OPEN entry with
    a known close date, the <article> carries a `data-close` ISO date and an
    empty `.pc-countdown` span that the page's client-side script fills with a
    "in N days" / "closing soon" hint at view time. Those hints are deliberately
    absent from the committed HTML (see _deadline_meta)."""
    facility = _esc(entry.get("facility") or "(facility)")
    kind = _esc(entry.get("kind") or "")
    link = entry.get("link") or ""
    link = _esc(link) if link.startswith(("http://", "https://")) else ""
    note = _esc(entry.get("note") or "")

    end_iso = (entry.get("end_date") or "").strip()
    has_countdown = bool(end_iso) and not closed and _parse_iso(end_iso) is not None

    meta = _deadline_meta(entry, closed)
    if kind:
        meta = f"{kind} &middot; {meta}"
    if has_countdown:
        meta += '<span class="pc-countdown"></span>'

    open_tag = (f'<article class="finding" data-close="{_esc(end_iso)}">'
                if has_countdown else '<article class="finding">')
    parts = [open_tag, f'<p class="finding-meta">{meta}</p>']
    if link:
        parts.append(f'<h3><a href="{link}">{facility}</a></h3>')
    else:
        parts.append(f"<h3>{facility}</h3>")
    if note:
        parts.append(f'<p class="finding-kdp">{note}</p>')
    parts.append("</article>")
    return "\n".join(parts)


def _render_section(title: str, entries: list[dict], closed: bool = False,
                    empty_msg: str | None = None) -> str:
    if not entries and empty_msg is None:
        return ""
    body = ("\n".join(render_entry(e, closed) for e in entries)
            or f"<p>{_esc(empty_msg)}</p>")
    return (f'<h2>{_esc(title)}</h2>\n'
            f'<div class="findings-list">\n{body}\n</div>')


# Client-side progressive enhancement: turn each open entry's absolute close date
# into a "in N days" / "closing soon" hint at VIEW time. Kept out of the static
# HTML on purpose so the committed page is byte-stable day to day (see
# _deadline_meta). No external dependencies; the absolute date still shows with
# JavaScript disabled.
_COUNTDOWN_SCRIPT = """<script>
(function () {
  var now = new Date();
  var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  var arts = document.querySelectorAll('article.finding[data-close]');
  for (var i = 0; i < arts.length; i++) {
    var p = arts[i].getAttribute('data-close').split('-');
    if (p.length !== 3) continue;
    var close = new Date(+p[0], +p[1] - 1, +p[2]);
    var days = Math.round((close - today) / 86400000);
    var span = arts[i].querySelector('.pc-countdown');
    if (!span || days < 0) continue;
    span.textContent = days === 0
      ? ' (closes today)'
      : ' (in ' + days + ' day' + (days === 1 ? '' : 's') + ')';
    if (days <= 7) {
      var flag = document.createElement('strong');
      flag.textContent = ' (closing soon)';
      span.appendChild(flag);
    }
  }
})();
</script>"""


def render_page(buckets: dict[str, list[dict]], generated_at: str,
                errors: list[str] | None = None) -> str:
    """Full static HTML for site/public-comment/index.html.

    `generated_at` is a UTC timestamp string; because the per-entry countdown is
    computed client-side (not baked in), this footer timestamp is the ONLY line
    that changes on an otherwise-identical rerun, and the workflow's diff-quiet
    guard is keyed to it (see .github/workflows/public-comment.yml) -- if this
    wording or format changes, update that guard's regex in the same commit.
    Which bucket an entry lands in still depends on the date, but that only
    changes when a period actually opens or closes -- a real event worth a
    commit."""
    errors = errors or []
    open_ = buckets.get("open", [])
    n_open = len(open_)

    intro = (
        "<p>Public applications and permit actions for the Arbor Hills "
        "facilities that are currently open for public comment at Michigan "
        "EGLE, with their deadlines. Each entry links to the official EGLE "
        "notice, where you can read the filing and submit a comment. This page "
        "updates automatically from EGLE's public notices.</p>\n"
    )

    err_block = ""
    if errors:
        items = "\n".join(f"<li>{_esc(e)}</li>" for e in errors)
        err_block = (
            '<div class="data-links">\n'
            "<p>Some sources could not be checked on this run, so the list "
            "below may be incomplete. Please also check EGLE's portal "
            "directly.</p>\n"
            f"<ul>\n{items}\n</ul>\n</div>\n"
        )

    sections = [
        _render_section(
            "Open for comment", open_,
            empty_msg="No comment periods are open right now.",
        ),
    ]
    if buckets.get("upcoming"):
        sections.append(_render_section("Opening soon", buckets["upcoming"]))
    if buckets.get("closed"):
        sections.append(_render_section("Recently closed", buckets["closed"], closed=True))
    body = "\n\n".join(s for s in sections if s)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Public Comment Periods &middot; Arbor Hills Monitor</title>
<meta name="description" content="Open Michigan EGLE public-comment periods for the Arbor Hills Landfill complex, with deadlines and links to the official notices.">
<link rel="stylesheet" href="../style.css">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" href="/favicon.ico" sizes="any">
</head>
<body>
<div class="wrap">

<p><a href="../index.html">&larr; Arbor Hills Monitor</a></p>

<h1>Open Public Comment Periods</h1>
{intro}<p class="findings-count">{n_open} open for comment now</p>

{err_block}{body}

{_COUNTDOWN_SCRIPT}

<footer class="site-footer">
<p>Generated {_esc(generated_at)} from Michigan EGLE's public notices. This is an informational summary, not a legal notice; always confirm the deadline and details on EGLE's own portal before relying on them. An independent project. All source data is public regulatory filings from Michigan EGLE.</p>
</footer>

</div>
</body>
</html>
"""
