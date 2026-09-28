# ADR 061 — RRD documents watch (Stream T): RIDE's anonymous file listing

*Status: built — 2026-09-28; review fixes the same day (drafted as "ADR 058",
renumbered 061 on merge because 058-060 were taken). Ships `ride_docs.enabled:
false`; see Activation.*
Builds on: ADR 019 (Stream J, RRD status; this ADR corrects its "no anonymous
document API" premise), ADR 007/010 (Drive mirror idiom), ADR 015/017/019
(snapshot-diff watches), ADR 041 (Stream S: probe mode, display-only recipients).

## Context

EGLE has four divisions with Arbor Hills records. The monitor pulls AQD, MMD and
WRD records routinely; **RRD (Remediation and Redevelopment Division)** was
watched only for *status* (Stream J), never for *documents*. Per the
overnight-coder handoff of 2026-09-28, EGLE FOIA request E614007-080526 (released
publicly on EGLE's GovQA archive on 2026-08-20) contained about 45 RRD file
documents the monitor had not seen, and nothing watched for the next ones.

## Feasibility spike (read-only, 2026-09-28) — the anonymous document channel

Earlier recon (worker #69, 7/2026; ADR 019) found RIDE is an Angular SPA and
concluded it had no anonymous document API. Re-checked with a headless browser,
**no credentials and no login click**, that is true of RIDE's *status* data and
false of its *documents*:

- The public Inventory of Facilities page gives every anonymous visitor a
  "Public User" session on its own (`GET /RIDE/Home/GetAppSettings` returns
  `userName` "Public1", `roleId` 13). We treat that as anonymous access, not as
  logging in; it is the site's own front end doing it, and this stream sends no
  credential of any kind. **Ruled by Trisha, 2026-09-28: the anonymous "Public
  User" session counts as NOT logging in** (PR #85/#86), so this stream may run.
- Expanding a facility row's "Location Files (N)" panel fires three JSON
  endpoints, all of which a plain HTTP client can replay once it holds the
  session cookies (a `POST` without them returns HTTP 405):
  - `POST api/Location/GetFacilitiesTable` — program number to `locationId`.
  - `POST api/ContentManagerFile/GetContentManagerFilesForLocationFilesTable` —
    the location's file list; each record has a unique integer `uri`.
  - `POST api/ContentManagerFile/GetFileContents` with `{"uri": N}` — returns
    the file bytes (a 90,332-byte PDF specimen downloaded and verified `%PDF-`).
- The page's own disclaimer: records maintained by RRD are available for
  download through RIDE, and "RRD is in the process of converting the entire
  record backlog to electronic format, and records may exist that are not
  displayed here." Files therefore keep appearing — Salem Landfill's were added
  Jul-Oct 2025.

Live counts, one anonymous listing per watched Part 201 site (95 files in all):
81000004 Arbor Hills - East (location 2085) 37; 81000033 Salem Landfill (9549)
21; 82008712 MITC Corridor (83658) 29; 81000835 7667 Chubb Rd (75890) 5; 81000840
7941 Salem Rd (75895) 3. `uri` was unique in every listing. Two facts shaped the
design: (a) the list endpoint accepts `rowCount` up to at least 1000 and sorts on
`uri`, so paging is stable (a sort on `dateOfDocument` has ties); (b) files reach
216 MB, so downloads must stream with a cap.

The two other Phase-1A channels: RRDOpenData (all five layers enumerated: sites,
USTs, brownfields, two land-use-restriction layers) has **no** documents layer;
nSITE/MiEnviro has no RRD document profile (its public map layer 8 is the same
status data as RRDOpenData layer 0). UST facility ids are not RIDE inventory
programs (`00040223`, `00038889` return zero rows); `00038889`'s files are listed
under `81000004`'s location.

## Decision

### 1. Watch each Part 201 location's file list; key on `uri`

`ride_docs_client.py` opens a warmed anonymous session, resolves each program
number in `ride_docs.program_nums` (default: `ride.site_ids`) to a `locationId`,
and lists that location's files. One canonical record per file (`uri`, title,
file name, index type, extension, size, document date, **added-to-RIDE date**,
folder number, document number); its hash is the change detector. The 1900-01-31
value RIDE stores for an unknown document date is a placeholder and is canonicalized
to blank, never shown or hashed as a 1900 date.

### 2. The state is the tab; events are append-only

`RRD Documents` rows are events (`baseline`, `new`, `changed`, `removed`,
`mirrored`, `mirror-skipped`, `mirror-failed`, `fetch-skipped`, `fetch-ok`); the
LAST row for a key (`rrd:<uri>` or `loc:<locationId>`) is its state, so nothing
races (the ADR 019 idiom). First sighting of a location writes silent `baseline`
rows for all its files plus one `loc:` marker, **file rows first and the marker
last**: a crash between them re-baselines silently next run instead of alerting on
the whole list. A `new` uri, a `changed` record, or a `removed` uri (a public
record disappearing is signal) alerts; a `removed` uri that returns is `new`
again. The Sheet read is **not** error-swallowing (unlike the other tabs'
helpers): a swallowed transient read error would look like "never baselined" and
silently fold genuinely-new files into a fresh baseline. It raises.

### 3. Alert copy states the accuracy caveat

Every alert shows both the document's own date and the date it was added to RIDE,
(the "added to RIDE" date is RIDE's `contentManagerCreatedOn`), and says RRD is digitizing a backlog (an old document can be newly listed with no
new activity at the site) and that the file has not been reviewed. Alerts are
source-labeled listing events, not findings.

### 4. HARD RULE: never publishes; private Sheet only

RIDE titles can carry residents' names and street addresses (real examples exist
in the Arbor Hills East listing). So:

- Rows go to the **private** Sheet (`GSHEET_ID_PRIVATE`, shared only with the
  service account and Trisha) — never the public case-file Sheet. The watcher
  refuses to run (exit 1) when that secret is unset, and — **the check that works
  in CI** — when the spreadsheet it names already holds any public case-file tab
  (New Documents / Evidence by Risk / Measurements), checked before any write
  (`looks_like_public_sheet`, the ADR 059 guard). It also refuses when the secret
  equals `GSHEET_ID`, but that comparison is a **local safeguard only**: the
  workflow does not receive `GSHEET_ID` at all, so in CI it compares against "".
- Files mirror to a **private Drive folder** (`GOAUTH_RRD_FOLDER_ID`), which must
  not equal any other `GOAUTH_*_FOLDER_ID` (several are public-facing); the
  workflow passes those ids in solely so the watcher can enforce that.
- Nothing here imports or feeds `findings_feed`/`gen_findings_feed` or any other
  archiver. Publishing anything is Trisha's hand-curation decision.
- **The Actions log is public**, so nothing this job prints may carry a title.
  Mirror filenames are title-free (below); every printed error is its class plus
  HTTP status only (`_err`), never the exception text — a googleapiclient
  `HttpError` embeds the request URL, Drive query included; the full text goes
  only to the private-Sheet note. `main()` silences googleapiclient's retry
  logger (it prints the request URL) and reports an unhandled exception as its
  class only.
- Recipients are scoped verbatim (Trisha only). An **empty** list means
  display-only — rows, no email — and the watcher never calls `send_email` then,
  because `send_email` would fall back to the whole coalition list.

All of this is pinned by `tests/test_ride_docs.py` (private-sheet-only writes via a
recording fake, the fail-closed cases, the AST-level "no feed/archiver reference"
check, the workflow's env, the folder-equality refusal, a fake `HttpError`
carrying a title never reaching stdout/stderr).

### 5. Private mirror, bounded

Not-yet-mirrored files are streamed to disk (never buffered whole), hashed
(SHA-256 + MD5), and uploaded as `<uri>_<hash8>.<ext>` — **title-free on
purpose** (the name is embedded in Drive queries, which googleapiclient prints on
errors and retries); the title lives in the private Sheet row with the same uri.
The record-hash part means a changed file re-mirrors instead of silently reusing
the old copy. Bounds: `max_mirror_per_run` (default 8; the ~95-file
backlog drains over about 12 daily runs, newest first), `max_file_mb`
(default 250; larger files are recorded `mirror-skipped`, never retried), and a
per-file failure cap (two `mirror-failed` rows, then `mirror-skipped` on the third
failed attempt) so one
bad file cannot starve the rest. Mirroring is optional: without the folder secret
the stream still lists, records and alerts.

### 6. Failure modes and liveness

A fetch failure (`RideDocsFetchError`, including the 405 a bot challenge gives) is
skip-and-warn for a baselined location and **loud** (exit 1) for one with no
baseline, so an activation-time block surfaces. A structural break
(`RideDocsParseError`: no `data`/`totalRows`, a record without `uri`, a duplicate
`uri`, a page-cap overflow) is always loud. Because a persistent skip-and-warn
would otherwise go quiet forever, each skipped run records a `fetch-skipped` row
and the run that makes it `stale_alert_after_skips` (default 3) **consecutive**
skips sends ONE liveness alert ("any new file there is currently going unseen");
the first good run after records `fetch-ok` and resets the count.

Three more cases would otherwise go quiet for a baselined location, so each is
also a `fetch-skipped` run (counting toward the liveness alert), never a diff:

- **An empty listing** (`totalRows` 0) where files were listed before. Diffing it
  would write every file `removed`, then re-alert all of them `new` on recovery.
- **A program that stops resolving** in RIDE's inventory. Also alerts on the
  first run it happens.
- **A program that resolves to a different `locationId`.** Baselining the new
  location silently would absorb any genuinely new file, so the run is red
  (exit 1) and alerts on the first occurrence, naming the one manual row that
  accepts the move (`loc:<new id>`, event `baseline`); the next run then diffs
  the new location against every known uri, so anything unseen alerts as new.

A session that cannot be opened records a skip for every baselined location, and
is loud as well when some program has no baseline yet. `stale_alert_after_skips`
is at least 1 (0 cannot disable liveness).
`python ride_docs_watcher.py --probe` (workflow input `probe`) runs the client end
to end regardless of the enabled flag and touches no Sheet, Drive or email.

## Adversarial review

**Show-stoppers considered:** (1) RIDE's bot defense (the session carries
F5- and Cloudflare-style cookies) challenges the GitHub runner IP. *Detect:* `--probe` before activation; a first run without a
baseline exits red; after activation the liveness alert above. *Recover:* the
probe/liveness signal tells Trisha before anything is lost; the fallback is a
different runner egress, or pausing by flipping `enabled` (the tab state
survives). (2) Resident PII reaching a public surface. *Mitigated as features*,
not aspirations — see Decision 4. (3) The anonymous "Public User" session is
read as logging in. *Resolved:* Trisha ruled 2026-09-28 that it is not.

