"""
well_aliases.py — the curated well-ID alias map (ADR 068): one loader, one
exact-match rule, shared by the WOI table path (Part A) and the Measurements /
egle_doc_parser path (Part B), so one physical well's history stops fragmenting
across id spellings (e.g. the 0-padded era `AHWW0290` vs the R-suffix era
`AHWW290R`).

SOURCE OF TRUTH (ADR 068 addendum 2026-10-22): `data/well_registry.json`, in this
repo, holds every well-id ruling with its citation and date: the spelling aliases,
the wells ruled new (kept under their own ids) and the gas probes, labeled by GFL's
own "Probe ID" column. It is changed by PR only. `data/well_aliases.json` (the
{alias: canonical} map every caller loads) is GENERATED from it with
`python3 well_aliases.py --export` and pinned equal to it by a test; never edit it
by hand. Lotext's wellfield staging (and its alias relabel script) reads the
registry from this repo; a release build that labels probes will read it too.

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

import collections
import datetime
import json
import types
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable, Optional

ALIAS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "well_aliases.json")
REGISTRY_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "well_registry.json")
REGISTRY_SCHEMA_VERSION = 1

# Every alias and canonical id must be AH-form (the only form the WOI parser's
# WELL_RE can see, and the only form the 2026-10-12 ruling covers). A non-AH pair
# in a future export fails the loader (and CI) rather than silently widening the
# rule to short/spaced ids.
_AH_FORM = re.compile(r"^AH[A-Z0-9]+$")

# A documented GUARD, not active logic: identifiers that must never be renamed by
# any future normalization rule (they were listed in 2026-07 as flares, outfalls,
# probes, perimeter stations, lab QA labels and sensor strings; the registry, not
# this list, now says what a point IS: e.g. GFL prints the AHWWCO and AHWTR ids
# under its "Well ID" column). The loader refuses a map that uses one as an alias
# KEY. One may be an alias TARGET (a spelling variant mapped ONTO it, e.g.
# AHWTRO22 -> AHWTR022, Trisha 2026-10-19), which never renames the id itself. Literal ids only (the source
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
    alias), no key in EXCLUDE_FROM_NORMALIZATION (a target may be one)."""
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
    touched = sorted(set(aliases) & EXCLUDE_FROM_NORMALIZATION)
    if touched:
        raise WellAliasError(f"alias map would rename excluded ids: {touched}")


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
    _REG_CACHE.clear()


# ---------------------------------------------------------------------------
# The well registry (ADR 068 addendum 2026-10-22)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WellRegistry:
    """Every well-id ruling, loaded from data/well_registry.json. Read-only:
    `aliases` and `sources` are mapping proxies, so a caller can't edit the
    cached copy."""
    aliases: types.MappingProxyType   # {alias: canonical}
    new_wells: frozenset              # ruled new wells, kept under their own ids
    probes: frozenset                 # gas probes, by GFL's "Probe ID" label
    sources: types.MappingProxyType   # {id or alias: citation}

    @property
    def ruled_ids(self) -> frozenset:
        """Ids a human has already ruled on (never a rename candidate)."""
        return self.new_wells | self.probes | frozenset(self.aliases.values())

    def point_type(self, well_id) -> str:
        """"probe" for a GFL-labeled probe, else "well" (every other id GFL
        prints under its "Well ID" column)."""
        return "probe" if str(well_id or "").rstrip("*") in self.probes else "well"


EMPTY_REGISTRY = WellRegistry(types.MappingProxyType({}), frozenset(), frozenset(),
                              types.MappingProxyType({}))
_DATE_RE = re.compile(r"^20\d\d-\d\d-\d\d$")
_MAX_SOURCE_CHARS = 1000  # a citation, not a payload (the file passes the data guard)
_MAX_NOTE_CHARS = 1000
_MAX_REGISTRY_BYTES = 256 * 1024
_MAX_ENTRIES = 2000
_TOP_KEYS = {"_note", "schema_version", "counts", "aliases", "new_wells", "probes"}
_REG_CACHE: dict[str, WellRegistry] = {}


def _entries(doc: dict, key: str, fields: tuple) -> list:
    """The `key` list; each entry has exactly `fields` + source + ruled: the
    strings non-empty, `ruled` a YYYY-MM-DD date. Unknown fields are refused
    (the file is allowlisted past the data guard, so it must carry rulings
    only)."""
    items = doc.get(key)
    if not isinstance(items, list):
        raise WellAliasError(f"well registry has no '{key}' list")
    want = set(fields) | {"source", "ruled"}
    for e in items:
        if (not isinstance(e, dict) or set(e) != want
                or any(not isinstance(e.get(f), str) or not e.get(f).strip()
                       for f in fields + ("source",))
                or not _is_date(e.get("ruled"))
                or len(e.get("source") or "") > _MAX_SOURCE_CHARS):
            raise WellAliasError(f"well registry '{key}' entry must have exactly "
                                 f"{sorted(want)} (ruled = a real YYYY-MM-DD date, source "
                                 f"<= {_MAX_SOURCE_CHARS} chars): {e!r:.120}")
    return items


