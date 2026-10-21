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
    [ADJ]                (optional: the post-valve-adjustment reading; a
                          table whose header has no ADJ column prints no token,
                          see _ADJ_LABEL_RE)
    CH4% CO2% O2% Balance% Diff.Press Temp Flow Well-Pres Header-Pres   (9 numbers)
    <comment lines, may themselves contain numbers like "581821: 0">

Tables whose header has an Alias column print Well ID, Alias, Date/Time; the
Well ID is the well (_ALIAS_LABEL_RE). Cells are read token by token: a blank
cell prints "---" and keeps its position, and a value can share its line with
the next cell or a comment ("27.6 ---", "-0.68 No Change: :"). A row with the
four gas columns is kept even when Temp is blank (ADR 005 addendum 2026-10-19).

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
# The Alias column printed next to Well ID in some tables (ADR 005 addendum
# 2026-10-19): looser than WELL_RE, since aliases such as EW-008 or V-10 are
# not AH-format.
ALIAS_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*\*{0,4}$")
DT_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}$")  # date AND time
DATE_ONLY_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
# A blank cell. It fills its column's position with None (ADR 005 addendum
# 2026-10-19); it never ends the row.
BLANK_CELL = "---"

# The full column set, in the order the 9-number layout prints it after Well ID
# / Date/Time / optional ADJ. Not every report prints every column: see LAYOUTS.
COLUMNS = ["ch4", "co2", "o2", "balance", "diff_press", "temp", "flow",
           "well_pres", "header_pres"]

# ---------------------------------------------------------------------------
# Column-layout detection (coder:woi-layout-guard, ADR 005 addendum 2026-10-15)
# ---------------------------------------------------------------------------
#
# The numbers in a row are read in ORDER, so the parser must know which columns
# the table actually prints. Six layouts exist in the filed reports, and two
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
# Does a gas table's header print an ADJ column? (ADR 005 addendum 2026-10-17.)
# Attachment 1 of the 2026-10-08 N2688 report has no ADJ column, yet GFL still
# prints each adjusted well as an as-found row, a comment line, then the
# after-adjustment row under the SAME well + timestamp; the labeled back table
# (p103) prints the literal "ADJ" token on that second row. In a header with no
# ADJ label, a row is therefore read as ADJ when the row right before it in the
# same table has the same printed well and timestamp and is not itself ADJ.
# That gives as-found, ADJ, as-found, ADJ for a pair printed twice, exactly
# what the labeled table prints. Tables that label ADJ are read as before.
_ADJ_LABEL_RE = re.compile(r"\bADJ\b")
# Does a gas table's header print an Alias column? (ADR 005 addendum
# 2026-10-19.) Such tables print each row as Well ID, Alias, Date/Time. Only
# the timestamp's neighbour was ever tested, so the ALIAS became the row's
# well. Under an Alias label a row starts at Well ID / Alias / Date/Time and
# the Well ID is the well; the Alias is kept as printed.
# Only the column label right after "Well ID" counts: the word "alias" in
# prose elsewhere in a header must not switch the row-start rule.
_ALIAS_LABEL_RE = re.compile(r"^Well\s*ID\s+Alias\b")
# With page geometry, a Well ID / Alias / Date/Time start also needs its Well
# ID line in the page's Well ID column (the leftmost well-shaped line, in the
# text's own direction). Measured 2026-10-20 on 4,549 pages: every such start
# sits exactly at that position (offset 0). A well-shaped line elsewhere (a
# comment that names a well) cannot take over the next row.
_WELL_COLUMN_TOLERANCE = 3.0
# A cell sharing its line with a comment ("-0.68 No Change: :") is read only
# when its right edge lines up with the row's last column on that page (the
# median right edge of that column's bare-number cells), within this fraction
# of the cell's own character width. A comment that starts with a number
# ("2 turns open") begins at the Comments column, after that edge, so its
# number ends at least one character width past it. Measured 2026-10-20 on
# the corpus: every real comment-line cell is within 0.07 of a character
# width (see the ADR 005 addendum). Scaled to the font, since the filings
# print these tables at very different sizes.
_LAST_COLUMN_TOLERANCE = 0.3
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

# Row-level misalignment check: a reading whose CH4+CO2+O2+Balance falls
# outside this window is not used. Measured 2026-10-17 on 32 filed reports
# (217,384 parsed rows): every row's sum lies between 96.2 and 100.2, in a
# continuous tail below 100. The earlier symmetric 100 +/- 1.5 window dropped
# 258 of those real rows, among them AHWW0279's 154.4 F reading of 5/31/2024
# (sum 97.8). A shifted read puts a pressure or temperature column into a gas
# slot and lands far outside either bound (e.g. 40 or 78).
GAS_SUM_MIN = 95.0
GAS_SUM_MAX = 101.5


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
    # The Alias column as printed, for a table that has one (None otherwise).
    alias_as_printed: Optional[str] = None

    @property
    def valid(self) -> bool:
        """CH4+CO2+O2+Balance close to 100 — guards against column misalignment.
        The window is one-sided (GAS_SUM_MIN/GAS_SUM_MAX): filed rows run short
        of 100, never over it."""
        if None in (self.ch4, self.co2, self.o2, self.balance):
            return False
        return GAS_SUM_MIN <= self.ch4 + self.co2 + self.o2 + self.balance <= GAS_SUM_MAX


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
    # Rows under a recognized layout that were NOT emitted (counts only):
    # every cell printed "---" (blank_rows); some values but not all four gas
    # columns (partial_rows); the four gas columns, but the row's cells ended
    # before the Temp position (truncated_rows: a cell the parser could not
    # read, e.g. "177.0*", never becomes a "no temperature" reading).
    # ambiguous_rows: the row needed the new reader but doesn't fill every
    # column, so only the cells main's reader would have taken are trusted;
    # counted here when that leaves no reading. off_column_rows: a Well ID /
    # Alias / Date/Time start whose Well ID line is not in the page's Well ID
    # column (read with main's two-line rule instead, or not at all).
    blank_rows: int = 0
    partial_rows: int = 0
    truncated_rows: int = 0
    ambiguous_rows: int = 0
    off_column_rows: int = 0
    # Cells that shared their line with a comment but didn't line up with the
    # last column (_LAST_COLUMN_TOLERANCE): read as blank, the row is kept.
    unaligned_cells: int = 0
    # Split gas tables (ADR 005 addendum 2026-10-20): rows of a table printed
    # as a left half (Well ID ... gas columns) plus a right half (Temp ...
    # pressures) that were joined into readings, and rows of such a table
    # that were not (no clean right-half row; kept rejected).
    split_joined_rows: int = 0
    split_unjoined_rows: int = 0


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
# A real header page has one or two "Well ID" labels; more is not a table header page.
_MAX_ID_WORDS_PER_PAGE = 4
# "Well" also appears in comments ("well flooded"), so it gets a looser cap.
_MAX_WELL_WORDS_PER_PAGE = 500
_MAX_WORDS_PER_HEADER_PAGE = 20000


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
    ids = [w for w in words if w[4] == "ID"]
    if (len(ids) > _MAX_ID_WORDS_PER_PAGE or len(wells) > _MAX_WELL_WORDS_PER_PAGE
            or len(words) > _MAX_WORDS_PER_HEADER_PAGE):
        return []  # not a table header page; bounds the pairing work on a hostile page
    for idw in ids:
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
                if rx.match(w[4]):
                    found[col] = min(found.get(col, w[0]), w[0])  # leftmost match
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


