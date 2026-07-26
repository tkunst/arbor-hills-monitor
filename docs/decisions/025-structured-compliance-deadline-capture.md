# ADR 025 — Structured compliance-deadline capture

Date: 2026-07-26
Status: accepted
Builds on: ADR 009 (WDS Stream C), ADR 006 (`_state` append-only log), ADR 024
(Stream C durability). Closes Gap G1-A-1 and the WDS compliance-deadline gap from
the data-lifecycle architecture (`docs/data-lifecycle-architecture.md`).

## Context

Compliance deadlines are the highest legal-value data the monitor sees
(enforcement due dates, corrective-action deadlines, public-comment windows), and
they were the least durably captured:

- **nSITE procedural filings.** The extracted deadline text lived only in the
  classifier's one-line `key_data_point`, written to the **mutable** Feed tab. It
  was not in the append-only `_state` payload and not structured. A Feed-tab clear
  lost it.
- **WDS `compliance_actions`.** The records carry structured fields
  (`Company Response Due Date`, `Company Response Date`, `Corrective Action
  Component`, ...), but nothing extracted them into a durable, deadline-shaped
  store — they existed only in the raw HTML snapshot and the mutable feed row.

There was no single place a human or script could read "every obligation the
landfill is under, its due date, and whether it was met."

## Decision

### 1. New append-only "Compliance Deadlines" tab

`TAB_COMPLIANCE_DEADLINES`, created by `ensure_tabs` (always present, since it is
fed by the always-on nSITE path). Six leading columns are Trisha's required
schema — Due Date, Extension Due Date, Actual Completion Date, Item Description,
Compelled By (document), Compliance Doc Effective Date — followed by provenance
(Source Stream, Facility/Site, Source Doc/URL, Extracted At). Append-only: a later
observation that fills in a completion or extension date appends a new row (latest
wins on read), never overwrites — so the history of an obligation is preserved.

### 2. A generic `deadlines` field in the classifier (not a domain coupling)

`egle_doc_parser`'s classifier gains an optional `deadlines` list (and `ParsedDoc`
a matching field), alongside the existing `measurements`. It is **generic**: "any
dated obligation a regulatory document imposes or reports," with the six-field
shape. This keeps the Decode base domain-agnostic — the classifier learns a
general document-understanding capability, not anything landfill-specific, exactly
as `measurements` and `risks` are generic. It reuses the existing per-doc
classification call, so there is **no added LLM cost**. The field defaults to `[]`,
so an older classification shape (or a doc with no obligation) is handled without
error.

### 3. Expand the nSITE `_state` payload (Gap G1-A-1)

`mark_processed`'s payload now carries `key_data_point` and `summary` in addition
to the existing keys. The append-only `_state` log therefore becomes a full
per-doc archive: the extracted claim (including enforcement-deadline text) is no
longer mutable-Feed-only. `read_state` already stores the payload verbatim, so no
reducer change was needed.

### 4. Two feed paths, one tab

- **nSITE:** after `write_document`, the watcher writes `parsed.deadlines` to the
  tab (source stream `nSITE`).
- **WDS:** the `compliance_actions` collection spec gains a `deadline` extractor
  (the only collection that defines one) that maps its already-structured fields
  onto the six-field schema (WDS has no separate extension field, so
  `extension_due_date` is blank). The event carries `deadline`, and the WDS
  `on_row` handler writes it (source stream `WDS compliance_actions`).

Both writes are **best-effort** (wrapped by the caller): a deadline-tab failure
can never block marking a doc processed, writing the feed row, or sending an
alert — the same rule the WOI summary tab follows.

## Consequences

- Gap G1-A-1 closed (nSITE) and the WDS compliance-deadline gap closed: deadlines
  now live in a durable, structured, machine-joinable tab, and the nSITE claim is
  in the append-only `_state` archive.
- No added LLM cost (reuses the existing classification call). One extra Sheet
  append per doc/record that actually carries a deadline (most do not).
- WDS deadline extraction is deterministic field-mapping (no model). nSITE
  extraction is model-derived, so the source PDF (Drive, Tier 1) remains the
  system of record — the tab row carries the source link for verification.

## Alternatives considered

- **A separate, dedicated LLM extraction pass for nSITE deadlines.** Keeps the
  classifier untouched but doubles the LLM calls on procedural docs. Rejected: the
  generic-field approach captures the same data in the existing call and stays
  domain-agnostic, so the extra pass buys nothing.
- **A landfill-specific `compliance_deadline` field in the classifier.** Would
  couple the domain-agnostic base to this project. Rejected in favor of the
  generic `deadlines` framing (a general capability any regulatory-doc consumer
  could use).
- **Overwrite-in-place on update.** Rejected: append-only preserves the full
  history of an obligation (when it was imposed, extended, completed) and matches
  the tab-durability model in ADR 024.
