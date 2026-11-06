# ADR 073 — `Mirror Index`: one metadata row per mirrored PDF

Date: 2026-11-05
Status: accepted 2026-11-05 (Trisha approved; `mirror_index.enabled: true`)
Builds on: ADR 007 (OAuth durable Drive archive), ADR 010/037 (MMPC and
CivicClerk meeting archives), ADR 069 (OCR on upload), CODER-PLAN step 2
(`ocr_util.OcrStats`). Source analysis: Lotext
`documents/arbor-hills/analysis/ocr-download-redundancy-2026-11-04/REPORT.md`;
plan: `CODER-PLAN.md` step 3 in the same folder.

## Context

Several tools read the public Drive mirror and each re-learns the same facts by
downloading files: the full-text cache (30-day refetch timer), the OCR backfill
(downloads every mirrored PDF to find image-only pages), and the Lotext
wellfield staging job (downloads and page-checks each candidate). Drive already
knows each file's `sha256Checksum`, `md5Checksum`, `size` and `headRevisionId`,
and since step 2 the upload already knows the page count, which pages are
image-only, and whether OCR ran. Nothing recorded either, so every tool paid
for them again.

## Decision

### 1. A new tab on the PUBLIC case-file Sheet: `Mirror Index`

One row per Drive file, upserted by `drive_id`. Columns:

| Column | Meaning |
|---|---|
| `drive_id` | Drive file id (the key) |
| `source_kind` | `nsite`, `mmpc` or `civicclerk` |
| `source_key` | nSITE doc id, or the CivicClerk file id |
| `mirror_sha256`, `mirror_md5`, `size`, `head_revision` | From Drive's own create/update response, verbatim. Blank if Drive returned none. |
| `original_sha256` | sha256 of the bytes the SOURCE served. Filled only at upload, only for MMPC/CivicClerk, where the uploaded input is exactly the download. Blank for nSITE: that file may already have been OCR'd in place by `parse_document()` or synthesized from `.msg`/`.docx`, so its hash would only look like the source's. Never overwritten: a later write with it blank (a content replacement) keeps the existing value, and `update_file_content()` never hashes its input as the source's. |
| `pages`, `image_only_pages` | From `ocr_util.OcrStats` for the UPLOADED bytes (`image_only_pages` is a count) |
| `text_status` | See table below |
| `ocr_engine` | `none`, or the file's `/Creator` (first 120 chars) when it starts with `OCRmyPDF`. Intentionally public: the same text is in the public PDF. (It stays out of public Actions logs.) |
| `raw_revision_pinned` | Revision id the OCR backfill pinned before overwriting (blank at upload) |
| `indexed_at` | UTC ISO timestamp of the write |

`text_status`, from the uploaded file's stats:

| Value | Rule |
|---|---|
| `native` | no image-only page, engine `none`. Means only "no image-only page and no OCRmyPDF stamp": a scan OCR'd elsewhere (Acrobat, a scanner, ABBYY) also reads `native` |
| `ocr_full` | no image-only page, engine `OCRmyPDF …` |
| `ocr_partial` | some, not all, pages image-only |
| `no_text` | every page image-only |
| `unknown` | no stats (unreadable), 0 pages, or unknown page set |

Only `native` and `ocr_full` mean "already searchable". Both require zero
image-only pages; the engine only records how the text got there.

File metadata only. Nothing here is private: every file is already linked
from this same public Sheet (`Archived PDFs`, `MMPC Archived Files`,
`CivicClerk Archived Files`).

### 2. Who writes it

`archive_client.upload_file()` / `upload_pdf()` / `update_file_content()` take
an optional `index` (`mirror_index.IndexTarget`). Only when one is passed do
they ask Drive for the checksum fields and write a row, AFTER the upload
succeeded. Callers build a target with `mirror_index.target(cfg, ...)`, which
returns `None` unless `mirror_index.enabled` is true.

