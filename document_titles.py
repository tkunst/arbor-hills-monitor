"""document_titles.py — Arbor-Hills-specific generic-title detection + display-
name resolution (ADR 065).

Deliberately kept OUT of egle_doc_parser.py (the domain-agnostic Decode base):
this module knows about THIS repo's config shape (`document_titles:` in
config.yml) and about name_check.py (public-feed redaction), neither of which
egle_doc_parser.py depends on. egle_doc_parser.parse_document() instead takes a
plain `title_is_generic: bool` — this module is what decides that bool, and
what the final shown name is once the model has (maybe) proposed one.

Call shape (see watcher.py/backfill.py's chokepoint, right after
parse_document() returns):
    generic = title_is_generic(d["document_name"], cfg_exact, cfg_prefixes)
    parsed = parse_document(..., title_is_generic=generic)
    clean = sanitize_display_title(parsed.display_title) if generic else ""
    d["egle_title"] = d["document_name"]          # the raw nSITE title, preserved
    d["document_name"] = resolve_display_name(did, d["egle_title"], clean, overrides)
"""
from __future__ import annotations

import re

import name_check


def title_is_generic(title: str, generic_exact=(), generic_prefixes=()) -> bool:
    """True if `title` is a known-generic nSITE filing-system placeholder
    ("nForm Document", "Site", ...) or starts with a known-generic prefix
    ("Schedule - ") — both lists are caller-supplied (config.yml's
    `document_titles.generic_exact` / `generic_prefixes`), matched case-
    insensitively. An empty/blank title is never "generic" — it's handled by
    the plain nSITE-title fallback already (nothing to replace it with)."""
    t = (title or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(low == g.strip().lower() for g in generic_exact):
        return True
    if any(low.startswith(g.strip().lower()) for g in generic_prefixes if g.strip()):
        return True
    return False


def sanitize_display_title(display_title: str) -> str:
    """Strip-until-clean against name_check's RELIABLE denylist (personal
    names + internal markers) before a model-proposed display_title is ever
    allowed to reach the public case-file Sheet. This is a deterministic text
    edit, NOT a retry call to the model — re-asking the LLM would break
    egle_doc_parser's "no extra LLM call for new documents" contract.

    Each matched hit is removed from the text and the check re-run; if a
    second pass still finds a denylist hit (e.g. two overlapping matches), OR
    the result is empty/still unclean after that, returns "" so the caller
    falls back to the plain nSITE title rather than publish anything
    questionable. find_heuristic_hits (advisory, may false-positive) is
    deliberately NOT used here — same reasoning as is_clean_for_publish."""
    text = (display_title or "").strip()
    if not text:
        return ""

    for _ in range(4):  # a handful of matches is realistic; never loop forever
        hits = name_check.find_denylist_hits(text)
        if not hits:
            break
        # Longest match first: KNOWN_NAMES lists both a full name AND its bare
        # surname (e.g. "Testa" and "Anthony Testa") as independent entries,
        # both of which match the same span of text. Stripping the shorter
        # "Testa" first would leave "Anthony" behind — removing the longest
        # match first takes the whole name out in one pass.
        for h in sorted(hits, key=lambda h: len(h["match"]), reverse=True):
            text = re.sub(re.escape(h["match"]), "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s{2,}", " ", text).strip(" ,.-")

    if not text or not name_check.is_clean_for_publish(text):
        return ""
    return text


def resolve_display_name(doc_id: str, nsite_title: str, display_title: str,
                          overrides: dict) -> str:
    """Override precedence (ADR 065): config.yml's `document_titles.overrides`
    (doc_id -> name) > display_title (the classifier's proposed name, already
    sanitized by the caller and only ever non-empty when the nSITE title was
    generic) > the raw nSITE title. Display-only — never touches doc_id,
    doc_url, or Date Filed."""
    override = (overrides or {}).get(doc_id)
    if override:
        return override
    if display_title:
        return display_title
    return nsite_title
