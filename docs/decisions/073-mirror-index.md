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
| `native` | no image-only page, engine `none` |
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
