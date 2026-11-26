"""
gfl_air_render.py — the Stream E daily-run SCREENING email, layout C (ADR 075).

One email per daily run (replacing ADR 039's separate screening + closeout emails),
as a plain-text part and an HTML part built from the SAME content: every sentence of
copy lives once in this module and both renderers read it. Pure: no network, no clock,
no Sheets. gfl_air_watcher assembles the inputs and sends via email_alerts.send_email
(html=...), which fills digest_render.FOOTER_SLOT with the per-recipient unsubscribe
footer (ADR 040) and leaves owner copies without one.

Layout (chosen by Trisha 2026-11-26 from the mockups in session AHM-AlertsRedesign):
  * Subject starts with the substance over the benchmark: "[H2S SCREENING ALERT]",
    "[Methane SCREENING ALERT]" or "[H2S + Methane SCREENING ALERT]"; old data gets
    "[HISTORICAL <gas> SCREENING ALERT — NOT A LIVE INCIDENT]".
  * Lede: run length + window, episodes opened / returned below / still above, and the
    run's highest H2S and methane.
  * A title-only "Automated screening alert — public hourly data" box.
  * One hour-by-station grid per gas with an episode (amber above the benchmark, light
    blue at or below, lilac suspect sensor, grey no data); a gas with no episode gets a
    one-line "not above limit" heading with its max.
  * Episodes: station · gas · peak, with "returned below" / "still above" and duration
    boxes, and one short line (first above, peak if later, back at or below) + map link.
  * Latest reading per station (no ▲; BDL shows only its label; suspect sensors say so).
  * "How to read this alert": benchmarks first, H2S health benchmarks + CARB odor
    standard, what the alert is and is not, event details, where levels are measured,
    record and timing, detection limits, and a suspect-sensor footnote.

Accuracy rules this module holds (data layer):
  * Values verbatim (unrounded %g); "above" is STRICTLY above on the raw value.
  * A value the source marks BDL never shows its number and never counts as an H2S max.
  * No-data (missing / sentinel / TEST, gc.is_no_data) is never a value, never a max.
  * Suspect sensor = a channel inside a configured period (gfl_air.suspect_sensors, the
    perimeter-air dataset's data-quality note) or a value the feed labels ERR. A suspect
    value never sets a max; it is shown as the feed reports it in the grid colour only.
  * Any max carries its reading's station and time unless it is an episode peak listed
    below with its time (CLAUDE.md "Assumptions").
  * Every filer-supplied string is escaped; only http(s) links become hrefs.
  * Never the word "own"; no surface standards (Trisha 2026-11-26).
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import gfl_air_client as gc
from digest_render import (FOOTER_SLOT, LOGO_URL, MON_BG, MON_BORDER, MON_FG, MON_LINK,
                           MON_MARK, MON_MUTED, SANS, SERIF, e, safe_href)
from gfl_air_episodes import (_CH4_FLAMMABLE_PCT, _GAS_LABEL, FALLBACK_N_STATIONS_EXPECTED, _et,
                              et_date, et_label)

HTML_BUDGET_BYTES = 85_000      # Gmail clips near 102 KB and would hide the footer
MAX_GRID_COLUMNS = 36           # beyond this the grid buckets hours (multi-day catch-up)

# Tier / cell colours (colour encodes state only).
SCREEN_BAR, SCREEN_BG, SCREEN_INK = "#b26b00", "#fff6e5", "#5c3a00"
CELL_ABOVE = "#d98a00"
CELL_OK = "#e6edf1"
CELL_SUSPECT = "#e7dcf5"
CELL_NODATA = "#b8bec4"
SUSPECT_BORDER = "#c9b8e6"
TABLE_NODATA_BG = "#eeeeee"

_SUBJ_GAS = {"h2s": "H2S", "ch4": "Methane"}
_BENCH_TXT = {"h2s": "30 ppb", "ch4": "40 ppm"}

# --------------------------------------------------------------------------- #
# Fixed copy (one place; both renderers read it)
# --------------------------------------------------------------------------- #

BANNER_TITLE = "Automated screening alert — public hourly data"

BENCH_INTRO = "These alerts compare public hourly readings with two Consent Judgment action levels:"
BENCH_ITEMS = [
    "Perimeter H2S: 30 ppb, 15-minute rolling average (Consent Judgment, definition T)",
    "Perimeter methane (CH4): 40 ppm, 15-minute rolling average (Consent Judgment, definition U). "
    "That is about 0.08% of methane's lower explosive limit.",
]
BENCH_FLAG = "A reading is flagged only when its raw, unrounded value is strictly above the benchmark."

HEALTH_INTRO = ("Each covers a set averaging time, from 15 minutes to a lifetime. The feed does not say how "
                "its hourly values are calculated, so one hourly value above a benchmark is not, by itself, "
                "an exceedance of it.")
# EGLE: SET-report B1 master table. ATSDR: MRL list (final 11/2016). OEHHA: chemical page
# (acute REL 42 µg/m³, revised 1999; checked 2026-11-26; ppb conversion ours). EPA: IRIS
# (revised 2003-07-28; ppb conversion ours).
HEALTH_ITEMS = [
    "EGLE screening level (ITSL): 72 ppb, 24-hour average (100 µg/m³)",
    "EGLE acute screening level (ITSL): 750 ppb, 15-minute average",
    "California OEHHA acute Reference Exposure Level: 42 µg/m³ (about 30 ppb), 1-hour average, "
    "based on headache, nausea and physiological responses to odor",
    "ATSDR acute Minimal Risk Level: 70 ppb (0.07 ppm), exposures of 1 to 14 days",
    "ATSDR intermediate Minimal Risk Level: 20 ppb (0.02 ppm), exposures of 15 to 364 days",
    "U.S. EPA IRIS reference concentration: 2 µg/m³ (about 1.4 ppb), lifetime exposure",
]
ODOR_LINE = ("California Air Resources Board ambient air quality standard, 30 ppb (0.03 ppm), 1-hour "
             "average, adopted in 1969 for odor control, not as a health level.")
NO_METHANE_HEALTH = "Methane has no EGLE health screening level."

IS_NOT_LEAD = ("This is an automated screening alert from public hourly data. It is NOT a formal "
               "determination that a Consent Judgment action level was exceeded. It does NOT establish "
               "that AHL exceeded, or failed to correct, anything.")
IS_NOT_ITEMS = [
    "The action levels are 15-minute rolling averages of about 1-minute measurements. The public feed "
    "gives hourly values only.",
    "The feed does not say whether an hourly value is instantaneous, an average or a maximum.",
    "Only AHL's nonpublic data can show whether a 15-minute exceedance occurred. The operator's continuous "
    "perimeter monitor and AHL's ¶6.3 Perimeter Action Level Log are the authoritative 15-minute record.",
    "Returned below means the public hourly value fell back to or under the benchmark. It says nothing "
    "about compliance or correction.",
]

FACILITY = "Arbor Hills Landfill, 10690 West Six Mile Road, Salem Township, MI"
RESPONSIBLE = "Arbor Hills Landfill, Inc. (AHL), defendant under the Consent Judgment. Not the operator, GFL."
AUTHORITY = "Consent Judgment No. 2020-0593-CE, 30th Circuit Court, entered 2022-03-07"

WHERE_ITEMS = [
    "H2S 30 ppb and methane 40 ppm are perimeter levels. Stations MS-1 to MS-6 sample ambient air at the "
    "fenceline. The 72 and 750 ppb ITSL values are health benchmarks, not Consent Judgment triggers.",
]
REGIMES = ("Arbor Hills is subject to NSPS Subpart WWW (40 CFR 60) and NESHAP Subpart AAAA (40 CFR 63) "
           "under its permit MI-ROP-N2688-2011a, to Consent Judgment No. 2020-0593-CE, and to Michigan "
           "Part 115. The perimeter action levels are Consent Judgment terms.")
RECORD_ITEMS = [
    "Each episode's start, peak, return and duration is logged on the Perimeter Action-Level Episodes tab "
    "of the case file. This supports checking whether AHL's ¶6.3 log and quarterly root-cause report show "
    "a root-cause analysis and a prevent-recurrence fix for each.",
    "The monitor reads the public feed once a day, so one email typically covers 24 hours.",
    # Inferred from every MS-* row (checked 2026-11-26): H2S labelled BDL up to 6.9 ppb, smallest
    # number-labelled 7.0 ppb (same 2024-2026); CH4 BDL only at 0 or -1, smallest number 1 ppm.
    "Detection limits: the feed does not publish them, but its labels imply them. It labels H2S readings "
    "under 7 ppb as BDL (below detection limit), and methane readings of 0 ppm or less. The smallest values "
    "it reports as numbers are 7 ppb H2S and 1 ppm methane. The H2S figure is above some of the health "
    "benchmarks listed above, such as the EPA reference concentration (about 1.4 ppb).",
]
SUSPECT_FOOT_TAIL = ("These periods come from the perimeter-air dataset's data-quality note. Values are "
                     "shown as the feed reports them.")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def num(v) -> str:
    """Verbatim, unrounded; thousands separators only for whole numbers >= 1000."""
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.0f}" if abs(f) >= 1000 and f == int(f) else f"{f:g}"


def unit(gas: str) -> str:
    return gc.gas_unit(gas)


def et_short(iso: str) -> str:
    """'Nov 22, 2026, 1:00 AM ET' (the reading's own ET date and time)."""
    dt = _et(iso)
    if dt is None:
        return et_label(iso)
    return dt.strftime("%b ") + str(dt.day) + dt.strftime(", %Y, ") + \
        dt.strftime("%I:%M %p").lstrip("0") + " ET"


def utc_hm(iso: str) -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%H:%M UTC")
    except (TypeError, ValueError):
        return ""


def _when(iso: str, first_iso: str) -> str:
    """Time only when on the first reading's ET date, else 'Nov 22, 1:00 AM'."""
    full, first = et_short(iso), et_short(first_iso)
    d, t = full.rsplit(", ", 1)
    t = t.replace(" ET", "")
    return t if d == first.rsplit(", ", 1)[0] else f"{d.rsplit(', ', 1)[0]}, {t}"


def _is_label(t: str) -> bool:
    t = (t or "").strip()
    return bool(t) and not t.replace(".", "", 1).lstrip("-").isdigit()


def _et_naive(iso: str) -> str:
    dt = _et(iso)
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""


def suspect_periods(cfg_gfl: dict) -> list[dict]:
    """The configured suspect-sensor periods (gfl_air.suspect_sensors). Each entry:
    station, gas ('h2s'|'ch4'), start / end (America/Detroit local, 'YYYY-MM-DD' or
    'YYYY-MM-DDTHH:MM'; start inclusive, end exclusive), note. Malformed entries dropped."""
    out = []
    for p in (cfg_gfl or {}).get("suspect_sensors") or []:
        if not isinstance(p, dict):
            continue
        st, gas, a, b = p.get("station"), p.get("gas"), str(p.get("start") or ""), str(p.get("end") or "")
        if st and gas in gc.GASES and a and b:
            out.append({"station": str(st), "gas": gas, "start": a, "end": b, "note": str(p.get("note") or "")})
    return out


def in_period(periods: list[dict], station: str, gas: str, iso: str) -> dict | None:
    t = _et_naive(iso)
    if not t:
        return None
    for p in periods:
        if p["station"] == station and p["gas"] == gas and p["start"] <= t < p["end"]:
            return p
    return None


def is_suspect(periods, row: dict, gas: str) -> bool:
    """A suspect reading: inside a configured period, or the feed labels it ERR."""
    if gc.gas_text(row, gas).upper() == "ERR":
        return True
    return in_period(periods, gc.station_of(row), gas, gc.reading_iso(row)) is not None


# --------------------------------------------------------------------------- #
# Content model
# --------------------------------------------------------------------------- #

def _series(readings: list[dict], prefix: str) -> dict:
    out: dict[str, dict[str, dict]] = {}
    for r in readings:
        st = gc.station_of(r)
        if not st or (prefix and not st.startswith(prefix)):
            continue
        out.setdefault(st, {})[gc.reading_iso(r)] = r
    return out


def run_max(series: dict, gas: str, sentinels, periods) -> tuple | None:
    """(value, station, iso) of the highest real, non-suspect hourly value; H2S BDL excluded."""
    best = None
    for st, by in series.items():
        for iso, r in by.items():
            if gc.is_no_data(r, gas, sentinels) or is_suspect(periods, r, gas):
                continue
            if gas == "h2s" and gc.gas_text(r, gas).upper() == "BDL":
                continue
            v = gc.gas_value(r, gas)
            if v is not None and (best is None or v > best[0]):
                best = (v, st, iso)
    return best


def _episodes(opened, closed, state_after) -> list[dict]:
    """Every episode this email reports: opened this run (and maybe closed), closed this run
    after opening earlier, and still open at the end of the run. One dict each, with
    'closed' (the close record or None)."""
    closed_by = {c.get("event_id"): c for c in closed}
    seen, out = set(), []
    for o in opened:
        eid = o.get("event_id")
        out.append({**o, "closed": closed_by.get(eid)})
        seen.add(eid)
    for c in closed:
        if c.get("event_id") not in seen:
            out.append({**c, "closed": c})
            seen.add(c.get("event_id"))
    for key, ep in sorted((state_after or {}).items()):
        if ep.get("event_id") in seen:
            continue
        st, _, gas = key.partition("|")
        out.append({"station": st, "gas": gas, **ep, "closed": None})
    return sorted(out, key=lambda x: (x.get("opened_at", ""), x["station"], gc.GASES.index(x["gas"])))


def build(*, opened, closed, state_after, readings, monitor_rows, watch_thresholds, sentinels,
          retrieved_iso, coords=None, roster=None, registry_available=True, historical=False,
          periods=None, prefix=gc.DEFAULT_STATION_PREFIX, link="") -> dict:
    """Everything both renderers need, computed once."""
    periods = periods or []
    series = _series(readings, prefix)
    hours = sorted({iso for by in series.values() for iso in by})
    eps = _episodes(opened, closed, state_after)
    gases = [g for g in gc.GASES if any(x["gas"] == g for x in eps)]
    subj_gases = [g for g in gc.GASES if any(x["gas"] == g for x in list(opened) + list(closed))] or gases
    still = sum(1 for x in eps if x["closed"] is None)
    maxes = {g: run_max(series, g, sentinels, periods) for g in gc.GASES}
    peaks = {(x["station"], x["gas"], x.get("peak_at")) for x in eps}

    # Stations: roster first, then anything seen.
    roster_list = list(dict.fromkeys(roster)) if roster else []
    stations = list(dict.fromkeys(roster_list + sorted(set(series) | {r["station"] for r in monitor_rows})))
    reporting = sum(1 for r in monitor_rows if not r.get("silent")
                    and (not roster_list or r["station"] in roster_list))
    legacy = not roster_list
    n_expected = len(roster_list) if roster_list else FALLBACK_N_STATIONS_EXPECTED
    seen_st = {r["station"] for r in monitor_rows}
    missing = [s for s in roster_list if s not in seen_st]
    unexpected = sorted(s for s in seen_st if roster_list and s not in roster_list)

    run_hours = hours
    stuck = []
    for p in periods:
        if any(in_period([p], p["station"], p["gas"], h) for h in run_hours) and \
                p["station"] in series:
            stuck.append(p)
    err_channels = sorted({(st, g) for st, by in series.items() for r in by.values() for g in gc.GASES
                           if gc.gas_text(r, g).upper() == "ERR"})

    subj_stations = sorted({x["station"] for x in list(opened) + list(closed)})
    span_isos = [x.get("opened_at", "") for x in opened] + [x.get("cleared_at", "") for x in closed]
    dates = sorted({d for d in (et_date(i) for i in span_isos) if d})
    span = dates[0] if len(dates) == 1 else (f"{dates[0]}..{dates[-1]}" if dates else "")
    tag = " + ".join(_SUBJ_GAS[g] for g in subj_gases) + " SCREENING ALERT"
    tag = f"[HISTORICAL {tag} — NOT A LIVE INCIDENT]" if historical else f"[{tag}]"
    verb = ("above CJ benchmark, then returned below" if opened and closed
            else "above CJ benchmark" if opened else "returned below CJ benchmark")
    subject = (f"{tag} Arbor Hills perimeter — hourly value(s) {verb} — "
               f"{', '.join(subj_stations) or 'perimeter'} — {span}, ET")

    return {
        "subject": subject, "series": series, "hours": hours, "episodes": eps, "gases": gases,
        "n_opened": len(opened), "n_closed": len(closed), "n_still": still, "maxes": maxes,
        "peaks": peaks, "stations": stations, "monitor_rows": monitor_rows, "reporting": reporting,
        "n_expected": n_expected, "missing": missing, "unexpected": unexpected, "legacy": legacy,
        "n_rows": len(monitor_rows),
        "registry_available": registry_available, "stuck": stuck, "err_channels": err_channels,
        "periods": periods, "sentinels": sentinels, "wt": watch_thresholds or {},
        "retrieved": retrieved_iso, "coords": coords or {}, "link": link, "historical": historical,
    }


# --------------------------------------------------------------------------- #
# Shared phrasing
# --------------------------------------------------------------------------- #

def _highest_phrase(m: dict) -> str:
    parts = []
    for g in ("h2s", "ch4"):
        b = m["maxes"][g]
        if b:
            parts.append(f"{num(b[0])} {unit(g)} {_GAS_LABEL[g]}")
        elif g == "h2s":
            parts.append("H2S below detection limit")
    return f"Highest: {', '.join(parts)}." if parts else ""


def lede_text(m: dict) -> tuple[str, str]:
    """(first sentence, counts sentence + highest)."""
    h = m["hours"]
    first = (f"This {len(h)}-hour run covered {et_short(h[0])} to {et_short(h[-1])}." if h else
             "This run had no readings.")
    counts = (f"{m['n_opened']} episode(s) opened, {m['n_closed']} returned below, "
              f"{m['n_still']} still above at the end of the run.")
    return first, counts + " " + _highest_phrase(m)


def preheader(m: dict) -> str:
    return (f"{m['n_opened']} opened · {m['n_closed']} returned below · {m['n_still']} still above · "
            f"{_highest_phrase(m)} · screening lead, not an exceedance determination")


def grid_title(m: dict, gas: str) -> tuple[str, str]:
    """(bold title, muted tail)."""
    if gas in m["gases"]:
        b = m["maxes"][gas]
        title = f"Hourly {_GAS_LABEL[gas]} by station" + (f" · Max {num(b[0])} {unit(gas)}" if b else "")
        tail = ""
        if b and (b[1], gas, b[2]) not in m["peaks"]:
            tail = f"({b[1]}, {et_short(b[2])})"
        return title, tail
    title = f"Hourly {_GAS_LABEL[gas]} by station: not above limit"
    b = m["maxes"][gas]
    if b:
        title += f" · Max {num(b[0])} {unit(gas)}"
        tail = "" if (b[1], gas, b[2]) in m["peaks"] else f"({b[1]}, {et_short(b[2])})"
        return title, tail
    silent = [st for st in m["stations"] if not any(
        not gc.is_no_data(r, gas, m["sentinels"]) for r in m["series"].get(st, {}).values())]
    if gas == "h2s":
        title += " · all readings below detection limit"
    tail = f"(no {_GAS_LABEL[gas]} data from {', '.join(silent)} this run)" if silent else ""
    return title, tail


def episode_head(x: dict) -> str:
    return f"{x['station']} · {_GAS_LABEL[x['gas']]} · peak {num(x.get('peak_value'))} {unit(x['gas'])}"


def episode_line(x: dict) -> str:
    u, c = unit(x["gas"]), x["closed"]
    out = (f"Above {num(x.get('threshold'))} {u} from {et_short(x.get('opened_at', ''))} "
           f"({num(x.get('opened_value'))} {u}).")
    if x.get("peak_at") and x.get("peak_at") != x.get("opened_at"):
        out += f" Peak {num(x.get('peak_value'))} {u} at {_when(x['peak_at'], x['opened_at'])}."
    if c:
        # A value the source marks BDL shows no number (Trisha 2026-11-26).
        back = ("below detection limit" if (c.get("cleared_text") or "").upper() == "BDL"
                else f"{num(c.get('cleared_value'))} {u}")
        out += f" Back at or below at {_when(c['cleared_at'], x['opened_at'])} ({back})."
    else:
        out += " Still above at the end of the run."
    if x["gas"] == "ch4":
        try:
            pct = float(x.get("peak_value")) / 10000.0
        except (TypeError, ValueError):
            pct = None
        if pct is not None and pct >= _CH4_FLAMMABLE_PCT:
            out += (f" If confirmed as a representative ambient-air concentration, {pct:g}% methane falls "
                    f"within methane's ~5-15% flammable range and warrants immediate safety assessment.")
    return out


def duration_text(x: dict) -> str:
    c = x["closed"]
    if c and c.get("duration_hours") is not None:
        return f"duration {num(c['duration_hours'])} hr"
    return ""


def map_url(m: dict, st: str) -> str:
    c = m["coords"].get(st) or {}
    if c.get("lat") is None or c.get("lon") is None:
        return ""
    return f"https://www.google.com/maps?q={c['lat']:.5f},{c['lon']:.5f}"


def cell_state(m: dict, st: str, iso: str, gas: str) -> str:
    """'above' | 'suspect' | 'ok' | 'nodata' for one station-hour."""
    r = m["series"].get(st, {}).get(iso)
    if r is None or gc.is_no_data(r, gas, m["sentinels"]):
        return "nodata"
    v = gc.gas_value(r, gas)
    thr = m["wt"].get(gc.gas_cfgkey(gas))
    if thr is not None and v is not None and v > float(thr):
        return "above"
    return "suspect" if is_suspect(m["periods"], r, gas) else "ok"


_RANK = {"above": 3, "suspect": 1, "ok": 2, "nodata": 0}


def grid_columns(m: dict) -> tuple[list[list[str]], int]:
    """Hour columns; buckets of k hours when a catch-up run is long (worst cell wins)."""
    h = m["hours"]
    k = max(1, math.ceil(len(h) / MAX_GRID_COLUMNS)) if len(h) > MAX_GRID_COLUMNS else 1
    return [h[i:i + k] for i in range(0, len(h), k)], k


def grid_state(m: dict, st: str, col: list[str], gas: str) -> str:
    return max((cell_state(m, st, iso, gas) for iso in col), key=lambda s: _RANK[s], default="nodata")


def latest_cell(m: dict, row: dict, gas: str) -> tuple[str, str]:
    """(kind, text) for the latest-reading table: kind in value|above|bdl|suspect|nodata|label."""
    g = row[gas]
    st_period = in_period(m["periods"], row["station"], gas, row.get("as_of", ""))
    if g["no_data"]:
        return "nodata", "no data"
    if st_period or (g.get("text") or "").upper() == "ERR":
        return "suspect", "suspect sensor"
    if g["over"]:
        return "above", num(g["val"])
    if (g.get("text") or "").upper() == "BDL":
        return "bdl", "source: BDL"
    if _is_label(g.get("text")):
        return "label", f"{num(g['val'])} (source: {g['text']})"
    return "value", num(g["val"])


def asof_line(m: dict) -> str:
    times = [r["as_of"] for r in m["monitor_rows"] if not r.get("silent") and r.get("as_of")]
    if not times:
        return ""
    common = max(set(times), key=times.count)
    odd = [f"{r['station']}: {et_short(r['as_of'])}" for r in m["monitor_rows"]
           if not r.get("silent") and r.get("as_of") and r["as_of"] != common]
    return f"As of {et_short(common)}" + (f" ({'; '.join(odd)})" if odd else "")


def silent_text(r: dict, batch_start: str = "") -> str:
    """A station with no reading this run. Its last reading is named only when it predates
    this batch (a newer registry date — historical replay, fetch timing — says nothing)."""
    last = r.get("last_read_iso") or ""
    if last and (not batch_start or last < batch_start):
        return f"no data at all this run (no H2S, methane or wind); last reading {et_short(last)}"
    return "no data at all this run (no H2S, methane or wind)"


def event_pairs(m: dict) -> list[tuple[str, str]]:
    eps = m["episodes"]
    isos = [x.get("opened_at", "") for x in eps] + [x["closed"]["cleared_at"] for x in eps if x["closed"]] + \
        [r["as_of"] for r in m["monitor_rows"] if r.get("as_of")]
    isos = sorted(i for i in isos if i)
    period = f"{et_short(isos[0])} to {et_short(isos[-1])} ({utc_hm(isos[0])} to {utc_hm(isos[-1])})" if isos else "n/a"
    status = f"{m['n_opened']} newly detected; {m['n_closed']} returned below; {m['n_still']} still above"
    dq = f"{m['reporting']} of {m['n_expected']} stations reporting"
    dq += (" (expected count is the legacy default; gfl_air.expected_stations not configured). "
           if m["legacy"] else ". ")
    why = ("the station list could not be retrieved this run" if not m["registry_available"]
           else "not in the source's station list")
    if m["legacy"] and m["n_rows"] < m["n_expected"]:
        dq += f"{m['n_expected'] - m['n_rows']} station(s) not listed ({why}). "
    if m["missing"]:
        dq += f"Not listed: {', '.join(m['missing'])} ({why}). "
    if m["unexpected"]:
        dq += (f"Listed but not in this monitor's configured roster: {', '.join(m['unexpected'])} "
               f"(not counted toward the {m['n_expected']} expected). ")
    dq += "No-data and test markers (999, 99999, TEST) are never flagged. The source does not publish calibration status. "
    names = [f"{p['station']} {_GAS_LABEL[p['gas']]}" for p in m["stuck"]]
    names += [f"{st} {_GAS_LABEL[g]} (values labelled ERR)" for st, g in m["err_channels"]]
    if names:
        grid_gases = [g for g in gc.GASES if g in m["gases"] and any(p["gas"] == g for p in m["stuck"])]
        where = (f" (lilac in the {' and '.join(_GAS_LABEL[g] for g in grid_gases)} grid)" if grid_gases else "")
        dq += f"Stuck sensor noted: {', '.join(names)}{where}; details in the note at the end."
    else:
        dq += "No stuck sensor noted for this period."
    return [
        ("Facility", FACILITY), ("Responsible party", RESPONSIBLE), ("Authority", AUTHORITY),
        ("Event IDs", ", ".join(x.get("event_id", "") for x in eps)), ("Status", status),
        ("Period", period),
        ("Source", f"GFL public perimeter-air dashboard, retrieved {et_short(m['retrieved'])} ({utc_hm(m['retrieved'])})"),
        ("Data quality", dq),
    ]


def suspect_footnote(m: dict) -> str:
    bits = [f"{p['station']} {_GAS_LABEL[p['gas']]}: {p['note']}" for p in m["stuck"] if p["note"]]
    bits += [f"{st} {_GAS_LABEL[g]}: values the feed labels ERR" for st, g in m["err_channels"]]
    return ("Suspect sensors: " + "; ".join(bits) + ". " + SUSPECT_FOOT_TAIL) if bits else ""


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #

def render_text(m: dict) -> str:
    L: list[str] = []
    a, b = lede_text(m)
    L += [a, b, "", BANNER_TITLE.upper(), ""]
    for g in gc.GASES:
        title, tail = grid_title(m, g)
        L.append(title + (f" {tail}" if tail else ""))
        if g in m["gases"]:
            for st in m["stations"]:
                above = [et_short(i) for i in m["hours"] if cell_state(m, st, i, g) == "above"]
                nod = sum(1 for i in m["hours"] if cell_state(m, st, i, g) == "nodata")
                sus = sum(1 for i in m["hours"] if cell_state(m, st, i, g) == "suspect")
                bits = [f"above {_BENCH_TXT[g]}: {len(above)} hour(s)"]
                if sus:
                    bits.append(f"suspect sensor: {sus} hour(s)")
                if nod:
                    bits.append(f"no data: {nod} hour(s)")
                L.append(f"  {st}: " + "; ".join(bits))
        L.append("")
    L.append(f"EPISODES ({len(m['episodes'])})")
    for x in m["episodes"]:
        status = "returned below" if x["closed"] else "still above"
        dur = duration_text(x)
        L.append(f"  {episode_head(x)} [{status}]" + (f" [{dur}]" if dur else ""))
        L.append(f"    {episode_line(x)}" + (f" Map: {map_url(m, x['station'])}" if map_url(m, x['station']) else ""))
    L += ["", "ALL PERIMETER MONITORS, LATEST READING (station: H2S ppb | CH4 ppm | wind)"]
    for r in m["monitor_rows"]:
        if r.get("silent"):
            L.append(f"  {r['station']}: {silent_text(r, m['hours'][0] if m['hours'] else '')}")
            continue
        h = latest_cell(m, r, "h2s")[1]
        c = latest_cell(m, r, "ch4")[1]
        wind = f"{r.get('dir_text') or r.get('dir') or '?'} {num(r.get('wind'))} mph"
        L.append(f"  {r['station']}: {h} | {c} | {wind}")
    if asof_line(m):
        L.append(f"  {asof_line(m)}")
    L += ["", f"Live dashboard: {m['link']}", "", "HOW TO READ THIS ALERT", "", "Benchmarks", BENCH_INTRO]
    L += [f"  - {x}" for x in BENCH_ITEMS] + [BENCH_FLAG, "", "H2S health benchmarks", HEALTH_INTRO]
    L += [f"  - {x}" for x in HEALTH_ITEMS] + [f"H2S odor standard: {ODOR_LINE}", NO_METHANE_HEALTH, ""]
    L += ["What this alert is, and is not", IS_NOT_LEAD] + [f"  - {x}" for x in IS_NOT_ITEMS] + ["", "Event details"]
    L += [f"  {k}: {v}" for k, v in event_pairs(m)] + ["", "Where each level is measured"]
    L += [f"  - {x}" for x in WHERE_ITEMS] + [REGIMES, "", "Record and timing"]
    L += [f"  - {x}" for x in RECORD_ITEMS]
    foot = suspect_footnote(m)
    if foot:
        L += ["", foot]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

def _row(inner: str, pad: str = "18px 28px 0 28px") -> str:
    return f'<tr><td style="padding:{pad};">{inner}</td></tr>'


def _h2(title: str, tail: str = "", count=None) -> str:
    c = f' <span style="font-weight:normal;color:{MON_MUTED};">({count})</span>' if count is not None else ""
    t = f' <span style="font-weight:normal;font-size:14px;color:{MON_MUTED};">{e(tail)}</span>' if tail else ""
    return _row(f'<div style="font-family:{SERIF};font-size:18px;line-height:24px;font-weight:bold;color:{MON_FG};'
                f'border-bottom:1px solid {MON_BORDER};padding-bottom:6px;">{e(title)}{c}{t}</div>', "26px 28px 4px 28px")


def _h3(text: str) -> str:
    return _row(f'<div style="font-family:{SANS};font-size:12px;line-height:18px;letter-spacing:0.5px;'
                f'text-transform:uppercase;font-weight:bold;color:{MON_FG};">{e(text)}</div>', "16px 28px 0 28px")


def _p(html_text: str, size: int = 14, color: str = MON_MUTED, pad: str = "4px 28px 0 28px") -> str:
    return _row(f'<div style="font-family:{SERIF};font-size:{size}px;line-height:{int(size * 1.55)}px;'
                f'color:{color};">{html_text}</div>', pad)


def _ul(items: list[str]) -> str:
    lis = "".join(f'<li style="margin:0 0 4px 0;">{e(x)}</li>' for x in items)
    return _row(f'<ul style="margin:4px 0 0 0;padding-left:18px;font-family:{SERIF};font-size:14px;'
                f'line-height:21px;color:{MON_MUTED};">{lis}</ul>', "2px 28px 0 28px")


def _chip(text: str, *, suspect: bool = False) -> str:
    bg = f"background:{CELL_SUSPECT};border:1px solid {SUSPECT_BORDER};color:{MON_FG};" if suspect else \
        f"border:1px solid #cccccc;color:{MON_MUTED};"
    return (f'<span style="font-family:{SANS};font-size:11px;line-height:14px;{bg}border-radius:3px;'
            f'padding:0 4px;white-space:nowrap;">{e(text)}</span>')


_TH = (f"font-family:{SANS};font-size:11px;line-height:16px;letter-spacing:0.4px;text-transform:uppercase;"
       f"color:{MON_MUTED};padding:6px 6px;border-bottom:1px solid {MON_BORDER};text-align:left;")
_TD = f"font-family:{SANS};font-size:13px;line-height:19px;color:{MON_FG};padding:6px 6px;border-bottom:1px solid {MON_BORDER};vertical-align:top;"
_CELL_BG = {"above": CELL_ABOVE, "ok": CELL_OK, "suspect": CELL_SUSPECT, "nodata": CELL_NODATA}


def _grid(m: dict, gas: str) -> tuple[str, int]:
    cols, k = grid_columns(m)
    tick_every = max(1, round(6 / k))
    head = '<td style="width:44px;"></td>'
    i = 0
    while i < len(cols):
        span = min(tick_every, len(cols) - i)
        dt = _et(cols[i][0])
        lab = (dt.strftime("%I %p").lstrip("0") if k == 1 else dt.strftime("%b ") + str(dt.day)) if dt else ""
        head += (f'<td colspan="{span}" style="font-family:{SANS};font-size:10px;line-height:14px;'
                 f'color:{MON_MUTED};padding:0 0 2px 0;">{e(lab)}</td>')
        i += span
    body = ""
    for st in m["stations"]:
        cells = "".join(f'<td bgcolor="{_CELL_BG[grid_state(m, st, c, gas)]}" height="16"></td>' for c in cols)
        body += f'<tr><td style="font-family:{SANS};font-size:12px;color:{MON_FG};"><b>{e(st)}</b></td>{cells}</tr>'
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="2" border="0" '
            f'style="table-layout:fixed;"><tr>{head}</tr>{body}</table>'), k


