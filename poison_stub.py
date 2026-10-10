"""
poison_stub.py — the terminal "stub + skip" step for a poison document, shared
by watcher.py and backfill.py.

When a doc's permanent-error count reaches MAX_ERRORS_PER_DOC, it is made
VISIBLE instead of silently dropped: a stub feed row (title/date/native-download
link) is written and the doc is marked 'skipped' so it isn't retried. The link
uses the downloadfile endpoint (serves the original bytes for legacy .doc /
zips / images that downloadpdf 400s on), so a human can open it.

Why this is shared (issue #82): this block used to live only in backfill.py.
A doc whose 3rd strike came from watcher.py's daily run was then excluded by
BOTH jobs' poison gates (watcher's done_or_poisoned(), backfill's select_todo())
and never got its stub row — silently parked, invisible in the public feed.
Both jobs now call stub_if_poisoned() from their per-doc except handlers, so the
doc is stubbed no matter which job caused the terminal failure.
"""
from __future__ import annotations

import sheet_writer as sw
import nsite_client as nc

MAX_ERRORS_PER_DOC = 3  # give up on a poison doc after this many failures


def stub_if_poisoned(sheets, sheet_id: str, state: dict, d: dict, cnt: int,
                     exc: BaseException, now: str,
                     feed_tab: str = sw.TAB_HISTORICAL,
                     title_overrides: dict | None = None) -> bool:
    """If `cnt` (the doc's running error count) has reached MAX_ERRORS_PER_DOC
    and the doc isn't already skipped, write its stub feed row to `feed_tab`,
    append a 'skipped' state event, and update the in-memory `state` (skipped
    set + error count cleared). Stub row first, then state (crash-safe).

    ADR 065: a poison doc never gets parsed (download/classify failed), so
    there's no display_title to generate — only the doc_id -> name override
    map (never the generic-title/LLM path, which needs a successful parse)
    can give it a better displayed name. `egle_title` always preserves the
    raw nSITE title either way; `document_date` stays blank (no text was
    ever extracted to look for one).

    Returns True iff the doc was stubbed. Never raises: a write failure is
    logged and the doc stays at its strike count."""
    did = d["doc_id"]
    if cnt < MAX_ERRORS_PER_DOC or did in state["skipped"]:
        return False
    reason = f"Source not classifiable after {cnt} attempts: {str(exc)[:140]}"
    link = nc.native_download_url(did)
    egle_title = d["document_name"]
    stub_meta = dict(d)
    stub_meta["egle_title"] = egle_title
    stub_meta["document_name"] = (title_overrides or {}).get(did) or egle_title
    stub_meta["document_date"] = d.get("document_date", "")
    try:
        sw.write_stub_row(sheets, sheet_id, stub_meta, link, reason, feed_tab=feed_tab)
        sw.mark_skipped(sheets, sheet_id, did,
                        {"document_name": stub_meta["document_name"],
                         "date_filed": d["date_filed"], "reason": reason},
                        now)
        state["skipped"][did] = {"reason": reason}
        state["errors"].pop(did, None)
        print(f"  ->  stubbed + skipped (now visible in feed): "
              f"{stub_meta['document_name'][:50]}")
        return True
    except Exception as e2:  # noqa: BLE001
        print(f"  ->  stub/skip write failed: {e2}")
        return False
