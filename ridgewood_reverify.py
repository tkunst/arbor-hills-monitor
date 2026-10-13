"""
ridgewood_reverify.py — Stream G add-on: monthly re-verification of every Ridge Wood
Elementary H2S report Stream G (ridgewood_archiver.py, ADR 016) has already archived,
plus a committed, tamper-evident provenance manifest. See docs/decisions/066.

Stream G archives a month's report ONCE and never looks at it again (month-keyed
dedup). That leaves two gaps:

  1. A silently REVISED PDF (Barr replaces a past month's file) is never noticed —
     our record still holds only the old version.
  2. A report that DISAPPEARS from Barr's page is never noticed.

This job closes both, plus gives the whole archive a single tamper-evident record:
`data/provenance/ridgewood-manifest.json`, committed to this PUBLIC repo by the
workflow. Per Trisha's 2026-10-08 ruling, the manifest needs no cryptographic
signature — the GIT COMMIT HISTORY is the signature (a silent post-hoc edit to the
file would show up as an unexplained commit, or none at all if someone edited the
working tree without committing — either way, the public commit log is the audit
trail this repo already has for free).

SCOPE NOTE: this does NOT re-extract H2S values or re-run Stream G's exceedance
alert on a changed report — that is Stream G's job on NEW months, and a revision to
an already-alerted-on month is flagged for a HUMAN to decide, not auto-alerted as
a public exceedance. (It DOES read the revised PDF's content, read-only, purely to
name whether it newly reads as an exceedance in the OWNER-only alert text — see
_revision_note(). No Measurements write, no public/full-list routing, ever.)

MANIFEST SHAPE — {month: [version, ...]}, one entry per VERSION of that month's
report (never mutated for a real content change — a revision always APPENDS a new
version; the old one stays exactly as it was). A version is:
    report_title, source_url, fetched_at, content_hash, archive_link,
    status ("original" | "revised <date>" | "removed-from-source <date>"),
    last_verified (date, or null if never separately confirmed)
The ONE field a verification pass is allowed to mutate in place is last_verified
on the CURRENT (the LAST entry in the version list — see latest_version()'s
docstring for why that's list order, not a fetched_at comparison) version —
"record 'verified <date>', nothing else" per the handoff. A REVISED or REMOVED
month always gets a brand-new version entry instead (append, never mutate the
hash/link/status of a past version).

BOOTSTRAP: Stream G has already been mirroring reports since 2026-07-15, long before
this manifest existed. The FIRST time this job ever checks a month, there is no
manifest entry yet, so the "latest recorded hash" is seeded from Stream G's OWN
Sheet row (Ridge Wood Reports — the real original fetch date/hash/link, not today's
date) via sheet_writer.ridgewood_report_rows(). If that first check already finds a
mismatch against the freshly re-downloaded bytes, this run records BOTH the seeded
"original" version AND a "revised <today>" version in the same pass — a revision
that happened before this job ever existed is still caught once it exists, instead
of being silently absorbed as the "original". A Sheet row with a blank Content Hash
(shouldn't happen, but Sheets are hand-editable) means "nothing to compare against
yet", not "already changed" — it seeds from today's download instead.

CRASH/RETRY SAFETY for a changed/removed/reappeared month: the Drive upload (if
any) and the Ridge Wood Revisions Sheet row are written FIRST (durable,
human-visible) and the owner alert SECOND; the manifest's new version is only
merged in and SAVED once the owner alert actually sends. A run that writes the
Sheet row and then fails to email (SMTP down, empty owner list) exits 1 and
leaves the manifest exactly as it was for that month — the next run re-detects
the same event and compares it against the month's MOST RECENT logged event
(sheet_writer.ridgewood_last_event()). If that last event is this exact
(event, hash), it's an unresolved retry — skip the re-upload and the duplicate
Sheet row, just finish advancing the manifest + the alert. If it ISN'T (a
removal, then a reappearance, then a second removal with byte-identical
content — the second removal's hash matches the first's, but it is NOT the
month's last logged event any more; the reappearance is), it's a genuinely new
occurrence and gets logged fresh, even though its hash collides with an older
event. (An earlier version of this check keyed purely on "was this
(month, event, hash) EVER logged", with no notion of "most recent" — correct for
retrying "revised" within one unresolved episode, but it silently swallowed a
real second removal as a stale retry of the first one; two independent reviews
caught successively narrower versions of this bug, which is why the fix reads
"most recent", not "ever".) Routine "unchanged, verified again" progress for
OTHER months in the same run is still saved even when one event's alert fails —
losing a harmless last_verified bump to a retried alert would be a worse trade.

Gated on `ridgewood.reverify_enabled` (ships false — a new job whose Drive
mirror/Sheet/email side effects reach a live stream is switched on by a human, per
docs/overnight-coder.md). Own workflow + concurrency group; does not touch
ridgewood_archiver.py or anything Stream G writes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone

import archive_client as ac
import drive_client as dc
import email_alerts as ea
import ocr_util
import ridgewood_client as rc
import sheet_writer as sw
from config_loader import load_config

FOLDER_ENV = "GOAUTH_RIDGEWOOD_FOLDER_ID"   # same Drive folder Stream G mirrors into
MANIFEST_PATH = "data/provenance/ridgewood-manifest.json"
SCHEMA_VERSION = 1

# A monthly report is a couple of text-layer pages (a few hundred KB observed); this
# job downloads EVERY listed month every run, so it is more exposed to a hostile or
# broken oversized response than Stream G's own download_report() (new-months-only).
MAX_REPORT_BYTES = 20_000_000

# A confirmed-twice absence spanning more than this fraction of archived months
# reads as a page restructure or a scrape-format break, not that many real
# removals in one run — abort loudly rather than flag a flood of false removals.
MAX_REMOVED_FRACTION = 0.5


class ReverifyDownloadError(RuntimeError):
    """A report PDF could not be downloaded/validated this run. The month is
    neither 'changed' nor 'removed' — last_verified is left untouched and it is
    simply retried next run."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate, directly unit-testable — mirrors ridgewood_archiver._should_run()."""
    if not (cfg.get("ridgewood") or {}).get("reverify_enabled"):
        return False, "ridgewood.reverify_enabled is false — skipping (no-op)."
    return True, ""


