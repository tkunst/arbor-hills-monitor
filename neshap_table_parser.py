"""
neshap_table_parser.py — table-aware extractor for GFL's Arbor Hills Semi-Annual
NESHAP reports (40 CFR Part 63 Subpart AAAA, ROP-N2688-2011).

WHY THIS EXISTS (see docs/decisions/033): the NESHAP reports carry structured
compliance data — Appendix A exceedance rosters, Appendix F enhanced-monitoring
and visual subsurface-oxidation inspection logs, plus report-level RCA/downwell
flags — that is NOT in any current public dataset (the wellfield-data-* release
is built only from WOI Status Reports + Gas-Extraction Exceedance filings; no
NESHAP `source_report` value exists in it). This module parses that data into a
NEW, separate dataset. Different documents, different data model, different
output than `woi_table_parser.py` (WOI Status Reports) and `egle_doc_parser.py`
(the generic LLM classifier) — do not conflate the three, and this module does
not touch either of them.

FEASIBILITY (verified against both real 2025 semi-annual reports before writing
this module, per docs/overnight-coder.md Step 3 — the OCR-risk gate this build
was staged behind): both reports have a full text layer, not scanned images.
`fitz` (PyMuPDF) `get_text()` returns real, densely tabular text for every
appendix used here, and a parse against both real PDFs matched every sanity-
check count in the handoff exactly (654 pressure + 38 temperature exceedance
readings for 2025 H2, 263R5 x29 / 328R x9 of the temperature readings, 28
enhanced-monitoring readings + 4 visual inspections for 263R5 in H2, etc.).

Appendix A ("Wellfield Exceedance Reports") linearizes as two same-shaped
tables sharing one divider page — a (Pressure) table then a (Temperature)
table, switched on a standalone "(Pressure)" / "(Temperature)" line. Each row:
    Well ID
    Date/Time              (MM/DD/YY HH:MM — always carries a time in both
                            real reports, unlike Appendix F below)
    Value                  (float, may be negative)
    [Duration]              (optional: "<1", "36+", a footnote-flagged "*64",
                            a bare int, or absent entirely when the well is
                            still mid-episode as of the report's cutoff)
A well is listed here because it exceeded the parameter's threshold at least
ONCE during the episode (pressure: zero/positive gauge pressure; temperature:
>145F, the NESHAP wellhead standard, or the well's own approved Higher
Operating Value). Not every printed row is itself an over-threshold reading —
an episode's closing/return-to-negative row is logged too, with its Duration
filled in — so `exceeded` is computed per row against the constant baseline
threshold, never assumed True just because the well appears in this table.

IMPORTANT: AHW272R4 legitimately appears in the Pressure table but never the
Temperature table, in EITHER report — it holds an approved 180F HOV, so its
wellhead temperature readings never exceed that raised threshold. An empty
272R4 temperature result is correct, not a parse miss (confirmed against both
real reports; see docs/overnight-coder-handoffs/neshap-exceedance-extraction.md).
This module does not model per-well HOV overrides — TEMPERATURE_LIMIT_F is the
one constant baseline stated in the report narrative, not a per-well value.

Appendix F ("Enhanced Monitoring Results") holds two further same-shaped
tables under one divider page: a numeric weekly-reading table (Methane / Oxygen
/ Gas Temperature / [Carbon Monoxide, optional], switched on a standalone
"Methane" header line) and a visual subsurface-oxidation inspection log
(Staff Person Name / Smoke / Ash / Damage, all Y-N, switched on a standalone
"Staff Person" line). **The date/time format is NOT consistent across the two
real reports** — 2025 H1 prints bare M/D/YYYY with no time; 2025 H2 prints
MM/DD/YY HH:MM. This was found by testing both real PDFs, not assumed; the
date regex here (`_DT_RE_APPENDIX_F`) accepts both shapes.

Report-level metadata (RCA-required, downwell-monitoring-conducted) is read
from the narrative body via a small set of known-phrasing regexes — deliberately
NOT general NLP, per the handoff ("capture the narrative facts... not free
text"). If the expected phrasing isn't found, the field comes back `None`
(unknown) rather than a guessed True/False: this module has only ever seen the
"not required" / "not conducted" phrasing in a real report (both real 2025
reports say no RCA was required and no downwell monitoring was conducted), so
there's no real exemplar to base a confident "True" detection on. A future
report that phrases this differently — or actually requires an RCA — should
surface as "couldn't determine," never a silently wrong guess.
`wells_enhanced_monitored` is NOT read from the narrative at all (it would
mean regexing "One (1) well was subject to..." / "Two (2) wells were subject
to..." into a count with no way to name the wells); instead it's the set of
well IDs that actually appear in the parsed Appendix F enhanced-monitoring
table — more robust, and it's actually checkable against the roster.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import fitz  # pymupdf

# --- Appendix A (Wellfield Exceedance Reports) -----------------------------

# Every real well ID observed in both reports (Appendix A AND Appendix F)
# carries at least one digit (AHEW0012, AHW272R4, AHC4W109, AHWWTS02, ...) —
# require one so an all-caps narrative/footnote word (e.g. a stray "AHEAD")
# can't false-match as a well ID. Confirmed against every distinct well ID
# in both real reports before tightening this (zero false negatives).
# NOTE: unlike woi_table_parser.WELL_RE, this does NOT tolerate trailing
# footnote asterisks (WOI's real specimens print them glued onto the well-ID
# token itself, e.g. "AHW272R4**"). Confirmed by a document-wide regex scan
# of both real NESHAP PDFs (every page, not just Appendix A/F) for any
# "AH[A-Z0-9]+\*+" token: zero matches in either report — NESHAP's footnote
# markers are always separate full-sentence lines (e.g. "*Higher operating
# value (HOV) requested for well 263R5..."), never suffixed onto a well ID.
# If a future report ever does suffix one, this regex would reject that row
# rather than mis-tag it — the same "skip rather than guess" posture as the
# rest of this module.
WELL_RE = re.compile(r"^AH(?=[A-Z0-9]*\d)[A-Z0-9]+$")
DT_RE_APPENDIX_A = re.compile(r"^\d{1,2}/\d{1,2}/\d{2}\s+\d{1,2}:\d{2}$")
FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
# Duration values seen in both real reports: "<1", a bare int ("1".."64"),
# "NN+", and a footnote-flagged "*NN". FLOAT_RE and DURATION_RE both match a
# bare small integer (e.g. "1") by design — that's not an ambiguity in
# practice: a Duration cell is always immediately preceded by that row's own
# Value (already consumed) and immediately followed by either the NEXT row's
# Well ID (never digit-only — WELL_RE above requires an "AH" prefix) or the
# end of the table, so there is no OTHER token shape a bare digit here could
# be confused with.
DURATION_RE = re.compile(r"^\*?<?\d+\+?$")

PRESSURE_LIMIT_INWC = 0.0    # in. H2O gauge pressure; exceedance = zero or positive.
TEMPERATURE_LIMIT_F = 145.0  # Landfill NESHAP wellhead temperature standard.
# HOV-approved wells (e.g. AHW272R4 at 180F) are NOT modeled here — this is
# the one constant baseline the report itself states; see module docstring.

# Loose physical-range sanity gates (mirror woi_table_parser.WOIReading.valid's
# CH4+CO2+O2+Balance~=100 gate) — catch gross column misalignment, not a
# precision check. Both real reports' readings comfortably clear these bounds
# (observed pressure: -47.28..120.55 "H2O; observed temperature: 22.5..161.6F);
# the margin is deliberately generous since these are a coarse defense against
# misalignment, not a claim about what's physically plausible for this site.
PRESSURE_PLAUSIBLE_RANGE_INWC = (-500.0, 500.0)
TEMPERATURE_PLAUSIBLE_RANGE_F = (-50.0, 400.0)


@dataclass
class ExceedanceReading:
    well_id: str
    reading_date: str           # "M/D/YY HH:MM" as printed
    parameter: str               # "pressure" | "temperature"
    value: float
    limit: float
    exceeded: bool
    duration: Optional[str]      # raw duration token as printed, if present
    page: int

    @property
    def plausible(self) -> bool:
        """Loose sanity gate against gross column misalignment — see the
        module-level PRESSURE_PLAUSIBLE_RANGE_INWC / TEMPERATURE_PLAUSIBLE_RANGE_F
        comment. Not a precision check."""
        lo, hi = (PRESSURE_PLAUSIBLE_RANGE_INWC if self.parameter == "pressure"
                   else TEMPERATURE_PLAUSIBLE_RANGE_F)
        return lo <= self.value <= hi


def _exceeded(parameter: str, value: float) -> bool:
    if parameter == "pressure":
        return value >= PRESSURE_LIMIT_INWC
    return value > TEMPERATURE_LIMIT_F


def _parse_appendix_a_lines(lines: list[tuple[str, int]]) -> list[ExceedanceReading]:
    """Pure line state-machine over Appendix A's two same-shaped tables.
    `lines` is [(text, page_number)], already sliced to the Appendix A..B page
    range. Factored out so it is unit-testable without a PDF (mirrors
    woi_table_parser._parse_lines).

    KNOWN LIMIT (shared with woi_table_parser._parse_lines, same design): a
    well ID must be IMMEDIATELY followed by its date/time line, and a
    reading's value must be IMMEDIATELY followed by (well ID, date/time)
    to end one row and start the next. Any single unexpected line wedged in
    between (a stray footnote marker, a page artifact) silently drops that
    row rather than guessing around it — "skip rather than guess" is the
    deliberate posture here (matching the sibling module), but it does mean
    a row can vanish with no signal beyond the total count coming up short.
    Neither real 2025 report exhibits this — both were verified to match the
    handoff's sanity-check counts exactly — but a future report with
    different formatting could trip it silently. There is no per-row "did I
    skip something" signal. `_find_divider_page`'s divider-page gate IS
    applied automatically by every PDF-facing entry point. `plausible` (on
    `ExceedanceReading`/`EnhancedMonitoringReading`) is NOT auto-applied
    anywhere in this module today — it's exposed as API surface for a future
    caller (e.g. the named CSV-build follow-on) to filter on, the same way
    `woi_table_parser.per_well_summary`'s `valid_only` consumes its sibling
    `WOIReading.valid`; this module has no analogous aggregation function to
    wire it into yet."""
    out: list[ExceedanceReading] = []
    mode: Optional[str] = None
    i, n = 0, len(lines)
    while i < n:
        text, page = lines[i]
        stripped = text.strip()
        if stripped == "(Pressure)":
            mode = "pressure"
        elif stripped == "(Temperature)":
            mode = "temperature"
        if not WELL_RE.match(stripped):
            i += 1
            continue
        if i + 1 >= n or not DT_RE_APPENDIX_A.match(lines[i + 1][0].strip()):
            i += 1
            continue
        if mode is None:
            # A well/date pair before any (Pressure)/(Temperature) marker has
            # been seen — shouldn't happen in a well-formed report; skip
            # rather than guess a parameter.
            i += 1
            continue
        well = stripped
        reading_date = lines[i + 1][0].strip()
        j = i + 2
        if j >= n or not FLOAT_RE.match(lines[j][0].strip()):
            i += 1
            continue
        value = float(lines[j][0])
        j += 1
        duration = None
        if j < n and DURATION_RE.match(lines[j][0].strip()):
            duration = lines[j][0].strip()
            j += 1
        out.append(ExceedanceReading(
            well_id=well, reading_date=reading_date, parameter=mode,
            value=value, limit=(PRESSURE_LIMIT_INWC if mode == "pressure" else TEMPERATURE_LIMIT_F),
            exceeded=_exceeded(mode, value), duration=duration, page=page,
        ))
        i = j
    return out


# --- Appendix F (Enhanced Monitoring Results) -------------------------------

# Appendix F dates are NOT consistently formatted across the two real reports:
# 2025 H1 prints bare "M/D/YYYY" (no time); 2025 H2 prints "MM/DD/YY HH:MM".
# Accept both.
DT_RE_APPENDIX_F = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2})?$")

_YN = {"Y": True, "N": False}

# Loose sanity gate for EnhancedMonitoringReading.plausible: methane/oxygen are
# percentages, gas temp uses the same wide physical range as Appendix A's
# temperature gate above. Observed in both real reports: methane 0.0..57.0,
# oxygen 0.0..21.8, gas temp 15.2..161.6F.
_PERCENT_RANGE = (0.0, 100.0)


@dataclass
class EnhancedMonitoringReading:
    well_id: str
    reading_date: str
    methane_pct: float
    oxygen_pct: float
    gas_temp_f: float
    co_ppm: Optional[float]     # often blank in the source
    page: int

    @property
    def plausible(self) -> bool:
        """Loose sanity gate against gross column misalignment — see
        _PERCENT_RANGE / TEMPERATURE_PLAUSIBLE_RANGE_F. Not a precision check."""
        lo_pct, hi_pct = _PERCENT_RANGE
        lo_t, hi_t = TEMPERATURE_PLAUSIBLE_RANGE_F
        return (lo_pct <= self.methane_pct <= hi_pct
                and lo_pct <= self.oxygen_pct <= hi_pct
                and lo_t <= self.gas_temp_f <= hi_t)


@dataclass
class VisualInspection:
    well_id: str
    reading_date: str
    staff_name: str
    smoke_observed: bool
    ash_observed: bool
    damage_observed: bool
    page: int


def _parse_appendix_f_lines(
    lines: list[tuple[str, int]],
) -> tuple[list[EnhancedMonitoringReading], list[VisualInspection]]:
    """Pure line state-machine over Appendix F's two same-shaped tables.
    `lines` is [(text, page_number)], already sliced to the Appendix F..G page
    range (or F..end-of-doc; there is no Appendix G in either real report).

    KNOWN LIMIT: same "well ID must be immediately followed by its date" /
    "skip rather than guess" posture as `_parse_appendix_a_lines` — see that
    function's docstring. Applies here identically."""
    enhanced: list[EnhancedMonitoringReading] = []
    visual: list[VisualInspection] = []
    mode: Optional[str] = None
    i, n = 0, len(lines)
    while i < n:
        text, page = lines[i]
        stripped = text.strip()
        if stripped == "Methane":
            mode = "enhanced"
        elif stripped == "Staff Person":
            mode = "visual"
        if not WELL_RE.match(stripped):
            i += 1
            continue
        if i + 1 >= n or not DT_RE_APPENDIX_F.match(lines[i + 1][0].strip()):
            i += 1
            continue
        well = stripped
        reading_date = lines[i + 1][0].strip()
        j = i + 2
        if mode == "enhanced":
            nums: list[float] = []
            while j < n and len(nums) < 4 and FLOAT_RE.match(lines[j][0].strip()):
                nums.append(float(lines[j][0]))
                j += 1
            if len(nums) >= 3:  # Methane, Oxygen, Gas Temp required; CO optional
                enhanced.append(EnhancedMonitoringReading(
                    well_id=well, reading_date=reading_date,
                    methane_pct=nums[0], oxygen_pct=nums[1], gas_temp_f=nums[2],
                    co_ppm=(nums[3] if len(nums) > 3 else None), page=page,
                ))
            i = j
            continue
        elif mode == "visual":
            staff_tokens: list[str] = []
            k = j
            while (k < n and lines[k][0].strip() not in ("Y", "N")
                   and not WELL_RE.match(lines[k][0].strip())):
                staff_tokens.append(lines[k][0].strip())
                k += 1
            yn: list[str] = []
            while k < n and len(yn) < 3 and lines[k][0].strip() in ("Y", "N"):
                yn.append(lines[k][0].strip())
                k += 1
            if len(yn) == 3:
                visual.append(VisualInspection(
                    well_id=well, reading_date=reading_date,
                    staff_name=" ".join(staff_tokens),
                    smoke_observed=_YN[yn[0]], ash_observed=_YN[yn[1]],
                    damage_observed=_YN[yn[2]], page=page,
                ))
                i = k
                continue
            i += 1
            continue
        else:
            i += 1
            continue
    return enhanced, visual


