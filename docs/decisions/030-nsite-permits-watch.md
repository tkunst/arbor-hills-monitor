# ADR 030 — Stream O: nSITE Permits watch

Date: 2026-08-22
Status: accepted
Builds on: ADR 029 (Stream N, the Evaluations watch — the near-exact template
this copies, per the handoff's explicit stale-framing guard that the merged
Evaluations code wins over the handoff where the two disagree), ADR 028/023
(the failure-handling layers and budget-degradation guard both inherit),
ADR 020/021 (the Submissions watch + tiered polling, whose `_is_due` this
reuses and whose ref-number-keyed diff design this profile also needs),
ADR 022 (the shared `nsite_sites` registry), ADR 017 (Stream H, the ROP
watch — this profile's targeted overlap)

## Context

ADR 029 built the Evaluations watch; the handoff for this build named
**Permits** as the next of the six originally-staged unpolled nSITE profiles
(the 5th; only Active Public Notices remains after this). A permit's
lifecycle (issued -> extended -> expiring -> terminated) is a status signal
about a facility's regulatory standing, broader than Stream H's targeted ROP
watch, which follows only the Air Renewable Operating Permit renewal process
(and only its public-comment window) for N2688/N1504/P1488. This profile
covers every permit type on file, across all 19 `nsite_sites`.

**Dependency check (per the handoff):** confirmed both `coder:nsite-
violations-watch` (PR #36) and `coder:nsite-compliance-actions-watch` are
merged on `main` before this build started — `grep -rl NsiteStructuralError .`
returned `nsite_client.py`, `nsite_compliance_actions_watcher.py`,
`nsite_evaluations_watcher.py`, `nsite_violations_watcher.py`, and their test
files (non-empty). `main` was clean at `a0403cb`, no open PRs, no competing
branch for this goal.

## The feasibility gate (and what it decided)

A genuine gate, not a formality — every one of the 19 `nsite_sites` was
live-fetched at build time (2026-08-22), re-confirming the handoff's
2026-07-24 sample against the real API before any code was written.

### Endpoint and shape

`https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/default-mode/profiles/2-environmental-interests/1-permits`

Query shape, `Referer` header, and `queryResults` envelope are identical to
the five sibling fetches. Anonymous, no auth. The endpoint sits under
`2-environmental-interests`, the same top-level namespace as Submissions
(`2-submissions`) — this profile is `1-permits`.

The response shape is **stable**: every record across every site that has
any carries exactly the same seven fields — one distinct key-set across all
22 live records (confirmed via a live double-check across N2688/N1504/
P1488/RA/WRD/P1504/SANL), no optional fields, no nesting, and independently
confirmed against the response's own `lookups.metadata` field descriptions
("Permit Number", "Category", "Type", "Status", "Effective Date",
"Expiration Date", "Termination Date"). No pagination observed
(`hasResultsRemaining` null on every site).

| srn | records | notable |
|---|---:|---|
| N2688 | 9 | incl. Air ROP `ROP0000224` ("Extended") |
| N1504 | 4 | incl. Air ROP `ROP0000656` ("Extended") |
| P1488 | 3 | incl. Air ROP `ROP0000236` ("Extended") |
| RA | 2 | both "In Effect" (NPDES water) |
| WRD | 2 | both "In Effect" (Resources/wetland) |
| P1504 | 1 | already "Terminated" |
| SANL | 1 | already "Expired" (since 1996) |
| (12 other registry sites) | 0 | — |

The seven fields, renamed to short readable names in `_normalize_permit`:
`prmt_num` (`prmtPrmtNum`), `status` (`prmtRefPrmtStatDescr` — "Extended",
"In Effect", "Terminated", "Expired" observed live), `category`
(`prmtRefPrmtCatgDescr`), `permit_type` (`prmtPrmtTypeDescr` — null on 18/22
live records), `effective_date`, `expiration_date`, `termination_date` (null
until terminated — confirmed populated on N2688's `19-17B`/`179-13` and
N1504's `913-90B`, all "Terminated").

### Finding 1 — `prmtPrmtNum` IS a safe key → ref-number-keyed, NOT a multiset

The handoff named uniqueness of `prmtPrmtNum` as the thing to confirm, with a
ref-number-keyed diff (the Submissions/Evaluations idiom) as the preferred
design if it held and a full-record multiset (the Violations/CA idiom) as the
fallback if it didn't.

**It holds, everywhere.** N2688: 9/9 distinct. N1504: 4/4. P1488: 3/3. RA:
2/2. WRD: 2/2. P1504: 1/1. SANL: 1/1. No two DIFFERENT permits were found
sharing a `prmtPrmtNum` anywhere in the live sample — this is the third
profile (after Submissions and Evaluations) to earn the keyed design, and the
second (after Evaluations) where the handoff's own gate criterion was the
deciding factor rather than an assumption.

**Decided: a ref-number-keyed snapshot/diff**, mirroring `nsite_evaluations_
watcher`. A brand-new `prmt_num` means a NEW PERMIT; an existing `prmt_num`
with a changed field means that permit's status/detail is advancing.

### Finding 2 — the actual signal is status/date changes on an EXISTING permit

The handoff was explicit and adversarial-review-tested on this point: a naive
"new numbers only" diff would MISS the real event this watch exists to
surface — a status flip (Extended -> Expired) or a termination date
populating on a permit nSITE already knows about. The ref-number-keyed design
handles this for free (the same mechanism `nsite_evaluations_watcher` uses
for a "detail advancing" case), but two things were verified explicitly
rather than assumed:

1. **A synthetic status flip is tested directly** —
   `test_a_status_flip_on_an_existing_permit_reads_as_changed` diffs
   `status: Extended -> Expired` on the same `prmt_num` and asserts the
   `~ CHANGED` line names both the key and the transition.
2. **The null -> populated `termination_date` case is tested directly, not
   inferred from a generic "any field change" test** —
   `test_a_termination_date_populating_on_an_existing_permit_reads_as_changed`
   diffs a permit from `In Effect`/`termination_date=""` to
   `Terminated`/`termination_date="2026-08-20"` and asserts BOTH fields show
   in the changed-detail line. Every field is read with `or ""` in
   `_normalize_permit` (not `.get(f, "")`), so a present-but-null
   `termination_date` and an absent one normalize identically — a null ->
   value transition is therefore a genuine value inequality in the diff, not
   a special case that needs its own code path.

### Finding 3 — no volatile field

A double-fetch of N2688 (9 records, seconds apart) found **zero** fields that
changed between identical fetches — no volatile field to exclude, same
finding as every prior enforcement/inspection profile.

### Finding 4 — the ROP overlap is real, not hypothetical (adversarial review, confirmed live)

The handoff flagged, as a show-stopper-ish concern to mitigate rather than
just note: `ROP0000224`/`ROP0000656`/`ROP0000236` — Stream H's three watched
ROP permits — are ALSO present in this broader Permits profile. **Confirmed
live 2026-08-22**, not merely asserted from the handoff: all three appear at
their respective sites, all three currently show `status="Extended"`. This
is a genuine overlap in permit NUMBER, but the two streams watch DIFFERENT
EVENTS:

- Stream H (`rop_client.py`/`rop_watcher.py`) trip-wires the permit
  **ENTERING its 30-day public-comment window** — detected via a statewide
  `ROP_Public_Notice.pdf` mention or a new file in the N2688 renewal folder,
  NOT via this Permits profile at all.
- This watch trip-wires the permit's **STATUS/LIFECYCLE** (e.g. `status`
  advancing, `termination_date` populating) via nSITE's own structured
  Permits record.

**Decided: do NOT suppress ROP permits from this profile's diff** — that
would blind the watch to a real status change on exactly the three permits
most likely to have one. Instead, disambiguate explicitly in the alert copy:
`format_change_body` carries an explicit paragraph naming Stream H, the ROP
watch, and stating plainly that seeing both alerts on the same permit number
does not mean one duplicates the other. Two dedicated tests
(`test_format_change_body_disambiguates_from_the_rop_watch` and the general
essentials test) pin this text down so a future edit can't silently drop it.

## Decisions

### 1. Diffed field set: all seven, minus `prmt_num`'s exclude-ability

`prmt_num`, `status`, `category`, `permit_type`, `effective_date`,
`expiration_date`, `termination_date`. Three canonicalizations, reusing
`nsite_client._parse_egle_date` (added for Evaluations) rather than
re-duplicating the inline date-parsing block Violations/Compliance Actions
each still carry — this is the third caller of that helper and the first
needing it three times in one normalizer, which is exactly the "needed
twice-or-more" threshold that justified factoring it out in the first place.

`nsite_permits.exclude_fields` is inherited for parity, but `prmt_num` can
**never** be excluded via that lever — `diff_fields()` discards it from any
configured exclude set before applying it, matching Evaluations' `eval_num`
guard.

### 2. Status/date changes on an existing key ARE the primary signal, tested explicitly

Covered above (Finding 2). This is the one place this build diverges from a
pure copy-paste of Evaluations: two named tests exist specifically because
the handoff called out this exact scenario as the profile's reason for
existing, not an incidental case a generic "field changed" test happens to
cover.

### 3. No severity judgment, but an explicit ROP-overlap disambiguation

There is a status vocabulary here (unlike Evaluations), but it is a
multi-state lifecycle ("Extended", "In Effect", "Terminated", "Expired"), not
a good/bad binary — the watch alerts on any change and lets a human read what
it means, same posture as Violations/Compliance Actions. The one addition
beyond that posture is the ROP-overlap paragraph (Finding 4), which exists
because this profile — uniquely among the nSITE watches built so far —
genuinely shares reference numbers with a DIFFERENT, already-live alerting
stream in this same repo.

### 4. Budget-degradation guard: inherited structure, verified-inert at this volume

Kept for structural parity with every sibling (a reviewer who knows one nSITE
watcher's code can read this one), but at Permits volumes (max 9 records at
N2688) it is verified-inert insurance, like Compliance Actions'/Violations' —
NOT a live necessity the way it is for Evaluations' N2688 (477 records, which
runs permanently digest-degraded). `test_real_n2688_scale_never_triggers_
the_degradation_guard` pins this down with the REAL N2688 record count so a
future volume increase would be caught by seeing this test start failing
without anyone reasoning about why the file is only "kind of" needed.

### 5. Per-site tiers, assigned from Permits-specific status/recency — NOT copied from any sibling

The handoff's own suggestion ("N2688 can be daily if you want the fastest
signal on its ROP0000224") named only one candidate for daily; the live data
justifies going further. **3 daily / 2 biweekly / 14 quarterly** — a
distribution not shared by any sibling (Submissions 6/6/7; Violations 3/3/13;
Compliance Actions 2/4/13; Evaluations 1/4/14):

**daily (3)** — every site whose OWN permit is currently mid-renewal-lifecycle:

| srn | why |
|---|---|
| N2688 | 9 permits, incl. Air ROP `ROP0000224` — currently "Extended", and the fastest-moving of the three ROP sites by record count. |
| N1504 | 4 permits, incl. Air ROP `ROP0000656` — also currently "Extended". |
| P1488 | 3 permits, incl. Air ROP `ROP0000236` — also currently "Extended", plus a live ROP renewal in public comment (Stream H). |

Unlike every prior profile, all three ROP-holding sites are daily HERE —
justified by THIS profile's own data (all three permits share the identical
"Extended" status, the exact lifecycle stage this watch is built to trip-wire
a change on), not copied from Violations' or any sibling's own daily set.

