"""
recheck_key_data_points.py — ADR 067 repair path for rows ALREADY in the case
file whose classifier text fails kdp_check (the live pipeline classifies each
document once, so the prompt/check fix only protects new filings).

Three steps, each opt-in:

  1. (default) SCAN, free: read the New/Historical Documents tabs, run
     kdp_check on every Summary + Key Data Point, list the failing documents.
  2. --run-llm: for each failing document, download the PDF from its row link
     and run the SAME egle_doc_parser.parse_document() the live pipeline uses
     (new prompt + check + retry/withhold). Writes the proposals to
     <out-dir>/kdp-recheck-proposals.json. Still NO Sheet writes.
  3. --apply <proposals.json>: write exactly the reviewed proposals — Summary
     and Key Data Point on the feed row, Key Data Point + Full Summary on every
     Evidence by Risk row with the same Link, and Severity only with
     --update-severity. Nothing else: no Measurements/Deadlines rows, no
     alerts, no titles, no _state changes.

Usage:
  python3 scripts/recheck_key_data_points.py --out-dir DIR [--run-llm]
  python3 scripts/recheck_key_data_points.py --apply DIR/kdp-recheck-proposals.json [--update-severity] [--write]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drive_client as dc  # noqa: E402
import kdp_check  # noqa: E402
import sheet_writer as sw  # noqa: E402
from config_loader import load_config  # noqa: E402

FEED_TABS = (sw.TAB_NEW, sw.TAB_HISTORICAL)
F_SEV, F_SUM, F_KDP, F_LINK = (sw.FEED_HEADERS.index(h) for h in
                               ("Severity", "Summary", "Key Data Point", "Link"))
E_KDP, E_SUM, E_LINK = (sw.EVIDENCE_HEADERS.index(h) for h in
                        ("Key Data Point", "Full Summary", "Link"))


def _col(i: int) -> str:
    return chr(ord("A") + i)


def _cell(row: list, i: int) -> str:
    return row[i] if i < len(row) else ""


def scan(service, sheet_id: str, ref) -> list[dict]:
    out = []
    for tab in FEED_TABS:
        rows = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{tab}'!A2:K").execute().get("values", [])
        for n, r in enumerate(rows, start=2):
            fields = {"summary": _cell(r, F_SUM), "key_data_point": _cell(r, F_KDP)}
            problems = kdp_check.check_fields(fields, ref)
            if problems:
                out.append({"tab": tab, "row": n, "document_name": _cell(r, 1),
                            "date_filed": _cell(r, 0), "link": _cell(r, F_LINK),
                            "old_kdp_sha256": _sha(fields["key_data_point"]),
                            "old": {"severity": _cell(r, F_SEV),
                                    "summary": _redact(fields["summary"]),
                                    "key_data_point": _redact(fields["key_data_point"])},
                            "problems": problems})
    return out


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _redact(text: str) -> str:
    """Proposals can leave the machine (a public-repo Actions artifact), so all
    free text in them goes through the same name redaction the public feed uses."""
    from findings_feed import redact_names
    return redact_names(text) or ""


def _download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "arbor-hills-monitor"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"not a PDF: {url}")
    with open(dest, "wb") as fh:
        fh.write(data)


def run_llm(items: list[dict], cfg: dict) -> None:
    from egle_doc_parser import parse_document
    from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS
    for it in items:
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "doc.pdf")
            try:
                _download(it["link"], local)
                parsed = parse_document(
                    local, {"document_name": it["document_name"], "date_filed": it["date_filed"]},
                    RISK_REGISTER, model=cfg["anthropic_model"],
                    signal_keywords=SIGNAL_KEYWORDS,
                    page_threshold=cfg["large_doc_page_threshold"],
                    max_keyword_pages=cfg["large_doc_max_keyword_pages"],
                    max_tokens=cfg["classification_max_tokens"],
                    title_is_generic=False,
                )
                it["new"] = {"severity": parsed.severity, "summary": _redact(parsed.summary),
                             "key_data_point": _redact(parsed.key_data_point)}
            except Exception as e:  # noqa: BLE001 — report and keep going
                it["error"] = str(e)


def apply(service, sheet_id: str, items: list[dict], update_severity: bool,
          write: bool = False) -> None:
    """Without write=True this only PRINTS the exact ranges it would write."""
    ev = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{sw.TAB_EVIDENCE}'!A2:K").execute().get("values", [])
    data = []
    for it in items:
        new = it.get("new")
        if not new:
            continue
        # guard: only write over the exact text that was reviewed
        cur = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{it['tab']}'!A{it['row']}:K{it['row']}"
        ).execute().get("values", [[]])[0]
        if _sha(_cell(cur, F_KDP)) != it["old_kdp_sha256"] or _cell(cur, F_LINK) != it["link"]:
            print(f"SKIP (row changed since review): {it['tab']} row {it['row']}")
            continue
        r = it["row"]
        data.append({"range": f"'{it['tab']}'!{_col(F_SUM)}{r}", "values": [[new["summary"]]]})
        data.append({"range": f"'{it['tab']}'!{_col(F_KDP)}{r}", "values": [[new["key_data_point"]]]})
        if update_severity:
            data.append({"range": f"'{it['tab']}'!{_col(F_SEV)}{r}", "values": [[new["severity"]]]})
        for n, er in enumerate(ev, start=2):
            if _cell(er, E_LINK) == it["link"]:
                data.append({"range": f"'{sw.TAB_EVIDENCE}'!{_col(E_KDP)}{n}",
                             "values": [[new["key_data_point"]]]})
                data.append({"range": f"'{sw.TAB_EVIDENCE}'!{_col(E_SUM)}{n}",
                             "values": [[new["summary"]]]})
    if not data:
        print("nothing to write")
        return
    for d in data:
        print(f"  {d['range']}: {d['values'][0][0][:110]}")
    if not write:
        print(f"DRY RUN: {len(data)} cell(s) would be written; re-run with --write")
        return
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id, body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"wrote {len(data)} cell(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-dir", help="where proposals JSON goes (outside the repo)")
    ap.add_argument("--run-llm", action="store_true")
    ap.add_argument("--apply", metavar="PROPOSALS_JSON")
    ap.add_argument("--update-severity", action="store_true")
    ap.add_argument("--write", action="store_true", help="with --apply: actually write")
    ap.add_argument("--counts-only", action="store_true",
                    help="log rows/links/problem counts only (for public CI logs)")
    a = ap.parse_args()
    cfg = load_config()
    ref = kdp_check.reference_from_config(cfg)
    service = dc.sheets_service()
    sheet_id = os.environ["GSHEET_ID"]

    if a.apply:
        items = json.load(open(a.apply))
        apply(service, sheet_id, items, a.update_severity, write=a.write)
        return 0

    items = scan(service, sheet_id, ref)
    print(f"{len(items)} document row(s) fail kdp_check")
    if a.run_llm:
        run_llm(items, cfg)
    for it in items:
        print(f"\n{it['tab']} row {it['row']}  {it['date_filed']}  {it['link']}")
        if a.counts_only:
            print(f"  problems: {sum(len(v) for v in it['problems'].values())}"
                  f"  proposal: {'yes' if 'new' in it else 'ERROR' if 'error' in it else 'n/a'}")
            continue
        print(f"  problems: {it['problems']}")
        print(f"  OLD [{it['old']['severity']}] KDP: {it['old']['key_data_point']}")
        if "new" in it:
            n = it["new"]
            print(f"  NEW [{n['severity']}] KDP: {n['key_data_point']}")
            print(f"  NEW summary: {n['summary']}")
        if "error" in it:
            print(f"  ERROR: {it['error']}")
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        path = os.path.join(a.out_dir, "kdp-recheck-proposals.json")
        json.dump(items, open(path, "w"), indent=1, ensure_ascii=False)
        print(f"\nproposals: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
