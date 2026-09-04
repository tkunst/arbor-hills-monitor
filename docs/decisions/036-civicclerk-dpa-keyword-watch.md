# ADR 036 — CivicClerk DPA group + keyword watch (ADR 015 addendum)

*Status: built — 2026-09-03. Ships `civicclerk_watch.enabled: true` (already
live) and `civicclerk_watch.keyword_scan.enabled: true`. The new DPA group and
the keyword scan both baseline/scan silently on their first pass — see
Activation below for the real-specimen false-positive-rate evidence this
shipped on (84 events / 161 files / 32 hit-carrying meetings, ~16% pure
noise). The one-time 12-month backfill's actual Sheet-write + email run
happens post-merge (see Decision 4's "Why the backfill needed to run via
workflow_dispatch" — GitHub won't dispatch a workflow that only exists on a
branch).*

## Context

Every other watcher in this repo depends, directly or indirectly, on EGLE's
nSITE system: a document filed under N2688, a permit, a violation, an
evaluation. But GFL's landfill-expansion siting arc — a plan amendment, a
Materials Management Plan (MMP) consistency determination, a "Good Neighbor
Plan" commitment, a siting application — can be **agendized on a Washtenaw
County public body** (the DPA, the MMPC, the Board of Commissioners) without
ever touching nSITE at all. This is the county-side/off-nSITE gap Trisha asked
to close: (1) watch the Board of Public Works (categoryId 68, the DPA) — a
category ADR 015's build never covered; (2) keyword-scan the actual
Agenda/Minutes TEXT across every watched category (68 + 72 MMPC + 26/27 BOC),
not just the meeting-record metadata ADR 015 already diffs; (3) a one-time
12-month historical sweep so anything that already happened isn't missed.

## Decision 1 — categoryId 68 gets an AUTO-DISCOVER group, not a hand-picked one

ADR 015's `events:` list is hand-picked: Trisha (or a coder session) looks up
each meeting's event id ahead of time and adds it to `config.yml`. The live
feasibility gate for this build (`GET .../Events?$filter=categoryId eq 68`,
run 2026-09-03) confirmed cat 68 returns real events in the exact same shape
as cat 72 (`publishedFiles` with `fileId`/`type`/`name`/`publishOn`), and that
`GetMeetingFileStream` fetches a cat-68 fileId identically to a cat-72 one —
so the underlying mechanism ADR 015 already built (`mmpc_client`,
`event_snapshot`, the hash diff) needed no changes to work against this
category.

The open question was **how DPA events get INTO the watch**. An early probe
using the existing `fetch_mmpc_files` (which flattens `publishedFiles`,
skipping any event with an empty file list) found nothing past Aug 2026 and
concluded DPA doesn't pre-create future event stubs the way MMPC/BOC do. A
second probe using the new `fetch_category_events` (full, unflattened event
objects — see Decision 2) corrected that: **event stubs for Sep–Dec 2026 DO
already exist**, just with zero files posted yet (confirmed live: ids 4023/
4025/4026/4027). The county's actual behavior is closer to "stubs sometimes
exist ahead of time, sometimes don't, unpredictably" than either "always" or
"never" — which is itself the argument for auto-discovery over hand-picking:
a hand-picked list has to be manually re-checked and extended every time the
county happens to publish a new stub, with no signal for when that happens.

So the DPA group is a new, **coexisting** group mode:

```yaml
- name: "Washtenaw County Board of Public Works (DPA)"
  cadence: every_run
  category_id: 68
  discover_since_days: 400
```

`civicclerk_watcher.run()` detects `category_id` and, instead of iterating a
hand-picked `events:` list, calls the new `mmpc_client.fetch_category_events`
ONCE for the whole category, filters to events on/after
`today - discover_since_days` (`discoverable_events`, pure/tested), and
diffs each by event id exactly like a hand-picked entry — same
`event_snapshot`/`snapshot_hash`/`Meeting Watch` tab, same baseline-then-
changed flow. **MMPC and BOC are untouched** — they keep their hand-picked
`events:` lists exactly as ADR 015 shipped them; auto-discovery is additive,
not a replacement.

