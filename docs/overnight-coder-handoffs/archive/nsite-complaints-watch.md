# Overnight-coder handoff -- watch EGLE's nSITE Complaints profile

*Staged 2026-08-08. Read `docs/overnight-coder.md` first -- this is a goal handed
to that loop, not a new procedure. New poller against a live external system, so
it ships `enabled: false`. **The endpoint/field-schema discovery is already done**
(live-queried 2026-07-24 -- see "The profile" below); confirm it against a broader
sample as your Step-3 spike. Branch name suggestion: `nsite-complaints-watch`.*

*This is the 4th of the 6 unpolled nSITE profiles. Unlike Evaluations/Permits it
is NOT a straight copy: N2688 carries 6,392 complaint records, which breaks the
naive "one full snapshot per site in one Sheets cell" assumption. Read the
Feasibility gate before writing any storage code -- the volume decision is the
whole build.*

## Dependency

**`coder:nsite-violations-watch` (PR #36, merged) AND
`coder:nsite-compliance-actions-watch` must both be merged first.** Reuses their
patterns (`NsiteStructuralError`/`NsiteFetchError`, per-site try over the Sheets
write, batched tab read, `alerting_is_configured()`, `_is_due` imported, shared
`nsite_sites` registry, `docs/pending-workflows/` mechanism + enforcing test).
After rebasing, `grep -rl "NsiteStructuralError" .` -- empty means a dependency
didn't land; stop and note it.

> **STALE-FRAMING GUARD (written BEFORE the CA build ran).** Where this handoff and
> the actual merged `nsite_compliance_actions_watcher.py` disagree on a shared
> pattern, **the merged code wins** -- rebase onto it and follow it. This handoff is
> authoritative on the schema (fixed) and the volume decision; the merged watchers
> are authoritative on mechanics.
>
> **CRITICAL, also stale-prone:** the `nsite-six-unpolled-profiles-schemas` memory
> says Complaints "likely reuses `_normalize_submission`'s shape almost verbatim."
> That is true of the FIELD shape (`subm*` prefix) but NOT of the STORAGE mechanism.
> The Submissions watcher (`nsite_submissions_watcher.py`) serializes its snapshot
> with a plain `json.dumps(snap, ...)` and has NO char-budget/digest degradation.
> At 6,392 records that plain snapshot blows the 50,000-char Sheets-cell cap on the
> first baseline. Do NOT copy Submissions' storage. See the Feasibility gate.

## Why this profile (and the volume caveat up front)

Complaints are citizen/agency reports against a facility -- direct evidence of
community concern and often the trigger for an inspection. For Arbor Hills (N2688)
this is the single highest-count profile in the whole nSITE set (6,392), which is
both why it matters and why it needs care. That 6,392 is a HISTORICAL ARCHIVE; what
matters operationally is the RATE of NEW complaints, not re-diffing 6,392 old ones
every run. That reframing drives the design: a "new complaint filed" tripwire, not
a full per-record snapshot.

## The profile (confirmed live 2026-07-24 -- do not re-discover)

- **Endpoint:** `https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/
  default-mode/profiles/2-environmental-interests/3-complaints`
- **Query shape:** IDENTICAL to `fetch_site_submissions` (same query-param
  encoding, same suffix, same `Referer`, top-level `queryResults` array; anonymous).
- **Field schema** (`subm*` prefix -- SAME field NAMES as Submissions, 4 fields
  from the 2026-07-24 sample):

  | Field | Notes |
  |---|---|
  | `submSubmRefNum` | **stable reference number** -- same field Submissions uses; the candidate diff key |
  | `submRefFormTypeDescr` | form type -- reads "Complaint" for this profile |
  | `submRefProgramAreaDescr` | program area (e.g. "AQD - Air") |
  | `submRcvdDate` | date the complaint was received |

- **Real counts across the 5 already-known sites** (2026-07-24 snapshot):

  | Site | Count |
  |---|---|
  | N2688 | **6,392** |
  | RA | 5 |
  | N1504 | 0 |
  | P1488 | 0 |
  | WRD | 0 |

  N2688 is the entire volume problem; every other site is trivially small. A
  per-site design that handles N2688 handles all 19.

## Feasibility gate (this IS the build -- the storage decision, not the endpoint)

The endpoint and field shape are confirmed. The real question is storage + diff
design at N2688's scale.

1. **Measure the serialized size of N2688's full complaint set FIRST.** Fetch all
   6,392 records; serialize under each candidate encoding and print the char count
   against `HARD_SHEETS_CELL_LIMIT = 50000`:
   - Full per-record dict: ~hundreds of KB -- fails hard.
   - Ref-number-keyed minimal (`{submSubmRefNum: [status/date]}`): even the KEYS
     alone (6,392 x ~12-15 chars) are ~80-96K -- also fails the single-cell cap.
   - **Conclusion you should expect to confirm empirically:** no full-membership
     encoding of N2688 Complaints fits one 50K cell. Confirm it with the real number
     rather than assuming.
2. **Therefore choose a COUNT+HASH tripwire design, not full-membership diffing.**
   The recommended design (confirm it survives the spike, then record it in the ADR):
   - Store per site a small snapshot: `{"n": <count>, "hash": <sha256 of the sorted
     submSubmRefNum set>, "latest": <most-recent submRcvdDate + its ref nums>}`.
     This is O(100) chars regardless of 6,392.
   - Diff fires when `n` increases OR `hash` changes (a complaint added/removed) OR
     a new ref number appears in `latest`.
   - On a fire, the alert says "N new complaint(s) at <site>" and lists the NEW ref
     numbers + received dates (computable by set-diffing the current ref-number set
     against the stored hash's source set -- keep the previous ref-number set in a
     SECOND cell if needed, since it is 6,392 x 15 chars ~= 96K and itself needs the
     Violations digest-multiset treatment OR a hash-only comparison that detects
     change without naming the record). **Decide explicitly** which of these you
     ship: (a) name-the-new-complaint (needs the prior ref set stored, so needs the
     Violations `snapshot_char_budget` degradation from `nsite_violations_watcher.py`
     applied to the ref-set cell), or (b) count-only tripwire (store just n+hash;
     alert "complaint count changed A -> B", human re-fetches to see which). (b) is
     smaller and simpler; (a) is more useful. Recommend (a) if the ref-set fits under
     budget with the digest degradation, else (b).
3. **Do NOT reuse the Submissions plain-`json.dumps` snapshot.** Reuse the
   Violations watcher's `_cell_payload` / `snapshot_char_budget` / digest-multiset
   machinery if you store any per-record set; it already has the hash-only final
   clamp for exactly this "too many distinct rows" case (ADR 023).
4. **Schema stability:** RA (5 records) is the only other non-zero site -- confirm
   the same 4 field names. Double-fetch N2688 to confirm no field churns.

This is a genuine STOP gate: if neither (a) nor (b) yields a design that both fits
the Sheets cell AND reliably detects a new complaint without false positives at
6,392 records, a draft PR with the measured sizes + a written finding (e.g. "N2688
Complaints needs a paged/multi-cell store -- larger than an overnight build") is the
correct done state. Say so; do not force a broken snapshot.

## Approach (once the storage design is settled)

Same skeleton as the other nSITE watchers, differing only in the snapshot/diff:

1. **`fetch_site_complaints`** in `nsite_client.py` (modeled on
   `fetch_site_submissions`; raise-never-swallow contract).
2. **`nsite_complaints_watcher.py`** -- tiered cadence from `nsite_complaints.tiers`
   (join `nsite_sites`, don't re-copy); per-site try over fetch + write; batched
   read; `alerting_is_configured()`; `_is_due` imported; item key `cmplt:<srn>`
   (NB: distinct from Compliance Actions' `ca:<srn>` -- do not collide); baseline
   silently. The snapshot/diff functions implement the count+hash design from the gate.
3. **Tiers:** N2688 has volume but complaints arrive at a human rate -- daily is
   defensible for N2688, quarterly for the near-zero sites. Justify from the
   NEW-complaint rate you can infer from `submRcvdDate` spread, not the raw 6,392.
4. **Config:** `nsite_complaints: { enabled: false, recipients: [...], tiers: {...} }`.
   Ships disabled.
5. **Sheet tab:** `Complaints Watch`. If you store a prior-ref-set cell, keep it in
   its own column, budget-degraded.
6. **Parked workflow:** `nsite-complaints-watch.yml` in `docs/pending-workflows/` +
   the enforcing test.
7. **Tests:** mirror `tests/test_nsite_violations.py`, PLUS an explicit
   large-volume test (synthesize 6,392+ records, assert the snapshot stays under
   50K and a single added complaint is detected). This is the test that matters here.
8. **Docs:** new ADR (`ls docs/decisions/ | sort | tail -1`; last merged 027 at
   staging). Title: "nSITE Complaints watch." Lead with the volume decision + the
   measured sizes + which of design (a)/(b) shipped and why. A deliberate note on the
   6,392 figure (per the memory) belongs in the ADR. Update `README.md`/`CLAUDE.md`.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: the snapshot silently exceeds 50K and the Sheets write fails (or
  truncates) mid-run, corrupting the baseline.** Detection: the char-count assertion
  in the large-volume test + the `HARD_SHEETS_CELL_LIMIT` guard. Mitigation: the
  count+hash design keeps the primary snapshot tiny by construction; any per-record
  cell uses the Violations budget degradation, which never writes over the cap.
- **Show-stopper: 6,392 baseline "new" complaints flood on first enable.** Mitigation:
  first sighting baselines SILENTLY (standard, but call it out explicitly here given
  the count -- verify no alert fires on the baseline path at this scale in a test).
- **Manageable: count-only design (b) can't name the new complaint.** Accept + note:
  the alert points a human at the site to see which; the tripwire (something new
  arrived) is the high-value part. Prefer design (a) if it fits budget.
- **Manageable: a removed/withdrawn complaint changes the hash and fires a "change"
  with no new ref number.** Mitigation: compare counts (n went DOWN) and label it a
  removal, not a new complaint, so the alert copy is accurate.
- **Residual (accept + note in ADR):** persistent fetch failure after baseline goes
  skip-and-warn quiet -- standard across every stream.

## Definition of done

Green `pytest -q` incl. the large-volume test; the measured N2688 serialized sizes
and the chosen design ((a) or (b)) recorded in the ADR; a `nsite_complaints` stream
that is a no-op while `enabled: false` and provably does not flood on baseline;
per-site tiers justified by new-complaint rate; pending workflow +
enforcing test; README/CLAUDE.md updated; PR merged per Step 8. A measured "needs a
bigger store than an overnight build" finding in a draft PR is a legitimate done state.

## Then Step 9 (after a successful merge)

Archive `coder:nsite-complaints-watch` from
`Cowork-claude/documents/overnight-coder-queue.md` to `overnight-coder-archive.md`.
No worker pin to release. Next of the four: `coder:nsite-permits-watch` (small
counts, back to a straight copy).
