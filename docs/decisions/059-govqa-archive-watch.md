# ADR 059 — GovQA public FOIA archive watch (Stream U)

*Status: built — 2026-09-28 (`govqa.enabled: false` pending Trisha's review; see
Activation).*
Builds on: ADR 058's pattern (private-Sheet-only, display-only recipients) — that ADR is
in a separate, not-yet-merged PR; ADR 015/017/019 (snapshot-diff watches); ADR 041
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

`GovQA Archive Watch` rows are events keyed by request number, `file:<E>:<name>` (a
repeated name gets `#2`, `#3`), `term:<keyword>` (a keyword's first-sweep marker) or
`csv:<Drive id>:<content hash>`; the LAST row for a key is its state. A **first sweep of
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
transient fetch error (retried). A by-number lookup that returns neither the request nor
an empty grid is the same. If every keyword comes back empty while requests are on
record, that is reported as a broken read and nothing is recorded.

### 4. Re-check by number, statuses fail safe

Every configured `watch_requests` number plus every recorded matched request not yet in
a terminal status (GRANTED, DENIED, CANCELLED, ABANDONED — real statuses seen include
"WAITING FOR PAYMENT", "PARTIAL", "UTLR", "New Request") is looked up by number each
day, newest first, capped at `max_open_rechecks` (a truncated list is reported). Any
other status counts as open. A malformed `watch_requests` entry (it must be the full
`E######-MMDDYY`) is reported and ignored, never a crash; lookups that return nothing
for most recorded requests are reported.

### 5. Released files: list, optionally stage privately

For a new or status-changed request whose status says records were released, the
attachment list is recorded (`file-listed`, with the request's close date). If
`download_attachments` is on and a private staging folder is configured, each file is
streamed to disk (size-capped), hashed (SHA-256 + MD5) and uploaded under a
**content-addressed name** — `<E-number>__<sha256[:16]>.<ext>` — so the attachment's own
name (which can name a resident) never enters a Drive query, and distinct files can
never collide. A transient download failure reloads the detail page and retries once
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
`GOAUTH_*_FOLDER_ID`. Nothing imports or feeds `findings_feed`/`gen_findings_feed` or
another archiver. Recipients are scoped verbatim; an empty list is display-only and
`send_email` (which would fall back to the coalition list) is never called. All pinned
by `tests/test_govqa.py`.

### 8. Robustness: guarded phases, an always-sent report, a public log

Each phase (sweep, CSV, re-check, file listing, staging) is guarded: an exception
becomes a reported problem and the next phase still runs, and the one report email is
sent from a `finally`, so an alert can never be lost to a crash after its rows were
written. A configured recipient whose report could not be sent makes the run red.
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
a session expiring mid-download (retried once in-run); a storage account rename (fails
loudly at the redirect allowlist).

**Residual risks accepted:** a `require` phrase, if configured, can miss a hit the site
matched on text the grid doesn't show; file contents are never read; **released files are
listed when a request is new or changes status — attachments posted later to an
already-terminal request are not seen** (a possible follow-up: re-list until the close
date plus N days); requests already released when first seen (a first sweep or a CSV)
are not listed; `PARTIAL` is not terminal, so such a request is re-checked until it
closes.

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
create a private staging folder, set `GOAUTH_GOVQA_STAGING_FOLDER_ID`, set
`download_attachments: true`; (4) flip `govqa.enabled` (and update
`test_the_workflow_never_receives_the_public_sheet_id_ships_disabled_and_pins_playwright`,
which pins the shipped `false`). The first run sweeps every keyword and baselines
silently. Pause = flip back to `false`.
