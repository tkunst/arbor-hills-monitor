# Pending workflows — files that belong in `.github/workflows/`

Every `.yml` in this directory is a **finished, reviewed GitHub Actions
workflow that could not be pushed** from the session that wrote it, and is
waiting on one `git mv` from someone whose credentials can write to
`.github/workflows/`.

## Why a file ends up here

GitHub refuses a push that creates or updates anything under
`.github/workflows/` unless the pushing credential carries the **`workflow`**
OAuth scope:

```text
! [remote rejected] refusing to allow an OAuth App to create or update
  workflow `.github/workflows/<name>.yml` without `workflow` scope
```

The `gh` CLI token in an unattended session has `repo` but not `workflow`
(granting it needs an interactive browser flow), and the REST Contents API is
scope-restricted the same way — it returns a bare `404` for the same write. If
the machine's SSH key is also passphrase-locked and unreachable
non-interactively, there is no write path left for that one file, even though
every other file in the change pushes fine.

Parking the file here keeps the reviewed content durable and diffable instead
of losing it to the session, and keeps it visible instead of leaving a stream
that silently never runs.

## How to land one

From a session with SSH access or a `workflow`-scoped token:

```bash
git mv docs/pending-workflows/<name>.yml .github/workflows/<name>.yml
```

Then commit and push. Nothing else needs to change — these files are written
against the same conventions as their siblings (own `concurrency` group, own
cron slot, `permissions: contents: read`, secrets by name) and are reviewed as
part of the PR that added them here.

## Currently pending

_Nothing is pending right now — every workflow file lives in
`.github/workflows/`._

Stream L (nSITE Violations, ADR 023) was parked here and moved into place when
PR #36 merged; Stream M (nSITE Compliance Actions, ADR 028) likewise, when it
was activated 2026-08-09 (moved into `.github/workflows/` via an SSH push and
`nsite_compliance_actions.enabled` flipped `true` in the same change — the
`workflow` OAuth scope is deliberately not kept on the build machine).

**When you move a file out, delete its row above.** This directory is a holding
pen, not a permanent fixture — a stale "currently pending" list on `main` is
worse than no list at all. It is kept while empty only because ADR 023 / ADR 028
and `CLAUDE.md` reference this pattern doc, and the next overnight stream built
without the `workflow` scope will park its `.yml` here again.
