# Overnight-coder handoff — auto-route WOI Status Reports through `woi_table_parser`

*Staged 2026-07-12. Resolves ADR 005's deferred "Integration (follow-up)" and
`docs/business-rules.md` Note 6. Read `docs/overnight-coder.md` first — this is a
goal handed to that loop, not a new procedure.*

## Invocation

Point the loop at this file (or paste the **Goal** below as the `/loop` goal).
Branch name suggestion: `woi-auto-routing`.

---

## Goal

The semi-annual **WOI (Wells of Interest) Status Reports** are the densest R8
(overheating / ETLF) evidence in the system — 180–320-page per-well monitoring
tables. Today the live pipeline (`watcher.py` / `backfill.py`) parses them via
the **generic** path (`egle_doc_parser.parse_document`), which keyword-windows
any doc over `large_doc_page_threshold` (30 pp): it sends the classifier the
cover + up to 10 keyword-matched pages (~11 of 181), and PyMuPDF returns those
tables as "table soup." Result: **<5% of ~14,000 readings captured**, and — the
part that matters — a real measured temperature on a page with no signal keyword
is **never seen by the classifier**, so no measurement is emitted for it.

That is not just an archive gap. `email_alerts.is_urgent` fires the same-day
urgent alert off a **measured** temperature ≥ `measured_temp_urgent_f` (145 °F)
found in `parsed.measurements` (`email_alerts.py:41-53, 63-65`). Under
windowing, a 150 °F well buried on page 140 produces no measurement, so **no
urgent alert ever fires** — the monitor silently misses a MACT exceedance. The
free-text fallback doesn't save it (it's suppressed once any temp is emitted, and
`full_text` under windowing doesn't contain page 140 anyway).

The exhaustive, deterministic fix already exists and is unit-tested:
`woi_table_parser.py` (ADR 005) parses **all** pages with a line state-machine,
validates every reading against the CH4+CO2+O2+Balance ≈ 100 gate (99% of 13,976
readings passed on the real 2025 report), and emits ADR-004 measurements. It is
imported today **only** by `scripts/woi_summary.py` / `scripts/co_summary.py` —
run by hand. **Wire it into the live pipeline** so WOI reports are extracted
exhaustively and automatically.

This closes two gaps at once: **evidence-archive completeness** and — the reason
it's worth doing — **alerting completeness**.

## Why this is a good overnight goal

The hard, format-sensitive part (parsing 180-page tables reliably) is **already
built and de-risked** — `woi_table_parser.py` + `tests/test_woi_parser.py` (16
tests). What's left is integration glue. Estimated ~80–85% done.

## This is a LIVE-PATH change — the real-specimen gate applies

Per `docs/overnight-coder.md` Step 3: this changes a path that runs against real
nSITE docs on the very next scheduled run. It has **no `enabled:false` new-source
flag to hide behind** in the overnight-coder sense. **You must verify it against
at least one real WOI Status Report before merging autonomously.** A real
specimen IS obtainable — the WOI reports are already in the nSITE document list;
download one via `nsite_client` (the same path the pipeline uses). If for any
reason a real report can't be downloaded and run end-to-end tonight, **do not
merge — that's a Step-3 stop** (draft PR, leave for Trisha). Do **not** commit
the PDF (CLAUDE.md forbidden pattern; `data-guard` CI enforces it) — download to
the scratchpad, verify, delete.

**Specimen check to run:** download a WOI report → `parse_gas_extraction` →
confirm thousands of readings at ~99% validity → confirm `per_well_summary`
produces the hottest-well rows → confirm that if the report contains a ≥145 °F
as-found reading, the wired `is_urgent` would now return `True` (it does not
today).

## Approach (pinned — don't re-litigate these; they're the design decisions)

1. **Detector — new, small, pure, tested.** Add `is_woi_report(pdf_path,
   metadata) -> bool`. Require **multiple** signals so it's conservative:
   nSITE `type_name`/`document_name` looks like a WOI/Status Report **and** the
   text carries the Attachment-1 "Gas Extraction Report" header **and**
   page_count is over the large-doc threshold. Over-triggering is harmless (the
   sum-to-100 gate returns ~0 valid readings on a non-WOI doc); under-triggering
   silently reopens the gap, so log the decision.

2. **Route ABOVE `parse_document`, never inside it.** `egle_doc_parser.py` is the
   domain-agnostic Decode reuse surface — do **not** hardcode "Gas Extraction
   Report" or WOI logic there. Put the dispatch at the call sites (a small shared
   helper, e.g. `woi_router.py`, imported by both `watcher.py` and
   `backfill.py`).

3. **Augment, don't replace.** Still call `parse_document` for the five narrative
   fields (summary / key_data_point / doc_type / risks / severity). Then, for a
   WOI-shaped doc, **replace `parsed.measurements`** with `woi_table_parser`'s
   exhaustive set. In `watcher.py` this replacement must happen **before** the
   `is_urgent(parsed, cfg)` call (so a page-140 exceedance actually alerts) and
   before `write_document`. Bonus: this removes the `classification_max_tokens`
   truncation risk that exists today because the model tries to emit thousands of
   measurements (`config.yml:29`).

