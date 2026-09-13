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


# URL builders, one per collection — shared by the parsed fetchers below and by
# fetch_raw_snapshot() (archival raw-HTML snapshots, no parsing; see wds_archiver.py).
_URLS = {
    "qmr": lambda w: f"{_BASE}/SolidWaste/QMRReports.aspx?w={w}",
    "applications": lambda w: f"{_BASE}/SolidWaste/Default.aspx?w={w}",
    "annual": lambda w: f"{_BASE}/SolidWaste/AnnualLandfillReports.aspx?w={w}",
    "evaluations": lambda w: f"{_BASE}/Cme/Evaluations.aspx?w={w}",
    "compliance_actions": lambda w: f"{_BASE}/Cme/ComplianceActions.aspx?w={w}",
    # penalties share the ComplianceActions page with compliance_actions (a nested
    # sub-grid, a DIFFERENT parse — see fetch_penalties); composting_registrations
    # and composting_reports are two inline grids on the Utilization landing page.
    # Two collections can therefore resolve to one URL — fetch_raw_snapshot / the
    # archiver de-dup by URL so each distinct page is snapshotted only once.
    "penalties": lambda w: f"{_BASE}/Cme/ComplianceActions.aspx?w={w}",
    "composting_registrations": lambda w: f"{_BASE}/Utilization/Default.aspx?w={w}",
    "composting_reports": lambda w: f"{_BASE}/Utilization/Default.aspx?w={w}",
}


def page_url(name: str, w: str) -> str:
    """The WDS page URL a collection reads. Public accessor over _URLS so
    wds_archiver can de-dup snapshots (penalties + compliance_actions share the
    ComplianceActions page; the two composting grids share the Utilization page)."""
    return _URLS[name](w)


def fetch_qmr(w: str) -> list[dict]:
    return _paged_detail(_opener(), _URLS["qmr"](w), "QMRReportList")


def fetch_applications(w: str) -> list[dict]:
    return _paged_detail(_opener(), _URLS["applications"](w), "ApplicationList")


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


def _annual_waste_totals(block: str) -> dict:
    """Total waste volume (CYDS) and amount (TONS) for one annual report block.

    The waste grid is an editable WebForms table: each stream's figures live in
    <input value="..."> attributes (WasteVolumeCYTextBox / WasteAmtTonsTextBox),
    NOT in <td> text — so we sum the input values across the block's rows. (The
    original <td>-cell reader matched a 7-column `year + 6 volumes` layout the
    page never renders, so it silently produced no tonnage at all — verified
    against the live 475946 page 2026-07-10.) Empty add-a-row template inputs are
    skipped; an all-empty field yields no key (graceful for pre-tonnage reports).
    Input-attribute-order tolerant (name/value read independently)."""
    def _sum(field: str):
        total, seen = 0.0, False
        for tag in re.findall(r"<input\b[^>]*>", block, re.I):
            nm = re.search(r'name="([^"]*)"', tag)
            if not nm or field not in nm.group(1):
                continue
            vm = re.search(r'value="([^"]*)"', tag)
            v = (vm.group(1) if vm else "").replace(",", "").strip()
            if not v:
                continue
            try:
                total += float(v)
                seen = True
            except ValueError:
                pass
        return total if seen else None

    out = {}
    cy = _sum("WasteVolumeCYTextBox")
    tons = _sum("WasteAmtTonsTextBox")
    if cy is not None:
        out["Waste_Total"] = f"{cy:,.2f}"
    if tons is not None:
        out["Waste_Tons"] = f"{tons:,.2f}"
    return out


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
        ym = re.search(r">(\d{4})<", b)
        yr = ym.group(1) if ym else ""
        if not yr:
            continue
        rec = {
            "Year": yr,
            "Total Permitted Capacity": _annual_detail_value(b, "Total Permitted Capacity:"),
            "Capacity Used This Year": _annual_detail_value(b, "Capacity used during this reporting year:"),
            "Yrs Remaining Start": _annual_detail_value(b, "Estimated years of capacity remaining at start of year:"),
            "Yrs Remaining End": _annual_detail_value(b, "Estimated years of capacity remaining at end of year:"),
        }
        rec.update(_annual_waste_totals(b))
        rows.append(rec)
    return rows


def fetch_annual(w: str) -> list[dict]:
    """Annual Landfill Reports: waste-volume grid joined to per-report capacity
    detail-spans (permitted capacity + years-remaining). One row per year."""
    return _parse_annual(_get(_opener(), _URLS["annual"](w)))


