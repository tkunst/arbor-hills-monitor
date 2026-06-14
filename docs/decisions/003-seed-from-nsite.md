# ADR 003 — Seed the backfill from the live nSITE list, not Documents.csv

*Status: accepted — 2026-06-13 — deviation from monitor-plan v3*

## Context

The v3 plan says to seed the backfill from the existing `Documents.csv` in the
Drive folder. That CSV's exact column names were not visible at build time.

The live nSITE document API for facility `8094300008956198244` returns the full,
authoritative document list — verified during the build: **754 documents**,
dated 2006–2026, each with the `nSITE doc id` we dedup on. (This is the same
source the CSV was originally generated from.)

## Decision

Backfill seeds from `nsite_client.fetch_site_documents()` (the live list), not
from `Documents.csv`.

## Why

- **No unseen-schema risk.** Seeding from a CSV whose columns we can't see is a
  guess that would break at deploy. The nSITE list is self-describing and was
  validated end-to-end (fetch + download + PyMuPDF open + `classify()`) against
  real documents during the build.
- **Authoritative + dedup-safe.** `nsite_doc_id` is the dedup key in the state
  file; the live list always reflects current filings, so backfill and the daily
  watcher share one source of truth.
- **Reuses tested code.** The same `nsite_client` the watcher depends on.

## Consequences

- `Documents.csv` is not read. The ~38 manually-downloaded PDFs already in the
  folder are not name-matched (manual names → nSITE ids is unreliable); instead
  every doc is (re)downloaded from nSITE and stored under a canonical name
  `N2688_<docid>.pdf`. The state file + canonical naming are the dedup
  mechanism, so re-runs are cheap and idempotent.
- If a future need arises to reconcile against the CSV, do it as an explicit,
  fail-loud column mapping — never a silent best-effort match.
