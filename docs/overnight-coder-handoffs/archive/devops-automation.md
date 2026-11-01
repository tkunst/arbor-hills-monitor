# Overnight-coder handoff: devops automation (Dependabot, safe auto-merge, briefing surfacing, monthly sweep)

*Staged 2026-10-14, Trisha-directed ("Devops now, option 2A"). Read `docs/overnight-coder.md`
first. Repo config + CI change; it touches no runtime path, but it changes what merges to
`main` without a human. So: **DRAFT PR for Trisha's review, no autonomous merge**, and do not
change any GitHub repo SETTING yourself (list the exact `gh api` commands in the PR for
Trisha to run). Recommended tier: **Sonnet**.*

## Invocation

Branch name suggestion: `devops-automation`.

## Trisha's decisions (do not re-ask)

- Go now. The hashed lockfile is merged (PR #136, `025adba`: `requirements.in` -> hashed
  `requirements.txt`, `pip install --require-hashes` in 37 workflows).
- **Auto-merge policy 2A:** Dependabot **patch and minor** updates merge themselves once
  the tests pass; **major** updates wait for Trisha as normal PRs.

## Current state (verify in Step 1)

- No `.github/dependabot.yml`. Dependabot security updates and alerts are ENABLED; secret
  scanning and push protection are enabled.
- Repo settings: `allow_auto_merge: false`, `delete_branch_on_merge: false`.
- Branch protection on `main` exists, `enforce_admins: true`, but **no required status
  checks**. With no required check, GitHub auto-merge would merge immediately, before tests
  finish. A required check is therefore a precondition for 2A, not an option.
- `better-arbor-hills` (public; workflows: `links.yml`, `markdownlint.yml`, `secrets.yml`):
  Dependabot security updates disabled. Lotext and `lotext-platform` have no GitHub remote.

## What to build

### 1. `.github/dependabot.yml` (arbor-hills-monitor)

- `pip` ecosystem on `/`: weekly (Monday morning ET), ONE grouped PR for patch+minor, majors
  as separate PRs. Dependabot must update the pip-compile lockfile with hashes from
  `requirements.in`; spike that it does (create the PR on a fork or check the docs for
  `pip-compile` support) and say how you verified it.
- `github-actions` ecosystem: monthly, grouped. It also updates the SHA-pinned third-party
  actions (keep the `# vX` comment convention).
- Labels `dependencies`, a commit-message prefix `build(deps)`, `open-pull-requests-limit` small (3-5).

### 2. Safe auto-merge workflow (policy 2A)

`.github/workflows/dependabot-auto-merge.yml`:
- Trigger `pull_request_target` is the documented pattern but is dangerous. Use it only with
  `if: github.actor == 'dependabot[bot]'`, never check out or run PR code in that job,
  minimal `permissions` (`contents: write`, `pull-requests: write`), and
  `dependabot/fetch-metadata` pinned to a SHA. (Alternative if safer: `pull_request` + the
  Dependabot-scoped token; pick one and justify it against GitHub's guidance.)
- If `update-type` is `semver-patch` or `semver-minor`, run `gh pr merge --auto --rebase`.
  Majors: add a `needs-review` label and do nothing else.
- Auto-merge must WAIT for the tests. Requires (Trisha runs, listed in the PR):
  1. `allow_auto_merge: true` on the repo.
  2. Branch protection on `main`: required status check = the `pytest` job (exact check name
     from `tests.yml`), plus `block-data-files`/`gitleaks` if cheap. Make sure this does not
     block Trisha's own merges or the bot commits workflows make to `main` (findings feed,
     ridgewood manifest, data-notes: they push directly; required checks apply to PRs, but
     confirm `enforce_admins` plus direct pushes from Actions still work, or scope it).
- **Edge case to handle:** a minor bump of a package that changes behavior (e.g. `anthropic`,
  `PyMuPDF`, `google-api-python-client`). Tests are the gate; add an explicit
  `ignore`/major-only rule for `anthropic` if minor releases there have broken things
  before (check git history), and say what you chose.

### 3. better-arbor-hills

Its own `.github/dependabot.yml` for `github-actions` only (monthly). No auto-merge
workflow there; list in the PR a `gh api` command to enable Dependabot security updates for
Trisha to run. That repo is a separate PR (draft) in its own checkout; do not push to its
`main`.

### 4. Morning-briefing surfacing (Lotext; local, commit, do not push)

The `morning-briefing` skill already scans open PRs. Add: open Dependabot PRs waiting for
review (majors), Dependabot alerts (`gh api repos/tkunst/<repo>/dependabot/alerts?state=open`),
and failed auto-merges, for both GitHub repos. Find where the briefing's PR scan lives (the
skill under `~/.claude/skills/morning-briefing/` or Lotext `skills/`) and extend that; do not
build a parallel tracker. Show Trisha the diff in the PR description, since this file is hers.

### 5. Monthly devops sweep (staged, not scheduled)

Write `docs/devops-monthly-sweep.md`: a checklist prompt a scheduled cloud agent (or an
overnight-coder run) executes monthly:
- Python and key-package end-of-life dates vs. CI (`python-version: "3.12"` in the
  workflows; 3.12 is supported until October 2028). Open a PR to move CI up when due.
- GitHub Actions deprecation warnings in recent run annotations (Node runtime, set-output).
- Prune merged branches and stale worktrees (both repos; never delete unmerged work).
- Stale open Dependabot PRs; failed scheduled workflows in the last 30 days.
- Lotext / lotext-platform: `pip list --outdated` in the interpreters they use, and
  `brew outdated`. File findings to Lotext `TASKS.md` and do not upgrade anything.
Do NOT create the schedule; Trisha (or the parent session) schedules it after review.

## Risks (adversarial review)

- **A bad minor update auto-merges and breaks a live job overnight.** Gate: required `pytest`.
  Detection: the job goes red, and the briefing shows failed scheduled runs. Recovery:
  `git revert` the bump commit, and the lockfile pins make it exact.
- **`pull_request_target` misuse runs untrusted code with write tokens.** Actor check, no
  checkout, SHA-pinned actions, minimal permissions; `/security-review` must cover it.
- **Required checks block the Actions bots that commit to `main`.** Verify in Step 3 against
  one real bot run, or scope the protection.
- **Notification noise.** Grouping plus monthly Actions updates, and a small PR limit.

## Verification

YAML validated (`actionlint` if available). Dependabot config validated through the GitHub
API or UI ("Check for updates") on the branch if possible. One dry demonstration of the
auto-merge decision logic (patch, minor, major) with fixture metadata. The full suite green.
