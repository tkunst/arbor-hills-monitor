"""
woi_router.py — route WOI Status Reports to the exhaustive woi_table_parser,
ABOVE egle_doc_parser.parse_document. The Decode base stays domain-agnostic: it
never learns "Gas Extraction Report" or WOI; that knowledge lives here, at the
call sites (watcher.py / backfill.py). See docs/decisions/005.

WHY (ADR 005 "Integration"): the semi-annual WOI (Wells of Interest) Status
Reports are 180-320pp per-well gas-extraction tables — the densest R8
(overheating / ETLF) evidence. The generic path keyword-windows any doc over the
large-doc threshold (cover + ~10 keyword-matched pages of 181), so <5% of the
~14,000 readings are captured AND a real measured temperature on a page with no
signal keyword is never seen by the classifier — no measurement is emitted for
it. That is not just an archive gap: email_alerts.is_urgent fires the same-day
urgent alert off a MEASURED temperature >= measured_temp_urgent_f found in
parsed.measurements, so a 150F well buried on page 140 produces no measurement
and NO urgent alert ever fires. This module detects a WOI report and REPLACES the
model's windowed measurements with woi_table_parser's exhaustive, validated set,
BEFORE is_urgent / write_document.

DETECTION IS CONTENT-BASED, NOT NAME-BASED. The real reports are filed in nSITE
under generic names ("nForm Document" / "Site") — verified 2026-07-13 against the
live 181pp report (doc_id 7022559137978826651), whose nSITE type_name is
"nForm Document". So a filename signal would never fire and would silently reopen
the gap. The reliable signals are the Attachment-1 "Gas Extraction Report" header
plus a large page count. The valid-reading-count guard in route_measurements() is
the confirming multi-signal: it makes an over-trigger data-loss-safe (a non-WOI
doc yields ~0 valid gas rows -> we do NOT replace its measurements) and doubles as
the EGLE-format-drift alarm. (This is a deliberate, evidence-backed deviation from
the handoff's pinned "nSITE name looks like WOI" signal; see the PR / ADR 005.)
"""
from __future__ import annotations

import re
from typing import Optional

import fitz  # pymupdf

import woi_table_parser as wtp

# Attachment-1 header ("Gas Extraction Report"); tolerate whitespace/case drift.
_HEADER_RE = re.compile(r"gas\s+extraction\s+report", re.I)
# How many front pages to scan for the header. It's on ~p8 of the real reports;
# bounding the scan keeps detection cheap on a large NON-WOI doc (which would
# otherwise have its full text extracted looking for a header that isn't there).
_HEADER_SCAN_PAGES = 25
# Minimum VALID gas-extraction readings for a detected doc to actually route. A
# real WOI report has ~14,000; a misdetected narrative doc that merely mentions
# the phrase yields ~0. Well below any real report, well above any false positive.
# This is the data-loss guard (never replace real measurements with ~nothing) AND
# the format-drift alarm (a detected report that suddenly parses to near-zero).
_MIN_VALID_READINGS = 50
# Watch band: emit as measurements only the non-ADJ readings at/above this (deg F)
# — the EPA gas-well operating limit and up — so the Measurements tab and
# is_urgent get the alert- and watch-band evidence at ~hundreds/low-thousands of
# rows, not the full ~14k dump (which stays reproducible via scripts/woi_summary.py).
_DEFAULT_WATCH_F = 131.0


def _auto_route_enabled(cfg: dict) -> bool:
    """The woi.auto_route kill-switch. Defaults ON (this is a live-path fix); it's
    a rollback lever, not a new-source enable gate — flip it off and the monitor
    falls back to keyword-windowing."""
    return bool((cfg.get("woi") or {}).get("auto_route", True))


def _watch_f(cfg: dict) -> float:
    """The watch-band threshold (deg F). 131 lives in two config spots (see
    business-rules Note 7); prefer the urgent.measured_temp_watch_f semantic
    ("EPA gas-well operating limit and up")."""
    return float((cfg.get("urgent") or {}).get("measured_temp_watch_f", _DEFAULT_WATCH_F))


def is_woi_report(pdf_path: str, metadata: dict, page_threshold: int = 30) -> bool:
    """True iff this looks like a WOI Status Report: over the large-doc page
    threshold AND carrying the Attachment-1 "Gas Extraction Report" header in its
    front pages. Deliberately NOT gated on the nSITE name — the real reports are
    filed as "nForm Document"/"Site". Conservative via the specific header; made
    safe against a false positive by the valid-reading guard in
    route_measurements(). Logs the decision so an under-trigger (a silently
    reopened gap) is diagnosable from the run log."""
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:  # noqa: BLE001 — an unreadable PDF is simply not routable
        print(f"[woi_router] not routing (PDF open failed): {e}")
        return False
    try:
        n = len(doc)
        if n <= page_threshold:
            return False
        for p in range(min(n, _HEADER_SCAN_PAGES)):
            if _HEADER_RE.search(doc[p].get_text()):
                print(f"[woi_router] WOI-shaped: {n}pp, 'Gas Extraction Report' on "
                      f"p{p + 1} — {metadata.get('document_name')!r} "
                      f"(nSITE type={metadata.get('type_name')!r})")
                return True
        return False
    finally:
        doc.close()