# ---------------------------------------------------------------------------
# Capped download (own copy, NOT ridgewood_client.download_report — that function
# is Stream G's live path and stays untouched; this job needs a size cap because it
# re-downloads every listed month, not just new ones). Same opener/quoting as
# download_report() so the fetched bytes — and therefore the hash — match exactly
# what the live archiver itself would have fetched.
# ---------------------------------------------------------------------------

def _download_capped(url: str, dest_path: str, timeout: int = 60) -> str:
    safe = urllib.parse.quote(url, safe=":/?&=%_.-")
    try:
        r = rc._opener().open(safe, timeout=timeout)
        status = getattr(r, "status", None) or r.getcode()
        content = r.read(MAX_REPORT_BYTES + 1)
    except Exception as e:  # noqa: BLE001 — network/HTTP -> transient, logged by class only
        raise ReverifyDownloadError(f"{type(e).__name__}") from e
    if status != 200:
        raise ReverifyDownloadError(f"HTTP {status}")
    if len(content) > MAX_REPORT_BYTES:
        raise ReverifyDownloadError(f"response exceeds {MAX_REPORT_BYTES:,} bytes")
    if not content or not content.startswith(b"%PDF"):
        raise ReverifyDownloadError("response is not a PDF")
    with open(dest_path, "wb") as out:
        out.write(content)
    return dest_path


# ---------------------------------------------------------------------------
# Manifest I/O — local filesystem only (no network/Sheets). The workflow commits
# whatever this writes; a human never edits data/provenance/*.json by hand.
# ---------------------------------------------------------------------------

def load_manifest(path: str = MANIFEST_PATH) -> dict:
    if not os.path.exists(path):
        return {"schema_version": SCHEMA_VERSION, "generated_at": "", "months": {}}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("months", {})
    return data


def save_manifest(manifest: dict, path: str = MANIFEST_PATH) -> None:
    """Deterministic (sorted keys, fixed indent) so the git diff of a routine
    'nothing changed, just re-verified' run is small and legible, and atomic (temp
    file + rename) so a crash mid-write can never leave a half-written manifest."""
    out = dict(manifest)
    out["schema_version"] = SCHEMA_VERSION
    out["generated_at"] = _now_iso()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Pure decision helpers — no network/Sheets/Drive, directly unit-tested.
