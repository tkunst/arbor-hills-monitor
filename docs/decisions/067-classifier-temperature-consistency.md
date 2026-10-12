# ADR 067 — Temperature-consistency check on classifier text, alerts and severity

*Status: **BUILT** — 2026-10-12 (Trisha-directed: "fix the script, not the cells").*

## Context

`egle_doc_parser.parse_document()` asks Claude for each filing's summary, key data
point, severity and structured measurements. Those three text/label fields are
published verbatim on the Public Records pages and in the digest, and `severity` +
measurements drive `email_alerts.is_urgent()`. Nothing checked them against the
temperature values the code itself uses (131°F NSPS/EG watch, 145°F MACT urgent).
A read of the live Sheet (5,766 summary/key-data-point cells) found five documents
with wrong claims, e.g. "AHWW414R at 553°F … MACT limit of 204°F" (553 is the 2024
H2S survey in ppm), "the 135°F baseline MACT limit", "180°F (35°F above the 131°F
NSPS limit)". Causes: the prompt's own example ("180F - 35F above MACT limit") primed
limit arithmetic and presented a permitted ceiling as the headline; large documents
are keyword-windowed, so table rows can arrive without their column header; and no
deterministic check existed.

## Decision

1. **Prompt** (`_TEMPERATURE_RULES`): the only limits are 131°F and 145°F (HOVs must be
   stated in the document); limit arithmetic must name its limit and be exact; units
   come from the column header, and a number whose header isn't on the page shown is
   not reported; H2S/methane/CO are ppm. The old example is replaced by an as-found
   measured reading.
2. **`kdp_check.py`** (pure): checks summary + key data point. Flags a stated
   regulatory limit not in {131, 145}; an "N°F above X" gap that doesn't reconcile
   (±1.1°F) with the nearest reading and the limit/HOV values; a °F value above
   `max_plausible_wellhead_f` (250) in a sentence about wells (flare/combustion
   exempt). Reference values come from `config.yml temperature_thresholds`
   (`epa_gas_operating_f`, `mact_f`, new `hov_values_f`, `max_plausible_wellhead_f`).
3. **Enforcement in `parse_document`**: on failure, one retry with the problems named;
   still failing → the failing field is replaced by a fixed "withheld, see source"
   line (logged). Never raises into ingest.
4. **Alert gaps**: (a) a structured *measured* temperature above the plausibility cap
   (non-flare) is re-tagged `basis: unknown`, so `is_urgent`'s measured path ignores
   it; (b) the free-text fallback in `_max_temperature_f` only considers 50–250°F;
   (c) when text was withheld, an "urgent" severity stands only if a plausible
   measured temperature ≥145°F backs it, else it becomes "notable" (digest, no
   same-day email).
5. **Existing rows**: `scripts/recheck_key_data_points.py` + `kdp-recheck.yml`
   (dispatch-only, read-only, proposals as an artifact). Applying reviewed proposals
   is a local `--apply` step that writes only Summary/Key Data Point (+ Severity with
   `--update-severity`) on the feed row and its Evidence by Risk copies, and refuses
   a row whose text changed since review. No measurements, alerts, titles or _state.

## Consequences

- Wrong temperature claims can no longer reach the public pages unchecked; the cost
  is one extra classifier call only for a document that fails, and a withheld line
  instead of a plausible-looking wrong one.
- Validated against the live Sheet: exactly the 5 known-bad documents flag; the
  AHW272R4 180°F lines, HOV gaps and flare temperatures pass (tests pin both sets).
- `hov_values_f` must be updated when EGLE issues an HOV at a new value.
- Not covered: non-temperature claims (CO, methane, PFAS) and the model's severity on
  documents whose text passes. Older Haiku-era backfill rows are not re-classified
  (separate decision).
