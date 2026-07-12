# Roadmap

Larger, not-yet-scheduled improvements to the monitor. Unlike
`docs/decisions/` (ADRs — decisions already made and built) or
`README.md`'s "Residual risks (accepted)" (known gaps deliberately not
fixed), this file is for real, scoped-but-not-started projects.

## Vision-based classification for image-only content

**Status:** proposed 2026-07-11/12. Not started.

**The gap:** `egle_doc_parser._classify_with_claude()` is text-only — it
sends Claude the document's extracted text (`page.get_text()`, after OCR
where applicable), never the page images themselves. For a page that's a
genuine photo (a site inspection photo, not a scanned text document), OCR
correctly finds zero text — there's no writing to read — so the pipeline
has *no* signal about what the photo actually shows. This isn't a bug
anywhere in the extraction path; it's a structural limit of a text-only
classifier.

**Where this showed up:** doc `1681010528757159679` ("Arbor Hills
Compost.msg", Arbor Hills Remediation Area / WRD-Groundwater, filed
2018-06-22) — a `.msg` with 4 real site-inspection photos
(`IMG_0682.JPG`–`IMG_0685.JPG`) from a storm-water review. Confirmed by
direct testing (2026-07-12): `poison_doc_extractor.py` (ADR 011) embeds all
4 photos correctly as real image pages in the synthesized PDF — the
extraction side works. But since Claude never sees the pixels, the
resulting Sheet summary can only report metadata (sender, date, "4 photos
attached"), not what the photos actually depict. This was investigated as
a candidate fix for that one doc and found to be a dead end for a targeted
retry — see `docs/session-2026-07-11-mmpc-nsite-extraction-overnight-worker.md`
for the full investigation. Reprocessing the doc changes nothing; only
giving the classifier vision would.

**What it would take:** for pages `classify()` already flags as image-only
(`needs_ocr`/`likely` with zero OCR text recovered, or specifically pages
PyMuPDF reports as image-only after OCR), send the page render (not just
`get_text()`) to Claude via a vision-capable call instead of, or alongside,
the current text-only path. Scope questions to work through before
building: page-image extraction/encoding, added token/cost per image page,
whether this applies to ALL docs or only ones that OCR flags as
image-heavy, and whether the resulting description should be tagged
distinctly (e.g. `severity`/`key_data_point` sourced from vision vs. text)
so a human reviewer knows which is which. A real, separate project — not a
bug fix, and not scoped in detail yet.

**Why not build it now:** deliberately out of scope for the ADR
011/overnight-worker session that surfaced it — accepted as a known gap
for now (see the doc's Sheet row, which already honestly flags the photos
as unassessed rather than guessing).