# ---------------------------------------------------------------------------

def latest_version(manifest: dict, month: str) -> dict | None:
    """The CURRENT version of `month` — the LAST entry in its version list, or
    None if the manifest has never recorded this month. Every version is only
    ever APPENDED (never inserted out of order — see reverify_month()/
    _apply_staged()), so list position IS chronological order; this is
    deliberately NOT `max(versions, key=lambda v: v['fetched_at'])` — a review
    found that two versions appended within the same wall-clock SECOND (now_iso
    is second-resolution) produce an identical fetched_at string, and max()'s
    tie-break keeps the FIRST (oldest) one it sees, silently returning a stale
    version as "current". List order has no such tie at all."""
    versions = (manifest.get("months") or {}).get(month) or []
    return versions[-1] if versions else None


def seed_version(sheet_row: dict | None, title: str, source_url: str,
                  fallback_hash: str, now_iso: str) -> dict:
    """The 'original' version for a month this job has never checked before,
    sourced from Stream G's OWN Sheet row (its real historical fetch) when one
    exists. A blank/missing Sheet hash ("can't verify yet", not "already changed")
    falls back to `fallback_hash` — the bytes just downloaded THIS run — so the very
    first check never spuriously reads as a revision."""
    row = sheet_row or {}
    sheet_hash = (row.get("content_hash") or "").strip()
    return {
        "report_title": row.get("report_title") or title,
        "source_url": row.get("source_url") or source_url,
        "fetched_at": row.get("fetched_at") or now_iso,
        "content_hash": sheet_hash or fallback_hash,
        "archive_link": row.get("archive_link") or "",
        "status": "original",
        "last_verified": None,
    }


def _version_entry(title: str, source_url: str, content_hash: str, status: str,
                    today: str, now_iso: str) -> dict:
    return {
        "report_title": title,
        "source_url": source_url,
        "fetched_at": now_iso,
        "content_hash": content_hash,
        "archive_link": "",
        "status": status,
        "last_verified": today,
    }


def reappeared_entry(prior: dict, title: str, source_url: str, today: str, now_iso: str) -> dict:
    """A NEW version appended when a month previously flagged removed-from-source
    reappears with IDENTICAL content. APPENDS (never mutates `prior`) so the
    removed entry's own history is preserved, and carries `prior`'s archive_link
    forward — the bytes are already safely mirrored, nothing new to upload."""
    return {
        "report_title": title or prior["report_title"],
        "source_url": source_url or prior["source_url"],
        "fetched_at": now_iso,
        "content_hash": prior["content_hash"],
        "archive_link": prior.get("archive_link", ""),
        "status": "original",
        "last_verified": today,
    }


def reverify_month(manifest: dict, month: str, sheet_row: dict | None, title: str,
                    source_url: str, downloaded_hash: str, today: str,
                    now_iso: str) -> tuple[str, list[dict]]:
    """Pure. Decide what happened to one already-archived, STILL-LISTED month this
    run. Returns (outcome, new_versions): outcome is 'baseline' | 'unchanged' |
    'changed' | 'reappeared'; new_versions is what the caller should APPEND to
    manifest['months'] for this month (0, 1, or 2 entries — a month never checked
    before that ALREADY differs from Stream G's own recorded hash gets both the
    seeded 'original' AND a 'revised <today>' entry in the same run, so no history
    is lost). NEVER mutates `manifest` or any existing version — on 'unchanged' the
    caller bumps last_verified on the EXISTING latest version itself (the one
    explicitly allowed in-place field); every other outcome, including a month
    that was removed-from-source and has now reappeared with byte-identical
    content ('reappeared' — the stale removed flag is corrected by a brand-new
    version, not by rewriting the removed one), only ever appends."""
    existing = latest_version(manifest, month)
    if existing is None:
        seed = seed_version(sheet_row, title, source_url, downloaded_hash, now_iso)
        seed["last_verified"] = today
        if seed["content_hash"] == downloaded_hash:
            return "baseline", [seed]
        revised = _version_entry(title, source_url, downloaded_hash,
                                  f"revised {today}", today, now_iso)
        return "changed", [seed, revised]
    if existing["content_hash"] == downloaded_hash:
        if existing["status"].startswith("removed-from-source"):
            return "reappeared", [reappeared_entry(existing, title, source_url, today, now_iso)]
        return "unchanged", []
    revised = _version_entry(title, source_url, downloaded_hash,
                              f"revised {today}", today, now_iso)
    return "changed", [revised]