**Manageable risks:** a title-only change re-alerts once as `changed` (the record
hash covers it; the alert prints the record); an alert storm if RIDE re-issues
every `uri` (the email lists at most 25 per location; every file still gets its
Sheet row; `removed` + `new` batches are visible at a glance); the `Public User`
role or endpoints changing (structural error, loud).

**Residual risks accepted:** file *contents* are never reviewed or classified by
this job (no LLM, no `egle_doc_parser`) — it is a listing watch; the "Location
Submittals" list (0 entries for Arbor Hills East) is not watched; a location with
no exact program match at first sight is skipped with a printed note only (one that
WAS baselined is handled under Decision 6); `--probe` exits 1 when any configured
program does not resolve, so activation catches a wrong program number.

## Alternatives considered

- **Public Sheet tab + auto-publish like nSITE documents** — rejected: titles
  carry resident names/addresses; publishing is a hand-curation decision.
- **Poll GovQA/FOIA releases instead** — kept, as a separate stream (ADR 059);
  it catches other requesters' releases but not RRD's own uploads.
- **Drive `md5Checksum` dedupe against the other mirrors** — not needed here:
  the mirror is a private copy keyed by `uri`, not a public archive.
- **Playwright for the whole run** — rejected: the JSON endpoints work with plain
  `requests` once warmed, and the repo has no browser dependency.

## Activation

Ships `ride_docs.enabled: false` (a brand-new external source). To activate:

1. Share the private Sheet with the service account as **Editor**.
2. Run the workflow manually with `probe=true`; confirm the runner is not
   challenged and all five programs resolve.
3. Optional: create a **private** Drive folder and set the `GOAUTH_RRD_FOLDER_ID`
   secret (mirroring is skipped without it).
4. Flip `ride_docs.enabled: true`. The first run baselines ~95 files silently.

Pause = flip back to `false` (tab state survives).
