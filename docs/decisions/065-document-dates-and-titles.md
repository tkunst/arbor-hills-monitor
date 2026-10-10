# ADR 065 — Document Date + real titles for generic nSITE documents

*Status: active — 2026-10-09 (Trisha-directed). Absorbs and supersedes the never-built
`schedule-title-overrides.md` handoff (2026-09-01): its override map, correspondence
classifier, and digest-tier bump are all implemented here as part of this work.*

## Context

The monitor shows two things that come straight from nSITE and routinely mislead:

1. **Date.** The only date shown is nSITE's **Date Filed** (when EGLE attached the file to
   its record), not the date printed on the document. They differ by weeks or months, in
   both directions — a Fibertec lab report dated 2019-12-23 wasn't filed until 2020-03-23
   (EGLE assembled its file after a later meeting); a VN-011821 letter dated 2021-06-14 was
   filed 2021-05-21 (filed before its own stated date).
2. **Title.** nSITE titles are often generic. Of 2,301 documents across the monitored sites
   (2026-09-30 pull), 957 (42%) carry one of six placeholder titles: `nForm Document` (427),
   `Site` (213), `Submission PDF` (169), `Schedule - …` (134), `Submital Attachments` (10),
   `Correspondence` (3). A live scan of every N2688 `Schedule -` doc found 8/8 were substantive
   EGLE approval/extension letters hiding under a bland title — one went out in a Sunday digest
   as a soft line between wetland filings, two days before a community member independently
   emailed the same letter around.

## Decision

### Deterministic date extraction (egle_doc_parser.py), LLM only as fallback

`extract_document_date_from_text(pages)` scans the document's own RAW first two pages (never
the keyword-windowed classifier text, which carries page-marker banners) for, in priority
order per page: an email `Sent:` header, a `DATE:` label (same-line value only), or a
month-name dateline (optionally weekday-prefixed, e.g. "Monday, December 23, 2019"). **A
LATER page's own match overrides an earlier page's** — confirmed on a real EGLE Violation
Notice (`-7969590931980962586`) whose page-1 cover date (July 10, 2026) and page-2 running
header (July 8, 2026, matching Date Filed) disagree; the later page is the correct one. A page
with no match leaves the running candidate as-is (a plain continuation page never blanks an
earlier finding).

