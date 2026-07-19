# Session 2026-07-18 — CD-governance / SAST enforcement package (plan + build)

Interactive session (Trisha-directed) building the provable continuous-deployment
enforcement package **designed** in the 2026-07-18 CI-governance handoff. This
file is the resume point; the full working plan lives at
`~/.claude/plans/mighty-bouncing-stroustrup.md` (not committed — this doc carries
the substance).

## Source & context

- **Handoff (design):** `/Volumes/Samsung-Pro-2TB/Lotext/handoffs/2026-07-18-arbor-hills-ci-governance-and-sast.md`
  — designed, did not build.
- **Design-session record (Lotext):** `sessions/2026-07-18-overnight-coder-outcome-cd-governance-sast.md`.
- **Goal (locked 2026-07-18):** any session — coder or not — may deploy to `main`
  **only** if reviews + security + tests are green, and Trisha can **prove a code
  review ran**. Continuous deployment is the model (green → auto-merge, no human gate).
- **The hole being closed:** branch protection has `enforce_admins:true` + force-push/
  deletion blocked, but **no `required_status_checks` and no require-PR** — so "green →
  merge" is agent discipline, not mechanically enforced. Direct `git push origin main`
  works today.

## Verified current state (this session)

- Repo `tkunst/arbor-hills-monitor`, PUBLIC, default `main`. `enforce_admins:true`,
  `required_status_checks:null`, `required_pull_request_reviews:null`.
- `ANTHROPIC_API_KEY` secret already set (2026-06-14) — the review job needs no new secret.
- **XXE:** single non-test site — `poison_doc_extractor.py:37` (`import xml.etree.ElementTree
  as ET`) + `:167` (`ET.fromstring(xml_bytes)` on untrusted `.docx` XML). `defusedxml` not in
  `requirements.txt`. **Fail-safe boundary confirmed:** a `defusedxml` raise propagates →
  `synthesize_pdf`'s broad `except Exception` (`:111`) → `ExtractionError` → caught by
  `nsite_client.py:246`. A hostile doc becomes a handled poison strike, not a crash.
- PR-triggered workflows / path filters: `tests`(HAS filter, job `pytest`),
  `sast`(no filter, job `bandit`, report-only `exit 0`), `secrets`(no filter, `gitleaks`),
  `data-guard`(no filter, `block-data-files`), `links`(HAS filter), `markdownlint`(HAS filter).
- SAST: XXE is the one *real* finding; handoff says "XXE + 4 low-risk" to clear before gating.
  Bandit not installed locally — the 4 get enumerated in the PR-A/B spike.
- Research (claude-code-guide agent): `anthropics/claude-code-action@v1`, `ANTHROPIC_API_KEY`,
  perms `contents:read` + `pull-requests:write`; **no native fail-on-findings** (a review job
  always passes unless the exit code is hand-scripted); required-check name = `<workflow>/<job>`.

## Locked decisions

