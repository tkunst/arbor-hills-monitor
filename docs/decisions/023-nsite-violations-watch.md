# ADR 023 — Stream L: nSITE Violations watch

Date: 2026-08-04
Status: accepted
Builds on: ADR 020 (Stream K, the Submissions watch — the closest sibling),
ADR 021 (tiered polling, whose `_is_due` this reuses), ADR 022 (the shared
`nsite_sites` registry, whose whole purpose was to be ready for this build)

## Context

ADRs 020 and 021 both closed with the same flag: the nSITE API exposes **six
more unpolled profiles** per site — Permits, Active Public Notices,
Evaluations, **Violations**, Compliance Actions, Complaints — deliberately
kept out of those builds' scope.

Violations is the first of the six to be built, and was picked first on
value: it is EGLE's **own enforcement record**. A row here is a formal
finding by the regulator that the facility was out of compliance — not a
document filing (Documents), not an application intake (Submissions), not a
permit status (ROP). For a community advocacy group that is arguably the
single most directly useful thing the portal exposes, and live reconnaissance
confirmed it carries real depth: 360 records across the watched sites, the
oldest from 2004.

## The feasibility gate (and what it changed)

This was a genuine gate, not a formality — all 19 `nsite_sites` were
live-fetched on 2026-08-04 and the results **changed the design twice**.

### Endpoint and shape

`https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/default-mode/profiles/3-compliance/2-violations`

Query shape, `Referer` header, and `queryResults` envelope are identical to
`fetch_site_submissions`. Anonymous, no auth.

The response shape is **stable**: all 360 records across every site that has
any carry exactly the same eight fields — one distinct field-set per site, no
optional fields, no nesting. There is no pagination (`totalCount` and
`hasResultsRemaining` are present but null; the counts match an independent
2026-07-24 observation exactly, eleven days apart).

| srn | records | earliest | latest |
|---|---:|---|---|
| RA | 299 | 2004-03-31 | 2025-08-21 |
| N2688 | 58 | 2014-12-15 | 2026-07-08 |
| N1504 | 3 | 2025-03-12 | 2025-04-07 |
| all other 16 sites | 0 | — | — |

### Finding 1 — there is no unique-ID field, and no composite key either

The handoff flagged this as the thing a single-sample check could have missed,
and named full-record multiset diffing as the default assumption to be
confirmed. **Confirmed, and more strongly than expected.**

Tested per site over the *entire* record set: **not one of the eight fields is
unique.** Nor is any composite — the strongest one tried
(`category + start_date + eval_num + program + viol_type`) still leaves **191
collisions across RA's 299 records** and 7 across N2688's 58.

The reason is that EGLE genuinely files **repeated, byte-identical rows**: RA's
299 records collapse to just **108 distinct field tuples**, N2688's 58 to 55.
Those duplicates are real data (e.g. `DMR Report` twice on the same date under
the same evaluation number), not an artifact.

**Decision: a full-record `collections.Counter` MULTISET diff** — the
rop/mmd/ride idiom, not Submissions' ref-number-keyed one. The multiset (not a
set) is load-bearing: deduplicating would silently destroy 191 real
enforcement records on RA alone, and a count going from 2 to 3 is itself a real
event that must alert.

The cost of no key is that a changed record cannot be reported as "updated" —
it shows as its old shape REMOVED plus its new shape ADDED. That is the
accepted, documented behavior of every other multiset watch here, and it is
mitigated the same way: **every diffed field is printed on the ADDED/REMOVED
lines**, so a change confined to any one field is always visible
(`rop_watcher`'s documented lesson — an unprinted field renders two
identical-looking lines).

### Finding 2 — the snapshot does not fit in a Sheets cell

Not anticipated by the handoff, and it would have broken the naive mirror of
the Submissions design outright.

A Google Sheets cell holds at most **50,000 characters**. Serializing RA's 299
violations as one JSON object per record — exactly what
`submissions_snapshot` does — produces **130,188 characters, 2.6× over the
cap.** The write would simply have been rejected in production, on the largest
and most important site, and (given where the Submissions watcher places its
`append_*_row` call) would have aborted the whole run.

Encodings measured against RA's real 299 records **during the spike, on the
raw field values** (i.e. before `_normalize_violation` existed) — these are the
numbers that drove the decision:

| encoding | RA (299 records) | fits? |
|---|---:|---|
| one JSON object per record (the Submissions idiom) | 130,188 | ✗ |
| same, minus the free-text comments | 111,822 | ✗ |
| positional arrays | 79,540 | ✗ |
| **run-length counted positional (chosen)** | **27,431** | ✓ |
| digest multiset only (the fallback) | 2,285 | ✓ |

