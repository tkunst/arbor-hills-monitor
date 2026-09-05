"""findings_feed.py — pure render/format logic for the public "Findings" feed
(site/public-records/): every document from the case-file Sheet's New Documents +
Historical Documents tabs, newest first, paginated as static HTML.

Reads nothing, writes nothing, no Sheets API — scripts/gen_findings_feed.py is
the thin I/O wrapper that fetches the two tabs and calls build_pages(). Kept
pure so it's unit-tested directly, the same split sheet_writer.py uses between
its row-building functions and its API calls.
"""
from __future__ import annotations

import html
import re

# Only this one pure helper is reused from sheet_writer -- a plain string
# parse with zero I/O of its own (see its docstring), so importing it doesn't
# compromise this module's "no Sheets API" claim above.
from sheet_writer import _link_doc_id

# Same column order as sheet_writer.FEED_HEADERS — New Documents and Historical
# Documents share this schema (sheet_writer.feed_row()).
FEED_FIELDS = [
    "date_filed", "document_name", "type", "risks", "severity",
    "summary", "key_data_point", "link", "facility",
]

# 1,720 rows across both tabs as of 2026-08-21 (64 New + 1,656 Historical) —
# comfortably in the "well over 1,000" range the handoff flagged. A fixed
# N-per-page reverse-chronological list with prev/next scales to any future
# volume without a redesign, unlike a year/quarter or per-facility grouping
# (six facilities of wildly different volume — N2688 alone is most of the
# total — would make some groups pages-long and others one entry).
PAGE_SIZE = 50

# Only WRD's raw config.yml `name:` reads oddly to a public reader unfamiliar
# with EGLE's site-registry split — it's a distinct nSITE profile (the Land &
# Water Interface program) from N2688's own Documents profile, even though
# both track facilities at the same physical landfill (see config.yml's
# `facilities:` comment). Every other facility's raw name already reads fine
# as a public label, so this is a single, deliberate alias, not a general
# renaming layer.
FACILITY_DISPLAY = {
    "GFL-Arbor Hills Landfill-Washtenaw Co": "Arbor Hills Landfill (Land & Water Interface)",
}


def facility_display(name: str) -> str:
    return FACILITY_DISPLAY.get(name, name)


# The nSITE `downloadpdf` link (New/Historical Documents' Link column) works
# for the monitor's own authenticated fetch but errors for a human clicking it
# in a browser (confirmed 2026-08-20 debugging a reader's broken links — see
# sessions/2026-08-20-arbor-hills-term-search-and-dave-data-idea.md). The
# archiver's Drive mirror (ADR 007) is a public, browser-clickable copy of the
# same document, keyed by the same doc_id that's already embedded in the nSITE
# URL's own path. Extracting it here means no schema change to New/Historical
# Documents (which still carries the nSITE link, matching the digest/email
# path) — this is a display-time substitution the feed alone makes.


def doc_id_from_nsite_link(link: str) -> str | None:
    """The nSITE doc_id embedded in a Link URL, via sheet_writer._link_doc_id
    (the same parser purge_doc_rows relies on elsewhere in this repo) rather
    than a fresh regex here — it already strips the query string/trailing
    slash and handles both …/downloadpdf/<id> and …/downloadfile/<id> (the
    shape write_stub_row uses for an unprocessable-source doc), so reusing it
    means a future EGLE URL-shape change only needs fixing in one place.
    Translates its "" (nothing to parse) into None, which reads better at the
    call site below."""
    return _link_doc_id(link or "") or None


def resolve_display_link(nsite_link: str, archive_links: dict) -> str:
    """The durable Drive archive mirror when one exists for this doc_id,
    otherwise the original nSITE link unchanged (the archiver trails newly
    filed/backfilled docs by up to one nightly run, so a small minority have
    no mirror yet)."""
    doc_id = doc_id_from_nsite_link(nsite_link)
    return archive_links.get(doc_id, nsite_link) if doc_id else nsite_link


