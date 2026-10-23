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
from datetime import datetime, timezone

from gfl_air_episodes import _ET  # one America/Detroit zone for the whole stream

import archive_client as ac
import drive_client as dc
import sheet_writer as sw
import gfl_air_client as gc
import email_alerts as ea
from config_loader import load_config
from types import SimpleNamespace

_FACILITY = "Arbor Hills Landfill"
_DOC_NAME = "GFL perimeter air monitoring (GFL self-reported)"


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


# Pure helpers live in three modules split out by consolidation PR 3 (pure moves,
# no logic change): episodes + time helpers, email rendering, liveness. Every
# name is re-exported here, so gfl_air_watcher.<name> keeps working.
from gfl_air_episodes import (  # noqa: F401 — pure move; re-exported for callers + tests
    _CJ_SOURCE,
    _GAS_LABEL,
    _CH4_FLAMMABLE_PCT,
    EpisodeResult,
    _et,
    et_date,
    et_label,
    _et_iso,
    _duration_hours,
    _iso_to_epoch_ms,
    _HISTORICAL_AFTER_DAYS,
    is_historical,
    _episode_key,
    _event_id,
    _close_record,
    process_episodes,
    perimeter_episode_rows,
    latest_per_station,
    _monitor_rows,
    _num,
    FALLBACK_N_STATIONS_EXPECTED,
    station_roster,
    roster_mismatch,
)
from gfl_air_emails import (  # noqa: F401 — pure move; re-exported for callers + tests
    _MAX_ALERT_LINES,
    station_snapshot,
    alert_lines,
    h2s_average_alert_lines,
    _levels_line,
    format_alert_body,
    _rounds_to_benchmark,
    _ch4_pct,
    _gas_word,
    _date_span_et,
    _coord_str,
    _wind_str,
    _context_table,
    _screening_disclaimer_lines,
    _identity_block,
    _cj_implications_lines,
    _benchmark_definition_lines,
    _benchmark_reference_lines,
    _subject,
    format_screening_email,
    format_closeout_email,
)
from gfl_air_liveness import (  # noqa: F401 — pure move; re-exported for callers + tests
    _AS_OF_FMT,
    _parse_as_of,
    liveness_decision,
    format_liveness_body,
)

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
    interpretation stays downstream. OBJECTID rides along as an ordering key.
    `h2s_text`/`ch4_text` keep the source's own labels ("BDL" below 7 ppb), and
    `raw` keeps EVERY field the source returned, untouched (addendum 2026-10-06)."""
    return {
        "monitor_id": gc.station_of(r),
        "timestamp": gc.reading_iso(r),
        "h2s_ppb": r.get("H2S"),
        "ch4_ppm": r.get("CH4"),
        "h2s_text": r.get("H2S_Text"),
        "ch4_text": r.get("CH4_Text"),
        "wind_direction": r.get("Direction"),
        "wind_speed": r.get("Speed"),
        "temp": r.get("Temp"),
        "oid": gc.oid_of(r),
        "raw": dict(r),
    }


def select_capture_rows(readings: list[dict], thresholds: dict, sentinels: dict | None,
                        watch_thresholds: dict | None, baseline_hours: float,
                        station_prefix: str, mode: str = "sample") -> list[dict]:
    """Pick the readings to durably capture this poll (Trisha's spec, ADR 026).
    mode "all" (addendum 2026-10-06, the live setting): keep EVERY perimeter
    reading. mode "sample" (the original spec): keep EVERY reading whose own
    classification is exceedance OR watch (Tier-1 CH4 >= 40) — the full hourly
    series through any elevated period — and, for non-elevated readings, keep at
    least one per `baseline_hours` window per station. `readings` are OBJECTID-ASC
    (oldest first), so the spacing walks forward correctly. Pure/unit-tested."""
    baseline_secs = float(baseline_hours) * 3600.0
    kept: list[dict] = []
    last_kept_s: dict[str, float] = {}
    for r in readings:
        st = gc.station_of(r)
        if not st or (station_prefix and not st.startswith(station_prefix)):
            continue
        if mode == "all":
            kept.append(_capture_row(r))
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


# Set when the durable capture of a fetched batch fails (or is enabled but not
# configured). run() still finishes the alert path; __main__ then exits 1 so the
# GitHub failure email surfaces it — with capture.mode "all" a silent miss would be
# real data loss (the cursor has already moved past those readings).
_CAPTURE_FAILED = False


def capture_mode(cfg_gfl: dict) -> str:
    """Normalized capture.mode. Anything other than "sample" captures everything:
    a typo must fail SAFE (keep more data), never quietly fall back to sampling."""
    cap = cfg_gfl.get("capture") or {}
    if "mode" not in cap:
        return "sample"                     # legacy configs keep their old behavior
    raw = str(cap.get("mode") or "").strip().lower()
    if raw not in ("all", "sample"):
        print(f"[gfl-air]   WARNING: gfl_air.capture.mode is {cap.get('mode')!r} "
              "(expected 'all' or 'sample'); capturing ALL readings")
        return "all"
    return raw


def _capture_batch(cfg_gfl: dict, readings: list[dict], thresholds: dict,
                   sentinels: dict | None, watch_thresholds: dict | None,
                   prefix: str) -> int:
    """Select + upload one fetched batch (best-effort; never raises). A failure, or
    capture enabled without the Drive folder/creds, sets _CAPTURE_FAILED."""
    global _CAPTURE_FAILED
    cap = cfg_gfl.get("capture") or {}
    if cap.get("enabled") and not ac.is_configured(_CAPTURE_FOLDER_ENV):
        _CAPTURE_FAILED = True
    try:
        captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = select_capture_rows(
            readings, thresholds, sentinels, watch_thresholds,
            float(cap.get("baseline_hours", 8)), prefix, mode=capture_mode(cfg_gfl))
        n = _write_capture(cfg_gfl, rows, captured_at)
        if n:
            print(f"[gfl-air]   durable capture: {n} reading(s) -> Drive.")
        return n
    except Exception as ce:  # noqa: BLE001 — durable capture is best-effort, but LOUD
        _CAPTURE_FAILED = True
        print(f"[gfl-air]   durable capture FAILED ({len(readings)} reading(s) not saved): {ce}")
        return 0


def _capture_filename(rows: list[dict], when_utc: str, suffix: str = "") -> str:
    """Immutable per-poll name: run date + max OBJECTID in the batch, so a re-run
    of the same batch dedups (upload_file reuses by name) and files sort by date.
    `suffix` keeps the hourly job's files distinct from the daily run's ("-h")."""
    max_oid = max((r.get("oid") or 0) for r in rows) if rows else 0
    return f"gfl-air-capture-{when_utc[:10]}-oid{max_oid}{suffix}.json"


def _write_capture(cfg_gfl: dict, rows: list[dict], when_utc: str, suffix: str = "") -> int:
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
        ac.upload_file(drive, tmp, _capture_filename(rows, when_utc, suffix),
                       "application/json", ac.folder_id(_CAPTURE_FOLDER_ENV))
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return len(rows)


def main() -> int:
    """Process exit code: run()'s, or 1 when the durable capture had a gap this run
    (failed/unconfigured upload, over-cap skip, or the core-field fallback)."""
    global _CAPTURE_FAILED
    _CAPTURE_FAILED = False
    gc.FIELDS_FALLBACK_USED = False
    rc = run()
    if gc.FIELDS_FALLBACK_USED:
        print("[gfl-air] the source rejected the extended field list (its schema changed); "
              "readings were fetched with the core fields only this run.")
        try:
            capture_on = bool(((load_config().get("gfl_air") or {}).get("capture") or {}).get("enabled"))
        except Exception:  # noqa: BLE001 — unreadable config: stay loud
            capture_on = True
        if capture_on:
            _CAPTURE_FAILED = True
    if not rc and _CAPTURE_FAILED:
        print("[gfl-air] exiting 1: the durable capture did not fully save this run's readings.")
        rc = 1
    return rc


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
    # The configured station ROSTER (what should exist) — resolved + warned about on
    # EVERY run, not just when a screening email fires, so a missing/typo'd roster
    # is visible in a quiet run's log too. None => legacy fallback count (logged).
    roster, roster_warnings = station_roster(cfg_gfl, prefix)
    for _w in roster_warnings:
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
        # of blasting the case file (WDS Rule B(ii)). The fetched batch (the oldest
        # cap+1 rows only) is durably captured first (ADR 026 addendum 2026-10-06),
        # but any readings beyond it are skipped by the re-baseline, so with capture
        # on this run is flagged as a capture gap (exit 1); the monthly full-feed
        # snapshot is the backstop that recovers them.
        _capture_batch(cfg_gfl, readings, thresholds, sentinels, watch_thresholds, prefix)
        if (cfg_gfl.get("capture") or {}).get("enabled"):
            global _CAPTURE_FAILED
            _CAPTURE_FAILED = True
            print("[gfl-air]   over-cap re-baseline: readings past this batch were not "
                  "captured (the monthly full snapshot recovers them)")
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
    # Readings-side roster reconciliation on EVERY poll (free: no extra fetch), so a
    # new monitor coming online, or a roster station gone quiet, is in the run log
    # even on a quiet perimeter where no screening email fires for months. The
    # registry (layer-0) side is reconciled on every run that fetches it (an episode
    # opened or closed), inside _run_action_level_episodes.
    unexpected, no_reading = roster_mismatch(roster, latest_per_station(readings, prefix).keys())
    if unexpected:
        print("[gfl-air]   ROSTER MISMATCH: reading(s) this poll from station(s) not in "
              f"gfl_air.expected_stations: {', '.join(unexpected)}")
    if no_reading:
        print(f"[gfl-air]   roster station(s) with no reading this poll: "
              f"{', '.join(no_reading)}")

    # Durable air-readings exhibit (ADR 026) — immutable Drive capture of the
    # selected readings (capture.mode "all" = every reading; "sample" = every elevated
    # hour + baseline downsample). Best-effort
    # and gated OFF until the folder/secret exist, so it can't affect the live
    # stream; it never touches measurements, the cursor, or the alert path.
    _capture_batch(cfg_gfl, readings, thresholds, sentinels, watch_thresholds, prefix)

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
            link, watch_recipients, cfg_gfl, roster)

    return 0


def _open_episode_from_state(key: str, ep: dict) -> dict:
    """A state-map entry (keyed 'station|gas') as a flat dict for the email helpers."""
    station, _, gas = key.partition("|")
    return {"station": station, "gas": gas, **ep}


def _run_action_level_episodes(sheets, sheet_id, cfg, readings, watch_thresholds,
                               sentinels, prefix, link, watch_recipients, cfg_gfl,
                               roster: list[str] | None = None) -> None:
    """Advance the per-(station,gas) episode state on this poll's readings, write the
    durable episode-log ROW before the state entry (repo ordering), and send AT MOST
    one consolidated SCREENING (open) email and one CLOSEOUT (close) email. Coords +
    ±2h context are best-effort; a failure there degrades the email but never blocks
    it. Emails are best-effort (like the exceedance email). `roster` is the configured
    expected-station list from station_roster() (None = legacy fallback count, already
    warned about by run()); it drives the SCREENING email's N/<len> count and the
    roster-vs-registry reconciliation logged below.

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
    registry_available = True
    try:
        coords = gc.fetch_station_coords(cfg_gfl, station_prefix=prefix)
        registry_available = bool(coords)      # an empty station list is no list
    except Exception as e:  # noqa: BLE001
        registry_available = False
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

    # Roster vs. what was actually seen (readings ∪ registry) — reconciled and logged
    # loudly on EVERY run that fetched the registry (open or close), and named in
    # the screening email below; never absorbed into the count. Placed AFTER the
    # durable row + state writes above, like every other email-only step, so it can
    # never sit between a close and its log row.
    latest = latest_per_station(readings, prefix)
    monitor_rows = _monitor_rows(latest, watch_thresholds, sentinels, registry=coords)
    unexpected, missing = roster_mismatch(roster, (r["station"] for r in monitor_rows))
    if unexpected:
        print("[gfl-air]   ROSTER MISMATCH: station(s) seen in the source but not in "
              f"gfl_air.expected_stations: {', '.join(unexpected)} (listed in the "
              "screening email, not counted toward the expected total)")
    if missing:
        why = ("no reading this run, and the station list could not be retrieved"
               if not registry_available
               else "neither a reading this run nor a station-list entry")
        print("[gfl-air]   ROSTER MISMATCH: roster station(s) unaccounted for this "
              f"run: {', '.join(missing)} ({why})")

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
        subject, body = format_screening_email(
            result.opened, continuing, monitor_rows, watch_thresholds, link=link,
            retrieved_iso=retrieved_iso, coords=coords, context=context, historical=hist,
            expected_stations=roster, registry_available=registry_available)
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
    sys.exit(main())
