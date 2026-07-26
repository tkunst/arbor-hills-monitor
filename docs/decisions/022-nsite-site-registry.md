# ADR 022 — nSITE site registry: shared identity across profile watches

Date: 2026-07-26
Status: accepted
Builds on: ADR 020 (Stream K, the Submissions watch), ADR 021 (tiered
Submissions polling, the source of the 19-entry list this ADR extracts)

## Context

ADR 021 closed out with an explicit flag: *"if a future session adds a watch
for one of the other 6 unpolled nSITE profiles, extract the 19 (srn, name,
id) tuples into a single shared site registry FIRST, before that second
profile-watcher duplicates the list a third time."* `nsite_submissions.sites`
(19 `{srn, name, id, poll}` tuples, all of Trisha's MiEnviro Portal
subscriptions resolved to real nSITE site IDs) is read by
`nsite_submissions_watcher.py` (`nsite_submissions.enabled: true`, already
live in production).

That flag is now live: `coder:nsite-violations-watch` — a watch on the
Violations profile for the same 19 sites, at its own cadence — is queued
right after this item. Copy-pasting the 19 tuples into a second
`nsite_violations.sites` list, then a third/fourth/fifth/sixth time for the
remaining unpolled profiles (Compliance Actions, Complaints, Active Public
Notices, Evaluations, Permits — see the `nsite-six-unpolled-profiles-schemas`
project memory for the full backlog), means a site's name or ID correction
has to be applied in up to 7 places and will drift eventually. This build is
that extraction, done once, before Violations needs it.

## Decision

### 1. Site identity moves to a top-level `nsite_sites` registry; cadence stays per-profile

```yaml
nsite_sites:
  - {srn: "RA",    name: "Arbor Hills Remediation Area",             id: "-714792003991405124"}
  - {srn: "WRD",   name: "GFL-Arbor Hills Landfill-Washtenaw Co",    id: "306291952280313698"}
  # ... all 19, names/ids copied VERBATIM from the pre-refactor nsite_submissions.sites

nsite_submissions:
  enabled: true
  recipients: [...]
  tiers:            # srn -> poll cadence; keys MUST be a subset of nsite_sites' srns
    RA: daily
    WRD: daily
    # ... all 19, poll values copied VERBATIM from the pre-refactor nsite_submissions.sites
```

`nsite_submissions_watcher.py`'s `run()` resolves the working site list by
joining the two:

```python
registry = {s["srn"]: s for s in cfg["nsite_sites"]}
sites = [
    {**registry[srn], "poll": poll}
    for srn, poll in (cfg["nsite_submissions"].get("tiers") or {}).items()
]
```

Cadence is deliberately **not** unified into the registry — it's legitimately
profile-specific (a site could reasonably be daily for Submissions but
quarterly for Violations). Only the identity tuple (srn/name/id) is shared;
`_is_due` itself is untouched by this refactor.

A `tiers` srn absent from `nsite_sites` is a config error and raises
naturally (`KeyError`) — no defensive `.get()`/try-except added around the
registry lookup. A loud failure on a config typo, caught by the first run or
a test, is correct; a silently-unwatched site is not.

### 2. `facilities:` (the Documents pipeline) is untouched

`nsite_sites` is scoped to profile-specific watches (Submissions today,
Violations next) — the same "own list, deliberately separate from
`facilities:`" posture ADR 021 established. This refactor does not touch
`facilities:`, does not add sites to it, and does not change
`nsite_submissions.enabled` in either direction.

### 3. Names/ids copied verbatim — no cleanup bundled in

The pre-refactor list has one known typo (`COMP`'s name is "Arbor Hills
Composting Faciltiy"). Per the handoff's explicit instruction, this refactor
does not fix it — mixing a content correction into a refactor whose entire
safety argument rests on "the resolved set is unchanged" would muddy that
argument for no reason. Fixing the typo, if wanted, is a separate one-line
follow-up.

## Behavior-preservation verification (the safety property this ADR rests on)

This is a refactor of an **already-live** path
(`nsite_submissions.enabled: true`) — per `overnight-coder.md` Step 3, a
change to a live path needs verification before an autonomous merge. This
refactor's specific safety property is narrower than a normal live-path
change (nothing about the external fetch, the diff, or the alerting changes —
only how the working site list is assembled from config), so the check is a
direct equivalence proof rather than a real-specimen poll:

1. Dumped the pre-refactor `nsite_submissions.sites` (19 entries) verbatim to
   a scratch file before touching `config.yml`.
2. After the refactor, reconstructed the working site list in a scratch
   script using the exact same join `run()` uses (`registry = {s["srn"]: s
   for s in cfg["nsite_sites"]}`, then `{**registry[srn], "poll": poll} for
   srn, poll in cfg["nsite_submissions"]["tiers"].items()`), and diffed it
   against the pre-refactor dump.
3. **Result: identical.** Same 19 srns, same names, same ids, same poll
   tiers — `pre_sorted == post_sorted` is `True`. Re-ran through the real
   production path (`config_loader.load_config()`, not just `yaml.safe_load`
   directly) for the same result — `config_loader.py` is a bare
   `yaml.safe_load` with no schema/allow-list, so there was no risk of the
   new top-level `nsite_sites` key being silently dropped, but proving it
   through the actual loader closes that question rather than assuming it.
4. `pytest -q`: 532 passed, the same count as before this change (no test
   added or removed — pure refactor). The `_is_due`-specific tests
   (`test_is_due_*`) needed **zero** changes — confirming this refactor
   touched only the config-loading path, not the cadence logic itself, per
   the handoff's explicit check.

## Consequences / residual risks (accepted)

- **A `tiers` srn typo now raises instead of silently omitting a site** — a
  deliberate behavior change from "site present in a malformed entry would
  just be whatever `sites` contained" to "site missing from the registry
  crashes the run." Accepted per the adversarial-review mitigation in the
  handoff: a loud config-error failure is strictly safer than a silently
  unwatched site, and this can only happen from a future hand-edit to
  `config.yml`, not from anything in this PR's diff (verified identical).
- **`nsite_sites` and `facilities:` still carry independently-maintained
  name strings for the same srns** (e.g. `nsite_sites`'s "Arbor Hills Energy,
  LLC (N1504)" vs `facilities:`'s "Arbor Hills Energy") — unchanged from
  before this refactor (ADR 021 already established these as deliberately
  separate lists); out of scope here.

## Alternatives considered

- **Merge `nsite_sites` into `facilities:`** — rejected, same reasoning as
  ADR 021 Decision 2: `facilities:` drives unconditional daily Documents
  polling; folding the 14 Submissions-only sites into it would poll their
  Documents profile daily for no benefit, exactly what ADR 021 avoided.
- **Keep `poll` on the registry entries and let each profile watch filter to
  the srns it cares about** — rejected: this would force every future
  profile's cadence into the same field, contradicting the explicit
  requirement that cadence stay legitimately profile-specific (a site could
  be daily for Submissions, quarterly for Violations).
- **Defensive `.get()`/skip on a missing `tiers` srn instead of `KeyError`** —
  rejected per the handoff's adversarial-review mitigation: a config typo
  should fail loudly and immediately (caught by the first run or a test), not
  silently produce an unwatched site.

## Activation

No new `enabled` flag — this is a pure refactor of an already-live path, ships
with `nsite_submissions.enabled` carried through unchanged. No new secrets, no
workflow file changes. `coder:nsite-violations-watch` is now unblocked: it can
reference `nsite_sites` by srn and add its own `nsite_violations.tiers` block
without duplicating site identity a second time.
