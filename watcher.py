"""
watcher.py — daily run: new nSITE filings + alerts (+ WDS Stream C when enabled).

  - New docs: anything in the nSITE list not already in _state.
    Download -> parse -> mirror to Drive inline, best-effort (ADR 007 addendum
    — the Sheet row and alert link to the durable Drive copy when the mirror
    succeeds, the nSITE link otherwise; never blocks on a Drive problem) ->
    Sheet row -> alert/digest -> THEN append the 'processed' state event
    (crash-safe order). Urgent docs trigger a same-day email; everything else
    accrues into the Sunday digest.
  - Digest: on Sunday, email the accumulated non-urgent items and clear them.
    An urgent item ALSO gets a labeled recap in that same Sunday email (a
    reader who only reads the digest would otherwise never see it) — the
    same-day [URGENT] send itself is unchanged; this is purely additive.

MMPC meeting documents are captured separately by Mirror D (mmpc_archiver.py,
ADR 010) on its own schedule; the watcher's old in-window "minutes likely posted,
go check" reminder was retired once Mirror D made it redundant (ADR 013).

State lives in the Sheet's _state (per-doc event log) and _meta (digest /
last-run singletons) tabs — not a Drive file (see ADR 006). Runs daily at 6am ET.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import nsite_client as nc
import email_alerts as ea
import well_aliases
import owner_notices as on
import woi_router  # noqa: F401 — tests patch through this module name
import well_watch  # noqa: F401 — tests patch through this module name
import archiver as av  # noqa: F401 — tests patch through this module name
import poison_stub
import doc_pipeline as dp
import doc_pipeline_liveness as dl
from egle_doc_parser import parse_document
from risk_register import RISK_REGISTER, RISK_NAMES
from config_loader import load_config

MAX_ERRORS_PER_DOC = poison_stub.MAX_ERRORS_PER_DOC  # give up on a poison doc after this many failures (cf. backfill)


def _today():
    return datetime.now(_ET).date() if _ET else datetime.now().date()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _digest_record(parsed, d: dict, link: str) -> dict:
    return {
        "date_filed": d["date_filed"],
        "document_name": d["document_name"],
        "doc_type": parsed.doc_type,
        "severity": parsed.severity,
        "risks": parsed.risks,
        "key_data_point": parsed.key_data_point,
        "link": link,
        # ADR 065: only ever True for a doc whose nSITE title was generic AND
        # whose text reads as a substantive EGLE letter — see
        # email_alerts.is_correspondence_letter + format_digest_body's pinned
        # "CORRESPONDENCE & ENFORCEMENT" section.
        "is_correspondence": bool(d.get("title_was_generic")) and ea.is_correspondence_letter(parsed.full_text),
    }


def _urgent_recap_record(parsed, d: dict, link: str, sent_at: str) -> dict:
    return {
        "date_filed": d["date_filed"],
        "document_name": d["document_name"],
        "doc_type": parsed.doc_type,
        "severity": parsed.severity,
        "risks": parsed.risks,
        "key_data_point": parsed.key_data_point,
        "link": link,
        "urgent_sent_at": sent_at,
    }


def _record_to_item(rec: dict) -> dict:
    parsed = SimpleNamespace(
        doc_type=rec["doc_type"],
        severity=rec["severity"],
        risks=rec.get("risks", []),
        key_data_point=rec.get("key_data_point", ""),
        summary=rec.get("key_data_point", ""),
        is_correspondence=rec.get("is_correspondence", False),
    )
    meta = {"date_filed": rec["date_filed"], "document_name": rec["document_name"]}
    if "urgent_sent_at" in rec:
        meta["urgent_sent_at"] = rec["urgent_sent_at"]
    return {"parsed": parsed, "metadata": meta, "link": rec["link"]}


def _route_urgent_or_digest(parsed, d: dict, link: str, cfg: dict, state: dict,
                             sheets, sheet_id: str) -> None:
    """Urgent -> same-day email, ALSO queued for the next Sunday's labeled
    recap section; routine -> queued for the next Sunday digest. A failed OR
    skipped (SMTP unconfigured / no recipients) urgent send is dropped from
    BOTH queues — matches the pre-existing behavior of not queuing a failed
    urgent send to pending_digest either (an alert that never went out has
    nothing to recap).

    The try/except covers ONLY the send call, matching the pre-existing
    scope (this branch had no write_meta call at all before this feature) —
    the append + write_meta run OUTSIDE it, unguarded, same as the routine
    branch below. That's deliberate: if write_meta itself fails after a
    successful send, it must propagate to the caller's outer per-doc
    try/except (transient -> retried next run, same as any other _meta write
    failure), not get swallowed here and mislabeled as "ALERT FAILED to
    send" when the email actually went out."""
    if ea.is_urgent(parsed, cfg):
        sent_at = _now()
        try:
            sent = ea.send_urgent_alert(parsed, d, link, cfg)
        except Exception as ae:  # noqa: BLE001 — notification is best-effort
            print(f"  URGENT ALERT FAILED to send (doc still recorded): "
                  f"{d['document_name'][:50]}: {ae}")
            on.record_send_failure(sheets, "urgent alert", f"raised {type(ae).__name__}",
                                   d, link, sent_at)
            return
        if not sent:
            # send_email() no-op'd (SMTP not configured / no recipients) --
            # no exception, but nothing actually went out either, so there is
            # nothing to recap (see email_alerts.send_email's docstring).
            print(f"  URGENT ALERT skipped, SMTP not configured (doc still "
                  f"recorded): {d['document_name'][:50]}")
            on.record_send_failure(sheets, "urgent alert", "not sent (SMTP or recipients)",
                                   d, link, sent_at)
            return
        state["pending_urgent_recap"].append(
            _urgent_recap_record(parsed, d, link, sent_at))
        sw.write_meta(sheets, sheet_id, state)
        print(f"  URGENT emailed: {d['document_name'][:50]}")
    else:
        state["pending_digest"].append(_digest_record(parsed, d, link))
        sw.write_meta(sheets, sheet_id, state)


