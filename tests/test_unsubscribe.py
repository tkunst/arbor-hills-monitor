"""CAN-SPAM unsubscribe (ADR 040) — the shared suppression list + List-Unsubscribe
header + compliant footer, all applied at the single send_email choke point so
every stream (same-day [URGENT], Sunday digest, GFL-air perimeter WATCH, and any
other multi-recipient send) inherits it.

Covers the handoff's five required tests, the four traps the design was built
against, and — per the review bar for a live-mail change — real EmailMessage
serialization (the actual header + footer as they'd hit the wire), not just
pure-function asserts.
"""
import smtplib

import pytest

import email_alerts as ea
from egle_doc_parser import ParsedDoc


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

_ENV_VARS = (
    "UNSUBSCRIBED_EMAILS", "MONITOR_OWNER_EMAILS", "ALERT_RECIPIENTS_EXTRA",
    "DIGEST_RECIPIENTS_EXTRA", "GFL_AIR_WATCH_RECIPIENTS_EXTRA",
    "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_PORT",
)

OWNER = "arbor-hills@trishakunst.com"
ALLY = "ally@example.org"          # a legitimate non-owner third party
COMMISSIONER = "commissioner@washtenaw.org"

POSTAL = "PO Box 123, Ann Arbor, MI 48104"
MAILTO = "unsubscribe@trishakunst.com"

# Fully-armed config: mailto + postal both set → apparatus is armed.
ARMED = {
    "alert_recipients": [OWNER, ALLY],
    "unsubscribe": {
        "mailto": MAILTO,
        "postal_address": POSTAL,
        "sender_identity": "Arbor Hills Landfill Monitor",
        "owner_addresses": [OWNER],
    },
}

# Not armed: no postal address → self-arming fail-safe (non-owners held).
UNARMED = {
    "alert_recipients": [OWNER, ALLY],
    "unsubscribe": {
        "mailto": MAILTO,
        "postal_address": "",
        "owner_addresses": [OWNER],
    },
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)


class _CapturingServer:
    """A stand-in SMTP server that records the real EmailMessage objects it is
    handed (one per recipient, since send_email loops)."""

    def __init__(self):
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        self.messages.append(msg)


