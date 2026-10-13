# ADR 069 — Every Drive-mirrored PDF must be searchable (ocr_util, one choke point)

*Status: **BUILT, DRAFT PR** — 2026-10-13 (handoff
`documents/arbor-hills/analysis/mirror-ocr-audit-2026-10-08/HANDOFF-coder-mirror-ocr.md`,
queue slug `coder:mirror-ocr`). Live-path change: not auto-merged.*

## Context

Trisha's rule, already live in `/dedupe-curate` Step 3b since 2026-10-08: every PDF
that reaches a public surface must be fully searchable. A 2026-10-10 full scan of the
Case File Mirror (2,103 PDFs) found **392 (18.6%) with no text layer**, worst in N2688
(the WOI/HOV reports the watcher actively classifies) at **36%** — plus 44 multi-page
scan-only docs carrying real substantive content, and 10 malformed PDFs excluded from
the count.

`egle_doc_parser.classify()` + `ocr_in_place()` already existed and already ran
correctly: `parse_document()` OCRs BEFORE classification, and watcher.py/backfill.py
upload that same OCR'd file. **The classifier was never blind** — a 2026-10-10
overnight-worker analysis (`documents/arbor-hills/analysis/mirror-ocr-audit-2026-10-08/
README.md` in Lotext) proposed rebuilding the OCR check from scratch and was corrected
before this build: the actual gap is narrower and lives downstream of classification,
in the Drive-mirror upload path itself.

**Where the 392 actually came from (the choke point never covered them):**
`archive_client.upload_pdf()`/`upload_file()` uploads whatever local file it's handed —
and five modules call it with a PDF that was **never** run through
`parse_document()`'s OCR step at all:

- `archiver.py`'s catch-up branch (`_mirror_and_record()`, no `local_path` given) —
  re-downloads the PDF fresh from nSITE and uploads it directly.
- `mmpc_archiver.py`, `civicclerk_archiver.py` — download CivicClerk PDFs straight to
  Drive; neither ever calls `egle_doc_parser`.
- `ridgewood_archiver.py`, `ridgewood_reverify.py` — ADR 016 deliberately *deferred*
  OCR here: a scanned Ridge Wood report was mirrored + flagged for manual OCR, never
  fixed automatically.
- Two more direct `upload_file(..., "application/pdf", ...)` callers found during this
  build, not named in the original audit: `govqa_watcher.py` (GovQA request-file
  staging) and `ride_docs_watcher.py` (RRD document mirror).

