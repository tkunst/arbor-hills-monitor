"""
gfl_air_emails.py — Stream E email rendering (ADR 014): the snapshot rows and the
exceedance / anomaly alert lines and body. Pure. (The screening email is in
gfl_air_render.py, ADR 075.)
Split out of gfl_air_watcher.py (consolidation PR 3) as a pure move;
gfl_air_watcher re-exports every name.
"""
from __future__ import annotations

import gfl_air_client as gc

_MAX_ALERT_LINES = 20  # cap the per-poll email; the tab/dashboard has the rest


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
        "These are GFL's self-reported perimeter readings (H2S in ppb, CH4 in "
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


# The consolidated SCREENING / CLOSEOUT emails (ADR 039) were replaced on 2026-11-26 by
# ONE daily-run email, layout C — see gfl_air_render.py and ADR 075. Their legal
# framing lives there now, revised per Trisha's 2026-11-26 decisions.
