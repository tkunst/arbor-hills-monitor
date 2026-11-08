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

### A. WDS extra sites (`wds.extra_sites`): a before/now change watch

Stream C keeps polling Arbor Hills (475946) exactly as before. After that loop,
`check_extra_sites()` polls each extra site's listed collections
(`applications`, `annual`).

- **Any change, reported as it is.** A new row, a changed field, or a row no
  longer listed goes out in ONE email per site per run, showing what the record
  said before and what it says now (applications: Closure Type and Closure Date;
  annual: permitted capacity, years remaining, waste CY and tons). The subject is
  factual (`Pine Tree Acres (WM, Macomb) WDS: 1 record change(s)`). There are no
  severity tiers and no inference about what a change means.
- **Why not classify.** The first design sent "[URGENT] … construction permit
  issued" when it judged a permit had been issued. Four review rounds each found
  a new way for that judgement to be wrong (a reworded status, a capitalisation
  change, a Receipt Date corrected backward or forward re-keying an old Issued
  permit as "new"). These records change a few times a year at most (Pine Tree
  Acres has 10 applications since 2002), so reporting every change is cheap and
  makes no claim: a corrected Receipt Date shows up as a near-identical row
  "(not listed) → …", and the next run reports the old one "(no longer
  listed)".
- **State** lives in the same `_meta.wds_seen` dict under
  `"<site_id>:<collection>"` keys. Arbor Hills keys are bare collection names
  (never a colon), so the two cannot collide. The stored per-record value is the
  watched fields as readable text (e.g. `Closure Type: Issued; Closure Date:
  3/18/2020`), not a digest, so the next run can print "before". The ADR 024
  `_wds_seen` backup and recovery log persists it as-is.
- **Rules.** Rule A (bad-fetch guard) and Rule B(i) (silent first baseline)
  apply unchanged (`diff_collection()` with optional `classify=` / `hasher=`).
  Rule B(ii)'s over-cap re-baseline is off for extra sites: however many
  changes, it is one email, so nothing is silently dropped. A first run or a
  Rule A skip never reports rows as "no longer listed", and a row is reported
  gone only after it is missing from two good fetches in a row (a per-key
  `missing` count in the entry), so one short page from WDS is not read as
  records deleted and then re-added. Two rows with the same key in one fetch
  (same type, same receipt date) are kept apart with a " #2" suffix instead of
  flapping against each other every run; rows are matched to the stored
  records by value, so a new duplicate never takes over an existing record's
  key (which would read as the old permit's status changing).
- **Routing.** Extra-site changes never reach the Arbor Hills Sheet tabs, the
  risk register, or the coalition Sunday digest (ADR 064's lesson). Email goes to
  the site's scoped `recipients` only; empty = log only. A send that raises, or
  that `send_email` reports as not sent (returns False, e.g. no SMTP), puts every
  change back so the next run reports it again. Each collection is isolated: one
  that raises keeps its old seen-state and the others still report.
- **Switch.** `wds.extra_sites_enabled` (on the generated activation table)
  pauses every extra site without losing state.

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
Plans Watch`, on the PRIVATE Sheet (`GSHEET_ID_PRIVATE`), read strictly. These
rows are working notes, not case-file evidence, so they sit with the owner's
other working tabs. That the monitor watches this page is not secret (the
public repo and Actions log show it). No private Sheet configured = the check
is skipped, never written to the public Sheet. A fetch error or a missing row is a failed check,
never a change; failures on 3 distinct days in a row send one stale email per
outage. For a change the email goes out before the row is written, so a crash
re-sends rather than loses (the event may happen once in years; a duplicate is
the cheaper failure), and while the row write keeps failing, `_meta` remembers
the change already emailed so it is not re-sent every day. A send that returns
False (no SMTP) counts as not sent. The fetch uses `pfas_client.fetch_page` (browser
User-Agent): the page returns 403 to a bare client and 200 to a browser UA.

A failure of the step itself (no private Sheet configured, its tab unreadable,
a county's row write failing) can't be recorded on the private Sheet, so it is
counted in `_meta.county_plans_health` on the main Sheet (dates, an error
class, and while failing the already-emailed change's public row text and file
names); failures on 3 distinct days send one "watch not running" email, and the
next successful run resets the count.

Row matching accepts the county name with an optional "County", plan year and
stars ("Washtenaw 2019**", "Macomb*"), so an approval marked on the name itself
reads as a change, not as a missing row.

Ships **disabled** (`egle_county_plans.enabled: false`), per the new-source
rule. It runs inside the existing daily workflow, so no new workflow file.

### C. nSITE N5984: not added

Adding PTA to `nsite_sites` is not a one-row change: every nSITE profile
watcher's tier map must list every registry SRN (pinned by tests), and PTA's
nSITE record is air only (ROP0000321, air submissions) and unrelated to the
expansion, which will appear in WDS. So PTA stays out of `nsite_sites`.

## Consequences

- Trisha gets an email whenever a watched Pine Tree Acres WDS record or the
  Macomb plans row changes, showing before and now. PTA events reach no
  public Sheet tab, the findings feed or the coalition digest. One exception to
  know about: the PTA WDS seen-state (under `398972:applications` and
  `398972:annual`: record keys such as `Construction Permit|10/31/2019` with
  readable values such as `Closure Type: Issued; Closure Date: 3/18/2020`) sits
  in `_meta.wds_seen` and the `_wds_seen` backup log on the public case-file
  Sheet, like Arbor Hills' own. It is public EGLE data with no analysis in it.
- PTA watch events have no durable Sheet row (only the email and the seen-state).
  If a durable record is wanted later, add a private-Sheet tab; out of scope here.
- Out of scope (per the handoff): collecting PTA documents, any public page about
  PTA, and other landfills. Adding another WDS site later is one `extra_sites`
  entry; a collection beyond `applications`/`annual` needs a related classifier.
