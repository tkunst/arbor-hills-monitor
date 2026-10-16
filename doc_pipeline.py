"""
doc_pipeline.py — the one per-document pipeline shared by watcher.run and
backfill.run (consolidation PR 3, docs/overnight-coder-handoffs/
sheet-writer-watcher-consolidation.md).

Both jobs used to carry their own ~150-line copy of the same steps, and every
cross-cutting change (ADR 065 title resolution, ADR 067 temperature checks,
ADR 068 well aliases, the WOI review flags) had to be wired into both. The
steps now live here once; a `Job` says only where the two jobs differ:

  download -> parse_document -> ADR 065 title/date chokepoint ->
  archiver.mirror_one_now() (inline, best-effort) ->
  related-scope doc: Related Documents row, then state, done ->
  WOI routing (best-effort) -> [backfill FORCE: purge stale rows] ->
  Sheet row -> [watcher: compliance deadlines] -> WOI summary tab ->
  [watcher: well_watch] -> [watcher: urgent/digest] -> review flags ->
  'processed' state event LAST (Sheet row before state, ADR 006).

A failure goes through the same transient / strike / poison-stub handling in
both jobs. Behavior-preserving: the order of every write and the log lines are
exactly what each job did before.

Collaborators that tests patch on the job module (`parse_document`, `_now`,
the job's own alert / payload functions) are handed in through RunContext and
Job, read at call time, so a patch on `watcher.parse_document` still takes
effect.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import archiver as av
import document_titles as dt
import nsite_client as nc
import owner_notices as on
import poison_stub
import retry_policy as rp
import review_flags as rf
import sheet_writer as sw
import well_watch
import woi_router
from poison_stub import MAX_ERRORS_PER_DOC
from risk_register import RISK_NAMES, RISK_REGISTER, SIGNAL_KEYWORDS


@dataclass
class RunContext:
    """Per-run constants, built once by the job's run()."""
    cfg: dict
    sheets: object
    sheet_id: str
    session: object
    model: str
    well_alias_map: Optional[dict]
    parse: Callable          # egle_doc_parser.parse_document (as seen by the job module)
    now: Callable[[], str]
    generic_exact: list = field(default_factory=list)
    generic_prefixes: list = field(default_factory=list)
    title_overrides: dict = field(default_factory=dict)
    drive_state: dict = field(default_factory=dict)  # reused across docs (mirror_one_now)


@dataclass
class Job:
    """Where watcher.run and backfill.run differ."""
    name: str                                   # log prefix only
    feed_tab: str                               # TAB_NEW / TAB_HISTORICAL
    payload: Callable                           # (parsed, d) -> _state payload
    stub_feed_tab: Optional[str] = None         # None = poison_stub's default
    alert: Optional[Callable] = None            # (parsed, d, link) -> None (watcher: urgent/digest)
    write_deadlines: bool = False               # watcher: Compliance Deadlines tab
    well_watch: bool = False                    # watcher: ADR 058 trip-wires
    clear_errors_on_success: bool = False       # backfill: drop the error count
    related_log_tag: bool = False               # watcher logs related docs as "[related]"
    review_flag_alert: bool = False             # watcher: same-day owner email (backfill re-reads history)


def title_config(cfg: dict) -> tuple[list, list, dict]:
    """ADR 065: generic-title detection + the doc_id -> display-name override
    map. An empty/missing block is a safe no-op."""
    title_cfg = cfg.get("document_titles") or {}
    return (title_cfg.get("generic_exact") or [], title_cfg.get("generic_prefixes") or [],
            title_cfg.get("overrides") or {})


def local_path(tmp: str, d: dict) -> str:
    return os.path.join(tmp, f"{d.get('facility_srn', 'N2688')}_{d['doc_id']}.pdf")


