#!/usr/bin/env python3
"""
gen_mermaid.py — derive the three exportable Mermaid diagrams from topology.json.

Kept small and re-runnable so the .mmd files never drift from the JSON. Each stays
well under ~40 edges (the interactive TOPOLOGY.html is for the dense full graph):
  - call-graph.mmd    domain-level graph TD, entry-point domains highlighted
  - data-lineage.mmd  graph LR, modules -> data stores, read vs write
  - critical-path.mmd flowchart TD of the primary persona flow
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.path.join(HERE, "topology.json")))

# module id -> domain id, and datastore names
mod_domain, ds_name, leaf_kind = {}, {}, {}
for dom in T["root"]["children"]:
    for leaf in dom["children"]:
        leaf_kind[leaf["id"]] = leaf["kind"]
        if leaf["kind"] == "datastore":
            ds_name[leaf["id"]] = leaf["name"]
        else:
            mod_domain[leaf["id"]] = dom["id"]
DOM_LABEL = {d["id"]: d["name"] for d in T["root"]["children"]}


def _slug(x):
    return x.replace(":", "_").replace("-", "_").replace(".", "_")


# --- 1. call-graph.mmd (domain level) --------------------------------------
def call_graph():
    pairs = set()
    for e in T["edges"]:
        if e["kind"] in ("call", "dispatch"):
            a, b = mod_domain.get(e["source"]), mod_domain.get(e["target"])
            if a and b and a != b:
                pairs.add((a, b))
    entry_domains = {mod_domain[m] for m in T["entryPoints"] if m in mod_domain}
    out = ["graph TD",
           "  %% domain-level call graph — arbor-hills-monitor",
           "  %% entry-point domains (scheduled/manual runners) highlighted"]
    for did, label in DOM_LABEL.items():
        if did == "dom:data":
            continue
        out.append(f"  {_slug(did)}[\"{label}\"]")
    for a, b in sorted(pairs):
        out.append(f"  {_slug(a)} --> {_slug(b)}")
    for did in entry_domains:
        out.append(f"  style {_slug(did)} fill:#2563eb,color:#fff,stroke:#1e40af")
    return "\n".join(out) + "\n"


# --- 2. data-lineage.mmd ----------------------------------------------------
def data_lineage():
    out = ["graph LR",
           "  %% program -> data store lineage; dotted = read, bold = write"]
    for dsid, name in ds_name.items():
        out.append(f"  {_slug(dsid)}[(\"{name}\")]")
    seen_mod = set()
    for e in T["edges"]:
        if e["kind"] not in ("read", "write"):
            continue
        m = e["source"]
        if m not in seen_mod:
            out.append(f"  {_slug(m)}[\"{m}\"]")
            seen_mod.add(m)
        if e["kind"] == "read":
            out.append(f"  {_slug(m)} -. read .-> {_slug(e['target'])}")
        else:
            out.append(f"  {_slug(m)} ==>|write| {_slug(e['target'])}")
    for dsid in ds_name:
        out.append(f"  style {_slug(dsid)} fill:#f59e0b,color:#000,stroke:#b45309")
    return "\n".join(out) + "\n"


# --- 3. critical-path.mmd (primary flow) -----------------------------------
def critical_path():
    flow = T["flows"][0]
    out = ["flowchart TD",
           f"  %% {flow['name']} — persona: {flow['persona']}",
           f"  %% {flow['description']}",
           "  %% (no telemetry wired yet — annotate p50/p99 here once available)"]
    prev = None
    for i, st in enumerate(flow["steps"], 1):
        nid = f"s{i}"
        mods = ", ".join(n for n in st["nodes"] if leaf_kind.get(n) != "datastore")
        stores = [ds_name[n] for n in st["nodes"] if leaf_kind.get(n) == "datastore"]
        label = f"{i}. {st['label']}<br/><small>{mods}</small>"
        out.append(f"  {nid}[\"{label}\"]")
        if prev:
            out.append(f"  {prev} --> {nid}")
        for s in stores:
            sid = f"{nid}_ds"
            out.append(f"  {sid}[(\"{s}\")]")
            out.append(f"  {nid} -. touches .-> {sid}")
        prev = nid
    return "\n".join(out) + "\n"


for fname, gen in [("call-graph.mmd", call_graph),
                   ("data-lineage.mmd", data_lineage),
                   ("critical-path.mmd", critical_path)]:
    with open(os.path.join(HERE, fname), "w") as fh:
        fh.write(gen())
    print(f"wrote {os.path.relpath(os.path.join(HERE, fname))}")