def route_measurements(parsed, pdf_path: str, metadata: dict, cfg: dict) -> Optional[dict]:
    """If woi.auto_route is on and this is a WOI report, exhaustively parse it and
    REPLACE parsed.measurements with the non-ADJ, valid, >= watch-band readings —
    plus the single peak as-found reading, always — then return a summary payload
    (per_well_summary + counts) for the caller to write to the WOI Summary tab.
    Returns None when not routed (auto_route off, not WOI-shaped, or the guard
    below trips), leaving parsed.measurements untouched.

    Does NO Sheets I/O: the in-memory measurement replacement that feeds
    is_urgent must not be blockable by a Sheets failure. The caller writes the
    summary tab separately, best-effort.

    Two safety rules from the plan's adversarial review:

    * DATA-LOSS GUARD. If the exhaustive parse yields fewer than
      _MIN_VALID_READINGS valid readings, do NOT replace (keep the generic
      measurements) and log loudly. A detected-but-near-empty parse means either
      a false-positive detection or an EGLE format change; silently replacing
      real measurements with ~nothing would be data loss. This is what makes an
      over-trigger harmless, and it is the format-drift alarm.

    * PEAK-TEMP PRESERVATION. Always keep at least the peak as-found temperature
      in the emitted set. is_urgent decides from parsed.measurements when ANY
      temperature measurement is present, and only regexes free text when there
      is none. On an all-cool report (no well >= watch band) an unguarded >=131
      trim would emit zero temperatures, sending is_urgent to its free-text
      fallback, which would match the HOV permitted ceilings (155/180F) printed
      on the windowed cover pages and FALSE-FIRE an urgent alert off a permitted
      limit — the exact measured-vs-permitted conflation ADR 004 forbids. Keeping
      the peak guarantees >= 1 measured temperature, so urgency is always decided
      from structured data (correctly False when the peak is < the MACT limit).
    """
    if not _auto_route_enabled(cfg):
        return None
    page_threshold = cfg.get("large_doc_page_threshold", 30)
    if not is_woi_report(pdf_path, metadata, page_threshold):
        return None

    readings = wtp.parse_gas_extraction(pdf_path)
    valid = [r for r in readings if r.valid]
    if len(valid) < _MIN_VALID_READINGS:
        print(f"[woi_router] WARNING: detected a WOI report but only {len(valid)} "
              f"valid readings (< {_MIN_VALID_READINGS}) — NOT replacing "
              f"measurements (possible false-positive detection or EGLE format "
              f"drift): {metadata.get('document_name')!r}")
        return None

    watch_f = _watch_f(cfg)
    asfound = [r for r in valid if not r.adj and r.temp is not None]
    measurements: list[dict] = []
    for r in asfound:
        if r.temp >= watch_f:
            measurements.extend(wtp.to_measurements(r))
    # Peak-temp preservation (see docstring). On a hot report the peak is already
    # in the >= watch_f set; on an all-cool report this adds the one peak row so
    # is_urgent never reaches its permitted-ceiling-matching free-text fallback.
    peak = max(asfound, key=lambda r: r.temp) if asfound else None
    if peak is not None and peak.temp < watch_f:
        measurements.extend(wtp.to_measurements(peak))

    if not measurements:
        # Degenerate: detected + enough valid readings, but no as-found
        # temperature to anchor is_urgent. Keep the generic set rather than
        # emit an empty one that would re-expose the free-text fallback.
        print(f"[woi_router] WARNING: WOI report with no as-found temperature "
              f"reading — NOT replacing measurements: {metadata.get('document_name')!r}")
        return None

    woi_set = wtp.extract_woi_well_list(pdf_path)
    summary = wtp.per_well_summary(valid, woi_set=woi_set)
    parsed.measurements = measurements

    validity = 100.0 * len(valid) / max(1, len(readings))
    n_hot = sum(1 for r in asfound if r.temp >= watch_f)
    n_hot_wells = sum(1 for d in summary if (d["max_temp_f"] or 0) >= watch_f)
    print(f"[woi_router] ROUTED {metadata.get('document_name')!r}: {len(readings)} "
          f"readings ({validity:.1f}% valid), {len(summary)} wells "
          f"({len(woi_set)} on the WOI list), {n_hot} as-found readings "
          f">= {watch_f:.0f}F -> {len(measurements)} measurements "
          f"({n_hot_wells} wells >= {watch_f:.0f}F). Replaced the windowed set.")
    return {
        "summary": summary,
        "woi_set": woi_set,
        "n_readings": len(readings),
        "n_valid": len(valid),
        "validity_pct": validity,
        "n_measurements": len(measurements),
    }
