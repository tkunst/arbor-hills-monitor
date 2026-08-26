# ADR 032 — Stream Q: nSITE Active Public Notices watch

Date: 2026-08-25 (Open Decision 1 resolved 2026-08-26 — see the addendum at
the end)
Status: **accepted, as amended by the 2026-08-26 addendum.** Open Decision 1
(the ROP-overlap question) is resolved; Open Decision 2 (the n=1 evidence
base for the diff key) is unaffected and remains a disclosed residual risk.
Activation (`enabled: true`) is still a separate, later human step, same as
every other new-source stream in this series — resolving the design
question is not the same action as turning the poller on.
Builds on: ADR 023 (Violations), ADR 028 (Compliance Actions), ADR 029/030
(Evaluations/Permits — the ref-number-keyed idiom this profile follows), ADR
031 (Complaints — the most recent merged sibling, whose mechanics this
rebases onto per the handoff's stale-framing guard), ADR 022 (the shared
`nsite_sites` registry), ADR 017 (the ROP renewal watch this profile
overlaps with — the central open question below)

## Context

This is the **6th and LAST** of the six originally-staged unpolled nSITE
profiles (staged 2026-08-08). Active Public Notices are the formal
comment-window announcements EGLE files per site — permit renewals, draft
permits, hearings. For anyone tracking Arbor Hills, a public-comment window
is the single most actionable EGLE record type: it has a hard deadline.

