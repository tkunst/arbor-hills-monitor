"""
wds_client.py — EGLE Waste Data System (Part 115 solid-waste) fetch + parse.

WDS (`egle.state.mi.us/wdspi`, = legacy `deq.state.mi.us/wdspi`, byte-identical)
is EGLE's solid-waste system of record — a DIFFERENT portal from nSITE (Air, the
`nsite_client.py` feed) and from RIDE. Most of the landfill's solid-waste life
(permits/licenses, quarterly groundwater monitoring, annual tonnage/capacity,
inspections, enforcement) lives here and is NOT in nSITE. This is the Stream C
source (see docs/decisions/009-wds-stream-c.md); the daily watcher polls it when
`wds.enabled` is true.

It is a server-rendered ASP.NET WebForms app, so plain HTTP GET reads it — no
browser needed. Two parse shapes:
  - "detail-span" grids (QMR, Applications, Annual): each record's read-only
    values render as <span class="detailControl" title="Field:">value</span>
    grouped under a per-row container id ...<Grid>_R_ctl<NN>_... . Grouping the
    detailControl spans by that ctl index reads the RENDERED value (so a Yes/No
    dropdown yields the *selected* option, not the label list) and does not bleed
    an empty field into the next row. Tolerant of attribute order.
  - CME list grids (Evaluations, Compliance Actions): label-delimited text blocks,
    ported from the standalone scraper (scripts/wds_scrape.py in the Lotext repo).

Paging: the Wndsr ExpandableListControl pager — GET var
`<prefix>=<0-based-page>*_*0*0`, where prefix = the $PageCurrent field name with
$ -> _ and $PageCurrent stripped; PageEnd gives the last page.

This module only FETCHES + PARSES. Diff / classification / alerting is
wds_watcher.py. Discovery notes + verified findings for site 475946 live in the
Lotext repo: documents/arbor-hills/source-docs/WDS-crawl-and-monitor-map.md.
"""
from __future__ import annotations

import html
import http.cookiejar
import re
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
_BASE = "https://www.egle.state.mi.us/wdspi"


class WDSFetchError(RuntimeError):
    """A WDS page could not be fetched/parsed. Treated as TRANSIENT by the
    watcher (skip-and-warn), never as 'the data changed' — a short/failed fetch
    must not be diffed (see wds_watcher for the last_count guard)."""


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", _UA), ("Accept", "text/html")]
    return op


def _get(op, url: str) -> str:
    try:
        return op.open(url, timeout=90).read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001 — network / HTTP → transient
        raise WDSFetchError(f"GET {url} failed: {e}") from e


def _form_fields(h: str) -> dict:
    f = {}
    for m in re.finditer(r"<input\b[^>]*>", h, re.I):
        n = re.search(r'name="([^"]+)"', m.group(0))
        if n:
            v = re.search(r'value="([^"]*)"', m.group(0))
            f[n.group(1)] = html.unescape(v.group(1)) if v else ""
    return f


def _pager(h: str):
    """(prefix, page_end) for the list control on the page, or (None, 0)."""
    f = _form_fields(h)
    pc = next((k for k in f if k.endswith("$PageCurrent")), None)
    if not pc:
        return None, 0
    prefix = pc.replace("$PageCurrent", "").replace("$", "_")
    pe = int(f.get(pc.replace("PageCurrent", "PageEnd"), "0") or 0)
    return prefix, pe


def _detail_rows(h: str, grid: str) -> list[dict]:
    """Group <span class=detailControl title=Label:>value</span> by row index for
    a grid. Two-step (tolerant of attribute order): find every detailControl span,
    then read its own row index (..<grid>_R_ctl<NN>_) and title from the tag."""
    rows: dict[str, dict] = {}
    row_re = re.compile(re.escape(grid) + r"_R_ctl(\d+)_")
    for m in re.finditer(
        r'<span\b([^>]*\bclass="[^"]*detailControl[^"]*"[^>]*)>(.*?)</span>',
        h, re.S | re.I,
    ):
        attrs, inner = m.group(1), m.group(2)
        ri = row_re.search(attrs)
        if not ri:
            continue
        t = re.search(r'title="([^"]*)"', attrs)
        key = t.group(1).rstrip(":").strip() if t else "?"
        val = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        rows.setdefault(ri.group(1), {})[key] = val
    return [rows[k] for k in sorted(rows, key=int)]