def resolve_display_links(rows: list[dict], archive_links: dict) -> list[dict]:
    """Swap every row's link for its archive mirror where one exists (see
    resolve_display_link). archive_links is {doc_id: archive_url}, already
    read from the Archived PDFs tab by the caller — this stays pure. Every
    row here always has a "link" key (parse_feed_rows always writes all
    FEED_FIELDS, padding a short row with ""), so `r["link"]` is safe; kept
    as a plain lookup rather than `.get()` so a genuinely malformed caller
    input (missing the key entirely) fails loudly instead of silently
    treating a bug upstream as "no link"."""
    return [dict(r, link=resolve_display_link(r["link"], archive_links)) for r in rows]


# EGLE's own filing metadata sometimes embeds a date in a document's title
# that doesn't match its actual Date Filed -- confirmed on a real doc: title
# read "On-Site Inspection (06/01/2025)", the actual inspection/filing date
# was 2026-06-18 (over a year off; an EGLE-side data entry artifact, not
# something the monitor introduced). Since Date Filed is already shown
# separately and reliably in the entry's meta line, a second, sometimes-wrong
# date baked into the title is confusing, not informative -- strip it.
#
# Checked before shipping this, not just assumed: does the title date ever
# carry real information Date Filed doesn't (e.g. a genuine "inspection
# conducted" date vs. a later "record filed" date)? Compared all 175
# parseable dated titles against their row's Date Filed: 152 (87%) match
# exactly, another 20 (11%) are within a year, and the only 3 over a year off
# are the SAME templated "On-Site Inspection (06/01/2025)" error repeated
# across three separate facilities' reports for the one real event (all
# actually filed 2026-06-18) -- a copy-pasted EGLE data-entry artifact, not
# an alternate meaningful date. No case in the live dataset loses real
# information by stripping.
_TRAILING_DATE_RE = re.compile(r'\s*\(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\)\s*$')

# document_name is EGLE filing-system free text (nsite_client's
# docMgmtDocDescr) with no length cap or whitespace normalization upstream --
# not attacker-controlled in the usual sense, but not trusted either. The
# regex above has no anchor forcing WHERE it starts scanning, so on a title
# that's mostly/entirely a long run of whitespace with no real date in it,
# re.sub retries the \s* backtrack at every candidate start position: O(n^2)
# on n. Measured in Step 6 security review of PR #46 (2026-08-21): ~16s on
# ~100k whitespace/tab characters, near Sheets' own 50k-char cell cap. Fixed
# by bounding the regex to a small fixed-size trailing slice rather than the
# whole title -- caps the search space to a constant regardless of title
# length, so the vector is gone, not just slower. A real date suffix
# ("(12/31/2026)" = 13 chars, generous headroom for leading/trailing
# whitespace) always fits well inside this window.
_DATE_SUFFIX_WINDOW = 40


def strip_embedded_date(name: str) -> str:
    """Drop a trailing "(MM/DD/YYYY)"-shaped date from a title before display.
    Only a BARE parenthetical date at the very end matches -- other
    parenthetical content ("(Updated 5/12/2022)", "(due by 3/15/2021)") is
    left alone, since that's part of the title's own meaning, not a redundant
    restatement of Date Filed. Confirmed against all 1,720 real Document Name
    values in the Sheet: 195 end in a bare date suffix, zero false positives
    on the other ~30 titles that merely contain a parenthetical."""
    # .rstrip() first (a plain linear scan, no backtracking -- safe even on
    # an all-whitespace pathological string) so unbounded trailing whitespace
    # AFTER a real date can't push the "(...)" itself outside the window
    # below; only then take the fixed-size trailing slice the regex runs
    # against.
    name = (name or "").rstrip()
    head, tail = name[:-_DATE_SUFFIX_WINDOW], name[-_DATE_SUFFIX_WINDOW:]
    return (head + _TRAILING_DATE_RE.sub("", tail)).strip()


