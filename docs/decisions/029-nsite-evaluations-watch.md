# ADR 029 — Stream N: nSITE Evaluations watch

Date: 2026-08-22
Status: accepted
Builds on: ADR 028 (Stream M, the Compliance Actions watch — the near-exact
template this copies, per the handoff's explicit stale-framing guard that the
merged CA code wins over the handoff where the two disagree), ADR 023 (Stream
L, the Violations watch — the fetch-error contract and failure-handling
layers both inherit), ADR 020/021 (the Submissions watch + tiered polling,
whose `_is_due` this reuses AND whose ref-number-keyed diff design this
profile turns out to need instead of Violations/CA's multiset), ADR 022 (the
shared `nsite_sites` registry)

## Context

ADR 028 built the Compliance Actions watch and named **Evaluations** as the
natural next profile: the underlying INSPECTION record a violation or
compliance action often stems from. A Violations record already carries
`evalEvalNum` (`VIOLATION_FIELDS`' `eval_num`), so a violation can be joined
back to the evaluation that found it; watching Evaluations gives visibility
into new inspections — the event that often PRECEDES a violation or
compliance action — rather than only their downstream consequences. It is
the cleanest and lowest-risk of the four profiles staged in the
`nsite-six-unpolled-profiles-schemas` batch (Evaluations, Complaints,
Permits, Active Public Notices): a stable reference number and moderate
volume, hence first.

## The feasibility gate (and what it decided)

A genuine gate, not a formality — all 19 `nsite_sites` were live-fetched at
build time (2026-08-22, re-confirming the handoff's 2026-07-24/2026-08-08
sample) and the result **chose the diff design** — differently from both
prior profiles.

### Endpoint and shape

`https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/default-mode/profiles/3-compliance/1-evaluations`

Query shape, `Referer` header, and `queryResults` envelope are identical to
`fetch_site_submissions` / `fetch_site_violations` / `fetch_site_compliance_
actions`. Anonymous, no auth.

The response shape is **stable**: every record across every site that has
any carries exactly the same eight fields — one distinct key-set per site
(verified via a live double-check against RA/WRD/N1504/N2688/P1488), no
optional fields, no nesting. No pagination observed (`hasResultsRemaining`
null on every site).

| srn | records | earliest | latest |
|---|---:|---|---|
| N2688 | 477 | 2007-09-13 | **2026-08-07** (2 weeks old at build time) |
| RA | 40 | 1989-09-27 | 2024-10-17 |
| N1504 | 5 | 2025-03-06 | 2025-08-27 |
| P1488 | 2 | 2025-06-01 | 2025-06-01 |
| WRD | 1 | 2022-03-18 | 2022-03-18 |
| (14 other registry sites) | 0 | — | — |

The eight fields, renamed to short readable names in `_normalize_evaluation`:
`eval_num` (`evalEvalNum`), `program_area`, `eval_type`, `eval_category`,
`permit_num` (cross-references the Permits profile; null on 470/477 at
N2688), `start_date`, `sample_transmit_date` (null on **all 525** live
records — the schema promises it but no evaluation has populated it yet),
`site_name`.

### Finding 1 — `evalEvalNum` IS a safe key → ref-number-keyed, NOT a multiset

The handoff named uniqueness of `evalEvalNum` as the thing to confirm, with a
ref-number-keyed diff (the Submissions idiom) as the preferred design if it
held and a full-record multiset (the Violations/CA idiom) as the fallback if
it didn't.

**It holds, everywhere.** N2688: 477/477 distinct. RA: 40/40. N1504: 5/5.
WRD: 1/1. Unlike Violations (RA's 299 records collapsed to 108 distinct
tuples) and Compliance Actions (N2688 files one federal case number on two
records), no two DIFFERENT evaluations were found sharing an `evalEvalNum`
anywhere in the live sample.

**Decided: a ref-number-keyed snapshot/diff**, the `nsite_submissions_
watcher` idiom — the first of the three enforcement/inspection profiles
built so far to use it instead of the rop/mmd/ride/violations/compliance-
actions `Counter` multiset. A brand-new `eval_num` means a NEW EVALUATION (a
new inspection — the highest-value event this watch exists to surface); an
existing `eval_num` with a changed field means that evaluation's detail
advanced (e.g. `sample_transmit_date` being filled in after the fact, or a
program/category correction).

### Finding 2 — the snapshot does NOT fit a Sheets cell, even in compact form

This is where Evaluations diverges sharply from both Violations and
Compliance Actions, and it is the reason the budget-degradation guard is
load-bearing here rather than inherited insurance:

| encoding | N2688 (477 records) |
|---|---:|
| one JSON object per record (the plain Submissions idiom) | 134,020 chars |
| positional (`fields` header + `[eval_num, *values]` rows) | **75,494 chars** |
| per-`eval_num` digest map (`[eval_num, digest]` pairs) | 15,560 chars |

Even the compact positional form is **1.5× over** the 45,000-char default
budget and **1.5× over** the hard 50,000-char Sheets cell cap itself — there
is no budget value that lets N2688's full evaluation detail fit in one cell.
**N2688 will run in degraded (digest) mode on every real run from day one.**
This is fundamentally different from Violations/Compliance Actions, where the
truncation guard is verified-inert insurance against a re-import that has
never happened.

**Decided: keep `[eval_num, digest]` pairs in the degraded form, not an
anonymous digest multiset.** This is the one genuine design departure from a
straight copy of ADR 023/028's `_cell_payload`. Because `eval_num` is a real
key (not hashed away, unlike Violations/CA's positional-tuple digests), the
degraded form can still name **exactly which evaluation is new, changed, or
removed** — `summarize_evaluations_change`'s truncated branch reports
`+ NEW EVALUATION E-...` by ref number even with zero field-level detail
surviving. A bare count-only degraded diff (Violations/CA's fallback) would
have been a real quality regression for the site this watch exists for, so
this extra ~30 lines of code over a verbatim copy is direct product value,
not gold-plating.

`snapshot_hash` is always computed over the **full** snapshot, never the
truncated payload — otherwise editing the budget would silently re-baseline
every site (same rule as ADR 023/028).

### Finding 3 — no volatile field, and no status field

A double-fetch of N2688 (477 records, seconds apart) found **zero** fields
that changed between identical fetches — no volatile field to exclude, same
finding as Compliance Actions. And, as the handoff anticipated, **no field in
this profile resembles a status** (unlike Violations/CA's multi-state
lifecycle vocabulary): the eight fields are program/type/category
descriptors, two dates, a permit cross-reference, and a site-name echo. The
primary signal really is a new `evalEvalNum` appearing, exactly as the
handoff predicted — there is no "FIRST EVALUATION(S) RECORDED" banner the way
Violations/CA emphasize a zero→some transition, because the ref-keyed design
already gives every new evaluation its own explicit line regardless of
whether the site had zero or hundreds before (the Submissions precedent, not
the Violations/CA one).

## Decisions

### 1. Diffed field set: all eight, minus `eval_num`'s exclude-ability

`eval_num`, `program_area`, `eval_type`, `eval_category`, `permit_num`,
`start_date`, `sample_transmit_date`, `site_name`. One canonicalization,
matching `_normalize_violation`/`_normalize_compliance_action`, stops a
*representation* change from firing a false alert: `start_date` and
`sample_transmit_date` → a bare ISO date (the raw value carries a UTC offset
that must not flip the hash each EDT/EST transition).

`nsite_evaluations.exclude_fields` is inherited for parity with Violations/
CA, but with one structural difference documented in code and config: `eval_
num` can **never** be excluded via that lever, because unlike a display
headline it is this profile's diff KEY — `diff_fields()` discards it from any
configured exclude set before applying it.

### 2. The truncated-diff branch names eval_nums, not just counts

Covered above (Finding 2) — this is the substantive divergence from a
verbatim ADR 028 copy. `_digest_map` reads a stored snapshot's `eval_num`
column by field NAME (`fields.index("eval_num")`), not position, so a
hand-reordered Sheet cell still parses.

### 3. No severity or status judgment — a pure additions-and-changes trip-wire

There is no status vocabulary to judge (Finding 3). The watch alerts on a new
evaluation or a changed detail and lets a human read what it means in
MiEnviro.

### 4. Per-site tiers, assigned from Evaluations-specific data — NOT copied from any sibling

The handoff was explicit these must not be a copy, and the data is the most
lopsided of the four profiles so far: **1 daily / 4 biweekly / 14
quarterly** (Submissions: 6/6/7; Violations: 3/3/13; Compliance Actions:
2/4/13).

**daily (1)** — the only site with recent, ongoing evaluation activity:

| srn | why |
|---|---|
| N2688 | 477 evaluations, most recent **2026-08-07** — two weeks old at build time. Genuinely ongoing inspection activity, unlike any other site in this profile. |

**biweekly (4)** — real evaluation history that isn't recent enough alone to
justify daily, or an open matter that could plausibly produce a new one:

| srn | why |
|---|---|
| RA | 40 evaluations, but the newest is 2024-10-17 — nearly 2 years stale at build time. Substantial volume, not recent enough for daily on this profile's own merits (unlike Violations/CA, where RA's *open PFOS action* justifies daily). |
| N1504 | 5 evaluations, newest 2025-08-27 — moderate, about a year old. |
| P1488 | 2 evaluations (2025-06-01) — plus a live ROP renewal in public comment. |
| WRD | 1 evaluation, and it is from **2022** — the stalest real record in the whole profile. Bumped up from what its own count/recency would suggest, on the same override Violations/CA apply: an open JPA + PFOS interest at the Land & Water Interface site that could plausibly produce a new inspection at any time. |

**quarterly (14)** — `AHLI`, `P1504`, `AHLA`–`AHLE`, `ADL1`, `ADL2`, `COMP`,
`AHE1`, `AHE2`, `SANL`, `ERNGX`. Zero evaluations ever, dormant or duplicate
registrations. Polled at all only as typo/mis-filing insurance (ADR 021's
rationale). Note `AHLI` moves from *daily* (Submissions)/*biweekly*
(Violations/CA) to *quarterly* here — it has zero evaluations on file, and
none of the other profiles' activity at that srn is evaluation activity.

### 5. Error contract: raise, don't swallow — but DOES filter, unlike Violations/CA

`fetch_site_evaluations` reuses `NsiteFetchError` / `NsiteStructuralError` and
**raises** after 3 attempts, exactly like its three siblings. The watcher
diffs the returned list, so a failure swallowed to `[]` would read as "every
evaluation withdrawn at once".

UNLIKE `fetch_site_violations`/`fetch_site_compliance_actions` (which keep
every record because they have no key and any filter would drop real
enforcement records), `fetch_site_evaluations` DOES filter — on `evalEvalNum`
present, the same filter `fetch_site_submissions` applies to `submSubmRefNum`
— because `eval_num` is this profile's genuine diff key and a keyless record
cannot be placed in a ref-number-keyed snapshot. No such record has been
observed live across 525 real records; if nSITE ever serves one, it is
silently excluded rather than raising, matching the Submissions precedent.

All of ADR 023/028's other hardening carries over verbatim and is re-tested
here:

- Per-site `try` covers the Sheets write too, so one bad site can't abort the
  run.
- The tab read is **batched and RAISES**; a read failure aborts before any
  write (a swallowed `[]` would spuriously re-baseline every site on this
  append-only, last-write-wins tab).
- A change recorded but not emailed exits non-zero; `alerting_is_configured()`
  aborts up front when delivery is already impossible, so a known-
  undeliverable change is deferred, not consumed.
- The paging signal (`hasResultsRemaining`) raises `NsiteStructuralError`, a
  subclass `run()` treats as loud (a shape change is not transient).

### 6. `_is_due` is imported, not reimplemented

Imported from `nsite_submissions_watcher` (generic over `(cadence, srn,
today)`), with an identity test (`ew._is_due is sub_w._is_due`) so a future
copy-paste drift fails the suite.

## Risks and mitigations

**Show-stopper if unmitigated: `evalEvalNum` uniqueness is verified live, not
guaranteed by the API — and both prior enforcement profiles' feasibility
gates found their own candidate keys NOT unique in production
(`cmplActnCmplActnNum` collides at N2688; no Violations field/composite is
unique at all).** A naive by-key dict diff (`_rows_by_key`/`_digest_map`)
silently collapses same-key rows, which would misreport a genuinely NEW
evaluation sharing an existing `eval_num` as a bland "changed" line on the
existing one — understating what happened, behind a green build, forever.
**Mitigated:** `_duplicate_key_count` checks both snapshots (in whichever
form — full or digest-degraded, since N2688 runs the latter permanently) for
a duplicate `eval_num` BEFORE any by-key dict is built, and
`summarize_evaluations_change` surfaces a distinct, loud "eval_num was NOT
unique" note + re-baseline rather than proceeding to a diff it can no longer
trust. Caught in review before merge; regression-tested on both the full and
truncated paths.

**Manageable: N2688 runs permanently degraded, so its alert body never shows
field-level detail on a changed evaluation.** Mitigated by the digest form
still naming the exact `eval_num` (Finding 2) — a human can look it up in
MiEnviro directly. A field-level diff at N2688 would require either raising
the Sheets budget beyond what the API allows (not possible) or moving the
snapshot off Sheets entirely (out of scope for this build; noted as a
possible future improvement, not built speculatively here).

**Manageable: a changed evaluation at a non-degraded site reads with full
field detail, but degraded sites show only the ref number.** This is an
honest reflection of what the two encodings can support, not a bug; both
paths are tested.

**Residual (accepted, same as every stream here): a persistent fetch failure
after baseline goes skip-and-warn quiet.** Loud (exit 1) before baseline, so
an activation-time block surfaces.

**Residual (accepted, same as Violations/CA): the alert subject can print the
SRN twice** for `nsite_sites` names that embed their own SRN — exact parity
with the live siblings; fixing it would mean editing a live stream's shared
label-formatting convention for this new stream's benefit.

## Activation (Trisha's step, not the build's)

Ships **`nsite_evaluations.enabled: false`** — a brand-new poller against a
live external system, built unattended (overnight-coder Step 3). Confirmed
against the live `config.yml` at build time (it did not exist there before
this change), not assumed from the handoff.

The workflow file was landed directly into `.github/workflows/`, not parked:
this build session's SSH key authenticated non-interactively against GitHub
(`ssh -T git@github.com` returned a clean greeting with no passphrase
prompt), so the `workflow` OAuth-scope blocker that forced Streams L/M's
workflow files through `docs/pending-workflows/` did not apply here (see
overnight-coder.md Step 4). `test_the_workflow_is_scheduled_directly_not_
parked` still tolerates a future re-park, matching the enforcing-test pattern
ADR 023/028 established, in case a later SSH-key rotation reintroduces the
blocker for some other stream.

To go live: set `nsite_evaluations.enabled: true` and commit. No new secret
is required — the workflow reuses the same `GSHEET_ID` / SMTP secrets, no
Drive/OAuth dependency.

**Predicted first enabled run:** every due site records a `baseline` row and
**zero emails** are sent. Which sites are due depends on the date via
`_is_due` (N2688, the one daily site, always; biweekly/quarterly only inside
their staggered 3-day windows). Alerts begin only on the next run in which
something changes — plausibly soon at N2688, whose evaluations run through
2026-08-07 and which is evaluated at the highest cadence in this profile.

Recipients start as **Trisha only** (`nsite_evaluations.recipients`), the
established precedent for a brand-new alert stream.

## Consequences

- The monitor now watches **five** nSITE profiles per site — Documents,
  Submissions, Violations, Compliance Actions, and Evaluations — each with
  its own cadence map over the one shared `nsite_sites` registry, and now
  TWO distinct diff idioms (ref-number-keyed for Submissions/Evaluations,
  full-record multiset for Violations/Compliance Actions) depending on
  whether the underlying profile actually carries a unique key.
- **Two** of the six profiles ADR 020/021 flagged remain unpolled:
  Complaints, Permits, Active Public Notices. The
  `nsite-six-unpolled-profiles-schemas` memory carries confirmed endpoints
  for the rest. Next of the four originally staged: `coder:nsite-complaints-
  watch` (flagged as NOT a straight copy — N2688's 6,392-record volume gate
  is a materially different scale than anything built so far).
