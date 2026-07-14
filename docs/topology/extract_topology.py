#!/usr/bin/env python3
"""
extract_topology.py — dependency & topology map for arbor-hills-monitor.

Re-runnable, auditable. Emits analysis/topology.json (schema consumed by the
interactive TOPOLOGY.html viewer) and prints a human summary.

Three layers, by how each fact is derived — so a reviewer can tell what is
mechanical vs. what is analyst judgement:

  1. AUTO from source (Python `ast`): the module inventory, LOC, and the
     import-based CALL graph. Ground truth; re-derives on every run.
  2. AUTO from deployment config (.github/workflows/*.yml): the ENTRY POINTS.
     A GitHub Actions `schedule:`/`workflow_dispatch:` + `run: python X.py` is
     this repo's analogue of a JCL EXEC PGM / cron table — top-level modules
     look unreachable without it. Parsed here, not assumed.
  3. CURATED but documented (the tables below): domain grouping, the code<->store
     data edges, the two config-resolved DISPATCH edges, observations, and the
     persona flows. These need semantic knowledge a regex can't derive (which
     client performs which I/O; which fetchers a config list selects). Every
     table says where its truth comes from. Datastore names are LOGICAL only —
     never raw config values / URLs / credentials (this file is committed).

Usage:  python analysis/extract_topology.py
"""
from __future__ import annotations

import ast
import glob
import json
import os
import re

def _repo_root(start):
    """Walk up from this script to the repo root (dir holding .git), so the
    extractor works regardless of where under the repo it is committed."""
    d = os.path.dirname(os.path.abspath(start))
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))  # fallback


REPO = _repo_root(__file__)
OUTDIR = os.path.dirname(os.path.abspath(__file__))   # emit outputs next to this script
OUT = os.path.join(OUTDIR, "topology.json")
SYSTEM = "Arbor Hills Landfill Monitor"

# --- Layer 3 curated tables (documented derivations) -----------------------

# Domain grouping. Source of truth: each module's own header docstring + its role
# in the daily run (verified by reading the modules). Leaf `kind` is "module"
# for importable runtime code, "job" for scripts/ CLI/ops tools.
DOMAIN = {
    # Orchestration / entry-point runners
    "watcher": "orchestration", "backfill": "orchestration", "archiver": "orchestration",
    "wds_archiver": "orchestration",   # nightly WDS page-snapshot runner (ADR 009)
    "mmpc_archiver": "orchestration",  # nightly MMPC PDF mirror — Mirror D (ADR 010)
    "pfas_watcher": "orchestration",   # daily PFAS page-watch runner (ADR 012)
    "gfl_air_watcher": "orchestration",# daily GFL perimeter-air poll — Stream E (ADR 014)
    # Ingestion — one client per external source (nSITE, WDS, MMPC, PFAS, GFL air)
    "nsite_client": "ingestion", "mmpc_client": "ingestion",
    "wds_watcher": "ingestion", "wds_client": "ingestion",
    "pfas_client": "ingestion",        # PFAS page fetch + content-hash normalize (ADR 012)
    "gfl_air_client": "ingestion",     # GFL ArcGIS FeatureServer fetch + ADR-004 mapping (ADR 014)
    # Document processing & risk
    "egle_doc_parser": "processing", "risk_register": "processing",
    "retry_policy": "processing", "woi_table_parser": "processing",
    "woi_router": "processing",         # routes WOI Status Reports to woi_table_parser (ADR 005)
    "poison_doc_extractor": "processing",  # .msg/.docx -> synthesized PDF (ADR 011)
    # Persistence & notification
    "sheet_writer": "persistence", "drive_client": "persistence",
    "archive_client": "persistence", "email_alerts": "persistence",
    # Config
    "config_loader": "config",
    # Ops / manual jobs (scripts/)
    "seed_wds_state": "ops", "verify_state": "ops", "smoke_one": "ops",
    "oauth_setup": "ops", "co_summary": "ops", "woi_summary": "ops",
    "dump_wds_historical": "ops",      # one-off WDS backfill dump (ADR 009)
}
DOMAIN_NAME = {
    "orchestration": "Orchestration / Runners",
    "ingestion": "Ingestion (EGLE / MMPC sources)",
    "processing": "Document Processing & Risk",
    "persistence": "Persistence & Notification",
    "config": "Config",
    "ops": "Ops / Manual Jobs",
    "data": "Data stores",
}
JOB_MODULES = {"seed_wds_state", "verify_state", "smoke_one", "oauth_setup",
               "co_summary", "woi_summary", "dump_wds_historical"}

