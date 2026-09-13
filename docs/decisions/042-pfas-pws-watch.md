# ADR 042 — Public Water Supply PFAS sampling watch (Stream R)

*Status: built — 2026-09-12 (`pfas_pws.enabled: false` pending Trisha's review +
recipient choice; see Activation).*

## Context

Backlog item `coder:pfas-pws-watch` (from the Dave Drinan PFAS finding, TASKS
2026-08-18): stand up a watcher on EGLE's Public Water Supply PFAS sampling layer
for supplies near the landfill. **Salem Elementary (WSSN 2001381)** sits in the
landfill's capture zone (a Type-2 *provisional* WHPA since 2013), is sampled
~annually, and has come back **all-non-detect (<2 ppt) across six rounds
Dec-2020 → Feb-2025** — the human baseline in Lotext
`documents/arbor-hills/source-docs/salem-elementary-pfas-egle-pws/NOTES.md`.

This is the **drinking-water half** of the PFAS story the landfill's own
leachate/groundwater PFAS record (the JPA/VN-011821 work, RIDE Stream J) can't
see. The signal to catch: **(a) a new sampling round** appearing and **(b) any
first detection** of a regulated PFAS. Prioritized ahead of the Sep-16 BOC
meeting (PFAS-in-drinking-water is a top expansion argument).

New external (non-EGLE-document) source — a keyless ArcGIS FeatureServer, the
same idiom as Streams E (GFL air) / I (MMD) / J (RIDE). It **never routes through
`egle_doc_parser`**.

## Feasibility spike (live, 2026-09-12)

Endpoint: `gisagoegle.state.mi.us/.../PublicWaterSupplySamplingOpenData/FeatureServer/1`
(the data behind the statewide MPART PFAS map). Findings:

- **Schema:** `WSSN` (int), `SystemName` / `LocName` / `SysSampleCode` (string),
  `SampleDate` (epoch-ms), and the **seven Michigan-regulated PFAS** as *String*
  columns: `HFPODA` (GenX), `PFBS`, `PFHxA`, `PFHxS`, `PFNA`, `PFOA`, `PFOS`.
  One row per (WSSN, sampling event, entry point).
- **`WSSN=2001381` returns exactly the six documented Salem rounds**, all `<2`.
- **Value vocabulary (the correctness-gating check):** enumerated the *complete*
  distinct-value set per analyte across the whole 23,951-row table via
  `returnDistinctValues=true`. Every value is EITHER the single non-detect token
  `<2` OR a plain number (431 distinct numerics). The **"danger set" is empty** —
  no `J`-flags, no `ND`, no mixed reporting limits today.
- **Round key:** `SysSampleCode` is **0 null/blank table-wide** and unique per
  Salem round — a reliable round key. A `(SampleDate, LocName)` composite is kept
  as a fallback; a WSSN can legitimately have two rows on one date (two entry
  points), which `SysSampleCode` distinguishes.

## Design

Mirrors `mmd_client` (the keyless-ArcGIS sibling): one query for every watched
WSSN (`WSSN IN (...)`, explicit `outFields`, `returnGeometry=false`), a
Fetch-vs-Parse error split (`PwsFetchError` transient / `PwsParseError`
structural + always-loud), `epoch_ms_to_date`, a canonical `record_view`. The
per-WSSN snapshot is a ref-keyed dict of rounds (keyed by `SysSampleCode`),
diffed in `pfas_pws_watcher` (the ROP/Evaluations ref-keyed idiom). State is the
append-only `Public Water Supply PFAS Watch` tab. First sighting baselines
silently.

### The classifier is FAIL-SAFE (the load-bearing decision)

`classify_value` returns one of: `nodata` (null/blank), `nondetect` (a
`<`-prefixed limit like `<2`/`<4`, or a known token `ND`/`NOT DETECTED`/`BDL`/
`U`), `detection` (parses as a number, after stripping a trailing qualifier token
like `J` — **including a quantified value below 2 ppt** such as `1.9`, because a
quantified value *is* a detection, not a non-detect), or **`unrecognized`**
(anything else). The watcher treats `unrecognized` as a **possible detection**
(alert + log + route to Measurements with a flag), **never silently dropping
it**. Today's data is clean, but a future lab `J`-flag or new token must never
become a silent false-negative — the worst outcome for a drinking-water watch
(the `fail-safe-when-external-semantics-unknowable` rule).

