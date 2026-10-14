# Overnight-coder handoff: bring CLAUDE.md back in line with the code and config.yml

*Staged 2026-10-14 from the code-modernization assessment (Technical Debt #9,
Documentation Gaps #1-#5). Read `docs/overnight-coder.md` first. Docs only; no runtime
change. Still a **DRAFT PR for Trisha's review**: CLAUDE.md is what every agent session
follows, so its wording is hers to approve. Recommended tier: **Sonnet**.*

## Invocation

Branch name suggestion: `claude-md-drift`.

## What is wrong (verify each against `main` in Step 1)

1. **Activation status contradicts `config.yml`.** CLAUDE.md says Streams L
   (violations), M (compliance actions), N (evaluations), O (permits) and Q (public
   notices), plus `gfl_info_site`, `pfas_pws` and `ridgewood.reverify_enabled`, "ship
   `false`". `config.yml` has them `enabled: true`. Only `nsite_complaints` is still false.
   Re-read `config.yml` for every stream; do not trust this list either.
2. **Stale path.** CLAUDE.md says Stream M's workflow is "parked at
   `docs/pending-workflows/`"; that folder holds only a README and the workflow is live at
   `.github/workflows/nsite-compliance-actions-watch.yml`.
3. **Modules missing from the architecture section** (running in production, unmentioned
   in CLAUDE.md and README): `kdp_check.py`, `egle_newsroom_watcher.py`,
   `gfl_feed_snapshot.py`, `gfl_air_hourly_capture.py`, `public_comment_feed.py`,
   `poison_doc_extractor.py`, `retry_policy.py`, `well_watch.py`, `config_loader.py`.
   Add one accurate line each (read the module and its ADR; `egle_newsroom_watcher.py`
   and `retry_policy.py` have no ADR, so describe only what the code does).
4. **A layering claim that is false.** CLAUDE.md calls `egle_doc_parser.py`
   "domain-agnostic: the risk register is passed in, never hardcoded", but it reads repo
   config through `kdp_check.default_reference()` (~726-728). And `nsite_client.py:93`
   imports `poison_doc_extractor` (which imports the parser), so every nSITE profile
   watcher loads the PDF/LLM stack. State both facts plainly; do not refactor them here.
5. **State model.** Add a short subsection listing which Sheet tabs are run STATE
   (`_state`, `_meta`, the `* Watch` snapshot tabs) versus DATA, and the rule that state
   reads must raise on failure (link `coder:state-reads-raise` / its ADR if merged first).

## Prefer generation over hand-sync for item 1

If it is simple, add `scripts/gen_stream_status.py` that prints a "what is live" table
from `config.yml`, plus a test that fails when CLAUDE.md's table differs from the
generated one (same pattern as `tests/test_sitemap.py`). If that grows beyond ~150 lines,
skip it and just fix the text.

## Also

`docs/topology/extract_topology.py`'s hardcoded module map is missing 32 of 67 modules, so
the generated viewer describes an older system. Either add the missing modules or add a
one-line STALE banner to the viewer's README; say which in the PR.

Fix the two workflow comments (`sast.yml:7`, `tests.yml:4`) that point at
`docs/session-2026-07-18-cd-governance-enforcement.md` (it lives in `docs/sessions/`).

Delete the applied one-off `scripts/oneoff_move_neighbor_rows.py` and its test (its own
line 27 says to delete it once applied; it was applied in `6b3c6b5`). Update the stale
`requirements.txt` comment about "no LICENSE file of its own yet" to say the code license
is undecided and `extract-msg` is GPL. Do not choose a license.
