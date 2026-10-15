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

## Amendment (2026-10-15, coder:woi-layout-guard): flag, don't drop

Point 4's protections stay exactly as they are: a °F number above
`max_plausible_wellhead_f` (250) never fires an URGENT alert on its own. What
changes is that such a number is no longer dropped silently. Each one becomes a
row in a new append-only **Parser Review Flags** tab (`review_flags.py`):

- `measurement_temp_above_ceiling`: a classifier MEASURED reading that
  `sanitize_measurements` re-tagged `basis: unknown` (still kept in Measurements
  with its note, as before);
- `text_temp_above_ceiling`: when `is_urgent` had to fall back to free text
  (no structured temperature at all), each above-ceiling number that fallback
  skipped. Flare/combustion context (±150 characters) is not flagged: flare
  temperatures are real and expected;
- `woi_temp_above_ceiling`: a valid WOI gas-table reading above the ceiling.
  This is new protection, not only a new flag: `woi_router` used to emit any
  valid reading at or above the watch band as a measured temperature, so a
  shifted column (the 526.8°F case in ADR 005's 2026-10-15 addendum) would have
  fired a same-day URGENT alert and a `well_watch` email. Such readings are now
  held out of the urgency path, the per-well summary, the peak anchor and the
  readings passed to `well_watch`, and flagged. Like a re-tagged classifier
  reading, the as-found value stays in Measurements with `basis: unknown` and a
  note, so it is on record but never counts as a measured temperature.

The tab holds parser metadata only (document id, link, kind, value, well,
reading time, pages, a fixed-phrase detail); no classifier prose. It lives on
the PRIVATE Sheet (`GSHEET_ID_PRIVATE`, now also passed to the backfill
workflow), never the public case file: an above-ceiling value next to a well
id is an unverified number the pipeline itself judged implausible, and must not
read as a measured value there. With no private Sheet configured the flags are
only counted in the run log. Writes are best-effort and never block a document.
No email is sent from this path: `review_flags.flags_since()` is the read entry
point for a later owner-only email.

**Residual risk (accepted, follow-up queued):** a REAL wellhead reading above
250°F (e.g. a subsurface-fire excursion) is now held out of the URGENT path,
`well_watch` and the WOI summary, and is visible only in the review tab until
the owner-only email (`coder:owner-weekly-email`) surfaces it. The report still
alerts if any other reading is at or above 145°F. Likewise, a WOI report whose
gas-table header drifts into an unrecognized form is no longer read with the
9-column default: its rows are rejected (`rejected_gas_table`) and the report
falls back to the classifier's windowed sample, so that follow-up should treat
a rejected table on a detected WOI report as owner-alert material.
