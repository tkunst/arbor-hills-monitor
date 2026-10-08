# Overnight-coder handoff — Ridge Wood report re-verification + provenance manifest (selfreport-provenance, Ridge Wood slice)

*Staged 2026-10-08. Design decisions ruled by Trisha 2026-10-08 (see "Decided"). Read
`docs/overnight-coder.md` first; this is a goal handed to that loop, not a new
procedure. The GFL slice of this item is DONE (PRs #108/#109/#110, ADR 063). This
handoff covers the remaining Ridge Wood slice only.*

## Invocation

Point the loop at this file. Branch name suggestion: `ridgewood-provenance`.
Open a DRAFT PR. Do not merge.

## Goal

Stream G (`ridgewood_archiver.py`, ADR 016) already mirrors each NEW monthly Barr
Engineering Ridge Wood Elementary H2S report to Drive and records `Source URL`,
`Content Hash`, `Archive Link` and `Fetched At` in the `Ridge Wood Reports` tab.
It is enabled (since 2026-07-15) and the 66-month backfill is mirrored.

What it does NOT do: once a month is recorded it is never looked at again
(month-keyed dedup). So:

1. **A silently revised report is never noticed or re-captured.** If Barr replaces
   a past month's PDF, our record still holds only the old version and nobody is told.
2. **A report that disappears from Barr's page is never noticed.**
3. **There is no single tamper-evident manifest** of the whole archive; provenance
   lives only in a Sheet tab that anyone with edit access can change.

Build a monthly **re-verification pass** plus a **committed provenance manifest**
that closes those three gaps. This is the "assume it could disappear or change;
the only copy left is ours, with provenance" insurance for the Ridge Wood source.

## Scope

- **Monthly re-verify job** (new script, e.g. `ridgewood_reverify.py`, its own
  workflow and concurrency group, monthly cron away from the 3-8am cluster):
  - Scrape the current report list from the Ridge Wood page (reuse
    `ridgewood_client`; never construct URLs, the `_NNNN` suffix is unpredictable).
  - For every listed report: download, compute `content_hash`, compare with the
    latest recorded hash for that month.
    - **Unchanged:** record "verified <date>" only (manifest), nothing else.
    - **Changed:** upload the new bytes to the Ridge Wood Drive folder as a NEW
      file (suffix the fetch date; NEVER overwrite or delete the earlier copy),
      append a new `Ridge Wood Reports`-style row or a separate change-log row
      (coder's choice; do not mutate the existing row), and send an owner-only
      alert.
    - **New month not yet archived:** leave it alone; Stream G owns new months.
  - For every month we hold that is **no longer listed** on the page: owner-only
    alert ("removed from source; our copy remains at <Drive link>"). Do not touch
    our copy.
  - Do NOT re-extract H2S values or re-run the exceedance alert on a changed
    report in this job; flag the change and let Trisha decide. (A revised report
    that newly shows an exceedance should be called out in the change alert text.)
- **Provenance manifest**: write `data/provenance/ridgewood-manifest.json` (or
  similar) with one entry per report version: month, report title, source URL,
  fetched-at timestamp, SHA-256 content hash, Drive file id/link, status
  (original / revised <date> / removed-from-source <date>), last-verified date.
  The workflow commits it to the repo. **The git commit history is the timestamp
  "signature"** (decided; no cryptographic signing).
- **Alerts:** owner-only via `ea.load_owner_emails(cfg)`, same routing as the GFL
  change alert in `gfl_feed_snapshot.py`. Never the public or commissioner lists.
  A real H2S exceedance alert from Stream G keeps its existing full-list routing;
  this job does not send exceedance alerts.
- **Gating:** ship behind a new config flag (e.g. `ridgewood.reverify_enabled:
  false`). Trisha flips it on after review.
- **Tests:** unchanged / changed / removed / new-month-ignored cases with mocked
  page + downloads; manifest round-trip; owner-only recipient assertion.

## Out of scope

- The offline Seagate copy and any restore (Mac-native, manual).
- Changing Stream G's new-month behavior, its extraction, or its alert routing.
- Any public-site page or public-records feed change.

## Decided (Trisha, 2026-10-08)

- Git-commit timestamp is sufficient as the manifest "signature."
- Change and removal alerts go to Trisha only (owner list).

## Done when

DRAFT PR open with the re-verify script + workflow (disabled by flag), manifest
writer, tests green, an ADR (next number) recording the design, and a dry-run
output against the live page showing all 66+ months as unchanged (or listing any
real differences found).
