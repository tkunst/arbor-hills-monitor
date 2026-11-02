#!/usr/bin/env python3
"""scripts/fulltext_flag.py -- print `enabled=true|false` for config.yml's
fulltext_search.enabled, for a workflow's $GITHUB_OUTPUT. Stdlib only on
purpose: pages.yml and fulltext-index.yml call it BEFORE any pip install, so
the flag check costs nothing when the feature is off. Reads the top-level
`fulltext_search:` block's own `enabled:` line; anything unexpected reads as
false (fail closed: nothing full-text is built or deployed)."""
from __future__ import annotations

import re
import sys


def enabled(text: str) -> bool:
    block = re.search(r"^fulltext_search:[ \t]*\n((?:[ \t]+.*\n|[ \t]*\n)*)", text + "\n", re.MULTILINE)
    if not block:
        return False
    m = re.search(r"^[ \t]+enabled:[ \t]*(\S+)", block.group(1), re.MULTILINE)
    return bool(m) and m.group(1).strip().lower() == "true"


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yml"
    with open(path, encoding="utf-8") as f:
        print("enabled=" + ("true" if enabled(f.read()) else "false"))
