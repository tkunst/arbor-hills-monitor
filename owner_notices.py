"""
owner_notices.py — the "For Trisha only" channel (coder:owner-weekly-email,
2026-10-15).

Three things used to reach no one, or the wrong audience: a filing the monitor
could not read after MAX_ERRORS_PER_DOC attempts (stubbed `skipped`, never
classified, so no alert and no digest line), a hand-curated row nobody routed,
and an alert email that failed to send (logged to Actions only). The Sunday
digest can't carry them: it goes to everyone, county commissioners included.

So this module sends to the OWNER ONLY (`email_alerts.load_owner_emails`),
never the alert / digest / watch lists and never a fallback to them:

  A. a weekly "For Trisha only" email (Sunday, after the digest), sections
     omitted when empty and no email at all when every section is empty:
       1. documents that could not be read (stubbed `skipped`) since last week
       2. hand-curated rows not yet routed (since `owner_weekly.hand_curated_since`)
       3. alert emails that failed to send this week
       4. parser review flags logged this week (review_flags.py, ADR 067)
  C. a same-day owner-only email when a document stubbed after repeated
     failures has an enforcement title (risk_register.enforcement_title_signal),
     and, under the same gate, when a document's parse produced a temperature
     above the plausibility ceiling (review_flags.TEMP_KINDS).

Durable state: `last_owner_weekly` in `_meta` (the weekly window), the
append-only "Owner Notices" tab on the PRIVATE Sheet (each owner alert attempt
and each failed send: a permanent record, and section 3's source), and the
"Hand-Curated Routing Log" tab on the case-file Sheet (written by
scripts/route_hand_curated.py; section 2's source). Every read the weekly
email depends on is STRICT (a failed read raises; the weekly is then skipped
and retried next Sunday, never sent as a false "all clear").

Gated by `owner_weekly.enabled` (A) and `owner_weekly.enforcement_alert_enabled`
(C), both shipped false. The Actions log is public: nothing here prints a
title, link or address — counts and exception classes only.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import email_alerts as ea
import nsite_client as nc
import review_flags as rf
import risk_register
import sheet_writer as sw

TAB_OWNER_NOTICES = "Owner Notices"
OWNER_NOTICE_HEADERS = ["Logged At", "Kind", "Doc ID", "Document Name", "Detail", "Link"]
KIND_SEND_FAILED = "send_failed"
KIND_ENFORCEMENT_ALERT = "enforcement_alert"
KIND_REVIEW_ALERT = "review_flag_alert"

TAB_ROUTING_LOG = "Hand-Curated Routing Log"
ROUTING_LOG_HEADERS = ["Drive Link", "Document Name", "Severity", "Routed At"]

WEEKLY_SUBJECT = "[Arbor Hills monitor] Weekly housekeeping (for Trisha only)"
DEFAULT_HAND_CURATED_SINCE = "2026-10-15"


def _CFG_FOR_GATE() -> dict:
    """config.yml, for record_notice's on/off gate (callers on the failure
    paths don't all hold cfg). Unreadable config = gate off."""
    try:
        from config_loader import load_config
        return load_config()
    except Exception:  # noqa: BLE001
        return {}


def _ow(cfg: Optional[dict]) -> dict:
    return (cfg or {}).get("owner_weekly") or {}


def weekly_enabled(cfg: Optional[dict]) -> bool:
    return bool(_ow(cfg).get("enabled", False))


def alerts_enabled(cfg: Optional[dict]) -> bool:
    return bool(_ow(cfg).get("enforcement_alert_enabled", False))


def owner_recipients(cfg: dict) -> list:
    """The owner addresses ONLY. Never resolve_recipients / any list."""
    return sorted(ea.load_owner_emails(cfg))


def send_owner_email(subject: str, body: str, cfg: dict) -> bool:
    """Send to the owner addresses only. Empty owner set = no send (False),
    never a fallback to another list. The subject is kept out of the public
    log. A mid-send SMTP failure raises (EmailSendError), like send_email."""
    recipients = owner_recipients(cfg)
    if not recipients:
        print("[owner_notices] no owner addresses configured; owner email not sent")
        return False
    return ea.send_email(subject, body, cfg, recipients=recipients, log_subject=False)


