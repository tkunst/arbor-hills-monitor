"""
gfl_air_watcher.py — Stream E: daily poll of GFL's public perimeter air-monitoring
ArcGIS feed (gfl_air_client), routing new readings into the case file with
exceedance alerts. This is the first source that yields real fenceline READINGS
(R3 odor / R4 air), not documents. See docs/decisions/014-gfl-perimeter-air-stream-e.md.

Standalone + self-terminating, the same shape as pfas_watcher.py, and runs from
its OWN workflow (.github/workflows/gfl-air.yml, own concurrency group). GATED ON
gfl_air.enabled (false by default): a brand-new poller against a live external
system ships disabled and a human flips it on — this loop never does (overnight-
coder procedure). Until enabled: true is on main, every run is a quiet no-op.

WHAT IT DOES each run:
  - Reads the incremental cursor = max(OBJECTID) from the "GFL Air" tab (that tab
    is BOTH the small human snapshot AND the cursor store; see sheet_writer). NO
    _meta writes — a separate workflow must never write _meta (it would clobber the
    daily watcher's wds_seen/pending_digest), so unlike WDS-inside-the-watcher, the
    state lives in this stream's own tab (the pfas_watcher precedent).
  - FIRST run (empty tab) → baseline: record the current latest-per-station
    snapshot, set the cursor, alert on NONE (WDS Rule B — flipping enabled on can't
    blast years of history even though the readings table is ~214k rows). If that
    baseline fetch fails on the activation run, exit 1 (loud) so it surfaces.
  - Incremental run → fetch readings with OBJECTID > cursor (skip-proof, dedup-free
    monotone cursor), classify each with the stream's OWN action-level classifier
    (NOT email_alerts.is_urgent, which is temperature-specific — WDS Rule D), write
    the selected measurements to the shared Measurements tab, refresh the snapshot
    (advancing the cursor), and send ONE same-day email if any reading crossed an
    action level.
  - OVER-CAP guard → if a poll returns more readings than max_new_readings_per_run
    (e.g. the source reinserts the whole table, bumping every OBJECTID), re-baseline
    silently and warn instead of stampeding ~214k rows into the case file (mirrors
    WDS Rule B(ii) / watcher.max_new_docs_per_run).

Alert thresholds are CONSERVATIVE + config-driven and default to published action
levels (Michigan EGLE H2S ITSL ≈ 72 ppb; 25% of the methane LEL); a wrong
threshold is a false-alarm generator, so "what counts as an exceedance" is a value
Trisha confirms when she enables the stream (ADR 014).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter, namedtuple
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import archive_client as ac
import drive_client as dc
import sheet_writer as sw
import gfl_air_client as gc
import email_alerts as ea
from config_loader import load_config
from types import SimpleNamespace

_FACILITY = "Arbor Hills Landfill"
_DOC_NAME = "GFL perimeter air monitoring (GFL self-reported)"
_MAX_ALERT_LINES = 20  # cap the per-poll email; the tab/dashboard has the rest


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact bug
    this guards against (the poller doing real work / emailing before
    gfl_air.enabled is set) has a direct unit test. Mirrors
    pfas_watcher._should_run / wds_archiver._should_run."""
    if not (cfg.get("gfl_air") or {}).get("enabled"):
        return False, "gfl_air.enabled is false — skipping (no-op)."
    return True, ""


# ---------------------------------------------------------------------------
# Pure helpers (no network / no Sheets) — unit-tested directly
# ---------------------------------------------------------------------------

def station_snapshot(readings: list[dict], thresholds: dict, sentinels: dict | None,
                     station_prefix: str, watch_thresholds: dict | None = None) -> list[dict]:
    """Latest reading per perimeter station among `readings`, with the classifier's
    per-pollutant status attached — the rows the GFL Air snapshot tab shows AND the
    OBJECTID cursor store. Latest = highest OBJECTID for that station. Sorted by
    station name for a stable, human-readable tab. A station absent from this poll
    simply doesn't appear (a visible 'went dark' signal); the reading-count log
    flags a drop to zero."""
    latest: dict[str, dict] = {}
    for r in readings:
        st = gc.station_of(r)
        if station_prefix and not st.startswith(station_prefix):
            continue
        oid = gc.oid_of(r)
        if oid is None or not st:
            continue
        cur = latest.get(st)
        if cur is None or oid > (gc.oid_of(cur) or -1):
            latest[st] = r
    out = []
    for st in sorted(latest):
        r = latest[st]
        c = gc.classify_reading(r, thresholds, sentinels, watch_thresholds)
        h2s_val, h2s_status = c["h2s"]
        ch4_val, ch4_status = c["ch4"]
        out.append({
            "station": st,
            "as_of": gc.reading_iso(r),
            "h2s": h2s_val, "h2s_status": h2s_status,
            "ch4": ch4_val, "ch4_status": ch4_status,
            "wind": r.get("Speed"), "direction": r.get("Direction"), "temp": r.get("Temp"),
            "oid": gc.oid_of(r),
            "note": gc.SELF_REPORTED,
        })
    return out


def alert_lines(readings: list[dict], thresholds: dict, sentinels: dict | None,
                alert_on_sentinel: bool,
                watch_thresholds: dict | None = None,
                h2s_averaged: bool = False,
                include_watch: bool = True) -> tuple[list[str], bool, bool]:
    """(lines, has_exceedance, has_watch): one human line per reading that crossed an
    action level ('EXCEEDANCE'), reached the lower early-warning WATCH level ('watch'),
    or (if alert_on_sentinel) is a sentinel/no-data reading ('anomaly'). has_exceedance
    is True iff >=1 REAL action-level exceedance appears; has_watch iff >=1 watch-level
    reading appears. Together they set the email urgency: [URGENT] > [GFL air watch] >
    [GFL air anomaly]. Pure.

    `include_watch=False` drops watch-severity readings from THIS combined pass
    entirely. run() ALWAYS passes False now (ADR 039): the action-level (watch) tier
    is owned by the consolidated per-(station,gas) episode engine
    (_run_action_level_episodes) and its dedicated SCREENING/CLOSEOUT emails, never
    folded into this full-list exceedance/anomaly email — so the H2S 30 ppb / CH4
    40 ppm leads can't blast the whole distribution list. The default True is retained
    only for the pure-unit-test callers of this helper.

    When `h2s_averaged` is True the INSTANTANEOUS H2S action-level tier is dropped from
    THIS (per-reading) alert path — the H2S exceedance alert is driven instead by the
    rolling 24-hr per-station average (run() computes it via
    gc.fetch_h2s_window_avg + h2s_average_alert_lines), to match the 72 ppb action
    level's own 24-hour averaging period. We express that by passing classify_reading a
    thresholds dict with `h2s_ppb` removed: instantaneous H2S then classifies as 'ok'
    (a single hot hour no longer emails), while CH4 exceedance, the CH4 watch tier, and
    the H2S sentinel->anomaly path are ALL untouched (the sentinel check keys off the
    sentinel config, not the threshold). The snapshot tab still uses the full thresholds
    (station_snapshot), so it keeps showing the instantaneous H2S status. CH4 is never
    averaged (its level is an explosivity/surface-emission boundary, not a 24-hr level)."""
    if h2s_averaged:
        thresholds = {k: v for k, v in (thresholds or {}).items() if k != "h2s_ppb"}
    lines: list[str] = []
    has_exceedance = False
    has_watch = False
    for r in readings:
        c = gc.classify_reading(r, thresholds, sentinels, watch_thresholds)
        sev = c["severity"]
        if sev == "ok":
            continue
        st = gc.station_of(r)
        when = gc.reading_iso(r)
        reasons = c["reasons"]
        # When sentinels are configured silent, don't let a co-located sentinel's
        # detail ride along inside a real exceedance/watch line (a pure-sentinel
        # reading is already dropped by the anomaly branch below).
        if not alert_on_sentinel:
            reasons = [rn for rn in reasons if gc.SENTINEL_REASON not in rn]
        if sev == "urgent":
            has_exceedance = True
            lines.append(f"EXCEEDANCE  {st} {when}: " + "; ".join(reasons))
        elif sev == "watch":
            if include_watch:
                has_watch = True
                lines.append(f"watch       {st} {when}: " + "; ".join(reasons))
            # else: suppressed here on purpose — the consolidated action-level
            # screening engine (_run_action_level_episodes) owns the watch tier, so it
            # isn't also folded into this full-list exceedance/anomaly email.
        elif sev == "anomaly" and alert_on_sentinel:
            lines.append(f"anomaly     {st} {when}: " + "; ".join(reasons))
    return lines, has_exceedance, has_watch


def h2s_average_alert_lines(avgs: dict, h2s_thr, min_readings: int,
                            window_hours: int = 24) -> tuple[list[str], bool, list[str]]:
    """(lines, has_exceedance, notes) for the per-station rolling H2S average — the
    quantity the H2S exceedance alert fires on (the 72 ppb level IS a 24-hr average;
    ADR 014 decision 4). `avgs` is gc.fetch_h2s_window_avg's {station: {'avg', 'n'}}.

    A station's average alerts ONLY when avg >= the action level AND n >= min_readings.
    The n guard stops a SPARSE window (a feed gap leaving only a couple of readings)
    from letting one spike masquerade as 'the 24-hr average': such a station is added
    to `notes` (for the watcher to LOG, not email) rather than alerted, so a too-thin
    window never fires and never silently over-alerts. avg=None (no usable readings) or
    h2s_thr=None (no configured level) yields nothing. Stations are processed in sorted
    order for a stable, readable email. Pure — unit-tested."""
    lines: list[str] = []
    notes: list[str] = []
    has_exceedance = False
    if h2s_thr is None:
        return lines, has_exceedance, notes
    thr = float(h2s_thr)
    for st in sorted(avgs):
        rec = avgs.get(st) or {}
        avg = rec.get("avg")
        n = int(rec.get("n") or 0)
        if avg is None or avg < thr:
            continue
        if n >= min_readings:
            has_exceedance = True
            lines.append(f"EXCEEDANCE  {st}: {window_hours}-hr avg H2S = {avg:g} ppb "
                         f">= {thr:g} ppb action level (n={n} readings)")
        else:
            notes.append(f"{st}: {window_hours}-hr avg H2S = {avg:g} ppb >= {thr:g} "
                         f"action level but only n={n} < {min_readings} readings — "
                         f"sparse window, average alert SUPPRESSED")
    return lines, has_exceedance, notes


