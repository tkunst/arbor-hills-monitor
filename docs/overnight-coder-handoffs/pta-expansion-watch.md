# Handoff: `coder:pta-expansion-watch` -- watch the Pine Tree Acres expansion's state approvals

**Written 2026-11-07 (Trisha-directed).** This file IS the goal. Read it fully, then
`docs/overnight-coder.md` and `CLAUDE.md`.

## Why

The Pine Tree Acres (PTA) 75M cubic-yard expansion (WM, Lenox Twp, Macomb Co.) is the biggest single
input to the regional capacity model: whether it is approved, and when it opens. Macomb County's Board
approved the plan amendment that sites it on 2026-02-19, at least 21 municipalities ratified it, and the
county sent it to EGLE around May 2026. Two state approvals remain, and Trisha wants the monitor to
alert when either moves:

1. **EGLE's approval of the Macomb County plan amendment.** Checked 2026-11-07: EGLE's "Currently
   Approved County Plans and Plan Amendments" table
   (https://www.michigan.gov/egle/about/organization/materials-management/solid-waste/planning/solid-waste-management-plans)
   shows Macomb with only "Appendices" and no starred amendment year, so no approved amendment is
   listed yet.
2. **The Part 115 construction permit for the expansion.** Checked 2026-11-07 in WDS (site **398972**,
   Pine Tree Acres): no expansion construction-permit application yet. The newest construction permit
   was received 10/31/2019 and issued 3/18/2020; the newest operating license was issued 2/6/2026. Past
   construction permits took about 5 to 7 months from receipt to issue (10/2014 to 5/2015; 10/2019 to
   3/2020).

PTA's nSITE registration (SRN **N5984**, nSITE id **6656726126269863785**) is air only (ROP0000321 in
effect 2025-09-03 to 2030-09-03; air submissions). The expansion permit will NOT show up on nSITE; it
will show up in WDS.

## Scope

**A. WDS: watch a second site (the important part).** `wds_watcher` reads one `wds.site_id`
(475946, Arbor Hills). Add support for a list of extra sites with their own collection list and tiers,
keyed so their state does not collide with Arbor Hills' in `_meta wds_seen`. Add PTA:

```yaml
wds:
  extra_sites:
    - {site_id: "398972", name: "Pine Tree Acres (WM, Macomb)", collections: [applications, annual]}
```

- `applications`: a NEW Construction Permit application = **URGENT** with the subject "Pine Tree Acres
  expansion: construction permit application received"; Closure Type changing to Issued = **URGENT**;
  any other change = watch. Operating License rows = watch only.
- `annual`: new report-year = watch (feeds the capacity model's remaining-space number).
- First run self-baselines silently (Rule B), so the existing 11 rows do not alert.
- Arbor Hills' behavior must not change at all (same state key, same alerts); prove it with tests.

**B. EGLE plan-amendment page: alert when Macomb's row changes.** A small page-diff check on the EGLE
URL above: extract the Macomb row's text from the "Currently Approved County Plans and Plan
Amendments" table and alert (notable) when it changes, quoting old and new text. The page returned 403
to a bare WebFetch but 200 to `curl` with a normal browser User-Agent; use the repo's existing
fetch conventions. If the fetch fails 3 days running, alert once (stale), never silently.

**C. nSITE N5984 (optional, config-only).** Add PTA to `nsite_sites` (and nothing else) only if it costs
nothing beyond one config row + the pinned-set test update; its records are air and mostly unrelated to
the expansion. Write an ADR either way (next free number) recording the decision.

## Out of scope

Collecting PTA's documents into the Arbor Hills mirror, any public site page about PTA, and Carleton
Farms or any other landfill.

## Acceptance

- Unit tests for the multi-site WDS state keys and both URGENT rules (fixtures from today's 11 PTA rows
  plus a synthetic new Construction Permit row and a synthetic Issued transition).
- The page-diff check tested against a saved copy of today's page and a synthetic changed Macomb row.
- Full test suite green; Arbor Hills WDS alerts unchanged on a replay of current state.
- New ADR. Open a PR and stop: merge needs Trisha's review (this changes a live alerting path).
