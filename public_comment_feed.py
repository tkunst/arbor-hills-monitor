"""public_comment_feed.py -- pure render logic for the public "Public Comment
Periods" page (site/public-comment/).

Companion to findings_feed.py, and built the same way: this module reads
nothing and writes nothing (no Sheets API, no HTTP, no filesystem), so it is
unit-tested directly. scripts/gen_public_comment_feed.py is the thin I/O wrapper
that live-fetches EGLE's active public notices + the statewide ROP notice,
maintains the persisted state file (so closed periods are remembered after EGLE
drops them from its active feed), and writes the static HTML this module renders.

The page has two tables: OPEN periods (Facility / What / Opened / Closes / Link)
and CLOSED periods (Facility / What / Link / Outcome).

Editorial note: this page is FACTUAL RECORDS -- it lists what comment periods
are open, when they close, and links to the official EGLE notice. It does NOT
tell anyone to comment. The "submit a comment" call to action lives on the
orange advocacy site. See docs/editorial-standards-two-sites.md.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime

# The MiEnviro public-notice details page. The notice_id is embedded in the
# URL path; it can be negative (EGLE uses signed 64-bit ids), and the details
# page accepts it verbatim, sign included -- confirmed live against both a
# positive id (the Wetland 1 JPA) and a negative one (the Arbor Hills Energy
# ROP notice), see docs/decisions/035-public-comment-tracker.md.
NOTICE_INFO_BASE = "https://mienviro.michigan.gov/ncore/external/publicnotice/info/"

# EGLE's monthly statewide Renewable Operating Permit public notice (a single
# PDF listing every facility currently open for ROP comment, plus later-stage
# and issued permits). Some facilities' ROP comment windows appear ONLY here,
# with no facility-specific nSITE notice (Emerald RNG's did in mid-2026).
ROP_NOTICE_URL = "https://www.egle.state.mi.us/aps/downloads/ROP/pub_ntce/ROP_Public_Notice.pdf"

# A comment window closing within this many days is flagged "closing soon"
# (client-side only -- see the countdown script).
CLOSING_SOON_DAYS = 7

# Shown in the Outcome column when a period has closed but its disposition is not
# yet known/recorded (EGLE issues or denies the permit as a later, separate act).
PENDING_OUTCOME = "Pending (awaiting EGLE decision)"

# Auto-filled outcome when a closed ROP period reaches the statewide notice's
# FINAL (issued) section.
ISSUED_OUTCOME = "Renewable Operating Permit renewal issued"


# --- Statewide ROP public-notice parsing -----------------------------------
#
# EGLE's statewide ROP notice PDF has three sections, in order: a "30-DAY PUBLIC
# COMMENT" section (facilities OPEN for comment), a "45-DAY EPA REVIEW" section
# (facilities whose comment period has ENDED and are now at EPA), and a "FINAL
# TITLE V / ROP PERMITS" section (issued). A whole-PDF text search for a facility
# is NOT enough: a facility in the 45-DAY or FINAL section is NOT open for
# comment. So we isolate each section and read facilities from the right one (a
# real bug this guards against: Emerald RNG appeared in the 45-DAY section on
# 2026-09-01, i.e. comment ended, and a naive search would have shown it "open").
# The 30-DAY section also carries each facility's exact window ("... from <date>
# until <date>" or "... to <date>"), so we extract the close date too. The FINAL
# section tells us an ROP renewal was issued (an Outcome for a closed period).

_ROP_DATE_RE = re.compile(r"\b(?:until|to)\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")


def _looks_like_toc_line(text: str, pos: int) -> bool:
    """A table-of-contents entry reads 'HEADER ......... 12' -- dots/spaces then a
    page number. The real section header does not. Used to tell the TOC copy of
    a header from the actual section header further down."""
    return bool(re.match(r"[ .]*\d", text[pos:pos + 40]))


def _section(notice_text: str, start_marker: str, end_markers: tuple) -> str:
    """The text between `start_marker`'s real header and the first of
    `end_markers` after it ('' if the start header can't be found -- fail safe,
    so a PDF-format change yields zero results rather than wrong ones). Skips the
    table-of-contents copy of each header."""
    start = None
    for m in re.finditer(re.escape(start_marker), notice_text):
        if not _looks_like_toc_line(notice_text, m.end()):
            start = m.end()  # last non-TOC occurrence is the real header
    if start is None:
        return ""
    end = len(notice_text)
    for marker in end_markers:
        for m in re.finditer(re.escape(marker), notice_text):
            if m.start() > start and not _looks_like_toc_line(notice_text, m.end()):
                end = min(end, m.start())
                break
    return notice_text[start:end]


def rop_30day_section(notice_text: str) -> str:
    """The statewide ROP notice's '30-DAY PUBLIC COMMENT' section only."""
    return _section(notice_text, "30-DAY PUBLIC COMMENT",
                    ("45-DAY EPA REVIEW", "FINAL TITLE V"))


