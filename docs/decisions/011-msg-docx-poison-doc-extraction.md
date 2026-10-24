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

### Two more bugs found in a second code-review pass (2026-07-11, before merge)

A follow-up independent review (correctness/quality, not security — a
separate security review found nothing) of the post-fix state found two more
real, verified bugs in `_docx_body_text()` (numbered 7-8, continuing from the
six above), both confirmed by reproduction against the real hand-pull
specimens:

1. **(#7) Adjacent `<w:t>` runs separated only by `<w:tab/>` or `<w:br/>` were
   glued together with no separator.** Collecting just `<w:t>` text and
   joining with `""` meant a tabular reading Word represents as two runs
   either side of a `<w:tab/>` — e.g. "Well AHW272R4"\<tab\>"180F" — came out
   as the single glued token `"AHW272R4180F"`. Confirmed against the real
   `.docx` specimens: up to 34 `<w:tab/>` elements and 30 multi-run
   paragraphs in a single document. Given this codebase's own invariant that
   measurement data is load-bearing for credibility, a silently glued value
   reaching the classifier is a real (if quiet) data-quality risk. Fixed by
   treating `<w:tab/>` as `\t` and `<w:br/>` as `\n` during extraction.
2. **(#8) The same fix's first draft (walking each paragraph's full
   descendant tree) double-extracted text nested in a text box or drawing
   anchored inside a run** — a letterhead/signature block, e.g., got
   captured once as part of the outer paragraph (whose `.iter()` doesn't
   stop at a nested `<w:p>` boundary) and again when the nested `<w:p>` was
   reached independently. Confirmed on a real specimen: `"LIESL EICHLER
   CLARK"` appears twice in raw `<w:t>` elements but came out 4 times in the
   (buggy) extracted text. This duplication bug actually predates this ADR
   — it was present in the original `_docx_body_text()` too, just never
   caught because the original test fixtures never had nested-paragraph
   structure and the manual real-specimen verification checked page counts /
   `classify()` verdicts, not exact text content. Fixed by only descending
   into each paragraph's direct-child `<w:r>` runs (`p.findall`, not
   `p.iter`) and their direct-child content elements — a `<w:drawing>` is a
   sibling of `<w:t>`/`<w:tab/>`/`<w:br/>` within a run, never itself walked
   into.

Both fixes were re-verified end-to-end against all 14 real hand-pulled
`.msg`/`.docx` specimens (still 14/14 succeeding, same `has_text` verdicts)
plus targeted checks confirming the specific glued/duplicated strings found
during review are now correct.

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

## 2026-09-10 addendum — 11 legacy `.doc` (RA) are ENCRYPTED: do NOT `RETRY_DOC_IDS` them

A `/dedupe-curate` audit (2026-09-10) found 14 Arbor Hills **Remediation Area**
(`-714792003991405124`) documents still absent from `Archived PDFs`. **11 of
them are legacy binary `.doc` that are genuinely password/RC4-ENCRYPTED at the
source** — the "human-judgment-call problem" `poison_doc_extractor.py`'s
docstring anticipated, now confirmed concretely:

- `antiword` → "Encrypted documents are not supported" on all 11; LibreOffice →
  "source file could not be loaded"; `textutil` → binary garbage. The OLE2
  `Security` flag is `5`. Trisha downloaded one manually (2026-09-10) and Word
  confirmed it is **password-protected**.
- These are NOT the ADR-011 `.msg`/`.docx` backfill set above (different
  doc_ids), and residual-risk #4 (legacy `.doc` is a genuine gap) applies —
  but here the gap is not "no `.doc` parser," it is **encryption**: no parser,
  and no future `.doc` library, can read them without the password. **`RETRY_DOC_IDS`
  will re-fail on every one.** They stay terminally `skipped`; leave them there.
