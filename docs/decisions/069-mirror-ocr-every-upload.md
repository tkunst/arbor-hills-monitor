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
pages) as `has_text` overall and never OCR those pages — see Decision 2 below. This is a
documented hypothesis from `git log`, not a live-credentialed confirmation (no Drive/
Sheet reads were done to check actual `Archived At` timestamps against this build).

## Decision

1. **One shared module, one choke point.** `classify()` and `ocr_in_place()` move
   verbatim (unchanged behavior) from `egle_doc_parser.py` to the new `ocr_util.py`.
   `egle_doc_parser.py` imports both names so `egle_doc_parser.classify` /
   `egle_doc_parser.ocr_in_place` keep working for every existing caller
   (`parse_document()`'s own pre-classification OCR gate, `poison_doc_extractor.py`'s
   proactive OCR for synthesized PDFs, and both modules' tests, which monkeypatch
   these names directly) — **the classification pipeline's behavior is explicitly
   unchanged by this ADR.**
2. **New, page-by-page check for the Drive-copy gap:** `ocr_util.needs_ocr(path)`
   returns True if ANY page — every page, not a 12-page sample — is image-only (its own
   text is under 20 characters AND it carries at least one image; the same pairing
   `classify()` already uses per page internally, just applied everywhere instead of
   rolled into one whole-document verdict). This is what the mixed-document gap needs:
   a 300-page WOI report that's almost entirely real text still gets its handful of
   image-only pages caught.
   - **Refinement from the handoff's literal wording** ("any page under ~5 words needs
     OCR"): the image requirement was added so a genuinely blank separator page (no
     text, no image — nothing for ocrmypdf to add) isn't flagged. Without it, any
     document with a blank page would re-shell to ocrmypdf on every single upload,
     breaking the "already-searchable files pass straight through" requirement this
     ADR depends on for workflow timeout headroom (Decision 5).
3. **`ocr_util.ensure_searchable(local_path)`** is the fail-open layer: returns
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
4. **Wired once, in `archive_client.upload_file()`**, gated on
   `mimetype == "application/pdf"` — not `upload_pdf()` specifically, so the two direct
   `upload_file(..., "application/pdf", ...)` callers found during this build
   (`govqa_watcher.py`, `ride_docs_watcher.py`) are covered too, with no changes to
   either watcher. `upload_pdf()` stays a thin wrapper over `upload_file()`.
   `find_in_folder()`'s existing idempotency check runs FIRST, so a file already
   mirrored under that name is never re-OCR'd on a re-run. `ocr_util` is imported
   lazily inside the function so `archive_client` stays import-light for callers that
   never touch a PDF (`wds_archiver.py`'s HTML snapshots, the two watchers' non-PDF
   uploads).
5. **Ridge Wood (ADR 016's deferred note, now resolved):** `ridgewood_archiver.py` and
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
6. **Workflows.** `ocrmypdf` install step added to every workflow whose script reaches
   a PDF `upload_file()` call and lacked it: `archive.yml`, `mmpc-archive.yml`,
   `civicclerk-archive.yml`, `ridgewood.yml`, `ridgewood-reverify.yml`,
   `govqa-watch.yml`, `ride-docs-watch.yml` (the last two found during this build, not
   in the original handoff's list). `daily.yml`/`backfill.yml`/`kdp-recheck.yml`
   already had it. If the binary is missing at runtime, `ensure_searchable()` fails
   open: one count-only warning, original file uploaded.
7. **Live-path timeout budget.** `ensure_searchable()` defaults to a 300s per-document
   ocrmypdf timeout (half of `ocr_in_place()`'s 600s) — this runs inline on every
   upload, not as a dedicated backfill batch, so a shorter ceiling bounds how long one
   slow scan can hold up a workflow run; still generous for a typical EGLE filing (the
   2026-10-08 audit measured 2-10s for 1-9pp, 60-120s for 200pp). `timeout-minutes` was
   also bumped modestly on the five archiver-family workflows (archive.yml 30→45;
   mmpc-archive/civicclerk-archive/ridgewood.yml 20→30; ridgewood-reverify.yml 30→40)
   as headroom.
8. **Backfill (`scripts/backfill_mirror_ocr.py`)** for the 392 PDFs already in the
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
     OCR+reupload operations per run (a file found already-searchable doesn't count
     against the limit — it's a free skip). Group priority (N2688 first, then other
     nSITE SRN groups, then MMPC/CivicClerk/Ridge Wood) is resolved from each
     candidate's Drive filename, not a full page-count sort — page count isn't
     knowable without a download, and dry-run must make none, so a true "multi-page
     N2688 first" global sort isn't achievable without the one-time audit's own
     download pass (which this script doesn't have access to). This is a documented
     approximation of the handoff's priority ask, not the literal ordering.
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
   - **Resumable:** state file at `local-state/backfill-mirror-ocr-state.json`
     (gitignored), keyed per candidate; a file already recorded `done` or
     `skip_has_text` is never re-downloaded on a later run.
   - **Never run with `--apply` by the coder** — this PR's own backfill runs, if any,
     are dry-run only.

## Consequences

- Every future PDF upload, across all seven call sites, is guaranteed searchable
  (or fails open with a logged, count-only warning) — closing the gap the 2026-10-08
  audit found, for new documents going forward.
- The mirror becomes a **searchable derivative**, not byte-identical to the source PDF,
  for any document that needed OCR. The original scan-only bytes survive only in Drive
  revision history (pinned via `keepForever`, pending the live-credential confirmation
  noted in Decision 8) — this is a provenance trade-off Trisha is accepting by queuing
  this build, not a silent one.
- Ridge Wood's safety posture tightens: a value extracted via OCR can no longer read as
  a plain "ok" anywhere (archiver, reverify, or the Measurements Note), closing the one
  failure mode this ADR's build found that the original handoff didn't flag.
- **Not done / residual risk, explicit:** no per-run cross-document OCR time budget
  (the pattern `ride_docs_watcher.py`'s `_MIRROR_BUDGET_S` already establishes) was
  added to the five archiver-family workflows. Decision 7's per-document 300s cap plus
  the `timeout-minutes` bumps are a mitigation, not a guarantee, against a pathological
  run where many documents in one batch all need OCR at once. If a workflow starts
  timing out in practice, that budget pattern is the recommended fix — threading it
  through `archive_client.upload_file()` across all seven call sites was judged too
  large an expansion for this draft PR.
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
