# ADR 004 — Structured measurement schema (CANONICAL)

*Status: accepted — 2026-06-13. This is the canonical schema for quantitative
readings; the plan file links here rather than prescribing its own.*

## Context

A document's free-text `key_data_point` is not enough for the R8 (overheating /
ETLF) case. Two failure modes would discredit the artifact:

1. **Conflating a permitted ceiling with a measured reading.** "180°F" as an
   HOV-waiver *requested limit* and "180°F" as an *actual well reading* mean
   opposite things.
2. **Losing the per-well trajectory.** The most predictive signal is trend —
   well count rising over years, single wells jumping (e.g. 101→140°F in 15
   min). Per-document rows alone can't express that.

## Decision

The parser extracts a generic, atomic `measurements[]` list. Each measurement:

| Field        | Type / values                                             | Notes |
|--------------|-----------------------------------------------------------|-------|
| `metric`     | `temperature` \| `carbon_monoxide` \| `oxygen` \| `methane` \| `other` | named metrics grow as needed; `other` (+ `note`) is the catch-all |
| `value`      | number                                                    | the reading |
| `unit`       | string (`F`, `ppm`, `percent`, …)                         | |
| `basis`      | `measured` \| `permitted_limit` \| `unknown`              | **load-bearing** — measured vs permitted ceiling/HOV/MACT limit |
| `well_id`    | string \| null                                            | e.g. `AHW272R4` |
| `as_of_date` | ISO `YYYY-MM-DD` \| null                                  | the reading's own date if stated |
| `note`       | string \| null                                            | short context |

Defined in `egle_doc_parser.py` (Pydantic `Measurement` inside
`_classify_with_claude`, mirrored on the `ParsedDoc.measurements` dataclass
field). Written to the **Measurements** sheet tab, one row per reading.

## Why this shape

- **`basis` defuses failure mode #1.** `email_alerts.is_urgent` triggers only on
  `basis == "measured"` temperatures ≥ 145°F; a permitted ceiling never fires.
  (Unit-tested: `tests/test_email.py::test_permitted_ceiling_does_not_fire`.)
- **Atomic rows defuse failure mode #2 without reprocessing.** Because every
  reading is captured per-well/per-date/per-basis in the Measurements tab, the
  time series and velocity are *derivable downstream by aggregation* — the
  trend/velocity engine can be built later without re-running the 754 docs
  through Claude.
- **No redundant denormalization.** We store raw `value` + `basis`; threshold
  booleans (over-131/over-145) are derived from `value` + the config thresholds
  (`temperature_thresholds.epa_gas_operating_f` = 131,
  `temperature_thresholds.mact_f` = 145), not stored.

## Metric vocabulary growth

`metric` started as temperature / carbon_monoxide / oxygen / other. **`methane`
was promoted to a named metric 2026-07-13** (it's the central landfill gas; the
WOI table parser emits it directly, and the classifier prompt now names it).
`other` remains the catch-all, but a review of the live Measurements tab found it
had become a grab-bag of ~30-40 distinct substances/measurements (H2S, PFAS at
`ng/L`, metals, wastewater params, NOx, SO2, odor, liquid level, …) where the
UNIT does not identify the metric (`%` is used for methane, O₂, gas composition,
and efficiency alike). A follow-up will expand the named-metric vocabulary to
decompose `other` by SUBSTANCE (classified by the model, not by unit or brittle
keyword rules), keeping a small `event`/`status` bucket for the rows that are not
chemical readings (well "screen submerged", operational "gas shortfall").

## Deferred (not in this ADR; owned by the plan's alert semantics + a future ADR)

- Per-well trend/velocity computation and a per-well view.
- 3-tier Watch / Warning / Crisis alerting (maps onto the existing
  `routine`/`notable`/`urgent` severity field + trend triggers).
- The full named-metric taxonomy that decomposes `other` (see "Metric vocabulary
  growth" above).

## Cost note

No source documents have been processed yet (backfill is deploy-day), so
finalizing this schema now incurs no reprocessing cost.
