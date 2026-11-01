# Overnight-coder handoff — Well-ID normalization (short<->full + cross-report renames)

*Staged 2026-08-30 from the reviewed worker-#68 draft; **REFRESHED 2026-10-12** (Trisha-directed):
the alias source moved, so "Source of truth", Part A, Part B and the deferred list below were
rewritten. Read `docs/overnight-coder.md` first. This wires a **curated alias map** into the existing `canonicalize(well_id, alias_map)`
hook so one physical well's history stops fragmenting across id variants. A live-parse path
is touched, so per Step 3 verify against real routed specimens, and per Step 8 open a **DRAFT
PR for Trisha's review, not an autonomous merge** — especially if you take Part B. Recommended
model tier: **Sonnet** (judgment about which variants are real + the A-vs-B scope fork; not Haiku).*

## Invocation

Branch name suggestion: `well-id-normalization`.

## Source of truth (REFRESHED 2026-10-12 -- the July 14 draft is SUPERSEDED)

- **The alias map is the `ALIASES` dict in Lotext's current wellfield release builder:**
  `/Volumes/Samsung-Pro-2TB/Cowork-claude/documents/arbor-hills/draft/arbor-hills-wellfield-data-2026-10-09/merge_well_aliases.py`
  (38 pairs as of 2026-10-10, every one verified + Trisha-approved; canonical spelling =
  EGLE's Well Master List). It is the single source of truth: the wellfield dataset build and
  the wellfield explorer already import it. **Do not hand-edit a copy; do not use the July 14
  `DRAFT-INTERNAL-well-id-alias-map-2026-07-14.md` table** (superseded; its HIGH/MODERATE/UNRESOLVED
  tiers and its short/spaced Measurements-Sheet forms are no longer the spec).
- **Get the map into this repo with the generator, not by copying:**
  `python3 <that path> --export-only /Volumes/Samsung-Pro-2TB/repos/arbor-hills-monitor/data/well_aliases.json`
  writes `data/well_aliases.json` (`{"_note", "count", "aliases": {alias: canonical}}`, sorted,
  deterministic). A copy was exported 2026-10-12 and sits in `data/` untracked: `*.json` is
  git-ignored and the data-guard CI blocks data files, so **add a narrow allowlist for exactly
  `data/well_aliases.json`** (a `!data/well_aliases.json` line in `.gitignore` plus whatever the
  data-guard check's allowlist needs) and commit it in the PR. Re-run the export just before
  committing so the committed file matches the source (assert `count == 38` or the current count).
- **What the 38 pairs are:** all AH-form -> AH-form spelling variants of ONE physical well, mostly
  0-padded era -> R-suffix era (`AHWW0312`->`AHWW312R`, `AHWW0177`->`AHWW177R`, ...), plus the
  2026-10-10 batch (`AHWW301`->`AHWW301R`, `AHWW523`->`AHWW0523`, `AHEW0023R`->`AHEW023R`,
  `AHEWW0081`->`AHEW0081`, `AHEWHW27`->`AHWWHW27`, `AHWC4106`->`AHC4W106`,
  `AHWW423R2`->`AHW423R2`). Note the direction is not always "add R": two map TO a 0-padded or
  non-R spelling because that is the Master List spelling. No self-aliases, no chains (the
  generator's `check_aliases()` enforces this; keep a test that does too).
- **Trisha's 2026-08-30 "HIGH-confidence core only" ruling is satisfied by construction**: the
  38-pair map contains only verified pairs. Anything not in it stays unmapped.

## The two code paths (verify these facts in Step 1)

*(Refreshed 2026-10-12: the old draft map was full of short/spaced Measurements-Sheet forms
(`272R4`, `WW 237R3`, `Well 260R`), which is why this fork mattered so much. The current 38-pair
map is all AH-form, so Part A now carries the whole map and Part B is a smaller question. The
hook facts below are unchanged; re-verify them in Step 1.)*

The ONLY place `canonicalize(well_id, alias_map)` is currently wired is
**`woi_table_parser.py`**, and that parser's `WELL_RE` is:

```python
WELL_RE = re.compile(r"^AH[A-Z0-9]+\*{0,4}$")   # AH-prefixed, no spaces
```

`extract_woi_well_list()` likewise only harvests `re.findall(r"AH[A-Z0-9]+\*{0,4}", t)`. So the
WOI-report parser **never sees a bare short-form or a spaced id** — the only alias entries that
can ever fire inside the wired hook are **AH-form -> AH-form** renames, which is exactly what all
38 current pairs are. Meanwhile `egle_doc_parser.py` (the Measurements/LLM path) emits `well_id` straight from the model with **no
canonicalization hook at all** (verified: it only has `well_id: Optional[str] = None`).

Therefore the build has two parts (both required since Trisha's 2026-10-12 ruling):

### Part A — REQUIRED, safe: populate + wire the map into the existing WOI hook
1. Load `ALIAS_MAP` from the committed `data/well_aliases.json` in a small `well_aliases.py`
   (one loader, cached; fail loudly if the file is missing or malformed) and keep
   `EXCLUDE_FROM_NORMALIZATION` as a documented constant there. The current `canonicalize()` already implements the needed
   behavior (strip `*`; if in map -> map; else as-is), so `EXCLUDE_FROM_NORMALIZATION` is a
   **documented guard** against any future blunt "prepend AH" rule, not active logic — keep it as
   a constant + a test that asserts excluded ids pass through untouched.
2. Thread `ALIAS_MAP` through the real call sites (currently they pass nothing):
   `woi_router.py:139` (`parse_gas_extraction(pdf_path)`),
   `woi_router.py:181` (`extract_woi_well_list(pdf_path)`), and `scripts/woi_summary.py:21,23`.
3. **`_needs_canonical_review` logging:** when `canonicalize()` sees an AH-form token that is
   neither a first-class known well nor in `ALIAS_MAP` but pattern-matches a possible rename
   (e.g. a zero-padded `AHWW0###` when a bare `AHWW###R` also exists), log it once so unresolved
   ids surface at QA rather than silently fragmenting. Keep it a log line, not an auto-map.

### Part B — REQUIRED (Trisha ruled 2026-10-12: apply the AH-form map to the Measurements path too)
The Measurements/`egle_doc_parser` path has no hook today. Add ONE post-classification
`canonicalize(well_id, ALIAS_MAP)` step where the classifier's `well_id` is finalized, before it is
written to the Measurements Sheet, using the SAME loader as Part A (one map, one code path).
- **Exact-match only.** Map a `well_id` only if, after stripping trailing `*`, it is a key in
  `ALIAS_MAP`. Do NOT add any fuzzy/short-form/spacing/"prepend AH" normalization here; short or
  spaced ids pass through unchanged (and may be logged via `_needs_canonical_review`).
- **Keep the original:** if the row schema has (or can cheaply get) a raw/original well-id field,
  store the pre-canonical value there so the change is auditable; if adding a Sheet column is not
  cheap, log the raw->canonical pairs instead and say so in the PR.
- **Spike first (Step 1):** re-pull the live Measurements tab, list the distinct `well_id` values,
  intersect with `ALIAS_MAP` keys, and report how many rows each key would relabel.
- **Existing Sheet rows (backfill):** do NOT rewrite live Sheet rows in this build. Ship a
  `scripts/backfill_well_aliases.py` that defaults to **dry-run** (prints per-key row counts and a
  sample), with an explicit `--apply` flag that is NOT run. Trisha runs `--apply` after review.
- This is a live-path change to the daily classifier output: real-specimen verify, and **DRAFT PR,
  not an auto-merge.**

## Verification (real specimens, not mock-green)
- Parse >=1 real WOI Status Report PDF (prefer one from before mid-2023 AND one after, since most
  pairs are an era switch); collect the distinct `raw_well_id` tokens; show which `ALIAS_MAP` keys
  actually fire. Report coverage honestly; dead entries are harmless.
- **Fragmentation-reduction signal:** run the WOI well join / `woi_summary` before and after; the
  canonical `AHWW177R` history should absorb the pre-rename `AHWW0177` rows (one timeline, not two).
- Keep every existing `woi_table_parser` / `test_woi_router` test green; add tests for: a mapped
  rename (`AHWW0177`->`AHWW177R`), an excluded id passing through, and an unmapped id logging
  `_needs_canonical_review`.

## Deferred / do-not-guess (REFRESHED 2026-10-12)
- Map ONLY what `merge_well_aliases.py` `ALIASES` contains. Do not add pairs found while building;
  log them via `_needs_canonical_review` and list them in the PR for Trisha (new pairs are added
  in Lotext's `ALIASES`, then re-exported).
- Redrill generations (`*R2`, `*R3`, e.g. `AHW272R3` vs `AHW272R4`) are different physical wells,
  never aliases.
- `AHWTS01R` vs `AHWWTS01` and the `391`/`392` flare-vs-well question from the old draft are not in
  the map: leave them unmapped.
- "AHC4" ids are Cell 4 gas extraction wells (corrected 2026-10-10), not compost-area monitors.

## Scope boundaries
- **In scope:** `well_aliases.py` loader + committed `data/well_aliases.json`, `EXCLUDE` constant, wiring into the WOI hook (Part A), the Measurements-path post-classification hook (Part B), `_needs_canonical_review` logging, the dry-run backfill script, tests, ADR, topology, PR.
- **OUT of scope — a SEPARATE axis:** the **redrill-lineage** merge (collapsing successive wells at
  one borehole, e.g. `AHWW285R`->`AHW285R2`->`AHW285R3`). That is NOT the alias map (aliases =
  same physical well under different id spellings; lineage = different physical redrills at one
  location) and C1 deliberately keeps them distinct. A working reference implementation exists in
  Lotext at `documents/arbor-hills/draft/arbor-hills-wellfield-explorer-2026-08-24/build_data.py`
  (`lineage_base` + zero-date-overlap gate; 632->579 locations, 47 merges, 12 kept-split) if a
  future lineage roadmap item wants it — but do NOT implement lineage in this build.
- Also out: `location_type` axis (`coder:location-type` / worker #67); the metric taxonomy
  (`coder:metric-taxonomy` / worker #66).

## PR requirements
ADR (record the map source, the 2026-08-30 and 2026-10-12 rulings, Parts A and B, and the
backfill plan) + tests + topology in the **same PR**. Open as a **draft PR for Trisha's review**
(live-path change); include the spike's per-key row counts and the backfill dry-run output in the
PR description.