def _revision_note(has_text: bool, verdict: dict, prior_sheet_row: dict | None,
                    ocr_applied: bool = False) -> str:
    """Read-only context for the owner alert/Sheet row: does the REVISED report's
    content newly read as an exceedance? Never written to Measurements, never
    routed to the public/full alert list — Stream G owns that decision for NEW
    months; a revision to an already-archived month is for Trisha to review.

    `ocr_applied` (coder:mirror-ocr, 2026-10-13): True when the revised PDF had
    no native text layer and ensure_searchable() OCR'd it to produce `verdict`
    at all — surfaced here since an OCR misread could turn a real exceedance
    into a false "ok" read, and this note is the only place a human sees it."""
    if not has_text:
        return "revised report has no extractable text layer (scanned, even after OCR) — review manually."
    prior_max = (prior_sheet_row or {}).get("max_24h") or "n/a"
    prior_alert = (prior_sheet_row or {}).get("alert") or "n/a"
    now_desc = ("EXCEEDANCE" if verdict.get("exceed_24h")
                else "review" if verdict.get("alert") else "ok")
    note = (f"Originally recorded: max {prior_max} ppb ({prior_alert}). "
            f"Revised report reads: max {verdict.get('value_token') or 'n/a'} ppb ({now_desc}).")
    if ocr_applied:
        note += (" Revised report had no native text layer; this reading was extracted "
                  "via OCR — verify against the original scan before relying on it.")
    return note


def format_owner_alert(changed: list, removed: list, multi_link: list | None = None,
                        reappeared: list | None = None) -> tuple[str, str]:
    """(subject, body) for ONE consolidated owner-only email covering every
    revised/removed/reappeared month found this run. `changed` items are
    (month, prior_hash, new_hash, archive_link, note); `removed` items are
    (month, archive_link); `reappeared` items are (month, archive_link) — a month
    previously flagged removed-from-source that has come back with content
    identical to what we already held; `multi_link` items are (month, [urls]) — a
    month with more than one distinct report link posted, which this job only ever
    checks the first of (page order), so a genuine second revision sitting beside
    it could be invisible to the hash check above. Flagged for a human look, not
    auto-resolved."""
    multi_link = multi_link or []
    reappeared = reappeared or []
    parts = []
    if changed:
        parts.append(f"{len(changed)} report(s) revised")
    if removed:
        parts.append(f"{len(removed)} report(s) removed from the source page")
    if reappeared:
        parts.append(f"{len(reappeared)} report(s) reappeared")
    if multi_link:
        parts.append(f"{len(multi_link)} month(s) with multiple posted links")
    subject = "[Arbor Hills Monitor] Ridge Wood H2S archive: " + ", ".join(parts)
    lines = [
        "The monthly re-verification pass over the Ridge Wood Elementary H2S report",
        "archive found a change. This is an OWNER-ONLY notice — never routed to the",
        "public or township/county recipient lists.",
        "",
    ]
    if changed:
        lines.append("REVISED (the earlier copy was NOT touched or deleted):")
        for month, prior_hash, new_hash, link, note in changed:
            lines.append(f"  {month}: content hash {prior_hash[:12]}… -> {new_hash[:12]}…")
            if link:
                lines.append(f"    new mirrored copy: {link}")
            if note:
                lines.append(f"    {note}")
        lines.append("")
    if removed:
        lines.append("REMOVED FROM SOURCE (confirmed absent on two page fetches this run):")
        for month, link in removed:
            lines.append(f"  {month}: no longer listed on Barr's report page.")
            lines.append(f"    our mirrored copy remains at: {link or '(no Drive mirror on file)'}")
        lines.append("")
    if reappeared:
        lines.append("REAPPEARED (content identical to the last-known copy; the earlier "
                      "removed-from-source flag is now corrected):")
        for month, link in reappeared:
            lines.append(f"  {month}: back on Barr's report page.")
            lines.append(f"    mirrored copy: {link or '(no Drive mirror on file)'}")
        lines.append("")
    if multi_link:
        lines.append("MULTIPLE LINKS POSTED (only the first was checked — review by hand):")
        for month, urls in multi_link:
            lines.append(f"  {month}:")
            for u in urls:
                lines.append(f"    {u}")
        lines.append("")
    lines.append(f"Full provenance (committed to the repo): {MANIFEST_PATH}")
    return subject, "\n".join(lines)


