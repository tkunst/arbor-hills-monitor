"""
egle_newsroom_watcher.py — watch EGLE's newsroom (michigan.gov/egle/newsroom)
for new items and email Trisha when one looks relevant to Arbor Hills.
Standalone + self-terminating, the same shape as pfas_watcher.py.

WHY THIS EXISTS. EGLE announces things that need a timely reply (e.g. the
2026-10-01 call for 2027 water-quality monitoring locations, due 10/30) only in
its newsroom. Nothing else in the monitor watches it.

WHAT IT DOES each run:
  1. Fetch the newsroom landing page and parse its article links. Only the
     landing page is server-rendered, and it lists just the NEWEST FOUR items;
     the full archive (/egle/newsroom/news) is a JS/Coveo search with no public
     key, and EGLE's GovDelivery feed refuses non-browser requests. So the
     workflow polls several times a day (see egle-newsroom-watch.yml), and a GAP
     NOTICE fires if every listed item is new (more may have scrolled past).
  2. Items never seen before (the "EGLE Newsroom Watch" tab IS the state) are
     fetched and scored for relevance against config keywords, using ONLY the
     article body: each article page ends with a "Related News" block whose
     neighbours' keywords (PFAS, recycling, ...) would otherwise leak in.
  3. Durable row FIRST, alert email SECOND (best-effort): a crash between them
     loses the alert, never the record, and never re-fires.

RELEVANCE (egle_newsroom.keywords in config.yml):
  - `specific` terms (Arbor Hills, GFL, Johnson Creek, ...) alert on any hit.
  - `area` terms (Washtenaw, Northville, ...) alert only alongside a `topic`
    term: county names appear in statewide lists (the 2026-09-28 spotted-
    lanternfly item names Washtenaw among infested counties).
  - `topic` terms (landfill, wetland, water quality monitoring, ...) alert if
    one is in the TITLE, or at least `topic_min_body_hits` distinct ones are in
    the body.

FIRST RUN baselines every listed item silently (no alerts). A read error on
the state tab raises (never treated as a first run). LIVENESS: if the landing
page fetches but zero article links parse (layout change, bot wall), exit 1 so
the workflow-failure email surfaces it.

RECIPIENTS are scoped (egle_newsroom.recipients) and never fall through to the
shared alert_recipients list: an empty list means no email, not "everyone".

GATED ON egle_newsroom.enabled.
"""
from __future__ import annotations

import html as _html
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import pfas_client as pc
import email_alerts as ea
from config_loader import load_config

BASE = "https://www.michigan.gov"
_ITEM_RE = re.compile(
    r'<a[^>]+href="((?:https://www\.michigan\.gov)?/egle/newsroom/[a-z-]+/\d{4}/\d{2}/\d{2}/[^"?#]+)"[^>]*>(.*?)</a>',
    re.S | re.I)
_EXCERPT_CHARS = 900


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    if not (cfg.get("egle_newsroom") or {}).get("enabled"):
        return False, "egle_newsroom.enabled is false — skipping (no-op)."
    return True, ""


def _text(fragment: str) -> str:
    # Text extraction for keyword matching only (never rendered as HTML).
    fragment = re.sub(r"<(script|style)\b[^>]*>.*?</\1\b[^>]*>", " ", fragment, flags=re.S | re.I)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_listing(page_html: str) -> list[tuple[str, str]]:
    """[(absolute_url, title)] for each newsroom article link, page order, deduped
    by URL. A link whose anchor text is empty (an image link) takes its title
    from a later anchor to the same URL. Pure — unit-tested."""
    order, titles = [], {}
    for m in _ITEM_RE.finditer(page_html):
        url = urljoin(BASE, m.group(1))
        title = _text(m.group(2))
        if url not in titles:
            order.append(url)
            titles[url] = title
        elif not titles[url] and title:
            titles[url] = title
    return [(u, titles[u]) for u in order]


def article_parts(page_html: str) -> dict:
    """{title, published, body} from an article page. The body is ONLY the
    news-item__section-content block, cut before the tags/contacts and the
    'Related News' block. Pure — unit-tested."""
    m = re.search(r"<title>(.*?)</title>", page_html, re.S | re.I)
    title = _text(m.group(1)) if m else ""
    d = re.search(r'class="news-item__section-date[^"]*"[^>]*>(.*?)</', page_html, re.S)
    published = _text(d.group(1)) if d else ""
    i = page_html.find('class="news-item__section-content"')
    if i < 0:
        return {"title": title, "published": published, "body": ""}
    i = page_html.find(">", i) + 1
    ends = [j for j in (page_html.find('class="news-item__section-tags', i),
                        page_html.find('class="news-item__section-meta', i),
                        page_html.find("Related News", i)) if j > i]
    body = _text(page_html[i:min(ends)] if ends else page_html[i:])
    return {"title": title, "published": published, "body": body}


