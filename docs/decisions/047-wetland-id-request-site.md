# ADR 047 — Watch the 2015 wetland identification request site (WIP15)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

## Context

nSITE registration `1335503966479023299` holds one record: a WRD-Resources **Wetland
Identification Application (WIP Request)**, 294-E9PM-E16P, received 2015-08-26, Completed.
Every other profile, including Documents, is empty. A Wetland Identification Program request
asks EGLE to delineate wetlands on a parcel, typically before development; the 2015 date
coincides with the 2015-2016 expansion attempt.

## Decision

Add srn **`WIP15`** to `nsite_sites` and all 7 profile tier maps at **quarterly** (dormant-site
tier). Not in `facilities:` (no documents). A new request on this site alerts as a new submission.

The WIP report itself is not on nSITE; obtaining it is a records question (MiEnviro submission
detail requires a login; otherwise an EGLE WRD FOIA), tracked in Lotext, not here.
