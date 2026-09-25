# ADR 052 — Five more nearby nSITE sites (CHUBBRD, WIP25, BDPH, ASB26, CMP23)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

Found by Trisha in nSITE; identified from live fetches and first-page reads on 2026-09-25.
Most sit in Sections 12-13, the landfill's own sections, along Chubb Rd and Five Mile Rd.

| srn | nSITE id | What it is | Records at add time | Tiers |
|---|---|---|---|---|
| CHUBBRD | -5141175505123096250 | "81 Chubb Rd Recon 6 Mile Rd to 5 Mile Rd and Five Mile Rd Recon Chubb Rd to Napier Rd": 2018 JPA (HNE-734F-SRE4E) + construction stormwater NOC (MIR115382); permit WRP013195 (2018-11-05..2023-11-05, Expired); 2020 stormwater inspection found 2 SESC violations still "Active - Addressed not Resolved"; adjacent owners include Advanced Disposal. | Docs 23, Subm 2, Viol 2, CA 1, Eval 1, Permit 1 | Violations biweekly; rest quarterly |
| WIP25 | -8179920515493197813 | 7506 Chubb Rd (parcel A-01-13-300-012, Sec. 13): 2024 pre-application meeting request withdrawn; 2025 Level 3 Wetland Identification (HQ3-NSC0-TP7FT), ~13 ac, report 2025-06-23. Applicant Anglin Civil (same address as the parcel owner, Dalice Co., LLC); consultant PEA Group. | Docs 22, Subm 2 | Submissions + Permits biweekly (a JPA would follow a WIP); rest quarterly |
| BDPH | -1974820987593684099 | 7374 Chubb Rd, Sec. 13: Part 303 permit WRP038206 (2023-07-26..2028-07-26, In Effect) to BD Property Holdings (P.O. box, Dexter; engineer Midwestern Consulting) for two storm-water outfalls discharging treated storm water to wetland. | Docs 29, Subm 1, Eval 1, Permit 1 | Submissions + Permits biweekly; rest quarterly |
| ASB26 | 6658906756287014686 | AQD Asbestos "New Contractor Registration" ASB-00009055, received 2026-01-21, In Process. Contractor name is on the login-only profile tab. | Subm 1 | Submissions biweekly; rest quarterly |
| CMP23 | 8106421448071964642 | One WRD-NPDES complaint (HPY-5JZ9-JFEQE, 2023-10-05). Subject on the login-only profile tab. | Complaints 1 | quarterly |

All five go into `facilities:` (Documents) and `nsite_sites`. The 74 existing documents (CHUBBRD,
WIP25, BDPH) are cleared by a manual `backfill.yml` run (each site is under the watcher's 25-doc
guard). Personal names are kept out of config labels (sites are labeled by address or record number).
