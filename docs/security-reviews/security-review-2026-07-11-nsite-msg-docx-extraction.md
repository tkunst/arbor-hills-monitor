# Security Review: nsite-msg-docx-extraction

Date: 2026-07-11
Branch reviewed: `nsite-msg-docx-extraction` vs `main` (PR #3, ADR 011)
Reviewer: Claude Code (`/security-review`), multi-agent adversarial pass

No HIGH or MEDIUM confidence security findings identified in this PR.

**Scope reviewed:** `nsite_client.py` (the new gzip-decode helper and three-URL fallback chain in `download_pdf()`), `poison_doc_extractor.py` (new — `.msg`/`.docx` format sniffing, `_docx_body_text()`'s XML parsing, `extract_msg`-based `.msg` parsing and attachment recursion, `openpyxl`/`xlrd` spreadsheet extraction), `sheet_writer.py`'s `read_state()` change, `backfill.py`'s `RETRY_DOC_IDS` mechanism, `.github/workflows/backfill.yml`'s new `workflow_dispatch` input, and `requirements.txt`'s new dependencies.

**Methodology:** a finder sub-agent traced every data-flow sink reachable from the untrusted third-party (EGLE nSITE) HTTP response bytes this PR newly parses, verifying claims empirically rather than from memory or by trusting the code's own comments:

- Tested an XXE payload (`<!ENTITY xxe SYSTEM "file:///etc/hostname">`) directly against `xml.etree.ElementTree.fromstring()` (used by `_docx_body_text()`) — rejected with `ParseError: undefined entity`; stdlib expat does not resolve external entities by default.
- Read the installed `openpyxl==3.1.5` source directly and confirmed its XML parser is built with `resolve_entities=False` (or falls back to `defusedxml`/stdlib `ElementTree` without lxml) — no external-entity resolution in the `.xlsx` spreadsheet-attachment path either.
- Confirmed no `zipfile.extract()`/`.extractall()` calls exist anywhere in the new code — both `sniff_format()` and `_docx_body_text()` only ever call `.read()` on a single hardcoded member name (`word/document.xml`), so there is no path from a crafted zip member name to an arbitrary filesystem write.
- Traced every use of attachment filenames (`att.longFilename`/`shortFilename`) and confirmed they're used only for `.lower()`/`.endswith()` extension checks and as a literal text label inserted into a synthesized PDF page — never as a filesystem path, shell command, or format/eval string.
- Confirmed the one genuine filesystem write target (`dest_path`) is constructed upstream in `backfill.py`/`watcher.py` from trusted values (`facility_srn`, `doc_id`) — unchanged by this PR and never influenced by document/attachment content.
- Confirmed the new `.github/workflows/backfill.yml` `retry_doc_ids` input flows only through the safe `env:` pattern into the `RETRY_DOC_IDS` environment variable, never interpolated directly into a `run:` shell string — no GitHub Actions script-injection surface. `backfill.py`'s `_retry_doc_ids()` only does string splitting into a `set()` used for membership tests.
- Confirmed the new `PDF`/image-attachment parsing via PyMuPDF (`fitz.open(stream=raw, ...)`, `page.insert_image(...)`) is an extension of an *existing* trust boundary, not a new one: `egle_doc_parser.py` already hands arbitrary nSITE-sourced bytes to `fitz.open()` for every top-level document today, unmodified by this PR.

One low-confidence candidate was surfaced by the finder — "PyMuPDF now also parses PDF/image bytes nested inside `.msg` attachments, extending its existing native-parser attack surface to a second content source." An independent adversarial re-verification confirmed the finder's mitigating context (PyMuPDF already parses arbitrary untrusted nSITE bytes today) but rejected the finding itself: it names no concrete vulnerability, CVE, or attack path — a generic "native parsers can have bugs" observation, which falls outside this review's scope of concrete, exploitable vulnerabilities (confidence 2/10, below the reporting threshold).

**Key data-flow paths traced** (untrusted nSITE `.msg`/`.docx`/attachment content → sinks):

- `.docx` body/attachment text → `xml.etree.ElementTree` parsing: no external-entity resolution, confirmed by direct test.
- `.xlsx` spreadsheet attachments → `openpyxl.load_workbook()`: no external-entity resolution, confirmed by reading the installed library's source.
- `.msg` envelope/attachments → `extract_msg.openMsg()`: a third-party OLE2 parser; failures on malformed/non-`.msg` OLE2 content raise cleanly (verified against a real non-`.msg` OLE2 specimen during design) rather than producing unsafe output.
- Zip member reads (`.docx` format detection and body extraction): fixed hardcoded member names only, no traversal surface.
- Attachment filenames: text-label and extension-check use only, never a path/command/eval sink.
- The new GitHub Actions `workflow_dispatch` input: passed via `env:`, not shell-interpolated.
- No `subprocess`, `eval`, `exec`, `pickle`, or `yaml.load()` usage introduced beyond the pre-existing, unmodified `ocrmypdf` subprocess call in `egle_doc_parser.ocr_in_place()` (not touched by this diff, `dest_path` is never attacker-influenced).

A separate, non-security code review of this same PR found two real correctness issues in `.docx` text extraction (tab/line-break characters silently dropped, gluing adjacent values together; and a pre-existing text-box/drawing nesting bug that duplicated some extracted text) — both are data-quality defects, not security ones, and both were fixed and re-verified against the real hand-pulled specimens as part of that review. A third, lower-severity finding (an integration gap between the proactive-OCR fix and `parse_document()`'s own OCR gate) remains tracked for follow-up outside this security review's scope.
