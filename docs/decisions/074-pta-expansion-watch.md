# ADR 074 — Watch the Pine Tree Acres expansion's two state approvals

Date: 2026-11-07
Status: proposed (coder:pta-expansion-watch; PR open for Trisha's review)
Builds on: ADR 009/043 (WDS Stream C), ADR 024 (`_wds_seen` backup log),
ADR 064 (facility scope: neighbor sites are not Arbor Hills evidence).
Handoff: `docs/overnight-coder-handoffs/pta-expansion-watch.md`.

## Context

The Pine Tree Acres (PTA) 75M cubic-yard expansion (WM, Lenox Township, Macomb
County) is the biggest single input to the regional capacity model. Macomb
County approved the plan amendment that sites it on 2026-02-19 and sent it to
EGLE around May 2026. Two state approvals remain:

1. **EGLE's approval of the Macomb County plan amendment.** EGLE lists approved
   plans and amendments on its solid-waste planning page; on 2026-11-07
   Macomb's row read `Macomb | Appendices` (no amendment listed).
2. **The Part 115 construction permit for the expansion**, which shows up in
   WDS (site 398972), not nSITE. On 2026-11-07 WDS listed 10 PTA applications;
   the newest Construction Permit was received 10/31/2019 and issued 3/18/2020.

## Decision

### A. WDS extra sites (`wds.extra_sites`)

Stream C keeps polling Arbor Hills (475946) exactly as before. After that loop,
`check_extra_sites()` polls each extra site's listed collections.

- **State** lives in the same `_meta.wds_seen` dict under
  `"<site_id>:<collection>"` keys. Arbor Hills keys are bare collection names
  (never a colon), so the two cannot collide, and the ADR 024 `_wds_seen` backup
  and recovery log covers the new keys with no code change. Rules A (bad-fetch
  guard) and B (silent first baseline, over-cap re-baseline) apply unchanged:
  the same `diff_collection()` runs, with an optional `classify=` override.
- **Classification** (`RELATED_CLASSIFIERS`; only `applications` and `annual`
  have rules): a NEW Construction Permit application = urgent, subject
  "[URGENT] Pine Tree Acres expansion: construction permit application
  received"; a Construction Permit whose Closure Type changes to Issued =
  urgent, "... construction permit issued" (a new row first seen already
  Issued gets the "issued" subject too); any other change, every Operating
  License row, and every annual report-year = watch. No risk codes: these are
  not Arbor Hills risks. For related sites an application's content is its
  Closure Type alone, so a Closure Date correction on an Issued permit is no
  event (no false "issued" email).
- **Anti-stampede.** Extra sites use a cap of 3 events per collection per run
  (`max_alerts_per_run` overrides): if WDS ever re-formats dates and every row
  re-keys as new, the collection re-baselines silently instead of sending
  several urgent emails for old permits.
- **Routing.** Extra-site events never reach the Arbor Hills Sheet tabs, the
  risk register, or the coalition Sunday digest (ADR 064's lesson: neighbor
  records in the Arbor Hills feed confused readers). Urgent events email the
  site's scoped `recipients` at once; the rest go out as one summary email per
  run to the same list. Empty `recipients` = log only, never the shared list. A
  send that raises, or that `send_email` reports as not sent (returns False,
  e.g. no SMTP configured), reverts the events' seen-state so the next run
  re-sends. Each collection is isolated: one that raises keeps its old
  seen-state and the others still alert.
- **Wording.** WDS shows an application's type and dates, not what it is for.
  The subject uses the "expansion" wording the handoff asked for; the body says
  to open the record to confirm which project the application covers.

PTA is configured with Trisha's address only (the new-stream precedent).
Because WDS is already enabled, this part goes live on merge; the first run
baselines PTA's 10 applications and 30 annual years silently (verified against
the live pages; replaying the live `_meta.wds_seen` through the new code gave
the same Arbor Hills result as `main`: no events, identical seen-state).

### B. EGLE county plans page (`egle_county_plans`)

A small page-diff check, `egle_county_plans_watcher.py`, run from the daily
watcher. For each configured county (Macomb) it reads the row's visible text and
the file names its links point at (query strings dropped, so a re-upload under a
new `?rev=` is not a change) and emails the scoped recipients, quoting old and
new text, when either changes. State is a new append-only tab, `EGLE County
Plans Watch`, on the PRIVATE Sheet (`GSHEET_ID_PRIVATE`), read strictly: the
watch serves the capacity analysis, so it stays off the operator-visible case
file (no private Sheet configured = the check is skipped, never written to the
public Sheet). A fetch error or a missing row is a failed check,
never a change; failures on 3 distinct days in a row send one stale email per
outage. For a change the email goes out before the row is written, so a crash
re-sends rather than loses (the event may happen once in years; a duplicate is
the cheaper failure). A send that returns False (no SMTP) counts as not sent. The fetch uses `pfas_client.fetch_page` (browser
User-Agent): the page returns 403 to a bare client and 200 to a browser UA.

Ships **disabled** (`egle_county_plans.enabled: false`), per the new-source
rule. It runs inside the existing daily workflow, so no new workflow file.

### C. nSITE N5984: not added

Adding PTA to `nsite_sites` is not a one-row change: every nSITE profile
watcher's tier map must list every registry SRN (pinned by tests), and PTA's
nSITE record is air only (ROP0000321, air submissions) and unrelated to the
expansion, which will appear in WDS. So PTA stays out of `nsite_sites`.

## Consequences

- Trisha gets an email when either state approval moves. PTA events reach no
  public Sheet tab, the findings feed or the coalition digest. One exception to
  know about: the PTA WDS seen-state (record keys such as
  `398972:applications|Construction Permit|10/31/2019` and content hashes) sits
  in `_meta.wds_seen` and the `_wds_seen` backup log on the public case-file
  Sheet, like Arbor Hills' own. It is public EGLE data with no analysis in it.
- PTA watch events have no durable Sheet row (only the email and the seen-state).
  If a durable record is wanted later, add a private-Sheet tab; out of scope here.
- Out of scope (per the handoff): collecting PTA documents, any public page about
  PTA, and other landfills. Adding another WDS site later is one `extra_sites`
  entry; a collection beyond `applications`/`annual` needs a related classifier.