| Decision | Choice |
|---|---|
| Review-check teeth | **Provably-ran / advisory** — independent CI `claude-review`, non-blocking. "Reviews green" is thus vacuous; tests+SAST do the real gating; "fix all comments" stays procedural |
| Enforcement level | **require-PR, `required_approving_review_count: 0`** (no 2nd identity to approve the coder's own PRs), `strict:false`, `enforce_admins` on — flipped LAST |
| Code review | Independent CI job = authoritative (cold process, fresh context, diff-only = more independent than today's in-session `/review`); light in-session `/code-review` kept as pre-push preflight (Step 5) |
| SAST | **Bandit AND CodeQL** (complementary: pattern vs semantic-dataflow), both required; gitleaks for secrets |
| Security review | **A now:** in-session `/security-review` stays authoritative, med/high hard-stop UNCHANGED; SAST/CodeQL/gitleaks added as deterministic tests beneath it. **B fast-follow:** optional independent CI security pass (adversarial prompt) — NOT bundled into the enforcement flip. `claude-code-action` is prompt-driven and does NOT load the local `/security-review` plugin |
| Low-sev security findings | Fix in convergence loop by default; substantial → tracked follow-up noted in PR desc; never a buried comment; **uncertain severity → round UP + escalate** (fail-safe). Come from LLM reviewers (Bandit is medium+, gitleaks binary) |
| Convergence | 3-round cap + escalate on non-convergence |
| Merge | `gh pr merge` (respects branch protection) replaces local ff-merge + `git push` |

**Rejected:** deterministic-security-only (loses contextual/business-logic security reasoning
SAST can't do — e.g. the `basis` measured-vs-permitted invariant); LLM review as a hard
fail-on-findings gate (nondeterministic, flaky); a custom GitHub App approver (Claude-approving-
Claude = the check with extra steps, no independence gained); human-approval gate (ends CD).

## Build order

- **PR A — XXE fix** (autonomous-safe; unblocks Bandit gate): `defusedxml` → `requirements.txt`;
  import swap in `poison_doc_extractor.py`; malicious-`.docx` test. Live-path change → verify
  against a real specimen before merge (overnight-coder Step 3).
- **PR B — required-check readiness** (autonomous-safe; no enforcement flip): de-filter `tests.yml`
  so `pytest` always reports; add `claude-review.yml` (always-reports); clear the 4 low-risk Bandit
  findings + flip `sast.yml` `exit 0`→`exit "$rc"`; `overnight-coder.md` edits (Step 8 → `gh pr
  merge`; Step 4 waits on new checks; Step 7 consumes the CI review's comments; Step 5 demoted to
  preflight; low-sev policy; Steps 6/9/Standing-auth unchanged).
- **Phase C — enable enforcement** (HITL, WITH Trisha — the lockout zone): enable auto-merge
  (CodeQL already enabled 2026-07-18, see posture below); throwaway PR to read EXACT check-context
  names; set `required_status_checks` (`pytest`,`bandit`,`gitleaks`,`block-data-files`,`CodeQL`,
  review job) with `enforce_admins:false`; prove a real green PR merges; **then** flip
  `enforce_admins:true`.
  - **Check-context names (read off PR #23's real runs — supersede the research guess):** the
    review job's context is **`review`** (NOT `claude-review / review`, which the claude-code-guide
    research guessed — the actual check-run `name` is just the job id). Other names as they appear:
    `pytest`, `bandit`, `gitleaks`, `block-data-files`, `lint` (markdownlint), `lychee` (links),
    `CodeQL` **and** `Analyze (python)` / `Analyze (actions)` both show up for CodeQL — resolve which
    one branch-protection accepts on the throwaway PR before requiring it (don't guess).
  - **Phase-C PRECONDITION — prove `claude-review` actually posts (owed, relocated here):** on PR #23
    the `review` check was **green but the action SKIPPED** — claude-code-action deliberately skips
    (and still exits 0) on any PR that MODIFIES a workflow file (log: "Action skipped due to workflow
    validation"). PR B necessarily modifies workflows, so it could not self-verify. The first
    NON-workflow PR is the first time `claude-review` can really run. **Use Phase C's opening throwaway
    PR (a non-workflow change) to confirm a review COMMENT actually posts** — add "a review comment
    posted" as a pass condition. If it skips or posts nothing *there* too, the config is broken (not
    merely guarded) and must be fixed BEFORE `review` joins `required_status_checks`. Corollary for the
    threat model: a workflow-touching PR will always pass a required `review` WITHOUT a review — fine,
    because `review` is advisory-by-design and the real gates (`bandit`/`pytest`/`CodeQL`/`gitleaks`)
    still run; the in-session `/code-review` is the documented fallback (overnight-coder Step 5).
  - **Phase-C TO-DO — Dependabot secrets gotcha (don't lose this):** once the `claude-review` job
    is a REQUIRED check, Dependabot-authored PRs may not merge. Dependabot-triggered workflow runs
    use a SEPARATE secrets store (`secrets.DEPENDABOT_*`, not Actions secrets) and a read-only
    `GITHUB_TOKEN`, so the review job may not see `ANTHROPIC_API_KEY` → job fails → blocks the
    Dependabot PR. Fix at wire-up time, pick one: (a) add `ANTHROPIC_API_KEY` to the repo's
    **Dependabot** secrets (Settings → Secrets → Dependabot), or (b) exempt the bot in the review
    job (`if: github.actor != 'dependabot[bot]'`) — but note (b) makes it a "skipped required check"
    on those PRs, so prefer (a). Applies to any secret-using required check on Dependabot PRs.

## Adversarial review — load-bearing points

- **Lockout is recoverable, not fatal:** `enforce_admins` governs *merges*, not *settings edits* —
  the owner can always remove the protection rule. Rollback: `gh api -X DELETE
  repos/:owner/:repo/branches/main/protection/enforce_admins` (DELETE, not PATCH). Worst case =
  "annoying, fully recoverable."
- **Path-filter "pending-forever" trap** fixed by de-filtering required workflows (not a
  disconnected always-green gate, which doesn't clear a sibling required check's pending state).
- **require-PR self-lockout** avoided by `required_approving_review_count: 0`.
- **Anti-lockout ordering:** throwaway PR reveals real check names BEFORE they're required;
  `enforce_admins` off until a real merge is proven.

## Security tooling posture (repo settings — enabled 2026-07-18 by Trisha, GitHub UI)

Full coverage now = code semantics (CodeQL) + code patterns (Bandit) + secrets (gitleaks) +
dependency CVEs/malware (Dependabot).

- **CodeQL** — default setup, languages `python` + `actions`, threat model `remote`; first scan ran
  2026-07-18. NOT yet a required check (that's Phase C; read its exact check-name off a real run first).
- **Dependabot alerts** — ON (+ default rule preset).
- **Dependabot security updates** — ON.
- **Dependabot malware alerts** — ON.
- **Grouped security updates** — ON (one security-fix PR per ecosystem — less PR churn).
- **Dependency graph** — ON. **Copilot Autofix** (CodeQL alert suggestions) — ON.
- **In-repo code gates:** Bandit SAST (report-only until PR B arms `exit "$rc"`), gitleaks, data-guard.

Deliberately OFF: **Dependabot version updates** (routine-bump noise — defer; if wanted later,
grouped + weekly via `dependabot.yml`); **Automatic dependency submission** (`requirements.txt` read
directly); **AI findings** preview (Bandit already covers Python).

## Status / next

- **Plan APPROVED by Trisha** 2026-07-18.
- **PR A — MERGED** (`fix(sast): XXE-harden .docx parsing with defusedxml`, PR #22 → `main`
  @ `9569e53`). CI green (pytest/bandit/gitleaks/block-data-files); `/code-review` clean;
  `/security-review` zero med/high. Real-specimen live-path check passed (4 real Word docs,
  byte-identical stdlib↔defusedxml). Branch deleted. B314 cleared → SAST gate unblocked.
- **PR B — MERGED** (PR #23 → `main`, rebased/linear, `dc0136c`→`47f5d0c`; branch deleted).
  Autonomous-safe, **no enforcement flip** (branch protection untouched — can't lock anyone out).
  Shipped exactly as planned:
  1. `tests.yml` — dropped both `paths:` filters; `pytest` now always reports.
  2. `claude-review.yml` (new) — advisory `claude-code-action@v1` review; deliberately NOT
     `continue-on-error` (research confirmed the action already exits 0 on findings, so green ⟺
     it ran, red ⟺ it couldn't — except the workflow-skip case, see Phase C precondition above).
  3. Cleared the 4 Bandit findings (B310 `# nosec` + 3× B324 `usedforsecurity=False`, digests
     byte-identical) and armed `sast.yml` `exit "$rc"`; **pinned `bandit==1.9.4`** (added over the
     plan — a required check must be deterministic so a future release can't silently red it).
  4. `overnight-coder.md` — Step 8 `gh pr merge <n> --rebase --delete-branch` (explicit method,
     non-interactive; `--auto` deferred to Phase C); Step 4 waits on new checks; Step 5 CI-review-
     authoritative + **workflow-skip fallback** (if `claude-review` skips, in-session `/code-review`
     IS the review); Step 6 low-sev policy; Step 7 consumes CI review comments; 9/Standing-auth
     unchanged.
  - Verified: 406 tests green; Bandit 0 findings; all 10 PR checks + push-to-main CI green;
    `/security-review` (+ independent adversarial subagent) **zero** med/high; `/code-review` one
     low doc-consistency nit, fixed; privacy pre-push gate passed.
- **NEXT: Phase C — enforcement flip (HITL with Trisha; do NOT start autonomously).** See the Phase C
  bullet above — now carries the exact check-context names + the `claude-review` empirical precondition
  discovered building PR B. **This is where the session STOPS.**
