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

import review_flags as rf
import well_aliases
import woi_table_parser as wtp

# Attachment-1 header ("Gas Extraction Report"); tolerate whitespace/case drift.
_HEADER_RE = re.compile(r"gas\s+extraction\s+report", re.I)
# The header is searched on EVERY page (coder:woi-layout-guard, 2026-10-15).
# It used to be bounded to the first 25 pages to keep detection cheap, but the
# Q3 2023 Consent Judgment report carries its gas table from p163 and was never
# detected. A full scan is cheap (a 1,045-page report reads in ~3 s), and the
# file the watcher/backfill hands in has already been through parse_document's
# OCR step, so a scanned header page is searched as text too.
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
# The metrics route_measurements() emits from the gas and CO tables; only
# these replace the classifier's own (windowed) measurements.
_ROUTED_METRICS = {"temperature", "oxygen", "methane", "carbon_monoxide"}


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
    threshold AND carrying the Attachment-1 "Gas Extraction Report" header on
    any page. Deliberately NOT gated on the nSITE name — the real reports are
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
        for p in range(n):
            try:
                text = doc[p].get_text()
            except Exception:  # noqa: BLE001 — one damaged page; keep scanning
                continue
            if _HEADER_RE.search(text):
                print(f"[woi_router] WOI-shaped: {n}pp, 'Gas Extraction Report' on "
                      f"p{p + 1} — {metadata.get('document_name')!r} "
                      f"(nSITE type={metadata.get('type_name')!r})")
                return True
        return False
    finally:
        doc.close()


def _max_plausible_f(cfg: dict) -> float:
    return float((cfg.get("temperature_thresholds") or {}).get("max_plausible_wellhead_f", 250))


