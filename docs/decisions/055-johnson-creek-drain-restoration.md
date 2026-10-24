# ADR 055 — Watch the Johnson Creek Intercounty Drain Restoration site (JCDR)

*Status: active — 2026-09-25 (Trisha-directed; config-only).*

nSITE `-9161999632999417877`: Joint Permit Application **HPG-9289-R76GQ**, "Johnson Creek Intercounty
Drain Restoration", received 2022-05-18, status **On Hold**; consultant Environmental Consulting &
Technology (ECT). EGLE's 2022-06-30 correction request (6 items) raised that dredging over 2,000
cubic yards normally requires **sediment testing for contaminants** (waivable only with a showing of
minimal downstream risk) and that no in-stream BMPs were shown to keep suspended sediment from
moving downstream. Johnson Creek receives the landfill's discharges, so sediment testing (or its
waiver) here bears directly on the PFAS/leachate record. Documents 45; no permit issued yet.

Decision: srn **`JCDR`** into `facilities:` and `nsite_sites`; **Submissions + Permits biweekly**
(the on-hold application resuming, or a permit issuing, is the event to catch), rest quarterly. Its
45 documents are cleared by manual `backfill.yml` runs.