# Data stores — LOGICAL identifiers only. The physical binding (Sheet ID, SA key,
# SMTP host, API key) is the external code<->storage join: it lives in GitHub
# Secrets + local .env (GSHEET_ID / GDRIVE_SA_KEY / SMTP_* / ANTHROPIC_API_KEY),
# none of it committed. We deliberately record only the logical name here.
DATASTORES = [
    ("ds:conservancy-sheet", "Conservancy Case-File Sheet"),
    ("ds:drive-archive", "Google Drive PDF Archive"),
    ("ds:nsite", "EGLE nSITE Portal (Air)"),
    ("ds:wds", "EGLE WDS Portal (Solid Waste)"),
    ("ds:mmpc", "Washtenaw County MMPC Site"),
    ("ds:pfas", "EGLE PFAS Investigation Pages (michigan.gov)"),
    ("ds:gfl-air", "GFL Perimeter Air ArcGIS FeatureServer (Barr, public)"),
    ("ds:smtp", "Email Recipients (SMTP)"),
    ("ds:anthropic", "Anthropic Claude API"),
    ("ds:config", "config.yml (risk register + settings)"),
]
# code<->store read/write edges, attributed to the module that issues the I/O
# (the client that actually calls the API), verified by reading each module.
DATA_EDGES = [
    ("sheet_writer", "ds:conservancy-sheet", "read"),
    ("sheet_writer", "ds:conservancy-sheet", "write"),
    ("archive_client", "ds:drive-archive", "write"),
    ("nsite_client", "ds:nsite", "read"),
    ("wds_client", "ds:wds", "read"),
    ("mmpc_client", "ds:mmpc", "read"),            # CivicClerk JSON API (Mirror D)
    ("pfas_client", "ds:pfas", "read"),            # fetches EGLE PFAS pages, hashes <main>
    ("gfl_air_client", "ds:gfl-air", "read"),      # GET the ArcGIS FeatureServer readings (Stream E)
    ("email_alerts", "ds:smtp", "write"),
    ("egle_doc_parser", "ds:anthropic", "read"),   # sends doc, reads classification
    ("config_loader", "ds:config", "read"),
    ("wds_archiver", "ds:wds", "read"),            # fetches the 5 collection pages
    ("wds_archiver", "ds:drive-archive", "write"), # uploads raw-HTML snapshots
    ("mmpc_archiver", "ds:drive-archive", "write"),# uploads MMPC PDFs (Mirror D, ADR 010)
]
# DISPATCH edges — call targets resolved against config, not a static symbol.
# Represented as `dispatch` (and the equivalent plain import edge is suppressed,
# so each pair carries exactly one, more-informative, edge):
#   watcher -> wds_watcher : gated dynamic import; fires only when config
#                            `wds.enabled` is true (default false -> dormant).
#   wds_watcher -> wds_client : the fetchers actually run are wc.FETCHERS[name]
#                            for name in config `wds.collections` (the route table).
DISPATCH_EDGES = [("watcher", "wds_watcher"), ("wds_watcher", "wds_client")]

