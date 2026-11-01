# Overnight-coder handoff — extract a shared nSITE site registry (groundwork for Violations + 5 more profiles)

*Staged 2026-07-24. Read `docs/overnight-coder.md` first — this is a goal handed to
that loop, not a new procedure. Pure refactor, no new capability — the "ships
disabled" rule does not apply here (there is nothing new to gate); instead it must
be verified **behavior-preserving** against the live config before merging, per
Step 3's "change to an already-live path" rule. Branch name suggestion:
`nsite-site-registry`.*

## Why this exists

`config.yml`'s `nsite_submissions.sites` (added 2026-07-24, ADR 021) is a 19-entry
list of `{srn, name, id, poll}` tuples — every nSITE site ID resolved from Trisha's
MiEnviro Portal subscriptions. It is read by `nsite_submissions_watcher.py`
(**already `enabled: true`, live in production**).

The next planned build (`coder:nsite-violations-watch`, staged right after this
item in the queue — see its handoff once this lands) polls a DIFFERENT nSITE
profile (Violations) for the SAME 19 sites, at its own cadence. Copy-pasting the
19 `{srn, name, id}` tuples into a second `nsite_violations.sites` list — and then
a third, fourth, fifth, sixth time for the remaining unpolled profiles (Compliance
Actions, Complaints, Active Public Notices, Evaluations, Permits — see
`wrd-jpa-coverage-gap`/`tiered-submissions-polling-adr021` project memory for the
full backlog) — means a site's name or ID correction has to be applied in up to 7
places, and WILL drift eventually. This was flagged explicitly in ADR 021's
Alternatives/close-out: *"if a future session adds a watch for one of the other 6
unpolled nSITE profiles, extract the 19 (srn, name, id) tuples into a single shared
site registry FIRST, before that second profile-watcher duplicates the list a third
time."* This build is that extraction, done once, before Violations needs it.

## Goal

Refactor `config.yml` so site **identity** (srn/name/id) lives in exactly ONE
place, and each profile-specific watcher (Submissions today; Violations and
eventually the rest) references it by `srn` with only its own `poll` cadence
override. Cadence is legitimately profile-specific (a site could reasonably be
daily for Submissions but quarterly for Violations) — do NOT try to unify that;
only the identity tuple should be shared.

### Target config shape

```yaml
# --- nSITE site registry (ADR 022) ---
# Canonical identity (srn/name/id) for every nSITE site this monitor knows about,
# shared across every profile-specific watch (Submissions today; more as they're
# added). A site's `poll` cadence is set PER PROFILE below, not here — it can
# legitimately differ (e.g. daily Submissions checks, quarterly Violations checks
# for the same site).
nsite_sites:
  - {srn: "RA",    name: "Arbor Hills Remediation Area",             id: "-714792003991405124"}
  - {srn: "WRD",   name: "GFL-Arbor Hills Landfill-Washtenaw Co",    id: "306291952280313698"}
  # ... all 19, names/ids copied VERBATIM from the current nsite_submissions.sites
  # (do not "clean up" or re-derive any name/id — this is a pure move, not a rewrite)

nsite_submissions:
  enabled: true
  recipients:
    - arbor-hills@trishakunst.com
  tiers:            # srn -> poll cadence; keys MUST be a subset of nsite_sites' srns
    RA: daily
    WRD: daily
    N1504: daily
    P1488: daily
    N2688: daily
    AHLI: daily
    P1504: biweekly
    AHLA: biweekly
    # ... all 19, poll values copied VERBATIM from the current nsite_submissions.sites
```

`nsite_submissions_watcher.py`'s `run()` must resolve the working site list by
joining `nsite_sites` (by srn) with `nsite_submissions.tiers`, e.g.:

```python
registry = {s["srn"]: s for s in cfg["nsite_sites"]}
sites = [
    {**registry[srn], "poll": poll}
    for srn, poll in (cfg["nsite_submissions"].get("tiers") or {}).items()
]
```

If a `tiers` srn isn't in the registry, that's a config error — let it raise
naturally (a `KeyError` is fine; don't add defensive handling for a config
mistake that would be caught immediately by the first run or by a test).

## This is a live-path refactor — verify behavior-preserving, don't just ship mocked-green

