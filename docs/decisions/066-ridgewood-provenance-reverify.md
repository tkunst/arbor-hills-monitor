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
is allowed is bumping `last_verified` on the CURRENT (latest `fetched_at`) version —
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

Per changed/removed month: the Drive upload (if any) and the `Ridge Wood Revisions`
Sheet row are written FIRST — durable and human-visible even if nothing else
succeeds — then the owner alert. The manifest's new version is only merged into the
in-memory manifest and SAVED TO DISK once the owner alert actually sends. A run that
writes the Sheet row and then fails to email (SMTP down, empty owner list) exits 1
and leaves the manifest exactly as it was for that month; the next run re-detects
the same change, recognizes its own already-logged Sheet row by
`(month, new content hash)` (`sheet_writer.ridgewood_revision_keys()`, which returns
the already-uploaded Drive link too), skips re-uploading a duplicate copy, and just
finishes advancing the manifest + retrying the alert. Routine "unchanged, verified
again" progress for OTHER months in the SAME run is still saved even when one
changed/removed month's alert fails — losing a harmless `last_verified` bump to a
retried alert would be the worse trade. A failed PDF download for one month is
neither "changed" nor "removed" — it's skipped, `last_verified` untouched, retried
next run; the overall run still exits 1 so a human notices. Per-month processing
(main loop AND the removal loop) catches any exception, not only the download
error — mirrors `ridgewood_archiver.py`'s own "one month's failure must not abort
the batch" `except Exception` around its whole per-month body, so an unexpected
failure (e.g. a transient Sheets write error) for one month doesn't discard
already-decided baseline/unchanged progress for every other month processed
earlier in the same run.

### 7. Why no data-guard allowlist entry was needed

`data/provenance/ridgewood-manifest.json` is written and committed by the
**workflow's own direct push to `main`** (`contents: write`, same mechanism
`findings-feed.yml` already uses for `site/public-records/search-index.json`), never
through a pull request. `data-guard.yml`'s `block-data-files` job only runs
`on: pull_request`, so it never sees this file. This PR itself adds no `*.json` file
(the manifest doesn't exist until the flag is flipped and the workflow runs for
real), so CI on THIS PR is unaffected.

### 8. Security

- `_download_capped()` (new, in `ridgewood_reverify.py` — NOT a change to
  `ridgewood_client.download_report()`, which stays untouched for Stream G) adds a
  20 MB response cap; this job re-downloads every listed month every run, so it is
  more exposed to a hostile/broken oversized response than Stream G's own
  new-months-only download path.
- `rc.allowed_report_host()` (new, additive) pins every scraped link to an https URL
  on the configured `page_url`/`base_url` host before downloading — defensive, since
  this job re-scrapes a fresh page fetch every run and is new code.
- Every caught exception is logged by class name only (`type(e).__name__`), never
  the raw exception text — consistent with this repo's public-stdout discipline.

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