def _us_date_to_iso(us_date: str) -> str | None:
    """'September 9, 2026' -> '2026-09-09'; None if unparseable."""
    try:
        return datetime.strptime(us_date.strip(), "%B %d, %Y").date().isoformat()
    except (ValueError, TypeError):
        return None


def parse_rop_30day_comment(notice_text: str, srns) -> dict[str, str | None]:
    """{srn: close_date_iso_or_None} for each of `srns` currently in the
    statewide ROP notice's 30-DAY PUBLIC COMMENT section. A SRN absent from that
    section is absent from the result (not open for comment, even if it appears
    elsewhere in the PDF). Value is the parsed close date, or None when the date
    phrasing can't be parsed (still open, just undated)."""
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


def parse_rop_final(notice_text: str, srns) -> set:
    """SRNs that appear in the statewide ROP notice's 'FINAL TITLE V / ROP
    PERMITS' (issued) section -- used to fill a closed ROP period's Outcome as
    'renewal issued'."""
    section = _section(notice_text, "FINAL TITLE V", ())
    return {srn for srn in srns if section and f"SRN: {srn}" in section}


def comment_link(notice_id: str) -> str:
    """The official MiEnviro details/comment URL for a facility notice, or ""
    when there is no notice_id (e.g. a statewide-ROP-only entry, which carries
    its own link instead)."""
    nid = (notice_id or "").strip()
    return f"{NOTICE_INFO_BASE}{nid}/details" if nid else ""


def _parse_iso(d: str) -> date | None:
    """Lenient YYYY-MM-DD parse; None on empty/malformed rather than raising, so
    one bad EGLE date never takes down the whole page render."""
    d = (d or "").strip()
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def _short_date(iso: str) -> str:
    """'2026-09-15' -> '9/15/2026' (M/D/YYYY, no leading zeros). Kept compact so
    the date columns stay narrow enough to avoid a horizontal scrollbar; the
    input is returned unchanged if it can't be parsed."""
    d = _parse_iso(iso)
    return f"{d.month}/{d.day}/{d.year}" if d else (iso or "")


def sort_open(entries: list[dict]) -> list[dict]:
    """Open periods, soonest deadline first; an undated period (statewide ROP
    with no parseable close date) sorts last."""
    return sorted(entries, key=lambda e: (_parse_iso(e.get("closes", "")) is None,
                                          _parse_iso(e.get("closes", "")) or date.max))


def sort_closed(entries: list[dict]) -> list[dict]:
    """Closed periods, most recently closed first."""
    return sorted(entries, key=lambda e: _parse_iso(e.get("closes", "")) or date.min,
                  reverse=True)


