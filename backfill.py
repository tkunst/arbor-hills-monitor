"""
backfill.py — process the existing ~754 N2688 documents, one batch per run.

Seeds from the AUTHORITATIVE nSITE document list (not the unseen Documents.csv —
see docs/decisions/003-seed-from-nsite.md). For each not-yet-processed doc:
  download from nSITE -> parse (OCR + classify) -> mirror to Drive inline,
  best-effort (ADR 007 addendum — the Sheet row links to the durable Drive
  copy when the mirror succeeds, the nSITE link otherwise; never blocks on a
  Drive problem) -> write the Sheet row -> THEN append the 'processed' state
  event (so a kill between the two double-writes the row on resume, never
  silently drops it).

The service account itself still has no Drive quota (ADR 006) — the mirror
upload goes through the separate OAuth user credential (see archive_client.py
/ archiver.mirror_one_now()), same as the nightly archive.yml catch-up job.
When archiving isn't configured (no GOAUTH_*) or a mirror attempt fails, the
Sheet links straight to the nSITE source instead, as before. State is the
Sheet's _state tab, not a Drive file.

Self-terminating: when _state shows every doc processed, it logs "Backfill
complete" and exits 0 — a no-op. Runs on manual workflow_dispatch only; the
nightly 2am ET cron was disabled 2026-07-07 because it raced the daily watcher
and suppressed new-doc emails (see backfill.yml's schedule comment).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import drive_client as dc
import sheet_writer as sw
import nsite_client as nc
import well_aliases
import woi_router  # noqa: F401 — tests patch through this module name
import archiver as av  # noqa: F401 — tests patch through this module name
import poison_stub
import doc_pipeline as dp
from egle_doc_parser import parse_document
from risk_register import RISK_REGISTER
from config_loader import load_config

MAX_ERRORS_PER_DOC = poison_stub.MAX_ERRORS_PER_DOC  # give up on a poison doc after this many failures


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _retry_poisoned() -> bool:
    """Whether to re-attempt already-poisoned docs (>= MAX_ERRORS_PER_DOC errors)
    this run — e.g. after raising classification_max_tokens to fix a truncation.
    Opt-in via the RETRY_POISONED env (a backfill workflow_dispatch input) so
    normal scheduled runs keep skipping poison docs and stay self-terminating."""
    return os.environ.get("RETRY_POISONED", "").strip().lower() in {"1", "true", "yes"}


def _retry_doc_ids() -> set:
    """Explicit doc_id allowlist for a one-time retroactive retry (ADR 011 —
    the WRD-Groundwater .msg/.docx docs terminally skipped before
    poison_doc_extractor existed). Comma-separated nSITE doc_ids via the
    RETRY_DOC_IDS env (a backfill workflow_dispatch input). Empty/unset ->
    no override, every doc obeys the normal processed/skipped/poison gates.

    NOTE: RETRY_DOC_IDS re-attempts skipped/poisoned docs but NEVER a doc already
    'processed' (a genuine success is never re-touched). To re-extract an
    already-processed doc — e.g. a WOI Status Report processed via the old
    windowed path before woi_router existed — use FORCE_REPROCESS_DOC_IDS below."""
    raw = os.environ.get("RETRY_DOC_IDS", "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _force_reprocess_doc_ids() -> set:
    """Explicit doc_id allowlist to RE-PROCESS docs even if already 'processed' —
    the one override that bypasses the processed gate. For re-extracting a doc
    whose classification/measurements should change because the parser did (the
    WOI Status Reports processed via the old windowed path, now routed exhaustively
    through woi_router — see ADR 005 / docs/overnight-coder-handoffs/woi-auto-routing.md).

    Comma-separated nSITE doc_ids via the FORCE_REPROCESS_DOC_IDS env (a backfill
    workflow_dispatch input). Because re-processing an already-recorded doc would
    otherwise pile fresh rows on top of the stale ones, each force doc's existing
    rows are PURGED (sheet_writer.purge_doc_rows) before the fresh rows are written
    — but ONLY when FORCE_REPROCESS_APPLY is set. Without it the run is a DRY RUN:
    it previews exactly what would be purged + re-extracted and mutates nothing."""
    raw = os.environ.get("FORCE_REPROCESS_DOC_IDS", "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _force_reprocess_apply() -> bool:
    """Whether a FORCE_REPROCESS_DOC_IDS run actually mutates the Sheet (purge +
    re-extract), vs. the safe default of a DRY RUN that only previews. A
    deliberate second switch so a destructive re-extract of hand-curated case-file
    rows can never fire from the doc-id list alone."""
    return os.environ.get("FORCE_REPROCESS_APPLY", "").strip().lower() in {"1", "true", "yes"}


def select_todo(docs: list, state: dict, retry_poisoned: bool = False,
                retry_doc_ids: set = None, force_reprocess_doc_ids: set = None) -> list:
    """Docs still needing work. Normal mode skips both processed and poisoned
    docs (poisoned = at least MAX_ERRORS_PER_DOC failures, OR terminally
    'skipped'). retry_poisoned mode re-attempts poisoned docs but still skips
    terminal 'skipped' docs; a later success appends a 'processed' row that
    clears the error count in read_state's reducer, while a renewed failure
    just pushes the count higher (still poisoned).

    retry_doc_ids is a narrower, surgical override on top of that: an explicit
    doc_id allowlist that re-attempts EXACTLY those docs regardless of
    skipped/poisoned status (but never a doc already 'processed' — a genuine
    success is never re-attempted). Unlike retry_poisoned, which churns
    through the WHOLE poisoned population, this is for 'these specific docs
    were terminally skipped before a parser fix existed, and are now known-
    processable' — see backfill's RETRY_DOC_IDS / ADR 011.

    force_reprocess_doc_ids is the ONLY override that bypasses the 'processed'
    gate — an explicit allowlist to RE-PROCESS docs whose output should change
    because the parser did (WOI reports processed via the old windowed path, now
    routed through woi_router — ADR 005). A force-reprocess run is SURGICAL: when
    it is set, ONLY the named docs are returned — the normal backlog is left
    untouched, so the run can never silently co-process unrelated pending docs
    (backfill sends no alerts; that silent-processing risk is exactly why the
    nightly schedule was disabled). Order follows `docs`."""
    skipped = state.get("skipped", {})
    retry_doc_ids = retry_doc_ids or set()
    force_reprocess_doc_ids = force_reprocess_doc_ids or set()
    if force_reprocess_doc_ids:
        return [d for d in docs if d["doc_id"] in force_reprocess_doc_ids]
    out = []
    for d in docs:
        did = d["doc_id"]
        if did in state["processed"]:
            continue
        if did in retry_doc_ids:
            out.append(d)
            continue
        if did in skipped:
            continue
        if not retry_poisoned and state["errors"].get(did, 0) >= MAX_ERRORS_PER_DOC:
            continue
        out.append(d)
    return out


def count_remaining(docs: list, state: dict) -> int:
    """Poison-aware remaining count: docs neither processed nor poisoned. This is
    the normal completion signal, used for the end-of-run log line even during a
    retry run — so "0 remaining" still means "a normal scheduled run has no work
    left," and the job stays self-terminating regardless of stuck poison docs."""
    skipped = state.get("skipped", {})
    return sum(
        1 for d in docs
        if d["doc_id"] not in state["processed"]
        and d["doc_id"] not in skipped
        and state["errors"].get(d["doc_id"], 0) < MAX_ERRORS_PER_DOC
    )


