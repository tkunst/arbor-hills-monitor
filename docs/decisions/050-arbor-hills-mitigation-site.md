# ADR 050 — Watch the Arbor Hills Mitigation Site (MITIG)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

## Context

Trisha identified nSITE registration `6707784663696162097` as the **Arbor Hills Mitigation Site**.
Live fetch at add time: every profile is empty (Documents, Submissions, Violations, Compliance
Actions, Evaluations, Permits, Complaints, Public Notices = 0). The 2026 Wetland 1 Joint Permit
Application (HQK-4R25-67T36) included a conceptual wetland mitigation plan, so this registration is
the likely home of the mitigation permit, conservation easement, construction and monitoring
filings once they start.

## Decision

Add srn **`MITIG`** to `facilities:` (Documents are polled daily with the other facilities) and
`nsite_sites`; tier maps: **Submissions biweekly**, **Permits biweekly** (the first filings expected
here), every other profile **quarterly**. Because the site is empty, the first run baselines
nothing; the first real filing alerts normally (Documents through the daily watcher, well under the
25-doc guard).
