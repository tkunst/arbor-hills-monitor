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
3. **New, page-by-page check for the Drive-copy gap (and now also Decision 2's gap):**
   `ocr_util.image_only_pages(path)` returns the SET of pages — every page, not a
   12-page sample — that are image-only (LITERALLY NO extractable text AND at least
   one image); `needs_ocr(path)` is just `bool()` of that set. This is what the
   mixed-document gap needs: a 300-page WOI report that's almost entirely real text
   still gets its handful of image-only pages caught. The SET form (not just the
   bool) is what Decision 9's backfill script needs to tell a partial improvement
   from none — see Decision 9.
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
   that path.
   - **Skips the subprocess call entirely** (added 2026-10-13, the advisor) if the
     document's own `/Creator` metadata already starts with `OCRmyPDF` — confirmed
     empirically that ocrmypdf stamps this on every output file it produces, whether a
     given page was actually OCR'd or skipped for already having text. A document
     already run through ocrmypdf (e.g. by `parse_document()`) that STILL has an
     image-only page has nothing more to recover; re-running ocrmypdf would just repeat
     the same failed recognition attempt for up to `timeout` seconds. This only skips
     the SUBPROCESS call — `needs_ocr()`'s own verdict is unaffected, so a caller that
     checks it afterward (the backfill script) still correctly sees the page as
     unrecoverable.
   - **Validates ocrmypdf's output before trusting it** (added 2026-10-13, the
     advisor): rc=0 alone isn't proof of a usable result. The output must be non-empty,
     openable by `fitz`, have the SAME page count as the input, and introduce no NEW
     image-only page (a page that had text must never lose it — `--skip-text` should
     make this structurally impossible, but it's cheap to re-check on the live upload
     path rather than trust that invariant blindly). Any validation failure is treated
     the same as any other OCR failure: fail open, return the original path.
   - **Never raises** — the entire function body, including temp-file creation and
     binary resolution (previously outside any try/except, contradicting this
     guarantee — found by independent review), now runs inside one try/except. Any
     failure (corrupt PDF, missing `ocrmypdf` binary, non-zero exit, timeout, or a
     failed output validation) logs one count-only warning and returns the original
     path — upload the document as-is rather than drop it, or upload something
     ocrmypdf silently corrupted. **SEC-001:** the log line never carries a filename,
     title, or exception text (this repo's Actions logs are public); only a class name
     and exit code.
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
   - **Fail-safe regression fixed 2026-10-13 (found by round-2 independent review):**
     `ridgewood_archiver.py`'s fallback extraction (on the OCR'd copy, when native
     extraction found nothing) was unguarded — if it raised, the per-month exception
     handler swallowed the WHOLE month (no row, no measurement, no alert at all),
     which is a real regression from `main`'s "the month is archived, never dropped"
     fail-safe. Now wrapped: a failure there falls back to `has_text=False`, so the
     existing "no text layer, review manually" branch still writes a row instead of
     silently skipping the month. (`ridgewood_reverify.py`'s equivalent fallback was
     already guarded this way from the first draft.)