def _paged_detail(op, url: str, grid: str) -> list[dict]:
    h = _get(op, url)
    prefix, pe = _pager(h)
    out = _detail_rows(h, grid)
    sep = "&" if "?" in url else "?"
    for n in range(1, pe + 1):
        out += _detail_rows(_get(op, url + sep + f"{prefix}={n}*_*0*0"), grid)
    seen, clean = set(), []
    for r in out:
        if not any(r.values()):
            continue
        sig = tuple(sorted(r.items()))
        if sig in seen:
            continue
        seen.add(sig)
        clean.append(r)
    return clean


# --- CME list grids (label-delimited text blocks) ---------------------------

_EVAL_LABELS = [
    "Responsible Person", "Evaluation Date", "Primary Reason", "Day Zero", "Branch",
    "Day In Compliance Determined", "Evaluation Status", "Secondary Violation Date",
    "Regulatory Program", "High Priority", "Current Tire Inventory", "Secondary Reasons",
]
_CA_LABELS = [
    "Compliance Action Type", "Determined By", "Branch", "Lead Program",
    "Compliance Action Date", "Corrective Action Component", "High Priority",
    "Company Response Due Date", "Company Response Date",
]


def _fields_from_text(t: str, labels: list[str]) -> dict:
    rec = {}
    for lab in labels:
        mm = re.search(re.escape(lab) + r":", t)
        if not mm:
            rec[lab] = ""
            continue
        vs = mm.end()
        nxt = [t.find(nl + ":", vs) for nl in labels]
        nxt = [x for x in nxt if x > 0]
        ve = min(nxt) if nxt else len(t)
        val = re.sub(r"\s+", " ", t[vs:ve]).strip()
        m2 = re.match(r"^(.*?)\s+\1$", val)  # collapse exact doubling
        if m2:
            val = m2.group(1).strip()
        rec[lab] = val
    return rec


