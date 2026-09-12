"""
gfl_info_site_watcher.py — daily snapshot + change-diff watch on GFL's public
"informational website" for Arbor Hills (arborhillslandfill.com, Stream S /
ADR 041). Standalone + self-terminating, the same shape as pfas_watcher.py.

WHY THIS EXISTS. GFL launched the site on 2026-09-10 to address community
questions about the proposed expansion. It is the operator's own narrative — the
value of watching it is catching WHEN and HOW that narrative changes (a FAQ
answer edited, a claim added or walked back, a new page, an email contact
finally published). Every other stream in this monitor watches EGLE/county
systems; this is the first watch on the operator's own marketing site.

WHAT IT DOES each run (see gfl_info_site_client.py for fetch/normalize details):
  1. DISCOVER the current page set: /sitemap.xml (a WP/Rank-Math index, followed
     one level) or a nav-crawl fallback, unioned with the configured seed_paths
     floor, minus ignore_path_patterns.
  2. Build the CHECK SET = discovered ∪ every URL ever recorded ∪ seed floor, so
     a page that vanished from the sitemap is still fetched (removal is confirmed
     by a real HTTP 404/410, never by sitemap-absence).
  3. For each URL, fetch + normalize and CLASSIFY against the last row for that
     URL in the "GFL Info Site Watch" tab (that tab IS the state — append-only,
     race-free):
       - never seen + this is the initial run   → SILENT baseline (atomic batch)
       - never seen + steady state              → NEW-PAGE alert
       - seen, hash unchanged                   → no-op
       - seen, hash changed                     → CHANGED alert (with a diff)
       - seen (200 before), now HTTP 404/410    → REMOVED-PAGE alert
       - was removed, now 200 again             → NEW-PAGE (returned) alert
  4. Durable row FIRST, alert email SECOND (crash-safe: a kill between them loses
     the alert, never the record, and never re-fires since the row advanced the
     stored hash).

ANTI-STAMPEDE. The very first run baselines EVERY discovered page in ONE atomic
append (so a crash can't leave a partial baseline that next run misreads as a
burst of "new" pages). In steady state, if more than max_new_pages_per_run pages
look new at once (a whole-site republish, a host change, or the sitemap
ballooning), the run RE-BASELINES them silently instead of blasting new-page
emails — the same defense as watcher.max_new_docs_per_run / wds / gfl_air.

EMAIL-CONTACT SURFACING. The launch site exposes a PHONE ONLY, no email. If GFL
adds an email later (even Cloudflare-obfuscated), the client decodes it and the
change alert calls it out explicitly (not just buried in the diff).

GATED ON gfl_info_site.enabled (false by default). A brand-new external source
ships disabled and a human flips it on + picks recipients — this loop never does
(overnight-coder procedure). Until enabled: true is on main, every scheduled run
is a quiet no-op (exit 0).

ACTIVATION-BLOCK IS LOUD: if a SEED page has no baseline yet AND its fetch/parse
fails, that run exits 1 (→ workflow-failure email) — so a Cloudflare wall on the
Actions runner surfaces on the activation run instead of silently no-oping
forever. (The fetch path is verified from residential + datacenter IPs, but the
GitHub Actions Azure IP is a different ASN Cloudflare could challenge; run the
workflow_dispatch PROBE mode before flipping enabled to confirm — see below.)

PROBE MODE (workflow_dispatch input `probe=true`, or `--probe`): a pure fetch-
path diagnostic that runs REGARDLESS of the enabled flag and writes NOTHING to
the Sheet and sends NO email. It fetches + normalizes every seed page and reports
OK / CHALLENGE / FAIL per page, exiting non-zero if any seed page is unreadable.
This is how the Azure runner's fetch path gets verified before activation.

NO DRIVE / OAUTH (deliberate, like pfas_watcher): the deliverable is the ALERT,
and the tab row carries the full normalized text as both diff basis and durable
dated snapshot — so Sheets + SMTP (both already live) are all this needs.

Runs daily (see .github/workflows/gfl-info-site-watch.yml).
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
import gfl_info_site_client as gc
import email_alerts as ea
from config_loader import load_config

_DEFAULT_MAX_DIFF_LINES = 80          # cap the diff in the email; the URL has full context
_DEFAULT_MAX_NEW_PAGES = 3            # > this many "new" pages at once → silent re-baseline
_DEFAULT_MIN_CHARS = 150
# Stored in a removed page's row so a re-run that still 404s recognizes the page
# as already-known-removed (no re-alert) — and a later 200 (hash != this) reads
# as the page returning. Printable (stored in a Sheets cell).
_REMOVED_HASH = "(removed)"

# Sensible defaults so a minimal config still works; overridable per key.
_DEFAULT_BASE_URL = "https://arborhillslandfill.com"
_DEFAULT_SEED_PATHS = [
    "/", "/what-we-do", "/landfill-improvements",
    "/community", "/faq", "/location-hours",
]
_DEFAULT_IGNORE = [r"^/sitemap($|/)", r"\.kml$", r"\.xsl$", r"^/wp-"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact bug
    this guards against (the watch doing real work / emailing before
    gfl_info_site.enabled is set) has a direct unit test. Mirrors
    pfas_watcher._should_run / wds_archiver._should_run."""
    if not (cfg.get("gfl_info_site") or {}).get("enabled"):
        return False, "gfl_info_site.enabled is false — skipping (no-op)."
    return True, ""


