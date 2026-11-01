# Overnight-coder handoff — Document Date + real titles for every generic-titled nSITE document

*Opened 2026-10-09 (Trisha-directed). Read `docs/overnight-coder.md` first. This is a LIVE-PATH
change (daily parse/display path + Sheet schema) with a one-time live-Sheet backfill, so per Step 8
open a **DRAFT PR for Trisha's review, not an autonomous merge**, and the backfill stays **GATED on
Trisha's explicit go**. Recommended model tier: **Sonnet**.*

**Supersedes and absorbs `schedule-title-overrides.md`** (written 2026-09-01, never built). Build that
handoff's override map, classifier and digest-tier bump as part of this work; its 8-doc rename map
(plus the 2026-09-25 nForm addition) seeds the override map here. Mark the old handoff superseded.

## Invocation

Branch name suggestion: `document-dates-and-titles`.

## Why (what Trisha keeps hitting)

The monitor shows two things that come straight from nSITE and routinely mislead:

1. **Date.** The only date shown is nSITE's **date filed** (when EGLE attached the file to its
   record), not the date on the document. They often differ by weeks or months, in both directions:
   - Compost-pond lab report (RA doc `-2761350602420392110`): Fibertec report **dated 12/23/2019**,
     filed **2020-03-23** (EGLE WRD assembled its file after a 3/10/2020 meeting).
   - VN-011821 (RA doc `8343469287207026660`): letter **dated 6/14/2021**, filed **2021-05-21**.
   - AQD Violation Notice VN-019436 (N2688 doc `-7969590931980962586`): letter dated **7/8/2026**
     (cover page also shows July 10, 2026); EGLE's compliance action is dated 7/15/2026.
   Searching by date in the Sheet or on the site therefore misses or misplaces documents.
2. **Title.** nSITE titles are often generic. Of 2,301 documents across the monitored sites
   (2026-09-30 profile pull), **957 (42%)** carry one: `nForm Document` (427), `Site` (213),
   `Submission PDF` (169), `Schedule - …` (134), `Submital Attachments` (10), `Correspondence` (3).
   The compost-pond lab report above is titled just "Site".

## The change

### (a) New fields from the existing parse (no extra LLM call for new documents)

Extend the parser's output (`egle_doc_parser.parse_document` / the classifier prompt) with:

- `document_date` — the date printed on the document: letter date, report date, lab report date,
  inspection date for an inspection report. ISO `YYYY-MM-DD`, or empty if none/ambiguous. Prefer a
  **deterministic first pass** (regex for a dateline in the top of page 1: `Month D, YYYY`,
  `M/D/YYYY`, `Monday, December 23, 2019`, `DATE:` lines; email `Sent:` headers for cover emails),
  LLM only as fallback. Record which method produced it.
- `display_title` — a descriptive title (≤ ~120 chars) for documents whose nSITE title is generic:
  what it is, who sent it to whom (organizations, not people), subject, and the document date, e.g.
  *"Fibertec lab report to ERG for Advanced Disposal compost-pond samples (submitted 12/16/2019), dated 12/23/2019"*.
  Source it from the letter SUBJECT line / report title page when present.
- `title_is_generic` — computed **deterministically** (not by the LLM) from a small list of generic
  nSITE names (the six above; make the list config-driven).

**Privacy (hard requirement):** `display_title` is published on the public feed. It must contain no
personal names (staff, residents, correspondents). Run it through `name_check.py` and strip/redo until
publish-clean; if it can't be made clean, fall back to the nSITE title. Coordinate with the
`coder:auto-feed-name-redaction` direction (classifier omits individuals' names).

### (b) Display

- **Override precedence:** `config.yml` `document_title_overrides` (doc_id → name, from the old
  handoff) > `display_title` (only when `title_is_generic`) > nSITE title.
- **Never change** `doc_id`, the state/dedupe key, `doc_url`, or **Date Filed**. Display-only.
- Sheet: add **`Document Date`** and **`EGLE Title`** (the raw nSITE title) columns. **Append them at
  the END of each affected tab** unless you audit every consumer that indexes columns by position
  (`sheet_writer`, `findings_feed`/`gen_findings_feed`, `public_comment`, the WDS tabs, any script
  in `scripts/`, the site build). Affected tabs at minimum: New Documents, Historical Documents,
  Archived PDFs, Evidence by Risk, All Evidence by Risk.
- `Document Name` shows the resolved display name. Public feed + digest show the display name and
  `Document Date` (with "filed <date>" when they differ by more than ~7 days).
- Search/sort on the public site should be able to use Document Date (fall back to Date Filed when
  empty).

### (c) Classifier + digest tier (from the old handoff)

Keep the old handoff's section (b): detect EGLE approval / extension / corrective-action /
enforcement letters under generic titles and bump them off the soft digest line.

## Backfill (GATED)

1. **Dry run first, no Sheet writes.** For every tracked document: extract `document_date`, and for
   `title_is_generic` docs propose `display_title`. Source text: whatever `_state` Payload JSON
   already holds; otherwise the PDF in the Case File Mirror (kunst mount,
   `<SRN>_<doc_id>.pdf`) or a fresh nSITE download. Write **`backfill-review-<date>.csv`** (doc_id,
   site, date_filed, document_date, method, nSITE title, proposed display_title, name_check result)
   and STOP. Trisha reviews it.
2. **Cost:** deterministic dates should cover most rows; cap LLM use to the generic-title set plus
   date misses, and report the token/cost estimate in the PR before running it. Use whatever API key
   the monitor already uses; do not add a new paid service.
3. Only after Trisha's explicit go: write the two new columns + resolved names to the live Sheet in
   batched cell updates (no row deletions or re-orders).

## Acceptance checks (real specimens, not mocks)

| doc_id (site) | nSITE title / filed | expected `document_date` | expected title gist |
|---|---|---|---|
| `-2761350602420392110` (RA) | Site / 2020-03-23 | 2019-12-23 | Fibertec lab report, compost-pond samples |
| `-9174128623563815566` (RA) | Site / 2020-03-23 | (table; latest sample 2020-02-20 or empty) | ERG compost-pond NPDES data table |
| `3513501826776499700` (RA) | Site / 2020-03-23 | (table) | ERG compost-pond YCUA data table |
| `8343469287207026660` (RA) | Violation Notice / 2021-05-21 | 2021-06-14 | (title already fine; date fixes) |
| `-7969590931980962586` (N2688) | Violation Notice / 2026-07-08 | 2026-07-08 | (date check) |
| `5234117446316116012` (N2688) | Schedule - Air General Compliance Report / 2026-08-26 | 2026-08-27 | EGLE approval of 120-day extension, CJ 5.5(E) |
| `7045536706099143566` (N2688) | nForm Document | (report date) | 2026 Q2 Consent Judgment quarterly report |

Add unit tests for the deterministic date extractor (each dateline format above, plus an email
cover page) and for override precedence.

## Out of scope

- Renaming files in the Drive mirror (filenames stay `<SRN>_<doc_id>.pdf`).
- Hand-Curated rows (they already carry `doc_date` + human titles).
