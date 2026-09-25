# ADR 049 — Add the 2015 soil-boring JPA site (BORE15) and the 1989 floodplain request site (FP89)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

## BORE15 — `-4454026774380249654`

Golder Associates, for **Advanced Disposal Inc.** (owner/applicant), submitted a Joint Permit
Application on **2015-10-15** (submission 2A7-61A7-P0BR) for General Permit Category Q (Survey
Activities): **11 soil boreholes for a hydrogeologic study**, Section 12, T1S R7E, Washtenaw County,
with temporary wetland impacts for access. Permit **WRP000808**, Resources General Permit, issued
2015-11-17, expired 2020-11-17. The application's attachments include a borehole-and-wetland
location map and an adjacent-property-owners list. Timing matches the 2015-2016 expansion attempt.
Live fetch: Documents 8 (none previously held by the monitor), Submissions 1, Permits 1, all else 0.

## FP89 — `7284130751247156626`

Site "WASHTENAW: 89-11-0108-FP", 8627 Napier Rd (parcel A-01-12-100-020, Section 12, next to the
landfill). One WRD "Floodplain Elevation Calculation Request", Completed, no date recorded (the
reference format suggests November 1989). No documents on nSITE.

## Decision

Both into `facilities:` (Documents) and `nsite_sites`, and all 7 profile tier maps at
**quarterly**. BORE15's 8 documents are cleared by a manual `backfill.yml` run right after this
lands (below the watcher's 25-doc guard). No personal names in config (FP89 is labeled by address).
