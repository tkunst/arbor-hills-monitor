# Overnight-coder handoff — archive GFL's Ridge Wood Elementary H2S monthly reports (Stream F)

*Staged 2026-07-14. New data source (data-sources doc row 8). Read
`docs/overnight-coder.md` first — this is a goal handed to that loop, not a new
procedure. The feasibility recon is already done (2026-07-14); its full write-up is
`Cowork-claude/documents/arbor-hills/source-docs/item-08-ridge-wood-h2s-how-to-get.md`.
Verdict: **automatable, document-archive shape** (monthly PDFs), not an API. Like
the GFL air handoff this ships `enabled: false` and is feasibility-gated, but the
gate is essentially cleared — you mainly re-confirm the reports still resolve.*

## Invocation

Point the loop at this file. Branch name suggestion: `ridgewood-h2s`.

## Goal

GFL operates an **H2S air monitor at Ridge Wood Elementary School** (Northville
Public Schools), installed December 2020 under a U.S. EPA agreement and run by
**Barr Engineering** (the same consultant behind the GFL perimeter ArcGIS feed,
Stream E / item 6). Barr posts **monthly H2S 24-hour-average data reports** to a
public site. This is the school-adjacent R3 (odor) / R4 (air) monitor — a
**separate** location from the perimeter fenceline stations in Stream E, and
complementary evidence, not a duplicate.

**Build a poller that mirrors each new monthly report to Drive and extracts its
24-hour-average H2S concentration(s) into the case file, alerting on a stated
exceedance.** This is the "new PDF appears → mirror + extract" shape (Mirror D /
`mmpc_archiver.py`), NOT the GFL cursor-poller shape — the data is monthly QA'd
documents, not a live numeric feed.

## Why this is a good overnight goal

- The repo already has the exact template: `mmpc_archiver.py` (ADR 010) mirrors
  newly-published PDFs from a public listing, deduped by a Sheet-derived id set,
  gated behind `mmpc_archive.enabled`. This is that pattern against a simpler,
  auth-free, monthly source.
- The reports are **born-digital text-layer PDFs** (verified) — no OCR needed, so
  extraction is a clean `egle_doc_parser`-style pass (or a small dedicated H2S
  extractor).
- It ships `enabled: false`, so it cannot affect the live monitor until a human
  step. Safe to build and merge autonomously.
- **High cross-value:** the monitor's own published action levels are
  **72 ppb (24-hr average)** and **750 ppb (15-minute average)** — the 72 ppb
  number independently confirms the H2S threshold shipped for Stream E (ADR 014),
  and 750 ppb is a new acute level worth carrying.

## Feasibility recon (already done — don't re-discover, just re-confirm)

Source page: `https://arborhillsmonitoring.com/Home/Ridgewood`.

- The page embeds a **list of every monthly report** (its `#pastreports` section):
  **66 reports** as of recon, **December 2020 → present**, one per month.
- Each report is a PDF at
  `https://www.arborhillsmonitoring.com/Files/YYYY-MM_Arbor Hills H2S Data_24-hour ave[_NNNN].pdf`.
  **Do not construct URLs:** the `_NNNN` suffix is an unpredictable cache-buster
  (random-looking; some months have **no** suffix), and the filename contains
  spaces (URL-encode `%20` when fetching). Scrape the `Files/*.pdf` links off the
  page and diff against what you've archived.