def _processed_payload(parsed, d: dict) -> dict:
    return {
        "document_name": d["document_name"],
        "date_filed": d["date_filed"],
        "doc_type": parsed.doc_type,
        "severity": parsed.severity,
        "risks": parsed.risks,
        # ADR 025 (Gap G1-A-1): carry the extracted claim + summary in the
        # append-only _state payload so it survives a Feed-tab clear — the
        # key_data_point (incl. enforcement-deadline text) is no longer
        # mutable-feed-only.
        "key_data_point": parsed.key_data_point,
        "summary": parsed.summary,
        # ADR 065: carried so archiver.py's nightly catch-up run (which only
        # has this payload + the live nSITE list, never a fresh parse) can
        # still write the resolved name/date into a later Archived PDFs row.
        "document_date": d.get("document_date", ""),
        "egle_title": d.get("egle_title") or d["document_name"],
    }


def _split_by_history(new_docs: list, docs: list, state: dict) -> tuple[list, list]:
    """Pure (stall-hardening part (c)). Split the unprocessed documents into
    those from ESTABLISHED facilities — a facility with at least one document
    already `processed` or terminally `skipped` in state, i.e. one the pipeline
    has been through before — and those from facilities with NO processing
    history (a facility just added to `facilities:`, whose years of filings
    are backfill's job, never the live watcher's). Order is preserved; a
    document with no facility_srn is treated as history-less."""
    history = set()
    for d in docs:
        did = d.get("doc_id")
        if did in state.get("processed", {}) or did in state.get("skipped", {}):
            history.add(d.get("facility_srn"))
    history.discard(None)
    established = [d for d in new_docs if d.get("facility_srn") in history]
    fresh = [d for d in new_docs if d.get("facility_srn") not in history]
    return established, fresh