# ---------------------------------------------------------------------------
# Owner Notices tab (private Sheet)
# ---------------------------------------------------------------------------

def notice_row(kind: str, metadata: dict, detail: str, link: str, now: str) -> list:
    return [now, kind, metadata.get("doc_id", ""),
            metadata.get("egle_title") or metadata.get("document_name", ""), detail, link]


def record_notice(service, kind: str, metadata: dict, detail: str, link: str,
                  now: str) -> bool:
    """Append one Owner Notices row on the private Sheet. Best-effort: never
    raises; logs the exception class only. A no-op while both owner_weekly
    switches are off (ships disabled: no new tab, no extra API calls)."""
    if not (weekly_enabled(_CFG_FOR_GATE()) or alerts_enabled(_CFG_FOR_GATE())):
        return False
    try:
        sheet_id, why = rf.private_sheet_for_write(service)
        if sheet_id is None:
            print(f"[owner_notices] notice not recorded: {why}")
            return False
        sw.ensure_tab(service, sheet_id, TAB_OWNER_NOTICES, OWNER_NOTICE_HEADERS)
        sw.append_rows(service, sheet_id, TAB_OWNER_NOTICES,
                       [notice_row(kind, metadata, detail, link, now)])
        return True
    except Exception as e:  # noqa: BLE001 — a notice must never block the caller
        print(f"[owner_notices] notice not recorded: {type(e).__name__}")
        return False


def record_routing(service, rows: list) -> bool:
    """Append Hand-Curated Routing Log rows ([drive link, document name,
    severity, routed at]) on the PRIVATE Sheet. Best-effort, never raises."""
    if not rows:
        return True
    try:
        sheet_id, why = rf.private_sheet_for_write(service)
        if sheet_id is None:
            print(f"[owner_notices] routing log not written: {why}")
            return False
        sw.ensure_tab(service, sheet_id, TAB_ROUTING_LOG, ROUTING_LOG_HEADERS)
        sw.append_rows(service, sheet_id, TAB_ROUTING_LOG, rows)
        return True
    except Exception as e:  # noqa: BLE001 — never cost the caller's _meta write
        print(f"[owner_notices] routing log not written: {type(e).__name__}")
        return False


def record_send_failure(service, what: str, outcome: str, metadata: dict, link: str,
                        now: str) -> bool:
    """A failed (raised) or skipped (nothing handed to SMTP) send of `what`
    ("urgent alert", "digest", ...). Feeds the weekly email's section 3."""
    return record_notice(service, KIND_SEND_FAILED, metadata, f"{what}: {outcome}", link, now)


def notices_since(service, sheet_id: str, since: str, kinds: Optional[tuple] = None) -> list[dict]:
    """Owner Notices rows logged at/after `since`. STRICT read."""
    out = []
    for r in sw._tab_rows_strict(service, sheet_id, TAB_OWNER_NOTICES, "A2:F"):
        r = list(r) + [""] * (len(OWNER_NOTICE_HEADERS) - len(r))
        rec = dict(zip(OWNER_NOTICE_HEADERS, r))
        if rec["Logged At"] < since or (kinds and rec["Kind"] not in kinds):
            continue
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# C. Same-day owner-only alerts
# ---------------------------------------------------------------------------

