# ADR 059 — GovQA public FOIA archive watch (Stream U)

*Status: built — 2026-09-28 (`govqa.enabled: false` pending Trisha's review; see
Activation).*
Builds on: ADR 058 (the private-Sheet-only / display-only-recipients pattern this
stream shares), ADR 015/017/019 (snapshot-diff watches), ADR 041 (Stream S: probe
mode).

## Context

EGLE answers other people's FOIA requests and posts each request, its status and
the records it released on a public archive
(`michiganegle.govqa.us`, "Open Records Summary"), readable without a login. Per the
overnight-coder handoff of 2026-09-28, request E614007-080526 (an 8/2026 request by
the operator's outside counsel, closed 8/20/2026) put about 45 RRD file documents in
front of us that the monitor had never seen; its detail page lists 71 attachments
(checked 2026-09-28). Nothing watched the archive for the next release.

## Feasibility spike (live, 2026-09-28)

- **One request by number needs no browser.** With a plain cookie-jar `requests`
  session: GET the summary (302 to a session URL), POST its form inputs back with
  `txtRefsearch=<E-number>`, read the internal `rid`, GET
  `RequestArchiveDetails.aspx?rid=`, and download an attachment by re-POSTing the
  detail page's form with `__EVENTTARGET=rptAttachments$ctlNN$lnkStreamCloud` and
  following the 302 to a time-limited Azure blob URL. A real download of E614007's
  `10690_6_mile_2021.pdf` (276,389 bytes) has a SHA-256 identical to the copy
  already held in Lotext.
- **The keyword list needs the browser for paging.** A plain POST returns page 1 (10
  rows) in about 10 s, but pages 2+ go through a DevExpress callback that carries an
  encrypted client-state blob, so — per the handoff's §1C-bis, learned in the
  9/25-9/27 manual runs — the keyword sweep drives headless Playwright
  (`ASPx.GVPagerOnClick('gridView','PBN')`). Verified live: "Holloway" 1 item;
  "10690" 12 items over 2 pages (the incremental stop and the page cap both work);
  "Six Mile" 79 items over 8 pages; four searches with paging took 85 s.
- **Search semantics (observed, undocumented by the site).** An UNQUOTED multi-word
  term is OR-ed word by word ("Arbor Hills" returned 97 items whose first hits were
  "Ann Arbor …" requests; "Great Lakes Recycling" returned 213), which would push the
  very first sweep past any sane page cap. A **double-quoted phrase is honoured**:
  `"Arbor Hills"` 13 items, `"Great Lakes Recycling"` 1, `"Six Mile"` 9, `"Five Mile"`
  6. (This differs from the handoff's "no boolean queries" note, which is about
  AND/OR operators, not phrases.) It does not always help: a quoted `"10833 Five
  Mile"` returned 1,637 items, so the address number `10833` is used on its own (3
  items). The site also matches text the grid does not show (E609907 came back for
  "Napier" though its visible request text lacks the word). So shipped multi-word
  keywords are quoted, and a per-term `require` phrase is only an optional extra
  filter (used for `"Six Mile"` -> `Salem`); a term without `require` trusts the
  site's own match.
- Results are newest-first, which the incremental stop relies on.

## Decision

### 1. The method is the handoff's §1C-bis

One browser at a time, one term per search, about 1 request/second; stop paging a
term at the first page that holds a request already in the tab; on any timeout close
the context, start a fresh one and retry after 30 s, then 2 min; after
`max_attempts` (3) record a structural error for that term and **move on** — never
hang. Never the UI's CSV Export headless, never the ignored date filter, never in
parallel. Playwright is imported lazily and is **not** in `requirements.txt`: the
workflow installs it only when the stream is enabled or a probe is requested.

### 2. State is the tab; events are append-only

`GovQA Archive Watch` rows are events keyed by request number, `file:<E>:<name>`,
`term:<keyword>` (a keyword's first-sweep marker) or `csv:<Drive file id>`; the LAST
row for a key is its state. A request from a keyword's very first sweep is recorded
silently (`baseline`); later ones alert (`new`). Rows are written first and the
`term:` marker last, so a crash re-baselines silently instead of alerting on history.
A row the site returned that lacks the keyword's `require` phrase is recorded
`nomatch` (no request text kept), which lets the incremental stop work. A first
sweep that reaches `max_pages_per_term` without meeting a known request records
what it read, writes a `partial` marker (older history NOT read) and reports "export
a CSV" once; later runs are ordinary incremental sweeps. The Sheet
read raises rather than swallowing errors — a swallowed read would make every request
look new.

### 3. Re-check by number, statuses fail safe

Every configured `watch_requests` number plus every recorded matched request not yet
in a terminal status (GRANTED, DENIED, CANCELLED, ABANDONED) is looked up by number
each day, capped at `max_open_rechecks`, so a status flip is caught the day it posts.
Any other status — including ones never seen — counts as open.

### 4. Released files: list, optionally stage privately

For a new or status-changed request whose status says records were released, the
attachment list is recorded (`file-listed`). If `download_attachments` is on and a
private staging folder is configured, each file is streamed to disk (size-capped),
hashed (SHA-256 + MD5) and copied there (`file-staged`). The handoff asked to skip
files already held "by SHA-256 against the Archived-PDFs mirror and Hand-Curated
folder listings". **That is not possible in CI:** the OAuth client's `drive.file`
scope sees only files the app itself created (the Hand-Curated files were uploaded by
hand), the service account's `list_files` returns no checksums, and the Hand-Curated
folder id is not in the repo's config. So every staged file records both hashes (for
`dedupe-curate` to match), and an optional `held_folder_envs` list names folders the
service account can read whose MD5s mark a file `file-held`.

### 5. CSV drop for a backfill

A gridView CSV exported in a real browser, dropped in a Drive folder shared with the
service account (`GOAUTH_GOVQA_CSV_FOLDER_ID`), is ingested silently: rows whose text
matches a keyword and whose number is new. When a keyword's sweep exhausts
`max_pages_per_term` without reaching a known request, the run reports "export a CSV"
(exit 1, no term marker) instead of looping.

### 6. HARD RULE: never publishes; private Sheet only

Request text and attachment names can name residents and street addresses, and EGLE
marks some items privileged. Rows go only to the **private** Sheet
(`GSHEET_ID_PRIVATE`; the watcher refuses to run if it is unset or equals
`GSHEET_ID`, and the workflow never receives `GSHEET_ID`). The handoff did not say
which Sheet; this is a decision for Trisha to confirm. Files stage only to a private
folder that must not equal any other `GOAUTH_*_FOLDER_ID`. Nothing imports or feeds
`findings_feed`/`gen_findings_feed` or another archiver. Recipients are scoped
verbatim; an empty list is display-only and `send_email` (which would fall back to
the coalition list) is never called. All pinned by `tests/test_govqa.py`.

### 7. Alert copy

One email per run, sections only when non-empty: new matching requests (number,
status, created, matched keywords, a 300-character excerpt), status changes
(old -> new), files staged, and anything needing attention. It says these are
source-labeled listing events and that file contents are not read.

## Adversarial review

**Show-stoppers considered:** (1) Playwright or the archive is blocked on the GitHub
runner. *Detect:* `--probe` before activation; a failing term is reported per run
(exit 1, an email naming it) rather than skipped quietly — unlike the RRD document
watch, a failure here is never skip-and-warn. *Recover:* the CSV drop; pause by
flipping `enabled`. (2) Resident PII to a public surface — pinned by tests (Decision
6). (3) An alert storm on activation — each keyword's first sweep baselines silently.
(4) A backfill too large to scrape — stops and asks for a CSV.

**Manageable risks:** the site's markup changes (`parse_detail` raises a structural
error without a Reference No.; empty grids are visible in the log); a session expiring
mid-download (an HTML answer is a transient error and retried); a redirect to an
unexpected host (allowlisted: the archive and its Azure blob store only).

**Residual risks accepted:** a `require` phrase can miss a hit the site matched on
text the grid doesn't show (documented in the config); file contents are never read;
a request that is closed without files is not re-checked again.

## Alternatives considered

- **Plain-requests keyword list** — works for page 1 only; paging needs the DevExpress
  callback state, so rejected per the handoff.
- **Shelling out to curl** as the prior-art scripts do — the same protocol through
  `requests`, which the repo already depends on and which is testable without a
  subprocess.
- **Public Sheet tab** — rejected (Decision 6).
- **Auto-publishing releases** — out of scope by the handoff's hard rule.

## Activation

Ships `govqa.enabled: false`. To activate: (1) share the private Sheet with the
service account as Editor; (2) run the workflow once with `probe=true`; (3) optional:
create a private staging folder, set `GOAUTH_GOVQA_STAGING_FOLDER_ID`, set
`download_attachments: true`; (4) flip `govqa.enabled`. The first run sweeps every
keyword and baselines silently. Pause = flip back to `false` (tab state survives).