def _parse_evaluations(h: str) -> list[dict]:
    c = re.sub(r"<input\b[^>]*>", " ", h, flags=re.I)
    c = re.sub(r"<select\b.*?</select>", " ", c, flags=re.S | re.I)
    txt = re.sub(r"[ \t\r\n]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c)))
    starts = [m.start() for m in re.finditer(r"Responsible Person:", txt)]
    rows = []
    for k, s in enumerate(starts):
        # Non-tail records are bounded by the next record's start. The tail record
        # has no successor, so bound it with a generous fixed window (12 short
        # label:value fields fit easily); capping it (vs. running to end-of-text)
        # keeps page-footer chrome from bleeding into the last field's value.
        e = starts[k + 1] if k + 1 < len(starts) else min(s + 2500, len(txt))
        rows.append(_fields_from_text(txt[s:e], _EVAL_LABELS))
    return [r for r in rows if r.get("Evaluation Date")]


def _parse_compliance_actions(h: str) -> list[dict]:
    rows = []
    for m in re.finditer(
        r'id="ctl00_Body_ComplianceActionsL_R_ctl\d+_DetailEditRow"[^>]*>(.*?)</tr>',
        h, re.S,
    ):
        c = re.sub(r"<input\b[^>]*>", " ", m.group(1), flags=re.I)
        c = re.sub(r"<select\b.*?</select>", " ", c, flags=re.S | re.I)
        t = re.sub(r"[ \t\r\n]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c)))
        rec = _fields_from_text(t, _CA_LABELS)
        if rec.get("Compliance Action Date") or rec.get("Compliance Action Type"):
            rows.append(rec)
    return rows


def _paged_cme(op, url: str, grid: str, parser) -> list[dict]:
    h = _get(op, url)
    prefix, pe = _pager(h)
    out = parser(h)
    sep = "&" if "?" in url else "?"
    for n in range(1, pe + 1):
        out += parser(_get(op, url + sep + f"{prefix}={n}*_*0*0"))
    return out


# --- Public fetch API (one function per collection) -------------------------
# Each returns a list of row-dicts with the WDS field names as keys. Empty list
# is a valid "no records" answer; a fetch problem raises WDSFetchError (never a
# silent []), so the watcher can tell "0 records" from "couldn't read it".


def fetch_qmr(w: str) -> list[dict]:
    return _paged_detail(_opener(), f"{_BASE}/SolidWaste/QMRReports.aspx?w={w}", "QMRReportList")


def fetch_applications(w: str) -> list[dict]:
    return _paged_detail(_opener(), f"{_BASE}/SolidWaste/Default.aspx?w={w}", "ApplicationList")


def _annual_detail_value(block: str, title: str) -> str:
    """Read one detailControl span value by title from an annual-report block.
    Attribute-order tolerant (matches the span first, then reads title/value) —
    the same tolerance _detail_rows() has, because the WDS annual grid renders
    these spans in varying attribute orders too (class-first vs title-first). A
    strict single regex that required title-before-class silently returned '' on
    the class-first rows, which blanked capacity / years-remaining and killed the
    R1 airspace alert (see docs/decisions/009-wds-stream-c.md)."""
    want = title.rstrip(":").strip()
    for m in re.finditer(
        r'<span\b([^>]*\bclass="[^"]*detailControl[^"]*"[^>]*)>(.*?)</span>',
        block, re.S | re.I,
    ):
        attrs, inner = m.group(1), m.group(2)
        t = re.search(r'title="([^"]*)"', attrs)
        if t and t.group(1).rstrip(":").strip() == want:
            return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
    return ""


def _annual_waste_row(block: str):
    """The (year, {waste-type: volume}) tuple from a report block's tonnage table."""
    for tr in re.findall(r"<tr\b.*?</tr>", block, re.S | re.I):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        cells = [c for c in cells if c]
        if len(cells) >= 7 and re.fullmatch(r"\d{4}", cells[0]):
            return cells[0], dict(zip(
                ["MCW", "IW", "C&D", "ADC", "Other", "Waste_Total"],
                [c.replace(" CYDS", "") for c in cells[1:7]]))
    return None, {}


def _parse_annual(h: str) -> list[dict]:
    """Pure parse of an AnnualLandfillReports page: per-report block -> row dict
    joining the waste-tonnage table to the capacity/years-remaining detail-spans.
    One row per year. Split out from fetch_annual so the parser is unit-testable
    (the strict-regex attribute-order bug lived exactly here, untested)."""
    starts = []
    for m in re.finditer(r'id="ctl00_Body_ReportList_R_ctl(\d+)_', h):
        if not starts or starts[-1][0] != m.group(1):
            starts.append((m.group(1), m.start()))

    rows = []
    for k, (_ri, pos) in enumerate(starts):
        b = h[pos:(starts[k + 1][1] if k + 1 < len(starts) else len(h))]
        wyr, wvol = _annual_waste_row(b)
        ym = re.search(r">(\d{4})<", b)
        yr = wyr or (ym.group(1) if ym else "")
        if not yr:
            continue
        rec = {
            "Year": yr,
            "Total Permitted Capacity": _annual_detail_value(b, "Total Permitted Capacity:"),
            "Capacity Used This Year": _annual_detail_value(b, "Capacity used during this reporting year:"),
            "Yrs Remaining Start": _annual_detail_value(b, "Estimated years of capacity remaining at start of year:"),
            "Yrs Remaining End": _annual_detail_value(b, "Estimated years of capacity remaining at end of year:"),
        }
        rec.update(wvol)
        rows.append(rec)
    return rows


def fetch_annual(w: str) -> list[dict]:
    """Annual Landfill Reports: waste-volume grid joined to per-report capacity
    detail-spans (permitted capacity + years-remaining). One row per year."""
    return _parse_annual(_get(_opener(), f"{_BASE}/SolidWaste/AnnualLandfillReports.aspx?w={w}"))


def fetch_evaluations(w: str) -> list[dict]:
    return _paged_cme(_opener(), f"{_BASE}/Cme/Evaluations.aspx?w={w}",
                      "EvalL", _parse_evaluations)


def fetch_compliance_actions(w: str) -> list[dict]:
    return _paged_cme(_opener(), f"{_BASE}/Cme/ComplianceActions.aspx?w={w}",
                      "ComplianceActionsL", _parse_compliance_actions)


FETCHERS = {
    "qmr": fetch_qmr,
    "applications": fetch_applications,
    "annual": fetch_annual,
    "evaluations": fetch_evaluations,
    "compliance_actions": fetch_compliance_actions,
}
