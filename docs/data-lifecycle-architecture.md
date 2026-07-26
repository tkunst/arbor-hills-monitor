# Arbor Hills Monitor: Data-Lifecycle Architecture

This document defines the durability model that governs how every data stream in
this monitor moves from fetch to storage to index. It is the "what should hold"
reference; the per-stream implementation status and the roadmap for closing any
gaps are tracked separately (see `docs/roadmap.md` and the ADRs under
`docs/decisions/`).

Companion documents:

- **`docs/business-rules.md`** covers the orthogonal concern: risk
  classification, alert routing, WOI temperature thresholds, urgency and
  severity taxonomy, and measurement basis rules (measured / permitted_limit /
  unknown). This document does not restate any of that.
- **`docs/decisions/`** (ADRs) record the per-stream design choices this model
  is derived from: ADR 004 (measurement schema), 005 (WOI routing), 007 (OAuth
  durable PDF archive), 009 (WDS Stream C), 010 (MMPC mirror), 012 (PFAS page
  watch), 014 (GFL air), 016 (Ridge Wood), 017 (ROP), 018 (MMD), 019 (RIDE),
  020 (nSITE submissions), 022 (nSITE site registry).

---

## Overview

The monitor watches a dozen public data sources for the Arbor Hills landfill and
its neighbors, and lands what it finds in three places, each with a different
durability guarantee:

1. **Google Drive** holds immutable source blobs (PDFs, HTML snapshots) through
   the OAuth user client.
2. **A single Google Sheet** is the working monitoring interface. Its tabs come
   in two flavors: append-only watch/log tabs, and mutable feed/index tabs that
   the pipeline rewrites.
3. **The `_state` tab** is the pipeline's own processed-record audit log.

The design question this document answers is: for any given record, **where does
it durably live, and can it survive the loss or corruption of the working
Sheet?** The Durability Predicate below formalizes "durably live"; the Universal
Contract states the three properties every stream should satisfy; the Worked
Example traces one stream end to end.

Scope note: this is a data-movement and storage-durability model. It says nothing
about *whether* a record should alert, *how* it is classified, or *what* risk it
carries. Those are business rules.

---

## Durability Predicate

**A data record is DURABLE when it exists in a store that cannot be silently
overwritten in the ordinary course of pipeline operations.**

"Silently overwritten in the ordinary course of operations" is the key phrase. A
human can always delete a Drive file or a Sheet row by hand; that is not what
this predicate guards against. It guards against the *pipeline itself* clobbering
a record on a routine run.

There are three durability tiers in this system:

| Tier | Store | Can the pipeline silently overwrite it? | Examples |
|---|---|---|---|
| **1: Immutable** | A Drive file (PDF or HTML blob) uploaded via `archive_client.upload_file` / `upload_pdf` | No. A new upload creates a new object; Drive preserves version history. | nSITE PDFs, MMPC meeting PDFs, Ridge Wood monthly PDFs, WDS HTML snapshots |
| **2: Append-only** | A Sheet tab written only through `sheet_writer.append_rows` (never `values().update()`) | No. The pipeline only ever adds rows; it never rewrites or clears an existing row. | The watch/log tabs (per-observation snapshot rows) and the `_state` audit log written by `mark_processed` |
| **3: Mutable** | A Sheet tab written with `values().update()` or rebuilt on a run | Yes. The pipeline can overwrite or clear any row on any run. | Feed / index / summary tabs and singleton state cells |

The ranking is strict: Tier 1 is more durable than Tier 2 is more durable than
Tier 3.

**How the predicate is applied:**

- For the **durable-structured-export** property (Rule U1) and the
  **courtroom-exhibit** property (Rule U3), "durable" means **Tier 1 or Tier 2
  with the full structured payload preserved**. A record that lives only in a
  Tier 3 mutable tab does not satisfy U1 or U3.
- For the **Sheet-index** property (Rule U2), a **Tier 3 mutable tab is
  acceptable**, because U2's goal is human and machine readability of the current
  view, not archival immutability.

**Write-ordering invariant.** Durability is only useful if it is established
*before* the monitor tells a human the record exists. The intended order for any
stream that produces an immutable copy is:

```
Drive upload (Tier 1)  ->  Sheet index row  ->  alert email
```

If a run crashes between steps, the durable copy already exists and the next run
reconciles the Sheet from it (dedup is derived from the Sheet or the folder, not
from a fragile in-memory cursor), so an alert is not sent for a record that is
not already durably stored. The Worked Example below traces this order for the
MMPC stream; holding it uniformly across every stream is an acceptance criterion
tracked in the roadmap.

---

## Universal Contract

Three properties should hold for every data type the monitor captures. They are
stated as Given / When / Then rule cards so each is independently testable
against the code.

### Rule U1: Durable Structured Export

