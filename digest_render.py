"""
digest_render.py — the Sunday digest's content, as plain text and as HTML.

Pure (no network, no clock): email_alerts.send_digest passes in the items, the
send date and the active announcement, and gets back the two bodies that go
out as one multipart/alternative message. Layout "B, dense scan table", chosen
by Trisha 2026-11-26 from the mockups in that session.

Rules this module holds (Arbor Hills Monitor design system, data layer):
  * Sections, in order: urgent items from the past two weeks, correspondence
    and enforcement, deadlines and notices, other notable, other routine.
    Each lists items newest first by digest_date() (the document's own date,
    else nSITE's Date Filed, which is printed as "filed ...").
  * No risk codes (R1-R10): subscribers never see the risk register.
  * The count line counts exactly what each section shows.
  * Every filer- or model-written string is HTML-escaped, and only http(s)
    links become hrefs (titles and summaries are untrusted input, SEC-003).
  * The unsubscribe footer is NOT rendered here. The HTML carries
    FOOTER_SLOT where send_email puts the per-recipient footer (ADR 040),
    so owner copies can go without one.
  * Gmail clips an HTML body near 102 KB and hides everything below the cut,
    footer included. Above HTML_BUDGET_BYTES the oldest routine (then
    notable, then deadline) rows are dropped from the HTML only and replaced
    by an "...and N more" line; the urgent recap is never trimmed. The plain
    text part always lists everything.
"""
from __future__ import annotations

import html as _html
import re
from datetime import date

FOOTER_SLOT = "<!--AHM-UNSUBSCRIBE-FOOTER-->"
HTML_BUDGET_BYTES = 85_000
LOGO_URL = "https://arborhillsmonitor.org/img/letterhead-mark-96.png"
SITE_URL = "https://arborhillsmonitor.org/"
RECORDS_URL = "https://arborhillsmonitor.org/public-records/"
AUTO_SUMMARY_NOTE = (
    "The one-line descriptions above are automated summaries of the linked "
    "documents. They are machine-generated and may contain errors. Consult the "
    "source document before relying on them."
)

# Design-system tokens (light values; email is light-only).
MON_BG = "#ffffff"
MON_FG = "#1a1a1a"
MON_MUTED = "#555555"
MON_LINK = "#0a5b8c"
MON_BORDER = "#dddddd"
MON_MARK = "#2f7fb3"
ADV_TINT = "#f4f7f9"          # mon-advocacy-tint flattened onto white
SERIF = "Georgia, 'Times New Roman', serif"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

_SEV_RANK = {"urgent": 0, "notable": 1}


# --------------------------------------------------------------------------- #
# Dates and sections
# --------------------------------------------------------------------------- #

def digest_date(metadata: dict) -> str:
    """The date the digest shows and sorts by: the date printed on the document
    (`document_date`, ADR 065), else nSITE's Date Filed. Both are ISO strings,
    so they sort as text."""
    return metadata.get("document_date") or metadata.get("date_filed") or ""


def date_label(metadata: dict) -> str:
    """Printed date for one plain-text line. A fallback to Date Filed is labeled
    "filed ..." so a reader never mistakes a filing date for the document's."""
    if metadata.get("document_date"):
        return metadata["document_date"]
    filed = metadata.get("date_filed") or ""
    return f"filed {filed}" if filed else ""


def newest_first(items: list[dict]) -> list[dict]:
    """Newest first by digest_date(). Stable, so items with the same date keep
    their queue order; an item with no date sorts last."""
    return sorted(items, key=lambda it: digest_date(it["metadata"]), reverse=True)


def sections(items: list[dict], urgent_recap: list[dict] | None) -> list[tuple[str, str, list[dict]]]:
    """[(key, label, items)] in display order, empty sections left out."""
    recap = newest_first(list(urgent_recap or []))
    items = newest_first(list(items))
    corr = [it for it in items if getattr(it["parsed"], "is_correspondence", False)]
    rest = [it for it in items if not getattr(it["parsed"], "is_correspondence", False)]
    proc = [it for it in rest if it["parsed"].doc_type == "procedural"]
    other = [it for it in rest if it["parsed"].doc_type != "procedural"]
    notable = [it for it in other if it["parsed"].severity in _SEV_RANK]
    routine = [it for it in other if it["parsed"].severity not in _SEV_RANK]
    out = [
        ("urgent", "Urgent items from the past two weeks", recap),
        ("correspondence", "Correspondence and enforcement", corr),
        ("deadlines", "Deadlines and notices", proc),
        ("notable", "Other new documents: notable", notable),
        ("routine", "Other new documents: routine", routine),
    ]
    return [s for s in out if s[2]]