- Cadence: monthly, posted ~15–20 days after month-end. Most polls find nothing new.
- Report format (verified on a recent month): a ~2-page, ~3.5k-char **text-layer**
  PDF titled e.g. *"Ridge Wood Elementary Hydrogen Sulfide (H2S) Monitoring:
  24-hour average concentrations — April 2026"*, with sections Equipment Operation,
  Data Downloading, Meteorological Conditions, Maintenance, Audits, and **Data –
  Average Air Concentrations** (the month's 24-hr averages + any exceedance note).

**Re-confirm before building** (cheap): the report list still resolves off the page
and a recent PDF still carries a text layer. If the page has been restructured or a
recent report is now a scanned image, adapt (fall back to the OCR path) — do not
grind against a changed source; a documented STOP is valid.

## Approach (pinned — don't re-litigate)

1. **New client — `ridgewood_client.py`.** GET the Ridgewood page, scrape the
   `Files/*.pdf` report links, parse the `YYYY-MM` month from each filename. Pure +
   stdlib; raise a typed `RidgewoodFetchError` (never a silent `[]`) so a bad fetch
   isn't read as "no reports" — same posture as `pfas_client` / `mmpc_client`.
   Do **not** touch `egle_doc_parser.py` (keep the Decode base domain-agnostic) —
   but you MAY route the downloaded PDF *through* it for extraction, the way the
   watcher already calls `parse_document`.

2. **New archiver — `ridgewood_archiver.py`.** Model on `mmpc_archiver.py`: diff
   the scraped month-set against an append-only **"Ridge Wood Reports"** tab (dedup
   by `YYYY-MM`, the `mmpc_archived_file_ids` idiom — Sheet-derived, race-free,
   **not** `_meta`, since this is its own workflow). Download each new month's PDF,
   mirror it to Drive via `archive_client` (its own OAuth folder id — a new secret,
   flag it in "Before merging"), and record provenance (source URL, fetched-at,
   content hash) in the tab. Crash-safe order: Drive upload succeeds → then the Sheet
   row.

3. **Extraction → Measurements.** Extract the month's 24-hour-average H2S value(s)
   and write to the existing **Measurements** tab (do not invent a schema):
   `metric="hydrogen_sulfide"`, `unit="ppb"`, `basis="measured"`,
   `well_id="Ridge Wood Elementary"` (or the report's station id),
   `as_of_date=<report month>`, `note` attributing it to the **Barr/EPA-agreement
   monitor** (honest provenance — not GFL self-report, not an EGLE measurement).
   Alert (same-day, its own classifier — not the temperature `is_urgent`) if the
   report states a **24-hr exceedance of 72 ppb** or a **15-min exceedance of
   750 ppb**; thresholds config-driven, defaulting to those published levels.

4. **Config — `ridgewood: { enabled: false, ... }`** in `config.yml`, matching the
   `mmpc_archive` / `pfas` blocks (copy the 3-step activation comment). No-op until
   `enabled: true` is on `main` — a human step, never this loop's.

5. **Workflow.** A scheduled `ridgewood.yml` (its own concurrency group, staggered
   off the 3–8am cluster), a no-op while disabled. Monthly cadence, but a daily run
   that usually finds nothing is fine and simplest.

6. **Tests (hermetic, mocked, no committed data).** Small synthetic fixtures: the
   page HTML with a few `Files/*.pdf` links (to test scrape + `YYYY-MM` parse + the
   cache-buster/space handling), and a tiny synthetic monthly report (built
   in-process like `tests/conftest.py` does — never a real captured PDF/HTML;
   `data-guard` forbids committed data). Cover: link scrape, month dedup vs the tab,
   the 24-hr-average → Measurement mapping, an exceedance firing the alert, and the
   `enabled:false` no-op gate.

7. **Docs (same PR).** New ADR (next number after 014 — "GFL Ridge Wood Elementary
   H2S, Stream F"): the source, the monthly-PDF mechanism (and why scrape-the-list,
   not construct-URLs), the schema mapping, the 72/750 ppb thresholds + their
   source, the enabled:false step, and the item-6 cross-confirmation of 72 ppb. Add
   the stream to `README.md` (Streams table + a residual-risk line: "rides an
   undocumented public report list; if the page restructures the fetch fails
   loudly"). Update `CLAUDE.md`'s architecture list. Regenerate topology.

## Existing pieces you're building on (don't rebuild)

- `mmpc_archiver.py` / `mmpc_client.py` + `mmpc_archived_file_ids()` — the exact
  new-PDF-mirror template (ADR 010).
- `archive_client.py` — Drive upload (OAuth folder id).
- `egle_doc_parser.parse_document` — text extraction/classification you can reuse
  for the report PDF (it is text-layer, so no OCR needed).
- `sheet_writer.measurement_rows` + `TAB_MEASUREMENTS` — the measurement write path.
- The GFL air stream (Stream E, ADR 014) — the sibling H2S stream; keep the two as
  distinct locations/tabs and cross-reference their thresholds.

## Adversarial review (mitigations to build in)

- **Show-stopper (none expected):** the source is a plain public page with directly
  linked PDFs. Recovery if the recon has gone stale by build time: adapt or STOP
  with a doc-PR (valid outcome).
- **Manageable — the page markup / file path changes.** Mitigation: scrape fails
  loudly (`RidgewoodFetchError` aborts the run); log the report count per poll so a
  drop to zero is visible, not silent.
- **Manageable — a future report is a scanned image.** Mitigation: fall back to the
  repo's OCR path (`poison_doc_extractor` / `egle_doc_parser`'s OCR) rather than
  dropping the month.
- **Provenance (this is evidence).** Record source URL + fetched-at + content hash
  per mirrored report (ties into data-sources item 9's provenance requirement).
- **Overlap with Stream E — don't double-count.** Different location (school vs
  perimeter); keep separate streams/tabs.

## Definition of done

Green `pytest -q`; a new `ridgewood` stream that is a **no-op while
`enabled:false`** (client + archiver + workflow + config + tests); the new ADR +
README/CLAUDE.md/topology in the same PR; PR merged per overnight-coder Step 8 with
a closing comment stating **(a)** that it's disabled and **(b)** the two human
steps left (provision the Drive folder secret; set `enabled:true`) — plus the nice
finding that this source's 72 ppb action level confirms Stream E's threshold. If
the recon has gone stale and the source can't be polled, the done state is a
**draft PR** with the finding, not a forced green build.