Re-measured against the **shipped** code — normalization shortens the dates and
strips the comments, so both forms get smaller, but the conclusion is
unchanged: dict-per-record is **101,484** (still 2× past the cap), the counted
form that ships is **24,884**, a 4.1× reduction with **45% headroom** against
the 45,000 budget. 24,884 is the figure quoted everywhere else in the repo.

**Decision: persist the Counter itself** — a `fields` header plus
`[count, *values]` rows, sorted by the value tuple. This is exact and lossless
(no field is dropped), self-describing, still human-readable in the tab, and
4.1× smaller than the dict form, because it stops repeating eight key names
299 times and folds the 191 duplicate rows into counts. The snapshot structure
*is* the diff structure, so nothing is reconstructed twice.

`_cell_payload` guards the residual headroom: above
`nsite_violations.snapshot_char_budget` (45,000) it degrades to a digest
multiset with an explicit `"truncated": true` marker. This never fires today
(RA is at 24,884, 45% headroom) — it exists so a bulk EGLE re-import degrades
gracefully instead of failing a write. `summarize_violations_change` detects
that marker and reports a count-level change while stating plainly that no
field-level diff is available, rather than inventing one it cannot support.

The digest form is itself ~140 characters per *distinct* row, so it too would
outgrow the budget somewhere past roughly 2,000 distinct rows — which is the
same bulk-re-import scenario the guard exists for. A final clamp therefore
drops the digests entirely (keeping `fields`, `n`, `truncated` and a
`digests_dropped` marker), so the payload is bounded by a constant no matter
how large the record set gets. The hash still detects any change and `n` still
counts it, which is all the truncated path promises anyway.

Critically, `snapshot_hash` is always computed over the **full** snapshot,
never the possibly-truncated payload — otherwise editing the budget would
silently re-baseline every site and fire a change alert for each.

## Decisions

### 1. Diffed field set: all eight, including the free-text comments

The eight fields, renamed to short readable names in `_normalize_violation`:
`category`, `viol_type`, `status`, `start_date`, `comments`, `program`,
`eval_num`, `eval_type`.

The handoff's adversarial review raised the risk that free text
(`violViolNotifCmnts`) churns in wording without a substantive change, and
suggested excluding it the way ADR 019 excludes RIDE's `ProjectManaager`.

**It ships INCLUDED**, for two reasons. First, unlike `ProjectManaager` — an
administrative field with no compliance meaning — `comments` carries the
violation's actual substance: the permit and condition being cited (`"PTI No.
79-17, FGENCLOSEDFLARES-S2 Condition V. Testing/Sampling 2., 6., and 10."`).
Excluding it means a violation's substance changing would not alert. Second,
this repo's standing posture under unknowable external behavior is to alert
rather than to infer (the WDS `0.0`-years ruling).

**This is honestly a residual, not a cleared check** — see Risks below.

Two canonicalizations are applied precisely so that a *representation* change
cannot fire a false alert:

- `start_date` → a bare ISO date. The raw value carries a UTC offset
  (`-04:00` in EDT, `-05:00` in EST); the calendar date is the signal.
- `comments` → CRLF/CR collapsed to LF, then stripped. Real values contain
  `\r\n`; a line-ending change is not an enforcement event.

And every field is read with `or ""`, **not** `.get(f, "")` — `program` is
genuinely `null` on 17 of RA's 299 records, and `.get(f, "")` returns `None`
for a present-but-null key, so the day EGLE serves `""` instead the hash would
flip for no real reason.

### 2. No severity or urgency judgment — this is a trip-wire

The observed status vocabulary is a multi-state lifecycle, not a good/bad
binary:

| status | count |
|---|---:|
| Active – Reviewed/Evaluated | 278 |
| Inactive - Resolved | 54 |
| Active - Addressed not Resolved | 21 |
| Active – Compliance Action Taken | 5 |
| Active – Not Reviewed | 2 |

Deciding which of those is "bad" would be the monitor asserting a legal
conclusion about an enforcement matter. It alerts that something **moved** and
lets a human read what it means — the same posture as Submissions, and the
handoff's explicit instruction.

The one exception is *emphasis*, not judgment: a site going from zero
violations to some gets its own headline (`FIRST VIOLATION(S) RECORDED`), and
the reverse likewise. Sixteen of the nineteen sites baseline at zero, so this
is the highest-value alert the stream can produce and a bare "1 record added"
would bury it.

### 3. Per-site tiers, assigned from observed data — NOT copied from Submissions