def unreadable_enforcement_email(meta: dict, link: str, attempts: int) -> tuple[str, str]:
    """(subject, body) for an enforcement notice that could not be read. Pure."""
    title = meta.get("egle_title") or meta.get("document_name") or "(untitled)"
    subject = f"[URGENT, Trisha only] Enforcement notice filed but unreadable: {title}"
    body = "\n".join([
        "The monitor could not read this filing after "
        f"{attempts} attempts, so it was never classified and sent no alert.",
        "Its regulator title reads as an enforcement action. Open it directly:",
        "",
        f"  Title:       {title}",
        f"  Date filed:  {meta.get('date_filed', '')}",
        f"  Facility:    {meta.get('facility_name') or meta.get('facility_srn', '')}",
        f"  Download:    {link}",
        "",
        "(nSITE refuses links clicked from Gmail; if it fails, paste the URL into",
        "the browser's address bar.)",
        "",
        "Sent to the monitor owner only. Not part of the Sunday digest or its",
        "urgent recap.",
    ])
    return subject, body


def alert_unreadable_enforcement(service, meta: dict, link: str, cfg: Optional[dict],
                                 now: str, attempts: int) -> Optional[bool]:
    """Section C. Called by poison_stub AFTER the stub row and the skipped
    state are written. None = not applicable (gate off, or the title isn't an
    enforcement title); else whether the email was sent. Never raises: a failed
    send is recorded for the weekly email and swallowed."""
    if not alerts_enabled(cfg):
        return None
    title = meta.get("egle_title") or meta.get("document_name") or ""
    if not risk_register.enforcement_title_signal(title):
        return None
    subject, body = unreadable_enforcement_email(meta, link, attempts)
    try:
        sent = send_owner_email(subject, body, cfg)
    except Exception as e:  # noqa: BLE001 — never block stubbing
        record_send_failure(service, "enforcement alert", f"raised {type(e).__name__}",
                            meta, link, now)
        print(f"  owner enforcement alert FAILED: {type(e).__name__}")
        return False
    if sent:
        record_notice(service, KIND_ENFORCEMENT_ALERT, meta, "sent", link, now)
        print("  owner enforcement alert sent")
    else:
        record_send_failure(service, "enforcement alert", "not sent (SMTP or owner list)",
                            meta, link, now)
    return sent


def review_flag_email(flags: list[dict], meta: dict, link: str, max_f) -> tuple[str, str]:
    """(subject, body) for above-ceiling temperatures in one document. Pure."""
    title = meta.get("egle_title") or meta.get("document_name") or "(untitled)"
    subject = f"[Trisha only] Needs review: value above {max_f:g}F in {title}"
    lines = [
        f"Parsing this document produced {len(flags)} temperature value(s) above the "
        f"{max_f:g}F plausibility ceiling.",
        "None of them fired an alert (a value that high is usually a flare temperature",
        "or a misread column), but check the source before relying on it either way.",
        "",
        f"  Document:   {title}",
        f"  Date filed: {meta.get('date_filed', '')}",
        f"  Link:       {link}",
        "",
    ]
    for f in flags:
        v = f.get("value_f")
        parts = [f"{v:g}F" if isinstance(v, (int, float)) else "value n/a"]
        if f.get("well_id"):
            parts.append(f"well {f['well_id']}")
        if f.get("reading"):
            parts.append(f.get("reading"))
        if f.get("pages"):
            parts.append(f"p{f['pages']}")
        lines.append("  - " + ", ".join(parts) + f"  ({f.get('detail', '')})")
    lines += ["", "Sent to the monitor owner only."]
    return subject, "\n".join(lines)


def alert_review_flags(service, flags: list[dict], meta: dict, link: str,
                       cfg: Optional[dict], now: str) -> Optional[bool]:
    """Same-day owner-only email for a document's above-ceiling temperature
    flags, behind the same gate as section C. None = nothing to send or gate
    off. Never raises."""
    temps = [f for f in flags if f.get("kind") in rf.TEMP_KINDS]
    if not temps or not alerts_enabled(cfg):
        return None
    try:
        subject, body = review_flag_email(temps, meta, link, rf.max_plausible_f(cfg))
        sent = send_owner_email(subject, body, cfg)
    except Exception as e:  # noqa: BLE001 — never block the document
        record_send_failure(service, "review-flag alert", f"raised {type(e).__name__}",
                            meta, link, now)
        print(f"  owner review-flag alert FAILED: {type(e).__name__}")
        return False
    if sent:
        record_notice(service, KIND_REVIEW_ALERT, meta, f"sent ({len(temps)} value(s))", link, now)
    else:
        record_send_failure(service, "review-flag alert", "not sent (SMTP or owner list)",
                            meta, link, now)
    return sent


