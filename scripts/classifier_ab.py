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
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-parser", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=24)
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
                o = old.parse_document(path, dict(meta), RISK_REGISTER, **kw)
                n = new.parse_document(path, dict(meta), RISK_REGISTER, **kw)
                text = getattr(n, "full_text", "") or ""
                rec.update(old=o.severity, new=n.severity,
                           co_spike=bool(text and rr._CO_SPIKE_RE.search(text)),
                           smoldering=bool(text and rr._SMOLDER_RE.search(text)),
                           field_sso=bool(text and rr._FIELD_SSO_RE.search(text)))
            except Exception as e:  # noqa: BLE001 -- one bad doc must not stop the A/B
                rec["error"] = type(e).__name__
            results.append(rec)
            flag = "" if rec.get("old") == rec.get("new") else "  <-- CHANGED"
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
