# ADR 051 — Add the 8110 Chubb Rd wetland-fill violation site (CHUBB18)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

nSITE `604563607848499529`, site "81-8110 Chubb, Northville": a WRD-Resources complaint
(HNE-1GQ6-4VT89, 2018-05-24), an inspection 2018-05-23, and **VN-008432** (Violation Notice,
Request for Information, 2018-06-05, Closed) to 8110 Chubb, LLC for unpermitted fill in a
Part 303 regulated wetland at both ends of a new driveway that ends at a stream (Part 301).
Parcel A-01-12-300-012, 5.12 ac, Section 12 (the landfill's section), about 0.5-1 mile west of the
northern expansion parcel; **not GFL-owned** per the parcel data. Live fetch: Documents 1 (the VN),
Compliance Actions 1, Complaints 1, all else 0.

Decision: add srn **`CHUBB18`** to `facilities:` and `nsite_sites`, all 7 tier maps **quarterly**.
The one 2018 document is cleared by a manual `backfill.yml` run (below the watcher's 25-doc guard).
The LLC name stays out of the public labels beyond the site's own nSITE name.
