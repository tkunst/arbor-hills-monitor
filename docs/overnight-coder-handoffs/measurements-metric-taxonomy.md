# Overnight-coder handoff — Measurements metric taxonomy (decompose the `other` bucket)

*Staged 2026-08-25 from the reviewed worker-#66 draft. Read `docs/overnight-coder.md`
first. This IS a live-path change: it edits the Measurements extraction schema in
`egle_doc_parser.py` that the daily pipeline runs unconditionally — there is **no
`enabled:false` gate to hide behind**, so per Step 3 it must be verified against real
routed specimens before an autonomous merge, and per Step 8 a live-path change with a
data-backfill component should open a **DRAFT PR for Trisha's review, not auto-merge**.
Recommended model tier: **Sonnet** (schema + prompt judgment; not Haiku).*

## Invocation

Branch name suggestion: `metric-taxonomy`.

## Source of truth

- **Approved vocabulary + rulings:** the reviewed draft (Lotext, internal):
  `documents/arbor-hills/draft/DRAFT-INTERNAL-measurements-metric-taxonomy-2026-07-14.md`
  — the "Proposed named-metric vocabulary (~50 substances)" tables, the "Scope question
  resolution summary for the build" table, and the **"Trisha's rulings — 2026-08-25"**
  block at the bottom. That draft is the spec; this handoff is the build framing. If any
  detail here disagrees with the draft's rulings block, the rulings block wins.
- **Live data to re-derive against:** the Measurements tab (via the service account):

  ```text
  curl -sL "https://docs.google.com/spreadsheets/d/14wezh-B1vnVhHrvUjflHwgq30JSRs4VJBeOAs99kMXI/gviz/tq?tqx=out:csv&sheet=Measurements" -o /tmp/ah-measurements.csv
  ```

  The draft's row counts are from 2026-07-14 and are **stale** (the wellfield dataset has
  been rebuilt several times since; most recently 2026-08-24, +~9,600 gas rows). Treat the
  vocabulary (which substances exist) as the fixed spec and **re-derive current counts** —
  new rows are more of the same substances, not new classes, but confirm that before merge.

## Why (context a fresh session won't have)

The Measurements tab classifies each reading into a `metric` field. Today `egle_doc_parser.py`
only knows **five** metric values:

```python
# egle_doc_parser.py — the Pydantic Measurement model the LLM is forced to emit (~line 292)
class Measurement(BaseModel):
    metric: Literal["temperature", "carbon_monoxide", "oxygen", "methane", "other"]
    ...
```

Everything that isn't one of the four first-class metrics falls into **`other`** — which as of
the 2026-07-14 analysis was **4,951 of 9,303 rows (53% of the tab)**: a grab-bag of ~50 distinct
substance classes (hydrogen sulfide, PFAS congeners at ng/L, 14 metals, wastewater NPDES params
like TSS/BOD/pH/ammonia, NOx, SO2, VOCs, plus non-chemical operational events). You cannot chart
an arsenic trend or compare mercury against its permit limit while they're all `other`.

