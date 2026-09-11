# Overnight-coder handoff — Well-ID normalization (short<->full + cross-report renames)

*Staged 2026-08-30 from the reviewed worker-#68 draft. Read `docs/overnight-coder.md`
first. This wires a **curated alias map** into the existing `canonicalize(well_id, alias_map)`
hook so one physical well's history stops fragmenting across id variants. A live-parse path
is touched, so per Step 3 verify against real routed specimens, and per Step 8 open a **DRAFT
PR for Trisha's review, not an autonomous merge** — especially if you take Part B. Recommended
model tier: **Sonnet** (judgment about which variants are real + the A-vs-B scope fork; not Haiku).*

## Invocation

Branch name suggestion: `well-id-normalization`.

## Source of truth

- **Curated alias map + exclude list + rulings:** the reviewed draft (Lotext, internal):
  `documents/arbor-hills/draft/DRAFT-INTERNAL-well-id-alias-map-2026-07-14.md`
  — Section 1 "HIGH-confidence aliases" table (~39 rows), the confirmed cross-report rename
  (Section 2), the `ALIAS_MAP` / `EXCLUDE_FROM_NORMALIZATION` code block near the end, and the
  "Needs Trisha" list. **That draft is the spec; this handoff is the build framing.** If any
  detail here disagrees with the draft, the draft's tables win.
- **Trisha's ruling (2026-08-30) — bake it in:** *ship the HIGH-confidence core only.* Concretely:
  the ~39 HIGH-confidence aliases **plus the one confirmed rename `AHWW0177` -> `AHWW177R`**
  (EGLE WOI-id update 6/22/2023). The **~25 UNRESOLVED + 6 MODERATE** entries stay **unmapped
  but flagged** (see `_needs_canonical_review` below) — do **not** guess them into the map; no
  per-id adjudication is owed by Trisha for this build. Validated-by-use: the HIGH-confidence
  core was consumed by the C1 spatial analysis and the 2026-08-29 thermal-cluster viz and held.

## The scoping fork you MUST resolve in Step 1 (this is the load-bearing part)

The draft's alias map was built from the **Measurements Sheet's** distinct Well IDs, which
include messy short/spaced/non-AH forms (`272R4`, `WW 237R3`, `Well 260R`, `502R`, `290`).
But the ONLY place `canonicalize(well_id, alias_map)` is currently wired is
**`woi_table_parser.py`**, and that parser's `WELL_RE` is:

```python
WELL_RE = re.compile(r"^AH[A-Z0-9]+\*{0,4}$")   # AH-prefixed, no spaces
```

`extract_woi_well_list()` likewise only harvests `re.findall(r"AH[A-Z0-9]+\*{0,4}", t)`. So the
WOI-report parser **never sees a bare short-form or a spaced id** — the only alias entries that
can ever fire inside the wired hook are **AH-form -> AH-form** cross-report renames (chiefly the
confirmed `AHWW0177` -> `AHWW177R`). Meanwhile `egle_doc_parser.py` (the Measurements/LLM path
where the short-forms actually appear) emits `well_id` straight from the model with **no
canonicalization hook at all** (verified: it only has `well_id: Optional[str] = None`).

Therefore the build splits into a safe part and a decision-gated part:

### Part A — REQUIRED, safe: populate + wire the map into the existing WOI hook
1. Define `ALIAS_MAP` and `EXCLUDE_FROM_NORMALIZATION` as documented module constants (in
   `woi_table_parser.py`, or a small `well_aliases.py` it imports) from the draft's HIGH-confidence
   rows + the confirmed rename. The current `canonicalize()` already implements the needed
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

### Part B — DECISION-GATED: normalization for the Measurements path
Most of the alias map (all the short/spaced forms) only matters in the Measurements/`egle_doc_parser`
path, which has no hook today. Adding one is a **live-path change to the daily classifier's output**.
**Do NOT silently wire this.** Instead: spike which raw `well_id` tokens the Measurements path
actually emits (re-pull the live tab), intersect with the map to show real coverage, and **STOP
with the draft PR presenting the A-only vs A+B choice for Trisha** — including where a
post-classification `canonicalize()` would sit and its backfill implications for existing Sheet
rows. Part B is the same shape of live-path+backfill risk that made `coder:metric-taxonomy` a
draft PR; treat it that way.

## Verification (real specimens, not mock-green)
- Parse >=1 real WOI Status Report PDF; collect the distinct `raw_well_id` tokens; show which
  `ALIAS_MAP` keys actually fire (dead entries are harmless but report the coverage honestly —
  for the WOI parser this may be just the `AHWW0177` rename, which is the point of Part A being small/safe).
- **Fragmentation-reduction signal:** run the WOI well join / `woi_summary` before and after; the
  canonical `AHWW177R` history should absorb the pre-rename `AHWW0177` rows (one timeline, not two).
- Keep every existing `woi_table_parser` / `test_woi_router` test green; add tests for: a mapped
  rename (`AHWW0177`->`AHWW177R`), an excluded id passing through, and an unmapped id logging
  `_needs_canonical_review`.

## Deferred / do-not-guess (per the ruling + the draft's "Needs Trisha")
- The **~25 UNRESOLVED + 6 MODERATE** aliases: leave unmapped + flagged; do not add to `ALIAS_MAP`.
- `AHWTS01R` vs `AHWWTS01` (same monitoring station, two formats): the draft *recommends* aliasing
  `AHWTS01R -> AHWWTS01`. It's not a gas well; include it only if it's AH-form and actually appears
  in the parsed corpus — otherwise leave it for Part B / QA.
- `391` / `392` flare-vs-well: unresolved in the draft (McGill/Zink flares). Do not map; flag.

## Scope boundaries
- **In scope:** `ALIAS_MAP`/`EXCLUDE` constants, wiring into the WOI hook, `_needs_canonical_review`
  logging, tests, ADR, topology, PR. Part B only as a **decision surfaced in the PR**, not built.
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
ADR (record the map source + the ruling + the A-vs-B fork + Part B recommendation) + tests +
topology in the **same PR**. Open as a **draft PR for Trisha's review** — Part A alone is low-risk,
but the PR should present the Part B decision, so it is not an auto-merge-on-green.
