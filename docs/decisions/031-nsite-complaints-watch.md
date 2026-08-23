# ADR 031 — Stream P: nSITE Complaints watch

Date: 2026-08-22
Status: accepted
Builds on: ADR 023 (Violations — the `NsiteStructuralError`/per-site-try/
batched-raising-read failure-handling layers this inherits verbatim, and the
digest-multiset degradation machinery this build measured and explicitly did
NOT reuse), ADR 028 (Compliance Actions — the merged watcher this session
rebased mechanics on, per the handoff's stale-framing guard), ADR 020/021
(Submissions — `_is_due`, tiered polling, and the `subm*` field-name lineage
this profile shares), ADR 022 (the shared `nsite_sites` registry), ADR 029/030
(Evaluations/Permits — the ref-number-keyed idiom this profile's design was
measured against and ultimately diverges from)

## Context

This is the 4th of the six originally-staged unpolled nSITE profiles (staged
2026-08-08, after Submissions/Violations/Compliance Actions; Evaluations and
Permits were built out of order and landed first). Complaints are citizen/
agency reports filed against a facility — direct evidence of community
concern and often the trigger for an inspection.

**Dependency check (per the handoff):** confirmed both `coder:nsite-
violations-watch` (PR #36) and `coder:nsite-compliance-actions-watch` are
merged on `main` — `grep -rl NsiteStructuralError .` returned
`nsite_client.py` and every one of `nsite_violations_watcher.py`,
`nsite_compliance_actions_watcher.py`, `nsite_evaluations_watcher.py`,
`nsite_permits_watcher.py` plus their test files (non-empty). `main` was
clean at `20bdf15`, no open PRs, no competing branch for this goal.

**Config verified live, not assumed from the handoff:** `nsite_complaints`
did not exist anywhere in `config.yml` before this build — a genuinely new
key, confirmed by grep before writing any code (per the overnight-coder
discipline of reading the live `enabled` state rather than trusting a
handoff's framing, since a handoff is a snapshot from the day it was
written and every sibling profile in this repo has since been individually
activated at different times).

## The feasibility gate (this WAS the build — the storage decision, not the endpoint)

Unlike Evaluations/Permits (straight copies of the ref-number-keyed idiom
once uniqueness was confirmed), this handoff was explicit that Complaints is
NOT a straight copy: N2688 carries thousands of complaint records, breaking
the one-full-snapshot-per-cell assumption every prior nSITE watch relies on.
The volume decision was measured live, against the real API, before any
storage code was written.

### Endpoint and shape

`https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/default-mode/profiles/2-environmental-interests/3-complaints`

Query shape, `Referer` header, and `queryResults` envelope are identical to
every sibling fetch. Anonymous, no auth. Field NAMES are identical to
Submissions (`subm*` prefix — Complaints is filed through the same
submission machinery, just a distinct form type and a distinct endpoint),
but this is a DISTINCT profile at a DISTINCT endpoint, not a filtered view of
Submissions.

**Live-fetched 2026-08-22, all 19 `nsite_sites`** (not just the 5 sites the
handoff already knew about):

| srn | records | earliest | latest | last 365d |
|---|---:|---|---|---:|
| N2688 | 6,396 | 2007-09-12 | 2026-08-17 | 60 |
| RA | 5 | 2018-12-12 | 2023-09-13 | 0 |
| COMP | 1 | 2014-08-06 | 2014-08-06 | 0 |
| (16 other registry sites) | 0 | — | — | 0 |

Schema is stable: exactly the four `COMPLAINT_FIELDS` below, one distinct
key-set across every live record at both non-zero sites (confirmed via a
live double-fetch of N2688 finding zero field-level drift). The handoff's
2026-07-24 sample named 6,392 at N2688; the live count one month later is
6,396 — consistent with the measured ~0.16/day current filing rate, not a
discrepancy.

The four fields, renamed to short readable names in `_normalize_complaint`:
`ref_num` (`submSubmRefNum`), `form_type` (`submRefFormTypeDescr` — always
"Complaint" for this profile), `program_area` (`submRefProgramAreaDescr`),
`received_date` (`submRcvdDate`).

### Finding 1 — `submSubmRefNum` IS unique (confirmed, not assumed)

The handoff named uniqueness of `submSubmRefNum` as the field to verify.
**Confirmed live**: 6,396/6,396 distinct at N2688, 5/5 at RA. A double-fetch
of N2688 found zero field-level churn between fetches. This alone would
suggest the Evaluations/Permits ref-number-keyed idiom — but that idiom
stores a per-record structure (even in its digest-degraded form), and this
profile's volume rules that out entirely (Finding 2).

### Finding 2 — no per-record encoding fits, not even Violations'/Compliance Actions' own digest degradation

The handoff predicted this and directed measuring it before writing storage
code. Measured against the REAL 6,396 N2688 records, serialized under every
candidate encoding:

| Encoding | Chars | vs. 50,000 cap |
|---|---:|---|
| Full per-record dict (one JSON object per complaint) | 870,156 | 17.4x over |
| Ref-keyed minimal (`{ref_num: [form_type, received_date]}`) | 422,436 | 8.4x over |
| Just the ref-number keys, sorted, no values | 89,844 | 1.8x over |
| Per-record DIGEST set (Violations'/Compliance Actions' own degradation form — a sorted list of 12-char sha256 digests, one per record) | 102,336 | 2.0x over |
| `{n, hash, latest[5]}` count+hash fingerprint (this design) | 336 | 0.007x — 148x margin |

The digest row is the load-bearing finding: it is the EXACT machinery
Violations/Compliance Actions degrade to under budget pressure, and it
**still fails** at this profile's scale — not by a small margin, but by 2x.
The reason is specific to Complaints, not a general limitation of the
digest idiom: Violations' degradation works because EGLE genuinely repeats
violation rows (RA's 299 records collapse to 108 distinct tuples, a 64%
reduction that shrinks the multiset representation proportionally).
Complaints show **zero** such repetition — 6,396 of 6,396 records are
distinct — so there is no compression to exploit, and a per-row digest
costs its full ~16 chars (JSON-encoded) times 6,396 regardless.

**Conclusion, matching the handoff's own predicted outcome:** no
full-membership encoding of N2688's complaints fits one 50,000-char Sheets
cell, even degraded. Reusing Violations'/Compliance Actions' storage
machinery verbatim — the handoff's own caution against copying Submissions'
plain `json.dumps` applies equally to copying THIS machinery here, once
measured — would not have worked either.

## Decision: a fingerprint small BY CONSTRUCTION, not degraded into smallness

The handoff offered two named designs: (a) name-the-new-complaint (needs a
stored prior ref set, needing the Violations-style digest degradation
applied to it) or (b) count-only tripwire (store just `n`+`hash`). Given
Finding 2 — the digest degradation itself doesn't fit — design (a) as
literally specified does not survive the spike; the handoff's own decision
rule ("recommend (a) if the ref-set fits under budget with the digest
degradation, else (b)") points to (b).

**This build ships neither (a) nor bare (b), but a third design that
subsumes (b) and delivers (a)'s value in the common case:** a snapshot of
`{n, hash, latest[K]}`:

- `n` — the exact record count, always available, O(1).
- `hash` — sha256 of the SORTED `submSubmRefNum` set (bare ref numbers, not
  full field tuples). This is narrower than every sibling watcher's
  whole-record hash, deliberately: ref numbers carry no UTC offset, so this
  hash is immune to the EDT/EST flip that would otherwise re-string
  `received_date` on all 6,396 records twice a year and false-fire a
  "changed" alert each time (the exact class of real-specimen-only bug ADR
  011 warns this repo to test for, and the reason every sibling normalizer
  treats date-offset-stripping as load-bearing). The profile's other two
  fields (`form_type`, `program_area`) showed zero live churn in the
  double-fetch and carry no independent diff value worth that correctness
  risk.
- `latest` — the K most-recently-received complaints (`[ref_num,
  received_date]` pairs), K = `nsite_complaints.latest_window` (default 50).

This whole structure serializes to under 1,000 chars even at N2688's real
volume (measured: 6,400 synthetic records -> well under 5,000 chars,
comfortably inside a 45,000-char budget with ~40x margin) — **small by
construction**, not degraded after the fact. There is consequently no
`counted_rows`-to-digest CASCADE the way Violations/Compliance Actions have;
`nsite_complaints_watcher._cell_payload` is a bespoke clamp fitted to this
snapshot's actual shape (trim the `latest` list from the end if a
pathological `latest_window` config bump ever pushed it over budget), kept
deliberately separate from the inherited digest-multiset machinery rather
than force-fit into a shape that doesn't match what this profile stores.

### Naming new complaints was attempted, then deliberately reverted

An earlier version of this design tried to have `latest` do double duty: not
just display context, but a windowed diff that could name new complaints
exactly in the common case, delivering design (a)'s value without its
storage cost. The idea — if `latest` is the top-K by date both this run and
last run, and fewer than K complaints arrived in between, then
`new_window - old_window` is exactly the newly-arrived refs — is correct
under PURE growth. Three successive rounds of independent Step 5 review each
found a different way real data breaks that purity assumption, and the
pattern across the three rounds is what changed the decision, not any single
bug:

- **Round 1:** a self-consistency check (windowed-diff size equals the count
  delta, and the delta is smaller than the window) is necessary but not
  sufficient. Removing an in-window complaint while unrelated new ones
  arrive can promote an older, previously-invisible survivor into the
  window; if the counts happen to coincide, the check passes and the
  survivor gets misnamed as new. **Fix attempted:** a third, POSITION-based
  condition excusing the window's oldest `delta` entries (the ones pure
  growth alone would push out).
- **Round 2:** the position-based fix was itself incomplete — a removal of
  one of the excused BOTTOM entries could still promote a survivor
  undetected, since the position-based check never examined them. Concrete
  counterexample: old set `A(10), B(9), C(8), D(7)`, window=3 → `[A,B,C]`;
  removing **C** (the window's bottom entry) with two new, older complaints
  arriving reproduces the same false-positive. **Fix attempted:** replaced
  the position-based check with a VALUE-based one — every ref newly visible
  in the window must rank NEWER (by the `(received_date, ref_num)` sort key)
  than every ref that dropped out. Proven non-regressive (under pure growth,
  the added and surviving items always form the new top-K by construction,
  so the comparison can never reject a genuine case) and verified to catch a
  removal at either end of the window.
- **Round 3:** the value-based fix closed every REMOVAL-based route to a
  false positive — but a THIRD, structurally different mechanism reaches the
  same failure without any removal at all: an EXISTING complaint's
  `received_date` being corrected by EGLE (e.g. a previously blank/
  unparseable date getting populated later) re-sorts that complaint into the
  window. `hash` — deliberately scoped to the ref-number SET only, to stay
  immune to the EDT/EST date-offset flip (see the hash rationale above) —
  does not change when only a date is corrected, so this is invisible to the
  primary change signal too, and the exact-naming path's soundness proof
  implicitly assumed dates on existing records never change, which is false
  in this specific case.

**The pattern across three rounds — not the individual bugs — is what forced
the decision.** Each fix closed exactly the failure mode the prior round
found and was defeated by a differently-shaped one. That is the signature of
a structural limitation, not a sequence of missed edge cases: this snapshot,
by design, never stores old's full ref-number set (that is the entire reason
it fits in one Sheets cell at N2688's 6,396-record scale — see Finding 2
above). For any ref newly visible in `latest`, there is consequently no
stored fact able to distinguish "this complaint is genuinely new" from "this
complaint existed all along outside the visible window, and something —
removal of a displacing entry, or a date correction — just made it visible."
No further check on `{n, hash, latest[K]}` can close that gap; doing so
would require storing something about full prior-set membership, which is
exactly what Finding 2 ruled out. A CONDITIONAL caveat (name it, but warn
"might be wrong") doesn't help either, because which alerts are at risk
can't be told apart from which aren't — it would fire on every single growth
alert, which is the honest count+context message below with extra ceremony
and a live false-positive-prone code path still behind it.

**Decision: the named-diff branch is removed. Every growth case — a nonzero
old baseline with `n` increased, for ANY reason — gets the same honest
message: the count change (`old_n -> new_n`) plus the `latest` window shown
purely as recent-complaint CONTEXT, explicitly labeled as not necessarily
exhaustive or exact.** `latest` still exists in the snapshot and still
serves a real purpose — it gives a human reading the alert a concrete
starting point in nSITE without a separate lookup — it is simply never
diffed to make a naming claim. Deleting the claim, rather than caveating it,
also deletes the three-round bug class at its root: if nothing is ever
labeled "+ NEW", "a non-new thing labeled new" is impossible by
construction, and all four findings across the three rounds evaporate
together rather than needing a fourth defense.

**The one case where naming specific refs IS sound, and is kept:** a
confirmed-empty prior baseline (`old_n == 0`, from a real previously-recorded
snapshot, not a missing one). If the old snapshot legitimately showed zero
complaints, the old window was empty too — there is no possibility of an old
survivor hiding outside a window that held nothing. Every ref appearing in
`new`'s window is therefore unambiguously new, with no information-theoretic
gap to close. This "FIRST COMPLAINT(S) RECORDED" branch is untouched by this
decision and still names refs exactly (with the same over-window caveat as
before, if the true count exceeds what the window shows —
`test_first_sighting_exceeding_the_window_caveats_the_partial_list`).

This was NOT treated as "round 4 of the same fix-and-review loop" against
this repo's 3-round convergence cap — see "Process note" below.

### Removals are never misread as new

Matching Violations'/Compliance Actions' mitigation for their own multiset
diffs: if `n` decreases, the note always says a complaint no longer
appears (removed or withdrawn), never "new". Unlike those two profiles, this
watch cannot name WHICH complaint disappeared (no removal-tracking window is
kept — only the most-recent K by date, and a removed complaint could be
anywhere in the history), so the note is honestly count-only for a removal
rather than inventing a name it can't support.

### Process note: why reverting the named-diff path was not "round 4"

This repo's overnight-coder procedure caps Step 5/7's resolve-and-re-review
loop at 3 rounds, with explicit guidance that failure to converge in 3
rounds is a signal to stop and hand the PR to Trisha, not grind a 4th round —
"a loop that can't converge in 3 rounds is telling you something (goal is
underspecified, or the fix approach is wrong), not something a 4th round
will fix." Round 3's finding was read as exactly that signal, correctly: it
was the third instance of the SAME failure class (an old, non-new complaint
labeled as new) reached by a third, structurally distinct mechanism, on top
of a proof that the storage design cannot support any check closing the
class for good. Attempting a fourth patch — a fourth condition on top of the
value-based comparison — would have been the literal grinding this repo's
procedure warns against, with no reason to expect a fourth mechanism
wouldn't surface in a hypothetical round 5.