def update_state(state: dict, open_entries: list[dict], fetched_srns: set,
                 rop_fetched: bool, rop_final_srns: set, today: str):
    """Pure state-transition for the persisted comment-period record.

    `state` is {key: record}, mutated in place. Upserts each currently-open
    period, and marks it **closed once its deadline has passed** (`end_date <
    today`) -- the deadline, not mere feed-presence, is what the Open/Closed split
    means, and EGLE can leave a notice in its feed a little past the close date.
    A period that has instead dropped out of the feed entirely (and whose source
    was actually checked this run, so a transient fetch error can't false-close
    it) is also closed -- that path covers undated periods the date test can't
    decide. Finally, a closed ROP period's Outcome is filled once its SRN reaches
    the statewide notice's FINAL/issued list. `outcome` is preserved from any
    existing record (so a hand-set outcome sticks); `what` is re-derived each run
    from the wrapper's label logic, so label improvements propagate. The `closed`
    flag flips exactly once (on the real close date), so this is churn-safe.
    Returns (open_for_render, closed_for_render)."""
    today_d = _parse_iso(today)
    open_keys = {e["key"] for e in open_entries}
    for e in open_entries:
        rec = state.get(e["key"], {})
        end_d = _parse_iso(e.get("closes", ""))
        ended = today_d is not None and end_d is not None and end_d < today_d
        state[e["key"]] = {
            "key": e["key"],
            "facility": e.get("facility", ""),
            "srn": e.get("srn", ""),
            "what": e.get("what", ""),        # re-derived each run (see the wrapper)
            "opened": e.get("opened", ""),
            "closes": e.get("closes", ""),
            "link": e.get("link", ""),
            "source": e.get("source", ""),
            "outcome": rec.get("outcome", ""),              # keep a curated outcome
            "closed": ended,      # past its deadline -> Closed even if still listed
        }
    for key, rec in state.items():
        if key in open_keys:
            continue
        checked = ((rec.get("source") == "rop" and rop_fetched)
                   or (rec.get("source") == "notice" and rec.get("srn") in fetched_srns))
        if checked:
            rec["closed"] = True
    if rop_fetched:
        for rec in state.values():
            if rec.get("closed") and not rec.get("outcome") and rec.get("srn") in rop_final_srns:
                rec["outcome"] = ISSUED_OUTCOME

    open_for_render = [state[e["key"]] for e in open_entries
                       if not state[e["key"]]["closed"]]
    closed_for_render = [r for r in state.values() if r.get("closed")]
    return open_for_render, closed_for_render


# --- Self-persisting state (embedded in the committed page) ----------------
#
# The repo forbids committed data files (`*.json` is gitignored AND blocked by
# the data-guard CI check), and its convention keeps state in the Sheet/Drive.
# Rather than add Sheet secrets to this otherwise secret-free page, state rides
# INSIDE the committed HTML as a hidden JSON block, and the generator reads its
# own previously-committed page back to recover it -- the same self-referential
# trick findings_feed uses (`_previous_total` reads its own committed index.html).
# The block holds only the public data already shown in the tables, so nothing
# private is embedded, and it is deterministic so a no-op run stays byte-stable.
_STATE_ID = "pc-state"
_STATE_FIELDS = ("key", "facility", "srn", "what", "opened", "closes", "link",
                 "source", "outcome")
_STATE_RE = re.compile(
    r'<script type="application/json" id="pc-state">\s*(.*?)\s*</script>', re.S)


def render_state_block(records: list[dict]) -> str:
    """A hidden, machine-readable snapshot of every tracked period, embedded in
    the committed page so the next run can recover state. Deterministic (sorted)
    for byte-stability."""
    clean = []
    for r in records:
        rec = {k: (r.get(k) or "") for k in _STATE_FIELDS}
        rec["closed"] = bool(r.get("closed"))
        clean.append(rec)
    clean.sort(key=lambda r: r["key"])
    payload = json.dumps({"periods": clean}, ensure_ascii=False, sort_keys=True,
                         indent=0)
    # json.dumps does NOT escape "<", so a "<" or "</script>" in any free-text
    # field (e.g. a hand-curated `what`) would terminate the block early and
    # break both the page and the next run's parse. Escaping "<" prevents that;
    # JSON.parse reads < back as "<".
    payload = payload.replace("<", "\\u003c")
    return f'<script type="application/json" id="{_STATE_ID}">\n{payload}\n</script>'


