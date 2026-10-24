# ADR 046 — Watch the DTE Nitro substation NPDES site (DTEN, MI0060368)

*Status: active — 2026-09-25 (Trisha-directed, option "a": profile watchers only; config-only).*

## Context

Trisha found nSITE registration `-4327269113100269233` and could not identify it. It is
**"DTE Nitro Substation Napier Road & 5 Mile Conduit"** (NPDES application checklist, 2024-03-26:
"DTE: Nitro SS Napier Rd & 5 Mile Conduit"; consultant TRC; lab project "DTE Nitro Phase II"),
next to the landfill at Napier Rd and Five Mile.

Live fetch at add time:

| Profile | Records | Detail |
|---|---|---|
| Permits | 1 | MI0060368, NPDES Individual Permit, Industrial/Commercial IP, In Effect 2024-08-28 to 2028-10-01 |
| Submissions | 2 | NPDES application (2024-03-22, Completed); **Facility Ownership/Control Transfer Notification (2026-09-17, In Process)** |
| Violations | 8 | 7 "DMR value exceeds Limit value (Permit)" 2025-11-01..2026-04-01, all "Active – Not Reviewed"; 1 DMR non-report (2024-11) |
| Documents | 213 | DMRs, lab reports (PFAS effluent monitoring at MP-002B, ~1.1 MGD), EGLE notes (9/21/2026 voicemail: consultant reported a TSS exceedance ~120 mg/L) |
| Compliance Actions / Evaluations / Complaints / Public Notices | 0 | |

## Decision

Add srn **`DTEN`** to `nsite_sites` and all 7 profile tier maps. **Daily**: Submissions (the
ownership transfer is in process), Violations (active, unreviewed limit exceedances), Permits
(a transfer changes the permit record). **Biweekly**: Compliance Actions, Evaluations, Public
Notices (enforcement or a permit modification is plausible). **Quarterly**: Complaints.

**Not added to `facilities:` (Documents).** 213 documents would each need an Anthropic
classification, which risks the monthly spend cap; Trisha chose watchers only. Revisit if the
PFAS results or the discharge point become important to the case.

First sighting baselines silently (no alert flood).