**Deliberately does not scan for a bare, unlabeled M/D/YYYY** anywhere on a page. Verified
against two real RA NPDES/YCUA lab-data table specimens (`-9174128623563815566`,
`3513501826776499700`): their only dates are bare slash-dates in a sample-date table column,
indistinguishable syntactically from a real dateline. A labeled `DATE:`/`Sent:` value may still
be a slash-date (the label is what makes it trustworthy); an unlabeled one never is. All 7
acceptance specimens pass with this rule; the two table docs correctly produce `("", "")`
(empty — one of the handoff's own tolerated outcomes).

The model is still asked for `document_date` (the SAME classification call — no extra LLM
call for new documents) as a fallback for docs the deterministic pass finds nothing in;
`_is_plausible_iso_date` validates it (parses as ISO, year in [1990, this-year+1]) before it's
trusted. `document_date_method` records which pass produced it: `dateline` / `date_label` /
`email_sent_header` / `llm` / `""`.

### Generic-title detection + display_title (egle_doc_parser.py + document_titles.py)

`parse_document()` gains a plain `title_is_generic: bool` parameter — domain-agnostic, same
reuse story as `risk_register` (this module has no opinion on WHAT makes a title generic). The
per-document user message (not the cached system prompt — this must not break prompt caching)
carries a `Title is generic (propose a display_title): yes/no` line; the model only fills
`display_title` when told to. `ParsedDoc.display_title` is the RAW model output — egle_doc_parser
has no name_check dependency.

`document_titles.py` (a NEW, separate module — kept out of egle_doc_parser.py, which stays the
domain-agnostic Decode base) owns the Arbor-Hills-specific pieces:

- `title_is_generic(title, generic_exact, generic_prefixes)` — exact match against
  `config.yml`'s `document_titles.generic_exact` list, or a case-insensitive prefix match
  against `generic_prefixes` (`"Schedule - "` covers every `Schedule -*` title as one entry,
  confirmed 8/8 substantive at N2688; the same prefix also sweeps in other facilities' genuine
  `Schedule - DMR`/`Schedule - Submit...` stubs, which is harmless — a genuine stub's classifier
  just finds no usable display_title and keeps its plain nSITE title).
- `sanitize_display_title(text)` — strips every `name_check.find_denylist_hits` match (longest
  match first, so a listed full name and its listed bare surname don't leave a fragment
  behind), re-checks, and returns `""` if it still isn't `is_clean_for_publish` after a few
  passes. **No retry call to the model** — this is a deterministic text edit, so the
  "no extra LLM call" contract holds even for the privacy gate.
- `resolve_display_name(doc_id, nsite_title, display_title, overrides)` — precedence:
  `config.yml`'s `document_titles.overrides` (doc_id → name, Trisha-curated) > the classifier's
  (sanitized) `display_title` > the raw nSITE title.

### The chokepoint (watcher.py / backfill.py)

Right after `parse_document()` returns and before `mirror_one_now()` / `write_document()` /
the digest-or-alert routing / the `_state` payload, both jobs run the SAME sequence on the
live `d` metadata dict:

```python
is_generic = dt.title_is_generic(d["document_name"], generic_exact, generic_prefixes)
parsed = parse_document(..., title_is_generic=is_generic)
clean = dt.sanitize_display_title(parsed.display_title) if is_generic else ""
d["egle_title"] = d["document_name"]              # raw nSITE title, preserved
d["document_name"] = dt.resolve_display_name(did, d["egle_title"], clean, overrides)
d["document_date"] = parsed.document_date
d["title_was_generic"] = is_generic
```

Everything downstream — the Drive mirror row, the feed/evidence rows, the digest/urgent
record, the `_state` payload — reads `d` as before and inherits the resolved values for free.
The classifier itself still only ever sees the RAW title (via `metadata["document_name"]`
passed into `parse_document` before the chokepoint runs); dedupe stays on `doc_id`, never
touched.

**`poison_stub.py`** (a poison doc never gets parsed) applies ONLY the `overrides` map — no
generic-title/LLM path exists without a parse. `egle_title` is always the raw title either way;
`document_date` stays blank.

### Correspondence & enforcement digest tier (absorbs the retired handoff's section b/c)

`email_alerts.is_correspondence_letter(full_text)` — EGLE's own letterhead footer
(`Michigan.gov/EGLE`, distinctive enough not to match a letter merely ADDRESSED to the agency —
confirmed against a real GFL→EGLE cover letter that spells out the agency's full name in its
own address block but never this URL) AND at least one substantive signal phrase (extension
request, corrective action, approval, Consent Judg(e)ment, action-level exceedance, HOV /
Higher Operating Value). Verified against 3 real confirmed-positive EGLE letters and 2 real RA
DMR stubs (negative controls) during this build's spike.

Only ever checked for a doc already flagged `title_was_generic` (the rescue this exists for —
a document with a real, specific title doesn't need it). `watcher.py._digest_record` computes
`is_correspondence` once, at queue time (the only point `full_text` is still available — it
isn't persisted into `_meta.pending_digest`), and bakes the bool into the record.
`email_alerts.format_digest_body` pulls every `is_correspondence` item into its own pinned
**"CORRESPONDENCE & ENFORCEMENT"** section, rendered right after the urgent recap and before
the ordinary procedural/other split — Option A from the retired handoff's own two options
(recommended there: visible without blasting a same-day email to the whole coalition list).
Option B (escalate specific classes to same-day) is NOT built; it's a config-free follow-up if
Trisha wants it later.

### Display

`sheet_writer.py` appends **`Document Date`** and **`EGLE Title`** at the END of
`FEED_HEADERS` (covers New/Historical/Related Documents), `EVIDENCE_HEADERS`, `ARCHIVE_HEADERS`,
and `ALL_EVIDENCE_HEADERS` (WDS rows get blank values for both — a WDS record isn't a filed
nSITE document). Appending at the end, never inserting, means every existing positional reader
(`feed_row`, `evidence_rows`, `append_archive_row`, `all_evidence_rows`, `archived_doc_links`'s
`A2:F` read, `archived_doc_ids`'s `A2:A` read) keeps working unchanged — checked by grep across
`sheet_writer.py` and `scripts/`. `scripts/gen_findings_feed.py`'s `_tab_values` default range
widened `A2:I` → `A2:K` to match the new FEED_HEADERS width (this one WOULD have silently
dropped both new columns otherwise — the gap the handoff's own guardrail warned about).

`findings_feed.py`: `FEED_FIELDS` gains `document_date`/`egle_title`. `display_date(row)` shows
the document's own date, falling back to Date Filed when unknown, annotated `"(filed <date>)"`
only when the two differ by more than 7 days — browse-list ORDER is deliberately untouched
(still `date_filed` via `_sort_newest_first`; a doc with an old `document_date` must still land
on the newest-first page it was actually filed on). The search index's `date` field prefers
`document_date` (falling back to Date Filed) since `search.js` is schema-agnostic — a reader
searching "December 2019" should find the Fibertec report by its real date.

**Accuracy note (added mid-build, not in the original handoff text):** the displayed title may
now be a classifier-proposed `display_title`, which the existing per-item "Automated summary"
disclaimer (PR #73 / master analysis 5.4) doesn't cover — that label only governs the summary
and key-data-point paragraphs below the `<h3>` title. `render_entry`'s meta line now shows
`EGLE title: <raw title>` whenever the raw nSITE title differs from what's displayed (covers
BOTH the override and classifier-generated cases, since a reader doesn't need to know which
mechanism produced the shown title — only that nSITE's own title was something else), through
the SAME `redact_names()` env-configured redaction every other public field here uses (not
`name_check` — that's this file's existing, deliberate split; `name_check` is the write-time
gate `sanitize_display_title` already applied before the row was ever written).

## Backfill (gated — not run by this change)

A SEPARATE, standalone script (never folded into `backfill.py`'s live pipeline) does a dry-run
pass only: deterministic extraction is free; the generic-title/date-miss set gets an LLM pass
with a cost estimate shown before anything runs; writes a CSV outside the repo (data-guard
blocks `*.csv`, and the file carries `name_check` results) and stops. A second, separately-gated
apply step writes the two new columns to the live Sheet only after Trisha's explicit go.

## Guardrails / residual risk

- Additive and config-driven (`document_titles:` in `config.yml`), matching every existing
  watcher's pattern. No change to `doc_id`, the dedupe/state key, or `doc_url`.
- `display_title` generation is fail-safe toward the plain nSITE title: a blank/unstrippable
  model proposal, or `title_is_generic=False`, always falls through to `resolve_display_name`'s
  last branch.
- `is_correspondence_letter` fails safe toward False (a miss just leaves today's existing soft
  digest line — no regression).
- The correspondence classifier's letterhead/signal phrases were verified against 3 real
  positives + 2 real negatives, not the full 8-doc set from the retired handoff (time-boxed
  during this build) — the backfill dry-run CSV surfaces every historical generic-titled doc
  for Trisha's eyes before anything is asserted about it live.