# --- Report-level metadata ---------------------------------------------------

# Both real reports (2025 H1 + H2) use this exact "not required"/"not
# conducted" phrasing; neither has ever been observed to require an RCA or
# conduct downwell monitoring, so there is no real exemplar for a confident
# True detection. See module docstring: unmatched -> None (unknown), not a guess.
_RCA_NOT_REQUIRED_RE = re.compile(
    r"no root cause analysis forms?\s+(?:were|are)\s+required", re.IGNORECASE)
# KNOWN PRECISION LIMIT: "downwell_monitoring_conducted" is asking a fact
# question (did it happen), but this regex also accepts "...was/is required"
# phrasing as evidence of False. Those are, strictly, two different claims —
# "not required" is a statement about the compliance obligation, not a direct
# statement that nothing was done. The two real reports happen to phrase the
# same underlying situation differently (H1: "was conducted"; H2: "was
# required") with no report ever separately claiming "not required, but we
# did it anyway" — so both are treated as equivalent evidence here. A future
# report phrased as "downwell monitoring was required but has not yet been
# conducted" would NOT match this regex (the literal "no ... monitoring
# was/is required" substring isn't present), so that active-gap case doesn't
# get mis-read as "not conducted" — but a report that separately volunteers
# monitoring beyond what's required, while still stating none was required,
# would read as this regex's False when the truer answer is True/unknown.
# No real report has shown that shape; if one does, this needs revisiting.
_DOWNWELL_NOT_CONDUCTED_RE = re.compile(
    r"no down\s?well monitoring (?:was|is)\s+(?:conducted|required)", re.IGNORECASE)
