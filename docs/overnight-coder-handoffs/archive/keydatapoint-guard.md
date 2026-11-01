# Overnight-coder handoff — key_data_point guard + urgency_reason (the 202°F false alert)

*Staged 2026-10-16 from worker #217's root-cause analysis (reviewed). Read
`docs/overnight-coder.md` first. This is a LIVE-PATH change: the same-day
`[URGENT]` email body and the Sheet row for N2688 WOI reports, which already run
nightly. Per Step 3, verify against a real specimen before merge; end at a
**draft PR for Trisha's review**, not an autonomous merge. Recommended tier:
Sonnet.

**Cloud-ready:** Runs in cloud mode: ends at a DRAFT PR. No live credentials. Any
real-specimen / live verification is done locally by Trisha before merge.

**Live-path check:** `woi.auto_route` and the N2688 watcher are both live today
(`config.yml`: `woi: auto_route: true` by default, no explicit `enabled` flag to
check since this is the core document pipeline, not an optional stream). Treat
this as a live-path change per `docs/overnight-coder.md` Step 3 regardless of
anything below: no mocked-green autonomous merge, real-specimen verification is
mandatory, and this build does not flip any flag.*

## Background

On 2026-10-08 an `[URGENT]` email for N2688 claimed a well recorded 202°F. The
actual max measured temperature in that 119-page report was far lower. The "202"
came from a **Depth to Bottom** sounding (in feet, not degrees) for a different
well, on a page the model saw without its own column-header page (the header
lives on a different page of the same report, outside the classifier's windowed
sample for an over-threshold document). A second, related bug: the same day, an
email went out with subject `[URGENT]` while its body printed `Severity:
routine`, because the alert fired through the measured-temperature branch while
the model's own severity classification disagreed, and the body never says
which path fired.

**Root cause, verified against `origin/main` as of this writing:**

- `woi_router.route_measurements()` (`woi_router.py`) detects a WOI Status
  Report, exhaustively parses the gas-extraction tables, and **replaces**
  `parsed.measurements` with validated, deterministic readings. It does NOT
  touch `parsed.key_data_point` or `parsed.summary` — those stay whatever the
  classifier's windowed read produced.
- The per-document pipeline is `doc_pipeline.process_one_doc()` (this repo
  consolidated what the RCA's original pointer called "`watcher.py`" into this
  shared function — verify you're editing the current location, not a stale
  one). The call order there is:
  ```
  routed = woi_router.route_measurements(parsed, local, d, ctx.cfg, ...)
  ...
  sw.write_document(ctx.sheets, ctx.sheet_id, parsed, d, link, RISK_NAMES, feed_tab=job.feed_tab)
  ...
  if routed is not None:
      sw.write_woi_summary(ctx.sheets, ctx.sheet_id, routed["summary"], d, link)
      ...
  if job.alert is not None:
      job.alert(parsed, d, link)   # -> is_urgent / send_urgent_alert / format_urgent_body
  ```
  **`sw.write_document()` — which writes the public Sheet row — runs BEFORE the
  `routed is not None` block.** So even after this fix, inserting the headline
  rebuild only inside that later block would still let the uncorrected
  `key_data_point` reach the Sheet. The fix must happen **immediately after**
  `route_measurements()` returns, **before** `sw.write_document()` is called —
  verify this ordering against current code; it may have shifted again since
  this handoff was written.
- `email_alerts.format_urgent_body()` prints `parsed.key_data_point` and
  `parsed.severity` verbatim, with no field explaining why `is_urgent()` fired
  and no cross-check against `parsed.measurements`.
- There IS already a partial, unrelated mitigation in this repo: `kdp_check.py`
  (ADR 067) runs inside `egle_doc_parser._enforce_temperature_consistency()`
  and withholds/sanitizes `key_data_point`/`summary` text that names an
  implausible temperature (above `temperature_thresholds.max_plausible_wellhead_f`,
  250°F by default) or misstates a regulatory/HOV limit. **This would NOT have
  caught the 202°F bug** — 202 is below the 250°F plausibility ceiling, so it
  read as a perfectly plausible (if wrong) wellhead reading. Do not treat
  `kdp_check` as already solving this; it solves a different failure mode
  (implausible numbers), not "a plausible-looking number attributed to the
  wrong column/well." Say so explicitly in the PR so a reviewer doesn't think
  this duplicates ADR 067.
