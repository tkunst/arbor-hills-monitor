"""
pfas_watcher.py — daily content-hash watch on EGLE's PFAS-investigation page(s)
for Arbor Hills, alerting on any change. Standalone + self-terminating, the same
shape as wds_archiver.py / mmpc_archiver.py.

WHAT IT DOES each run (per watched page in cfg pfas.pages):
  - fetch + normalize the page (pfas_client — see there for the Sitecore
    cache-buster problem this normalization exists to solve),
  - compare its content hash to the last one recorded in the PFAS Page Watch tab
    (that tab IS the state — append-only, so no _meta clobber race),
  - FIRST sighting → record a silent "baseline" row (no alert; there's no change
    to report yet, and firing on activation day would just be noise),
  - hash changed → record a "changed" row THEN email an alert with a capped diff
    of the page's visible text (row first = durable record; email best-effort),
  - hash unchanged → no-op.

GATED ON pfas.enabled (false by default). A brand-new external source ships
disabled and a human flips it on — this loop never does (overnight-coder
procedure). Until enabled: true is on main, every run is a quiet no-op (exit 0).

ACTIVATION-BLOCK IS LOUD: if a page has NO baseline yet AND the fetch/parse
fails, that run exits 1 (→ workflow-failure email). This is deliberate: these
pages were verified fetchable from a residential IP, but a GitHub Actions runner
(Azure IP) could hit an Akamai/bot wall a Mac never sees. Failing loudly on the
first run surfaces that immediately instead of silently no-oping forever while
looking healthy. A transient failure AFTER a baseline exists is just skipped-and-
warned — a blip must never be diffed into a false "page changed" alert.

PER-ITEM ISOLATION: each configured page is read/diffed/recorded/alerted in its
own try/except — a Sheets read raise (state-reads-raise, ADR 012/023) or any
other failure on ONE page is logged (class-only, never the raw exception
string) and the run continues to the next page, returning exit 1 at the end.

DRIVE / OAUTH ONLY VIA THE OPTIONAL EXPORTER (ADR 012 scope call, amended by
ADR 072): the deliverable is the ALERT. The tab row carries the full normalized
text, which is both the diff basis and a durable dated snapshot — so SMTP +
Sheets (both already live) are all this needs. ADR 072 adds a best-effort
Tier-1 copy of that same normalized text to Drive (snapshot_exporter.py),
called right BEFORE each snapshot row and gated on
data_lifecycle.snapshot_export.enabled (default off) — a no-op until Trisha
activates it, never a blocker when she does.

Runs daily (see .github/workflows/pfas-watch.yml).
"""
from __future__ import annotations

import difflib
import os
import sys
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import pfas_client as pc
import email_alerts as ea
import snapshot_exporter as se
from config_loader import load_config

