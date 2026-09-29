# ADR 060 — MPART PFAS open-data layers watch (Stream V)

*Status: built — 2026-09-28 (`mpart.enabled: false` pending Trisha's review of the alert
copy; see Activation).*
Builds on: ADR 012 (MPART web-page watch), ADR 042 (Stream R: the public-water-supply
layer), ADR 018/019 (keyless ArcGIS status watches; explicit `outFields`, OID/GlobalID/
coordinates kept out), ADR 023 (digest snapshots for large tables).

## Context

MPART (the Michigan PFAS Action Response Team, a multi-agency team housed at EGLE, not
a division) publishes PFAS results as keyless ArcGIS open-data layers. The monitor
watches MPART's Arbor Hills *web page* (ADR 012) and, through Stream R, the public-
water-supply layer, but none of the layers that hold the Johnson Drain / Johnson Creek /
upper-Rouge surface-water and fish results, which were found by hand in 9/2026.

## Feasibility spike (live, 2026-09-28)

- **Surface water** — `EGLE/PfasOpenData/MapServer/0` "Pfas Surface Water": a point
  feature layer (`maxRecordCount` 1000), one row per sample with about 30 PFAS analytes
  as `CAS…_<name>` value / `Flag` / `Mdl` / `Rl` column groups, plus `LabSampleId`,
  `SiteCode`, `Waterbody`, `Description`, `CollectionDate`, `Unit`, `Latitude`,
  `Longitude`. In the Johnson Drain box (lon -83.66..-83.40, lat 42.34..42.46) it holds
  **17 rows**. The lab ids are a mix: Eurofins ids (`240-169452-6`) and short, site-derived
  Vista labels (`UT-0100`, `JD-0100`) — so `LabSampleId` alone is not a safe key. Flags seen
  on PFOS: `'K'`, `'J'`, `'J, Q'`, `' J'` (padded), `' '` and empty.
- **Fish** — `EGLE/FcmpOpenData/FeatureServer/1` "Fish Contaminant Monitoring Sampling
  Data" is a **table**, not a layer; layer 0 is the sites. Stations 1484 (Fish Hatchery
  Park) and 1507 ("6-mile") plus the box give **388 rows** across 8 stations, every
  `SampleID` distinct. Station 1484's ten brown-trout rows carry `PFOScode` `'I'` and no
  value; station 1507's ten white-sucker rows carry PFOS 5.1-9.5 ppb; other PFOS codes
  seen in the box (live query) are `'K'` and `'NA'`.
- **Sites / AOIs** — MPART's `SitesAoisMerge` (an ArcGIS Online service, layer 1 — layer
  0 does not exist): `Name LIKE '%Arbor Hills%'` returns one row; the box adds six more (7
  in all). The layer carries `SiteLead`, `SiteLeadEmail` and `SiteLeadPhone` — an EGLE
  employee's name, e-mail and phone.
- **Public water supply** — `PublicWaterSupplySamplingOpenData/FeatureServer/1` is
  already watched by the live Stream R (`pfas_pws.enabled: true`, WSSN 2001381). Live:
  WSSN 2046881 (named "GFL ENV - COMPOST FACILITY" / "GREEN FOR LIFE") has 7 rows, last
  sampled 2025-07-09; WSSN 2037081 has none.

**What EGLE says about these layers** (their ArcGIS item descriptions, saved in Lotext
`documents/arbor-hills/source-docs/egle-mpart-pfas-gis-app-2026-09-26/`):

- Both are **static pulls**: surface water "last pulled 2/2025"; fish "a static pull … on
  01/14/2026 … updated annually". A new row therefore means EGLE republished the layer —
  not that sampling just happened. Expect quiet stretches and a burst when EGLE re-pulls.
