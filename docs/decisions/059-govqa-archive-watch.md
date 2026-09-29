# ADR 059 — GovQA public FOIA archive watch (Stream U)

*Status: built — 2026-09-28; ACTIVATED 2026-09-28 (Trisha) after a green runner probe
(run 36518570536), notify-only (`download_attachments: false`). See Activation.*
Builds on: ADR 061's pattern (private-Sheet-only, display-only recipients; the RRD documents
watch, drafted as "ADR 058" and renumbered on merge); ADR 015/017/019 (snapshot-diff watches); ADR 041
(Stream S: probe mode).

## Context

EGLE answers other people's FOIA requests and posts each request, its status and the
records it released on a public archive (`michiganegle.govqa.us`, "Open Records
Summary"), readable without a login. Per the overnight-coder handoff of 2026-09-28,
request E614007-080526 (an 8/2026 request by the operator's outside counsel, closed
8/20/2026) put about 45 RRD file documents in front of us that the monitor had never
seen; its detail page lists 71 attachments (checked 2026-09-28). Nothing watched the
archive for the next release.

## Feasibility spike (live, 2026-09-28)

- **One request by number needs no browser.** With a plain cookie-jar `requests`
  session: GET the summary (302 to a session URL), POST its form inputs back with
  `txtRefsearch=<E-number>`, read the internal `rid`, GET
  `RequestArchiveDetails.aspx?rid=`, and download an attachment by re-POSTing the
  detail page's form with `__EVENTTARGET=rptAttachments$ctlNN$lnkStreamCloud` and
  following the 302 to a time-limited Azure blob URL (host
  `1michigandeq.blob.core.usgovcloudapi.net`). A real download of E614007's
  `10690_6_mile_2021.pdf` (276,389 bytes) has a SHA-256 identical to the copy already
  held in Lotext.
- **The keyword list needs the browser for paging.** A plain POST returns page 1 (10
  rows) in about 10 s, but pages 2+ go through a DevExpress callback that carries an
  encrypted client-state blob, so — per the handoff's §1C-bis, learned in the
  9/25-9/27 manual runs — the keyword sweep drives headless Playwright
  (`ASPx.GVPagerOnClick('gridView','PBN')`). Verified live: "Holloway" 1 item; "10690"
  12 items over 2 pages (the incremental stop and the page cap both work); unquoted
  "Six Mile" 79 items over 8 pages. A real 180 s timeout on one search was recovered by
  the fresh-context-and-retry method.
- **Search semantics (observed, undocumented by the site).** An UNQUOTED multi-word
  term is OR-ed word by word ("Arbor Hills" returned 97 items whose first hits were
  "Ann Arbor …" requests; "Great Lakes Recycling" returned 213), which would push the
  very first sweep past any sane page cap. A **double-quoted phrase is honoured**:
  `"Arbor Hills"` 13 items, `"Great Lakes Recycling"` 1, `"Six Mile"` 9, `"Five Mile"`
  6. (This differs from the handoff's "no boolean queries" note, which is about
  AND/OR operators, not phrases.) It does not always help: a quoted `"10833 Five
  Mile"` returned 1,637 items, so the address number `10833` is used on its own (3
  items). The site also matches text the grid does not show (E609907 came back for
  "Napier" though its visible request text lacks the word), and the landfill's own
  requests write "10690 6 Mile Road, Northville" — so shipped keywords are quoted
  phrases with **no** `require` filter (a `require: ["Salem"]` on "Six Mile" would have
  dropped them).
- Results are newest-first, which the incremental stop relies on. A search with no
  results shows the grid's own "No data to display" empty row, which is how a real
  "nothing found" is told from a page that did not render.

## Decision

### 1. The method is the handoff's §1C-bis

One browser at a time, one term per search, about 1 request/second; stop paging a
term at the first page whose E-numbers are **all** already in the tab (the handoff's
rule; a page that mixes known and unknown rows is read and so is the next, so a date
tie across a page boundary is not missed); on any timeout close the context, start a
fresh one and retry after 30 s, then 2 min; after `max_attempts` (3) record a structural
error for that term and **move on**, and after three failing terms in a row abort the
phase — never hang (there is also a wall-clock budget, default 40 minutes). Never the
UI's CSV Export headless, never the ignored date filter, never in parallel. Playwright
is imported lazily, is **not** in `requirements.txt`, and the workflow installs a pinned
version only when the stream is enabled or a probe is requested.

### 2. State is the tab; events are append-only

`GovQA Archive Watch` rows are events keyed by request number, `file:<E>:<name>#<n>` (n =
the occurrence of that name on the request, ALWAYS written, so a name that itself ends in
`#3` cannot be misread), `list:<E>` (the release-listing marker, below), `term:<keyword>`
(a keyword's first-sweep marker) or `csv:<Drive id>:<content hash>`; the LAST row for a key
is its state. A **first sweep of
a keyword reads every page up to `max_pages_per_term`** and records what it finds
silently (`baseline`); later sweeps alert (`new`). Rows are written first and the
`term:` marker last, so a crash re-baselines silently instead of alerting on history. A
first sweep that reaches the page cap records what it read, writes a `partial` marker
(older history NOT read) and reports "export a CSV" once; later runs are ordinary
incremental sweeps. A row the site returned that lacks a configured `require` phrase is
recorded `nomatch` (no request text kept) so the incremental stop works, and is
**upgraded** to matched if a term that matches it later finds it (or an explicit
`watch_requests` entry names it). The Sheet read raises rather than swallowing errors.

### 3. Zero rows are only believed with the empty marker

A grid with no data rows and no "No data to display" marker did not render — that is a
transient fetch error (retried); `<style>`/`<script>` blocks are ignored when looking for
the markers, so a stylesheet that merely names the empty-row class cannot pass for an empty
grid. A grid that DOES show data rows but from which no request could be read (the markup
changed) is the same fetch error — never "no requests". A by-number lookup that returns
neither a grid nor an empty grid is the same (a grid that lists OTHER requests but not the
one asked for is "not shown", and a test pins that). A page of more than 10 rows with no
pager text is impossible and fails the keyword; a first sweep that reads a FULL page of 10
rows with no pager text cannot tell whether older pages exist, so it is marked `partial`
(the marker's note names which cause)
and a CSV is requested (a false alarm for a term with exactly 10 results — accepted). If
every keyword comes back empty (with or without requests on record), that is reported as a broken
read and nothing is recorded. The `rid` that opens a request's detail page is read only
from the row's details-link tag (never from request text), and the detail page's own
`Reference No` must equal the request it was opened for.

### 4. Re-check by number, statuses fail safe

Every configured `watch_requests` number plus every recorded matched request not yet in
a terminal status (GRANTED, DENIED, CANCELLED, ABANDONED — real statuses seen include
"WAITING FOR PAYMENT", "PARTIAL", "UTLR", "New Request") is looked up by number each
day, newest first, capped at `max_open_rechecks` (a truncated list is reported). Any
other status counts as open. A malformed `watch_requests` entry (it must be the full
`E######-MMDDYY`) is reported and ignored, never a crash; a well-formed one the archive
never shows (a mistyped date suffix) is reported on every run until it resolves; lookups
that return nothing for most recorded requests are reported. Requests ingested from a CSV
in this run are history, not re-checked in the same run.

### 5. Released files: list, optionally stage privately

For EVERY new or status-changed request (not only statuses this code recognises as
"released": an unseen status must not hide a release), the attachment list is recorded
(`file-listed`, with the request's close date). The request is
given a `list-pending` marker **in the same append as its `new`/`status` row**, and a
`file-list-done` marker after its detail page was read (files first, marker last); every
run retries the pending ones, so a failure, a spent time budget or a kill between the
status row (which makes the request terminal — never re-checked) and the listing cannot lose
the release. A retry reopens the archive session first (a stale `(S(...))` session is the
usual cause). The detail page's attachment-link count must equal the number of names read (a
markup change that wraps the link text would otherwise record a SHORT list as the whole
release and close it — verified against live pages of 0–71 files); a page for a different
request is refused before it is remembered; a listing that fails 5 times is given up
(`list-skipped`, reported once), and the rid found by number is kept on the `file-list-done`
row. If
`download_attachments` is on and a private staging folder is configured, each file is
streamed to disk (size-capped), hashed (SHA-256 + MD5) and uploaded under a
**content-addressed name** — `<E-number>__<sha256[:16]>[.<ext>]` — the extension only from a short list of known
document/image types, never a slice of the attachment's own name — so that name (which can
name a resident) never enters a Drive query, and distinct files can never collide. A transient download failure reloads the detail page and retries once
in the same run; a file gets up to five strikes across runs before it is recorded
`file-skipped` **and reported**; an over-size file is skipped once. The handoff asked to
skip files already held "by SHA-256 against the Archived-PDFs mirror and Hand-Curated
folder listings". **That is not possible in CI:** the OAuth client's `drive.file` scope
sees only files the app itself created (the Hand-Curated files were uploaded by hand),
the service account's `list_files` returns no checksums, and the Hand-Curated folder id
is not in the repo's config. So every staged file records both hashes (for
`dedupe-curate` to match), and an optional `held_folder_envs` list names folders the
service account can read whose MD5s mark a file `file-held`.

### 6. CSV drop for a backfill

A gridView CSV exported in a real browser, dropped in a Drive folder shared with the
service account (`GOAUTH_GOVQA_CSV_FOLDER_ID`), is ingested silently **after** the
sweep (so anything genuinely new was already alerted by the sweep and the CSV only fills
older history): rows whose text matches a keyword and whose number is unknown. A file is
identified by Drive id and content hash, so one replaced in place is re-read, and a CSV
with no parseable rows is reported.

### 7. HARD RULE: never publishes; private Sheet only

Request text and attachment names can name residents and street addresses, and EGLE
marks some items privileged. Rows go only to the **private** Sheet
(`GSHEET_ID_PRIVATE`). The handoff did not say which Sheet; this is a decision for
Trisha to confirm. The watcher fails closed three ways: the secret unset; equal to
`GSHEET_ID` (a local-run check — the workflow does not receive the public id at all);
and — the check that works in CI — the target spreadsheet already holding a public
case-file tab (New Documents / Evidence by Risk / Measurements), checked before any
write. Files stage only to a private folder that must not equal any other
`GOAUTH_*_FOLDER_ID` or the public PDF archive's `GDRIVE_FOLDER_ID` (the workflow passes
them for exactly that check; the guard compares ids, it cannot tell whether a folder is
shared — create the staging folder private). Nothing imports or feeds `findings_feed`/`gen_findings_feed` or
another archiver. Recipients are scoped verbatim; an empty list is display-only and
`send_email` (which would fall back to the coalition list) is never called. All pinned
by `tests/test_govqa.py`.