SECTION_NOTES = {"correspondence": "EGLE letter under a generic filing title"}


def count_line(items: list[dict], urgent_recap: list[dict] | None, sep: str = " · ") -> str:
    """'17 new documents · 1 urgent from the past two weeks · ...', each number
    matching the section it names. Urgent and correspondence appear only when
    present; deadlines and notable always do."""
    by_key = {k: len(its) for k, _label, its in sections(items, urgent_recap)}
    n = len(items)
    parts = [f"{n} new document{'' if n == 1 else 's'}"]
    if by_key.get("urgent"):
        parts.append(f"{by_key['urgent']} urgent from the past two weeks")
    if by_key.get("correspondence"):
        parts.append(f"{by_key['correspondence']} correspondence")
    parts.append(f"{by_key.get('deadlines', 0)} deadlines and notices")
    parts.append(f"{by_key.get('notable', 0)} notable")
    return sep.join(parts)


def _meta_bits(it: dict) -> list[str]:
    p, m = it["parsed"], it["metadata"]
    bits = [str(p.doc_type or ""), str(p.severity or "")]
    if m.get("urgent_sent_at"):
        bits.append(f"urgent alert sent {str(m['urgent_sent_at'])[:10]}")
    return [b for b in bits if b]


def _long_date(d: date) -> str:
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #

def render_text(items: list[dict], urgent_recap: list[dict] | None = None,
                announcement: str = "", send_date: date | None = None,
                new_pages: list[dict] | None = None, labels: dict | None = None) -> str:
    lines: list[str] = []
    if announcement:
        lines += ["ANNOUNCEMENT:", *[f"  {ln}" if ln else "" for ln in announcement.strip().splitlines()], ""]
    if new_pages:
        lines.append("NEW AT ARBOR HILLS MONITOR:")
        for site, pages in _pages_by_site(new_pages, labels):
            host, disclosure = (labels or {}).get(site, (site, ""))
            lines.append(f"  {host}" + (f" ({disclosure})" if disclosure else ""))
            for p in pages:
                lines += [f"  - {p['title']}", *([f"      {p['desc']}"] if p.get("desc") else []),
                          f"      {p['url']}"]
        lines.append("")
    if not items and not urgent_recap:
        return "\n".join(lines + ["No new Arbor Hills (N2688) documents this period."])
    head = "Arbor Hills Monitor weekly digest"
    if send_date:
        head += f", {_long_date(send_date)}"
    lines += [head, count_line(items, urgent_recap) + ".", ""]
    for key, label, its in sections(items, urgent_recap):
        note = SECTION_NOTES.get(key)
        lines.append(f"{label.upper()} ({len(its)})" + (f": {note}" if note else ""))
        for it in its:
            m = it["metadata"]
            lines.append(f"  - {date_label(m)}  {m.get('document_name', '')}")
            lines.append(f"      {' · '.join(_meta_bits(it))}")
            lines.append(f"      {it['parsed'].key_data_point}")
            lines.append(f"      {it['link']}")
        lines.append("")
    lines.append(AUTO_SUMMARY_NOTE)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