def _smtp(monkeypatch):
    """Configure SMTP env and install the capturing server. Returns it."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("SMTP_FROM", "monitor@trishakunst.com")
    srv = _CapturingServer()
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout=30: srv)
    return srv


def _by_to(srv):
    """Captured messages keyed by their To address."""
    return {m["To"]: m for m in srv.messages}


def _urgent_doc():
    return ParsedDoc(
        summary="s", key_data_point="k", doc_type="evidence", risks=["R8"],
        severity="urgent", full_text="", ocr_applied=False, page_count=1,
        measurements=[],
    )


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def test_norm_email_lowercases_and_trims():
    assert ea._norm_email("  Foo@Example.ORG ") == "foo@example.org"
    assert ea._norm_email(None) == ""


def test_load_owner_emails_unions_config_and_env_normalized(monkeypatch):
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", " Trisha@Proton.me ; ME@GMAIL.com ")
    owners = ea.load_owner_emails(ARMED)
    assert owners == {OWNER, "trisha@proton.me", "me@gmail.com"}


def test_load_suppressed_never_contains_an_owner(monkeypatch):
    # An owner address accidentally added to the suppression list is removed,
    # so it can never be suppressed (self-lockout guard).
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", f"{ALLY}, {OWNER.upper()}")
    supp = ea.load_suppressed_emails(cfg=ARMED)
    assert supp == {ALLY}


def test_build_list_unsubscribe_is_wellformed():
    hdr = ea.build_list_unsubscribe(MAILTO, ALLY)
    assert hdr.startswith(f"<mailto:{MAILTO}?subject=")
    assert hdr.endswith(">")
    # the recipient address is carried in the (url-encoded) body so triage knows
    # exactly whom to add to UNSUBSCRIBED_EMAILS
    assert "ally%40example.org" in hdr


def test_unsubscribe_footer_has_identity_postal_and_mailto():
    footer = ea.unsubscribe_footer(ARMED, MAILTO, POSTAL, ALLY)
    assert footer.startswith("\n\n-- \n")          # standard signature separator
    assert "Arbor Hills Landfill Monitor" in footer  # sender identity
    assert POSTAL in footer                          # physical postal address
    assert MAILTO in footer                          # opt-out method
    assert ALLY in footer                            # this recipient's address


# --------------------------------------------------------------------------- #
# Suppression (all streams route through send_email)
# --------------------------------------------------------------------------- #

def test_suppressed_address_is_dropped(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", ALLY)
    ea.send_email("s", "body", ARMED, recipients=[OWNER, ALLY])
    tos = set(_by_to(srv))
    assert OWNER in tos
    assert ALLY not in tos


def test_owner_is_never_suppressed_case_insensitive(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    # owner listed for suppression (wrong case + spaces) must still be delivered
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", f"  {OWNER.upper()} ")
    assert ea.send_email("s", "body", ARMED, recipients=[OWNER]) is True
    assert OWNER in _by_to(srv)


def test_empty_suppression_list_is_a_noop(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    ea.send_email("s", "body", ARMED, recipients=[OWNER, ALLY])
    assert set(_by_to(srv)) == {OWNER, ALLY}


def test_add_then_remove_suppression_round_trips(monkeypatch):
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    # added -> dropped
    srv1 = _smtp(monkeypatch)
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", ALLY)
    ea.send_email("s", "body", ARMED, recipients=[OWNER, ALLY])
    assert ALLY not in _by_to(srv1)
    # removed -> back
    srv2 = _smtp(monkeypatch)
    monkeypatch.delenv("UNSUBSCRIBED_EMAILS", raising=False)
    ea.send_email("s", "body", ARMED, recipients=[OWNER, ALLY])
    assert ALLY in _by_to(srv2)


# --------------------------------------------------------------------------- #
# CAN-SPAM apparatus (header + footer) — armed vs owner
# --------------------------------------------------------------------------- #

def test_armed_nonowner_gets_header_and_footer_owner_does_not(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    ea.send_email("s", "BODYTEXT", ARMED, recipients=[OWNER, ALLY])
    msgs = _by_to(srv)

    # non-owner: List-Unsubscribe header + footer present
    ally = msgs[ALLY]
    assert ally["List-Unsubscribe"] is not None
    assert ally["List-Unsubscribe"].startswith(f"<mailto:{MAILTO}")
    assert POSTAL in ally.get_content()
    assert "To unsubscribe" in ally.get_content()

    # owner: no header, no footer — the message body is unchanged
    owner = msgs[OWNER]
    assert owner["List-Unsubscribe"] is None
    assert POSTAL not in owner.get_content()
    assert owner.get_content().rstrip("\n") == "BODYTEXT"


# --------------------------------------------------------------------------- #
# Trap 4 — the self-arming fail-safe never empties the URGENT send / never
# corrupts recap state
# --------------------------------------------------------------------------- #

def test_unarmed_nonowner_held_owner_still_sent_returns_true(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    # not armed (no postal address): the non-owner is HELD rather than sent a
    # non-compliant message, but the owner is always sent -> True (never a
    # spurious False that would drop the URGENT recap).
    assert ea.send_email("s", "body", UNARMED, recipients=[OWNER, ALLY]) is True
    tos = set(_by_to(srv))
    assert OWNER in tos
    assert ALLY not in tos


def test_unarmed_all_nonowner_scoped_holds_everyone_returns_false(monkeypatch):
    # A scoped stream with no owner on it (e.g. a watch list of only third
    # parties), unarmed -> everyone held -> False. This is only reached by
    # streams whose return value is NOT state-load-bearing (the WATCH send is
    # fire-and-forget); URGENT always carries the owner, so it never lands here.
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    assert ea.send_email("s", "body", UNARMED, recipients=[ALLY, COMMISSIONER]) is False
    assert srv.messages == []


# --------------------------------------------------------------------------- #
# Trap 3 — empty owner list must not silently drop everyone; warn instead
# --------------------------------------------------------------------------- #

def test_empty_owner_list_with_suppression_warns_and_does_not_drop_nonsuppressed(
        monkeypatch, capsys):
    srv = _smtp(monkeypatch)
    # owner list empty, suppression present
    cfg = {"unsubscribe": {"mailto": MAILTO, "postal_address": "", "owner_addresses": []}}
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", "gone@example.org")
    ok = ea.send_email("s", "body", cfg, recipients=["gone@example.org", "stay@example.org"])
    out = capsys.readouterr().out
    assert "WARNING" in out and "owner" in out.lower()
    tos = set(_by_to(srv))
    # the suppressed address is still dropped...
    assert "gone@example.org" not in tos
    # ...but a non-suppressed recipient is NOT dropped (can't classify a third
    # party with no owner list, so never risk silencing anyone).
    assert "stay@example.org" in tos
    assert ok is True


# --------------------------------------------------------------------------- #
# Trap 1 — an emptied SCOPED list must send to NOBODY, never fall through to the
# default coalition audience (the private-list-leak guard)
# --------------------------------------------------------------------------- #

def test_scoped_list_fully_suppressed_sends_to_nobody_not_default(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", "scoped@example.org")
    cfg = dict(ARMED)
    cfg["alert_recipients"] = ["base@coalition.org"]   # the default list
    result = ea.send_email("s", "body", cfg, recipients=["scoped@example.org"])
    assert result is False
    assert srv.messages == []                          # nobody got it
    assert "base@coalition.org" not in _by_to(srv)     # did NOT fall through


# --------------------------------------------------------------------------- #
# Per-stream integration through the real entry points + real serialization
# --------------------------------------------------------------------------- #

def test_urgent_stream_suppression_apparatus_and_no_spurious_false(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    # ally suppressed; owner + a fresh commissioner remain
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", ALLY)
    monkeypatch.setenv("ALERT_RECIPIENTS_EXTRA", f"{ALLY}, {COMMISSIONER}")
    sent = ea.send_urgent_alert(_urgent_doc(), {"document_name": "Doc X"}, "http://x", ARMED)
    assert sent is True                                 # trap 4: owner present -> True
    msgs = _by_to(srv)
    assert ALLY not in msgs                             # suppressed
    assert OWNER in msgs and msgs[OWNER]["List-Unsubscribe"] is None  # owner clean
    assert msgs[COMMISSIONER]["List-Unsubscribe"] is not None         # third party gets apparatus
    # real serialization: the header + footer are actually on the wire
    raw = msgs[COMMISSIONER].as_string()
    assert "List-Unsubscribe:" in raw
    assert POSTAL in raw


def test_urgent_stream_unarmed_returns_true_holds_third_party(monkeypatch):
    # The critical trap-4 case at the real entry point: unarmed, owner on the
    # list -> the urgent doc is still delivered to the owner -> True, so
    # watcher._route_urgent_or_digest records the recap (no silent miss).
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    monkeypatch.setenv("ALERT_RECIPIENTS_EXTRA", COMMISSIONER)
    sent = ea.send_urgent_alert(_urgent_doc(), {"document_name": "Doc X"}, "http://x", UNARMED)
    assert sent is True
    tos = set(_by_to(srv))
    assert OWNER in tos and COMMISSIONER not in tos


def test_digest_stream_suppression_and_apparatus(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    # digest-only extra recipients; one of them opts out
    monkeypatch.setenv("DIGEST_RECIPIENTS_EXTRA", f"{COMMISSIONER}, dropme@example.org")
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", "dropme@example.org")
    ea.send_digest([], ARMED)
    msgs = _by_to(srv)
    assert "dropme@example.org" not in msgs             # suppressed
    assert msgs[OWNER]["List-Unsubscribe"] is None      # owner clean
    assert msgs[COMMISSIONER]["List-Unsubscribe"] is not None
    assert POSTAL in msgs[COMMISSIONER].get_content()


def test_watch_stream_suppression_and_apparatus(monkeypatch):
    # The GFL-air WATCH email is exactly send_email(subj, body, cfg,
    # recipients=watch_recipients). Same choke point, same guarantees.
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    monkeypatch.setenv("UNSUBSCRIBED_EMAILS", ALLY)
    watch_recipients = [OWNER, ALLY, COMMISSIONER]
    ea.send_email("[GFL air] perimeter", "readings", ARMED, recipients=watch_recipients)
    msgs = _by_to(srv)
    assert ALLY not in msgs
    assert msgs[OWNER]["List-Unsubscribe"] is None
    assert msgs[COMMISSIONER]["List-Unsubscribe"] is not None
    raw = msgs[COMMISSIONER].as_string()
    assert "List-Unsubscribe:" in raw and POSTAL in raw


# --------------------------------------------------------------------------- #
# Backward compatibility: an owner-only scoped send (e.g. the CivicClerk
# meeting-watch to Trisha) is unchanged — no footer, no header, even when armed.
# --------------------------------------------------------------------------- #

def test_owner_only_send_is_unchanged_even_when_armed(monkeypatch):
    srv = _smtp(monkeypatch)
    monkeypatch.setenv("MONITOR_OWNER_EMAILS", OWNER)
    assert ea.send_email("meeting", "BODY", ARMED, recipients=[OWNER]) is True
    msg = _by_to(srv)[OWNER]
    assert msg["List-Unsubscribe"] is None
    assert msg.get_content().rstrip("\n") == "BODY"


def test_no_unsubscribe_config_and_no_owner_env_preserves_legacy_behavior(monkeypatch):
    # cfg with no `unsubscribe` block and no owner env: behaves exactly as before
    # this feature — everyone is sent, no footer, no header (never armed, owners
    # empty so nothing is dropped).
    srv = _smtp(monkeypatch)
    assert ea.send_email("s", "BODY", {}, recipients=[OWNER, ALLY]) is True
    assert set(_by_to(srv)) == {OWNER, ALLY}
    for m in srv.messages:
        assert m["List-Unsubscribe"] is None
        assert m.get_content().rstrip("\n") == "BODY"
