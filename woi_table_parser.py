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
from dataclasses import dataclass, field
from typing import Optional

import fitz  # pymupdf

WELL_RE = re.compile(r"^AH[A-Z0-9]+\*{0,4}$")
DT_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}$")  # date AND time
DATE_ONLY_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

# The full column set, in the order the 9-number layout prints it after Well ID
# / Date/Time / optional ADJ. Not every report prints every column: see LAYOUTS.
COLUMNS = ["ch4", "co2", "o2", "balance", "diff_press", "temp", "flow",
           "well_pres", "header_pres"]

# ---------------------------------------------------------------------------
# Column-layout detection (coder:woi-layout-guard, ADR 005 addendum 2026-10-15)
# ---------------------------------------------------------------------------
#
# The numbers in a row are read in ORDER, so the parser must know which columns
# the table actually prints. Three layouts exist in the filed reports, and two
# of them have 8 numbers per row:
#
#   with Diff. Press, with Header Pres (9)  -- e.g. the 2025 H1 report
#   with Diff. Press, no Header Pres    (8)  -- e.g. the 2025 H2 report
#   NO Diff. Press,   with Header Pres  (8)  -- e.g. the first table of the
#                                               Q3 2023 HOV WOI report
#   no Well / Header Pres               (7)  -- Gas Extraction subset reports
#   gas + Diff. Press + Temp only       (6)  -- Gas Extraction subset reports
#   gas + Temp only                     (5)  -- Gas Extraction subset reports
#
# Reading the third layout as if it were the first put each row's FLOW value in
# the temperature field (a 110.5F well came out as 526.8F, 82 readings over
# 200F, all passing the sum-to-100 gate, which only checks the gas columns). So
# the layout is read from each table's own header row ("Well ID ... CH4 (%) ...
# Diff. Press ... Temp. ...") and carried forward until the next header. The
# label ORDER comes from each label word's position on the page (in the
# table's own reading direction: some tables are printed rotated), not from the
# linearized text: a wrapped label ("Balance / (%)", "Diff. / Press. / (in.
# H2O)") is linearized AFTER the single-line labels to its right, so text order
# would read "CH4 CO2 O2 Temp. Balance Diff." for a table printed
# CH4 CO2 O2 Balance Diff.Press Temp. A row
# is never parsed without a recognized layout: rows under an unrecognized gas
# header, or before any header, are rejected whole (never emitted) and
# reported in GasExtractionParse.rejected_tables.
LAYOUTS = {
    "full": ["ch4", "co2", "o2", "balance", "diff_press", "temp", "flow",
             "well_pres", "header_pres"],
    "no_header_pres": ["ch4", "co2", "o2", "balance", "diff_press", "temp",
                       "flow", "well_pres"],
    "no_diff_press": ["ch4", "co2", "o2", "balance", "temp", "flow",
                      "well_pres", "header_pres"],
    # the Gas Extraction subset reports (2021-2024): no pressure columns, or
    # gas + Diff. Press + Temp only
    "no_pressures": ["ch4", "co2", "o2", "balance", "diff_press", "temp", "flow"],
    "no_flow": ["ch4", "co2", "o2", "balance", "diff_press", "temp"],
    "gas_temp": ["ch4", "co2", "o2", "balance", "temp"],
}

# Header label -> column. Searched in the joined header text; the columns are
# ordered by where their label appears.
_LABEL_RES = [
    ("ch4", re.compile(r"\bCH4\b")),
    ("co2", re.compile(r"\bCO2\b")),
    ("o2", re.compile(r"\bO2\b")),
    ("balance", re.compile(r"\bBalance\b", re.I)),
    ("diff_press", re.compile(r"\bDiff\.?\s*Press", re.I)),
    ("temp", re.compile(r"\bTemp\b", re.I)),
    ("flow", re.compile(r"\bFlow\b", re.I)),
    ("well_pres", re.compile(r"\bWell\s+Pres", re.I)),
    ("header_pres", re.compile(r"\bHeader\s+Pres", re.I)),
]
NON_GAS_HEADER = "(non-gas table)"
_HEADER_START_RE = re.compile(r"^Well\s*ID\b", re.I)
# How far past "Well ID" a header can run before its first data row (the 2025
# reports carry ~40 label lines of pump-count / depth columns).
_HEADER_MAX_LINES = 150

# Table-level column-shift check: in a correctly read gas table the flow column
# (scfm) is non-negative on almost every row; in a shifted read it holds a
# pressure column, which is negative on most rows. A table where more than
# this share of rows show a negative "flow" is rejected whole. Measured on the
# filed reports 2026-10-15: every correctly read table runs 0%; the shifted
# read of the Q3 2023 HOV table ran 94%.
NEGATIVE_FLOW_REJECT_SHARE = 0.25
_NEGATIVE_FLOW_MIN_ROWS = 20


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


