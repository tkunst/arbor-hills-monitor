# Overnight-coder handoff -- watch EGLE's nSITE Evaluations profile

*Staged 2026-08-08. Read `docs/overnight-coder.md` first -- this is a goal handed
to that loop, not a new procedure. New poller against a live external system, so
it ships `enabled: false`. **The endpoint/field-schema discovery is already done**
(live-queried 2026-07-24 -- see "The profile" below); you should not need to
re-discover it, only confirm it against a broader sample as your Step-3 spike.
Branch name suggestion: `nsite-evaluations-watch`.*

*This is the 3rd of the 6 unpolled nSITE profiles -- the FIRST of the final four
(Evaluations, Complaints, Permits, Active Public Notices), staged as a batch from
the `nsite-six-unpolled-profiles-schemas` memory + the Violations/Compliance-Actions
templates. It is the cleanest and lowest-risk of the four (stable diff key, moderate
volume) -- hence first.*

## Dependency

**`coder:nsite-violations-watch` (PR #36, merged) AND
`coder:nsite-compliance-actions-watch` must both be merged first.** This build
reuses the patterns those two PRs introduced: `NsiteStructuralError` /
`NsiteFetchError`, the per-site try covering the Sheets write, the batched tab
read, the `alerting_is_configured()` gate, `_is_due` (imported, not copied), the
shared `nsite_sites` registry, and the `docs/pending-workflows/` parked-workflow
mechanism + its enforcing test. After rebasing onto the merged watchers, run
`grep -rl "NsiteStructuralError" .` -- if it returns nothing, a dependency has not
landed; stop, note it in the PR, and don't re-invent it.

> **STALE-FRAMING GUARD (this handoff was written BEFORE the Compliance Actions
> build ran).** Where this handoff and the actual merged `nsite_compliance_actions_
> watcher.py` disagree on any shared pattern (diff-key encoding, tier framework,
> snapshot format, test layout), **the merged CA watcher's real code wins** --
> rebase onto it and follow it. This handoff describes the schema (which does not
> change) and the profile-specific judgment calls; the CA watcher is authoritative
> on the mechanics.

## Why this profile is next (of the four)

Evaluations are the underlying inspection record. A Violations record already
carries `evalEvalNum`, and an Evaluations record exposes that SAME field -- so a
violation can be joined back to the evaluation/inspection that found it. Watching
Evaluations gives visibility into new inspections (the event that often precedes a
violation or compliance action) rather than only their downstream consequences. It
has a genuine stable reference number (`evalEvalNum`), moderate volume (N2688 473,
RA 40 -- large enough to matter, small enough not to force a new storage design),
and no cross-stream overlap to untangle. That combination makes it the safest of
the four to build and auto-merge.

## The profile (confirmed live 2026-07-24 -- do not re-discover)

- **Endpoint:** `https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/
  default-mode/profiles/3-compliance/1-evaluations`
- **Query shape:** IDENTICAL to `fetch_site_submissions` / `fetch_site_violations`
  in `nsite_client.py` -- same `queryParams={"filter":[{"id":"<nsite_id>"}]}`
  URL-quoted param, same `responseContentType=application/json&
  includeMetadataInResponse=true&loadChildren=true&filterString=` suffix, same
  `Referer` header pattern, response has a top-level `queryResults` array.
  Confirmed anonymous (no auth) against 5 known site IDs.
- **Field schema** (`eval*` prefix -- 8 fields from the 2026-07-24 sample):

  | Field | Notes |
  |---|---|
  | `evalEvalNum` | **stable reference number** -- the candidate diff key; the SAME field Violations records reference, so it also joins a violation back to its evaluation. See Feasibility gate. |
  | `evalRefProgramAreaDescr` | program area (e.g. "AQD - Air") |
  | `evalRefEvalTypeDescr` | evaluation type |
  | `evalRefEvalCatgDescr` | evaluation category |
  | `evalPrmtNum` | cross-references the Permits profile (`prmtPrmtNum`) |
  | `evalStartDate` | date the evaluation started |
  | `evalSmplTransmtDate` | sample-transmittal date (may be null) |
  | `siteName` | site name echo |

  There is no obvious single "status" field in this profile's sample (unlike
  Violations/Compliance Actions). The primary signal here is a NEW evaluation
  appearing (new `evalEvalNum`); a secondary signal is a date/field advancing on
  an existing one. Confirm during the spike whether a status-like field exists
  across a broader sample.

- **Real counts across the 5 already-known sites** (2026-07-24 snapshot; 5 of 19
  sites polled -- the other 14 are expected mostly 0, matching the Violations
  pattern):

  | Site | Count |
  |---|---|
  | N2688 | 473 |
  | RA | 40 |
  | N1504 | 3 |
  | P1488 | 0 |
  | WRD | 1 |

## Feasibility gate (confirm the broader schema + diff key -- don't re-discover the endpoint)

The endpoint and one sample record are already confirmed. What is NOT yet confirmed:

1. **Is `evalEvalNum` unique within a site's record set?** The hypothesis is that
   it behaves like Submissions' `submSubmRefNum` -- a stable per-evaluation number.
   Test explicitly: fetch Evaluations for N2688 (473 records) and RA (40), and
   check whether `evalEvalNum` values are unique within each site.
   - **If unique:** use it as the diff key (ref-number-keyed, mirroring
     Submissions). Alert on a new evaluation (new `evalEvalNum`) and, if a
     date/field advances on an existing number, on that change.
   - **If NOT unique:** fall back to the full-record `Counter` multiset diff from
     ADR 023 (Violations). Don't force a broken key -- the multiset idiom is proven.