### 8. Robustness: guarded phases, an always-sent report, a public log

Each phase (sweep, CSV, re-check, file listing, staging) is guarded: an exception
becomes a reported problem and the next phase still runs, and the one report email is
sent from a `finally`, so an alert cannot be lost to an exception after its rows were
written (a HARD kill — SIGKILL or the job timeout — during a long staging phase still could;
staging ships off and is time-budgeted). Every keyword returning nothing is a broken read
(also on the activation run, where markers would otherwise make the whole history alert as
new); one keyword that goes dark although it matched before is reported. The pager is read
from the LAST match, and the summary/details responses must end on an allowlisted host. A configured recipient whose report could not be sent makes the run red. A retry
loop checks the run's time budget before every further attempt (one failing call cannot
outlive it), and a browser that cannot restart never aborts the retry (the next attempt
fails on its own and is counted); an unexpected error inside one keyword's sweep is
contained to that keyword.
stdout is a public log: no `print` interpolates request text or an attachment name
(an AST test pins it), every exception message is URL-scrubbed (Azure download links
carry the file name and a signed token), `googleapiclient`'s retry logger — which prints
request URLs — is silenced, and `main()` reports an uncaught error as class + scrubbed
message only. Redirects on a download are followed by hand, https-only, to exactly the
archive host and the state's storage account.

