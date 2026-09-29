# ADR 058 — Named single-well temperature trip-wires (`well_watch`)

*Status: active — 2026-09-28 (Trisha-directed: "I want to know the second we get a 145 or higher reading" for AHW263R5).*

**Problem.** `email_alerts.is_urgent` fires on any measured wellhead temperature at or above 145F. Every WOI
Status Report carries HOV-permitted wells above 145F (2026 H1 peak: AHWW0279 at 154.6F), so that alert fires
on every report and never names the well. A single well being watched gets buried.

**Decision.** New `well_watch.py`, driven by a `well_watch:` config block. After `woi_router` parses a
WOI-format filing (WOI Status Reports and Gas-Extraction Exceedance filings), `watcher.py` passes every valid
reading to `well_watch.check_and_alert`, which sends one dedicated email per tripped watch naming the well,
value, reading time, as-found vs ADJ, page and filing link. `woi_router.route_measurements` now also returns
`readings` (all valid rows) for this. Best-effort: a failure logs and never blocks recording the document.

* **Match by well number, not exact ID** (`^AHW{1,2}0*263R\d*$`): redrills (R5 to R6), AHW/AHWW prefixes and
  zero-padding would otherwise silently kill the watch.
* **ADJ rows count** and are labeled: near the line, the ADJ value can be the one that crosses.
* **`since`** drops re-printed history rows so an old reading can't read as new; a row with an unparseable date
  is kept (fail toward alerting).
* Threshold is `>=` (145.0 fires, 144.9 does not).

**First watch:** AHW263R5, 145F, since 2026-05-01, recipient Trisha only. Context in the email note: 170F HOV
granted 7/23/2025 (no end date stated), not in the 4/7/2026 16-well renewal; held at 142.1-144.2F through
6/30/2026. Dry run on the real 2026 H1 report: zero hits at 145F; 13 hits at a 143.5F test threshold, all
AHW263R5, correctly labeled.

**Limits (not covered).** No live wellhead feed exists; latency is filing latency plus one daily run. The
NESHAP semi-annual Appendix A roster is not on the live path (`neshap_table_parser` is a dataset tool), and
spreadsheets EGLE sends outside nSITE never reach the monitor. Those channels are handled manually in Lotext.

**Rollback.** Delete the `well_watch.wells` entry (or the block); the hook no-ops on empty config.
