# Overnight-coder handoff -- watch EGLE's nSITE Active Public Notices profile

*Staged 2026-08-08. Read `docs/overnight-coder.md` first -- this is a goal handed
to that loop, not a new procedure. New poller against a live external system, so
it ships `enabled: false`. **The endpoint/field-schema discovery is already done**
(live-queried 2026-07-24 -- see "The profile"); confirm it against a broader sample
as your Step-3 spike. Branch name suggestion: `nsite-active-public-notices-watch`.*

*This is the 6th and LAST of the unpolled nSITE profiles. It is the LEAST safe to
autonomously merge, for two reasons baked into the data: (1) it overlaps
`rop_client.py`'s existing statewide public-notice scrape (ADR 017), which is a real
design decision -- not a mitigation -- and (2) the sample has only ONE record total
across the 5 known sites, so the Step-3 feasibility signal is inherently weak.
**Expect to STOP as a draft PR for human review**, not auto-merge (see Definition
of done).*

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
> authoritative on the schema and the two open decisions below; the merged watchers
> on the mechanics.

## Why this profile (and why it is different)

Active Public Notices are the formal comment-window announcements (permit renewals,
draft permits, hearings). For advocacy these are high-value -- a public-comment
window is an actionable deadline. BUT the highest-value ones for the Arbor Hills air
facilities (the ROP renewal comment windows) are ALREADY watched by Stream H
(`rop_client.py`, ADR 017) through a statewide `ROP_Public_Notice.pdf` / CSV
mechanism. So this profile's marginal value is the NON-ROP notices (other program
areas, other notice types) and per-site coverage the statewide scrape may miss --
which is exactly what the two open decisions below are about.

## The profile (confirmed live 2026-07-24 -- do not re-discover)

- **Endpoint:** `https://mienviro.michigan.gov/nsite/ss/api/nsite-explorer/
  default-mode/profiles/1-profile/2-active-public-notices`
- **Query shape:** IDENTICAL to `fetch_site_submissions` (same encoding/suffix/
  `Referer`, top-level `queryResults`, anonymous).
- **Field schema** (`publicNotif*` prefix -- 6 fields from the single 2026-07-24
  P1488 sample):

  | Field | Notes |
  |---|---|
  | `publicNotifPnurl` | an `<a>` HTML tag wrapping a download URL that embeds a long +/- integer ID (e.g. `.../publicnotice/info/-2510414165377867817/details`). **Regex-extract that ID as the candidate diff key** -- same idiom this codebase uses to pull a doc_id out of a URL elsewhere. |
  | `publicNotifExtrnlPublNoticeNum` | null in the one sample seen -- may populate for other program areas/notice types; do not rely on it as the key without confirming |
  | `publicNotifRefPublicNotifCovrg` | notice coverage/scope |
  | `publicNotifStartDate` | comment-window start |
  | `publicNotifEndDate` | comment-window end (**the actionable deadline**) |
  | `publicNotifComments` | free text, can be LONG (the P1488 sample was a full ROP renewal comment-period description) |

- **Real counts across the 5 already-known sites** (2026-07-24 snapshot):

  | Site | Count |
  |---|---|
  | N2688 | 0 |
  | RA | 0 |
  | N1504 | 0 |
  | P1488 | 1 |
  | WRD | 0 |

  ONE record total. This is the weak-signal problem: there is almost nothing to test
  the schema/diff-key against, and the one record is itself a ROP notice (the overlap
  problem). A broader 19-site fetch may still find near-zero.

## Two OPEN DECISIONS to SURFACE, not resolve autonomously

These are the reason this profile stops for review. Put both in the PR body as
explicit questions for Trisha; do not pick an answer and auto-merge.

1. **ROP overlap (ADR 017): build standalone, dedupe, or scope to non-ROP?**
   - *Standalone* (poll this profile for all sites, alert on everything): simplest,
     but will double-alert on the P1488/N2688/N1504 ROP renewal windows that Stream H
     already emails -- redundant mail on the single highest-traffic notice type.
   - *Dedupe against Stream H*: suppress a nSITE notice whose window matches an
     already-alerted ROP notice. More correct, more coupling, needs a shared key
     between two mechanisms that were not designed to share one.
   - *Scope to non-ROP program areas only*: this profile's marginal value is exactly
     the notices Stream H does NOT cover, so filtering `publicNotifRefProgramArea`-
     style to non-AQD-ROP could be the cleanest -- but confirm such a field exists and
     is reliable before depending on it.
   Recommend presenting all three with a lean toward "scope to non-ROP" IF a reliable
   program-area filter exists, else "standalone with clear 'may duplicate Stream H'
   alert copy." Let Trisha choose.
2. **Is one sample enough to commit a diff key?** The `publicNotifPnurl` regex-ID is
   the only viable key seen, from ONE record. Fetch all 19 sites in the spike; if you
   still have 0-1 records, state plainly that the key/schema is UNVERIFIED at any real
   sample size and that enabling this profile is a bet, not a confirmed build.

