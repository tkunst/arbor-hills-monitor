# Overnight coder loop

A procedure for running unattended, autonomous coding sessions against this
repo — feed the goal in when you kick off the loop (e.g. via `/loop`), then
this document is the standing instruction for how each iteration proceeds
without further check-ins from Trisha.

Not to be confused with the Lotext **overnight-worker** Routine
(`Cowork-claude/skills/overnight-worker/SKILL.md`) — that one drains a
read/research-only queue and never writes code; this one builds, reviews,
and merges code in this repo. This file was renamed from
`overnight-worker.md` at merge time to keep the two distinct.

## Standing authorization (read this first)

This loop is **pre-authorized to merge to `main` and push without asking**,
provided every gate in Step 8 passes. This is a deliberate exception to the
normal "confirm before risky actions" default — the whole point of running
overnight is that it doesn't wait on a human. It does **not** authorize
merging past an open medium/high security finding under any circumstances —
see Step 6. Nor does it authorize merging code whose only evidence of
working is a mocked test suite: a new data-source integration ships
disabled (`enabled: false`) until a human flips it on, and a change to a
path that's already live is only mergeable autonomously once it's been
checked against a real specimen — both mechanisms are spelled out in
Step 3. If you're re-reading this later wondering whether a merge was
authorized: yes, as long as this document was in effect and every gate
below was actually met — check the PR's CI status and review history to
confirm, don't just take the merge on faith.

## Inputs

- **The goal** — supplied at invocation time, not read from a backlog. If
  invoked with no goal, stop and say so; don't guess at what to work on.
  In practice the goal will usually be one row from the "Need to get / not
  yet automated" table in the Cowork-workspace doc
  `arbor-hills-multiple-data-sources.md` (outside this repo) — Trisha's
  intent is to clear one automatable row per night. That table's rows
  aren't all code tasks (some are FOIA requests or one-time manual pulls,
  and some are flagged as needing a feasibility spike before anyone can
  say they're automatable at all); pre-filtering to an actually-automatable
  row is a human judgment call made *before* invoking the loop, not
  something this procedure figures out on its own. The goal handed to the
  loop should carry that row's real detail — the source URL and the
  intended approach — not just its one-line label, since a bare label
  isn't enough to start Step 3 without guessing.
- This repo's `CLAUDE.md` (forbidden patterns, invariants, test command) —
  read it at session start as usual and follow it throughout. This document
  doesn't repeat those rules, it only adds the PR/review/merge procedure
  wrapped around them.

## Procedure

### 1. Setup

- `cd ~/repos/arbor-hills-monitor`
- `git status` — the check that matters is **uncommitted changes to
  tracked files**, or an in-progress branch that isn't this loop's own
  prior work; either of those means stop and leave a note rather than
  steamrolling it. This repo persistently carries a few stray *untracked*
  docs from other sessions (e.g. leftover review write-ups) — those are
  expected clutter, not a reason to halt. Don't assume unfamiliar
  *tracked* state is yours to discard; do assume unrelated untracked
  files are someone else's work-in-progress to leave alone, not a blocker.
- `git fetch origin`, checkout `main`, `git pull --ff-only` — start from a
  known-good, up-to-date base.

### 2. Create a branch

- First, check whether this goal already has an open PR or branch —
  `gh pr list --state open` and `git branch -a` — from a prior night that
  got escalated or interrupted. If one exists, that's Trisha's to
  resolve or hers to explicitly hand back; don't start a second, competing
  attempt at the same goal. Only proceed to a fresh branch if there's
  nothing already in flight for it.
- A short, descriptive branch name tied to the goal (e.g.
  `nsite-msg-docx-extraction`, `stream-d-mmpc-archive` — match this repo's
  existing branch-naming style, `git log --all --oneline` for examples).
- Branch off `main`.

### 3. Iterate on the goal and tests

- If the goal is substantial (multi-file, multi-step), use `TaskCreate` to
  track the work — it keeps a long unattended run legible after the fact.
- **Cap the iteration itself: roughly 6 write-test-fix cycles.** If the
  implementation still isn't converging toward a green test suite by then,
  stop rather than grinding all night on one goal. (6 is deliberately
  double Step 7's 3-round review cap — building an implementation covers
  more ground than re-reviewing one.) This is a Step-3 stop; see "What
  'stopped for Trisha' looks like" at the end of this document for exactly
  how to surface it.