# ---------------------------------------------------------------------------
# Pure composition (no network / no Sheets) — all unit-tested
# ---------------------------------------------------------------------------


def summarize_diff(old_text: str, new_text: str,
                   max_lines: int = _DEFAULT_MAX_DIFF_LINES) -> tuple[str, str]:
    """(note, body): a short counter for the row's Note column and a capped
    unified diff for the email body. Pure. A hash change with an EMPTY visible-
    text diff means only a link target / page structure changed, which the note
    calls out explicitly (same idiom as pfas_watcher.summarize_diff)."""
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


def new_emails(old_content: str, new_content: str) -> list[str]:
    """Email addresses present in new_content but not old_content — the "GFL just
    published an email contact" signal (the site is phone-only at launch). Pure."""
    before = set(gc.emails_in(old_content or ""))
    return [e for e in gc.emails_in(new_content or "") if e not in before]


def format_change_body(label: str, url: str, note: str, body_diff: str,
                       added_emails: list[str] | None = None) -> str:
    """The change-alert email body. Pure — unit-tested."""
    shown = body_diff or (
        "(no line-level text diff — a link target or page structure changed; "
        "open the page to see what.)")
    email_block = ""
    if added_emails:
        email_block = (
            "\n*** NEW EMAIL CONTACT PUBLISHED ***\n  "
            + "\n  ".join(added_emails)
            + "\n(The launch site was phone-only; an email contact was added.)\n")
    return (
        "A watched page on GFL's Arbor Hills information site changed.\n\n"
        f"Page:   {label}\n"
        f"URL:    {url}\n"
        f"Change: {note}\n"
        f"{email_block}\n"
        "What changed (unified diff of the page's visible text; '+' added, "
        "'-' removed):\n\n"
        f"{shown}\n\n"
        "This is an automated change-watch on the operator's own informational "
        "website (not an EGLE record). Open the URL above for full context.\n"
    )


def format_new_page_body(label: str, url: str, returned: bool,
                         emails: list[str] | None = None) -> str:
    """The new-page (or page-returned) alert body. Pure — unit-tested."""
    what = ("A page that had been removed is back on GFL's Arbor Hills "
            "information site." if returned else
            "A NEW page appeared on GFL's Arbor Hills information site.")
    email_block = ""
    if emails:
        email_block = "\nEmail contact(s) on the page:\n  " + "\n  ".join(emails) + "\n"
    return (
        f"{what}\n\n"
        f"Page:   {label}\n"
        f"URL:    {url}\n"
        f"{email_block}\n"
        "This page is now baselined; future edits to it will be diffed and "
        "alerted. Open the URL above to read it.\n"
    )