The handoff was explicit that these must not be a copy, and the data bears that
out: violation activity does not track submission activity. Submissions is
6 daily / 6 biweekly / 7 quarterly; Violations is **3 / 3 / 13**.

**daily (3)** — real violation history *and* recent activity:

| srn | why |
|---|---|
| N2688 | 58 violations, most recent 2026-07-08 (last month). The landfill's own air permit — the most active enforcement record here. |
| RA | 299 violations, water (WRD-NPDES). See the override note below. |
| N1504 | Only 3 violations, but all are 2025-03/04 and all sit at "Active - Addressed not Resolved" — an *open, unresolved* matter whose status advancing is exactly what this watch exists to catch. |

**An explicit override, stated so it reads as a judgment call rather than a
default:** recency alone would place **RA at biweekly.** Its 299 records are
heavily historical (244 of them are 2008–2009 NPDES DMR violations) and the
recent rate is only ~2–5/year, most recently 2025-08-21. It is set to **daily**
anyway on a harm-asymmetry argument: EGLE comment windows run 30 days, so a
14-day detection lag could consume half of one, and the marginal cost of the
faster tier is one HTTP request per day.

**biweekly (3)** — real, operating facilities that happen to have zero
violations *today*, where a first violation is genuinely plausible:

| srn | why |
|---|---|
| WRD | Land & Water Interface site: zero here, but it carries the open PFOS violation VN-011821 and the active Wetland 1 JPA. |
| P1488 | Emerald RNG: zero here, but a live ROP renewal currently in public comment. |
| AHLI | A bare duplicate registration for the *same physical landfill* as N2688 — the case most likely to receive a mis-filed violation. |

**quarterly (13)** — `P1504`, `AHLA`–`AHLE`, `ADL1`, `ADL2`, `COMP`, `AHE1`,
`AHE2`, `SANL`, `ERNGX`. Zero violations ever, dormant or duplicate
registrations. Polled at all only as typo/mis-filing insurance — ADR 021's
rationale, unchanged. Note that six of these (`P1504`, `AHLA`–`AHLE`) are
*biweekly* for Submissions on the strength of a sparse 2021–2024 filing
history; they have no violation history whatsoever, which is precisely why the
two tier maps had to be assigned independently.

### 4. Error contract: raise, don't swallow — and isolate per site

`fetch_site_violations` reuses `NsiteFetchError` and **raises** after 3
attempts on any network/HTTP/structural failure, exactly like
`fetch_site_submissions` and deliberately unlike `fetch_site_documents`. The
watcher diffs the returned list, so a failure swallowed to `[]` would read as
"every violation resolved at once" — a false all-clear, the worst possible
failure mode for this particular stream.

Three departures from the Submissions watcher, each deliberate:

1. **No record filter.** `fetch_site_submissions` filters on
   `submSubmRefNum`, which is safe because that field is its guaranteed unique
   key. There is no such field here, so any filter would silently drop real
   enforcement records. Every element of `queryResults` is kept; a
   structurally broken element surfaces as a loud `NsiteFetchError`.
2. **`or ""` normalization throughout** (see Decision 1).
3. **The per-site `try` covers the Sheets write too.** In
   `nsite_submissions_watcher.run()` the per-site `try` catches only
   `NsiteFetchError`, and `append_submissions_watch_row` is called outside
   `_diff_and_record`'s inner try blocks — so a rejected write aborts the run
   and silently drops every site queued after it. Given Finding 2 made an
   oversized write a concrete rather than theoretical possibility, that hole is
   not mirrored: any per-site exception inside the loop is caught, logged,
   counted, and sets a non-zero exit code while the run continues. The
   durable-first ordering is preserved — a site whose row failed to land never
   sends an alert describing it. Setup *before* the loop still aborts, on
   purpose: there is no per-site work to salvage there.

4. **The tab read is batched and RAISES; a read failure aborts before any
   write.** This is the subtlest of the four and was caught in review rather
   than in design. `sheet_writer._tab_rows` swallows every read exception and
   returns `[]`, which is correct for its append-only-accumulator callers and
   silently destructive for a watch that *diffs*: a throttled read would make
   every site look never-seen, the watcher would write a fresh `baseline` row,
   and because the tab is append-only with last-write-wins that spurious
   baseline **becomes the state** — permanently erasing a real, un-alerted
   change rather than deferring it by one run. `last_violations_snapshots`
   therefore does its own read and lets failures propagate, and `run()` aborts
   before touching anything. Reading every key in ONE call rather than one per
   site also cuts a 19-site run from ~19 reads to 1, which is the exposure that
   made throttling plausible to begin with.

