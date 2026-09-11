"""
email_alerts.py — SMTP urgent alerts + weekly digest.

SMTP (not the Gmail MCP connector — that's interactive-only). Creds come from
SMTP_* env vars; recipients from config.yml (edit the list, no code change).

Urgency is decided here, not in the parser: a doc is urgent if the parser marked
it "urgent" OR a temperature reading at/above the configured threshold appears in
its key data point / text. `is_urgent` and `_max_temperature_f` are pure and
unit-tested.
"""
from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from typing import Optional

# Matches "180F", "180 F", "180°F", "180 degrees F", "180 deg F"
_TEMP_RE = re.compile(r"(\d{2,4})\s*(?:°|deg(?:rees)?\.?\s*)?\s*f\b", re.IGNORECASE)


def _max_temperature_f(text: str) -> Optional[int]:
    """Return the highest plausible Fahrenheit reading found in text, or None."""
    if not text:
        return None
    best = None
    for m in _TEMP_RE.finditer(text):
        val = int(m.group(1))
        if 50 <= val <= 1000:  # ignore stray years / tiny numbers
            best = val if best is None else max(best, val)
    return best


def max_measured_temp_f(parsed) -> Optional[int]:
    """Highest MEASURED temperature (deg F) among the structured measurements.
    Permitted ceilings / HOV-waiver limits are deliberately excluded — a 180F
    permitted ceiling is not a 180F reading."""
    best = None
    for m in getattr(parsed, "measurements", []) or []:
        if m.get("metric") != "temperature" or m.get("basis") != "measured":
            continue
        unit = (m.get("unit") or "F").lower()
        try:
            val = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if unit.startswith("c"):  # convert any stray Celsius readings
            val = val * 9 / 5 + 32
        ival = int(round(val))
        best = ival if best is None else max(best, ival)
    return best


def is_urgent(parsed, cfg: dict) -> bool:
    urgent_cfg = cfg.get("urgent", {})
    if urgent_cfg.get("severity_is_urgent", True) and parsed.severity == "urgent":
        return True
    threshold = urgent_cfg.get("measured_temp_urgent_f", 145)

    # Prefer structured measurements (correctly excludes permitted ceilings).
    measured = max_measured_temp_f(parsed)
    if measured is not None:
        return measured >= threshold

    # If the parser extracted ANY temperature reading at all (even a permitted
    # ceiling or unknown-basis one), trust the structured path and do NOT regex
    # free text — otherwise a permitted 180F ceiling mentioned in the text would
    # falsely fire, which is the exact conflation this whole design avoids.
    has_any_temp = any(
        (m.get("metric") == "temperature")
        for m in (getattr(parsed, "measurements", []) or [])
    )
    if has_any_temp:
        return False

    # Last resort, ONLY when the parser produced no structured temperature at
    # all: scan free text. Less safe (can't tell measured from permitted).
    hay = f"{parsed.key_data_point}\n{parsed.summary}\n{parsed.full_text}"
    temp = _max_temperature_f(hay)
    return temp is not None and temp >= threshold


# ---------------------------------------------------------------------------
# Composition (pure-ish — no network)
# ---------------------------------------------------------------------------


def format_urgent_body(parsed, metadata: dict, link: str) -> str:
    return (
        f"URGENT — Arbor Hills (N2688) document flagged.\n\n"
        f"Document: {metadata.get('document_name', '(unknown)')}\n"
        f"Date filed: {metadata.get('date_filed', '(unknown)')}\n"
        f"Type: {parsed.doc_type}   Severity: {parsed.severity}\n"
        f"Risks: {', '.join(parsed.risks) or '(none tagged)'}\n\n"
        f"Key data point:\n  {parsed.key_data_point}\n\n"
        f"Summary:\n  {parsed.summary}\n\n"
        f"Document: {link}\n"
    )


