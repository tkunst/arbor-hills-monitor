# ADR 028 — Stream M: nSITE Compliance Actions watch

Date: 2026-08-09
Status: accepted
Builds on: ADR 023 (Stream L, the Violations watch — the near-exact template
this copies), ADR 020/021 (the Submissions watch + tiered polling, whose
`_is_due` this reuses), ADR 022 (the shared `nsite_sites` registry)

## Context

ADR 023 built the Violations watch — EGLE's record that it *found* the facility
out of compliance — and closed by naming **Compliance Actions** as the natural
next profile: the record of what EGLE *did* about it (Violation Notices, Consent
Orders, Consent Judgments). It is the documented other half of the enforcement
story, and the tightest conceptual sibling to the already-live Violations watch:
five of N2688's violation records already sit at "Active – Compliance Action
Taken", and this watch gives visibility into whether those actions resolve,
escalate, or multiply.

It is also the narrowest of the six unpolled profiles by record count (N2688 39,
RA 10), which is why it was chosen next.

## The feasibility gate (and what it decided)

A genuine gate, not a formality — the 5 non-dormant sites were live-fetched on
2026-08-08 and the result **chose the diff design**.

### Endpoint and shape

`https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/default-mode/profiles/3-compliance/3-compliance-actions`

Query shape, `Referer` header, and `queryResults` envelope are identical to
`fetch_site_submissions` / `fetch_site_violations`. Anonymous, no auth.

The response shape is **stable**: every record across every site that has any
carries exactly the same six fields — one distinct field-set per site, no
optional fields, no nesting. No pagination (`totalCount` and `hasResultsRemaining`
present but null).

| srn | records | date range | non-`Closed` |
|---|---:|---|---:|
| N2688 | 39 | 2014-12-15 → **2026-07-15** | 5 |
| RA | 10 | 2012-10-17 → 2024-11-06 | 2 (incl. open PFOS VN-011821) |
| N1504 | 2 | 2025-03-06 → 2025-04-09 | 0 (both Closed) |
| P1488, WRD | 0 | — | — |
| (14 other registry sites) | 0 (assumed, matching Violations) | — | — |

The six fields, renamed to short readable names in `_normalize_compliance_action`:
`num` (`cmplActnCmplActnNum`), `type`, `status`, `action_date`, `category`,
`program`.

### Finding 1 — `cmplActnCmplActnNum` is NOT a safe key → multiset

The handoff named uniqueness of `cmplActnCmplActnNum` as the thing to confirm,
with a ref-number-keyed diff as the *preferred* design if it held and a
full-record multiset as the fallback if it didn't.