_TRANSMITTAL_DATE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2},\s*\d{4})")
_REPORTING_PERIOD_RE = re.compile(
    r"For the Reporting Period\s*\n?\s*(.+?)\s*\n", re.IGNORECASE)


@dataclass
class ReportMetadata:
    reporting_period: Optional[str]
    transmittal_date: Optional[str]
    rca_required: Optional[bool]                  # None = couldn't determine
    downwell_monitoring_conducted: Optional[bool]  # None = couldn't determine
    wells_enhanced_monitored: list[str] = field(default_factory=list)


def _rca_required(full_text: str) -> Optional[bool]:
    if _RCA_NOT_REQUIRED_RE.search(full_text):
        return False
    return None


def _downwell_monitoring_conducted(full_text: str) -> Optional[bool]:
    if _DOWNWELL_NOT_CONDUCTED_RE.search(full_text):
        return False
    return None


def parse_report_metadata(
    pages: list[str], enhanced: list[EnhancedMonitoringReading]
) -> ReportMetadata:
    """Pure: `pages` is the list of per-page text (page 1 first), `enhanced` is
    Appendix F's enhanced-monitoring readings (for the well roster)."""
    full_text = "\n".join(pages)
    transmittal = _TRANSMITTAL_DATE_RE.search(pages[0]) if pages else None
    period = _REPORTING_PERIOD_RE.search(full_text)
    wells = sorted({r.well_id for r in enhanced})
    return ReportMetadata(
        reporting_period=(period.group(1) if period else None),
        transmittal_date=(transmittal.group(1) if transmittal else None),
        rca_required=_rca_required(full_text),
        downwell_monitoring_conducted=_downwell_monitoring_conducted(full_text),
        wells_enhanced_monitored=wells,
    )


