"""
ridgewood_archiver.py — Stream G: mirror + extract Barr Engineering's monthly
Ridge Wood Elementary H2S data reports. See docs/decisions/016-ridgewood-h2s-stream-g.md.

Modeled on mmpc_archiver.py (Mirror D, ADR 010): a standalone, config-gated poller
that mirrors newly-published PDFs from a public listing, deduped by a Sheet-derived
id set — here the id is the report's `YYYY-MM` month. On top of the mirror it does
what Mirror D doesn't: EXTRACT the month's 24-hr-average H2S value into the shared
Measurements tab (basis=measured, honestly attributed to the Barr/EPA-agreement
monitor — not GFL self-report, not an EGLE measurement), and ALERT when a report
states a 24-hr exceedance of 72 ppb or a 15-minute exceedance of 750 ppb (the
monitor's own published action levels, which also confirm Stream E's H2S threshold).

GATED on ridgewood.enabled (false by default): a brand-new poller against a live
external system ships disabled and a human flips it on — this loop never does
(overnight-coder procedure). Until enabled: true is on main, every run is a quiet
no-op. Runs from its own workflow (.github/workflows/ridgewood.yml, own concurrency
group), so it never races the other jobs on shared state (it writes only its own tab
+ append-only Measurements rows, never _meta).

DRIVE MIRROR IS DECOUPLED FROM THE SAFETY FUNCTION (a deliberate divergence from
mmpc_archiver, which no-ops entirely when its OAuth folder isn't configured). The
exceedance alert is this stream's safety-critical job and must NOT depend on an
optional Drive folder secret being present:
  - Drive folder configured + OAuth healthy -> mirror each PDF, record the link.
  - Drive folder configured but the OAuth token is dead -> fail LOUDLY (exit 1), so
    the mirror doesn't fall behind invisibly (mmpc_archiver's posture).
  - Drive folder NOT configured -> skip the mirror (blank link), but STILL extract
    + alert. Enabling the stream without the folder secret is a valid, safe state.

CRASH-SAFE WRITE ORDER, per new month (three durable writes, not two):
  1. Drive upload (durable PDF copy)         -- idempotent re-upload on re-run
  2. Measurements row(s) (the extracted value, the system of record)
  3. Ridge Wood Reports row (the dedup 'done' marker) -- written LAST
  4. alert email (best-effort, last)
A crash between (1) and (3) re-processes the month next run: an idempotent Drive
re-upload plus AT MOST a duplicate monthly measurement — never a dropped month. At
one row per month that duplicate is negligible; it's an accepted residual. A crash
between (3) and (4) loses that month's real-time email but keeps the durable record
(Measurements + Reports tab + mirrored PDF), and never re-fires — the same
best-effort-alert posture as pfas_watcher / mmpc.

BACKFILL vs STEADY STATE: the first enabled run finds ~66 historical months. To
avoid both a workflow timeout and an alert flood, a run processes at most
max_new_reports_per_run months (newest first, resumable via the dedup tab), and when
a run is draining a backlog (> cap new months) it SUPPRESSES alerts — those months
are historical (a real-time email about a 2021 reading is noise) and their data is
recorded regardless. Steady-state runs (one new month) alert normally.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import archive_client as ac
import ridgewood_client as rc
import email_alerts as ea
from config_loader import load_config

FOLDER_ENV = "GOAUTH_RIDGEWOOD_FOLDER_ID"

_FACILITY = "Arbor Hills Landfill"
_WELL_ID = "Ridge Wood Elementary School"
_MEASURE_METRIC = "hydrogen_sulfide"
_MEASURE_UNIT = "ppb"
_DEFAULT_CAP = 12


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/Drive/network mocking, so the exact
    bug this guards (the poller doing real work / emailing before ridgewood.enabled
    is set) has a direct unit test. Mirrors mmpc_archiver / gfl_air_watcher. Note we
    gate ONLY on `enabled` here, NOT on OAuth being configured — the Drive mirror is
    decoupled from the extract+alert safety function (see module docstring)."""
    if not (cfg.get("ridgewood") or {}).get("enabled"):
        return False, "ridgewood.enabled is false — skipping (no-op)."
    return True, ""


# ---------------------------------------------------------------------------
# Pure helpers (no network / no Sheets) — unit-tested directly
# ---------------------------------------------------------------------------