def _legend(m: dict, gas: str, k: int) -> str:
    sw = lambda c: f'<span style="display:inline-block;width:10px;height:10px;background:{c};vertical-align:middle;"></span>'  # noqa: E731
    sus = any(cell_state(m, st, i, gas) == "suspect" for st in m["stations"] for i in m["hours"])
    per = "one cell per hour, ET" if k == 1 else f"one cell per {k} hours, ET; each cell shows its highest state"
    return (f'{sw(CELL_ABOVE)} above {_BENCH_TXT[gas]} &nbsp; {sw(CELL_OK)} at or below'
            + (f' &nbsp; {sw(CELL_SUSPECT)} suspect sensor' if sus else "")
            + f' &nbsp; {sw(CELL_NODATA)} no data &nbsp;&middot;&nbsp; {e(per)}')


def _latest_table(m: dict) -> str:
    head = (f'<tr><th style="{_TH}">Station</th><th style="{_TH}">H2S ppb</th>'
            f'<th style="{_TH}">CH4 ppm</th><th style="{_TH}">Wind</th></tr>')
    body = ""
    for r in m["monitor_rows"]:
        if r.get("silent"):
            body += (f'<tr><td style="{_TD}"><b>{e(r["station"])}</b></td><td colspan="3" '
                     f'style="{_TD}background:{TABLE_NODATA_BG};color:{MON_MUTED};">{e(silent_text(r, m["hours"][0] if m["hours"] else ""))}</td></tr>')
            continue
        cells = ""
        for g in gc.GASES:
            kind, txt = latest_cell(m, r, g)
            if kind == "nodata":
                cells += f'<td style="{_TD}background:{TABLE_NODATA_BG};color:{MON_MUTED};">no data</td>'
            elif kind == "suspect":
                cells += f'<td style="{_TD}">{_chip("suspect sensor", suspect=True)}</td>'
            elif kind == "bdl":
                cells += f'<td style="{_TD}">{_chip("source: BDL")}</td>'
            elif kind == "above":
                cells += f'<td style="{_TD}background:#fde4b8;"><b>{e(txt)}</b></td>'
            else:
                cells += f'<td style="{_TD}">{e(txt)}</td>'
        wind = f"{r.get('dir_text') or r.get('dir') or '?'} {num(r.get('wind'))} mph"
        body += f'<tr><td style="{_TD}"><b>{e(r["station"])}</b></td>{cells}<td style="{_TD}white-space:nowrap;">{e(wind)}</td></tr>'
    asof = asof_line(m)
    asof_html = (f'<div style="font-family:{SANS};font-size:13px;line-height:20px;color:{MON_MUTED};'
                 f'padding-top:6px;">{e(asof)}</div>') if asof else ""
    return _row(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                f'style="border-collapse:collapse;">{head}{body}</table>{asof_html}', "10px 28px 0 28px")