# --- PDF-facing entry points --------------------------------------------------

def _page_texts(pdf_path: str) -> list[str]:
    doc = fitz.open(pdf_path)
    try:
        return [doc[p].get_text() for p in range(len(doc))]
    finally:
        doc.close()


# A real divider page in both reports has exactly 2 non-blank lines (e.g.
# "APPENDIX A" / "WELLFIELD EXCEEDANCE REPORTS"); the real table-of-contents
# page has 26. This threshold is a generous multiple of the observed divider
# size, so it tolerates a couple of extra lines (a running header, a page
# number) a future report's divider page might carry, while staying nowhere
# near the TOC's actual line count.
_DIVIDER_PAGE_MAX_NONBLANK_LINES = 6


def _find_divider_page(pages: list[str], letter: str, start_from: int = 0) -> Optional[int]:
    """Find the 'APPENDIX <letter>' divider page (0-indexed): a SHORT page
    (<= _DIVIDER_PAGE_MAX_NONBLANK_LINES non-blank lines) with a line that
    starts with 'APPENDIX <letter>'. Both conditions matter — checking only
    "does APPENDIX <letter> appear on this page" would false-match the
    report's own table of contents, which lists 'APPENDIX A' / 'APPENDIX B'
    back-to-back as consecutive TOC lines (found via the real reports, both
    of which have this TOC); the line-scan (not just a whole-page-prefix
    check) additionally tolerates a short leading line (e.g. a running header
    or page number) ahead of the 'APPENDIX <letter>' line itself."""
    marker = f"APPENDIX {letter}"
    for p in range(start_from, len(pages)):
        nonblank = [ln.strip() for ln in pages[p].split("\n") if ln.strip()]
        if len(nonblank) > _DIVIDER_PAGE_MAX_NONBLANK_LINES:
            continue
        if any(ln.startswith(marker) for ln in nonblank):
            return p
    return None