`discover_since_days: 400` bounds the fetch to recent + future meetings only,
so activation doesn't baseline (and keyword-scan-download) the DPA's full
~190-event, decade-plus history in one run — that history is covered once by
Decision 3's backfill instead. The two windows deliberately overlap (400 days
vs. the backfill's 12 months) rather than abut exactly: an event the backfill
already baselined is simply read as "already known" (unchanged) by the live
group's first pass, which is harmless by construction (diffing is per event
id, not per fetch) — no double-baseline, no false alert from the overlap.

**Residual/asymmetry, disclosed not fixed:** a hand-picked event ID persists
in config even if the API later returns nothing for it, so `fetch_event`
returning `None` is caught as a real "vanished" change (ADR 015's vanish
rule). An auto-discover group has no such memory across runs beyond what's in
the Meeting Watch tab: if an event is later removed OUTRIGHT from the
category's returned list (not just flagged `isDeleted`, which the snapshot
already hashes and would catch normally), it simply stops appearing in
`discoverable_events` and this watch loses track of it silently — no
"vanished" alert. Building full historical-ID tracking to close this exactly
would mean comparing every run's fetched ID set against every ID ever seen in
the tab and re-fetching each dropped one individually — a materially larger
design for a low-probability case (the county deleting a record outright
rather than marking it cancelled, which two real cat-68 events already show
it does via `eventName`/`isDeleted`, both of which the existing hash already
catches).

## Decision 2 — `mmpc_client.fetch_category_events` (new, additive)

A new function alongside `fetch_mmpc_files`, not a refactor of it: same
endpoint, same `@odata.nextLink` pagination and error handling, but returns
the RAW, unflattened event objects (everything `event_snapshot()` needs —
`eventName`/`eventDate`/`isPublished`/`eventNotice`/`isDeleted`/
`publishedFiles`) instead of one flattened row per file. Kept as a genuinely
separate function (some duplication accepted) specifically so this addition
carries zero regression risk to `fetch_mmpc_files`, which Mirror D
(`mmpc_archiver.py`, ADR 010) depends on and is already live. Used by both the
DPA auto-discover group and the historical backfill (Decision 3) — the one
enumeration primitive both needed.

Also added `mmpc_client.download_file_bytes` (fetch a file's PDF bytes IN
MEMORY, no disk write) — `download_file` (used by Mirror D) is refactored to
call it internally with its external contract unchanged (same signature,
same return value, same exceptions; verified via the full existing
`test_mmpc_client.py`/`test_mmpc_archiver.py` suites, unmodified, still
green).

## Decision 3 — keyword scan on Agenda/Minutes TEXT

`civicclerk_watch.keyword_scan` (new block, `config.yml`):

```yaml
keyword_scan:
  enabled: true
  keywords:
    - "Arbor Hills" | "plan amendment" | "amend the plan" | "consistency"
    - "consistency determination" | "letter of consistency" | "siting" | "GFL"
    - "landfill expansion" | "Good Neighbor Plan"
    - "materials management plan amendment" | "251 acre" | "92 acre" | "Six Mile"
    - "Eggermont"
```

`"Eggermont"` (Theo Eggermont, Washtenaw County's Director of Public Works) was
added 2026-09-04, after the historical backfill's own evidence showed both real
DPW hits (`GFL`, `siting`) came from his standing Director's Report — he
reports on GFL/landfill status at, apparently, every DPW meeting. Trisha
directed it added after reviewing that evidence. Same fail-open posture as
`"consistency"`/`"siting"`: broader than a pure Arbor-Hills-only signal (it
will likely match most/all DPW meetings' minutes, not just the ones with
landfill content), kept anyway because the two real hits to date both trace
back to him specifically.

On every due-checked event, across every group (hand-picked or auto-discover
alike), `files_to_scan(old_files, new_files)` (pure) identifies the
Agenda/Minutes files that are NEW or CHANGED relative to the prior snapshot —
including every file present at a brand-new event's FIRST sighting, which
matters most: a DPA/MMPC/BOC meeting whose agenda already carries an Arbor
Hills item the moment this watch first sees it. `scan_files_for_keywords`
downloads each such file's bytes (`download_file_bytes`) and extracts its
text layer (`extract_pdf_text`, PyMuPDF — same dependency this repo already
uses for `egle_doc_parser`/`rop_client`/`ridgewood_client`), then
`find_keyword_hits` matches each keyword as a case-insensitive, word-boundary
regex that tolerates a hyphen/extra-whitespace/line-wrap BETWEEN a phrase's
words (so `"251 acre"` matches a source PDF's `"251-acre"` or a
`"251\nacre"` line-wrap) — the same `\b`-boundary approach
`rop_client.notice_mentions_srn` already established, generalized to
multi-word phrases.

A hit **elevates** the alert that's already firing for that change (subject
prefix `[Arbor Hills ALERT]` instead of `[Meeting watch]`, body led with a
`⚠️ KEYWORD MATCH` block naming the file, the matched keyword, and a short
excerpt) — and, notably, **overrides an otherwise-silent first sighting**: a
brand-new meeting whose agenda already matches gets an alert even though
ADR 015's baseline write is normally silent. A download/parse failure for ONE
file is logged and skipped, never raised — keyword scanning is a best-effort
enhancement layered on the change alert that's already firing (or about to);
failing to read one file must never suppress or crash the base
document-added/changed flow it augments.

