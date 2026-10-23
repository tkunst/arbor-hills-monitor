"""
doc_pipeline_liveness.py — the nSITE document pipeline's liveness / silent-
stall guard (docs/overnight-coder-handoffs/stall-hardening.md, part (a)):
the pure health-state update, the pure alarm decision and the notice body.
The orchestration wrapper (`_check_liveness`) lives in watcher.py, mirroring
how gfl_air_liveness.py / gfl_air_watcher._check_liveness are split.

Why this exists (2026-08-02): the weekly digest silently stopped for two
weeks. Two facilities added on 2026-07-24 pushed the unprocessed-doc count
over the watcher's anti-stampede cap, so the watcher deferred EVERY run to
backfill; backfill's nightly schedule was separately off; and nothing alarmed
on "zero documents processed for two weeks" because the document pipeline —
unlike the GFL perimeter-air stream — had no liveness check at all.

Two failure shapes, two signals, both kept in ONE `_meta` singleton
(`doc_pipeline_health`, written by watcher.run through the existing
read_state/write_meta helpers):

  consecutive_deferrals  runs in a row in which the watcher deferred >= 1
                         unprocessed document to backfill (the anti-stampede
                         cap). Reset to 0 by any run that deferred nothing.
                         A backlog that is never drained (the 2026-07-24
                         shape) trips this after `max_consecutive_deferrals`
                         runs — days, since the watcher runs daily.
  last_activity_at       the last run (UTC) on which the pipeline did its
                         job: processed >= 1 document, made a terminal
                         decision on one (a poison strike — the pipeline IS
                         working through its queue), or had nothing
                         unprocessed at all (an honest quiet day). A run that
                         had unprocessed documents and made no progress on
                         any of them — every one deferred, or every one a
                         transient failure (a dead or throttled API key) —
                         does NOT advance it. So "days since last activity"
                         measures the pipeline, not EGLE's filing rate: a
                         quiet month never trips it, three days of a filing
                         sitting unprocessed does.

Once-per-episode gates (the `warned_as_of` idiom in gfl_air_liveness):
  warned_stale_for       the `last_activity_at` value the stale alarm was
                         sent for. The pipeline recovering advances
                         last_activity_at, which re-arms the gate.
  warned_deferrals       True once the deferral alarm went out for the
                         current deferral episode; cleared with the counter.

Everything here is pure (now injected, no I/O). The notice is a pipeline-
HEALTH email to the owner only — never formatted like an exceedance or an
urgent document alert.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

META_KEY = "doc_pipeline_health"

# Config defaults (config.yml `watcher:`; 0 disables that signal).
DEFAULT_MAX_STALE_DAYS = 3
DEFAULT_MAX_CONSECUTIVE_DEFERRALS = 3

_ACTIVITY_FMT = "%Y-%m-%dT%H:%M:%SZ"      # stored UTC, second precision


def format_activity(now_utc: datetime) -> str:
    """The stored form of an activity timestamp (UTC, `_ACTIVITY_FMT`)."""
    return now_utc.astimezone(timezone.utc).strftime(_ACTIVITY_FMT)


def parse_activity(s) -> Optional[datetime]:
    """Parse a stored activity timestamp back to an aware UTC datetime, or None
    on blank/garbage (never raises — a parse miss must not fire a misleading
    alarm or break the run)."""
    if not s or not str(s).strip():
        return None
    try:
        return datetime.strptime(str(s).strip(), _ACTIVITY_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def update_health(health: Optional[dict], *, now_utc: datetime, unprocessed: int,
                  processed: int, struck: int, deferred: int) -> dict:
    """Pure. The health cell after a watcher run that saw `unprocessed`
    documents awaiting work, processed `processed` of them, took a poison
    strike on `struck`, and deferred `deferred` to backfill. Returns a NEW dict
    (the stored one is never mutated); unknown keys are carried through."""
    h = dict(health or {})
    prev_deferrals = _int(h.get("consecutive_deferrals"))
    if deferred > 0:
        h["consecutive_deferrals"] = prev_deferrals + 1
    else:
        h["consecutive_deferrals"] = 0
        h.pop("warned_deferrals", None)       # episode over: re-arm the gate
    if unprocessed == 0 or processed > 0 or struck > 0:
        h["last_activity_at"] = format_activity(now_utc)
    else:
        h.setdefault("last_activity_at", "")
    return h


@dataclass(frozen=True)
class Decision:
    should_warn: bool
    stale: bool                 # the stale signal is tripped (whether or not already warned)
    deferrals_tripped: bool     # the deferral signal is tripped (ditto)
    stale_days: Optional[int]   # whole days since last activity; None = unquantifiable
    deferrals: int
    unquantifiable: bool        # last_activity_at blank/unparseable


def liveness_decision(health: Optional[dict], now_utc: datetime, max_stale_days: int,
                      max_consecutive_deferrals: int) -> Decision:
    """Pure. Whether to send the ONE pipeline-health notice this run.

    The stale signal trips when `last_activity_at` is at least `max_stale_days`
    old and no notice was already sent for that exact value (the once-per-
    episode gate — self-resetting because activity re-stamps the value). A
    blank or unparseable `last_activity_at` never trips it: we do not fire a
    'stale' alarm we can't quantify (the caller logs that case loudly).

    The deferral signal trips when `consecutive_deferrals` reaches
    `max_consecutive_deferrals` and no notice was already sent for this
    deferral episode (`warned_deferrals`; cleared when the counter resets).

    A threshold of 0 (or less) disables that signal. `should_warn` is True iff
    at least one signal trips AND is not already warned — the two signals share
    one email when they trip on the same run."""
    h = health or {}
    last = str(h.get("last_activity_at") or "").strip()
    dt = parse_activity(last)
    stale_days = None if dt is None else int((now_utc - dt).total_seconds() // 86400)
    stale = (max_stale_days > 0 and stale_days is not None and stale_days >= max_stale_days)
    stale_unwarned = stale and str(h.get("warned_stale_for") or "") != last

    deferrals = _int(h.get("consecutive_deferrals"))
    tripped = max_consecutive_deferrals > 0 and deferrals >= max_consecutive_deferrals
    deferrals_unwarned = tripped and not bool(h.get("warned_deferrals"))

    return Decision(
        should_warn=stale_unwarned or deferrals_unwarned,
        stale=stale, deferrals_tripped=tripped,
        stale_days=stale_days, deferrals=deferrals,
        unquantifiable=dt is None,
    )


def mark_warned(health: Optional[dict], decision: Decision) -> dict:
    """Pure. The health cell after the notice for `decision` was SENT: every
    signal that tripped is marked warned for its current episode. Returns a
    NEW dict."""
    h = dict(health or {})
    if decision.stale:
        h["warned_stale_for"] = str(h.get("last_activity_at") or "")
    if decision.deferrals_tripped:
        h["warned_deferrals"] = True
    return h


def format_liveness_body(decision: Decision, health: Optional[dict], max_stale_days: int,
                         max_consecutive_deferrals: int, cap: int) -> str:
    """The pipeline-health notice body — deliberately NOT the urgent-alert or
    digest formatter, and labeled a health notice in its first line so it can
    never be mistaken for a document alert."""
    h = health or {}
    last = str(h.get("last_activity_at") or "").strip() or "(never recorded)"
    lines = [
        "Arbor Hills monitor — nSITE DOCUMENT PIPELINE LIVENESS / health notice.\n",
        "This is NOT a document alert and NOT a reading. It says the daily watcher "
        "is not getting through its document queue.\n",
    ]
    if decision.deferrals_tripped:
        lines.append(
            f"- {decision.deferrals} consecutive daily run(s) have deferred unprocessed "
            f"documents to the backfill job (alert threshold: {max_consecutive_deferrals}). "
            f"The anti-stampede cap (watcher.max_new_docs_per_run = {cap}) defers a "
            "backlog larger than the cap so historical filings don't stampede into the "
            "live feed — but a backlog only drains if backfill actually runs. On "
            "2026-07-24 two newly added facilities tripped this and backfill's nightly "
            "schedule was off, so the watcher processed nothing for two weeks.\n")
    if decision.stale:
        lines.append(
            f"- No document-pipeline activity for {decision.stale_days} day(s) (alert "
            f"threshold: {max_stale_days}): every daily run in that window found "
            "unprocessed documents and made progress on none of them (each one deferred, "
            "or each one a transient failure — a dead/throttled API key looks exactly like "
            "this). A quiet week with nothing new to process does NOT count as a stall.\n")
    lines.append(f"Last recorded pipeline activity (UTC): {last}\n")
    lines.append(
        "Check: the latest `daily` workflow run's log (how many unprocessed documents it "
        "saw and whether it said 'deferring doc processing to the backfill job' or "
        "'RETRY (transient, no strike)'), whether the `backfill` workflow's schedule is "
        "enabled, and the Anthropic key/usage cap.\n")
    lines.append(
        "(You will not get another notice for this same episode — each signal fires "
        "once per episode and re-arms only after the pipeline makes progress again.)")
    return "\n".join(lines)


def liveness_subject(decision: Decision) -> str:
    """A health-notice subject: never '[URGENT]', never an exceedance."""
    parts = []
    if decision.deferrals_tripped:
        parts.append(f"{decision.deferrals} consecutive runs deferred to backfill")
    if decision.stale:
        parts.append(f"no activity in {decision.stale_days} day(s)")
    return ("[Arbor Hills monitor health] nSITE document pipeline appears STALLED — "
            + "; ".join(parts))
