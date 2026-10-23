# Topology map

Dependency & topology map of the monitor — the picture an engineer wants before
touching anything. Generated, not hand-maintained: re-run the two scripts here
after any structural change.

## View it

Open **`TOPOLOGY.html`** in a browser (self-contained, works offline). Try:

- **Search** a module (e.g. `watcher`), click it to see its connections.
- Toggle edge kinds (call / dispatch / read / write).
- Pick a **persona flow** from the walkthrough dropdown to play it as a numbered path.

Small exportable diagrams for docs/PRs: `call-graph.mmd` (domain-level calls),
`data-lineage.mmd` (module → data store, read vs write), `critical-path.mmd`
(the primary flow).

## Regenerate

```bash
python3 docs/topology/extract_topology.py   # -> topology.json (+ prints a summary)
python3 docs/topology/gen_mermaid.py         # -> the three .mmd files
# then re-inject topology.json into the viewer template (see the plugin command)
```

`topology.json` is intentionally **not committed** — the repo's `data-guard` CI
bans committed `*.json` (data-file hygiene), and the file is regenerable from the
script above. Its data is already inlined into the self-contained `TOPOLOGY.html`,
so the viewer needs nothing else; only re-running `gen_mermaid.py` needs the JSON,
which the extract step above produces.

`extract_topology.py` derives three layers, so a reviewer can tell mechanical
facts from analyst judgement:

1. **Auto from source** (`ast`) — modules, LOC, import (call) graph.
2. **Auto from deployment config** (`.github/workflows/*.yml`) — entry points
   (a scheduled/dispatchable `run: python X.py` is this repo's cron/EXEC PGM).
3. **Curated but documented** (tables in the script) — domain grouping, the
   code↔store read/write edges, the config-resolved dispatch edges, the
   architect observations, and the persona flows. Datastore names are **logical
   only** — never raw config values / URLs / credentials.

## Snapshot

Regenerated 2026-10-23 (ADR 071) — adds `snapshot_exporter` (Persistence; a
write edge to the Drive archive, called by the ROP / MMD / nSITE Submissions /
PFAS watchers before their Sheet append). The PFAS persona flow gained a step
for the exporter.

Regenerated 2026-10-23 (coder:poison-import-move) — `nsite_client` now imports
`poison_doc_extractor` lazily inside `download_pdf()`, so the seven nSITE
profile watchers (K–Q) no longer load the PDF stack on import. The `ast`-derived
call graph still shows the `nsite_client → poison_doc_extractor` edge (it sees
function-level imports too); the Observations panel says to read it as
"reachable from `download_pdf()`", not "loaded on import". Also classified the
two Dataset Provenance modules (`dataset_provenance`, `sync_dataset_provenance`)
that PR #170 added without a `DOMAIN` entry, which had been failing the
extractor with a `KeyError`. 98 modules · 23 data stores · 353 edges.

Regenerated 2026-10-16 — every root and `scripts/` module is now classified in
`DOMAIN` (the extractor raises a `KeyError` on an unclassified module, so a new
module can't silently land in the wrong domain again). Adds Streams F–V, the
standalone jobs (EGLE newsroom, GFL hourly capture + monthly snapshot, Ridge Wood
re-verification), `doc_pipeline`, `nsite_watch_common`, the `gfl_air_*` split,
`owner_notices` / `review_flags`, the publishing scripts, and a new **Public Site
Publishing** domain. Entry points now also come from `python X.py` lines inside a
`run: |` block.

96 modules · 23 logical data stores · 345 edges · 5 persona flows · 65 entry
points. Hubs: `drive_client` / `config_loader` (42 importers each) and
`sheet_writer` (41); `doc_pipeline` is the one per-document path behind
`watcher` and `backfill`. The only dead-end candidate is `neshap_table_parser`
(a dataset tool imported only by tests). Full architect notes render in the
viewer's **Observations** panel.