def _is_alias_row_start(lines, i) -> bool:
    """Well ID, Alias, Date/Time on three lines (a table with an Alias column).
    A blank alias may print BLANK_CELL."""
    return (WELL_RE.match(lines[i][0]) is not None and i + 2 < len(lines)
            and (ALIAS_RE.match(lines[i + 1][0]) is not None or lines[i + 1][0] == BLANK_CELL)
            and DT_RE.match(lines[i + 2][0]) is not None)


def _cell(tok: str):
    """(True, value) for a cell token (a number, or BLANK_CELL -> None),
    (False, None) for anything else."""
    if tok == BLANK_CELL:
        return True, None
    if FLOAT_RE.match(tok):
        return True, float(tok)
    return False, None


def _read_values(lines, j: int, width: int, mixed_ok: frozenset = frozenset(),
                 mixed_line_ok=None) -> tuple[list, int, int, bool, Optional[int]]:
    """Read up to `width` cells starting at line j, token by token. A number
    fills the next column; BLANK_CELL fills it with None. A line that starts
    with any other token is not read (the next row's Well ID, a header, a
    comment line). A line with cells and then other text is the row's last
    cell followed by the Comments column ("-0.68 No Change: :"): it is read
    only when it holds ONE cell, that cell completes the row, and its
    position is in `mixed_ok` (the pressure columns), and the row ends there.
    Otherwise the row ends before it, so a comment that starts with a number
    ("150 ft of water", "2 turns open") can never fill Temp, Flow or any gas
    column. Measured 2026-10-20: every such line in the 91-file corpus is a
    single Header Pres cell. `mixed_line_ok(j)`, when given, must also accept
    the line (the page-geometry check, _LAST_COLUMN_TOLERANCE). A cell the
    check refuses still holds its column, as a blank (None): the cell was
    printed, only its value is not trusted, so the row's positions stay known.

    Also returns how many cells main's reader took (one bare number per line,
    stopping at the first other line): those are the cells whose positions
    main trusted. Returns (cells, next_j, main_len, refused, last_line):
    `refused` says a comment-line cell was blanked by the geometry check;
    `last_line` is the line index of a bare-number cell read into the last
    column (a reference cell for that check), or None."""
    vals: list = []
    n = len(lines)
    main_len, main_open, refused, last_line = 0, True, False, None
    while j < n and len(vals) < width:
        toks = lines[j][0].split()
        cells, mixed = [], False
        for tok in toks:
            ok, v = _cell(tok)
            if not ok:
                mixed = True
                break
            cells.append(v)
        if not cells:
            break
        if main_open and len(toks) == 1 and cells[0] is not None:
            main_len += 1
        else:
            main_open = False
        if mixed:
            if len(cells) == 1 and len(vals) + 1 == width and len(vals) in mixed_ok:
                if cells[0] is None or mixed_line_ok is None or mixed_line_ok(j):
                    vals.extend(cells)  # a "---" cell is blank either way
                else:
                    vals.append(None)
                    refused = True
                j += 1
            break
        if len(toks) == 1 and cells[0] is not None and len(vals) == width - 1:
            last_line = j
        vals.extend(cells[:width - len(vals)])
        j += 1
    return vals, j, main_len, refused, last_line


def _read_header(lines, i) -> tuple[str, int]:
    """Join the label lines after a "Well ID" line at i, up to the first data
    row (or _HEADER_MAX_LINES). Under an Alias label the first data row is a
    Well ID / Alias / Date/Time start; any other header ends where main ended
    it. Returns (header_text, index_after_header)."""
    j = i + 1
    parts = [lines[i][0]]  # labels can share the "Well ID" line
    while (j < len(lines) and j - i <= _HEADER_MAX_LINES and not _is_row_start(lines, j)
           and not (_is_alias_row_start(lines, j)
                    and _ALIAS_LABEL_RE.search(" ".join(parts)))):
        if _HEADER_START_RE.match(lines[j][0]):
            break  # another table's header: stop here, it is read on its own
        parts.append(lines[j][0])
        j += 1
    return " ".join(parts), j


def _negative_flow_share(flows: list) -> Optional[float]:
    """Share of negative values among `flows` (the table's Flow cells that
    main's reader would also have read, see _parse_table_lines)."""
    if len(flows) < _NEGATIVE_FLOW_MIN_ROWS:
        return None
    return sum(1 for f in flows if f < 0) / len(flows)