def parse_state_block(html_text: str) -> dict:
    """{key: record} recovered from a prior page's embedded state block; empty on
    the first run (no prior page) or if the block is missing/malformed."""
    m = _STATE_RE.search(html_text or "")
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        return {}
    return {r["key"]: r for r in data.get("periods", [])
            if isinstance(r, dict) and r.get("key")}


def _link_cell(url: str | None) -> str:
    url = (url or "").strip()
    if url.startswith(("http://", "https://")):
        return f'<a href="{_esc(url)}">View notice</a>'
    return "n/a"


# --- Table styling + client-side countdown ---------------------------------

_TABLE_STYLE = """<style>
.pc-scroll { overflow-x: auto; margin: 1.25rem 0 2rem; }
.pc-table { border-collapse: collapse; width: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 0.9rem; }
.pc-table th, .pc-table td { text-align: left; padding: 0.55rem 0.7rem;
  border-bottom: 1px solid var(--border); vertical-align: top; }
/* Headers wrap (so a long label like "Public comment opened" doesn't force the
   date column wide); the short M/D/YYYY dates themselves don't wrap. */
.pc-table th { font-weight: 700; color: var(--muted); }
.pc-table td:first-child { font-weight: 600; }
.pc-countdown { color: var(--muted); }
</style>"""

# Progressive enhancement: turn each open row's absolute close date into a
# "in N days" / "closing soon" hint at VIEW time. Kept OUT of the static HTML on
# purpose so the committed page is byte-stable day to day (a daily-changing
# countdown baked in would make the workflow's diff-quiet guard commit every
# run). No external dependencies; the absolute date still shows with JS off.
_COUNTDOWN_SCRIPT = """<script>
(function () {
  var now = new Date();
  var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  var rows = document.querySelectorAll('tr[data-close]');
  for (var i = 0; i < rows.length; i++) {
    var p = rows[i].getAttribute('data-close').split('-');
    if (p.length !== 3) continue;
    var close = new Date(+p[0], +p[1] - 1, +p[2]);
    var days = Math.round((close - today) / 86400000);
    var span = rows[i].querySelector('.pc-countdown');
    if (!span || days < 0) continue;
    span.textContent = days === 0
      ? ' (closes today)'
      : ' (in ' + days + ' day' + (days === 1 ? '' : 's') + ')';
    if (days <= 7) {
      var f = document.createElement('strong');
      f.textContent = ' (closing soon)';
      span.appendChild(f);
    }
  }
})();
</script>"""


def render_open_table(entries: list[dict]) -> str:
    """The OPEN table: Facility / What / Public comment opened / closes / Link.
    Each dated row carries a `data-close` ISO date + an empty `.pc-countdown`
    span for the client-side "in N days" hint (see _COUNTDOWN_SCRIPT)."""
    if not entries:
        return "<p>No comment periods are open right now.</p>"
    rows = []
    for e in sort_open(entries):
        closes_iso = (e.get("closes") or "").strip()
        dated = _parse_iso(closes_iso) is not None
        opened = e.get("opened") or ""
        opened_cell = _esc(_short_date(opened)) if opened.strip() else "n/a"
        if dated:
            closes_cell = f'{_esc(_short_date(closes_iso))}<span class="pc-countdown"></span>'
        elif closes_iso:
            closes_cell = _esc(_short_date(closes_iso))
        else:
            closes_cell = "See notice"
        tr = f'<tr data-close="{_esc(closes_iso)}">' if dated else "<tr>"
        rows.append(
            f"{tr}"
            f"<td>{_esc(e.get('facility'))}</td>"
            f"<td>{_esc(e.get('what'))}</td>"
            f"<td>{opened_cell}</td>"
            f"<td>{closes_cell}</td>"
            f"<td>{_link_cell(e.get('link'))}</td>"
            "</tr>"
        )
    head = ("<thead><tr><th>Facility</th><th>What</th>"
            "<th>Public comment opened</th><th>Public comment closes</th>"
            "<th>Link</th></tr></thead>")
    return ('<div class="pc-scroll"><table class="pc-table">'
            f"{head}<tbody>{''.join(rows)}</tbody></table></div>")