## Feasibility gate (weak by construction -- report that honestly)

1. **Fetch all 19 sites** (not just the 5 known) to gather every Active Public Notice
   that exists right now. Record the real total. If it is 0-1, say so.
2. **Confirm the `publicNotifPnurl` regex extracts a stable ID** from each record
   found. Confirm whether `publicNotifExtrnlPublNoticeNum` ever populates (a cleaner
   key if it does).
3. **Char budget:** trivial at this volume, but `publicNotifComments` can be long --
   if you store it, truncate/hash it so a verbose comment field cannot blow the cell.
4. **This is explicitly a weak-evidence gate.** With ~1 record, you cannot confirm
   uniqueness or schema stability the way you could for Evaluations. The correct
   Step-3 outcome is a draft PR documenting what little the data supports, NOT an
   autonomous merge of an unverified diff design. Say the evidence is thin.

## Approach (build it, but hold it for review)

Same skeleton as the other nSITE watchers:

1. **`fetch_site_public_notices`** in `nsite_client.py` (raise-never-swallow).
2. **`nsite_public_notices_watcher.py`** -- tiered cadence from
   `nsite_public_notices.tiers` (join `nsite_sites`); per-site try over fetch + write;
   batched read; `alerting_is_configured()`; `_is_due` imported; item key
   `pubntc:<srn>`; baseline silently. Diff on the regex-extracted URL ID; store
   `publicNotifEndDate` so the alert can lead with the deadline; truncate/hash
   `publicNotifComments`.
3. **Tiers:** near-zero activity -> quarterly is fine for most sites; the air
   facilities (N2688/N1504/P1488) can be more frequent since their ROP windows are
   the live ones.
4. **Config:** `nsite_public_notices: { enabled: false, recipients: [...], tiers: {...} }`.
   Ships disabled AND stays disabled pending Trisha's answer to the two open decisions.
5. **Sheet tab:** `Public Notices Watch`.
6. **Parked workflow:** `nsite-active-public-notices-watch.yml` in
   `docs/pending-workflows/` + the enforcing test.
7. **Tests:** mirror `tests/test_nsite_violations.py`; because live data is ~1 record,
   lean on SYNTHETIC fixtures (a new notice appears, a window closes, a long comment
   field, the regex-ID extraction, a ROP-vs-non-ROP notice) rather than real-sample
   coverage. Note the fixtures are synthetic in the ADR.
8. **Docs:** new ADR (`ls docs/decisions/ | sort | tail -1`; last merged 027 at
   staging). Title: "nSITE Active Public Notices watch." Lead with the two open
   decisions + the weak-evidence caveat; document the ROP-overlap relationship in
   detail (cross-link ADR 017). Update `README.md`/`CLAUDE.md`.

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: double-alerting on ROP comment windows already handled by Stream
  H.** This is Open Decision 1, not a footnote -- surface it, do not silently
  resolve. Whichever option Trisha picks, the alert copy must make clear this is the
  nSITE per-site notice, distinct from Stream H, so the two are not read as one.
- **Show-stopper: committing an unverified diff key from a single sample.** Detection:
  the 19-site fetch in the gate. Mitigation: hold for review (draft PR) rather than
  auto-merge; label the schema/key UNVERIFIED-at-scale.
- **Manageable: `publicNotifExtrnlPublNoticeNum` is null now but may be the real key
  later.** Mitigation: prefer it when present, fall back to the URL-ID regex; note
  the ambiguity so a future record that populates it is handled.
- **Manageable: a long `publicNotifComments` blows the cell.** Mitigation: truncate/
  hash it in the stored snapshot; keep the full text only in the alert email.
- **Residual (accept + note in ADR):** persistent fetch failure after baseline goes
  skip-and-warn quiet -- standard across every stream.

## Definition of done

Green `pytest -q` (mostly synthetic fixtures); the 19-site real total recorded; the
two open decisions written up as explicit questions in the PR body; a
`nsite_public_notices` stream that is a no-op while `enabled: false`. **The expected
done state here is a DRAFT PR held for Trisha's review** -- the ROP-overlap design
choice and the thin evidence make an autonomous merge inappropriate for this profile
specifically (per overnight-coder Step 8's "stop for a human on a genuine design
decision"). If the 19-site fetch surprises with real volume and no ROP overlap, a
normal merge is fine -- but default to hold.

## Then Step 9 (after merge or a held draft PR)

Archive `coder:nsite-active-public-notices-watch` from
`Cowork-claude/documents/overnight-coder-queue.md` to `overnight-coder-archive.md`
(note "held as draft PR for review" if that is the outcome). No worker pin to
release. This completes all 6 nSITE profiles -- note in the archive that the nSITE
profile build-out is DONE.