OBSERVATIONS = [
    "watcher.py is a single orchestration hub importing ~10 of the 24 runtime "
    "modules — a star topology and the daily run's single point of failure. "
    "Partly mitigated: the Stream C (WDS) step is wrapped in its own try/except "
    "so a fault there can't sink the nSITE path.",
    "sheet_writer + the Conservancy Google Sheet is the data spine — the only "
    "read+write store, backing the append-only processing log (_state) plus the "
    "_meta singletons (digest queue, WDS seen-set + snapshot hashes, last-run) and "
    "the per-tab feeds (New/Historical, Evidence, MMPC Archived Files, PFAS Page "
    "Watch). Single point of failure and a scaling ceiling (50k-char cells).",
    "egle_doc_parser -> Anthropic is the only external-LLM dependency and the "
    "sole per-document cost driver; it is isolated to one module (swappable) and "
    "reached only on the backfill/watcher classify path.",
    "Five ingestion streams — nSITE/Air, WDS/Solid-Waste, MMPC (CivicClerk), PFAS "
    "pages, and GFL perimeter air (ArcGIS FeatureServer) — are cleanly separated "
    "into their own client modules with no cross-talk (good service-extraction "
    "seams). Stream E (gfl_air_watcher -> gfl_air_client, ADR 014) is the first "
    "source of real fenceline READINGS (H2S/CH4), not documents; config-gated "
    "(gfl_air.enabled, off by default). The MMPC minutes *reminder* was retired "
    "(ADR 013); MMPC is now archive-only via Mirror D (mmpc_archiver -> "
    "mmpc_client). Stream C (wds_watcher -> wds_client) is config-gated and active.",
    "Six independent runners now write to the Sheet/Drive spine, each on its own "
    "morning cron + concurrency group so they never race the shared _meta state: "
    "archiver (nSITE PDFs, 3am), wds_archiver (WDS HTML snapshots, 4am), "
    "mmpc_archiver (MMPC PDFs, 5am), watcher (nSITE + WDS + alerts, 6am), "
    "pfas_watcher (PFAS page hash, 7am), and gfl_air_watcher (GFL perimeter air, "
    "8am). backfill is a seventh, now manual-only. Like pfas_watcher, gfl_air_watcher "
    "keeps its cursor in its OWN tab (GFL Air), never _meta — a separate workflow "
    "must not write the shared _meta cell (ADR 014).",
    "Three runners write into the same Drive archive store (archive_client for "
    "nSITE PDFs, wds_archiver for WDS HTML snapshots, mmpc_archiver for MMPC PDFs) "
    "— watch for a shared-folder or quota coupling as they scale (Mirror D uses a "
    "distinct Drive folder from Mirror B/C).",
    "poison_doc_extractor (ADR 011) lets nsite_client salvage legacy .msg/.docx "
    "sources the downloadpdf endpoint can't render, synthesizing a PDF from the "
    "extracted text + embedded images. Reached only from the ingestion path; a "
    "known gap remains — image-only pages extract but are not vision-classified.",
    "The WOI extractor (woi_table_parser) is now wired into the daily runtime via "
    "woi_router: watcher and backfill route WOI Status Reports (detected by the "
    "'Gas Extraction Report' header + page count, NOT the nSITE filename) to it, "
    "replacing the windowed measurements before is_urgent so a buried exceedance "
    "still alerts (ADR 005). co_summary + woi_summary remain manual hand-to-EGLE "
    "generators built on the same parser.",
    "Unresolved-at-extraction dynamic dispatch: which WDS fetchers run depends on "
    "config `wds.collections` at runtime; watcher -> wds_watcher fires only when "
    "`wds.enabled` is true. Both are surfaced as dispatch edges, not calls.",
]