2. **Is the schema stable across all non-zero sites?** Fetch RA (40) and WRD (1)
   and confirm the same field names appear; note any field that is null in some
   records (e.g. `evalSmplTransmtDate`).
3. **Char-budget check (mild).** N2688 has 473 records. A ref-number-keyed minimal
   payload (key -> the handful of fields that change) is very likely well under the
   50,000-char Sheets-cell cap, but MEASURE it (ADR 023 Finding 2). If it exceeds
   the budget, reuse the Violations watcher's `snapshot_char_budget` /
   digest-multiset degradation (`nsite_violations_watcher.py`) rather than inventing
   a new one -- do not use the Submissions watcher's plain `json.dumps` snapshot,
   which has no degradation.
4. **Watch for volatile fields.** Before committing to any diff, fetch the same
   record twice and confirm no field churns between identical fetches (would cause
   false positives). Exclude any that do, same as RIDE excludes `ProjectManager`.

This is a genuine gate, not a formality: if the schema is unstable or `evalEvalNum`
is non-unique in a way that breaks both designs, a draft PR with a written finding
is a legitimate Step-3 stop.

## Approach (adapt, don't re-litigate -- mirror the merged nSITE watchers closely)

A near-straight copy of `nsite_compliance_actions_watcher.py` (or, if that is not
yet as expected, `nsite_violations_watcher.py`) with the field list and diff-key
design adjusted for the feasibility outcome. Carry forward verbatim:

1. **New client fn `fetch_site_evaluations`** in `nsite_client.py`, modeled on
   `fetch_site_violations`. Same retry/error contract: raise, never swallow
   (`NsiteFetchError` transient, `NsiteStructuralError` on shape change).
2. **New watcher `nsite_evaluations_watcher.py`**, modeled on the merged CA/
   Violations watcher's full shape: tiered cadence from `nsite_evaluations.tiers`
   (join the shared `nsite_sites` registry -- do NOT re-copy the 19-site list);
   per-site try/except covering fetch AND Sheets write; batched snapshot read (one
   call before the loop); `alerting_is_configured()` before any fetch/write;
   `_is_due` imported from `nsite_submissions_watcher`; item key `eval:<srn>`;
   baseline silently on first sight.
3. **Cadence tiers for THIS profile -- do not copy another profile's tiers.**
   Assign daily/biweekly/quarterly from the Evaluations-specific counts + recency
   you observe in the gate, using the ADR 021/023 framework. N2688 (473) and RA
   (40) are the only meaningfully active sites in the 5-site sample; most of the
   other 14 will be 0 (quarterly). Document actual assignments + rationale in the ADR.
4. **Config:** `nsite_evaluations: { enabled: false, recipients: [...], tiers: {...} }`
   in `config.yml`, same shape as the violations block. Ships disabled.
5. **Sheet tab:** `Evaluations Watch`, same append-only shape as `Violations Watch`.
6. **Parked workflow:** write `nsite-evaluations-watch.yml` to
   `docs/pending-workflows/` (NOT `.github/workflows/` -- the `workflow` OAuth-scope
   constraint). Add the same enforcing test (`enabled is False OR the workflow is in
   .github/workflows/`). Don't rewrite `docs/pending-workflows/README.md`.
7. **Tests:** mirror `tests/test_nsite_violations.py` -- fetch-error contract,
   snapshot/diff correctness for the chosen design, full `run()` flows (baseline/
   unchanged/new/changed/fetch-fail), tiered `_is_due` reuse/gate, pending-workflow
   enforcing test.
8. **Docs:** new ADR (next unused number -- `ls docs/decisions/ | sort | tail -1`
   on your branch; last merged is 027 at staging time). Title: "nSITE Evaluations
   watch." Cover endpoint, diff-key decision with the real uniqueness finding,
   tier assignments + rationale, snapshot encoding. Update `README.md` / `CLAUDE.md`.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: `evalEvalNum` is non-unique AND full-record multiset diffing is
  unstable** (a date field churns on every fetch -> constant false positives).
  Detection: the double-fetch check in the gate. Mitigation: exclude volatile
  fields from the diffed set, or watch only the stable subset (evalEvalNum +
  program area + type).
- **Manageable: no clear "status" field means the primary signal is new-evaluation
  appearance.** That is fine and high-value (a new inspection is exactly what
  advocacy wants early warning of). Make sure the diff fires on a new `evalEvalNum`
  and, if the gate finds a status-like field, on its change too.
- **Manageable: N2688's 473-record first baseline is large but silent** (first
  sighting baselines with no alert -- standard). No cross-stream join to Violations
  is needed at build time; `evalEvalNum` is the join key IF a future analysis wants
  it, but this watch is self-contained.
- **Residual (accept + note in ADR):** a persistent fetch failure after baseline
  goes skip-and-warn quiet -- the same accepted residual as every other stream.

## Definition of done

Green `pytest -q`; the gate's uniqueness/stability finding recorded in the ADR
(diff design chosen + why, with real data); a new `nsite_evaluations` stream that
is a no-op while `enabled: false`; per-site tiers justified with real observed
data; pending workflow at `docs/pending-workflows/nsite-evaluations-watch.yml` with
its enforcing test; README/CLAUDE.md updated; PR merged per overnight-coder Step 8.
If the gate finds the schema genuinely unworkable, a draft PR with a written finding
is the legitimate done state.

## Then Step 9 (after a successful merge)

Archive `coder:nsite-evaluations-watch` from
`Cowork-claude/documents/overnight-coder-queue.md` to `overnight-coder-archive.md`
with the date, PR number, and one-line outcome. No worker pin to release -- staged
directly. Next of the four: `coder:nsite-complaints-watch` (note its 6392-record
N2688 volume gate -- it is NOT a straight copy).
