#!/usr/bin/env python3
"""scripts/gen_stream_status.py -- "what is live" status, generated from
config.yml.

CLAUDE.md's per-module prose has repeatedly drifted from config.yml: a
stream's `enabled` flag flips (or a sibling flag like
`gfl_air.hourly_capture.enabled` gets added later) without every mention of
it in CLAUDE.md getting updated by hand -- the claude-md-drift pass
(2026-10-14) found eight stale "ships `false`"/"parked" claims this way, one
of which (`civicclerk_archive.enabled`) wasn't even on the handoff's own list.

This script is the fix going forward: it walks config.yml for every key
literally named `enabled` or ending in `_enabled`, AT ANY NESTING DEPTH, and
renders a markdown table of dotted-path -> live/off. A walk can't drift the
way a hand-maintained list of streams can -- a newly added flag shows up here
automatically, with no second place anyone has to remember to update.

CLAUDE.md carries exactly this table between the `<!-- stream-status:begin
-->` / `<!-- stream-status:end -->` markers. `tests/test_stream_status.py`
fails if the committed table and this script's live output ever diverge --
the same pattern `tests/test_sitemap.py` uses for site/sitemap.xml.

This table answers ONLY "is it live right now." Each flag's activation DATE
and the human decision behind it stay in CLAUDE.md's hand-written per-module
prose above the table -- that's history, not state, and a generator has no
way to know it.

Usage: python3 scripts/gen_stream_status.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_loader import load_config  # noqa: E402

BEGIN_MARKER = "<!-- stream-status:begin -->"
END_MARKER = "<!-- stream-status:end -->"


def find_enabled_flags(node, prefix: str = "") -> list[tuple[str, bool]]:
    """[(dotted.path, value)] for every key literally `enabled` or ending in
    `_enabled`, at any nesting depth -- including inside list items, so a
    future per-item flag (e.g. one `enabled:` per entry in a `mirrors:`
    list) is not silently skipped -- in config order (PyYAML preserves the
    source file's key order via plain dicts). `bool(v)` coercion means a
    QUOTED string value ("false") would read as truthy; no flag in
    config.yml is quoted today, and this matches the runtime's own
    truthiness convention (e.g. `gfl_air_hourly_capture.py`'s
    `if not hc.get("enabled")`), so it's a known, accepted limitation rather
    than a silent one."""
    out: list[tuple[str, bool]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if k == "enabled" or (isinstance(k, str) and k.endswith("_enabled")):
                out.append((path, bool(v)))
            out.extend(find_enabled_flags(v, path))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            out.extend(find_enabled_flags(item, f"{prefix}[{i}]"))
    return out


def build_table(cfg: dict) -> str:
    """The exact markdown table CLAUDE.md pins between the markers. Pure:
    takes an already-loaded config dict, no I/O of its own."""
    lines = ["| Config flag | Live |", "| --- | --- |"]
    for path, val in find_enabled_flags(cfg):
        lines.append(f"| `{path}` | {'yes' if val else 'no'} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.write(build_table(load_config()))


if __name__ == "__main__":
    main()
