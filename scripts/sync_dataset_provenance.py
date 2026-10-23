"""sync_dataset_provenance.py -- seed the "Dataset Provenance" tab and its
annotation columns from the worker's provenance-map.csv
(coder:dataset-provenance-sheet, design §7 steps a-d; see dataset_provenance.py).

  python scripts/sync_dataset_provenance.py --map <provenance-map.csv>            # DRY-RUN
  python scripts/sync_dataset_provenance.py --map <provenance-map.csv> --apply    # writes
  add --release-csv <the release's wellfield CSV> to take row counts from it

The map lives in Lotext
(documents/arbor-hills/wellfield-auto-staging/provenance-map.csv); it is a data
file, so it is never committed here. Only its `wellfield-data-*` rows are used.

The map is the worker's 2026-10-16 read, and two things in it were wrong when
checked against the real files on 2026-10-22. They are corrected here in code,
so the CSV stays as the worker wrote it:
  * Drive file ids are cut to 20 characters. Each is resolved to the one full
    id in Hand-Curated Files that starts with it (zero or several = stop).
  * The Nov 2022 row's candidate nSITE id 4283353693233058745 is an air
    emission test report (19 pages), not the 73-page Sep-Oct 2022 WOI report
    bundled in the release. It is dropped (REJECTED_NSITE_IDS).
A row with no verified document id (Nov 2022, Mar-Apr 2023) gets
"REL-<bundled source file>" and a link to the release, which bundles that file.

What --apply writes (the target cells were all blank on 2026-10-22):
  * Dataset Provenance: the tab, plus one row per wellfield map row.
  * Archived PDFs same_content_as: ARCHIVE_PAIRS.
  * Hand-Curated Files same_as_monitor_doc: HAND_CURATED_MATCHES.
  * New/Historical Documents dataset_status: the pointer formulas.
Nothing else. `--include-proposed` adds the PROPOSED_* pairs, which were
verified the same way but are not in the approved design; they are for Trisha
to approve first. Rollback: delete the tab and clear the three annotation
columns (L on the feed tabs, J on Archived PDFs, N on Hand-Curated Files).

Don't run it while a backfill FORCE_REPROCESS purge is running (rows are
addressed by number). Credentials: GDRIVE_SA_KEY + GSHEET_ID (a .env in the
current directory is loaded if present).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataset_provenance as dp  # noqa: E402

# Checked 2026-10-22 against the mirrored PDF: an "AIR EMISSION TEST REPORT"
# for a landfill gas fueled turbine (19 pages), not the Sep-Oct 2022 WOI report.
REJECTED_NSITE_IDS = {"4283353693233058745"}

# A second filing of the same report: rows_contributed 0, status "covered".
# 8700874927542414020 parses to the same 3,456 readings as -2136493721019571731
# and as the release's WOI-status-report-2023-Q2.pdf (identical sets, 2026-10-22).
COVERED = {"8700874927542414020": "-2136493721019571731"}

# Extra status text for a published document (from the map's own notes).
STATUS_NOTES = {"-6975595422894935530": "needs review: 114 rows validation_ok=no"}

# Approved in the design (§4, §5).
ARCHIVE_PAIRS = dict(COVERED)
HAND_CURATED_MATCHES = {
    "2025-01-06-arbor-hills-woi-status-report-second-semi-annual-2024.pdf": "7894782240984169987",
    "2026-08-07-gfl-response-to-egle-re-subsurface-oxidation-event-n2688.pdf": "409490399157019600",
}

# Verified 2026-10-22, NOT in the approved design: written only with
# --include-proposed, after Trisha approves.
PROPOSED_ARCHIVE_PAIRS = {
    # Byte-identical (SHA-256) to the canonical copy.
    "-8194605618676510356": "7894782240984169987",   # 2024 H2
    "-7583536641080008007": "7022559137978826651",   # 2025 H1
    "-7728912290220215253": "7022559137978826651",   # 2025 H1
    # Third Q2 2023 filing; same 3,456 parsed readings.
    "1230339285137039248": "-2136493721019571731",
}
PROPOSED_HAND_CURATED_MATCHES = {
    # Its origin_url is the same nSITE document as the GFL response above.
    "2026-08-07-arbor-hills-egle-sso-records-review-email.pdf": "409490399157019600",
}


def release_row_counts(path: str) -> dict:
    """{source_report: row count} from the release's wellfield CSV."""
    counts: dict = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            counts[r["source_report"]] = counts.get(r["source_report"], 0) + 1
    return counts


