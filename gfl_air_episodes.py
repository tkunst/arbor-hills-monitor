"""
gfl_air_episodes.py — Stream E (ADR 039): the per-(station, gas) action-level
episode state machine, its America/Detroit time helpers, the per-monitor
snapshot rows and the station roster/registry reconciliation. Pure. Split out of gfl_air_watcher.py (consolidation PR 3) as a
pure move; gfl_air_watcher re-exports every name.
"""
from __future__ import annotations

from collections import Counter, namedtuple
from collections.abc import Iterable
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import gfl_air_client as gc


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
# The "all monitors" snapshot rows the SCREENING email shows — raw verbatim
# readings + a strictly-above-benchmark flag + no-data/BDL qualifiers. Pure.
# ---------------------------------------------------------------------------

# The pre-roster count the SCREENING email assumed before `gfl_air.expected_stations`
# existed (six perimeter stations, MS-1..MS-6). Used ONLY when that config key is
# missing or empty, and the watcher logs loudly whenever it is — a magic number
# must never be the quiet default again (coder:h2s-roster-count).
FALLBACK_N_STATIONS_EXPECTED = 6


def station_roster(cfg_gfl: dict, station_prefix: str = gc.DEFAULT_STATION_PREFIX
                   ) -> tuple[list[str] | None, list[str]]:
    """The human-maintained station ROSTER from config (`gfl_air.expected_stations`):
    which perimeter stations are SUPPOSED to exist, as distinct from the live layer-0
    REGISTRY (gc.fetch_station_coords: what the source lists right now). Returns
    (roster, warnings). `roster` is the de-duplicated list in config order, or None
    when the key is missing, empty or not a list — the caller then falls back to
    FALLBACK_N_STATIONS_EXPECTED and must log every warning returned here. An entry
    that is not a string, is blank, or doesn't carry `station_prefix` is DROPPED with
    a warning: a prefix-less name can never match a reading or a registry entry (both
    are prefix-filtered), and keeping it would have the email state a config typo as
    a fact about the source ("MS1: not in the source's station list"). Pure."""
    raw = (cfg_gfl or {}).get("expected_stations")
    warnings: list[str] = []
    if not isinstance(raw, (list, tuple)) or not raw:
        warnings.append(
            "gfl_air.expected_stations is missing, empty, or not a list in config.yml — "
            f"the SCREENING email falls back to the legacy count of "
            f"{FALLBACK_N_STATIONS_EXPECTED} expected stations; a silent station can "
            "only be named once the roster is configured.")
        return None, warnings
    roster: list[str] = []
    seen: set[str] = set()          # every name met, dropped ones included: one warning per cause
    for item in raw:
        if not isinstance(item, str):
            warnings.append(f"gfl_air.expected_stations entry {item!r} is not a string "
                            "(ignored).")
            continue
        name = item.strip()
        if not name:
            warnings.append("gfl_air.expected_stations has a blank entry (ignored).")
            continue
        if name in seen:
            warnings.append(f"gfl_air.expected_stations lists {name} more than once "
                            "(counted once).")
            continue
        seen.add(name)
        if station_prefix and not name.startswith(station_prefix):
            warnings.append(
                f"gfl_air.expected_stations entry {name!r} does not start with "
                f"station_prefix {station_prefix!r} — no reading or registry entry can "
                "ever match it (ignored; fix the spelling in config.yml).")
            continue
        roster.append(name)
    if not roster:
        warnings.append("gfl_air.expected_stations has no usable entries — falling back "
                        f"to the legacy count of {FALLBACK_N_STATIONS_EXPECTED}.")
        return None, warnings
    return roster, warnings


def roster_mismatch(expected_stations: list[str] | None, seen: Iterable[str]
                    ) -> tuple[list[str], list[str]]:
    """Reconcile the configured roster against the stations SEEN — a reading set
    (every poll: the stations with a reading in this batch) or the monitor-table rows
    (screening-email time: readings ∪ layer-0 registry). Returns (unexpected,
    missing): `unexpected` = seen but not in the roster (a genuinely new MS-7, or a
    naming variant such as "MS-01" beside "MS-1" — never silently counted as one of
    the expected N, never silently dropped), sorted; `missing` = in the roster but
    not seen, in roster order (against the table rows this means neither a reading
    nor a registry entry — distinct from "silent", a registry station with no
    reading). Both empty when there is no roster. Pure."""
    if not expected_stations:
        return [], []
    roster = set(expected_stations)
    seen_set = {s for s in seen if s}
    return sorted(seen_set - roster), [s for s in expected_stations if s not in seen_set]


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


def _monitor_rows(latest: dict, watch_thresholds: dict, sentinels: dict | None,
                  registry: dict | None = None) -> list[dict]:
    """Per-station display rows for the all-monitors table, built from raw latest rows.

    `registry` is the station layer (gc.fetch_station_coords: {station: {..,
    'last_read_iso'}}). Every registry station with NO reading this run still gets a
    row, marked `silent` with its `last_read_iso`, so a station that stopped sending
    data is listed as "no data since ..." rather than vanishing from the table
    (MS-1, silent from 2026-09-17, dropped out this way). A silent row's `as_of` is
    '' so its stale time never widens the email's reporting period. The configured
    ROSTER (station_roster) is deliberately NOT a row source here: a roster station
    with neither a reading nor a registry entry is named by the email's own "not
    listed" note (roster_mismatch), not invented as a row."""
    wt = watch_thresholds or {}
    rows = []
    for st in sorted(set(latest) | set(registry or {})):
        if st not in latest:
            rows.append({"station": st, "as_of": "", "silent": True,
                         "last_read_iso": ((registry or {}).get(st) or {}).get("last_read_iso", "")})
            continue
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


# Rendering helper shared with gfl_air_emails (moved with the code that uses it first).
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
