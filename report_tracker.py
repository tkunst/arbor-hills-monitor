"""
report_tracker.py — expected reports, arrivals, and the "missing and overdue"
log (ADR 071, coder:report-expectations-tracker, 2026-10-23).

The monitor logs everything that arrives; the project keeps hand-built lists of
what SHOULD arrive; nothing compared the two. This module does, for any number
of landfills (facility is a column everywhere; nothing here is keyed to Arbor
Hills). All of its tables live on the PRIVATE Sheet (GSHEET_ID_PRIVATE —
the Missing & Overdue Log carries FOIA request ids and collection priorities):

  Report Facilities / Expected Reports / Trigger Rules   hand-edited registries
  Report Arrivals                                        written here (hand rows allowed)
  Missing & Overdue Log                                  written here; FOIA + date columns hand-edited;
                                                         NEVER deleted; one row per instance_key, ever
  Report Tracker Review                                  proposed matches / unmatched periodic titles /
                                                         rejected hand rows, for the owner's weekly look
  Report Tracker Runs                                    one summary row per run (the briefing reads it)

The daily job (`run()`), in the ADR's order: load + validate the hand tables
(a bad row is logged, never guessed) -> expand every active schedule into
instances with due dates -> evaluate trigger rules against Measurements ->
match every arrival-stream record to an instance with a high-confidence
title/period rule (a `proposed` match is logged for a hand decision, never
applied) and fill `date_received` on the log when an item on it arrives ->
append every newly missing instance (due + grace < today, no arrival) exactly
once -> write the Runs row. The job is READ-ONLY on every existing tab and
fails closed when no usable private Sheet is configured.

Everything above `run()` is pure (dates in, dates out; strings in, strings
out) and unit-tested without the API. Period strings are `YYYY-MM` (month),
`YYYYBn` (bi-month, n = 1..6), `YYYYQn`, `YYYYHn`, `YYYY` (annual) and
`YYYY-MM-DD` (one-time / event). Gated on `report_tracking.enabled`.
"""
from __future__ import annotations

import calendar
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import review_flags as rf
import sheet_writer as sw

# ---------------------------------------------------------------------------
# Vocabulary (ADR 071, v1; extend by ADR amendment)
# ---------------------------------------------------------------------------

REPORT_TYPES = (
    "woi-status", "cj-semiannual", "cj-quarterly-probe", "cj-perimeter-rca",
    "nsps-neshap-semiannual", "rop-annual-certification", "rop-deviation-semiannual",
    "hov-request", "hov-approval", "hov-plan-approval", "hov-traditional-monthly",
    "hov-aoi-semiannual", "gas-extraction-exceedance", "downhole-profile",
    "neshap-24h-170f-co", "rule-433-notification", "hmp-quarterly",
    "annual-landfill-report", "ridgewood-monthly",
)
CADENCES = ("monthly", "bimonthly", "quarterly", "semiannual", "annual", "one-time", "event")
# Period kind each scheduled cadence expands to (event cadences never expand).
_KIND_FOR_CADENCE = {"monthly": "M", "bimonthly": "B", "quarterly": "Q",
                     "semiannual": "H", "annual": "Y", "one-time": "D"}
STREAMS = ("nsite", "wds", "hand-curated", "govqa", "ridgewood", "email-hand")
# Streams whose every record belongs to the one facility that lists them
# (a WDS site id / Barr's Ridge Wood page), so no facility text is needed.
SINGLE_SITE_STREAMS = ("wds", "ridgewood")
CONFIDENCE = ("primary", "secondary")

KIND_PROPOSED = "proposed_match"
KIND_UNMATCHED = "unmatched_periodic"
KIND_BAD_ROW = "bad_row"

REVIEW_SUBJECT = "[Arbor Hills monitor] Report tracker review (for Trisha only)"

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4}


_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/((?:19|20)\d\d)$")


