# Overnight-coder handoff — watch EGLE's nSITE Violations profile

*Staged 2026-07-24. Read `docs/overnight-coder.md` first — this is a goal handed
to that loop, not a new procedure. New poller against a live external system, so
it ships `enabled: false`. **The endpoint/field-schema discovery is already
done** (this session live-queried it — see "The profile" below); you should not
need to re-discover it, only confirm it against a broader sample as your Step-3
spike. Branch name suggestion: `nsite-violations-watch`.
**Dependency: `coder:nsite-site-registry` must be merged first** — this build
references the shared `nsite_sites` registry it creates, not a fresh copy of the
19-site list. If that item hasn't landed yet, stop and leave a note; don't
duplicate the site list to route around the dependency.*

## Why this is next

`docs/decisions/020-nsite-submissions-watch.md` and `021-tiered-submissions-
polling.md` built a Submissions watch across all 19 of Trisha's MiEnviro
subscriptions. Both ADRs flag the SAME nSITE API exposes 6 more unpolled
profiles per facility — Permits, Active Public Notices, Evaluations,
Violations, Compliance Actions, Complaints — a real, distinct enhancement
opportunity, deliberately kept out of those builds' scope. This is the first of
those six, picked first because live reconnaissance (below) found it carries
the most real, high-value history: **RA alone has 299 recorded violations,
N2688 has 58** — this is EGLE's own enforcement record, arguably the single
most directly advocacy-relevant profile available (a formal violation finding,
not just a filing or a permit status).

## The profile (confirmed live 2026-07-24 — do not re-discover)

- **Endpoint:** `https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/
  default-mode/profiles/3-compliance/2-violations`
- **Query shape:** identical to `fetch_site_submissions` in `nsite_client.py` —
  same `queryParams={"filter":[{"id":"<nsite_id>"}]}` URL-quoted param, same
  `responseContentType=application/json&includeMetadataInResponse=true&
  loadChildren=true&filterString=` suffix, same `Referer` header pattern,
  response has a top-level `queryResults` array. Anonymous (no auth needed),
  confirmed against 5 known site IDs.
- **Real sample record** (from N2688, 2026-07-24):

  ```json
  {
    "violRefViolCatgDescr": "Rule 1001: Performance tests by owner",
    "violRefViolTypeDescr": "Testing/Sampling",
    "violRefViolStatDescr": "Active – Compliance Action Taken",
    "violNonCmplStartDate": "2026-07-08T00:00:00.0000000-04:00",
    "violViolNotifCmnts": "PTI No. 79-17, FGENCLOSEDFLARES-S2 Condition V. Testing/Sampling 2., 6., and 10.",
    "violDescr": "AQD - Air",
    "evalEvalNum": "E-1DEM-T431-WSP2",
    "evalRefEvalTypeDescr": "On-Site Inspection"
  }
  ```

