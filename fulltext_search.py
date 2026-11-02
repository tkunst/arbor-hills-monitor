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
- Text is capped per document: the first PROSE_CAP_CHARS characters verbatim,
  then each remaining distinct word once -- minus every word that belongs to a
  listed name (listed_name_words), because phrase redaction cannot see a name
  whose words are no longer adjacent. This runs again at index time, so a name
  added to a list later is also dropped from text cached before it was added. A 300-page WOI table stays
  findable by well ID without storing every number in the public index.
"""
from __future__ import annotations

import re

import os

import name_check

PROSE_CAP_CHARS = 100_000
# Words longer than this in the de-duplicated tail are dropped (base64 blobs,
# run-together table rows): nobody searches for them and they bloat the index.
TAIL_MAX_WORD_LEN = 40

_DRIVE_ID = re.compile(r"^https://drive\.google\.com/(?:file/d/|open\?id=)([A-Za-z0-9_-]{10,})")
_NSITE_ID = re.compile(r"^https://[a-z0-9.-]*michigan\.gov/.*?/(?:downloadpdf|downloadfile)/(-?\d+)\b",
                       re.IGNORECASE)
_YEAR = re.compile(r"^(\d{4})(?:-|$)")
_WS = re.compile(r"\s+")
_WORD_EDGE = re.compile(r"^\W+|\W+$")


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


def listed_name_words() -> set[str]:
    """Every single word of every listed name (name_check.KNOWN_NAMES and
    REDACT_NAMES), lower-cased. Used on the de-duplicated tail, where a full
    name's words are no longer next to each other, so phrase matching cannot work
    there: any word that is part of a listed name is dropped from the tail."""
    names = list(name_check.KNOWN_NAMES) + os.environ.get("REDACT_NAMES", "").split(",")
    return {w.lower() for n in names for w in n.split() if w}


def prepare_content(text: str, cap: int = PROSE_CAP_CHARS) -> str:
    """Redact, normalize and cap one document's text for the public index."""
    text = redact(normalize(text))
    if len(text) <= cap:
        return text
    cut = text.rfind(" ", 0, cap)
    head = text[: cut if cut > cap // 2 else cap]
    seen = {w.lower() for w in head.split()}
    name_words = listed_name_words()
    tail = []
    for w in text[len(head):].split():
        lw = w.lower()
        if lw in seen or len(w) > TAIL_MAX_WORD_LEN or _WORD_EDGE.sub("", lw) in name_words:
            continue
        seen.add(lw)
        tail.append(w)
    return head + ("\n" + " ".join(tail) if tail else "")


def build_record(entry: dict, text: str, cap: int = PROSE_CAP_CHARS) -> dict | None:
    """One Pagefind custom record for a published entry, or None when the entry is
    ineligible or has no usable text. `url` is the entry's own published link (the
    Mirror copy), so a full-text hit opens the same document the list does."""
    if not is_eligible(entry):
        return None
    content = prepare_content(text, cap)
    if not content:
        return None
    meta = {"title": entry.get("title") or "(untitled document)"}
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