Indexed today: nSITE PDFs (`archiver._mirror_and_record`, both the inline
`mirror_one_now()` path, which reads the flag from the run's own config handed
in by `doc_pipeline`, and the nightly `run()` catch-up), MMPC meeting PDFs,
CivicClerk (DPW/BOC) meeting PDFs.

Never indexed, by construction (no target passed; a test pins it): GovQA and
RIDE documents (private Sheet / private folders), GFL air captures, snapshot
exports and WDS pages (not PDFs from a document source), and Ridge Wood
reports. Ridge Wood is left out for now: it has its own tamper-evident manifest
(ADR 066) and its own folder outside the Case File Mirror; adding it later is
a one-line `target(...)` at its two `upload_pdf()` calls. Hand-Curated files
are uploaded outside this repo; the step-4 backfill covers them. The private
Sheet twin for GovQA/RRD is a later decision.

The reuse-existing branch of `upload_file()` (a file of that name is already
in the folder) writes no row: no new bytes moved. The step-4 backfill indexes
existing files.

### 3. Flag off means unchanged

With `index=None`, `upload_file()` sends the same `fields="id, webViewLink"`,
hashes nothing, and makes no Sheets call. Tests pin both.

### 4. Write order and failures

Drive upload first, index row second. `mirror_index.record()` never raises: a
failed index write logs a class name only (SEC-001: no file names, ids, or
exception text in public Actions logs) and the upload still returns its link.
A missing row only ever means "process this file the old way" later.

The upsert reads column A first. If that read fails it RAISES (inside
`record()`, so the write is skipped). It never appends without knowing what is
there: that would create duplicate rows.

### 5. Reads are strict; the fail-safe rule

`mirror_index.read_index()` uses `sheet_writer._tab_rows_strict()`: a
transport failure raises; only a genuinely absent tab reads as empty. This is
a state tab that later steps SKIP WORK from.

Every reader must follow one rule: **skip work only when Drive's LIVE
`sha256Checksum` equals the row's `mirror_sha256`.** A missing row, a blank
sha, a mismatch, or a read error means process the file the old way.

Two workflows can upsert the same `drive_id` at once and leave two rows; the
reader takes the LAST row per `drive_id` as current
(`mirror_index.latest_by_drive_id()`).

### 6. Tab creation (deviation from the plan)

The plan said "an `ensure_tabs` entry". It is deliberately NOT in
`sheet_writer._TAB_HEADERS`: `ensure_tabs()` runs on every watcher/backfill
run, so an entry there would create an empty public tab before the flag is on.
Instead the tab is created on the first enabled write
(`sheet_writer.ensure_tab()`), the same rule every watch/archive tab follows.
This also keeps `sheet_writer.py` out of this change.

## Consequences

- Flipping the flag (Gate B) makes the next uploads write rows; existing files
  get rows from the step-4 backfill (`--dry-run` by default, `--apply` is
  Gate C).
- Per indexed upload: one Sheets read (`A:N`) and one write, plus a one-time
  tab check per process (`ensure_tab`: a metadata get and a header write).
  Uploads are a few a day, plus the nightly archive batch (up to 100).
- Changes `archive_client.py`, a module pinned by the Lotext staging job; that
  job needs a re-pin after merge (Gate A).

## Addendum (2026-11-05, CODER-PLAN step 4): backfilling existing files

`scripts/backfill_mirror_index.py` fills rows for PDFs mirrored before the tab
existed. Dry run by default; `--apply` waits for Trisha (Gate C).

- **What it indexes.** Only the PDFs linked from the four public tabs that
  index Mirror files: `Archived PDFs` (`nsite`), `MMPC Archived Files`
  (`mmpc`), `CivicClerk Archived Files` (`civicclerk`) and `Hand-Curated
  Files` (new kind `hand_curated`, `source_key` = `curated_filename`). One row
  per Drive id: when two tabs link the same file (some Hand-Curated rows point
  at an nSITE Mirror copy), the first of those kinds in that order owns the
  row. Non-PDF files and Mirror PDFs no tab links (WDS records, Ridge Wood,
  loose folders) are counted and left out.
