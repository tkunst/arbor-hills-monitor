"""
recheck_key_data_points.py — ADR 067 repair path for rows ALREADY in the case
file whose classifier text fails kdp_check (the live pipeline classifies each
document once, so the prompt/check fix only protects new filings).

Three steps, each opt-in:

  1. (default) SCAN, free: read the New/Historical Documents tabs, run
     kdp_check on every Summary + Key Data Point, list the failing documents.
  2. --run-llm: for each failing document, get the PDF its row links to (a
     Drive Mirror link listed in Archived PDFs is fetched from its public
     link with no credentials; a MiEnviro link is downloaded from nSITE,
     CODER-PLAN step 6)
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
import re
import sys
import tempfile
import time
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


_ALLOWED_DOWNLOAD_PREFIX = "https://mienviro.michigan.gov/ncore/downloadpdf/"


_DRIVE_LINK = re.compile(r"^https://drive\.google\.com/file/d/([A-Za-z0-9_-]+)(?:/|$|\?)")


_PUBLIC_DRIVE_DOWNLOAD = "https://drive.usercontent.google.com/download"
_MAX_PDF_BYTES = 200 * 1024 * 1024
# Whole-download ceiling (requests' timeout is per connect/read, not total).
_MAX_DOWNLOAD_S = 300


class MirrorLinkRefused(RuntimeError):
    """A Drive link whose file id is not an Archived PDFs Mirror copy."""


class MirrorThrottled(RuntimeError):
    """Drive answered with an HTML page (a sign-in, quota or interstitial
    page), not the file: usually throttling, or a file that isn't public."""


class MirrorTooLarge(RuntimeError):
    """Over the size cap or the whole-download time ceiling."""


class NotAPdf(RuntimeError):
    """The fetched bytes are not a PDF."""


def _public_drive_download(file_id: str, dest: str, clock=None) -> None:
    """Fetch a PUBLIC ("anyone with the link") Drive file with NO credentials.
    The Mirror copies feed rows link to are public by design, so this needs
    no secret, and a non-public file simply fails: the service account can't
    be used to read anything private (no confused deputy). A session that
    ignores the environment (no ~/.netrc, no proxy credentials), a fixed https
    host, a size cap and a total-time ceiling."""
    import time

    import requests

    clock = clock or time.monotonic
    started = clock()
    with requests.Session() as sess:
        sess.trust_env = False
        with sess.get(_PUBLIC_DRIVE_DOWNLOAD,
                      params={"id": file_id, "export": "download", "confirm": "t"},
                      stream=True, timeout=120) as r:
            r.raise_for_status()
            if "text/html" in (r.headers.get("Content-Type") or "").lower():
                raise MirrorThrottled("Drive returned an HTML page")
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    size += len(chunk)
                    if size > _MAX_PDF_BYTES or clock() - started > _MAX_DOWNLOAD_S:
                        raise MirrorTooLarge("over the size or time cap")
                    f.write(chunk)


def mirror_file_ids(service, sheet_id: str) -> set:
    """Drive file ids of the nSITE Mirror copies (Archived PDFs): the only
    Drive files _download() will read."""
    ids = set()
    for link in sw.archived_doc_links(service, sheet_id, strict=True).values():
        m = _DRIVE_LINK.match(link or "")
        if m:
            ids.add(m.group(1))
    return ids


def _download(url: str, dest: str, downloader=None, mirror_ids: set | None = None) -> None:
    # Feed rows link to the Drive Mirror copy (most rows since ADR 007's
    # inline mirror) or to MiEnviro. A Mirror copy is fetched by id from its
    # PUBLIC link, with no credentials (no nSITE download, never a Drive
    # write), and only if that id is in Archived PDFs; nothing else is
    # fetched: never file:/ or another scheme/host taken from Sheet text.
    m = _DRIVE_LINK.match(url or "")
    if m:
        if m.group(1) not in (mirror_ids or set()):
            raise MirrorLinkRefused("Drive link is not an Archived PDFs Mirror copy")
        (downloader or _public_drive_download)(m.group(1), dest)
        with open(dest, "rb") as fh:
            if b"%PDF" not in fh.read(1024):   # readers accept leading junk
                raise NotAPdf("Mirror copy is not a PDF")
        return
    if not (url or "").startswith(_ALLOWED_DOWNLOAD_PREFIX):
        raise RuntimeError(f"refusing to download non-MiEnviro link: {(url or '')[:80]}")
    req = urllib.request.Request(url, headers={"User-Agent": "arbor-hills-monitor"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310  # nosec B310 — https MiEnviro prefix enforced above
        data = resp.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"not a PDF: {url}")
    with open(dest, "wb") as fh:
        fh.write(data)


# Stop starting new documents this long after the PROCESS started, and the
# proposals file is rewritten after every document. kdp-recheck.yml kills
# this step at 20 min (leaving room for one slow last document) and uploads
# the file with if: always(), so even a cut-off run keeps what it finished.
# Items not reached show "n/a".
DEFAULT_MAX_MINUTES = 14
_PROCESS_START = time.monotonic()


def run_llm(items: list[dict], cfg: dict, mirror_ids: set | None = None,
            max_minutes: float = DEFAULT_MAX_MINUTES, clock=None, started=None,
            on_item=None) -> int:
    """Returns how many items were not reached because of the time budget.
    `on_item()` runs after each document (main() saves the proposals there)."""
    from egle_doc_parser import parse_document
    from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS
    clock = clock or time.monotonic
    started = _PROCESS_START if started is None and clock is time.monotonic else (
        clock() if started is None else started)
    for n, it in enumerate(items):
        if (clock() - started) / 60 >= max_minutes:
            return len(items) - n
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "doc.pdf")
            try:
                _download(it["link"], local, mirror_ids=mirror_ids)
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
                # Class only: the proposals file is a public-repo artifact, and
                # a Drive error's text carries the file id / request URL.
                it["error"] = type(e).__name__
        if on_item is not None:
            on_item()
    return 0


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
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="with --run-llm: start no new document after this long")
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
    path = None
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        path = os.path.join(a.out_dir, "kdp-recheck-proposals.json")

    def save():
        if path:
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(items, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, path)

    if a.run_llm and items:
        try:
            mirror_ids = mirror_file_ids(service, sheet_id)
        except Exception as e:  # noqa: BLE001 — strict read failed: stop, class only (public log)
            print(f"could not read Archived PDFs ({type(e).__name__}); nothing checked")
            return 1
        left = run_llm(items, cfg, mirror_ids=mirror_ids,
                       max_minutes=a.max_minutes, on_item=save)
        if left:
            print(f"time budget reached: {left} document(s) not checked this run")
    for it in items:
        if a.counts_only:
            # Public CI log: no link (a Drive file id is not printed there).
            print(f"\n{it['tab']} row {it['row']}  {it['date_filed']}")
            print(f"  problems: {sum(len(v) for v in it['problems'].values())}"
                  f"  proposal: {'yes' if 'new' in it else 'ERROR' if 'error' in it else 'n/a'}")
            continue
        print(f"\n{it['tab']} row {it['row']}  {it['date_filed']}  {it['link']}")
        print(f"  problems: {it['problems']}")
        print(f"  OLD [{it['old']['severity']}] KDP: {it['old']['key_data_point']}")
        if "new" in it:
            n = it["new"]
            print(f"  NEW [{n['severity']}] KDP: {n['key_data_point']}")
            print(f"  NEW summary: {n['summary']}")
        if "error" in it:
            print(f"  ERROR: {it['error']}")
    if path:
        save()
        print(f"\nproposals: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