def _check_liveness(sheets, sheet_id: str, cfg: dict, state: dict, *, unprocessed: int,
                    processed: int, struck: int, deferred: int, cap: int,
                    now_utc=None) -> None:
    """The document pipeline's liveness / silent-stall guard (stall-hardening
    part (a); pure parts in doc_pipeline_liveness.py). Runs after the doc loop,
    every run: records this run's outcome in `_meta.doc_pipeline_health`
    (last activity, consecutive deferrals) and sends ONE owner-only health
    notice per episode when the pipeline has deferred its backlog for
    `watcher.max_consecutive_deferrals` runs in a row, or has made no progress
    on unprocessed documents for `watcher.max_stale_days` days.

    Owner-only on purpose (owner_notices.send_owner_email, never the alert /
    digest lists): this is a pipeline-health notice, not a document alert.

    FULLY ISOLATED + best-effort, like gfl_air_watcher._check_liveness: every
    failure here is logged (class only) and never propagates — a liveness bug
    must not break the run. The warned markers are set only AFTER a successful
    send, so a failed send retries next run until it lands exactly once. The
    health dict is mutated into `state` first, so the unconditional end-of-run
    write_meta persists it even if the write here fails."""
    try:
        wcfg = cfg.get("watcher") or {}
        max_stale = int(wcfg.get("max_stale_days", dl.DEFAULT_MAX_STALE_DAYS))
        max_def = int(wcfg.get("max_consecutive_deferrals",
                               dl.DEFAULT_MAX_CONSECUTIVE_DEFERRALS))
        now_utc = now_utc or datetime.now(timezone.utc)
        prev = state.get(dl.META_KEY) or {}
        health = dl.update_health(prev, now_utc=now_utc, unprocessed=unprocessed,
                                  processed=processed, struck=struck, deferred=deferred)
        state[dl.META_KEY] = health
        was_warned = bool(prev.get("warned_deferrals")) or (
            bool(prev.get("warned_stale_for"))
            and prev.get("warned_stale_for") == prev.get("last_activity_at"))
        recovered = was_warned and (
            health["consecutive_deferrals"] == 0
            and health.get("last_activity_at") != prev.get("last_activity_at"))
        if recovered:
            print("[watcher] liveness: document pipeline made progress again — "
                  "health notice re-armed (log only, no email).")
        try:
            sw.write_meta(sheets, sheet_id, state)
        except Exception as we:  # noqa: BLE001 — the end-of-run write_meta carries it
            print(f"[watcher] liveness: health write deferred to end of run: "
                  f"{type(we).__name__}")
        decision = dl.liveness_decision(health, now_utc, max_stale, max_def)
        if decision.unquantifiable and unprocessed and not processed and not struck:
            print("[watcher] liveness: no parseable last_activity_at on record while "
                  "unprocessed documents made no progress — cannot quantify staleness "
                  "yet (first run with this guard?); the deferral counter still applies.")
        if not decision.should_warn:
            return
        subject = dl.liveness_subject(decision)
        body = dl.format_liveness_body(decision, health, max_stale, max_def, cap)
        try:
            sent = on.send_owner_email(subject, body, cfg)
        except Exception as se:  # noqa: BLE001 — notice best-effort; markers NOT set → retries
            print(f"[watcher] liveness: STALL detected (deferrals={decision.deferrals}, "
                  f"stale_days={decision.stale_days}) but the owner notice FAILED to "
                  f"send (will retry next run): {type(se).__name__}")
            return
        if not sent:
            print(f"[watcher] liveness: STALL detected (deferrals={decision.deferrals}, "
                  f"stale_days={decision.stale_days}) but no owner address is configured; "
                  "notice not sent.")
            return
        state[dl.META_KEY] = dl.mark_warned(health, decision)
        try:
            sw.write_meta(sheets, sheet_id, state)
        except Exception as we:  # noqa: BLE001
            print(f"[watcher] liveness: warned-marker write deferred to end of run: "
                  f"{type(we).__name__}")
        print(f"[watcher] liveness: STALL notice emailed to the owner (deferrals="
              f"{decision.deferrals}, stale_days={decision.stale_days}); episode marked.")
    except Exception as e:  # noqa: BLE001 — liveness must NEVER break the run
        print(f"[watcher] liveness check errored (ignored — run unaffected): "
              f"{type(e).__name__}")


