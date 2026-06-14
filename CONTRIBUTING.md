# Contributing

## Commits — Conventional Commits

Format: `type(scope): summary`

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.

Examples:

- `feat(parser): add structured measurement extraction`
- `fix(watcher): write sheet row before state entry`
- `ci(data-guard): block *.pdf in addition to data files`

## Before you push

1. `pytest -q` passes (hermetic — no secrets needed).
2. No PDFs / data files / credentials staged (CI will block them anyway:
   `gitleaks`, `data-guard`).
3. `npx markdownlint-cli2 "**/*.md"` is clean if you touched docs.

## Branch protection

`main` requires the CI checks (`gitleaks`, `data-guard`, `markdownlint`,
`links`, `tests`) to pass and disallows direct pushes — open a PR.

## Setup

`scripts/setup.sh` takes a fresh clone to a runnable state in under 10 minutes.
Cloud credentials (GCP service account, SMTP, Anthropic) are documented in
`scripts/setup_gcp.md` and are only needed to actually run against Drive/Sheets/
email — not to develop or run the tests.
