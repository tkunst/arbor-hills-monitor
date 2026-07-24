# ADR 021 — Tiered Submissions polling across all 19 MiEnviro-subscribed entities

Date: 2026-07-24
Status: accepted
Builds on: ADR 020 (Stream K, the Submissions watch itself), ADR 008
(multi-facility nSITE polling / anti-stampede guard)

## Context

After Stream K shipped (ADR 020), Trisha provided the PDF listing all 19
entities she personally subscribed to on MiEnviro Portal — she "signed up for
every variation of all the entities I could find," expecting duplicates.
Resolving each subscription to a real nSITE site ID (via the authoritative
`ncore/ss/subscriptionusers` API, not free-text search) found 19 genuinely
distinct site IDs: the 5 already tracked by Stream K, plus 14 more —
duplicate/historical registrations for Arbor Hills Landfill, Arbor Hills
Energy, and Advanced Disposal, most with sparse-to-zero Documents/Submissions
history.

Trisha pulled Documents + Submissions activity for all 19 (a one-off script,
not committed) to inform a polling cadence: "Then we can see how many are
used recently and which ones to poll daily. Perhaps we should poll the
infrequently used ones every other week in case a EGLE staffer makes a
typo." Her final direction: **"Do lowest tier quarterly. Build it."**

## Decision

### 1. Three tiers by observed activity, not per-facility guessing

| Tier | Sites | Basis |
|---|---|---|
| Daily | 6 | The 5 original Stream K facilities (RA, WRD, N1504, P1488, N2688) + `AHLI` ("Arbor Hills Landfill, Inc.", bare) — genuine Dec-2025 document activity, surfaced resolving the 19 subscriptions. |
| Biweekly | 6 | `P1504` (a 4th nSITE record for Arbor Hills Energy) + 5 "Arbor Hills Landfill" Asbestos-type duplicate registrations (`AHLA`–`AHLE`) — real but sparse 2021–2024 filing history. |
| Quarterly | 7 | 2 Advanced Disposal duplicates, the Composting Facility, 2 bare Arbor Hills Energy duplicates, the historical Sanitary Landfill registration, and the no-SRN Emerald RNG sibling — zero or a single decades-old record each. |

Quarterly is deliberately the floor, not "skip" — Trisha's own framing
("in case a EGLE staffer makes a typo") is insurance against a filing landing
under the wrong duplicate registration, which is exactly the failure mode a
zero-history site can't rule out just because nothing has ever appeared there.

### 2. `nsite_submissions.sites` is its OWN list — the Documents `facilities:` list is untouched (except one deliberate addition)

The first draft of this build added all 14 new sites to `facilities:`, since
that's the list `watcher.py`/`backfill.py` already loop over. Rejected after
review: that loop is **unconditional** — every site in `facilities:` gets
polled for Documents *daily*, regardless of any Submissions-side cadence. A
"quarterly" site would then be polled daily for documents while only
quarterly for submissions, contradicting the cadence Trisha asked for. It
would also pull ~12 never-reviewed historical documents (summed across the 14
sites) into the next weekly digest as an unrequested side effect of a build
that was only supposed to add polling cadence.

So `config.yml` gets a new `nsite_submissions.sites:` list — all 19 sites,
each carrying its own `poll` tier — read only by `nsite_submissions_watcher.py`.
`facilities:` (the Documents pipeline) gets exactly **one** addition: `AHLI`,
added on its own merits (real recent activity, same bar as WRD in ADR 020),
not as a byproduct of the tiering system. The other 13 sites are Submissions-
only. The 5 original facilities appear in both lists — accepted duplication,
far cheaper than the alternative of `watcher.py`/`backfill.py` having to
understand a cadence concept they don't otherwise need.

### 3. Cadence is a pure, stateless, hash-staggered function firing across a 3-day window — not one exact day

`_is_due(cadence, srn, today)` in `nsite_submissions_watcher.py`:

```python
if cadence == "daily":
    return True
period = {"biweekly": 14, "quarterly": 90}[cadence]
offset = int(hashlib.sha256(srn.encode()).hexdigest(), 16) % period
return (today.toordinal() - offset) % period < _DUE_WINDOW_DAYS   # = 3
```

No stored "last polled" date is needed — the workflow's existing daily cron
is untouched; `run()` just skips a site (no fetch, no Sheet read, no alert)
on days its tier says isn't due. `offset` is hashed per-`srn` so same-tier
sites don't all land on the same day. The window is **3 consecutive days per
period, not 1**, specifically because a single missed/failed run — the exact
failure class this repo's `rop-watch` hit the same morning this build
started (a GitHub Actions runner-acquisition miss, not a code bug) — would
otherwise blank a quarterly site out for a full 90 days with no recovery
until the next window. A 3-day window means one missed run still leaves
another chance within days, not months.

### 4. `facilities:` addition (`AHLI`) does not stampede alerts and does not collide doc_ids

Same checks as ADR 020 Decision 4, re-verified for `AHLI` specifically —
not assumed just because WRD passed them:

- **Stampede:** `AHLI` has 4 Documents, far under `watcher.max_new_docs_per_run`
  (25) even combined with any other pending backlog, and `backfill.py` never
  calls `is_urgent` regardless. No urgency-email risk from this addition.