**biweekly (2)** — real, currently-active permits, but none mid-renewal:

| srn | why |
|---|---|
| RA | 2 permits, both "In Effect" (NPDES water) — real content, no pending status transition. |
| WRD | 2 permits, both "In Effect" (Resources/wetland) — same open-JPA-plus-PFOS override every sibling profile applies to this site. |

**quarterly (14)** — a single already-settled permit, or zero permits ever:

| srn | why |
|---|---|
| P1504 | 1 permit, already "Terminated" — settled, no live transition pending. |
| SANL | 1 permit, already "Expired" since 1996 — the stalest real record in this profile. |
| AHLI | 0 permits on file. UNLIKE Violations/Compliance Actions (which bump AHLI to biweekly as a duplicate-registration mis-filing risk), a PERMIT is issued to a specific registered entity and is not the kind of record prone to mis-filing under a bare duplicate registration — same per-profile judgment call ADR 029 made for AHLI on Evaluations. |
| `AHLA`–`AHLE`, `ADL1`, `ADL2`, `COMP`, `AHE1`, `AHE2`, `ERNGX` | Zero permits ever, dormant or duplicate registrations. Polled at all only as typo/mis-filing insurance (ADR 021's rationale). |

`test_shipped_config_tiers_cover_every_registry_site_and_differ_from_
siblings` pins the full 19-site coverage and pairwise inequality against
Submissions/Violations/Compliance Actions/Evaluations.

### 6. Error contract: raise, don't swallow — but DOES filter, like Evaluations

`fetch_site_permits` reuses `NsiteFetchError`/`NsiteStructuralError` and
**raises** after 3 attempts, exactly like its five siblings. The watcher
diffs the returned list, so a failure swallowed to `[]` would read as "every
permit withdrawn at once".

Like `fetch_site_evaluations` (and UNLIKE `fetch_site_violations`/
`fetch_site_compliance_actions`), `fetch_site_permits` DOES filter — on
`prmtPrmtNum` present — because `prmt_num` is this profile's genuine diff key
and a keyless record cannot be placed in a ref-number-keyed snapshot. No such
record has been observed live across 22 real records; if nSITE ever serves
one, it is silently excluded rather than raising, matching the Evaluations/
Submissions precedent.

All of ADR 023/028/029's other hardening carries over verbatim and is
re-tested here:

- Per-site `try` covers the Sheets write too, so one bad site can't abort the
  run.
- The tab read is **batched and RAISES**; a read failure aborts before any
  write.
- A change recorded but not emailed exits non-zero; `alerting_is_configured()`
  aborts up front when delivery is already impossible.
- The paging signal (`hasResultsRemaining`) raises `NsiteStructuralError`.
- `_duplicate_key_count` checks BOTH snapshots (whichever form) for a
  duplicate `prmt_num` BEFORE any by-key dict is built — the same guard
  ADR 029's Step 5 review added for Evaluations, applied here from the start
  rather than discovered in review.

### 7. `_is_due` is imported, not reimplemented

Imported from `nsite_submissions_watcher` (generic over `(cadence, srn,
today)`), with an identity test (`pw._is_due is sub_w._is_due`) so a future
copy-paste drift fails the suite.

## Risks and mitigations

**Show-stopper if unmitigated: a reader could mistake this watch's alerts for
duplicates of Stream H's ROP watch, undermining trust in both.** Mitigated
by the explicit disambiguation paragraph in `format_change_body` (Finding 4),
pinned down by a dedicated test so it can't silently regress.

**Show-stopper if unmitigated: `prmtPrmtNum` uniqueness is verified live, not
guaranteed by the API** — both prior enforcement profiles' own gates found
their own candidate keys NOT unique in production. **Mitigated:**
`_duplicate_key_count` (present from the start, not added in a later review
round as it was for Evaluations) checks both snapshots before any by-key
dict is built; regression-tested on both the full and truncated paths.

**Manageable: watching only new permit numbers would miss the real signal.**
Mitigated by the ref-keyed diff reporting every changed field on an existing
key, with two named tests pinning the specific status-flip and termination-
date scenarios the handoff called out.

**Manageable: null `termination_date` -> populated must read as a change,
not just string inequality on a present value.** Mitigated by `or ""`
normalization treating an absent and a null value identically, so any
transition away from "" is a genuine diff.

**Residual (accepted, same as every stream here): a persistent fetch failure
after baseline goes skip-and-warn quiet.** Loud (exit 1) before baseline, so
an activation-time block surfaces.

**Residual (accepted, same as Evaluations/CA/Violations): the alert subject
can print the SRN twice** for `nsite_sites` names that embed their own SRN —
exact parity with the live siblings.

## Activation (Trisha's step, not the build's)

Ships **`nsite_permits.enabled: false`** — a brand-new poller against a live
external system, built unattended (overnight-coder Step 3). Confirmed
against the live `config.yml` at build time (it did not exist there before
this change), not assumed from the handoff.

The workflow file was landed directly into `.github/workflows/`, not parked:
this build session's SSH key authenticated non-interactively against GitHub
(`ssh -T git@github.com` returned a clean greeting with no passphrase
prompt), so the `workflow` OAuth-scope blocker that forced Streams L/M's
workflow files through `docs/pending-workflows/` did not apply here — same as
Stream N. `test_the_workflow_is_scheduled_directly_not_parked` still
tolerates a future re-park, matching the enforcing-test pattern ADR 023/028
established.

To go live: set `nsite_permits.enabled: true` and commit. No new secret is
required — the workflow reuses the same `GSHEET_ID`/SMTP secrets, no
Drive/OAuth dependency.

**Predicted first enabled run:** every due site records a `baseline` row and
**zero emails** are sent. Which sites are due depends on the date via
`_is_due` (N2688/N1504/P1488, the three daily sites, always; biweekly/
quarterly only inside their staggered 3-day windows). Alerts begin only on
the next run in which something changes — plausibly a status advance on one
of the three "Extended" ROP permits, or a change Stream H's own watch also
happens to notice from its side, at which point the disambiguation copy is
what a reader needs.

Recipients start as **Trisha only** (`nsite_permits.recipients`), the
established precedent for a brand-new alert stream.

## Consequences

- The monitor now watches **six** nSITE profiles per site — Documents,
  Submissions, Violations, Compliance Actions, Evaluations, and Permits —
  each with its own cadence map over the one shared `nsite_sites` registry,
  and still exactly two distinct diff idioms (ref-number-keyed for
  Submissions/Evaluations/Permits, full-record multiset for Violations/
  Compliance Actions) depending on whether the underlying profile actually
  carries a unique key.
- **One** of the six profiles ADR 020/021 flagged remains unpolled: Active
  Public Notices. Per the handoff, this last one is flagged as "very sparse
  data + a real ROP-overlap DESIGN decision — likely a draft-PR-for-review,
  not an autonomous merge" — a genuinely different build shape than the
  straight-copy pattern this build and its five predecessors followed.
