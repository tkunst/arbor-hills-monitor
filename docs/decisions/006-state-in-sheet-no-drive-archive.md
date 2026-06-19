# ADR 006 — State in the Sheet; link to nSITE instead of archiving PDFs to Drive

*Status: accepted — 2026-06-14. Supersedes the storage half of [ADR 001](001-data-store.md).*

## Context

Deploy day surfaced a hard limit that ADR 001 / ADR 002 did not anticipate.

A **Google service account on a personal `@gmail.com` Drive has no storage
quota of its own.** Empirically, on Trisha's real Drive the service account can:

- **edit** a Sheet she owns and shared with it as Editor (verified — 5 tabs
  created, rows written), but
- **cannot create files** in her My Drive: `files.create` returns
  `403 "Service Accounts do not have storage quota. Leverage shared drives
  ... or use OAuth delegation instead."` (verified).

Shared Drives would lift the limit but require Google Workspace; a personal
Gmail is not Workspace. OAuth-as-Trisha would also work but an **unpublished**
personal OAuth app issues refresh tokens that **expire every 7 days** — a
non-starter for an unattended nightly GitHub Action.

ADR 001 assumed the runner would (a) upload each OCR'd PDF to a Drive folder and
link the Sheet row to that Drive copy, and (b) read/write a JSON state file in
that same folder. Both require `files.create`. Both break.

Two facts make a clean pivot possible:

1. **nSITE download URLs resolve unauthenticated.** A cold `curl` with no session
   cookie against `https://mienviro.michigan.gov/ncore/downloadpdf/<id>` returns
   `HTTP 200, application/pdf` (verified). So a Sheet row can link straight to the
   canonical EGLE source, and any Conservancy member clicking it gets the PDF.
2. **The service account can write Sheet cells.** State that lived in a Drive JSON
   file can live in tabs of the Sheet it already writes.

## Decision

1. **No Drive PDF archive.** The Sheet's `Link` column points to the canonical
   nSITE URL (`doc["doc_url"]`), not a Drive copy. The runner downloads each PDF
   to `/tmp` only long enough to OCR + classify it, then deletes it.
2. **Processing state moves into the Sheet**, in two internal tabs (prefixed `_`,
   ignored by the Conservancy):
   - **`_state`** — an **append-only event log**, one row per processing attempt:
     `Doc ID | Status (processed|error) | Error Count | Processed At |
     Payload JSON`. `read_state` reduces it (latest `processed` row ⇒ done; count
     `error` rows ⇒ retry count). Append-only means **no read-modify-write race**
     and the 754-doc backfill never rewrites a ~150k-char blob.
   - **`_meta`** — the three small global singletons as one JSON cell each:
     `pending_digest`, `mmpc_minutes_found`, `last_run`. These are tiny by
     construction (the digest clears every Sunday), so the 50k-char per-cell cap
     is never in play — which is exactly why the 754-entry processed map is rows
     in `_state` and these three are cells in `_meta`, not one JSON cell for all.
3. **`GDRIVE_FOLDER_ID` becomes vestigial.** Nothing on the deploy path reads it.
   The Drive helpers in `drive_client.py` (`list/find/download/upload_file`) are
   kept but explicitly OFF the deploy path.

## Why not store everything in one `_meta` JSON cell

A Sheets cell caps at ~50,000 characters. The processed map for 754 docs is
~150k+ characters — it would overflow a single cell. Rows in `_state` have no
such ceiling, and append-only writes are cheaper and safer than rewriting the
whole map on every doc.

## Consequences

- **No durable PDF mirror.** If EGLE ever removes or renames a document, the
  Sheet link dies and the evidence is only as durable as nSITE. This is the one
  real loss vs. ADR 001 and was an **accepted residual risk** for v1.
  *(Resolved: the fast-follow OAuth archive job below was specified in
  [ADR 007](007-oauth-durable-pdf-archive.md) and activated in production
  2026-06-15.)*
  - *Detection:* the CI `lychee` link-checker runs over committed docs, and a
    future watcher health-check can periodically HEAD a sample of `_state` doc
    URLs and alert on 404s.
  - *Recovery (fast-follow, not v1):* an **OAuth-as-Trisha** archive job —
    interactive, re-consented as needed, separate from the unattended runner —
    can mirror PDFs into Drive and backfill a second `Archive Link` column
    without reprocessing anything. The append-only `_state` tab already records
    every doc ID, so the archive job can drive straight off it.
- **Crash-safety unchanged in spirit:** Sheet row written first, `processed`
  state event appended last. A crash between them re-processes the doc next run
  (a duplicate row, never a silent drop) — same contract ADR 001 accepted.
- **Single-writer still enforced** by the workflow `concurrency` group, so the
  append-only log has one writer at a time.
- **The "_meta is tiny" invariant has a deploy-time dependency.** `pending_digest`
  is only small if the watcher never processes a large batch at once. So the
  daily schedule stays disabled until backfill completes, and the watcher carries
  a `max_new_docs_per_run` cap that makes it defer to backfill while a backlog
  exists — without these, the first daily run after deploy would try to digest
  hundreds of historical docs and overflow the cell. See README deploy step 9.
- **Secrets shrink:** `GDRIVE_SA_KEY` (auth) and `GSHEET_ID` are the only Google
  inputs the deploy path needs; `GDRIVE_FOLDER_ID` can be left unset.
