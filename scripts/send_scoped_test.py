"""send_scoped_test.py — send a [TEST] email to exactly ONE address, no one else.

email_alerts.send_test_email() always resolves the FULL recipient list
(config alert_recipients + ALERT_RECIPIENTS_EXTRA), so it's the wrong tool
for "does this one specific address see the CAN-SPAM footer / render
correctly" — every run also emails everyone else on the list. This script
calls send_email() with an EXPLICIT recipients=[...] list, which is used
VERBATIM (never merged with the default audience — see send_email's own
"TRAP GUARDS" docstring section), so it reaches only the address given.

Local-only / attended-run tool (like scripts/oauth_setup.py,
scripts/seed_wds_state.py, scripts/route_hand_curated.py). No permanent
config/secret change needed or made — the address is never added to any
recipient list; this is a one-off probe.

Input: SCOPED_TEST_RECIPIENT env var, a single email address.
SCOPED_TEST_KIND: "basic" (default) sends the one-line footer/render check;
"digest" (2026-11-26) sends a preview of the NEXT Sunday digest, rendered by
the real digest code from the live _meta queue, with the subscriber footer
shown even when the recipient is an owner (owners never get one otherwise).
Digest mode is READ-ONLY: it reads the queue and the site baseline and never
writes _meta, so the real Sunday digest is unaffected.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import email_alerts as ea
from config_loader import load_config


def next_sunday(today: date) -> date:
    return today + timedelta(days=(6 - today.weekday()) % 7)


def build_digest_preview(state: dict, cfg: dict, recipient: str, new_pages: list[dict],
                         today: date) -> tuple[str, str, str]:
    """Pure: (subject, text, html) for a [TEST] preview of the next digest.
    The subscriber footer is put in here for an owner recipient (send_email
    adds it itself for anyone else, so it is never doubled)."""
    import digest_render
    import site_pages
    import watcher
    items = [watcher._record_to_item(r) for r in state.get("pending_digest") or []]
    recap = [watcher._record_to_item(r) for r in state.get("pending_urgent_recap") or []]
    send_date = next_sunday(today)
    labels = site_pages.site_labels(cfg)
    announcement = ea.active_digest_announcement(cfg, today=send_date)
    text = ea.format_digest_body(items, recap, announcement=announcement, send_date=send_date,
                                 new_pages=new_pages, labels=labels)
    html = digest_render.render_html(items, recap, announcement=announcement, send_date=send_date,
                                     new_pages=new_pages, labels=labels)
    mailto, postal = ea._unsubscribe_settings(cfg)
    is_owner = (ea._norm_email(recipient) in ea.load_owner_emails(cfg)
                or ea._email_domain(recipient) in ea.load_owner_domains(cfg))
    if mailto and is_owner:
        text += ea.unsubscribe_footer(cfg, mailto, postal, recipient, show_address=False)
        html = ea._with_html_footer(html, cfg, mailto, postal)
    subject = f"[TEST] Arbor Hills N2688 digest: {len(items)} new document(s)"
    return subject, text, html


def _send_digest_preview(cfg: dict, recipient: str) -> bool:
    import drive_client as dc
    import sheet_writer as sw
    import site_pages
    state = sw.read_meta(dc.sheets_service(), os.environ["GSHEET_ID"])
    # The new baseline is deliberately discarded: only a real sent digest moves it.
    new_pages, _ = site_pages.find_new_pages(cfg, state.get("digest_site_urls") or {})
    subject, text, html = build_digest_preview(state, cfg, recipient, new_pages, date.today())
    return ea.send_email(subject, text, cfg, recipients=[recipient], html=html,
                         footer_show_address=False)


def run() -> int:
    recipient = os.environ.get("SCOPED_TEST_RECIPIENT", "").strip()
    if not recipient:
        print("[send_scoped_test] SCOPED_TEST_RECIPIENT is empty — nothing to do.")
        return 1
    if "," in recipient or ";" in recipient or " " in recipient:
        print("[send_scoped_test] exactly one address, please — nothing sent.")
        return 1
    kind = (os.environ.get("SCOPED_TEST_KIND") or "basic").strip().lower()

    cfg = load_config()
    if kind == "digest":
        sent = _send_digest_preview(cfg, recipient)
    elif kind == "basic":
        sent = ea.send_email(
            "[TEST] Arbor Hills monitor — scoped footer/render check",
            "If you're reading this, the monitor's SMTP alerts are configured "
            "correctly. This is a ONE-OFF scoped test sent to a single address "
            "only (not the full alert list) — no action needed.",
            cfg,
            recipients=[recipient],
        )
    else:
        print(f"[send_scoped_test] unknown SCOPED_TEST_KIND {kind!r} — nothing sent.")
        return 1
    # Public Actions log: no address (SEC-001 count-only logging).
    print(f"[send_scoped_test] kind={kind} sent={sent} to 1 recipient")
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(run())
