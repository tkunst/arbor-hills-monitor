# Monthly devops sweep

*Staged 2026-10-14 (coder:devops-automation). This is a checklist PROMPT for a
future scheduled cloud agent or an overnight-coder run to execute monthly --
**not itself a schedule**. Nothing in this repo invokes this file
automatically. Trisha (or the parent session, after reviewing this doc)
creates the actual schedule later, via the `schedule` skill or a cron
workflow, pointed at this checklist.*

## How to run this

Paste (or have a scheduled agent paste) roughly:

> Run the monthly devops sweep: `docs/devops-monthly-sweep.md` in
> arbor-hills-monitor. Follow docs/overnight-coder.md's conventions for any
> PR this produces (draft, not merged, unless a step says otherwise). Report
> what you found and filed.

## Scope and ground rules

- **Read-only unless a step says otherwise.** This sweep finds and reports;
  it does not upgrade packages, does not merge PRs, does not touch branch
  protection or other repo settings, and does not create schedules.
- **Never delete unmerged work.** Branch/worktree pruning below is restricted
  to branches already merged into `main`.
- Two repos to cover: `arbor-hills-monitor` and `better-arbor-hills`. Plus a
  separate, non-code check of the Lotext workspace (see item 5) -- that part
  reports to `TASKS.md`, not a PR.

## Checklist

### 1. Python / key-package end-of-life horizon

- Every workflow in both repos' `.github/workflows/` pins
  `python-version: "3.12"` (`grep -rn 'python-version' .github/workflows/*.yml`
  in each repo). Check the current CPython 3.12 end-of-life date against
  today (3.12 is supported until **October 2028** as of this writing -- verify
  that date hasn't moved, and check whether 3.13/3.14 is now the better
  target given this repo's dependency set). If a move is due (inside ~12
  months of EOL, or a key dependency has dropped 3.12 support), open a PR
  bumping `python-version` across every workflow in one change, plus
  `requirements.in`'s own Python-version assumptions if any, and run the full
  `pytest -q` suite against the new interpreter locally before opening it.
- Skim `requirements.in`'s comments for any package with a stated minimum
  Python version and re-check it's still compatible with 3.12 (or whatever
  the current pin is).

### 2. GitHub Actions deprecation warnings

- `gh run list --workflow <name> --limit 5` across the busiest workflows
  (`daily`, `tests`, `sast`, `secrets`), then `gh run view <run-id> --log` (or
  the run's own Annotations tab) looking for deprecation annotations --
  classically "Node.js 16/18 actions are deprecated", "the `set-output`
  command is deprecated", or a specific action version nearing end-of-support.
- Cross-reference against GitHub's own Actions deprecation notices (search
  "GitHub Actions deprecation" for anything new since the last sweep).
- File anything found as a tracked item (see "Where findings go" below); only
  fix directly if it's a trivial, obviously-safe action-version bump with
  no behavior change (and say so clearly in the PR if you do).

### 3. Branch and worktree hygiene

- **arbor-hills-monitor**: `git branch -r --merged origin/main` to find
  remote branches already merged into `main`; `gh pr list --state merged
  --limit 20` to cross-check each has a merged PR (don't delete a branch
  whose merge wasn't actually via a tracked PR without a second look). Delete
  only branches confirmed merged. Never delete `main`, never delete a branch
  with any open PR against it (check `gh pr list --state open` first), never
  delete anything that looks like another session's active work (see
  `docs/overnight-coder.md` Step 1's guidance on stray state).
- Same for **better-arbor-hills**.
- Stale worktrees: `git worktree list` in both repos' main checkouts. A
  worktree whose branch is already merged-and-deleted is a candidate for
  `git worktree remove`; a worktree with uncommitted or unmerged work is
  NOT -- leave it and report it instead. Do not touch
  `/Volumes/Samsung-Pro-2TB/worktrees/better-arbor-hills-set-brief-halves`
  (or its branch `set-brief-halves`) or any other worktree that looks like
  parked, intentional work-in-progress; when in doubt, report rather than
  remove.

### 4. Dependabot backlog

- `gh pr list --search "author:app/dependabot" --state open` in both repos.
  Flag any PR open for more than 2 weeks (patch/minor that never auto-merged
  -- check why: a failing gate, a label stuck, the auto-merge workflow itself
  erroring) and any PR labeled `needs-review` waiting on Trisha.
- `gh api repos/tkunst/arbor-hills-monitor/dependabot/alerts --paginate -q
  '.[] | select(.state=="open")'` (and the same for `better-arbor-hills`) --
  summarize open Dependabot security alerts by severity.
- Failed scheduled workflow runs in the last 30 days:
  `gh run list --status failure --created "$(date -v-30d +%Y-%m-%d)..$(date
  +%Y-%m-%d)"` (both repos) -- note which workflow, how many times, and
  whether it's a known/flagged-flaky job or a new failure.

### 5. Lotext / lotext-platform package currency

- For each Python interpreter Lotext/lotext-platform actually use (find them
  -- don't assume one venv; check for multiple project-specific
  virtualenvs), run `pip list --outdated`.
- `brew outdated` (Mac-native context only -- see the CLAUDE.md
  execution-context self-detection note; skip this one line in a sandboxed
  context and say so).
- **File findings to `/Volumes/Samsung-Pro-2TB/Lotext/TASKS.md`. Do not
  upgrade anything** -- this is a report-only step, consistent with
  `docs/overnight-coder.md`'s stance that Lotext isn't this repo's to modify
  beyond `TASKS.md`/handoff bookkeeping (see its Step 9). Do not push Lotext
  (local commit only, same as every other overnight-coder Lotext touch).

## Where findings go

- Anything actionable in **arbor-hills-monitor** or **better-arbor-hills**
  (a Python-version bump, an action-version bump, a stuck Dependabot PR, an
  alert needing triage): open or update ONE draft PR per repo per sweep
  titled `devops: monthly sweep findings <YYYY-MM>`, body = the checklist
  above with each item's result inline (found / not found / fixed directly /
  needs Trisha). If item 1 or item 2 produced a real, safe, standalone code
  change, that change can go in the same PR or a linked one -- use judgment,
  but never silently fold an upgrade into the findings PR without saying so
  in bold at the top.
- Lotext findings (item 5): `TASKS.md` only, per above.
- Nothing here is merged automatically -- these PRs wait for Trisha like any
  other draft, per `docs/overnight-coder.md`'s standing rule that a security
  or governance-adjacent change is never auto-merged.

## Why staged, not scheduled, right now

Trisha asked for this checklist staged so she can review its scope before
anything runs on a timer. Creating the actual `schedule` (or a monthly cron
workflow) is a deliberate separate step for her or the parent session --
this document is deliberately inert until that happens.