### Deliberately fail-open on short keywords

`"consistency"` and `"siting"` are short, common words. Verified empirically
before shipping (see Activation below): both false-positive on ordinary
county business unrelated to Arbor Hills (a budget-consistency remark, a
county-garage siting item). **Kept exactly as directed anyway** — a keyword
scan that misses a real hit is worse than one that occasionally over-fires,
and every alert names its matched keyword + excerpt, so a false positive is a
two-second triage, not a mystery. This is the same fail-open posture this
repo already applies elsewhere (MMD/ROP/Complaints watches make no severity
judgment; the GFL air WATCH tier fires below the actionable threshold on
purpose).

## Decision 4 — one-time 12-month historical backfill

`categoryId 72/26/27` have been watched since ADR 015 (2026-07-14) but never
keyword-scanned — this feature didn't exist yet. A one-time, explicitly
invoked (never scheduled) sweep closes that: `run_historical_backfill(months
=12)` walks all four categories via `fetch_category_events`, keyword-scans
every event's Agenda/Minutes files in the window (`discoverable_events`
reused verbatim), and:

- emails Trisha ONE summary report (`format_backfill_report`) — hits listed
  FIRST and prominently (meeting, date, matched keyword, excerpt, link), a
  clean scan still gets an explicit "nothing found" line rather than silence;
- writes a SILENT baseline row for any event that doesn't already have one in
  the Meeting Watch tab — using the exact same `event_snapshot`/
  `snapshot_hash`/`append_meeting_watch_row` the live watch reads, so a
  baseline this backfill writes hashes IDENTICALLY to one the live watch
  would have written, and a later live run of an already-known event reads
  "unchanged", never a false "changed" alert;
- **never touches an existing baseline** — an event already tracked by the
  live hand-picked groups (most of the last 12 months of cat 72/26/27) is
  still keyword-scanned and still reported if it hits, but its real snapshot
  history in the tab is left alone.

Safe to run more than once by construction (idempotent on the baseline
write; the report is regenerated fresh each time, so a re-run just re-sends
the same "here's what's in the window" email).

Read-only against the live API; a `scripts/oneoff_meeting_watch_keyword_
backfill.py` + `.github/workflows/oneoff-meeting-watch-keyword-backfill.yml`
(`workflow_dispatch` only, never scheduled) pair runs it with real
credentials — matching this repo's established one-off pattern (the
2026-08-23 digest resend, the 2026-08-26 stale-links fix). **Both files are
deleted once the send is confirmed** — this is not a capability the repo
carries forward; the ONGOING keyword watch lives permanently in
`civicclerk_watcher.run()`.

### Why the backfill needed to run via `workflow_dispatch`, not locally

Local `.env` carries a real `GDRIVE_SA_KEY`/`GSHEET_ID` but placeholder SMTP
credentials (`SMTP_USER`/`SMTP_PASSWORD` are literal `your-...` stand-ins) —
confirmed before writing any code. `email_alerts.send_email` no-ops (returns
`False`) without real SMTP creds, so a local run could baseline real Sheet
rows but could never actually deliver Trisha's report. Real SMTP secrets
exist only in GitHub Actions, hence the one-off workflow.

