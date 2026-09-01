#!/usr/bin/env python3
"""gen_public_comment_feed.py -- regenerate the public "Open Public Comment
Periods" page (site/public-comment/) from EGLE's LIVE public notices.

Unlike the Findings feed (which renders the case-file Sheet), this page
live-fetches EGLE's active public notices at generation time, for two reasons:
a comment-deadline tracker has to be as current as possible, and the authoritative
comment-window dates live in EGLE's notice feed, not in the monitor's Sheet.
It reuses the same read-only client calls the watchers already make
(nsite_client.fetch_site_public_notices per facility + rop_client for the
statewide ROP notice) -- no new endpoints, no writes anywhere.

Usage: python3 scripts/gen_public_comment_feed.py
Run by .github/workflows/public-comment.yml on a schedule; the workflow commits
the result only when the content (not just the timestamp) changed.

This page is FACTUAL RECORDS -- see docs/editorial-standards-two-sites.md. It
lists what is open and links to the official EGLE notice; it does not urge anyone
to comment (that belongs on the orange advocacy site).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover - zoneinfo always present on py3.12 CI
    _ET = None

# Repo root on path so `import nsite_client` etc. resolve when run as
# `python3 scripts/gen_public_comment_feed.py` (same idiom as the sibling
# gen_findings_feed.py / term_search.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nsite_client  # noqa: E402
import public_comment_feed as pcf  # noqa: E402
import rop_client  # noqa: E402
from findings_feed import facility_display  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "site", "public-comment")


def _load_sites() -> list[dict]:
    import yaml  # local import: only this entrypoint needs it
    with open(os.path.join(REPO_ROOT, "config.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("nsite_sites") or []


def _display_name(site: dict) -> str:
    """A reader-friendly facility label: the shared alias map from
    findings_feed, with a trailing ' (SRN)' suffix stripped (the raw config
    names carry it to disambiguate duplicate-named nSITE profiles, but it reads
    as noise to the public)."""
    name = facility_display(site.get("name") or site.get("srn") or "")
    srn = site.get("srn") or ""
    suffix = f" ({srn})"
    if srn and name.endswith(suffix):
        name = name[: -len(suffix)]
    return name.strip()


def collect_entries(session) -> tuple[list[dict], list[str]]:
    """Return (entries, errors). Raises SystemExit only if EVERY facility fetch
    fails (a systemic outage) -- a partial failure is reported on the page via
    `errors` rather than silently shrinking the list to look like "nothing
    open"."""
    sites = _load_sites()
    entries: list[dict] = []
    errors: list[str] = []
    open_srns: set[str] = set()
    fetched_any = False

    for site in sites:
        srn, nsite_id = site.get("srn", ""), str(site.get("id", ""))
        name = _display_name(site)
        try:
            notices = nsite_client.fetch_site_public_notices(session, nsite_id)
            fetched_any = True
        except nsite_client.NsiteFetchError as exc:
            errors.append(f"Could not check {name} ({srn}): {exc}")
            continue
        for n in notices:
            entries.append({
                "facility": name,
                "srn": srn,
                "kind": "EGLE public notice",
                "notice_id": n.get("notice_id", ""),
                "coverage": n.get("coverage", ""),
                "start_date": n.get("start_date", ""),
                "end_date": n.get("end_date", ""),
                "link": pcf.comment_link(n.get("notice_id", "")),
                "source": "notice",
            })
            open_srns.add(srn)

    if not fetched_any:
        raise SystemExit(
            "Refusing to write: every facility public-notice fetch failed "
            f"({len(errors)} error(s)). This looks like an EGLE-side or network "
            "outage, not an empty result -- leaving the existing "
            "site/public-comment/ untouched. Errors: " + " | ".join(errors)
        )

    _add_statewide_rop(entries, errors, open_srns)
    return entries, errors


def _notice_text(pdf_bytes: bytes) -> str:
    import fitz  # pymupdf, the repo's existing PDF text-layer dependency
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _add_statewide_rop(entries: list[dict], errors: list[str],
                       open_srns: set[str]) -> None:
    """EGLE runs air Renewable Operating Permit comment periods through a single
    monthly statewide notice PDF. A facility's ROP window sometimes appears ONLY
    there, with no facility-specific nSITE notice (Emerald RNG's did earlier in
    2026), so we read that notice too. We read ONLY its 30-DAY PUBLIC COMMENT
    section (a facility in the 45-DAY EPA REVIEW / FINAL sections is NOT open for
    comment) and extract each facility's exact close date -- see
    public_comment_feed.parse_rop_30day_comment. A facility that already has its
    own dated notice this run gets an annotation instead of a duplicate row."""
    try:
        text = _notice_text(rop_client.fetch_notice_pdf())
    except Exception as exc:  # noqa: BLE001 - report, don't abort the whole page
        errors.append(
            "Could not check EGLE's statewide Renewable Operating Permit public "
            f"notice: {exc}. Check it directly: {pcf.ROP_NOTICE_URL}"
        )
        return

    for srn, end_iso in pcf.parse_rop_30day_comment(text, rop_client.TARGET_SRNS).items():
        if srn in open_srns:
            # Already listed via its own dated notice -- annotate that row so the
            # reader knows it is the ROP renewal, rather than emitting a dup.
            for e in entries:
                if e.get("srn") == srn and e.get("source") == "notice":
                    e["note"] = (
                        "This facility is also named in EGLE's statewide "
                        "Renewable Operating Permit (air permit) public notice."
                    )
            continue
        note = "Open for comment in EGLE's monthly statewide ROP public notice."
        if not end_iso:
            note += " See the notice for the exact deadline."
        entries.append({
            "facility": _srn_label(srn),
            "srn": srn,
            "kind": "Air Renewable Operating Permit (ROP) renewal",
            "notice_id": "",
            "coverage": "Statewide ROP public notice",
            "start_date": "",
            "end_date": end_iso or "",
            "link": pcf.ROP_NOTICE_URL,
            "note": note,
            "source": "rop",
        })


def _srn_label(srn: str) -> str:
    """A display name for a statewide-ROP-only facility, looked up from the site
    registry so it matches the rest of the page (falls back to the bare SRN)."""
    for site in _load_sites():
        if site.get("srn") == srn:
            return _display_name(site)
    return srn


def main() -> None:
    session = nsite_client.make_session()
    entries, errors = collect_entries(session)

    now_utc = datetime.now(timezone.utc)
    generated_at = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    today = (now_utc.astimezone(_ET) if _ET else now_utc).date().isoformat()

    buckets = pcf.bucket_entries(entries, today)
    page = pcf.render_page(buckets, generated_at, errors)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Wrote {OUT_DIR}/index.html "
          f"({len(buckets['open'])} open, {len(buckets['upcoming'])} upcoming, "
          f"{len(buckets['closed'])} recently closed, {len(errors)} error(s)).")


if __name__ == "__main__":
    main()