- **Correct remedy = FOIA EGLE** for unencrypted copies of the 2002 NPDES
  `MI0045713` permit-issuance packet (they are a 2002 "Conversion - Permit"
  set: General Purpose Memo/Letter, Fact Sheet, Application Transmittal +
  Acknowledgment, Pre-public Notice, Decision-maker Memo, Mixing Zone, Basis of
  Decision, Individual Permit Issued Letter, Public Notice).

**Do-not-retry doc_ids (encrypted):** `-8684491649718103300`,
`-6904300104235532049`, `-3591020644751821597`, `-3504600192496034508`,
`-1987809141968712770`, `3101556787685491880`, `3507011584973462812`,
`4952804410694111352`, `6093035641993986024`, `6145408853465357390`,
`8281766965637565431`.

The other 3 of the 14 are images and are resolved: `-7959968098742911921`
("10/5/23 Pic") was already hand-curated (2026-07-23); `9145541881522253416`
("Pond") and `-3310380287454320847` ("Stilling basin") were hand-curated
2026-09-10 — each source file's main image data is corrupt at nSITE, but the
intact embedded 2016×1512 JPEG was recovered and rendered to PDF. Full
disposition: the Cowork workspace's
`documents/arbor-hills/source-docs/RA-unmirrored-photos-2026-09-10/DISPOSITION-14-poison-docs.md`.

*(Optional future hardening, not done here: a `KNOWN_ENCRYPTED_DOC_IDS`
constant that `select_todo()` honors even under `RETRY_DOC_IDS`, so a manual
retry can't waste a run on these. Deferred — documenting them here, at the
one place a human consults before running `RETRY_DOC_IDS`, is the proportionate
fix; the normal/retry-poisoned paths already skip them.)*

## 2026-09-20 addendum — zip-of-forwarded-.msg over-produced a corrupt PDF (doc_id 4008289922215821067)