_MAX_DIFF_LINES = 60  # cap the diff in the email; the page link has the full context


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact bug
    this guards against (the watch doing real work / emailing before pfas.enabled
    is set) has a direct unit test. Mirrors wds_archiver._should_run."""
    if not (cfg.get("pfas") or {}).get("enabled"):
        return False, "pfas.enabled is false — skipping (no-op)."
    return True, ""


def summarize_diff(old_text: str, new_text: str,
                   max_lines: int = _MAX_DIFF_LINES) -> tuple[str, str]:
    """(note, body): a short counter for the row's Note column and a capped
    unified diff for the email body. Pure — unit-tested. A hash change with an
    EMPTY visible-text diff means only a link target / structure changed (the
    LINKS half of the normalized content), which the note calls out explicitly."""
    old, new = old_text.splitlines(), new_text.splitlines()
    diff = [ln for ln in difflib.unified_diff(old, new, lineterm="", n=2)
            if not ln.startswith(("---", "+++"))]
    added = sum(1 for ln in diff if ln.startswith("+"))
    removed = sum(1 for ln in diff if ln.startswith("-"))
    if added == 0 and removed == 0:
        note = "link/structure change (no visible-text diff)"
    else:
        note = f"+{added}/-{removed} lines"
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... (diff truncated at {max_lines} lines)"]
    return note, "\n".join(diff)


def format_change_body(label: str, url: str, note: str, body_diff: str) -> str:
    """The change-alert email body. Pure — unit-tested."""
    shown = body_diff or (
        "(no line-level text diff — a link target or page structure changed; "
        "open the page to see what.)")
    return (
        "The watched PFAS investigation page changed.\n\n"
        f"Page:   {label}\n"
        f"URL:    {url}\n"
        f"Change: {note}\n\n"
        "What changed (unified diff of the page's visible text; '+' added, "
        "'-' removed):\n\n"
        f"{shown}\n\n"
        "This is an automated content-hash page-watch (no document to parse — the "
        "signal is simply that EGLE edited the page). Open the URL above to read "
        "the full context.\n"
    )


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[pfas-watch] {reason}")
        return 0

    pfas_cfg = cfg.get("pfas") or {}
    pages = pfas_cfg.get("pages") or []
    min_chars = int(pfas_cfg.get("min_content_chars", 200))
    if not pages:
        print("[pfas-watch] pfas.enabled is true but pfas.pages is empty — nothing to watch.")
        return 0

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_pfas_tabs(sheets, sheet_id)

    today = _today()
    exit_code = 0
    changed = baselined = unchanged = 0

    for page in pages:
        url = (page or {}).get("url", "")
        label = (page or {}).get("label") or url
        if not url:
            print("[pfas-watch] page entry with no url — skipping")
            continue

        # Isolate THIS page's whole body — a strict-reader raise (state-reads-
        # raise, ADR 012/023) from last_pfas_snapshot, or any other failure on
        # ONE page, must not abort the other watched pages.
        try:
            last = sw.last_pfas_snapshot(sheets, sheet_id, url)

            try:
                html = pc.fetch_page(url)
                content = pc.extract_content(html, min_chars=min_chars)
            except (pc.PFASFetchError, pc.PFASContentError) as e:
                if last is None:
                    # No baseline yet AND unreadable: surface a CI-only block on the
                    # activation run instead of a silent forever-no-op. See module doc.
                    print(f"[pfas-watch] {label}: NO BASELINE and fetch/parse failed "
                          f"(failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                else:
                    print(f"[pfas-watch] {label}: fetch/parse failed, skipping this run "
                          f"(baseline preserved, not diffed): {e}")
                continue

            new_hash = pc.hash_text(content)
            chars = len(pc.visible_text(content))

            if last is None:
                # Tier-1 Drive copy of the normalized text BEFORE the Tier-2 row
                # (ADR 072 write order; a best-effort no-op unless
                # data_lifecycle.snapshot_export is enabled).
                se.export_snapshot(content, se.snapshot_name("pfas", label, today, new_hash, "txt"), cfg)
                sw.append_pfas_snapshot_row(
                    sheets, sheet_id, today, label, url, "baseline",
                    new_hash, chars, "initial snapshot (no alert)", _now(), content)
                baselined += 1
                print(f"[pfas-watch] {label}: baseline recorded ({new_hash}).")
                continue

            last_hash, last_text = last
            if new_hash == last_hash:
                unchanged += 1
                print(f"[pfas-watch] {label}: unchanged ({new_hash}).")
                continue

            # Changed: durable row FIRST, alert email SECOND (best-effort). A crash
            # between them loses the alert, never the record — and the row already
            # advanced the stored hash, so next run won't re-fire.
            note, body_diff = summarize_diff(
                pc.visible_text(last_text), pc.visible_text(content))
            se.export_snapshot(content, se.snapshot_name("pfas", label, today, new_hash, "txt"), cfg)
            sw.append_pfas_snapshot_row(
                sheets, sheet_id, today, label, url, "changed",
                new_hash, chars, note, _now(), content)
            changed += 1
            print(f"[pfas-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
            try:
                ea.send_email(
                    f"[PFAS watch] Page changed: {label}",
                    format_change_body(label, url, note, body_diff),
                    cfg,
                )
            except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
                print(f"[pfas-watch] {label}: change recorded but alert email FAILED: {e}")
        except Exception as e:  # noqa: BLE001 — isolate ONE page's failure
            print(f"[pfas-watch] {label}: FAILED this run "
                  f"({type(e).__name__}) — other pages continue.")
            exit_code = 1

    print(f"[pfas-watch] done — {changed} changed, {baselined} baselined, "
          f"{unchanged} unchanged.")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
