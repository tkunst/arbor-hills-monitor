"""
well_aliases.py — the curated well-ID alias map (ADR 068): one loader, one
exact-match rule, shared by the WOI table path (Part A) and the Measurements /
egle_doc_parser path (Part B), so one physical well's history stops fragmenting
across id spellings (e.g. the 0-padded era `AHWW0290` vs the R-suffix era
`AHWW290R`).

SOURCE OF TRUTH: the `ALIASES` dict in Lotext's wellfield release builder
(`merge_well_aliases.py`, outside this repo). It is exported — never hand-copied —
with `merge_well_aliases.py --export-only data/well_aliases.json`, and the export
is committed here (narrow `.gitignore` + data-guard allowlist for exactly that
path). New pairs are added THERE and re-exported; never edit the JSON by hand.

Rules (Trisha's 2026-08-30 + 2026-10-12 rulings):
  * Exact key match only, after stripping trailing `*` footnote markers. No
    fuzzy, short-form, spacing or "prepend AH" normalization anywhere.
  * Redrill generations (`*R2`, `*R3`) are different physical wells, never
    aliases. Nothing here infers one; only the curated map relabels.
  * A missing or malformed map fails LOUDLY (WellAliasError). Callers load it once
    at run start (watcher.run / backfill.run), outside any per-document try, so a
    bad map turns the run red instead of becoming per-doc poison strikes or a
    silent fallback to the generic parse.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterable, Optional

ALIAS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "well_aliases.json")

# Every alias and canonical id must be AH-form (the only form the WOI parser's
# WELL_RE can see, and the only form the 2026-10-12 ruling covers). A non-AH pair
# in a future export fails the loader (and CI) rather than silently widening the
# rule to short/spaced ids.
_AH_FORM = re.compile(r"^AH[A-Z0-9]+$")

# A documented GUARD, not active logic: identifiers that are NOT gas-extraction
# wells (flares, outfalls, probes, perimeter stations, lab QA labels, sensor
# strings) and must never be renamed by any future normalization rule. The
# loader refuses a map that touches one of them. Literal ids only (the source
# list's group/pattern labels — `ECS-*`, `260-series` — can't be enumerated).
# Carried over from the exclusion section of the superseded 2026-07-14 draft;
# the AHC4 ids it once listed were dropped (they are Cell 4 gas extraction
# wells, corrected 2026-10-10), as were the extraction-well prefixes `AHEW*`,
# which the curated map legitimately touches.
EXCLUDE_FROM_NORMALIZATION = frozenset({
    "EUENCLOSEDFLARE1", "EUENCLOSEDFLARE2",
    "GC-1", "GC-1R", "GC-2",
    "GP-14", "GP-15", "GP-16",
    "EGT-1", "TRBN4",
    "Flare_391_McGill", "Flare_392_Zink", "FLR_D",
    "AHTGP18D",
    "DISCHARGE 001A", "Effluent-001A", "001A", "Outfall-CR",
    "NPDES EAST POND", "NPDES WEST POND (B)", "NPDES WEST POND (M)",
    "East Pond", "West Pond",
    "MS-1", "MS-2", "MS-3", "MS-4", "MS-5", "MS-6", "MS-7",
    "MPB-393", "MPB-403",
    "SW-1", "SW-2", "ECS-4", "ECS-9", "M7",
    "DUPLICATE", "FIELD BLANK", "TOX",
    "Super Sump", "SCO2",
    "P3-6", "P3-15", "P8",
    "Purge Well 401", "Purge Well 403", "Purge Well 404",
    "EW-46R", "EW-64R2", "EW51R", "EWL-L9R",
    "WW-148R/DW01",
    "AHWTCH06", "AHWTCH07", "AHWTCH08", "AHWTCH09", "AHWTCH10", "AHWTCH11",
    "AHWTR022", "AHWTR023", "AHWTR024",
    "AHWWCO05", "AHWWCO06", "AHWWCO07", "AHWWCO08", "AHWWCO09", "AHWWCO10",
    "AHWWCO11", "AHWWCO12",
    "AHWWSC05", "AHWWSC06", "AHWWSC07",
    "AHWWTS01", "AHWTS01R",
    "AHWWDW01", "AH4ETGW1",
    "TE381", "TE391", "TS-01", "TS-01R", "TS-02", "TR-25", "TR-26",
})


class WellAliasError(RuntimeError):
    """The alias map file is missing or malformed. Never swallow this."""


def validate_alias_map(aliases) -> None:
    """Raise WellAliasError unless `aliases` is a clean {alias: canonical} map:
    AH-form strings, no self-alias, no chains (a canonical that is itself an
    alias), nothing touching EXCLUDE_FROM_NORMALIZATION."""
    if not isinstance(aliases, dict) or not aliases:
        raise WellAliasError("alias map must be a non-empty {alias: canonical} object")
    bad = [(k, v) for k, v in aliases.items()
           if not (isinstance(k, str) and isinstance(v, str)
                   and _AH_FORM.match(k) and _AH_FORM.match(v))]
    if bad:
        raise WellAliasError(f"non-AH-form or non-string alias entries: {bad[:5]}")
    selfs = sorted(k for k, v in aliases.items() if k == v)
    chains = sorted(k for k, v in aliases.items() if v in aliases)
    if selfs or chains:
        raise WellAliasError(f"self-aliases {selfs} / chains {chains}")
    touched = sorted((set(aliases) | set(aliases.values())) & EXCLUDE_FROM_NORMALIZATION)
    if touched:
        raise WellAliasError(f"alias map touches excluded non-well ids: {touched}")


_CACHE: dict[str, dict] = {}


def load_alias_map(path: Optional[str] = None) -> dict:
    """Load + validate the committed alias map (cached per path). Raises
    WellAliasError if the file is missing, unreadable, or malformed — including
    a `count` that disagrees with the number of pairs (a truncated export)."""
    path = path or ALIAS_JSON
    if path in _CACHE:
        return _CACHE[path]
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError as e:
        raise WellAliasError(f"well alias map not found: {path}") from e
    except (OSError, ValueError) as e:
        raise WellAliasError(f"well alias map unreadable ({type(e).__name__}): {path}") from e
    if not isinstance(doc, dict) or "aliases" not in doc:
        raise WellAliasError("well alias map has no 'aliases' object")
    aliases = doc["aliases"]
    validate_alias_map(aliases)
    if doc.get("count") != len(aliases):
        raise WellAliasError(
            f"well alias map count {doc.get('count')!r} != {len(aliases)} pairs")
    _CACHE[path] = dict(aliases)
    return _CACHE[path]


def clear_cache() -> None:
    _CACHE.clear()


def canonical_well_id(well_id, alias_map: Optional[dict]):
    """Exact-match rule for the Measurements path: return the canonical id when
    `well_id` (trailing `*` stripped) is an alias key, else `well_id` UNCHANGED
    (unmapped ids keep their stars/spaces/case exactly as filed). Never raises:
    a None / non-string id passes through."""
    if not alias_map or not isinstance(well_id, str):
        return well_id
    return alias_map.get(well_id.rstrip("*"), well_id)


def canonicalize_measurements(measurements, alias_map: Optional[dict]) -> list[tuple]:
    """Relabel each measurement dict's `well_id` in place via canonical_well_id().
    A relabeled reading keeps its as-filed id in `raw_well_id` (written to the
    Measurements tab's "Well ID As Filed" column) so every change is auditable; an
    existing `raw_well_id` (set upstream by the WOI path) is never overwritten.
    Returns the (raw, canonical) pairs changed. Never raises."""
    changed: list[tuple] = []
    for m in measurements or []:
        if not isinstance(m, dict):
            continue
        raw = m.get("well_id")
        canon = canonical_well_id(raw, alias_map)
        if canon != raw:
            m["well_id"] = canon
            m.setdefault("raw_well_id", raw)
            changed.append((raw, canon))
    return changed


# ---------------------------------------------------------------------------
# _needs_canonical_review — surface POSSIBLE unmapped renames at QA (log only,
# never an auto-map). Two signals:
#   1. Two different AH-form ids in one batch (a report / a document) that share
#      a rename stem: same prefix letters (W-runs collapsed, so AHWW ~ AHW), same
#      well number (zero padding ignored), same redrill generation, where the
#      original and first-redrill spellings ("" vs "R") are one generation (that
#      is the 0-padded -> R-suffix era switch most curated pairs are). R2/R3/...
#      stay distinct, so redrills are never paired.
#   2. A short / spaced well-number form (`272R4`, `WW 237R3`, `Well 260R`) that
#      the exact-match rule deliberately leaves alone.
# ---------------------------------------------------------------------------

_STEM_RE = re.compile(r"^AH([A-Z]*?)0*(\d+)(?:R(\d*))?$")
_SHORT_FORM_RE = re.compile(r"^(?:WELL\s*|W{1,2}\s*-?\s*)?0*\d+(?:R\d*)?$", re.I)


def _rename_stem(well_id: str):
    m = _STEM_RE.match(well_id)
    if not m:
        return None
    letters = re.sub("W+", "W", m.group(1))
    gen = m.group(3)
    gen = 1 if gen in (None, "", "1") else int(gen)
    return (letters, int(m.group(2)), gen)


def review_candidates(ids: Iterable, alias_map: Optional[dict]) -> list[str]:
    """Pure: the ids (after * strip + alias map) that look like an unresolved
    rename or a short form. Sorted, deduplicated."""
    canon = set()
    for raw in ids:
        if not isinstance(raw, str) or not raw.strip():
            continue
        s = raw.strip().rstrip("*")
        if s in EXCLUDE_FROM_NORMALIZATION or raw in EXCLUDE_FROM_NORMALIZATION:
            continue
        canon.add((alias_map or {}).get(s, s))
    flagged: set[str] = set()
    by_stem: dict = {}
    for c in canon:
        stem = _rename_stem(c)
        if stem is not None:
            by_stem.setdefault(stem, set()).add(c)
        elif _SHORT_FORM_RE.match(c):
            flagged.add(c)
    for group in by_stem.values():
        if len(group) > 1:
            flagged |= group
    return sorted(flagged)


_LOGGED: set[str] = set()


def log_review_candidates(ids: Iterable, alias_map: Optional[dict], context: str = "") -> list[str]:
    """Print each review candidate ONCE per process (`_needs_canonical_review`
    log line) and return the newly logged ones. Never raises."""
    try:
        new = [c for c in review_candidates(ids, alias_map) if c not in _LOGGED]
    except Exception as e:  # noqa: BLE001 — QA logging must never block ingest
        print(f"[well_aliases] review-candidate scan skipped: {type(e).__name__}")
        return []
    if new:
        _LOGGED.update(new)
        where = f" ({context})" if context else ""
        print(f"[well_aliases] _needs_canonical_review{where}: {len(new)} unmapped "
              f"id(s) look like a possible rename / short form (NOT mapped — add "
              f"verified pairs to Lotext merge_well_aliases.py ALIASES): {', '.join(new)}")
    return new


def reset_review_log() -> None:
    _LOGGED.clear()
