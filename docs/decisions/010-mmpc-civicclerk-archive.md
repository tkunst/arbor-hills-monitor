# ADR 010 — Mirror D: MMPC document archive (CivicClerk)

*Status: built — 2026-07-10 (`mmpc_archive.enabled: false` pending Trisha's review; see Activation).*

## Context

The MMPC (Washtenaw County Materials Management Planning Committee) publishes
its meeting Agenda/Minutes/Other PDFs on a Washtenaw County CivicClerk portal
(`washtenawcomi.portal.civicclerk.com/?category_id=72`). Today (`mmpc_watcher.py`,
built before this ADR) the monitor only *reminds* Trisha: during each meeting's
poll window it does a plain HTTP GET on the category page and, if the response
looks "updated" (200 + >500 bytes — the portal's own JS-SPA rendering made a
real scrape look infeasible at the time), emails a "minutes likely posted, go
check" link. Trisha then downloads and uploads each PDF into the
`MMPC-meeting-minutes/` Drive folder **by hand** — confirmed directly with her
2026-07-10 that this manual step, not an automation, is what's been happening.

That "can't scrape it, it's a SPA" assumption turned out to be about the
*public-facing page*, not the *data*. Inspecting the portal's own network
traffic (Chrome DevTools + direct `fetch()` calls, 2026-07-10) found the SPA
itself talks to a fully public, unauthenticated JSON API:

- `GET https://washtenawcomi.api.civicclerk.com/v1/Events?$filter=categoryId eq 72`
  → every MMPC event. Each carries a `publishedFiles[]` array, one entry per
  document:

  ```json
  {"fileId": 9107, "type": "Minutes", "name": "Materials Management Planning
   Committee Minutes February 11, 2026", "publishOn": "2026-03-11T16:20:51.647Z",
   "sort": 3, "fileType": 4}
  ```

  `type` is one of `"Agenda"`, `"Minutes"`, `"Other"`. OData-paginated via
  `@odata.nextLink` (followed by `mmpc_client.fetch_mmpc_files()`).
- `GET https://washtenawcomi.api.civicclerk.com/v1/Meetings/GetMeetingFileStream(fileId={id},plainText=false)`
  → the raw PDF bytes for that `fileId`. Verified live: fetched a real 181 KB
  minutes PDF for fileId 9107 with no auth headers, no cookies.

This is strictly simpler than the WDS scrape (ADR 009) — no ASP.NET pager,
no HTML parsing, no `plainText`/detail-span grid logic — because CivicClerk is
a modern app with a real JSON API underneath, unlike WDS's 2001-era ASP.NET.

## Decision

Add **Mirror D**: `mmpc_client.py` (fetch + flatten) + `mmpc_archiver.py`
(diff + download + upload + log), following the exact structural pattern
Mirror B (`archiver.py`, ADR 007) and Mirror C (`wds_archiver.py`, ADR 009)
already established — gated behind `mmpc_archive.enabled` in `config.yml`,
off by default until Trisha reviews and flips it.

**Destination — Trisha's explicit call (not a default):** upload into the
*existing* `MMPC-meeting-minutes/` folder under
`PUBLIC_View_and_Copy/Arbor Hills Landfill, Michigan, Washtenaw County/`
(Drive folder ID `16yjIycNEBPhydliBe_ybU6H5roskZOmM`) — the same folder she
already hand-populates — rather than a new dedicated archive folder or
Mirror B/C's folder. Confirmed via direct question 2026-07-10 (three options
offered: existing folder / new dedicated folder / alert-only-no-upload;
existing folder chosen).

**`archive_client.py` generalized:** `find_in_folder()`/`upload_file()`/
`upload_pdf()` now take an explicit `folder_id` parameter instead of always
reading `GOAUTH_ARCHIVE_FOLDER_ID` internally — Mirror D needs a *different*
folder than Mirror B/C share. `is_configured()`/`folder_id()` take a
`folder_env` parameter for the same reason (`GOAUTH_MMPC_FOLDER_ID` vs.
`GOAUTH_ARCHIVE_FOLDER_ID`); the three `GOAUTH_CLIENT_ID`/`_SECRET`/
`_REFRESH_TOKEN` credential vars are unchanged and shared across all mirrors.
`archiver.py` and `wds_archiver.py` were updated to pass their folder
explicitly — no behavior change for either.

