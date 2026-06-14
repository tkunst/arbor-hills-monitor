"""
woi_table_parser.py — table-aware extractor for EGLE WOI Status Reports.

WHY THIS EXISTS (see docs/decisions/005): the WOI Status Reports are ~180-320
page tabular monitoring filings — the densest R8 (overheating/ETLF) evidence.
The generic parser's keyword-windowing sends only ~11 pages to the model and
PyMuPDF `find_tables()` returns nothing, so <5% of the per-well readings would be
captured. This module instead parses ALL pages with a line state-machine over
the linearized text and validates each reading, then emits ADR-004
`measurements[]` directly.

Attachment 1 ("Gas Extraction Report") rows linearize as:
    Well ID
    Date/Time            (M/D/YYYY HH:MM  — the TIME distinguishes these from the
                          date-only CO rows in Attachment 2, which are skipped)
    [ADJ]                (optional: the post-valve-adjustment reading)
    CH4% CO2% O2% Balance% Diff.Press Temp Flow Well-Pres Header-Pres   (9 numbers)
    <comment lines, may themselves contain numbers like "581821: 0">

Validation gate: CH4 + CO2 + O2 + Balance ≈ 100. On the 2025 1st-semi-annual
report this passed 99% of 13,976 parsed readings.

IMPORTANT denominator note: Attachment 1 is the FULL wellfield (478 wells on the
2025 report), which is broader than the formally-designated Wells of Interest.
Use `extract_woi_well_list()` to tag which wells are on the WOI list rather than
implying every Attachment-1 well is a WOI. The asterisk markers in the report
(`*`/`**`/`***`/`****`) are footnote references whose meaning is defined by the
report's own legend and varies by attachment — do NOT infer WOI status from star
count; cross-reference the WOI list instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import fitz  # pymupdf

WELL_RE = re.compile(r"^AH[A-Z0-9]+\*{0,4}$")
DT_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}$")  # date AND time
DATE_ONLY_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

# Column order after Well ID / Date/Time / optional ADJ (header on report p8).
COLUMNS = ["ch4", "co2", "o2", "balance", "diff_press", "temp", "flow",
           "well_pres", "header_pres"]


@dataclass
class WOIReading:
    well_id: str          # canonicalized (asterisks stripped, alias applied)
    raw_well_id: str      # as printed (may carry asterisks)
    dt: str               # "M/D/YYYY HH:MM"
    adj: bool             # True = post-adjustment reading
    page: int
    ch4: Optional[float] = None
    co2: Optional[float] = None
    o2: Optional[float] = None
    balance: Optional[float] = None
    diff_press: Optional[float] = None
    temp: Optional[float] = None
    flow: Optional[float] = None
    well_pres: Optional[float] = None
    header_pres: Optional[float] = None

    @property
    def valid(self) -> bool:
        """CH4+CO2+O2+Balance ~ 100 — guards against column misalignment."""
        if None in (self.ch4, self.co2, self.o2, self.balance):
            return False
        return abs(self.ch4 + self.co2 + self.o2 + self.balance - 100) <= 1.5


def canonicalize(well_id: str, alias_map: Optional[dict] = None) -> str:
    """Strip trailing asterisks and apply a cross-report alias map. Across
    reports a physical well is abandoned and replaced with a NEW id (and EGLE
    issued a formal WOI-id update on 6/22/2023); pass an alias_map to keep one
    well's history under a single canonical id. Within a single report no map is
    needed."""
    canon = well_id.rstrip("*")
    if alias_map and canon in alias_map:
        canon = alias_map[canon]
    return canon


def _parse_lines(lines: list[tuple[str, int]], alias_map: Optional[dict] = None) -> list[WOIReading]:
    """Pure line state-machine. `lines` is [(text, page_number)]. Factored out so
    it is unit-testable without a PDF."""
    out: list[WOIReading] = []
    i, n = 0, len(lines)
    while i < n:
        text, page = lines[i]
        if not WELL_RE.match(text):
            i += 1
            continue
        # A well id must be followed by a date+time row to be a data reading.
        if i + 1 >= n or not DT_RE.match(lines[i + 1][0]):
            i += 1
            continue
        raw_well = text
        dt = lines[i + 1][0]
        j = i + 2
        adj = False
        if j < n and lines[j][0] == "ADJ":
            adj = True
            j += 1
        nums: list[float] = []
        while j < n and len(nums) < 9 and FLOAT_RE.match(lines[j][0]):
            nums.append(float(lines[j][0]))
            j += 1
        if len(nums) >= 6:  # need at least through Temp (index 5)
            r = WOIReading(
                well_id=canonicalize(raw_well, alias_map),
                raw_well_id=raw_well, dt=dt, adj=adj, page=page,
            )
            for ci, v in enumerate(nums):
                setattr(r, COLUMNS[ci], v)
            out.append(r)
        i = j
    return out


def _pdf_lines(pdf_path: str) -> list[tuple[str, int]]:
    doc = fitz.open(pdf_path)
    try:
        lines = []
        for p in range(len(doc)):
            for ln in doc[p].get_text().split("\n"):
                s = ln.strip()
                if s:
                    lines.append((s, p + 1))
        return lines
    finally:
        doc.close()


def parse_gas_extraction(pdf_path: str, alias_map: Optional[dict] = None) -> list[WOIReading]:
    """Parse every Attachment-1 gas-extraction reading in the report."""
    return _parse_lines(_pdf_lines(pdf_path), alias_map=alias_map)


def extract_woi_well_list(pdf_path: str, alias_map: Optional[dict] = None) -> set[str]:
    """Return the canonical ids of the formally-designated Wells of Interest, read
    from the Attachment-2 'Wells of Interest Per ... HOV Approval Letter' tables
    (date-only rows). These are the wells to count as WOI; Attachment-1 holds the
    broader wellfield."""
    doc = fitz.open(pdf_path)
    try:
        woi: set[str] = set()
        for p in range(len(doc)):
            t = doc[p].get_text()
            if "Wells of Interest" not in t:
                continue
            for tok in re.findall(r"AH[A-Z0-9]+\*{0,4}", t):
                woi.add(canonicalize(tok, alias_map))
        return woi
    finally:
        doc.close()


def to_measurements(reading: WOIReading, include_adj: bool = False) -> list[dict]:
    """Convert one reading to ADR-004 measurement dicts. By default skips ADJ
    (post-adjustment) rows so the same well/time isn't double-counted — the
    as-found reading is what the well was actually doing."""
    if reading.adj and not include_adj:
        return []
    iso = _to_iso(reading.dt)
    out = []
    if reading.temp is not None:
        out.append(_m("temperature", reading.temp, "F", reading.well_id, iso))
    if reading.o2 is not None:
        out.append(_m("oxygen", reading.o2, "percent", reading.well_id, iso))
    if reading.ch4 is not None:
        # methane has no native enum slot; carry it as 'other' (low CH4 alongside
        # high temp + O2 is the subsurface-oxidation signature).
        m = _m("other", reading.ch4, "percent", reading.well_id, iso)
        m["note"] = "methane (CH4)"
        out.append(m)
    return out


def _m(metric, value, unit, well_id, iso):
    return {"metric": metric, "value": value, "unit": unit, "basis": "measured",
            "well_id": well_id, "as_of_date": iso, "note": None}


def _to_iso(dt: str) -> Optional[str]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", dt)
    if not m:
        return None
    return f"{int(m[3]):04d}-{int(m[1]):02d}-{int(m[2]):02d}"


def per_well_summary(
    readings: list[WOIReading],
    woi_set: Optional[set] = None,
    valid_only: bool = True,
) -> list[dict]:
    """Aggregate to one row per canonical well: max temp (with its date + the O2
    and CH4 read at that same moment), and max O2. Sorted hottest-first."""
    by_well: dict[str, list[WOIReading]] = {}
    for r in readings:
        if valid_only and not r.valid:
            continue
        if r.adj:  # use as-found for the well's headline state
            continue
        by_well.setdefault(r.well_id, []).append(r)

    rows = []
    for well, rs in by_well.items():
        temps = [r for r in rs if r.temp is not None]
        o2s = [r for r in rs if r.o2 is not None]
        hottest = max(temps, key=lambda r: r.temp) if temps else None
        max_o2 = max((r.o2 for r in o2s), default=None)
        rows.append({
            "well": well,
            "is_woi": (woi_set is not None and well in woi_set),
            "max_temp_f": hottest.temp if hottest else None,
            "max_temp_date": hottest.dt if hottest else None,
            "o2_at_max_temp": hottest.o2 if hottest else None,
            "ch4_at_max_temp": hottest.ch4 if hottest else None,
            "max_o2_pct": max_o2,
            "n_readings": len(rs),
        })
    rows.sort(key=lambda d: (d["max_temp_f"] is not None, d["max_temp_f"] or -1), reverse=True)
    return rows
