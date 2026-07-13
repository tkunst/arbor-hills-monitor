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
        # methane (CH4) is a first-class metric (ADR 004). Low CH4 alongside high
        # temp + some O2 at the same well is the subsurface-oxidation signature.
        out.append(_m("methane", reading.ch4, "percent", reading.well_id, iso))
    return out


def _m(metric, value, unit, well_id, iso):
    return {"metric": metric, "value": value, "unit": unit, "basis": "measured",
            "well_id": well_id, "as_of_date": iso, "note": None}


def _to_iso(dt: str) -> Optional[str]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", dt)
    if not m:
        return None
    return f"{int(m[3]):04d}-{int(m[1]):02d}-{int(m[2]):02d}"


# ---------------------------------------------------------------------------
# Attachment 2 — carbon monoxide (CO) data
# ---------------------------------------------------------------------------
#
# CO is a direct product of combustion, so rising CO in landfill gas is an
# early-warning signal for subsurface oxidation. Attachment 2 lists CO (ppm) by
# month for the Wells of Interest only (so every CO reading is a WOI well).
#
# Row shape (canonical monthly tables): Well ID / Date(no time) / ppm. The page
# header is "Attachment 2 - <Month> <Year> CO Data" with a standalone "ppm"
# column header. The report also contains a second, malformed representation
# (a "%" double-table with Excel date-serial leaks like 45658.00) — we parse
# ONLY pages that carry a standalone "ppm" header line, which excludes those.

CO_IMPLAUSIBLE_PPM = 10000  # backstop against Excel-serial leaks (real CO << this)

_MONTH_RE = re.compile(r"Attachment\s*2\s*-\s*([A-Za-z]+)\s+(\d{4})\s*CO Data")


@dataclass
class COReading:
    well_id: str        # canonical (asterisks stripped, alias applied)
    raw_well_id: str
    date: str           # "M/D/YYYY"
    month: str          # e.g. "January 2025" (from the page header)
    ppm: float
    page: int


def _parse_co_page(page_lines: list[str], page: int, alias_map: Optional[dict] = None) -> list[COReading]:
    """Parse one canonical CO page (pure). Returns [] for the malformed '%'
    double-table pages (no standalone 'ppm' header)."""
    if "ppm" not in page_lines:
        return []
    mm = _MONTH_RE.search(" ".join(page_lines))
    month = f"{mm.group(1)} {mm.group(2)}" if mm else f"page {page}"
    out: list[COReading] = []
    i = page_lines.index("ppm") + 1
    n = len(page_lines)
    while i < n:
        s = page_lines[i]
        if (WELL_RE.match(s) and i + 2 < n and DATE_ONLY_RE.match(page_lines[i + 1])
                and FLOAT_RE.match(page_lines[i + 2])):
            ppm = float(page_lines[i + 2])
            if ppm < CO_IMPLAUSIBLE_PPM:  # drop Excel-serial leaks
                out.append(COReading(
                    well_id=canonicalize(s, alias_map), raw_well_id=s,
                    date=page_lines[i + 1], month=month, ppm=ppm, page=page,
                ))
            i += 3
        else:
            i += 1
    return out


def _dedupe_co(readings: list[COReading]) -> list[COReading]:
    """Keep the first reading per (well, month) — canonical pages come first."""
    seen = set()
    out = []
    for r in readings:
        key = (r.well_id, r.month)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def parse_co_data(pdf_path: str, alias_map: Optional[dict] = None) -> list[COReading]:
    """Parse the monthly CO (ppm) tables from Attachment 2 (WOI wells only)."""
    doc = fitz.open(pdf_path)
    try:
        out: list[COReading] = []
        for p in range(len(doc)):
            t = doc[p].get_text()
            if "Attachment 2" not in t or "CO Data" not in t:
                continue
            page_lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
            out.extend(_parse_co_page(page_lines, p + 1, alias_map))
        return _dedupe_co(out)
    finally:
        doc.close()


def co_to_measurements(reading: COReading) -> list[dict]:
    return [{"metric": "carbon_monoxide", "value": reading.ppm, "unit": "ppm",
             "basis": "measured", "well_id": reading.well_id,
             "as_of_date": _to_iso(reading.date + " 00:00"), "note": f"CO, {reading.month}"}]


# Chronological month ordering for trend display.
_MONTH_ORDER = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


def per_well_co_summary(co_readings: list[COReading]) -> list[dict]:
    """One row per well: the monthly CO series (chronological), max ppm, and the
    rise from the well's first to last reported month (trend signal)."""
    by_well: dict[str, list[COReading]] = {}
    for r in co_readings:
        by_well.setdefault(r.well_id, []).append(r)
    rows = []
    for well, rs in by_well.items():
        def mkey(r):
            parts = r.month.split()
            return (parts[-1], _MONTH_ORDER.get(parts[0], 99)) if len(parts) == 2 else ("", 99)
        rs_sorted = sorted(rs, key=mkey)
        series = [(r.month, r.ppm) for r in rs_sorted]
        ppms = [r.ppm for r in rs_sorted]
        rows.append({
            "well": well,
            "series": series,
            "max_ppm": max(ppms),
            "first_ppm": ppms[0],
            "last_ppm": ppms[-1],
            "rise": ppms[-1] - ppms[0],
            "n_months": len(series),
        })
    rows.sort(key=lambda d: (d["max_ppm"], d["rise"]), reverse=True)
    return rows


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
