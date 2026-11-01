# Handoff: coder:dataset-provenance-sheet

Source: Lotext `documents/arbor-hills/wellfield-auto-staging/DESIGN-sheet-provenance-and-dedupe.md`
§7 (overnight-worker #222, 2026-10-16), approved by Trisha as written on
2026-10-19. The block below is copied verbatim; the design's §3-§5 carry the
column definitions and the status vocabulary.

```
### [Coder] dataset-provenance-sheet: add Dataset Provenance tab + annotation columns to monitor Sheet

* **Context:** DESIGN-sheet-provenance-and-dedupe.md (overnight-worker 2026-10-16); provenance-map.csv; SPEC.md;
  FINDINGS.md. This is the Sheet follow-on to the already-approved coder:woi-auto-staging build (do not
  rebuild or modify the staging pipeline -- that ships separately).

* **Scope:** monitor repo only. Touches: Google Sheet (via service account) + the Mac staging script that
  writes to it. Does NOT touch public/ or any public-facing content.

* **Do:**
  (a) Create a new `Dataset Provenance` tab in the Sheet with columns: doc_id, document_title, link, dataset,
      source_report, status, first_release_tag, rows_contributed, last_updated. Populate from provenance-map.csv
      (the 21 wellfield rows whose status is known). Leave nSITE-doc-id-unknown rows with doc_id = "HC-<drive_id>".
  (b) Add `dataset_status` pointer column to `Historical Documents` and `New Documents` tabs: HYPERLINK formula
      pointing to the matching Dataset Provenance row, empty if not a dataset document.
  (c) Add `same_as_monitor_doc` column to `Hand-Curated Files` tab. Set it for the 2 confirmed ANNOTATION-mode
      duplicates: `7894782240984169987` (2025-01-06 2024 H2 copy) and `409490399157019600` (2026-08-07 SSO copy).
  (d) Add `same_content_as` column to `Archived PDFs` tab. Set it for the confirmed Q2 2023 duplicate pair
      (one of -2136493721019571731 / 8700874927542414020 gets `same_content_as` pointing to the other).
  (e) Update the Mac staging script (the one approved in coder:woi-auto-staging) so that at each staging run
      it also writes a row to `Dataset Provenance` for any newly staged document, with status = "staged for next
      release". The publish step (manual) flips status to "published (since <tag>)".
  (f) Guardrails: read-only on all other tabs; no workflow_dispatch; no public/ edits; no release edits.

* **Done when:** Dataset Provenance tab exists with 21+ populated rows; pointer column exists in feed tabs;
  annotation columns added to Hand-Curated Files + Archived PDFs; staging script writes to the tab; tests pass.
```