_ANY_APPENDIX_DIVIDER_RE = re.compile(r"^APPENDIX [A-Z]\b")


def _find_next_any_divider_page(pages: list[str], start_from: int) -> Optional[int]:
    """Find the next 'APPENDIX <any letter>' divider page (0-indexed), same
    short-page heuristic as _find_divider_page. Used as a SAFER closing
    boundary than "scan to end of document" when the specific next-expected
    letter's divider can't be found: Appendix A's temperature/pressure rows
    and Appendix F's H2-style enhanced-monitoring rows share the exact same
    MM/DD/YY HH:MM date shape, so if Appendix A's own closing divider (a
    genuinely different appendix, normally B) were ever missed, the old
    end-of-document fallback would let Appendix A's parser run straight into
    Appendix F and mis-parse its rows as spurious pressure/temperature
    exceedance readings — silently WRONG data, not just missing data.
    Stopping at ANY later appendix divider (not only the one specifically
    expected next) closes that gap without needing to know every real
    report's exact appendix ordering in advance."""
    for p in range(start_from, len(pages)):
        nonblank = [ln.strip() for ln in pages[p].split("\n") if ln.strip()]
        if len(nonblank) > _DIVIDER_PAGE_MAX_NONBLANK_LINES:
            continue
        if any(_ANY_APPENDIX_DIVIDER_RE.match(ln) for ln in nonblank):
            return p
    return None