def format_digest_body(items: list[dict], urgent_recap: list[dict] | None = None) -> str:
    """items: [{parsed, metadata, link}]. Procedural action items first.

    urgent_recap: same shape, plus each item's metadata carries
    'urgent_sent_at' — a recap of items sent as their own same-day [URGENT]
    email to the urgent-tier list. Rendered FIRST. NOTE: the header deliberately
    does NOT say the items were "already emailed separately" — digest-only
    recipients (e.g. DIGEST_RECIPIENTS_EXTRA — the commissioners) do NOT receive
    the same-day [URGENT] emails, so for them this recap is their FIRST sighting,
    not a repeat; claiming otherwise would be false. The 'Sent <date>' line still
    records when the urgent email went to the urgent-tier list."""
    if not items and not urgent_recap:
        return "No new Arbor Hills (N2688) documents this period."
    lines = []
    if urgent_recap:
        lines.append("URGENT ITEMS FROM EARLIER:")
        for it in urgent_recap:
            p, m = it["parsed"], it["metadata"]
            sent_at = m.get("urgent_sent_at", "")
            lines.append(f"  - Sent {sent_at}  {m.get('document_name','')}")
            lines.append(f"      {p.key_data_point}")
            lines.append(f"      {it['link']}")
        lines.append("")
    procedural = [it for it in items if it["parsed"].doc_type == "procedural"]
    others = [it for it in items if it["parsed"].doc_type != "procedural"]
    lines += [f"Arbor Hills (N2688) digest — {len(items)} new document(s).", ""]
    if procedural:
        lines.append("ACTION ITEMS (deadlines / notices):")
        for it in procedural:
            p, m = it["parsed"], it["metadata"]
            lines.append(f"  - {m.get('date_filed','')}  {m.get('document_name','')}")
            lines.append(f"      {p.key_data_point}")
            lines.append(f"      {it['link']}")
        lines.append("")
    if others:
        lines.append("OTHER NEW DOCUMENTS:")
        for it in others:
            p, m = it["parsed"], it["metadata"]
            risks = ", ".join(p.risks) or "-"
            lines.append(
                f"  - {m.get('date_filed','')}  [{p.doc_type}/{p.severity}/{risks}]  "
                f"{m.get('document_name','')}"
            )
            lines.append(f"      {p.key_data_point}")
            lines.append(f"      {it['link']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------


def merge_extra_recipients(base: list, env_var: str) -> list:
    """Merge `base` with addresses from `env_var` (comma/semicolon-separated),
    order preserved, de-duplicated. Generalizes the ALERT_RECIPIENTS_EXTRA
    pattern (a PRIVATE address added WITHOUT committing it to this PUBLIC
    repo's config.yml) so any recipient list — not just the main
    `alert_recipients` — can take a private supplement via its own env var.
    See resolve_recipients() below for the original single-purpose caller, and
    gfl_air_watcher.py's use with GFL_AIR_WATCH_RECIPIENTS_EXTRA for a second."""
    out = list(base or [])
    for addr in (os.environ.get(env_var, "") or "").replace(";", ",").split(","):
        addr = addr.strip()
        if addr and addr not in out:
            out.append(addr)
    return out


def resolve_recipients(cfg: dict) -> list:
    """Recipients = config.yml `alert_recipients` PLUS any in the
    ALERT_RECIPIENTS_EXTRA env (comma/semicolon-separated). The env is how a
    PRIVATE address (e.g. a personal stopgap inbox) gets added WITHOUT committing
    it to this PUBLIC repo's config.yml. Order preserved, de-duplicated."""
    return merge_extra_recipients(cfg.get("alert_recipients", []) or [], "ALERT_RECIPIENTS_EXTRA")


# ---------------------------------------------------------------------------
# Unsubscribe / suppression (CAN-SPAM) — ADR 040
#
# One shared suppression list + a List-Unsubscribe header + a compliant footer,
# applied HERE at send_email — the single choke point every stream already calls
# (urgent, Sunday digest, GFL-air WATCH, and every nsite_*/mmd/ride/rop/... watch)
# — so there is no per-stream wiring and no other module is touched. Mechanism is
# mailto: the send path is plain SMTP (no Resend), and RFC-8058 one-click needs an
# HTTPS POST endpoint this static-GitHub-Pages project has no backend for.
# ---------------------------------------------------------------------------


def _norm_email(addr: str) -> str:
    """Lowercased + trimmed — the canonical form for ALL address comparison
    (suppression, owner-matching), so case/whitespace never defeats the guard."""
    return (addr or "").strip().lower()


def _split_env_emails(env_var: str) -> list:
    """Addresses from a comma/semicolon-separated env var — same shape as the
    ALERT_RECIPIENTS_EXTRA / GFL_AIR_WATCH_RECIPIENTS_EXTRA private-supplement
    pattern already used here."""
    raw = (os.environ.get(env_var, "") or "").replace(";", ",")
    return [a.strip() for a in raw.split(",") if a.strip()]


def load_owner_emails(cfg: dict | None = None) -> set:
    """Trisha's OWN addresses — NEVER suppressed, NEVER dropped by the fail-safe,
    and exempt from the unsubscribe footer/header (the self-lockout guard the goal
    requires). Two sources, UNIONED:
      - config `unsubscribe.owner_addresses` — fine in this PUBLIC repo for a
        non-sensitive address like the arbor-hills@ catch-all, which is ALREADY
        committed in `alert_recipients`; and
      - the `MONITOR_OWNER_EMAILS` env secret — for her PRIVATE addresses
        (proton / gmail) that must NOT be committed to a public repo.
    Populate BOTH so every owner address that appears on ANY recipient list is
    protected; a missing one would let the fail-safe drop her (see send_email)."""
    owners = set()
    if cfg:
        for a in ((cfg.get("unsubscribe") or {}).get("owner_addresses") or []):
            n = _norm_email(a)
            if n:
                owners.add(n)
    for a in _split_env_emails("MONITOR_OWNER_EMAILS"):
        n = _norm_email(a)
        if n:
            owners.add(n)
    return owners


def load_suppressed_emails(owners: set | None = None, cfg: dict | None = None) -> set:
    """Opted-out addresses from the `UNSUBSCRIBED_EMAILS` env secret (comma/
    semicolon list). Deliberately a PRIVATE secret, never a Sheet/config tab: a
    suppression list is third-party PII, and both the case-file Sheet and
    config.yml are PUBLIC. Owner addresses are removed from the result, so an
    owner can NEVER be suppressed — even by a mistaken entry."""
    if owners is None:
        owners = load_owner_emails(cfg)
    supp = {_norm_email(a) for a in _split_env_emails("UNSUBSCRIBED_EMAILS")}
    return supp - owners


def _unsubscribe_settings(cfg: dict) -> tuple:
    """(mailto, postal_address) from config, trimmed. BOTH must be non-empty for
    the apparatus to be ARMED — a compliant message needs a working opt-out method
    AND a physical postal address. `postal_address` ships EMPTY (Trisha's call), so
    'postal address configured' is itself the arming switch; there is deliberately
    no separate `enabled` flag (that would recreate the compliance gap)."""
    u = cfg.get("unsubscribe") or {}
    return (u.get("mailto") or "").strip(), (u.get("postal_address") or "").strip()


def build_list_unsubscribe(mailto: str, recipient: str) -> str:
    """RFC 2369 List-Unsubscribe value: a mailto whose body carries the EXACT
    address to remove, so manual triage (or a future inbox-reader) can add the
    right address to UNSUBSCRIBED_EMAILS without guessing from headers."""
    from urllib.parse import quote
    subj = quote("Unsubscribe: Arbor Hills Landfill Monitor")
    body = quote(
        "Unsubscribe this address from all Arbor Hills Landfill Monitor "
        f"emails: {recipient}"
    )
    return f"<mailto:{mailto}?subject={subj}&body={body}>"


def unsubscribe_footer(cfg: dict, mailto: str, postal: str, recipient: str) -> str:
    """The CAN-SPAM footer: why they receive it, how to opt out (of ALL monitor
    mail), a clear sender identity, and a valid physical postal address — the two
    requirements beyond the link. Uses the standard '-- ' signature separator."""
    u = cfg.get("unsubscribe") or {}
    sender = (u.get("sender_identity") or "Arbor Hills Landfill Monitor").strip()
    reason = (u.get("receiving_reason")
              or "You are receiving this because you subscribed to Arbor Hills "
                 "Landfill Monitor email alerts.").strip()
    return (
        "\n\n-- \n"
        f"{reason}\n"
        f"To unsubscribe from all Arbor Hills Landfill Monitor emails, reply to "
        f"this message or email {mailto} (this address: {recipient}).\n"
        f"{sender}\n"
        f"{postal}\n"
    )


def send_email(subject: str, body: str, cfg: dict, recipients: list | None = None) -> bool:
    """Send to all configured recipients via SMTP (TLS). No-op with a warning if
    SMTP env vars are missing (so a dry/local run doesn't crash) — returns False
    in that case, True once at least one message was actually handed to the SMTP
    server, so a caller that needs to distinguish "sent" from "silently skipped"
    can (e.g. _route_urgent_or_digest in watcher.py, which must not record a
    same-day [URGENT] alert as sent — and recap it later — if it never actually
    went out). A mid-send SMTP failure still raises (unchanged).

    `recipients` overrides the audience: when a non-empty list is passed it is used
    VERBATIM (not merged with the shared `alert_recipients` list or the
    ALERT_RECIPIENTS_EXTRA env). This is how a narrowly-scoped alert reaches only a
    subset — e.g. the CivicClerk meeting-watch (ADR 015) sends meeting-logistics
    changes to Trisha alone, not the whole advocacy list. When None/empty, the
    default whole-list behaviour (resolve_recipients) is unchanged.

    CAN-SPAM unsubscribe (ADR 040) — applied HERE so all three multi-recipient
    streams (urgent, digest, GFL-air WATCH) inherit it from one choke point:
      * The shared suppression list (`UNSUBSCRIBED_EMAILS`) is filtered out of
        EVERY send. Owner addresses (load_owner_emails) are never suppressed.
      * A non-owner recipient's message gets a `List-Unsubscribe` mailto header
        and a compliant footer (sender identity + postal address + opt-out) — but
        ONLY when the apparatus is ARMED (unsubscribe.mailto AND
        unsubscribe.postal_address both set). Owner copies never get the footer.
      * Fail-safe when NOT armed: a non-owner recipient is DROPPED rather than
        sent a non-compliant commercial message — self-arming the moment the
        postal address is filled in. Owners are always sent. If the owner list is
        EMPTY we cannot classify third parties, so we DON'T drop (never risk
        silencing Trisha) and only warn.

    TRAP GUARDS:
      * An emptied SCOPED (explicitly-passed) list sends to NOBODY — it never
        falls through to the default `alert_recipients` audience (that fallthrough
        only runs when NO list was passed). Prevents a suppressed private/scoped
        list from leaking to the coalition list.
      * The URGENT path always includes an owner (arbor-hills@…), so suppression
        and the fail-safe can never empty it → never a spurious False → never a
        silently-unsent-and-unrecapped urgent doc."""
    scoped = bool(recipients)
    recipients = list(recipients) if scoped else resolve_recipients(cfg)

    owners = load_owner_emails(cfg)
    suppressed = load_suppressed_emails(owners, cfg)
    if suppressed and not owners:
        print("[email_alerts] WARNING: UNSUBSCRIBED_EMAILS is set but the owner "
              "list (MONITOR_OWNER_EMAILS / unsubscribe.owner_addresses) is empty "
              "— owner self-protection is inert and no recipient will be dropped. "
              "Set the owner list so Trisha's own addresses can never be "
              "suppressed.")
    recipients = [r for r in recipients if _norm_email(r) not in suppressed]

    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not (host and user and password and recipients):
        # An emptied SCOPED list lands here (recipients falsy) and returns False —
        # it does NOT fall through to resolve_recipients (that only happens above,
        # and only when `scoped` is False). This is the private-list-leak guard.
        print(f"[email_alerts] SMTP not configured / no recipients — would send: {subject!r}")
        return False

    mailto, postal = _unsubscribe_settings(cfg)
    armed = bool(mailto and postal)

    sender = os.environ.get("SMTP_FROM") or user
    sent = 0
    dropped = []
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        for recipient in recipients:
            is_owner = _norm_email(recipient) in owners
            out_body = body
            add_unsub_header = False
            if not is_owner:
                if armed:
                    out_body = body + unsubscribe_footer(cfg, mailto, postal, recipient)
                    add_unsub_header = True
                elif owners:
                    # We can positively tell this is a third party AND we cannot
                    # be compliant (unarmed) → do NOT send non-compliant mail.
                    # Self-arms once the postal address is set. Owners unaffected.
                    dropped.append(recipient)
                    continue
                # else: owners empty → can't classify a third party → send as-is
                # (never risk silencing Trisha); the WARNING above surfaced it.
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = recipient
            if add_unsub_header:
                # No List-Unsubscribe-Post header: RFC-8058 one-click needs an
                # HTTPS POST endpoint, and this static-Pages project has no
                # backend — mailto is the compliant mechanism.
                msg["List-Unsubscribe"] = build_list_unsubscribe(mailto, recipient)
            msg.set_content(out_body)
            server.send_message(msg)
            sent += 1
    if dropped:
        print(f"[email_alerts] {len(dropped)} non-owner recipient(s) NOT sent "
              f"{subject!r} — unsubscribe apparatus not armed. Set "
              f"unsubscribe.postal_address (+ unsubscribe.mailto) to send them. "
              f"Held: {', '.join(dropped)}")
    print(f"[email_alerts] sent {subject!r} to {sent} recipient(s)")
    return sent > 0


def send_test_email(cfg: dict) -> None:
    """Send a one-off test message to the configured recipients to verify SMTP is
    wired up end-to-end. Triggered by the daily workflow's `send_test` input."""
    send_email(
        "[TEST] Arbor Hills monitor — email is working",
        "If you're reading this, the monitor's SMTP alerts are configured "
        "correctly. Urgent alerts (same-day) and the weekly digest will arrive "
        "this way. This is a manual test send; no action needed.",
        cfg,
    )


def send_urgent_alert(parsed, metadata: dict, link: str, cfg: dict) -> bool:
    """Returns True iff the email was actually handed to the SMTP server (False
    if SMTP is unconfigured/no recipients — see send_email). watcher.py's
    _route_urgent_or_digest uses this to avoid recording a same-day [URGENT]
    alert as sent, and recapping it in the Sunday digest, when it never
    actually went out."""
    subject = f"[URGENT] Arbor Hills N2688: {metadata.get('document_name', 'new document')}"
    return send_email(subject, format_urgent_body(parsed, metadata, link), cfg)


def send_digest(items: list[dict], cfg: dict, urgent_recap: list[dict] | None = None) -> None:
    """The weekly digest goes to resolve_recipients(cfg) PLUS anyone in
    DIGEST_RECIPIENTS_EXTRA (comma/semicolon-separated env, private — not
    committed to this PUBLIC repo's config.yml). This is a DIGEST-ONLY
    supplement: send_urgent_alert() above does not read this env, so adding
    someone here gets them the Sunday roundup without also putting them on the
    same-day [URGENT] send. Added 2026-08-21, Trisha's direction, to bring
    Washtenaw commissioners onto the digest ahead of adding them to urgent/
    methane-perimeter alerts later — see SUSPENSE.md for the follow-up dates.

    urgent_recap: items already sent as their own same-day [URGENT] email,
    recapped here under their own labeled section. Kept out of the subject
    line on purpose — the header still reads as "N new documents"; the recap
    section inside the body is what surfaces the urgent items."""
    subject = f"Arbor Hills N2688 digest — {len(items)} new document(s)"
    recipients = merge_extra_recipients(resolve_recipients(cfg), "DIGEST_RECIPIENTS_EXTRA")
    send_email(subject, format_digest_body(items, urgent_recap), cfg, recipients=recipients)


def send_upcoming(upcoming_block: str, cfg: dict) -> None:
    """Send the digest's "Upcoming Activities" section as its OWN email, scoped
    VERBATIM to `upcoming.recipients` in config (the same `send_email(recipients=…)`
    override the CivicClerk meeting-watch uses, ADR 015). Kept SEPARATE from
    send_digest on purpose: the section is sourced from the PRIVATE Sheet
    (strategy-flavored key dates), so it must never ride in the coalition document
    digest that goes to the full `alert_recipients` list. FAIL-SAFE on the
    confidential direction — if `upcoming.recipients` is unset/empty the section is
    NOT sent at all, rather than falling back to the whole list the way
    `resolve_recipients` would. No-op on an empty block."""
    if not upcoming_block:
        return
    recipients = (cfg.get("upcoming") or {}).get("recipients")
    if not recipients:
        print("[email_alerts] upcoming section not sent: no upcoming.recipients configured")
        return
    send_email(
        "Arbor Hills — Upcoming Activities (next 14 days)", upcoming_block, cfg,
        recipients=recipients,
    )