- `is_urgent()` (`email_alerts.py`) already prefers structured measurements
  over free text when any temperature measurement exists — that part is fine.
  The problem is specifically the **headline text shown to the human**, not
  whether the alert fires.

## What to build

### 1. Deterministic headline from woi_router output

Add a function — `woi_router.build_key_data_point(routed, parsed, cfg)` fits
the existing module's shape — that, given `route_measurements()`'s return dict
(`{"summary": [...], "readings": [...], "n_measurements": ..., ...}`, see
`woi_table_parser.per_well_summary()` for the per-well dict shape: `well`,
`max_temp_f`, `max_temp_date`, `o2_at_max_temp`, `ch4_at_max_temp`, sorted
hottest-first), builds a plain-language headline from the **hottest well in
`summary`**, e.g.:

```
"AHWW312R: max measured 158.3°F (2023-08-29, Gas Extraction Report)."
```

In `doc_pipeline.process_one_doc()`, when `routed is not None`, overwrite
`parsed.key_data_point` with this deterministic headline **before**
`sw.write_document()` runs (see the ordering note above). Do not touch
`parsed.summary` unless you find it also reaches an alert/Sheet surface
unchecked — check before deciding; the RCA's primary finding was about
`key_data_point` specifically.

**Do not weaken the existing `kdp_check` path.** It runs earlier, inside
`egle_doc_parser.py`'s classification step, on the model's own (windowed)
free text. This fix runs later, after `woi_router` has already replaced
`parsed.measurements` with the exhaustive set. Both can and should coexist —
document the two-layer design in the PR rather than merging or removing
either.

### 2. `urgency_reason` — explain why an alert fired

Add an `urgency_reason: str` output, set at the point `is_urgent()` or its
caller decides to fire (one plausible location: have `is_urgent()` return a
reason alongside its bool, or add a small `urgency_reason(parsed, cfg) ->
str` sibling function in `email_alerts.py` that mirrors `is_urgent()`'s own
branches — a measured-temperature trigger, a classifier-severity trigger, or
free-text fallback). Thread it into `format_urgent_body()` so the body states
the actual trigger, e.g.:

```
Urgent because: measured temperature 158.3°F at AHWW312R, at or above the
145°F threshold (MACT limit).
Classifier severity: routine.
```

so the subject (`[URGENT]`, hardcoded in `send_urgent_alert()`) and the body
never disagree without explanation. **Do not change the 145°F
(`urgent.measured_temp_urgent_f`) threshold or gate/suppress any alert on
this basis** — Trisha's explicit ruling (preserved from the worker analysis)
is that every measured reading at or above 145°F alerts, full stop; this
change is presentation-only.

### 3. HOV approved-ceiling context (additive, never a gate)

