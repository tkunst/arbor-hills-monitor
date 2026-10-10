"""
backfill_document_dates_titles.py — ADR 065 dry-run review. NO Sheet writes,
ever — this is Step 1 of the handoff's gated backfill: "write a review CSV
and STOP. Trisha reviews it." A separate apply step (not this script) writes
the two new columns to the live Sheet, only after her explicit go.

For every doc_id already in the case file's `_state.processed` (read-only —
the Sheets creds this needs are the same ones watcher.py/backfill.py already
use: GDRIVE_SA_KEY + GSHEET_ID):

  Phase 1 (always runs, free — no LLM, no OCR): get the document's RAW PDF
  text and run egle_doc_parser's deterministic date extractor. Source, in
  priority order:
    1. A local PDF already on disk under --pdf-dir, named <SRN>_<doc_id>.pdf
       (the Case File Mirror convention archiver.py/every other entry point
       in this repo uses).
    2. A fresh nSITE download (keyless — nc.download_pdf, same call every
       other job here makes) when --pdf-dir is unset or the file isn't there.
  No OCR is run in this phase (ocrmypdf costs real time across ~2,300 docs);
  an image-only PDF with no text layer simply yields no deterministic date
  here — Phase 2 (if requested) catches it with the full OCR'd pipeline.

  Phase 2 (--run-llm only, costs real money): for the docs STILL needing
  something — a generic nSITE title (needs display_title) OR no document_date
  found in Phase 1 — run the SAME egle_doc_parser.parse_document() the live
  pipeline uses (OCR-if-needed, deterministic pass again on the OCR'd text,
  then the LLM fallback/display_title). Reusing the exact live call means
  this review measures the SAME behavior production will show, not an
  approximation. Prints a cost estimate and asks for confirmation (skip with
  --yes) before spending anything.

Writes backfill-review-<date>.csv to --out-dir (REQUIRED — never defaults
into this repo; data-guard blocks *.csv, and the file carries name_check
results Trisha hasn't cleared for a public repo). Columns: doc_id, site,
date_filed, document_date, method, nsite_title, proposed_display_title,
name_check_result.

Usage:
  python3 scripts/backfill_document_dates_titles.py --out-dir /path/to/dir \\
      [--pdf-dir /path/to/case-file-mirror] [--limit N] [--run-llm] [--yes]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import drive_client as dc  # noqa: E402
import document_titles as dt  # noqa: E402
import nsite_client as nc  # noqa: E402
import sheet_writer as sw  # noqa: E402
from egle_doc_parser import extract_document_date_from_text, parse_document  # noqa: E402
from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS  # noqa: E402
from config_loader import load_config  # noqa: E402

CSV_FIELDS = [
    "doc_id", "site", "date_filed", "document_date", "method",
    "nsite_title", "proposed_display_title", "name_check_result",
]

# Historical reference point for the cost estimate (config.yml's own backfill
# cost note): ~754 docs cost ~$2-4 total on Haiku ($1/$5 per 1M tok) -- about
# $0.003-0.005/doc. Sonnet 5 (the currently-configured model) is 2x Haiku's
# per-token price on both input and output, so ~$0.006-0.01/doc is the
# comparable estimate here. A clearly-labeled estimate, not a measurement --
# the printed range is deliberately wide.
_EST_COST_PER_DOC_LOW = 0.006
_EST_COST_PER_DOC_HIGH = 0.010


def _local_pdf_path(pdf_dir: str | None, srn: str, doc_id: str) -> str | None:
    if not pdf_dir:
        return None
    path = os.path.join(pdf_dir, f"{srn}_{doc_id}.pdf")
    return path if os.path.exists(path) else None


def _raw_text_phase1(session, doc: dict, pdf_dir: str | None, tmp_dir: str) -> str | None:
    """Phase 1: RAW text of the first 2 pages, no OCR. Returns None if the PDF
    can't be obtained at all (e.g. the doc is no longer on nSITE and no local
    mirror copy exists)."""
    srn = doc.get("facility_srn", "N2688")
    did = doc["doc_id"]
    local = _local_pdf_path(pdf_dir, srn, did)
    downloaded = False
    if local is None:
        local = os.path.join(tmp_dir, f"{srn}_{did}.pdf")
        try:
            nc.download_pdf(session, doc, local)
            downloaded = True
        except Exception as e:  # noqa: BLE001 — a dry-run review must never crash on one doc
            print(f"  [{did}] could not fetch PDF: {e}")
            return None
    try:
        pdf = fitz.open(local)
        try:
            n = min(len(pdf), 2)
            return "\n".join(pdf[i].get_text() for i in range(n))
        finally:
            pdf.close()
    finally:
        if downloaded and os.path.exists(local):
            os.remove(local)


def _name_check_result(display_title: str) -> str:
    if not display_title:
        return "n/a (no display_title proposed)"
    cleaned = dt.sanitize_display_title(display_title)
    if not cleaned:
        return "BLOCKED — could not be made publish-clean, falls back to nSITE title"
    if cleaned != display_title:
        return f"cleaned: {cleaned!r}"
    return "clean"


def run(out_dir: str, pdf_dir: str | None, limit: int | None,
        run_llm: bool, assume_yes: bool) -> int:
    cfg = load_config()
    sheet_id = os.environ["GSHEET_ID"]
    title_cfg = cfg.get("document_titles") or {}
    generic_exact = title_cfg.get("generic_exact") or []
    generic_prefixes = title_cfg.get("generic_prefixes") or []

    sheets = dc.sheets_service()
    state = sw.read_state(sheets, sheet_id)
    processed = state["processed"]

    session = nc.make_session()
    docs = nc.fetch_all_documents(session, cfg)
    by_id = {d["doc_id"]: d for d in docs}

    doc_ids = list(processed.keys())
    if limit:
        doc_ids = doc_ids[:limit]
    print(f"[backfill-review] {len(doc_ids)} tracked document(s) to review "
          f"({'LLM pass enabled' if run_llm else 'deterministic pass only'}).")

    tmp_dir = tempfile.mkdtemp(prefix="arbor-hills-backfill-review-")
    rows: list[dict] = []
    llm_candidates: list[tuple[dict, dict, bool]] = []  # (doc, payload, is_generic)

    for did in doc_ids:
        payload = processed.get(did) or {}
        live = by_id.get(did)
        nsite_title = payload.get("egle_title") or payload.get("document_name", "")
        site = (live or {}).get("facility_srn", "unknown")
        date_filed = payload.get("date_filed", "")
        is_generic = dt.title_is_generic(nsite_title, generic_exact, generic_prefixes)

        if live is None:
            rows.append({
                "doc_id": did, "site": site, "date_filed": date_filed,
                "document_date": "", "method": "doc no longer on nSITE — not re-checked",
                "nsite_title": nsite_title, "proposed_display_title": "",
                "name_check_result": "",
            })
            continue

        text = _raw_text_phase1(session, live, pdf_dir, tmp_dir)
        if text is None:
            rows.append({
                "doc_id": did, "site": site, "date_filed": date_filed,
                "document_date": "", "method": "PDF unavailable (fetch failed)",
                "nsite_title": nsite_title, "proposed_display_title": "",
                "name_check_result": "",
            })
            continue

        det_date, det_method = extract_document_date_from_text([text])

        needs_llm = is_generic or not det_date
        if needs_llm and run_llm:
            llm_candidates.append((live, payload, is_generic))
            continue  # Phase 2 fills this row in below

        rows.append({
            "doc_id": did, "site": site, "date_filed": date_filed,
            "document_date": det_date, "method": det_method or "none found",
            "nsite_title": nsite_title, "proposed_display_title": "",
            "name_check_result": "",
        })

    n_llm = len(llm_candidates)
    if n_llm and run_llm:
        est_low = n_llm * _EST_COST_PER_DOC_LOW
        est_high = n_llm * _EST_COST_PER_DOC_HIGH
        print(f"\n[backfill-review] {n_llm} document(s) need an LLM pass "
              f"(generic title or no deterministic date) using model "
              f"{cfg['anthropic_model']!r}.")
        print(f"[backfill-review] ESTIMATED cost: ${est_low:.2f}-${est_high:.2f} "
              f"(a rough estimate from this repo's own historical backfill cost "
              f"note, scaled for the current model's pricing — not a measurement).")
        if not assume_yes:
            resp = input("Proceed with the LLM pass? [y/N] ").strip().lower()
            if resp != "y":
                print("[backfill-review] Skipping the LLM pass; "
                      "writing the deterministic-only results.")
                run_llm = False

    if n_llm and run_llm:
        model = cfg["anthropic_model"]
        for live, payload, is_generic in llm_candidates:
            did = live["doc_id"]
            srn = live.get("facility_srn", "N2688")
            nsite_title = payload.get("egle_title") or payload.get("document_name", "")
            date_filed = payload.get("date_filed", "")
            local = _local_pdf_path(pdf_dir, srn, did)
            downloaded = False
            if local is None:
                local = os.path.join(tmp_dir, f"{srn}_{did}.pdf")
                try:
                    nc.download_pdf(session, live, local)
                    downloaded = True
                except Exception as e:  # noqa: BLE001
                    print(f"  [{did}] LLM pass: could not fetch PDF: {e}")
                    rows.append({
                        "doc_id": did, "site": srn, "date_filed": date_filed,
                        "document_date": "", "method": "PDF unavailable (fetch failed)",
                        "nsite_title": nsite_title, "proposed_display_title": "",
                        "name_check_result": "",
                    })
                    continue
            try:
                parsed = parse_document(
                    local, live, RISK_REGISTER, model=model,
                    signal_keywords=SIGNAL_KEYWORDS,
                    page_threshold=cfg["large_doc_page_threshold"],
                    max_keyword_pages=cfg["large_doc_max_keyword_pages"],
                    max_tokens=cfg["classification_max_tokens"],
                    title_is_generic=is_generic,
                )
                rows.append({
                    "doc_id": did, "site": srn, "date_filed": date_filed,
                    "document_date": parsed.document_date,
                    "method": parsed.document_date_method or "none found",
                    "nsite_title": nsite_title,
                    "proposed_display_title": parsed.display_title,
                    "name_check_result": _name_check_result(parsed.display_title),
                })
                print(f"  ok  {did}  date={parsed.document_date or '(none)'}")
            except Exception as e:  # noqa: BLE001 — one doc's failure never aborts the review
                print(f"  [{did}] LLM pass failed: {e}")
                rows.append({
                    "doc_id": did, "site": srn, "date_filed": date_filed,
                    "document_date": "", "method": f"LLM pass error: {e}",
                    "nsite_title": nsite_title, "proposed_display_title": "",
                    "name_check_result": "",
                })
            finally:
                if downloaded and os.path.exists(local):
                    os.remove(local)
    elif n_llm:
        # --run-llm not set at all (not just declined above) — note the gap.
        for live, payload, _ in llm_candidates:
            did = live["doc_id"]
            rows.append({
                "doc_id": did, "site": live.get("facility_srn", "unknown"),
                "date_filed": payload.get("date_filed", ""),
                "document_date": "", "method": "LLM pass not run (--run-llm not set)",
                "nsite_title": payload.get("egle_title") or payload.get("document_name", ""),
                "proposed_display_title": "", "name_check_result": "",
            })

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"backfill-review-{date.today().isoformat()}.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n[backfill-review] wrote {len(rows)} row(s) to {out_path}")
    print("[backfill-review] NO Sheet writes were made. This is a dry run only "
          "— review the CSV, then run the (separate, gated) apply step.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", required=True,
                     help="Directory to write the review CSV into (NEVER this repo).")
    ap.add_argument("--pdf-dir", default=None,
                     help="Local Case File Mirror directory (<SRN>_<doc_id>.pdf files). "
                          "Falls back to a fresh nSITE download when unset or missing.")
    ap.add_argument("--limit", type=int, default=None,
                     help="Review at most this many tracked documents (for a quick check).")
    ap.add_argument("--run-llm", action="store_true",
                     help="Also run the LLM pass for generic-title/date-miss docs "
                          "(costs real money — a cost estimate is shown first).")
    ap.add_argument("--yes", action="store_true",
                     help="Skip the interactive confirmation before the LLM pass "
                          "(for a non-interactive run).")
    args = ap.parse_args()
    return run(args.out_dir, args.pdf_dir, args.limit, args.run_llm, args.yes)


if __name__ == "__main__":
    sys.exit(main())
