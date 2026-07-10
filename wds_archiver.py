"""
wds_archiver.py — nightly raw-HTML snapshot of the 5 WDS collection pages into
Trisha's Drive (portal-drift insurance, ADR 009 residual risk #1).

WDS has no per-record PDFs to mirror the way archiver.py mirrors nSITE PDFs —
each "record" is a row rendered server-side by a 2001-era ASP.NET app; the
record IS the page. The durable-copy insurance here is a dated raw-HTML copy of
each collection's page(s), recorded in the "WDS Page Snapshots" tab, so a future
markup change or portal disappearance doesn't erase the only record of what a
page actually said on a given date.

DUAL CREDENTIAL (same pattern as archiver.py, see archive_client.py / ADR 007):
Sheet reads/writes go through the SERVICE ACCOUNT; the HTML upload goes through
the OAUTH user client. Reuses the SAME Drive mirror folder as the PDF archive —
distinct file names (WDS_<collection>_p<page>_<date>.html) keep the two artifact
types apart, so no new OAuth folder/setup is needed.

CONTENT-HASH GATED: uploads + logs a page only when its HTML differs from the
last snapshot taken (state["wds_snapshot_hashes"][collection][page]) — so a
nightly run doesn't pile up ~5-20 near-identical files every single night
forever. The job still polls nightly; it just doesn't always write.

  - Not configured (no GOAUTH_*): quiet no-op, exit 0 (snapshotting is optional).
  - Configured but the token is dead: loud failure, exit 1 — a silent skip would
    let the mirror fall behind invisibly, defeating the whole point.

Runs nightly (see .github/workflows/wds-archive.yml). Self-terminating.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from datetime import datetime

import drive_client as dc
import sheet_writer as sw
import wds_client as wc
import wds_watcher as ww
import archive_client as ac
from config_loader import load_config


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().date().isoformat()


def _hash(html: str) -> str:
    return hashlib.sha1(html.encode("utf-8", "ignore")).hexdigest()[:12]


def run() -> int:
    if not ac.is_configured():
        print("[wds-archive] GOAUTH_* not set — snapshotting disabled (no-op). "
              "See scripts/oauth_setup.py + docs/decisions/007.")
        return 0

    cfg = load_config()
    wds_cfg = cfg.get("wds") or {}
    w = str(wds_cfg.get("site_id", "475946"))
    enabled_collections = wds_cfg.get("collections") or list(ww.COLLECTIONS)
    sheet_id = os.environ["GSHEET_ID"]

    sheets = dc.sheets_service()
    sw.ensure_wds_tabs(sheets, sheet_id)

    # OAuth Drive: fail LOUDLY here if the refresh token is revoked/invalid, so
    # the workflow-failure email fires instead of the mirror silently stalling.
    try:
        drive = ac.oauth_drive_service()
    except Exception as e:  # noqa: BLE001
        print(f"[wds-archive] OAuth Drive auth FAILED ({e}). Re-run scripts/oauth_setup.py "
              f"and update the GOAUTH_REFRESH_TOKEN secret.")
        return 1

    state = sw.read_state(sheets, sheet_id)
    hashes = state.setdefault("wds_snapshot_hashes", {})
    today = _today()
    tmp = tempfile.gettempdir()

    uploaded = skipped = 0
    for name in enabled_collections:
        if name not in ww.COLLECTIONS:
            print(f"[wds-archive] unknown collection {name!r} — skipping")
            continue
        try:
            pages = wc.fetch_raw_snapshot(name, w)
        except Exception as e:  # noqa: BLE001 — WDSFetchError / network → skip, warn
            print(f"[wds-archive] {name}: fetch failed, skipping this run: {e}")
            continue

        col_hashes = hashes.setdefault(name, {})
        for i, html in enumerate(pages):
            h = _hash(html)
            key = str(i)
            if col_hashes.get(key) == h:
                skipped += 1
                continue
            local = os.path.join(tmp, f"wds_{name}_p{i}_{today}.html")
            try:
                with open(local, "w", encoding="utf-8") as f:
                    f.write(html)
                # Upload (durable copy) FIRST, then record the index row — a crash
                # between them re-uploads next run, made idempotent by the exact
                # dated filename (find_in_folder reuses rather than duplicates).
                drive_name = f"WDS_{name}_p{i}_{today}.html"
                link = ac.upload_file(drive, local, drive_name, "text/html")
                sw.append_wds_snapshot_row(sheets, sheet_id, today, name, i, h, link, _now())
                col_hashes[key] = h
                uploaded += 1
                print(f"  ok  {name} page {i}: snapshot uploaded ({h})")
            except Exception as e:  # noqa: BLE001
                print(f"  ERR {name} page {i}: {e}")
            finally:
                if os.path.exists(local):
                    os.remove(local)
        # Persist per-collection so a crash mid-run only loses the current
        # collection's not-yet-uploaded pages, not everything so far.
        sw.write_meta(sheets, sheet_id, state)

    print(f"[wds-archive] {uploaded} page(s) snapshotted (content changed), "
          f"{skipped} unchanged (skipped) this run.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