```
Given: any new or changed data record has been fetched and parsed by the pipeline
When:  the pipeline writes its output
Then:  the structured payload (all extracted fields) is saved to a durable store
       (Tier 1 immutable Drive, OR a Tier 2 append-only tab carrying the full
       payload) BEFORE any alert email is sent, and INDEPENDENT of the mutable
       feed / index tabs
```

**Rationale.** The mutable Sheet is the working interface. If it is corrupted,
accidentally cleared, or its API quota is exhausted, the only recovery path is
the durable store. A record that exists only in a mutable tab is one Sheet API
call away from permanent loss.

**Predicate test.** For a given stream, can the complete structured payload be
recovered from non-mutable-Sheet storage? Tier 1 or Tier 2 with the full payload
satisfies U1.

### Rule U2: Sheet Index

```
Given: a durable record exists (per U1)
When:  the pipeline writes to the Sheet
Then:  at least one Sheet tab row contains human-readable fields that uniquely
       identify the record AND a machine-parseable key (date, ID, hash, or URL)
       that links it back to the durable copy or the source
```

**Rationale.** The Sheet is the primary monitoring interface. Every durable
record must also appear as a Sheet row a human can read without decoding blobs,
and a script can JOIN without a full-text search.

### Rule U3: URL-Rot-Proof Courtroom Exhibit

```
Given: a source document or data snapshot has been captured
When:  the pipeline archives it
Then:  a self-contained copy is saved to Google Drive, reachable through a
       permanent public share link, and renderable without fetching any external
       resource (CSS, images, fonts, or live data) at viewing time
```

**Rationale.** Legal and advocacy contexts may require producing an exhibit
months or years after the source URL has changed, the underlying site has gone
offline, or an interactive dashboard has been retired. A born-digital Drive PDF
is immune to all of these. A stored copy that still loads styling or data from
the live source site is not self-contained: it degrades silently when that site
changes.

`archive_client.upload_file` returns the Drive `webViewLink`, and the archive
folders are shared "Anyone with the link" (ADR 007), so the exhibit link is
stable and portable.

---

## Worked Example

**Stream: MMPC Minutes (Mirror D).** The Washtenaw County Materials Management
Planning Committee (MMPC) document mirror (`mmpc_archiver.py`, `mmpc_client.py`,
ADR 010) auto-downloads every Agenda / Minutes / Other PDF that CivicClerk
publishes for the committee (category_id 72) and archives it. It is the clearest
illustration of all three rules holding together.

**U1 (Durable Structured Export).**

- The **source PDF** is uploaded to the MMPC Drive folder
  (`GOAUTH_MMPC_FOLDER_ID`) via the OAuth user client
  (`mmpc_archiver.run` calling `archive_client.upload_pdf`). This is Tier 1
  immutable: a new upload creates a new object, and the file cannot be
  overwritten by a routine run.
- The **structured payload** for this stream is the document's metadata (file
  ID, meeting date, type, document name, event ID). It is captured in two durable
  places at once: encoded in the immutable Drive filename, and written as a row
  in the append-only MMPC Archived Files tab (`sheet_writer` append via
  `TAB_MMPC_ARCHIVE`, headers `MMPC_ARCHIVE_HEADERS`, using `append_rows`, never
  an update). Both are recoverable without the mutable feed tabs. U1 holds.

**U2 (Sheet Index).**

- The MMPC Archived Files tab row is the index: it carries human-readable fields
  (meeting date, type, document name) and machine-joinable keys (`file_id`,
  `event_id`, plus the `archive_link` pointing at the Drive copy). A human reads
  the row; a script JOINs on `file_id` or `event_id`. U2 holds.

**U3 (URL-Rot-Proof Exhibit).**

- The archived PDFs are CivicClerk-exported meeting documents: born-digital and
  self-contained, requiring no external CSS or live data to render. They live in
  Drive behind a permanent "Anyone with the link" share (`webViewLink` from
  `archive_client.upload_file`). An exhibit of "here is what the committee
  published on date X" points at a Drive PDF that survives the CivicClerk portal
  changing or going offline. U3 holds.

**Write-ordering invariant, made concrete.** `mmpc_archiver` uploads the PDF to
Drive *before* it appends the Sheet index row, and dedup is derived from the
Archived Files tab itself (`sheet_writer.mmpc_archived_file_ids()`), not from a
mutable singleton cursor. If a run dies mid-stream, the durable PDF is already in
Drive and the next run reconciles the missing Sheet row from the folder. The
human-facing signal never precedes the durable copy.

---

*Architecture reference. File and line references reflect the repository as of
2026-07-26; cite by module and symbol, which are stable, when line numbers have
drifted. This document is descriptive of the durability model only; per-stream
status and remediation are tracked in `docs/roadmap.md` and the ADRs.*
