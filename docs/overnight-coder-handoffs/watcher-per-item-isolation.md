# Overnight-coder handoff: per-item isolation for the 8 watchers PR #135 left alone

*Staged 2026-10-14 (Trisha-directed) — the optional follow-up PR #135 offered and
declined. Read `docs/overnight-coder.md` first. This touches 8 LIVE watchers
(`config.yml` all `enabled: true`), so per Step 3 it needs real-specimen verification
and ends at a **DRAFT PR for Trisha's review, no autonomous merge.**
Recommended tier: **Sonnet**.*

## Background

PR #135 (`fix(sheet_writer): state/snapshot reads raise on a transient error, not []`,
merged) made every state/snapshot Sheets reader that diffs or baselines (`read_state`,
`read_meta`, and the `last_*_snapshot(s)` family) RAISE on a transient error instead of
silently returning `[]`/`None`. Read its description and comments
(`gh pr view 135 --comments`) for the full reasoning.

One watcher, `pfas_pws_watcher.py`, already had a per-item `try/except Exception` around
its per-WSSN body (for an unrelated, pre-existing reason: isolating a Sheets-write
hiccup or a malformed record from aborting the other WSSNs). PR #135 found that this
existing `except` would have *swallowed* the new strict-reader raise into a green run,
and fixed it: the per-item catch stays (other WSSNs still process), but `run()` now
returns `1` if any WSSN failed.

The other 8 watchers that call a now-strict reader — `rop_watcher`, `mmd_watcher`,
`ride_watcher`, `nsite_submissions_watcher`, `pfas_watcher`, `civicclerk_watcher`,
`gfl_info_site_watcher`, `wds_archiver` — have no such per-item catch. A transient read
failure on ONE item currently crashes the WHOLE run (uncaught exception propagates out
of `run()`). PR #135's Step 5 review flagged this as an inconsistency; the build
session's disposition (independently re-verified in round 2) was to **decline**, on the
grounds that:

- nothing regressed (the old lenient reader never raised, so there was nothing to
  isolate from before this PR);
- the six already-raising nSITE profile watchers are a legitimate precedent for "a read
  failure fails the whole run," since their own per-item isolation covers writes only;
- extending it anyway costs each failing item its own full `GOOGLE_API_NUM_RETRIES`
  backoff, inside a job with a bounded `timeout-minutes`.

Trisha's direction (2026-10-14): build the optional follow-up anyway. This handoff
captures that build.

## What to build

Extend per-item isolation to the 8 watchers named above, so that:

1. **Isolate per ITEM only where the code genuinely reads and processes each item
   independently.** Where a watcher does ONE batched read of its snapshot tab for ALL
   items (there is nothing to isolate), a failure of that batched read must still fail
   the whole run — do not split a batched read into N reads just to isolate it (each
   would pay the full Sheets retry backoff). Document, per watcher, which case applies
   (see the disposition table below).
2. Inside each isolated item: log a **class-only** error — `type(e).__name__` plus the
   item's own key/id (srn, wdsid, event id, collection name — never a meeting title,
   file name, or the raw exception string, since `str(HttpError)` embeds the request
   URI, which contains the spreadsheet id) — skip that item's write/alert, continue to
   the next item. **Never swallow into a green run:** if any item failed, the run
   returns/exits non-zero at the end, after every other item has been given a chance to
   process.
3. Preserve every existing invariant: Sheet-row-before-state and row-before-email
   ordering, no re-alert on retry, the 2-run removal debounce in `gfl_info_site`,
   liveness/stale checks, anti-stampede caps. A failed item must be retried on the next
   run against its real stored snapshot — no partial snapshot written for it.
4. Do NOT touch the six nSITE profile watchers (violations/compliance actions/
   evaluations/permits/complaints/public notices) or `pfas_pws_watcher.py` — both are
   already-correct precedents, out of scope here.

## Per-watcher disposition (read this before writing any code)

| Watcher | Item granularity | Batched read left unisolated (fails whole run) | Breaker? |
|---|---|---|---|
| `rop_watcher` | one per `_diff_and_record` call (csv:SRN / folder:N2688 / notice:SRN) — `last_rop_snapshot` is already its own full-tab read per key, not shared | `_all_baselined` (inside each `except RopFetchError` handler) — uses the batched `last_rop_snapshots` for one source's keys | no (N ≤ 7) |
| `mmd_watcher` | one per wdsid | `_all_baselined`-equivalent on the fetch-failure path (`last_mmd_snapshots`) | no (N = 2) |
| `ride_watcher` | one per site / per facility | per-layer `_all_baselined` (`last_ride_snapshots`) | no (N ≤ 7) |
| `nsite_submissions_watcher` | one per site (already its own fetch per site; the Sheet read is per-site too) | none batched — every site's read is independent; the registry `KeyError` lookup stays outside any try (a config typo must stay loud) | **yes** — up to 44 `tiers` entries; a daily cluster of biweekly/quarterly due-windows could exceed `timeout-minutes: 15` under a real outage |
| `pfas_watcher` | one per configured page | none (read happens before fetch, per page) | no (configured N = 1 today, but no hard cap either — noted as residual) |
| `civicclerk_watcher` | one per meeting event (both hand-picked `events:` and auto-discovered) | the per-GROUP `meeting_watch_group_has_rows` call inside the auto-discover fetch-failure handler is its own single read — isolate it at the GROUP level only (so one group's read failure doesn't abort the other groups), never split into per-event reads | **yes** — MMPC/DPA auto-discover over `discover_since_days: 400` plus BOC's hand-picked list can run 50-100+ events/day; the largest N of the eight |
| `gfl_info_site_watcher` | one per URL, **Phase 1 (classify) only** | the pre-loop `gfl_info_site_tab_is_empty` + `all_gfl_info_site_urls` reads — fail the whole run; Phases 2/4/6 are deliberate ATOMIC batch appends (`sw.append_rows`) and must stay un-split; Phase 5's per-page loop is also left un-isolated (see trap below) | **yes** — sitemap discovery can surface many URLs |
| `wds_archiver` | one per collection | the pre-loop `read_meta` — fails the whole run before any collection is touched | no (N ≈ 8 collections) |

