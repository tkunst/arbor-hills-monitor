# ADR 047 — Watch the 2015 wetland identification request site (WIP15)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

## Context

nSITE registration `1335503966479023299` holds one record: a WRD-Resources **Wetland
Identification Application (WIP Request)**, 294-E9PM-E16P, received 2015-08-26, Completed.
Every other profile, including Documents, is empty. A Wetland Identification Program request
asks EGLE to delineate wetlands on a parcel, typically before development.

**Update 2026-09-25 (ADR 048):** EGLE's 2019 re-do of a wetland ID at 6255 Napier Rd (Salem
Springs Owner, LLC, Section 25; site WIP19) calls itself a "re-do after 3+ years" of a **2015 WIP**
that also covered 45 acres to the northeast. This 2015 request is very likely that one, i.e. the
Salem Springs developer's land, not landfill property. Not yet confirmed (no documents here).

## Decision

Add srn **`WIP15`** to `nsite_sites` and all 7 profile tier maps at **quarterly** (dormant-site
tier). Not in `facilities:` (no documents). A new request on this site alerts as a new submission.

The WIP report itself is not on nSITE; obtaining it is a records question (MiEnviro submission
detail requires a login; otherwise an EGLE WRD FOIA), tracked in Lotext, not here.