**Dedup is Sheet-derived, not a new `_meta` key** — `sheet_writer.
mmpc_archived_file_ids()` reads column A of the new `MMPC Archived Files` tab,
exactly like `archived_doc_ids()` does for Mirror B. This fits MMPC files
better than WDS's content-hash approach: a published fileId is a static PDF
once CivicClerk assigns it (like an nSITE doc), not a live page whose content
can change in place.

**Filenames kept as bare `<fileId>.pdf`** — matches the ~24 files Trisha
already hand-uploaded (e.g. `8034.pdf`), so the folder stays internally
consistent. Meeting date / document type / name / event ID go in the Sheet
row instead, same convention as nSITE (`Archived PDFs`) and WDS (`WDS Page
Snapshots`): the Drive filename is a bare ID, human context lives in the
Sheet.

**`mmpc_watcher.py`'s existing email-reminder flow is left untouched.** It's
tested, working code with no dependents beyond its own block in `watcher.py`
(confirmed by grep — `mmpc_minutes_found` state is read/written only there).
It becomes redundant once Mirror D is enabled, but retiring it is Trisha's
call, not bundled into this change — see Alternatives.

## Consequences / residual risks (accepted)

1. **`drive.file` OAuth scope can't see Trisha's hand-uploaded files.** The
   scope only grants visibility into files this app itself created (see
   `archive_client.py`'s updated docstring). `find_in_folder()`'s idempotency
   check is therefore blind to the ~24 already-hand-placed PDFs — but the real
   dedup check (`mmpc_archived_file_ids()`, Sheet-derived) doesn't depend on
   it. The only way this bites: if a `fileId` this automation is about to
   upload happens to collide by *filename* with something Trisha placed by
   hand around the same time, Drive would end up with two same-named files
   (visual clutter, not data loss or corruption). Low-probability, low-
   severity, accepted.
2. **No alert on new archives.** Unlike the nSITE/WDS watchers, Mirror D
   doesn't email anything — it silently archives and logs to the Sheet. If
   Trisha wants a "new MMPC document archived" notification, that's a small
   follow-up (send_email() call after a successful upload), deliberately left
   out of v1 to keep this change purely additive/low-risk.
3. **CivicClerk API stability is unverified over time.** Discovered by
   inspecting live traffic, not from published API docs — CivicClerk could
   change the endpoint shape without notice. `mmpc_client.MMPCFetchError`
   makes a broken/changed API fail loudly (abort the run) rather than silently
   diffing against an empty or partial list, same contract as `WDSFetchError`.
4. **Two MMPC systems run in parallel until Trisha retires the old one** (see
   Alternatives) — some duplication (an email reminder AND a silent archive)
   during the overlap period. Harmless, just noise.

## Alternatives considered

- **New dedicated Drive folder** (not the existing `MMPC-meeting-minutes/`) —
  cleaner separation of automated vs. hand-curated content, but leaves the
  folder Trisha and the Conservancy already link to permanently stale. Not
  chosen (Trisha's explicit call).
- **Alert-only, no auto-upload** (just replace the weak "page reachable"
  heuristic with a precise "fileId X, type Minutes, posted" email) — lower
  risk, smallest change, but leaves the manual download/upload step in place,
  which was the actual thing being asked for automation. Not chosen.
- **Retire `mmpc_watcher.py`'s reminder flow immediately** — not done in this
  change. The flag-gated, additive path (build Mirror D fully working and
  reviewed first, decide on the old flow after) is lower-risk than touching
  tested code in the same change that introduces new untested code.

## Activation (Trisha's call)

1. Review + merge this branch to `main`.
2. Add the Drive folder secret to the repo: `GOAUTH_MMPC_FOLDER_ID` =
   `16yjIycNEBPhydliBe_ybU6H5roskZOmM` (the existing `MMPC-meeting-minutes/`
   folder — I can't set repo secrets myself). The three `GOAUTH_CLIENT_ID`/
   `_SECRET`/`_REFRESH_TOKEN` secrets are already live (shared with Mirror
   B/C) and need no change.
3. First enabled run archives everything CivicClerk currently has published
   for the MMPC category — no seed/baseline script needed (unlike WDS): there
   is no alert-flood risk here, since this path only ever uploads a file and
   appends a Sheet row, never sends email.
4. Set `mmpc_archive.enabled: true` in `config.yml` and commit.

Until `enabled: true` is on `main`, `mmpc-archive.yml` runs on schedule but
`mmpc_archiver.py`'s `_should_run()` gate makes every run a no-op (verified by
`tests/test_mmpc_archiver.py`, mirroring the exact test that caught the
equivalent gap in `wds_archiver.py` before it shipped — see ADR 009's
Addendum).