def e(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def safe_href(url) -> str:
    """The URL, escaped, if it is http(s); otherwise "" (render as text)."""
    u = str(url or "").strip()
    return e(u) if re.match(r"^https?://", u, re.IGNORECASE) else ""


def _link(url, text: str, style: str) -> str:
    href = safe_href(url)
    return f'<a href="{href}" style="{style}">{text}</a>' if href else f'<span style="{style}">{text}</span>'


def _linkify(escaped: str) -> str:
    """Turn bare http(s) URLs in already-escaped text into links."""
    return re.sub(r"https?://[^\s<]+[^\s<.,;:!?)]",
                  lambda mm: f'<a href="{mm.group(0)}" style="color:{MON_LINK};">{mm.group(0)}</a>', escaped)


def _series_label(text: str) -> str:
    return (f'<div style="font-family:{SANS};font-size:12px;line-height:18px;letter-spacing:0.5px;'
            f'text-transform:uppercase;color:{MON_MUTED};">{text}</div>')


def _masthead(subline: str, send_date: date | None) -> str:
    when = f"Weekly digest &middot; {e(_long_date(send_date))}" if send_date else "Weekly digest"
    return f"""
<tr><td style="padding:26px 28px 0 28px;">
  {_series_label(when)}
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="padding-top:4px;"><tr>
    <td valign="middle" style="padding-right:10px;"><img src="{LOGO_URL}" width="40" height="40" alt="" style="display:block;width:40px;height:40px;border:0;"></td>
    <td valign="middle" style="font-family:{SERIF};font-size:26px;line-height:32px;font-weight:bold;color:{MON_FG};">Arbor Hills Monitor</td>
  </tr></table>
  <div style="font-family:{SERIF};font-size:16px;line-height:25px;color:{MON_MUTED};padding:4px 0 14px 0;">{subline}</div>
</td></tr>
<tr><td style="padding:0 28px;"><div style="border-top:2px solid {MON_MARK};font-size:1px;line-height:1px;">&nbsp;</div></td></tr>"""


def _pages_by_site(new_pages, labels):
    order = list((labels or {}).keys())
    sites = order + [p["site"] for p in new_pages if p["site"] not in order]
    seen, out = set(), []
    for site in sites:
        if site in seen:
            continue
        seen.add(site)
        pages = [p for p in new_pages if p["site"] == site]
        if pages:
            out.append((site, pages))
    return out


def _new_pages_html(new_pages, labels) -> str:
    blocks = []
    for site, pages in _pages_by_site(new_pages, labels):
        host, disclosure = (labels or {}).get(site, (site, ""))
        disc = (f'<div style="font-family:{SERIF};font-size:14px;line-height:21px;font-style:italic;color:{MON_MUTED};">{e(disclosure)}</div>'
                if disclosure else "")
        items = "".join(
            f'''<div style="padding-top:9px;">
          {_link(p["url"], e(p["title"]) + " &rarr;", f"font-family:{SERIF};font-size:16px;line-height:23px;font-weight:bold;color:{MON_LINK};text-decoration:none;")}
          {f'<div style="font-family:{SERIF};font-size:15px;line-height:23px;color:{MON_FG};">{e(p["desc"])}</div>' if p.get("desc") else ""}
        </div>''' for p in pages)
        blocks.append(f'''<div style="padding-top:12px;">
        <div style="font-family:{SANS};font-size:13px;line-height:20px;font-weight:bold;color:{MON_FG};">{e(host)}</div>
        {disc}{items}
      </div>''')
    return "".join(blocks)


def _announcement_box(announcement: str, new_pages=None, labels=None) -> str:
    if not announcement and not new_pages:
        return ""
    label = "Announcement &middot; New at Arbor Hills Monitor" if new_pages else "Announcement"
    paras = "".join(
        f'<div style="font-family:{SERIF};font-size:15px;line-height:23px;color:{MON_FG};padding-top:6px;">{_linkify(e(ln))}</div>'
        for ln in (announcement or "").strip().splitlines() if ln.strip())
    return f"""
<tr><td style="padding:22px 28px 4px 28px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td width="4" bgcolor="{MON_LINK}" style="background:{MON_LINK};width:4px;font-size:1px;line-height:1px;">&nbsp;</td>
    <td bgcolor="{ADV_TINT}" style="background:{ADV_TINT};padding:12px 16px 14px 16px;">
      {_series_label(label)}
      {paras}{_new_pages_html(new_pages or [], labels)}
    </td>
  </tr></table>
</td></tr>"""


def _date_cell(m: dict) -> str:
    dd = digest_date(m)
    try:
        d = date.fromisoformat(dd)
        shown = f"{d.strftime('%b')} {d.day}<br>{d.year}"
    except ValueError:
        # Hand-curated dates can be partial: "2016-09" or "2016".
        mm = re.fullmatch(r"(\d{4})-(\d{2})", dd)
        if mm and 1 <= int(mm.group(2)) <= 12:
            shown = f"{date(int(mm.group(1)), int(mm.group(2)), 1).strftime('%b')}<br>{mm.group(1)}"
        else:
            shown = e(dd)
    if not m.get("document_date") and dd:
        shown += "<br>filed"
    return shown


def _row(it: dict) -> str:
    m, p = it["metadata"], it["parsed"]
    title = _link(it.get("link"), e(m.get("document_name", "")),
                  f"font-family:{SERIF};font-size:15px;line-height:21px;color:{MON_LINK};font-weight:bold;text-decoration:none;")
    meta = " &middot; ".join(e(b) for b in _meta_bits(it))
    return f"""
<tr>
  <td valign="top" style="padding:10px 8px 10px 28px;border-bottom:1px solid {MON_BORDER};font-family:{SANS};font-size:13px;line-height:18px;color:{MON_MUTED};white-space:nowrap;width:56px;">{_date_cell(m)}</td>
  <td valign="top" style="padding:10px 28px 10px 4px;border-bottom:1px solid {MON_BORDER};">
    {title}
    <div style="font-family:{SERIF};font-size:14px;line-height:21px;color:{MON_FG};padding-top:3px;">{e(p.key_data_point)}</div>
    <div style="font-family:{SANS};font-size:13px;line-height:20px;color:{MON_MUTED};">{meta}</div>
  </td>
</tr>"""


def _section(key: str, label: str, its: list[dict], hidden: int) -> str:
    note = SECTION_NOTES.get(key)
    note_html = (f'<div style="font-family:{SERIF};font-size:14px;line-height:21px;font-style:italic;color:{MON_MUTED};">{e(note)}</div>'
                 if note else "")
    more = ""
    if hidden:
        more = f"""
<tr><td colspan="2" style="padding:10px 28px;font-family:{SERIF};font-size:14px;line-height:21px;color:{MON_MUTED};">
  &hellip;and {hidden} more, listed in the plain-text version of this email and on
  {_link(RECORDS_URL, "the public records page", f"color:{MON_LINK};")}.
</td></tr>"""
    return f"""
<tr><td style="padding:22px 0 0 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td colspan="2" style="padding:0 28px 6px 28px;">
  <div style="font-family:{SERIF};font-size:17px;line-height:23px;font-weight:bold;color:{MON_FG};">{e(label)} <span style="font-weight:normal;color:{MON_MUTED};">({len(its) + hidden})</span></div>
  {note_html}
</td></tr>
<tr><td colspan="2" style="padding:0 28px;"><div style="border-top:1px solid {MON_FG};font-size:1px;line-height:1px;">&nbsp;</div></td></tr>
{"".join(_row(it) for it in its)}{more}
</table>
</td></tr>"""


def _preheader(text: str) -> str:
    return (f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
            f'font-size:1px;line-height:1px;color:{MON_BG};">{e(text)}'
            + "&#8199;&#65279;&#847; " * 30 + "</div>")


def _page(items, urgent_recap, announcement, send_date, shown_sections,
          new_pages=None, labels=None) -> str:
    counts = count_line(items, urgent_recap)
    rows = _masthead(e(counts) + ".", send_date)
    rows += _announcement_box(announcement, new_pages, labels)
    for key, label, its, hidden in shown_sections:
        rows += _section(key, label, its, hidden)
    rows += f"""
<tr><td style="padding:22px 28px 8px 28px;">
  <div style="font-family:{SERIF};font-size:13px;line-height:18px;font-style:italic;color:{MON_MUTED};">{e(AUTO_SUMMARY_NOTE)}</div>
</td></tr>
{FOOTER_SLOT}"""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Arbor Hills Monitor weekly digest</title></head>
<body style="margin:0;padding:0;background:{MON_BG};" bgcolor="{MON_BG}">
{_preheader(counts)}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{MON_BG}" style="background:{MON_BG};">
<tr><td align="center" style="padding:16px 8px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{MON_BG}" style="width:100%;max-width:600px;background:{MON_BG};">
{rows}
</table>
</td></tr></table>
</body></html>"""


def render_html(items: list[dict], urgent_recap: list[dict] | None = None,
                announcement: str = "", send_date: date | None = None,
                budget: int = HTML_BUDGET_BYTES, new_pages: list[dict] | None = None,
                labels: dict | None = None) -> str:
    """The HTML body, with FOOTER_SLOT where the per-recipient footer goes.
    Trims the oldest rows (routine, then notable, then deadlines and notices,
    then correspondence; never the urgent recap) to stay under `budget` bytes,
    so Gmail never clips off the footer."""
    secs = [[k, label, list(its), 0] for k, label, its in sections(items, urgent_recap)]
    out = _page(items, urgent_recap, announcement, send_date, secs, new_pages, labels)
    for key in ("routine", "notable", "deadlines", "correspondence"):
        sec = next((s for s in secs if s[0] == key), None)
        while sec and sec[2] and len(out.encode("utf-8")) > budget:
            drop = max(1, len(sec[2]) // 4)
            sec[2] = sec[2][:-drop]          # newest first, so the oldest go
            sec[3] += drop
            out = _page(items, urgent_recap, announcement, send_date, secs, new_pages, labels)
    return out


def footer_html(reason: str, mailto: str, sender: str, postal: str = "") -> str:
    """The non-owner footer row that send_email puts in FOOTER_SLOT."""
    postal_line = f"<br>{e(postal)}" if postal else ""
    return f"""
<tr><td style="padding:12px 28px 28px 28px;border-top:1px solid {MON_BORDER};font-family:{SERIF};font-size:14px;line-height:22px;color:{MON_MUTED};">
  {e(reason)}<br>
  To unsubscribe from all Arbor Hills Monitor emails, reply to this message or email
  <a href="mailto:{e(mailto)}" style="color:{MON_LINK};">{e(mailto)}</a>.<br>
  {e(sender)} &middot; <a href="{SITE_URL}" style="color:{MON_LINK};">arborhillsmonitor.org</a>{postal_line}
</td></tr>"""