- Fish codes (the FISH layer's own description): **K** = not detected, the method detection
  limit is displayed; **J** = an estimated concentration; **I** = "analytical interference was present in the sample;
  therefore, a concentration could not be determined"; **QNS** = not enough sample
  remained. Only edible-portion data are shown. (The overnight-coder handoff glossed `I`
  as "no value published"; EGLE's own wording is used here instead.)
- Surface-water flags are "a note from the analytical laboratory"; `Not Measured` means
  that analyte was not part of the analysis. EGLE's prose: "K" flagged analytes "were not
  detected in the sample and therefore the method detection limit (MDL) is displayed"; "J"
  flagged results "indicate an estimated concentration as the result is above the MDL but
  below the laboratory reporting limit". Its qualifier table words K as "below the Method
  Detection Limit/LOD" (Vista, EGLE, Eurofins) and J as "below the Reporting Limit/LOQ", and
  also lists Q (ion-transition ratio outside acceptance criteria), B (also in the method
  blank), E (above the calibration range), I (chemical interference; EMPC for Eurofins) and
  IDA01 (estimated/suspect). EGLE: "Qualifiers are analytical laboratory specific and often
  it is better to refer to the original analytical report". `Unit` is "provided by the
  analytical laboratory".

One row in the area data reports PFOS above the non-drink screening value: the Napier Rd
tributary sample (`19-UUTJD-0010`, 2021-08-05; lab id `UT-0100`; waterbody "Unnamed Trib to an
Unnamed Trib", not Johnson Drain) reports PFOS **16.5 ng/L** with no flag; Lotext's Rule 57 note
(`documents/arbor-hills/source-docs/egle-rule-57-water-quality-values-2026-09-26/`) compares it
with the 12 ng/L non-drink value. At baseline this watch records that row as
already above its value; it does not announce it as new.

## Decision

### 1. Three items; a versioned `{row key: row hash}` snapshot each

`mpart:sw`, `mpart:fish`, `mpart:sites`, one keyless query each, one explicit `outFields`
list (never `*`), `returnGeometry=false`, https-only. The snapshot is the compact JSON
`{"v": 2, "rows": {key: hash16}, "hits": {site|date|analyte: value}}`: the fish table alone is 79,163 chars as
full canonical records (388 rows) — over a Sheets cell's 50,000-char cap — but 12,598 as
hashes, and the watcher refuses to store anything over 45,000 chars rather than truncate.
A stored snapshot that is unreadable or from another `v` is re-baselined silently (and
says so) instead of flagging every row as changed. `hits` records the surface-water results
already announced as above a screening value, keyed by **site | collection date | analyte**
(not by row key, so a row re-issued under a new lab id is the same result) and **never
pruned** (a result that leaves the layer and returns is not announced twice).
Keys: surface water is **composite** — `LabSampleId|SiteCode|date|Duplicate` (the lab id
alone can repeat across lab jobs); fish `SampleID`; sites name + kind. A blank key is a
structural error; a **duplicate key is disambiguated** (`#1`, `#2`, ordered by row hash,
independent of the order the service returns them) and recorded in the row note, so one
collision can never leave the layer permanently unwatched. `OBJECTID`, `GlobalID`,
coordinates and staff-contact fields are never fetched (ADR 018/019 precedent).

### 2. Alerts and the Rule 57 screening

First sighting of an item is a silent baseline. Afterwards a new, changed or removed row
writes a `changed` row and then alerts (Trisha only; an empty recipient list is display-
only and never falls back to the coalition list). A new or changed surface-water row is
compared with the Rule 57 **non-drinking-water human non-cancer values** — PFOS 12, PFOA
170, PFHxS 210, PFNA 30 ng/L (config `thresholds_ng_l`; EGLE's Rule 57 values spreadsheet
as saved 2026-09-26, where PFOS was verified in 2014, PFOA 2022 — replacing an older value
— and PFHxS/PFNA were added in 2023). Johnson Drain / Johnson Creek is a non-drink water
body per the monitor's own Rule 57 note (Lotext README — not EGLE's statement, and not
checked against the water body's Rule 100 designation), but the layer carries no
designation and the watch applies the non-drink values to **every surface-water
row in the search box** — the alert says so and quotes the lower drink-water values (PFOS 11,
PFOA 66, PFHxS 59, PFNA 19 ng/L). Rules:

- It is a **screening comparison** of the reported value with the published value, not a
  regulatory determination, and the subject says "Rule 57 screening", not "exceedance".
- A sample collected **before the year an analyte's CURRENT value was verified is not
  screened** (`thresholds_verified_year`): at that date an older value (for PFOA, a much
  higher one) or none applied, so the report has to be checked by hand. A row with no
  collection date is screened (fail-open).
- A `K` flag (EGLE's surface-water layer description: K flagged analytes "were not detected
  in the sample and therefore the method detection limit (MDL) is displayed"; the observed K
  rows have value = MDL) is never compared. Every other flag is printed **as published** and
  **not interpreted or glossed by the watch**: a `J` on a surface-water hit is compared like
  any reported value and shown as `[J]`, with no meaning of the monitor's own on the hit
  line. EGLE words J two ways (prose: "an estimated concentration as the result is above the
  MDL but below the laboratory reporting limit"; table: "below the Reporting Limit/LOQ") and
  says qualifiers are analytical-laboratory specific, so the alert's caveat paragraph quotes
  EGLE's prose for K and J as attributed quotes and points to the original analytical report.
  `Not Measured` is shown as such.
- A row is screened only when `Unit` reads as ng/L (also `ppt`); otherwise the alert says
  it was not screened. A value must be strictly above the threshold.
- The snapshot records which (site, date, analyte) results are above their value, so only
  a **new** one raises the subject; a known one on a changed row is labelled "already
  recorded; not new" and, if its value differs, "the value on record for it is X ng/L". Rows with a
  new result are listed first, so the 25-row email cap cannot hide them.
- A change that touches **no row** but newly puts an unchanged row above a value (a
  threshold in config was tightened, or this code changed) is written as a `changed` row
  and alerted in its own "UNCHANGED rows now above a screening value" section — never as a
  subject-only email — and its subject and opening line say the monitor's screening changed,
  not the layer ("N unchanged … row(s) now above a non-drink value (screening values or watch
  logic changed)"). A snapshot that moved with nothing to say writes its row and no email.
- **Fish** PFOS (ppb, edible portion) is printed as published with EGLE's own code
  definitions; **no fish threshold is applied** (none was specified).

### 3. Failure modes and liveness

A transient fetch failure is skip-and-warn for a baselined item, but every skipped run is
recorded (`fetch-skipped`); the run that makes it `stale_alert_after_skips` (3) consecutive
skips sends a liveness alert, repeated weekly while the outage lasts, and the first good
run records `fetch-ok`. A **successful but empty or sharply smaller response** (under
`max_shrink_fraction` of what was recorded) is treated as a republish glitch and has its
OWN counter — `shrink-held` rows, separate from fetch failures, so two failed fetches can
never pre-authorize accepting a truncated response (review round 2, HIGH). It is not
diffed, and is accepted as real only after the SAME shrunken snapshot (by hash) has been
held on `accept_shrink_after_skips` (2) earlier runs; a flapping response is never accepted
(`stale_alert_after_skips` consecutive holds send a liveness alert), and a normal-sized
response records `held-cleared`, so a stale streak cannot carry over. So it cannot fire
"REMOVED (17)" and then re-announce everything as new. An item never baselined that
can't be fetched is loud (exit 1) — and so is one whose FIRST response is empty (nothing is
baselined: a wrong bbox/URL/filter must not become a silently "watched" empty layer); a
structural break (`MpartParseError`: no `features`, a truncated result, a missing field, a
query ArcGIS rejects with code 400, a retired layer (HTTP 404/410), a blank key, an
oversized snapshot) is always loud; any other exception in one item is reported and the run
moves on (exit 1). A configured recipient whose alert could not be **sent** makes the run
red — change alert or liveness alert alike (the row is already written, so it will not
re-fire — the red run is the signal). The
Sheet read raises instead of swallowing errors (a swallowed read would look like "never
baselined"). `--probe` fetches every layer and prints the counts, writing nothing.

### 4. Public Sheet is right here

Unlike the RRD/GovQA document streams (ADR 058/059), this stream reads public lab and
site-list data with no personal information, so its `MPART Data Watch` tab lives on the
public case-file Sheet like the other watch tabs. The one personal-data field in scope,
the sites layer's EGLE staff contact, is never fetched.

## Deviations from the handoff (flagged for Trisha)

1. **The public-water-supply layer is not included.** It is already watched by the live
   Stream R; re-watching it would double-alert. WSSN 2046881 (above) is a candidate to add
   to `pfas_pws.wssns` — a one-line live-path edit left for Trisha, deliberately not made
   here. WSSN 2037081 returns no rows.
2. **`SiteLead` is not diffed.** The handoff said to diff the site lead; it is a named
   EGLE employee (and reassignment churn), so it is neither fetched nor displayed.
3. **Surface-water key is composite, not `GlobalID`** (a service-assigned id, like
   `OBJECTID`).
4. **Fish code `I`** is described in EGLE's words (analytical interference), not as "no
   value published".
5. **Non-drink values on every row.** The handoff named the non-drink values; the layer has
   no water-body designation, so they are applied to every surface-water row in the box and
   the alert says so (see §2).

## Adversarial review

**Show-stoppers considered:** (1) a false or stale "exceedance" claim — mitigated as
features: screening wording, verified-year and unit gating, the `K` rule, the
previously-recorded-hit rule, thresholds in config with their source and years. (2) A
first-run alert storm — silent baselines. (3) A republish glitch (empty or shrunken
response) — the suspect-response guard. (4) The service reorganizing or truncating —
structural, always loud. **Detection of a dead watch:** recorded-skip liveness alerts (with
a weekly repeat); a red run for a structural break or a lost alert. **Recovery:** flip
`enabled` to pause (tab state survives); thresholds are config, not code.

**Residual risks accepted:** only PFOS/PFOA/PFHxS/PFNA are fetched for surface water, so a
change to any other analyte, or to a fish field other than PFOS, is not detected; flags
other than `K` are printed but not interpreted (EGLE says qualifiers are laboratory
specific); a value revised by the lab alerts as `changed` and shows only the new value (the
snapshot stores hashes; a known hit shows what was recorded); the `hits` record is never
pruned, so raising a threshold in config leaves earlier announced hits on record; a row
edit and a threshold change in the SAME run can announce an unchanged value under "new/changed
… result(s)" (rare, cosmetic); a stored snapshot that is unreadable or from a future
`SNAPSHOT_VERSION` re-baselines on whatever the current response is, WITHOUT the
shrink guard — inert today (only `v=2` exists), but a future version bump landing on a
truncated response would trust it as the new baseline;
`Matrix` and the reporting limit are not part of the record; a duplicate group's `#n`
labels follow row-hash order, so editing one member can relabel its siblings (all list as
changed; rare — the live layers held no duplicate keys); a layer that legitimately
returns zero rows at first sighting is refused loudly rather than baselined.

## Activation

Ships `mpart.enabled: false`. Keyless — nothing to provision. Review the alert copy (the
Rule 57 wording especially), optionally run the workflow once with `probe=true`, then flip
`enabled: true` (and update `test_shipped_config…`, which pins the shipped `false`). The
first run baselines all three items silently. Pause = flip back to `false`.
