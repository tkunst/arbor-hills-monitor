# ADR 001 — Data store: state file in Drive + Google Sheets (not SQLite)

*Status: accepted — 2026-06-13*

## Context

The monitor needs two kinds of persistence: (a) "have I processed this document
already?" across nightly GitHub Actions runs, and (b) a human-usable case file
the Conservancy (non-technical) can read, filter, and hand to a commissioner or
EGLE.

GitHub Actions runners are ephemeral — no disk persists between runs. The users
are not engineers and will not run queries.

## Decision

- **Processing state** lives in a JSON file, `egle-n2688-state.json`, stored in
  the shared Google Drive folder (read/written via the Drive API). It survives
  across runners because it lives in Drive, not on the runner.
- **The case file** is a Google Sheet with four+ tabs (New / Historical
  Documents, Evidence by Risk, Risk Register, Measurements). The Conservancy
  already works in Sheets.

## Why not SQLite

- A SQLite file would also have to live in Drive (runners are ephemeral), giving
  the same round-trip cost with none of the human-readability — the Conservancy
  can't open a `.db`.
- The data volume is tiny (≤ ~754 docs). A JSON state file plus a Sheet is
  simpler, debuggable by hand, and directly shareable.
- Graduation trigger: if we ever need cross-document queries the Sheet can't
  express (per-well trend/velocity at scale), revisit — a small SQLite or
  DuckDB layer derived from the Measurements tab would be the next step, without
  reprocessing source PDFs.

## Consequences

- Every processed doc writes the Sheet row first, then the state entry (see
  backfill.py / watcher.py) — a crash between the two re-writes the row on
  resume rather than silently dropping it.
- State is small JSON; concurrent runs are prevented by the workflow
  `concurrency` group, so there is no multi-writer race on the state file.
