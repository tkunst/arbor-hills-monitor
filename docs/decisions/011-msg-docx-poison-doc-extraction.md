# ADR 011 — .msg / .docx poison-doc extraction (WRD-Groundwater)

*Status: built — 2026-07-11, pending review. Not gated behind a feature flag
(this is a bug fix to always-on infrastructure, not a new monitored source);
the retroactive retry of the 15 already-known docs IS a manual, one-time
opt-in step — see Activation.*

## Context

`nsite_client.download_pdf()` tries a document's own link, then nSITE's
`downloadpdf/<id>` render endpoint. If neither yields a real PDF, the doc
accrues an error strike; after `MAX_ERRORS_PER_DOC` (3) it's permanently
marked `skipped` and stubbed with just a native-download link — never
classified into the case-file Sheet. This is the "poison doc" path
(`backfill.py` / `watcher.py`).

The render endpoint successfully rasterizes images and nForm submissions into
a PDF, but 400s on Outlook `.msg` and Word `.docx` sources. The **Arbor Hills
Remediation Area** facility (nSITE `-714792003991405124`, WRD-NPDES/
WRD-Groundwater programs) is already monitored in `config.yml`, but every one
of its `.msg`/`.docx` filings has been parking as poison — including the 15
documents Trisha hand-pulled on 2026-07-07 establishing the
un-permitted-discharge-to-groundwater finding at the compost leachate pond
and the PFAS-leachate-treatment-to-Johnson-Drain planning thread (see
`arbor-hills-multiple-data-sources.md` row 1 and the hand-pull's own
`source-docs/gw-recon-handpull-2026-07-07/README.md`, both in the Cowork
workspace, not this repo).

That hand-pull also established the working recipe this ADR automates:
`ncore/downloadfile/<id>` serves the original bytes (vs. `downloadpdf`'s
render-or-400), gzip-compressed regardless of the `Accept-Encoding` request
header — `curl --compressed` decodes it transparently; a raw `requests` GET
does not, since the endpoint doesn't set `Content-Encoding` correctly.

## Decision

Add **`poison_doc_extractor.py`**: given raw bytes, sniff the format (OLE2
magic → `.msg`, ZIP magic + `word/document.xml` → `.docx`) and synthesize a
PDF containing everything extractable, so `egle_doc_parser.parse_document()`'s
existing classify/OCR/extract/Claude pipeline ingests it completely
unchanged downstream. Wired into `nsite_client.download_pdf()` as a last
resort: after the primary link and the render endpoint both fail, the last
non-PDF body fetched (gunzip-decoded first — `_maybe_gunzip()`) gets one shot
at `poison_doc_extractor.synthesize_pdf()` before the doc poisons.

**`.docx`**: stdlib `zipfile` + `xml.etree` reads `word/document.xml` directly
— no new dependency.

**`.msg`**: `extract_msg.openMsg()` (new dependency) gives envelope
(from/to/date/subject/body) plus attachments, which are recursed into rather
than ignored — per Trisha's explicit choice (2026-07-11), since the hand-pull
found the actual substantive evidence (e.g. the Pace lab report's analyte
table) often lives in attachments, not the email body:

- PDF attachments have their real pages merged in (`fitz.insert_pdf`), not
  re-extracted as text.
- `.xls`/`.xlsx` attachments (the lab-report EDD) become a text table
  (`xlrd` / `openpyxl`, both new dependencies).