def fetch_evaluations(w: str) -> list[dict]:
    return _paged_cme(_opener(), _URLS["evaluations"](w), "EvalL", _parse_evaluations)


def fetch_compliance_actions(w: str) -> list[dict]:
    return _paged_cme(_opener(), _URLS["compliance_actions"](w),
                      "ComplianceActionsL", _parse_compliance_actions)


# --- Penalties (nested sub-grid on the ComplianceActions page) --------------
# A compliance action can carry penalty rows; WDS renders them as nested
# <tr ...SummaryRow> rows under the parent action's DetailEditRow. A penalty row's
# cells are [ , Penalty Type ("FA - ..."), Assessment Amount ($), Document #,
# Payment ID, ], optionally FOLLOWED in document order by a payment row
# [Sched Date, $Sched, Date Paid, $Paid]. This is a faithful port of the
# hand-verified Lotext scripts/wds_scrape_penalties.py — it reproduces the six
# 475946 penalty rows ($447,485.46 assessed) exactly (verified live 2026-09-13).

_PTYPE_RE = re.compile(r"^[A-Z]{2} - ")          # "FA - ...", "AC - ..."
_DATE_ONLY_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def _tr_cells(tr: str) -> list[str]:
    """Rendered <td> cell texts of one table row, with WebForms' exact-doubling
    collapse (a value sometimes renders twice inside its own cell)."""
    t = re.sub(r"<input\b[^>]*>", " ", tr, flags=re.I)
    t = re.sub(r"<select\b.*?</select>", " ", t, flags=re.S | re.I)
    out = []
    for c in re.findall(r"<td[^>]*>(.*?)</td>", t, re.S | re.I):
        c = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
        m = re.match(r"^(.*?)\s+\1$", c)
        if m:
            c = m.group(1).strip()
        out.append(c)
    return out