def route_measurements(parsed, pdf_path: str, metadata: dict, cfg: dict,
                       alias_map: Optional[dict] = None,
                       review_flags: Optional[list] = None,
                       registry=None) -> Optional[dict]:
    """If woi.auto_route is on and this is a WOI report, exhaustively parse it and
    REPLACE parsed.measurements with the non-ADJ, valid, >= watch-band readings —
    plus the single peak as-found reading, always — then return a summary payload
    (per_well_summary + counts) for the caller to write to the WOI Summary tab.
    Returns None when not routed (auto_route off, not WOI-shaped, or the guard
    below trips), leaving parsed.measurements untouched.

    Does NO Sheets I/O: the in-memory measurement replacement that feeds
    is_urgent must not be blockable by a Sheets failure. The caller writes the
    summary tab separately, best-effort.

    `alias_map` (ADR 068): the curated well-ID alias map, loaded ONCE at run
    start by the caller (well_aliases.load_alias_map) and threaded into every
    woi_table_parser call here, so a pre-rename spelling (AHWW0290) lands under
    its canonical id (AHWW290R) in the measurements, the WOI list, the per-well
    summary, the CO series and well_watch. None = no aliasing.

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

    And from coder:woi-layout-guard (2026-10-15):

    * PER-TABLE LAYOUT. woi_table_parser reads each gas table's own header to
      learn its column order, rejects a table it can't place (unknown layout,
      or the negative-flow column-shift signature), and skips unreadable
      pages instead of failing the whole report.

    * PLAUSIBILITY CEILING. A valid reading whose temperature is above
      temperature_thresholds.max_plausible_wellhead_f (250F, ADR 067) is
      removed BEFORE every alerting path: the emitted measurements, the
      per-well summary, the peak anchor and the readings handed to
      well_watch. It is never an URGENT on its own (usually a flare or a
      misread column), but it is no longer dropped silently either.

    `review_flags`, if a list is passed, receives one review_flags.flag()
    dict per above-ceiling reading, rejected table and unreadable-page run,
    whether or not the report ends up routed. The caller logs them.
    """
    if not _auto_route_enabled(cfg):
        return None
    page_threshold = cfg.get("large_doc_page_threshold", 30)
    if not is_woi_report(pdf_path, metadata, page_threshold):
        return None

    gp = wtp.parse_gas_extraction_report(pdf_path, alias_map=alias_map)
    readings = gp.readings
    max_f = _max_plausible_f(cfg)
    # A reading printed in two tables of the same report reaches Measurements,
    # the summary and well_watch once (woi_table_parser.merge_cross_table_duplicates).
    valid_raw = [r for r in readings if r.valid]
    valid_all = wtp.merge_cross_table_duplicates(valid_raw)
    n_merged = len(valid_raw) - len(valid_all)  # validity_pct stays a pre-merge ratio
    above = [r for r in valid_all if r.temp is not None and r.temp > max_f]
    valid = [r for r in valid_all if not (r.temp is not None and r.temp > max_f)]
    if above:
        print(f"[woi_router] {len(above)} valid reading(s) above the {max_f:g}F "
              f"plausibility ceiling held out of alerting and the summary "
              f"(logged for review)")
    if review_flags is not None:
        review_flags.extend(rf.woi_flags(above, gp.rejected_tables, gp.skipped_pages, max_f))
    # The guard counts readings WITH a temperature: gas-only rows (Temp
    # printed "---", ADR 005 addendum 2026-10-19) must not let a report whose
    # temperatures don't parse replace the classifier's measurements.
    n_with_temp = sum(1 for r in valid if r.temp is not None)
    if n_with_temp < _MIN_VALID_READINGS:
        print(f"[woi_router] WARNING: detected a WOI report but only {n_with_temp} "
              f"valid readings with a temperature (< {_MIN_VALID_READINGS}) — NOT replacing "
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
    # (The anchor is the hottest as-found reading of ANY point, probes
    # included, so the registry never changes what reaches Measurements; the
    # headline is the hottest WELL, see build_key_data_point.)
    probes = registry.probes if registry is not None else frozenset()
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

    # CO (Attachment 2) — carbon monoxide is a direct combustion product, so
    # rising CO is an early-warning signal for subsurface oxidation. It's a small,
    # bounded set (the WOI wells, monthly), so it is NOT temperature-gated: every
    # CO reading is emitted. Added AFTER the temperature-anchor guard above so CO
    # can never stand in for a missing temperature and re-expose the is_urgent
    # free-text fallback. (The old windowed path caught only a few CO readings by
    # luck; this captures the full series.) See ADR 005.
    # Above-ceiling readings: kept on record the way kdp_check keeps a
    # re-tagged classifier reading (basis "unknown" + a note), so they stay
    # visible in Measurements but can never reach is_urgent's measured path.
    # Added after the anchor guard above, so they can never stand in for it.
    for r in above:
        if r.adj:
            continue
        for m in wtp.to_measurements(r):
            if m["metric"] == "temperature":
                m["basis"] = "unknown"
                m["note"] = (f"[above the {max_f:g}F plausibility ceiling; held out of "
                             "alerts by woi_router, logged for review]")
                measurements.append(m)

    co_measurements: list[dict] = []
    for cr in wtp.parse_co_data(pdf_path, alias_map=alias_map):
        co_measurements.extend(wtp.co_to_measurements(cr))
    measurements.extend(co_measurements)

    woi_set = wtp.extract_woi_well_list(pdf_path, alias_map=alias_map)
    # Unmapped ids that look like an unresolved rename: logged for QA, never
    # auto-mapped (ADR 068 _needs_canonical_review).
    well_aliases.log_review_candidates(
        [r.raw_well_id for r in readings], alias_map,
        context=f"WOI report {metadata.get('doc_id', '?')}",
        ruled=registry.ruled_ids if registry is not None else None)
    summary = wtp.per_well_summary(valid, woi_set=woi_set,
                                   probe_ids=set(probes) if registry is not None else None)
    n_probes = sum(1 for d in summary if d.get("point_type") == "probe")
    # Replace only the metrics this router re-derives exhaustively from the
    # tables; any other classifier measurement (H2S, a permitted limit, ...)
    # is kept. With the header now searched on every page, large compilations
    # (e.g. a Consent Judgment quarterly report) route too, and their
    # narrative measurements must not be lost from the record.
    kept = [m for m in (getattr(parsed, "measurements", None) or [])
            if not (isinstance(m, dict) and m.get("metric") in _ROUTED_METRICS)]
    parsed.measurements = kept + measurements

    validity = 100.0 * (len(valid) + n_merged) / max(1, len(readings))
    n_hot = sum(1 for r in asfound if r.temp >= watch_f)
    n_hot_wells = sum(1 for d in summary if (d["max_temp_f"] or 0) >= watch_f
                      and d.get("point_type") != "probe")
    print(f"[woi_router] ROUTED {metadata.get('document_name')!r}: {len(readings)} "
          f"readings ({validity:.1f}% valid), "
          + (f"{len(summary) - n_probes} wells + {n_probes} probes "
             if registry is not None else f"{len(summary)} points (probes not known) ")
          + f"({len(woi_set)} on the WOI list), {n_hot} as-found readings "
          f">= {watch_f:.0f}F -> {len(measurements)} measurements "
          f"({len(co_measurements)} of them CO; {n_hot_wells} wells "
          f">= {watch_f:.0f}F). Replaced the windowed set.")
    return {
        "summary": summary,
        "woi_set": woi_set,
        "n_readings": len(readings),
        "n_valid": len(valid),
        "validity_pct": validity,
        "n_measurements": len(measurements),
        "n_co": len(co_measurements),
        # Every valid reading (as-found AND ADJ), probes included: the
        # headline / summary checks compare text against all of them.
        "readings": valid,
        # The same without GFL-labeled probes, for well_watch (ADR 058), which
        # needs single-well rows below the watch band trim and the ADJ rows.
        "well_readings": [r for r in valid if r.well_id not in probes],
        "n_probes": n_probes,
    }


# ---------------------------------------------------------------------------
# Deterministic headline (coder:keydatapoint-guard, 2026-10-17)
# ---------------------------------------------------------------------------
#
# route_measurements() replaces parsed.measurements but used to leave the
# classifier's key_data_point/summary alone. Those are the model's read of a
# WINDOWED sample (cover + keyword pages), so on 2026-10-08 a Depth to Bottom
# sounding (202, in feet) on a page seen without its column header reached the
# [URGENT] email and the public Sheet row as "202F". kdp_check (ADR 067) runs
# earlier, inside parse_document, and could not catch it: 202 is under the 250F
# plausibility ceiling. This is the second layer. For a ROUTED report only, the
# headline is rebuilt from the exhaustive table parse, and a summary stating a
# wellhead-range temperature above every reading the parse found is withheld.

# Shown in place of a routed report's summary when it fails the check below.
# Wording: owner ruling 2026-10-17 (PR #160 checklist).
WITHHELD_WOI_SUMMARY = (
    "Automated summary withheld because it did not match this report's own "
    "temperature tables. See the key data point, taken from those tables, and "
    "the source document.")

# A summary value up to this many degrees over the routed peak still passes
# (the model rounding 158.6 up to 159, say).
_SUMMARY_SLACK_F = 1.0

# "202°F", "202 F", "202 degrees F(ahrenheit)", "202º F", "158.3°F". Thousands separators are read
# whole ("1,500°F" is 1500, a flare value above the ceiling).
_SUMMARY_TEMP_RE = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{2,4}(?:\.\d+)?)\s*(?:[°º˚]|deg(?:rees)?\.?\s*)?\s*F(?:ahrenheit)?\b",
    re.I)