Add the well's approved HOV ceiling to the alert body as context, never as a
trigger or suppressor. **What's actually in this repo today:**
`config.yml`'s `temperature_thresholds` block has a flat `hov_values_f: [150,
155, 160, 170, 180]` (the only values ever granted site-wide) and
`top_hov_wells: [AHW272R4]` (the one well allowed the top value, 180°F) — used
today only by `kdp_check.py` to flag classifier text that misattributes 180°F
to any OTHER well. **There is no per-well ceiling lookup anywhere in this
repo** beyond that single well/value pair, so most wells have no determinable
ceiling from code alone.

Build a small, explicit lookup — e.g. a `hov_ceiling_for(well_id, as_of_date,
cfg) -> float | None` helper (new module or added to `woi_router.py`/a new
`hov_ceiling.py`) backed by a new, clearly-named config/data structure (a list
of `{well_id, ceiling_f, effective_start, effective_end}` entries fits the
"effective dates matter" need called out by the original research). **Seed it
with only the single pairing already established in code** (`AHW272R4` →
180°F) — do not invent or guess any other well's ceiling value; that roster
lives outside this repo and Trisha will fill it in later. Use the lookup in
`format_urgent_body()` to label each alerted reading:

```
HOV approved ceiling for AHW272R4: 180°F. Measured: 154.4°F (below ceiling).
```

or, for a well with no entry: `"No approved ceiling on record for <well>."`
Never let a ceiling value change whether the alert fires (test this
explicitly — see below).

### 4. Regression test on page-8-absent windowing

Add a test fixture reproducing the real bug shape: a short page-1 + "page 59"
style excerpt (synthetic, built from the bug description — a tabular block
with a sounding/depth column adjacent to temperature columns, no header row)
with a stubbed classifier response containing a fabricated high-temperature
`key_data_point`, routed through `woi_router` with a measurements set that
does NOT contain that fabricated value. Assert the final `key_data_point`
does not contain the fabricated number and does contain the real
`woi_router`-derived peak.

## Tests

- `test_woi_routed_key_data_point_uses_deterministic_temp` — after a
  successful route, `parsed.key_data_point` reflects `summary[0]`'s well/value/
  date, not whatever the stubbed classifier put there.
- `test_sheet_row_gets_corrected_key_data_point` — a `doc_pipeline` /
  `process_one_doc`-level test (mock `sw.write_document`) asserting the
  **Sheet row**, not just the email, carries the corrected headline (this is
  the regression that actually happened on 2026-10-08 — the Sheet needed a
  manual correction afterward).
- `test_urgent_body_states_urgency_reason` — body contains the measured value,
  the threshold, and the classifier's own severity, never contradicting
  itself silently.
- `test_urgent_fires_regardless_of_hov_ceiling_label` — a reading above AND
  below AH W272R4's 180°F ceiling both alert correctly at >=145°F; the label
  never changes whether `is_urgent()` returns True.
- `test_no_ceiling_on_record_label` — a well with no roster entry gets the
  explicit "no approved ceiling on record" text, not a blank or a guess.
- Existing `test_woi_router.py` / `test_email_alerts*.py` / `test_doc_pipeline.py`
  tests must stay green; if any assert the OLD verbatim-`key_data_point`
  behavior for a routed document, update them deliberately and say so in the
  PR.

## Out of scope (do not attempt in the cloud run)

- **The live-Sheet audit** for other past `key_data_point` values that cite an
  implausible temperature (worker #217 item (e)). That needs live Google
  Sheets credentials this cloud session doesn't have. Leave it named as an
  explicit follow-up in the PR description for a local session.
- Populating the HOV ceiling roster beyond the one pairing already in
  `config.yml`. That data lives outside this repo.
- Changing the 145°F alert threshold or adding any suppression/gating logic.

## Acceptance

- The fabricated-number regression test (item 4) fails against the
  pre-change code and passes after.
- `pytest -q` fully green.
- The PR states plainly: (a) the headline now comes from `woi_router`'s
  deterministic data for every routed document, including the Sheet row; (b)
  every `[URGENT]` body states why it fired; (c) HOV ceiling context is
  additive-only, proven by the never-changes-firing tests; (d) this is
  distinct from and does not duplicate the ADR 067 / `kdp_check` plausibility
  check — both layers are documented as coexisting.
- CLAUDE.md: add or extend the `woi_router.py` entry in the Architecture
  section to describe the new headline-building responsibility (verify
  whether `woi_router.py` already has an entry — it had none as of this
  writing, per a repo-wide grep against CLAUDE.md's module bullets. If still
  true, this is a genuinely missing entry, not just a missing detail).
- If this introduces a new cross-module dependency edge (e.g. a new
  `hov_ceiling` module imported by `email_alerts.py` or `woi_router.py`),
  regenerate the topology artifacts per `docs/topology/README.md`
  (`scripts/extract_topology.py` / `scripts/gen_mermaid.py`).
