# Session 2026-07-16 — ROP watch (Stream H): build, activate, and close the Step 7 gap

Overnight-coder loop against
`/Volumes/Samsung-Pro-2TB/Lotext/handoffs/2026-07-15-overnight-coder-rop-watch.md`,
followed by same-session activation, a live-trigger incident, a deliberate
Step 7 self-audit, and a second review pass that found — and fixed — real bugs
in the first fix commit.

## What it is

A new watch stream on EGLE's ROP (air Title V permit) renewal process for all
three Arbor Hills air facilities (N2688 landfill, N1504 Energy, P1488 Emerald
RNG), each with a renewal IN PROCESS as of 2026-07-15. A renewal reaching its
30-day public comment window is a second advocacy venue, easy to miss buried in
a ~1,800-row statewide export. See `docs/decisions/017-rop-watch.md` (ADR 017)
for the full design.

Five watched items across three sources, each independently baselined/diffed
in the new `ROP Watch` tab: the EPA ROP Monthly Report CSV (one item per target
facility, filtered by exact SRN so M3333's "Emerald Spa Corp" name-collision is
excluded by construction), the N2688 renewal folder's file list, and the
statewide public-notice PDF's N2688 mention.

## Shipped, in two PRs

### PR #17 → `main` @ `3cec09e` (build)

- `rop_client.py` (fetch + parse, pure where possible) + `rop_watcher.py`
  (snapshot/diff/alert), modeled on `pfas_watcher.py`/`civicclerk_watcher.py`.
  `sheet_writer.py` additive (`ROP Watch` tab + helpers); `config.yml` additive
  (`rop:` block); `.github/workflows/rop-watch.yml`; `tests/test_rop.py` (44
  tests, CSV fixture built from trimmed VERBATIM real rows, not fabricated);
  ADR 017; README/CLAUDE.md architecture lines.
- Ships **`rop.enabled: false`** (new-source gate, per overnight-coder Step 3).
- **`/code-review`** (10-angle, xhigh effort) found 4 real defects — alert-
  clarity information loss (only `task_status` shown, so a `permit_status`-only
  change rendered two identical lines), an unguarded `fitz.open()` that could
  crash the run on a truncated notice PDF, an `any()`/`all()` baseline-gating
  bug (`_any_baseline` → `_all_baselined`, semantics fixed), and
  `RopParseError` wrongly bucketed with `RopFetchError` under the same
  skip-if-baselined logic. Fixed in `f0a849e`, each with a regression test.
- **`/security-review`**: zero medium/high findings. Traced the one
  externally-influenced sink (folder/PDF content into the Sheet's snapshot-
  JSON cell) to `append_rows`' `valueInputOption="RAW"`, same as every sibling
  tab — no formula-injection path; confirmed no untrusted content reaches an
  email header; confirmed the three fetch URLs are hardcoded constants (the
  dead `csv_url`/`folder_url`/`notice_url` config overrides — never set,
  documented, or tested — were removed in the same review-fix commit).
- ADR 017 Section 4 corrected (`3cec09e`) before merge to match the merged
  code's actual `RopParseError`-always-loud behavior (the first draft still
  described it as sharing `RopFetchError`'s baseline-gated logic).
- An unrelated, pre-existing flaky test (`test_gfl_air.py`'s no-new-readings
  fixture anchored to a fixed `DAY0` instead of real time) was found and fixed
  separately, straight to `main` as `1070fc7`, **before** cutting the
  `rop-watch` branch — verified via `git stash` not caused by this work.

### Activation + first live run (same session, human-directed, outside the loop)

- Trisha explicitly asked to flip `rop.enabled: true` and merge — the
  overnight-coder loop never takes this step itself; a human always does.
  Committed directly to `main` as `f9f3763`, matching the established
  one-off activation-commit pattern (WDS/MMPC/PFAS/GFL-air/Ridge Wood before
  it — `config.yml` only, one line, no PR).
