# Overnight-coder handoff — remaining doc follow-ups from coder:claude-md-drift (PR #139)

*Staged 2026-10-16. PR #139 (`coder:claude-md-drift`) landed most of a
documentation-accuracy pass; this handoff is the leftover items, re-verified
against current `main` since two later PRs (#149, #157) may have already
covered some of them. Docs-only change — no code logic touched. Recommended
tier: Sonnet (or even Haiku, but the module audits benefit from careful
reading).

**Cloud-ready:** Runs in cloud mode: ends at a DRAFT PR. No live credentials
needed — this is a documentation/comment-only change.

**Live-path check:** not applicable — nothing here changes runtime behavior.
This is purely `CLAUDE.md`, a docstring, and two CI workflow *comments* (not
workflow logic). Still open a draft PR, not an autonomous merge, since it
touches `.github/workflows/*.yml` files and this handoff's own re-verification
step (below) needs a careful human skim before trusting it.*

## Background

TASKS.md's tracking line for this item lists five sub-items (a)–(e). Two are
explicitly EXCLUDED already (re-stated here so the cloud session doesn't
redo them): **(c)** the `docs/topology/` viewer gap is DONE (PR #157); **(e)**
the code-license decision is Trisha's own open call, not a coder task. That
leaves (a), (b), (d) below — **re-verify each against current `main` before
touching anything**, since PR #149 and #157 may have already fixed one or
more of them since the TASKS.md line was written (2026-10-14).

## What to build

### (a) `gfl_feed_snapshot.py`'s stale docstring

The TASKS.md line just says "stale," with no specifics — **read the current
docstring yourself and compare it against the current implementation** to
find what's actually wrong (e.g. a described step that no longer matches the
code, a referenced ADR number, a folder-env-var name, a row/field count that
drifted). Fix only what's genuinely stale; don't rewrite the whole docstring
if only one sentence is wrong. If, on inspection, the docstring reads
accurate to you, say so explicitly in the PR and skip this sub-item — don't
invent a change to justify the line item.

### (b) Root modules still undocumented in `CLAUDE.md`'s Architecture section

TASKS.md says "6 more root modules," written 2026-10-14. **Re-derive this
list yourself rather than trusting that number** — re-verify with something
like:

```
ls *.py | sed 's/\.py$//' | sort > /tmp/all.txt
grep -oE '^- `[a-zA-Z_]+\.py`' CLAUDE.md | sed -E 's/^- `//; s/\.py`$//' | sort -u > /tmp/documented.txt
comm -23 /tmp/all.txt /tmp/documented.txt
```

then, for each name that comes back, grep `CLAUDE.md` for ANY mention of it
(not just a bullet header) before concluding it's genuinely undocumented —
some modules are covered inline inside another module's paragraph rather
than getting their own bullet, and that may be an intentional, acceptable
style choice, not a gap. **As of this writing**, the modules with literally
**zero** mentions anywhere in `CLAUDE.md` were: `archive_client.py`,
`backfill_metric_taxonomy.py`, `name_check.py`, `neshap_table_parser.py`,
`nsite_watch_common.py`, `woi_router.py`, `woi_table_parser.py` (seven, not
six — the TASKS.md count may simply be stale by one, or may have used
slightly different criteria; trust your own re-derivation over either
number). For each one that's still genuinely undocumented when you check:
add a one-line-each Architecture bullet matching this repo's existing style
(see the entries already there for the format: what the module does, its
one load-bearing design fact, cross-references to the ADR that explains why
if one exists). Keep each entry proportionate — some existing entries run to
a full paragraph because the module has subtle behavior worth explaining
(e.g. `egle_doc_parser.py`'s); a module with nothing subtle about it (e.g. a
thin wrapper) can get a genuinely short one-liner. Don't pad for padding's
sake.

### (d) Stale paths in `sast.yml`/`tests.yml` CI-config comments

Both `.github/workflows/sast.yml` (around its header comment) and
`.github/workflows/tests.yml` (same) reference `docs/session-2026-07-18-cd-governance-enforcement.md`.
That file now actually lives at `docs/sessions/session-2026-07-18-cd-governance-enforcement.md`
(it was moved into a `sessions/` subdirectory at some point after these
comments were written — confirm this is still the real current path before
fixing the comment, in case it moved again since). Update both comments to
the correct path.

**Workflow-scope caveat (per `docs/overnight-coder.md` Step 4):** pushing a
change to a `.github/workflows/*.yml` file needs the `workflow` OAuth scope
UNLESS the push goes over SSH, in which case the scope restriction doesn't
apply. Check whether this cloud session's push path is SSH or HTTPS/token
(`git remote -v` / try `ssh -T git@github.com`) before assuming you can push
these two files directly. **If the push is blocked by the missing scope**,
park the two comment-only fixes by leaving a clear note in the PR description
(you can't use the `docs/pending-workflows/<name>.yml` parking mechanism
here, since that's for a NEW workflow file, not a one-line comment edit in an
existing one) — describe the exact two-line fix needed so a local,
SSH-capable session can apply it in thirty seconds, and don't let this one
sub-item block the rest of the PR (ship (a)/(b) regardless of whether (d)
lands).

## Tests

- None of this changes behavior, so there's no new pytest coverage to write.
  Instead:
  - `markdownlint` (or whatever this repo's CI uses for `CLAUDE.md`/docs)
    must stay green on the edited `CLAUDE.md`.
  - If this repo's CI validates workflow YAML syntax (it almost certainly
    does, implicitly, by the workflows actually running), make sure your
    comment edits don't break YAML parsing — a `#` comment line is safe, but
    double-check indentation/quoting around it.
  - `pytest -q` should be unaffected; run it anyway to confirm nothing broke
    by accident (e.g. a docstring edit introducing a syntax error).

## Out of scope

- (c) topology viewer coverage — DONE, PR #157. Do not touch
  `docs/topology/` as part of this item (a later, unrelated session may
  still need to regenerate it if THIS PR adds new modules via sub-item (b) —
  if your new CLAUDE.md bullets correspond to modules the topology map
  already has nodes for, no regeneration is needed since you're not adding a
  module, just documenting an existing one).
- (e) code license decision — Trisha's call, not this PR's.
- Any behavior change to `sast.yml`/`tests.yml` beyond the two stale-path
  comments — do not touch the actual job logic, triggers, or permissions in
  either file.
- Re-litigating whether PR #149/#157 fully covered items outside this list —
  just confirm (a)/(b)/(d) specifically; don't go hunting for other
  unrelated documentation drift as part of this PR (scope creep).

## Acceptance

- `pytest -q` fully green (should be a no-op change for the test suite).
- The PR states, for each of (a)/(b)/(d): what you found (confirmed stale,
  already fixed by a prior PR, or genuinely needed a change) and exactly
  what you changed, with your own re-derived module list for (b) shown
  explicitly (not just "fixed per TASKS.md") so a reviewer can check your
  work against the live repo rather than trusting a possibly-stale count.
- If (d) couldn't be pushed due to the workflow scope, the PR names the exact
  two-line fix for a local session to apply.
