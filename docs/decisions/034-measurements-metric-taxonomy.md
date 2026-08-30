# ADR 034 — Measurements metric taxonomy (decompose the `other` bucket)

*Status: **BUILT — draft PR for Trisha's review** (2026-08-30). This is a
LIVE-path change (the Measurements extraction schema the daily pipeline runs
unconditionally — no `enabled:false` to hide behind) plus a one-shot data
backfill of the existing rows, so per `docs/overnight-coder.md` Steps 3 & 8 it
opens as a **draft**, not an autonomous merge. The forward-classifier change
(Part A) affects newly-parsed documents once merged; the `other`-bucket backfill
(Part B) is a **separate, human-initiated `--apply`** that mutates the public
Sheet — never run by the overnight loop.*

## Context

The Measurements tab labels each reading with a `metric`. Historically the
classifier's schema allowed only **five** values —
`temperature / carbon_monoxide / oxygen / methane / other` — so every OTHER
substance collapsed into `other`. As read live on 2026-08-30, that bucket was
**5,127 of 11,336 rows (45% of the tab)**: hydrogen sulfide, PFAS congeners, 14
metals, NPDES wastewater parameters (TSS/BOD/pH/ammonia/phosphorus), NOx/SO2,
NMOC/VOCs, plus non-chemical operational events — all indistinguishable. You
cannot chart an arsenic trend or compare mercury against its permit limit while
they are all `other`.