# ---------------------------------------------------------------------------
# A/B. Weekly email
# ---------------------------------------------------------------------------

# Hand-Curated Files columns (docs/hand-curated-intake-design.md).
_HC = {"curated_filename": 0, "title": 1, "source": 2, "doc_date": 3, "origin_url": 7,
       "drive_link": 9, "added_at": 10}


def _col(row: list, name: str) -> str:
    i = _HC[name]
    return (row[i] if len(row) > i else "") or ""


def is_annotation_row(row: list) -> bool:
    """An ANNOTATION-mode row added a description to a file that was already
    public; there is no new file to route. Its drive_link is the
    already-public URL verbatim, i.e. not a Drive copy (or equal to origin_url)."""
    link = _col(row, "drive_link").strip()
    return (not link or "drive.google.com" not in link
            or link == _col(row, "origin_url").strip())


def unrouted_hand_curated(hc_rows: list, routing_rows: list, since: str) -> list[list]:
    """Hand-Curated Files rows added on/after `since` (date prefix compare)
    that have no Routing Log row, matched by Drive link (fallback: the
    curated filename vs the routed document name; never the title, which can
    be generic). Pure."""
    routed_links = {(r[0] if r else "").strip() for r in routing_rows}
    routed_names = {(r[1] if len(r) > 1 else "").strip().lower() for r in routing_rows}
    routed_names.discard("")
    out = []
    for row in hc_rows:
        if _col(row, "added_at")[:10] < since[:10] or is_annotation_row(row):
            continue
        if _col(row, "drive_link").strip() in routed_links:
            continue
        if _col(row, "curated_filename").strip().lower() in routed_names:
            continue
        out.append(row)
    return out


def _route_command_hint(rows: list) -> str:
    """The `gh workflow run` shape for routing these rows (reminder only)."""
    example = rows[0] if rows else []
    link = _col(example, "drive_link") if example else "<drive link>"
    return ("gh workflow run route-hand-curated.yml -f docs_json='[{\"document_name\": "
            "\"...\", \"date_filed\": \"YYYY-MM-DD\", \"doc_type\": \"...\", \"severity\": "
            "\"routine|notable|urgent\", \"risks\": [\"R..\"], \"key_data_point\": \"...\", "
            f"\"link\": \"{link}\"}}]'")