### No MCL comparison (deliberately dropped)

An earlier design carried a Michigan-MCL reference map to label a detection
"above/below MCL." **Dropped.** Michigan's 2020 MCLs (PFOA 8 / PFOS 16 / PFHxS
51 / PFNA 6 ppt …) are now **looser than the federal 2024 MCLs** (PFOA & PFOS
4 ppt; PFHxS/PFNA/HFPO-DA 10 ppt; plus a mixture Hazard Index). A single-regime
"below MCL" label would *understate* an enforceable federal exceedance — exactly
the "one sloppy number discredits the artifact" failure the data layer's
accuracy bar forbids. The alert ships the **raw value + analyte + WSSN + date**
(fully actionable) and points to "the applicable state and federal PFAS
drinking-water standards" without asserting a number this watch can't keep
correct across two shifting regimes.

### Alerting + Measurements

- **New round, all-non-detect** → alert (the negative result is the news:
  "sampled again, still clean"), subject un-elevated.
- **New round with a detection** (or an unrecognized value) → alert with the
  subject elevated to `DETECTION`, the analyte(s) + value(s) in the body.
- **Detections route to the shared Measurements tab** (`basis=measured`,
  `metric=pfas_<analyte>`, `unit=ppt`, value verbatim). Non-detects do NOT (the
  source feed is the record for those). Write order is **Measurements → watch row
  (advances the hash) → best-effort email**, so a crash re-detects rather than
  loses a detection (a duplicate Measurements row is low-stakes and rare — the
  `ridgewood_archiver` ordering).
- **Baseline** (first sighting) is silent; a *historical* detection already on
  record is **noted in the row + logged, not alerted** (the watch is
  forward-looking; a pre-existing detection is a human-review item, not a new
  event).

## Activation

Ships `pfas_pws.enabled: false` (new-source gate). All Trisha's: review + merge;
the first enabled run baselines every WSSN silently (verified live — Salem
baselines to hash `e910c166…`, 0 emails); pick recipients (Trisha-only to start,
the Meeting/MMD/RIDE precedent), then flip `enabled: true`. Keyless — no secret
to provision. `wssns` is a config list; more capture-zone WSSNs are added there,
no code change. Until enabled, `pfas-pws-watch.yml` runs on schedule as a quiet
no-op.

## Adversarial review — residual risks

1. **A `WSSN IN (...)` query for many/large systems could exceed the ArcGIS
   transfer limit** → a truncated diff could drop a round (and a detection).
   *Detection:* `exceededTransferLimit` raises `PwsParseError` (always loud).
   *Recovery:* paginate per-WSSN. Today's watched set (Salem, ~annual, 6 rows) is
   far under the limit; accepted until the set grows.
2. **The panel is the 7 regulated PFAS only** — not a full EPA-537/533 40-analyte
   scan, and not the non-PFAS landfill contaminants (VOCs, 1,4-dioxane, metals).
   A detection outside this panel is invisible here. Accepted (it's the data EGLE
   publishes); documented so the coverage limit isn't mistaken for "all clean."
3. **A future lab qualifier / new non-detect token** the classifier doesn't
   recognize → surfaced as a *possible detection* (alert + log), never dropped
   (fail-safe by design). The false-positive direction is the safe one here.
4. **Capture-zone completeness:** only Salem (2001381) is confirmed and shipped;
   other in-zone WSSNs aren't yet identified. The watch covers exactly what's
   configured — adding WSSNs is a config edit, and the gap is documented, not
   silent.

## Tests

`tests/test_pfas_pws.py` — hermetic (synthetic ArcGIS payloads, opener mocked
for fetch guards, `FakeSheets` + canned fetch + captured mailer for run()): the
fetch Fetch/Parse split (HTTP error, non-JSON, ArcGIS-error, missing features,
transfer-limit, schema drift, non-numeric WSSN), the fail-safe classifier across
every value form, `record_view` / `round_key` (+ composite fallback) /
`round_detections`, snapshot order-stability, the new-round / detection diff, the
Measurements dict shape, and the full run() matrix (silent baseline, unchanged
no-op, new-clean-round alert, detection-elevated + Measurements write,
baseline-with-historical-detection silent-but-noted, fetch-fail skip-vs-loud,
always-loud ParseError, best-effort email failure still records the row).