def build_key_data_point(routed: dict, parsed=None, cfg: Optional[dict] = None) -> Optional[str]:
    """A plain-language headline from the hottest well in routed["summary"]
    (per_well_summary is sorted hottest-first, as-found readings only), e.g.
    "AHWW312R: max measured 158.3°F (2023-08-29, Gas Extraction Report)."
    None when the summary holds no temperature. Pure. `parsed`/`cfg` are
    accepted for call-site symmetry and unused."""
    rows = [d for d in ((routed or {}).get("summary") or [])
            if d.get("point_type") != "probe"]  # wells only (ADR 068 addendum 2026-10-22)
    if not rows or rows[0].get("max_temp_f") is None:
        return None
    top = rows[0]
    when = wtp._to_iso(top.get("max_temp_date") or "")
    when = f"{when}, " if when else ""
    return (f"{top['well']}: max measured {top['max_temp_f']:g}°F "
            f"({when}Gas Extraction Report).")


def _reference_values_f(cfg: dict) -> set:
    """Regulatory/HOV numbers a summary may name without them being readings."""
    tt = cfg.get("temperature_thresholds") or {}
    urgent = cfg.get("urgent") or {}
    vals = {tt.get("epa_gas_operating_f", 131), tt.get("mact_f", 145),
            urgent.get("measured_temp_urgent_f", 145), urgent.get("measured_temp_watch_f", 131)}
    vals.update(tt.get("hov_values_f") or [])
    return {float(v) for v in vals if v is not None}