## Two invariant traps found during the design pass (fix before writing tests)

1. **`gfl_info_site_watcher`'s anti-stampede cap must count read-failed URLs, not just
   successfully-classified "new" ones.** Example: cap = 3, and 5 pages would otherwise
   look new, but 2 of those 5 hit a Sheets read failure during Phase 1. Counting only
   the 3 successfully-classified "new" events would send 3 alert emails now and silently
   discover 2 more next run — a real behavior change from today's crash-the-whole-run
   (which instead retries all 5 next run and, if under the cap then, re-baselines them
   silently with zero emails). Fix: count read-failed, non-seed URLs toward the
   `stampeded` threshold using the full candidate set, not just the ones that finished
   classifying. Do NOT defer Phase 2 entirely if anything in Phase 1 failed — a single
   persistent bad URL (e.g., a slow or wedged page) would then suppress every genuine
   new-page alert forever. A read-failed item also counts toward the `failed` tally so
   the existing ALL-OR-NOTHING initial-baseline guard and the BLIND liveness guard both
   still fire correctly on an activation-time outage.
2. **`wds_archiver`'s per-page upload `except Exception` already swallows into a green
   run — this predates PR #135 and is the same bug class pfas_pws_watcher had.** Fixing
   per-collection `read_meta`/`write_meta` isolation without also setting `exit_code = 1`
   on that pre-existing per-page upload failure would be an inconsistent half-fix (one
   failure mode reddens the run, the other doesn't, for the same collection). Fix both in
   this PR and call it out explicitly as a behavior change for Trisha: a page-upload
   failure that used to leave the run green now makes it exit 1. Also note: because
   `hashes` is one shared in-memory dict across the collection loop, a collection whose
   own `write_meta` fails can still have its hash persisted later by a SUBSEQUENT
   collection's `write_meta` call (which re-reads `_meta` fresh and writes the WHOLE
   accumulated `hashes` dict back) — so "this item's snapshot was not advanced" is not
   quite true for `wds_archiver` the way it is for the append-only watch tabs. Document
   this plainly so a reviewer doesn't flag it as a bug.

## Tests

- PR #135's existing per-watcher "raises without writing" tests
  (`test_run_raises_without_writing_when_<x>_watch_read_fails` in `test_rop.py`,
  `test_mmd.py`, `test_ride.py`, `test_nsite_submissions.py`, `test_pfas_watcher.py`,
  `test_civicclerk_watcher.py`) arm EVERY read of the watch tab to fail, which after this
  change is caught per item — update these to assert `run() == 1` (not
  `pytest.raises`), with the no-new-rows/no-email assertions kept. Say so explicitly in
  the PR; don't claim these tests are unchanged.
- `test_gfl_info_site.py`'s and `wds_archiver`'s equivalent tests arm the PRE-LOOP
  read, which stays unisolated — keep those as `pytest.raises`, and add a new,
  separate test pinning the Phase-1 / per-collection isolation specifically.
- New tests per watcher: arm exactly ONE item's read to fail (by call ordinal on the
  watch tab, since every item's read hits the same range) and assert the OTHER items
  still get their rows/emails, the run returns/exits 1, and the failed item's own row
  count is unchanged (retried next run against its real snapshot).
- A log-scrubbing test per watcher: raise with a message containing a sentinel string
  (e.g., `"SENTINEL-DO-NOT-PRINT"`) and assert it does NOT appear in captured stdout —
  pins the class-only logging rule.
- `nsite_submissions_watcher` and `civicclerk_watcher` get a breaker test: N consecutive
  item-level failures (3, matching `govqa_watcher`'s existing `_CIRCUIT_BREAKER`
  precedent) abort further item processing for that run, still returning 1.
- Existing tests must stay green.

## Real-specimen verification (Step 3) — this is an orchestration change, not a reader
change, so PR #135's reader-level live-Sheet probe does not cover it

Do a dry-run differential instead: extract `git archive origin/main` into the
scratchpad, run each of the 8 watchers' `run()` against both the pre-change code and
this branch with `dc.sheets_service()` monkeypatched to an allow-list proxy over the
REAL live Sheet (pass through `spreadsheets().get` / `values().get`/`batchGet`; record
and no-op everything else — `values().update`/`append`/`batchUpdate`/`clear` — so
`ensure_*_tabs`' header rewrite and any append are captured, not sent), `ea.send_email`
stubbed to record (with `smtplib` raising as a backstop), and for `wds_archiver`,
`archive_client.is_configured`/`oauth_drive_service`/`upload_file` stubbed too. Freeze
each module's `_now`/`_today`. Confirm:

- the healthy-path recorded writes/emails are byte-identical between old and new code;
- injecting one per-item read failure makes the new code return 1 while the other
  items' recorded writes are unchanged, and the old code crashes (proving the isolation
  is the only behavior delta).

## Out of scope

- The six nSITE profile watchers and `pfas_pws_watcher.py` (already correct).
- `docs/overnight-coder-handoffs/sheet-writer-watcher-consolidation.md`'s generic
  `ensure_tab`/`append_watch_row`/`last_snapshots` refactor.
- `civicclerk_watcher.run_historical_backfill` — a one-time, never-scheduled function
  whose invoking script/workflow were already deleted after use.
