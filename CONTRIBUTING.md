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

## Dependencies — bumping or adding a package

`requirements.in` is the human-edited input (loose `>=` bounds, with a comment
on anything non-obvious). `requirements.txt` is the **compiled, hashed
lockfile** every workflow actually installs from (`pip install --require-hashes
-r requirements.txt`) — never hand-edit it.

To bump a version or add a new package:

1. Edit `requirements.in` (change the bound, or add a new line with a comment
   saying what imports it and why).
2. Recompile against **Python 3.12** (what every workflow's `setup-python`
   step pins — a different interpreter can resolve different package
   versions or miss a `python_version` marker):

   ```
   python3.12 -m venv /tmp/lockvenv && /tmp/lockvenv/bin/pip install pip-tools
   /tmp/lockvenv/bin/pip-compile --generate-hashes --output-file=requirements.txt requirements.in
   ```

   `--generate-hashes` pulls hashes for every published distribution of the
   resolved version (sdist + all platform wheels, not just the one the
   compiling machine would install), so a lockfile compiled on a Mac still
   carries the `manylinux`/`x86_64` hash the `ubuntu-latest` runners need —
   confirm with `grep` for the package's Linux wheel filename on
   `pypi.org/pypi/<pkg>/<version>/json` if in doubt.
3. `pip install --require-hashes -r requirements.txt` into a clean venv and
   run `pytest -q` before pushing — a successful hashed install plus a green
   test suite is the proof the bump didn't change behavior unexpectedly.
4. Open a PR as usual. A version bump is a real behavior-risk change (see
   `docs/overnight-coder.md` Step 3's live-path rules) — don't bundle it with
   an unrelated feature change.