This is the roadmap's "Metric taxonomy" project. It was drafted for review
**before any code** (worker #66, DRAFT-ONLY); Trisha reviewed and greenlit it
2026-08-25 (`DRAFT-INTERNAL-measurements-metric-taxonomy-2026-07-14.md`, the
"Trisha's rulings" block). This ADR implements the approved vocabulary.

## Decision

### The vocabulary (single source of truth)

`egle_doc_parser.MetricLiteral` is the one place the vocabulary is spelled out
(~52 named substances + the four first-class metrics + `other` = 59 values).
`METRIC_VALUES` is derived from it via `typing.get_args`, so the doc classifier's
`Measurement.metric` field and the backfill's note classifier can never fork the
list. Grouped: landfill gas/air (H2S, methane_secondary, CO2, H2, NMOC/VOC, NOx,
SO2, HCl, PM, surface emissions, TRS, combustion efficiency); water priority
(PFAS, arsenic, mercury, selenium); other metals/inorganics (nickel, chromium,
lead, cadmium, zinc, copper, barium, boron, antimony, cyanide); organics/
biochemical/nutrient (TSS, BOD, COD, TOC, ammonia_nitrogen, phosphorus,
btex_chlorinated_voc, benzene, PAHs, E. coli/coliform); physical (pH, DO, flow,
chloride, fluoride, hardness, alkalinity, conductivity, TDS, major_ions);
temperature_secondary; operational (event_status, well_operational,
operational_capacity, pressure_vacuum, wind_odor, exceedances_count, qa_sample).

### Classify by the MODEL, not keyword rules

The **unit does not identify the substance** (`%` is used for methane, O2, CO2,
gas composition, and combustion efficiency alike), and a keyword ruleset proved
brittle (the NMOC-vs-"non-methane" substring trap). So the change is: **expand
the enum** and give the model brief guidance for the known traps
(`_METRIC_CLASSIFY_GUIDANCE`, shared verbatim by the document prompt and the
single-note classifier — no fork). No keyword branch was added.

### Preserve the four first-class metrics; split the secondaries

`temperature`, `carbon_monoxide`, `oxygen`, `methane` keep their exact meaning.
`methane` = **per-well** CH4; facility/adjusted CH4 (FGPROJECT23, EURNGPLANT,
"CH4 adjusted", gas-composition) routes to the **separate** `methane_secondary`
so the existing per-well methane series is never corrupted. Same for
`temperature_secondary` (effluent/pond/ambient) vs the well-temperature
`temperature`. Tested on the forward path (per-well methane stays `methane`).

### Trisha's rulings (2026-08-25), baked in

1. **`benzene` is its own metric** (own MCL). `btex_chlorinated_voc` keeps the
   BETX mixture + toluene/xylene/trans-1,2-DCE/Hexane; benzene lines → `benzene`.
2. **`ammonia_nitrogen` stays lumped** — ammonia/nitrate/nitrite/TKN/Total
   Nitrogen/Kjeldahl are one metric. Not split.
3. **New `major_ions`** — calcium/magnesium (and any major ion with no permit
   limit) move out of the operational bucket.
4. **`WW-148R/DW01` combined-ID rows are OUT of scope** — deferred to
   `coder:well-id-normalization` (#68). The backfill fixes the metric label only;
   `well_id` is left as-is.
5. **~52-metric size approved** — the extra NPDES/wastewater parameters are
   intended; the vocabulary was not trimmed to the old ~30–40 target.

### The backfill (Part B) — `backfill_metric_taxonomy.py`

Expanding the enum only affects newly-parsed documents. The existing `other`
rows are reclassified by a one-shot script that:

- reads the live Measurements rows and selects those with `metric == "other"`;
- **deduplicates by note text** (unit does not identify the substance, so equal
  notes map equally) and classifies each distinct note **once** via the SAME
  model path (`egle_doc_parser.classify_note_metric`, same model, same shared
  guidance) — reuse, never a second classifier;
- updates **only the Metric column** — value/unit/basis/well_id/as_of_date/note
  are untouched (this changes only the label).

Safety properties (all required by the handoff):

- **Dry-run by default.** No flag → classify, write a Markdown report
  (before/after distribution + full note→metric mapping), write NOTHING to the
  Sheet. `--apply` performs the update and emits a JSON revert manifest.
- **Idempotent** — only current-`other` rows are ever considered, so a second
  run after apply is a no-op; a note that resolves back to `other` is skipped.
- **Reversible** — every touched row's old value is uniformly `other`, so the
  committed note→metric mapping fully determines each remap; `--apply` also
  writes a per-row manifest to set rows back to `other`.
- **Resumable** — distinct-note classifications are cached to a gitignored JSON,
  so an interrupted run does not re-bill already-classified notes.
- **Model = production (Haiku).** The backfill classifies existing rows with the
  same model as new rows (`config.yml anthropic_model`), so the two paths do not
  diverge. (The handoff's "Sonnet" note was a build-tier recommendation, not the
  runtime classifier.)

Because `--apply` mutates a public, operator-visible artifact, it is deliberately
a **separate human step**, distinct from merging the code.

## Verification (this is a live path — mocked-green is not enough)

The hermetic suite (49 tests) proves: the enum accepts the approved metrics and
rejects unknowns; the structured-output schema carries all 59 values;
`classify_note_metric` reuses the shared vocabulary + guidance and fail-safes to
`other`; the forward path preserves basis and keeps per-well methane on
`methane`; and the backfill's select/dedup/plan/project/apply/idempotency/
reversibility/report logic is correct.

Two gates need the **live production model**. The build session had no working
Anthropic API key (the local `.env` key is a 10-char placeholder; the production
key lives only in GitHub Secrets), so both were run 2026-08-30 with **Claude
Haiku 4.5 via the Claude subscription** — the SAME production model, so no
model-brain divergence. Results (full report:
`docs/backfill-reports/metric-taxonomy-backfill-report.md`):

- **Backfill dry-run over the real 5,127 `other` rows** (all 3,304 distinct notes
  classified): **`other` 5,127 (45.2%) → 149 (1.3%), 4,978 rows reclassified.**
  Trap checks clean on the live data: 0/24 NMOC notes → methane; 0/98 `leachate`
  notes → lead; 7/7 facility/adjusted-methane notes → `methane_secondary`; 9/11
  benzene-specific → `benzene`. The 141 residual `other` notes are genuinely
  unplaceable (net heating values, flare exit velocities, groundwater velocity,
  treatment additives, PCBs, balance gas/N₂, penalty rates — all outside the ~52
  vocabulary). Every assigned value validated against `METRIC_VALUES` (0 invalid).
- **Forward real-specimen test** — a real 123-page Arbor Hills air-emission
  stack-test report (SRN N2688, currently produces `other` rows) run through the
  production measurements-extraction spec: 33 readings, **0 invalid metrics, 0
  invalid basis**. NOx→`nitrogen_oxides`, CO→`carbon_monoxide`, CO₂→
  `carbon_dioxide`, and — the headline trap — NMOC→`nmoc_voc`, never `methane`.
  The 4 permit ceilings correctly carry `basis=permitted_limit` vs 29 `measured`
  stack-test values. Per-well `methane` was NOT corrupted (the fuel-CH₄ gas-
  composition readings went to `other`, a conservative miss — arguably
  `methane_secondary` — never wrongly into `methane`).

Method caveat: the classification ran via subscription subagents on the real note
text with the same vocabulary + guidance, not the script's own `messages.parse`
call (no API key), and 16 notes whose keys the subagents retyped with drifted
unicode were recovered by normalized matching (12) + 4 obvious manual
corrections. A real `--apply` re-runs through the API structured-output path
(which hard-constrains the enum) and may differ on a few genuinely-ambiguous
edge notes. The 59-value schema itself is separately validated (the SDK accepted
it and built the request).

## Scope boundaries

- **In scope:** the metric enum + shared classification guidance in
  `egle_doc_parser.py`, `classify_note_metric`, the backfill script, tests, this
  ADR, topology.
- **Out of scope:** well-ID normalization / the `WW-148R/DW01` combined ID (#68);
  the `location_type` axis (#67); the **basis-integrity audit** (#73) — some
  `other` rows are permitted *limits* mislabeled as measurements, but this build
  fixes only the **metric name**, never `basis`. Basis problems are left for #73.
- **Coordinates with** the GFL air (`gfl_air_client.py`) and Ridge Wood
  (`ridgewood_archiver.py`) streams, which already emit `hydrogen_sulfide` and
  `methane` as text; those exact spellings are now first-class enum values, so no
  rows changed and the streams share one vocabulary (their comments updated).

## Consequences / residual risks

- **Reclassification is model judgment.** A distinct note the model places wrong
  is one wrong label; the dry-run report is the committed record and the
  `--apply` manifest is the revert path. The residual-`other` list in the report
  is there to eyeball for anything that *should* have had a metric.
- **`other` remains a real fallback** — genuinely unplaceable readings still land
  there by design; the goal is to shrink the bucket drastically, not to empty it.
- **Enum size vs. structured output.** 59 enum values enlarge the schema; verified
  to round-trip through `messages.parse` and to add negligible tokens.
