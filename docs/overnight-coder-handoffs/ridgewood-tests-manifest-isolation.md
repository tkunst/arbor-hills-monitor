# Overnight-coder handoff — isolate the Ridge Wood re-verify tests from the committed manifest

*Staged 2026-10-12 (Trisha-directed, Cloud mode). Small, code-only fix. Read
`docs/overnight-coder.md` first (Cloud mode: stop at a DRAFT PR; no live credentials).*

## Problem

`main` has 9 failing tests in `tests/test_ridgewood_reverify.py` (every PR's `pytest`
check shows them):

- test_first_run_matching_sheet_hash_baselines_silently_no_alert
- test_changed_report_mirrors_logs_and_alerts
- test_changed_report_empty_owner_list_fails_loudly_and_does_not_advance_manifest
- test_changed_report_email_send_failure_does_not_advance_but_keeps_sheet_row
- test_removed_from_source_confirmed_on_two_fetches_alerts_and_flags
- test_new_month_not_yet_archived_is_left_alone
- test_multi_link_month_is_flagged_in_owner_alert
- test_download_failure_is_skipped_not_changed_or_removed
- test_multi_link_sheet_write_failure_does_not_abort_other_months

They passed at `09b95b5` ("activate Ridge Wood monthly re-verification") and fail
from `5e0b5b4` ("chore(ridgewood): monthly re-verification manifest update"), the
workflow's first real run, which committed a populated
`data/provenance/ridgewood-manifest.json` (776 lines).

Likely cause (verify, don't assume): `ridgewood_reverify.MANIFEST_PATH` is a
relative path (`data/provenance/ridgewood-manifest.json`) and `run()` /
`load_manifest()` / `save_manifest()` default to it, so the end-to-end tests read
(and may WRITE) the real committed manifest instead of an isolated one. With an
empty manifest the tests' expectations held; with the real one they don't.

## Goal

1. Confirm the cause by reproducing on `main`.
2. Make the tests hermetic: every test that exercises `run()` (or anything that
   loads/saves the manifest) uses a `tmp_path` manifest — e.g. a fixture that
   monkeypatches `ridgewood_reverify.MANIFEST_PATH` (and any default arguments
   bound at import time) to a temp file. Prefer a test-side fix; change production
   code only if the default path is captured in a way tests can't override
   (then add an explicit `manifest_path` parameter threaded from `run()`, default
   unchanged).
3. Add a guard test: running the suite must never modify
   `data/provenance/ridgewood-manifest.json` (e.g. hash it before/after in a
   session-scoped fixture, or assert the patched path is under tmp).
4. Do NOT edit `data/provenance/ridgewood-manifest.json` or the workflow.

## Done when

- Full suite green locally and in CI (no other failures introduced).
- The committed manifest is byte-identical before and after `pytest`.
- DRAFT PR titled `fix(tests): isolate ridgewood re-verify tests from the committed manifest`,
  with the reproduction and root cause in the description. No auto-merge.