def _clean_row_text(block: str) -> str:
    c = re.sub(r"<input\b[^>]*>", " ", block, flags=re.I)
    c = re.sub(r"<select\b.*?</select>", " ", c, flags=re.S | re.I)
    return re.sub(r"[ \t\r\n]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c)))


def _parse_penalties_page(h: str) -> list[dict]:
    """Penalty records on ONE ComplianceActions page. The penalty->payment pairing
    walks summary rows in DOCUMENT ORDER, so it is done per page — a penalty and its
    payment row never straddle a page boundary on this grid (verified against
    475946: all six pair cleanly; test_penalty_and_payment_never_straddle_pages
    asserts it)."""
    # Parent-action map: which (date, type) each ctlNN row is a penalty of.
    actions = {}
    for m in re.finditer(
        r'id="ctl00_Body_ComplianceActionsL_R_(ctl\d+)_DetailEditRow"[^>]*>(.*?)</tr>',
        h, re.S,
    ):
        rec = _fields_from_text(_clean_row_text(m.group(2)), _CA_LABELS)
        actions[m.group(1)] = (rec.get("Compliance Action Date", ""),
                               rec.get("Compliance Action Type", ""))
    out, last = [], None
    for m in re.finditer(
        r'<tr[^>]*id="ctl00_Body_ComplianceActionsL_R_(ctl\d+)_T_[^"]*SummaryRow"[^>]*>(.*?)</tr>',
        h, re.S,
    ):
        parent = m.group(1)
        c = (_tr_cells(m.group(2)) + [""] * 6)[:6]
        if len(c) >= 4 and _PTYPE_RE.match(c[1]) and c[3]:
            ad, at = actions.get(parent, ("", ""))
            last = {
                "Action Date": ad, "Action Type": at, "Penalty Type": c[1],
                "Assessment Amount": c[2], "Document #": c[3], "Penalty Payment ID": c[4],
                "Scheduled Date": "", "Scheduled Amount": "", "Date Paid": "", "Amount Paid": "",
            }
            out.append(last)
        elif last is not None and _DATE_ONLY_RE.match(c[0]) and any("$" in x for x in c):
            # Payment row for the penalty just seen: [SchedDate, $Sched, DatePaid, $Paid].
            last["Scheduled Date"] = c[0]
            last["Scheduled Amount"] = c[1]
            last["Date Paid"] = c[2] if len(c) > 2 else ""
            last["Amount Paid"] = c[3] if len(c) > 3 else ""
            last = None
    return out


def fetch_penalties(w: str) -> list[dict]:
    """Penalty sub-grid across all ComplianceActions pages — same page + pager as
    fetch_compliance_actions, a different parse. Full-record de-dup guards against a
    page-repeat double-counting a penalty (mirrors _paged_detail's de-dup)."""
    op = _opener()
    url = _URLS["penalties"](w)
    h = _get(op, url)
    prefix, pe = _pager(h)
    out = _parse_penalties_page(h)
    sep = "&" if "?" in url else "?"
    for n in range(1, pe + 1):
        out += _parse_penalties_page(_get(op, url + sep + f"{prefix}={n}*_*0*0"))
    seen, clean = set(), []
    for r in out:
        sig = tuple(sorted(r.items()))
        if sig in seen:
            continue
        seen.add(sig)
        clean.append(r)
    return clean


# --- Composting (Utilization module: two inline grids) ----------------------
# Reg  = composting registrations — clean single-value detail rows, so the shared
#        _paged_detail reader handles it. RptYr = composting report-years; each row
#        REPEATS Product Types / quantity spans (yard clippings, finished compost,
#        ...), so _detail_rows' last-wins grouping would DROP the tonnages. Collect
#        EVERY span value under each RptYr row in document order into one opaque
#        fingerprint instead (a new year is new; a later tonnage backfill is
#        changed). The add-a-record template row renders Year "0" -> filtered by
#        requiring a 4-digit year.


def fetch_composting_registrations(w: str) -> list[dict]:
    return _paged_detail(_opener(), _URLS["composting_registrations"](w), "Reg")


_YEAR_RE = re.compile(r"^\d{4}$")


def _parse_composting_reports(h: str) -> list[dict]:
    rows: dict[str, dict] = {}
    order: list[str] = []
    for m in re.finditer(
        r'<span\b([^>]*\bclass="[^"]*detailControl[^"]*"[^>]*)>(.*?)</span>',
        h, re.S | re.I,
    ):
        attrs, inner = m.group(1), m.group(2)
        ri = re.search(r"RptYr_R_ctl(\d+)_", attrs)
        if not ri:
            continue
        t = re.search(r'title="([^"]*)"', attrs)
        title = t.group(1).rstrip(":").strip() if t else ""
        val = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        k = ri.group(1)
        if k not in rows:
            rows[k] = {"Year": "", "values": []}
            order.append(k)
        if title == "Year" and not rows[k]["Year"]:
            rows[k]["Year"] = val
        if val:
            rows[k]["values"].append(val)
    out = []
    for k in order:
        r = rows[k]
        if not _YEAR_RE.match(r["Year"]):     # drops the "Year: 0" add-a-record template
            continue
        out.append({"Year": r["Year"], "Report Detail": " | ".join(r["values"])})
    return out


def fetch_composting_reports(w: str) -> list[dict]:
    """Composting annual report-years on the Utilization page. De-dup by Year (the
    RptYr pager returns the same rows on each page — the same 'counts more than it
    displays' quirk Applications has; the watch is forward-looking, so the
    non-displayed older years being absent from the baseline is harmless)."""
    op = _opener()
    url = _URLS["composting_reports"](w)
    h = _get(op, url)
    prefix, pe = _pager(h)
    out = _parse_composting_reports(h)
    sep = "&" if "?" in url else "?"
    for n in range(1, pe + 1):
        out += _parse_composting_reports(_get(op, url + sep + f"{prefix}={n}*_*0*0"))
    seen, clean = set(), []
    for r in out:
        if r["Year"] in seen:
            continue
        seen.add(r["Year"])
        clean.append(r)
    return clean


FETCHERS = {
    "qmr": fetch_qmr,
    "applications": fetch_applications,
    "annual": fetch_annual,
    "evaluations": fetch_evaluations,
    "compliance_actions": fetch_compliance_actions,
    "penalties": fetch_penalties,
    "composting_registrations": fetch_composting_registrations,
    "composting_reports": fetch_composting_reports,
}


def fetch_raw_snapshot(name: str, w: str) -> list[str]:
    """Raw HTML for every page of collection `name` — archival snapshot only, no
    parsing. Used by wds_archiver.py as portal-drift insurance (WDS has no
    per-record PDFs to mirror the way nSITE does; a dated raw-HTML copy of the
    page itself is the real analog). Shares the exact pager/URL logic the parsed
    fetchers use, so a page this returns is byte-identical to what they parsed."""
    op = _opener()
    url = _URLS[name](w)
    h = _get(op, url)
    prefix, pe = _pager(h)
    pages = [h]
    sep = "&" if "?" in url else "?"
    for n in range(1, pe + 1):
        pages.append(_get(op, url + sep + f"{prefix}={n}*_*0*0"))
    return pages