`nsite_submissions.enabled: true` is live. Per `overnight-coder.md` Step 3, a
change to an already-live path needs real-specimen verification before an
autonomous merge — but this refactor's specific safety property is narrower and
easier to check than a normal live-path change: **prove the resolved site set is
byte-for-byte identical before and after**, for every srn, at every cadence. Do
this concretely:

1. Before touching `config.yml`, dump the current `nsite_submissions.sites` list
   (19 entries) verbatim somewhere you can diff against (a scratch file, not
   committed).
2. After the refactor, reconstruct the same list by joining `nsite_sites` +
   `nsite_submissions.tiers` in code (a one-off script is fine, scratchpad only)
   and diff it against the pre-refactor dump. It must match exactly — same 19
   srns, same names, same ids, same poll tiers, same order doesn't matter but
   the SET must be identical.
3. Also re-run the existing `_is_due` tests (unchanged — this refactor must not
   touch `_is_due` itself, only how `run()` builds the `sites` list it iterates)
   and confirm `pytest -q` is still fully green with ZERO test changes needed to
   the `_is_due`-specific tests (if you find yourself editing those, you've
   changed more than the config-loading path — stop and reconsider).
4. State this equivalence check explicitly in the PR description and the new
   ADR (see below) — this is the evidence that makes an autonomous merge of a
   live-path change safe here.

Do **not** flip `nsite_submissions.enabled` either direction — its value carries
through unchanged. Do **not** touch `facilities:` (the Documents pipeline) — it's
a separate, unrelated list and out of scope for this refactor.

## Also update

- `tests/test_nsite_submissions.py` — the test config fixtures (`SITES`,
  `SUB_CFG`) currently build `nsite_submissions.sites` directly; update them to
  build `nsite_sites` + `nsite_submissions.tiers` instead, matching the new
  shape. The `_wire()` helper's srn-lookup logic will need the same join. Every
  existing test should still pass with equivalent (not necessarily identical)
  fixture data — don't weaken any assertion to make this land.
- `README.md` / `CLAUDE.md` — update the `nsite_submissions.sites` references
  added by ADR 021 to describe the new `nsite_sites` + `nsite_submissions.tiers`
  split.
- New **ADR 022** — "nSITE site registry — shared identity across profile
  watches": why (duplication risk flagged in ADR 021), the target shape, the
  behavior-preservation verification you ran (with its actual result), and that
  this is groundwork for `coder:nsite-violations-watch` and the remaining
  backlog (Compliance Actions, Complaints, Active Public Notices, Evaluations,
  Permits).

## Adversarial review (mitigations to build in, not just note)

- **Show-stopper: the refactor silently drops or mis-tiers a site.** Mitigation:
  the byte-for-byte equivalence check above is exactly designed to catch this
  before merge — do not skip it or treat it as optional.
- **Manageable: a future profile's `tiers` references an srn not in
  `nsite_sites`.** Mitigation: let it raise (`KeyError`) rather than silently
  skipping the site — a loud failure on a config typo is correct; a silently
  unwatched site is not.
- **Manageable: scope creep into "cleaning up" names/ids while moving them.**
  Mitigation: this handoff explicitly says copy verbatim — resist the urge to
  fix a typo you notice in a name (`"Arbor Hills Composting Faciltiy"` is
  misspelled in the live config, for example) as part of this PR; that's a
  separate, tiny follow-up if Trisha wants it, not bundled into a refactor PR
  where it would muddy the equivalence check.

## Definition of done

Green `pytest -q`; `nsite_sites` + `nsite_submissions.tiers` replace
`nsite_submissions.sites` with a documented, stated-in-the-PR equivalence check
showing the resolved 19-site set is unchanged; ADR 022 written; README/CLAUDE.md
updated; PR merged per overnight-coder Step 8 with a closing comment stating
plainly that this is pure groundwork (no user-visible behavior change) and that
`coder:nsite-violations-watch` is now unblocked.

## Then Step 9 (after a successful merge)

This item did not come from the worker queue (no worker pin to release) — it was
staged directly by a live session at Trisha's request. Just archive
`coder:nsite-site-registry` from `Cowork-claude/documents/overnight-coder-queue.md`
to `overnight-coder-archive.md` per Step 9, and update
`coder:nsite-violations-watch`'s `Dependency:` line in the queue from "this item
merged" to `null` (it's now ready to run the following night). Commit those
Lotext files locally by explicit path, never push.