- First manually-triggered run **partially failed**: the CSV fetch hit an
  HTTP 406 from `egle.state.mi.us`'s WAF — correctly loud (`exit 1`) since no
  CSV baseline existed yet, per the fail-safe design. The folder and notice
  items succeeded and baselined normally in the *same* run (the "partial
  activation block, not all-or-nothing" guarantee working as designed).
  An immediate retry succeeded completely — all 5 items baselined, 0 alerts.
  Confirmed **transient**, not a persistent GitHub-Actions-IP block (reproduced
  the exact request from a residential IP too, both succeeding and failing
  across attempts — genuinely flaky WAF behavior, not IP-based).
- GitHub's own "Run failed" email for that first attempt was the same event
  already diagnosed and resolved — not a new incident.

### Step 7 self-audit (human-prompted)

Trisha asked directly whether this session had followed
`docs/overnight-coder.md`. Self-assessment (checked against an advisor pass):
Steps 1–6, 8, 9 were followed correctly, but **Step 7 (convergence loop) had a
real gap** — after `/code-review`'s fixes landed in `f0a849e`, that fix commit
itself was never re-reviewed. Regression tests + a full pytest run + manual
real-data re-verification were substituted, but those aren't equivalent to a
second adversarial pass: tests catch regressions in known behavior, not new
bug classes a fix introduces — precisely the failure mode Step 7 exists to
catch (this repo's own ADR 011 history: a fix introducing its own bug, twice).

### PR #18 → `main` @ `5b5bd0a` (closing the gap)

A dedicated second `/code-review` — 10 finder angles + a gap-sweep, scoped
specifically to `git diff f0a849e^..f0a849e` rather than the whole PR — found
the gap was real. Six confirmed findings, all fixed:

1. **Crash isolation lost.** `_diff_and_record` moved `format_change_body()`
   outside its try block (to fix the earlier misattribution bug), but `run()`
   never wraps its calls to `_diff_and_record` in anything — an exception
   there now crashed the *entire* run instead of failing one item,
   contradicting ADR 017's per-item "partial activation block" guarantee.
   Fixed: `format_change_body` gets its own try, distinct from the
   email-send try.
2. **`_detail()` incomplete.** Only ever showed a hand-picked subset (4 of 12)
   of the fields actually in a row's hash/diff identity — a change confined to
   `rop_action`, `rop_action_status`, `issue_date`, `effective_date`, or
   `expiration_date` still rendered two identical-looking ADDED/REMOVED lines,
   reproducing the exact bug the earlier fix claimed to close. Fixed: derives
   its field list from `_FACILITY_ROW_FIELDS` directly so it can't drift again.
3. **Over-broad exception scope.** `notice_mentions_srn`'s `except Exception`
   mislabeled *every* PDF-parsing failure — including a genuine future bug —
   as transient. Since `notice:N2688` already has a live baseline, that meant
   silent skip-and-warn forever. Narrowed to `fitz.FileDataError` (confirmed
   `fitz.EmptyFileError` is a subclass, so no real corruption case regresses).
4. **Two stale docstrings** (rop_watcher.py's module header, rop_client.py's
   `RopParseError` class) still asserted the old, now-false "same treatment as
   RopFetchError" contract. Corrected.
5. **Efficiency regression.** The `any()`→`all()` correctness fix in
   `_all_baselined` turned a best-case 1-network-call check into a guaranteed
   N sequential Sheets reads. Added `sheet_writer.last_rop_snapshots`
   (batched — one tab read for however many keys).
6. **Dead code** — an unreachable `except RopFetchError: raise` clause,
   resolved as a side effect of fix #3.

Every fix has a regression test that fails on the pre-fix code (48 ROP tests,
up from 44; 383 total). Re-verified live: `fetch_folder_listing`/
`parse_folder_listing` and `fetch_notice_pdf`/`notice_mentions_srn` against the
real N2688 folder and statewide notice PDF — unchanged results (11 folder
entries, N2688 not yet mentioned). `fetch_csv`/`parse_csv_rows` untouched by
this PR; hit the same known intermittent WAF 406 during this verification pass
— unrelated to the diff, already handled by the existing fail-safe.

`/security-review` on this diff: zero findings. Specifically ruled out the one
candidate worth checking (the expanded `_detail()` field list) on three
grounds: the Sheets sink is still `RAW`-mode, the email sink is still
plain-text (`msg.set_content`, no HTML part), and the data was already fully
serialized into the Snapshot JSON column pre-PR — this only makes
already-stored, already-public EGLE data render legibly in the diff line, no
new audience or new sink.

## Process notes

- Both PRs followed the full gate sequence (`/code-review` → fix → `/security-
  review` → merge only with zero open items), even though PR #18 was a
  same-session, human-directed follow-up rather than a fresh `/loop` — the
  discipline doesn't relax just because the human is actively driving.
- `.claude/COORDINATION.md` claimed at start, closed with a full summary at
  the end of the PR #17 arc (surfaces released); the top banner is updated
  here too, not just a log entry appended — this file's own documented lesson
  from a prior session (Session-Cowork, 2026-07-13) about a stale banner
  misleading a later session.
- Step 9 (overnight-coder queue archiving) was checked and correctly
  **skipped** — this handoff was an ad-hoc paste, not a `Goal + full spec:`
  entry in the Cowork coder queue.
- The live-trigger incident is a useful data point for future sessions: this
  specific EGLE CSV endpoint's WAF is genuinely flaky (observed failing and
  succeeding across consecutive attempts from *both* a GitHub Actions runner
  and a residential IP with identical requests) — not deterministically tied
  to network origin or request headers. The existing `RopFetchError`
  skip-if-baselined-else-loud fail-safe is the correct, already-built answer;
  no code change was needed to "fix" the flakiness itself.

## Final state

- `main` @ `5b5bd0a`, clean, in sync with origin. 383 tests green.
- `rop.enabled: true` — live, all 5 items baselined, 0 alerts fired (correct
  for first sighting). Daily 10am ET runs (`rop-watch.yml`) will diff and
  alert on any future change.
- No open PRs, no stray branches (`rop-watch` and `rop-watch-review-fixes`
  both deleted post-merge, local + remote).
- No `.claude/COORDINATION.md` surfaces outstanding for this stream.
