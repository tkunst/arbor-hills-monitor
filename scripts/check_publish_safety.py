#!/usr/bin/env python3
"""check_publish_safety.py -- the pre-publish GATE for the public Public Records
feed. Deterministic, NO-LLM (see name_check.py).

Scans the GENERATED HTML in site/public-records/ -- the EXACT bytes about to be
committed and deployed -- NOT a fresh re-read of the Sheet. That matters: the
generator and a separate Sheet re-read could diverge (e.g. a silently-truncated
API response), which would let a name the generator already wrote slip past a
gate that re-derived from a different read. Scanning the artifact closes that
window by construction, and it also covers EVERY rendered field, not just the
two the generator maps by name.

Each rendered <article> is classified by whether its finding-meta carries a
"Source:" tag -- present only on HAND-CURATED rows (see findings_feed.
render_entry), absent on auto/EGLE rows:

- HAND-CURATED article -> HARD BLOCK on any denylist OR heuristic (name-shaped)
  hit. Fail-safe: a name or name-shaped token in a published hand-curated field
  fails this step, so findings-feed.yml's "Commit and push" never runs and
  nothing deploys. A human clears it by rewording the Sheet field, or (for a
  genuine org/term the heuristic mis-flags) by extending name_check.ORG_ALLOWLIST.
- AUTO article -> WARN only. The live auto feed already carries some names (from
  nSITE titles / the classifier); that pre-existing exposure is a separate,
  larger cleanup and deliberately does NOT block the daily regeneration here
  (scope decision, 2026-09-05).

BACKSTOP, NOT ABSOLUTE FILTER: being deterministic + no-LLM, this catches KNOWN
names (denylist) and NAME-SHAPED tokens (heuristic: parenthetical / "signed X" /
possessive). A brand-new person's name written in ORDINARY PROSE (e.g. "letter
from John Smith to EGLE") is not name-shaped and would pass. The PRIMARY control
is human redaction of the published fields at curation time -- the dedupe-curate
title check + the curated `source_public` column; this gate backstops known
names and name-shapes.

Usage: python3 scripts/check_publish_safety.py   (run AFTER gen_findings_feed.py)
No Sheet/network access -- reads only the generated HTML files on disk.
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import name_check  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "site", "public-records")
INDEX_PATH = os.path.join(OUT_DIR, "search-index.json")

_ARTICLE_RE = re.compile(r'<article class="finding">(.*?)</article>', re.DOTALL)
_META_RE = re.compile(r'<p class="finding-meta">(.*?)</p>', re.DOTALL)
_TITLE_RE = re.compile(r"<h3>(?:<a [^>]*>)?(.*?)(?:</a>)?</h3>", re.DOTALL)


def _visible_text(fragment: str) -> str:
    """Rendered text of an HTML fragment: strip tags, unescape entities."""
    return _html.unescape(re.sub(r"<[^>]+>", " ", fragment))


def _is_handcurated(article_html: str) -> bool:
    """A rendered article is hand-curated iff its finding-meta carries a
    "Source:" tag (render_entry emits that only for hand-curated rows)."""
    m = _META_RE.search(article_html)
    return bool(m and "Source:" in _html.unescape(m.group(1)))


def _title(article_html: str) -> str:
    m = _TITLE_RE.search(article_html)
    return _visible_text(m.group(1)).strip() if m else "(untitled)"


def evaluate_pages(pages: dict[str, str]) -> dict:
    """Pure: given {filename: html}, return
    {block:[...], warn_handcurated:[...], warn_auto:[...]}. `block` non-empty =>
    the gate must fail (exit 1). Hand-curated articles block on denylist OR
    heuristic hits; auto articles warn on denylist hits only."""
    block, warn_hc, warn_auto = [], [], []
    for html_text in pages.values():
        for art in _ARTICLE_RE.findall(html_text):
            text = _visible_text(art)
            deny = name_check.find_denylist_hits(text)
            heur = name_check.find_heuristic_hits(text)
            title = _title(art)
            if _is_handcurated(art):
                hits = deny + heur
                if hits:
                    block.append({"name": title, "hits": hits})
            elif deny:
                warn_auto.append({"name": title, "hits": deny})
    return {"block": block, "warn_handcurated": warn_hc, "warn_auto": warn_auto}


def evaluate_search_index(entries: list[dict]) -> dict:
    """Same rules as evaluate_pages(), applied to search-index.json entries
    (ADR 062 Phase 2) instead of rendered HTML articles. An entry is
    hand-curated iff it carries a "source" key at all (even ""; see
    findings_feed._search_entry's docstring) -- the JSON-side equivalent of
    _is_handcurated's "Source:" check on the rendered HTML. JSON values are
    already raw text (no HTML tags to strip), so the denylist/heuristic
    scanners run directly on the concatenated visible fields -- no
    _visible_text needed. Returns the same {block, warn_handcurated,
    warn_auto} shape as evaluate_pages() so the two can be merged at the
    call site."""
    block, warn_hc, warn_auto = [], [], []
    for entry in entries:
        text = " ".join(
            str(entry[k]) for k in ("title", "excerpt", "facility", "source")
            if entry.get(k)
        )
        deny = name_check.find_denylist_hits(text)
        heur = name_check.find_heuristic_hits(text)
        title = entry.get("title") or "(untitled)"
        if "source" in entry:
            hits = deny + heur
            if hits:
                block.append({"name": title, "hits": hits})
        elif deny:
            warn_auto.append({"name": title, "hits": deny})
    return {"block": block, "warn_handcurated": warn_hc, "warn_auto": warn_auto}


def _load_search_index(path: str) -> list[dict]:
    """[] if the file is missing (same "warn, don't crash" tolerance as the
    HTML loader -- gen_findings_feed.py runs before this script and always
    writes it, but a missing file here must not hard-crash the gate)."""
    if not os.path.exists(path):
        print(f"::warning::no {path} found -- run gen_findings_feed.py first. "
              f"Skipping the JSON index check.")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_pages(out_dir: str) -> dict:
    pages = {}
    if not os.path.isdir(out_dir):
        return pages
    for name in sorted(os.listdir(out_dir)):
        if name.endswith(".html"):
            with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                pages[name] = f.read()
    return pages


def _fmt(hits) -> str:
    return ", ".join(h["match"] for h in hits)


def main() -> int:
    pages = _load_pages(OUT_DIR)
    if not pages:
        print(f"::warning::no generated pages found in {OUT_DIR} -- run "
              f"gen_findings_feed.py first. Nothing to gate.")
        return 0

    result = evaluate_pages(pages)

    index_entries = _load_search_index(INDEX_PATH)
    if index_entries:
        index_result = evaluate_search_index(index_entries)
        result["block"] += index_result["block"]
        result["warn_auto"] += index_result["warn_auto"]

    for w in result["warn_auto"]:
        print(f"::warning title=Name in auto feed (pre-existing, not blocking)::"
              f"{w['name']} -- {_fmt(w['hits'])}")

    if result["block"]:
        for b in result["block"]:
            print(f"::error title=Personal name / name-shaped token in a PUBLISHED "
                  f"hand-curated field::{b['name']} -- {_fmt(b['hits'])}")
        print(f"\nPUBLISH BLOCKED: {len(result['block'])} hand-curated record(s) would "
              f"publish a personal name or name-shaped token. Reword the title / "
              f"source_public in the Hand-Curated Files Sheet (keep org/role/date, "
              f"drop the person), or -- if a flagged token is a genuine org/term -- "
              f"add it to name_check.ORG_ALLOWLIST. Then re-run.")
        return 1

    n_auto = len(result["warn_auto"])
    print(f"publish-safety OK: no personal names or name-shaped tokens in any "
          f"PUBLISHED hand-curated field across {len(pages)} page(s)."
          + (f" ({n_auto} pre-existing auto-feed name warning(s).)" if n_auto else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
