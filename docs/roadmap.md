# Roadmap

Larger, not-yet-scheduled improvements to the monitor. Unlike
`docs/decisions/` (ADRs — decisions already made and built) or
`README.md`'s "Residual risks (accepted)" (known gaps deliberately not
fixed), this file is for real, scoped-but-not-started projects.

## Metric taxonomy: decompose the `other` bucket into named metrics

**Problem.** The ADR-004 `metric` field has a handful of named metrics
(`temperature`, `carbon_monoxide`, `oxygen`, `methane`) plus `other`. A
2026-07-13 review of the live Measurements tab found `other` had become a
grab-bag of ~30-40 distinct substances/measurements — hydrogen sulfide, PFAS
(at `ng/L`), metals (arsenic/mercury/lead/…), wastewater parameters
(TSS/BOD/pH/ammonia/phosphorus), NOx, SO2, cyanide, hardness, VOCs, odor,
liquid level, pressure, flow, and more. Crucially, **the unit does not identify
the metric** (`%` is used for methane, O₂, gas composition, and efficiency
alike; `ppm` for CO, methane, H2S, VOCs), so classification must go by
SUBSTANCE, not unit.

**What it would take.** Expand the named-metric vocabulary to ~40 metrics, add
them to the classifier prompt + the `Measurement` enum, and reclassify. The
reliable classifier is the MODEL itself (it maps "PFHxS → pfas", "NOx →
nitrogen_oxides" accurately) — NOT brittle keyword rules (the NMOC-vs-methane
substring trap proved that). For historical rows, reclassify the distinct
`(note, unit)` pairs once (cheap, accurate) rather than re-parsing every PDF.
Keep a small `event`/`status` bucket for rows that are not chemical readings
(a well "screen submerged", an operational "gas shortfall").

**Companion idea — a `location_type` field.** A 2026-07-13 cross-tab of `well_id`
vs. `metric` on the live tab found that the substance and the location are
ORTHOGONAL, and that today's `well_id` column silently conflates several kinds of
location: gas wells (`AHW272R4`), purge/monitoring wells (`Purge Well 4`),
effluent outfalls (`Effluent-001`), perimeter monitoring stations (`MS-2`), and
surface/ambient readings with no location at all. Consequences seen in the data:
42% of rows carry a `well_id` while 47% are "well metrics" (temperature/O2/
methane/CO), and they DON'T line up — e.g. `Final Effluent temperature` and
`Dissolved Oxygen` are well-metrics with no well (they're measured at an
outfall), while `AHW263R5 | Hydrogen (H2)` is a non-gas substance measured AT a
well. So "% with a well_id" isn't a meaningful single number today. Consider
adding a `location_type` (gas_well / monitoring_well / outfall /
perimeter_station / surface_ambient / none) alongside the substance metric, so
the case file is queryable by BOTH *what* (substance) and *where* (location) —
the two independent axes this data actually has. Design it together with the
metric taxonomy.

**Scope questions to settle first.** Granularity (separate arsenic/mercury/lead
vs. one `metal`?); how to treat permitted-limit rows (basis already handles it);
the residual that genuinely can't be a substance metric; the `location_type`
axis above; and whether a full overnight re-extract is warranted vs. a targeted
note-reclassification pass. Draft the metric list for review BEFORE building.
See ADR 004 "Metric vocabulary growth".

## Well-ID normalization (short↔full + cross-report renames)

**Problem.** The same physical well appears in the Measurements tab under
different identifiers, which FRAGMENTS its per-well history — the exact failure
mode ADR 004 exists to prevent. Two causes seen in the live data (2026-07-13):

- **Short vs. full form.** Structured WOI table reports use the canonical ID
  (`AHWW502R`); inspection/field reports refer to the same well by its short
  number ("well 502R"), and the generic model path extracts it as-written
  (`502R`). Concrete hit on the flagship well: `AHW272R4` (hottest, 177 °F) has
  **165 rows** under the full ID and **2 more** under `272R4` — a filter on
  `AHW272R4` silently misses them. ~13 distinct short-forms map to full wells
  (`502R`→`AHWW502R`, `286R`→`AHW286R`, `290`→`AHWW0290`, …), ~50 rows total.