**A second, platform-level constraint discovered mid-build:** GitHub Actions
will not dispatch a `workflow_dispatch` workflow that exists only on a
feature branch — `gh workflow run <name> --ref <branch>` 404s until the
workflow FILE is present on the repo's default branch, even though
`--ref` can target any branch for the *checkout* once it IS dispatchable.
So the real, production backfill run (writing baseline rows to the live
Sheet, sending Trisha's actual email) can only happen once this PR is
merged — it cannot serve as this PR's own pre-merge real-specimen
verification. The Activation section's dry-run (same scan code, read-only,
no Sheet/email side effects) fills that gap instead.

## Coverage disclosure (FOIA gap)

This watch — the live one and the backfill alike — covers **AGENDIZED items
only**: what CivicClerk actually published to a public meeting record. It
does **NOT** cover a raw paper or UPS filing to the county that never reaches
a public body's agenda. Closing that gap would need a FOIA request or a
different data source entirely; it's out of scope here and stated explicitly
so this watch's silence is never mistaken for "nothing is happening at the
county."

## Adversarial review (per the plan-hardening rule)

| Risk | Class | Detection | Mitigation (shipped) |
|---|---|---|---|
| `"consistency"`/`"siting"` flood Trisha's inbox with unrelated county business | manageable, verified real | every-run email volume vs. the 12-month backfill's actual hit rate | kept per explicit direction (fail open); every alert names its matched keyword + excerpt for 2-second triage; `keyword_scan.enabled: false` is a one-line rollback that doesn't touch the underlying meeting-change watch |
| Auto-discover group silently stops watching a category-wide fetch failure | manageable | `[meeting-watch]` log line; loud exit 1 if the group has zero prior rows | `sheet_writer.meeting_watch_group_has_rows` distinguishes first-ever failure (loud) from a transient blip on an established group (skip-warn, baselines preserved) — same two-tier fail-safe ADR 015 already established per-event, applied at the group level |
| A keyword-scan PDF download/parse failure masks a real document-added alert | show-stopper if unmitigated | the base "changed"/baseline alert simply never mentions a keyword hit that was missed | `scan_files_for_keywords` catches ONLY `MMPCFetchError` per file and continues — never raises, never suppresses the underlying diff-driven alert that's already firing |
| An event outright REMOVED (not just flagged) from an auto-discover category is never flagged "vanished" | manageable, disclosed | none currently — silent | accepted; see Decision 1's residual-risk paragraph |
| Backfill double-baselines an event the live watch already tracks, corrupting its real hash history | show-stopper if it happened | a live run reads a spurious "changed" against a backfill-authored snapshot | `last_meeting_snapshot` checked before every backfill write; unit-tested (`test_backfill_skips_baseline_for_already_known_event_but_still_reports`) |
| Backfill can't actually reach Trisha's inbox from a local/dev run | manageable | `ea.send_email` returns `False`; printed in the run log | confirmed local SMTP creds are placeholders BEFORE building; backfill ships as a `workflow_dispatch` job with real Actions secrets, not a local script |
| A keyword hit on a "changed" event was only visible in the best-effort email, not the durable Sheet row (a send failure would erase it from the record) | show-stopper if unmitigated — found by independent Step-5 review | the durable row's Note column vs. what the email said | fixed: the "changed" branch now folds the match into `note` before the row is written, matching the baseline branch's existing behaviour; regression-tested |
| `fetch_category_events`'s own pagination/loop-guard/error-handling had no direct test — only exercised indirectly through mocked `civicclerk_watcher` flows | manageable — found by independent Step-5 review | a regression there would pass CI silently | fixed: 6 direct tests added mirroring `fetch_mmpc_files`'s existing coverage exactly (single page, nextLink paging, loop guard, HTTP error, unparseable JSON), plus one specific to why this function exists (an empty-`publishedFiles` event, invisible to `fetch_mmpc_files`, still comes through) |

## Activation