- **Where the values come from.** Checksums, size and head revision from
  Drive's own metadata. Page stats from downloading the file and running the
  upload path's own check (`ocr_util.page_stats()`); the download's sha256
  must equal Drive's. If not, Drive is asked again: if it now reports the
  downloaded sha, its fresh metadata is used; otherwise the file changed
  mid-run and gets no row.
  `original_sha256` stays blank (the backfill can't know the source's bytes).
- **Apple Vision attribution.** The Lotext Mac Vision pass replaced some
  Mirror files in place and kept the input's `/Creator`, so those files would
  read as `native`. When the Vision state says it wrote OCR output over that
  Drive file (its recorded `item_id` equals the Drive id), Drive's
  `modifiedTime` shows no change since (within 60 s of Vision's own
  timestamp; measured gap ≤ 1 s), and the fresh check
  matches what it recorded (same page count, same image-only count),
  `ocr_engine` is `Apple Vision` (or `<OCRmyPDF creator> + Apple
  Vision`), so `text_status` is `ocr_full`/`ocr_partial`, not `native`. If
  Vision wrote over that Drive id but either check fails, the row's
  `text_status` is `unknown` and `ocr_engine` blank (never `native`/`none`);
  the run counts both cases. The 10 `.msg` files the Vision tooling rebuilt
  as PDFs (`converted_msg`, no timestamp or page outcome recorded) are always
  `unknown` with a blank engine.
  `head_revision` is the listing's, which can be hours old by write time;
  nothing trusts it (the OCR backfill pins the live revision itself).
- **No download shortcut from the Vision state.** That state has page counts
  for most files but no sha256, so it can't prove its numbers describe the
  current bytes, and its `skip_has_text` entries can't tell `native` from
  `ocr_full`. The dry run reports what reusing it would save and, with
  `--validate-sample N`, how often it agrees with a fresh check; using it would
  need a change to the `text_status` values above, so it is Trisha's call at
  Gate C, not built.
- **Writes.** Ids whose existing row already matches Drive's live sha are
  skipped. Rows are appended in batches; before each batch the index is read
  again (strict) and ids an upload wrote meanwhile are dropped, so a rerun
  resumes. A row the plan saw with a different sha is replaced in place. A
  file Drive gives no sha256 gets no row. Two narrow races remain, both in
  the moment between a batch's re-read and its write: an upload appending
  there leaves two rows and the reader takes the backfill's (last), and an
  upload rewriting a row the backfill is replacing gets overwritten. Either
  way the backfill's row has an older sha than Drive's, so readers process
  the file the old way (fail-safe) and a rerun reindexes it.
- **Vision state is required for `--apply`** (`--vision-state PATH`, or an
  explicit `--no-vision-state`). Without it, Vision-OCR'd files are indexed
  with engine `none`; their sha matches Drive's, so no rerun would correct
  them. The dry run prints how many Vision entries were loaded and the
  latest date.

## Addendum (2026-11-06, CODER-PLAN step 5): the first readers

Two scripts now read what this ADR records, both under the fail-safe rule
(skip work only when Drive's live `sha256Checksum` equals a recorded sha).
`mirror_index.live_sha256()` asks Drive for live shas in batches of 100
`files.get` metadata calls (no download); a failed call or batch leaves
those ids absent, which every caller treats as "process the old way".

- **`scripts/backfill_mirror_ocr.py` (ADR 069).** With `mirror_index.enabled`
  on, `main()` reads the index STRICTLY (a transport failure stops the run).
  Candidates are still the three archive tabs' rows with the same
  `{tab}:{id}` state keys. Rows whose index `text_status` is `ocr_partial` or
  `no_text` go first. A row the index calls `native` or `ocr_full` is skipped
  without a download only when its live sha (one lookup through the OAuth
  client for all such rows) equals `mirror_sha256`. Such a skip writes no
  state entry, so a later content change is checked again, and it does not
  count against the per-run scan cap. No row, `unknown`, a `hand_curated`
  row, a blank or different sha, or a failed lookup gets the old download
  check. After an in-place replacement (`done`, `done_partial`,
  `done_unverified`), the row is rewritten AFTER the per-file watchdog is
  cleared and the state entry is saved (so neither a slow Sheets write nor a
  kill mid-write can lose the record of a landed replacement): Drive's new checksums (one `files.get`), the
  replacement's stats built from the page check already done (no second
  scan; failure, or Drive now holding bytes with a different sha, = no stats
  = `unknown`), and the revision
  `pin_head_revision()` kept (`raw_revision_pinned`).
  A `hand_curated` row is never rewritten. The dry run still makes no Drive
  call and prints the index's counts as "not checked against Drive".
  Real data, 2026-11-06 (service account, read-only): 3,971 archive-tab rows;
  the index calls 3,851 searchable, 106 `ocr_partial`/`no_text`, 14
  `unknown`; all 3,851 live shas matched (22 s of batched lookups), so an
  `--apply` run would download-check 120 files instead of 3,971.