**It does not hold.** N2688 files the federal case number `5:21-cv-12098-S` on
**two** records — a single Administrative Consent Order "Entered" on two dates
(2021-12-15 and 2022-08-22). A ref-number-keyed diff (Submissions' idiom) would
silently drop one of them. RA (10/10) and N1504 (2/2) are unique, but N2688 —
the most important site — is not, so the number cannot be the key.

A subtlety worth recording honestly, because it shapes how strong the claim is:
unlike Violations (where RA's 299 records collapsed to 108 distinct tuples, 191
collisions), **every full-field tuple here is distinct today** (39/39, 10/10,
2/2). A plain `set` would lose nothing at present. The `collections.Counter`
MULTISET is chosen anyway, for three reasons:

1. It is strictly safe if EGLE ever files a byte-identical duplicate action (a
   count going 1→2 is a real event that must alert) — a `set` would silently
   swallow it.
2. It is the proven rop/mmd/ride/violations idiom.
3. It keeps this module **line-for-line** with the already-reviewed Violations
   watch, so the diff/snapshot code carries zero new risk and the review is a
   comparison, not a fresh read.

**Decided: full-record `Counter` multiset diff**, exactly as ADR 023. Not a
composite `(num, date)` key — that is the "forcing a key" the handoff warns
against, and `action_date` can itself move.

The cost of no key is that a changed record shows as its old shape REMOVED plus
its new shape ADDED (no "updated" verb). Mitigated the same way as Violations —
**every diffed field is printed on both lines** — with one CA-specific
adjustment (Decision 2).

### Finding 2 — the snapshot fits a Sheets cell with enormous margin

Unlike Violations (where RA's 299 records blew 2.6× past the 50,000-char cell
cap and *forced* the run-length counted encoding), Compliance-Actions volumes
are tiny. N2688 (the largest, 39 records) serializes to **~9,200 chars** as one
JSON object per record and **~4,800 chars** run-length counted — both far under
the cap.

**Decided: keep ADR 023's run-length counted encoding and `_cell_payload`
truncation guard verbatim anyway.** Two honest, separate justifications:

- The **counted encoding** is justified independently of the cap: the snapshot
  structure *is* the Counter the diff needs, so nothing is reconstructed twice.
- The **truncation guard / digest fallback / final clamp** is inherited
  **insurance against a bulk EGLE re-import** — it never fires at CA volumes.
  This is stated plainly rather than dressed up as a live concern (it was live
  for RA's 299 violations; it is not here). Keeping it costs a few dozen lines
  and buys cross-watcher consistency + a trivial review; a bespoke smaller
  version would be *more* net-new code to review, not less.

`snapshot_hash` is always computed over the **full** snapshot, never the
truncated payload — otherwise editing the budget would silently re-baseline
every site.

### Finding 3 — no volatile field, and no free text

Two fetches seconds apart were byte-identical at N2688 and RA (which proves
nothing about editorial churn over months, and is not dressed up as if it did).
More usefully: **all six fields are controlled vocabularies or a reference
number** — there is no free-text field like Violations' `comments`. So the
comment-churn residual that ADR 023 had to accept **does not exist here**. The
`exclude_fields` rollback lever is still inherited for parity, but it ships
pointing at nothing and the ADR does not claim a known churn risk.

## Decisions

### 1. Diffed field set: all six

`num`, `type`, `status`, `action_date`, `category`, `program`. One
canonicalization, matching `_normalize_violation`, exists to stop a
*representation* change from firing a false alert:

- `action_date` → a bare ISO date. The raw value carries a UTC offset (`-04:00`
  EDT, `-05:00` EST); the calendar date is the signal, so the EDT→EST flip each
  fall must not read as a change. (Verified: the real `5:21-cv-12098-S` records
  carry both offsets.)

Every field is read with `or ""`, not `.get(f, "")`, for parity — a
present-but-null key normalizes to the same `""` an absent one would. No CRLF
collapsing is needed (no free-text field).

### 2. ADDED/REMOVED lines lead with the action NUMBER, not the category

This is the one substantive divergence from a verbatim copy of Violations, and
it matters. Violations leads its lines with `category` (the rule cited — the most
identifying thing about a violation). The Compliance-Actions `category` field is
a bare `"Administrative"` / `"Civil"` — useless as an identifier. Because the
multiset has no "updated" verb, a status change on a known action renders as
REMOVED + ADDED, and the **number is the only thing that makes those two lines
legible as the same action**:

```text
- REMOVED  VN-019436 (type=Violation Notice, status=Issued, action_date=2026-07-15, …)
+ ADDED    VN-019436 (type=Violation Notice, status=Closed, action_date=2026-07-15, …)
```

`_headline_field` returns `num` (read from the snapshot's own field list, so
excluding `num` degrades to the next field rather than printing a bare em-dash).
This is exactly the handoff's "make sure the diff captures a status change on an
existing CA."

### 3. No severity or urgency judgment — a trip-wire

The observed status vocabulary is a multi-state lifecycle, not a good/bad binary:

| status | count (across N2688/RA/N1504) |
|---|---:|
| Closed | 44 |
| Entered | 3 |
| Issued | 3 |
| Terminated | 1 |

Deciding which is "bad" would be the monitor asserting a legal conclusion. It
alerts that something *moved*. The one exception is emphasis, not judgment: a
site going from zero actions to some gets its own headline (`FIRST COMPLIANCE
ACTION(S) RECORDED`), and the reverse likewise.

### 4. Per-site tiers, assigned from observed CA data — NOT copied from Violations

The handoff was explicit these must not be a copy, and the data bears it out.
Violations is **3 daily / 3 biweekly / 13 quarterly**; Compliance Actions is
**2 / 4 / 13**. The substantive difference is **N1504**:

**daily (2)** — real CA history *and* open/recent enforcement:

| srn | why |
|---|---|
| N2688 | 39 CAs, most recent a Violation Notice **ISSUED 2026-07-15** (open, last month), plus a federal Consent Judgment / Consent Order. The most active enforcement record here. |
| RA | 10 CAs including the **open PFOS VN-011821** (2021, still "Issued"). Set daily on the same harm-asymmetry argument ADR 023 used for RA's violations — WRD-NPDES, 30-day comment windows, one HTTP request/day — even though its newest action is 2024-11. |

**biweekly (4)** — real-but-closed history, or a live facility where a first CA
is plausible:

| srn | why |
|---|---|
| N1504 | 2 CAs, both 2025 and **both Closed** — real history but no open enforcement. This is the genuine data-driven difference: N1504 is *daily* for Violations (whose 3 records are open/unresolved), but its compliance actions are settled, so a *new* action (not a status advance) is the only plausible event, and biweekly with the 3-day window catches that. |
| WRD | 0 CAs, but an open JPA + PFOS interest at the Land & Water Interface site. |
| P1488 | 0 CAs, but a live ROP renewal in public comment (Emerald RNG). |
| AHLI | 0 CAs; bare duplicate registration for the same physical landfill as N2688 — the case most likely to receive a mis-filed action. |

**quarterly (13)** — `P1504`, `AHLA`–`AHLE`, `ADL1`, `ADL2`, `COMP`, `AHE1`,
`AHE2`, `SANL`, `ERNGX`. Zero compliance actions ever, dormant or duplicate
registrations. Polled at all only as typo/mis-filing insurance (ADR 021's
rationale).

### 5. Error contract: raise, don't swallow — and isolate per site

`fetch_site_compliance_actions` reuses `NsiteFetchError` / `NsiteStructuralError`
and **raises** after 3 attempts, exactly like `fetch_site_violations`. The
watcher diffs the returned list, so a failure swallowed to `[]` would read as
"every compliance action closed at once". All of ADR 023's hardening carries
over verbatim and is re-tested here:

- No record filter (the number isn't a unique key, so any filter would drop real
  records); a non-dict element surfaces as a loud `NsiteFetchError`.
- Per-site `try` covers the Sheets write too, so one bad site can't abort the run.
- The tab read is **batched and RAISES**; a read failure aborts before any write
  (a swallowed `[]` would spuriously re-baseline every site on this append-only,
  last-write-wins tab).
- A change recorded but not emailed exits non-zero; `alerting_is_configured()`
  aborts up front when delivery is already impossible, so a known-undeliverable
  change is deferred, not consumed.
- The paging signal (`hasResultsRemaining`) raises `NsiteStructuralError`, a
  subclass `run()` treats as loud (a shape change is not transient).

### 6. `_is_due` is imported, not reimplemented

Imported from `nsite_submissions_watcher` (generic over `(cadence, srn, today)`),
with an identity test (`caw._is_due is sub_w._is_due`) so a future copy-paste
drift fails the suite. Deliberately not moved to a shared module — that would
edit a live `enabled: true` stream's file for this new stream's benefit.

## Risks and mitigations

**Residual (accepted): comment/free-text churn — N/A here.** Unlike Violations,
there is no free-text field, so the biggest Violations residual simply does not
apply. The `exclude_fields` lever exists for parity but points at nothing.

**Manageable: a changed action reads as REMOVED + ADDED.** Inherent to a keyless
multiset diff. Mitigated by leading with the action number and printing every
field (Decision 2).

**Manageable: a wholesale re-import could emit many alert lines.** Capped at
`MAX_ALERT_LINES` (200) with the remainder explicitly stated; the durable row is
always complete.

**Residual (accepted, same as every stream here): a persistent fetch failure
after baseline goes skip-and-warn quiet.** Loud (exit 1) before baseline, so an
activation-time block surfaces.

**Cosmetic, not fixed:** the alert subject can print the SRN twice for the five
`nsite_sites` names that embed their own SRN — exact parity with the live
Submissions/Violations watches; fixing it would mean editing a live stream.

**Observed at build time, not fixed (out of scope):** the live `config.yml`
comment above `nsite_violations.enabled` still reads "Ships OFF" even though
Trisha set `enabled: true` after PR #36 merged. Noted here for the record; it is
the Violations block, not this stream's surface.

## Activation (Trisha's step, not the build's)

Ships **`nsite_compliance_actions.enabled: false`** — a brand-new poller against
a live external system, built unattended (overnight-coder Step 3). Confirmed
against the live `config.yml`, not the handoff.

To go live, **two** steps, in order:

1. **Move the workflow file into place** — it is parked at
   `docs/pending-workflows/nsite-compliance-actions-watch.yml`:

   ```bash
   git mv docs/pending-workflows/nsite-compliance-actions-watch.yml .github/workflows/
   ```

   The build session had no credential carrying the `workflow` OAuth scope, so
   that one file could not be pushed; everything else pushed normally. Harmless
   while `enabled` is `false`, but **flipping the flag without moving the file
   gives a watch that is never scheduled and fails silently.** A test enforces
   the ordering (`test_the_parked_workflow_must_be_in_place_before_the_stream_is_
   enabled` asserts `enabled is False OR the workflow is in .github/workflows/`),
   so it survives activation rather than being the first line deleted.

2. **Set `nsite_compliance_actions.enabled: true`** and commit.

No new secret is required — the workflow reuses the same `GSHEET_ID` / SMTP /
`GDRIVE_SA_KEY` secrets, no Drive/OAuth dependency.

**Predicted first enabled run:** every due site records a `baseline` row and
**zero emails** are sent. Which sites are due depends on the date via `_is_due`
(the 2 daily sites always; biweekly/quarterly only inside their staggered 3-day
windows). Alerts begin only on the next run in which something changes.

Recipients start as **Trisha only** (`nsite_compliance_actions.recipients`), the
Meeting/MMD/RIDE/Submissions/Violations precedent for a brand-new alert stream.

## Consequences

- The monitor now watches **four** nSITE profiles per site — Documents, Submissions,
  Violations, and Compliance Actions — each with its own cadence map over the one
  shared `nsite_sites` registry.
- **Three** of the six profiles ADR 020/021 flagged remain unpolled at the
  time this ADR was written: Complaints, Permits, Evaluations, Active Public
  Notices. The `nsite-six-unpolled-profiles-schemas` memory carries confirmed
  endpoints for the rest.
- **Best next pick: Evaluations** — since built, see ADR 029. Every violation
  record carries an `evalEvalNum` and every compliance action responds to
  one, so Evaluations is the shared parent both enforcement profiles hang
  off — the record of EGLE's inspections themselves. It closes the
  inspection → finding → action chain this monitor now watches the back
  two-thirds of. Complaints (citizen-reported) is the other high-value
  candidate but has a different shape (it is intake, closer to Submissions)
  and would be a fresh feasibility gate rather than a near-copy.