# Persona flows — the people who EXPERIENCE the monitor's output. Node ids are
# validated against the tree before write.
FLOWS = [
    {
        "name": "A new EGLE Air filing is caught and triaged",
        "persona": "N2688 Conservancy advocate",
        "description": "The monitor spots a new Air permit filing for the "
                       "landfill, classifies its risk, and alerts the advocate.",
        "steps": [
            {"label": "The daily monitor wakes at 6am",
             "nodes": ["watcher", "config_loader", "ds:config"]},
            {"label": "Check EGLE nSITE for new Air filings",
             "nodes": ["watcher", "nsite_client", "ds:nsite"]},
            {"label": "Read each new PDF and classify it against the risk register with Claude",
             "nodes": ["egle_doc_parser", "risk_register", "ds:anthropic"]},
            {"label": "Record the finding as a row in the case-file Sheet",
             "nodes": ["sheet_writer", "ds:conservancy-sheet"]},
            {"label": "Email the advocate now if urgent, else queue for the Sunday digest",
             "nodes": ["email_alerts", "ds:smtp"]},
        ],
    },
    {
        "name": "A solid-waste change at the landfill (Stream C)",
        "persona": "N2688 Conservancy advocate",
        "description": "When Stream C is enabled, a new construction permit or "
                       "groundwater exceedance in the Waste Data System reaches the advocate.",
        "steps": [
            {"label": "If Stream C is enabled, the daily run also polls solid-waste",
             "nodes": ["watcher", "config_loader", "wds_watcher"]},
            {"label": "Poll the EGLE Waste Data System collections",
             "nodes": ["wds_watcher", "wds_client", "ds:wds"]},
            {"label": "Diff against the stored seen-set and classify severity",
             "nodes": ["wds_watcher", "ds:conservancy-sheet"]},
            {"label": "Urgent (new permit / violation) emails same-day; the rest joins the digest",
             "nodes": ["email_alerts", "ds:smtp", "sheet_writer"]},
        ],
    },
    {
        "name": "Preserve durable evidence against link rot",
        "persona": "Advocate building the case file",
        "description": "A nightly job mirrors every processed EGLE PDF into Drive "
                       "so the evidence survives EGLE removing or renaming the source.",
        "steps": [
            {"label": "The nightly archive job runs after the watcher (3am)",
             "nodes": ["archiver"]},
            {"label": "Find processed documents not yet mirrored",
             "nodes": ["archiver", "sheet_writer", "ds:conservancy-sheet"]},
            {"label": "Download the source PDF from EGLE",
             "nodes": ["nsite_client", "ds:nsite"]},
            {"label": "Upload a durable copy to Google Drive",
             "nodes": ["archive_client", "ds:drive-archive"]},
        ],
    },
    {
        "name": "EGLE quietly edits the PFAS investigation page",
        "persona": "Remediation-Area / water advocate",
        "description": "EGLE updates the PFAS investigation status as prose on a "
                       "web page (no feed, no PDF); the watcher notices and emails a diff.",
        "steps": [
            {"label": "The daily PFAS watch runs at 7am",
             "nodes": ["pfas_watcher", "config_loader", "ds:config"]},
            {"label": "Fetch the EGLE PFAS page and isolate + hash its <main> content",
             "nodes": ["pfas_watcher", "pfas_client", "ds:pfas"]},
            {"label": "Compare the hash to the last snapshot in the PFAS Page Watch tab",
             "nodes": ["pfas_watcher", "sheet_writer", "ds:conservancy-sheet"]},
            {"label": "Email a visible-text diff when the page has changed",
             "nodes": ["email_alerts", "ds:smtp"]},
        ],
    },
    {
        "name": "New MMPC meeting documents are published (Mirror D)",
        "persona": "Advocate tracking the county committee",
        "description": "The county posts new MMPC agenda/minutes PDFs; Mirror D "
                       "downloads each into Drive automatically — no manual step.",
        "steps": [
            {"label": "The nightly MMPC mirror runs at 5am",
             "nodes": ["mmpc_archiver", "config_loader", "ds:config"]},
            {"label": "List every published file via the CivicClerk JSON API",
             "nodes": ["mmpc_archiver", "mmpc_client", "ds:mmpc"]},
            {"label": "Skip files already logged in the MMPC Archived Files tab",
             "nodes": ["mmpc_archiver", "sheet_writer", "ds:conservancy-sheet"]},
            {"label": "Download each new PDF and upload a durable copy to Drive",
             "nodes": ["archive_client", "ds:drive-archive"]},
        ],
    },
]