# The feed only ever grows in normal operation (append-only Sheet tabs). A
# scheduled run that regenerates from a Sheet read gone wrong (an API response
# truncated to a handful of rows without actually raising — everything else
# in this pipeline raises loud on a real read failure, see gen_findings_feed's
# _tab_values) would otherwise auto-commit a collapsed public feed straight to
# main with no human in the loop. This is the trip-wire scripts/
# gen_findings_feed.py checks before writing anything.
SHRINK_GUARD_RATIO = 0.9


def is_suspicious_shrink(previous_total: int | None, new_total: int) -> bool:
    """True when new_total looks like a bad read rather than real data loss —
    previous_total is None on the very first run (nothing to compare against,
    never suspicious)."""
    return previous_total is not None and previous_total > 0 and new_total < previous_total * SHRINK_GUARD_RATIO


def parse_feed_rows(raw_rows: list[list]) -> list[dict]:
    """Zip FEED_HEADERS-shaped Sheet rows into dicts. Pads a short row (a
    trailing blank cell is sometimes not written at all) and ignores any
    extra columns, so a header drift on either side doesn't crash this."""
    out = []
    for r in raw_rows:
        if not r:
            continue
        padded = list(r) + [""] * (len(FEED_FIELDS) - len(r))
        out.append(dict(zip(FEED_FIELDS, padded)))
    return out


def _sort_newest_first(rows: list[dict]) -> list[dict]:
    """Newest Date Filed first. Date Filed is normally ISO (YYYY-MM-DD); a
    handful of Hand-Curated Files rows only know a partial ISO-prefixed date
    (e.g. "2022-03") -- a plain string sort still orders those correctly
    relative to full dates (a shorter string that's a prefix of a longer one
    sorts before it in Python), and never raises regardless of format. A
    NON-ISO date_filed (e.g. "3/15/2021"), were one ever entered, would sort
    by lexicographic accident rather than raise -- silently misplaced, not
    crashed. Hand-Curated Files has no format validation on doc_date today
    (a manually-populated column, unlike the auto tabs' pipeline-guaranteed
    ISO dates); all real rows as of this writing are ISO-prefixed. Python's
    sort is stable, so rows sharing an identical date_filed keep their
    relative input order."""
    return sorted(rows, key=lambda r: r["date_filed"], reverse=True)


def merge_and_sort(new_rows: list[list], historical_rows: list[list]) -> list[dict]:
    """New Documents + Historical Documents, newest Date Filed first."""
    return _sort_newest_first(parse_feed_rows(new_rows) + parse_feed_rows(historical_rows))


# Hand-Curated Files tab columns (docs/hand-curated-intake-design.md) -- a
# SEPARATE schema from New/Historical Documents' FEED_HEADERS, since these
# rows are entered by a human, not the classifier pipeline. See
# sheet_writer.TAB_HANDCURATED's docstring comment for the tab's origin.
HANDCURATED_FIELDS = [
    "curated_filename", "title", "source", "doc_date", "facility",
    "doc_type", "risks", "origin_url", "note", "drive_link", "added_at",
    "folded_into_public",
]


def parse_handcurated_rows(raw_rows: list[list]) -> list[dict]:
    """Hand-Curated Files rows -> the same FEED_FIELDS-shaped dicts
    parse_feed_rows() produces for the auto tabs, so both merge into one
    sortable/renderable list. Mapping: doc_date -> date_filed, title ->
    document_name, doc_type -> type, risks -> risks, note -> summary
    (falls back to title when note is blank), drive_link -> link,
    facility -> facility. severity and key_data_point have no Hand-Curated
    equivalent -- left blank, which render_entry already tolerates (several
    auto rows are blank there too). Pads a short row the same way
    parse_feed_rows does, and skips a blank row entirely.

    Carries one field the auto tabs have no equivalent for: `source` (the
    issuing/holding body -- GFL, the operator, a township/county, a community
    group, an EGLE records request, ...). render_entry surfaces it as a
    "Source: ..." tag ONLY on rows that have it, so the public data-layer feed
    stays source-labeled once these non-EGLE records appear alongside EGLE
    filings (CLAUDE.md's "accuracy over posturing" data-layer rule). Auto/EGLE
    rows carry no `source` key at all, so nothing changes for them."""
    out = []
    for r in raw_rows:
        if not r:
            continue
        padded = list(r) + [""] * (len(HANDCURATED_FIELDS) - len(r))
        hc = dict(zip(HANDCURATED_FIELDS, padded))
        out.append({
            "date_filed": hc["doc_date"],
            "document_name": hc["title"],
            "type": hc["doc_type"],
            "risks": hc["risks"],
            "severity": "",
            "summary": hc["note"] or hc["title"],
            "key_data_point": "",
            "link": hc["drive_link"],
            "facility": hc["facility"],
            "source": hc["source"],
        })
    return out