def _is_date(v) -> bool:
    if not (isinstance(v, str) and _DATE_RE.match(v)):
        return False
    try:
        datetime.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def validate_registry(doc) -> WellRegistry:
    """Raise WellAliasError unless `doc` is a clean registry; return it parsed.
    Checks: schema version and counts; every id AH-form; every entry cites a
    source; the alias map passes validate_alias_map (no self-alias, chain or
    excluded key); no id listed twice; a ruled new well is never an alias KEY
    (it may be a target: GFL prints AHWW0CS9 as AHWWCS09's alias); a probe is
    never an alias key or target; no id is both a new well and a probe."""
    if not isinstance(doc, dict):
        raise WellAliasError("well registry must be a JSON object")
    extra = sorted(set(doc) - _TOP_KEYS)
    if extra:
        raise WellAliasError(f"well registry has unknown top-level keys: {extra}")
    note = doc.get("_note", "")
    if not isinstance(note, str) or len(note) > _MAX_NOTE_CHARS:
        raise WellAliasError(f"well registry _note must be a string of <= {_MAX_NOTE_CHARS} chars")
    if sum(len(doc.get(k) or []) for k in ("aliases", "new_wells", "probes")) > _MAX_ENTRIES:
        raise WellAliasError(f"well registry has more than {_MAX_ENTRIES} entries")
    if doc.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise WellAliasError(f"well registry schema_version {doc.get('schema_version')!r} "
                             f"!= {REGISTRY_SCHEMA_VERSION}")
    al = _entries(doc, "aliases", ("alias", "canonical"))
    nw = _entries(doc, "new_wells", ("id",))
    pr = _entries(doc, "probes", ("id",))
    counts = doc.get("counts") or {}
    got = {"aliases": len(al), "new_wells": len(nw), "probes": len(pr)}
    if counts != got:
        raise WellAliasError(f"well registry counts {counts!r} != {got}")
    aliases = {e["alias"]: e["canonical"] for e in al}
    dup = [k for k, n in collections.Counter([e["alias"] for e in al] + [e["id"] for e in nw]
                                             + [e["id"] for e in pr]).items() if n > 1]
    if dup:
        raise WellAliasError(f"well registry lists an id twice: {sorted(dup)}")
    validate_alias_map(aliases)
    new_wells = frozenset(e["id"] for e in nw)
    probes = frozenset(e["id"] for e in pr)
    bad = sorted(i for i in new_wells | probes if not _AH_FORM.match(i))
    if bad:
        raise WellAliasError(f"well registry has non-AH-form ids: {bad}")
    renamed = sorted(new_wells & set(aliases))
    probe_alias = sorted(probes & (set(aliases) | set(aliases.values())))
    if renamed or probe_alias:
        raise WellAliasError(f"ruled new wells used as an alias key {renamed}; "
                             f"probes used as an alias key or target {probe_alias}")
    for k, v in aliases.items():
        # a curated pair may respell an R2 (AHWW423R2 -> AHW423R2) but never
        # change the redrill generation (R2 vs R3 are different physical wells)
        sk, sv = _rename_stem(k), _rename_stem(v)
        if sk and sv and sk[2] != sv[2]:
            raise WellAliasError(f"alias {k} -> {v} changes the redrill generation")
    sources = {e["alias"]: e["source"] for e in al}
    sources.update({e["id"]: e["source"] for e in nw + pr})
    return WellRegistry(types.MappingProxyType(dict(aliases)), new_wells, probes,
                        types.MappingProxyType(sources))


def check_alias_map_matches(alias_map: dict, registry: WellRegistry) -> None:
    """Run-start guard: the generated data/well_aliases.json must equal the
    registry's aliases. A registry edited without `--export` would otherwise
    have the monitor apply a stale map while Lotext applies the new one."""
    if dict(alias_map) != dict(registry.aliases):
        only_map = sorted(set(alias_map.items()) - set(registry.aliases.items()))
        only_reg = sorted(set(registry.aliases.items()) - set(alias_map.items()))
        raise WellAliasError(
            "data/well_aliases.json differs from data/well_registry.json (run "
            f"`python3 well_aliases.py --export`): only in the map {only_map[:5]}, "
            f"only in the registry {only_reg[:5]}")