@dataclass
class RejectedTable:
    """A run of gas-shaped rows that was NOT emitted, and why. Pages are
    1-based. `reason` is a short fixed phrase (safe to log publicly)."""
    first_page: int
    last_page: int
    n_rows: int
    reason: str
    header: str = ""      # the header labels as read (empty if none)


@dataclass
class GasExtractionParse:
    readings: list = field(default_factory=list)          # [WOIReading]
    rejected_tables: list = field(default_factory=list)   # [RejectedTable]
    skipped_pages: list = field(default_factory=list)     # [int], 1-based


def layout_for_columns(cols: list) -> Optional[list]:
    """`cols` (header columns in printed left-to-right order) if they form a
    known layout (LAYOUTS), else None. Pure."""
    for layout in LAYOUTS.values():
        if list(cols) == layout:
            return list(layout)
    return None


def _text_columns(header_text: str) -> list:
    found = []
    for col, rx in _LABEL_RES:
        m = rx.search(header_text)
        if m:
            found.append((m.start(), col))
    return [c for _, c in sorted(found)]


def detect_layout(header_text: str) -> Optional[list]:
    """Layout from header TEXT order. Only right when no label wraps onto a
    second line; a PDF is read with the word positions instead
    (_page_header_columns). Used for line fragments with no page geometry."""
    return layout_for_columns(_text_columns(header_text))


# First word of each header label, as get_text("words") splits it.
_LABEL_WORDS = {"ch4": re.compile(r"^CH4"), "co2": re.compile(r"^CO2"),
                "o2": re.compile(r"^O2"), "balance": re.compile(r"^Balance", re.I),
                "diff_press": re.compile(r"^Diff\.?$", re.I),
                "temp": re.compile(r"^Temp\.?$", re.I), "flow": re.compile(r"^Flow$", re.I),
                "well_pres": re.compile(r"^Well$", re.I),
                "header_pres": re.compile(r"^Header$", re.I)}
# Header labels sit on, or wrap upward from, the "Well ID" baseline.
_HEADER_BAND_ABOVE = 60.0
_HEADER_BAND_BELOW = 5.0


def _center(w) -> tuple[float, float]:
    return (w[0] + w[2]) / 2, (w[1] + w[3]) / 2


def _reading_frame(well_w, id_w):
    """The text direction of a "Well ID" label, as a function mapping a word
    box into a frame where that text runs left to right (rows go down). Some
    reports draw the table rotated on an unrotated page, others set /Rotate:
    either way the word boxes come back in the page's own frame. Returns None
    if the two words aren't adjacent."""
    (wx, wy), (ix, iy) = _center(well_w), _center(id_w)
    dx, dy = ix - wx, iy - wy
    if abs(dx) + abs(dy) > 40:
        return None
    if abs(dx) >= abs(dy):
        f = (lambda x, y: (x, y)) if dx > 0 else (lambda x, y: (-x, -y))
    else:
        f = (lambda x, y: (-y, x)) if dy < 0 else (lambda x, y: (y, -x))

    def box(w):
        (ax, ay), (bx, by) = f(w[0], w[1]), f(w[2], w[3])
        return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by), w[4])
    return box


def _page_header_columns(words: list) -> list[list]:
    """For each "Well ID" header on a page (in reading order), the label
    columns in left-to-right order, from the words' positions. `words` is
    page.get_text("words") output. Pure."""
    found_headers = []
    wells = [w for w in words if w[4].lower() == "well"]
    for idw in (w for w in words if w[4] == "ID"):
        frame = None
        for ww in wells:
            frame = _reading_frame(ww, idw)
            if frame is not None:
                break
        if frame is None:
            continue  # an "ID" that isn't the "Well ID" column label
        tw = [frame(w) for w in words]
        tid = frame(idw)
        found = {}
        for w in tw:
            if not (tid[1] - _HEADER_BAND_ABOVE <= w[1] <= tid[1] + _HEADER_BAND_BELOW):
                continue
            if w[0] <= tid[2]:
                continue  # left of the Well ID label (incl. its own "Well")
            for col, rx in _LABEL_WORDS.items():
                if rx.match(w[4]) and col not in found:
                    found[col] = w[0]
        cols = [c for c, _ in sorted(found.items(), key=lambda kv: kv[1])]
        found_headers.append((tid[1], cols))
    return [cols for _, cols in sorted(found_headers, key=lambda t: t[0])]