def render_closed_table(entries: list[dict]) -> str:
    """The CLOSED table: Facility / What / Link / Outcome."""
    if not entries:
        return ("<p>None yet. When a comment period closes it moves here, with "
                "the permit outcome once EGLE decides.</p>")
    rows = []
    for e in sort_closed(entries):
        rows.append(
            "<tr>"
            f"<td>{_esc(e.get('facility'))}</td>"
            f"<td>{_esc(e.get('what'))}</td>"
            f"<td>{_link_cell(e.get('link'))}</td>"
            f"<td>{_esc(e.get('outcome') or PENDING_OUTCOME)}</td>"
            "</tr>"
        )
    head = ("<thead><tr><th>Facility</th><th>What</th><th>Link</th>"
            "<th>Outcome</th></tr></thead>")
    return ('<div class="pc-scroll"><table class="pc-table">'
            f"{head}<tbody>{''.join(rows)}</tbody></table></div>")


def render_page(open_entries: list[dict], closed_entries: list[dict],
                generated_at: str, errors: list[str] | None = None) -> str:
    """Full static HTML for site/public-comment/index.html.

    `generated_at` is a UTC timestamp string; because the per-row countdown is
    computed client-side (not baked in), this footer timestamp is the ONLY line
    that changes on an otherwise-identical rerun, and the workflow's diff-quiet
    guard is keyed to it (see .github/workflows/public-comment.yml) -- if this
    wording or format changes, update that guard's regex in the same commit."""
    errors = errors or []
    n_open = len(open_entries)

    intro = (
        "<p>Applications and permit actions for the Arbor Hills facilities that "
        "are open for public comment at Michigan EGLE, with their deadlines. Each "
        "row links to the official EGLE notice, where you can read the filing and "
        "submit a comment. When a comment period closes it moves to the Closed "
        "table below. This page updates automatically from EGLE's public "
        "notices.</p>\n"
    )

    err_block = ""
    if errors:
        items = "\n".join(f"<li>{_esc(e)}</li>" for e in errors)
        err_block = (
            '<div class="data-links">\n'
            "<p>Some sources could not be checked on this run, so the tables "
            "below may be incomplete. Please also check EGLE's portal "
            "directly.</p>\n"
            f"<ul>\n{items}\n</ul>\n</div>\n"
        )

    body = (f"<h2>Open for comment</h2>\n{render_open_table(open_entries)}\n\n"
            f"<h2>Closed public comments</h2>\n{render_closed_table(closed_entries)}")

    # Hidden, machine-readable state for the next run (see render_state_block).
    state_block = render_state_block(list(open_entries) + list(closed_entries))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Public Comment Periods &middot; Arbor Hills Monitor</title>
<meta name="description" content="Michigan EGLE public-comment periods for the Arbor Hills Landfill complex -- open now with deadlines, and closed with outcomes.">
<link rel="stylesheet" href="../style.css">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" href="/favicon.ico" sizes="any">
{_TABLE_STYLE}
</head>
<body>
<div class="wrap">

<p><a href="../index.html">&larr; Arbor Hills Monitor</a></p>

<h1>Public Comment Periods</h1>
{intro}<p class="findings-count">{n_open} open for comment now</p>

{err_block}{body}

{state_block}

{_COUNTDOWN_SCRIPT}

<footer class="site-footer">
<p>Generated {_esc(generated_at)} from Michigan EGLE's public notices. This is an informational summary, not a legal notice; always confirm the deadline and details on EGLE's own portal before relying on them. An independent project. All source data is public regulatory filings from Michigan EGLE.</p>
</footer>

</div>
</body>
</html>
"""
