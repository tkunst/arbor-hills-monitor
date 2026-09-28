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
  value; station 1507's ten white-sucker rows carry PFOS 5.1-9.5 ppb; other codes seen in
  the box are `'K'` and `'NA'`.
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
- Fish codes: **K** = not detected, the method detection limit is displayed; **J** = an
  estimated concentration; **I** = "analytical interference was present in the sample;
  therefore, a concentration could not be determined"; **QNS** = not enough sample
  remained. Only edible-portion data are shown. (The overnight-coder handoff glossed `I`
  as "no value published"; EGLE's own wording is used here instead.)
- Surface-water flags are "a note from the analytical laboratory"; `Not Measured` means
  that analyte was not part of the analysis. Qualifier definitions include K (below the
  method detection limit), J (below the reporting limit / LOQ), Q (ion-transition ratio
  outside acceptance criteria), B (also in the method blank), E (above the calibration
  range), I (chemical interference; EMPC for Eurofins) and IDA01 (estimated/suspect), and
  "the definition varies by report". `Unit` is "provided by the analytical laboratory".

One real exceedance exists in the area data: the Napier Rd tributary sample
(`19-UUTJD-0010`, 2021-08-05; lab id `UT-0100`) reports PFOS **16.5 ng/L** with no flag;
Lotext's Rule 57 note (`documents/arbor-hills/source-docs/egle-rule-57-water-quality-values-2026-09-26/`)
compares it with the 12 ng/L non-drink value. At baseline this watch records that row as
already above its value; it does not announce it as new.

## Decision

### 1. Three items; a versioned `{row key: row hash}` snapshot each

`mpart:sw`, `mpart:fish`, `mpart:sites`, one keyless query each, one explicit `outFields`
list (never `*`), `returnGeometry=false`, https-only. The snapshot is the compact JSON
`{"v": 1, "rows": {key: hash16}, "hits": [...]}`: the fish table alone is 79,163 chars as
full canonical records (388 rows) — over a Sheets cell's 50,000-char cap — but 12,598 as
hashes, and the watcher refuses to store anything over 45,000 chars rather than truncate.
A stored snapshot that is unreadable or from another `v` is re-baselined silently (and
says so) instead of flagging every row as changed.
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
— and PFHxS/PFNA were added in 2023; Johnson Drain / Johnson Creek is a non-drink water
body per that note). Rules:

- It is a **screening comparison** of the reported value with the published value, not a
  regulatory determination, and the subject says "Rule 57 screening", not "exceedance".
- A sample collected **before the year an analyte's value was verified is not screened**
  (`thresholds_verified_year`): no such value was in force at the sample date.
- A `K` flag (below the method detection limit; the value shown is the limit — the
  observed K rows have value = MDL) is never compared. Every other flag is printed **as
  published** (not re-cased or re-sorted); the alert notes that qualifier definitions vary
  by report. `Not Measured` is shown as such.
- A row is screened only when `Unit` reads as ng/L (also `ppt`); otherwise the alert says
  it was not screened. A value must be strictly above the threshold.
- The snapshot records which (row, analyte) pairs are above their value, so only a **new**
  pair raises the subject; a known one on a changed row is labelled "previously recorded".
  Rows with a new pair are listed first, so the 25-row email cap cannot hide them.
- **Fish** PFOS (ppb, edible portion) is printed as published with EGLE's own code
  definitions; **no fish threshold is applied** (none was specified).

### 3. Failure modes and liveness

A transient fetch failure is skip-and-warn for a baselined item, but every skipped run is
recorded (`fetch-skipped`); the run that makes it `stale_alert_after_skips` (3) consecutive
skips sends a liveness alert, repeated weekly while the outage lasts, and the first good
run records `fetch-ok`. A **successful but empty or sharply smaller response** (under
`max_shrink_fraction` of what was recorded) is treated as a republish glitch — recorded as
a skip, not diffed — until it persists `accept_shrink_after_skips` (2) runs, so it cannot
fire "REMOVED (17)" and then re-announce everything as new. An item never baselined that
can't be fetched is loud (exit 1); a structural break (`MpartParseError`: no `features`, a
truncated result, a missing field, a query ArcGIS rejects with code 400, a blank key, an
oversized snapshot) is always loud; any other exception in one item is reported and the run
moves on (exit 1). A configured recipient whose alert could not be **sent** makes the run
red (the row is already written, so it will not re-fire — the red run is the signal). The
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
other than `K` are printed but not interpreted (EGLE says their meaning varies by report);
a value revised by the lab alerts as `changed` and shows only the new value (the snapshot
stores hashes); `Matrix` and the reporting limit are not part of the record.

## Activation

Ships `mpart.enabled: false`. Keyless — nothing to provision. Review the alert copy (the
Rule 57 wording especially), optionally run the workflow once with `probe=true`, then flip
`enabled: true` (and update `test_shipped_config…`, which pins the shipped `false`). The
first run baselines all three items silently. Pause = flip back to `false`.