7. **Workflows.** `ocrmypdf` install step added to every workflow whose script reaches
   a PDF `upload_file()` call and lacked it: `archive.yml`, `mmpc-archive.yml`,
   `civicclerk-archive.yml`, `ridgewood.yml`, `ridgewood-reverify.yml`,
   `govqa-watch.yml`, `ride-docs-watch.yml` (the last two found during this build, not
   in the original handoff's list). `daily.yml`/`backfill.yml`/`kdp-recheck.yml`
   already had it. If the binary is missing at runtime, `ensure_searchable()` fails
   open: one count-only warning, original file uploaded.
   - **`continue-on-error: true` added to the install step in ALL TEN workflows**
     (the 7 above plus the 3 that already had the step), found by the advisor
     2026-10-13: without it, a flaky apt mirror fails the WHOLE job before any of
     this repo's own logic runs — and `ridgewood.yml` specifically carries the H2S
     safety-alert pipeline (`ridgewood.enabled: true`, live since 2026-07-15), where
     ADR 016 already rules the safety function must never depend on the mirror
     succeeding. `ensure_searchable()`/`ocr_in_place()` already fail open per-document
     when the binary is missing, so this just extends that same posture to the
     install step itself.
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
     Files, CivicClerk Archived Files) — so it only ever touches files this app
     created (`drive.file` scope can't see anything else, and Hand-Curated Files /
     WDS Page Snapshots are deliberately excluded: the former isn't app-created, the
     latter is HTML not PDF). **Ridge Wood Reports / Ridge Wood Revisions are ALSO
     excluded (decided 2026-10-13, cutting an earlier draft of this script that
     included them):** every one of the 66 Ridge Wood reports is verified text-layer
     per ADR 016's own doc, so there's nothing to fix there, and overwriting one would
     break the Content Hash <-> Archive Link pairing ADR 066's provenance manifest
     relies on (the hash is of the ORIGINAL bytes; an OCR'd derivative wouldn't match
     it). This also removes the row-number-keying fragility a round-1 review flagged
     for the Revisions tab, since that tab is no longer touched at all.
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
     mirror, dominated by N2688 — first, entirely, then MMPC, then CivicClerk). A true
     "multi-page N2688 first, then multi-page other groups, then 1-page" sort would
     need per-candidate Drive metadata (the SRN lives in the Drive filename, e.g.
     `N2688_<doc_id>.pdf`, not in any Sheet column) or a download — and dry-run must
     make zero Drive calls — so this
     is a documented, simpler approximation of the handoff's priority ask, not the
     literal ordering. An earlier draft of this ADR described filename-based SRN
     sub-grouping; that was cut for the simpler tab-order approximation before any
     `--apply` run, and this is the corrected description.
   - **Checks locally BEFORE touching Drive at all, and tracks the SET of image-only
     pages, not just a bool** (added 2026-10-13, found by independent review, then
     corrected again the same day by a SECOND round of independent review +
     the advisor — this was the single highest-severity finding across both rounds):
     `ocr_util.image_only_pages()` (not `needs_ocr()`) is re-checked on the OCR'd
     copy. A page with a genuinely unreadable image (a photo with nothing printed on
     it) stays in that set no matter how many times OCR runs — confirmed
     empirically — and WOI/inspection filings routinely carry such pages ALONGSIDE
     real tables. The FIRST version of this check treated "still flagged anywhere"
     as "OCR failed, don't write Drive," which would have thrown away a real
     improvement on every OTHER page of a mixed scan — plausibly leaving a large
     share of the 392-file backlog unfixed while still spending `--limit` on each
     one. The corrected rule: write to Drive if the image-only-page set strictly
     SHRANK (`done` if it's now empty, `done_partial` if some genuinely unreadable
     pages remain); skip the Drive write only when OCR helped NOTHING at all
     (`ocr_incomplete`).
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
   - **Failure handling, refined 2026-10-13 across two rounds of independent review:**
     the pin+update step and the post-update verify step are two SEPARATE try/except
     blocks. If the update itself fails, that's `error_upload` — retryable on the next
     run (see below), AND treated as a SYSTEMIC signal the FIRST time it happens for a
     given file: `run_apply()` aborts the rest of that run immediately rather than
     repeating the same failure up to `--limit` or `_MAX_SCAN_PER_RUN` times. If the
     SAME file then fails the SAME way AGAIN on its retry, that's reclassified
     file-specific, not systemic (round 2 fix: a single bad file would otherwise abort
     every future run forever, since it's always the first thing re-attempted) —
     recorded `"<status>_persistent"` (now terminal) and the run continues past it. If
     the update SUCCEEDS but the post-update re-download/verify step fails, that's
     `done_unverified` — never `error_upload` — so a later run doesn't re-OCR and
     re-upload a file that's probably already fixed. Before `--apply` does anything, a
     whole-run precheck runs a REAL smoke test (OCRs a tiny synthetic image-only PDF
     and confirms the output is genuinely different) rather than just checking that a
     file exists at the resolved binary path — `isfile()`+executable passes even when
     ocrmypdf is installed but its own Tesseract dependency is missing, which would
     otherwise get every real candidate permanently recorded `ocr_failed` for a
     machine-config reason, not a per-file one.
   - **Resumable:** state file at `local-state/backfill-mirror-ocr-state.json`
     (gitignored, and anchored to the repo root rather than the invoking shell's cwd —
     a bare relative path would silently lose resume if the script were ever run from
     elsewhere), keyed per candidate, saved INCREMENTALLY after every candidate (not
     only at the end — a crash mid-run must never lose the record of a file already
     overwritten in Drive). Most statuses are terminal (never auto-retried);
     `error_download` and `error_upload` are the exception (transient-shaped, retried
     on the next run) unless they recur on the same file (see above); a human can force
     a retry of any terminal status by deleting its entry from the state file.
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

- No change to `classify()`'s OWN verdict logic (the function itself, moved verbatim).
  **This is narrower than the handoff's original framing** — the handoff scoped this
  ADR to the Drive copies only, but Decision 2 (added mid-build, 2026-10-13) DOES
  change what `parse_document()` does with that verdict: `classify()` is no longer the
  sole input to whether `ocr_in_place()` runs. See Decision 2 and the Context section's
  mid-build-correction note for the full reasoning and the real specimen that forced it.
- No retroactive re-check of already-mirrored, already-searchable files.
- No multilingual OCR (`eng` only, per the handoff's decision 4 — no known non-English
  content in the mirror).
- No MB-based size skip for `ensure_searchable()` (per the handoff's decision 3) —
  large scans still upload, just unsearchable until a manual/backfill pass, bounded by
  the 300s timeout and a count-only timeout log line.

## Addendum (2026-10-14) — the backfill-mirror-ocr hang, coder:ocr-hang-fix

**Incident.** The `backfill-mirror-ocr` GitHub Actions run (run `38585715746`,
`scripts/backfill_mirror_ocr.py --apply --limit 50`) fixed one candidate at
15:04Z, then produced **zero further log output** until Trisha manually
cancelled it at 19:56:29Z — 4h53m later (`gh run view 38585715746`: "The run
was canceled"). The script had no per-candidate log line at all (only a
final summary, printed once at the very end and never reached on a stall),
so the stuck candidate was unknowable from the log.

**Two mechanisms were suspected going in; both turned out to be more subtle
than first described, found by direct verification before writing any fix
— not assumed:**

1. **"`ocr_in_place()`/`ensure_searchable()`'s `subprocess.run(timeout=...)`
   can block indefinitely because Python only kills the direct child, and
   ocrmypdf's own worker processes keep the stdout/stderr pipes open."**
   **Refuted as literally stated, confirmed by a direct repro:** a fake
   "ocrmypdf" that forks a detached grandchild holding the pipes open does
   **not** prevent `subprocess.run(timeout=N)` from raising
   `TimeoutExpired` and returning within `N` seconds — its `communicate()`
   loop is driven by a wall-clock deadline check via a selector, not by
   waiting for pipe EOF. What **is** real, confirmed by the same repro: the
   grandchild survives, unkilled — `subprocess.run()`'s own timeout handler
   only `kill()`s the one pid it holds. One leaked orphan per timeout is not
   a hang by itself, but `scripts/backfill_mirror_ocr.py` can hit this path
   repeatedly in one run, and a pile of orphaned OCR worker processes
   competing for a GitHub-hosted runner's 2-4 vCPUs for the rest of a run is
   a real way for everything after the first timeout to get much slower
   than the nominal per-call ceiling suggests. **Shipped as "orphan
   reaping,"** not "fixes an indefinite block": `ocr_util._run_ocrmypdf()`
   now runs ocrmypdf in its own process group (`start_new_session=True`)
   and, on timeout, `os.killpg()`s the whole group.
2. **"Drive API calls (`archive_client`/`drive_client`) have no network
   timeout — `build()` uses default httplib2 with `timeout=None`."**
   **Refuted by direct inspection of the installed library:**
   `googleapiclient.discovery.build(credentials=...)` — which is exactly
   what `drive_client.drive_service()`, `drive_client.sheets_service()`,
   and `archive_client.oauth_drive_service()` were all already calling —
   resolves internally to `googleapiclient.http.build_http()`, which sets
   `httplib2.Http(timeout=60)` by default (confirmed by reading
   `googleapiclient._auth.authorized_http()` and `build_http()`'s own
   source in this repo's pinned version, and by constructing a real
   service with `AnonymousCredentials()` and reading
   `svc._http.http.timeout` — it printed `60`, not `None`). The OAuth
   token-refresh call (`archive_client._credentials()`'s
   `creds.refresh(Request())`) separately defaults to a 120s timeout via
   `google.auth.transport.requests.Request`. A bare `httplib2.Http()` (no
   args) genuinely has no timeout and hangs forever against a stalled
   connection — confirmed by a local repro against a real TCP listener that
   accepts the connection and never responds — but that is not the
   constructor this codebase's `build()` calls were using. **Shipped as
   "make the existing bound explicit and in-repo,"** not "close a
   previously-unbounded gap": `drive_client.authorized_http()` builds
   `httplib2.Http(timeout=HTTP_TIMEOUT_S)` with `HTTP_TIMEOUT_S = 60` — the
   SAME value, deliberately, so this does not loosen or tighten anything.
   `drive_service()`/`sheets_service()`/`oauth_drive_service()` now pass
   `http=authorized_http(creds)` instead of a bare `credentials=` (the two
   are mutually exclusive on `build()` — confirmed from
   `build_from_document()`'s own source, which raises `ValueError` if both
   are given).

**What the evidence actually shows, instead.** `gh run view --job=... --log`
for the incident confirms the script's output was completely silent from
15:02:59.36Z (the only line being Python's own `fitz` deprecation warning at
import time) until the 19:56:29.37Z cancellation — consistent with having
no per-candidate log line at all, not informative about *why*.

A first attempt to narrow this further used `gh cache list`'s reported
sizes for the resume-state cache (`local-state/backfill-mirror-ocr-state.
json`, cached between runs — 1.20 KiB restored from the prior run, 1.89 KiB
saved at cancellation) to argue the run only recorded a handful of new
entries, not fifty. **That argument is withdrawn — it doesn't hold up.**
`gh cache list` reports the size of the compressed cache archive, and this
state file is repetitive JSON (the same few key names and status strings
repeated per entry); zstd/gzip-style compression on that kind of data can
easily fit several dozen entries into well under a kilobyte of compressed
size. A 0.69 KiB compressed delta says nothing reliable about entry count
either way, so this evidence does not actually rule out "a cascade of
individually-bounded OCR timeouts" the way it was first written up to.

A second, more direct check: load the script the way the tests do, import
`drive_client`, and probe what a bare `log.warning(...)` call actually does
with NO logging configured anywhere (exactly this script's real condition —
`main()` never calls `logging.basicConfig()`). Result, confirmed by
executing it: a `logging.getLogger("ocr_util").warning(...)` call DOES
reach stderr, via Python's `logging.lastResort` fallback (the root logger
has no handlers, so the default last-resort stderr handler fires) — meaning
`ensure_searchable()`'s own count-only timeout/failure warnings WOULD have
shown up in the Actions log if any had fired during the stall. Since none
did, this rules out an `ensure_searchable()`-driven OCR-subprocess-timeout
cascade specifically — a cleaner, more direct way to reach the same
narrower conclusion the (withdrawn) cache-size argument was reaching for.
**But this does NOT extend to Drive-API-level retries/timeouts**: importing
the real `googleapiclient` package and re-running the same probe shows it
installs a `logging.NullHandler()` on its own top-level `googleapiclient`
logger at import time — and once ANY handler (even a no-op one) is present
anywhere in a logger's ancestor chain, Python's `lastResort` fallback never
fires for it. A warning logged from `googleapiclient.http` (where this
library's own retry/backoff logging would originate) is confirmed, by the
same direct test, to produce **zero output anywhere** — not even to
stderr. So a silent, repeated Drive-side retry/backoff grind (bounded, per
call, by `HTTP_TIMEOUT_S` × `GOOGLE_API_NUM_RETRIES` plus backoff, but
possibly recurring across many calls within one candidate) is **not ruled
out** by the empty log; the log would look identical either way.

**Honest conclusion: the exact call that hung is not conclusively
identified.** Every timeout mechanism this addendum could directly test —
`subprocess.run()`'s own deadline, `httplib2`'s per-request socket timeout,
`google.auth`'s OAuth-refresh timeout, this repo's existing
`GOOGLE_API_NUM_RETRIES` retry/backoff — behaved correctly and boundedly
under realistic simulation of their documented failure modes, and an
`ensure_searchable()`-specific OCR-timeout cascade is now reasonably ruled
out by the logging check above. What remains open: a real hang in the
actual `ocrmypdf` binary under a condition this addendum's Python stand-in
couldn't reproduce (e.g. an uninterruptible kernel wait state — `signal.
alarm`/`proc.wait()` cannot interrupt that either, so if this is the real
mechanism, nothing in this PR can break it loose, only bound how long it's
allowed to hold up the REST of a run); a resumable-upload / token-refresh
edge case specific to a pathological server response not bounded
end-to-end by a 60s per-request timeout; or a silent Drive-side retry grind
per the `NullHandler` finding above. Given that, the two fixes below are
deliberately **root-cause-agnostic** — they bound the BLAST RADIUS of this
exact failure mode (one file silently consuming an entire run) regardless
of which call turns out to be responsible, rather than resting on a
specific, unconfirmed mechanism. This is a strong mitigation, not a
guarantee that no candidate can ever stall again — see the explicit
residual risk on the watchdog below, and the open security finding noted
at the end of this addendum, which is NOT fixed in this PR:

1. **Per-candidate progress lines** (`scripts/backfill_mirror_ocr.py`):
   `[backfill] <n> <key> starting` before each candidate, flushed, and
   `[backfill] <n> <key> <status> <elapsed>s` after — `<key>` only
   (nSITE/CivicClerk ids, already public), never a title, link, or
   `result["note"]` (which can carry a Drive file id pair on
   `error_id_changed`, or an exception class name on `error_download`/
   `error_upload`). This is the direct fix for "the stuck file is unknown"
   — the next occurrence will show exactly which candidate, and for how
   long, before anyone needs to cancel the run.
2. **A per-candidate watchdog**, `_CANDIDATE_TIMEOUT_S = 900` (15 min):
   `signal.alarm()` (POSIX-only — fine, this only ever runs on macOS
   locally or `ubuntu-latest` in Actions) bounds the ENTIRE
   `process_candidate()` call — download, OCR check, `ensure_searchable()`,
   Drive upload, verify — regardless of which internal step is responsible.
   A timed-out candidate is recorded `error_timeout` (terminal, not
   auto-retried — the same posture as `error_malformed`; it also counts
   against `--limit`, same as any other real attempt) and the run
   continues to the next candidate. The state entry's `note` (never
   printed to the log) records which phase (`download` / `check_before` /
   `ocr` / `check_after` / `upload` / `verify`) was in flight when the
   alarm fired, so a human reviewing the state file afterward has a
   starting point — in particular, a timeout recorded during `verify`
   means the Drive write in `upload` likely already succeeded even though
   this run couldn't confirm it.
   - **Residual risk, explicit:** `signal.alarm` interrupts a blocking
     syscall (a subprocess wait, a socket read) but not a pure CPU-bound
     loop inside a C extension (e.g. a pathological PyMuPDF
     `image_only_pages()` call on a malicious or corrupt PDF structure) —
     this is a strong mitigation, not an absolute guarantee, against every
     conceivable hang.

**Verification.** `tests/test_ocr_util.py` gained a real, non-mocked,
end-to-end test (`test_ensure_searchable_real_subprocess_kills_grandchild_
on_timeout`) that forks an actual grandchild process holding the output
pipes open and confirms it is genuinely killed (never completes its sleep),
not merely orphaned-and-left-running — confirmed directly (not just
asserted) to fail against `main`'s `ocr_util.py` before this fix: swapping
`main`'s version in and re-running just this one test reproduces the
orphan surviving, with `ocr_util`'s own "ocrmypdf timed out" warning
confirming `subprocess.run()`'s timeout still fired correctly even then —
i.e. the test fails for the REASON this addendum claims, not some other
reason. `tests/test_drive_client.py` and `tests/test_archive_client.py`
(both new) assert the service builders pass `http=` with the expected
finite timeout, never a bare `credentials=`. `tests/test_backfill_mirror_
ocr.py` gained watchdog and progress-line tests, including one that a hung
candidate (simulated via `time.sleep()` under a shortened test ceiling) is
bounded, recorded `error_timeout`, and does not stop the run from reaching
the next candidate.

**Step 5 independent review found one HIGH finding, fixed before merge.**
`drive_client.authorized_http()`'s first draft built a bare
`httplib2.Http(timeout=timeout)` directly. `googleapiclient.http.
build_http()` — what `build(credentials=...)` was already using
internally, before this PR — does one thing on top of setting that same
timeout: `http.redirect_codes = http.redirect_codes - {308}`. 308 is
Drive/YouTube's "Resume Incomplete" status for resumable uploads, not a
real redirect; a bare `httplib2.Http()` keeps 308 in its default
`redirect_codes` set, so httplib2 would try to follow a mid-upload 308 as
an actual redirect (no `Location` header present) and the upload would
fail. This would have broken every resumable upload in this codebase —
`archive_client.upload_file()`, `archive_client.update_file_content()`
(this backfill script's own re-upload step), and `drive_client.py`'s own
upload helper, all `MediaFileUpload(..., resumable=True)` — on any file
large enough to span multiple chunks. Confirmed directly against this
repo's pinned libraries, not just upstream source: `build_http().
redirect_codes` excludes 308, a bare `httplib2.Http()` includes it, and
the original `authorized_http()` did too, before the fix. **Fixed:**
`authorized_http()` now builds on `build_http()` itself (so
`redirect_codes` is correct) and only overrides `.timeout`, rather than
constructing a bare `httplib2.Http()`. `tests/test_drive_client.py`
gained a dedicated test asserting `308 not in http.http.redirect_codes`.
This is exactly the kind of gap the earlier "corrected framing" paragraph
above should have caught — the direct inspection that found the 60s
timeout default stopped at the timeout and never checked what else
`build_http()` does.

## Open finding from Step 6 security review — NOT fixed in this PR

The independent Step 6 security review of this PR (`/security-review`
could not run cross-repo from the build session; a dedicated review
subagent given the diff + full post-change file content was used instead
— see PR #143's comments for its full report) found **one MEDIUM
finding**, which this repo's standing procedure requires treating as a
hard stop: escalate, do not fix it autonomously, do not merge past it.

**The finding:** `ocr_util._run_ocrmypdf()`'s `TimeoutExpired` branch, after
`os.killpg()`-ing the process group, calls `proc.communicate()` a SECOND
time with no timeout, intending to "drain cleanly" before re-raising. This
is backwards and reintroduces a worse version of the exact bug this PR set
out to fix: `communicate()` on a PIPE blocks until **every** process
holding that pipe's write end closes it, and `killpg()` only reaches
processes still in ocrmypdf's own process group — a descendant that calls
its own `setsid()`/`setpgid()` (escaping the group) would still hold the
pipe open, and the second `communicate()` call would then hang forever.
`main`'s original `subprocess.run()` avoided this specific trap by calling
`process.wait()` after a timeout kill, never `communicate()` again — this
PR's fix, in trying to go further than `main`, introduced a regression
past it. This path is reachable on a LIVE, unattended path with no
watchdog backstop: `capture=True` (the affected branch) is used only by
`ocr_in_place()`, which `egle_doc_parser.parse_document()` calls directly
on every real, externally-submitted filing `watcher.py`/`backfill.py`
processes every run — and neither of those jobs has anything like
`scripts/backfill_mirror_ocr.py`'s new per-candidate watchdog wrapping
that call.

**Not fixed here, on purpose** — per this repo's standing rule, security
findings are Trisha's call, regardless of how confident a fix looks. A
sketch exists (not applied): make `ensure_searchable()`'s `capture=False`
pattern — stdout to `DEVNULL`, nothing captured — the norm for the
timeout/kill path too, and for `ocr_in_place()`'s `capture=True` case,
write stderr to a real temp file (never a pipe) and read it back with a
plain `open()` after the process is confirmed dead; a file read never
blocks waiting for some other process to close it, unlike a pipe, so
there is nothing left that killpg's reach can fail to fully clean up.
`os.killpg(proc.pid, SIGKILL)` directly (no `os.getpgid()` lookup) was
also suggested, since under `start_new_session=True` the process group id
equals the leader's own pid by construction — looking it up fresh via
`getpgid()` is both redundant and a real TOCTOU risk if the leader has
already been reaped and its pid reused by an unrelated process. This is
recorded here for whoever picks this up next; it has not been
independently re-reviewed since it was only ever a sketch.