def _levels_line(label: str, levels: dict | None) -> str | None:
    """One 'label: H2S >= X ppb, CH4 >= Y ppm.' line built from gc._POLLUTANTS (the
    single source of pollutant identity + units), skipping pollutants absent from
    `levels`. Returns None when there is nothing to show. Shared by the action-levels
    and watch-levels lines so they can never drift or duplicate the pollutant list."""
    lv = levels or {}
    parts = [f"{field} >= {lv[cfgkey]} {unit}"
             for _key, field, unit, cfgkey, _metric in gc._POLLUTANTS
             if lv.get(cfgkey) is not None]
    return f"{label}: " + ", ".join(parts) + ".\n" if parts else None


def format_alert_body(lines: list[str], has_exceedance: bool, has_watch: bool,
                      link: str, thresholds: dict,
                      watch_thresholds: dict | None = None,
                      h2s_averaged: bool = False, h2s_window_hours: int = 24) -> str:
    if has_exceedance:
        kind = "readings crossed a perimeter action level"
    elif has_watch:
        kind = "readings reached an early-warning WATCH level (below the action level)"
    else:
        kind = "sensor anomalies were flagged"
    shown = lines[:_MAX_ALERT_LINES]
    more = len(lines) - len(shown)
    body = [
        f"GFL Arbor Hills perimeter air monitoring — {kind}.\n",
        "These are GFL's OWN self-reported perimeter readings (H2S in ppb, CH4 in "
        "ppm), not an EGLE measurement.\n",
    ]
    if h2s_averaged:
        body.append(
            f"H2S exceedance alerts fire on the rolling {h2s_window_hours}-hour "
            "per-station AVERAGE (matching the H2S action level's own 24-hour averaging "
            "period; see the action level below), not a single instantaneous hour; CH4 "
            "alerts on the instantaneous reading.\n")
    for levels_line in (_levels_line("Action levels (config, Trisha-confirmed)", thresholds),
                        _levels_line("Early-warning WATCH levels (lower urgency)", watch_thresholds)):
        if levels_line:
            body.append(levels_line)
    body.extend("  " + ln for ln in shown)
    if more > 0:
        body.append(f"  ... and {more} more (see the GFL Air tab / dashboard).")
    body.append(f"\nLive dashboard:\n  {link}\n")
    return "\n".join(body)


# ===========================================================================
# Consolidated perimeter ACTION-LEVEL SCREENING system (ADR 039) — REPLACES the
# per-station CH4-40 WATCH (coder:gfl-air-thresholds). Per-(station,gas) open/close
# episodes on the Consent Judgment's OWN enforceable perimeter action levels
# (H2S 30 ppb / ¶def T, CH4 40 ppm / ¶def U), a single consolidated SCREENING email
# per run + a consolidated CLOSEOUT email per run, and a durable episode log.
#
# ⚖️  These emails are SCREENING alerts on PUBLIC HOURLY data, NOT determinations
# that a Consent Judgment action level was exceeded. The CJ defines both levels as
# 15-MINUTE ROLLING AVERAGES computed from ~1-min sampling (¶def T/U, ¶5.4); the
# public ArcGIS feed gives HOURLY values only, so an hourly value above the numeric
# benchmark is a LEAD for EGLE to obtain AHL's nonpublic 1-min/15-min records — never
# itself an exceedance finding. The obligated party is Arbor Hills Landfill, Inc.
# ("AHL"), the CJ defendant — never "GFL". Flagging is STRICTLY ABOVE the benchmark
# (¶5.5 "above") on the raw unrounded numeric; a no-data / sentinel / TEST reading
# never opens or closes. All wording is fixed boilerplate with <data> slotted in —
# NO per-send LLM (deterministic, testable, auditable). See the handoff + ADR 039.
# ===========================================================================

# The CJ action-level provenance per gas — the "source" column in the episode log
# and the benchmark-definition lines in the email.
_CJ_SOURCE = {"h2s": "CJ ¶def T", "ch4": "CJ ¶def U"}
# The gas label used in subjects / prose ("methane", never "CH4", per the handoff's
# methane wording; "H2S" reads fine as-is).
_GAS_LABEL = {"h2s": "H2S", "ch4": "methane"}
# Methane %-by-volume that falls within the ~5-15% flammable range. Below this the
# flammable-range safety sentence is NOT emitted (a 40-ppm = 0.004% reading is
# nowhere near flammable; asserting otherwise would be exactly the overstated claim
# the accuracy brand forbids). 5% by volume = 50,000 ppm.
_CH4_FLAMMABLE_PCT = 5.0

EpisodeResult = namedtuple("EpisodeResult", ["state", "opened", "closed"])


# ---------------------------------------------------------------------------
# ET / time helpers — the CJ + EGLE operate in Michigan local time, so every
# recipient-facing timestamp is America/Detroit (correct DST), never UTC/Z. Pure.
# ---------------------------------------------------------------------------

def _et(iso_utc: str):
    """A 'YYYY-MM-DDTHH:MM(:SS)Z' UTC stamp as an aware America/Detroit datetime, or
    None on blank/garbage / no tz database. Correct DST — unlike the feed's fixed-EST
    Date_Text, which trails real EDT by 1h in summer (spike 2026-09-14)."""
    if not iso_utc or _ET is None:
        return None
    try:
        return datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00")).astimezone(_ET)
    except (ValueError, TypeError):
        return None


def et_date(iso_utc: str) -> str:
    """The America/Detroit calendar date ('YYYY-MM-DD') of a UTC stamp ('' on a parse
    miss). The recipient-facing event date + the Event-ID date. NOTE it can differ by
    a day from the raw UTC date near midnight — the 2025-06-19T01:00Z MS-3 spike is
    2025-06-18 in ET — and the ET date is the correct compliance-local one."""
    et = _et(iso_utc)
    return et.strftime("%Y-%m-%d") if et else ""


def et_label(iso_utc: str) -> str:
    """A UTC stamp as a human ET label, e.g. '2025-06-18 9:00 PM ET (01:00 UTC)'.
    Falls back to the raw string on a parse miss — a display nicety must never abort
    an alert."""
    et = _et(iso_utc)
    if et is None:
        return iso_utc or ""
    ampm = "AM" if et.hour < 12 else "PM"
    h12 = et.hour % 12 or 12
    utc = (str(iso_utc).replace("Z", "").split("T")[-1][:5]) if iso_utc else ""
    return f"{et.strftime('%Y-%m-%d')} {h12}:{et.minute:02d} {ampm} ET ({utc} UTC)"


def _et_iso(iso_utc: str) -> str:
    """A UTC stamp as a sortable 24-hour ET string 'YYYY-MM-DD HH:MM ET' (falls back
    to the raw string on a parse miss) — used for the durable episode-log time cells,
    where lexical sortability beats the 12-hour email label."""
    et = _et(iso_utc)
    return f"{et.strftime('%Y-%m-%d %H:%M')} ET" if et else (iso_utc or "")


def _duration_hours(opened_iso: str, cleared_iso: str):
    """Whole+tenths hours between two UTC stamps, or None on a parse miss (the diff is
    tz-independent, so parsing both to ET is fine)."""
    a, b = _et(opened_iso), _et(cleared_iso)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 3600.0, 1)


def _iso_to_epoch_ms(iso_utc: str):
    """A 'YYYY-MM-DDTHH:MM(:SS)Z' UTC stamp back to epoch-milliseconds (or None) — the
    center for a ±context-window fetch."""
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


# A reported reading whose ET date is at least this many days before today is a
# backfill / catch-up, not the normal daily cycle. The poll runs ONCE daily (~8am ET)
# and its batch covers the prior ~24h, so an excursion that opened YESTERDAY evening
# is still part of the current cycle and must read LIVE — only genuinely old data (a
# 2022/2023 event replayed, or a multi-day catch-up) is HISTORICAL. Keying on "before
# today" instead would wrongly stamp the common case (a ~18-hour-old, current
# excursion detected at the next morning's run) as "NOT A LIVE INCIDENT."
_HISTORICAL_AFTER_DAYS = 2


