# ADR 064 — Facility scope: neighbor sites out of the Arbor Hills feed, a Related Documents tab

*Status: active — 2026-10-07 (Trisha-directed). Supersedes the `facilities:` part of ADRs 051–057.*

## Context

On 2026-09-25 ADRs 044–057 added about 20 nSITE registrations near the landfill to `facilities:`.
That list is the Documents pipeline. Every document from every entry was classified against the
Arbor Hills risk register and written to New/Historical Documents (and so to the public-records
pages), Evidence by Risk, Measurements and Compliance Deadlines. Routine ones were queued for the
coalition Sunday digest, and urgent ones were emailed as `[URGENT] Arbor Hills N2688`. The
2026-10-04 digest had 15 items, and 10 of them were Toll Brothers "Coldwater West" filings from
the Coldwater Ridge site (COLDR). The classifier itself called several of them "unrelated to
Arbor Hills Landfill". Four Johnson Creek Intercounty Drain Restoration (JCDR) permit documents
were queued for the 2026-10-11 digest.

The cause was the roster, not keyword matching, so an exclude list on words like "Coldwater" would
have been wrong. "Coldwater" also names Johnson Creek's cold-water designation, which appears
throughout the real record.

## Decision (Trisha's rulings, 2026-10-07)

Each `facilities:` entry now has a scope. An entry with no scope is `core`. The test
`tests/test_facility_scope.py` pins the full set, so adding a facility means classifying it.

| Scope | Sites | What happens to their documents |
|---|---|---|
| core | RA, WRD, N1504, P1488, N2688, AHLI, NAPR, PTI87, BORE15, MITIG | Unchanged. |
| related | JCDR, CHUBB18, CHUBBRD, WIP25, BDPH, WIP19, FP89, ASB26, CMP23, H95 | **Related Documents** tab only (feed columns). No Evidence/Measurements/Deadlines rows, no WOI routing, no digest, no urgent alert, not on the website (Trisha chose "Sheet only"). |
| dropped | COLDR, TOLL, RIDGE, PTEX, LUMB, DTE5M, FMCOM, NTWP | Removed from `facilities:`; documents are no longer collected. They stay in `nsite_sites` with their profile-watch tiers, which email Trisha only, so a new permit or violation there still reaches her. |

Trisha ruled on JCDR (related: watershed news, not GFL), the eight development sites (dropped)
and the Sections 12–13 parcels (related). FP89, ASB26, CMP23 and H95 have 0 documents. They
default to related: if one turns out to be a landfill record, it is still captured, just not
published as one. Move it to core if that happens.

`nsite_client.fetch_all_documents` tags each document with `facility_scope`. It refuses to run
on an unknown scope, because a typo must neither publish a neighbor as core nor hide a GFL site.
`watcher.py` and `backfill.py` branch on `sheet_writer.is_related(d)`. A poison stub for a
related document lands on Related Documents (`write_stub_row` → `feed_tab_for`). The new tab is
in `_PURGE_TABS`, so FORCE_REPROCESS stays clean.

## Existing rows

`scripts/oneoff_move_neighbor_rows.py` (dry run by default) handles rows already written. It
first writes a full JSON backup of every tab it touches, plus `_meta`, outside the repo. Then it:

- copies the related sites' feed rows to Related Documents;
- deletes the rows of both groups from New/Historical Documents, Evidence by Risk, Measurements
  and Compliance Deadlines, after re-checking that each row is unchanged;
- removes their `pending_digest` / `pending_urgent_recap` entries;
- rebuilds Risk Register and All Evidence.

Dry run on 2026-10-07: 14 + 311 feed rows, 9 evidence, 90 measurements, 16 deadlines, 4 pending
digest entries; 128 rows copied. Archived PDFs (the Drive mirror index) is left alone, so nothing
is re-uploaded. `_state` is left alone, so nothing is re-processed. The public-records pages drop
the rows on the next `gen_findings_feed` run. Delete the script once it has been applied.

## Risks

- **A misclassified site goes quiet.** Related documents are still collected and visible on the
  Sheet. Dropped sites keep their profile watches. Recovery is a one-line config change, plus a
  `RETRY_DOC_IDS` / FORCE_REPROCESS backfill if their documents are wanted as core.
- **Rows deleted by mistake.** Recovery: the JSON backup and Sheets version history.