def _ymd(s: str) -> Optional[date]:
    """`YYYY-MM-DD` (what this job writes) or `m/d/yyyy` (what Sheets hands
    back for a date someone TYPED into a hand-edited column, en_US) -> date;
    blank or anything else -> None. Callers that need to tell "blank" from
    "unreadable" check the raw string themselves (see run())."""
    s = (s or "").strip()
    if not s:
        return None
    m = _US_DATE_RE.match(s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _month_from_word(w: str) -> Optional[int]:
    """'Jan' / 'January' / 'Sept.' -> 1 / 1 / 9; None for a non-month."""
    w = (w or "").lower().rstrip(".")
    if len(w) < 3:
        return None
    for i, m in enumerate(_MONTHS, 1):
        if m.startswith(w) or (w == "sept" and m == "september"):
            return i
    return None


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Period:
    kind: str   # M B Q H Y D
    year: int
    n: int      # month 1-12 / bimonth 1-6 / quarter 1-4 / half 1-2 / 1 for Y / day-of-year for D
    day: Optional[date] = None  # kind D only

    def __str__(self) -> str:
        if self.kind == "M":
            return f"{self.year}-{self.n:02d}"
        if self.kind == "Y":
            return f"{self.year}"
        if self.kind == "D":
            assert self.day is not None
            return self.day.isoformat()
        return f"{self.year}{self.kind}{self.n}"

    def bounds(self) -> tuple[date, date]:
        """(first day, last day) of the period."""
        y, n = self.year, self.n
        if self.kind == "M":
            return date(y, n, 1), _month_end(y, n)
        if self.kind == "B":
            m1 = 2 * n - 1
            return date(y, m1, 1), _month_end(y, m1 + 1)
        if self.kind == "Q":
            m1 = 3 * n - 2
            return date(y, m1, 1), _month_end(y, m1 + 2)
        if self.kind == "H":
            m1 = 6 * n - 5
            return date(y, m1, 1), _month_end(y, m1 + 5)
        if self.kind == "Y":
            return date(y, 1, 1), date(y, 12, 31)
        assert self.day is not None
        return self.day, self.day

    def next(self) -> "Period":
        if self.kind == "D":
            raise ValueError("a one-time period has no next period")
        per = {"M": 12, "B": 6, "Q": 4, "H": 2, "Y": 1}[self.kind]
        if self.n < per:
            return Period(self.kind, self.year, self.n + 1)
        return Period(self.kind, self.year + 1, 1)

    def sort_key(self):
        return self.bounds()[0]


_PERIOD_RE = re.compile(r"^(\d{4})(?:([BQH])(\d)|-(\d{2})(?:-(\d{2}))?)?$")


def parse_period(s: str) -> Period:
    """`2025-10` | `2022B1` | `2025Q3` | `2025H2` | `2024` | `2023-06-12`.
    Raises ValueError on anything else."""
    s = (s or "").strip()
    if _US_DATE_RE.match(s):
        d = _ymd(s)
        if d is None:
            raise ValueError(f"unrecognized period {s!r}")
        return Period("D", d.year, d.timetuple().tm_yday, d)
    m = _PERIOD_RE.match(s)
    if not m:
        raise ValueError(f"unrecognized period {s!r}")
    y = int(m.group(1))
    if m.group(2):
        kind, n = m.group(2), int(m.group(3))
        if not 1 <= n <= {"B": 6, "Q": 4, "H": 2}[kind]:
            raise ValueError(f"period index out of range in {s!r}")
        return Period(kind, y, n)
    if m.group(5):
        d = date(y, int(m.group(4)), int(m.group(5)))
        return Period("D", y, d.timetuple().tm_yday, d)
    if m.group(4):
        mo = int(m.group(4))
        if not 1 <= mo <= 12:
            raise ValueError(f"month out of range in {s!r}")
        return Period("M", y, mo)
    return Period("Y", y, 1)


def period_for_months(y: int, m1: int, m2: int) -> Optional[Period]:
    """The period a month span [m1, m2] of one year names: a half, a quarter,
    a bi-month or a single month when the span is aligned; else None."""
    if not (1 <= m1 <= m2 <= 12):
        return None
    span = m2 - m1 + 1
    if span == 6 and m1 in (1, 7):
        return Period("H", y, 1 if m1 == 1 else 2)
    if span == 3 and (m1 - 1) % 3 == 0:
        return Period("Q", y, (m1 - 1) // 3 + 1)
    if span == 2 and m1 % 2 == 1:
        return Period("B", y, (m1 + 1) // 2)
    if span == 1:
        return Period("M", y, m1)
    if span == 12:
        return Period("Y", y, 1)
    return None


def period_ending_before(kind: str, d: date) -> Period:
    """The most recent period of `kind` whose last day is strictly before
    `d` (the period a report filed on `d` covers): a bi-monthly report
    filed 2022-03-15 covers 2022B1 (Jan-Feb); a quarterly one filed
    2024-01-15 covers 2023Q4."""
    size = {"M": 1, "B": 2, "Q": 3, "H": 6, "Y": 12}[kind]
    # First month of the period containing d, then one period earlier.
    m0 = ((d.month - 1) // size) * size + 1
    y, m = (d.year, m0 - size) if m0 > size else (d.year - 1, m0 - size + 12)
    if kind == "Y":
        return Period("Y", y, 1)
    return Period(kind, y, (m - 1) // size + 1)


# ---------------------------------------------------------------------------
# Due rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DueRule:
    kind: str                       # period_end | fixed | next_month_day | on | within
    days: int = 0                   # period_end / within (hours folded to days, see below)
    hours: int = 0                  # within:Nh keeps the hours too (for the note)
    fixed: tuple = ()               # ((mm, dd), ...) in calendar order
    day: int = 0                    # next_month_day
    on: Optional[date] = None       # one-time


_DUE_PE = re.compile(r"^period_end\+(\d+)d$")
_DUE_FIXED = re.compile(r"^fixed:((?:\d{2}-\d{2})(?:,\d{2}-\d{2})*)$")
_DUE_NMD = re.compile(r"^next_month_day:(\d{1,2})$")
_DUE_ON = re.compile(r"^on:(\d{4}-\d{2}-\d{2})$")
_DUE_WITHIN = re.compile(r"^within:(\d+)([hd])$")


def parse_due_rule(s: str) -> DueRule:
    """The ADR's small grammar; anything else raises ValueError (a bad row is
    logged and skipped at load, never guessed)."""
    s = (s or "").strip()
    if m := _DUE_PE.match(s):
        return DueRule("period_end", days=int(m.group(1)))
    if m := _DUE_FIXED.match(s):
        fixed = []
        for part in m.group(1).split(","):
            mm, dd = (int(x) for x in part.split("-"))
            if not (1 <= mm <= 12 and 1 <= dd <= calendar.monthrange(2001, mm)[1]):
                raise ValueError(f"bad fixed date in due_rule {s!r}")
            fixed.append((mm, dd))
        return DueRule("fixed", fixed=tuple(sorted(fixed)))
    if m := _DUE_NMD.match(s):
        d = int(m.group(1))
        if not 1 <= d <= 31:
            raise ValueError(f"bad day in due_rule {s!r}")
        return DueRule("next_month_day", day=d)
    if m := _DUE_ON.match(s):
        return DueRule("on", on=date.fromisoformat(m.group(1)))
    if m := _DUE_WITHIN.match(s):
        n, unit = int(m.group(1)), m.group(2)
        if unit == "h":
            return DueRule("within", days=max(1, -(-n // 24)), hours=n)
        return DueRule("within", days=n)
    raise ValueError(f"unrecognized due_rule {s!r}")


def _clamp_day(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def due_date(rule: DueRule, period: Period, trigger: Optional[date] = None) -> date:
    """The due date of one instance. `trigger` is the event date for
    `within:` rules (and the anchor for a scheduled rule applied to a
    triggered instance); scheduled rules anchor on the period's last day."""
    anchor = trigger or period.bounds()[1]
    if rule.kind == "period_end":
        # Always the PERIOD's end (a triggered instance's period is its
        # trigger year): "annually" means by the end of that year.
        return period.bounds()[1] + timedelta(days=rule.days)
    if rule.kind == "within":
        if trigger is None:
            raise ValueError("within: needs a trigger date")
        return trigger + timedelta(days=rule.days)
    if rule.kind == "next_month_day":
        y, m = (anchor.year, anchor.month + 1) if anchor.month < 12 else (anchor.year + 1, 1)
        return _clamp_day(y, m, rule.day)
    if rule.kind == "on":
        assert rule.on is not None
        return rule.on
    if rule.kind == "fixed":
        # The first fixed date STRICTLY after the anchor, rolling into next year.
        for year in (anchor.year, anchor.year + 1):
            for mm, dd in rule.fixed:
                cand = _clamp_day(year, mm, dd)
                if cand > anchor:
                    return cand
    raise ValueError(f"cannot apply due_rule {rule}")


# ---------------------------------------------------------------------------
# Match rules + period extractors
# ---------------------------------------------------------------------------

EXTRACTORS = (
    "year_quarter_words", "year_half_words", "month_year", "date_range",
    "month_range", "year_in_title", "date_in_title", "prior_month_from_date",
    "prior_bimonth_from_date", "prior_quarter_from_date", "prior_half_from_date",
    "prior_year_from_date", "record_period", "record_date", "auto",
)
# Extractors that read the period off the record's OWN date fields rather
# than its title. A period they produce outside a schedule's window is only
# ever PROPOSED (see match_record); a title-derived one is trusted.
FALLBACK_EXTRACTORS = ("prior_month_from_date", "prior_bimonth_from_date", "prior_quarter_from_date",
                       "prior_half_from_date", "prior_year_from_date", "record_date")
_AUTO_ORDER = ("date_range", "month_range", "year_quarter_words", "year_half_words",
               "month_year", "year_in_title")


@dataclass
class MatchRule:
    streams: tuple
    title_re: re.Pattern
    exclude_re: Optional[re.Pattern]
    doc_type: str
    period_from: tuple

    @property
    def text(self) -> str:
        return (f"stream={'|'.join(self.streams)}; title_regex={self.title_re.pattern}"
                + (f"; exclude_regex={self.exclude_re.pattern}" if self.exclude_re else "")
                + (f"; doc_type={self.doc_type}" if self.doc_type else "")
                + f"; period_from={','.join(self.period_from)}")


# Titles are searched up to this many characters (a hand-edited regex with
# nested quantifiers can take minutes on a long string; the period words are
# always near the front of a title).
TITLE_SEARCH_CHARS = 300
_PROBE_STRINGS = ("a" * 60 + "!", "Status Report " * 12 + "!", "A-" * 40 + "Report" + " x" * 20 + "!")


def _regex_is_fast(pat: "re.Pattern", budget_s: float = 0.5) -> bool:
    """False when `pat` takes longer than `budget_s` on a few generic
    pathological strings — the typo-shaped regex (`(\\w+\\s?)+Report$`) that
    would otherwise hang the daily job silently. Uses SIGALRM when
    available (main thread, POSIX); elsewhere the probe is skipped (True)."""
    import signal
    import threading
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        return True

    def _timeout(signum, frame):
        raise TimeoutError

    old = signal.signal(signal.SIGALRM, _timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, budget_s)
        try:
            for probe in _PROBE_STRINGS:
                pat.search(probe)
        except TimeoutError:
            return False
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, old)
    return True


def parse_match_rule(s: str) -> MatchRule:
    """`stream=nsite|hand-curated; title_regex=...; [exclude_regex=...;]
    [doc_type=...;] period_from=year_quarter_words[,prior_quarter_from_date]`.
    A small structure, not free code. Raises ValueError on a bad key, an
    unknown stream/extractor, a regex that does not compile, or one that
    backtracks catastrophically. A `;` cannot appear inside a regex (the
    rule is split on it)."""
    s = (s or "").strip()
    if not s:
        raise ValueError("empty match_rule")
    kv: dict = {}
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"match_rule part without '=': {part!r}")
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k not in ("stream", "title_regex", "exclude_regex", "doc_type", "period_from"):
            raise ValueError(f"unknown match_rule key {k!r}")
        kv[k] = v
    streams = tuple(x.strip() for x in kv.get("stream", "").split("|") if x.strip())
    if not streams or any(x not in STREAMS for x in streams):
        raise ValueError(f"match_rule stream must be one or more of {STREAMS}")
    if not kv.get("title_regex"):
        raise ValueError("match_rule needs title_regex")
    try:
        title_re = re.compile(kv["title_regex"], re.I)
        exclude_re = re.compile(kv["exclude_regex"], re.I) if kv.get("exclude_regex") else None
    except re.error as e:
        raise ValueError(f"match_rule regex does not compile: {e}") from e
    for pat in (title_re, exclude_re):
        if pat is not None and not _regex_is_fast(pat):
            raise ValueError("match_rule regex is too slow (catastrophic backtracking); simplify it")
    period_from = tuple(x.strip() for x in kv.get("period_from", "auto").split(",") if x.strip())
    if any(x not in EXTRACTORS for x in period_from):
        raise ValueError(f"match_rule period_from must use {EXTRACTORS}")
    return MatchRule(streams, title_re, exclude_re, kv.get("doc_type", ""), period_from)


@dataclass
class Record:
    """One arrival-stream record, normalized. `period_hint` is the stream's
    own period field when it has one (Ridge Wood's Month)."""
    stream: str
    ref: str
    title: str
    facility_text: str = ""
    doc_date: str = ""
    filed_date: str = ""
    doc_type: str = ""
    period_hint: str = ""


_YEAR = r"(20\d\d)"
_ORD = r"(first|second|third|fourth|1st|2nd|3rd|4th)"
_Q_PATTERNS = (
    re.compile(r"\bQ([1-4])\s*[-/' ]?\s*" + _YEAR + r"\b", re.I),
    re.compile(r"\b" + _YEAR + r"\s*[-/' ]?\s*Q([1-4])\b", re.I),
    re.compile(r"\b" + _YEAR + r"\s+" + _ORD + r"\s+Quarter\b", re.I),
    re.compile(r"\b" + _ORD + r"\s+Quarter\b(?:(?!20\d\d).){0,60}?" + _YEAR, re.I | re.S),
)
_H_WORD = r"(?:Semi[- ]?Ann\w*|Half)"
_H_PATTERNS = (
    re.compile(r"\b" + _YEAR + r"\s+" + _ORD + r"\s+" + _H_WORD, re.I),
    re.compile(r"\b" + _ORD + r"\s+" + _H_WORD + r"(?:(?!20\d\d).){0,60}?" + _YEAR, re.I | re.S),
    re.compile(r"\b" + _H_WORD + r"\s+" + _YEAR + r"\s+" + _ORD + r"\b", re.I),
    re.compile(r"\b" + _ORD + r"\s+half(?:\s+of)?\s+" + _YEAR, re.I),
    re.compile(r"\b([12])H(\d\d)\b"),
)
_MON = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_MONTH_RANGE_RE = re.compile(
    r"\b" + _MON + r"\.?\s*(?:\d{1,2},?\s*)?(?:" + _YEAR + r"\s*)?(?:[-–—]|to|through|thru|and)\s*"
    + _MON + r"\.?\s*(?:\d{1,2},?\s*)?" + _YEAR + r"\b", re.I)
_MONTH_YEAR_RE = re.compile(r"(?<![-–—])(?<![-–—] )(?<!\bto )(?<!and )\b" + _MON + r"\s+" + _YEAR + r"\b", re.I)
_DATE_RANGE_RE = re.compile(r"\b(\d{6})\s*(?:through|thru|to|[-–—])\s*(\d{6})\b")
_DATED_RE = re.compile(r"\b(?:dated|due)\s+(\d{1,2})/(\d{1,2})/((?:19|20)\d\d)\b", re.I)
_YEAR_RE = re.compile(r"(?<![\d-])((?:19|20)\d\d)(?![\d-])")
_LABELED_YEAR_RE = re.compile(r"\b(?:CY|FY|calendar year|fiscal year)\s*((?:19|20)\d\d)\b", re.I)
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/((?:19|20)\d\d)\b")
_LONG_DATE_RE = re.compile(r"\b" + _MON + r"\.?\s+(\d{1,2}),?\s+((?:19|20)\d\d)\b", re.I)


def _ord(s: str) -> int:
    return _ORDINALS[s.lower()]


def _record_date(rec: Record) -> Optional[date]:
    """The date a prior_* extractor anchors on: a "dated m/d/yyyy" in the
    title first (the monitor's display titles carry the letter's own date;
    nSITE's Document Date column is often wrong), then doc_date, then
    filed_date."""
    m = _DATED_RE.search(rec.title or "")
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return _ymd(rec.doc_date) or _ymd(rec.filed_date)


def extract_period(name: str, rec: Record) -> Optional[Period]:
    """One named extractor over a record. Pure. Returns None when the pattern
    is absent; a bad year/month never raises."""
    t = (rec.title or "")[:TITLE_SEARCH_CHARS]
    if name == "auto":
        for n in _AUTO_ORDER:
            p = extract_period(n, rec)
            if p is not None:
                return p
        return None
    try:
        if name == "year_quarter_words":
            for i, pat in enumerate(_Q_PATTERNS):
                m = pat.search(t)
                if m:
                    if i == 0:
                        return Period("Q", int(m.group(2)), int(m.group(1)))
                    if i == 1:
                        return Period("Q", int(m.group(1)), int(m.group(2)))
                    if i == 2:
                        return Period("Q", int(m.group(1)), _ord(m.group(2)))
                    return Period("Q", int(m.group(2)), _ord(m.group(1)))
            return None
        if name == "year_half_words":
            for i, pat in enumerate(_H_PATTERNS):
                m = pat.search(t)
                if not m:
                    continue
                if i == 0:
                    y, n = int(m.group(1)), _ord(m.group(2))
                elif i in (1, 3):
                    y, n = int(m.group(2)), _ord(m.group(1))
                elif i == 2:
                    y, n = int(m.group(1)), _ord(m.group(2))
                else:
                    y, n = 2000 + int(m.group(2)), int(m.group(1))
                if n in (1, 2):
                    return Period("H", y, n)
            return None
        if name == "month_range":
            m = _MONTH_RANGE_RE.search(t)
            if not m:
                return None
            m1, y1, m2, y2 = _month_from_word(m.group(1)), m.group(2), _month_from_word(m.group(3)), int(m.group(4))
            if m1 is None or m2 is None or (y1 and int(y1) != y2):
                return None
            return period_for_months(y2, m1, m2)
        if name == "month_year":
            m = _MONTH_YEAR_RE.search(t)
            if not m:
                return None
            mo = _month_from_word(m.group(1))
            return Period("M", int(m.group(2)), mo) if mo else None
        if name == "date_range":
            m = _DATE_RANGE_RE.search(t)
            if not m:
                return None
            a, b = m.group(1), m.group(2)
            m1, y1 = int(a[:2]), 2000 + int(a[4:])
            m2, d2, y2 = int(b[:2]), int(b[2:4]), 2000 + int(b[4:])
            if y1 != y2 or d2 != calendar.monthrange(y2, m2)[1]:
                return None
            return period_for_months(y2, m1, m2)
        if name == "year_in_title":
            m = _LABELED_YEAR_RE.search(t)
            if m:
                return Period("Y", int(m.group(1)), 1)
            years = _YEAR_RE.findall(t)   # a "2020-0593-CE" case number never counts
            return Period("Y", int(years[-1]), 1) if years else None
        if name == "record_period":
            return parse_period(rec.period_hint) if rec.period_hint else None
        if name == "date_in_title":
            m = _SLASH_DATE_RE.search(t)
            if m:
                d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            else:
                m = _LONG_DATE_RE.search(t)
                if not m:
                    return None
                mo = _month_from_word(m.group(1))
                if mo is None:
                    return None
                d = date(int(m.group(3)), mo, int(m.group(2)))
            return Period("D", d.year, d.timetuple().tm_yday, d)
        if name == "record_date":
            d = _ymd(rec.doc_date) or _ymd(rec.filed_date)
            return Period("D", d.year, d.timetuple().tm_yday, d) if d else None
        if name.startswith("prior_") and name.endswith("_from_date"):
            kind = {"month": "M", "bimonth": "B", "quarter": "Q", "half": "H", "year": "Y"}[name[6:-10]]
            d = _record_date(rec)
            return period_ending_before(kind, d) if d else None
    except (ValueError, KeyError):
        return None
    raise ValueError(f"unknown extractor {name!r}")


def period_from_rule(rule: MatchRule, rec: Record, kind: str) -> tuple:
    """(period, extractor_name) for the first extractor in the rule's list
    that yields a period of the schedule's own kind (a quarterly schedule
    never accepts a half); (None, "") when none does."""
    for name in rule.period_from:
        p = extract_period(name, rec)
        if p is not None and p.kind == kind:
            # A prior_* extractor anchored on a date PRINTED IN THE TITLE
            # ("dated 7/30/2024", "(due 10/30/2004)") is title evidence, not
            # a fallback on the record's date fields.
            if name in FALLBACK_EXTRACTORS and name != "record_date" and _DATED_RE.search(rec.title or ""):
                return p, name + "@title"
            return p, name
    return None, ""


# ---------------------------------------------------------------------------
# Hand tables
# ---------------------------------------------------------------------------

@dataclass
class Facility:
    facility_id: str
    name: str
    air_srn: str = ""
    wds_id: str = ""
    streams: tuple = ()
    active: bool = True
    aliases: tuple = ()
    row: dict = field(default_factory=dict)

    def matches_text(self, text: str) -> bool:
        """Case-insensitive; a short all-caps alias (AHL, CFL, N2688) must
        stand as a whole word so "Stahl" never resolves to Arbor Hills."""
        t = (text or "").lower()
        if not t:
            return False
        for a in (self.name, self.air_srn, *self.aliases):
            a = (a or "").strip()
            if not a:
                continue
            if len(a) <= 6 and a.upper() == a:
                if re.search(r"(?<![A-Za-z0-9])" + re.escape(a.lower()) + r"(?![A-Za-z0-9])", t):
                    return True
            elif a.lower() in t:
                return True
        return False


@dataclass
class Schedule:
    schedule_id: str
    facility_id: str
    report_type: str
    report_name: str
    cadence: str
    due_rule: Optional[DueRule]
    grace_days: Optional[int]
    first_period: Optional[Period]
    last_period: Optional[Period]
    match_rule: Optional[MatchRule]
    active: bool
    row: dict = field(default_factory=dict)

    @property
    def kind(self) -> Optional[str]:
        return _KIND_FOR_CADENCE.get(self.cadence)


@dataclass
class TriggerRule:
    rule_id: str
    facility_id: str
    terms: tuple          # ((var, op, value) | ("no_hov",), ...)
    input: str
    creates: str
    due_rule: DueRule
    repeat: str
    active: bool
    row: dict = field(default_factory=dict)


@dataclass
class Instance:
    facility_id: str
    report_type: str
    report_name: str
    period: str
    due: date
    grace: int
    source: str          # schedule_id or rule_id
    subject: str = ""    # triggered instances: the well

    @property
    def key(self) -> str:
        if self.subject:
            return f"{self.facility_id}|{self.report_type}|{self.subject}|{self.period}"
        return f"{self.facility_id}|{self.report_type}|{self.period}"


def instance_key(facility_id: str, report_type: str, period: str, subject: str = "") -> str:
    return Instance(facility_id, report_type, "", period, date.min, 0, "", subject).key


def _yes(v) -> bool:
    return str(v or "").strip().lower() in ("y", "yes", "true", "1")


def _rowdict(headers: list, row: list) -> dict:
    row = list(row) + [""] * (len(headers) - len(row))
    return {h: (str(v).strip() if v is not None else "") for h, v in zip(headers, row)}


def load_facilities(rows: list) -> tuple[list, list]:
    """(facilities, bad) from Report Facilities value rows. `bad` =
    [(row_id, reason)] for rows skipped at load."""
    out, bad, seen = [], [], set()
    for r in rows:
        d = _rowdict(sw.REPORT_FACILITY_HEADERS, r)
        fid = d["facility_id"]
        if not fid:
            continue
        if fid in seen:
            bad.append((fid, "duplicate facility_id"))
            continue
        if not d["name"]:
            bad.append((fid, "facility needs a name"))
            continue
        streams = tuple(x.strip() for x in d["arrival_streams"].replace("|", ";").split(";") if x.strip())
        unknown = [x for x in streams if x not in STREAMS]
        if unknown:
            bad.append((fid, f"unknown arrival_streams {unknown}"))
            continue
        seen.add(fid)
        out.append(Facility(fid, d["name"], d["air_srn"], d["wds_id"], streams, _yes(d["active"]),
                            tuple(x.strip() for x in d["name_aliases"].split(";") if x.strip()), d))
    return out, bad


def load_schedules(rows: list, facility_ids: set) -> tuple[list, list]:
    """(schedules, bad) from Expected Reports value rows. Every due_rule,
    match_rule, cadence, period and report_type is validated here; a row
    that fails is returned in `bad` with a fixed-phrase reason and skipped,
    never guessed. An inactive row still has to parse (its fields are
    visible in the Sheet and a typo there should surface too)."""
    out, bad, seen = [], [], set()
    for r in rows:
        d = _rowdict(sw.EXPECTED_REPORT_HEADERS, r)
        sid = d["schedule_id"]
        if not sid:
            continue
        try:
            if sid in seen:
                raise ValueError("duplicate schedule_id")
            if d["facility_id"] not in facility_ids:
                raise ValueError(f"unknown facility_id {d['facility_id']!r}")
            if d["report_type"] not in REPORT_TYPES:
                raise ValueError(f"report_type {d['report_type']!r} not in the vocabulary")
            if d["cadence"] not in CADENCES:
                raise ValueError(f"cadence {d['cadence']!r} not in {CADENCES}")
            if d["citation_confidence"] and d["citation_confidence"] not in CONFIDENCE:
                raise ValueError("citation_confidence must be primary or secondary")
            due = parse_due_rule(d["due_rule"]) if d["due_rule"] else None
            kind = _KIND_FOR_CADENCE.get(d["cadence"])
            if kind is not None:
                if due is None:
                    raise ValueError("a scheduled cadence needs a due_rule")
                if due.kind == "within":
                    raise ValueError("within: is only for event cadences")
                if due.kind == "on" and kind != "D":
                    raise ValueError("on: is only for one-time cadence")
                if not d["first_period"]:
                    raise ValueError("a scheduled cadence needs first_period")
            elif due is not None and due.kind != "within":
                raise ValueError("an event cadence takes within:N only (or no due_rule)")
            first = parse_period(d["first_period"]) if d["first_period"] else None
            last = parse_period(d["last_period"]) if d["last_period"] else None
            for p in (first, last):
                if p is not None and kind is not None and p.kind != kind:
                    raise ValueError(f"period {p} does not match cadence {d['cadence']}")
            if first and last and last.sort_key() < first.sort_key():
                raise ValueError("last_period is before first_period")
            grace = None
            if d["grace_days"]:
                grace = int(d["grace_days"])
                if grace < 0:
                    raise ValueError("grace_days must be >= 0")
            match = parse_match_rule(d["match_rule"]) if d["match_rule"] else None
        except (ValueError, KeyError) as e:
            bad.append((sid, str(e)))
            continue
        seen.add(sid)
        out.append(Schedule(sid, d["facility_id"], d["report_type"], d["report_name"] or d["report_type"],
                            d["cadence"], due, grace, first, last, match, _yes(d["active"]), d))
    return out, bad


_TERM_RE = re.compile(r"^(wellhead_temp_f|co_ppmv)\s*(>=|>|<=|<|==)\s*(-?\d+(?:\.\d+)?)$")
_NO_HOV_RE = re.compile(r"^no_hov\(\s*well\s*\)$")
_REPEATS = ("", "annual while condition holds", "once per well")


def parse_condition(s: str) -> tuple:
    """`wellhead_temp_f >= 165 AND co_ppmv >= 1000 AND no_hov(well)` ->
    terms. v1 variables: wellhead_temp_f (Measurements metric temperature),
    co_ppmv (carbon_monoxide), no_hov(well). Anything else raises."""
    terms = []
    for part in re.split(r"\s+AND\s+", (s or "").strip(), flags=re.I):
        part = part.strip()
        if not part:
            continue
        if _NO_HOV_RE.match(part):
            terms.append(("no_hov",))
            continue
        m = _TERM_RE.match(part)
        if not m:
            raise ValueError(f"unsupported condition term {part!r}")
        terms.append((m.group(1), m.group(2), float(m.group(3))))
    if not any(t[0] == "wellhead_temp_f" for t in terms):
        raise ValueError("a condition needs a wellhead_temp_f term")
    return tuple(terms)


def load_trigger_rules(rows: list, facility_ids: set) -> tuple[list, list]:
    out, bad, seen = [], [], set()
    for r in rows:
        d = _rowdict(sw.TRIGGER_RULE_HEADERS, r)
        rid = d["rule_id"]
        if not rid:
            continue
        try:
            if rid in seen:
                raise ValueError("duplicate rule_id")
            if d["facility_id"] != "*" and d["facility_id"] not in facility_ids:
                raise ValueError(f"unknown facility_id {d['facility_id']!r}")
            terms = parse_condition(d["condition"])
            if d["input"] != "Measurements":
                raise ValueError("v1 evaluates input=Measurements only")
            if d["creates"] not in REPORT_TYPES:
                raise ValueError(f"creates {d['creates']!r} not in the vocabulary")
            due = parse_due_rule(d["due_rule"])
            if due.kind == "on":
                raise ValueError("on: makes no sense for a trigger")
            if d["repeat"] not in _REPEATS:
                raise ValueError(f"repeat must be one of {_REPEATS[1:]} or blank")
        except (ValueError, KeyError) as e:
            bad.append((rid, str(e)))
            continue
        seen.add(rid)
        out.append(TriggerRule(rid, d["facility_id"], terms, d["input"], d["creates"], due,
                               d["repeat"], _yes(d["active"]), d))
    return out, bad


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

def expand_schedule(s: Schedule, today: date, default_grace: int, lookahead_days: int = 30) -> list:
    """Every instance of an ACTIVE scheduled row from first_period through the
    last period whose due date falls within today + lookahead (or
    last_period). Event cadences and inactive rows expand to nothing."""
    if not s.active or s.kind is None or s.first_period is None or s.due_rule is None:
        return []
    grace = s.grace_days if s.grace_days is not None else default_grace
    horizon = today + timedelta(days=lookahead_days)
    out = []
    p = s.first_period
    if s.kind == "D":
        return [Instance(s.facility_id, s.report_type, s.report_name, str(p),
                         due_date(s.due_rule, p), grace, s.schedule_id)]
    guard = 0
    while guard < 2000:
        guard += 1
        if s.last_period is not None and p.sort_key() > s.last_period.sort_key():
            break
        due = due_date(s.due_rule, p)
        if due > horizon:
            break
        out.append(Instance(s.facility_id, s.report_type, s.report_name, str(p), due, grace, s.schedule_id))
        p = p.next()
    return out


def _measurement_rows(rows: list) -> list:
    """Measurements value rows (A2:K) -> dicts with parsed date/value for
    basis=measured temperature and carbon_monoxide readings only."""
    out = []
    for r in rows:
        d = _rowdict(sw.MEASUREMENTS_HEADERS, r)
        if d["Basis"] != "measured" or d["Metric"] not in ("temperature", "carbon_monoxide"):
            continue
        when = _ymd(d["As-Of Date"])
        try:
            val = float(d["Value"].replace(",", ""))
        except ValueError:
            continue
        if when is None or not d["Well ID"]:
            continue
        out.append({"date": when, "well": d["Well ID"], "metric": d["Metric"], "value": val,
                    "facility": d["Facility"]})
    return out


def _cmp(op: str, a: float, b: float) -> bool:
    return {">=": a >= b, ">": a > b, "<=": a <= b, "<": a < b, "==": a == b}[op]


def evaluate_triggers(rules: list, measurement_rows: list, facilities: list, hov_wells: set,
                      today: date, default_grace: int) -> list:
    """Triggered instances. For every ACTIVE rule and every measured wellhead
    temperature reading at a facility the rule covers, the condition is
    evaluated on that reading (a `co_ppmv` term joins the well's
    carbon_monoxide readings in the same calendar month; `no_hov(well)` is
    the well id's absence from the HOV ceiling roster). The first qualifying
    reading per well (repeat blank / "once per well") or per well and year
    ("annual while condition holds") is the trigger date; the instance's
    period is the trigger year; the due date applies the rule's due_rule to
    the trigger date. stop_condition is text for the reader, not evaluated.
    Pure."""
    ms = _measurement_rows(measurement_rows)
    co_by_well_month: dict = {}
    for m in ms:
        if m["metric"] == "carbon_monoxide":
            co_by_well_month.setdefault((m["well"], m["date"].year, m["date"].month), []).append(m["value"])
    fac_by_id = {f.facility_id: f for f in facilities}
    # A Measurements row with a BLANK Facility (rows predating that column)
    # belongs to the one active facility the nSITE Documents stream feeds;
    # with none or several such facilities it matches nobody.
    nsite_owners = [f for f in facilities if f.active and "nsite" in f.streams]
    blank_owner = nsite_owners[0].facility_id if len(nsite_owners) == 1 else None
    hov_norm = {_norm_well(w) for w in hov_wells}
    out: dict = {}
    for rule in rules:
        if not rule.active:
            continue
        targets = list(facilities) if rule.facility_id == "*" else [fac_by_id[rule.facility_id]]
        for fac in targets:
            if not fac.active:
                continue
            temps = sorted((m for m in ms if m["metric"] == "temperature"
                            and ((m["facility"] and fac.matches_text(m["facility"]))
                                 or (not m["facility"] and fac.facility_id == blank_owner))),
                           key=lambda m: m["date"])
            for m in temps:
                ok = True
                for term in rule.terms:
                    if term[0] == "no_hov":
                        ok = _norm_well(m["well"]) not in hov_norm
                    elif term[0] == "wellhead_temp_f":
                        ok = _cmp(term[1], m["value"], term[2])
                    else:  # co_ppmv
                        cos = co_by_well_month.get((m["well"], m["date"].year, m["date"].month), [])
                        ok = any(_cmp(term[1], c, term[2]) for c in cos)
                    if not ok:
                        break
                if not ok:
                    continue
                period = str(m["date"].year)
                key = (fac.facility_id, rule.creates, m["well"], period if rule.repeat == "annual while condition holds" else "")
                if key in out:
                    continue
                inst = Instance(fac.facility_id, rule.creates, rule.creates, period,
                                due_date(rule.due_rule, Period("Y", m["date"].year, 1), trigger=m["date"]),
                                default_grace, rule.rule_id, subject=m["well"])
                out[key] = inst
    return list(out.values())


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass
class Match:
    rec: Record
    facility_id: str
    report_type: str
    period: str
    schedule_id: str
    confidence: str      # high | proposed
    detail: str = ""


def resolve_facility(rec: Record, facilities: list) -> list:
    """Facility ids a record can belong to: alias text match on the
    record's facility text and title; for a single-site stream, the one
    active facility that lists the stream."""
    hits = [f.facility_id for f in facilities
            if f.active and (f.matches_text(rec.facility_text) or f.matches_text(rec.title))]
    if not hits and rec.stream in SINGLE_SITE_STREAMS:
        hits = [f.facility_id for f in facilities if f.active and rec.stream in f.streams]
    return hits


def _in_window(s: Schedule, p: Period) -> bool:
    """A schedule only answers for a period inside its own first/last window
    (an inactive 2019-2021 row must not claim a 2025 title through a
    date-based fallback extractor)."""
    if s.first_period is not None and p.sort_key() < s.first_period.sort_key():
        return False
    if s.last_period is not None and p.sort_key() > s.last_period.sort_key():
        return False
    return True


def match_record(rec: Record, schedules: list, facilities: list) -> Optional[Match]:
    """The ADR's match step for one record: every schedule's match_rule for
    the record's stream is tried; a HIGH match needs exactly one (facility,
    report_type, period) answer with the period extracted and the facility
    resolved. A period inside the schedule's first/last window wins; a
    TITLE-derived period outside every matching schedule's window is still
    a sound arrival (a 2004 QMR is a QMR) and is HIGH with a note, but a
    period that only a date-fallback extractor produced outside the window
    is PROPOSED (never applied) — that is how an inactive 2019-2021 row is
    kept from claiming a 2025 title. None when no rule matches at all. Pure."""
    fids = resolve_facility(rec, facilities)
    title = (rec.title or "")[:TITLE_SEARCH_CHARS]
    cands = []
    for s in schedules:
        rule = s.match_rule
        if rule is None or rec.stream not in rule.streams:
            continue
        if not rule.title_re.search(title):
            continue
        if rule.exclude_re and rule.exclude_re.search(title):
            continue
        if rule.doc_type and rec.doc_type and rule.doc_type.lower() != rec.doc_type.lower():
            continue
        kind = s.kind or "D"
        p, how = period_from_rule(rule, rec, kind)
        cands.append((s, p, how))
    if not cands:
        return None
    with_p = [(s, p, how) for s, p, how in cands if p is not None and s.facility_id in fids]
    windowed = [(s, p, how) for s, p, how in with_p if _in_window(s, p)]
    pool, note = windowed, ""
    if not pool:
        # Outside every window: a period named in the title's own words
        # outranks one computed from a "dated m/d/yyyy" in the title, which
        # outranks nothing (a record-date fallback is never trusted here).
        note = "period outside the schedule's window (not an expected instance)"
        pool = [(s, p, how) for s, p, how in with_p if how not in FALLBACK_EXTRACTORS and not how.endswith("@title")]
        if not pool:
            pool = [(s, p, how) for s, p, how in with_p if how.endswith("@title")]
    answers = {(s.facility_id, s.report_type, str(p)) for s, p, _ in pool}
    if len(answers) == 1:
        s, p, _ = pool[0]
        return Match(rec, s.facility_id, s.report_type, str(p), s.schedule_id, "high", note)
    s0, p0, _ = (with_p or cands)[0]
    if not fids:
        why = "facility not resolved from the record"
    elif len(answers) > 1:
        why = "more than one schedule answers: " + ", ".join(sorted("|".join(a) for a in answers))
    elif all(p is None for _, p, _ in cands):
        why = "period not found in the title"
    elif not with_p:
        why = "facility differs from the rule's"
    else:
        why = "period from the record's date falls outside every matching schedule's window"
    fid = fids[0] if len(fids) == 1 else (s0.facility_id if not fids else "")
    return Match(rec, fid, s0.report_type, str(p0) if p0 else "", s0.schedule_id, "proposed", why)


def looks_periodic(title: str, periodic_re: re.Pattern) -> bool:
    return bool(periodic_re.search(title or ""))


def arrival_id(stream: str, ref: str) -> str:
    return "A" + hashlib.sha256(f"{stream}|{ref}".encode()).hexdigest()[:10]


def arrival_row(m: Match, first_seen: str, method: str = "rule", notes: str = "") -> list:
    r = m.rec
    return [arrival_id(r.stream, r.ref), m.facility_id, instance_key(m.facility_id, m.report_type, m.period),
            m.report_type, m.period, r.stream, r.ref, r.doc_date, r.filed_date, first_seen,
            method, m.confidence, notes]


# ---------------------------------------------------------------------------
# Missing & Overdue Log helpers (pure)
# ---------------------------------------------------------------------------

def is_complete(log_row: dict, today: date) -> bool:
    """ADR rule: complete for collection purposes when date_received holds a
    real date BEFORE today. Blank, unparseable or today/future = open."""
    d = _ymd(log_row.get("date_received", ""))
    return d is not None and d < today


def missing_instances(instances: list, arrived_keys: set, log_keys: set, today: date) -> list:
    """Instances past due + grace with no arrival and not yet on the log."""
    out, seen = [], set(log_keys)
    for inst in instances:
        if inst.key in arrived_keys or inst.key in seen:
            continue
        if inst.due + timedelta(days=inst.grace) < today:
            out.append(inst)
            seen.add(inst.key)   # two sources for one key never make two rows
    return out


def log_row(inst: Instance, today: date, notes: str = "") -> list:
    return [inst.key, inst.facility_id, inst.report_name, inst.period, inst.due.isoformat(),
            today.isoformat(), "", "", "", "", notes]


def per_facility_summary(log_rows: list, today: date, added: dict, completed: dict) -> str:
    """`arbor-hills: 16 open (+2/-0) · carleton-farms: 68 open (+68/-0)`."""
    open_by: dict = {}
    for r in log_rows:
        if not is_complete(r, today):
            open_by[r["facility_id"]] = open_by.get(r["facility_id"], 0) + 1
    fids = sorted(set(open_by) | set(added) | set(completed))
    return " · ".join(f"{f}: {open_by.get(f, 0)} open (+{added.get(f, 0)}/-{completed.get(f, 0)})" for f in fids)


# ---------------------------------------------------------------------------
# Arrival streams (Sheet reads — strict; thin, so the pure parts stay pure)
# ---------------------------------------------------------------------------

_NSITE_ID_RE = re.compile(r"/(?:downloadpdf|downloadfile|document)/(-?\d{6,})")


def nsite_records(feed_rows: list, archive_rows: list) -> list:
    """New/Historical Documents rows -> Records. source_ref is the nSITE doc
    id, read from the Link when it is still an nSITE URL, else looked up by
    the Drive link in the Archived PDFs index (archiver.mirror_one_now()
    rewrites the feed's Link to the Drive copy), else the Link itself."""
    by_archive_link = {}
    for r in archive_rows:
        d = _rowdict(sw.ARCHIVE_HEADERS, r)
        if d["Archive Link"]:
            by_archive_link[d["Archive Link"]] = d["Doc ID"]
    out = []
    for r in feed_rows:
        d = _rowdict(sw.FEED_HEADERS, r)
        link = d["Link"]
        m = _NSITE_ID_RE.search(link)
        ref = m.group(1) if m else by_archive_link.get(link, link)
        if not ref:
            continue
        title = d["Document Name"] or d["EGLE Title"]
        out.append(Record("nsite", ref, title, d["Facility"], d["Document Date"], d["Date Filed"], d["Type"]))
    return out


def wds_records(rows: list) -> list:
    out = []
    for r in rows:
        d = _rowdict(sw.WDS_HEADERS, r)
        if not d["Item"]:
            continue
        ref = f"wds:{d['Collection']}:{d['Date']}:{d['Item']}"
        out.append(Record("wds", ref, f"{d['Item']} — {d['Detail']}".strip(" —"), "", "", d["Date"], d["Collection"]))
    return out


_HC_COLS = ("curated_filename", "title", "source", "doc_date", "facility", "doc_type", "risks",
            "origin_url", "note", "drive_link", "added_at", "folded_into_public", "source_public")


def hand_curated_records(rows: list) -> list:
    out = []
    for r in rows:
        d = _rowdict(list(_HC_COLS), r)
        ref = d["drive_link"] or d["curated_filename"]
        if not ref or not (d["title"] or d["curated_filename"]):
            continue
        out.append(Record("hand-curated", ref, d["title"] or d["curated_filename"], d["facility"],
                          d["doc_date"], d["added_at"][:10], d["doc_type"]))
    return out


def govqa_records(rows: list) -> list:
    """GovQA Archive Watch `file-listed` rows (an attachment EGLE's archive
    lists on a request) -> Records. The file name is the title; the request
    excerpt (when the row carries one) is the facility text."""
    out = []
    for r in rows:
        d = _rowdict(sw.GOVQA_HEADERS, r)
        if d["Event"] != "file-listed" or not d["File Name"]:
            continue
        out.append(Record("govqa", d["Key"], d["File Name"], d["Request Excerpt"], "", d["Date"]))
    return out


def ridgewood_records(rows: list) -> list:
    out = []
    for r in rows:
        d = _rowdict(sw.RIDGEWOOD_REPORT_HEADERS, r)
        if not d["Month"] or not d["Source URL"]:
            continue
        out.append(Record("ridgewood", d["Source URL"], d["Report Title"] or f"Ridge Wood {d['Month']}",
                          "", "", d["Fetched At"][:10], "", period_hint=d["Month"]))
    return out


def load_stream_records(service, public_id: str, private_id: str, streams_in_use: set) -> list:
    """Every record of every stream some facility lists. STRICT reads: a
    transport failure raises (a false-empty stream would make every
    instance look missing). Only the streams in use are read."""
    recs = []
    if "nsite" in streams_in_use:
        feed = (sw._tab_rows_strict(service, public_id, sw.TAB_NEW, "A2:K")
                + sw._tab_rows_strict(service, public_id, sw.TAB_HISTORICAL, "A2:K"))
        archive = sw._tab_rows_strict(service, public_id, sw.TAB_ARCHIVE, "A2:I")
        recs += nsite_records(feed, archive)
    if "wds" in streams_in_use:
        recs += wds_records(sw._tab_rows_strict(service, public_id, sw.TAB_WDS_NEW, "A2:H")
                            + sw._tab_rows_strict(service, public_id, sw.TAB_WDS_HISTORICAL, "A2:H"))
    if "hand-curated" in streams_in_use:
        recs += hand_curated_records(sw._tab_rows_strict(service, public_id, sw.TAB_HANDCURATED, "A2:M"))
    if "govqa" in streams_in_use:
        recs += govqa_records(sw._tab_rows_strict(service, private_id, sw.TAB_GOVQA, "A2:P"))
    if "ridgewood" in streams_in_use:
        recs += ridgewood_records(sw._tab_rows_strict(service, public_id, sw.TAB_RIDGEWOOD, "A2:I"))
    return recs


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def _cfg_block(cfg: dict) -> dict:
    return (cfg or {}).get("report_tracking") or {}


def tracker_enabled(cfg: dict) -> bool:
    return bool(_cfg_block(cfg).get("enabled", False))


def run_mode(cfg: dict, today: date) -> str:
    until = _ymd(str(_cfg_block(cfg).get("shadow_until") or ""))
    return "shadow" if until is not None and today <= until else "live"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_well(w: str) -> str:
    return str(w or "").strip().rstrip("*").upper()


def hov_wells(cfg: dict) -> set:
    """Well ids with an approved HOV ceiling, normalized like
    `email_alerts.hov_ceiling_for` (trailing `*` dropped, upper-cased).
    `temperature_thresholds.hov_ceilings` is a LIST of {well_id, ceiling_f,
    effective_start, effective_end} entries in config.yml (a dict keyed by
    well id is accepted too). Covers only the letters the roster holds
    (2024-2026 as of 2026-10-17), so `no_hov(well)` is about the CURRENT
    roster, not a pre-2024 reading's own date."""
    ceilings = (cfg.get("temperature_thresholds") or {}).get("hov_ceilings") or []
    if isinstance(ceilings, dict):
        return {_norm_well(k) for k in ceilings} - {""}
    out = set()
    for e in ceilings:
        if isinstance(e, dict):
            out.add(_norm_well(e.get("well_id", "")))
        else:
            out.add(_norm_well(e))
    return out - {""}


_hov_wells = hov_wells


def _rows_with_rownums(service, sheet_id: str, tab: str, a1: str) -> list:
    """[(sheet_row_number, row)] for the NON-EMPTY rows of a range, keeping
    each row's real position. `_tab_rows_strict` drops blank rows, so an
    index into its result would shift every later in-place cell update up
    one row the moment a hand-edited tab holds a cleared row or a spacer;
    the Missing & Overdue Log's `date_received` fill needs the true row
    number. Same strictness as `_tab_rows_strict`: a genuinely absent tab is
    [], any other failure raises."""
    try:
        resp = (service.spreadsheets().values()
                .get(spreadsheetId=sheet_id, range=f"'{tab}'!{a1}")
                .execute(num_retries=sw.GOOGLE_API_NUM_RETRIES))
    except Exception as e:  # noqa: BLE001 — duck-typed absent-tab check
        if sw._is_absent_tab_error(e):
            return []
        raise
    m = re.match(r"[A-Z]+(\d+)", a1)
    first = int(m.group(1)) if m else 1
    return [(first + i, r) for i, r in enumerate(resp.get("values", [])) if r]


def _update_cells(service, sheet_id: str, tab: str, a1: str, values: list) -> None:
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"'{tab}'!{a1}", valueInputOption="RAW",
        body={"values": values}).execute(num_retries=sw.GOOGLE_API_NUM_RETRIES)


def build_review_email(review_rows: list, since: date, runs_row: Optional[dict]) -> Optional[str]:
    """Owner-only weekly body, or None when there is nothing to show. Pure."""
    if not review_rows and runs_row is None:
        return None
    lines = [f"Report tracker review since {since.isoformat()}. Sent to the monitor owner only.", ""]
    if runs_row:
        lines += [f"Latest run {runs_row.get('run_date', '')} ({runs_row.get('mode', '')}): "
                  f"{runs_row.get('open_count', '')} open, +{runs_row.get('added_today', '')} added, "
                  f"-{runs_row.get('completed_today', '')} completed. {runs_row.get('per_facility', '')}", ""]
    by_kind: dict = {}
    for r in review_rows:
        by_kind.setdefault(r["kind"], []).append(r)
    titles = {KIND_PROPOSED: "PROPOSED MATCHES (not applied; add a hand Report Arrivals row or fix the rule)",
              KIND_UNMATCHED: "UNMATCHED PERIODIC TITLES (no schedule rule matched; fix the schedule)",
              KIND_BAD_ROW: "HAND-TABLE ROWS SKIPPED (fix the row; it is not expanded)"}
    n = 1
    for kind in (KIND_PROPOSED, KIND_UNMATCHED, KIND_BAD_ROW):
        rows = by_kind.get(kind)
        if not rows:
            continue
        lines.append(f"{n}. {titles[kind]} ({len(rows)}):")
        for r in rows:
            lines.append(f"  - [{r.get('source_stream', '')}] {r.get('title', '')}")
            lines.append(f"      {r.get('facility_id', '')} | {r.get('source_ref', '')} | {r.get('detail', '')}")
        lines.append("")
        n += 1
    if n == 1 and not runs_row:
        return None
    return "\n".join(lines).rstrip() + "\n"


def run(today: Optional[date] = None, send_email: bool = False, cfg: Optional[dict] = None) -> int:
    """The daily job. Returns the process exit code."""
    if cfg is None:
        from config_loader import load_config
        cfg = load_config()
    rt = _cfg_block(cfg)
    if not tracker_enabled(cfg):
        print("[report_tracker] report_tracking.enabled is false; nothing to do")
        return 0
    today = today or datetime.now(timezone.utc).date()
    now = _now_iso()
    public_id = (os.environ.get("GSHEET_ID") or "").strip()
    if not public_id:
        print("[report_tracker] refusing to run: GSHEET_ID is not set")
        return 1
    import drive_client as dc
    service = dc.sheets_service()
    private_id, why = rf.private_sheet_for_write(service)
    if private_id is None:
        print(f"[report_tracker] refusing to run: {why} (the tracker writes the private Sheet only)")
        return 1
    default_grace = int(rt.get("default_grace_days", 7))
    lookahead = int(rt.get("lookahead_days", 30))
    agency_re = re.compile(str(rt.get("agency_title_regex") or r"(?!x)x"), re.I)
    periodic_re = re.compile(str(rt.get("periodic_title_regex") or r"\bReport\b"), re.I)

    sw.ensure_report_tracker_tabs(service, private_id)

    # 1. Load + validate the hand tables.
    facilities, bad_f = load_facilities(sw._tab_rows_strict(service, private_id, sw.TAB_REPORT_FACILITIES, "A2:L"))
    fids = {f.facility_id for f in facilities}
    schedules, bad_s = load_schedules(sw._tab_rows_strict(service, private_id, sw.TAB_EXPECTED_REPORTS, "A2:S"), fids)
    triggers, bad_t = load_trigger_rules(sw._tab_rows_strict(service, private_id, sw.TAB_TRIGGER_RULES, "A2:L"), fids)
    bad = [("Report Facilities", *b) for b in bad_f] + [("Expected Reports", *b) for b in bad_s] \
        + [("Trigger Rules", *b) for b in bad_t]
    if not facilities:
        print("[report_tracker] no facilities in Report Facilities; seed the hand tables first "
              "(python3 report_tracker_seed.py --apply)")
        return 1

    # 2 + 3. Expand schedules; evaluate triggers.
    instances = []
    for s in schedules:
        instances += expand_schedule(s, today, default_grace, lookahead)
    if any(t.active for t in triggers):
        meas = sw._tab_rows_strict(service, public_id, sw.TAB_MEASUREMENTS, "A2:K")
        instances += evaluate_triggers(triggers, meas, facilities, hov_wells(cfg), today, default_grace)
    by_key: dict = {}
    for i in instances:          # one instance per key, first source wins
        by_key.setdefault(i.key, i)
    instances = list(by_key.values())

    # Existing tracker state (all three are this job's own tabs).
    arrivals = [_rowdict(sw.REPORT_ARRIVAL_HEADERS, r)
                for r in sw._tab_rows_strict(service, private_id, sw.TAB_REPORT_ARRIVALS, "A2:M")]
    log_raw = _rows_with_rownums(service, private_id, sw.TAB_MISSING_OVERDUE, "A2:K")
    log_rows = [_rowdict(sw.MISSING_OVERDUE_HEADERS, r) for _, r in log_raw]
    log_rownums = [n for n, _ in log_raw]
    review_seen = {(r["kind"], r["source_stream"], r["source_ref"]) for r in
                   (_rowdict(sw.REPORT_TRACKER_REVIEW_HEADERS, x)
                    for x in sw._tab_rows_strict(service, private_id, sw.TAB_REPORT_TRACKER_REVIEW, "A2:G"))}
    runs = [_rowdict(sw.REPORT_TRACKER_RUN_HEADERS, r)
            for r in sw._tab_rows_strict(service, private_id, sw.TAB_REPORT_TRACKER_RUNS, "A2:L")]
    last_run = _ymd(runs[-1]["run_date"]) if runs else None
    arrived_refs = {(a["source_stream"], a["source_ref"]) for a in arrivals}
    arrived_keys = {a["instance_key"] for a in arrivals}
    arrival_by_key: dict = {}
    for a in arrivals:
        arrival_by_key.setdefault(a["instance_key"], a)
    # A hand-entered date nobody can read must not pass as "blank" (open
    # forever, silently): surface it, keep the row open.
    for lr in log_rows:
        if lr["date_received"] and _ymd(lr["date_received"]) is None:
            bad.append(("Missing & Overdue Log", lr["instance_key"],
                        f"date_received {lr['date_received']!r} is not a date (use YYYY-MM-DD)"))

    # 4. Match arrivals. First reconcile: an OPEN log row whose instance
    # already has an arrival on the Report Arrivals tab (a hand-added row,
    # or a run killed between the arrival append and the log fill) is
    # received now, from that arrival — idempotent, so the fill can never be
    # lost to a crash.
    new_arrivals, review_new, fills = [], [], []
    for idx, lr in enumerate(log_rows):
        a = arrival_by_key.get(lr["instance_key"])
        if a is None or lr["date_received"] or idx >= len(log_rownums):
            continue
        received = (a["filed_date"][:10] if _ymd(a["filed_date"]) else
                    a["doc_date"][:10] if _ymd(a["doc_date"]) else
                    a["first_seen_at"][:10] if _ymd(a["first_seen_at"]) else today.isoformat())
        lr["date_received"], lr["received_via"] = received, a["arrival_id"] or "hand"
        fills.append((log_rownums[idx], received, lr["received_via"]))
    streams_in_use = {s for f in facilities if f.active for s in f.streams}
    records = load_stream_records(service, public_id, private_id, streams_in_use)
    for rec in records:
        if (rec.stream, rec.ref) in arrived_refs or agency_re.search(rec.title or ""):
            continue
        m = match_record(rec, schedules, facilities)
        if m is None:
            if looks_periodic(rec.title, periodic_re) and resolve_facility(rec, facilities):
                k = (KIND_UNMATCHED, rec.stream, rec.ref)
                if k not in review_seen:
                    review_seen.add(k)
                    review_new.append([now, KIND_UNMATCHED, ",".join(resolve_facility(rec, facilities)),
                                       rec.stream, rec.ref, rec.title[:200], "no schedule rule matched"])
            continue
        if m.confidence != "high":
            k = (KIND_PROPOSED, rec.stream, rec.ref)
            if k not in review_seen:
                review_seen.add(k)
                review_new.append([now, KIND_PROPOSED, m.facility_id, rec.stream, rec.ref, rec.title[:200],
                                   f"{m.report_type} {m.period}: {m.detail}".strip()])
            continue
        arrived_refs.add((rec.stream, rec.ref))
        key = instance_key(m.facility_id, m.report_type, m.period)
        note = m.detail or ("" if key in by_key else "no scheduled instance for this period")
        new_arrivals.append(arrival_row(m, now, notes=note))
        arrived_keys.add(key)
        for idx, lr in enumerate(log_rows):
            if idx < len(log_rownums) and lr["instance_key"] == key and not lr["date_received"]:
                received = rec.filed_date[:10] if _ymd(rec.filed_date) else today.isoformat()
                lr["date_received"], lr["received_via"] = received, arrival_id(rec.stream, rec.ref)
                fills.append((log_rownums[idx], received, lr["received_via"]))
    if new_arrivals:
        sw.append_rows(service, private_id, sw.TAB_REPORT_ARRIVALS, new_arrivals)
    for rownum, received, via in fills:
        _update_cells(service, private_id, sw.TAB_MISSING_OVERDUE, f"I{rownum}:J{rownum}", [[received, via]])

    # 5. Find missing (never a duplicate instance_key).
    log_keys = {r["instance_key"] for r in log_rows}
    missing = missing_instances(instances, arrived_keys, log_keys, today)
    added: dict = {}
    if missing:
        rows = [log_row(i, today) for i in sorted(missing, key=lambda i: (i.facility_id, i.due))]
        sw.append_rows(service, private_id, sw.TAB_MISSING_OVERDUE, rows)
        for i in missing:
            log_rows.append(_rowdict(sw.MISSING_OVERDUE_HEADERS, log_row(i, today)))
            added[i.facility_id] = added.get(i.facility_id, 0) + 1
    for table, row_id, reason in bad:
        k = (KIND_BAD_ROW, table, row_id)
        if k not in review_seen:
            review_seen.add(k)
            review_new.append([now, KIND_BAD_ROW, "", table, row_id, row_id, reason])
    if review_new:
        sw.append_rows(service, private_id, sw.TAB_REPORT_TRACKER_REVIEW, review_new)

    # 6. Summarize. "Completed today" = log rows complete now (date_received
    # before today) that this run filled, plus hand-filled rows whose
    # date_received falls on/after the previous run's date (the entered date
    # is the only signal of WHEN a hand entry was made).
    filled_keys = {lr["instance_key"] for lr in log_rows if lr["received_via"] in {f[2] for f in fills}}
    completed: dict = {}
    for r in log_rows:
        if not is_complete(r, today):
            continue
        d = _ymd(r["date_received"])
        hand_since_last = r["instance_key"] not in filled_keys and last_run is not None and d >= last_run
        if r["instance_key"] in filled_keys or hand_since_last:
            completed[r["facility_id"]] = completed.get(r["facility_id"], 0) + 1
    open_count = sum(1 for r in log_rows if not is_complete(r, today))
    coming = sum(1 for i in instances if i.key not in arrived_keys and today <= i.due <= today + timedelta(days=lookahead))
    per_fac = per_facility_summary(log_rows, today, added, completed)
    n_prop = sum(1 for r in review_new if r[1] == KIND_PROPOSED)
    n_unm = sum(1 for r in review_new if r[1] == KIND_UNMATCHED)
    runs_row = [str(x) for x in (
        today.isoformat(), now, run_mode(cfg, today), open_count, sum(added.values()),
        sum(completed.values()), coming, per_fac, n_prop, n_unm, len(bad),
        f"{len(records)} records read; {len(new_arrivals)} new arrivals; {len(fills)} log rows received")]
    sw.append_rows(service, private_id, sw.TAB_REPORT_TRACKER_RUNS, [runs_row])
    print(f"[report_tracker] {today} {runs_row[2]}: {open_count} open (+{runs_row[4]}/-{runs_row[5]}), "
          f"{coming} coming due, {len(new_arrivals)} new arrivals, {len(fills)} received, "
          f"{n_prop} proposed, {n_unm} unmatched, {len(bad)} bad rows")

    # Owner-only review email (Sundays, or on request). Best-effort.
    if rt.get("review_email_enabled") and (send_email or today.weekday() == 6):
        try:
            import owner_notices as on
            since = today - timedelta(days=7)
            recent = [_rowdict(sw.REPORT_TRACKER_REVIEW_HEADERS, x) for x in
                      sw._tab_rows_strict(service, private_id, sw.TAB_REPORT_TRACKER_REVIEW, "A2:G")]
            recent = [r for r in recent if r["logged_at"][:10] >= since.isoformat()]
            body = build_review_email(recent, since, _rowdict(sw.REPORT_TRACKER_RUN_HEADERS, runs_row))
            if body:
                sent = on.send_owner_email(REVIEW_SUBJECT, body, cfg)
                print(f"[report_tracker] review email {'sent' if sent else 'not sent (no owner addresses)'}")
        except Exception as e:  # noqa: BLE001 — the email is best-effort, the tabs are the record
            print(f"[report_tracker] review email skipped: {type(e).__name__}")
    return 0


_URL_RE = re.compile(r"https?://\S+")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,}")


def scrub(msg) -> str:
    """Strip URLs and long id-like tokens (a spreadsheet id, a Drive file
    id) from a message bound for the PUBLIC Actions log."""
    return _LONG_TOKEN_RE.sub("<id>", _URL_RE.sub("<url>", str(msg)))[:300]


def main(argv: Optional[list] = None) -> int:
    import argparse
    import logging
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--today", help="run as of this ISO date (tests/replays)")
    ap.add_argument("--email", action="store_true", help="send the owner-only review email this run")
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else None
    email = args.email or str(os.environ.get("REPORT_TRACKER_EMAIL", "")).lower() == "true"
    # stdout is public: googleapiclient's retry logger would print each
    # retried request URI (which carries the private spreadsheet id), and an
    # uncaught HttpError repr does the same. Same pattern as govqa_watcher /
    # ride_docs_watcher.
    logging.getLogger("googleapiclient.http").setLevel(logging.ERROR)
    try:
        return run(today=today, send_email=email)
    except Exception as e:  # noqa: BLE001 — class + scrubbed message only
        print(f"[report_tracker] failed: {type(e).__name__}: {scrub(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