def is_historical(isos, now_utc: datetime) -> bool:
    """True iff the newest reported reading is a BACKFILL / catch-up — its ET date is
    >= _HISTORICAL_AFTER_DAYS days before today (ET) — rather than the current daily
    cycle. Fires the '[HISTORICAL SCREENING ALERT — NOT A LIVE INCIDENT]' subject so a
    2022/2023 event replayed in 2026 can never masquerade as live, WITHOUT mislabeling
    a normal yesterday-evening excursion (which reads LIVE). Anything unparseable is
    treated as NOT historical (fail toward the plain live subject rather than denying
    the liveness of a current incident). Pure — now_utc injected for testing."""
    if _ET is None:
        return False
    today = now_utc.astimezone(_ET).date()
    dates = sorted({d for d in (et_date(i) for i in isos) if d})
    if not dates:
        return False
    try:
        newest = datetime.strptime(dates[-1], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return (today - newest).days >= _HISTORICAL_AFTER_DAYS


# ---------------------------------------------------------------------------
# Episode identity — the human Event ID. Stable per episode (assigned at open,
# persisted in state), legible, NOT relied on for uniqueness (the log row's
# (station, gas, opened_at) is the durable key). Pure.
# ---------------------------------------------------------------------------

def _episode_key(station: str, gas: str) -> str:
    return f"{station}|{gas}"


def _event_id(station: str, opened_iso: str, seq: int) -> str:
    """AHL-<ET-date>-<station-no-hyphen>-<seq3>, e.g. AHL-2025-06-18-MS3-001."""
    d = et_date(opened_iso) or (str(opened_iso) or "")[:10]
    return f"AHL-{d}-{station.replace('-', '')}-{seq:03d}"


# ---------------------------------------------------------------------------
# THE episode state machine — per (station, gas), open/close, cross-run peak. Pure.
# ---------------------------------------------------------------------------

def _close_record(station: str, gas: str, ep: dict, cleared_at: str, cleared_value) -> dict:
    return {
        "station": station, "gas": gas,
        "event_id": ep.get("event_id", ""),
        "threshold": ep.get("threshold"),
        "opened_at": ep.get("opened_at", ""), "opened_value": ep.get("opened_value"),
        "peak_value": ep.get("peak_value"), "peak_at": ep.get("peak_at", ""),
        "cleared_at": cleared_at, "cleared_value": cleared_value,
        "duration_hours": _duration_hours(ep.get("opened_at", ""), cleared_at),
        "n_over": int(ep.get("n_over", 0)),
        "source": _CJ_SOURCE.get(gas, ""),
    }


def process_episodes(readings: list[dict], watch_thresholds: dict, sentinels: dict | None,
                     prev_state: dict | None,
                     *, station_prefix: str = gc.DEFAULT_STATION_PREFIX) -> EpisodeResult:
    """The per-(station,gas) episode state machine (ADR 039). Walks the poll's
    readings in the order given (caller passes OBJECTID/Date ASC — oldest first) and,
    INDEPENDENTLY for each (station, gas) with a configured watch threshold:

      - OPENS an episode on the first reading STRICTLY ABOVE the threshold while armed
        (records event_id, opened_at/value, peak, n_over=1).
      - stays OPEN across subsequent above readings (no re-alert), tracking the running
        peak (value + timestamp) and n_over — this persists across daily runs because
        `prev_state` is reloaded each run, so an episode routinely spans many polls.
      - CLOSES / re-arms on the first real reading AT-OR-BELOW the threshold; that
        value + time is the 'returned below benchmark' recovery point.

    A no-data reading (missing / numeric sentinel / TEST — gc.is_no_data) NEVER opens
    or closes: recovery is read only from an affirmative real below-benchmark reading,
    never inferred from silence or a fault marker (the fail-safe the old per-station
    watch used, re-keyed per (station,gas)).

    (station, gas) INDEPENDENCE is the load-bearing correctness point (Trisha,
    2026-09-03): a station's open methane episode must not suppress a new H2S open for
    the same station, and vice versa — hence the (station, gas) key, not station alone.

    Returns EpisodeResult(state, opened, closed): `state` is the surviving open-episode
    map to persist; `opened` is every episode that opened this run (peak/n_over reflect
    the whole batch — the ep object is mutated in place then read back); `closed` is
    every episode that closed this run (drives the closeout email + one log row each).
    An episode that opens AND closes within one run appears in BOTH. Pure — unit-tested."""
    state = {k: dict(v) for k, v in (prev_state or {}).items()}
    opened_refs: list = []          # (station, gas, ep-object); ep mutated in place
    closed: list = []
    wt = watch_thresholds or {}
    # Monotonic per-(station, ET-date) Event-ID sequence, seeded from carried-over
    # episodes so a new open never collides with one from a prior run and never reuses
    # a number after a same-day close/reopen (unique within a run; a human handle, not
    # a key — the log row's (station, gas, opened_at) is the durable identity).
    seq_ctr: Counter = Counter()
    for _k, _ep in state.items():
        seq_ctr[(_k.split("|", 1)[0], et_date(_ep.get("opened_at", "")))] += 1
    for r in readings:
        st = gc.station_of(r)
        if not st or (station_prefix and not st.startswith(station_prefix)):
            continue
        when = gc.reading_iso(r)
        for gas in gc.GASES:
            thr = wt.get(gc.gas_cfgkey(gas))
            if thr is None:
                continue                        # gas has no watch tier -> display-only
            if gc.is_no_data(r, gas, sentinels):
                continue                        # never opens or closes
            val = gc.gas_value(r, gas)
            if val is None:                     # redundant with is_no_data; keeps the
                continue                        # comparison below unambiguously float>float
            thr = float(thr)
            key = _episode_key(st, gas)
            ep = state.get(key)
            over = val > thr                    # STRICT '>' on the raw unrounded numeric
            if ep is None:
                if over:
                    dkey = (st, et_date(when))
                    seq_ctr[dkey] += 1
                    ep = {"event_id": _event_id(st, when, seq_ctr[dkey]), "threshold": thr,
                          "opened_at": when, "opened_value": val,
                          "peak_value": val, "peak_at": when, "n_over": 1}
                    state[key] = ep
                    opened_refs.append((st, gas, ep))
            else:
                if over:
                    ep["n_over"] = int(ep.get("n_over", 0)) + 1
                    if val > ep["peak_value"]:
                        ep["peak_value"] = val
                        ep["peak_at"] = when
                else:
                    closed.append(_close_record(st, gas, ep, when, val))
                    del state[key]
    opened = [{"station": st, "gas": gas, **ep} for (st, gas, ep) in opened_refs]
    return EpisodeResult(state=state, opened=opened, closed=closed)


def perimeter_episode_rows(closed: list[dict], logged_utc: str) -> list[list]:
    """One durable log row per CLOSED episode, matching
    sheet_writer.PERIMETER_EPISODE_HEADERS. Timestamps render as sortable ET (the
    compliance-local time), values verbatim/unrounded. Pure — unit-tested."""
    rows = []
    for c in closed:
        dur = c.get("duration_hours")
        rows.append([
            c.get("station", ""),
            gc.gas_field(c["gas"]) if c.get("gas") in gc._GAS else c.get("gas", ""),
            _num(c.get("threshold")),
            _et_iso(c.get("opened_at", "")),
            _num(c.get("opened_value")),
            _num(c.get("peak_value")),
            _et_iso(c.get("peak_at", "")),
            _et_iso(c.get("cleared_at", "")),
            _num(c.get("cleared_value")),
            ("%g" % dur) if dur is not None else "",
            c.get("n_over", 0),
            c.get("source", ""),
            c.get("event_id", ""),
            logged_utc,
        ])
    return rows


# ---------------------------------------------------------------------------
# The "all six monitors" snapshot rows the SCREENING email shows — raw verbatim
# readings + a strictly-above-benchmark flag + no-data/BDL qualifiers. Pure.
# ---------------------------------------------------------------------------

def latest_per_station(readings: list[dict],
                       station_prefix: str = gc.DEFAULT_STATION_PREFIX) -> dict:
    """{station: newest raw reading row} (newest = highest OBJECTID) among perimeter
    stations in `readings`. Pure — the raw rows carry _Text, so the email can honor
    the no-data/BDL/strictly-above distinctions the processed snapshot dict drops."""
    latest: dict = {}
    for r in readings:
        st = gc.station_of(r)
        if not st or (station_prefix and not st.startswith(station_prefix)):
            continue
        oid = gc.oid_of(r)
        if oid is None:
            continue
        if st not in latest or oid > (gc.oid_of(latest[st]) or -1):
            latest[st] = r
    return latest


def _monitor_rows(latest: dict, watch_thresholds: dict, sentinels: dict | None) -> list[dict]:
    """Per-station display rows for the six-monitor table, built from raw latest rows."""
    wt = watch_thresholds or {}
    rows = []
    for st in sorted(latest):
        r = latest[st]
        row = {"station": st, "as_of": gc.reading_iso(r),
               "wind": r.get("Speed"), "dir": r.get("Direction"),
               "dir_text": (r.get("Direction_Text") or "").strip(), "temp": r.get("Temp")}
        for gas in gc.GASES:
            thr = wt.get(gc.gas_cfgkey(gas))
            val = gc.gas_value(r, gas)
            nod = gc.is_no_data(r, gas, sentinels)
            over = (thr is not None) and (not nod) and (val is not None) and (val > float(thr))
            row[gas] = {"val": val, "text": gc.gas_text(r, gas), "no_data": nod,
                        "bdl": gc.is_bdl(r, gas), "over": bool(over), "unit": gc.gas_unit(gas)}
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Rendering helpers for the consolidated emails (all pure).
# ---------------------------------------------------------------------------

def _num(v) -> str:
    """A raw numeric rendered verbatim/unrounded ('' for None). %g keeps 326.9 and
    40.4 (the value that a rounded _Text would show as 327 / 40) — the precision the
    handoff requires so 'above' is never confused with 'at'."""
    if v is None:
        return ""
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _rounds_to_benchmark(val, thr) -> bool:
    """True iff a flagged value is ABOVE the benchmark yet rounds (to the nearest
    integer) to it — e.g. 40.4 vs 40 — so the email can show extra precision and say
    so, per the handoff."""
    try:
        return float(val) > float(thr) and round(float(val)) == round(float(thr))
    except (TypeError, ValueError):
        return False


def _ch4_pct(ppm):
    """Methane %-by-volume from ppm (1% = 10,000 ppm), or None."""
    try:
        return float(ppm) / 10000.0
    except (TypeError, ValueError):
        return None


def _gas_word(gases) -> str:
    """Subject/prose label for a set of implicated gases: 'H2S', 'methane', or
    'H2S + methane' (never one gas when both are implicated)."""
    present = [g for g in gc.GASES if g in set(gases)]
    return " + ".join(_GAS_LABEL[g] for g in present) or "perimeter gas"


def _date_span_et(isos) -> str:
    """The ET date (or 'a..b' span) covering a set of UTC stamps — the subject date."""
    dates = sorted({d for d in (et_date(i) for i in isos) if d})
    if not dates:
        return ""
    return dates[0] if len(dates) == 1 else f"{dates[0]}..{dates[-1]}"


def _coord_str(coords: dict | None, station: str) -> str:
    c = (coords or {}).get(station) or {}
    if c.get("lat") is not None and c.get("lon") is not None:
        return (f"{c['lat']:.5f}, {c['lon']:.5f} "
                f"(https://www.google.com/maps?q={c['lat']:.5f},{c['lon']:.5f})")
    return "see dashboard"


def _wind_str(row: dict) -> str:
    d = row.get("dir_text") or _num(row.get("dir"))
    s = _num(row.get("wind"))
    if not d and not s:
        return "n/a"
    return f"{d or '?'}@{s or '?'} mph"


def _context_table(rows: list[dict]) -> list[str]:
    """The compact ±2-hour context block for one flagged station: its own hourly
    H2S/CH4 around the flagged reading. Pure; caller supplies the rows."""
    if not rows:
        return ["      (context readings unavailable)"]
    out = []
    for r in rows:
        out.append(f"      {et_label(gc.reading_iso(r))}: "
                   f"H2S={_num(gc.gas_value(r, 'h2s'))} ppb, "
                   f"CH4={_num(gc.gas_value(r, 'ch4'))} ppm")
    return out


def _screening_disclaimer_lines() -> list[str]:
    """The fixed 'this is a screening alert, not an exceedance determination' preamble
    — the single most important corrected legal point, so it leads every email."""
    return [
        "AUTOMATED SCREENING ALERT — PUBLIC HOURLY DATA",
        "This is NOT a formal determination that a Consent Judgment action level was "
        "exceeded. The public source provides HOURLY values only; it does not provide "
        "the ~1-minute measurements the Consent Judgment requires to calculate or "
        "verify the 15-minute rolling average that DEFINES an exceedance. A publicly "
        "reported hourly value above the numerical benchmark corresponding to the "
        "Consent Judgment action level applicable to Arbor Hills Landfill, Inc. "
        "(\"AHL\") is a LEAD for EGLE to obtain and examine AHL's nonpublic records; "
        "verification against the required 15-minute rolling-average data is "
        "necessary. An hourly series cannot reproduce the CJ's 15-minute rolling "
        "average, so it is NOT the official CJ exceedance count and likely undercounts "
        "brief between-sample excursions (whether any single hourly value over- or "
        "under-states a given 15-minute window depends on the hourly basis, which the "
        "source does not document). The operator's continuous perimeter monitor and "
        "AHL's ¶6.3 Perimeter Action Level Log are the authoritative 15-minute record. "
        "This does NOT establish that AHL exceeded, or failed to correct, anything.\n",
    ]


def _identity_block(event_ids: list[str], event_state: str, period: str,
                    source_link: str, retrieved_iso: str, data_quality: str) -> list[str]:
    """The standardized identity block shared by both consolidated emails."""
    ev = ", ".join(event_ids) if event_ids else "(none)"
    return [
        "Facility:            Arbor Hills Landfill, 10690 West Six Mile Road, Salem "
        "Township, MI",
        "Responsible entity:  Arbor Hills Landfill, Inc. (\"AHL\") — defendant under "
        "the Consent Judgment (NOT the operator GFL)",
        "Authority:           Consent Judgment No. 2020-0593-CE (30th Circuit Court, "
        "entered 2022-03-07)",
        f"Event ID(s):         {ev}",
        f"Event state:         {event_state}",
        f"Observed period:     {period}  (America/Detroit, ET)",
        f"Source:              {source_link}",
        f"                     retrieved {et_label(retrieved_iso)}",
        "                     Hourly-value basis: NOT documented by the source "
        "(service/layer/field/dashboard metadata all blank, verified) — whether the "
        "hourly value is instantaneous, an hourly average, or an hourly max is UNKNOWN "
        "from public data; a further reason to obtain AHL's underlying ~1-minute "
        "measurements.",
        f"Data quality:        {data_quality}",
        "",
    ]


def _cj_implications_lines(gases) -> list[str]:
    """The fixed ¶5.5(D)/(E) implication + requested-EGLE-review + AG-status footer,
    shared by both emails. Only meaningful IF verified as a 15-min-rolling exceedance."""
    lines = [
        "POTENTIAL CONSENT-JUDGMENT IMPLICATIONS (only if verified as a 15-minute "
        "rolling-average exceedance):",
        "  Potentially implicates ¶5.5(D) or ¶5.5(E) depending on whether "
        "Cell 6/4F excavation or relocation activities were underway (the alert cannot "
        "know operational status). Under ¶5.5(E) AHL must conduct a root-cause "
        "analysis and appropriate corrective actions, and CORRECT the exceedance "
        "within 48 hours of detection — or request an extension under ¶16.4. (The "
        "48-hour deadline applies to CORRECTION, not to completing the root-cause "
        "analysis.) During Cell 6/4F waste excavation/relocation, ¶5.5(D) routes "
        "the response through the Cell 6/4F Waste Relocation and Odor Control Plan "
        "(§3.6) instead.",
        "",
    ]
    if "ch4" in set(gases):
        lines += [
            "  Methane note: a perimeter methane reading above 40 ppm is the CJ "
            "¶def U action-level benchmark; it is far below the ~5-15% (50,000-"
            "150,000 ppm) flammable range at ordinary perimeter concentrations. A "
            "flammability assessment line is included below ONLY for a reading whose "
            "%-by-volume actually reaches that range.",
            "",
        ]
    lines += [
        "REQUESTED EGLE REVIEW: obtain AHL's underlying ~1-minute measurements and "
        "15-minute rolling averages for this period; determine whether a 15-minute "
        "rolling-average exceedance occurred; and review the required root-cause "
        "analysis, corrective-action record, and any ¶16.4 extension "
        "(¶5.5(E)/(F)).",
        "AG STATUS: informational lead pending EGLE technical verification — not a "
        "compliance determination.",
        "",
        "Processing note: the monitor ingests the public feed in a DAILY BATCH, so one "
        "email may cover several past hours at once; \"newly detected / returned below "
        "benchmark\" describe the PUBLIC HOURLY series, not AHL's compliance status.",
    ]
    return lines


def _benchmark_definition_lines() -> list[str]:
    return [
        "  Perimeter H2S  — CJ Action Level = 30 ppb as a 15-MINUTE ROLLING AVERAGE "
        "(¶def T)",
        "  Perimeter CH4  — CJ Action Level = 40 ppm as a 15-MINUTE ROLLING AVERAGE "
        "(¶def U)",
        "  The values below are PUBLIC HOURLY readings compared to those numerical "
        "benchmarks (flagged STRICTLY ABOVE, on the raw unrounded value). Whether a "
        "15-minute rolling-average exceedance occurred can be determined only from "
        "AHL's nonpublic data.",
    ]


# The fixed benchmark-reference block (numbers + regime framing pulled verbatim from
# the canonical SET-report B1 symptom master table; do not re-derive). Item 187's
# email-text upgrade: state each level's regulatory regime SEPARATELY, distinguish the
# CJ enforceable levels from the EGLE ITSL health-screening levels, and frame the
# 500 ppm CH4 level as a SURFACE (on-the-waste-mass) SEM standard vs. the perimeter
# fenceline where these monitors sit. Shared verbatim by both consolidated emails.
def _benchmark_reference_lines() -> list[str]:
    return [
        "BENCHMARK REFERENCE — what each level is, and WHERE it is measured "
        "(so a perimeter reading is read against the right standard):",
        "  • Perimeter H2S 30 ppb (15-min rolling) — the Consent Judgment's OWN "
        "enforceable Perimeter H2S Action Level (¶def T); this screening compares "
        "against it. It is DISTINCT FROM and far below the EGLE Air-Toxics ITSL "
        "health-screening levels for H2S — 72 ppb (24-hr avg, = 100 µg/m³) and 750 ppb "
        "(15-min acute) — which are a health benchmark, NOT the CJ trigger.",
        "  • Perimeter CH4 40 ppm (15-min rolling) — the CJ Perimeter Methane Action "
        "Level (¶def U); an early-warning tripwire (~0.08% of methane's lower "
        "explosive limit). Methane has no EGLE Air-Toxics screening level — its "
        "concerns are flammability + the surface SEM line below.",
        "  • CH4 500 ppm above background — a SURFACE standard, NOT a perimeter one: the "
        "federal NESHAP MACT Subpart AAAA surface-emission standard (40 CFR 63.1958(d); "
        "federal corrective action attaches at §63.1960) + NSPS Subpart WWW + Michigan "
        "Part 115, measured by a Method-21 surface-emission (SEM) scan with the probe "
        "held just above the landfill cover (~5-10 cm), over the waste (the CJ ¶5.4 "
        "layers its own RCA duty on top). MS-1..MS-6 instead sample AMBIENT AIR at the "
        "perimeter fenceline (coordinates above) — a fundamentally different measurement "
        "than an on-surface probe, so a perimeter reading approaching the on-surface "
        "500 ppm benchmark is notable, regardless of whether a fenceline 500 ppm is "
        "itself a violation. (The monitor's separate EXCEEDANCE alert still fires at "
        "CH4 500 ppm.)",
        "  • Surface H2S 122 ppb — the CJ surface H2S Action Level (¶def O, Jerome "
        "meter at 5-10 cm); a surface, not a perimeter, level.",
        "  Applicable regimes: Arbor Hills is subject to NSPS Subpart WWW (40 CFR 60) + "
        "NESHAP MACT Subpart AAAA (40 CFR 63) per its ROP (MI-ROP-N2688-2011a), the "
        "site-specific Consent Judgment No. 2020-0593-CE, and Michigan Part 115. The "
        "perimeter action levels above are Consent-Judgment terms.",
        "",
    ]


def _subject(historical: bool, verb: str, gases, stations, isos) -> str:
    tag = ("[HISTORICAL SCREENING ALERT — NOT A LIVE INCIDENT]" if historical
           else "[SCREENING ALERT]")
    st = ", ".join(sorted(set(stations))) or "perimeter"
    return (f"{tag} Arbor Hills perimeter — {_gas_word(gases)} hourly value(s) "
            f"{verb} CJ benchmark — {st} — {_date_span_et(isos)}, ET")


def format_screening_email(opened: list[dict], continuing: list[dict], monitor_rows: list[dict],
                           watch_thresholds: dict, *, link: str, retrieved_iso: str,
                           coords: dict | None = None, context: dict | None = None,
                           historical: bool = False, stations_reporting: int | None = None,
                           n_stations_expected: int = 6) -> tuple[str, str]:
    """The consolidated per-run SCREENING (OPEN) email — Model A. ONE email listing ALL
    monitors' verbatim per-station readings for BOTH gases (▲ = strictly above the
    benchmark), the newly-detected + continuing episodes, a ±2h context block per
    flagged (station, gas), and the full fixed legal framing. Returns (subject, body).
    Fired iff >=1 episode OPENED this run; a continuing-only run sends nothing (no
    re-alert). Pure — all <data> slotted into a static template, no per-send LLM."""
    coords = coords or {}
    context = context or {}
    opened_isos = [o.get("opened_at", "") for o in opened]
    stations = [o["station"] for o in opened]
    gases = {o["gas"] for o in opened}

    # Event state summary across opened + continuing.
    state_parts = []
    if opened:
        state_parts.append(f"Newly detected ({len(opened)})")
    if continuing:
        state_parts.append(f"Continuing ({len(continuing)})")
    if historical:
        state_parts.append("HISTORICAL BACKFILL")
    event_state = "; ".join(state_parts) or "Newly detected"

    period_isos = opened_isos + [r["as_of"] for r in monitor_rows if r.get("as_of")]
    period = (f"{et_label(min(opened_isos))} to {et_label(max(period_isos))}"
              if opened_isos else "n/a")

    n_report = stations_reporting if stations_reporting is not None else len(monitor_rows)
    dq = (f"stations reporting {n_report}/{n_stations_expected} · 999/99999 "
          "no-data & TEST-marker readings excluded from flagging · calibration "
          "status: not published by the source")

    body: list[str] = []
    body += _screening_disclaimer_lines()
    body += _identity_block([o.get("event_id", "") for o in opened] +
                            [c.get("event_id", "") for c in continuing],
                            event_state, period, link, retrieved_iso, dq)

    body.append("WHAT TRIGGERED THIS — public hourly values vs the CJ numerical "
                "benchmarks:")
    body += _benchmark_definition_lines()
    body.append("")

    # The all-six-monitors table.
    body.append("ALL PERIMETER MONITORS (latest reading this run; ▲ = public "
                "hourly value STRICTLY ABOVE the benchmark):")
    body.append("  Station  H2S (ppb)        CH4 (ppm)        Wind            As-Of (ET)")
    near = False
    for row in monitor_rows:
        def cell(gas):
            nonlocal near
            g = row[gas]
            thr = (watch_thresholds or {}).get(gc.gas_cfgkey(gas))
            if g["no_data"]:
                mark = "TEST/no-data" if (g["text"] or "").upper() in ("TEST",) else "no-data"
                return f"{_num(g['val'])} ({mark})"
            if g["bdl"]:
                return "BDL"
            txt = _num(g["val"])
            if g["over"]:
                txt += "▲"
                if thr is not None and _rounds_to_benchmark(g["val"], thr):
                    near = True
                    txt += "*"
            return txt or "·"
        body.append(f"  {row['station']:<7}  {cell('h2s'):<15}  {cell('ch4'):<15}  "
                    f"{_wind_str(row):<14}  {et_label(row.get('as_of',''))}")
    if near:
        body.append("  * value is ABOVE the benchmark but rounds to it — shown to full "
                    "precision (the raw unrounded value drives the flag, not the "
                    "rounded display).")
    body.append("  \"Above benchmark\" is NOT \"below detection\": BDL appears only "
                "where the source marks it; \"no-data\" is the ambiguous 999/99999/TEST "
                "marker, excluded from flagging.")
    body.append("")

    # Newly-detected episodes + per-flagged context.
    body.append("NEWLY DETECTED (public hourly value above the benchmark this run):")
    for o in opened:
        gl = _GAS_LABEL[o["gas"]]
        unit = gc.gas_unit(o["gas"])
        body.append(f"  {o['event_id']}  {o['station']} · {gl}: opened "
                    f"{et_label(o['opened_at'])} at {_num(o['opened_value'])} {unit} "
                    f"(> {_num(o['threshold'])} {unit} benchmark) · highest public "
                    f"hourly value so far {_num(o['peak_value'])} {unit} at "
                    f"{et_label(o['peak_at'])}")
        # Methane flammability line — ONLY when the %-by-volume is actually in range.
        if o["gas"] == "ch4":
            pct = _ch4_pct(o["peak_value"])
            if pct is not None and pct >= _CH4_FLAMMABLE_PCT:
                body.append(f"      If confirmed as a representative ambient-air "
                            f"concentration, {pct:g}% methane falls within methane's "
                            f"~5-15% flammable range and warrants immediate safety "
                            f"assessment.")
        ctx = context.get(_episode_key(o["station"], o["gas"])) or context.get(o["station"])
        body.append(f"      Station {o['station']} location: {_coord_str(coords, o['station'])}")
        body.append(f"      ±2-hour context around {o['station']} "
                    f"(all values that station's own raw hourly reading):")
        body += _context_table(ctx or [])
    if continuing:
        body.append("")
        body.append("CONTINUING (opened on an earlier run, still above benchmark on "
                    "the public hourly series; no re-alert):")
        for c in continuing:
            gl = _GAS_LABEL[c["gas"]]
            unit = gc.gas_unit(c["gas"])
            body.append(f"  {c.get('event_id','')}  {c['station']} · {gl}: opened "
                        f"{et_label(c.get('opened_at',''))} · peak so far "
                        f"{_num(c.get('peak_value'))} {unit} at "
                        f"{et_label(c.get('peak_at',''))}")
    body.append("")

    body += _benchmark_reference_lines()
    body += _cj_implications_lines(gases)
    body.append("")
    body.append(f"Live dashboard: {link}")

    subject = _subject(historical, "above", gases, stations, opened_isos)
    return subject, "\n".join(body)


def format_closeout_email(closed: list[dict], *, link: str,
                          retrieved_iso: str, coords: dict | None = None,
                          historical: bool = False) -> tuple[str, str]:
    """The consolidated per-run CLOSEOUT (CLOSE) email — the ping that each episode's
    public hourly series returned below the benchmark, with start / peak / return /
    duration per episode. It is NOT proof AHL corrected anything (only that the public
    hourly number fell back under the benchmark). Carries the same standardized
    framing block. Returns (subject, body). Fired iff >=1 episode CLOSED this run.
    Pure — a static template with <data> slotted in, no per-send LLM."""
    coords = coords or {}
    stations = [c["station"] for c in closed]
    gases = {c["gas"] for c in closed}
    close_isos = [c.get("cleared_at", "") for c in closed]
    open_isos = [c.get("opened_at", "") for c in closed]

    period = (f"{et_label(min(open_isos))} to {et_label(max(close_isos))}"
              if open_isos and close_isos else "n/a")
    event_state = "Returned below benchmark" + (" (HISTORICAL BACKFILL)" if historical else "")
    dq = ("closeout of episode(s) whose public hourly series returned at-or-below the "
          "benchmark · calibration status: not published by the source")

    body: list[str] = []
    body += _screening_disclaimer_lines()
    body += _identity_block([c.get("event_id", "") for c in closed], event_state, period,
                            link, retrieved_iso, dq)

    body.append("RETURNED BELOW BENCHMARK (the public hourly series recovered — this "
                "does NOT establish that AHL corrected anything):")
    for c in closed:
        gl = _GAS_LABEL[c["gas"]]
        unit = gc.gas_unit(c["gas"])
        dur = c.get("duration_hours")
        body.append(
            f"  {c.get('event_id','')}  {c['station']} · {gl} (benchmark "
            f"{_num(c.get('threshold'))} {unit}, {c.get('source','')}) · "
            f"location {_coord_str(coords, c['station'])}:")
        body.append(
            f"      first-above {et_label(c.get('opened_at',''))} at "
            f"{_num(c.get('opened_value'))} {unit} · highest public hourly value "
            f"{_num(c.get('peak_value'))} {unit} at {et_label(c.get('peak_at',''))} "
            f"· first-below-benchmark {et_label(c.get('cleared_at',''))} at "
            f"{_num(c.get('cleared_value'))} {unit} · elapsed "
            f"{('%g' % dur + ' hr') if dur is not None else 'n/a'} · "
            f"{c.get('n_over', 0)} hourly reading(s) above benchmark")
        if c["gas"] == "ch4":
            pct = _ch4_pct(c.get("peak_value"))
            if pct is not None and pct >= _CH4_FLAMMABLE_PCT:
                body.append(f"      Peak: if confirmed as a representative ambient-air "
                            f"concentration, {pct:g}% methane falls within methane's "
                            f"~5-15% flammable range and warrants immediate safety "
                            f"assessment.")
    body.append("")
    body += _benchmark_reference_lines()
    body += _cj_implications_lines(gases)
    body.append("")
    body.append("The durable record of each episode (start / peak / return / duration) "
                "is logged to the \"Perimeter Action-Level Episodes\" case-file tab, to "
                "back a review of whether AHL's ¶6.3 Perimeter Action Level Log + "
                "quarterly root-cause report show a root-cause analysis + "
                "prevent-recurrence correction for each.")
    body.append(f"Live dashboard: {link}")

    subject = _subject(historical, "returned below", gases, stations, close_isos)
    return subject, "\n".join(body)


# ---------------------------------------------------------------------------
# Liveness / silent-stall guard (ADR 014 residual — the OBJECTID-reset stall).
# Pure decision + body; the orchestration wrapper (_check_liveness) is below.
# ---------------------------------------------------------------------------

_AS_OF_FMT = "%Y-%m-%dT%H:%MZ"       # the format sheet_writer stores As-Of in


def _parse_as_of(s: str):
    """Parse a stored 'As-Of (UTC)' string back to an aware UTC datetime, or None
    on blank/garbage (never raises — a parse miss must not fire a misleading alert
    or break the poll)."""
    if not s or not str(s).strip():
        return None
    try:
        return datetime.strptime(str(s).strip(), _AS_OF_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def liveness_decision(newest_as_of: str, now_utc: datetime, max_stale_days: int,
                      warned_as_of) -> tuple[bool, int | None]:
    """Pure. Returns (should_warn, stale_days).

    should_warn is True iff the newest reading we've ingested is at least
    max_stale_days old AND we have not already warned for this exact As-Of (the
    once-per-episode gate — self-resetting because As-Of is monotonic, so a later
    stall carries a newer As-Of that differs from `warned_as_of`). stale_days is the
    integer age in whole days, or None if `newest_as_of` is unparseable (in which
    case should_warn is False — we do not fire a 'stale' alert we can't quantify;
    the caller logs that case loudly instead). `now_utc` is injected so this is
    fully unit-testable."""
    dt = _parse_as_of(newest_as_of)
    if dt is None:
        return False, None
    stale_days = int((now_utc - dt).total_seconds() // 86400)
    if stale_days < max_stale_days:
        return False, stale_days
    if warned_as_of and str(warned_as_of).strip() == str(newest_as_of).strip():
        return False, stale_days                 # already warned for this episode
    return True, stale_days


def format_liveness_body(newest_as_of: str, stale_days: int, max_stale_days: int,
                         link: str) -> str:
    """The stale-feed email body — deliberately NOT the exceedance formatter and
    clearly labeled a feed-health notice, never an exceedance."""
    return "\n".join([
        "GFL Arbor Hills perimeter air monitoring — LIVENESS / feed-health notice.\n",
        "This is NOT an exceedance and NOT a reading. The poller has seen NO new "
        f"perimeter readings for {stale_days} day(s) (alert threshold: "
        f"{max_stale_days}).\n",
        f"Newest reading on record: {newest_as_of} (UTC).\n",
        "A healthy feed updates roughly hourly, so a multi-day silence means it may "
        "have stalled — e.g. the ArcGIS service was rebuilt and the OBJECTID cursor "
        "no longer advances (ADR 014's OBJECTID-reset residual), or the source went "
        "offline. The rest of the pipeline is intact; it simply isn't seeing "
        "anything new.\n",
        "Check: open the dashboard and see whether new readings are appearing there. "
        "If they are but this monitor is not, the stored cursor likely needs a "
        "reset.\n",
        f"Live dashboard:\n  {link}\n",
        "(You will not get another liveness alert for this same stall — it fires "
        "once per stale episode, re-arming only after the feed recovers.)",
    ])


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _measure_metadata() -> dict:
    return {
        "date_filed": _today(),
        "document_name": _DOC_NAME,
        "facility_name": _FACILITY,
    }


def _write_measurements(sheets, sheet_id: str, measurements: list[dict], link: str) -> int:
    """Append the selected readings to the shared Measurements tab via the existing
    measurement_rows path (no new schema — the ADR-004 invariant). Returns the row
    count. Written BEFORE the summary/cursor update (crash-safe: a kill between them
    re-ingests the batch next run — a duplicate reading, never a dropped one)."""
    if not measurements:
        return 0
    parsed = SimpleNamespace(measurements=measurements)
    rows = sw.measurement_rows(parsed, _measure_metadata(), link)
    sw.append_rows(sheets, sheet_id, sw.TAB_MEASUREMENTS, rows)
    return len(rows)


def _baseline(sheets, sheet_id: str, cfg_gfl: dict, link: str, prefix: str,
              thresholds: dict, sentinels: dict, why: str,
              watch_thresholds: dict | None = None) -> None:
    """Record the current latest-per-station snapshot + set the cursor, alerting on
    NONE (WDS Rule B). Used on the first-ever run and on the over-cap re-baseline.
    Writes the snapshot only (no measurement backlog, no emails) — the whole point
    is to establish state without stampeding history."""
    baseline = gc.fetch_baseline(cfg_gfl, station_prefix=prefix)
    snapshot = station_snapshot(baseline, thresholds, sentinels, prefix, watch_thresholds)
    sw.write_gfl_air_summary(sheets, sheet_id, snapshot, link)
    print(f"[gfl-air] {why}: baselined {len(snapshot)} station(s), cursor -> "
          f"{gc.max_oid(baseline)}, no alerts.")


def _check_liveness(sheets, sheet_id: str, cfg: dict, link: str,
                    max_stale_days: int) -> None:
    """On a poll that found ZERO new readings, alert ONCE if the newest reading on
    record is older than max_stale_days — the mitigation for ADR 014's OBJECTID-reset
    silent stall (a permanent silent zero that is indistinguishable from a healthy
    quiet). Runs ONLY on the zero-new-readings path (deliberately not on baseline /
    over-cap / fetch-error — see the note where it's called), AFTER the poll has
    already decided to write nothing and leave the cursor unadvanced, so it can
    touch neither the measurements system-of-record nor the cursor.

    FULLY ISOLATED + best-effort: every failure here (a bad tab read, a send error)
    is caught-and-logged and NEVER propagates — a liveness bug must not break the
    poll. The stale marker is written only AFTER a successful send, so a failed send
    simply retries next run until it lands exactly once (mirrors the exceedance
    email's 'alert best-effort; readings recorded' posture in run())."""
    try:
        newest = sw.gfl_air_latest_as_of(sheets, sheet_id)
        if newest is None:
            print("[gfl-air]   liveness: no parseable As-Of in the tab — skipping "
                  "(cannot quantify freshness; not firing a misleading 'stale' "
                  "alert). This is itself unexpected; investigate the tab.")
            return
        warned = sw.gfl_air_stale_marker(sheets, sheet_id)
        should_warn, stale_days = liveness_decision(
            newest, datetime.now(timezone.utc), max_stale_days, warned)
        if not should_warn or stale_days is None:   # None only when should_warn False
            return
        subject = (f"[GFL air liveness] Arbor Hills perimeter feed appears STALE — "
                   f"no new readings in {stale_days} day(s)")
        body = format_liveness_body(newest, stale_days, max_stale_days, link)
        try:
            ea.send_email(subject, body, cfg)
            sw.set_gfl_air_stale_marker(sheets, sheet_id, newest)   # once per episode
            print(f"[gfl-air]   liveness: STALE alert emailed (newest={newest}, "
                  f"{stale_days}d >= {max_stale_days}d); marker set.")
        except Exception as e:  # noqa: BLE001 — alert best-effort, marker NOT set → retries
            print(f"[gfl-air]   liveness: STALE detected (newest={newest}, "
                  f"{stale_days}d) but alert email FAILED (will retry next run): {e}")
    except Exception as e:  # noqa: BLE001 — liveness must NEVER break the poll
        print(f"[gfl-air]   liveness check errored (ignored — poll unaffected): {e}")


# ---------------------------------------------------------------------------
# Durable air-readings exhibit (ADR 026, Gap G1-E-1/G3-E-1) — an immutable Drive
# capture of the selected readings. Ships GATED OFF (capture.enabled +
# GOAUTH_GFL_AIR_FOLDER_ID), so it cannot affect the live stream until Trisha
# provisions the folder/secret and flips the flag. The WATCH email + episode
# logic + wind/dir/temp snapshot already exist; this only adds the durable store.
# ---------------------------------------------------------------------------

_CAPTURE_FOLDER_ENV = "GOAUTH_GFL_AIR_FOLDER_ID"


def _reading_epoch_s(r: dict):
    """Reading time in epoch SECONDS (ArcGIS `Date` is epoch millis), or None if
    absent/unparseable. Used only for baseline-downsample spacing."""
    try:
        return int(r.get("Date")) / 1000.0
    except (TypeError, ValueError):
        return None


def _capture_row(r: dict) -> dict:
    """One durable capture record from a raw reading (ADR 026): Monitor ID,
    Timestamp, H2S, CH4, Wind Direction, Wind Speed (Trisha's fields) plus Temp
    (Q3). Raw values kept verbatim (incl. any sentinel) — a faithful record;
    interpretation stays downstream. OBJECTID rides along as an ordering key."""
    return {
        "monitor_id": gc.station_of(r),
        "timestamp": gc.reading_iso(r),
        "h2s_ppb": r.get("H2S"),
        "ch4_ppm": r.get("CH4"),
        "wind_direction": r.get("Direction"),
        "wind_speed": r.get("Speed"),
        "temp": r.get("Temp"),
        "oid": gc.oid_of(r),
    }


def select_capture_rows(readings: list[dict], thresholds: dict, sentinels: dict | None,
                        watch_thresholds: dict | None, baseline_hours: float,
                        station_prefix: str) -> list[dict]:
    """Pick the readings to durably capture this poll (Trisha's spec, ADR 026):
    keep EVERY reading whose own classification is exceedance OR watch (Tier-1 CH4
    >= 40) — the full hourly series through any elevated period — and, for
    non-elevated readings, keep at least one per `baseline_hours` window per
    station. `readings` are OBJECTID-ASC (oldest first), so the spacing walks
    forward correctly. Pure/unit-tested."""
    baseline_secs = float(baseline_hours) * 3600.0
    kept: list[dict] = []
    last_kept_s: dict[str, float] = {}
    for r in readings:
        st = gc.station_of(r)
        if not st or (station_prefix and not st.startswith(station_prefix)):
            continue
        c = gc.classify_reading(r, thresholds, sentinels, watch_thresholds)
        elevated = (c["h2s"][1] in ("exceedance", "watch")
                    or c["ch4"][1] in ("exceedance", "watch"))
        if elevated:
            kept.append(_capture_row(r))
            continue
        ts = _reading_epoch_s(r)
        prev = last_kept_s.get(st)
        if prev is None or ts is None or (ts - prev) >= baseline_secs:
            kept.append(_capture_row(r))
            if ts is not None:
                last_kept_s[st] = ts
    return kept


def _capture_filename(rows: list[dict], when_utc: str) -> str:
    """Immutable per-poll name: run date + max OBJECTID in the batch, so a re-run
    of the same batch dedups (upload_file reuses by name) and files sort by date."""
    max_oid = max((r.get("oid") or 0) for r in rows) if rows else 0
    return f"gfl-air-capture-{when_utc[:10]}-oid{max_oid}.json"


def _write_capture(cfg_gfl: dict, rows: list[dict], when_utc: str) -> int:
    """Upload the selected rows as an immutable JSON to the app-only GFL air Drive
    folder (ADR 026). Returns rows written, or 0 for a no-op. GATED: silently does
    nothing unless capture.enabled AND the OAuth Drive creds + the folder secret
    are configured — so it ships safe with the flag off and no secret. Best-effort
    by contract (caller wraps it)."""
    if not (cfg_gfl.get("capture") or {}).get("enabled"):
        return 0
    if not ac.is_configured(_CAPTURE_FOLDER_ENV):
        print(f"[gfl-air]   capture enabled but Drive folder/creds not set "
              f"({_CAPTURE_FOLDER_ENV}) — skipping durable capture")
        return 0
    if not rows:
        return 0
    payload = json.dumps(
        {"captured_at": when_utc, "count": len(rows), "readings": rows},
        indent=2, sort_keys=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(payload)
            tmp = fh.name
        drive = ac.oauth_drive_service()
        ac.upload_file(drive, tmp, _capture_filename(rows, when_utc),
                       "application/json", ac.folder_id(_CAPTURE_FOLDER_ENV))
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return len(rows)


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[gfl-air] {reason}")
        return 0

    cfg_gfl = cfg.get("gfl_air") or {}
    thresholds = cfg_gfl.get("thresholds") or {}
    watch_thresholds = cfg_gfl.get("watch_thresholds") or {}
    sentinels = cfg_gfl.get("sentinels") or {}
    prefix = cfg_gfl.get("station_prefix", gc.DEFAULT_STATION_PREFIX)
    mode = cfg_gfl.get("measurements_mode", "digest")
    cap = int(cfg_gfl.get("max_new_readings_per_run", 1000))
    alert_on_sentinel = bool(cfg_gfl.get("alert_on_sentinel", True))
    max_stale_days = int(cfg_gfl.get("max_stale_days", 3))
    # H2S alerts on a rolling per-station average to match the 72 ppb 24-hr action
    # level (ADR 014 decision 4). window_hours=0 restores the old instantaneous H2S
    # behavior (a rollback lever); min_readings guards a sparse window. CH4 is always
    # instantaneous. See gc.fetch_h2s_window_avg + h2s_average_alert_lines.
    h2s_avg_window_hours = int(cfg_gfl.get("h2s_avg_window_hours", 24))
    h2s_avg_min_readings = int(cfg_gfl.get("h2s_avg_min_readings", 12))
    h2s_averaged = h2s_avg_window_hours > 0
    # Consolidated ACTION-LEVEL screening tier recipients (ADR 039). Empty/unset =
    # DISPLAY-ONLY rollback lever: the episode engine is skipped entirely (no OPEN/
    # CLOSE emails, no episode-log writes), and the watch status still shows on the
    # snapshot tab. Configured (non-empty) = the per-(station,gas) episode engine runs
    # and emails this list. The action-level (watch) tier is NEVER folded into the
    # full-list exceedance/anomaly email (include_watch=False below), so a sub-
    # exceedance action-level lead — including the new H2S 30 ppb tier — cannot blast
    # the whole distribution list. GFL_AIR_WATCH_RECIPIENTS_EXTRA (2026-08-21, Trisha's
    # direction) is this list's private-supplement env, parallel to
    # email_alerts.resolve_recipients' ALERT_RECIPIENTS_EXTRA — a recipient added here
    # WITHOUT committing their address to this PUBLIC repo's config.yml.
    watch_recipients = ea.merge_extra_recipients(
        cfg_gfl.get("watch_alert_recipients") or [], "GFL_AIR_WATCH_RECIPIENTS_EXTRA")
    link = cfg_gfl.get("dashboard_url") or cfg_gfl.get("service_url", "")

    for _w in gc.watch_config_warnings(thresholds, watch_thresholds):
        print(f"[gfl-air] CONFIG WARNING: {_w}")

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)          # the shared Measurements tab must exist
    sw.ensure_gfl_air_tabs(sheets, sheet_id)  # our snapshot/cursor tab

    # Read the incremental cursor. gfl_air_cursor distinguishes an EMPTY tab
    # (None → first-run baseline) from a FAILED read (raises → skip-and-warn, never
    # re-baseline on a blip). This is the one place the _tab_rows error-swallow
    # would have re-baselined on a transient read; we don't let it.
    try:
        cursor = sw.gfl_air_cursor(sheets, sheet_id)
    except Exception as e:  # noqa: BLE001 — a real read failure, not "first run"
        print(f"[gfl-air] could not read cursor — skipping this run (state preserved): {e}")
        return 0

    # First-ever run → baseline (loud on activation if the feed can't be reached).
    if cursor is None:
        try:
            _baseline(sheets, sheet_id, cfg_gfl, link, prefix, thresholds, sentinels,
                      "first run", watch_thresholds)
        except gc.GflAirFetchError as e:
            print(f"[gfl-air] NO BASELINE and feed fetch failed (failing loudly so "
                  f"activation surfaces it): {e}")
            return 1
        return 0

    # Incremental poll.
    try:
        readings = gc.fetch_readings(cfg_gfl, cursor, limit=cap)
    except gc.GflAirFetchError as e:
        print(f"[gfl-air] feed fetch failed, skipping this run (cursor {cursor} "
              f"preserved, not advanced): {e}")
        return 0

    if len(readings) > cap:
        # Over-cap: almost certainly a source-side full-table reinsert (every
        # OBJECTID bumped), not thousands of real new readings. Re-baseline instead
        # of blasting the case file (WDS Rule B(ii)).
        try:
            _baseline(sheets, sheet_id, cfg_gfl, link, prefix, thresholds, sentinels,
                      f"OVER-CAP ({len(readings)} > {cap}) — suspected feed reinsert",
                      watch_thresholds)
        except gc.GflAirFetchError as e:
            print(f"[gfl-air] over-cap re-baseline fetch failed, skipping (cursor "
                  f"preserved): {e}")
        return 0

    if not readings:
        # Zero new readings past the cursor. Normally a healthy quiet — but this is
        # also EXACTLY what ADR 014's OBJECTID-reset silent stall looks like, so run
        # the liveness guard here (and only here: baseline/over-cap take other
        # branches, and a persistent fetch error returns above at the GflAirFetchError
        # handler — a separate silent-quiet vector, out of scope by design, that at
        # least logs each run rather than looking like a healthy zero). Isolated +
        # best-effort: it cannot touch measurements or the cursor.
        _check_liveness(sheets, sheet_id, cfg, link, max_stale_days)
        print(f"[gfl-air] no new readings (cursor {cursor}).")
        return 0

    # Measurements FIRST (system of record), then the snapshot/cursor, then the
    # best-effort email — the repo's crash-safe ordering.
    measurements = gc.select_measurements(
        readings, mode, thresholds, sentinels=sentinels, station_prefix=prefix)
    n_rows = _write_measurements(sheets, sheet_id, measurements, link)

    snapshot = station_snapshot(readings, thresholds, sentinels, prefix, watch_thresholds)
    sw.write_gfl_air_summary(sheets, sheet_id, snapshot, link)  # advances the cursor
    new_cursor = gc.max_oid(readings)
    print(f"[gfl-air] {len(readings)} new reading(s) across {len(snapshot)} "
          f"station(s); {n_rows} measurement row(s) ({mode}); cursor {cursor} -> "
          f"{new_cursor}.")

    # Durable air-readings exhibit (ADR 026) — immutable Drive capture of the
    # selected readings (every elevated hour + baseline downsample). Best-effort
    # and gated OFF until the folder/secret exist, so it can't affect the live
    # stream; it never touches measurements, the cursor, or the alert path.
    try:
        captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cap_rows = select_capture_rows(
            readings, thresholds, sentinels, watch_thresholds,
            float((cfg_gfl.get("capture") or {}).get("baseline_hours", 8)), prefix)
        n_cap = _write_capture(cfg_gfl, cap_rows, captured_at)
        if n_cap:
            print(f"[gfl-air]   durable capture: {n_cap} reading(s) -> Drive.")
    except Exception as ce:  # noqa: BLE001 — durable capture is best-effort
        print(f"[gfl-air]   durable capture skipped: {ce}")

    # The full-list EXCEEDANCE / anomaly email (unchanged tier: CH4 500 ppm / H2S
    # 72 ppb 24-hr avg). include_watch=False ALWAYS now: the action-level (watch) tier
    # is owned entirely by the consolidated per-(station,gas) episode engine below and
    # is NEVER folded into this full-list email (ADR 039) — so the H2S 30 ppb / CH4
    # 40 ppm leads can't blast the whole distribution list.
    lines, has_exceedance, has_watch = alert_lines(
        readings, thresholds, sentinels, alert_on_sentinel, watch_thresholds,
        h2s_averaged=h2s_averaged, include_watch=False)

    # H2S exceedance alerting is the rolling per-station average (the 72 ppb level IS a
    # 24-hr-average level; ADR 014 decision 4) — NOT the instantaneous readings, which
    # alert_lines dropped above when averaging is on. Computed server-side in one query;
    # a failed query is skip-and-warn (best-effort), NEVER read as 0 ppb. Independent of
    # the OBJECTID poll batch: it always looks back a true now-window, so a missed/off-
    # schedule poll doesn't skew it. CH4 stays instantaneous (handled in alert_lines).
    if h2s_averaged:
        try:
            avgs = gc.fetch_h2s_window_avg(cfg_gfl, h2s_avg_window_hours, station_prefix=prefix)
        except gc.GflAirFetchError as e:
            print(f"[gfl-air]   H2S {h2s_avg_window_hours}-hr average query FAILED "
                  f"(skipping the average alert this poll; readings recorded): {e}")
        else:
            avg_lines, avg_exc, avg_notes = h2s_average_alert_lines(
                avgs, thresholds.get("h2s_ppb"), h2s_avg_min_readings, h2s_avg_window_hours)
            for note in avg_notes:
                print(f"[gfl-air]   H2S average NOTE: {note}")
            lines.extend(avg_lines)
            has_exceedance = has_exceedance or avg_exc

    if lines:
        if has_exceedance:
            tag = "URGENT"
        elif has_watch:
            tag = "GFL air watch"
        else:
            tag = "GFL air anomaly"
        subject = f"[{tag}] Arbor Hills GFL perimeter air: {len(lines)} flagged reading(s)"
        try:
            ea.send_email(subject,
                          format_alert_body(lines, has_exceedance, has_watch, link,
                                            thresholds, watch_thresholds,
                                            h2s_averaged=h2s_averaged,
                                            h2s_window_hours=h2s_avg_window_hours), cfg)
            print(f"[gfl-air]   emailed: {subject}")
        except Exception as e:  # noqa: BLE001 — alert best-effort; readings recorded
            print(f"[gfl-air]   readings recorded but alert email FAILED: {e}")

    # Consolidated ACTION-LEVEL SCREENING system (ADR 039) — the per-(station,gas)
    # episode engine. Independent of the exceedance/anomaly path above. Fully skipped
    # (no Sheets read/write, no email) when watch_recipients is empty (display-only
    # rollback) or no gas has a watch threshold.
    if watch_thresholds and watch_recipients:
        _run_action_level_episodes(
            sheets, sheet_id, cfg, readings, watch_thresholds, sentinels, prefix,
            link, watch_recipients, cfg_gfl)

    return 0


def _open_episode_from_state(key: str, ep: dict) -> dict:
    """A state-map entry (keyed 'station|gas') as a flat dict for the email helpers."""
    station, _, gas = key.partition("|")
    return {"station": station, "gas": gas, **ep}


def _run_action_level_episodes(sheets, sheet_id, cfg, readings, watch_thresholds,
                               sentinels, prefix, link, watch_recipients, cfg_gfl) -> None:
    """Advance the per-(station,gas) episode state on this poll's readings, write the
    durable episode-log ROW before the state entry (repo ordering), and send AT MOST
    one consolidated SCREENING (open) email and one CLOSEOUT (close) email. Coords +
    ±2h context are best-effort; a failure there degrades the email but never blocks
    it. Emails are best-effort (like the exceedance email).

    ⚠️ Crash-safety caveat: the OBJECTID cursor is committed UPSTREAM (write_gfl_air
    _summary in run(), before this function), independent of the column-O episode
    state here — so these readings are NOT re-fetched next run. The row-before-state
    ordering therefore does NOT give an atomic retry the way the doc-ingestion path
    does; on a rare Sheets-write DOUBLE fault it degrades toward a duplicate /
    slightly-wrong close row or (open-only + recovers-before-next-run) a lost durable
    log row — never a false [URGENT], never lost measurements. Documented as ADR 039's
    accepted residual."""
    # Prior open-episode state — fail-safe toward re-alerting on an unreadable cell.
    try:
        prev_state = sw.gfl_air_episode_state(sheets, sheet_id)
    except Exception as e:  # noqa: BLE001 — unreadable state => empty (re-derive, never
        print(f"[gfl-air]   episode-state read FAILED, treating as empty: {e}")  # suppress)
        prev_state = {}

    result = process_episodes(readings, watch_thresholds, sentinels, prev_state,
                              station_prefix=prefix)
    # "Continuing" = episodes open BOTH before and after this run (not newly opened).
    prev_keys = set(prev_state)
    opened_keys = {_episode_key(o["station"], o["gas"]) for o in result.opened}
    continuing = [_open_episode_from_state(k, result.state[k])
                  for k in sorted(result.state)
                  if k in prev_keys and k not in opened_keys]

    if not result.opened and not result.closed:
        # Elevated-but-nothing-new (continuing only) or all-quiet — no re-alert. Still
        # persist state so cross-run peaks/n_over that advanced this run are kept.
        try:
            sw.set_gfl_air_episode_state(sheets, sheet_id, result.state)
        except Exception as e:  # noqa: BLE001 — best-effort; re-derived next poll
            print(f"[gfl-air]   episode-state write skipped (no alerts this run): {e}")
        return

    now_utc = datetime.now(timezone.utc)
    retrieved_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Best-effort station coordinates (layer 0) — a nicety; never blocks an alert.
    coords: dict = {}
    try:
        coords = gc.fetch_station_coords(cfg_gfl, station_prefix=prefix)
    except Exception as e:  # noqa: BLE001
        print(f"[gfl-air]   station coords unavailable (using dashboard link): {e}")

    # Durable log rows for CLOSED episodes FIRST (Sheet row before state entry — the
    # crash-safe invariant: a kill re-writes the row, never drops it).
    if result.closed:
        try:
            sw.ensure_perimeter_episodes_tab(sheets, sheet_id)
            sw.append_rows(sheets, sheet_id, sw.TAB_PERIMETER_EPISODES,
                           perimeter_episode_rows(result.closed, retrieved_iso))
            print(f"[gfl-air]   logged {len(result.closed)} closed episode(s).")
        except Exception as e:  # noqa: BLE001 — prefer a later duplicate/wrong close row
            print(f"[gfl-air]   episode-log write FAILED (state NOT advanced): {e}")
            # Leave state unwritten so the just-closed episode stays OPEN in column O.
            # The cursor already advanced upstream, so these readings won't re-run; the
            # episode instead re-closes on a FUTURE below-benchmark reading (a later,
            # longer-duration close row) rather than being dropped from the log here.
            return

    # Persist the advanced state. (If this write fails on an open-only run, the OPEN
    # email below still fires; the episode is re-derived next run only if it is STILL
    # above then — an episode that opened near the batch edge and recovered before the
    # next daily run would go unlogged. Rare Sheets-write fault; ADR 039 residual.)
    try:
        sw.set_gfl_air_episode_state(sheets, sheet_id, result.state)
    except Exception as e:  # noqa: BLE001 — best-effort; see the caveat above
        print(f"[gfl-air]   episode-state write FAILED: {e}")

    # SCREENING (open) email — one consolidated email listing all monitors + the
    # newly-detected + continuing episodes. Best-effort.
    if result.opened:
        opened_isos = [o.get("opened_at", "") for o in result.opened]
        hist = is_historical(opened_isos, now_utc)
        context: dict = {}
        for st in sorted({o["station"] for o in result.opened}):
            center = _iso_to_epoch_ms(next((o["opened_at"] for o in result.opened
                                            if o["station"] == st), ""))
            if center is None:
                continue
            try:
                context[st] = gc.fetch_station_window(cfg_gfl, st, center, 2)
            except Exception as e:  # noqa: BLE001 — context is best-effort
                print(f"[gfl-air]   ±2h context for {st} unavailable: {e}")
        monitor_rows = _monitor_rows(latest_per_station(readings, prefix),
                                     watch_thresholds, sentinels)
        subject, body = format_screening_email(
            result.opened, continuing, monitor_rows, watch_thresholds, link=link,
            retrieved_iso=retrieved_iso, coords=coords, context=context, historical=hist,
            stations_reporting=len(monitor_rows))
        try:
            ea.send_email(subject, body, cfg, recipients=watch_recipients)
            print(f"[gfl-air]   SCREENING emailed: {subject}")
        except Exception as e:  # noqa: BLE001 — best-effort; state already committed
            print(f"[gfl-air]   SCREENING email FAILED (episode(s) tracked): {e}")

    # CLOSEOUT (close) email — one consolidated closeout per run. Best-effort.
    if result.closed:
        close_isos = [c.get("cleared_at", "") for c in result.closed]
        hist = is_historical(close_isos, now_utc)
        subject, body = format_closeout_email(
            result.closed, link=link, retrieved_iso=retrieved_iso,
            coords=coords, historical=hist)
        try:
            ea.send_email(subject, body, cfg, recipients=watch_recipients)
            print(f"[gfl-air]   CLOSEOUT emailed: {subject}")
        except Exception as e:  # noqa: BLE001 — best-effort; log row already written
            print(f"[gfl-air]   CLOSEOUT email FAILED (episode(s) logged): {e}")


if __name__ == "__main__":
    sys.exit(run())