def _looks_like_gas_header(header_text: str) -> bool:
    """A header for a gas-extraction table (known layout or not), as opposed to
    another table that also starts "Well ID" (CO data, liquid levels)."""
    return bool(re.search(r"\bCH4\b", header_text)) and bool(
        re.search(r"\bCO2\b", header_text))


def canonicalize(well_id: str, alias_map: Optional[dict] = None) -> str:
    """Strip trailing asterisks and apply a cross-report alias map. Across
    reports a physical well is abandoned and replaced with a NEW id (and EGLE
    issued a formal WOI-id update on 6/22/2023); pass an alias_map to keep one
    well's history under a single canonical id. The live callers pass the curated
    map from well_aliases.load_alias_map() (ADR 068); None = stars stripped only."""
    canon = well_id.rstrip("*")
    if alias_map and canon in alias_map:
        canon = alias_map[canon]
    return canon


def _is_row_start(lines, i) -> bool:
    return (WELL_RE.match(lines[i][0]) is not None and i + 1 < len(lines)
            and DT_RE.match(lines[i + 1][0]) is not None)


def _read_header(lines, i) -> tuple[str, int]:
    """Join the label lines after a "Well ID" line at i, up to the first data
    row (or _HEADER_MAX_LINES). Returns (header_text, index_after_header)."""
    j = i + 1
    parts = [lines[i][0]]  # labels can share the "Well ID" line
    while j < len(lines) and j - i <= _HEADER_MAX_LINES and not _is_row_start(lines, j):
        if _HEADER_START_RE.match(lines[j][0]):
            break  # another table's header: stop here, it is read on its own
        parts.append(lines[j][0])
        j += 1
    return " ".join(parts), j


def _negative_flow_share(rows: list) -> Optional[float]:
    flows = [r.flow for r in rows if r.flow is not None]
    if len(flows) < _NEGATIVE_FLOW_MIN_ROWS:
        return None
    return sum(1 for f in flows if f < 0) / len(flows)


def _parse_table_lines(lines: list[tuple[str, int]], alias_map: Optional[dict] = None,
                       initial_layout: Optional[list] = None,
                       page_headers: Optional[dict] = None) -> GasExtractionParse:
    """Pure line state-machine with per-table layout detection. `lines` is
    [(text, page_number)]. Rows are parsed with the layout of the most recent
    gas header; with no recognized layout they are rejected (counted into a
    RejectedTable), never guessed. Each table (a run of rows under one layout)
    then gets the negative-flow column-shift check. `page_headers` ({page:
    [columns per "Well ID" header, top to bottom]}, from the word positions)
    supplies the column order; without it the header text order is used."""
    out = GasExtractionParse()
    seen_on_page: dict = {}
    layout = list(initial_layout) if initial_layout else None
    header = ""
    header_cols = None
    # the table currently being collected
    cur_rows: list = []
    cur_rejected = {"n": 0, "first": None, "last": None, "reason": ""}

    def flush():
        nonlocal cur_rows
        if cur_rows:
            share = _negative_flow_share(cur_rows)
            if share is not None and share > NEGATIVE_FLOW_REJECT_SHARE:
                out.rejected_tables.append(RejectedTable(
                    first_page=cur_rows[0].page, last_page=cur_rows[-1].page,
                    n_rows=len(cur_rows), header=header,
                    reason=f"column-shift signature: {share:.0%} of rows have a negative flow"))
            else:
                out.readings.extend(cur_rows)
        cur_rows = []
        if cur_rejected["n"]:
            out.rejected_tables.append(RejectedTable(
                first_page=cur_rejected["first"], last_page=cur_rejected["last"],
                n_rows=cur_rejected["n"], reason=cur_rejected["reason"],
                header=header if header_cols is None else "columns read: " + ",".join(header_cols)))
        cur_rejected.update(n=0, first=None, last=None, reason="")

    i, n = 0, len(lines)
    while i < n:
        text, page = lines[i]
        if _HEADER_START_RE.match(text):
            htext, j = _read_header(lines, i)
            k = seen_on_page.get(page, 0)
            seen_on_page[page] = k + 1
            if _looks_like_gas_header(htext):
                if page_headers is not None:
                    cols_on_page = page_headers.get(page) or []
                    cols = cols_on_page[k] if k < len(cols_on_page) else []
                else:
                    cols = _text_columns(htext)
                new_layout = layout_for_columns(cols)
                # A repeated header (one per page) continues the same table;
                # so does a repeat of the same unrecognized column set, so one
                # unknown table is reported once, not once per page.
                if new_layout != layout or (new_layout is None and cols != header_cols):
                    flush()
                    layout, header, header_cols = new_layout, htext[:200], cols
                i = j
                continue
            # Another table that also starts "Well ID" (CO data, liquid
            # levels): the gas table has ended. Its rows are not gas readings.
            flush()
            layout, header, header_cols = None, NON_GAS_HEADER, None
            i = j
            continue
        if not _is_row_start(lines, i):
            i += 1
            continue
        raw_well = text
        dt = lines[i + 1][0]
        j = i + 2
        adj = False
        if j < n and lines[j][0] == "ADJ":
            adj = True
            j += 1
        width = len(layout) if layout else len(COLUMNS)
        nums: list[float] = []
        while j < n and len(nums) < width and FLOAT_RE.match(lines[j][0]):
            nums.append(float(lines[j][0]))
            j += 1
        if layout is None:
            # Gas-shaped row with no recognized layout: reject, never guess.
            # Under a gas header we didn't recognize, every row with the four
            # gas columns counts (the 250pp variant prints 5 numbers); with no
            # gas header at all, only rows long enough to have been read as gas
            # readings before this guard existed.
            unknown_gas = header not in ("", NON_GAS_HEADER)
            if len(nums) >= (4 if unknown_gas else 6):
                cur_rejected["n"] += 1
                cur_rejected["first"] = cur_rejected["first"] or page
                cur_rejected["last"] = page
                cur_rejected["reason"] = (
                    "rows under a non-gas table header" if header == NON_GAS_HEADER
                    else "column layout not recognized" if header
                    else "rows before any recognized header")
        elif len(nums) > layout.index("temp"):  # need at least through Temp
            r = WOIReading(
                well_id=canonicalize(raw_well, alias_map),
                raw_well_id=raw_well, dt=dt, adj=adj, page=page,
            )
            for ci, v in enumerate(nums):
                setattr(r, layout[ci], v)
            cur_rows.append(r)
        i = j
    flush()
    return out