def build_measurement(month: str, verdict: dict) -> dict | None:
    """One Measurements-tab reading dict for the month's MAXIMUM daily 24-hr average
    (the decision-relevant, exceedance-determining number), or None when nothing
    parsed (a scanned/format-changed report — the archiver still mirrors it and
    alerts, but writes no fabricated measurement). Value is stored verbatim as the
    report presents it ("<1" below resolution, else the numeric max); the qualifier
    lives in Note, and exceedance logic runs on numerics only, so "<1" never touches
    the >=72 check. Monthly granularity per the handoff (as_of_date = report month);
    the daily detail lives in the mirrored PDF, not ~2000 rows of "<1"."""
    if not verdict.get("value_token"):
        return None
    n_days = verdict.get("n_days", 0)
    note = (
        f"Barr Engineering / U.S. EPA-agreement H2S monitor at Ridge Wood Elementary "
        f"School; monthly max of {n_days} daily 24-hr average(s); QA'd monthly report "
        f"— not GFL self-report, not an EGLE measurement.")
    if verdict.get("all_days_below_1"):
        note += " All days < 1 ppb (below the 1 ppb reporting resolution)."
    return {
        "as_of_date": f"{month}-01",   # first of the report month (monthly summary)
        "well_id": _WELL_ID,
        "metric": _MEASURE_METRIC,
        "value": verdict["value_token"],
        "unit": _MEASURE_UNIT,
        "basis": "measured",
        "note": note,
    }


def format_alert_body(month: str, verdict: dict, source_url: str, archive_link: str,
                      thresholds: dict) -> str:
    thr_24h = thresholds.get("h2s_24h_ppb", rc.DEFAULT_H2S_24H_PPB)
    thr_15m = thresholds.get("h2s_15min_ppb", rc.DEFAULT_H2S_15MIN_PPB)
    lines = [
        f"Ridge Wood Elementary School H2S monitoring — {month} report flagged for review.\n",
        "This is the Barr Engineering / U.S. EPA-agreement monitor at the school "
        "(monthly QA'd 24-hour-average H2S), NOT GFL self-report and NOT an EGLE "
        "measurement.\n",
        f"Published action levels: 24-hr average >= {thr_24h} ppb, "
        f"15-minute average >= {thr_15m} ppb.\n",
        "Why this alerted:",
    ]
    lines.extend("  - " + r for r in verdict.get("reasons", []))
    lines.append(f"\nSource report:\n  {source_url}")
    if archive_link:
        lines.append(f"Mirrored copy (Drive):\n  {archive_link}")
    return "\n".join(lines)


def _alert_subject(month: str, verdict: dict) -> str:
    tag = "URGENT" if verdict.get("exceed_24h") else "Ridge Wood H2S review"
    return f"[{tag}] Arbor Hills — Ridge Wood Elementary H2S report ({month})"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _measure_metadata(title: str) -> dict:
    return {"date_filed": _today(), "document_name": title, "facility_name": _FACILITY}