def build_seed_rows(map_rows: list[dict], hc_drive_ids: list[str],
                    release_counts: dict | None = None) -> list[dict]:
    """The provenance rows for the map's wellfield rows, without title/link for
    nSITE and HC ids (filled from the Sheet by dataset_provenance.describe).
    `release_counts` (from release_row_counts) replaces the map's row counts:
    the map's Jan-Feb 2023 count (4,228) is 4 short of the release's."""
    out = []
    for m in map_rows:
        if not m.get("dataset", "").startswith("wellfield-data-"):
            continue
        nid = (m.get("nsite_doc_id") or "").strip()
        prefix = (m.get("drive_file_id") or "").strip()
        if nid and nid not in REJECTED_NSITE_IDS:
            doc_id = nid
        elif prefix:
            doc_id = "HC-" + dp.resolve_drive_prefix(prefix, hc_drive_ids)
        else:
            doc_id = "REL-" + m["bundled_source_file"].strip()
        row = {
            "doc_id": dp.check_doc_id(doc_id),
            "dataset": m["dataset"].strip(),
            "source_report": m["source_report"].strip(),
            "first_release_tag": m["first_release_tag"].strip(),
            "rows_contributed": (m.get("rows_in_latest") or "").strip(),
        }
        if release_counts is not None:
            if row["source_report"] not in release_counts:
                raise dp.ProvenanceError(f"{row['source_report']!r} is not in the release CSV")
            row["rows_contributed"] = str(release_counts[row["source_report"]])
        if doc_id in COVERED:
            row["status"] = dp.covered_status(COVERED[doc_id])
            row["rows_contributed"] = "0"
        else:
            row["status"] = dp.published_status(row["first_release_tag"])
            if doc_id in STATUS_NOTES:
                row["status"] += "; " + STATUS_NOTES[doc_id]
        if not dp._clean(row["source_report"]):
            raise dp.ProvenanceError(f"source_report fails name_check: {row['source_report']!r}")
        if doc_id.startswith("REL-"):
            row["document_title"] = (f"{row['source_report']}: {doc_id[4:]} "
                                     f"(source file in the {row['dataset']} release bundle)")
            row["link"] = dp.RELEASE_URL.format(tag=row["dataset"])
        out.append(row)
    # Two copies feeding one label together (Exceedance Apr 2024: PDF + xlsx):
    # the map's row count is their combined contribution, so say so on both.
    groups: dict = {}
    for r in out:
        if not r["status"].startswith("covered"):
            groups.setdefault((r["dataset"], r["source_report"]), []).append(r)
    for rows in groups.values():
        if len(rows) > 1:
            for r in rows:
                others = ", ".join(o["doc_id"] for o in rows if o is not r)
                r["rows_contributed"] = f"{r['rows_contributed']} (combined with {others})"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seed the Dataset Provenance tab + annotation columns.")
    ap.add_argument("--map", required=True, help="Lotext provenance-map.csv")
    ap.add_argument("--apply", action="store_true", help="Write the live Sheet (default: dry-run).")
    ap.add_argument("--release-csv", help="The release's wellfield CSV; row counts come from it.")
    ap.add_argument("--include-proposed", action="store_true",
                    help="Also write the PROPOSED_* pairs (only after Trisha approves them).")
    args = ap.parse_args(argv)

    from backfill_metric_taxonomy import _load_dotenv
    _load_dotenv()
    import drive_client

    with open(args.map, newline="") as fh:
        map_rows = list(csv.DictReader(fh))
    service = drive_client.sheets_service()
    sheet_id = os.environ["GSHEET_ID"]
    hc_rows = dp._get(service, sheet_id, f"'{dp.sw.TAB_HANDCURATED}'!A2:J")
    hc_ids = [dp.drive_file_id(r[9]) for r in hc_rows if len(r) > 9 and dp.drive_file_id(r[9])]

    seed = build_seed_rows(map_rows, hc_ids,
                           release_row_counts(args.release_csv) if args.release_csv else None)
    info = dp.describe(service, sheet_id, [r["doc_id"] for r in seed if "link" not in r])
    for r in seed:
        if "link" not in r:
            r["document_title"], r["link"] = info[r["doc_id"]]
    pairs = dict(ARCHIVE_PAIRS, **(PROPOSED_ARCHIVE_PAIRS if args.include_proposed else {}))
    hc = dict(HAND_CURATED_MATCHES,
              **(PROPOSED_HAND_CURATED_MATCHES if args.include_proposed else {}))

    print(f"{len(seed)} provenance rows:")
    for r in seed:
        print(f"  {r['doc_id']:<42} {r['source_report'][:28]:<28} {r['rows_contributed']:<12} {r['status']}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dp.plan_upsert([], seed, now)       # duplicate keys / bad ids stop here, before any write
    dp.check_feed_headers(service, sheet_id)
    if args.apply:
        dp.ensure_tab(service, sheet_id)
    print("provenance upsert:", dp.upsert(service, sheet_id, seed, now, apply=args.apply))
    print("Archived PDFs same_content_as:", dp.annotate_archive_duplicates(
        service, sheet_id, pairs, apply=args.apply))
    print("Hand-Curated Files same_as_monitor_doc:", dp.annotate_hand_curated(
        service, sheet_id, hc, apply=args.apply))
    print("dataset_status pointers:", dp.sync_pointers(service, sheet_id, apply=args.apply))
    if not args.apply:
        print("\nDRY-RUN: no Sheet writes. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
