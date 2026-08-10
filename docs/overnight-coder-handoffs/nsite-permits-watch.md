# Overnight-coder handoff -- watch EGLE's nSITE Permits profile

*Staged 2026-08-08. Read `docs/overnight-coder.md` first -- this is a goal handed
to that loop, not a new procedure. New poller against a live external system, so
it ships `enabled: false`. **The endpoint/field-schema discovery is already done**
(live-queried 2026-07-24 -- see "The profile"); confirm it against a broader sample
as your Step-3 spike. Branch name suggestion: `nsite-permits-watch`.*

*This is the 5th of the 6 unpolled nSITE profiles. Small counts everywhere (2-9
per site) make it a straight copy of the other nSITE watchers -- the one real
subtlety is that the watch value is STATUS/DATE changes on existing permits, not
just new permit numbers.*

## Dependency

**`coder:nsite-violations-watch` (PR #36, merged) AND
`coder:nsite-compliance-actions-watch` must both be merged first.** Reuses their
patterns (`NsiteStructuralError`/`NsiteFetchError`, per-site try over the Sheets
write, batched read, `alerting_is_configured()`, `_is_due` imported, shared
`nsite_sites` registry, `docs/pending-workflows/` mechanism + enforcing test).
After rebasing, `grep -rl "NsiteStructuralError" .` -- empty means a dependency
didn't land; stop and note it.

> **STALE-FRAMING GUARD (written BEFORE the CA build ran).** Where this handoff and
> the merged `nsite_compliance_actions_watcher.py` disagree on a shared pattern,
> **the merged code wins** -- rebase onto it and follow it. This handoff is
> authoritative on the schema; the merged watchers on the mechanics.

## Why this profile

A permit's lifecycle (issued -> extended -> expiring -> terminated) is a status
signal about a facility's regulatory standing. For the Arbor Hills air facilities
the important permit events (ROP renewals entering a public-comment window) are
ALREADY watched by Stream H (`rop_client.py` / `rop_watcher.py`, ADR 017) via a
different, targeted mechanism. This nSITE Permits profile is BROADER (every permit
type across all 19 sites), so it is complementary rather than redundant -- but see
the overlap note in the adversarial section so you do not double-alert on ROP
renewals. Lower urgency than Violations/Compliance Actions (a permit is not itself
bad news), hence 3rd of the four.

## The profile (confirmed live 2026-07-24 -- do not re-discover)

- **Endpoint:** `https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/
  default-mode/profiles/2-environmental-interests/1-permits`
- **Query shape:** IDENTICAL to `fetch_site_submissions` / `fetch_site_violations`
  (same encoding/suffix/`Referer`, top-level `queryResults`, anonymous).
- **Field schema** (`prmt*` prefix -- 7 fields from the 2026-07-24 sample):

  | Field | Notes |
  |---|---|
  | `prmtPrmtNum` | **stable reference number** (e.g. "ROP0000224") -- the candidate diff key |
  | `prmtRefPrmtStatDescr` | status (e.g. "Extended") -- **the primary watch field** |
  | `prmtRefPrmtCatgDescr` | permit category |
  | `prmtPrmtTypeDescr` | permit type |
  | `prmtEfctvDate` | effective date |
  | `prmtExprDate` | expiration date |
  | `prmtTermDate` | termination date (often null until terminated) |

- **Real counts across the 5 already-known sites** (2026-07-24 snapshot):

  | Site | Count |
  |---|---|
  | N2688 | 9 |
  | RA | 2 |
  | N1504 | 4 |
  | P1488 | 3 |
  | WRD | 2 |

  Small everywhere -- char budget is a non-issue; a full per-record snapshot fits
  the 50K cell many times over. No degradation machinery needed (but if you copy the
  Violations watcher, its budget code is harmless at this volume).

## Feasibility gate (quick -- confirm the diff key + that status changes are captured)

1. **Is `prmtPrmtNum` unique within a site?** Fetch N2688 (9) and N1504 (4) and
   confirm. It almost certainly is (permit numbers are canonical). If unique, use
   ref-number-keyed; if somehow not, fall back to the ADR 023 multiset.
2. **The diff MUST capture status/date changes on an EXISTING `prmtPrmtNum`, not
   just a new number appearing.** This is the actual signal (a permit going
   Extended -> Expired, or a `prmtTermDate` populating). If ref-number-keyed, store
   and diff `prmtRefPrmtStatDescr` + the three dates per key -- do not store only
   the set of numbers. Test this explicitly with a synthetic status flip.
3. **Volatile-field check:** double-fetch a record; confirm no date/field churns
   between identical fetches. Exclude any that do.
4. **Schema stability:** confirm the 7 field names across P1488 (3) and WRD (2).

## Approach (straight copy -- mirror the merged nSITE watchers)

1. **`fetch_site_permits`** in `nsite_client.py` (modeled on `fetch_site_violations`;
   raise-never-swallow).
2. **`nsite_permits_watcher.py`** -- tiered cadence from `nsite_permits.tiers` (join
   `nsite_sites`, don't re-copy); per-site try over fetch + write; batched read;
   `alerting_is_configured()`; `_is_due` imported; item key `prmt:<srn>`; baseline
   silently. Ref-number-keyed diff storing status + the three dates per
   `prmtPrmtNum`, firing on a new number OR a status/date change on an existing one.
3. **Tiers:** permit events are slow -- a biweekly or quarterly cadence is defensible
   for most sites; N2688 (the primary advocacy concern) can be daily if you want the
   fastest signal on its ROP0000224. Justify from the low change-rate in the ADR.
4. **Config:** `nsite_permits: { enabled: false, recipients: [...], tiers: {...} }`.
   Ships disabled.
5. **Sheet tab:** `Permits Watch`, append-only, same shape as `Violations Watch`.
6. **Parked workflow:** `nsite-permits-watch.yml` in `docs/pending-workflows/` + the
   enforcing test.
7. **Tests:** mirror `tests/test_nsite_violations.py`, with a specific test for a
   status change on an existing permit (Extended -> Expired) and a `prmtTermDate`
   populating -- the signal that distinguishes this profile.
8. **Docs:** new ADR (`ls docs/decisions/ | sort | tail -1`; last merged 027 at
   staging). Title: "nSITE Permits watch." Cover the diff design (status-on-existing,
   not just new-number), the ROP-overlap relationship (see below), tiers. Update
   `README.md`/`CLAUDE.md`.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper-ish / overlap: double-alerting on ROP renewals already covered by
  Stream H.** N2688/N1504/P1488 ROP permits (ROP0000224/0656/0236) appear in BOTH
  this Permits profile and Stream H's targeted ROP watch. Detection: obvious from the
  `prmtPrmtNum` values matching the ROP numbers. Mitigation: this profile watches
  permit STATUS/lifecycle (Extended/Expired/Terminated), which is a DIFFERENT event
  than Stream H's public-comment-window trip-wire, so overlapping alerts describe
  different things and are not true duplicates -- but SAY THIS in the ADR and in the
  alert copy ("permit status change", not "public comment") so a reader is not
  confused into thinking one supersedes the other. Do NOT silently suppress ROP
  permits from this profile (that would blind it to a real status change); disambiguate
  in the copy instead.
- **Manageable: watching only new permit numbers would miss the real signal.**
  Mitigation: the gate mandates diffing status + dates per existing key.
- **Manageable: null `prmtTermDate` -> populated is the "terminated" signal.**
  Mitigation: treat null->value on any of the three dates as a change, not just
  string inequality on a present value.
- **Residual (accept + note in ADR):** persistent fetch failure after baseline goes
  skip-and-warn quiet -- standard across every stream.

## Definition of done

Green `pytest -q` incl. the status-change test; diff design recorded in the ADR
(status-on-existing, ROP-overlap disambiguation); a `nsite_permits` stream that is a
no-op while `enabled: false`; tiers justified; pending workflow + enforcing test;
README/CLAUDE.md updated; PR merged per Step 8.

## Then Step 9 (after a successful merge)

Archive `coder:nsite-permits-watch` from
`Cowork-claude/documents/overnight-coder-queue.md` to `overnight-coder-archive.md`.
No worker pin to release. Last of the four:
`coder:nsite-active-public-notices-watch` (very sparse data + a real ROP-overlap
DESIGN decision -- likely a draft-PR-for-review, not an autonomous merge).
