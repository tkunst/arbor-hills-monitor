# ADR 066 — Ridge Wood monthly re-verification + committed provenance manifest

*Status: **BUILT, disabled** — 2026-10-12. Ships `ridgewood.reverify_enabled: false`;
a new job whose Drive/Sheet/email side effects reach a live stream stays off until a
human flips it on (overnight-coder procedure). This is the Ridge Wood slice of
`coder:selfreport-provenance`; the GFL slice (full-field capture, monthly full
snapshot + diff, hourly capture) is already done — PRs #108/#109/#110, ADR 063.*

## Context

Stream G (`ridgewood_archiver.py`, ADR 016) mirrors each NEW monthly Barr Engineering
Ridge Wood Elementary H2S report to Drive and records `Source URL`, `Content Hash`,
`Archive Link`, `Fetched At` in the `Ridge Wood Reports` tab — enabled since
2026-07-15, the ~66-month historical backfill mirrored. What it does NOT do: once a
month is recorded it is never looked at again (month-keyed dedup). Trisha's goal,
spelled out in `docs/overnight-coder-handoffs/selfreport-provenance-archive.md`
(staged 2026-10-08): "assume it could disappear or change; the only copy left is
ours, with provenance." Three concrete gaps:

1. A **silently revised** report is never noticed or re-captured — if Barr replaces
   a past month's PDF, our record still holds only the old version.
2. A report that **disappears** from Barr's page is never noticed.
3. There is **no single tamper-evident manifest** of the whole archive; provenance
   lives only in a Sheet tab anyone with edit access can change.

## Decision

A new standalone, monthly job (`ridgewood_reverify.py`, own workflow + concurrency
group) that re-downloads EVERY month Stream G has already archived (not just new
ones), plus a committed JSON manifest recording every report *version* ever seen.
Nothing in `ridgewood_archiver.py`, `ridgewood_client.scrape_report_links()` /
`download_report()`, or the `Ridge Wood Reports` tab's header is touched — Stream G
is live and this is strictly additive.

### 1. The provenance manifest — one entry per report VERSION, never mutated

`data/provenance/ridgewood-manifest.json`, shape `{month: [version, ...]}`. A
version is `{report_title, source_url, fetched_at, content_hash, archive_link,
status, last_verified}`, `status` one of `"original"` / `"revised <date>"` /
`"removed-from-source <date>"` per the handoff's own field list. A REVISED or
REMOVED month always gets a brand-new version appended — the prior version's hash/
link/status is never overwritten, so the manifest is a full history, not just a
current snapshot. The ONE mutation a routine "still matches, verified again" pass
is allowed is bumping `last_verified` on the CURRENT (the LAST entry in the
version list by append order, not a `fetched_at` comparison — see §6) version —
"record 'verified `<date>`' only, nothing else" is the handoff's own phrasing for
the unchanged case, and it is the common case every month for all ~66+ reports.

