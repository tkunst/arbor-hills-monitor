# ADR 054 — Five Mile Rd corridor sites (LUMB, DTE5M, FMCOM, NTWP)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

Four nSITE registrations along Five Mile Rd between Napier and Ridge (Northville Twp, Wayne Co),
across from the landfill's south side. Together with PTEX (ADR 053) and DTEN (ADR 046) they show
the development and utility activity permitted near the landfill in 2023-2026.

| srn | nSITE id | What it is | Records | Tiers |
|---|---|---|---|---|
| LUMB | -138923829872982091 | "82-7480 Napier Rd" / project "Five Mile and Napier": a **new commercial lumber facility**. JPA 2023-02-17; after-the-fact Violation Notice VN-015316 (2023-11-08); an "Unauthorized Activity" violation from the 2023-10-17 inspection is still **Active - Addressed not Resolved**; Part 303 **Individual Permit WRP039817**, In Effect 2024-04-23 to 2029-04-23. | Docs 24, Viol 1, CA 1, Eval 1, Permit 1 | Violations + Permits biweekly |
| DTE5M | -3197747421558034961 | "82-N Side of 5 Mile Rd from Napier Rd to Ridge Rd": **DTE Energy** Individual Permit **WRP039922**, In Effect 2023-12-20 to 2028-12-20 (2021 pre-app, 2023 JPA). Likely related to the DTE Nitro substation (DTEN). | Docs 37, Subm 2, Permit 1 | Permits biweekly |
| FMCOM | 4473941391942993502 | "82-Five Mile Road East of Napier": 2025-11-03 **pre-application meeting** for a new commercial development on the north side of Five Mile, west of Ridge (Sec. 18). A permit application is the expected next filing. | Docs 5, Subm 1 | Submissions + Permits biweekly |
| NTWP | 1136159897475661963 | "82-Vacant Napier Rd", NW corner of Five Mile and Ridge: **Charter Township of Northville** project. Part 303 minor permit **WRP037947** (2023-06-26 to 2028-06-26; numbered next to PTEX's WRP037948); construction stormwater MIR117693 (2023, expired); a new construction-stormwater NOC filed 2026-05-08 is **On Hold**. | Docs 25, Subm 3, Eval 1, Permits 2 | Submissions + Permits biweekly |

Everything else quarterly. All four into `facilities:` and `nsite_sites`; their 91 documents are
cleared by manual `backfill.yml` runs (queued behind ADRs 052-053).
