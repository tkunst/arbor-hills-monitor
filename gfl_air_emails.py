"""
gfl_air_emails.py — Stream E email rendering (ADR 014 / 039): the exceedance
alert lines and body, and the consolidated SCREENING / CLOSEOUT emails. Pure.
Split out of gfl_air_watcher.py (consolidation PR 3) as a pure move;
gfl_air_watcher re-exports every name.
"""
from __future__ import annotations

import gfl_air_client as gc
from gfl_air_episodes import (_CH4_FLAMMABLE_PCT, _GAS_LABEL, FALLBACK_N_STATIONS_EXPECTED,
                              _episode_key, _num, et_date, et_label, roster_mismatch)

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


# ---------------------------------------------------------------------------
# Rendering helpers for the consolidated emails (all pure).
# ---------------------------------------------------------------------------



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
                           expected_stations: list[str] | None = None,
                           registry_available: bool = True) -> tuple[str, str]:
    """The consolidated per-run SCREENING (OPEN) email — Model A. ONE email listing ALL
    monitors' verbatim per-station readings for BOTH gases (▲ = strictly above the
    benchmark), the newly-detected + continuing episodes, a ±2h context block per
    flagged (station, gas), and the full fixed legal framing. Returns (subject, body).
    Fired iff >=1 episode OPENED this run; a continuing-only run sends nothing (no
    re-alert). Pure — all <data> slotted into a static template, no per-send LLM.

    `expected_stations` is the configured ROSTER (gfl_air_episodes.station_roster):
    its length is the "stations reporting N/<len>" denominator, a roster station with
    no row (neither a reading nor a registry entry) is NAMED in the "not listed" note,
    and a row whose station is NOT on the roster is named as unexpected and never
    counted toward the expected N. With no roster (None — the caller logs that) the
    pre-roster behaviour applies: FALLBACK_N_STATIONS_EXPECTED and a count-only note."""
    coords = coords or {}
    context = context or {}
    roster = list(expected_stations) if expected_stations else None
    n_stations_expected = len(roster) if roster else FALLBACK_N_STATIONS_EXPECTED
    unexpected, missing = roster_mismatch(roster, (r["station"] for r in monitor_rows))
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

    # Reporting = a non-silent row; with a roster, only ROSTER stations count toward
    # the expected N (an unexpected MS-7 reporting must not mask a silent MS-1).
    if stations_reporting is not None:
        n_report = stations_reporting
    else:
        n_report = sum(1 for r in monitor_rows
                       if not r.get("silent") and (roster is None or r["station"] in roster))
    expected_note = ("" if roster else
                     " (expected count is the legacy default — gfl_air.expected_stations "
                     "not configured)")
    dq = (f"stations reporting {n_report}/{n_stations_expected}{expected_note} · 999/99999 "
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

    # The all-monitors table (every reading / registry station this run).
    body.append("ALL PERIMETER MONITORS (latest reading this run; ▲ = public "
                "hourly value STRICTLY ABOVE the benchmark):")
    body.append("  Station  H2S (ppb)        CH4 (ppm)        Wind            As-Of (ET)")
    near = False
    # "no data since X" only when X predates the earliest reporting station's latest
    # reading in this batch (conservative: it can fall back to the plainer wording,
    # never to a false "no data since"); otherwise
    # (historical backfill, or a reading that landed between the layer-4 and layer-0
    # fetches) the honest statement is just that this batch has no reading for it.
    batch_start = min((r["as_of"] for r in monitor_rows if r.get("as_of")), default="")
    for row in monitor_rows:
        if row.get("silent"):
            last = row.get("last_read_iso") or ""
            since = (f"no data since {et_label(last)}"
                     if last and (not batch_start or last < batch_start)
                     else "no reading in this run's batch")
            body.append(f"  {row['station']:<7}  {since}")
            continue

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
    why = ("the station list could not be retrieved this run" if not registry_available
           else "not in the source's station list")
    if roster:
        # Roster/registry reconciliation — a mismatch is never invisible.
        if missing:
            body.append(f"  ({len(missing)} station(s) not listed — {', '.join(missing)}: "
                        f"{why}.)")
        if unexpected:
            body.append(f"  ({len(unexpected)} station(s) not in this monitor's configured "
                        f"roster — {', '.join(unexpected)}: listed above but NOT counted "
                        f"toward the {n_stations_expected} expected; if this is a real new "
                        "or renamed monitor, update gfl_air.expected_stations.)")
    elif len(monitor_rows) < n_stations_expected:
        # No roster: names are unknown, so the pre-roster count-only note.
        body.append(f"  ({n_stations_expected - len(monitor_rows)} station(s) not listed: "
                    f"{why}.)")
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