**No cryptographic signature** (decided by Trisha, 2026-10-08): the manifest is
committed to this public repo by the workflow on every run (even a no-op-content
run still bumps every month's `last_verified`, so there is always a real diff), so
**the git commit history is the "signature"** — a silent post-hoc edit to the file
on disk without a matching commit is exactly as suspicious as it is for any other
file in this repo, and that's accepted as sufficient. The file is written
deterministically (sorted keys, fixed indent) and atomically (temp file + rename).

### 2. Bootstrap from Stream G's own Sheet row, not from scratch

Stream G has been archiving since 2026-07-15 — long before this manifest existed.
The first time this job ever checks a given month there is no manifest entry, so
the "latest recorded hash" to compare against is seeded from the month's own
`Ridge Wood Reports` row (`sheet_writer.ridgewood_report_rows()`, a NEW read-only
helper — the real original fetch date/hash/link, not today's date). If that very
first check already finds a mismatch against freshly re-downloaded bytes, the run
records BOTH the seeded `"original"` version AND a `"revised <today>"` version in
the same pass, so a revision that happened before this job ever existed is still
caught the moment it exists, instead of silently becoming the new "original". A
blank Sheet hash (shouldn't happen, but Sheets are hand-editable) means "nothing to
compare against yet", not "already changed" — it seeds from today's download
instead of manufacturing a false revision.

### 3. Changed vs. removed vs. new-month-ignored

- **New month, not yet in Stream G's dedup set:** left alone entirely — Stream G
  owns new months; this job never races it.
- **Unchanged:** `last_verified` bumped on the manifest's current version. No Sheet
  write, no Drive upload, no alert (the handoff's explicit "nothing else").
- **Changed:** the new bytes are mirrored to the SAME Ridge Wood Drive folder
  (`GOAUTH_RIDGEWOOD_FOLDER_ID`) under a NEW filename
  (`ridgewood-h2s-<month>-revised-<date>-<hash8>.pdf` — the hash in the name, not
  just the date, so a retried run after a failed alert can't create a second
  duplicate copy of the identical bytes); the earlier copy is never touched or
  deleted. A new row goes to a SEPARATE append-only tab, `Ridge Wood Revisions`
  (never a second row in `Ridge Wood Reports` for a month that already has one —
  that tab is Stream G's one-row-per-month dedup marker).
- **Removed from source:** a NEW version is APPENDED with status
  `removed-from-source <date>` (copying the prior version's title/source_url/
  content_hash/archive_link — the removal is a status event, not a new PDF, but it
  still never mutates the version it supersedes, for the reason the next bullet
  needs); the mirrored copy is never touched. Confirmed with a SECOND, independent
  page fetch in the same run before declaring anything removed — a transient
  render glitch on one fetch must never fire a false removal. If more than half of
  the archived months are confirmed missing on both fetches, that reads as a page
  restructure or a scrape-format break, not that many real removals — the run
  aborts loudly instead of flagging a flood of false removals. A month that stays
  missing run after run is flagged once, not re-alerted every month.
- **Reappeared:** if a month previously flagged `removed-from-source` is later
  found listed again with content hash IDENTICAL to what the manifest already
  held, that's corrected by appending a fresh `"original"` version (carrying the
  prior version's archive_link forward — nothing new to mirror) rather than
  leaving the stale removed status to persist forever with only its
  `last_verified` silently advancing. This is why removal appends instead of
  mutating: the removed version's own content_hash has to survive for this
  comparison. (A review found the pre-fix code bumped `last_verified` on the
  "unchanged" path without ever checking whether the version it was bumping was
  flagged removed — a self-contradictory manifest entry with no correction path.)
  A reappearance with DIFFERENT content is just the ordinary "changed" path — a
  `revised <date>` version, same as any other revision.
- **Multiple links posted for one month:** `scrape_all_report_links()` (a NEW
  function in `ridgewood_client.py`, additive — `scrape_report_links()` itself,
  Stream G's live path, is untouched) returns every distinct link per month, not
  just the first. This job only ever checks the first (page order) for a content
  change, so a genuine second revision sitting beside it could be invisible to the
  hash check — flagged in the owner alert for a human look rather than guessed at.
  Logged once to `Ridge Wood Revisions` (event `multi-link`) and never re-alerted
  for the same month again — a persistent second link isn't a new condition every
  run, and alerting on it monthly forever would be exactly the kind of noise the
  rest of this design works to avoid.

### 4. Read-only exceedance callout on a revision — never a public alert

A revised report's content is read (via the existing `rc.extract_text` /
`rc.classify_report`, read-only) purely to name, in the owner alert/Sheet note,
whether the NEW bytes newly read as an exceedance compared with what was originally
recorded (`max_24h`/`alert` from the Sheet row). No Measurements write, no
re-running Stream G's own exceedance alert, no public/full-list routing — Stream G
owns that decision for NEW months; a revision to an already-archived, already-
alerted-on month is for Trisha to review, per the handoff's explicit instruction.

### 5. Alert routing — owner-only, always

Change/removal/multi-link alerts go through `ea.load_owner_emails(cfg)` exactly like
the GFL monthly snapshot's change alert (ADR 063) — NEVER `alert_recipients` or
`ridgewood.review_recipients` (Trisha only, not even the advocacy list). One
consolidated email per run covering every finding.

### 6. Crash/retry safety

Per changed/removed/reappeared month: the Drive upload (if any) and the
`Ridge Wood Revisions` Sheet row are written FIRST — durable and human-visible
even if nothing else succeeds — then the owner alert. The manifest's new version
is only merged into the in-memory manifest and SAVED TO DISK once the owner alert
actually sends. A run that writes the Sheet row and then fails to email (SMTP
down, empty owner list) exits 1 and leaves the manifest exactly as it was for
that month; the next run re-detects the same event and compares it against the
month's MOST RECENT logged event (`sheet_writer.ridgewood_last_event()`, which
also returns the already-uploaded Drive link). If that last event is this exact
(event, hash), it's an unresolved retry — skip the re-upload and the duplicate
Sheet row, just finish advancing the manifest + retrying the alert. **This
idempotency check covers all three of "revised"/"removed-from-source"/
"reappeared"** — a round-1 fix only added it for "revised" (keyed by the new
content hash); a round-2 review caught that "removed-from-source" and
"reappeared" had no equivalent guard, so a failed-alert retry duplicated their
Sheet row every retry (reproduced live: 2 retries → 2 identical rows). A
round-3 review then caught that round-2's OWN fix — keyed on "was
(month, event, hash) EVER logged", with no notion of recency — had its own bug:
month M removed (hash H) → reappears (hash H, a full alert-sent cycle) → removed
AGAIN (still hash H, nothing changed in between) is a genuinely new, third event,
but it hash-collides with the FIRST removal and was silently swallowed as a
stale retry of it (reproduced: 3 real removal occurrences across time, only 1
Sheet row). The fix scopes the check to "is this the event the month's LAST row
describes" rather than "was this ever logged" — a real second occurrence is
never the month's current last row (the reappearance's row is), so it's
correctly recognized as new even though its hash matches an older event. The key
for a removal is the *previous* content hash (there's no new content on a
removal); for a reappearance it's the hash of the content that came back — see
`ridgewood_last_event()`'s docstring. This also surfaced a second, more
fundamental bug while chasing the third: `latest_version()` picked the CURRENT
version by `max(fetched_at)`, and two versions appended within the same
wall-clock SECOND (`_now_iso()` is second-resolution) share an identical
`fetched_at` string — `max()`'s tie-break keeps the FIRST (oldest) one, so the
removal-then-reappearance sequence above could silently read the STALE removed
entry as "current" even after the reappearance had already been logged. Fixed
by making `latest_version()` return the LAST entry in the version list instead
(append order is chronological by construction; there is no tie to break).
Routine "unchanged, verified again"
progress for OTHER months in the SAME run is still saved even when one event's
alert fails — losing a harmless `last_verified` bump to a retried alert would be
the worse trade. A failed PDF download for one month is neither "changed" nor
"removed" — it's skipped, `last_verified` untouched, retried next run; the
overall run still exits 1 so a human notices. Per-month processing (main loop
AND the removal loop) catches any exception, not only the download error —
mirrors `ridgewood_archiver.py`'s own "one month's failure must not abort the
batch" `except Exception` around its whole per-month body, so an unexpected
failure (e.g. a transient Sheets write error) for one month doesn't discard
already-decided baseline/unchanged progress for every other month processed
earlier in the same run. (The multi-link Sheet write specifically has to live
INSIDE that same per-month try block, not before it — a round-2 review caught an
earlier draft where it sat just outside, so a Sheets failure there would have
propagated out of the loop uncaught.)

### 7. Two separate `*.json` gates — data-guard CI, and `.gitignore` itself

`data/provenance/ridgewood-manifest.json` is written and committed by the
**workflow's own direct push to `main`** (`contents: write`, same mechanism
`findings-feed.yml` already uses for `site/public-records/search-index.json`), never
through a pull request. `data-guard.yml`'s `block-data-files` job only runs
`on: pull_request`, so it never sees this file. This PR itself adds no `*.json` file
(the manifest doesn't exist until the flag is flipped and the workflow runs for
real), so CI on THIS PR is unaffected.

**That is NOT the same thing as `.gitignore`, and a terminal-round review caught
that this ADR originally only addressed the first.** This repo's `.gitignore` has
its own blanket `*.json` rule (the same data-file hygiene, enforced locally and in
every `git` command, not just in a PR-triggered CI job) with explicit `!`
exceptions for legitimate committed JSON — `package.json`, the markdownlint
configs, and `site/public-records/search-index.json` (which hit this *exact*
failure mode first, per its own comment in `.gitignore`: "the file would be
written on every run but never actually committed"). `data/provenance/
ridgewood-manifest.json` needed the identical exception and didn't have one —
`git add` on an explicitly-ignored path fails outright (confirmed by direct
reproduction: `git check-ignore -v` before the fix named this exact rule), which
would have made the workflow's commit step fail on **every single run**,
regardless of the `reverify_enabled` flag's state, with no Python-level signal at
all (the script itself always exits cleanly; only the shell `git add` call fails).
Added `!data/provenance/ridgewood-manifest.json` to `.gitignore`, pinned with a
test (`test_manifest_path_is_not_gitignored`), and reordered the workflow's
commit step to commit locally **before** pulling with `--rebase` (the previous
order pulled first, which fails on a dirty working tree — and `save_manifest()`
rewriting `generated_at` on every real run means the tree is *always* dirty by
this point) and to exit 0 cleanly before ever calling `git add` on a path that
doesn't exist yet (true on every run while the flag ships disabled).

### 8. Security

- `_download_capped()` (new, in `ridgewood_reverify.py` — NOT a change to
  `ridgewood_client.download_report()`, which stays untouched for Stream G) adds a
  20 MB response cap; this job re-downloads every listed month every run, so it is
  more exposed to a hostile/broken oversized response than Stream G's own
  new-months-only download path.
- `rc.allowed_report_host()` (new, additive) pins every scraped link to an https URL
  on the configured `page_url`/`base_url` host before downloading — defensive, since
  this job re-scrapes a fresh page fetch every run and is new code.
- Most caught exceptions are logged by class name only (`type(e).__name__`); a
  round-4 review noted three sites (the page fetch, the confirmation fetch, and a
  failed download) also print the underlying exception text — this is Stream G's
  OWN `RidgewoodFetchError`/network-error text (the public report-page URL plus a
  generic HTTP/urllib message, same as `ridgewood_archiver.py`'s own existing
  fetch-failure logging) or this module's own synthetic `ReverifyDownloadError`
  message, never a secret or credential, so there is no actual leak — but the
  blanket "class name only, never raw text" claim this bullet previously made was
  not quite accurate for those three sites.

## Out of scope (per the handoff)

- The offline Seagate copy and any restore (Mac-native, manual).
- Changing Stream G's new-month behavior, its extraction, or its alert routing.
- Any public-site page or public-records feed change.
- Regenerating `docs/topology/` (the generated dependency-map viewer). It is
  already ~30 streams stale (frozen at Stream E, 2026-07-14) and a full
  regeneration is a separate, disproportionate undertaking; this ADR plus the new
  `CLAUDE.md` architecture bullet are the documentation of record for this change.

## Done when / activation

Draft PR with the re-verify script + workflow (disabled by flag), the manifest
writer, tests green, this ADR, and a dry-run against the live page (see the PR
description for the captured output). Per the handoff: **DRAFT PR, not an
autonomous merge.** Activation (Trisha's call): review, merge, then set
`ridgewood.reverify_enabled: true` and commit. `GOAUTH_RIDGEWOOD_FOLDER_ID` is
already provisioned (shared with Stream G) and optional here exactly as it is there.

## Review history

Per `docs/overnight-coder.md`'s Step 7 convergence loop (cap: 3 resolve-and-
re-review rounds), four independent, diff-only subagent reviews ran against this
PR, each on the updated diff after the previous round's fixes:

1. Found a month that reappears unchanged after being flagged removed stayed
   stuck in that status forever (no correction path); per-month exception
   handling too narrow; no debounce on a repeating "multiple links" condition.
   Fixed.
2. Found the retry-idempotency guard (round 1's "revised" fix) wasn't extended to
   "removed-from-source"/"reappeared"; the multi-link Sheet write sat outside the
   per-month exception guard. Fixed.
3. Found round 2's OWN idempotency fix had a bug (keyed on "ever logged" instead
   of "most recently logged", so a real third event could hash-collide with and
   be swallowed as a retry of the first); while tracing it, also found
   `latest_version()`'s `max(fetched_at)` could tie-break to a stale entry. Fixed.
4. **This was the 3rd re-check — the cap.** Found a new-category, critical issue
   outside the Python logic the first three rounds examined: the workflow's
   commit step could not actually commit the manifest in ANY state (blocked by
   `.gitignore`'s blanket `*.json` rule; `git add` on a nonexistent path before
   the flag is ever enabled; a dirty tree blocking `git pull --rebase` on every
   real run) — confirmed by direct reproduction, not just review. Also flagged
   two lower-severity items (now listed as residual risks 5 and 6 below) and two
   stale doc phrasings (now corrected).

Per the procedure, round 4's findings were NOT resolved via a 5th independent
review (the cap). The `.gitignore`/workflow-commit bug was fixed directly and
verified **mechanically** (four hand-run scenarios against a scratch git repo
with a bare "origin": no manifest file yet, a brand-new manifest, a modified
tracked manifest, and a concurrent push from another workflow landing on `main`
in between — all four behave correctly) rather than by a further subagent
review, since it blocked the feature's core deliverable and had a narrow,
mechanical, already-precedented fix (the exact pattern `search-index.json`
already uses in this repo's `.gitignore`). Residual risks 5 and 6 below were
left open rather than fixed, per the cap.

## Consequences / residual risks (accepted)

1. A revision that happened to a month **before this job's very first run ever
   checked it** is still caught (see "Bootstrap" above) — but only once; there is no
   way to detect a revision that happened and was then revised AGAIN entirely before
   this job's first run, since only the final state would ever be downloaded.
2. A second report link posted beside an unchanged first link for the same month is
   flagged for a human, not auto-resolved — this job only ever hashes the first.
3. Rides the same undocumented public page Stream G already depends on; a scrape
   format break is caught the same way (a failed/empty scrape aborts the run loudly)
   plus the new >50%-missing sanity guard for a restructure that still returns SOME
   markup.
4. This re-verification pass runs monthly, not continuously — a revision live for
   less than a month between two runs is still caught on the next run (the manifest
   question is "did the bytes change since we last checked", not "when exactly").
5. **Open, NOT fixed in this PR (terminal review round reached its cap — see
   "Review history" below): the "multi-link" event type has no retry-idempotency
   at all.** `revised`/`removed-from-source`/`reappeared` all defer their Sheet
   row + manifest advance until the owner alert actually sends, and compare
   against the month's most recent logged event on retry (§6). "multi-link"
   instead logs its Sheet row unconditionally, before the alert is even attempted,
   with no linkage to whether that alert succeeds — `sheet_writer
   .ridgewood_multi_link_flagged_months()` dedupes purely on "was this ever
   flagged", not "was it successfully communicated". If the owner alert fails on
   the run that first finds a multi-link month (e.g. an empty owner list — an
   already-tested failure mode for the other three events), that specific
   notification is silently and permanently lost; the run still exits 1, so a
   human would see SOMETHING failed, but not which finding was dropped without
   checking the `Ridge Wood Revisions` tab by hand.
6. **Open, NOT fixed in this PR: a narrow, low-likelihood gap in the retry design
   itself** (found while tracing the fix for item 5's sibling bug in §6): if an
   owner alert fails for event A on a month, and before the retry succeeds the
   month's state changes AGAIN to a different event B, the retry only ever
   compares against and resends for the month's CURRENT state (B) — event A's own
   Sheet row and any Drive copy remain durable, but no email specifically about A
   is ever sent. No data is lost; one notification is. Considered unlikely enough
   (requires two real state changes to the same month within one unresolved
   alert-failure window) not to warrant a design change here.