def text_contradicts_routed(text: str, routed: dict, cfg: dict,
                            exempt_reference_values: bool = True) -> list:
    """°F values in `text` (a summary or key data point) that no reading in
    the routed report supports:
    inside the wellhead band (watch_f .. max_plausible_wellhead_f), more than
    _SUMMARY_SLACK_F above the highest valid reading (as-found and ADJ), and,
    when `exempt_reference_values`, not a regulatory/HOV reference value (a
    summary naming "the 145°F MACT limit" is not claiming a reading). Pass
    False for the headline (owner ruling 2026-10-17): a headline citing 160°F
    with a 140°F table peak is replaced. Deliberately narrow otherwise: a
    rounded, averaged or prior-report figure at or below the peak passes. Pure."""
    readings = (routed or {}).get("readings") or []
    temps = [r.temp for r in readings if getattr(r, "temp", None) is not None]
    if not text or not temps:
        return []
    peak = max(temps)
    lo, hi = _watch_f(cfg), _max_plausible_f(cfg)
    refs = _reference_values_f(cfg) if exempt_reference_values else set()
    out = []
    for m in _SUMMARY_TEMP_RE.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if lo <= v <= hi and v > peak + _SUMMARY_SLACK_F and v not in refs and v not in out:
            out.append(v)
    return out


def apply_deterministic_headline(parsed, routed: dict, cfg: dict) -> None:
    """For a routed report: replace parsed.key_data_point with the table-
    derived headline when the model's states a temperature the tables don't
    support (or is empty; reference values like 145/HOV get no exemption in
    the headline), and withhold parsed.summary on the same test (where they
    do). A
    headline that isn't about temperature (a deadline or an enforcement action
    in a Consent Judgment compilation) is kept (owner ruling, 2026-10-17).
    Called by doc_pipeline right after route_measurements() and BEFORE the
    Sheet row is written, so the row, the alert, the digest and the _state
    payload all carry the corrected text."""
    current = getattr(parsed, "key_data_point", "") or ""
    bad_kdp = text_contradicts_routed(current, routed, cfg, exempt_reference_values=False)
    if bad_kdp or not current.strip():
        kdp = build_key_data_point(routed, parsed, cfg)
        if kdp:
            print(f"[woi_router] key data point replaced by the table-derived headline: {kdp}")
            parsed.key_data_point = kdp
    bad = text_contradicts_routed(getattr(parsed, "summary", "") or "", routed, cfg)
    if bad:
        print(f"[woi_router] summary withheld: states {', '.join(f'{v:g}F' for v in bad)}, "
              f"above every reading in the Gas Extraction tables")
        parsed.summary = WITHHELD_WOI_SUMMARY


def _peak_measured_f(parsed, max_f: float) -> Optional[float]:
    """Highest MEASURED temperature (deg F, at or below the plausibility
    ceiling) in parsed.measurements, unrounded, as the urgency floor reads it."""
    best = None
    for m in getattr(parsed, "measurements", None) or []:
        if not isinstance(m, dict) or m.get("metric") != "temperature" \
                or m.get("basis") != "measured":
            continue
        try:
            v = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if str(m.get("unit") or "F").strip().lower().startswith("c"):
            v = v * 9 / 5 + 32
        if v <= max_f:
            best = v if best is None else max(best, v)
    return best


def recheck_severity_floor(parsed, routed: dict, cfg: dict) -> None:
    """Undo parse_document's urgency floor when it fired ONLY on a measured
    reading the table parse has since replaced (owner ruling, 2026-10-17: the
    2026-10-08 shape, a fabricated 202F that floored severity to urgent).
    Severity goes back to what the floor replaced. Kept as-is when the model
    itself said urgent, when the floor also fired on a text/title signal, or
    when a table reading at/above the MACT value survives (is_urgent's
    measured branch then fires on that real reading anyway). Pure."""
    if getattr(parsed, "severity", None) != "urgent":
        return
    if list(getattr(parsed, "severity_floor", None) or []) != ["measured_temperature"]:
        return
    # Keep urgent if a surviving reading meets EITHER test: the floor's own
    # (unrounded, >= mact_f) or is_urgent's (rounded, >= measured_temp_urgent_f).
    # The two are one YAML anchor today; this keeps a real reading urgent if
    # they ever drift, and keeps the recorded severity in step with an alert
    # that fires on a value that only rounds up (144.6 -> 145).
    threshold = float((cfg.get("urgent") or {}).get("measured_temp_urgent_f", 145))
    mact = float((cfg.get("temperature_thresholds") or {}).get("mact_f", threshold))
    peak = _peak_measured_f(parsed, _max_plausible_f(cfg))
    if peak is not None and (peak >= mact or int(round(peak)) >= threshold):
        return
    before = getattr(parsed, "severity_before_floor", "") or "notable"
    print(f"[woi_router] severity {parsed.severity!r} -> {before!r}: the urgency floor "
          f"fired only on a measured reading the Gas Extraction tables don't hold "
          f"(table peak {peak if peak is not None else 'none'}F < {min(mact, threshold):g}F)")
    parsed.severity = before