def format_removed_page_body(label: str, url: str) -> str:
    """The removed-page alert body. Pure — unit-tested."""
    return (
        "A page was REMOVED from GFL's Arbor Hills information site "
        "(it now returns HTTP 404/410).\n\n"
        f"Page:   {label}\n"
        f"URL:    {url}\n\n"
        "The last-known content of this page is preserved in the GFL Info Site "
        "Watch tab. This removal will not re-alert unless the page returns.\n"
    )


def _resolve_cfg(cfg: dict) -> dict:
    """Pull the gfl_info_site config with defaults filled in. Pure/testable."""
    g = cfg.get("gfl_info_site") or {}
    base_url = (g.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
    sitemap_url = g.get("sitemap_url") or (base_url + "/sitemap.xml")
    return {
        "base_url": base_url,
        "sitemap_url": sitemap_url,
        "seed_paths": g.get("seed_paths") or _DEFAULT_SEED_PATHS,
        "ignore_patterns": g.get("ignore_path_patterns") or _DEFAULT_IGNORE,
        "min_chars": int(g.get("min_content_chars", _DEFAULT_MIN_CHARS)),
        "max_diff_lines": int(g.get("max_diff_lines", _DEFAULT_MAX_DIFF_LINES)),
        "max_new_pages": int(g.get("max_new_pages_per_run", _DEFAULT_MAX_NEW_PAGES)),
        "recipients": g.get("recipients") or [],
    }


# ---------------------------------------------------------------------------
# Probe mode — fetch-path diagnostic (no Sheet, no email, any enabled state)
# ---------------------------------------------------------------------------


def run_probe(cfg: dict) -> int:
    """Fetch + normalize every seed page and report OK / CHALLENGE / FAIL, WITHOUT
    touching the Sheet or sending email. Exits non-zero if any seed page is
    unreadable — this is how the GitHub Actions (Azure) runner's fetch path gets
    verified before gfl_info_site.enabled is flipped on. Runs regardless of the
    enabled flag."""
    rc = _resolve_cfg(cfg)
    print(f"[gfl-info-site] PROBE — fetch-path diagnostic (base {rc['base_url']}); "
          "no Sheet writes, no email.")
    dr = gc.discover_page_urls(
        rc["base_url"], rc["sitemap_url"], rc["seed_paths"], rc["ignore_patterns"])
    print(f"[gfl-info-site] discovery source={dr.source}; {len(dr.urls)} url(s): "
          + ", ".join(dr.urls))
    for n in dr.notes:
        print(f"[gfl-info-site]   note: {n}")

    seeds = sorted({gc.canonical_url(p, rc["base_url"]) for p in rc["seed_paths"]} - {None})
    exit_code = 0
    for url in seeds:
        try:
            html = gc.fetch_page(url)
            content = gc.extract_content(html, min_chars=rc["min_chars"])
            print(f"[gfl-info-site]   OK        {gc.page_label(url):22} "
                  f"{len(gc.visible_text(content)):5} chars  hash={gc.hash_text(content)}")
        except gc.GFLInfoSiteContentError as e:
            print(f"[gfl-info-site]   CHALLENGE {gc.page_label(url):22} {e}")
            exit_code = 1
        except gc.GFLInfoSiteFetchError as e:
            print(f"[gfl-info-site]   FAIL      {gc.page_label(url):22} {e}")
            exit_code = 1
    if exit_code:
        print("[gfl-info-site] PROBE: at least one seed page is unreadable — the "
              "fetch path is BLOCKED from this runner (likely a Cloudflare wall).")
    else:
        print("[gfl-info-site] PROBE: all seed pages fetched + normalized cleanly.")
    return exit_code


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------


def run(probe: bool = False) -> int:
    cfg = load_config()

    if probe:
        return run_probe(cfg)

    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[gfl-info-site] {reason}")
        return 0

    rc = _resolve_cfg(cfg)
    # Scoped VERBATIM to this watch's own list (the Meeting Watch idiom), PLUS an
    # optional private supplement env (the ALERT_RECIPIENTS_EXTRA pattern) so a
    # non-public address can be added without committing it to this PUBLIC repo.
    # Empty (both unset) ⇒ DISPLAY-ONLY: durable rows written, NO email — it
    # never falls back to the full alert_recipients list (fail-safe, matching
    # upcoming.send_upcoming). This operator-narrative watch is Trisha's to
    # scope, not a coalition-wide broadcast.
    recipients = ea.merge_extra_recipients(
        rc["recipients"], "GFL_INFO_SITE_RECIPIENTS_EXTRA")

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_gfl_info_site_tabs(sheets, sheet_id)

    today, now = _today(), _now()
    is_initial = sw.gfl_info_site_tab_is_empty(sheets, sheet_id)
    seen_urls = sw.all_gfl_info_site_urls(sheets, sheet_id)

    dr = gc.discover_page_urls(
        rc["base_url"], rc["sitemap_url"], rc["seed_paths"], rc["ignore_patterns"])
    print(f"[gfl-info-site] discovery source={dr.source}; {len(dr.urls)} url(s).")
    for n in dr.notes:
        print(f"[gfl-info-site]   note: {n}")

    seed_canon = {gc.canonical_url(p, rc["base_url"]) for p in rc["seed_paths"]} - {None}
    check_set = sorted(set(dr.urls) | seen_urls | seed_canon)

    # ---- Phase 1: classify (no writes yet) ---------------------------------
    new_events: list[dict] = []       # never-seen pages, or a removed page returning
    change_events: list[dict] = []    # seen pages whose content changed
    removed_events: list[dict] = []   # seen pages now 404/410
    unchanged = skipped = 0
    exit_code = 0

    for url in check_set:
        label = gc.page_label(url)
        prior = sw.last_gfl_info_site_snapshot(sheets, sheet_id, url)
        is_seed = url in seed_canon
        try:
            html = gc.fetch_page(url)
            content = gc.extract_content(html, min_chars=rc["min_chars"])
        except gc.GFLInfoSiteGone:
            # Confirmed removal (HTTP 404/410).
            if prior is None:
                # A discovered-but-dead link we never baselined — noise, ignore.
                print(f"[gfl-info-site]   ignore    {label}: 404 and never baselined.")
                continue
            if prior[0] == _REMOVED_HASH:
                print(f"[gfl-info-site]   no-op     {label}: still removed.")
                continue
            removed_events.append({"url": url, "label": label})
            continue
        except (gc.GFLInfoSiteFetchError, gc.GFLInfoSiteContentError) as e:
            # Transient / challenge — never diffed into a false change.
            if prior is None and is_initial and is_seed:
                # Persistent block on the activation run for a core page → loud.
                print(f"[gfl-info-site]   BLOCK     {label}: NO BASELINE and fetch/"
                      f"parse failed on the activation run (exit 1): {e}")
                exit_code = 1
            else:
                print(f"[gfl-info-site]   skip      {label}: fetch/parse failed, "
                      f"baseline preserved, not diffed: {e}")
                skipped += 1
            continue

        new_hash = gc.hash_text(content)
        chars = len(gc.visible_text(content))

        if prior is None:
            new_events.append({"url": url, "label": label, "hash": new_hash,
                               "content": content, "chars": chars, "returned": False,
                               "emails": gc.emails_in(content)})
            continue

        prior_hash, prior_text = prior
        if prior_hash == _REMOVED_HASH:
            # Page returned after a removal.
            new_events.append({"url": url, "label": label, "hash": new_hash,
                               "content": content, "chars": chars, "returned": True,
                               "emails": gc.emails_in(content)})
            continue
        if new_hash == prior_hash:
            unchanged += 1
            continue

        note, body_diff = summarize_diff(
            gc.visible_text(prior_text), gc.visible_text(content),
            max_lines=rc["max_diff_lines"])
        change_events.append({
            "url": url, "label": label, "hash": new_hash, "content": content,
            "chars": chars, "note": note, "body_diff": body_diff,
            "added_emails": new_emails(prior_text, content)})

    # ---- Phase 2: new pages (initial baseline / stampede guard / alert) -----
    baselined = new_alerted = 0
    if new_events:
        if is_initial:
            rows = [[today, e["label"], e["url"], "baseline", e["hash"], e["chars"],
                     "initial snapshot (no alert)", now, e["content"]]
                    for e in new_events]
            sw.append_rows(sheets, sheet_id, sw.TAB_GFL_INFO_SITE, rows)
            baselined = len(rows)
            print(f"[gfl-info-site] initial run: baselined {baselined} page(s) "
                  "silently (one atomic write).")
        elif len(new_events) > rc["max_new_pages"]:
            rows = [[today, e["label"], e["url"], "baseline", e["hash"], e["chars"],
                     f"re-baseline ({len(new_events)} new > "
                     f"{rc['max_new_pages']} cap — no alert)", now, e["content"]]
                    for e in new_events]
            sw.append_rows(sheets, sheet_id, sw.TAB_GFL_INFO_SITE, rows)
            baselined = len(rows)
            print(f"[gfl-info-site] ANTI-STAMPEDE: {len(new_events)} pages looked "
                  f"new (> {rc['max_new_pages']}) — re-baselined silently, no alerts. "
                  "Likely a site republish / discovery change; review the tab.")
        else:
            for e in new_events:
                kind = "new-page"
                note = ("page returned after removal" if e["returned"]
                        else ("new page" + (
                            f"; email(s): {', '.join(e['emails'])}" if e["emails"] else "")))
                sw.append_gfl_info_site_snapshot_row(
                    sheets, sheet_id, today, e["label"], e["url"], kind,
                    e["hash"], e["chars"], note, now, e["content"])
                new_alerted += 1
                subj = (f"[GFL info site] Page {'returned' if e['returned'] else 'ADDED'}: "
                        f"{e['label']}")
                print(f"[gfl-info-site]   NEW       {e['label']} ({kind}).")
                _send(subj, format_new_page_body(
                    e["label"], e["url"], e["returned"], e["emails"]),
                    cfg, recipients, e["label"])

    # ---- Phase 3: changed pages (row before email, per page) ----------------
    changed = 0
    for e in change_events:
        sw.append_gfl_info_site_snapshot_row(
            sheets, sheet_id, today, e["label"], e["url"], "changed",
            e["hash"], e["chars"], e["note"], now, e["content"])
        changed += 1
        print(f"[gfl-info-site]   CHANGED   {e['label']} ({e['note']}).")
        _send(f"[GFL info site] Page changed: {e['label']}",
              format_change_body(e["label"], e["url"], e["note"], e["body_diff"],
                                 e["added_emails"]),
              cfg, recipients, e["label"])

    # ---- Phase 4: removed pages (row before email, per page) ----------------
    removed = 0
    for e in removed_events:
        sw.append_gfl_info_site_snapshot_row(
            sheets, sheet_id, today, e["label"], e["url"], "removed-page",
            _REMOVED_HASH, 0, "page returns HTTP 404/410", now, "")
        removed += 1
        print(f"[gfl-info-site]   REMOVED   {e['label']}.")
        _send(f"[GFL info site] Page REMOVED: {e['label']}",
              format_removed_page_body(e["label"], e["url"]),
              cfg, recipients, e["label"])

    print(f"[gfl-info-site] done — {changed} changed, {new_alerted} new-page, "
          f"{removed} removed, {baselined} baselined, {unchanged} unchanged, "
          f"{skipped} skipped.")
    return exit_code


def _send(subject: str, body: str, cfg: dict, recipients: list, label: str) -> None:
    """Send one alert, best-effort. The durable Sheet row is already written, so a
    send failure logs and moves on (never re-raises — matches the rest of the
    monitor's row-first, alert-best-effort ordering). Recipients are passed
    VERBATIM (scoped to this watch's own list). An EMPTY list means DISPLAY-ONLY:
    do NOT send (and do NOT fall back to the full alert_recipients list — that
    fallback is exactly what passing None to send_email would trigger). This
    mirrors upcoming.send_upcoming's fail-safe."""
    if not recipients:
        print(f"[gfl-info-site]   {label}: recorded; no recipients configured — "
              "display-only (no email sent).")
        return
    try:
        ea.send_email(subject, body, cfg, recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
        print(f"[gfl-info-site]   {label}: change recorded but alert email FAILED: {e}")


if __name__ == "__main__":
    _probe = ("--probe" in sys.argv[1:]) or bool(os.environ.get("GFL_INFO_SITE_PROBE"))
    sys.exit(run(probe=_probe))
