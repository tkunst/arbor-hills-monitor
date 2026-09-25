# ADR 048 — Add the Salem Springs 2019 wetland ID site (WIP19) and the 1987 air PTI site (PTI87)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

## WIP19 — `6076539015117221381`

EGLE Wetland Identification Report, **2019-06-24**, site "81-6255 Napier Road-Salem Township",
submission **HNP-75WK-9JX94**, to **Salem Springs Owner, LLC** (Schostak; consultant King &
MacGregor). Level 3 review (2019-05-30) of ~91 acres, PINs A-01-25-300-009/-018/-019/-020/-021 and
A-01-25-400-007, **Section 25**, Salem Twp: wetlands A, B, D/E, F, G, H, I = **25.51 acres regulated**
under Part 303. The reviewer's notes: a "re-do after 3+ years" of a 2015 WIP that also covered 45
acres to the NE (likely site WIP15, ADR 047); a riparian wetland / stream corridor "flowing off site
to the north" regulates most of the wetlands. **Not GFL property** (GFL's Salem parcels are in
Sections 12, 13 and 14), about 1-2 miles south of the landfill along Napier Rd, on the Salem Springs
development land. Tracked because Trisha wants to know about wetland identifications near GFL land.

Live fetch: Documents 4 (the WIP report, two transmittal emails, reviewer photos and notes),
Submissions 1, all else 0.

## PTI87 — `-3813933512584804016`

Two AQD **Air Permit to Install – New Permit** applications, **C-7567** and **C-7568**, received
1987-03-26, Completed. Every profile is otherwise empty, including Documents (pre-MiEnviro records
migrated as submission entries only). Tracked so that any document EGLE later posts to this
registration is archived.

## Decision

Both go into `facilities:` (Documents) and `nsite_sites`, and into all 7 profile tier maps at
**quarterly**. WIP19's 4 existing documents are cleared by a manual `backfill.yml` run right after
this lands (they are below the watcher's 25-doc guard, so the daily watcher would otherwise treat
2019 records as new filings). First sighting on every profile baselines silently.
