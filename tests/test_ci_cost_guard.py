"""Guards against the CI cost leak of 2026-08-04/08.

`.github/workflows/claude-review.yml` ran `anthropics/claude-code-action@v1` on
`pull_request: [opened, synchronize, reopened]` with
`anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}` and no model pinned. The
action defaults to `claude-opus-5[1m]`, so every push to a PR branch bought a
full Opus review of the diff at roughly $2. Because the overnight-coder loop
pushes a commit per review round, one build bought five: PR #36's runs cost
$1.84 + $2.45 + $2.40 + $2.14 + $1.03 = $9.85, against a ~$5-6/mo target for the
whole workspace. The monitor's own code is Haiku-only (`egle_doc_parser`, the
single Claude call site), so this job was the entire overspend.

Two things made it hard to see, and both are what these tests encode:

1. **An API key does not pin a model — the calling code does.** The first fix
   attempt repointed the GitHub secret to a key nicknamed "Haiku-only" and the
   reviewer went on running Opus against it ($0.37 on 8/07, $1.90 on 8/08).
   Anthropic keys are workspace-scoped, never model-scoped, so no amount of key
   hygiene can cap this. Assert it at the call site instead.
2. **Silence is the failure mode.** Nothing in CI goes red when a workflow bills
   an expensive model to a metered key; you find out from an invoice. So the
   assertion has to live somewhere that runs, which is here.

Scope note: these tests deliberately check *any* workflow, not `claude-review.yml`
by name. Asserting "that one file is gone" would be the first thing deleted by
whoever reintroduces it under a new name, taking the guard with it. This form
survives the file it was written for. For the same reason the search covers
`.yaml` as well as `.yml`, and `docs/pending-workflows/` as well as
`.github/workflows/` — this repo parks workflow files there before activating them
with a `git mv` (that is how nsite-violations-watch.yml lived before PR #36), so a
reviewer reintroduced in the parking lot would otherwise pass here and go live
later without ever tripping the guard.
"""

import pathlib

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_WORKFLOW_DIRS = (_ROOT / ".github" / "workflows", _ROOT / "docs" / "pending-workflows")
WORKFLOWS = sorted(
    p for d in _WORKFLOW_DIRS for ext in ("*.yml", "*.yaml") for p in d.glob(ext)
)


def test_the_guard_actually_has_workflows_to_scan():
    """An empty parametrize list collects zero tests and reports green — the same
    silent pass this whole module exists to prevent. If the workflow directories
    move or the globs stop matching, fail here rather than quietly guard nothing.
    """
    assert WORKFLOWS, f"no workflow files found under {[str(d) for d in _WORKFLOW_DIRS]}"


def _claude_action_steps(workflow_path):
    """Yield (job_name, step) for every step invoking anthropics/claude-code-action."""
    with open(workflow_path) as f:
        wf = yaml.safe_load(f)
    for job_name, job in (wf.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if "claude-code-action" in str(step.get("uses", "")):
                yield job_name, step


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_bills_claude_code_action_to_the_api_key(workflow):
    """A metered API key must never authenticate an agentic CI reviewer.

    `anthropic_api_key` bills per token against the workspace; the reviewer is
    unbounded work over a diff whose size we don't control. Use
    `claude_code_oauth_token` (subscription) if this ever comes back — that is
    the pattern roar-dashboard already uses.
    """
    for job_name, step in _claude_action_steps(workflow):
        assert "anthropic_api_key" not in (step.get("with") or {}), (
            f"{workflow.name} job '{job_name}' authenticates claude-code-action with "
            "anthropic_api_key — this bills per token against the AHM workspace and is "
            "what cost $9.85 on PR #36 alone. Use claude_code_oauth_token instead."
        )


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_any_claude_code_action_pins_an_explicit_model(workflow):
    """Unpinned means Opus. The action's default model is the most expensive one,
    so omitting `claude_args: --model ...` is not a neutral choice — it is the
    costliest one, made silently. Pin it even when billing is on a subscription,
    because Max is a single shared pool and CI competes with interactive work.
    """
    for job_name, step in _claude_action_steps(workflow):
        claude_args = (step.get("with") or {}).get("claude_args", "")
        assert "--model" in claude_args, (
            f"{workflow.name} job '{job_name}' runs claude-code-action without pinning a "
            "model in claude_args, so it takes the action default (claude-opus-5[1m] as of "
            "2026-08). Add e.g. `claude_args: --model claude-haiku-4-5-20251001`."
        )