**Evidenced, not inferred.** nSITE doc_id `4008289922215821067` ("Additional
Arbor Hills WRP033733 correspondence") is natively a **ZIP of 11 forwarded
Outlook `.msg` emails**. The archiver mirrored it as a **1,087-page, 16.7 MB
corrupt PDF** that leaked raw `.msg`/OLE2 stream bytes (`Root Entry`,
`__substg1.0_…`, `__nameid_version…`) rendered as text — confirmed live by
re-running the pre-fix `synthesize_pdf()` against the exact zip (curl'd from
`ncore/downloadfile/<id>`). That corrupt PDF drove a useless digest summary
("extensive file corruption") and a link Trisha then hand-fixed on Drive
(same URL, new file content — see `.claude/COORDINATION.md`'s 2026-09-20
entries for the operational history).

**Root cause:** `sniff_format()` correctly returned `"zipbundle"`, and
`_zipbundle_to_pdf()` correctly routed every entry through
`_add_content_by_name()` — but that dispatch had **no branch for a `.msg`
zip entry** (as opposed to a `.msg` *attachment* on an already-open `.msg`,
which this ADR's original design did handle). Every one of the 11 `.msg`
entries fell through to the generic best-effort-text fallback at the bottom
of `_add_content_by_name()`, which UTF-8-decodes the raw bytes — for an OLE2
binary, that's raw stream/property-set structure, not readable text. Not a
missing-format gap (`sniff_format` was right); a missing-*dispatch* gap one
level down.

**Fix, two parts:**

1. **`.msg` zip entries (and `.msg`-in-`.msg` attachments with real byte
   data) now recurse through `_msg_to_pdf()`** — `_add_content_by_name()`
   gained a branch checking `name.endswith(".msg") or sniff_format(raw) ==
   "msg"` before its PDF/image/docx/xlsx checks, merging the nested
   envelope+attachments in as pages instead of falling to the raw-decode
   fallback. **Content-hash dedup** (a `seen: set[str]` of SHA-256 digests,
   created once per `synthesize_pdf()` call and threaded through every
   nested call) collapses an attachment re-forwarded verbatim across the
   chain to its first occurrence — the real specimen repeats one PDF letter
   3× and several signature logos 4-6× each. **Inline signature/logo images**
   (Outlook's auto-named `imageNNN.png`/`.jpg`/…, verified against every one
   of the real specimen's ~90 image attachments — 100% match, 0 false
   positives against the 2 substantive PDF attachments) are skipped entirely
   rather than rasterized as pages — `_INLINE_SIGNATURE_IMAGE_RE`. A
   deliberately/camera-named image attachment (`photo.jpg`, `IMG_1234.jpg`)
   still gets a page, unaffected.

   Result on the real specimen: **1,087 pages / 16.7 MB → 42 pages / 1.86 MB,
   zero leaked OLE2 markers.** The 42 pages break down as 13 pages of
   deduped substantive attachments (the 6-page Wetland Monitoring Letter +
   the 7-page Encroachment Notification, each merged exactly once now) plus
   29 pages of envelope/body text across the 11 messages — higher than this
   task's original "~13 pages" estimate.

   **⚠️ Open question for Trisha, not resolved here:** an earlier draft of
   this addendum claimed those 29 pages were "real, non-redundant
   correspondence content." That claim doesn't hold up — checked directly
   against the real 11 bodies (line-level overlap vs. everything emitted by
   earlier messages in the same synthesis): the two largest bodies (16,979
   and 19,489 chars — a growing `RE:`/`FW:` chain on the same subject) are
   **82.9% and 82.2% overlapping** with lines already on an earlier page.
   Most of the extra volume beyond "~13" is the standard behavior of a
   forwarded email thread quoting its own history, re-quoted, re-emitted
   each hop. Content-hash dedup (fix above) can't catch this — it's the same
   information restated with different line-wrap/whitespace each time, not
   byte-identical. `synthesize_pdf()` ships as-is here (full per-message body
   text, redundant quoting included) rather than attempting a quote-stripping
   heuristic, which wasn't in this fix's scope and is its own fragile,
   client-format-dependent problem (disclaimer/signature reflow, `>`-prefix
   conventions that vary by mail client, none of which this doc exhibits
   consistently). **Still correctly bounded** (42 ≪ `MAX_SANE_PAGES` = 60,
   zero leaks) — this is a quality/redundancy tradeoff, not a correctness
   bug. Two ways to close the gap if Trisha wants ~13 instead of 42: (a) emit
   only the LATEST message's body per `.msg` entry-name "family" (heuristic,
   fragile — not implemented); (b) a genuine quoted-history stripper
   (`>`-prefix / `From:`-block detection, mail-client-dependent, real
   engineering effort). Neither shipped this pass; flagged for Trisha's call
   in the PR, not decided unilaterally.

2. **Post-synthesis sanity guard (`_reject_if_corrupt`), independent of fix
   #1.** Before `synthesize_pdf()` saves/returns, it now rejects (raises
   `ExtractionError`, the same poison-strike contract as any other
   extraction failure) any synthesis exceeding `MAX_SANE_PAGES = 60`, or
   whose first 5 pages contain any of `_OLE2_LEAK_MARKERS` (`"Root Entry"`,
   `"__substg1.0_"`, `"__nameid_version"`, `"__properties_version"` — checked
   with embedded NUL bytes stripped first, since a raw-byte OLE2 leak often
   decodes as interleaved UTF-16LE, e.g. `_\x00_\x00s\x00u\x00b\x00s\x00t...`,
   which a naive substring check misses). This is a backstop for ANY future
   extraction defect of this shape, not just this one root cause — checked
   on the in-memory `fitz.Document` before it's saved to disk, so a rejected
   synthesis never touches `dest_path` at all.