**Dependency check (per the handoff):** confirmed both `coder:nsite-
violations-watch` (PR #36) and `coder:nsite-compliance-actions-watch` are
merged on `main` — `grep -rl NsiteStructuralError` returned `nsite_client.py`
and every one of `nsite_violations_watcher.py`,
`nsite_compliance_actions_watcher.py`, `nsite_evaluations_watcher.py`,
`nsite_permits_watcher.py`, `nsite_complaints_watcher.py` plus their test
files (non-empty). `main` was clean at `13d4717`, no open PRs, no competing
branch for this goal.

**This handoff is explicit, in its own text, that this profile is different
from its five siblings** — flagged as "the LEAST safe to autonomously merge"
before any code was written, for two reasons baked into the data itself, not
a process caveat: (1) a genuine, unresolved overlap with Stream H's existing
ROP-renewal watch, and (2) a sample size of essentially one record. Both are
confirmed, not merely repeated from the handoff, below.

## The feasibility gate — run BEFORE any implementation code, per the handoff

### Endpoint and schema

`https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/default-mode/profiles/1-profile/2-active-public-notices`

Query shape, `Referer` header, and `queryResults` envelope are identical to
every sibling fetch. Anonymous, no auth.

**Live-fetched 2026-08-25, all 19 `nsite_sites`** (not just the 5 the handoff
already sampled on 2026-07-24):

| srn | records |
|---|---:|
| N1504 | 1 |
| (all other 18 registry sites) | 0 |

**One record total, across all 19 sites.** This is not a sampling
coincidence — it is the SECOND independent 19-site sample, a month apart,
and both times the total was exactly 1.

### Schema confirmed via the API's own field metadata, not just the one sample

The response's `lookups.defaultElement.metadata` block (the same
self-describing metadata `nsite_permits_watcher`'s build cross-checked)
lists exactly these fields for this profile:

| Field | `typeName` | Notes |
|---|---|---|
| `id` | String | "Primary Key" — declared, but absent from every actual record returned (present in neither the 2026-07-24 nor the 2026-08-25 sample) |
| `publicNotifPnurl` | String | an `<a>`-wrapped download URL embedding a long signed integer id |
| `publicNotifExtrnlPublNoticeNum` | String | "Notice Reference Number" — null on both live records seen |
| `publicNotifRefPublicNotifCovrg` | String | "Notice Coverage Type" — `"Facility Location"` on both live records seen |
| `publicNotifStartDate` | DateTime | comment-window start |
| `publicNotifEndDate` | DateTime | comment-window end — the actionable deadline |
| `publicNotifComments` | String | free text, can be long |

**No program-area, permit-type, or facility-category field exists anywhere
in this schema.** This is confirmed against the API's own metadata, not
inferred from one record's absence of such a field — it rules out one of
the three options below concretely (see Open Decision 1, Option 3).

### The one live record, both times, was a ROP renewal notice

- **2026-07-24 (handoff's original sample):** P1488, a ROP renewal comment
  window.
- **2026-08-25 (this build's re-sample):** N1504, a DIFFERENT ROP renewal
  comment window (2026-08-10 through 2026-09-09), for "Arbor Hills Energy,
  LLC" — full text: *"The Michigan Department of Environment, Great Lakes,
  and Energy (EGLE), Air Quality Division (AQD) has opened a public comment
  period on the renewal of a draft Renewable Operating Permit (ROP) for
  Arbor Hills Energy, LLC from August 10, 2026 to September 9, 2026. ..."*

`rop.enabled: true` (Stream H, ADR 017) already watches N1504 for exactly
this event, via a completely different mechanism (a statewide
`ROP_Public_Notice.pdf` text-mention check). **This is not a hypothetical
overlap — it is the live, current state of this profile's one real record.**
Two independent samples, a year apart in EGLE's own filing calendar, both
landing on a ROP notice, is what makes this profile's marginal, non-ROP
value genuinely uncertain rather than a one-off artifact worth waiting out.

## Open Decision 1: how should this watch relate to Stream H's ROP watch?

Presented as three options, per the handoff — **not resolved here**:

1. **Standalone** (what this code ships doing, since it needs to do
   *something* to be testable/reviewable): poll every site, alert on every
   new/changed notice, full stop. Simplest. Will double-alert recipients on
   a ROP renewal window Stream H already emailed about — two independently-
   worded emails about the same comment window.
2. **Dedupe against Stream H:** suppress a notice whose window matches an
   already-alerted ROP trip-wire. More correct, but needs a shared key
   between two mechanisms that were not designed to share one — `rop_
   watcher.py` keys on (SRN, "mentioned in the statewide notice PDF");
   this profile keys on a per-notice URL id. Not attempted here.
3. **Scope to non-ROP notices only:** this profile's entire marginal value
   over Stream H is the non-ROP notices (other program areas, other notice
   types) it might someday carry. **Ruled out concretely by this build's
   feasibility gate, not left as "unconfirmed":** no field exists — not in
   `queryResults`, not in the API's own field-metadata block — to filter on.
   The only signal that a notice is ROP-related is free-text pattern
   matching against `publicNotifComments` ("Renewable Operating Permit
   (ROP)" appears in prose on both live samples) — a fragile heuristic, not
   a structured filter, and not implemented here.

**This build's recommendation, updated from the handoff's own lean** ("scope
to non-ROP if a reliable filter exists, else standalone with disambiguation
copy"): since Option 3's premise (a reliable filter) is now confirmed absent,
the real choice is between Option 1 (as shipped, with disclosure) and Option
2 (dedupe, not built). **Whichever Trisha picks, this build already ships
the mitigation that applies regardless: every alert this profile sends
carries an explicit disclosure paragraph** (see
`nsite_public_notices_watcher.format_change_body`) stating that the notice
may duplicate a separate Stream H email about the same comment window — so
even under Option 1, a reader is never left thinking two unrelated things
happened.

## Open Decision 2: is one record enough to commit a diff key?

**No, not with the confidence Evaluations/Permits had** — and this build
does not claim otherwise. Both of those profiles' ref-number-keyed design
was verified against hundreds of live records (477 at N2688 for
Evaluations, 22 total for Permits). This profile has had **exactly one**
live record on each of two sampling dates, a month apart. The chosen key
(the id embedded in `publicNotifPnurl`, regex-extracted) has been present
and stable on both records seen — that is the entire evidentiary basis for
calling it a usable key. **This is a bet, not a confirmed build**, stated
plainly rather than dressed up as parity with its five siblings.

### Why the key is the URL-embedded id, NOT `publicNotifExtrnlPublNoticeNum`

The handoff suggested preferring `publicNotifExtrnlPublNoticeNum` "if it
ever populates" and falling back to the URL id otherwise. **This build
deviates from that literal suggestion, deliberately, after an advisor
review caught the failure mode:** if the key were chosen conditionally per
record (ext_num when present, else the URL id), a notice first seen with
`ext_num` null — keyed by its URL id — that later has EGLE populate
`ext_num` on that SAME notice would have its computed key change out from
under it. The diff would then report the OLD key REMOVED (a false "comment
window closed") and the NEW key ADDED (a false "new comment window
opened") for what is really one unchanged notice. Keying **unconditionally**
on the URL-embedded id avoids this: it has been present on both records
seen, and nothing about it depends on a field EGLE may or may not populate
later. `ext_num` is still carried as an ordinary diffed field, so the day it
does populate on an existing notice, that reads as a benign "changed" line —
tested explicitly
(`test_ext_num_populating_on_an_existing_notice_is_a_benign_change_not_a_key_flip`).

### The `_duplicate_key_count` guard earns its place here more than anywhere else

Every ref-keyed sibling (Evaluations, Permits) carries a guard that detects
if its key ever stops being unique and refuses to silently misdiagnose a
genuinely-new record sharing an existing key as a benign "changed" line.
This profile inherits the identical guard — but here it is not defensive
parity, it is close to the most likely thing to actually fire, given the
key's verification rests on n=1 rather than n=hundreds.

## Why `publicNotifComments` gets its own cap, distinct from the general budget guard

Every sibling nSITE watch (Violations, Compliance Actions, Evaluations,
Permits, Complaints) carries a general multi-record Sheets-cell budget
guard that degrades the WHOLE snapshot past a character threshold. That
guard is inherited here too, for structural parity, but at this profile's
observed volume (0-1 records per site) it is purely theoretical — the real
risk the handoff calls out is different: **a single verbose
`publicNotifComments` value could threaten the cell on its own, regardless
of record count.** Both live samples are a paragraph (~500-800 chars), but
nothing guarantees EGLE never files a multi-page hearing notice's full text
in this field. `nsite_public_notices_watcher._capped_comments` truncates
any one record's `comments` to `COMMENTS_STORED_CHARS` (4,000) and appends a
content hash of the FULL text, so a change beyond the truncation point
still changes the stored value — and so still fires a change alert — even
though the verbatim text past that point is never persisted.

**One documented simplification versus the handoff's literal phrasing**
("truncate/hash in storage, keep full text in the email"): this module uses
ONE capped value for both storage and the alert body, rather than a second,
uncapped copy threaded through to email formatting only. At
`COMMENTS_STORED_CHARS` (4,000) against the longest real sample observed
(~800 chars), this is not a live gap today — it is flagged here as a
deliberate choice, not hidden in the code.

## Decisions

### 1. Ref-number-keyed diff, on a URL-embedded id — covered above

### 2. Standalone polling, with disclosure copy — the shippable default, not a resolution

Covered under Open Decision 1. `format_change_body` states plainly, in
every alert, that this profile's history so far has only ever surfaced ROP
renewal notices Stream H also tracks, and that a duplicate-seeming email may
be expected. This is the correct default REGARDLESS of which of the three
options Trisha ultimately picks — Option 1 needs it as the actual behavior;
Option 2/3, if built later, would only ever need to REMOVE alerts this
disclosure already covers, never retrofit disclosure onto alerts that
lacked it.

### 3. Per-site tiers — this profile's own, not a copy of any sibling

`daily (3) / biweekly (3) / quarterly (13)`:

**daily (3)** — the three ROP-permit-holding sites; a live comment window
has now been observed at two of the three across the two sampling dates:

| srn | why |
|---|---|
| N2688 | ROP-permit-holding air facility; no notice observed yet, but the same category as the two sites that HAVE had one |
| N1504 | live comment window observed 2026-08-25 (this build) |
| P1488 | live comment window observed 2026-07-24 (the handoff's original sample) |

**biweekly (3)** — sites with another open matter tracked elsewhere in this
monitor, or a mis-filing-insurance duplicate of N2688 (the same override
every sibling profile applies):

| srn | why |
|---|---|
| RA | water/NPDES facility with other open enforcement history tracked by Violations/Compliance Actions |
| WRD | open JPA + PFOS interest at the Land & Water Interface site |
| AHLI | bare duplicate registration for the same landfill as N2688 |

**quarterly (13)** — zero active public notices ever seen; typo/mis-filing
insurance only: `P1504`, `AHLA`–`AHLE`, `ADL1`, `ADL2`, `COMP`, `AHE1`,
`AHE2`, `SANL`, `ERNGX`.

`test_shipped_config_tiers_cover_every_registry_site_and_differ_from_
siblings` pins the full 19-site coverage and pairwise inequality against
Evaluations/Permits/Complaints.

### 4. Error contract: raise, don't swallow — and DOES filter, like Evaluations/Permits/Complaints

`fetch_site_public_notices` reuses `NsiteFetchError`/`NsiteStructuralError`
and raises after 3 attempts, exactly like every sibling. It filters on a
non-empty regex-extracted URL id — a keyless record cannot be placed in a
ref-number-keyed snapshot, matching the Evaluations/Permits/Complaints
precedent. No such record has been observed live.

All of ADR 023/028/029/030/031's other hardening carries over verbatim: a
per-site `try` covering the Sheets write; a batched, RAISING tab read that
aborts before any write on failure; a change recorded but not emailed
exiting non-zero; `alerting_is_configured()` aborting up front when
delivery is already impossible; the `hasResultsRemaining` paging guard
raising `NsiteStructuralError` rather than silently diffing a partial page.

### 5. `_is_due` is imported, not reimplemented

Imported from `nsite_submissions_watcher`, with an identity test
(`test_is_due_is_imported_not_reimplemented`).

## Why this ADR was `proposed`, not `accepted`, at first (historical — see the 2026-08-26 addendum for the resolution)

Every prior nSITE ADR in this series (023/028/029/030/031) reached
`accepted` status by merging on green CI with zero open review findings —
the standard overnight-coder path. This one does not, on purpose:
overnight-coder.md Step 8's merge authorization requires the build to have
resolved every open question before merging; Open Decision 1 (the ROP
overlap) is a genuine design choice this build is not authorized to make
unilaterally, no matter how green the tests are. Per the handoff's own
"Definition of done," **the expected outcome here is a DRAFT PR held for
Trisha's review**, not an autonomous merge — mocked-green tests and a clean
security review are necessary but explicitly NOT sufficient for this
specific profile. This ADR will move to `accepted` (or be revised) once
Trisha answers Open Decision 1 and the PR is either merged as-is or amended.

## Adversarial review (mitigations built in, per this repo's standing process rule)

- **Show-stopper: double-alerting on a ROP comment window Stream H already
  emailed.** Not a footnote — this IS Open Decision 1. Mitigated in the
  shippable default with explicit disclosure copy in every alert; NOT
  silently resolved by picking an option unilaterally.
- **Show-stopper: committing an unverified diff key from essentially one
  sample.** Detection: the 19-site fetch, run twice a month apart, both
  times returning exactly one record. Mitigation: hold for review (this
  ADR, and a draft PR) rather than auto-merge; the key's real-world
  verification is stated as n=1, not dressed up as parity with Evaluations/
  Permits. The `_duplicate_key_count` guard is the code-level backstop if
  the key's uniqueness assumption is ever wrong.
- **Manageable: `publicNotifExtrnlPublNoticeNum` is null now but may be the
  real key later.** Mitigated: kept as an ordinary diffed field (not the
  key), so its populating on an existing notice reads as a benign change,
  not a false remove+add pair — see the key-flip discussion above.
- **Manageable: a long `publicNotifComments` blows the cell.** Mitigated:
  `_capped_comments` truncates + hashes per-record, independent of the
  general multi-record budget guard every sibling also carries.
- **Residual (accepted, same as every stream here): a persistent fetch
  failure after baseline goes skip-and-warn quiet.** Standard across every
  stream; loud (exit 1) before any baseline exists.

## Activation (Trisha's step, not the build's — and gated on more than the usual review)

Ships **`nsite_public_notices.enabled: false`**. Unlike every sibling new
poller in this series, this is not simply "flip it whenever you like after
skimming the diff" — it is held specifically pending Open Decision 1. The
config's own comment block says so directly: turning this on unmodified
silently picks "standalone" (Option 1) by default.
`test_shipped_config_ships_disabled_and_stays_disabled` asserts the flag
itself (not merely that a workflow exists), because for this one profile
"not yet activated" and "deliberately held" need to be told apart in the
test suite too.

The workflow file was landed directly into `.github/workflows/`, not
parked: this build session's SSH key authenticated non-interactively
against GitHub, so the `workflow` OAuth-scope blocker that forced Streams
L/M's workflow files through `docs/pending-workflows/` did not apply — same
as Streams N/O/P.

To go live (once Open Decision 1 has an answer): set
`nsite_public_notices.enabled: true` and commit. No new secret is required —
the workflow reuses the same `GSHEET_ID`/SMTP secrets, no Drive/OAuth
dependency.

Recipients start as **Trisha only** (`nsite_public_notices.recipients`), the
established precedent for a brand-new alert stream.

## Consequences

- The monitor now has code (though not yet an activation decision) covering
  **eight** nSITE profiles per relevant site — Documents, Submissions,
  Violations, Compliance Actions, Evaluations, Permits, Complaints, and
  Active Public Notices — completing the six-profile build-out this repo's
  overnight-coder queue staged on 2026-08-08.
- **This completes the six originally-unpolled nSITE profiles.** No further
  profile is queued behind this one from that original staging batch.
- Unlike every other stream in this repo, this one ships with an open
  design question as its headline deliverable, not a settled default
  waiting on a flag flip. The PR holding this ADR is a draft for exactly
  that reason.

## Addendum (2026-08-26) — Open Decision 1 resolved

Trisha reviewed the three options above and asked for a hybrid, built from
Options 1 and 3 rather than a pure pick of one: **suppress the redundant
EMAIL, never the durable Sheet row, when a notice's `comments` text reads as
a ROP renewal by keyword match — failing open (still emailing) whenever
that match can't be made confidently.**

### Why this, and not a pure option

- **Pure standalone (Option 1) was rejected** on the evidence this build
  gathered: both real notices this profile has ever surfaced (2026-07-24
  and 2026-08-25) were ROP renewals Stream H already covers. Shipping
  standalone unmodified would mean every alert emitted so far in this
  profile's real history would have been a duplicate — training recipients
  to ignore the stream before it ever earns their attention for a genuinely
  new event type.
- **A Stream H state-based dedupe (Option 2) was rejected** as more
  correct in principle but not worth its coupling cost: `rop_watcher.py`
  keys on (SRN, "mentioned in the statewide notice PDF text"), a
  fundamentally different and fuzzier signal than this profile's
  URL-embedded notice id. Building a reliable cross-mechanism match between
  the two — especially one that must not silently swallow a real, new,
  non-ROP notice if the match logic is ever wrong — is real ongoing
  engineering risk for a benefit the simpler heuristic below already
  captures for the one pattern actually observed.
- **A pure keyword-suppress-and-drop version of Option 3 was also rejected**
  — suppressing the underlying RECORD (not just the email) risks silently
  losing a real event if the keyword heuristic ever misfires, which
  conflicts with this monitor's broader posture of never letting a
  parse/classification choice make a real filing disappear without a
  trace.

### The implementation

- `nsite_public_notices_watcher._looks_like_rop_notice(comments)` — a pure,
  case-insensitive keyword match on `"renewable operating permit"` or a bare
  `"rop"` token. Both live records seen say "Renewable Operating Permit
  (ROP)" in prose; this is a free-text heuristic (Option 3's structured
  filter is still unavailable — see the schema finding above), disclosed as
  such, not dressed up as a reliable classifier.
- `_all_changed_notices_look_like_rop(old, new)` — True only if EVERY
  notice a given diff would otherwise alert on (newly added, changed, or
  no-longer-listed) matches. FAILS OPEN (returns False, meaning "still
  alert") on: a truncated/degraded snapshot (no full comments text to
  inspect — currently theoretical at this profile's real volume), a
  duplicate-key state, an unreadable previous snapshot, or a mixed batch
  where even one notice doesn't match. A mixed batch is not split into a
  "partial" email — this profile's real volume (0-1 records per site)
  doesn't call for that complexity, and the whole batch alerting on one
  non-match is the safer default anyway.
- `_diff_and_record` writes the durable Sheet row FIRST, unconditionally,
  exactly as before this change — suppression only ever affects whether
  `ea.send_email` is subsequently called. A suppressed change is a NEW
  result state, `"changed_suppressed"`, tracked separately from `"changed"`
  in `run()`'s counts and NEVER treated as an `alert_failed` (a deliberate
  suppression is working as designed, not a failure, and must not exit
  non-zero the way a lost alert does).
- Gated by a new config lever, `nsite_public_notices.rop_alert_suppression`
  (defaults `true` — a bare/minimal config still gets the safer behavior;
  set `false` to revert to pure standalone alerting without a code change).
- `format_change_body`'s disclosure paragraph was rewritten to match: since
  an email now only fires when the ROP-keyword match failed (or
  suppression is disabled), the copy explains that plainly rather than
  claiming "this profile has only ever surfaced ROP notices" (which would
  now read backwards — an email firing at all means THIS TIME it didn't
  look like one).

### Residual risk: the heuristic is validated only against ROP samples

Both real records seen to date (2026-07-24 and 2026-08-25) are ROP renewal
notices — the test suite's realistic fixtures are necessarily drawn from the
same two samples. That means the one direction that actually costs something
is unmeasured: a **false positive**, where a genuinely novel *non-ROP* notice
happens to mention "ROP" or "renewable operating permit" in passing (e.g. a
hearing notice distinguishing itself from the ROP track) and has its **email
suppressed** — muting the exact signal this stream exists to add over Stream
H, on the one category of notice it uniquely surfaces. The durable Sheet row
is unaffected (nothing is lost from the audit trail), but the alert — the
deliverable — would be silent. If that ever bites, `rop_alert_suppression:
false` is the immediate rollback, no code change required.

### What this does not change

- Open Decision 2 (the diff key's evidence base is n=1 real records) is
  untouched — this addendum is scoped to the ROP-overlap question only.
  `_duplicate_key_count` remains the code-level backstop for that residual
  risk.
- Activation (`nsite_public_notices.enabled: true`) remains a separate,
  later step. Resolving the design question removes the reason this PR
  could not be merged; it does not by itself make turning the live poller
  on Trisha's next action — that is still hers to decide and take
  explicitly, same as every other new-source stream in this series.
