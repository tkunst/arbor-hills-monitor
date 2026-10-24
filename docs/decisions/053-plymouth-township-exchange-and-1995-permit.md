# ADR 053 — Add Plymouth Township Exchange (PTEX) and a 1995 resources permit site (H95)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

**PTEX — `-1034065027376918328`.** "Plymouth Township Exchange", across Five Mile Rd from the
landfill (Plymouth Twp, Wayne Co). Applicant **JD 5 Mile, LLC** (Jones Development, Kansas City);
wetland agent **Barr Engineering** (also the operator of GFL's perimeter air network); site engineer
Kimley-Horn. Record: WIP (HPJ-02EH-BWWCM, 2022-06-03), pre-application meeting (2022-10-17), JPA
(HPQ-QWWD-ESFKW, 2023-02-23), two Wetland Permit Minor Revisions (2025-03-10, 2025-05-01). Permit
**WRP037948**, Resources Minor Project, In Effect 2025-08-06 to 2028-06-26. Revised plans show
temporary wetland impacts and a **Johnson Drain crossing**. Documents 45.
Tiers: Submissions + Permits biweekly (active permit with a revision history), rest quarterly.

**H95 — `-6102479567065163659`.** Historical WRD "Conversion Resources Application" 95-13-0412-P
(1995-08-17) with a General Permit 1995-09-28 to 1996-12-31 (Expired). No documents. All tiers
quarterly.

Both into `facilities:` and `nsite_sites`. PTEX's 45 documents are cleared by manual `backfill.yml`
runs (50 docs per run; queued behind ADR 052's 74).