def _parse_and_resolve(ctx: RunContext, d: dict, local: str):
    """Download, classify, then the ADR 065 chokepoint: resolve the displayed
    name/date ONCE, before anything downstream reads `d` (the Drive mirror row,
    the Sheet row, the digest/urgent record and the _state payload all inherit
    it). `egle_title` preserves the raw nSITE title BEFORE document_name is
    overwritten. A model-proposed display_title is sanitized against
    name_check; if it can't be made clean it is "" and the plain nSITE title
    is used."""
    did = d["doc_id"]
    nc.download_pdf(ctx.session, d, local)
    is_generic = dt.title_is_generic(d["document_name"], ctx.generic_exact, ctx.generic_prefixes)
    parsed = ctx.parse(
        local, d, RISK_REGISTER, model=ctx.model,
        signal_keywords=SIGNAL_KEYWORDS,
        page_threshold=ctx.cfg["large_doc_page_threshold"],
        max_keyword_pages=ctx.cfg["large_doc_max_keyword_pages"],
        max_tokens=ctx.cfg["classification_max_tokens"],
        title_is_generic=is_generic,
        well_alias_map=ctx.well_alias_map,
    )
    clean_display_title = dt.sanitize_display_title(parsed.display_title) if is_generic else ""
    d["egle_title"] = d["document_name"]
    d["document_name"] = dt.resolve_display_name(
        did, d["egle_title"], clean_display_title, ctx.title_overrides)
    d["document_date"] = parsed.document_date
    d["title_was_generic"] = is_generic
    # Mirror to Drive inline (best-effort; falls back to the nSITE link on any
    # failure — see archiver.mirror_one_now()) so the Sheet row and any alert
    # link somewhere that survives a Google-referer click-through.
    link = av.mirror_one_now(ctx.session, ctx.sheets, ctx.sheet_id, d, ctx.drive_state,
                             local_path=local)
    return parsed, link


def _mark_processed(ctx: RunContext, state: dict, d: dict, payload: dict, job: Job) -> None:
    did = d["doc_id"]
    state["processed"][did] = payload
    if job.clear_errors_on_success:
        state["errors"].pop(did, None)
    sw.mark_processed(ctx.sheets, ctx.sheet_id, did, payload, ctx.now())