**Likely primary cause, with evidence, not just plausible:** ADR 007 (the Drive mirror)
shipped 2026-06-14 using ONLY `archiver.run()`'s nightly batch — `_mirror_and_record()`'s
no-`local_path` branch, which re-downloads raw from nSITE and was never routed through
`parse_document()`. The INLINE, parse-time mirror path (`mirror_one_now()`, called from
watcher.py/backfill.py so the file that gets OCR'd is the SAME file that gets uploaded)
didn't exist until the 2026-08-23 addendum (`0f03adc`) — **223 commits, about 10 weeks,**
during which every single mirrored document (including the initial ~754-doc historical
backfill) went through the never-OCR'd catch-up path. That window, plus the structurally
never-OCR'd MMPC/CivicClerk/Ridge Wood/GovQA/RRD paths, plausibly accounts for most of
the 392. A secondary, smaller contributor: `classify()`'s own aggregate, 12-page-sampled
verdict can read a mixed document (mostly real text, a few image-only cover or signature
pages) as `has_text` overall and never OCR those pages — see Decision 3 below. This is a
documented hypothesis from `git log`, not a live-credentialed confirmation (no Drive/
Sheet reads were done to check actual `Archived At` timestamps against this build).

**Mid-build correction (2026-10-13, same day): the classification pipeline's gate DOES
change, not just the Drive-copy path.** The paragraph above ("the classifier was never
blind") is only TRUE for the common case. A fresh check against a real specimen found a
1,006-page WOI/CJ compilation with 316 image-only pages, ALL past `classify()`'s 12-page
sample window — `parse_document()` never called `ocr_in_place()` on it at all, and those
316 pages' content never reached the classifier. `parse_document()`'s OCR gate is
corrected in this same PR (Decision 2) to close that gap too, with a fail-open split
that keeps `main`'s exact behavior for every case `classify()` already caught. **This
changes watcher.py's and backfill.py's live classification behavior**, not only the
archive/upload path the original handoff scoped this task to — stated plainly here and
in the PR, since it's a materially larger blast radius than the handoff described.

## Decision

1. **One shared module, one choke point.** `classify()` and `ocr_in_place()` move
   verbatim (unchanged behavior) from `egle_doc_parser.py` to the new `ocr_util.py`.
   `egle_doc_parser.py` imports both names so `egle_doc_parser.classify` /
   `egle_doc_parser.ocr_in_place` keep working for every existing caller
   (`poison_doc_extractor.py`'s proactive OCR for synthesized PDFs, and both modules'
   tests, which monkeypatch these names directly). `classify()` the FUNCTION is
   unchanged; `ocr_in_place()` the function is unchanged — **but see 1a: what
   `parse_document()` does with them changed mid-build.**
2. **`parse_document()`'s OCR gate (corrected 2026-10-13, same day, after the
   1,006-page specimen above): checks BOTH verdicts, with different failure handling —
   this split is load-bearing, not cosmetic.**
   - `classify()` says `needs_ocr`/`likely` → call `ocr_in_place()` with NO try/except,
     exactly `main`'s behavior: a failure RAISES (a poison strike upstream in
     watcher.py/backfill.py), unchanged for every document that already triggered OCR
     there before this PR.
   - `classify()` says `has_text`/`empty` but `ocr_util.needs_ocr()` (1b) still finds an
     image-only page → a document `main` NEVER attempted OCR on (it classified,
     alerted, and shipped fine without it). This is a NEW capability, not a tightened
     requirement, so it's **FAIL-OPEN**: an OCR failure here is logged (class only,
     SEC-001 posture) and the parse proceeds with native text — exactly as if
     `needs_ocr()` had said False. A document that classifies fine on `main` cannot
     become a poison strike / `MAX_ERRORS` stub on this branch.
   - `ocr_in_place()`'s own contract (600s timeout, raise-on-failure) is otherwise
     untouched. A document `classify()` ALSO flags can still hit that 600s ceiling and
     poison-strike — not a new risk, same as `main` today.