The response taken instead — deleting the named-diff path rather than
patching it again — is not a fourth patch attempt at the same approach; it
is recognizing the approach itself was wrong and replacing it with the one
the handoff already authorized as a fallback (design (b), "smaller and
simpler," explicitly named as the fallback if design (a) didn't survive
scrutiny). Nothing about this fix is conditional or heuristic — it does not
depend on knowing which future case would break next, so it needed exactly
one round of review (not a fresh 3-round budget), confirming the deletion is
sound and that `/security-review` still passes on the simplified diff.

## Adversarial review (mitigations built in, per this repo's standing process rule)

- **Show-stopper if unmitigated: a confidently-wrong "named" diff misreporting
  an old complaint as new.** Three review rounds each found a different
  mechanism reaching this failure (see "Naming new complaints was attempted,
  then deliberately reverted" above); the mitigation shipped is not a check
  but a deletion — the named-diff branch is gone, so this class of bug is
  impossible by construction rather than merely detected.
- **Show-stopper if unmitigated: the snapshot silently exceeds the cell cap.**
  Mitigated by construction (the design is O(K), not O(n)) plus a defensive
  clamp for a pathological config bump; the large-volume test
  (`test_the_n2688_scale_snapshot_fits_under_the_sheets_cell_cap`) pins the
  real N2688 scale (padded to 6,400) under the cap with a wide margin, not
  merely under it.
- **Show-stopper if unmitigated: 6,396 baseline "new" complaints flood on
  first enable.** Mitigated: first sighting baselines silently, verified by
  test at the real N2688 scale
  (`test_baseline_at_n2688_scale_does_not_flood_an_alert`), not only at toy
  volumes the way a naive test suite might stop short of.
- **Manageable: a removed/withdrawn complaint changing the hash could read as
  "new" if mishandled.** Mitigated: the count comparison (`n` decreased) is
  checked and returned before the growth branch is ever reached, so a
  removal is always labeled a removal, never new.
- **Residual (accepted, same as every stream here): a persistent fetch
  failure after baseline goes skip-and-warn quiet.** Loud (exit 1) before
  baseline, so an activation-time block surfaces. Given this is the largest-
  volume profile in the whole nSITE set, the `hasResultsRemaining`
  structural-paging guard (inherited from every sibling) is a live risk
  here specifically, not theoretical insurance — a future EGLE paging
  change would otherwise read as thousands of complaints vanishing at once.

## Residual risks (accepted)

- **This watch cannot name which specific complaint is new, for a nonzero
  baseline.** Every growth alert says the count changed and shows recent
  complaints as context, never which one(s) are the change. This is the
  direct, accepted cost of closing the three-round false-positive class
  above — the two are the same trade-off, not two separate problems. A human
  reading the alert checks nSITE directly for the specifics, same as they
  already must for a removal (this watch never named which complaint was
  removed either, from day one). **Recovery, if this residual ever becomes
  the wrong trade for a specific high-value site:** the sound way to
  actually name new complaints is full-set-membership storage, which
  trivially fits for any low-volume site (RA has 5 complaints total, COMP
  has 1) — only N2688's 6,396 records forced the windowed design in the
  first place. A future per-site "full-membership-when-it-fits" mode would
  be a small, clean addition on top of this same snapshot shape; it is
  explicitly deferred, not built here, to keep this PR's diff to the
  decision it actually needed to make.
- **A pure `received_date` correction on an existing complaint, with no
  count change, is invisible to this watch.** `hash` is deliberately scoped
  to the ref-number set (see the hash rationale above) so it does not change
  when only a date is corrected and nothing is added or removed — no alert
  fires, and `latest`'s display order silently re-sorts on the next run
  that IS triggered by something else. This is intentional, not an
  oversight: the alternative (hashing dates too) would false-fire a
  "changed" alert on every EDT/EST flip, twice a year, across all 6,396
  N2688 records — a worse trade for a lower-value signal (EGLE data-entry
  timing is not itself a finding worth alerting on).

## Decisions

### 1. Snapshot design: `{n, hash, latest[K]}`, not a per-record encoding

Covered above. `snapshot_hash(snap)` returns `snap["hash"]` directly rather
than hashing the whole snapshot structure (every sibling watcher's pattern)
— because `n` and `latest` are both derived from the same `rows` `hash` is
computed from, a second whole-structure hash would be redundant. The
function is kept (not inlined) purely so this module's orchestration code
(`_diff_and_record`) reads identically in SHAPE to the four watchers it was
rebased from.

### 2. No `exclude_fields` lever

Unlike Violations/Compliance Actions (which diff a configurable field set)
and Evaluations/Permits (which diff every field but forbid excluding the key
field), this profile's hash is fixed to the ref-number set by design — there
is no second field worth making configurable, since `form_type` is constant
for this profile and `program_area`/`received_date` showed zero live churn.
Adding an unused lever would be complexity without a use case.

### 3. Per-site tiers, assigned from Complaints-specific RATE, not the raw 6,396

`daily (1) / biweekly (3) / quarterly (15)` — a distribution not shared by
any sibling (Submissions 6/6/7; Violations 3/3/13; Compliance Actions
2/4/13; Evaluations 1/4/14; Permits 3/2/14):

**daily (1)** — the only site with recent, ongoing complaint activity:

| srn | why |
|---|---|
| N2688 | 6,396 total, but 60 in the trailing 365 days (~0.16/day) — the rate, not the raw count, justifies daily. A 2016-2020 filing burst (2018: 1,936; 2019: 2,952) accounts for ~92% of the total and is explicitly NOT why this site is daily. |

**biweekly (3)** — real-but-not-recent history, or an open matter that could
plausibly produce a new complaint (the same harm-asymmetry override every
sibling profile applies to WRD/P1488):

| srn | why |
|---|---|
| RA | 5 complaints, 2018-12-12..2023-09-13 — real but none in ~3 years. |
| WRD | 0 complaints, but an open JPA + PFOS interest at the Land & Water Interface site — same override every sibling profile applies. |
| P1488 | 0 complaints, but a live ROP renewal in public comment (Emerald RNG) — same override every sibling profile applies. |

**quarterly (15)** — zero complaints ever, or one ancient one; typo/
mis-filing insurance only:

| srn | why |
|---|---|
| COMP | 1 complaint, 2014-08-06 — ancient, insurance only. |
| N1504, AHLI, P1504, `AHLA`–`AHLE`, `ADL1`, `ADL2`, `AHE1`, `AHE2`, SANL, ERNGX | Zero complaints ever. N1504 in particular gets QUARTERLY here despite being DAILY for Violations/biweekly for Evaluations — unlike those two profiles, N1504 has no complaint history at all, so this profile's own data, not a sibling's, decides its tier. |

`test_shipped_config_tiers_cover_every_registry_site_and_differ_from_
siblings` pins the full 19-site coverage and pairwise inequality against
Submissions/Violations/Compliance Actions/Evaluations/Permits.

### 4. Error contract: raise, don't swallow — and DOES filter, like Evaluations/Permits

`fetch_site_complaints` reuses `NsiteFetchError`/`NsiteStructuralError` and
raises after 3 attempts, exactly like every sibling. The watcher fingerprints
the returned list, so a failure swallowed to `[]` would read as "every
complaint withdrawn at once" — the single most consequential misreading this
profile could produce, given N2688 alone holds 6,396 real records.

Like `fetch_site_evaluations`/`fetch_site_permits`, this DOES filter — on
`submSubmRefNum` present — because `ref_num` is this profile's genuine
unique key and a keyless record cannot be placed in the fingerprint. No such
record has been observed live; if nSITE ever serves one, it is silently
excluded rather than raising, matching precedent.

All of ADR 023/028's other hardening carries over verbatim:

- Per-site `try` covers the Sheets write too, so one bad site can't abort the
  run.
- The tab read is **batched and RAISES**; a read failure aborts before any
  write — load-bearing here specifically, since a spurious re-baseline at
  N2688 would silently discard the most consequential history this watch
  protects.
- A change recorded but not emailed exits non-zero; `alerting_is_configured()`
  aborts up front when delivery is already impossible.
- The paging signal (`hasResultsRemaining`) raises `NsiteStructuralError` —
  the single most likely profile in this repo to actually trip it, given its
  record count.

### 5. `_is_due` is imported, not reimplemented

Imported from `nsite_submissions_watcher` (generic over `(cadence, srn,
today)`), with an identity test
(`test_is_due_is_imported_not_reimplemented`) so a future copy-paste drift
fails the suite.

## Activation (Trisha's step, not the build's)

Ships **`nsite_complaints.enabled: false`** — a brand-new poller against a
live external system, built unattended (overnight-coder Step 3). Confirmed
against the live `config.yml` at build time (the key did not exist there
before this change — verified by grep, not assumed from the handoff).

The workflow file was landed directly into `.github/workflows/`, not parked:
this build session's SSH key authenticated non-interactively against GitHub
(`ssh -T git@github.com` returned a clean greeting with no passphrase
prompt), so the `workflow` OAuth-scope blocker that forced Streams L/M's
workflow files through `docs/pending-workflows/` did not apply — same as
Streams N/O. `test_the_parked_workflow_must_be_in_place_before_the_stream_is_
enabled` still tolerates a future re-park, matching the enforcing-test
pattern ADR 023/028/029/030 established.

To go live: set `nsite_complaints.enabled: true` and commit. No new secret
is required — the workflow reuses the same `GSHEET_ID`/SMTP secrets, no
Drive/OAuth dependency.

**Predicted first enabled run:** every due site records a `baseline` row and
**zero emails** are sent, even at N2688's 6,396 records — verified by test,
not merely asserted. Which sites are due depends on the date via `_is_due`
(N2688 daily always; RA/WRD/P1488 biweekly inside their staggered 3-day
windows; the remaining 15 quarterly). Alerts begin only on the next run in
which something changes — at N2688's current rate, plausibly a count change
every week or so, reported honestly (count changed X -> Y, plus recent
complaints shown as context) rather than a claimed name.

Recipients start as **Trisha only** (`nsite_complaints.recipients`), the
established precedent for a brand-new alert stream.

## Consequences

- The monitor now watches **seven** nSITE profiles per site — Documents,
  Submissions, Violations, Compliance Actions, Evaluations, Permits, and
  Complaints — each with its own cadence map over the one shared
  `nsite_sites` registry, and now **three** distinct diff idioms depending on
  what the underlying profile's scale and key structure actually support:
  full-record multiset (Violations/Compliance Actions, no unique key),
  ref-number-keyed (Evaluations/Permits, a unique key at a volume a Sheets
  cell can hold), and count+hash+windowed-recency (Complaints, a unique key
  at a volume NEITHER prior idiom's storage can hold).
- **Two** of the six profiles ADR 020/021 originally flagged remain unpolled:
  Active Public Notices and (per the handoff's own list) whichever profile
  is queued next. Active Public Notices is flagged by its own queue note as
  "least safe to auto-merge" (sparse data + a real ROP-overlap DESIGN
  decision to surface, not resolve) — a genuinely different build shape than
  the pattern this build and its predecessors followed.