def _section_end(pages: list[str], start: int, expected_next_letter: str) -> int:
    """Resolve the exclusive end-page index for a section starting at `start`
    (a divider page found by _find_divider_page): the NEAREST later appendix
    divider — whether or not it's the specifically-expected next letter —
    else end-of-document (only when this is genuinely the report's last
    appendix).

    Deliberately takes the MIN of the two searches rather than preferring the
    specific letter: an earlier version of this function preferred the
    expected letter's divider outright, which only helped when that letter
    was entirely absent — if the expected letter's divider existed further
    away than an intervening different-letter divider (e.g. a future report
    with an extra/reordered appendix between this section and its normal
    successor), that version would skip straight past the intervening
    divider and re-open the exact bleed-through this function exists to
    prevent (see _find_next_any_divider_page's docstring). Confirmed via a
    round-3 code review's empirical reproduction before this fix; the
    regression test below encodes that exact scenario."""
    specific = _find_divider_page(pages, expected_next_letter, start_from=start + 1)
    nearest_any = _find_next_any_divider_page(pages, start + 1)
    candidates = [x for x in (specific, nearest_any) if x is not None]
    return min(candidates) if candidates else len(pages)


def _lines_for_pages(pages: list[str], lo: int, hi: int) -> list[tuple[str, int]]:
    """lo inclusive, hi exclusive (0-based page indices)."""
    out: list[tuple[str, int]] = []
    for p in range(lo, hi):
        for ln in pages[p].split("\n"):
            s = ln.strip()
            if s:
                out.append((s, p + 1))
    return out


def parse_exceedances(pdf_path: str) -> list[ExceedanceReading]:
    """Parse every Appendix A pressure + temperature exceedance reading."""
    pages = _page_texts(pdf_path)
    a = _find_divider_page(pages, "A")
    if a is None:
        return []
    hi = _section_end(pages, a, "B")
    return _parse_appendix_a_lines(_lines_for_pages(pages, a, hi))


def parse_enhanced_monitoring(
    pdf_path: str,
) -> tuple[list[EnhancedMonitoringReading], list[VisualInspection]]:
    """Parse Appendix F's enhanced-monitoring readings + visual inspections."""
    pages = _page_texts(pdf_path)
    f = _find_divider_page(pages, "F")
    if f is None:
        return [], []
    hi = _section_end(pages, f, "G")
    return _parse_appendix_f_lines(_lines_for_pages(pages, f, hi))


def parse_report(pdf_path: str) -> tuple[
    list[ExceedanceReading], list[EnhancedMonitoringReading],
    list[VisualInspection], ReportMetadata,
]:
    """Parse everything this module extracts from one NESHAP report PDF:
    (exceedance readings, enhanced-monitoring readings, visual inspections,
    report-level metadata). One fitz open/close per call."""
    pages = _page_texts(pdf_path)
    a = _find_divider_page(pages, "A")
    exceedances: list[ExceedanceReading] = []
    if a is not None:
        exceedances = _parse_appendix_a_lines(
            _lines_for_pages(pages, a, _section_end(pages, a, "B")))
    enhanced: list[EnhancedMonitoringReading] = []
    visual: list[VisualInspection] = []
    f = _find_divider_page(pages, "F")
    if f is not None:
        enhanced, visual = _parse_appendix_f_lines(
            _lines_for_pages(pages, f, _section_end(pages, f, "G")))
    metadata = parse_report_metadata(pages, enhanced)
    return exceedances, enhanced, visual, metadata