3. **New, page-by-page check for the Drive-copy gap (and now also 1a's gap):**
   `ocr_util.needs_ocr(path)` returns True if ANY page — every page, not a 12-page
   sample — is image-only (LITERALLY NO extractable text AND at least one image). This
   is what the mixed-document gap needs: a 300-page WOI report that's almost entirely
   real text still gets its handful of image-only pages caught.
   - **Threshold corrected 2026-10-13 (same day) after a spike against the REAL
     `ocrmypdf` binary.** The first version used "under 20 characters" (a refinement of
     the handoff's literal "~5 words"), reasoning that a stray caption wouldn't matter.
     It does: `ocrmypdf --skip-text` skips a page the instant it has ANY text at all —
     confirmed empirically ("1 skipping all processing on this page" for a page
     captioned only "Photo 1", and even for a single stray character). A <20-char
     threshold flags such a page, ocrmypdf then skips it anyway, and the page reads
     `needs_ocr()==True` again on the very next check — the file never converges, every
     future upload re-invokes ocrmypdf for zero gain (breaking the "pass straight
     through" promise from the OTHER direction than the blank-page case below), and the
     backfill script (Decision 9) would mislabel a genuinely successful OCR run as
     `ocr_incomplete` forever. The threshold is now EXACTLY ZERO characters, matching
     what `--skip-text` itself treats as "already has text."
   - The image requirement (not just a text-length check) is still there so a
     genuinely blank separator page (no text, no image — nothing for ocrmypdf to add)
     isn't flagged either. Without it, any document with a blank page would re-shell to
     ocrmypdf on every single upload.
4. **`ocr_util.ensure_searchable(local_path)`** is the fail-open layer: returns
   `local_path` unchanged (no copy, no subprocess) when `needs_ocr()` is False — the
   common case, since the watcher/backfill pipeline already OCR'd via
   `parse_document()` before ever reaching an upload. When a page is image-only, it
   OCRs a **temp copy** (never `local_path` itself — Ridge Wood's content hash and the
   ADR 066 provenance manifest both need the original bytes untouched) and returns
   that path. **Never raises.** Any failure (corrupt PDF, missing `ocrmypdf` binary,
   non-zero exit, timeout) logs one count-only warning and returns the original path —
   upload the document as-is rather than drop it. **SEC-001:** the log line never
   carries a filename, title, or exception text (this repo's Actions logs are public);
   only a class name and exit code.
5. **Wired once, in `archive_client.upload_file()`**, gated on
   `mimetype == "application/pdf"` — not `upload_pdf()` specifically, so the two direct
   `upload_file(..., "application/pdf", ...)` callers found during this build
   (`govqa_watcher.py`, `ride_docs_watcher.py`) are covered too, with no changes to
   either watcher. `upload_pdf()` stays a thin wrapper over `upload_file()`.
   `find_in_folder()`'s existing idempotency check runs FIRST, so a file already
   mirrored under that name is never re-OCR'd on a re-run. `ocr_util` is imported
   lazily inside the function so `archive_client` stays import-light for callers that
   never touch a PDF (`wds_archiver.py`'s HTML snapshots, the two watchers' non-PDF
   uploads).
6. **Ridge Wood (ADR 016's deferred note, now resolved):** `ridgewood_archiver.py` and
   `ridgewood_reverify.py` both call `ensure_searchable()` BEFORE text extraction (not
   after upload) — `content_hash()` still runs on the ORIGINAL bytes, then
   `ensure_searchable()`, then `extract_text()` on the result, then upload of that same
   searchable path (so `upload_file()`'s own internal `ensure_searchable()` call is a
   fast no-op, not a second OCR pass). The "likely scanned, review manually" flag still
   exists but now fires only if the text layer is **still empty after OCR**.
   - **Safety addition, not in the handoff:** when a value WAS extracted only because
     of OCR, the report is pinned to the review tier (`alert=True`, a reason appended)
     regardless of what the deterministic classifier concluded, and the Measurements
     row's Note carries the same caveat. An OCR misread of one digit could turn a real
     72 ppb exceedance into a false "all clear" — exactly the silent miss ADR 016's
     fail-safe exists to prevent — so a value born from OCR is never allowed to read as
     a plain "ok". A genuine `exceed_24h` determination still reaches the full
     `alert_recipients` list; this only forces the floor up to "review" for anyone
     whose "ok"/"all_clear" came from OCR'd text, and routes `ridgewood_reverify.py`'s
     owner-only note the same way (that path already goes to Trisha alone, never the
     public list).
   - **Precision fix 2026-10-13 (found by independent review):** `ocr_applied` must
     mean "this month's VALUE came from OCR'd text," not just "`ensure_searchable()`
     changed the file for some unrelated reason" — e.g. an incidental image-only
     logo/stamp page elsewhere in an otherwise born-digital report. Both files now try
     the NATIVE extraction first and only fall back to the OCR'd copy (setting
     `ocr_applied` only then) when the native pass finds nothing — so a normal,
     natively-readable report with one unrelated image page is never forced to the
     review tier on a false pretext.
7. **Workflows.** `ocrmypdf` install step added to every workflow whose script reaches
   a PDF `upload_file()` call and lacked it: `archive.yml`, `mmpc-archive.yml`,
   `civicclerk-archive.yml`, `ridgewood.yml`, `ridgewood-reverify.yml`,
   `govqa-watch.yml`, `ride-docs-watch.yml` (the last two found during this build, not
   in the original handoff's list). `daily.yml`/`backfill.yml`/`kdp-recheck.yml`
   already had it. If the binary is missing at runtime, `ensure_searchable()` fails
   open: one count-only warning, original file uploaded.
8. **Live-path timeout budget.** `ensure_searchable()` defaults to a 300s per-document
   ocrmypdf timeout (half of `ocr_in_place()`'s 600s) — this runs inline on every
   upload, not as a dedicated backfill batch, so a shorter ceiling bounds how long one
   slow scan can hold up a workflow run; still generous for a typical EGLE filing (the
   2026-10-08 audit measured 2-10s for 1-9pp, 60-120s for 200pp). `timeout-minutes` was
   also bumped modestly on the five archiver-family workflows (archive.yml 30→45;
   mmpc-archive/civicclerk-archive/ridgewood.yml 20→30; ridgewood-reverify.yml 30→40)
   as headroom.
9. **Backfill (`scripts/backfill_mirror_ocr.py`)** for the 392 PDFs already in the
   mirror. See its own module docstring for the full contract; summary:
   - **Source of truth: the Sheet's archive tabs** (Archived PDFs, MMPC Archived
     Files, CivicClerk Archived Files, Ridge Wood Reports) — so it only ever touches
     files this app created (`drive.file` scope can't see anything else, and
     Hand-Curated Files / WDS Page Snapshots are deliberately excluded: the former
     isn't app-created, the latter is HTML not PDF).
   - **Dry-run makes ZERO Drive API calls** — it can only enumerate Sheet rows and
     report counts, since any OCR-needs check requires downloading the file. This is a
     deliberate, narrower reading than the original README's "counts only" framing
     (which implicitly assumed inspecting content); the trade-off is that dry-run
     cannot report how many files actually need OCR, only how many candidate rows
     exist per tab.
   - **`--apply`** does the real work, capped at `--limit` (default 100) ACTUAL
     OCR+reupload attempts per run (a file found already-searchable doesn't count
     against the limit — it's a free skip). Priority is TAB-LEVEL, not a page-count
     sort: candidates are processed in a FIXED tab order (Archived PDFs — the nSITE
     mirror, dominated by N2688 — first, entirely, then MMPC, CivicClerk, Ridge Wood
     Reports, Ridge Wood Revisions last). A true "multi-page N2688 first, then
     multi-page other groups, then 1-page" sort would need per-candidate Drive
     metadata (the SRN lives in the Drive filename, e.g. `N2688_<doc_id>.pdf`, not in
     any Sheet column) or a download — and dry-run must make zero Drive calls — so this
     is a documented, simpler approximation of the handoff's priority ask, not the
     literal ordering. An earlier draft of this ADR described filename-based SRN
     sub-grouping; that was cut for the simpler tab-order approximation before any
     `--apply` run, and this is the corrected description.
   - **Checks locally BEFORE touching Drive at all** (added 2026-10-13, found by
     independent review): after OCR runs, `needs_ocr()` is re-checked on the OCR'd
     copy — a page with a genuinely unreadable image (no recoverable text no matter
     how many times OCR runs) still fails that check after a real, successful
     `ocrmypdf` pass. If it's still flagged, the file is recorded `ocr_incomplete`
     WITHOUT ever pinning a revision or overwriting Drive — no churn for zero benefit.
   - **Overwrites IN PLACE** with `files().update(fileId=..., media_body=...)` — same
     file id, so every Sheet link, public-records page link, and emailed link keeps
     working. Before overwriting, the current head revision is pinned
     (`revisions().update(..., body={"keepForever": True})`) — **the Drive API purges
     non-head binary-file revisions ~30 days after new content unless pinned**, so
     without this step the handoff's "the scan-only version stays in revision
     history" claim would quietly become false 30 days after the first `--apply` run.
     This repo's `drive.file`-scoped OAuth token has not been confirmed able to call
     `revisions().update()` against live credentials (no Drive access from inside this
     build) — **flagged as a residual risk for Trisha to verify on the first real
     `--apply` run**, not assumed.
   - **Failure handling, refined 2026-10-13 (found by independent review):** the
     pin+update step and the post-update verify step are now two SEPARATE try/except
     blocks. If the update itself fails, that's `error_upload` (retryable — see below
     — and treated as a SYSTEMIC signal: `run_apply()` aborts the rest of that run
     immediately rather than repeating the same failure up to `--limit` or
     `_MAX_SCAN_PER_RUN` times). If the update SUCCEEDS but the post-update
     re-download/verify step fails, that's `done_unverified` — never `error_upload` —
     so a later run doesn't re-OCR and re-upload a file that's probably already fixed.
     A whole-run precheck confirms the `ocrmypdf` binary actually resolves to an
     executable before `--apply` does anything, so a missing binary on the machine
     (a config problem, not a per-file one) can't get every candidate permanently
     recorded `ocr_failed`.
   - **Resumable:** state file at `local-state/backfill-mirror-ocr-state.json`
     (gitignored), keyed per candidate, saved INCREMENTALLY after every candidate (not
     only at the end — a crash mid-run must never lose the record of a file already
     overwritten in Drive). Every status is terminal (never auto-retried) EXCEPT
     `error_download` and `error_upload`, which are transient-shaped and get
     re-attempted on the next run; a human can force a retry of any other status by
     deleting its entry from the state file.
   - **Never run with `--apply` by the coder** — this PR's own backfill runs, if any,
     are dry-run only.

## Consequences

- Every future PDF upload, across all seven call sites, is guaranteed searchable
  (or fails open with a logged, count-only warning) — closing the gap the 2026-10-08
  audit found, for new documents going forward.
- The mirror becomes a **searchable derivative**, not byte-identical to the source PDF,
  for any document that needed OCR. The original scan-only bytes survive only in Drive
  revision history (pinned via `keepForever`, pending the live-credential confirmation
  noted in Decision 9) — this is a provenance trade-off Trisha is accepting by queuing
  this build, not a silent one.
- Ridge Wood's safety posture tightens: a value extracted via OCR can no longer read as
  a plain "ok" anywhere (archiver, reverify, or the Measurements Note), closing the one
  failure mode this ADR's build found that the original handoff didn't flag.
- **This PR changes LIVE CLASSIFICATION behavior, not only the upload/archive path the
  handoff scoped it to.** `parse_document()`'s OCR gate (Decision 2) now also fires for
  a needs_ocr()-only page `classify()` misses — new coverage for `watcher.py` and
  `backfill.py`, both of which call `parse_document()` on every document, every run.
  The fail-open split means this cannot turn a document that classifies fine on `main`
  into a poison strike, but it CAN make a previously-instant classification take up to
  an extra ~600s on a large or pathological file (a digitally-signed or encrypted PDF,
  a very large scanned compilation). `daily.yml`'s `timeout-minutes` was bumped 45→60
  for headroom; `backfill.yml` (already 60 with ~20 min baseline for a 50-doc batch)
  was left as-is. Not a correctness risk (fail-open), but a real wall-clock one if many
  documents in one run newly trigger this path — flagged, not fully mitigated.
- **Not done / residual risk, explicit:** no per-run cross-document OCR time budget
  (the pattern `ride_docs_watcher.py`'s `_MIRROR_BUDGET_S` already establishes) was
  added to the five archiver-family workflows or to `watcher.py`/`backfill.py`.
  Decision 8's per-document 300s cap (upload path) / `ocr_in_place()`'s existing 600s
  cap (classification path) plus the `timeout-minutes` bumps are a mitigation, not a
  guarantee, against a pathological run where many documents in one batch all need OCR
  at once. If a workflow starts timing out in practice, that budget pattern is the
  recommended fix — threading it through every call site was judged too large an
  expansion for this draft PR.
- **Not done:** the backfill script's `--apply` path has not been run against live
  credentials by this build (no Drive/Sheet access from inside this session) — the
  revision-pinning step, the in-place `files().update()`, and the re-download
  verification are implemented and unit-tested against mocks only. The first real
  `--apply` run is Trisha's, locally, per the handoff.

## Not done (out of scope)

- No change to `classify()`'s verdict or the classification pipeline's own OCR gate —
  this ADR is about the Drive copies, per the handoff.
- No retroactive re-check of already-mirrored, already-searchable files.
- No multilingual OCR (`eng` only, per the handoff's decision 4 — no known non-English
  content in the mirror).
- No MB-based size skip for `ensure_searchable()` (per the handoff's decision 3) —
  large scans still upload, just unsearchable until a manual/backfill pass, bounded by
  the 300s timeout and a count-only timeout log line.