def build_weekly_body(skipped: list, unrouted: list, failed: Optional[list],
                      flags: Optional[list], since: str) -> Optional[str]:
    """The weekly email body, or None when every section is empty. Pure.
    `skipped` = [(doc_id, logged_at, payload)]; `failed`/`flags` = None when
    the private Sheet isn't available (section omitted)."""
    sections = []
    if skipped:
        lines = [f"1. COULD NOT BE READ ({len(skipped)}): stubbed after repeated failures, "
                 "never classified, no alert sent."]
        for did, logged_at, p in skipped:
            title = p.get("egle_title") or p.get("document_name") or "(untitled)"
            why = p.get("error_class") or p.get("reason") or ""
            lines += [f"  - {title}",
                      f"      filed {p.get('date_filed', '')}"
                      + (f" | {p['facility']}" if p.get("facility") else "")
                      + f" | stubbed {logged_at[:10]}",
                      f"      {nc.native_download_url(did)}",
                      f"      error: {why}"]
        sections.append("\n".join(lines))
    if unrouted:
        lines = [f"2. HAND-CURATED ROWS NOT YET ROUTED ({len(unrouted)}): no urgent/digest "
                 "routing recorded for these. Routing an urgent one needs your OK."]
        for row in unrouted:
            lines += [f"  - {_col(row, 'title') or _col(row, 'curated_filename')}",
                      f"      added {_col(row, 'added_at')[:10]} | {_col(row, 'drive_link')}"]
        lines += ["", "  Route with:", "    " + _route_command_hint(unrouted)]
        sections.append("\n".join(lines))
    if failed:
        lines = [f"3. ALERTS THAT FAILED TO SEND ({len(failed)}):"]
        for n in failed:
            lines.append(f"  - {n['Logged At'][:16]}  {n['Detail']}"
                         + (f"  ({n['Document Name']})" if n.get("Document Name") else ""))
        sections.append("\n".join(lines))
    if flags:
        lines = [f"4. PARSER REVIEW FLAGS ({len(flags)}): values the monitor did not use "
                 "(above the plausibility ceiling, rejected tables, unreadable pages)."]
        for f in flags:
            val = f" {f['Value (F)']}F" if f.get("Value (F)") else ""
            well = f" {f['Well ID']}" if f.get("Well ID") else ""
            pages = f" p{f['Pages']}" if f.get("Pages") else ""
            lines.append(f"  - {f['Kind']}{val}{well}{pages}: {f.get('Document Name', '')}")
            lines.append(f"      {f.get('Detail', '')} | {f.get('Link', '')}")
        sections.append("\n".join(lines))
    if not sections:
        return None
    if failed is None and flags is None:
        sections.append("(Failed sends and parser review flags could not be listed: no "
                        "usable private Sheet. Hand-curated routing is not recorded "
                        "either, so section 2 may list rows that were routed.)")
    head = (f"Monitor housekeeping since {since[:10]}. Sent to the monitor owner only; "
            "none of this is in the Sunday digest.\n")
    return head + "\n\n".join([""] + sections) + "\n"


def weekly_since(state: dict, today: date) -> str:
    return state.get("last_owner_weekly") or (today - timedelta(days=7)).isoformat()


def gather_weekly(service, sheet_id: str, cfg: dict, since: str) -> tuple:
    """Read everything the weekly email lists. STRICT reads throughout."""
    skipped = sw.state_events_since(service, sheet_id, "skipped", since)
    hc_since = str(_ow(cfg).get("hand_curated_since") or DEFAULT_HAND_CURATED_SINCE)
    hc_rows = sw._tab_rows_strict(service, sheet_id, sw.TAB_HANDCURATED, "A2:M")
    priv, _why = rf.private_sheet_for_write(service)
    failed = flags = None
    routing: list = []
    if priv is not None:
        routing = sw._tab_rows_strict(service, priv, TAB_ROUTING_LOG, "A2:D")
        failed = notices_since(service, priv, since, kinds=(KIND_SEND_FAILED,))
        flags = rf.flags_since(service, priv, since)
    unrouted = unrouted_hand_curated(hc_rows, routing, hc_since)
    return skipped, unrouted, failed, flags


def send_weekly(service, sheet_id: str, state: dict, cfg: dict, today: date, now: str) -> str:
    """Section A. Returns a short outcome word for the run log. Advances
    `last_owner_weekly` (written to _meta) only after a successful send or a
    week with nothing to say; a failed send is recorded and retried next
    Sunday over the same window. Raises only on a read failure (the caller
    isolates it)."""
    if not weekly_enabled(cfg):
        return "disabled"
    since = weekly_since(state, today)
    body = build_weekly_body(*gather_weekly(service, sheet_id, cfg, since), since=since)
    if body is not None:
        try:
            sent = send_owner_email(WEEKLY_SUBJECT, body, cfg)
        except Exception as e:  # noqa: BLE001 — record, keep the window, retry next week
            record_send_failure(service, "owner weekly email", f"raised {type(e).__name__}",
                                {}, "", now)
            return f"failed ({type(e).__name__})"
        if not sent:
            record_send_failure(service, "owner weekly email", "not sent (SMTP or owner list)",
                                {}, "", now)
            return "not sent"
    state["last_owner_weekly"] = now
    sw.write_meta(service, sheet_id, state)
    return "sent" if body is not None else "nothing to send"
