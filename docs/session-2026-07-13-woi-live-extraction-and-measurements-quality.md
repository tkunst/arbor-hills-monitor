# Session: 2026-07-13 — WOI live extraction wired in, historical reports re-extracted, Measurements data-quality review

One-line: the WOI table parser is now wired into the live pipeline and both
historical WOI Status Reports were re-extracted into the case file (methane
promoted to a named metric, CO kept); a review of the Measurements tab surfaced
three data-quality projects, now captured on the roadmap.

Base at session start: `662d592`. All commits below are this session.

## 1. WOI auto-routing — PR #9 (`87e5bb8` + `114705d`)

Ran the overnight-coder loop against `docs/handoffs/woi-auto-routing.md`. New
`woi_router.py` wires the already-tested `woi_table_parser` into `watcher.py` +
`backfill.py`, ABOVE `parse_document` (Decode base stays domain-agnostic). On a
detected WOI report it REPLACES `parsed.measurements` with the exhaustive set
before `is_urgent`/`write_document`, so a measured ≥145 °F reading buried past the
keyword window now lands in `measurements[]` and fires the same-day alert (it
didn't before). New on-demand `WOI Well Summary` tab; ships **on** behind
`woi.auto_route: true` (rollback lever, not an enable gate).

Key decisions / findings baked in:

- **Detection is content-based, NOT filename-based.** The real reports are filed
  under generic nSITE names (`nForm Document` / `Site`) — a filename gate would
  never fire. Detector = "Gas Extraction Report" header + page count. Deliberate,
  specimen-verified departure from the handoff's proposed name signal.
- **Two adversarial-review safety rules** (the advisor caught both):
  1. *Data-loss guard* — replace only if the parse yields ≥50 valid readings
     (real report ~14k; misdetection ~0), else keep the generic set + log.
  2. *Peak-temp preservation* — always keep the peak as-found temperature, so on
     a cool report `is_urgent` never falls back to regexing the 155/180 °F HOV
     permitted ceilings out of `full_text` and false-firing (ADR 004 conflation).
- Verified against the real 181-page report before merge; `/review` +
  `/security-review` clean.

## 2. FORCE_REPROCESS_DOC_IDS — PR #10 (`e0c7e82` + `d9e7404`)

Tooling to cleanly re-extract already-`processed` docs (the two historical WOI
reports) through the new route. `RETRY_POISONED`/`RETRY_DOC_IDS` never re-touch a
`processed` doc; `FORCE_REPROCESS_DOC_IDS` is the one override that does.

- **Clean, not additive:** `sheet_writer.purge_doc_rows()` removes a doc's stale
  rows (matched by doc_id in the row's Link) from Historical/New/Evidence/
  Measurements/WOI-Summary BEFORE re-writing.
- **Surgical:** a force run processes ONLY the named docs (never the backlog —
  backfill sends no alerts, so silent co-processing is the foot-gun that disabled
  the nightly schedule).
- **Dry-run by default:** `FORCE_REPROCESS_APPLY` required to mutate; a pure
  dry-run returns before the rebuilds (changes nothing).
- Derived tabs (`All Evidence by Risk`, `Risk Register`) are recomputed on a
  force-apply (not purged); `_state` is append-only (superseded); **`Archived
  PDFs` is intentionally kept** (it indexes the unchanged Drive mirror).

## 3. MMPC "activation pending" correction — `78b4dc8` (direct to main)

A prior session had reported Mirror D "not activated (secret + enabled:true
pending)." **That was false.** Verified: the `GOAUTH_MMPC_FOLDER_ID` secret was
set 2026-07-11 and `mmpc_archive.enabled: true` is live (a scheduled
`mmpc-archive` run succeeded 2026-07-13 12:36 UTC — runtime proof). Corrected the
stale pending-TODO framing in `CLAUDE.md`, `README.md`, ADR 010 (status line),
and `config.yml`.

## 4. Harmless downloadpdf in-browser error — `c6cb1a4` (direct to main)

Trisha hit an ASP.NET "Server Error in '/ncore' Application" page when clicking a
case-file Link, but the file still downloaded. Verified a direct server-side GET
returns a clean 200 `application/pdf` (the valid 37 MB / 181 pp file) on plain,
ranged, and HEAD requests — the error is an intermittent EGLE-portal quirk that
does not affect the download or the monitor. Documented in `README.md` + a
comment in `nsite_client.py`.

## 5. Methane metric + CO routing — PR #11 (`2cd3c93`)

- **Methane is now a first-class metric** (`metric="methane"`), not `other`.
  `woi_table_parser.to_measurements` emits it; added to the ADR-004 enum + the
  classifier prompt (so the generic model path is consistent, reserving `other`
  for genuinely other substances like NMOC/H2S/benzene).
- **CO is kept.** `woi_router` routes the Attachment-2 CO series
  (`parse_co_data`), emitted for every reading but AFTER the temperature-anchor
  guard (CO can't substitute for a missing temperature).

## 6. LIVE DATA OPERATION — re-extracted both historical WOI reports

Run via the **backfill `workflow_dispatch`** (`FORCE_REPROCESS_DOC_IDS` +
`FORCE_REPROCESS_APPLY=true`) — the local `ANTHROPIC_API_KEY` in `.env` is
invalid (401), so classification must run on the CI runner with the GitHub
Secret. (A local attempt failed cleanly at the Anthropic call — purge happens
only after a successful parse, so nothing was damaged; the failed attempts left
harmless error strikes that the later success cleared.)

- **181pp** (`7022559137978826651`, 2025 1st semi-annual): 18 → **944**
  measurements (283 each temperature/oxygen/methane + **95 CO**).
- **323pp** — filed TWICE. Both `7894782240984169987` and
  `-8194605618676510356` are the same report (323 pp, 17,998 readings, 100%
  valid). Re-extracted **one** (`7894…`) → **936** (297×3 + **45 CO**); **purged
  the duplicate** (`-8194…`, 13 rows) so wells aren't double-counted. The
  duplicate now has 0 rows in every human tab, present only in `Archived PDFs`
  (kept mirror) + `_state` (log).
- `WOI Well Summary` tab: **883 wells**; hottest `AHW272R4 = 177 °F`. Measurements
  tab: **7,461 → 9,303** rows. Zero `other` among the re-extracted rows.

## 7. Measurements data-quality review → three roadmap projects

Investigating the tab surfaced three related issues (all now in
`docs/roadmap.md`, all **draft-for-review-before-building**):

- **Metric taxonomy** (`4137df7`) — `other` is a grab-bag of ~30-40 substances
  (H2S, PFAS at `ng/L`, metals, wastewater params, NOx, SO2, …). The **unit does
  not identify the metric** (`%` = methane/O2/gas-comp/efficiency). Decompose by
  substance (model-classified, not keyword rules — the NMOC-vs-methane substring
  trap proved keywords are brittle), with an `event`/`status` bucket for
  non-readings.
- **`location_type`** (`30b5b2e`) — `well_id` conflates gas wells, monitoring/
  purge wells, outfalls, and perimeter stations. Metric type and location are
  ORTHOGONAL (42% have a well_id, 47% are well-metrics, and they don't line up
  because e.g. effluent temperature has no well). A `location_type` axis
  (gas_well / monitoring_well / outfall / perimeter_station / surface_ambient)
  would make the tab queryable by both *what* and *where*.
- **Well-ID normalization** (`f823648`) — the same well is fragmented across IDs:
  `AHW272R4` (hottest, 165 rows) also appears as `272R4` (2 rows); ~13 short-forms
  map to full wells. Needs a **curated** alias map (NOT "prepend AH" — most non-AH
  ids are flares/outfalls/GC-points/QA-blanks). Hook exists:
  `woi_table_parser.canonicalize(well_id, alias_map)`, empty in the live pipeline.

## State / pending

- **Everything code is merged to `main` and green** (`f823648` is HEAD). Nothing
  uncommitted.
- **Live monitoring going forward:** future WOI reports auto-extract (methane +
  CO, exhaustive). No manual step remains for new reports.
- **Open (roadmap, not started):** the three data-quality projects above. The
  historical methane relabel (task tracker #13) is folded into the metric
  taxonomy reclassification — the ~319 historical `other`-methane rows are still
  labeled `other` and will be fixed there.
- **Not a concern:** an earlier scheduled `daily` run and one PR CI check hit
  transient GitHub-infra failures (`Service Unavailable` / lychee binary
  download) — unrelated to any code; they self-heal / re-run green.