`civicclerk_watch.enabled` was already `true` (ADR 015) — this is an
extension to an already-live path, not a new poller, so it does NOT ship
`enabled: false` by default the way a brand-new source would. Per the
overnight-coder procedure's live-path rule, this change needed real-specimen
verification before an autonomous merge could be justified — and GitHub
Actions' `workflow_dispatch` will not run a workflow that only exists on a
branch (it requires the workflow file to be on the default branch even when
targeting a different `--ref`), so the ACTUAL one-off backfill run (real
Sheet writes, real email) had to wait until after merge. To get the
verification evidence before that, the identical SCAN logic
(`fetch_category_events` → `discoverable_events` → `files_to_scan` →
`scan_files_for_keywords` → `find_keyword_hits`) was run read-only against
the live CivicClerk API in a throwaway local script — same code path, same
keyword list, no Sheet writes, no email send.

**Real result (run 2026-09-03, all 4 categories, 12-month window):** 84
events, 161 Agenda/Minutes files, 0 download failures. 32/84 meetings (38%)
matched at least one keyword. Per-keyword hit counts: `Arbor Hills` 24,
`GFL` 17, `consistency` 16, `landfill expansion` 11, `plan amendment` 10,
`siting` 9, `Six Mile` 1 — and zero hits for `amend the plan`,
`consistency determination`, `letter of consistency`,
`materials management plan amendment`, `Good Neighbor Plan`, `251 acre`,
`92 acre` (the more precise phrases simply haven't come up yet; if they ever
do, they're exactly the signal this watch exists to catch).

Reading the actual excerpts: the bare `"consistency"` keyword is almost
entirely noise as feared (Treasurer investment policy, youth-justice
program outcomes, tutoring-program quality — none about Arbor Hills).
`"siting"` is a genuine mix — several hits ARE the real signal (MMPC's own
"Siting Process Discussion"/"Siting Process & Requirements Discussion"
agenda items), alongside one unrelated "Renewable Energy Siting" county
contract. `"plan amendment"` also produces a false-positive class not
anticipated when the keyword list was drafted: an unrelated, recurring
"Broadway Park Redevelopment Brownfield Plan Amendment" BOC item. Of the 32
hit-carrying meetings, only about 5 (~16%) are PURE noise — every keyword
hit in that meeting is unrelated to Arbor Hills; the remaining ~84% carry at
least one genuinely on-target hit (`Arbor Hills`/`GFL`/`landfill expansion`
mentioned in real public comment or a real MMPC siting item), even when a
noisy keyword like `consistency` also rides along in the same meeting. This
is a one-time historical sweep across 12 months of ALREADY-published
documents; the ONGOING live watch only re-evaluates each document once, as
it's newly posted, so day-to-day alert volume going forward will be far
below this backfill's one-shot total.

**Decision: ship `keyword_scan.enabled: true`, keywords unchanged, exactly
as directed.** A ~16% pure-noise rate, with every alert self-triaging (named
keyword + excerpt) in 2 seconds, is a real but manageable cost against
catching the thing this watch exists for — an MMPC "Siting Process
Discussion" item, or a BOC agenda item that mentions the landfill by name,
neither of which would ever reach EGLE's nSITE system. If the noise proves
worse in practice than this sample suggests, `keyword_scan.enabled: false`
is a one-line rollback that doesn't touch the underlying (already-proven)
meeting-change watch.

To pause the DPA group specifically without touching MMPC/BOC: remove its
entry from `civicclerk_watch.groups`.

## Consequences

- New: `mmpc_client.fetch_category_events`, `mmpc_client.download_file_bytes`
  (`download_file` refactored to use it, contract unchanged),
  `civicclerk_watcher.{find_keyword_hits, _is_scannable_file_type,
  files_to_scan, extract_pdf_text, scan_files_for_keywords,
  format_keyword_hits, discoverable_events, format_backfill_report,
  run_historical_backfill}`, `sheet_writer.meeting_watch_group_has_rows`, the
  `civicclerk_watch.keyword_scan` config block, the DPA `category_id` group,
  `scripts/oneoff_meeting_watch_keyword_backfill.py` +
  `.github/workflows/oneoff-meeting-watch-keyword-backfill.yml` (both
  temporary — deleted once the backfill send is confirmed).
- Unchanged: `fetch_mmpc_files`, `mmpc_archiver.py` (Mirror D), the MMPC/BOC
  hand-picked group config, every other stream's watcher/config.
