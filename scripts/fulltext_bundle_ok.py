#!/usr/bin/env python3
"""scripts/fulltext_bundle_ok.py -- may pages.yml deploy this cached full-text
bundle? Prints `ok=true|false` for $GITHUB_OUTPUT, and a one-line reason (counts
only) on stderr. The bundle is restored from the Actions cache, so it can be
older than the page it sits next to; it is deployed only when ALL of these hold
(fail closed: anything missing or unreadable means false):

1. every link it indexes is still in the CURRENT published search-index.json --
   a withdrawn or purged document never stays searchable;
2. it was built against the CURRENT name_check denylist -- a name added since
   means the old bundle is refused until a rebuild;
3. it was built within MAX_AGE_DAYS -- so a failed rebuild, or a REDACT_NAMES
   change (a secret, deliberately not fingerprinted into a public file), keeps
   an old bundle live for at most that long.

Documents published since the build are fine: they just aren't full-text
searchable until the next build. Stdlib + repo modules only (no pip install).
Usage: fulltext_bundle_ok.py <bundle_dir> <search-index.json>
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

MAX_AGE_DAYS = 2


def _index_module():
    spec = importlib.util.spec_from_file_location("build_fulltext_index", os.path.join(_HERE, "build_fulltext_index.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(bundle_dir: str, index_path: str, today: date) -> tuple[bool, str]:
    try:
        with open(os.path.join(bundle_dir, "bundle-manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        with open(index_path, encoding="utf-8") as f:
            published = {e.get("link") for e in json.load(f)}
        built = date.fromisoformat(manifest["built"])
        links = manifest["links"]
        deny = manifest["denylist_sha"]
    except Exception as e:  # noqa: BLE001 -- unreadable means do not deploy
        return False, f"bundle manifest unreadable ({type(e).__name__})"
    if (today - built).days > MAX_AGE_DAYS:
        return False, f"bundle is {(today - built).days} days old (max {MAX_AGE_DAYS})"
    if deny != _index_module().denylist_fingerprint():
        return False, "bundle was built before the current name_check denylist"
    gone = sum(1 for link in links if link not in published)
    if gone:
        return False, f"{gone} indexed document(s) are no longer published"
    return True, f"{len(links)} documents, built {manifest['built']}"


if __name__ == "__main__":
    ok, why = check(sys.argv[1], sys.argv[2], datetime.now(timezone.utc).date())
    print("ok=" + ("true" if ok else "false"))
    print(("full-text bundle OK: " if ok else "full-text bundle NOT deployed: ") + why, file=sys.stderr)