def _hits(terms: list | None, text: str) -> list[str]:
    return sorted({t for t in terms or []
                   if re.search(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", text, re.I)})


def score(title: str, body: str, kw: dict) -> tuple[bool, list[str]]:
    """(relevant, matched_terms). See module docstring for the rules. Pure."""
    both = f"{title} {body}"
    specific = _hits(kw.get("specific"), both)
    area = _hits(kw.get("area"), both)
    topic_title = _hits(kw.get("topic"), title)
    topic_body = _hits(kw.get("topic"), body)
    topic_all = sorted(set(topic_title) | set(topic_body))
    min_body = int(kw.get("topic_min_body_hits", 2))
    topic_ok = bool(topic_title) or len(topic_body) >= min_body
    relevant = bool(specific) or topic_ok or (bool(area) and bool(topic_all))
    return relevant, specific + area + topic_all if relevant else []


def format_alert(title: str, url: str, published: str, matched: list, body: str) -> str:
    excerpt = body[:_EXCERPT_CHARS] + ("…" if len(body) > _EXCERPT_CHARS else "")
    return (
        "A new EGLE newsroom item looks relevant to Arbor Hills.\n\n"
        f"Title:     {title}\n"
        f"Published: {published or '(not shown)'}\n"
        f"Link:      {url}\n"
        f"Matched:   {', '.join(matched)}\n\n"
        f"{excerpt}\n\n"
        "Automated keyword match on the article text; open the link for the full "
        "release (deadlines, forms, contacts).\n")


def format_gap(listing_url: str, n: int) -> str:
    return (
        f"All {n} items on the EGLE newsroom landing page were new since the last "
        "check, so older items may have scrolled past unseen (the page shows only "
        f"the newest {n}).\n\nPlease skim the full list: {BASE}/egle/newsroom/news\n"
        f"(Landing page: {listing_url})\n")


def _send(subject: str, body: str, cfg: dict, recipients: list) -> None:
    if not recipients:
        # Never fall through to the shared alert list (send_email would, given
        # an empty list).
        print(f"[egle-news] no recipients configured — not sending: {subject!r}")
        return
    try:
        ea.send_email(subject, body, cfg, recipients=list(recipients))
    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
        print(f"[egle-news] row recorded but alert email FAILED: {e}")


def run() -> int:
    cfg = load_config()
    ok, reason = _should_run(cfg)
    if not ok:
        print(f"[egle-news] {reason}")
        return 0
    nc = cfg.get("egle_newsroom") or {}
    listing_url = nc.get("listing_url") or f"{BASE}/egle/newsroom"
    kw = nc.get("keywords") or {}
    recipients = nc.get("recipients") or []

    try:
        items = parse_listing(pc.fetch_page(listing_url))
    except pc.PFASFetchError as e:
        print(f"[egle-news] landing page fetch failed: {e}")
        return 1
    if not items:
        print("[egle-news] landing page fetched but ZERO article links parsed "
              "(layout change or bot wall) — failing loudly.")
        return 1

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_egle_news_tab(sheets, sheet_id)
    seen = sw.egle_news_seen_urls(sheets, sheet_id)  # raises on read error
    today, first_run = _today(), not seen
    new = [(u, t) for u, t in items if u not in seen]

    if first_run:
        sw.append_egle_news_rows(sheets, sheet_id, [
            [today, u, t, "", "", "", "no", "baseline (first run, no alert)", _now()]
            for u, t in new])
        print(f"[egle-news] first run: baselined {len(new)} item(s) silently.")
        return 0

    alerts = 0
    for url, list_title in new:
        try:
            parts = article_parts(pc.fetch_page(url))
            note = ""
        except pc.PFASFetchError as e:
            parts = {"title": list_title, "published": "", "body": ""}
            note = f"article fetch failed; scored on title only ({e})"[:300]
        title = parts["title"] or list_title
        relevant, matched = score(title, parts["body"], kw)
        sw.append_egle_news_rows(sheets, sheet_id, [[
            today, url, title, parts["published"], "yes" if relevant else "no",
            ", ".join(matched), "yes" if relevant and recipients else "no", note, _now()]])
        print(f"[egle-news] new: {title!r} relevant={relevant} {matched}")
        if relevant:
            alerts += 1
            _send(f"[EGLE newsroom] {title}",
                  format_alert(title, url, parts["published"], matched, parts["body"]),
                  cfg, recipients)

    if len(new) == len(items) and len(items) >= int(nc.get("gap_min_items", 4)):
        _send("[EGLE newsroom] Check the full list: items may have been missed",
              format_gap(listing_url, len(items)), cfg, recipients)

    print(f"[egle-news] done — {len(new)} new, {alerts} relevant/alerted.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
