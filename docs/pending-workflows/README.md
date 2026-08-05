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

| file | stream | blocks |
|---|---|---|
| `nsite-violations-watch.yml` | Stream L — nSITE Violations watch (ADR 023) | Nothing today: `nsite_violations.enabled` ships `false`, so the stream is a no-op either way. **It must be moved before `enabled` is flipped to `true`**, or the watch will never be scheduled and will fail silently. |

**When you move a file out, delete its row above** — and when the table empties,
delete this whole directory. It is a holding pen, not a permanent fixture, and a
stale "currently pending" list on `main` is worse than no list at all.