def merge_handcurated(rows: list[dict], handcurated_rows: list[list]) -> list[dict]:
    """Merge Hand-Curated Files rows into an already-built feed list (typically
    merge_and_sort()'s output, optionally after resolve_display_links()),
    re-sorting the combined set newest-first. A separate merge step from
    merge_and_sort -- not folded into it -- because Hand-Curated Files uses an
    entirely different column schema (see parse_handcurated_rows). Some
    Hand-Curated records describe a document also present in the auto tabs
    under a generic classification (the intake design's ANNOTATION mode);
    this can double-list that document once auto, once curated. That is
    accepted, not de-duplicated -- see docs/hand-curated-intake-design.md and
    the overnight-coder handoff for this feature's Decision 3."""
    return _sort_newest_first(rows + parse_handcurated_rows(handcurated_rows))


def paginate(rows: list[dict], page_size: int = PAGE_SIZE) -> list[list[dict]]:
    """Always returns at least one page (possibly empty), so build_pages()
    still emits a valid index.html when the feed is empty."""
    if not rows:
        return [[]]
    return [rows[i:i + page_size] for i in range(0, len(rows), page_size)]


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def render_entry(row: dict) -> str:
    """One finding as an HTML <article>. A blank optional field (summary, key
    data point) is left out of the markup entirely — never rendered as the
    literal "None" or an empty element. Some historical rows carry a blank
    Key Data Point (older backfill entries) or are stub rows for an
    unprocessable source (sheet_writer.write_stub_row); both must render
    cleanly, not crash the whole page. Risks (R1-R8) are deliberately never
    rendered here -- they're this project's own internal case-file taxonomy,
    meaningless to a public reader with no legend to decode them against."""
    date = _esc(row.get("date_filed"))
    name = _esc(strip_embedded_date(row.get("document_name") or "")) or "(untitled document)"
    doc_type = _esc(row.get("type"))
    severity = _esc(row.get("severity"))
    facility = _esc(facility_display(row.get("facility") or ""))
    # Every auto-tab producer of `link` builds it from a fixed nSITE base URL
    # + a numeric doc_id (see nsite_client.native_download_url) -- never
    # classifier or free-text output. Hand-Curated Files' `drive_link` is
    # different: it's typed by a human directly into a Sheet cell (see
    # parse_handcurated_rows), so `link` IS genuinely untrusted free-text on
    # that path. The scheme check below is this row's actual defense against
    # that input, not just defense in depth for a path that doesn't exist: an
    # http(s) link renders as a clickable href (still HTML-escaped by _esc,
    # so an attribute-breakout attempt fails closed too), anything else
    # (javascript:, data:, malformed text) renders as plain text instead of
    # ever reaching an <a href="..."> attribute. By the time a row reaches
    # here `link` may already be a Drive archive-mirror URL, not the
    # original nSITE one -- see resolve_display_link; both are https.
    raw_link = row.get("link") or ""
    link = _esc(raw_link) if raw_link.startswith(("http://", "https://")) else ""
    summary = _esc(row.get("summary"))
    kdp = _esc(row.get("key_data_point"))
    # `source` (issuing/holding body) is present only on Hand-Curated rows
    # (see parse_handcurated_rows) -- auto/EGLE rows have no `source` key at
    # all, so they never show a Source tag. Surfacing it keeps the public
    # data-layer feed source-labeled now that non-EGLE records (GFL, township,
    # county, community groups) sit alongside EGLE filings. A Hand-Curated row
    # with a BLANK source renders "Source: not stated" rather than silently
    # dropping the tag -- a missing source stays visible on the page (and is
    # reported by scripts/check_handcurated_sources.py), never quietly hidden.
    if "source" in row:
        src = _esc(row.get("source"))
        source_bit = f"Source: {src}" if src else "Source: not stated"
    else:
        source_bit = ""

    meta = " &middot; ".join(b for b in (date, facility, doc_type, severity, source_bit) if b)

    parts = ['<article class="finding">']
    if meta:
        parts.append(f'<p class="finding-meta">{meta}</p>')
    if link:
        parts.append(f'<h3><a href="{link}">{name}</a></h3>')
    else:
        parts.append(f'<h3>{name}</h3>')
    if summary:
        parts.append(f'<p>{summary}</p>')
    if kdp:
        parts.append(f'<p class="finding-kdp"><strong>Key data point:</strong> {kdp}</p>')
    parts.append('</article>')
    return "\n".join(parts)


