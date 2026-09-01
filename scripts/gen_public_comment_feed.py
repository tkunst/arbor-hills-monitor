#!/usr/bin/env python3
"""gen_public_comment_feed.py -- regenerate the public "Public Comment Periods"
page (site/public-comment/) from EGLE's LIVE public notices, and maintain the
persisted state that lets closed periods keep showing after EGLE drops them.

Unlike the Findings feed (which renders the case-file Sheet), this page
live-fetches EGLE's active public notices at generation time: a comment-deadline
tracker has to be current, and the authoritative window dates live in EGLE's
notice feed, not the Sheet. It reuses the read-only client calls the watchers
already make (nsite_client.fetch_site_public_notices per facility + rop_client
for the statewide ROP notice) -- no new endpoints, no secrets.

State: EGLE removes a notice from its active feed once the comment period ends,
so a purely stateless render would forget a period ever existed. This repo
forbids committed data files (`*.json` is gitignored and blocked by data-guard),
so state rides INSIDE the committed page as a hidden JSON block and each run
reads its own previously-committed index.html back to recover it -- the same
self-referential trick gen_findings_feed uses (`_previous_total`). Each run
upserts the currently-open periods, marks any that dropped out (and were actually
checked this run) as closed, and fills a closed ROP period's Outcome as "issued"
when it reaches the statewide notice's FINAL section. The Open table renders the
currently-open periods; the Closed table renders the remembered closed ones.
`what` and `outcome` are preserved across runs, so they can be hand-curated in
the page's embedded state block (e.g. "Wetland 1 PFAS JPA") without being
overwritten.

Usage: python3 scripts/gen_public_comment_feed.py
Run by .github/workflows/public-comment.yml on a schedule; the workflow commits
site/public-comment + the state file only when the content changed.

This page is FACTUAL RECORDS -- see docs/editorial-standards-two-sites.md. It
lists what is open/closed and links to the official EGLE notice; it does not urge
anyone to comment (that belongs on the orange advocacy site).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

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

NOTICE_LABEL = "EGLE public notice"
ROP_LABEL = "Air Renewable Operating Permit (ROP) renewal"


def _load_sites() -> list[dict]:
    import yaml  # local import: only this entrypoint needs it
    with open(os.path.join(REPO_ROOT, "config.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("nsite_sites") or []


def _display_name(site: dict) -> str:
    """A reader-friendly facility label: the shared alias map from findings_feed,
    with a trailing ' (SRN)' suffix stripped (the raw config names carry it to
    disambiguate duplicate-named nSITE profiles, but it reads as noise)."""
    name = facility_display(site.get("name") or site.get("srn") or "")
    srn = site.get("srn") or ""
    suffix = f" ({srn})"
    if srn and name.endswith(suffix):
        name = name[: -len(suffix)]
    return name.strip()


def _srn_label(srn: str) -> str:
    for site in _load_sites():
        if site.get("srn") == srn:
            return _display_name(site)
    return srn


def _notice_text(pdf_bytes: bytes) -> str:
    import fitz  # pymupdf, the repo's existing PDF text-layer dependency
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def collect_open(session):
    """Return (open_entries, errors, fetched_srns, rop_fetched, rop_final_srns).

    open_entries: [{key, facility, srn, what, opened, closes, link, source}].
    Raises SystemExit only if EVERY facility fetch fails (a systemic outage) --
    a partial failure is reported via `errors`, and `fetched_srns` records which
    facilities were actually reached so update_state never marks a period closed
    just because a transient fetch error hid it."""
    open_entries: list[dict] = []
    errors: list[str] = []
    fetched_srns: set[str] = set()
    open_srns: set[str] = set()
    fetched_any = False

    for site in _load_sites():
        srn, nsite_id = site.get("srn", ""), str(site.get("id", ""))
        name = _display_name(site)
        try:
            notices = nsite_client.fetch_site_public_notices(session, nsite_id)
            fetched_any = True
            fetched_srns.add(srn)
        except nsite_client.NsiteFetchError as exc:
            errors.append(f"Could not check {name} ({srn}): {exc}")
            continue
        for n in notices:
            nid = n.get("notice_id", "")
            open_entries.append({
                "key": f"notice:{nid}",
                "facility": name,
                "srn": srn,
                "what": NOTICE_LABEL,
                "opened": n.get("start_date", ""),
                "closes": n.get("end_date", ""),
                "link": pcf.comment_link(nid),
                "source": "notice",
            })
            open_srns.add(srn)

    if not fetched_any:
        raise SystemExit(
            "Refusing to write: every facility public-notice fetch failed "
            f"({len(errors)} error(s)). This looks like an EGLE-side or network "
            "outage, not an empty result -- leaving the existing files untouched. "
            "Errors: " + " | ".join(errors)
        )

    rop_fetched = False
    rop_final_srns: set[str] = set()
    try:
        text = _notice_text(rop_client.fetch_notice_pdf())
        rop_fetched = True
    except Exception as exc:  # noqa: BLE001 - report, don't abort the whole page
        errors.append(
            "Could not check EGLE's statewide Renewable Operating Permit public "
            f"notice: {exc}. Check it directly: {pcf.ROP_NOTICE_URL}"
        )
        return open_entries, errors, fetched_srns, rop_fetched, rop_final_srns

    rop_final_srns = pcf.parse_rop_final(text, rop_client.TARGET_SRNS)
    for srn, end_iso in pcf.parse_rop_30day_comment(text, rop_client.TARGET_SRNS).items():
        if srn in open_srns:
            # Already listed via its own dated notice -- name it as the ROP so the
            # reader knows what it is, rather than emitting a duplicate row.
            for e in open_entries:
                if e["srn"] == srn and e["source"] == "notice":
                    e["what"] = ROP_LABEL
            continue
        open_entries.append({
            "key": f"rop:{srn}",
            "facility": _srn_label(srn),
            "srn": srn,
            "what": ROP_LABEL,
            "opened": "",
            "closes": end_iso or "",
            "link": pcf.ROP_NOTICE_URL,
            "source": "rop",
        })
    return open_entries, errors, fetched_srns, rop_fetched, rop_final_srns


def load_prior_state() -> dict:
    """Recover state from the previously-committed page's embedded block (see
    public_comment_feed.render_state_block / parse_state_block). Empty on the
    first run, when there is no prior page yet. This is how state persists
    without a committed data file -- the same self-referential trick
    gen_findings_feed uses to read its own previous document count."""
    path = os.path.join(OUT_DIR, "index.html")
    try:
        with open(path, encoding="utf-8") as f:
            return pcf.parse_state_block(f.read())
    except FileNotFoundError:
        return {}


def main() -> None:
    session = nsite_client.make_session()
    open_entries, errors, fetched_srns, rop_fetched, rop_final_srns = collect_open(session)

    state = load_prior_state()
    open_render, closed_render = pcf.update_state(
        state, open_entries, fetched_srns, rop_fetched, rop_final_srns)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = pcf.render_page(open_render, closed_render, generated_at, errors)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Wrote {OUT_DIR}/index.html ({len(open_render)} open, "
          f"{len(closed_render)} closed, {len(errors)} error(s)).")


if __name__ == "__main__":
    main()
