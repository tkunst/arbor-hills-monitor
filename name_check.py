"""name_check.py -- deterministic, NO-LLM detection of personal names and
internal-only content in text bound for the PUBLIC Public Records feed.

No Claude / Anthropic / LLM API is used anywhere here: everything is a fixed
denylist + regular expressions, so it runs offline in CI and is fully
reproducible. Two layers, deliberately different in character:

- DENYLIST (reliable, near-zero false positives): the specific people known to
  appear in these records, plus internal-only markers (working-folder names,
  process phrases, internal file references) that must never reach the public
  page. `find_denylist_hits` is the RELIABLE signal -- it is what the
  dedupe-curate "redact the title until it is clean" loop keys on, because it
  only fires on real, listed items and therefore always converges.

- HEURISTIC (broad net for NOVEL names, may false-positive): structural
  name shapes -- parenthetical "(First Last)" / "(F. Surname)", "signed/by/attn
  X", possessive "X's" -- minus an ORG/TERM allowlist so agency/firm/chemistry
  parentheticals like "(Subsurface Oxidation)" or "(Solid Waste Section)" do
  not read as names. `find_heuristic_hits` is ADVISORY: it surfaces a possible
  new name for a human to confirm, but a false positive here must never be able
  to wedge the redaction loop (you cannot "remove" a non-name), so it is never
  the loop's convergence condition.

Used by:
  - the dedupe-curate intake check (redact a hand-curated title before it is
    written to the Hand-Curated Files tab), and
  - the findings-feed publish gate (block a deploy that would publish a listed
    name or internal marker on a hand-curated public field).

Extending it: add a newly-encountered person to KNOWN_NAMES; add an
org/firm/term that the heuristic wrongly flags to ORG_ALLOWLIST. Both are plain
lists -- no code change needed beyond the data.
"""
from __future__ import annotations

import re

# Specific individuals known to appear in these records (regulators, operator
# staff, correspondents, a records requester). Matched WHOLE-WORD, case-
# insensitive. Surnames are the reliable identifier; full names are listed too
# so a first-name-only mention is still caught. Extend as new people appear.
KNOWN_NAMES = [
    # regulators / EGLE staff
    "Kovalchick", "Mike Kovalchick", "Konieczki", "Koniec", "Matt Konieczki",
    "Kavanaugh Vetort", "Vetort", "Diane Kavanaugh Vetort", "Kavanaugh",
    "Scott Miller", "Schwerin", "Gary Schwerin",
    # operator / GFL / consultant staff
    "Testa", "Anthony Testa", "Seegert", "David Seegert", "Dave Seegert",
    "Mark Johnson",
    # records requester
    "Drinan", "David Drinan",
]

# Internal-only content that must never reach the public page. These come from
# the hand-curated `note` field's working vocabulary (which the public feed
# does NOT publish -- see findings_feed; this is defense-in-depth in case such
# text ever lands in a PUBLISHED field like title or source_public). Matched as
# case-insensitive substrings.
INTERNAL_MARKERS = [
    "trisha",              # "Trisha-directed", "Trisha's FOIA Downloads"
    "full-circle",         # internal working-folder name
    "trashfire",           # internal working-folder name
    "fire-foia", "fire foia",
    "foia downloads",
    "hand-curated", "hand curated",
    "enforcement thread",
    "folded_into_public",
    ".md",                 # internal reference-doc links
    "advocacy page",
]

# Org / agency / firm / program / chemistry terms that appear in Title-Case
# parentheticals and are NOT people -- so the heuristic name patterns skip them.
# Kept generous on purpose: a missed org here only produces an advisory false
# positive a human dismisses, never a wrong publish.
ORG_ALLOWLIST = [
    "GFL", "EGLE", "DEQ", "AQD", "MMD", "WRD", "DPW", "BOC", "MMPC", "NPDES",
    "NESHAP", "USACE", "EPA", "YCUA", "CEC", "USD", "SEM", "SSO", "HOV", "WOI",
    "WDS", "ETLF", "RNG", "PFAS", "GCCS", "JPA", "ACO", "RA", "USEPA", "NSPS",
    "Tetra Tech", "Golder", "Golder Associates", "Midwestern Consulting",
    "Crandell", "Crandell Environmental", "Advanced Disposal", "Onyx",
    "Salem Township", "Jackson District", "Jackson District Office",
    "Solid Waste Section", "Air Quality Division", "Materials Management",
    "Materials Management Division", "Arbor Hills", "Washtenaw", "Michigan",
    "United States", "Waste Data System", "Subsurface Oxidation",
    "Consent Judgment", "Consent Judgement", "Consent Order", "Consent Decree",
    "Elevated Temperature Landfill", "Waste Data", "Remediation Area",
    "Well Type", "Well Master List", "Wellhead Protection", "Pic",
]