def load_registry(path: Optional[str] = None) -> WellRegistry:
    """Load + validate data/well_registry.json (cached per path). Raises
    WellAliasError on a missing or malformed file: callers load it ONCE at run
    start, beside load_alias_map(), so a bad registry turns the run red."""
    path = path or REGISTRY_JSON
    if path in _REG_CACHE:
        return _REG_CACHE[path]
    try:
        if os.path.getsize(path) > _MAX_REGISTRY_BYTES:
            raise WellAliasError(f"well registry over {_MAX_REGISTRY_BYTES} bytes: {path}")
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError as e:
        raise WellAliasError(f"well registry not found: {path}") from e
    except (OSError, ValueError) as e:
        raise WellAliasError(f"well registry unreadable ({type(e).__name__}): {path}") from e
    _REG_CACHE[path] = validate_registry(doc)
    return _REG_CACHE[path]


def alias_json_payload(registry: WellRegistry) -> str:
    """The generated data/well_aliases.json text for `registry` (deterministic:
    sorted, no timestamp)."""
    return json.dumps({
        "_note": "GENERATED from data/well_registry.json (the source of truth for Arbor Hills "
                 "well-id rulings, ADR 068). Do not hand-edit; change the registry by PR and "
                 "run `python3 well_aliases.py --export`.",
        "count": len(registry.aliases),
        "aliases": dict(sorted(registry.aliases.items())),
    }, indent=2, ensure_ascii=False) + "\n"


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
    A relabeled reading keeps its as-filed id (footnote `*` stripped) in `raw_well_id` (written to the
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
            # Stars are footnote markers, stripped exactly as the WOI path does,
            # so the As-Filed column holds one form per printed id.
            m.setdefault("raw_well_id", raw.rstrip("*"))
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
# Bare numbers need >= 2 digits (a lone "1" from a model-extracted id is noise).
_SHORT_FORM_RE = re.compile(
    r"^(?:(?:WELL\s*|W{1,2}\s*-?\s*)\d+(?:R\d*)?|\d+R\d*|\d{2,})$", re.I)


def _rename_stem(well_id: str):
    m = _STEM_RE.match(well_id)
    if not m:
        return None
    letters = re.sub("W+", "W", m.group(1))
    gen = m.group(3)
    gen = 1 if gen in (None, "", "1") else int(gen)
    return (letters, int(m.group(2)), gen)


def review_candidates(ids: Iterable, alias_map: Optional[dict],
                      ruled: Iterable = ()) -> list[str]:
    """Pure: the ids (after * strip + alias map) that look like an unresolved
    rename or a short form. Sorted, deduplicated. Ids in `ruled` (a registry's
    ruled_ids: ruled new wells, probes, canonical spellings) are never flagged:
    a human has already ruled on them."""
    ruled = frozenset(ruled or ())
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
    return sorted(flagged - ruled)


_LOGGED: set[str] = set()


def log_review_candidates(ids: Iterable, alias_map: Optional[dict], context: str = "",
                          ruled: Optional[Iterable] = None) -> list[str]:
    """Print each review candidate ONCE per process (`_needs_canonical_review`
    log line) and return the newly logged ones. Never raises. Ids already ruled
    on are skipped: `ruled` (the run's registry.ruled_ids, passed by the WOI
    router), else the committed registry's (cached; one that can't be read
    only skips that filter: QA logging never blocks)."""
    try:
        if ruled is None:
            try:
                ruled = load_registry().ruled_ids
            except WellAliasError:
                ruled = frozenset()
        new = [c for c in review_candidates(ids, alias_map, ruled) if c not in _LOGGED]
    except Exception as e:  # noqa: BLE001 — QA logging must never block ingest
        print(f"[well_aliases] review-candidate scan skipped: {type(e).__name__}")
        return []
    if new:
        _LOGGED.update(new)
        where = f" ({context})" if context else ""
        print(f"[well_aliases] _needs_canonical_review{where}: {len(new)} unmapped "
              f"id(s) look like a possible rename / short form (NOT mapped — rule on "
              f"them in data/well_registry.json, by PR): {', '.join(new)}")
    return new


def reset_review_log() -> None:
    _LOGGED.clear()


def _main(argv: list) -> int:
    """`--export`: regenerate data/well_aliases.json from the registry.
    `--check`: exit 1 if the committed file differs from the registry's export."""
    reg = load_registry()
    payload = alias_json_payload(reg)
    old = open(ALIAS_JSON, encoding="utf-8").read() if os.path.exists(ALIAS_JSON) else None
    if argv[:1] == ["--export"]:
        if old != payload:
            with open(ALIAS_JSON, "w", encoding="utf-8") as fh:
                fh.write(payload)
            print(f"{ALIAS_JSON}: WRITTEN ({len(reg.aliases)} aliases)")
        else:
            print(f"{ALIAS_JSON}: up to date ({len(reg.aliases)} aliases)")
        return 0
    if argv[:1] == ["--check"]:
        ok = old == payload
        print(f"{ALIAS_JSON}: {'matches' if ok else 'DIFFERS FROM'} the registry")
        return 0 if ok else 1
    print("usage: python3 well_aliases.py --export | --check")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