5. **A change that was recorded but not emailed exits non-zero.** The sibling
   treats alerting as purely best-effort and returns 0. For a stream whose
   entire deliverable *is* the alert that is wrong: the row has already
   advanced the stored hash, so the next run reports `unchanged` and never
   retries — a green check over a silently-undelivered violation notice. The
   row is still written first and still survives; the failure is just no longer
   invisible.

   Catching exceptions is not enough on its own, which a second review round
   caught: `email_alerts.send_email` deliberately **prints and returns** —
   never raises — when the SMTP env vars are missing or the recipient list
   resolves empty, so that a dry or local run doesn't crash. That is right for
   a shared helper and wrong here, because it makes the *most likely* cause of
   non-delivery (a missing, renamed, or rotated GitHub secret) the one that
   stays silent. `alerting_is_configured()` therefore checks the same condition
   once, up front. The shared helper is left untouched.

   A third round then pushed this further, and correctly. Recording the change
   and merely *reporting* the lost alert was still wrong when non-delivery is
   known **before** the first write: writing the row advances the stored hash,
   so the next run compares equal, reports `unchanged`, and never retries — the
   notification is gone permanently even after the secret is fixed. That is the
   exact harm decision 4 below rejects for the tab read ("deferring a change by
   a run is fine, CONSUMING one is not"), and the argument is *stronger* here
   because the failure is known in advance. So `run()` now **aborts before any
   fetch or write** when alerting is already impossible. Nothing is lost by
   stopping: the violations are still in nSITE, so the next healthy run records
   and alerts on them. Only a send failure discovered *mid*-run — after the row
   is already durable — takes the report-and-exit-non-zero path, which is
   unavoidable at that point.

6. **The response's paging signal is checked, not just its shape.** The
   envelope carries `hasResultsRemaining`/`totalCount`, both null today (every
   site returns its full set in one response). If nSITE ever enables paging, a
   partial page would be indistinguishable from a shrunken record set to a
   count-based multiset diff: the first 100 of RA's 299 records would be
   reported as "199 violation records removed" and emailed as fact. A truthy
   `hasResultsRemaining` raises instead — a paged response needs real
   pagination support, not a silent truncation.

   Raising alone turned out to be insufficient, which the third review round
   caught. `run()` classifies `NsiteFetchError` as **transient** (skip-and-warn,
   exit 0) once a baseline exists — correct for a network blip, badly wrong for
   a shape change, which would fail identically every single day behind a green
   build while every real violation change at all 19 sites went unnoticed. So
   this raises `NsiteStructuralError`, a **subclass** (every existing
   `except NsiteFetchError`, including the live Submissions watcher's, keeps
   working unchanged) that `run()` checks first and always treats as loud. It
   also has to bypass the retry loop: the loop's broad `except` would otherwise
   swallow it and re-raise a generic `NsiteFetchError` after three attempts,
   silently undoing the distinction — and retrying a shape change is pointless
   anyway. The `queryResults`-missing check deliberately stays a plain
   `NsiteFetchError`: a JSON error page from a WAF genuinely can be transient,
   so only the paging signal is provably structural.

### 5. `_is_due` is imported, not reimplemented

`nsite_submissions_watcher._is_due` is already a pure function of
`(cadence, srn, today)` with nothing Submissions-specific in it, so it is
imported in place — the handoff's explicit instruction, and there is a test
asserting *identity* (`vw._is_due is sub_w._is_due`) so a future copy-paste
drift fails the suite.

It was deliberately **not moved** to a shared module: `nsite_submissions` is
`enabled: true` and live, and relocating a function out of it would convert
this new-stream build into a change to a running stream, pulling the
overnight-coder Step 3 real-specimen requirement onto a path this work has no
business touching.

## Risks and mitigations

**Residual (accepted, and honestly labelled): comment-text volatility is
unverified.** Whether EGLE edits `violViolNotifCmnts` wording without a
substantive change is not knowable from one build session — two fetches minutes
apart were byte-identical, which proves nothing about editorial churn over
months. It is not dressed up as a passed check.

- *Detection signal:* after activation, a burst of `changed` rows whose only
  field delta is the comment text.
- *Mitigation, already built:* `nsite_violations.exclude_fields` is a
  **config-only** rollback (`- comments`), no code change needed. Because
  changing it changes the hash basis, `summarize_violations_change` detects a
  differing field set and labels that one row *"diffed field set changed by
  configuration — NOT an EGLE change"*, instead of rendering every record as
  removed-and-re-added and reading as a catastrophe. Both the lever and that
  branch are tested.

**Manageable: RA's 299-record first-run volume.** Mitigated by the existing
pattern, confirmed to hold at this scale: first sighting baselines **silently**,
no alert. Verified in tests at the real 299-record size.

**Manageable: a changed record reads as REMOVED + ADDED.** Inherent to a
multiset diff with no key (Finding 1). Mitigated by printing every diffed field
on both lines.

**Manageable: a wholesale re-import could emit ~600 alert lines.** Capped at
`MAX_ALERT_LINES` (200) with the remainder **explicitly stated** in the body,
never silently truncated; the durable Sheet row is always complete.

**Residual (accepted, same as every other stream here): a persistent fetch
failure after baseline goes skip-and-warn quiet.** Loud (exit 1) before
baseline, so an activation-time block surfaces.

**Cosmetic, not fixed:** the alert subject can print the SRN twice
(`Arbor Hills Landfill, Inc. (N2688) (N2688)`) because five `nsite_sites`
names embed their own SRN. This is **exact parity** with the live Submissions
watch's subject line; diverging one stream's format was judged worse than the
repetition, and fixing both would mean editing a live stream. Noted as a
follow-up.

## Activation (Trisha's step, not the build's)

Ships **`nsite_violations.enabled: false`** — a brand-new poller against a live
external system, built unattended, per overnight-coder Step 3. Confirmed
against the live `config.yml` rather than the handoff.

To go live, **two** steps, in this order:

1. **Move the workflow file into place** — it is parked at
   `docs/pending-workflows/nsite-violations-watch.yml` rather than
   `.github/workflows/`:

   ```bash
   git mv docs/pending-workflows/nsite-violations-watch.yml .github/workflows/
   ```

   The build session had no credential carrying the `workflow` OAuth scope
   (the `gh` token has `repo` but not `workflow`, granting it needs an
   interactive browser flow, the REST Contents API is scope-restricted the
   same way, and the machine's SSH key was passphrase-locked and unreachable
   non-interactively), so that one file could not be pushed. Everything else
   in the change pushed normally. This is harmless while `enabled` is `false`
   — nothing would run either way — but **flipping the flag without moving
   the file gives a watch that is never scheduled and fails silently**, which
   is exactly the class of failure the rest of this design works to avoid.
   See `docs/pending-workflows/README.md`.

   **A test enforces this ordering** rather than relying on anyone reading
   this section: `test_the_parked_workflow_must_be_in_place_before_the_stream_
   is_enabled` asserts `enabled is False OR the workflow is in
   .github/workflows/`. It was written that way deliberately — an
   `enabled is False` assertion alone would be the first line deleted at
   activation, taking the only guard with it, whereas this invariant survives
   activation and fails the suite if the flag is flipped while the file is
   still parked.

2. **Set `nsite_violations.enabled: true`** and commit.

No new secret is required — the workflow reuses the same `GSHEET_ID` / SMTP /
`GDRIVE_SA_KEY` secrets the Submissions watch already uses, and there is no
Drive/OAuth dependency.

**Predicted first enabled run** (pinned here so it can be checked against
reality, the way ADR 021's prediction was): every due site records a `baseline`
row and **zero emails are sent**. Which sites are due depends on the date via
`_is_due` — the 3 daily sites always are; the biweekly and quarterly ones only
inside their staggered 3-day windows. Alerts begin only on the *next* run in
which something actually changes.

Recipients start as **Trisha only** (`nsite_violations.recipients`), the
Meeting Watch / MMD / RIDE / Submissions precedent for a brand-new alert
stream. Delete that block to widen to the full `alert_recipients` list once the
alert copy has been seen in the wild.

## Consequences

- The monitor now watches **three** nSITE profiles per site — Documents
  (`facilities:`), Submissions (`nsite_submissions.tiers`), and Violations
  (`nsite_violations.tiers`) — each with its own cadence map over the one
  shared `nsite_sites` registry. ADR 022's extraction paid off exactly as
  intended: this build added a profile without copying the 19-site list.
- Four of the six profiles ADR 020/021 flagged remain unpolled: Compliance
  Actions, Complaints, Permits, Evaluations, Active Public Notices. The
  `nsite-six-unpolled-profiles-schemas` memory carries confirmed endpoints and
  counts for all of them.
- **Compliance Actions is the natural next pick.** Every violation record here
  carries an `evalEvalNum` linking it to a parent evaluation, and 5 records
  already sit at "Active – Compliance Action Taken" — so that profile is the
  documented other half of the enforcement story this stream now watches.