def _word_re(term: str) -> re.Pattern:
    # Whole-word, case-insensitive. \b on each side so "Testa" does not match
    # inside "attestation" and "Miller" does not match a substring.
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


_KNOWN_NAME_RES = [(_word_re(n), n) for n in KNOWN_NAMES]

# Heuristic name shapes (advisory).
_NAME_PARENS = re.compile(r"\(((?:[A-Z]\.?\s?){0,2}[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\)")
_NAME_SIGNED = re.compile(r"\b(?:signed|attn:?|contact)\s+([A-Z][a-z]+\s+[A-Z][a-z]+)")
_NAME_POSS = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)'s\b")


def _allowlisted(fragment: str) -> bool:
    """True if the candidate fragment is (or contains) an allowlisted org/term,
    so the heuristic should not treat it as a personal name."""
    for org in ORG_ALLOWLIST:
        if re.search(r"\b" + re.escape(org) + r"\b", fragment, re.IGNORECASE):
            return True
    return False


def find_denylist_hits(text: str) -> list[dict]:
    """RELIABLE hits: listed personal names + internal markers. Near-zero false
    positives, so this is the signal the redact-until-clean loop keys on. Each
    hit: {kind: 'known_name'|'internal_marker', match: <the matched text>}."""
    if not text:
        return []
    hits: list[dict] = []
    for rx, name in _KNOWN_NAME_RES:
        if rx.search(text):
            hits.append({"kind": "known_name", "match": name})
    low = text.lower()
    for marker in INTERNAL_MARKERS:
        if marker in low:
            hits.append({"kind": "internal_marker", "match": marker})
    return hits


def find_heuristic_hits(text: str) -> list[dict]:
    """ADVISORY hits: structural name shapes not already covered by the
    denylist and not allowlisted as an org/term. May false-positive -- surface
    for human review, never use as a hard loop-convergence condition. Each hit:
    {kind: 'heuristic_name', match: <candidate>}."""
    if not text:
        return []
    known = {h["match"].lower() for h in find_denylist_hits(text)}
    out: list[dict] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        c = candidate.strip()
        if not c or _allowlisted(c):
            return
        if c.lower() in seen or c.lower() in known:
            return
        seen.add(c.lower())
        out.append({"kind": "heuristic_name", "match": c})

    for m in _NAME_PARENS.finditer(text):
        add(m.group(1))
    for m in _NAME_SIGNED.finditer(text):
        add(m.group(1))
    for m in _NAME_POSS.finditer(text):
        add(m.group(1))
    return out


def scan(text: str) -> dict:
    """Both layers. {denylist: [...], heuristic: [...]}."""
    return {"denylist": find_denylist_hits(text), "heuristic": find_heuristic_hits(text)}


def is_clean_for_publish(text: str) -> bool:
    """True iff no RELIABLE (denylist) hit -- the convergence condition for the
    dedupe-curate redact-until-clean loop. Deliberately ignores heuristic hits
    so a false positive can never make a genuinely-clean title un-cleanable."""
    return not find_denylist_hits(text)


def _main(argv: list[str]) -> int:
    """CLI for the dedupe-curate title check: `python name_check.py "<title>"`.
    Prints denylist hits (must redact) and heuristic candidates (review).
    Exit 1 if the text is NOT publish-clean (has a denylist hit), else 0 -- so
    the intake process can loop: redact -> re-run -> stop when exit 0."""
    text = " ".join(argv).strip()
    if not text:
        print("usage: python name_check.py \"<text to check>\"")
        return 2
    result = scan(text)
    if result["denylist"]:
        print("NOT PUBLISH-CLEAN -- redact these before writing to the Sheet:")
        for h in result["denylist"]:
            print(f"  [{h['kind']}] {h['match']}")
    else:
        print("publish-clean: no personal names or internal markers found.")
    if result["heuristic"]:
        print("review (possible NEW name -- confirm, then add to KNOWN_NAMES or dismiss):")
        for h in result["heuristic"]:
            print(f"  [heuristic] {h['match']}")
    return 0 if is_clean_for_publish(text) else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
