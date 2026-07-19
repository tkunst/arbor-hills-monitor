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

GATED ON wds.enabled, same as every other Stream C entry point (check_wds,
the WDS tabs, the historical dump). The workflow is scheduled from day one and
GOAUTH_* is already configured (the sibling PDF archiver has used it for
weeks), so without this gate merging this file would start creating WDS tabs
and writing to the live Sheet on the very next nightly tick — before Trisha
ever flips `enabled: true`. See _should_run() below.

  - wds.enabled is false, or GOAUTH_* not set: quiet no-op, exit 0 (both are
    "not activated yet", not an error).
  - GOAUTH_* configured but the token is dead: loud failure, exit 1 — a silent
    skip would let the mirror fall behind invisibly, defeating the whole point.

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
    return hashlib.sha1(html.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()[:12]


def _should_run(cfg: dict, oauth_configured: bool) -> tuple[bool, str]:
    """Pure gate check — testable without any Sheets/Drive/network mocking, so
    the exact bug this guards against (the job silently doing real work before
    Trisha ever sets wds.enabled: true) has a direct unit test rather than
    depending on someone noticing in a live run. wds.enabled is checked FIRST:
    that's the primary Stream C activation switch every other entry point
    (check_wds, the WDS tabs, the historical dump) already respects; OAuth
    being configured is a separate, secondary precondition."""
    if not (cfg.get("wds") or {}).get("enabled"):
        return False, "wds.enabled is false — skipping (no-op)."
    if not oauth_configured:
        return False, ("GOAUTH_* not set — snapshotting disabled (no-op). "
                       "See scripts/oauth_setup.py + docs/decisions/007.")
    return True, ""


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg, ac.is_configured())
    if not should_run:
        print(f"[wds-archive] {reason}")
        return 0

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

    # Only _meta is needed here (never _state) — read_meta() avoids scanning the
    # whole processed-doc event log for a job that doesn't use it.
    hashes = sw.read_meta(sheets, sheet_id).get("wds_snapshot_hashes", {})
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
                link = ac.upload_file(drive, local, drive_name, "text/html", ac.folder_id())
                sw.append_wds_snapshot_row(sheets, sheet_id, today, name, i, h, link, _now())
                col_hashes[key] = h
                uploaded += 1
                print(f"  ok  {name} page {i}: snapshot uploaded ({h})")
            except Exception as e:  # noqa: BLE001
                print(f"  ERR {name} page {i}: {e}")
            finally:
                if os.path.exists(local):
                    os.remove(local)
        # Re-read _meta FRESH right before writing and patch in only our own key
        # (wds_snapshot_hashes) — never write back the run-start snapshot of
        # keys this job doesn't own. A concurrent job (the daily watcher, run
        # manually or overlapping via scheduling drift) can still race with
        # this exact read-then-write, but this shrinks the clobber window from
        # "the whole run" to "one read+write per collection" instead of writing
        # a single stale copy of everything at the very end.
        fresh_meta = sw.read_meta(sheets, sheet_id)
        fresh_meta["wds_snapshot_hashes"] = hashes
        sw.write_meta(sheets, sheet_id, fresh_meta)

    print(f"[wds-archive] {uploaded} page(s) snapshotted (content changed), "
          f"{skipped} unchanged (skipped) this run.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
