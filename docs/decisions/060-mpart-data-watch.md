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
  **17 rows**, every `LabSampleId` distinct. Flags seen on PFOS: `'K'`, `'J'`, `'J, Q'`,
  `' J'` (padded), `' '` and empty. The layer's units are ng/L.
- **Fish** — `EGLE/FcmpOpenData/FeatureServer/1` "Fish Contaminant Monitoring Sampling
  Data" is a **table**, not a layer; layer 0 is the sites. Stations 1484 (Fish Hatchery
  Park) and 1507 ("6-mile") plus the box give **388 rows** across 8 stations, every
  `SampleID` distinct. Station 1484's ten brown-trout rows carry `PFOScode` `'I'` with no
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

One real exceedance exists in the area data: the Napier Rd tributary sample
(`19-UUTJD-0010`, 2021-08-05) reports PFOS **16.5 ng/L** with no flag; Lotext's Rule 57
note (`documents/arbor-hills/source-docs/egle-rule-57-water-quality-values-2026-09-26/`)
compares it with the 12 ng/L non-drink value. This watch reproduces that comparison.

## Decision

### 1. Three items; a `{row key: row hash}` snapshot each

`mpart:sw`, `mpart:fish`, `mpart:sites`, one keyless query each, one explicit `outFields`
list (never `*`), `returnGeometry=false`. The snapshot is the compact JSON
`{"rows": {key: hash16}}`: the fish table alone is 79,163 chars as full canonical records
(388 rows) — over a Sheets cell's 50,000-char cap — but 12,598 as hashes. It refuses to
store anything over 45,000 chars rather than truncate. A blank or **duplicate row key is
a structural error** (the diff is keyed on it; a collision would silently lose a row).
Keys: `LabSampleId` (the lab's own business key; falls back to site|date|duplicate),
`SampleID`, and name + kind for sites. `OBJECTID`, `GlobalID`, coordinates and
`ProjectManager`-style fields are not fetched, per the ADR 018/019 precedent.

### 2. Alerts and the Rule 57 screening

First sighting of an item is a silent baseline. Afterwards a new, changed or removed row
writes a `changed` row and then alerts (Trisha only; an empty recipient list is display-
only and never falls back to the coalition list). A new or changed surface-water row is
compared with the Rule 57 **non-drinking-water human non-cancer values** for PFOS 12,
PFOA 170, PFHxS 210 and PFNA 30 ng/L (config `thresholds_ng_l`; from EGLE's Rule 57
values spreadsheet as saved 2026-09-26, where PFOS was verified 2014, PFOA 2022 and
PFHxS/PFNA 2023). Johnson Drain / Johnson Creek is a non-drink water body per that note.
The alert says this is a **screening comparison** of the reported value with the
published value, not a regulatory determination, and to cite the value in force at the
sample date. Rules: a `K` flag is a non-detect (the value is the method detection limit)
and is never compared; `J` is an estimate and is labelled as such; a value must be
strictly above the threshold. **Fish** PFOS (ppb, fillet) is printed verbatim; code `I`
means no value published and is never a detection; **no fish threshold is applied**
(none was specified and none is invented here).

### 3. Failure modes and liveness

A transient fetch failure is skip-and-warn for a baselined item, but every skipped run
is recorded (`fetch-skipped`), and the run that makes it `stale_alert_after_skips` (3)
consecutive skips sends ONE liveness alert; the first good run records `fetch-ok`. An
item that was never baselined and can't be fetched is loud (exit 1), and a structural
break (`MpartParseError`: no `features`, a truncated result, a missing field, a duplicate
key, an oversized snapshot) is always loud. The Sheet read raises instead of swallowing
errors (a swallowed read would look like "never baselined" and absorb new samples into a
fresh baseline). `--probe` fetches every layer and prints the counts, writing nothing.

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
3. **Surface-water key is `LabSampleId`, not `GlobalID`** (a service-assigned id, like
   `OBJECTID`).

## Adversarial review

**Show-stoppers considered:** (1) a false "exceedance" claim. *Mitigated as features:*
screening language, the `K`/`J` rules, thresholds in config with their source and
verification years, values printed as published. (2) A first-run alert storm — silent
baselines. (3) The service reorganizing or truncating — structural, always loud.
**Detection of a dead watch:** the recorded-skip liveness alert; a red run for a
structural break. **Recovery:** flip `enabled` to pause (tab state survives); thresholds
are config, not code.

**Residual risks accepted:** only PFOS/PFOA/PFHxS/PFNA are fetched for surface water, so a
change to any other analyte, or to a fish field other than PFOS, is not detected; the
meaning of flags beyond `K`/`J` (e.g. `Q`) is not interpreted (they are printed
verbatim); a value revised by the lab alerts as `changed` and shows only the new value
(the snapshot stores hashes, not old values).

## Activation

Ships `mpart.enabled: false`. Keyless — nothing to provision. Review the alert copy (the
Rule 57 wording especially), optionally run the workflow once with `probe=true`, then flip
`enabled: true`. The first run baselines all three items silently. Pause = flip back to
`false`.