def _apply_staged(manifest: dict, staged_additions: dict, staged_removed_months: list,
                   today: str, now_iso: str) -> None:
    """Merge this run's changed/removed/reappeared decisions into `manifest` IN
    PLACE. Additions (new version entries) are merged before removal flags so a
    removed month seeded for the first time this same run (no prior manifest
    entry) gets its seed appended, then flagged, in one pass. The removal itself
    APPENDS a new version (never mutates the entry it's superseding) — see
    reverify_month()'s 'reappeared' handling, which depends on the removed
    entry's own content_hash/status staying intact for a later comparison."""
    for month, versions in staged_additions.items():
        manifest["months"].setdefault(month, []).extend(versions)
    for month in staged_removed_months:
        entry = latest_version(manifest, month)
        if entry is not None and not entry["status"].startswith("removed-from-source"):
            manifest["months"][month].append({
                "report_title": entry["report_title"],
                "source_url": entry["source_url"],
                "fetched_at": now_iso,
                "content_hash": entry["content_hash"],
                "archive_link": entry.get("archive_link", ""),
                "status": f"removed-from-source {today}",
                "last_verified": today,
            })


def _is_retry_event(last_event: dict, month: str, event: str, key_hash: str) -> str | None:
    """The already-logged archive_link if `month`'s MOST RECENT logged Sheet event
    is exactly (event, key_hash) — an unresolved retry (the owner alert failed on
    the run that first logged it, so the manifest was never advanced past it) —
    or None otherwise, meaning this is a genuinely new event even if this exact
    (event, key_hash) pair appeared earlier in the month's history (see
    sheet_writer.ridgewood_last_event()'s docstring for why "most recent", not
    "ever", is the correct scope)."""
    last = last_event.get(month)
    if last is not None and last[0] == event and last[1] == key_hash:
        return last[2]
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run and not dry_run:
        print(f"[ridgewood-reverify] {reason}")
        return 0
    if dry_run:
        print("[ridgewood-reverify] DRY RUN — no Drive upload, no Sheet write, no email, "
              "manifest not saved. Read-only against the live page + Sheets.")

    rw = cfg.get("ridgewood") or {}
    page_url = rw.get("page_url", rc.DEFAULT_PAGE_URL)
    base_url = rw.get("base_url", rc.DEFAULT_BASE_URL)
    thresholds = rw.get("thresholds") or {}
    allowed_hosts = {h for h in (
        urllib.parse.urlsplit(page_url).hostname,
        urllib.parse.urlsplit(base_url).hostname) if h}
    sheet_id = os.environ["GSHEET_ID"]

    sheets = dc.sheets_service()
    archived = sw.ridgewood_archived_months(sheets, sheet_id)
    if not archived:
        print("[ridgewood-reverify] Stream G hasn't archived any months yet — nothing to verify.")
        return 0
    sheet_rows = sw.ridgewood_report_rows(sheets, sheet_id)
    if not dry_run:
        sw.ensure_ridgewood_revisions_tab(sheets, sheet_id)   # must exist before append_rows() writes to it

    try:
        by_month = rc.scrape_all_report_links(rc.fetch_page(page_url), base_url)
    except rc.RidgewoodFetchError as e:
        print(f"[ridgewood-reverify] page fetch failed, aborting this run: {type(e).__name__}: {e}")
        return 1
    if not by_month:
        print("[ridgewood-reverify] no report links scraped — treating as a bad fetch, aborting.")
        return 1

    missing = sorted(archived - set(by_month))
    confirmed_missing: list[str] = []
    if missing:
        # A second, independent fetch before declaring anything removed — a
        # transient render glitch on ONE fetch must never fire a false removal.
        try:
            by_month2 = rc.scrape_all_report_links(rc.fetch_page(page_url), base_url)
            confirmed_missing = sorted(m for m in missing if m not in by_month2)
        except rc.RidgewoodFetchError as e:
            print(f"[ridgewood-reverify] confirmation fetch failed, skipping removal checks "
                  f"this run: {type(e).__name__}: {e}")
        if confirmed_missing and len(confirmed_missing) > MAX_REMOVED_FRACTION * len(archived):
            print(f"[ridgewood-reverify] {len(confirmed_missing)} of {len(archived)} archived "
                  f"months are missing from the page on TWO fetches — reads as a page "
                  f"restructure or a scrape-format break, not that many real removals. "
                  f"Aborting this run without flagging any as removed.")
            return 1

    manifest = load_manifest()
    today, now_iso = _today(), _now_iso()

    drive = None
    if not dry_run and ac.is_configured(FOLDER_ENV):
        try:
            drive = ac.oauth_drive_service()
        except Exception as e:  # noqa: BLE001 — configured but the OAuth token is dead
            print(f"[ridgewood-reverify] OAuth Drive auth FAILED ({type(e).__name__}). "
                  f"Re-run scripts/oauth_setup.py and update GOAUTH_REFRESH_TOKEN.")
            return 1
    elif not dry_run:
        print("[ridgewood-reverify] Drive folder secret not set — revisions will be logged "
              "without a mirrored copy of the new bytes.")

    last_event = {} if dry_run else sw.ridgewood_last_event(sheets, sheet_id)
    multi_link_flagged = set() if dry_run else sw.ridgewood_multi_link_flagged_months(sheets, sheet_id)
    tmp = tempfile.gettempdir()
    had_errors = False
    staged_additions: dict[str, list] = {}
    staged_removed_months: list[str] = []
    pending_changed, pending_removed, pending_multi_link, pending_reappeared = [], [], [], []
    n_baseline = n_unchanged = 0

    for month in sorted(m for m in archived if m in by_month):
        links = by_month[month]
        bad = [lk for lk in links if not rc.allowed_report_host(lk["url"], allowed_hosts)]
        if bad:
            print(f"  ERR {month}: scraped link host not allowed, skipping this month.")
            had_errors = True
            continue
        link = links[0]
        local = os.path.join(tmp, f"ridgewood-reverify-{month}.pdf")
        searchable_local = local  # overwritten in the "changed" branch; safe default for cleanup
        try:
            # The multi-link Sheet write lives INSIDE this try (not before it) so a
            # Sheets API failure here is caught by the same per-month "one month's
            # failure must not abort the batch" guard below, instead of propagating
            # out of the loop and discarding every other month's progress this run.
            if len(links) > 1 and month not in multi_link_flagged:
                print(f"  NOTE {month}: {len(links)} distinct report links posted for this month — "
                      f"checking the first; a second link for an already-posted month is itself "
                      f"worth a human look.")
                pending_multi_link.append((month, [lk["url"] for lk in links]))
                if not dry_run:
                    sw.append_ridgewood_revision_row(
                        sheets, sheet_id, month, "multi-link", now_iso, "", "", "", "",
                        f"{len(links)} distinct report links posted; only the first is checked. "
                        f"Flagged once — will not re-alert on this same condition.")
                    multi_link_flagged.add(month)
            _download_capped(link["url"], local)
            new_hash = rc.content_hash(local)
            title = rc.report_title(month)
            sheet_row = sheet_rows.get(month)
            prior = latest_version(manifest, month)
            outcome, new_versions = reverify_month(
                manifest, month, sheet_row, title, link["url"], new_hash, today, now_iso)

            if outcome == "baseline":
                manifest["months"].setdefault(month, []).extend(new_versions)
                n_baseline += 1
                print(f"  seed {month}  (first reverify pass — baselined from Stream G's record)")
                continue
            if outcome == "unchanged":
                if prior is not None:   # always true here — reverify_month only
                    prior["last_verified"] = today   # returns "unchanged" when a prior version exists
                n_unchanged += 1
                print(f"  ok   {month}  unchanged")
                continue
            if outcome == "reappeared":
                prior_hash = prior["content_hash"] if prior else ""
                if dry_run:
                    print(f"  REAPPEARED {month}  (dry run — not recorded)")
                    staged_additions.setdefault(month, []).extend(new_versions)
                    continue
                already = _is_retry_event(last_event, month, "reappeared", new_hash)
                if already is not None:
                    reappeared_link = already
                    print(f"  retry {month}  (reappearance already logged; finishing the manifest advance)")
                else:
                    reappeared_link = new_versions[-1]["archive_link"]
                    sw.append_ridgewood_revision_row(
                        sheets, sheet_id, month, "reappeared", now_iso,
                        prior_hash, new_hash, reappeared_link, link["url"],
                        "report reappeared with content identical to the last-known copy; the "
                        "earlier removed-from-source flag is now corrected.")
                    last_event[month] = ("reappeared", new_hash, reappeared_link)
                new_versions[-1]["archive_link"] = reappeared_link
                staged_additions.setdefault(month, []).extend(new_versions)
                pending_reappeared.append((month, reappeared_link))
                print(f"  REAPPEARED {month}  (status corrected)")
                continue

            # outcome == "changed"
            prior_hash = prior["content_hash"] if prior else new_versions[0]["content_hash"]
            if dry_run:
                print(f"  CHANGED {month}  prev={prior_hash[:12]}  new={new_hash[:12]}  (dry run — not recorded)")
                staged_additions.setdefault(month, []).extend(new_versions)
                continue
            already = _is_retry_event(last_event, month, "revised", new_hash)
            if already is not None:
                archive_link, note = already, "(already mirrored + logged in an earlier run; finishing the manifest advance)"
                print(f"  retry {month}  (already mirrored + logged; finishing the manifest advance)")
            else:
                # coder:mirror-ocr (2026-10-13): OCR BEFORE extracting text (ADR 016
                # deferred this for Ridge Wood; archive_client.upload_pdf() now OCRs
                # on upload anyway, so this does it first and reuses the result for
                # extraction too, same ordering fix as ridgewood_archiver.py). Never
                # touches `local` itself — new_hash above is computed on the original
                # bytes, and must stay that way.
                try:
                    searchable_local = ocr_util.ensure_searchable(local)
                except Exception:  # noqa: BLE001 — ensure_searchable is fail-open already;
                    searchable_local = local  # this is just extra defense-in-depth here

                archive_link = ""
                if drive is not None:
                    try:
                        name = f"ridgewood-h2s-{month}-revised-{today.replace('-', '')}-{new_hash[:8]}.pdf"
                        archive_link = ac.upload_pdf(drive, searchable_local, name, ac.folder_id(FOLDER_ENV))
                    except Exception as e:  # noqa: BLE001 — mirror best-effort, never blocks the record
                        print(f"      Drive mirror FAILED for revised {month}: {type(e).__name__}")
                # `ocr_applied` must mean "this reading came from OCR'd text",
                # not just "ensure_searchable() changed the file" — an
                # otherwise born-digital revision with one unrelated
                # image-only page is still natively readable. Try the NATIVE
                # extraction first; only fall back to the OCR'd copy when
                # that finds nothing (same fix as ridgewood_archiver.py).
                ocr_applied = False
                try:
                    text, _, has_text = rc.extract_text(local)
                    if not has_text and searchable_local != local:
                        text, _, has_text = rc.extract_text(searchable_local)
                        ocr_applied = has_text
                    verdict = rc.classify_report(text, thresholds) if has_text else {}
                except Exception as e:  # noqa: BLE001 — the callout is context, never blocking
                    has_text, verdict = False, {}
                    print(f"      could not classify the revised report: {type(e).__name__}")
                note = _revision_note(has_text, verdict, sheet_row, ocr_applied=ocr_applied)
                sw.append_ridgewood_revision_row(
                    sheets, sheet_id, month, "revised", now_iso,
                    prior_hash, new_hash, archive_link, link["url"], note)
                last_event[month] = ("revised", new_hash, archive_link)
            new_versions[-1]["archive_link"] = archive_link
            staged_additions.setdefault(month, []).extend(new_versions)
            pending_changed.append((month, prior_hash, new_hash, archive_link, note))
            print(f"  CHANGED {month}  prev={prior_hash[:12]}  new={new_hash[:12]}")
        except ReverifyDownloadError as e:
            print(f"  ERR {month}: download failed, skipping this month this run: {type(e).__name__}: {e}")
            had_errors = True
        except Exception as e:  # noqa: BLE001 — one month's failure must not abort the batch (mirrors ridgewood_archiver.py)
            print(f"  ERR {month}: unexpected failure, skipping this month this run: {type(e).__name__}")
            had_errors = True
        finally:
            if searchable_local != local and os.path.exists(searchable_local):
                os.remove(searchable_local)
            if os.path.exists(local):
                os.remove(local)

    for month in confirmed_missing:
        try:
            existing = latest_version(manifest, month)
            if existing is None:
                sheet_row = sheet_rows.get(month)
                if sheet_row is None:
                    print(f"  NOTE {month}: no manifest entry and no Sheet row — cannot flag a "
                          f"removal without a baseline; skipping.")
                    continue
                seed = seed_version(sheet_row, sheet_row.get("report_title") or rc.report_title(month),
                                     sheet_row.get("source_url") or "", "", now_iso)
                staged_additions.setdefault(month, []).append(seed)
                existing = seed
            if existing["status"].startswith("removed-from-source"):
                continue   # already flagged a prior run — no re-alert on a month that stays missing
            staged_removed_months.append(month)
            existing_hash = existing.get("content_hash", "")
            archive_link = existing.get("archive_link") or ""
            print(f"  REMOVED {month}  (confirmed absent on two fetches)")
            if not dry_run:
                already = _is_retry_event(last_event, month, "removed-from-source", existing_hash)
                if already is None:
                    sw.append_ridgewood_revision_row(
                        sheets, sheet_id, month, "removed-from-source", now_iso,
                        existing_hash, "", archive_link, existing.get("source_url", ""),
                        "no longer listed on Barr's page; our mirrored copy, if any, is unchanged.")
                    last_event[month] = ("removed-from-source", existing_hash, archive_link)
                else:
                    print(f"  retry {month}  (removal already logged; finishing the manifest advance)")
            pending_removed.append((month, archive_link))
        except Exception as e:  # noqa: BLE001 — one month's failure must not abort the batch
            print(f"  ERR {month}: unexpected failure while processing a removal, skipping: {type(e).__name__}")
            had_errors = True

    if dry_run:
        print(f"[ridgewood-reverify] DRY RUN summary: {len(archived)} months archived, "
              f"{n_baseline} baselined, {n_unchanged} unchanged, {len(pending_changed)} changed, "
              f"{len(pending_removed)} confirmed removed-from-source, "
              f"{len(pending_reappeared)} reappeared, "
              f"{len(pending_multi_link)} with multiple posted links (nothing written).")
        return 1 if had_errors else 0

    if pending_changed or pending_removed or pending_multi_link or pending_reappeared:
        owners = sorted(ea.load_owner_emails(cfg))
        sent_ok = False
        if not owners:
            print("[ridgewood-reverify] changes found but the owner list is empty — failing "
                  "loudly rather than proceeding without notifying anyone.")
        else:
            subject, body = format_owner_alert(
                pending_changed, pending_removed, pending_multi_link, pending_reappeared)
            try:
                sent_ok = ea.send_email(subject, body, cfg, recipients=owners)
            except Exception as e:  # noqa: BLE001
                print(f"[ridgewood-reverify] owner alert raised: {type(e).__name__}")
                sent_ok = False
        if not sent_ok:
            save_manifest(manifest)   # keep this run's baseline/unchanged progress
            print("[ridgewood-reverify] owner alert did NOT go out — the Ridge Wood Revisions "
                  "row(s) above are already durable, but the manifest is not advanced for the "
                  "changed/removed/reappeared month(s); next run retries them idempotently.")
            return 1
        print(f"[ridgewood-reverify] owner alert sent to {len(owners)} recipient(s).")
        _apply_staged(manifest, staged_additions, staged_removed_months, today, now_iso)

    save_manifest(manifest)
    print(f"[ridgewood-reverify] {n_baseline} baselined, {n_unchanged} unchanged, "
          f"{len(pending_changed)} revised, {len(pending_removed)} removed-from-source, "
          f"{len(pending_reappeared)} reappeared.")
    return 1 if had_errors else 0


if __name__ == "__main__":
    sys.exit(run(dry_run="--dry-run" in sys.argv[1:]))
