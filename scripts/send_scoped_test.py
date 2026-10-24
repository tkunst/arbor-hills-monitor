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
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import email_alerts as ea
from config_loader import load_config


def run() -> int:
    recipient = os.environ.get("SCOPED_TEST_RECIPIENT", "").strip()
    if not recipient:
        print("[send_scoped_test] SCOPED_TEST_RECIPIENT is empty — nothing to do.")
        return 1

    cfg = load_config()
    sent = ea.send_email(
        "[TEST] Arbor Hills monitor — scoped footer/render check",
        "If you're reading this, the monitor's SMTP alerts are configured "
        "correctly. This is a ONE-OFF scoped test sent to a single address "
        "only (not the full alert list) — no action needed.",
        cfg,
        recipients=[recipient],
    )
    print(f"[send_scoped_test] sent={sent} recipient={recipient}")
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(run())