def _parse_lines(lines: list[tuple[str, int]], alias_map: Optional[dict] = None) -> list[WOIReading]:
    """Header-less line state-machine for fragments of ONE table already known
    to be in the 9-number layout (unit tests, scripts). A real report goes
    through parse_gas_extraction_report(), which reads each table's own header
    and never assumes a layout."""
    return _parse_table_lines(lines, alias_map, initial_layout=COLUMNS).readings


_WELL_ID_TEXT_RE = re.compile(r"Well\s*ID", re.I)


def _pdf_read(pdf_path: str) -> tuple[list[tuple[str, int]], list[int], dict]:
    """Linearized text of every page, plus {page: header columns} for pages
    that carry a "Well ID" header. Any page MuPDF cannot read (e.g. "cannot
    find page N in page tree" on a damaged page tree) is skipped and returned:
    one bad page must not cost the whole report."""
    doc = fitz.open(pdf_path)
    try:
        lines, skipped, headers = [], [], {}
        for p in range(len(doc)):
            try:
                page = doc[p]
                text = page.get_text()
                if _WELL_ID_TEXT_RE.search(text):
                    headers[p + 1] = _page_header_columns(page.get_text("words"))
            except Exception:  # noqa: BLE001 — a damaged page; keep the rest
                skipped.append(p + 1)
                continue
            for ln in text.split("\n"):
                s = ln.strip()
                if s:
                    lines.append((s, p + 1))
        return lines, skipped, headers
    finally:
        doc.close()


def _pdf_lines(pdf_path: str) -> list[tuple[str, int]]:
    return _pdf_read(pdf_path)[0]


def parse_gas_extraction_report(pdf_path: str, alias_map: Optional[dict] = None) -> GasExtractionParse:
    """Parse every Attachment-1 gas-extraction reading in the report, table by
    table, with each table's own column layout. Returns the readings plus the
    tables that were rejected (unknown layout / column-shift signature) and the
    pages that could not be read. Logs rejections and skipped pages (counts and
    page numbers only)."""
    lines, skipped, headers = _pdf_read(pdf_path)
    out = _parse_table_lines(lines, alias_map, page_headers=headers)
    out.skipped_pages = skipped
    if skipped:
        print(f"[woi_table_parser] skipped {len(skipped)} unreadable page(s): "
              f"{_page_ranges(skipped)}")
    for t in out.rejected_tables:
        print(f"[woi_table_parser] REJECTED gas table pp{t.first_page}-{t.last_page} "
              f"({t.n_rows} rows): {t.reason}")
    return out


