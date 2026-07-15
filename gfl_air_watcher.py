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

import os
import sys
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

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
                     station_prefix: str) -> list[dict]:
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
        c = gc.classify_reading(r, thresholds, sentinels)
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
                alert_on_sentinel: bool) -> tuple[list[str], bool]:
    """(lines, has_exceedance): one human line per reading that crossed an action
    level, plus (if alert_on_sentinel) one per sentinel/no-data reading. has_exceedance
    is True iff at least one REAL exceedance (not just a sentinel) appears — it
    decides whether the email is [URGENT] vs a lower-key anomaly notice. Pure."""
    lines: list[str] = []
    has_exceedance = False
    for r in readings:
        c = gc.classify_reading(r, thresholds, sentinels)
        if c["severity"] == "ok":
            continue
        st = gc.station_of(r)
        when = gc.reading_iso(r)
        if c["severity"] == "urgent":
            has_exceedance = True
            lines.append(f"EXCEEDANCE  {st} {when}: " + "; ".join(c["reasons"]))
        elif c["severity"] == "anomaly" and alert_on_sentinel:
            lines.append(f"anomaly     {st} {when}: " + "; ".join(c["reasons"]))
    return lines, has_exceedance


def format_alert_body(lines: list[str], has_exceedance: bool, link: str,
                      thresholds: dict) -> str:
    kind = ("readings crossed a perimeter action level"
            if has_exceedance else "sensor anomalies were flagged")
    shown = lines[:_MAX_ALERT_LINES]
    more = len(lines) - len(shown)
    body = [
        f"GFL Arbor Hills perimeter air monitoring — {kind}.\n",
        "These are GFL's OWN self-reported perimeter readings (H2S in ppb, CH4 in "
        "ppm), not an EGLE measurement.\n",
        f"Action levels (config, Trisha-confirmed): "
        f"H2S >= {thresholds.get('h2s_ppb', '?')} ppb, "
        f"CH4 >= {thresholds.get('ch4_ppm', '?')} ppm.\n",
    ]
    body.extend("  " + ln for ln in shown)
    if more > 0:
        body.append(f"  ... and {more} more (see the GFL Air tab / dashboard).")
    body.append(f"\nLive dashboard:\n  {link}\n")
    return "\n".join(body)


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
              thresholds: dict, sentinels: dict, why: str) -> None:
    """Record the current latest-per-station snapshot + set the cursor, alerting on
    NONE (WDS Rule B). Used on the first-ever run and on the over-cap re-baseline.
    Writes the snapshot only (no measurement backlog, no emails) — the whole point
    is to establish state without stampeding history."""
    baseline = gc.fetch_baseline(cfg_gfl, station_prefix=prefix)
    snapshot = station_snapshot(baseline, thresholds, sentinels, prefix)
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


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[gfl-air] {reason}")
        return 0

    cfg_gfl = cfg.get("gfl_air") or {}
    thresholds = cfg_gfl.get("thresholds") or {}
    sentinels = cfg_gfl.get("sentinels") or {}
    prefix = cfg_gfl.get("station_prefix", gc.DEFAULT_STATION_PREFIX)
    mode = cfg_gfl.get("measurements_mode", "digest")
    cap = int(cfg_gfl.get("max_new_readings_per_run", 1000))
    alert_on_sentinel = bool(cfg_gfl.get("alert_on_sentinel", True))
    max_stale_days = int(cfg_gfl.get("max_stale_days", 3))
    link = cfg_gfl.get("dashboard_url") or cfg_gfl.get("service_url", "")

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
                      "first run")
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
                      f"OVER-CAP ({len(readings)} > {cap}) — suspected feed reinsert")
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

    snapshot = station_snapshot(readings, thresholds, sentinels, prefix)
    sw.write_gfl_air_summary(sheets, sheet_id, snapshot, link)  # advances the cursor
    new_cursor = gc.max_oid(readings)
    print(f"[gfl-air] {len(readings)} new reading(s) across {len(snapshot)} "
          f"station(s); {n_rows} measurement row(s) ({mode}); cursor {cursor} -> "
          f"{new_cursor}.")

    lines, has_exceedance = alert_lines(readings, thresholds, sentinels, alert_on_sentinel)
    if lines:
        tag = "URGENT" if has_exceedance else "GFL air anomaly"
        subject = f"[{tag}] Arbor Hills GFL perimeter air: {len(lines)} flagged reading(s)"
        try:
            ea.send_email(subject, format_alert_body(lines, has_exceedance, link, thresholds), cfg)
            print(f"[gfl-air]   emailed: {subject}")
        except Exception as e:  # noqa: BLE001 — alert best-effort; readings recorded
            print(f"[gfl-air]   readings recorded but alert email FAILED: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(run())