- **`scripts/build_fulltext_cache.py` (ADR 062 addendum).** Its workflow has
  no Sheet credentials, so it does not read this tab; the live Drive check the
  rule requires is enough on its own. Each fetch records the sha256 of the
  downloaded bytes in the cache manifest. For a cached Mirror copy with a
  recorded sha, the archiver's OAuth client asks Drive for the live sha:
  different = fetch again (new content: the textless-page refresh count
  restarts; every failed refetch, transient or not, and every fetch whose
  bytes still differ from the live sha counts a `change_errors` strike
  against that live sha; after 3 it goes back to the old rule until Drive's
  sha changes again),
  equal = never refetched (the 30-day timer no
  longer applies). No recorded sha (everything cached before 2026-11-06), an
  nSITE link, or no live sha (lookup failed, no OAuth) keeps the old timer
  rule. Verified on real Mirror files: the sha of the public-link download
  equals Drive's `sha256Checksum`, and after editing one recorded sha only that
  document was fetched again.

## Addendum (2026-11-06, CODER-PLAN step 6): fewer nSITE re-downloads

- `archiver.run()` lists nSITE only when a processed document still needs
  mirroring; most nights nothing does (the inline mirror got it), and the
  full listing was fetched for nothing. It does not build URLs from the
  Sheet: `_state` holds neither the doc URL nor the facility SRN, and a
  guessed SRN would name a second Drive copy of the same document.
- `scripts/recheck_key_data_points.py --run-llm` fetches a feed row's Drive
  Mirror copy from its PUBLIC link with no credentials, instead of refusing
  it; it used to accept only MiEnviro links, so all 156 Drive-linked feed
  rows failed. Only file ids listed in Archived PDFs are fetched, and with
  no credentials a non-public file can't be read at all, so a planted link
  can't expose anything private. Its proposals are reviewed by a person
  before `--apply`; the proposals artifact records error classes only (its
  `link` field holds the same public Mirror links the public records page
  shows).
- `archive.yml` no longer fails on a night when nSITE returns an empty
  listing but nothing needed mirroring; the watcher and backfill still list
  nSITE every run.
- Backfill's `FORCE_REPROCESS` keeps downloading from nSITE (built, then
  removed after review): it exists to re-extract after the parser or the
  `.msg`/`.docx`/zip converters change, and the Mirror holds the PDF those
  converters produced at first mirroring, so reading it would skip exactly
  the code a forced re-run is meant to exercise, and a bad Mirror copy would
  strike a good document instead of falling back.
- Not built: recording "the Mirror's OCR is worse than a fresh OCR" in this
  index from these paths. The index already holds the page stats of the
  exact Mirror bytes (Gate C backfill, live uploads, the OCR backfill), so a
  re-check there adds nothing a reader can use; the OCR backfill's
  `ocr_partial`/`no_text` queue is the place those files get fixed.
- Real data, 2026-11-06: no document was waiting to be mirrored, so that
  night's archive run skips the listing; a live Drive-linked feed row now
  downloads for the recheck script.