def _page_ranges(pages: list) -> str:
    """[71,72,73,80] -> "71-73, 80"."""
    out, start, prev = [], None, None
    for p in sorted(pages):
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{start}-{prev}" if prev != start else f"{start}")
            start = prev = p
    if start is not None:
        out.append(f"{start}-{prev}" if prev != start else f"{start}")
    return ", ".join(out)


def parse_gas_extraction(pdf_path: str, alias_map: Optional[dict] = None) -> list[WOIReading]:
    """Parse every Attachment-1 gas-extraction reading in the report (readings
    only; see parse_gas_extraction_report for the rejected tables)."""
    return parse_gas_extraction_report(pdf_path, alias_map=alias_map).readings


def extract_woi_well_list(pdf_path: str, alias_map: Optional[dict] = None) -> set[str]:
    """Return the canonical ids of the formally-designated Wells of Interest, read
    from the Attachment-2 'Wells of Interest Per ... HOV Approval Letter' tables
    (date-only rows). These are the wells to count as WOI; Attachment-1 holds the
    broader wellfield."""
    doc = fitz.open(pdf_path)
    try:
        woi: set[str] = set()
        for p in range(len(doc)):
            try:
                t = doc[p].get_text()
            except Exception:  # noqa: BLE001 — a damaged page; keep the rest
                continue
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
        out.append(_m("temperature", reading.temp, "F", reading.well_id, iso, reading.raw_well_id))
    if reading.o2 is not None:
        out.append(_m("oxygen", reading.o2, "percent", reading.well_id, iso, reading.raw_well_id))
    if reading.ch4 is not None:
        # methane (CH4) is a first-class metric (ADR 004). Low CH4 alongside high
        # temp + some O2 at the same well is the subsurface-oxidation signature.
        out.append(_m("methane", reading.ch4, "percent", reading.well_id, iso, reading.raw_well_id))
    return out


def _m(metric, value, unit, well_id, iso, raw_well_id=None):
    out = {"metric": metric, "value": value, "unit": unit, "basis": "measured",
           "well_id": well_id, "as_of_date": iso, "note": None}
    _keep_raw(out, raw_well_id)
    return out


def _keep_raw(m: dict, raw_well_id) -> None:
    """ADR 068: when the alias map relabeled a reading, carry the as-printed id
    (stars stripped) so the Measurements tab's "Well ID As Filed" column makes
    the relabel auditable. Unchanged ids carry nothing."""
    if raw_well_id is not None:
        raw = raw_well_id.rstrip("*")
        if raw != m["well_id"]:
            m["raw_well_id"] = raw


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
    seen: dict = {}
    out: list = []
    for r in readings:
        key = (r.well_id, r.month)
        if key not in seen:
            seen[key] = len(out)
            out.append(r)
            continue
        kept = out[seen[key]]
        if kept.raw_well_id.rstrip("*") == r.raw_well_id.rstrip("*"):
            continue  # an ordinary duplicate of the same printed id
        # Two different printed spellings collapsed onto one canonical id by the
        # alias map (ADR 068). Prefer the reading printed under the CANONICAL
        # spelling (the current-era table), not whichever page came first, and
        # never drop either silently.
        if kept.raw_well_id.rstrip("*") != r.well_id and r.raw_well_id.rstrip("*") == r.well_id:
            out[seen[key]], kept, r = r, r, kept
        print(f"[woi_table_parser] CO alias collision {kept.well_id} {kept.month}: kept "
              f"{kept.raw_well_id}={kept.ppm} (p{kept.page}), dropped "
              f"{r.raw_well_id}={r.ppm} (p{r.page})")
    return out


def parse_co_data(pdf_path: str, alias_map: Optional[dict] = None) -> list[COReading]:
    """Parse the monthly CO (ppm) tables from Attachment 2 (WOI wells only)."""
    doc = fitz.open(pdf_path)
    try:
        out: list[COReading] = []
        for p in range(len(doc)):
            try:
                t = doc[p].get_text()
            except Exception:  # noqa: BLE001 — a damaged page; keep the rest
                continue
            if "Attachment 2" not in t or "CO Data" not in t:
                continue
            page_lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
            out.extend(_parse_co_page(page_lines, p + 1, alias_map))
        return _dedupe_co(out)
    finally:
        doc.close()


def co_to_measurements(reading: COReading) -> list[dict]:
    m = {"metric": "carbon_monoxide", "value": reading.ppm, "unit": "ppm",
         "basis": "measured", "well_id": reading.well_id,
         "as_of_date": _to_iso(reading.date + " 00:00"), "note": f"CO, {reading.month}"}
    _keep_raw(m, reading.raw_well_id)
    return [m]


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