- **Real counts across the 5 already-known sites** (2026-07-24 snapshot):
  N2688 58, RA 299, N1504 3, P1488 0, WRD 0. Expect similar or lower for the
  other 14 sites in the registry (most are dormant duplicates with little-to-no
  Documents/Submissions history either — see ADR 021's tiering rationale).

## Feasibility gate (confirm the broader schema, don't re-discover the endpoint)

The endpoint and one sample record are already confirmed — what's NOT yet
confirmed is whether **every** violation record has this exact shape, and
critically, **whether there's a stable unique-ID field this shallow check
missed.** The sample above has no obvious single-value primary key (unlike
Submissions' `submSubmRefNum`). Before committing to a diff design:

1. Live-fetch Violations for all 6 already-known non-zero sites (N2688, RA,
   N1504 at minimum — WRD/P1488 are 0, not useful for schema confirmation) and
   inspect several records each, not just one. Look specifically for any field
   that looks like a row/violation ID (something like `violId`, a GUID, an
   integer) that a single-sample check could have missed.
2. **If a stable unique-ID field exists:** key the diff on it, mirroring
   Submissions' ref-number-keyed design (`summarize_submissions_change`) —
   distinguish a brand-new violation from an existing one's status advancing
   (e.g. `violRefViolStatDescr` moving from "Active" to "Resolved" or similar).
3. **If no stable unique-ID field exists** after this broader check: fall back
   to a full-record multiset diff — the ROP/RIDE/MMD ADDED/REMOVED-over-full-
   tuples idiom (Counter over the full field tuple, same as `rop_watcher.py`).
   This is the DEFAULT assumption from this session's reconnaissance; only
   switch to ref-number-keying if step 2 actually finds a real key.
4. This is a genuine feasibility gate, not a formality — if the response shape
   turns out to be unstable or radically different across sites (e.g. some
   sites return a completely different field set), that is a legitimate
   Step-3 stop; write up what was found and open a draft PR rather than
   forcing a design onto data that doesn't fit it.

## Approach (adapt, don't re-litigate — mirror `nsite_submissions_watcher.py` closely)

1. **New client function `fetch_site_violations`** in `nsite_client.py`, modeled
   directly on `fetch_site_submissions` (same retry/error contract — see next
   point) — NOT on `fetch_site_documents`.
2. **Error contract: raise, don't swallow — same as Submissions, NOT Documents.**
   `nsite_violations_watcher` will diff the returned list, so a swallowed
   failure returned as `[]` would misread an outage as "every violation
   resolved simultaneously" and could even suppress real signal. Reuse
   `NsiteFetchError` (it's not Submissions-specific) rather than inventing a
   parallel exception class.
3. **New watcher `nsite_violations_watcher.py`**, modeled on
   `nsite_submissions_watcher.py`'s full shape (durable-row-before-alert,
   partial-failure isolation per site, baseline-silently-on-first-sight) —
   INCLUDING its tiered cadence: read `nsite_violations.tiers` (srn -> poll)
   joined against the shared `nsite_sites` registry `coder:nsite-site-registry`
   created, exactly the way `nsite_submissions_watcher.py` will read
   `nsite_submissions.tiers` after that refactor. **Do not re-copy the 19-site
   list — if you find yourself doing that, the dependency didn't actually
   land; stop and check.**
   - Diff design: per the feasibility gate's outcome (ref-number-keyed OR
     full-record multiset — don't assume, confirm first).
   - Item key: `viol:<srn>` (matching `subm:<srn>`'s pattern).
4. **Cadence tiers for THIS profile — do not just copy Submissions' tiers.**
   The live counts above suggest Violations activity doesn't track Submissions
   activity 1:1 (e.g. WRD/P1488 show 0 violations despite having real
   Submissions history) — pick tiers based on VIOLATIONS-specific activity you
   observe during the feasibility gate's broader fetch, using the same daily/
   biweekly/quarterly framework and reasoning ADR 021 used (sites with real
   recent violation activity -> daily; sparse/old -> biweekly; zero -> quarterly
   as typo/mis-filing insurance). Document your actual tier assignments and
   why in the new ADR (see below) — don't just default everything to one
   tier for expedience.
5. **Config — `nsite_violations: { enabled: false, recipients: [...],
   tiers: {...} }`** in `config.yml`, same shape as `nsite_submissions`'
   block post-refactor. Ships disabled (new source, unattended build — unlike
   ADR 020/021 which shipped enabled because Trisha directed those live).
6. **Sheet tab** — `sheet_writer.py` gets a `Violations Watch` tab, same
   append-only shape as `Submissions Watch`.
7. **Workflow — `nsite-violations-watch.yml`**, mirroring
   `nsite-submissions-watch.yml` (own `concurrency` group so it never races
   the shared state; runs on schedule but is a no-op while `enabled: false`).
8. **Tests** — mirror `tests/test_nsite_submissions.py`'s structure closely:
   fetch-error contract (raises, never swallows), pure snapshot/diff
   correctness for WHICHEVER diff design the feasibility gate settles on, full
   `run()` flows (baseline/unchanged/new/changed/fetch-fail) through a fake
   Sheets service, and the tiered-cadence `_is_due` reuse/gate behavior
   (reuse `nsite_submissions_watcher._is_due` directly rather than
   reimplementing it — it's already generic over `cadence`/`srn`/`today`, not
   Submissions-specific; importing it is correct, copy-pasting it is not).
9. **Docs — new ADR 023** (022 is the site-registry refactor) — "Stream L:
   nSITE Violations watch": the endpoint, the diff-key decision (ref-number vs
   full-record, with the actual finding from your broader schema check), the
   per-site tier assignments and their rationale, the `enabled: false`
   activation step. Update `README.md`/`CLAUDE.md`.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: no stable diff key exists AND full-record diffing is
  unstable** (e.g. free-text fields like `violViolNotifCmnts` change wording
  without a real status change, causing constant false "changed" alerts).
  Detection: watch for this during the feasibility gate's broader sample —
  if free-text fields look volatile across repeated fetches of the same
  record, exclude them from the diffed field set (same as RIDE excluding
  `ProjectManaager`) rather than diffing everything blindly.
- **Manageable: RA's 299 violations is a lot of first-run baseline volume.**
  Mitigation: first sighting baselines silently (no alert) — same as every
  other watch in this repo; this is not a new risk, just confirm the existing
  pattern holds at this scale.
- **Manageable: violation status vocabulary EGLE uses may not match a simple
  "resolved good / active bad" binary** (the sample shows "Active – Compliance
  Action Taken", implying a multi-state lifecycle). Mitigation: don't build in
  any urgency/severity judgment about WHICH status is good or bad — this
  watch's job is trip-wiring change, the same posture as Submissions
  (alerting on ANY status change, letting a human read what it means), not
  classifying severity.
- **Residual risk (accept + note in the ADR):** a persistent fetch failure
  after baseline goes skip-and-warn quiet, same accepted residual as every
  other stream in this repo.

## Definition of done

Green `pytest -q`; the feasibility gate's broader-sample finding recorded in
ADR 023 (including which diff design was chosen and why); a new
`nsite_violations` stream that is a no-op while `enabled: false`; per-site
tier assignments justified with real observed data, not copied from
Submissions; README/CLAUDE.md updated; PR merged per overnight-coder Step 8
with a closing comment stating plainly that going live is a separate Trisha
step. If the feasibility gate's broader check finds the schema genuinely
unworkable, the "done" state is instead a draft PR with a written finding and
no client code — a legitimate outcome, not a failed night.

## Then Step 9 (after a successful merge)

Archive `coder:nsite-violations-watch` from
`Cowork-claude/documents/overnight-coder-queue.md` to
`overnight-coder-archive.md`. This item did not come from the worker queue (no
worker pin to release) — staged directly at Trisha's request. Note in the
archive entry which of the 5 remaining profiles (Compliance Actions,
Complaints, Permits, Evaluations, Active Public Notices) seems like the best
next pick given what this build learned, for whoever stages the next one — the
`nsite-six-unpolled-profiles-schemas` memory (this account) already has
confirmed endpoints/schemas/counts for all 5, so the next handoff should be
much faster to write than this one was.