def process_one_doc(ctx: RunContext, state: dict, d: dict, job: Job, local: str,
                    purge_first: bool = False) -> str:
    """Run one document through the pipeline. Returns 'processed', 'retry'
    (transient failure: no strike) or 'error' (a strike; stubbed on the
    MAX_ERRORS_PER_DOC-th). Never raises for a per-document failure."""
    did = d["doc_id"]
    try:
        parsed, link = _parse_and_resolve(ctx, d, local)

        # A related-scope facility (ADR 064) gets its Related Documents row and
        # nothing else: no Evidence/Measurements/Deadlines, no WOI routing, no
        # urgent alert, no digest entry, no review flags. Row, then state.
        if sw.is_related(d):
            if purge_first:
                _purge(ctx, did)
            sw.write_related_document(ctx.sheets, ctx.sheet_id, parsed, d, link)
            _mark_processed(ctx, state, d, job.payload(parsed, d), job)
            tag = "related" if job.related_log_tag else f"{parsed.doc_type}/{parsed.severity}"
            print(f"  ok  {d['date_filed']}  [{tag}]  {d['document_name'][:50]}")
            return "processed"

        # WOI Status Reports: route to the exhaustive woi_table_parser and
        # REPLACE parsed.measurements BEFORE is_urgent and write_document (ADR
        # 005). Best-effort: a failure degrades to the generic parse.
        flags: list = []
        try:
            routed = woi_router.route_measurements(
                parsed, local, d, ctx.cfg, alias_map=ctx.well_alias_map, review_flags=flags)
        except Exception as we:  # noqa: BLE001 — degrade to the generic parse
            print(f"  WOI routing failed, using generic parse: {we}")
            routed = None

        # FORCE_REPROCESS (apply): remove this doc's stale rows BEFORE writing
        # the fresh ones, only after a successful download+parse+route.
        if purge_first:
            _purge(ctx, did)

        # Crash-safe order: durable Sheet row first, the 'processed' state event
        # last (a crash before it re-processes the doc next run — a duplicate
        # row, never a silent drop; ADR 006). Alerts between them are
        # best-effort.
        sw.write_document(ctx.sheets, ctx.sheet_id, parsed, d, link, RISK_NAMES,
                          feed_tab=job.feed_tab)

        # Structured compliance deadlines (ADR 025). Best-effort.
        if job.write_deadlines:
            deadlines = getattr(parsed, "deadlines", None)
            if deadlines:
                try:
                    sw.write_compliance_deadlines(
                        ctx.sheets, ctx.sheet_id, deadlines, "nSITE",
                        d.get("facility_name", ""), link, ctx.now())
                except Exception as de:  # noqa: BLE001 — deadline tab is best-effort
                    print(f"  compliance-deadline write skipped (doc still recorded): {de}")

        if routed is not None:
            try:
                sw.ensure_woi_tabs(ctx.sheets, ctx.sheet_id)
                sw.write_woi_summary(ctx.sheets, ctx.sheet_id, routed["summary"], d, link)
            except Exception as we:  # noqa: BLE001 — summary tab is best-effort
                print(f"  WOI summary-tab write skipped (doc still recorded): {we}")
            # Named single-well trip-wires (ADR 058). Best-effort.
            if job.well_watch:
                try:
                    well_watch.check_and_alert(routed.get("readings") or [], d, link, ctx.cfg)
                except Exception as ww:  # noqa: BLE001 — alert is best-effort
                    print(f"  well_watch skipped (doc still recorded): {ww}")

        if job.alert is not None:
            job.alert(parsed, d, link)

        # Values the monitor deliberately did not use (above-ceiling
        # temperature, rejected WOI table, unreadable pages) go to the Parser
        # Review Flags tab, after the document row and any alert. The owner-only
        # same-day email for an above-ceiling value (gated off by default) is
        # watcher-only: backfill re-reads history and never alerts on it.
        flags = rf.collect(flags, parsed, ctx.cfg)
        rf.log_for_document(ctx.sheets, flags, d, link, ctx.now())
        if job.review_flag_alert:
            on.alert_review_flags(ctx.sheets, flags, d, link, ctx.cfg, ctx.now())

        _mark_processed(ctx, state, d, job.payload(parsed, d), job)
        print(f"  ok  {d['date_filed']}  [{parsed.doc_type}/{parsed.severity}]  "
              f"{d['document_name'][:50]}")
        return "processed"
    except Exception as e:  # noqa: BLE001
        if rp.is_transient(e):
            # Infrastructure/quota error (API cap, 429, 5xx, network) — not the
            # document's fault. Retry later WITHOUT a poison strike (the
            # 2026-07-07 false-poison incident).
            print(f"  RETRY (transient, no strike) {d['document_name'][:50]}: {e}")
            return "retry"
        cnt = state["errors"].get(did, 0) + 1
        state["errors"][did] = cnt
        sw.mark_error(ctx.sheets, ctx.sheet_id, did, cnt, ctx.now())
        print(f"  ERR {d['document_name'][:50]}: {e} "
              f"(attempt {cnt}/{MAX_ERRORS_PER_DOC})")
        # Terminal failure -> stub feed row + 'skipped', whichever job caused
        # the last strike (issue #82).
        kw = {"feed_tab": job.stub_feed_tab} if job.stub_feed_tab else {}
        poison_stub.stub_if_poisoned(ctx.sheets, ctx.sheet_id, state, d, cnt, e, ctx.now(),
                                     title_overrides=ctx.title_overrides, cfg=ctx.cfg, **kw)
        return "error"
    finally:
        if os.path.exists(local):
            os.remove(local)


def _purge(ctx: RunContext, did: str) -> None:
    purged = sw.purge_doc_rows(ctx.sheets, ctx.sheet_id, did)
    print(f"  [FORCE] purged {sum(purged.values())} stale row(s) {purged} "
          f"for {did} before re-extract")