def _parse_table_lines(lines: list[tuple[str, int]], alias_map: Optional[dict] = None,
                       initial_layout: Optional[list] = None,
                       page_headers: Optional[dict] = None,
                       positions: Optional[list] = None, split: Optional[dict] = None,
                       on_split_candidate=None) -> GasExtractionParse:
    """See _parse_table_lines_once. With page geometry (`positions`), the
    lines are parsed twice: the first pass collects, per page, the right edge
    of the last column's bare-number cells; the second reads a cell that
    shares its line with a comment only when it lines up with that edge
    (_LAST_COLUMN_TOLERANCE). Rows are identical in both passes otherwise:
    accepting or refusing such a cell never moves a row boundary."""
    kw = {"split": split, "on_split_candidate": on_split_candidate}
    if positions is None or all(p is None for p in positions):
        return _parse_table_lines_once(lines, alias_map, initial_layout, page_headers, positions,
                                       **kw)
    edges: dict = {}

    def collect(k: int) -> None:
        pos = positions[k]
        if pos is not None and pos[1] is not None:
            edges.setdefault(lines[k][1], []).append(pos[1])

    _parse_table_lines_once(lines, alias_map, initial_layout, page_headers, positions,
                            on_last_cell=collect, split=split)
    edge = {pg: sorted(v)[len(v) // 2] for pg, v in edges.items()}

    def lines_up(k: int) -> bool:
        pos = positions[k]
        if pos is None or pos[1] is None or not pos[2]:
            return False  # can't be checked: read as blank, never as a guess
        pg = lines[k][1]
        # A page with no reference cell of its own uses an adjacent page's
        # (the same table continues across pages).
        col = edge.get(pg, edge.get(pg - 1, edge.get(pg + 1)))
        return col is not None and abs(pos[1] - col) <= _LAST_COLUMN_TOLERANCE * pos[2]

    return _parse_table_lines_once(lines, alias_map, initial_layout, page_headers, positions,
                                   mixed_line_ok=lines_up, **kw)


def _parse_table_lines_once(lines: list[tuple[str, int]], alias_map: Optional[dict] = None,
                            initial_layout: Optional[list] = None,
                            page_headers: Optional[dict] = None,
                            positions: Optional[list] = None,
                            mixed_line_ok=None, on_last_cell=None,
                            split: Optional[dict] = None, on_split_row=None,
                            on_split_candidate=None) -> GasExtractionParse:
    """Pure line state-machine with per-table layout detection. `lines` is
    [(text, page_number)]. Rows are parsed with the layout of the most recent
    gas header; with no recognized layout they are rejected (counted into a
    RejectedTable), never guessed. Each table (a run of rows under one layout)
    then gets the negative-flow column-shift check. `page_headers` ({page:
    [columns per "Well ID" header, top to bottom]}, from the word positions)
    supplies the column order; without it the header text order is used.
    Under a gas header with no ADJ label, the second of two consecutive rows
    with the same well + timestamp is marked ADJ (_ADJ_LABEL_RE). With no
    header at all (`initial_layout`), only the literal ADJ token counts.
    `positions` (parallel to `lines`: each line's (start, first word's right
    edge) along its own text direction, or None) places Well ID lines in their
    column (_WELL_COLUMN_TOLERANCE). `mixed_line_ok`: see _read_values;
    `on_last_cell(j)` receives each reference cell's line (see
    _parse_table_lines).

    Rows main's line-at-a-time reader already read keep main's behavior: the
    negative-flow check and the unlabeled-ADJ pairing see only those rows
    (ADR 005 addendum 2026-10-19). Rows this reader adds can't move them.

    Split tables (ADR 005 addendum 2026-10-20): `on_split_candidate(cols,
    first, last)` receives each rejected table whose header reads gas columns
    only (_is_split_left). `split` ({left page: _SplitPage}, from
    _split_plan) joins such a table's rows to their right halves: a row whose
    left cells are all read is joined when its page pairs cleanly, else
    counted (split_unjoined_rows) and kept rejected. `on_split_row(page,
    coord)` receives each such row's coordinate (the pairing dry run)."""
    out = GasExtractionParse()
    seen_on_page: dict = {}
    text_headers: dict = {}
    for t, pg in lines:
        if _HEADER_START_RE.match(t):
            text_headers[pg] = text_headers.get(pg, 0) + 1
    layout = list(initial_layout) if initial_layout else None
    header = ""
    header_cols = None
    adj_labeled = True  # no header read yet: only a literal ADJ token marks ADJ
    alias_labeled = False  # no header read yet: rows are Well ID / Date/Time
    # the table currently being collected
    cur_rows: list = []
    cur_flows: list = []    # Flow cells main would have read (negative-flow check)
    cur_added: list = []    # rows whose Flow cell main would NOT have read
    partner = None          # the last row main would have emitted (unlabeled ADJ)
    well_col: dict = {}     # page -> Well ID column position
    cur_rejected = {"n": 0, "first": None, "last": None, "reason": ""}
    cur_unjoined = {"n": 0, "first": None, "last": None}  # split-table rows not joined
    page_first: dict = {}   # page -> index of its first line (split coordinates)

    def flush():
        nonlocal cur_rows, cur_flows, cur_added, partner
        if cur_rows:
            # Main's rows are judged on main's Flow cells alone, so added rows
            # can neither reject them nor dilute a shifted table. The added
            # rows get the same check on their own Flow cells (with the same
            # minimum row count main's check has).
            share = _negative_flow_share(cur_flows)
            added_share = _negative_flow_share([r.flow for r in cur_added if r.flow is not None])
            if share is not None and share > NEGATIVE_FLOW_REJECT_SHARE:
                out.rejected_tables.append(RejectedTable(
                    first_page=cur_rows[0].page, last_page=cur_rows[-1].page,
                    n_rows=len(cur_rows), header=header,
                    reason=f"column-shift signature: {share:.0%} of rows have a negative flow"))
            elif added_share is not None and added_share > NEGATIVE_FLOW_REJECT_SHARE:
                added_ids = {id(r) for r in cur_added}
                out.readings.extend(r for r in cur_rows if id(r) not in added_ids)
                out.rejected_tables.append(RejectedTable(
                    first_page=cur_added[0].page, last_page=cur_added[-1].page,
                    n_rows=len(cur_added), header=header,
                    reason=(f"column-shift signature in rows read past a blank cell: "
                            f"{added_share:.0%} have a negative flow")))
            else:
                out.readings.extend(cur_rows)
        cur_rows, cur_flows, cur_added, partner = [], [], [], None
        if cur_unjoined["n"]:
            out.rejected_tables.append(RejectedTable(
                first_page=cur_unjoined["first"], last_page=cur_unjoined["last"],
                n_rows=cur_unjoined["n"], reason=SPLIT_UNJOINED_REASON,
                header="columns read: " + ",".join(header_cols or [])))
            cur_unjoined.update(n=0, first=None, last=None)
        if (cur_rejected["n"] and on_split_candidate is not None
                and cur_rejected["reason"] == "column layout not recognized"
                and _is_split_left(header_cols)):
            on_split_candidate((tuple(header_cols), cur_rejected["first"], cur_rejected["last"]))
        if cur_rejected["n"]:
            out.rejected_tables.append(RejectedTable(
                first_page=cur_rejected["first"], last_page=cur_rejected["last"],
                n_rows=cur_rejected["n"], reason=cur_rejected["reason"],
                header=header if header_cols is None else "columns read: " + ",".join(header_cols)))
        cur_rejected.update(n=0, first=None, last=None, reason="")

    if split:
        for k, (_, pg) in enumerate(lines):
            page_first.setdefault(pg, k)

    if positions is not None:  # one pass: each page's Well ID column
        for (t, pg), pos in zip(lines, positions):
            if pos is not None and WELL_RE.match(t):
                well_col[pg] = min(well_col.get(pg, pos[0]), pos[0])

    def _in_well_column(k: int) -> bool:
        if positions is None or positions[k] is None:
            return True  # no geometry for this line: text order only
        col = well_col.get(lines[k][1])
        return col is None or positions[k][0] <= col + _WELL_COLUMN_TOLERANCE

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
                    # Pair the k-th "Well ID" text header with the k-th one
                    # found by position only when both sides see the same
                    # number of headers on the page; otherwise the pairing
                    # is unknowable and the table is not read.
                    cols = (cols_on_page[k] if len(cols_on_page) == text_headers.get(page, 0)
                            else [])
                    # The positions give the ORDER. Every column this header's
                    # own text names must be among them, or the text header was
                    # paired with another header's words (two on one page).
                    # (The text can name FEWER: a wrapped label is sometimes
                    # extracted after the first data row.)
                    if not set(_text_columns(htext)) <= set(cols):
                        cols = []
                else:
                    cols = _text_columns(htext)
                new_layout = layout_for_columns(cols)
                new_adj_labeled = bool(_ADJ_LABEL_RE.search(htext))
                new_alias_labeled = bool(_ALIAS_LABEL_RE.search(htext))
                # A repeated header (one per page) continues the same table;
                # so does a repeat of the same unrecognized column set, so one
                # unknown table is reported once, not once per page.
                if (new_layout != layout or new_adj_labeled != adj_labeled
                        or new_alias_labeled != alias_labeled
                        or (new_layout is None and cols != header_cols)):
                    flush()
                    layout, header, header_cols = new_layout, htext[:200], cols
                    adj_labeled = new_adj_labeled
                    alias_labeled = new_alias_labeled
                i = j
                continue
            # Another table that also starts "Well ID" (CO data, liquid
            # levels): the gas table has ended. Its rows are not gas readings.
            flush()
            layout, header, header_cols = None, NON_GAS_HEADER, None
            adj_labeled = True
            alias_labeled = False
            i = j
            continue
        alias = None
        alias_start = alias_labeled and _is_alias_row_start(lines, i)
        if alias_start and not _in_well_column(i):
            out.off_column_rows += 1
            alias_start = False
        if alias_start:
            alias = lines[i + 1][0] if lines[i + 1][0] != BLANK_CELL else None
            dt_i = i + 2
            j = i + 3
        elif _is_row_start(lines, i):
            dt_i = i + 1
            j = i + 2
        else:
            i += 1
            continue
        dt = lines[dt_i][0]
        raw_well = text
        adj = False
        if j < n and lines[j][0] == "ADJ":
            adj = True
            j += 1
        elif not adj_labeled and partner is not None:
            # Unlabeled ADJ: see _ADJ_LABEL_RE. The partner is the last row
            # main would have emitted, so a row this reader adds (gas-only, or
            # read past a "---") never changes a main row's ADJ flag.
            adj = (not partner.adj and partner.dt == dt
                   and partner.raw_well_id.rstrip("*") == raw_well.rstrip("*"))
        row_layout = layout
        joined = False
        sp = split.get(page) if split and layout is None and header_cols is not None else None
        if sp is not None and tuple(header_cols) == sp.left_cols:
            # A split table (see _split_plan): the left half's cells, then the
            # right half's row at the same coordinate on the paired page. A
            # row whose left cells are not all read is left to the reject
            # below, exactly as before.
            left, j_left, _, _, _ = _read_values(lines, j, len(sp.left_cols))
            if len(left) == len(sp.left_cols):
                coord = (sp.coords[dt_i - page_first[page]]
                         if sp.coords is not None and lines[dt_i][1] == page else None)
                if on_split_row is not None:
                    on_split_row(page, coord)
                right = sp.right_cells(coord)
                j = j_left
                if right is None:
                    out.split_unjoined_rows += 1
                    cur_unjoined["n"] += 1
                    cur_unjoined["first"] = cur_unjoined["first"] or page
                    cur_unjoined["last"] = page
                    i = j
                    continue
                cells = dict(zip(sp.left_cols, left))
                cells.update(right)
                row_layout = sp.layout
                nums = [cells[c] for c in row_layout]
                width, mixed_ok = len(nums), frozenset()
                main_len, last_line, joined = len(nums), None, True
        if not joined:
            width = len(layout) if layout else len(COLUMNS)
            mixed_ok = (frozenset(layout.index(c) for c in ("well_pres", "header_pres")
                                  if c in layout) if layout else frozenset())
            nums, j, main_len, refused, last_line = _read_values(lines, j, width, mixed_ok,
                                                                 mixed_line_ok)
            if refused:
                out.unaligned_cells += 1
        if (not joined and layout is not None and main_len < len(nums) < width
                and any(v is not None for v in nums)):
            # The row needed this reader (a "---" or a shared line such as
            # "100 150") but doesn't fill every column: some cell printed
            # nothing at all, so where the later cells belong is unknown.
            # Keep only the cells main's reader took (the positions main
            # trusted). An all-"---" row is left to the blank_rows count.
            nums = nums[:main_len]
            if len(nums) <= layout.index("temp"):
                out.ambiguous_rows += 1  # no reading whose positions are known
                i = j
                continue
        if row_layout is None:
            # Gas-shaped row with no recognized layout: reject, never guess.
            # Under a gas header we didn't recognize, every row with the four
            # gas columns counts (the 250pp variant prints 5 numbers); with no
            # gas header at all, only rows long enough to have been read as gas
            # readings before this guard existed.
            unknown_gas = header not in ("", NON_GAS_HEADER)
            # The cells main's reader took (one bare number per line, stopping
            # at the first other line): a non-gas table's blank cells, shared
            # lines, or numbers read past them, must not make it look gas.
            n_numeric = main_len
            if n_numeric >= (4 if unknown_gas else 6):
                cur_rejected["n"] += 1
                cur_rejected["first"] = cur_rejected["first"] or page
                cur_rejected["last"] = page
                cur_rejected["reason"] = (
                    "rows under a non-gas table header" if header == NON_GAS_HEADER
                    else "column layout not recognized" if header
                    else "rows before any recognized header")
        elif nums and all(v is None for v in nums):
            out.blank_rows += 1  # every cell "---": no reading to emit
        elif not nums:
            pass  # no cells at all (as on main: not a reading, not counted)
        elif len(nums) < 4 or None in nums[:4]:
            out.partial_rows += 1  # values, but not all four gas columns
        elif len(nums) <= row_layout.index("temp"):
            out.truncated_rows += 1  # cells ended before Temp: not a blank Temp
        else:
            # The four gas columns are present and the Temp position was read.
            # Temp (or any later column) may be blank ("---"): a gas-only
            # reading is kept, with no temperature (Trisha's ruling
            # 2026-10-19). `valid` still gates on the gas sum.
            r = WOIReading(
                well_id=canonicalize(raw_well, alias_map),
                raw_well_id=raw_well, dt=dt, adj=adj, page=page,
                alias_as_printed=alias,
            )
            for ci, v in enumerate(nums):
                setattr(r, row_layout[ci], v)
            cur_rows.append(r)
            if on_last_cell is not None and last_line is not None and mixed_ok and (
                    width - 1) in mixed_ok and len(nums) == width:
                on_last_cell(last_line)  # a reference cell: emitted row, pressure column
            if joined:
                # Every cell of a joined row is placed by its position on the
                # page: it is a full row for the unlabeled-ADJ pairing and the
                # negative-flow check of its own table (all rows of which are
                # joined rows).
                out.split_joined_rows += 1
                partner = r
                if r.flow is not None:
                    cur_flows.append(r.flow)
            else:
                if main_len > layout.index("temp"):  # main emitted this row
                    partner = r
                if "flow" in layout and main_len > layout.index("flow"):
                    cur_flows.append(r.flow)
                else:
                    cur_added.append(r)
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


def _line_positions(page, text_lines: list) -> list:
    """For each text line, (start, first word's right edge, first word's
    width per character), all measured along the line's own text direction
    (rotation-proof), from one text page's dict and words. All None when the dict lines don't match the plain-text
    lines one for one (the text page uses get_text()'s own flags, so they
    match exactly); the edge is None when the first word can't be matched."""
    out = []
    try:
        tp = page.get_textpage(flags=fitz.TEXTFLAGS_TEXT)  # what get_text() uses
        first_word: dict = {}
        for wx0, wy0, wx1, wy1, wtxt, bno, lno, wno in tp.extractWORDS():
            if (bno, lno) not in first_word or wno < first_word[(bno, lno)][0]:
                first_word[(bno, lno)] = (wno, wtxt, (wx0, wy0, wx1, wy1))
        for bno, b in enumerate(tp.extractDICT()["blocks"]):
            for lno, ln in enumerate(b.get("lines", [])):
                txt = "".join(sp["text"] for sp in ln["spans"]).strip()
                if not txt:
                    continue
                c, s_ = ln["dir"]
                ox, oy = ln["spans"][0]["origin"]
                edge = char_w = None
                fw = first_word.get((bno, lno))
                if fw is not None and fw[1] == txt.split()[0]:
                    x0, y0, x1, y1 = fw[2]
                    along = [x * c + y * s_ for x in (x0, x1) for y in (y0, y1)]
                    edge = max(along)
                    char_w = (edge - min(along)) / len(fw[1])
                out.append((txt, (ox * c + oy * s_, edge, char_w)))
    except Exception:  # noqa: BLE001 — no geometry: text order only
        return [None] * len(text_lines)
    if [t for t, _ in out] != text_lines:
        return [None] * len(text_lines)
    return [x for _, x in out]


# A line that starts with a number and goes on with text: the only lines the
# geometry check reads. Pages without one (and without an Alias label) skip
# the geometry extraction.
_MIXED_LINE_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?[ \t]+(?!-?\d|---)\S", re.M)


def _pdf_read(pdf_path: str, with_positions: bool = False):
    """Linearized text of every page, plus {page: header columns} for pages
    that carry a "Well ID" header. Any page MuPDF cannot read (e.g. "cannot
    find page N in page tree" on a damaged page tree) is skipped and returned:
    one bad page must not cost the whole report. With `with_positions`, also
    returns each line's geometry (_line_positions) for pages that carry an
    Alias label or a number-then-text line (_MIXED_LINE_RE); None elsewhere."""
    doc = fitz.open(pdf_path)
    try:
        lines, skipped, headers, positions = [], [], {}, []
        for p in range(len(doc)):
            try:
                page = doc[p]
                text = page.get_text()
            except Exception:  # noqa: BLE001 — a damaged page; keep the rest
                skipped.append(p + 1)
                continue
            if _WELL_ID_TEXT_RE.search(text):
                try:
                    headers[p + 1] = _page_header_columns(page.get_text("words"))
                except Exception:  # noqa: BLE001 — no geometry: its header reads as unknown
                    headers[p + 1] = []
            page_lines = [s for s in (ln.strip() for ln in text.split("\n")) if s]
            lines.extend((s, p + 1) for s in page_lines)
            if with_positions:
                positions.extend(_line_positions(page, page_lines)
                                 if ("Alias" in text or _MIXED_LINE_RE.search(text))
                                 else [None] * len(page_lines))
        if with_positions:
            return lines, skipped, headers, positions
        return lines, skipped, headers
    finally:
        doc.close()


def _pdf_lines(pdf_path: str) -> list[tuple[str, int]]:
    return _pdf_read(pdf_path)[0]


# ---------------------------------------------------------------------------
# Split gas tables (coder:woi-split-table-join, ADR 005 addendum 2026-10-20)
# ---------------------------------------------------------------------------
#
# Some reports print a gas table in two page ranges. The LEFT half (Well ID,
# [Alias], Date/Time, [ADJ], CH4, CO2, O2, Balance[, Diff. Press]) runs on n
# pages; the RIGHT half (Temp, Flow, the pressures[, Diff. Press], then
# Comments and sometimes pump counts) runs on the n pages right after it and
# prints no Well ID and no timestamp. Read alone, the left half is rejected
# ("column layout not recognized"). Left page k pairs with right page k + n,
# and a left row with the right-page cells printed at the same coordinate
# across the text (its baseline in the text's own frame). Measured
# 2026-10-20 on the four such reports in the corpus: every left row's
# baseline matches a right row's exactly.
#
# The right half's column ORDER comes from its labels' positions, never the
# text order (one report extracts "Temp." first but prints Diff. Press
# first). Each right column runs from its label's start to the next label's
# start along the text, and a cell must also line up with its column's other
# cells on the page (centre, left or right edge, within _SPLIT_ALIGN_TOLERANCE
# of a character width): a comment that starts with a number sits at the
# Comments position and lines up with nothing. Comments, valve settings and
# pump counts are not read (Trisha's 2026-10-19 ruling): only the measurement
# columns through Header Pres.
#
# Anything that doesn't fit stays rejected: a right range that isn't exactly
# n pages, a page without readable geometry, right labels that don't combine
# with the left columns into a known layout (LAYOUTS, compared as a set), a
# left row with no right row or two left rows on one right row (the whole
# page is then not joined), a right row missing a cell. Those rows are
# counted in split_unjoined_rows and listed as a RejectedTable.

_SPLIT_GAS = frozenset(("ch4", "co2", "o2", "balance"))
_SPLIT_LEFT_OK = _SPLIT_GAS | {"diff_press"}
# Baselines of a left row and its right row: equal in every pair measured
# (max difference 0.0); a wrapped comment line sits ~11 pt away.
_SPLIT_ROW_TOLERANCE = 0.5
# A cell's centre, start or end along the text vs the median of its column's
# bare cells on that page, in character widths. Measured: every cell within
# 0.03 (centred columns) or exactly 0 (right-aligned columns).
_SPLIT_ALIGN_TOLERANCE = 0.3
# A column needs this many bare cells on a page to set its alignment there;
# otherwise the whole right range's cells set it.
_SPLIT_MIN_REF_CELLS = 3
_DT_ANYWHERE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}")
SPLIT_UNJOINED_REASON = "split table: rows not joined to a right-half row"


def _is_split_left(cols) -> bool:
    """A gas header that reads the four gas columns (and maybe Diff. Press)
    and nothing else: a left half candidate. Pure."""
    return (cols is not None and len(set(cols)) == len(cols)
            and _SPLIT_GAS <= set(cols) <= _SPLIT_LEFT_OK)


@dataclass
class _FrameLine:
    text: str
    across: float        # baseline, across the text (the row)
    dir: tuple           # the line's text direction (cos, sin), rounded
    words: list          # [(start, end, text)] along the text


def _frame_lines(page) -> Optional[list]:
    """Every text line of a page, in its own text direction (rotation-proof):
    `across` is -ox*sin + oy*cos of the first span's origin. None when a
    line's words don't match its text one for one."""
    tp = page.get_textpage(flags=fitz.TEXTFLAGS_TEXT)
    words: dict = {}
    for x0, y0, x1, y1, t, bno, lno, wno in tp.extractWORDS():
        words.setdefault((bno, lno), []).append((wno, x0, y0, x1, y1, t))
    out = []
    for bno, b in enumerate(tp.extractDICT()["blocks"]):
        for lno, ln in enumerate(b.get("lines", [])):
            txt = "".join(sp["text"] for sp in ln["spans"]).strip()
            if not txt:
                continue
            c, s_ = ln["dir"]
            ox, oy = ln["spans"][0]["origin"]
            ws = []
            for _, x0, y0, x1, y1, t in sorted(words.get((bno, lno), [])):
                along = [x * c + y * s_ for x in (x0, x1) for y in (y0, y1)]
                ws.append((min(along), max(along), t))
            if [w[2] for w in ws] != txt.split():
                return None
            out.append(_FrameLine(txt, -ox * s_ + oy * c, (round(c, 3), round(s_, 3)), ws))
    return out


def _cell_tokens(fl: _FrameLine) -> list:
    """The leading cell tokens of a line (numbers and BLANK_CELL), up to its
    first other token: the rest of the line is a comment."""
    out = []
    for w in fl.words:
        if not _cell(w[2])[0]:
            break
        out.append(w)
    return out


def _right_columns(flines: list) -> Optional[list]:
    """The right half's label columns on one page, in order along the text:
    [(column name or None, start)]. None if the page prints no Temp label;
    [] if its labels can't be read (two Temp labels on different rows, a
    label naming two columns, a non-measurement label before or between the
    measurement ones). Label words one space apart form a phrase; phrases
    that overlap along the text form a column (a wrapped label)."""
    anchors = [fl for fl in flines if not _cell_tokens(fl)
               and any(_LABEL_WORDS["temp"].match(w[2]) for w in fl.words)]
    if not anchors:
        return None
    base = anchors[0].across
    if any(abs(a.across - base) > _SPLIT_ROW_TOLERANCE for a in anchors):
        return []
    phrases = []
    for fl in flines:
        if _cell_tokens(fl) or not (base - _HEADER_BAND_ABOVE <= fl.across
                                    <= base + _HEADER_BAND_BELOW):
            continue
        cur = None
        for a0, a1, t in fl.words:
            if cur is not None and a0 - cur[1] <= (a1 - a0) / max(len(t), 1):
                cur[1] = a1
                cur[2].append((fl.across, a0, t))
            else:
                cur = [a0, a1, [(fl.across, a0, t)]]
                phrases.append(cur)
    phrases.sort(key=lambda p: p[0])
    clusters: list = []
    for a0, a1, ws in phrases:
        if clusters and a0 <= clusters[-1][1]:
            clusters[-1][1] = max(clusters[-1][1], a1)
            clusters[-1][2].extend(ws)
        else:
            clusters.append([a0, a1, list(ws)])
    cols = []
    for a0, _, ws in clusters:
        text = " ".join(t for _, _, t in sorted(ws))
        names = [c for c, rx in _LABEL_RES if rx.search(text)]
        if len(names) > 1:
            return []
        cols.append((names[0] if names else None, a0))
    names = [c for c, _ in cols]
    meas = [c for c in names if c is not None]
    # measurement columns first, then only others (Comments, pump counts)
    if (not meas or None in names[:len(meas)] or len(set(meas)) != len(meas)
            or names[:len(meas)] != meas):
        return []
    return cols


@dataclass
class _RightPage:
    flines: list
    columns: Optional[list]   # this page's own labels (_right_columns)


def _right_page(page) -> Optional[_RightPage]:
    """A page that can be a right half: one text direction, no Well ID, no
    well-ID line, no timestamp, and at least one line of cells. Else None."""
    flines = _frame_lines(page)
    if not flines or len({fl.dir for fl in flines}) != 1:
        return None
    for fl in flines:
        if (WELL_RE.match(fl.text) or _HEADER_START_RE.match(fl.text)
                or _DT_ANYWHERE_RE.search(fl.text)):
            return None
    if not any(_cell_tokens(fl) for fl in flines):
        return None
    return _RightPage(flines, _right_columns(flines))


def _assign_cells(rp: _RightPage, columns: list) -> list:
    """[(across, column name, token, line is bare)] for every cell token that
    falls in a measurement column's band (its label start to the next
    label's start). Tokens in other columns, or before the first, are not
    cells of the row."""
    out = []
    starts = [a for _, a in columns]
    for fl in rp.flines:
        toks = _cell_tokens(fl)
        bare = len(fl.words) == 1
        for a0, a1, t in toks:
            mid = (a0 + a1) / 2
            for ci, (name, start) in enumerate(columns):
                end = starts[ci + 1] if ci + 1 < len(starts) else float("inf")
                if start <= mid < end:
                    if name is not None:
                        out.append((fl.across, name, (a0, a1, t), bare))
                    break
    return out


def _alignment_refs(cells: list) -> dict:
    """{column: (median centre, median start, median end)} of the bare
    number cells, for columns with at least _SPLIT_MIN_REF_CELLS of them."""
    by: dict = {}
    for _, name, (a0, a1, t), bare in cells:
        if bare and t != BLANK_CELL:
            by.setdefault(name, []).append((a0, a1))
    refs = {}
    for name, v in by.items():
        if len(v) >= _SPLIT_MIN_REF_CELLS:
            refs[name] = tuple(sorted(x)[len(x) // 2] for x in (
                [(a + b) / 2 for a, b in v], [a for a, _ in v], [b for _, b in v]))
    return refs


def _right_rows(cells: list, refs: dict, meas: list) -> list:
    """[(across, {column: value} or None)]: the right half's rows. A row is
    the aligned cells at one baseline; it is None (not joinable) unless every
    measurement column holds exactly one cell there."""
    aligned = []
    for across, name, (a0, a1, t), _ in cells:
        ref = refs.get(name)
        if ref is None:
            continue
        cw = (a1 - a0) / max(len(t), 1)
        if any(abs(x - r) <= _SPLIT_ALIGN_TOLERANCE * cw
               for x, r in zip(((a0 + a1) / 2, a0, a1), ref)):
            aligned.append((across, name, t))
    aligned.sort(key=lambda c: c[0])
    groups: list = []
    for c in aligned:
        if groups and c[0] - groups[-1][-1][0] <= _SPLIT_ROW_TOLERANCE:
            groups[-1].append(c)
        else:
            groups.append([c])
    rows = []
    for g in groups:
        names = [n for _, n, _ in g]
        vals = None
        if sorted(names) == sorted(meas):
            vals = {n: _cell(t)[1] for _, n, t in g}
        rows.append((g[0][0], vals))
    return rows


@dataclass
class _SplitPage:
    """One left page of a split table and its right half's rows."""
    left_cols: tuple
    layout: list              # the joined row's columns (a LAYOUTS entry)
    right_page: int
    coords: Optional[list]    # baseline of each line of the left page, or None
    rows: list                # _right_rows
    ok: bool = True           # set by the pairing dry run (_settle_split)

    def find(self, coord) -> Optional[int]:
        """The one right row at this baseline, else None (none, or two)."""
        if coord is None:
            return None
        hits = [k for k, (a, _) in enumerate(self.rows) if abs(a - coord) <= _SPLIT_ROW_TOLERANCE]
        return hits[0] if len(hits) == 1 else None

    def right_cells(self, coord) -> Optional[dict]:
        if not self.ok:
            return None
        k = self.find(coord)
        return None if k is None else self.rows[k][1]


def _plan_one(doc, left_cols: tuple, first: int, last: int, page_lines: dict,
              skipped: set) -> dict:
    """{left page: _SplitPage} for one candidate table on pages first..last
    (1-based), or {} when its right half isn't found."""
    n = last - first + 1
    right = list(range(last + 1, last + n + 1))
    if right[-1] > len(doc) or any(p in skipped for p in right):
        return {}
    rps = [_right_page(doc[p - 1]) for p in right]
    if any(rp is None for rp in rps):
        return {}
    labeled = [rp.columns for rp in rps if rp.columns is not None]
    if not labeled or any(not c for c in labeled):
        return {}
    meas = [c for c, _ in labeled[0] if c is not None]
    if any([c for c, _ in cols if c is not None] != meas for cols in labeled):
        return {}
    if set(meas) & set(left_cols):
        return {}
    layout = next((list(lay) for lay in LAYOUTS.values()
                   if set(lay) == set(left_cols) | set(meas)), None)
    if layout is None:
        return {}
    # A page without labels uses the nearest labeled page before it (else
    # the first labeled page): the right half is one table.
    cols_for, cur = [], None
    for rp in rps:
        cur = rp.columns if rp.columns is not None else cur
        cols_for.append(cur if cur is not None else labeled[0])
    cells = [_assign_cells(rp, cols) for rp, cols in zip(rps, cols_for)]
    range_refs = _alignment_refs([c for cs in cells for c in cs])
    # The right run must end here: a next page with a Temp label, or with
    # rows in these columns, means the page counts differ. (A next page of
    # other columns, e.g. pump counts and liquid depths, ends the run.)
    after = last + n + 1
    nxt = _right_page(doc[after - 1]) if after <= len(doc) and after not in skipped else None
    if nxt is not None and (nxt.columns is not None or any(
            v is not None for _, v in _right_rows(_assign_cells(nxt, cols_for[-1]),
                                                  range_refs, meas))):
        return {}
    plan = {}
    for k, p in enumerate(range(first, last + 1)):
        refs = dict(range_refs)
        refs.update(_alignment_refs(cells[k]))
        rows = _right_rows(cells[k], refs, meas)
        coords = None
        if p not in skipped:
            fl = _frame_lines(doc[p - 1])
            if (fl is not None and [x.text for x in fl] == page_lines.get(p)
                    and {x.dir for x in fl} == {rps[k].flines[0].dir}):
                coords = [x.across for x in fl]
        plan[p] = _SplitPage(tuple(left_cols), layout, right[k], coords, rows)
    return plan


def _split_plan(pdf_path: str, candidates: list, lines: list, skipped: list) -> dict:
    """{left page: _SplitPage} for every candidate split table whose right
    half is found. Never raises: a candidate that fails stays rejected."""
    page_lines: dict = {}
    for t, pg in lines:
        page_lines.setdefault(pg, []).append(t)
    plan: dict = {}
    try:
        doc = fitz.open(pdf_path)
    except Exception:  # noqa: BLE001 — no geometry: nothing is joined
        return {}
    try:
        for left_cols, first, last in candidates:
            try:
                plan.update(_plan_one(doc, left_cols, first, last, page_lines, set(skipped)))
            except Exception:  # noqa: BLE001 — this table stays rejected
                continue
    finally:
        doc.close()
    return plan


def _settle_split(plan: dict, seen: dict) -> None:
    """Page-level pairing verdict, from the dry run's row baselines (`seen`:
    {left page: [baseline]}): a page joins only if every left row has its
    own right row (one per row, none shared). Mutates `plan`."""
    for pg, sp in plan.items():
        ks = [sp.find(c) for c in seen.get(pg, [])]
        sp.ok = None not in ks and len(set(ks)) == len(ks)


def parse_gas_extraction_report(pdf_path: str, alias_map: Optional[dict] = None) -> GasExtractionParse:
    """Parse every Attachment-1 gas-extraction reading in the report, table by
    table, with each table's own column layout. Returns the readings plus the
    tables that were rejected (unknown layout / column-shift signature) and the
    pages that could not be read. Logs rejections and skipped pages (counts and
    page numbers only)."""
    lines, skipped, headers, positions = _pdf_read(pdf_path, with_positions=True)
    candidates: list = []
    out = _parse_table_lines(lines, alias_map, page_headers=headers, positions=positions,
                             on_split_candidate=candidates.append)
    plan = _split_plan(pdf_path, sorted(set(candidates)), lines, skipped) if candidates else {}
    if plan:
        # Split tables (see _split_plan): a dry run collects each left row's
        # baseline, which settles which pages pair cleanly; then the report
        # is parsed again with the join.
        seen: dict = {}
        _parse_table_lines_once(lines, alias_map, None, headers, positions, split=plan,
                                on_split_row=lambda pg, c: seen.setdefault(pg, []).append(c))
        _settle_split(plan, seen)
        out = _parse_table_lines(lines, alias_map, page_headers=headers, positions=positions,
                                 split=plan)
    out.skipped_pages = skipped
    if skipped:
        print(f"[woi_table_parser] skipped {len(skipped)} unreadable page(s): "
              f"{_page_ranges(skipped)}")
    for t in out.rejected_tables:
        print(f"[woi_table_parser] REJECTED gas table pp{t.first_page}-{t.last_page} "
              f"({t.n_rows} rows): {t.reason}")
    if (out.blank_rows or out.partial_rows or out.truncated_rows or out.ambiguous_rows
            or out.off_column_rows or out.unaligned_cells):
        print(f"[woi_table_parser] rows not emitted: {out.blank_rows} all blank, "
              f"{out.partial_rows} without all four gas columns, "
              f"{out.truncated_rows} ending before Temp, "
              f"{out.ambiguous_rows} with cells of unknown position; "
              f"{out.off_column_rows} Alias-table row starts outside the Well ID column; "
              f"{out.unaligned_cells} comment-line cells read as blank")
    if out.split_joined_rows or out.split_unjoined_rows:
        print(f"[woi_table_parser] split tables: {out.split_joined_rows} rows joined to "
              f"their right half, {out.split_unjoined_rows} not joined")
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


_SECONDARY_COLS = ("diff_press", "flow", "well_pres", "header_pres")


def merge_cross_table_duplicates(readings: list[WOIReading]) -> list[WOIReading]:
    """Collapse a reading a report prints in TWO tables (on different pages):
    same well, time, ADJ flag, gas columns and temperature, and no secondary
    column (diff press, flow, pressures) that disagrees. The most complete
    copy is kept, in first-seen order. In the Q3 2023 HOV WOI report, 234
    well/time readings appear in both its no-Diff.Press table and its full
    table; 232 of the 234 are such exact repeats, which would otherwise reach
    Measurements twice. Readings repeated on the SAME page, or
    whose secondary columns differ, are distinct records and are all kept.
    Pure; the parser's own output (parse_gas_extraction) is not changed."""
    groups: dict = {}
    order: list = []
    for r in readings:
        k = (r.well_id, r.dt, r.adj, r.ch4, r.co2, r.o2, r.balance, r.temp)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    out: list = []
    for k in order:
        g = groups[k]
        if len(g) == 1 or len({r.page for r in g}) != len(g) or any(
                len({getattr(r, c) for r in g if getattr(r, c) is not None}) > 1
                for c in _SECONDARY_COLS):
            out.extend(g)
            continue
        out.append(max(g, key=lambda r: sum(getattr(r, c) is not None for c in _SECONDARY_COLS)))
    return out


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