- **doc_id overlap:** `AHLI` is a duplicate nSITE registration for the SAME
  physical landfill N2688 already tracks — exactly the case most likely to
  share doc_ids, since ADR 008's shared-Sheet/no-composite-key design rests
  entirely on doc_ids being globally unique across facilities. Live-checked
  before merging: `AHLI`'s 4 doc_ids against all 5 already-tracked
  facilities' doc_ids (RA 741, WRD 69, N1504 53, P1488 25, N2688 774) —
  **zero overlap**. `AHLI`'s 4 docs are an Asbestos Notification of Intent to
  Renovate/Demolish (2024-12-16) and 3 Form-Submission attachments tied to a
  2025-12-08 filing — routine notification paperwork, the same document
  class the 5 Asbestos-duplicate registrations in the biweekly tier already
  file regularly, not a violation/exceedance type.

### 5. This supersedes ADR 008's exclusion of the no-SRN Emerald RNG sibling

ADR 008 deliberately excluded nSITE id `-1194242252385100852` ("EMERALD RNG,
LLC", no SRN assigned) from `facilities:` — "no SRN and ~0 air docs, so
tracking it would only add noise." That reasoning still holds for Documents
(it remains excluded from `facilities:`), but Trisha's 19-entity subscription
list includes it, and the whole point of the quarterly tier is exactly this
kind of near-zero-activity duplicate. It's now `ERNGX` in
`nsite_submissions.sites`, quarterly tier — a conscious override of ADR 008's
scope (Documents polling), not a contradiction of its actual finding (this
record has no meaningful document history).

### 6. No separate ADR-020-style client-layer live-verification section here

The fetch path (`fetch_site_submissions`), error contract, diff logic, and
alert formatting are all unchanged from ADR 020 — this build only adds a
gating function and a longer site list consumed by the same, already-verified
code path. Verification for this ADR is: `_is_due`'s pure behavior (unit
tested — periodicity, per-srn stagger, window-survives-a-missed-run) plus a
real post-merge `workflow_dispatch` run confirming the 14 new sites baseline
correctly and cadence-gating skips the sites not due that day (see
Activation).

## Consequences / residual risks (accepted)

- **A quarterly site's Submissions history is checked at most ~12 times a
  year** (3-day window × 4 quarters). If a submission both lands and is later
  removed/superseded entirely within a window we miss, this watch would never
  see it — accepted, matches the "we don't expect activity here" premise of
  the tier; if a site starts showing real activity, promote it to biweekly or
  daily by editing its `poll` field.
- **`AHLI`'s Documents backlog (4 docs) lands in the next weekly digest** as
  newly-discovered historical documents, not framed as "just filed" — a
  one-time, minor side effect of the single deliberate `facilities:` addition
  in Decision 2, distinct from the 13 Submissions-only sites which have zero
  Documents-side effect.
- **Hash-based stagger is deterministic but not rebalanced if a site's `srn`
  changes.** Renaming a site's `srn` code silently reassigns its due-day
  window and (per Decision 2's key scheme, `subm:<srn>`) resets its
  Submissions Watch history to a new tab key, since the srn is also the
  snapshot key. Don't rename an existing site's `srn` without also
  considering the Sheet-history implications.

## Alternatives considered

- **Collapse the 14 new sites into `facilities:` directly** — the original
  draft; rejected per Decision 2 (unconditional daily Documents polling
  contradicts the requested cadence, plus unrequested digest noise).
- **Store a "last polled" date per site in the Sheet instead of a hash-based
  window** — rejected: adds a write path and a new failure mode (a Sheet
  read failure could either double-poll or never-poll a site) for no benefit
  over a pure, stateless function; the existing "diff against last snapshot"
  Sheet read already gives an implicit floor (an unmodified snapshot never
  re-alerts) so nothing is lost by keeping the due-check itself stateless.
- **Exact single due-day instead of a 3-day window** — rejected per Decision
  3: too fragile against exactly the runner-acquisition failure class this
  repo has already hit once.
- **4 tiers (split "quarterly" from a separate "monthly" bucket for the 2
  single-old-record sites)** — considered (an earlier draft of the activity
  analysis proposed this), collapsed to 3 tiers per Trisha's explicit
  "lowest tier quarterly" direction, which reads as one bottom tier, not two.

## Activation

No new `enabled` flag — `nsite_submissions.enabled: true` already ships from
ADR 020; this build only widens `sites` from 5 to 19 and adds per-site
cadence. No new secrets, no workflow file changes (same daily cron; gating
happens inside the script). `_is_due` is date-dependent, so the exact due
set differs by dispatch day — computed for 2026-07-24 (the day this shipped):
**8 due** (6 daily + `AHLD` + `AHLE`, both biweekly) and **11 skipped** (the
other 4 biweekly sites + all 7 quarterly sites, none of them in-window yet).
Verify post-merge via a real `workflow_dispatch` run against THAT day's
actual due set (recompute `_is_due` for the actual dispatch date, don't
assume all 14 new sites baseline at once) — expect the 5 original daily
sites + `AHLI`'s Documents backfill to process as usual, the due
Submissions-only sites to baseline silently (zero alerts — first sighting
never alerts), and the rest to log a skip with no Sheet write. See the PR /
session log for the actual run and its counts.