def _resolve_drive(cfg_present: bool):
    """(drive_service_or_None, reason). Returns the OAuth Drive service when the
    folder secret is configured AND the token is healthy. Raises to the caller (loud
    exit 1) when configured-but-dead; returns (None, reason) when not configured
    (mirror skipped, extract+alert continues)."""
    if not cfg_present:
        return None, ("Drive folder secret not set — mirroring disabled, extracting + "
                      "alerting only (safe). Set GOAUTH_RIDGEWOOD_FOLDER_ID to mirror.")
    return ac.oauth_drive_service(), ""


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[ridgewood] {reason}")
        return 0

    rw = cfg.get("ridgewood") or {}
    page_url = rw.get("page_url", rc.DEFAULT_PAGE_URL)
    base_url = rw.get("base_url", rc.DEFAULT_BASE_URL)
    thresholds = rw.get("thresholds") or {}
    cap = int(rw.get("max_new_reports_per_run", _DEFAULT_CAP))
    sheet_id = os.environ["GSHEET_ID"]

    sheets = dc.sheets_service()
    sw.ensure_tabs(sheets, sheet_id)          # the shared Measurements tab must exist
    sw.ensure_ridgewood_tabs(sheets, sheet_id)

    # Drive mirror is OPTIONAL and decoupled — resolve it before the loop. Configured
    # but dead -> loud exit 1 (mmpc posture). Not configured -> skip mirror, continue.
    try:
        drive, drive_reason = _resolve_drive(ac.is_configured(FOLDER_ENV))
    except Exception as e:  # noqa: BLE001 — configured but the OAuth token is dead
        print(f"[ridgewood] OAuth Drive auth FAILED ({e}). Re-run scripts/oauth_setup.py "
              f"and update GOAUTH_REFRESH_TOKEN.")
        return 1
    if drive is None:
        print(f"[ridgewood] {drive_reason}")

    # Scrape the report list. A failed fetch aborts THIS run (never diff a partial
    # list as if reports were removed) — rc raises RidgewoodFetchError.
    try:
        html_text = rc.fetch_page(page_url)
    except rc.RidgewoodFetchError as e:
        print(f"[ridgewood] page fetch failed, aborting this run: {e}")
        return 1
    reports, unparsed = rc.scrape_report_links(html_text, base_url)
    if unparsed:
        # Expected: 1 (the old-format Dec-2020 duplicate). A jump (e.g. every link
        # going unparsed) means a page restructure — visible here, not silent.
        print(f"[ridgewood] note: {len(unparsed)} Files/*.pdf link(s) had no YYYY-MM "
              f"month and were skipped (expected: the old-format Dec-2020 duplicate).")
    if not reports:
        print("[ridgewood] no report links scraped — treating as a bad fetch, aborting.")
        return 1

    already = sw.ridgewood_archived_months(sheets, sheet_id)
    new_reports = rc.iter_new_reports(reports, already)     # newest first
    print(f"[ridgewood] {len(reports)} month(s) listed, {len(already)} already "
          f"archived, {len(new_reports)} new.")
    if not new_reports:
        print("[ridgewood] nothing new — up to date.")
        return 0

    backlog = len(new_reports) > cap
    todo = new_reports[:cap]
    if backlog:
        print(f"[ridgewood] draining a backlog ({len(new_reports)} new > cap {cap}); "
              f"processing {len(todo)} this run, alerts SUPPRESSED (historical backfill).")

    tmp = tempfile.gettempdir()
    processed = 0
    for r in todo:
        month = r["month"]
        source_url = r["url"]
        local = os.path.join(tmp, f"ridgewood-{month}.pdf")
        try:
            rc.download_report(source_url, local)
            chash = rc.content_hash(local)
            text, npages, has_text = rc.extract_text(local)

            # (1) Drive mirror (optional, idempotent), then classify + extract.
            archive_link = ""
            if drive is not None:
                archive_link = ac.upload_pdf(
                    drive, local, f"ridgewood-h2s-{month}.pdf", ac.folder_id(FOLDER_ENV))

            if has_text:
                verdict = rc.classify_report(text, thresholds)
            else:
                # Scanned / no text layer — mirror it, write no measurement, alert for
                # OCR/manual review (fail-safe: the month is archived, never dropped).
                verdict = {
                    "n_days": 0, "max_numeric": None, "value_token": "",
                    "all_days_below_1": False, "all_clear": False, "exceed_24h": False,
                    "parse_anomaly": True, "alert": True, "severity": "alert",
                    "reasons": [f"report PDF has no text layer ({npages} page(s)) — "
                                f"likely scanned; mirror archived, review/OCR manually"],
                }

            title = rc.report_title(month)

            # (2) Measurements (system of record) BEFORE the dedup row.
            meas = build_measurement(month, verdict)
            if meas is not None:
                parsed = SimpleNamespace(measurements=[meas])
                rows = sw.measurement_rows(parsed, _measure_metadata(title),
                                           archive_link or source_url)
                sw.append_rows(sheets, sheet_id, sw.TAB_MEASUREMENTS, rows)

            # (3) Ridge Wood Reports dedup row LAST (the 'done' marker).
            max_cell = verdict["value_token"] or "n/a"
            alert_cell = ("EXCEEDANCE" if verdict.get("exceed_24h")
                          else "review" if verdict.get("alert") else "ok")
            sw.append_ridgewood_report_row(
                sheets, sheet_id, month, title, max_cell, verdict["n_days"],
                alert_cell, source_url, chash, archive_link, _now())
            processed += 1
            print(f"  ok  {month}  max={max_cell:>4}  days={verdict['n_days']:>2}  "
                  f"{alert_cell}")

            # (4) Alert email (best-effort, last), unless draining a backlog.
            if verdict.get("alert") and not backlog:
                subject = _alert_subject(month, verdict)
                body = format_alert_body(month, verdict, source_url, archive_link, thresholds)
                try:
                    ea.send_email(subject, body, cfg)
                    print(f"      emailed: {subject}")
                except Exception as e:  # noqa: BLE001 — alert best-effort; record kept
                    print(f"      record written but alert email FAILED: {e}")
        except Exception as e:  # noqa: BLE001 — one month's failure must not abort the batch
            print(f"  ERR {month}: {e}")
        finally:
            if os.path.exists(local):
                os.remove(local)

    print(f"[ridgewood] processed {processed} of {len(todo)} new report(s) this run.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