def render_html(m: dict) -> str:
    a, b = lede_text(m)
    counts, highest = b.split(" Highest:", 1) if " Highest:" in b else (b, "")
    lede = f"{e(a)} <b>{e(counts)}</b>" + (f" Highest:{e(highest)}" if highest else "")
    eyebrow = "Perimeter air &middot; screening alert &middot; daily run"
    rows = f"""
<tr><td style="padding:22px 28px 0 28px;">
  <div style="font-family:{SANS};font-size:12px;line-height:18px;letter-spacing:0.5px;text-transform:uppercase;color:{MON_MUTED};">{eyebrow}</div>
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="padding-top:4px;"><tr>
    <td valign="middle" style="padding-right:10px;"><img src="{LOGO_URL}" width="32" height="32" alt="" style="display:block;width:32px;height:32px;border:0;"></td>
    <td valign="middle" style="font-family:{SERIF};font-size:22px;line-height:28px;font-weight:bold;color:{MON_FG};">Arbor Hills Monitor</td>
  </tr></table>
  <div style="font-family:{SERIF};font-size:16px;line-height:25px;color:{MON_FG};padding:8px 0 14px 0;">{lede}</div>
</td></tr>
<tr><td style="padding:0 28px;"><div style="border-top:2px solid {MON_MARK};font-size:1px;line-height:1px;">&nbsp;</div></td></tr>"""
    rows += _row(f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td width="4" bgcolor="{SCREEN_BAR}" style="background:{SCREEN_BAR};width:4px;font-size:1px;line-height:1px;">&nbsp;</td>
<td bgcolor="{SCREEN_BG}" style="background:{SCREEN_BG};padding:12px 16px;"><div style="font-family:{SANS};font-size:12px;line-height:18px;letter-spacing:0.5px;text-transform:uppercase;font-weight:bold;color:{SCREEN_INK};">{e(BANNER_TITLE)}</div></td>
</tr></table>""")
    for g in gc.GASES:
        title, tail = grid_title(m, g)
        rows += _h2(title, tail)
        if g in m["gases"]:
            grid, k = _grid(m, g)
            rows += _row(grid, "10px 28px 0 28px")
            rows += _p(_legend(m, g, k), 13, MON_MUTED, "6px 28px 0 28px")
    rows += _h2("Episodes", count=len(m["episodes"]))
    for x in m["episodes"]:
        status = (f'<span style="font-family:{SANS};font-size:11px;color:#2e5d3a;border:1px solid #9cc3a6;'
                  f'border-radius:3px;padding:0 5px;">returned below</span>') if x["closed"] else \
            (f'<span style="font-family:{SANS};font-size:11px;color:{SCREEN_INK};border:1px solid {SCREEN_BAR};'
             f'border-radius:3px;padding:0 5px;">still above</span>')
        dur = duration_text(x)
        if dur:
            status += (f' <span style="font-family:{SANS};font-size:11px;color:{MON_MUTED};border:1px solid #bbbbbb;'
                       f'border-radius:3px;padding:0 5px;white-space:nowrap;">{e(dur)}</span>')
        url = safe_href(map_url(m, x["station"]))
        maplink = f' &middot; <a href="{url}" style="color:{MON_LINK};">map</a>' if url else ""
        rows += _row(f'<div style="font-family:{SERIF};font-size:16px;line-height:23px;font-weight:bold;color:{MON_FG};">'
                     f'{e(episode_head(x))} {status}</div>'
                     f'<div style="font-family:{SANS};font-size:13px;line-height:20px;color:{MON_MUTED};padding-bottom:10px;'
                     f'border-bottom:1px solid {MON_BORDER};">{e(episode_line(x))}{maplink}</div>', "12px 28px 0 28px")
    rows += _h2("All perimeter monitors, latest reading")
    rows += _latest_table(m)
    href = safe_href(m["link"])
    if href:
        rows += _row(f'<a href="{href}" style="display:inline-block;font-family:{SANS};font-size:14px;line-height:20px;'
                     f'color:{MON_LINK};text-decoration:none;border:1px solid {MON_LINK};border-radius:4px;'
                     f'padding:8px 14px;">Live dashboard &rarr;</a>', "20px 28px 4px 28px")
    rows += _h2("How to read this alert")
    rows += _h3("Benchmarks") + _p(e(BENCH_INTRO)) + _ul(BENCH_ITEMS) + _p(e(BENCH_FLAG))
    rows += _h3("H2S health benchmarks") + _p(e(HEALTH_INTRO)) + _ul(HEALTH_ITEMS)
    rows += _p(f"<b>H2S odor standard:</b> {e(ODOR_LINE)}") + _p(e(NO_METHANE_HEALTH))
    lead = e(IS_NOT_LEAD).replace("NOT a formal determination", "<b>NOT a formal determination</b>") \
        .replace("does NOT establish", "does <b>NOT</b> establish")
    rows += _h3("What this alert is, and is not") + _p(lead) + _ul(IS_NOT_ITEMS)
    trs = ""
    for k, v in event_pairs(m):
        val = e(v)
        if k == "Source" and href:
            val = val.replace("GFL public perimeter-air dashboard",
                              f'<a href="{href}" style="color:{MON_LINK};">GFL public perimeter-air dashboard</a>', 1)
        trs += (f'<tr><td valign="top" style="font-family:{SANS};font-size:12px;line-height:18px;color:{MON_MUTED};'
                f'padding:4px 12px 4px 0;white-space:nowrap;">{e(k)}</td><td valign="top" style="font-family:{SERIF};'
                f'font-size:14px;line-height:21px;color:{MON_FG};padding:4px 0;">{val}</td></tr>')
    rows += _h3("Event details") + _row(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{trs}</table>', "10px 28px 0 28px")
    rows += _h3("Where each level is measured") + _ul(WHERE_ITEMS) + _p(e(REGIMES))
    rows += _h3("Record and timing") + _ul(RECORD_ITEMS)
    foot = suspect_footnote(m)
    if foot:
        name, rest = foot.split(": ", 1)
        rows += _p(f'<span style="font-size:13px;"><b>{e(name)}:</b> {e(rest)}</span>')
    rows += _row("", "8px 28px 0 28px")
    rows += FOOTER_SLOT
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Arbor Hills Monitor perimeter screening alert</title></head>
<body style="margin:0;padding:0;background:{MON_BG};" bgcolor="{MON_BG}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{MON_BG};">{e(preheader(m))}{"&#8199;&#65279;&#847; " * 30}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{MON_BG}" style="background:{MON_BG};">
<tr><td align="center" style="padding:16px 8px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{MON_BG}" style="width:100%;max-width:600px;background:{MON_BG};">
{rows}
</table>
</td></tr></table>
</body></html>"""


def render_run_email(**kwargs) -> tuple[str, str, str]:
    """(subject, plain text, html) for one daily run. Send iff >= 1 episode opened or closed."""
    m = build(**kwargs)
    return m["subject"], render_text(m), render_html(m)


# --------------------------------------------------------------------------- #
# Urgent (full-list exceedance / anomaly) and liveness emails — HTML parts.
# The plain-text parts stay gfl_air_emails.format_alert_body / gfl_air_liveness
# .format_liveness_body (same facts); these add the layout-C look (ADR 075).
# --------------------------------------------------------------------------- #

URG_BAR, URG_BG, URG_INK = "#b42318", "#fdecea", "#6e1710"
HEALTH_BAR, HEALTH_BG, HEALTH_INK = "#5a6a75", "#f1f3f4", "#2e3a42"
SELF_REPORTED = ("These are GFL's self-reported perimeter readings (H2S in ppb, CH4 in ppm), "
                 "not an EGLE measurement.")
LABEL_NOTE = ("A source box shows the label the feed prints beside the number, verbatim. "
              "A value the feed labels ERR is shown as a suspect sensor reading.")


def _shell(eyebrow: str, lede_html: str, pre: str, rows: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Arbor Hills Monitor perimeter alert</title></head>
<body style="margin:0;padding:0;background:{MON_BG};" bgcolor="{MON_BG}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{MON_BG};">{e(pre)}{"&#8199;&#65279;&#847; " * 30}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{MON_BG}" style="background:{MON_BG};">
<tr><td align="center" style="padding:16px 8px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{MON_BG}" style="width:100%;max-width:600px;background:{MON_BG};">
<tr><td style="padding:22px 28px 0 28px;">
  <div style="font-family:{SANS};font-size:12px;line-height:18px;letter-spacing:0.5px;text-transform:uppercase;color:{MON_MUTED};">{eyebrow}</div>
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="padding-top:4px;"><tr>
    <td valign="middle" style="padding-right:10px;"><img src="{LOGO_URL}" width="32" height="32" alt="" style="display:block;width:32px;height:32px;border:0;"></td>
    <td valign="middle" style="font-family:{SERIF};font-size:22px;line-height:28px;font-weight:bold;color:{MON_FG};">Arbor Hills Monitor</td>
  </tr></table>
  <div style="font-family:{SERIF};font-size:16px;line-height:25px;color:{MON_FG};padding:8px 0 14px 0;">{lede_html}</div>
</td></tr>
<tr><td style="padding:0 28px;"><div style="border-top:2px solid {MON_MARK};font-size:1px;line-height:1px;">&nbsp;</div></td></tr>
{rows}
<tr><td style="padding:8px 28px 0 28px;"></td></tr>
{FOOTER_SLOT}
</table>
</td></tr></table>
</body></html>"""


def _banner(bar: str, bg: str, ink: str, title: str, text: str = "") -> str:
    body = (f'<div style="font-family:{SERIF};font-size:15px;line-height:23px;color:{ink};padding-top:4px;">{e(text)}</div>'
            if text else "")
    return _row(f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td width="4" bgcolor="{bar}" style="background:{bar};width:4px;font-size:1px;line-height:1px;">&nbsp;</td>
<td bgcolor="{bg}" style="background:{bg};padding:12px 16px;"><div style="font-family:{SANS};font-size:12px;line-height:18px;letter-spacing:0.5px;text-transform:uppercase;font-weight:bold;color:{ink};">{e(title)}</div>{body}</td>
</tr></table>""")


def _button(link: str) -> str:
    href = safe_href(link)
    if not href:
        return ""
    return _row(f'<a href="{href}" style="display:inline-block;font-family:{SANS};font-size:14px;line-height:20px;'
                f'color:{MON_LINK};text-decoration:none;border:1px solid {MON_LINK};border-radius:4px;'
                f'padding:8px 14px;">Live dashboard &rarr;</a>', "20px 28px 4px 28px")


def urgent_rows(readings, thresholds, sentinels, alert_on_sentinel, h2s_averaged=False) -> list[dict]:
    """Structured rows for the urgent email: the same readings alert_lines flags (exceedance,
    and sentinel/anomaly when alert_on_sentinel), in feed order."""
    thr = {k: v for k, v in (thresholds or {}).items() if not (h2s_averaged and k == "h2s_ppb")}
    out = []
    for r in readings:
        c = gc.classify_reading(r, thr, sentinels, None)
        sev = c["severity"]
        if sev == "ok" or (sev == "anomaly" and not alert_on_sentinel):
            continue
        parts = []
        for g in gc.GASES:
            val, status = c[g]
            if status in ("exceedance", "sentinel"):
                parts.append({"gas": g, "val": val, "status": status, "text": gc.gas_text(r, g)})
        out.append({"flag": "EXCEEDANCE" if sev == "urgent" else "anomaly",
                    "station": gc.station_of(r), "iso": gc.reading_iso(r), "parts": parts})
    return out


def render_urgent_html(rows: list[dict], avg_lines: list[str], *, thresholds: dict, link: str,
                       h2s_window_hours: int = 24, total_lines: int | None = None,
                       max_rows: int = 20) -> str:
    exc = [r for r in rows if r["flag"] == "EXCEEDANCE"]
    best = None
    for r in exc:
        for p in r["parts"]:
            if p["status"] == "exceedance" and (best is None or p["val"] > best[0]):
                best = (p["val"], p["gas"], r["station"], r["iso"], p["text"])
    n_exc = len(exc) + len(avg_lines)
    if best:
        lede = (f"<b>{n_exc} flagged reading(s) at or above a perimeter action level</b>. Highest: "
                f"<b>{e(num(best[0]))} {unit(best[1])}</b> {e(_GAS_LABEL[best[1]])} at {e(best[2])}, "
                f"{e(et_short(best[3]))}.")
        if best[4].upper() == "ERR":
            lede += " The feed labels that value ERR (suspect sensor)."
    elif n_exc:
        lede = f"<b>{n_exc} station(s) at or above the H2S {h2s_window_hours}-hour average action level.</b>"
    else:
        lede = f"<b>{len(rows)} no-data or sensor-anomaly reading(s)</b> flagged in the perimeter feed."
    pre = re.sub(r"<[^>]+>", "", lede)
    body = _banner(URG_BAR, URG_BG, URG_INK, "Urgent: readings crossed a perimeter action level" if n_exc
                   else "Sensor anomaly in the perimeter feed", SELF_REPORTED)
    head = (f'<tr><th style="{_TH}">Flag</th><th style="{_TH}">Station</th><th style="{_TH}">Reading time (ET)</th>'
            f'<th style="{_TH}">Reading</th></tr>')
    trs = ""
    for line in avg_lines:
        trs += (f'<tr><td style="{_TD}background:{URG_BG};font-size:11px;letter-spacing:0.4px;">EXCEEDANCE</td>'
                f'<td colspan="3" style="{_TD}">{e(line.replace("EXCEEDANCE", "").strip())}</td></tr>')
    for r in rows[:max_rows]:
        bits = []
        for p in r["parts"]:
            g = p["gas"]
            s = f"{gc.gas_field(g)} {num(p['val'])} {unit(g)}"
            if p["status"] == "sentinel":
                s = e(s) + " (no-data marker)"
            else:
                lvl = (thresholds or {}).get(gc.gas_cfgkey(g))
                s = f"<b>{e(s)}</b>" + (f" &ge; {e(num(lvl))} {unit(g)} action level" if lvl is not None else "")
            t = (p["text"] or "").strip()
            if t.upper() == "ERR":
                s += " " + _chip("suspect sensor · source: ERR", suspect=True)
            elif _is_label(t):
                s += " " + _chip(f"source: {t}")
            bits.append(s)
        bg = f"background:{URG_BG};" if r["flag"] == "EXCEEDANCE" else f"background:{TABLE_NODATA_BG};"
        trs += (f'<tr><td style="{_TD}{bg}font-size:11px;letter-spacing:0.4px;">{e(r["flag"])}</td>'
                f'<td style="{_TD}"><b>{e(r["station"])}</b></td>'
                f'<td style="{_TD}">{e(et_short(r["iso"]))}<br><span style="color:{MON_MUTED};font-size:11px;">{e(utc_hm(r["iso"]))}</span></td>'
                f'<td style="{_TD}">{"<br>".join(bits)}</td></tr>')
    more = max(0, (total_lines if total_lines is not None else len(rows) + len(avg_lines)) - min(len(rows), max_rows) - len(avg_lines))
    body += _h2("Flagged readings", count=len(rows) + len(avg_lines))
    body += _row(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                 f'style="border-collapse:collapse;">{head}{trs}</table>', "10px 28px 0 28px")
    if more:
        body += _p(e(f"... and {more} more (see the GFL Air tab or the dashboard)."))
    body += _p(e(LABEL_NOTE), 13)
    body += _button(link)
    lv = thresholds or {}
    levels = ", ".join(f"{gc.gas_field(g)} >= {num(lv[gc.gas_cfgkey(g)])} {unit(g)}"
                       for g in gc.GASES if lv.get(gc.gas_cfgkey(g)) is not None)
    body += _h2("How these alerts work")
    body += _p(e(f"H2S exceedance alerts fire on the rolling {h2s_window_hours}-hour per-station AVERAGE (matching "
                 "the H2S action level's 24-hour averaging period), not a single instantaneous hour; CH4 alerts on "
                 "the instantaneous reading.") + "<br>" + e(f"Action levels (config, Trisha-confirmed): {levels}."))
    return _shell("Perimeter air &middot; urgent alert", lede, pre, body)


def render_liveness_html(newest_as_of: str, stale_days: int, max_stale_days: int, link: str) -> str:
    iso = newest_as_of or ""
    when = f"{et_short(iso)} ({utc_hm(iso)})" if iso else newest_as_of
    lede = f"<b>No new perimeter readings for {stale_days} day(s).</b> Newest reading on record: {e(when)}."
    body = _banner(HEALTH_BAR, HEALTH_BG, HEALTH_INK, "Feed-health notice",
                   f"This is NOT an exceedance and NOT a reading. Alert threshold: {max_stale_days} day(s).")
    body += _p(e("A healthy feed updates roughly hourly, so a multi-day silence means it may have stalled — e.g. "
                 "the ArcGIS service was rebuilt and the OBJECTID cursor no longer advances (ADR 014's OBJECTID-reset "
                 "residual), or the source went offline. The rest of the pipeline is intact; it simply isn't seeing "
                 "anything new."), 15, MON_FG, "14px 28px 0 28px")
    body += _p("<b>Check:</b> " + e("open the dashboard and see whether new readings are appearing there. If they "
                                    "are but this monitor is not, the stored cursor likely needs a reset."), 15, MON_FG)
    body += _button(link)
    body += _p(e("You will not get another liveness alert for this same stall — it fires once per stale episode, "
                 "re-arming only after the feed recovers."), 13)
    pre = f"Feed-health notice, not an exceedance · no new perimeter readings for {stale_days} day(s)"
    return _shell("Perimeter air &middot; feed health", lede, pre, body)