4. **Volume policy (PINNED — do not dump 14k rows into the Sheet).**
   - **Primary artifact:** a new `TAB_WOI_SUMMARY` tab (match the existing
     `TAB_*` / `_TAB_HEADERS` / `ensure_tabs` pattern in `sheet_writer.py`),
     written from `per_well_summary()` — ~one row per well (hundreds), the
     hand-to-EGLE artifact. Consider a companion CO summary from
     `per_well_co_summary()` if cheap; otherwise note it as a follow-up.
   - **Measurements tab:** emit as measurements only the **non-ADJ readings ≥ 131 °F**
     (EPA watch band and up). That keeps the alert-relevant + watch-band evidence
     present for `is_urgent` and the Measurements tab, at ~hundreds of rows, not
     ~21k. The complete ~14k-reading dump stays reproducible via
     `scripts/woi_summary.py` — that's fine, it's not the Sheet's job.

5. **Config kill-switch (rollback lever).** Add `woi: { auto_route: true }` to
   `config.yml` (default **on** — this is a live-path fix we want active), gating
   the dispatch. Matches the repo's config-driven style and gives a one-line
   revert if the detector or volume ever misbehaves: flip to `false`, monitor
   falls back to windowing. (Note: this is a rollback flag, not the
   new-source `enabled:false` pattern — the feature ships **on**.)

6. **Tests (hermetic, mocked, no secrets — `pytest -q` green).** Cover: the
   detector (WOI-shaped vs not); the dispatcher (a WOI-shaped doc gets its
   `measurements` replaced by the table-parser set, a normal doc is untouched);
   `woi_summary_rows()` shape; and that a ≥145 °F as-found reading drives
   `is_urgent` True through the wired path. Reuse the synthetic linearized-line
   fixtures already in `tests/test_woi_parser.py`.

7. **Docs (part of the same PR — Step 8 requires ADRs reflect final state).**
   Resolve ADR 005's "Integration (follow-up, not in this ADR)" section (mark it
   done, dated, pointing at this change). Flip `docs/business-rules.md` **Note 6**
   from open Eng-question to resolved. Regenerate the topology if the extractor's
   "WOI cluster … disconnected" note (`docs/topology/extract_topology.py:163`)
   changes — the cluster is no longer disconnected once this lands.

## Existing pieces you're building on (don't rebuild)

- `woi_table_parser.parse_gas_extraction / extract_woi_well_list /
  per_well_summary / to_measurements / parse_co_data / co_to_measurements /
  per_well_co_summary` — all done, all tested.
- `sheet_writer.measurement_rows` + `TAB_MEASUREMENTS` — the measurement write
  path already exists; you're adding a summary tab beside it, not inventing the
  schema.
- `is_urgent` already prefers `measurements[]` and correctly excludes permitted
  ceilings — **no change needed there**; feeding it the exhaustive set is enough.
- Precedent for wiring a specialized extractor into the live path:
  `poison_doc_extractor` → `nsite_client.download_pdf` (ADR 011).
- `RETRY_DOC_IDS` (`backfill.py`, ADR 011) — the mechanism to re-extract the two
  already-processed WOI reports after this merges (see follow-up below).

## Adversarial review (mitigations to build in, not just note)

- **Detector under-triggers → gap silently persists.** Mitigation: multi-signal
  detector + a log line naming the routing decision; the WOI Summary tab being
  empty for a report that should have one is the visible tell.
- **Sheet cell/quota blow-up from raw readings.** Mitigation: the pinned volume
  policy (summary tab + ≥131 °F only). Batch the tab write like the existing
  `append_rows` chunketing.
- **Mocked-green but real EGLE format drift silently under-extracts.**
  Mitigation: the real-specimen gate above; and log reading-count + validity-rate
  per WOI doc so a future format change shows up as a cliff, not silence.
- **Re-extracting historical reports fires stale urgent alerts.** Already safe:
  `backfill.py` never calls `is_urgent`. Keep it that way.
- **Residual risk (accept + note in PR):** a future EGLE report-format change
  under-extracts until someone notices the validity-rate drop. No auto-recovery
  beyond the log signal + the `woi.auto_route:false` kill-switch.

## Follow-up (NOT part of the autonomous merge — flag it for Trisha)

The two existing WOI reports are already `processed` via the old windowed path.
Re-extracting them through the new route is a **manual** `RETRY_DOC_IDS` /
force-reprocess trigger (they're `processed`, and `RETRY_DOC_IDS` by design never
re-touches a `processed` doc — same wrinkle the ADR 011 backfill hit; see that
session's postscript 2). Identify the two doc_ids, confirm against the live tab
first, and note whether a `FORCE_REPROCESS_DOC_IDS` path is needed. Leave the
trigger to Trisha — future reports flow through the new route automatically.

## Definition of done

Green `pytest -q`; a real WOI report verified end-to-end (readings extracted,
summary tab populated, a ≥145 °F reading would alert); ADR 005 + Note 6 updated
in the same PR; `woi.auto_route` defaulting on; PR merged per overnight-coder
Step 8 with a legible closing comment that names the one manual follow-up
(re-extract the two historical reports).