- Before writing the "real" implementation, de-risk anything genuinely
  uncertain with a throwaway spike in the scratchpad directory, *especially*
  if real specimen data is available to test against (this repo's own
  history: two real, hard-to-predict bugs were only found by testing against
  actual hand-pulled documents, not synthetic fixtures — see ADR 011).
  Delete or ignore spike artifacts; only the real implementation and its
  tests land in the repo.
- **If the spike shows the goal isn't actually automatable** (the source
  can't be polled without credentials nobody has, the format can't be
  parsed reliably, whatever the specific dealbreaker is), that's a valid
  and successful outcome of this step — don't force a speculative
  implementation just to reach a green build. This is also a Step-3 stop
  (same "stopped for Trisha" procedure). This applies directly to backlog
  rows that are already flagged as needing a feasibility spike before
  anyone can call them automatable.
- **Mocked-green is never enough on its own to let code start affecting
  the live monitor unattended.** Hermetic tests here are necessarily
  mocked, and this repo's own history shows mocked tests can pass while
  the real integration is still broken (the `insert_textbox` and
  mixed-doc-OCR bugs in ADR 011 — both invisible to synthetic fixtures,
  both only found by testing against real specimens). So, depending on
  what kind of change this goal is:
  - **A brand-new poller/source/scraper against a live external system**
    ships with its config flag `enabled: false` (the pattern already used
    for Streams C and D — see ADRs 009/010, e.g. `backfill.py`'s
    `wds.enabled` gate). Flipping it on is explicitly a separate, later,
    human step this loop never takes, regardless of how green CI is.
  - **A change to a path that's already live** (a parser fix, an
    extraction-logic change, anything that already runs against real
    nSITE docs every night — like the `.msg`/`.docx` work in ADR 011
    itself) has no flag to hide behind: it affects real monitoring on the
    very next scheduled run. Verify it against at least one real specimen
    (per the spike step above) before merging autonomously. If no real
    specimen is obtainable for this goal, don't merge — that's a Step-3
    stop too.
  - **Which of the two cases above applies is read from the live `config.yml`,
    never from the handoff.** A handoff is a snapshot from the day it was written;
    if the target stream was *enabled* after that, the handoff's "ships
    `enabled: false`" / "merge on mocked-green" / "stays disabled" framing is stale,
    and trusting it would autonomously merge an unverified change to a live path
    (exactly what happened to `coder:gfl-air-24h-average`: its handoff was written
    while Stream E was disabled, but `gfl_air.enabled` had been flipped to `true`
    before the build). So at build time, check the target's *actual* `enabled` flag
    in `config.yml` — if it is `true`, treat this as a live-path change (real-specimen
    verification mandatory, no mocked-green merge) regardless of any disabled-framing
    in the handoff, and do not flip the flag either way (its on/off state is Trisha's).
- Write tests alongside the implementation. Run `pytest -q` — must be fully
  green before moving on. Follow every rule in this repo's `CLAUDE.md`
  (no committed data files, no hardcoded paths/secrets, crash-safe write
  ordering, etc.) without exception.
- Stage only the files that belong to this change. This repo tends to
  accumulate incidental untracked files from other sessions — `git status`
  before every `git add` and don't sweep in anything unrelated.

### 4. Commit and open the PR

- Commit message: explain *why*, not just *what* (match the style of recent
  commits — `git log -5` for the current tone).
- Push the branch, `gh pr create` with a body following this repo's
  established shape: Summary (bullets), Test plan (checked boxes), and a
  "Before merging" section if there's anything a human should know before
  the merge (e.g. new secrets needed, a manual activation step).
- Wait for CI to finish (`gh pr checks <n> --watch`). The check set now
  includes the armed **`bandit`** SAST gate (fails the PR on any medium+
  finding) and the independent **`claude-review`** job (an advisory review that
  posts inline comments and stays green *unless it could not run* — a red
  `claude-review` means the review itself failed, not that it found problems).
  Both must finish before Step 5, so the review it posts exists to consume. If
  a check fails for a reason unrelated to the feature (e.g. markdownlint on a
  doc — this repo's CI is picky about blank lines around lists/fences and
  restarts ordered-list numbering at 1 for every list block, not just the
  first), fix it and push again before proceeding. Don't merge on red CI.

### 5. Resolve the code review (CI `claude-review` is authoritative)

- The authoritative code review is the independent **`claude-review` CI job** —
  a cold, diff-only read with none of this session's context, which makes it
  more independent than an in-session pass. Read its posted findings with
  `gh pr view <pr-number> --comments` (fall back to `gh api
  repos/{owner}/{repo}/pulls/<pr-number>/reviews` if the summary lands as a
  formal review rather than a comment).
- A light in-session `/code-review` is now only a **pre-push preflight** — run
  it in Step 4 before pushing to catch the obvious, but it is not the system of
  record; the CI job is.
- **Fallback — if `claude-review` skipped:** the action deliberately skips (and
  still reports the `review` check GREEN) on any PR that modifies a workflow
  file, as a security guard. When that happens there is no CI review, so the
  in-session `/code-review` is NOT merely a preflight for this PR — it *is* the
  review, and its findings must be resolved before merging. Confirm by checking
  the `review` job's log for "Action skipped due to workflow validation". Never
  merge a workflow-touching PR on a green-but-skipped `review` alone.
- Fix every finding directly in the same PR, **except**: if a finding is
  high-severity *and* substantial enough that folding it in would meaningfully
  bloat this PR's scope, open a **second, separate PR** for that fix instead
  (still autonomous, still gets fixed — a scope-discipline split, not an
  escalation). Note the split in both PRs' descriptions so the history reads
  clearly later.
- Push fixes; the `claude-review` job re-runs automatically on the new commit.
  Re-read its comments in the Step 7 loop until no findings remain open.

### 6. `/security-review` the PR

- Run `/security-review` against the same PR.
- **If it reports zero medium/high findings**: proceed to Step 7.
- **If it reports any medium or high finding, at any point in this loop —
  stop.** Do not attempt to fix it yourself, do not open a second PR for it,
  do not merge. Post a PR comment summarizing the finding(s) clearly (what,
  where, why it matters) and end this iteration. This is the one hard
  escalation condition in this whole procedure — security findings are
  Trisha's call, always, regardless of how confident the fix looks. See
  "Standing authorization" above.
- This escalation isn't gated on which skill surfaced the finding. If the code
  review (Step 5's CI `claude-review` job or the in-session `/code-review`)
  turns up something security-relevant (not just `/security-review`), it gets
  the same treatment — stop and escalate, not the autonomous-fix-or-second-PR
  path Step 5 otherwise allows. Severity, not which command found it, is what
  decides the route.
- **Low-severity security findings** (below the med/high hard-stop above —
  these come from the LLM reviewers, since Bandit is medium+ and gitleaks is
  binary): fix them in the Step 7 convergence loop by default. If a fix is
  substantial enough to bloat this PR's scope, record it as a **tracked
  follow-up in the PR description** — never leave it as a buried inline comment.
  **If a finding's severity is uncertain, round UP and escalate** per the hard
  stop above (fail-safe): a finding you can't confidently call "low" is treated
  as medium.

### 7. Convergence loop

Steps 5 and 6 can surface new findings after a fix (a fix can introduce its
own issue — this happened twice in this repo's own history, see ADR 011).
So: after applying fixes and pushing, re-read the `claude-review` CI job's
comments on the new commit (`gh pr view <pr-number> --comments` — the job
re-runs automatically on each push) and re-run `/security-review` before
declaring done. Loop:

- Re-check → nothing open, no security findings → done, go to Step 8.
- Re-check → new *non-security* findings → fix, loop again.
- Re-check → any medium/high security finding appears → stop per Step 6,
  regardless of what round this is.
- **Cap at 3 resolve-and-re-review rounds.** If still not converged after 3
  rounds, stop — post a PR comment explaining what's still open and why the
  loop didn't converge, and leave it for Trisha rather than grinding
  indefinitely. A loop that can't converge in 3 rounds is telling you
  something (goal is underspecified, or the fix approach is wrong), not
  something a 4th round will fix.

### 8. Merge and document (only reached with zero open items)

- Confirm CI is green on the latest commit — including the armed `bandit` gate
  and the `claude-review` job.
- Merge to `main` with `gh pr merge <pr-number> --rebase --delete-branch`
  (this replaces the old local `git merge --ff-only` + `git push origin main`).
  Going through `gh pr merge` respects branch protection once Phase C requires
  PRs + status checks, and `--rebase` keeps the repo's linear history while
  preserving the individual commits (use `--squash` instead when the PR reads
  better as a single commit — both stay linear). **Always pass an explicit
  merge method**: a bare `gh pr merge` prompts interactively and will hang an
  unattended run. Do NOT use `--auto` yet — merge-when-green needs branch
  protection, which is the separate Phase C step, not this loop's.
- If the change produced an ADR (`docs/decisions/NNN-*.md`) or a security
  review doc (`docs/security-review-*.md`), make sure both are committed and
  reflect the code's *final* state — not the state at first-draft time. Both
  should already be part of the PR by this point; this is a final accuracy
  check, not a new artifact.
- Leave a short closing comment on the merged PR (or in its description)
  summarizing: what shipped, whether anything is gated behind a flag/manual
  activation step Trisha still needs to do, and any residual risks accepted
  along the way. This is what she reads first each morning — make it
  legible without requiring her to re-read the whole diff.

### 9. Update the overnight-coder queue (Cowork) — automatic housekeeping

Only after a **successful merge**. The queue that fed you this goal lives in the
Lotext workspace, OUTSIDE this repo — update it so the morning review doesn't have
to. Your cwd is the repo, so use ABSOLUTE paths:

- `/Volumes/Samsung-Pro-2TB/Cowork-claude/documents/overnight-coder-queue.md` (active)
- `/Volumes/Samsung-Pro-2TB/Cowork-claude/documents/overnight-coder-archive.md` (done)
- `/Volumes/Samsung-Pro-2TB/Cowork-claude/documents/overnight-queue.md` (worker queue)

Steps:

1. **Find your item.** `grep -n` the coder queue for the `Goal + full spec:` path
   you were handed; that block's `### coder:<slug>` heading is your item. (If the
   goal didn't come from the queue — an ad-hoc paste — skip this whole step.)
2. **Archive it.** Move that whole block out of the coder queue into
   `overnight-coder-archive.md` under `## Done` (newest first), rewritten to the
   archive's compact form: `### <date> — coder:<slug> — <title>` with Handoff, PR #,
   spike verdict (if any), a one-line outcome, and what's left for Trisha. The slug
   retires — never reused.
3. **Release the worker pin.** If your item's `Dependency:` named a worker item
   (`worker #NN`), edit the worker queue: change that item's
   `[coder-prep -> coder:<slug>]` tag to `[coder-prep CONSUMED by coder:<slug> <date>]`
   (pin released; a later session may `[x]`/archive that worker item normally). If
   `Dependency: null`, skip this.
4. **Commit locally, never push.** These are Lotext files — commit them in the
   Cowork repo by EXPLICIT path (never `-A`), do NOT push (Lotext convention):
   `git -C /Volumes/Samsung-Pro-2TB/Cowork-claude add <the 2-3 files>` then
   `... commit -m "overnight-coder: archive coder:<slug>; release worker pin"`.

**Best-effort:** a failure here must never undo the merge. If any step can't
complete (missing path, git issue), say so in your closing PR comment so Trisha
finishes the move in her morning review, and stop — do not retry destructively.

## What "stopped for Trisha" looks like

Every stop in this procedure that happens after Step 2 — a Step-3
iteration-cap stop, a Step-3 not-automatable finding, a Step-6/7 security
finding, or Step-7 non-convergence — ends the same way, so there's exactly
one place to check each morning (`gh pr list --state open`, draft PRs
included):

- If real implementation code exists (even incomplete, even with failing
  tests), commit it as-is — clearly marked WIP/failed in the commit
  message — push the branch, and open a **draft PR** if one isn't already
  open.
- If no implementation code survived (a pure feasibility spike that
  concludes "not automatable" often won't have any), commit a short
  write-up under `docs/` instead — what was tried, what the dealbreaker
  was — and open *that* as a draft PR. A doc-only PR is still a PR: it
  shows up in `gh pr list` and gives the write-up somewhere to live.
- Either way, leave a comment (or the PR description, if opening it fresh)
  explaining clearly what happened and why the loop stopped there.
- Don't close the PR, don't delete the branch, don't start a fresh attempt
  at the same goal in a new branch unless explicitly asked to. One open,
  well-documented PR waiting for a human is the correct end state — not a
  queue of half-finished attempts.
- **Annotate the queue item as STOPPED (do NOT archive it).** If this goal came
  from the overnight-coder queue, mark it in place instead of running Step 9's
  archive move: in
  `/Volumes/Samsung-Pro-2TB/Cowork-claude/documents/overnight-coder-queue.md`,
  prepend `**STOPPED <date> — see draft PR #N (<one-line reason>).**` to your
  item's `Readiness:` line, and commit that locally (explicit path, no push) the
  way Step 9 does. The item stays in the ACTIVE queue (not the archive) so it's
  visibly awaiting attention, pointing at the draft PR. Do NOT release any worker
  pin on a stop — the dependency was not consumed.

The one exception is Step 1's dirty-tree stop: it happens before this loop
has touched anything, so there's nothing of its own to commit or push.
That one just ends the session — the invoking session's own output is the
record, since there's no PR to open for a stop that occurs before any of
this loop's own work exists.
