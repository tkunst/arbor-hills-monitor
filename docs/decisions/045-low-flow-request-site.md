# ADR 045 — Watch the low-flow discharge request site (LFLO)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

## Context

nSITE registration `-8293528710588524646` holds one record: a WRD-Resources service request,
"Low Flow Discharge Request" (**Lflow-7643**, received 2011-01-18, Completed). A low-flow
request asks EGLE for the receiving stream's low-flow statistics, which are used to set NPDES
discharge limits. Trisha has observed that these requests recur for this location about every
five years, so the next one is an early signal of a new or changed discharge permit.

Live fetch at add time: Submissions 1, every other profile (Documents, Violations, Compliance
Actions, Evaluations, Permits, Complaints, Public Notices) 0.

## Decision

Add srn **`LFLO`** to `nsite_sites` and to all 7 profile tier maps at **quarterly** (the tier every
dormant site uses; a request every ~5 years does not justify more). Not added to `facilities:`
(no documents). First sighting baselines silently; a new Submission Reference Number alerts as
"NEW SUBMISSION RECEIVED".
