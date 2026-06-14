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


def format_digest_body(items: list[dict]) -> str:
    """items: [{parsed, metadata, link}]. Procedural action items first."""
    if not items:
        return "No new Arbor Hills (N2688) documents this period."
    procedural = [it for it in items if it["parsed"].doc_type == "procedural"]
    others = [it for it in items if it["parsed"].doc_type != "procedural"]
    lines = [f"Arbor Hills (N2688) digest — {len(items)} new document(s).", ""]
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


def send_email(subject: str, body: str, cfg: dict) -> None:
    """Send to all configured recipients via SMTP (TLS). No-op with a warning if
    SMTP env vars are missing (so a dry/local run doesn't crash)."""
    recipients = cfg.get("alert_recipients", [])
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not (host and user and password and recipients):
        print(f"[email_alerts] SMTP not configured / no recipients — would send: {subject!r}")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    print(f"[email_alerts] sent {subject!r} to {len(recipients)} recipient(s)")


def send_urgent_alert(parsed, metadata: dict, link: str, cfg: dict) -> None:
    subject = f"[URGENT] Arbor Hills N2688: {metadata.get('document_name', 'new document')}"
    send_email(subject, format_urgent_body(parsed, metadata, link), cfg)


def send_digest(items: list[dict], cfg: dict) -> None:
    subject = f"Arbor Hills N2688 digest — {len(items)} new document(s)"
    send_email(subject, format_digest_body(items), cfg)