This is the monitor's `docs/roadmap.md` "Metric taxonomy" project. It was **drafted for Trisha's
review before any code was built** (that's why worker #66 was DRAFT-ONLY). She reviewed and
greenlit it 2026-08-25. Your job is to implement the approved vocabulary.

**Key classification principle (from the draft):** the **unit does NOT identify the substance**
(`%` is used for methane, O2, CO2, gas composition, and combustion efficiency alike). Classify by
**note text + context via the MODEL**, not by keyword rules on the unit. The classifier is
already model-based — structured output forces the `metric` Literal — so the primary change is
**expanding the allowed enum** and giving the model brief guidance for the known traps. Do NOT
add a keyword ruleset: the NMOC-vs-"non-methane" substring trap proved keywords brittle.

## Goal

Two parts — both required. Part B is the one that actually shrinks the `other` bucket for the
data already on the Sheet; do not skip it.

### Part A — Forward: expand the taxonomy so new documents classify correctly

1. Replace the 5-value `metric` Literal in the `Measurement` Pydantic model (`egle_doc_parser.py`
   ~line 292) with the **approved ~52-metric vocabulary** from the draft (the union of the
   "Landfill gas / air quality", "Water quality" (all sub-tables), "Temperature / ambient",
   and "Operational / non-chemical readings" tables), **plus the two rulings additions**:
   `benzene` and `major_ions` (see rulings below). Update the matching comment at ~line 52.
2. **Preserve the four existing first-class metrics** exactly — `temperature`, `carbon_monoxide`,
   `oxygen`, `methane` — and their meanings. `methane` = **per-well** readings. The draft's
   `methane_secondary` is a **separate** metric for facility/adjusted readings
   (`FGPROJECT23`, `EURNGPLANT`, "Methane (CH4) adjusted") — do NOT merge it into `methane` or
   you corrupt the existing per-well methane series. Same logic for `temperature_secondary`
   (effluent/pond/ambient) vs the well-temperature `temperature`.
3. Add short model guidance (in the classification prompt, not as keyword rules) for the known
   traps the draft names: NMOC/"non-methane" → `nmoc_voc` (never `methane`); "lead" the metal vs
   "lead" inside "leachate"; benzene → its own `benzene` metric, not `btex_chlorinated_voc`.
4. Keep `other` as a **real fallback** — anything the model genuinely can't place still lands in
   `other`. The goal is to shrink it drastically, not to force everything out of it.

### Part B — Backfill: reclassify the existing `other` rows already on the Sheet

Expanding the enum only affects **newly parsed** documents. The ~4,951 rows already written as
`other` will stay `other` unless you reclassify them. Write a **one-shot backfill** that:

1. Reads the current Measurements rows classified `other` (re-pull live; don't trust the stale count).
2. Reclassifies each by note text using the **same model-based path** (reuse the classifier, don't
   fork a second one), mapping to the approved vocabulary.
3. Updates the `metric` field in place (leave `value`/`unit`/`basis`/`well_id`/`as_of_date`/`note`
   untouched — this changes ONLY the metric label).
4. Is **idempotent and reversible** — re-running must not double-apply; keep a record of what was
   remapped (e.g., a dry-run report committed to the PR) so a bad mapping can be reverted.

## Trisha's rulings (2026-08-25) — bake these in

1. **`benzene` is its own metric** (has its own MCL). `btex_chlorinated_voc` keeps the BETX
   *mixture* lines + toluene, xylene, trans-1,2-Dichloroethene, Hexane; benzene-specific lines → `benzene`.
2. **`ammonia_nitrogen` stays lumped** — ammonia / nitrate / nitrite / TKN / Total Nitrogen /
   Kjeldahl remain one metric. Do not split.
3. **New `major_ions` metric** — calcium, magnesium (and any other major ions with no permit
   limit) move out of the operational/`event_status` bucket into `major_ions`.
4. **`WW-148R/DW01` combined-ID rows are OUT of scope** — deferred to `coder:well-id-normalization`
   (worker #68). Do not try to split the combined well ID in this build; classify the metric and
   leave the `well_id` as-is for that item to normalize.
5. **~52-metric size is approved** — do not trim the vocabulary to hit the roadmap's old ~30–40
   target; the extra NPDES/wastewater parameters are intended.

## Verification (this is a live path — mock-green is not enough)

- **Real-specimen forward test:** run the expanded classifier over ≥1 real Measurements-bearing
  document that currently produces `other` rows (a recent WOI/NPDES DMR), and confirm the readings
  now land on named metrics with correct `basis` preserved.
- **Backfill detection signal (the headline metric of success):** report the **`other` row count
  before vs after** the backfill on the live tab. The draft's target is to take `other` from ~4,951
  (53%) down to a small residual (only genuinely-unplaceable rows). A backfill that leaves `other`
  near its original size = the build failed its purpose; investigate before merge.
- **No-regression on existing metrics:** the current tests that assert CH4 → `methane` and never
  `other` (`tests/test_woi_router.py:153-159`) must stay green. Add tests for a representative
  sample of the new metrics (at least: `hydrogen_sulfide`, `pfas`, `arsenic`, `benzene`,
  `major_ions`, `ammonia_nitrogen`, one operational bucket, and `other`-as-fallback).
- Spot-check a random N of reclassified rows by hand against their note text.

## Scope boundaries

- **In scope:** the metric enum + classification guidance in `egle_doc_parser.py`, the backfill
  script, tests, ADR, topology update, PR.
- **Out of scope:** well-ID normalization (`coder:well-id-normalization` / worker #68) — including
  the `WW-148R/DW01` combined ID; the `location_type` axis (`coder:location-type` / worker #67);
  and the **`basis`-integrity audit** (worker #73) — some `other` rows are actually permitted
  *limits* mislabeled as measurements, but this build only fixes the **metric name**, not the
  `basis`. If you notice basis problems, note them for #73; don't fix them here.
- **Coordinate with** the GFL/Ridge Wood metrics already in production use (`hydrogen_sulfide`,
  `methane`) — match their existing spelling/casing exactly so you don't fork a near-duplicate.

## PR requirements

ADR (record the vocabulary + the rulings + the backfill approach) + tests + topology in the **same
PR**. Open as a **draft PR for Trisha's review** given the live-path + backfill nature — do not
autonomously merge on green alone.