- Image attachments are placed as a raster page.
- Anything else (Outlook's tiny inline-image-content-ID `.txt` sidecars) is
  decoded best-effort and included only if it carries ≥20 chars of content.
- One bad attachment is caught and skipped — never sinks the whole document.

Legacy binary `.doc` shares `.msg`'s OLE2 magic bytes but isn't a real `.msg`
— `extract_msg.openMsg()` raises on it (`StandardViolationError`, verified
against a real non-.msg OLE2 specimen during design), which this module
surfaces as `ExtractionError`. The caller treats that exactly like any other
download failure: a poison strike, not a crash. `.doc` stays unsupported —
same outcome as before this module existed.

### Two bugs found and fixed during design (both against real specimens)

1. **`fitz.Page.insert_textbox()` silently renders nothing once its internal
   capacity is exceeded**, for certain real text shapes, with no reliable
   signal in its return value. Found against the real 2018 Inspection Report
   `.docx` (item 9 of the hand-pull): a naive 3000-char-per-page chunking
   scheme produced a `classify()` verdict of `empty` despite 4768 real
   characters of extracted text. Fixed by abandoning `insert_textbox`
   entirely — `_add_text_page()` does its own line-wrap (`textwrap.wrap`) and
   places each line with `page.insert_text()`, giving full, predictable
   control over pagination instead of trusting PyMuPDF's box-fitting layout
   engine.

2. **A mixed text+image synthesized PDF can silently skip OCR entirely.**
   `parse_document()` only calls `ocr_in_place()` when `classify()`'s
   *whole-document* verdict is `needs_ocr`/`likely`. A `.msg` with a short
   text envelope plus several photo attachments (item 10 of the hand-pull:
   "Arbor Hills Compost.msg", 1 text page + 4 inspection photos) reads as
   `has_text` overall — the text page alone clears the threshold — so
   `parse_document()` would never OCR the photo pages, silently losing
   whatever text they might contain. Fixed by having `synthesize_pdf()` OCR
   proactively, itself, whenever it inserted any raster image page — before
   the file is ever handed to `parse_document()`. By the time `classify()`
   sees it, every page already has a real text layer, so the verdict is both
   correct *and* truthful, and `parse_document()` doesn't redundantly re-OCR.
   (Verified against the real item 10: OCR correctly finds zero text on the
   4 photos — they're genuine site photos with no legible text in them, not
   a pipeline bug.)

### Four more bugs found in code review (2026-07-11, before merge)

An independent review of this PR (not by the same session that wrote it)
found four more real gaps (numbered 3-6, continuing from the two above),
all fixed before merge:

1. **(#3) `.docx` attachments on a `.msg` were silently garbled or dropped.**
   `_add_attachment()` special-cased `.pdf`, images, and `.xls`/`.xlsx` only;
   a `.docx` attachment (a plausible EGLE attachment type) fell through to
   the generic best-effort UTF-8 decode, which — reproduced directly — turns
   real ZIP+XML bytes into ~300+ chars of binary noise that clears the
   20-char "is this real content" threshold and gets inserted into the
   synthesized PDF as if it were genuine text. Fixed by routing `.docx`
   attachments through the same `_docx_body_text()` extraction a top-level
   `.docx` document already uses.
2. **(#4) A merged PDF attachment that's itself a scan never triggered the
   proactive-OCR fix.** The PDF-merge branch always returned `needs_ocr =
   False`, even when the merged pages carry no text layer — the same
   mixed-document blind spot as bug #2 above, just not extended to merged
   PDF pages. Fixed with `_pdf_has_image_only_pages()`, the same
   imageonly-page heuristic `classify()` uses, checked before the merge.
3. **(#5) A doc retried successfully via `RETRY_DOC_IDS` stayed in both
   `state["processed"]` and `state["skipped"]` forever** (`sheet_writer.
   read_state()`'s `"processed"` branch never cleared a stale `"skipped"`
   entry for the same doc_id) — inert today (every real consumer checks
   `processed` first or safely ORs the two), but a latent trap for future
   code that treats `skipped` as "never processed." Fixed by clearing
   `state["skipped"]` when a `"processed"` event lands for the same doc_id.
4. **(#6) `download_pdf()`'s extraction fallback depended on an unverified
   assumption about `doc_url`.** `_normalize()` defaults `doc_url` to the
   *render* endpoint (not the native one) when a record's `docMgmtDocurl` is
   empty — in that case the old two-URL list (`primary`, `render`) deduped
   to one entry, `downloadpdf` 400s, and `synthesize_pdf()` never even got a
   non-PDF body to try. Fixed by adding `native_download_url(doc_id)` as an
   explicit third fallback URL (deduped against the other two).

Also fixed as part of the same pass: an image-attachment `insert_image()`
failure left an orphaned blank page in the synthesized PDF (now cleaned up);
per-attachment extraction failures were logged nowhere (now printed,
matching the OCR-failure branch's existing logging).

### Retroactive backfill (`RETRY_DOC_IDS`)

`state["skipped"]` is terminal — no existing code path un-skips a doc, and
`retry_poisoned` only re-attempts docs still accumulating `errors`, not ones
already stubbed. Per Trisha's explicit choice (2026-07-11) to also backfill
the 15 already-known docs rather than cover only future filings, added a
narrower mechanism: `RETRY_DOC_IDS` (comma-separated doc_ids, a
`backfill.yml` `workflow_dispatch` input) makes `select_todo()` re-attempt
**exactly** those docs regardless of skipped/poisoned status — composes with,
but is independent of, `retry_poisoned`. A doc already `processed` is never
re-attempted even if named.

## Consequences / residual risks (accepted)

1. **`extract-msg` is GPL-licensed** (`xlrd`/`openpyxl` are BSD/MIT). This
   repo has no `LICENSE` file of its own today, so there's no direct
   copyleft conflict right now — but the README explicitly invites reuse by
   other Great Lakes advocacy groups, so this is worth a conscious decision
   (add a LICENSE file addressing it, or swap the library later) rather than
   silently baked in. Flagged for Trisha's review, not resolved here.
2. **A successfully-retried doc leaves its old stub row in place.** When a
   previously-skipped doc is reprocessed via `RETRY_DOC_IDS`, `write_document()`
   appends a real, fully-classified row — but the original `write_stub_row()`
   row from the terminal skip is never deleted (Sheets row-deletion by
   content match is more machinery than a 15-row one-time cleanup warrants).
   Net effect: the doc briefly appears twice in the feed tab (one
   `(unprocessable source)` stub, one real classified row) until someone
   deletes the stub by hand — easy to spot, low severity, accepted.
3. **OCR failure degrades gracefully, not silently perfectly.** If
   `ocr_in_place()` raises (e.g. `ocrmypdf` missing on a given runner), the
   doc still gets archived with its text pages intact; the image pages just
   stay un-OCR'd that run. Logged, not raised — matches the "one bad
   attachment doesn't sink the doc" philosophy throughout this module.
4. **Legacy binary `.doc` is still a genuine gap**, unchanged from before
   this ADR — no reliable stdlib extraction path exists, and adding a
   dedicated `.doc` library wasn't in scope here (none of the 15 known docs
   are `.doc`).

## Alternatives considered

- **Envelope-only `.msg` extraction (no attachment recursion)** — smaller,
  faster to ship, but would miss the Pace lab report's analyte table
  entirely (it's an `.xls` attachment, not email body text) — exactly the
  kind of evidence this fix exists to surface. Not chosen (Trisha's explicit
  call).
- **Future filings only, no retroactive backfill** — lower risk (no
  production Sheet state mutation), but leaves the 15 already-known,
  evidentially significant docs unclassified in the live case file
  indefinitely. Not chosen (Trisha's explicit call).
- **One synthesized PDF per `.msg` attachment (separate nSITE-doc-like
  records)** — would give each attachment its own full classification pass,
  but the pipeline's data model is one `ParsedDoc` per doc_id; multiplying
  that out is a much larger architectural change than this fix warrants.
  Rejected as disproportionate to the problem.
- **Rely on `parse_document()`'s own OCR gate instead of proactive OCR in
  `synthesize_pdf()`** — the original design, until the mixed-document bug
  (see above) was found against a real specimen. Rejected once proven unsafe.

## Activation

No flag to flip — the extraction fallback is live as soon as this merges
(same "additive, only touches the poison-doc path that previously always
failed" reasoning as the render-endpoint fallback it extends).

The retroactive backfill of the 15 known docs is a manual step:

1. Confirm the doc_ids are still in the Sheet's `_state` tab as `skipped`
   (the hand-pull's filenames embed them — `NN-<doc_id>-name.ext` — but
   confirm against the live tab before running, don't trust this list
   blindly): `-7798041058739630016`, `-6369367700827176812`,
   `-4614153356626982099`, `-3979254939700745184`, `2606928633577458`,
   `73475713044769599`, `168079647999619622`, `622014093284838278`,
   `1681010528757159679`, `3601350606005200833`, `3791153473645098573`,
   `4538629476359190066`, `7076407470123836367`, `8609269736886387068`.
   (14 — item 7 of the hand-pull, the standalone `.JPG`, already downloads
   fine via the existing render endpoint and isn't part of this gap.)
2. Run `backfill.yml` via `workflow_dispatch` with `retry_doc_ids` set to
   that comma-separated list.
3. Spot-check the resulting Sheet rows, then manually delete the 14 old stub
   rows from the feed tab (residual risk #2 above).
