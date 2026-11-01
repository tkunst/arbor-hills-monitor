# Overnight-coder handoff: WOI parser + router hardening, and flag (not ignore) >250°F readings

*Staged 2026-10-15 (Trisha-approved). Read `docs/overnight-coder.md` first. A change to
an already-live path (the watcher routes every WOI-shaped filing through `woi_router`),
so there is no flag to hide behind: verify on the real specimens below before merging.
Normal Step 8 merge gates.*

## Why

A read-only re-run of every WOI-mentioning nSITE filing through `woi_router` +
`woi_table_parser` found four defects. None has reached the public WOI Well Summary
tab yet (every document already in it parses cleanly), but each one applies to the
live watcher path.

1. **Column shift on the older table layout (most serious).** `parse_gas_extraction`
   assumes the 9-number layout (CH4, CO2, O2, Balance, Diff. Press, Temp, Flow, Well
   Pres, Header Pres). Some reports print NO Diff. Press column, so the temperature
   field receives the flow value. nSITE `8071021799722275482` /
   `-8885291794816390696` (Q3 2023 HOV WOI report, 65pp) yields AHWW0402 = 526.8°F on
   8/3/2023 (the page shows 110.5°F) and 82 readings over 200°F, all passing the
   sum-to-100 gate. Through the live path that would fire a false URGENT alert.
2. **25-page header scan.** `woi_router._HEADER_SCAN_PAGES = 25` misses
   `2704244228618848129` (Q3 2023 Consent Judgment report, 1006pp; gas table header
   on p163).
3. **One bad page kills the parse.** `-4046818116875037513` / `5031184057377725991`
   (Q1 2024 HOV WOI report, 90pp): MuPDF "cannot find page 71 in page tree" aborts
   the whole parse.
4. **>250°F is silently ignored.** `temperature_thresholds.max_plausible_wellhead_f:
   250` (ADR 067) makes `email_alerts._max_temperature_f` skip any free-text number
   above 250 and `kdp_check` re-tag such a classifier measurement `basis: unknown`.
   Keep that protection (a >250°F number must never fire an URGENT alert by itself:
   it is usually a flare temperature or a misread column), but surface the number
   instead of dropping it.

## What to build

1. **Per-table column-layout detection** in `woi_table_parser`: read each gas table's
   own header ("Well ID ... CH4 (%) ... Diff. Press ... Temp. ..."), parse each known
   layout correctly, and reject a table that fits no known layout (never emitted,
   logged loudly). Add a table-level column-shift check; the signature is the share of
   negative "flow" values. Keep `parse_gas_extraction(pdf, alias_map) -> list`
   backward compatible (a Lotext dataset builder imports this module).
2. **All-page header detection** in `woi_router.is_woi_report`. The watcher/backfill
   hand the router a file that has already been through `parse_document`'s OCR step.
3. **Skip an unreadable page** in every page loop, log it, keep the rest.
4. **Flag, don't drop, >250°F.** Still never URGENT on its own. Run-specific override
   (2026-10-15): there is no owner-only alert channel yet, so record each flag in a
   review log only, expose it through one small read function a later owner-only
   email can call, and build NO email path. Amend ADR 067.
5. Out of scope, note only: the 250pp Gas Extraction pair (`1230339285137039248`,
   `2143683964897446383`) parses to 0 readings (its table has no Temp column); after
   fix 1 it should land in the rejected-table log rather than be silently empty.

## Real-specimen tests

Download from `https://mienviro.michigan.gov/ncore/downloadpdf/<id>` (`curl
--compressed`; the large reports come back gzip-encoded). Never commit them.

- Q3 2023 HOV 65pp: no shifted temperatures (max as filed; 0 readings >200°F, or the
  table rejected and logged).
- `2704244228618848129` detected as a WOI report.
- Q1 2024 HOV 90pp parses, minus the bad pages.
- Regression: `7022559137978826651` (13,976 readings) and `-2083857881305271673`
  (13,324) unchanged.
- A >250°F free-text case: no URGENT, review flag present.

## Downstream note

`coder:woi-auto-staging` (built outside this repo) consumes this parser. Its spec
expects the Q3 2023 HOV 65pp report to stage 0 rows with 1 needs-review entry because
the shifted table was assumed to be rejected. With per-table layout detection the
table is read correctly instead, so that build must re-check this report against the
current dataset rather than assume a rejection.