def _backfill_payload(parsed, d: dict) -> dict:
    """The backfill's _state payload (adds ocr_applied/page_count to the
    watcher's fields)."""
    return {
        "document_name": d["document_name"],
        "date_filed": d["date_filed"],
        "doc_type": parsed.doc_type,
        "severity": parsed.severity,
        "risks": parsed.risks,
        "ocr_applied": parsed.ocr_applied,
        "page_count": parsed.page_count,
        # ADR 065 — see watcher.py's _processed_payload for why these
        # are carried (archiver.py's nightly catch-up run needs them).
        "document_date": d.get("document_date", ""),
        "egle_title": d.get("egle_title") or d["document_name"],
    }


def run() -> int:
    cfg = load_config()
    sheet_id = os.environ["GSHEET_ID"]
    model = cfg["anthropic_model"]
    batch_size = cfg["backfill_batch_size"]
    # Fail fast on a missing key (doc_pipeline reads these per document).
    cfg["large_doc_page_threshold"], cfg["large_doc_max_keyword_pages"]
    # ADR 065: see watcher.py's identical block for the no-op-when-empty note.
    generic_exact, generic_prefixes, title_overrides = dp.title_config(cfg)
    # ADR 068: the curated well-ID alias map, loaded ONCE here — outside the
    # per-doc try — so a missing/malformed map fails the run red instead of
    # becoming per-doc poison strikes or a silent WOI-routing fallback.
    well_alias_map = well_aliases.load_alias_map()

    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)

    state = sw.read_state(sheets, sheet_id)

    session = nc.make_session()
    docs = nc.fetch_all_documents(session, cfg)
    if not docs:
        print("[backfill] nSITE returned 0 documents — aborting (transient?).")
        return 1

    retry_poisoned = _retry_poisoned()
    retry_doc_ids = _retry_doc_ids()
    force_ids = _force_reprocess_doc_ids()
    force_apply = _force_reprocess_apply()
    todo = select_todo(docs, state, retry_poisoned, retry_doc_ids, force_ids)
    mode = " (retrying poisoned)" if retry_poisoned else ""
    if retry_doc_ids:
        mode += f" (retrying {len(retry_doc_ids)} specific doc_id(s))"
    if force_ids:
        mode += (f" (FORCE-REPROCESS {len(force_ids)} doc_id(s)"
                 f"{' — APPLY' if force_apply else ' — DRY RUN'})")
    print(f"[backfill] {len(docs)} total, {len(state['processed'])} done, {len(todo)} remaining{mode}.")
    if force_ids and not force_apply:
        print("[backfill] FORCE_REPROCESS DRY RUN — previewing purge + re-extract, "
              "mutating nothing. Set FORCE_REPROCESS_APPLY=1 to execute.")
    if not todo:
        print("[backfill] Backfill complete — nothing to do.")
        return 0

    batch = todo[:batch_size]
    tmp = tempfile.gettempdir()
    processed_this_run = 0
    ctx = dp.RunContext(
        cfg=cfg, sheets=sheets, sheet_id=sheet_id, session=session, model=model,
        well_alias_map=well_alias_map, parse=parse_document, now=_now,
        generic_exact=generic_exact, generic_prefixes=generic_prefixes,
        title_overrides=title_overrides)
    # The backfill's half of the shared per-document pipeline (doc_pipeline.py):
    # Historical Documents feed, no alerts (backfill never calls is_urgent, so
    # re-extracting history fires nothing stale), error count cleared on success.
    job = dp.Job(name="backfill", feed_tab=sw.TAB_HISTORICAL, payload=_backfill_payload,
                 clear_errors_on_success=True)

    for d in batch:
        did = d["doc_id"]
        is_force = did in force_ids
        if is_force and not force_apply:
            # DRY RUN: preview which rows a purge would remove, then re-extract.
            # Download/classify nothing; mutate nothing.
            preview = sw.purge_doc_rows(sheets, sheet_id, did, dry_run=True)
            print(f"  [FORCE dry-run] {d['date_filed']} {d['document_name'][:40]}: "
                  f"would PURGE {sum(preview.values())} row(s) {preview}, then "
                  f"re-extract this doc.")
            # The doc may also appear in tabs that are NOT purged — spell out the
            # full accounting so nothing looks unhandled:
            print("      also, on APPLY: 'All Evidence by Risk' is recomputed from "
                  "the refreshed Evidence tab; '_state' gets a new 'processed' "
                  "event that supersedes the old; 'Archived PDFs' is left intact "
                  "(it indexes the unchanged Drive-mirrored PDF). "
                  "Set FORCE_REPROCESS_APPLY=1 to execute.")
            continue
        if dp.process_one_doc(ctx, state, d, job, dp.local_path(tmp, d),
                              purge_first=is_force) == "processed":
            processed_this_run += 1

    # A pure FORCE_REPROCESS dry run mutates NOTHING — skip the derived-tab
    # rebuilds below and return. (A force run's todo is only the named docs, which
    # all just previewed above; there is nothing to rebuild from.)
    if force_ids and not force_apply:
        print("[backfill] FORCE_REPROCESS DRY RUN complete — nothing was changed.")
        return 0

    # Keep the Risk Register tab current after each batch.
    try:
        sw.rebuild_risk_register_tab(sheets, sheet_id, RISK_REGISTER)
    except Exception as e:  # noqa: BLE001
        print(f"[backfill] risk-register rebuild skipped: {e}")

    # "All Evidence by Risk" is DERIVED from "Evidence by Risk" (rebuilt via
    # clear+rewrite), so it is NOT purged directly — this rebuild keeps it
    # consistent after a re-extract. Run it while Stream C is enabled (its normal
    # trigger) OR after a force-reprocess that applied changes, so a re-extract
    # leaves All Evidence correct even if Stream C is ever disabled (the write is
    # caught below if the tab doesn't exist).
    if (cfg.get("wds") or {}).get("enabled") or (force_ids and force_apply):
        try:
            sw.rebuild_all_evidence_tab(sheets, sheet_id)
        except Exception as e:  # noqa: BLE001
            print(f"[backfill] all-evidence rebuild skipped: {e}")

    remaining = count_remaining(docs, state)
    print(f"[backfill] processed {processed_this_run} this run; {remaining} remaining.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