3. **`nsite_client.download_pdf()` — `NativeFallbackError`.** When
   `synthesize_pdf()` raises `ExtractionError` for a source that sniffed as
   `"msg"`/`"zipbundle"` (including a `_reject_if_corrupt` rejection),
   `download_pdf()` now raises `NativeFallbackError` (a `RuntimeError`
   subclass) instead of the generic one, carrying `native_bytes` /
   `native_name` / `native_mimetype` (`application/zip` for a zipbundle,
   `application/vnd.ms-outlook` for a bare `.msg`) so a caller that wants to
   preserve the document as-is — instead of losing it to a bare failure —
   can. Because it's still a `RuntimeError`, every existing caller
   (`watcher.py`/`backfill.py`'s generic except-and-record-a-poison-strike
   handling around the whole download+parse block) behaves identically to
   before this existed — zero behavior change for the classification
   pipeline's error accounting.

   **Deliberately NOT wired further this pass:** nothing in `archiver.py`,
   `watcher.py`, or `backfill.py` currently catches `NativeFallbackError` to
   actually mirror the native bytes to Drive. Investigated and shelved,
   not overlooked: today, a doc that fails `download_pdf()` is never marked
   `processed`, so it's never a candidate for `archiver.run()`'s nightly
   catch-up (which only mirrors `did in processed`) and `mirror_one_now()`
   is never reached in the same run (watcher.py's/backfill.py's
   download→parse→mirror sequence short-circuits on the download failure).
   Wiring "stays downloadable" all the way to a real Drive upload with the
   right filename/mimetype means changing when/whether an unparseable doc
   gets marked done — a bookkeeping decision that deserves its own review,
   not a rider on this fix. `NativeFallbackError` exists so that follow-up
   is a small, additive change (catch the specific exception, call
   `archive_client.upload_file(..., e.native_name, e.native_mimetype, ...)`)
   rather than a re-plumb.

**Operational note:** doc_id `4008289922215821067` is already `processed`
and already in `archived_doc_links` (pointing at the hand-fixed Drive file,
`12mgaI2ieu47EtpLrWrCUMBlvm0UgmuE7`) — verified read-only against the live
Sheet — so no automated path (`watcher.py` reprocessing, or `archiver.run()`'s
catch-up) will re-touch it regardless of this fix. Only an explicit manual
`RETRY_DOC_IDS`/force-reprocess would regenerate it, and should NOT be run
against this doc_id until this fix is confirmed live (harmless either way
once it is — the new code produces the correct 42-page output).

**Test fixture note:** per this repo's no-committed-data-files rule (see
`CLAUDE.md`), the real zip is NOT committed. `tests/test_poison_doc_extractor.py`
synthesizes an in-process zip-of-.msg fixture of the same SHAPE (several
`.msg` entries via a multi-message `extract_msg.openMsg` fake, a repeated
substantive PDF attachment, several `imageNNN.png` signature attachments)
to exercise the same regression; the real-specimen numbers above (1,087→42
pages, 0 leaks) were verified by hand against the actual downloaded zip
during this fix's development, not committed as a test asset.

**Historical-corpus validation (post-review, 2026-09-21):** `_reject_if_corrupt`'s
`MAX_SANE_PAGES = 60` ceiling applies to every `synthesize_pdf()` call, not just
the zip-of-`.msg` case — a code review of this fix flagged an unvalidated
regression risk: any already-`processed` document that went through this
extractor before this fix, if it legitimately exceeds 60 pages, would newly
fail on any future manual retry/backfill of that doc_id. Checked directly
against the live Sheet + Drive: pulled the 14 `RETRY_DOC_IDS` from this ADR's
own "Activation" section (the RA `.msg`/`.docx` hand-pull specimens — every
ADR-documented document known to have gone through this extractor) plus
doc_id `4008289922215821067` itself, downloaded each one's archived Drive
PDF, and measured page counts directly: **0 of 15 exceed 60 pages** (largest
is 29, an "Analytical data" `.msg` with attached lab tables; most are 1-4
pages). The new ceiling would not have newly rejected any of them. Caveat
stated plainly: there's no recorded "went through the extractor" flag on any
Sheet row, so this covers every ADR-documented specimen but isn't a
mathematical proof against the full ~1,735-document corpus — given the
extractor only fires as a last resort (both the primary link and render
endpoint must fail first) and every known specimen sits nowhere near the
ceiling, this risk is treated as resolved rather than open.
