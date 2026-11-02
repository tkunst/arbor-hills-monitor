#!/usr/bin/env python3
"""scripts/build_fulltext_index.py -- build the Pagefind full-text bundle for
the public records search (ADR 062 addendum, 2026-11-02).

Joins the PUBLISHED site/public-records/search-index.json with the text cache
written by build_fulltext_cache.py, turns each eligible entry into one Pagefind
custom record (fulltext_search.build_record: redacted again with the CURRENT
denylist, capped, hand-curated rows excluded), and writes the bundle to --out
(in CI: site/public-records/fulltext/, never committed). Also writes
<out>/coverage.json so the page can say "full text available for N of M
documents" instead of implying the whole archive is searchable.

The publish gate for this bundle lives here, because check_publish_safety
cannot read Pagefind's compressed shards: only published entries, never a
hand-curated one, denylist redaction applied to every record. A bundle larger
than --max-mb is refused (exit 1) rather than deployed.

stdout is public in Actions: counts only (SEC-001).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fulltext_search as fts  # noqa: E402

DEFAULT_INDEX = "site/public-records/search-index.json"


def collect_records(entries: list[dict], cache_dir: str, cap: int) -> tuple[list[dict], dict]:
    """Pure-ish (reads cache files only): records to index + coverage counts."""
    records, seen = [], set()
    for e in entries:
        if not fts.is_eligible(e):
            continue
        key = fts.doc_key(e["link"])
        path = os.path.join(cache_dir, key + ".txt")
        if key in seen or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            rec = fts.build_record(e, f.read(), cap)
        if rec:
            seen.add(key)
            records.append(rec)
    coverage = {"documents": len(entries), "full_text": len(records),
                "eligible": sum(1 for e in entries if fts.is_eligible(e))}
    return records, coverage


async def write_bundle(records: list[dict], out_dir: str) -> None:
    from pagefind.index import IndexConfig, PagefindIndex

    async with PagefindIndex(IndexConfig(output_path=out_dir)) as index:
        for r in records:
            await index.add_custom_record(url=r["url"], content=r["content"], language="en",
                                          meta=r["meta"], filters=r["filters"], sort=r["sort"])


BUNDLE_MANIFEST = "bundle-manifest.json"


def denylist_fingerprint() -> str:
    """sha256 of the (public) name_check lists, so pages.yml can refuse a bundle
    built before a name was added to them."""
    import name_check

    blob = json.dumps([sorted(name_check.KNOWN_NAMES), sorted(name_check.INTERNAL_MARKERS)])
    return hashlib.sha256(blob.encode()).hexdigest()


def bundle_manifest(records: list[dict], built: str) -> dict:
    """What pages.yml checks before deploying a cached bundle
    (scripts/fulltext_bundle_ok.py): which links it indexes, when, and against
    which denylist. Links are already public (they are the published index)."""
    return {"built": built, "links": sorted({r["url"] for r in records}),
            "denylist_sha": denylist_fingerprint()}


def dir_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total / 1e6


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--force", action="store_true", help="run even if the config flag is off")
    args = ap.parse_args(argv)
    import config_loader

    cfg = (config_loader.load_config().get("fulltext_search") or {})
    if not cfg.get("enabled") and not args.force:
        print("fulltext_search.enabled is false; nothing to do")
        return 0
    cap = int(cfg.get("prose_cap_chars", fts.PROSE_CAP_CHARS))
    max_mb = float(cfg.get("max_bundle_mb", 400))
    with open(args.index, encoding="utf-8") as f:
        entries = json.load(f)
    records, coverage = collect_records(entries, args.cache, cap)
    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    asyncio.run(write_bundle(records, args.out))
    size = dir_mb(args.out)
    coverage["bundle_mb"] = round(size, 1)
    with open(os.path.join(args.out, "coverage.json"), "w", encoding="utf-8") as f:
        json.dump(coverage, f)
    from datetime import datetime, timezone

    with open(os.path.join(args.out, BUNDLE_MANIFEST), "w", encoding="utf-8") as f:
        json.dump(bundle_manifest(records, datetime.now(timezone.utc).date().isoformat()), f)
    print("fulltext bundle:", " ".join(f"{k}={v}" for k, v in coverage.items()))
    if size > max_mb:
        print(f"bundle {size:.0f} MB exceeds max_bundle_mb={max_mb:.0f}; refusing to publish it")
        shutil.rmtree(args.out)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
