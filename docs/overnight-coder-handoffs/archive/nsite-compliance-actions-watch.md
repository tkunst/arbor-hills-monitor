# Overnight-coder handoff -- watch EGLE's nSITE Compliance Actions profile

*Staged 2026-08-04. Read `docs/overnight-coder.md` first -- this is a goal handed
to that loop, not a new procedure. New poller against a live external system, so
it ships `enabled: false`. **The endpoint/field-schema discovery is already done**
(live-queried 2026-07-24 -- see "The profile" below); you should not need to
re-discover it, only confirm it against a broader sample as your Step-3 spike.
Branch name suggestion: `nsite-compliance-actions-watch`.*

**Dependency: `coder:nsite-violations-watch` must be merged (PR #36) first.**
This build reuses several patterns introduced in that PR (`NsiteStructuralError`,
the per-site try covering the Sheets write, the batched tab read, the
`alerting_is_configured()` gate, and the `docs/pending-workflows/` parked-workflow
mechanism + its enforcing test). After rebasing onto the merged violations watch,
run `grep -r "NsiteStructuralError" .` -- if it returns nothing, the dependency
has not landed; stop, leave a note in the PR, and don't re-invent it.

## Why this is next

ADR 023 (`docs/decisions/023-nsite-violations-watch.md`) explicitly names
Compliance Actions as the recommended next profile. Five records in N2688's
Violations list already carry `violRefViolStatDescr = "Active -- Compliance
Action Taken"` -- those violations have formal compliance actions open against
them. This watch gives visibility into whether those CAs get resolved, escalated,
or new ones opened. It is the narrowest, most targeted of the five remaining
profiles: fewest records (N2688 39, RA 10), a plausible stable diff key, and
the closest conceptual link to the already-live Violations watch.

## The profile (confirmed live 2026-07-24 -- do not re-discover)

- **Endpoint:** `https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/
  default-mode/profiles/3-compliance/3-compliance-actions`
- **Query shape:** IDENTICAL to `fetch_site_submissions` in `nsite_client.py` --
  same `queryParams={"filter":[{"id":"<nsite_id>"}]}` URL-quoted param, same
  `responseContentType=application/json&includeMetadataInResponse=true&
  loadChildren=true&filterString=` suffix, same `Referer` header pattern,
  response has a top-level `queryResults` array. Confirmed anonymous (no auth
  needed) against 5 known site IDs.
- **Field schema** (`cmplActn*` prefix -- 6 fields from the 2026-07-24 sample):

  | Field | Notes |
  |---|---|
  | `cmplActnRefProgramAreaDescr` | program area (e.g. "AQD - Air") |
  | `cmplActnRefCmplActnCatgDescr` | CA category |
  | `cmplActnRefCmplActnTypeDescr` | CA type |
  | `cmplActnCmplActnNum` | **looks like a stable reference number** (e.g. "VN-019436") -- the candidate diff key; see Feasibility gate |
  | `cmplActnActnDate` | date the action was issued |
  | `cmplActnRefCmplActnStatDescr` | status -- the primary watch field (alerts on status changes as well as new CAs) |

- **Real counts across the 5 already-known sites** (2026-07-24 snapshot;
  5 of 19 sites polled -- the other 14 are expected to be mostly 0, matching
  the Violations pattern):

  | Site | Count |
  |---|---|
  | N2688 | 39 |
  | RA | 10 |
  | N1504 | 2 |
  | P1488 | 0 |
  | WRD | 0 |

## Feasibility gate (confirm the broader schema + diff key -- don't re-discover the endpoint)

The endpoint and one sample record are already confirmed. What is NOT yet confirmed:

1. **Is `cmplActnCmplActnNum` actually unique within a site's record set?**
   The hypothesis is that it behaves like Submissions' `submSubmRefNum` -- a
   stable per-CA reference number. But "VN-019436" looks like a violation-notice
   number: if two compliance actions both reference the same violation notice, they
   could share this number, making it a non-unique key. Test this explicitly:
   - Fetch Compliance Actions for N2688 (39 records) and RA (10 records).
   - Check whether `cmplActnCmplActnNum` values are unique within each site's
     record set.
   - **If unique:** use it as the diff key, mirroring Submissions'
     ref-number-keyed design. Alert separately on a new CA (new `cmplActnCmplActnNum`
     appearing) vs. a status change on an existing CA (`cmplActnRefCmplActnStatDescr`
     moving on the same number). This is the preferred design if it holds.
   - **If NOT unique** (two records share the same number): fall back to a
     full-record `Counter` multiset diff, same as ADR 023's Violations design.
     Don't force a key that breaks uniqueness -- the multiset idiom is proven and
     safe at this record volume.
2. **Is the 6-field schema stable across all non-zero sites?**
   Fetch N1504 (2 records) and confirm the same 6 field names appear.
3. **Measure the snapshot size against the 50,000-character Sheets cell cap**
   (ADR 023 Finding 2). N2688 has 39 records -- at this volume a dict-per-record
   encoding is almost certainly well under 50K, but measure before assuming so.
   If ref-number-keyed (preferred design), store only the fields that change
   (status, date) under the key -- far smaller than a full-record dump. If
   multiset (fallback), follow ADR 023's run-length counted format.
4. This is a genuine feasibility gate, not a formality. If the schema is unstable
   or `cmplActnCmplActnNum` is non-unique in a way that makes both designs
   unworkable, that is a legitimate Step-3 stop.

## Approach (adapt, don't re-litigate -- mirror `nsite_violations_watcher.py` closely)

After `coder:nsite-violations-watch` merges, this build is a near-straight copy
with the diff-key design adjusted for the feasibility gate's outcome. The key
structural choices from ADR 023 that carry forward verbatim:

1. **New client function `fetch_site_compliance_actions`** in `nsite_client.py`,
   modeled on `fetch_site_violations` / `fetch_site_submissions`. Same retry/
   error contract: **raise, never swallow** (`NsiteFetchError` on a transient
   fetch failure; `NsiteStructuralError` if the response shape changes -- see
   ADR 023 for the distinction). Both exception classes are already in
   `nsite_client.py` after the violations watch merges.
2. **New watcher `nsite_compliance_actions_watcher.py`**, modeled on
   `nsite_violations_watcher.py`'s full shape:
   - Tiered cadence (read `nsite_compliance_actions.tiers` from the shared
     `nsite_sites` registry -- same as violations). Do NOT re-copy the 19-site
     list; if you find yourself doing that, the dependency didn't land.
   - Per-site try/except covering BOTH the fetch AND the Sheets write (ADR 023
     improvement over Submissions -- inherited, not re-litigated).
   - Batched snapshot read (one call for all tabs before the loop, not 19
     sequential reads -- ADR 023 pattern).
   - `alerting_is_configured()` check before any fetch or write.
   - `_is_due` imported from `nsite_submissions_watcher` -- it is generic over
     `cadence`/`srn`/`today`; import it, do not copy it.
   - Diff design: per the feasibility gate (ref-number-keyed if unique, multiset
     if not -- document the choice in the ADR).
   - Item key: `ca:<srn>` (matching `viol:<srn>`'s pattern).
   - Baseline silently on first sight.
3. **Cadence tiers for THIS profile -- do not copy Violations' tiers.**
   Violations and Compliance Actions have different activity rates: N2688 has
   39 CAs but 58 violations; RA has 10 CAs but 299 violations. Assign tiers
   based on the Compliance-Actions-specific counts you see during the feasibility
   gate, using the same daily/biweekly/quarterly framework ADR 021 and 023 used.
   Document your actual tier assignments and why in the new ADR.
4. **Config:** `nsite_compliance_actions: { enabled: false, recipients: [...],
   tiers: {...} }` in `config.yml`, same shape as the violations block. Ships
   disabled (new source, unattended build).
5. **Sheet tab:** `sheet_writer.py` gets a `Compliance Actions Watch` tab, same
   append-only shape as `Violations Watch`. If ref-number-keyed, the snapshot
   cell stores a dict keyed by `cmplActnCmplActnNum`; if multiset, use the
   run-length counted format from ADR 023.
6. **Parked workflow:** write the `nsite-compliance-actions-watch.yml` GitHub
   Actions workflow to `docs/pending-workflows/nsite-compliance-actions-watch.yml`
   (NOT `.github/workflows/`) -- the violations watch established this pattern
   because writing to `.github/workflows/` requires the `workflow` OAuth scope
   this session's token lacks. Add the same enforcing test (`enabled is False OR
   the workflow file is in .github/workflows/`) that the violations watch added
   for its own file. `docs/pending-workflows/README.md` already explains the
   pattern; don't rewrite it.
7. **Tests:** mirror `tests/test_nsite_violations.py`'s structure -- fetch-error
   contract, snapshot/diff correctness for the chosen diff design, full `run()`
   flows (baseline/unchanged/new/changed/fetch-fail) through a fake Sheets
   service, tiered-cadence `_is_due` reuse/gate behavior, and the
   pending-workflow enforcing test (new for this stream).
8. **Docs:** new ADR (take the next unused number by running
   `ls docs/decisions/ | sort | tail -1` on your branch -- at time of writing the
   last merged ADR is **026**; open PR #35 `upcoming-activities-digest` plans to
   use **027** for that stream, so verify whether 027 is available or taken before
   committing). Title: "Stream M: nSITE Compliance Actions watch." Cover: endpoint,
   diff-key decision (with the actual uniqueness finding from your broader sample),
   per-site tier assignments with rationale, snapshot encoding choice, the
   pending-workflow pattern. Update `README.md` / `CLAUDE.md`.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: `cmplActnCmplActnNum` is non-unique AND full-record
  multiset diffing is unstable** (e.g. a date field changes on every fetch for
  the same CA, causing constant false-positive alerts). Detection: during the
  feasibility gate, check for fields that vary across repeated fetches of the same
  record. Mitigation: exclude volatile fields from the diffed set (same as RIDE
  excluding `ProjectManager`), or treat the CA as a status-only watch (diff only
  `cmplActnRefCmplActnStatDescr`).
- **Manageable: status change on an existing CA (e.g. a CA closing) is exactly
  the high-value alert -- make sure the diff design captures it.** If
  ref-number-keyed: explicitly diff the `status` field per key (not just
  new-key-appeared). If multiset: the full-tuple diff catches it automatically,
  since a status change produces a new tuple.
- **Manageable: N2688's 39-record first baseline is small, but 5 of them are
  already "Active -- Compliance Action Taken" in Violations -- alerting context.**
  Mitigation: first sighting baselines silently (standard; confirmed at this
  scale). No cross-stream join needed -- the Compliance Actions watch is
  self-contained. If a CA closes in the future, the status-change alert is the
  signal.
- **Residual (accept + note in ADR):** a persistent fetch failure after baseline
  goes skip-and-warn quiet, same accepted residual as every other stream in this
  repo.

## Definition of done

Green `pytest -q`; the feasibility gate's uniqueness finding recorded in the ADR
(including which diff design was chosen and why, with the actual data); a new
`nsite_compliance_actions` stream that is a no-op while `enabled: false`; per-site
tier assignments justified with real observed data, not copied from Violations;
pending workflow at `docs/pending-workflows/nsite-compliance-actions-watch.yml`
with its enforcing test; README/CLAUDE.md updated; PR merged per overnight-coder
Step 8. If the feasibility gate finds the schema genuinely unworkable, a draft PR
with a written finding is the legitimate done state.

## Then Step 9 (after a successful merge)

Archive `coder:nsite-compliance-actions-watch` from
`Cowork-claude/documents/overnight-coder-queue.md` to `overnight-coder-archive.md`.
No worker pin to release -- staged directly. Note in the archive entry which of the
4 remaining profiles (Evaluations, Complaints, Permits, Active Public Notices) seems
like the best next pick given what this build learned. The
`nsite-six-unpolled-profiles-schemas` memory (this account) already has confirmed
endpoints/schemas/counts for all 4.