def run() -> int:
    cfg = load_config()
    sheet_id = os.environ["GSHEET_ID"]
    model = cfg["anthropic_model"]
    today = _today()
    # ADR 065: generic-title detection + the doc_id -> display-name override
    # map. An empty/missing block is a safe no-op (every doc's title_is_generic
    # is False, so the Document Name column renders exactly as before).
    generic_exact, generic_prefixes, title_overrides = dp.title_config(cfg)
    # ADR 068: the curated well-ID alias map, loaded ONCE here — outside the
    # per-doc try — so a missing/malformed map fails the run red instead of
    # becoming per-doc poison strikes or a silent WOI-routing fallback.
    well_alias_map = well_aliases.load_alias_map()
    # ADR 068 addendum 2026-10-22: the registry (probes, ruled wells) and the
    # check that the generated alias map still equals it. Both fail loudly.
    well_registry = well_aliases.load_registry()
    well_aliases.check_alias_map_matches(well_alias_map, well_registry)

    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)

    state = sw.read_state(sheets, sheet_id)

    session = nc.make_session()
    docs = nc.fetch_all_documents(session, cfg)
    if not docs:
        print("[watcher] nSITE returned 0 documents — aborting (transient?).")
        return 1

    # Skip docs already processed, terminally skipped (unprocessable source —
    # legacy .doc, encrypted PDF, raw image), AND poison docs that have failed
    # MAX_ERRORS_PER_DOC times. Without the skipped check, mark_skipped() clears
    # error counts in read_state(), so skipped docs slip through as "new" on
    # every run and trigger fresh download-fail retry loops.
    def done_or_poisoned(d):
        did = d["doc_id"]
        return (did in state["processed"] or did in state["skipped"]
                or state["errors"].get(did, 0) >= MAX_ERRORS_PER_DOC)

    new_docs = [d for d in docs if not done_or_poisoned(d)]
    unprocessed = len(new_docs)

    # Anti-stampede guard: the watcher is for incremental new filings. A backlog
    # larger than the cap means backfill hasn't cleared history yet — defer doc
    # processing to backfill (don't stampede historical docs into the live feed,
    # don't fire urgent alerts on years-old exceedances, don't overflow the
    # single-cell _meta pending_digest). Digest housekeeping still runs.
    cap = (cfg.get("watcher") or {}).get("max_new_docs_per_run", 25)
    deferred = 0
    if len(new_docs) > cap:
        # Stall-hardening part (c): a brand-new facility's historical backlog
        # must never freeze live monitoring of every OTHER, already-established
        # facility (2026-07-24: two new facilities' 73 never-seen filings
        # deferred N2688's own new filings for two weeks). Over the cap, the
        # documents from facilities with NO processing history are the backlog
        # — deferred to backfill exactly as before — while the established
        # facilities' documents are still processed when they alone fit under
        # the cap. An established facility's own over-cap burst keeps today's
        # conservative deferral (now alarmed by the liveness guard below).
        established, fresh = _split_by_history(new_docs, docs, state)
        if fresh and established and len(established) <= cap:
            srns = sorted({str(d.get("facility_srn")) for d in fresh})
            print(f"[watcher] {len(new_docs)} unprocessed docs > cap {cap}: {len(fresh)} "
                  f"belong to facility(ies) with no processing history yet "
                  f"({', '.join(srns)}) — deferring that backlog to the backfill job; "
                  f"processing the {len(established)} from established facilities.")
            deferred = len(fresh)
            new_docs = established
        else:
            print(f"[watcher] {len(new_docs)} unprocessed docs > cap {cap}: backfill "
                  f"still in progress — deferring doc processing to the backfill job.")
            deferred = len(new_docs)
            new_docs = []
    else:
        print(f"[watcher] {len(new_docs)} new document(s).")
    ctx = dp.RunContext(
        cfg=cfg, sheets=sheets, sheet_id=sheet_id, session=session, model=model,
        well_alias_map=well_alias_map, well_registry=well_registry, parse=parse_document,
        now=_now,
        generic_exact=generic_exact, generic_prefixes=generic_prefixes,
        title_overrides=title_overrides)
    # The watcher's half of the shared per-document pipeline (doc_pipeline.py):
    # New Documents feed, compliance deadlines, well_watch, and the same-day
    # urgent alert / digest queue.
    job = dp.Job(
        name="watcher", feed_tab=sw.TAB_NEW, stub_feed_tab=sw.TAB_NEW,
        payload=_processed_payload, write_deadlines=True, well_watch=True,
        related_log_tag=True, review_flag_alert=True,
        alert=lambda parsed, d, link: _route_urgent_or_digest(
            parsed, d, link, cfg, state, sheets, sheet_id))
    tmp = tempfile.gettempdir()
    outcomes: dict = {}
    for d in new_docs:
        outcome = dp.process_one_doc(ctx, state, d, job, dp.local_path(tmp, d))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    # Liveness / silent-stall guard (stall-hardening part (a)): record this
    # run's outcome and alarm, once per episode, on a backlog that keeps being
    # deferred or a queue that keeps making no progress. Best-effort, isolated.
    _check_liveness(sheets, sheet_id, cfg, state, unprocessed=unprocessed,
                    processed=outcomes.get("processed", 0), struck=outcomes.get("error", 0),
                    deferred=deferred, cap=cap)

    # MMPC meeting documents are archived by Mirror D (mmpc-archive.yml) on its
    # own schedule — the watcher no longer polls for or emails MMPC minutes (the
    # "go check" reminder was retired once Mirror D made it redundant; ADR 013).

    # --- WDS (Stream C — EGLE solid-waste system) polling, only when enabled ---
    # Off by default (cfg.wds.enabled). Gated import so a fault in the WDS modules
    # cannot affect the main nSITE path while Stream C is disabled. check_wds
    # is self-protecting: first run silently baselines (no alert flood), a
    # bad/short fetch is skipped not diffed, and it uses its OWN classifier (never
    # the temperature-oriented is_urgent). Its notable/watch items land in
    # pending_digest below; urgent items email immediately inside check_wds.
    if (cfg.get("wds") or {}).get("enabled"):
        try:
            import wds_watcher as ww
            sw.ensure_wds_tabs(sheets, sheet_id)
            # Recovery (ADR 024): the live diff cursor is _meta.wds_seen, but if
            # it was cleared/corrupted, restore it from the append-only _wds_seen
            # backup log so a _meta wipe doesn't re-fire every WDS record as "new"
            # (Gap G1-C-1). No-op on a genuine first run (both empty).
            if not state.get("wds_seen"):
                recovered = sw.read_wds_seen_log_latest(sheets, sheet_id)
                if recovered:
                    state["wds_seen"] = recovered
                    print(f"[watcher] WDS: _meta.wds_seen empty — recovered "
                          f"{len(recovered)} collection(s) from _wds_seen log")
            def _wds_on_row(ev):
                sw.write_wds_event(
                    sheets, sheet_id, ev, RISK_NAMES, feed_tab=sw.TAB_WDS_NEW)
                # ADR 025: compliance_actions events carry a structured deadline
                # mapped from their WDS fields; land it in the Compliance Deadlines
                # tab. Best-effort — never block the feed/alert write.
                if ev.get("deadline"):
                    try:
                        sw.write_compliance_deadlines(
                            sheets, sheet_id, [ev["deadline"]],
                            "WDS compliance_actions", "Arbor Hills Landfill, Inc.",
                            ev.get("link", ""), _now())
                    except Exception as de:  # noqa: BLE001 — deadline tab best-effort
                        print(f"[wds] compliance-deadline write skipped: {de}")

            ww.check_wds(state, cfg, ea.send_email, on_row=_wds_on_row)
            sw.write_meta(sheets, sheet_id, state)  # persist wds_seen + digest adds
            # Append-only backup of the (now-updated) cursor — recoverable if
            # _meta is ever cleared. Best-effort; seeds the log on the first run.
            try:
                sw.append_wds_seen_log(
                    sheets, sheet_id, state.get("wds_seen") or {}, _now())
            except Exception as le:  # noqa: BLE001 — backup log is best-effort
                print(f"[watcher] WDS: _wds_seen backup append skipped: {le}")
        except Exception as e:  # noqa: BLE001 — Stream C must never break the watcher
            print(f"[watcher] WDS (Stream C) step skipped: {e}")

    # --- Sunday digest ---
    if today.weekday() == 6:
        # (1) Document digest — the coalition email to the full alert_recipients
        # list, unchanged. Sent when there are new documents OR urgent items to
        # recap (a week with only a recapped urgent item and nothing new is not
        # an empty digest); still no email at all on a genuinely quiet week.
        if state["pending_digest"] or state["pending_urgent_recap"]:
            items = [_record_to_item(r) for r in state["pending_digest"]]
            recap_items = [_record_to_item(r) for r in state["pending_urgent_recap"]]
            try:
                digest_sent = ea.send_digest(items, cfg, urgent_recap=recap_items)
            except Exception as de:
                # Record it for the owner weekly email, then fail the run as
                # before (the queue is kept and retried next Sunday).
                on.record_send_failure(sheets, "Sunday digest", f"raised {type(de).__name__}",
                                       {}, "", _now())
                raise
            if digest_sent is False:
                on.record_send_failure(sheets, "Sunday digest", "not sent (SMTP or recipients)",
                                       {}, "", _now())
            state["pending_digest"] = []
            state["pending_urgent_recap"] = []
            sw.write_meta(sheets, sheet_id, state)
            print(f"[watcher] sent Sunday digest ({len(items)} item(s), "
                  f"{len(recap_items)} urgent recap item(s)).")

        # (2) "Upcoming Activities (next 14 days)" from the PRIVATE Sheet
        # (GSHEET_ID_PRIVATE — service-account-only, never operator-visible, so it
        # may carry strategy-flavored key dates the public case-file Sheet must
        # not). Sent as its OWN email, scoped to `upcoming.recipients` (Trisha only
        # to start; NEVER the operator) — deliberately NOT folded into the coalition
        # digest above, so the private dates can't reach the wider list. Best-effort:
        # a missing secret / unreachable Sheet / send failure degrades to no section,
        # never a crash. Sunday only.
        try:
            priv_id = os.environ.get("GSHEET_ID_PRIVATE")
            if priv_id:
                import upcoming as up
                priv = dc.sheets_service()
                up.ensure_upcoming_tab(priv, priv_id)
                upcoming_block = up.render_upcoming(up.fetch_upcoming(priv, priv_id), today)
                if upcoming_block:
                    ea.send_upcoming(upcoming_block, cfg)
                    print("[watcher] sent upcoming-activities section (scoped).")
        except Exception as e:  # noqa: BLE001 — the digest must never break on this
            print(f"[watcher] upcoming-activities section skipped: {e}")

        # (3) The owner-only weekly housekeeping email (owner_notices.py):
        # unreadable filings, unrouted hand-curated rows, failed sends, parser
        # review flags. Owner addresses only, never a list. Gated on
        # owner_weekly.enabled; isolated so it can never fail the run (class
        # only in the public log).
        try:
            outcome = on.send_weekly(sheets, sheet_id, state, cfg, today, _now())
            print(f"[watcher] owner weekly email: {outcome}")
        except Exception as e:  # noqa: BLE001 — isolated, retried next Sunday
            print(f"[watcher] owner weekly email skipped: {type(e).__name__}")

    try:
        sw.rebuild_risk_register_tab(sheets, sheet_id, RISK_REGISTER)
    except Exception as e:  # noqa: BLE001
        print(f"[watcher] risk-register rebuild skipped: {e}")

    if (cfg.get("wds") or {}).get("enabled"):
        try:
            sw.rebuild_all_evidence_tab(sheets, sheet_id)
        except Exception as e:  # noqa: BLE001
            print(f"[watcher] all-evidence rebuild skipped: {e}")

    state["last_run"] = _now()
    sw.write_meta(sheets, sheet_id, state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
