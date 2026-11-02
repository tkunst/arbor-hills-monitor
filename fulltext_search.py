"""fulltext_search.py -- pure logic for the public records FULL-TEXT search
(ADR 062 addendum, 2026-11-02). No I/O, no network: the two scripts
`scripts/build_fulltext_cache.py` (download + extract) and
`scripts/build_fulltext_index.py` (Pagefind bundle) do the I/O around it.

Scope rules live HERE so both scripts and the tests share one copy:

- Only documents already in the PUBLISHED `site/public-records/search-index.json`
  are ever indexed. That file has already passed findings_feed's redaction and
  check_publish_safety's gate, and it only holds core-facility rows (related
  sites go to their own tab, ADR 064). Nothing is read from the Sheet directly.
- Hand-curated rows (entries carrying a "source" key) are EXCLUDED from full
  text: they can be third-party material, and the archive OAuth scope
  (drive.file) cannot read files a person uploaded anyway. They stay in the
  title/summary search.
- Every document's text goes through BOTH name lists the public feed uses --
  name_check's denylist (redact_denylist) and the REDACT_NAMES secret -- before
  it is cached and again before it is indexed (either list can grow between
  runs). REDACT_NAMES is matched WHOLE-WORD here (redact_secret_names), unlike
  findings_feed.redact_names: on whole documents a bare surname inside another
  word ("Hall" in "shall") is common, and findings_feed's per-match prefix scan
  is quadratic on a 3M-character text.
- Redaction always runs on the FULL text, before any capping: the text cache
  stores full redacted text, and prepare_content redacts first, then caps. So a
  full name's words are still adjacent whenever a list is applied, including a
  name added after the document was cached.
- The cap is a plain cut: the first PROSE_CAP_CHARS characters, verbatim, and
  nothing after them (review round 8: an earlier "each remaining distinct word
  once" tail produced word orders that are not in the document, shown as
  "matching text" and matchable as phrases). A longer document is marked
  `truncated` and the page says only its first part is searched.
"""
from __future__ import annotations

import re

import os

import name_check

PROSE_CAP_CHARS = 300_000

_DRIVE_ID = re.compile(r"^https://drive\.google\.com/(?:file/d/|open\?id=)([A-Za-z0-9_-]{10,})")
_NSITE_ID = re.compile(r"^https://[a-z0-9.-]*michigan\.gov/.*?/(?:downloadpdf|downloadfile)/(-?\d+)\b",
                       re.IGNORECASE)
# Same rule as search.js entryYear(): the date's four leading digits.
_YEAR = re.compile(r"^(\d{4})")
_WS = re.compile(r"\s+")


def doc_key(link: str) -> str | None:
    """A stable, filesystem-safe cache key for a published link, or None when the
    link is neither a Drive mirror copy nor an nSITE download link."""
    if not link:
        return None
    m = _DRIVE_ID.match(link)
    if m:
        return "drive_" + m.group(1)
    m = _NSITE_ID.match(link)
    if m:
        return "nsite_" + m.group(1)
    return None


def is_eligible(entry: dict) -> bool:
    """True when a search-index entry may have its full text indexed: it has a
    recognised link and is NOT a hand-curated row ("source" key present)."""
    if "source" in entry:
        return False
    return doc_key(entry.get("link") or "") is not None


def entry_year(entry: dict) -> str:
    """The year of the entry's own date (document date, else date filed --
    findings_feed._search_entry's precedence), or "" when there is none."""
    m = _YEAR.match(entry.get("date") or "")
    return m.group(1) if m else ""


def normalize(text: str) -> str:
    """Collapse ALL whitespace, line breaks included, to single spaces -- before
    any name redaction runs, so a name wrapped across lines ("Scott\nMiller")
    is one phrase again. Search needs no line structure."""
    return _WS.sub(" ", text or "").strip()


def _secret_name_re():
    """Whole-word regex over every REDACT_NAMES variant (full name down to the
    bare surname, longest first -- the same variants findings_feed builds), or
    None when the secret is unset. Read at call time so tests and CI env apply."""
    variants = set()
    for name in os.environ.get("REDACT_NAMES", "").split(","):
        toks = name.strip().split()
        for i in range(len(toks)):
            variants.add(" ".join(toks[i:]))
    if not variants:
        return None
    alt = "|".join(r"\s+".join(re.escape(p) for p in v.split())
                   for v in sorted(variants, key=len, reverse=True))
    return re.compile(r"\b(?:" + alt + r")\b", re.IGNORECASE)


def redact_secret_names(text: str) -> str:
    rx = _secret_name_re()
    return rx.sub("[name withheld]", text) if (rx and text) else text


def redact(text: str) -> str:
    """Both public-feed name lists, in the same order everywhere."""
    text, _ = name_check.redact_denylist(text)
    return redact_secret_names(text) or ""


def prepare_content(text: str, cap: int = PROSE_CAP_CHARS) -> str:
    """Redact, normalize, then cut one document's text to at most `cap`
    characters (at a word boundary when one is reasonably close)."""
    return _cut(redact(normalize(text)), cap)


def _cut(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    cut = text.rfind(" ", 0, cap)
    return text[: cut if cut > cap // 2 else cap]


def build_record(entry: dict, text: str, cap: int = PROSE_CAP_CHARS) -> dict | None:
    """One Pagefind custom record for a published entry, or None when the entry is
    ineligible or has no usable text. `url` is the entry's own published link (the
    Mirror copy), so a full-text hit opens the same document the list does."""
    if not is_eligible(entry):
        return None
    full = redact(normalize(text))
    content = _cut(full, cap)
    if not content:
        return None
    meta = {"title": entry.get("title") or "(untitled document)"}
    if len(content) < len(full):
        meta["truncated"] = "1"
    filters: dict[str, list[str]] = {}
    for field in ("date", "facility", "type", "severity"):
        if entry.get(field):
            meta[field] = entry[field]
    for field in ("facility", "type", "severity"):
        if entry.get(field):
            filters[field] = [entry[field]]
    year = entry_year(entry)
    if year:
        filters["year"] = [year]
    return {"url": entry["link"], "content": content, "meta": meta, "filters": filters,
            "sort": {"date": entry.get("date") or ""}}