# --- Layer 1: modules, LOC, import (call) edges from source ------------------

def loc(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def discover_modules() -> dict:
    """{module_name: relpath} for every runtime .py (top-level + scripts/), tests
    excluded — tests are not part of the system topology."""
    files = sorted(glob.glob(os.path.join(REPO, "*.py")) +
                   glob.glob(os.path.join(REPO, "scripts", "*.py")))
    return {os.path.splitext(os.path.basename(f))[0]: os.path.relpath(f, REPO)
            for f in files}


def call_edges(mods: dict) -> list:
    """Local import edges (source -> imported local module) via ast. `ast.walk`
    also catches imports nested inside functions/gates (e.g. watcher's gated
    `import wds_watcher`)."""
    local = set(mods)
    edges = []
    for name, rel in mods.items():
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        deps = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    base = a.name.split(".")[0]
                    if base in local:
                        deps.add(base)
            elif isinstance(n, ast.ImportFrom) and n.module:
                base = n.module.split(".")[0]
                if base in local:
                    deps.add(base)
        deps.discard(name)
        for d in sorted(deps):
            edges.append((name, d))
    return edges


# --- Layer 2: entry points from deployment config ---------------------------

def entry_points_from_workflows() -> dict:
    """{module: [triggers]} parsed from .github/workflows/*.yml — a scheduled or
    dispatchable `run: python X.py` is this repo's EXEC PGM / cron entry point."""
    found = {}
    for wf in sorted(glob.glob(os.path.join(REPO, ".github", "workflows", "*.yml"))):
        text = open(wf, encoding="utf-8").read()
        cron = re.findall(r"cron:\s*\"([^\"]+)\"", text)
        commented = re.findall(r"#\s*-\s*cron:", text)  # disabled schedules
        has_dispatch = "workflow_dispatch:" in text
        for m in re.finditer(r"run:\s*python\s+([A-Za-z0-9_./]+\.py)", text):
            mod = os.path.splitext(os.path.basename(m.group(1)))[0]
            trig = []
            if cron:
                trig.append(f"cron {cron[0]}")
            elif commented:
                trig.append("cron (disabled)")
            if has_dispatch:
                trig.append("manual dispatch")
            found.setdefault(mod, [])
            for t in trig:
                if t not in found[mod]:
                    found[mod].append(t)
    return found


# --- Assemble ---------------------------------------------------------------

def build():
    mods = discover_modules()
    calls = call_edges(mods)
    wf_entries = entry_points_from_workflows()

    # Tree: domains -> leaves.
    by_domain = {}
    for name, rel in mods.items():
        dom = DOMAIN.get(name, "processing")
        kind = "job" if name in JOB_MODULES else "module"
        by_domain.setdefault(dom, []).append({
            "id": name, "name": name, "kind": kind,
            "language": "python", "loc": loc(os.path.join(REPO, rel)), "file": rel,
        })
    domain_children = []
    for dom in ["orchestration", "ingestion", "processing", "persistence", "config", "ops"]:
        if by_domain.get(dom):
            domain_children.append({
                "id": f"dom:{dom}", "name": DOMAIN_NAME[dom], "kind": "domain",
                "children": sorted(by_domain[dom], key=lambda c: -c["loc"]),
            })
    domain_children.append({
        "id": "dom:data", "name": DOMAIN_NAME["data"], "kind": "domain",
        "children": [{"id": i, "name": n, "kind": "datastore"} for i, n in DATASTORES],
    })
    root = {"id": "sys", "name": SYSTEM, "kind": "system", "children": domain_children}

    valid = set(mods) | {i for i, _ in DATASTORES}

    # Edges: dispatch (suppress the duplicate import edge), then remaining calls,
    # then data read/write.
    dispatch = set(DISPATCH_EDGES)
    edges = [{"source": s, "target": t, "kind": "dispatch"} for s, t in DISPATCH_EDGES]
    for s, t in calls:
        if (s, t) in dispatch:
            continue
        edges.append({"source": s, "target": t, "kind": "call"})
    for s, t, k in DATA_EDGES:
        edges.append({"source": s, "target": t, "kind": k})
    for e in edges:
        assert e["source"] in valid and e["target"] in valid, f"dangling edge {e}"

    # Entry points: workflow-derived (deployment) + manual ops jobs.
    entry = list(wf_entries) + sorted(JOB_MODULES)
    entry = [e for e in dict.fromkeys(entry) if e in mods]

    # Dead-end candidates: no inbound CALL/DISPATCH edge, not an entry point, not
    # a dynamic-dispatch target. (Data edges point AT stores, so they don't grant
    # a module inbound reachability.) After suppression this is empty for this
    # repo — every module is an entry point or imported by one; recorded in
    # observations instead of a false dead claim.
    inbound = {m: 0 for m in mods}
    for s, t in calls:
        if t in inbound:
            inbound[t] += 1
    for _s, t in DISPATCH_EDGES:
        if t in inbound:
            inbound[t] += 1
    dispatch_targets = {t for _s, t in DISPATCH_EDGES}
    dead = [m for m in sorted(mods)
            if inbound[m] == 0 and m not in entry and m not in dispatch_targets]

    # Validate flow node ids.
    for fl in FLOWS:
        for st in fl["steps"]:
            for nid in st["nodes"]:
                assert nid in valid, f"flow {fl['name']!r} references unknown node {nid}"

    topo = {
        "system": SYSTEM, "root": root, "edges": edges,
        "entryPoints": entry, "deadEnds": dead,
        "observations": OBSERVATIONS, "flows": FLOWS,
    }
    return topo, mods, wf_entries


def summary(topo, mods, wf_entries):
    lines = []
    P = lines.append
    P(f"# {topo['system']} — topology")
    P("")
    counts = {}
    for dom in topo["root"]["children"]:
        counts[dom["name"]] = len(dom["children"])
    P(f"Modules: {len(mods)}   Data stores: {len(DATASTORES)}   Edges: {len(topo['edges'])}")
    P("Domains: " + ", ".join(f"{n} ({c})" for n, c in counts.items()))
    P("")
    kinds = {}
    for e in topo["edges"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    P("Edge kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    P("")
    P("## Entry points (from .github/workflows + manual ops jobs)")
    for m in topo["entryPoints"]:
        trig = ", ".join(wf_entries.get(m, ["manual CLI"]))
        P(f"  - {m:18} [{trig}]")
    P("")
    P("## Dead-end candidates (post-suppression)")
    P("  " + (", ".join(topo["deadEnds"]) if topo["deadEnds"]
             else "(none — every module is an entry point or is imported by one)"))
    P("")
    P("## Fan-in (most-imported modules)")
    inbound = {}
    for e in topo["edges"]:
        if e["kind"] in ("call", "dispatch") and e["target"] in mods:
            inbound[e["target"]] = inbound.get(e["target"], 0) + 1
    for m, c in sorted(inbound.items(), key=lambda x: -x[1])[:8]:
        P(f"  {c:2}  <- {m}")
    P("")
    P("## Persona flows")
    for fl in topo["flows"]:
        P(f"  - {fl['name']}  ({fl['persona']})")
        for i, st in enumerate(fl["steps"], 1):
            P(f"      {i}. {st['label']}")
    P("")
    P("## Observations")
    for o in topo["observations"]:
        P(f"  - {o}")
    return "\n".join(lines)


if __name__ == "__main__":
    topo, mods, wf_entries = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(topo, fh, indent=2)
    print(summary(topo, mods, wf_entries))
    print(f"\nwrote {os.path.relpath(OUT, REPO)}")