- **Cross-report renames.** A well is abandoned and reissued under a new id
  (e.g. `AHW0177`→`AHWW177R`; EGLE issued a formal WOI-id update 6/22/2023) — the
  case ADR 005's `canonicalize(well_id, alias_map)` was designed for.

**Caveat — do NOT use a blunt rule.** Most non-`AH` identifiers are NOT wells:
flares (`EUENCLOSEDFLARE2`, `Flare_391`), outfalls (`DISCHARGE 001A`,
`Effluent-001A`), gas-collection points (`GC-1`, `GP-14`), QA samples
(`DUPLICATE`, `FIELD BLANK`), and `EW`-prefixed extraction wells. "Prepend `AH`"
would corrupt these. Normalization must be a **curated alias map**, human-checked.

**What it would take.** Build the curated alias map (short↔full + cross-report
renames) and apply it across BOTH paths — the WOI parser already has the hook
(`canonicalize(well_id, alias_map)`, currently passed an empty map in the live
pipeline), and the generic model path needs the same normalization. Likely
cleanest as a normalization step where measurements are written
(`sheet_writer`), so every source is covered, plus a one-time pass over existing
rows. Relates to the `location_type` idea above (both are about making the
Measurements tab cleanly queryable by well). Scope questions: where to normalize
(parse-time vs. write-time vs. a periodic sweep); how the curated map is
maintained and reviewed; and whether to normalize non-well location IDs too.

## Vision-based classification for image-only content

**Status:** proposed 2026-07-11/12. Not started.

**The gap:** `egle_doc_parser._classify_with_claude()` is text-only — it
sends Claude the document's extracted text (`page.get_text()`, after OCR
where applicable), never the page images themselves. For a page that's a
genuine photo (a site inspection photo, not a scanned text document), OCR
correctly finds zero text — there's no writing to read — so the pipeline
has *no* signal about what the photo actually shows. This isn't a bug
anywhere in the extraction path; it's a structural limit of a text-only
classifier.

**Where this showed up:** doc `1681010528757159679` ("Arbor Hills
Compost.msg", Arbor Hills Remediation Area / WRD-Groundwater, filed
2018-06-22) — a `.msg` with 4 real site-inspection photos
(`IMG_0682.JPG`–`IMG_0685.JPG`) from a storm-water review. Confirmed by
direct testing (2026-07-12): `poison_doc_extractor.py` (ADR 011) embeds all
4 photos correctly as real image pages in the synthesized PDF — the
extraction side works. But since Claude never sees the pixels, the
resulting Sheet summary can only report metadata (sender, date, "4 photos
attached"), not what the photos actually depict. This was investigated as
a candidate fix for that one doc and found to be a dead end for a targeted
retry — see `docs/session-2026-07-11-mmpc-nsite-extraction-overnight-worker.md`
for the full investigation. Reprocessing the doc changes nothing; only
giving the classifier vision would.

**What it would take:** for pages `classify()` already flags as image-only
(`needs_ocr`/`likely` with zero OCR text recovered, or specifically pages
PyMuPDF reports as image-only after OCR), send the page render (not just
`get_text()`) to Claude via a vision-capable call instead of, or alongside,
the current text-only path. Scope questions to work through before
building: page-image extraction/encoding, added token/cost per image page,
whether this applies to ALL docs or only ones that OCR flags as
image-heavy, and whether the resulting description should be tagged
distinctly (e.g. `severity`/`key_data_point` sourced from vision vs. text)
so a human reviewer knows which is which. A real, separate project — not a
bug fix, and not scoped in detail yet.

**Why not build it now:** deliberately out of scope for the ADR
011/overnight-worker session that surfaced it — accepted as a known gap
for now (see the doc's Sheet row, which already honestly flags the photos
as unassessed rather than guessing).
