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

24 runtime modules · 8 logical data stores · 61 edges · 3 persona flows.
`watcher.py` is the orchestration hub (star topology, daily SPOF); the
Conservancy Google Sheet is the single read+write data spine. Full architect
notes render in the viewer's **Observations** panel (and in a regenerated
`topology.json` → `observations`).
