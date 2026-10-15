#!/usr/bin/env python3
"""One-off live A/B for PR #142 (classifier-untrusted-input): classify the same
real nSITE documents with main's egle_doc_parser (OLD) and this branch's
(NEW), using the production model, and report where severity changes.

READ-ONLY: reads the public case-file Sheet, downloads PDFs keylessly from
nSITE, calls the Anthropic API. Writes nothing to the Sheet, Drive or email.

Public Actions log rule: stdout carries doc ids, dates and severities only
(no titles, summaries or file names). The JSON artifact adds which floor
trigger fired, nothing else.

Usage (CI): python scripts/classifier_ab.py --old-parser <path to main's
egle_doc_parser.py> --out <json> [--limit N]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_loader  # noqa: E402
import drive_client as dc  # noqa: E402
import nsite_client as nc  # noqa: E402
import risk_register as rr  # noqa: E402
from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS  # noqa: E402

AT_RISK_RE = re.compile(
    r"Wells of Interest|\bWOI\b|\bHOV\b|subsurface oxidation|\bSSO\b|smolder|CO spike|carbon monoxide",
    re.IGNORECASE)
DOC_ID_RE = re.compile(r"/ncore/download(?:pdf|file)/(-?\d+)")


def _rows(svc, sid, tab):
    v = svc.spreadsheets().values().get(spreadsheetId=sid, range=f"'{tab}'").execute().get("values", [])
    if not v:
        return []
    head = v[0]
    return [dict(zip(head, r)) for r in v[1:]]


ENFORCEMENT_TITLE_RE = re.compile(
    r"violation notice|\bVN\b|enforcement notice|consent order|notice of violation", re.IGNORECASE)


def pick_enforcement(feed, n_pos, n_neg):
    """Real nSITE titles: n_pos that the title backstop should catch (newest
    first) and n_neg look-alikes it must NOT catch (correspondence / responses)."""
    pos, neg = [], []
    for r in sorted(feed, key=lambda r: r.get("Date Filed", ""), reverse=True):
        m = DOC_ID_RE.search(r.get("Link", ""))
        t = r.get("EGLE Title") or r.get("Document Name", "")
        if not m or not ENFORCEMENT_TITLE_RE.search(t):
            continue
        if rr.enforcement_title_signal(t):
            if len(pos) < n_pos:
                pos.append((m.group(1), r))
        elif len(neg) < n_neg:
            neg.append((m.group(1), r))
    return pos + neg


def pick_sample(feed, limit):
    """At-risk rows first (WOI/HOV/SSO/CO/smoldering language, newest first),
    then the newest rows overall, then recent urgent ones; deduped by doc id."""
    def doc_id(r):
        m = DOC_ID_RE.search(r.get("Link", ""))
        return m.group(1) if m else None

    feed = [r for r in feed if doc_id(r)]
    feed.sort(key=lambda r: r.get("Date Filed", ""), reverse=True)
    at_risk = [r for r in feed if AT_RISK_RE.search(
        " ".join([r.get("Summary", ""), r.get("Key Data Point", ""), r.get("Document Name", "")]))]
    urgent = [r for r in feed if r.get("Severity", "").lower() == "urgent"]
    out, seen = [], set()
    for group, cap in ((at_risk, limit // 2), (feed, limit // 3), (urgent, limit)):
        n = 0
        for r in group:
            i = doc_id(r)
            if i in seen or n >= cap or len(out) >= limit:
                continue
            seen.add(i)
            out.append((i, r))
            n += 1
    return out


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses needs the module registered before exec
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-parser", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--focus", default="", help="comma-separated doc ids to always include")
    ap.add_argument("--repeat", type=int, default=1, help="classifications per side for focus ids")
    ap.add_argument("--enforcement", type=int, default=0, help="N enforcement-title docs (+N/2 look-alikes)")
    a = ap.parse_args()

    cfg = config_loader.load_config()
    model = cfg["anthropic_model"]
    old = _load(a.old_parser, "egle_doc_parser_old")
    new = _load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "egle_doc_parser.py"), "egle_doc_parser_new")

    svc = dc.sheets_service()
    sid = os.environ["GSHEET_ID"]
    feed = _rows(svc, sid, "New Documents") + _rows(svc, sid, "Historical Documents")
    sample = pick_sample(feed, a.limit)
    focus = [x for x in a.focus.split(",") if x]
    by_id = {}
    for r in feed:
        m = DOC_ID_RE.search(r.get("Link", ""))
        if m:
            by_id.setdefault(m.group(1), r)
    extra = [(i, by_id[i]) for i in focus if i in by_id]
    if a.enforcement:
        extra += pick_enforcement(feed, a.enforcement, max(1, a.enforcement // 2))
    seen = {i for i, _ in sample}
    sample += [(i, r) for i, r in extra if i not in seen and not seen.add(i)]
    print(f"model={model} sample={len(sample)}")

    session = nc.make_session()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (doc_id, row) in enumerate(sample, 1):
            rec = {"doc_id": doc_id, "date_filed": row.get("Date Filed", ""),
                   "sheet_severity": row.get("Severity", "").lower()}
            path = os.path.join(tmp, f"{int(doc_id)}.pdf")
            meta = {"doc_id": doc_id, "doc_url": row.get("Link", ""),
                    "document_name": row.get("Document Name", ""),
                    "date_filed": row.get("Date Filed", ""), "type_name": row.get("Type", "")}
            try:
                nc.download_pdf(session, meta, path)
                kw = dict(model=model, signal_keywords=SIGNAL_KEYWORDS,
                          page_threshold=cfg["large_doc_page_threshold"],
                          max_keyword_pages=cfg["large_doc_max_keyword_pages"],
                          max_tokens=cfg["classification_max_tokens"])
                reps = a.repeat if doc_id in focus else 1
                olds = [old.parse_document(path, dict(meta), RISK_REGISTER, **kw) for _ in range(reps)]
                news = [new.parse_document(path, dict(meta), RISK_REGISTER, **kw) for _ in range(reps)]
                o, n = olds[0], news[-1]
                if reps > 1:
                    rec["old_all"] = [x.severity for x in olds]
                    rec["new_all"] = [x.severity for x in news]
                    rec["new_peak_measured_f"] = [
                        max([m.get("value") for m in (x.measurements or [])
                             if m.get("metric") == "temperature" and m.get("basis") == "measured"
                             and isinstance(m.get("value"), (int, float))] or [None],
                            key=lambda v: -1 if v is None else v)
                        for x in news]
                rec["enforcement_title"] = rr.enforcement_title_signal(
                    row.get("EGLE Title") or row.get("Document Name", ""))
                text = getattr(n, "full_text", "") or ""
                rec.update(old=o.severity, new=n.severity,
                           co_spike=bool(text and rr._CO_SPIKE_RE.search(text)),
                           smoldering=bool(text and rr._SMOLDER_RE.search(text)),
                           field_sso=bool(text and rr._FIELD_SSO_RE.search(text)))
            except Exception as e:  # noqa: BLE001 -- one bad doc must not stop the A/B
                rec["error"] = type(e).__name__
            results.append(rec)
            flag = "" if rec.get("old") == rec.get("new") else "  <-- CHANGED"
            if "old_all" in rec:
                flag += f"  old_all={rec['old_all']} new_all={rec['new_all']}"
            if rec.get("enforcement_title"):
                flag += "  [enforcement-title]"
            print(f"{i:2d} {doc_id} {rec['date_filed']} sheet={rec['sheet_severity']} "
                  f"old={rec.get('old', '-')} new={rec.get('new', '-')}"
                  f"{' err=' + rec['error'] if 'error' in rec else ''}{flag}")

    changed = [r for r in results if "error" not in r and r["old"] != r["new"]]
    print(f"done: {len(results)} docs, {len(changed)} severity changes, "
          f"{sum(1 for r in results if 'error' in r)} errors")
    with open(a.out, "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
