# Overnight-coder handoff: pin Python dependencies with a hashed lockfile

*Staged 2026-10-14 from the code-modernization assessment (Technical Debt #6, SEC-010).
Read `docs/overnight-coder.md` first. This changes how EVERY scheduled workflow installs
packages, so it is a live-path change: **DRAFT PR for Trisha's review, no autonomous
merge.** Recommended tier: **Sonnet**.*

## Invocation

Branch name suggestion: `pin-dependencies`.

## The problem

- `requirements.txt` uses only `>=` bounds and there is no lockfile. About 40 workflows
  run `pip install -r requirements.txt` on every scheduled run, so an upstream release of
  `anthropic`, `PyMuPDF`, `ocrmypdf` or `google-api-python-client` can break production
  overnight with no change in this repo.
- `pydantic` (used in `egle_doc_parser.py`) and `httplib2` (used in tests) are imported but
  only arrive transitively. `scripts/oneoff_move_neighbor_rows.py` imports `dotenv`, which
  is not declared at all (that script is also slated for deletion; see below).
- `.github/workflows/secrets.yml` installs gitleaks with `curl | sudo tar` and no checksum.
  Third-party actions are pinned to tags, not SHAs: `lycheeverse/lychee-action@v2`,
  `DavidAnson/markdownlint-cli2-action@v23`.

## What to build

1. Keep `requirements.txt` as the human-edited input (rename to `requirements.in` if you
   prefer, but keep `requirements.txt` working for anyone with a stale checkout). Declare
   `pydantic` explicitly if the code imports it directly.
2. Generate `requirements.lock` (or `requirements.txt` compiled from `.in`) with
   `pip-compile --generate-hashes`, against **Python 3.12** (what CI runs; the local venv is
   3.14, so compile in a 3.12 environment or with `--python-version`). `.txt` lockfiles are
   not blocked by the data-guard, but confirm.
3. Switch every workflow's install step to `pip install --require-hashes -r <lockfile>`.
   GovQA's separately pinned Playwright install stays as it is.
4. gitleaks: verify the downloaded tarball against its published SHA-256 before extracting.
5. Pin the two third-party actions to full commit SHAs, keeping the tag in a comment.
   (First-party `actions/*` can stay on tags; say so in the PR.)
6. A short `CONTRIBUTING.md` note on how to bump a dependency (edit input, recompile, PR).

## Out of scope

Upgrading any package. Pin what CI resolves TODAY so behavior does not change.

## Verification (Step 3)

The full test suite green on the PR with the lockfile install; then trigger one cheap
scheduled workflow (e.g. `meeting-watch` or `rop-watch`) with `workflow_dispatch` on the
branch and paste its green run link. Diff `pip freeze` before/after to show nothing
upgraded.