def page_filename(page_num: int) -> str:
    """Page 1 is index.html (so /public-records/ works with no query string); every
    later page is page-N.html. Filenames are stable across regenerations —
    only the file count changes as the total row count changes."""
    return "index.html" if page_num == 1 else f"page-{page_num}.html"


def render_page(page_rows: list[dict], page_num: int, total_pages: int,
                 total_count: int, generated_at: str) -> str:
    entries = "\n".join(render_entry(r) for r in page_rows) or "<p>No documents found.</p>"

    nav_bits = []
    if page_num > 1:
        nav_bits.append(f'<a href="{page_filename(page_num - 1)}">&larr; Newer</a>')
    if page_num < total_pages:
        nav_bits.append(f'<a href="{page_filename(page_num + 1)}">Older &rarr;</a>')
    nav = " &middot; ".join(nav_bits)

    if page_num == 1:
        intro = (
            "<p>Every document the monitor has read for these facilities, newest "
            "first. The list runs back to the earliest record on file and "
            "updates automatically as new documents come in. Most are EGLE "
            "filings; hand-curated public records from other sources are "
            "labeled with their source.</p>\n"
        )
        title = "Public Records &middot; Arbor Hills Monitor"
    else:
        intro = ""
        title = f"Public Records, page {page_num} &middot; Arbor Hills Monitor"

    # The footer's "Generated ..." line (below) is prose-matched by
    # findings-feed.yml's `git diff --cached --quiet -I'...'` regex, which is
    # what lets a same-data rerun stay a no-op instead of committing all N
    # pages on every timestamp bump. If this line's wording or date format
    # ever changes, update that regex in the same commit — a silent mismatch
    # there brings back the exact daily-noise bug that guard exists to fix.
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="Every public regulatory record the Arbor Hills Monitor has collected for these facilities, newest first.">
<link rel="stylesheet" href="../style.css">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" href="/favicon.ico" sizes="any">
</head>
<body>
<div class="wrap">

<p><a href="../index.html">&larr; Arbor Hills Monitor</a></p>

<h1>Public Records on Arbor Hills</h1>
{intro}<p class="findings-count">{total_count:,} documents &middot; page {page_num} of {total_pages}</p>

<div class="findings-list">
{entries}
</div>

<p class="findings-nav">{nav}</p>

<footer class="site-footer">
<p>Generated {_esc(generated_at)} from the monitor's case file. An independent project. All source data is public regulatory records from Michigan EGLE and other public sources.</p>
</footer>

</div>
</body>
</html>
"""


def build_pages(rows: list[dict], generated_at: str, page_size: int = PAGE_SIZE) -> dict[str, str]:
    """rows: already merged+sorted (merge_and_sort()). Returns {filename: html}
    for every page — index.html plus page-2.html .. page-N.html."""
    pages = paginate(rows, page_size)
    total_pages = len(pages)
    total_count = len(rows)
    return {
        page_filename(i): render_page(page_rows, i, total_pages, total_count, generated_at)
        for i, page_rows in enumerate(pages, start=1)
    }