## Adversarial review

**Show-stoppers considered:** (1) Playwright or the archive is blocked on the GitHub
runner. *Detect:* `--probe` before activation; a failing term is reported per run (exit
1, an email naming it) rather than skipped quietly. *Recover:* the CSV drop; pause by
flipping `enabled`. (2) Resident PII reaching a public surface — Decisions 7 and 8. (3)
An alert storm on activation — each keyword's first sweep baselines silently. (4) A
backfill too large to scrape — records what it read, then asks for a CSV once.

**Manageable risks:** markup changes (`parse_detail` and the empty-grid check raise);
a session expiring mid-download or mid-listing (a fresh session, retried once in-run); a storage account rename (fails
loudly at the redirect allowlist).

**Residual risks accepted:** a `require` phrase, if configured, can miss a hit the site
matched on text the grid doesn't show; file contents are never read; **released files are
listed when a request is new or changes status — attachments posted later to an
already-terminal request are not seen** (a possible follow-up: re-list until the close
date plus N days); requests already released when first seen (a first sweep, a CSV or a `watch_requests` first sighting)
are not listed; `PARTIAL` is not terminal, so such a request is re-checked until it
closes; statuses this code has never seen (e.g. `UTLR`) count as open and re-check
daily — enough of them fill the `max_open_rechecks` cap and turn the truncation notice
into a daily nag (raise the cap); the headless browser inherits the job's environment
(which holds the SMTP and OAuth secrets) while it renders third-party-authored archive text —
passing a minimal `env=` to `chromium.launch` is a tracked follow-up (it cannot be verified
without a runner, so it is not shipped blind); partial parse loss (a page that yields fewer rows than its pager implies) is not detected —
only a total loss is — and a cross-check against the pager was tried and dropped for
false-positive risk; the requester's organization is not captured (the grid
does not show it); a hash exists only for staged files.

## Alternatives considered

- **Plain-requests keyword list** — works for page 1 only; paging needs the DevExpress
  callback state, so rejected per the handoff.
- **Shelling out to curl** as the prior-art scripts do — the same protocol through
  `requests`, which the repo already depends on and which is testable without a
  subprocess.
- **Public Sheet tab** — rejected (Decision 7).
- **Auto-publishing releases** — out of scope by the handoff's hard rule.

## Activation

Ships `govqa.enabled: false`. To activate: (1) share the private Sheet with the
service account as Editor; (2) run the workflow once with `probe=true`; (3) optional:
create a PRIVATE staging folder **with the app's OAuth identity** (the `drive.file` scope
only sees files the app created — the `create-oauth-folder` workflow does this), set
`GOAUTH_GOVQA_STAGING_FOLDER_ID`, set `download_attachments: true`; (4) flip `govqa.enabled` (and update
`test_the_workflow_never_receives_the_public_sheet_id_ships_disabled_and_pins_playwright`,
which pins the shipped `false`). The first run sweeps every keyword and baselines
silently. Pause = flip back to `false`.
