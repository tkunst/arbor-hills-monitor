"""Urgency logic — especially the permitted-vs-measured temperature distinction,
which is the credibility-critical case."""
import pytest

import email_alerts as ea
from egle_doc_parser import ParsedDoc

CFG = {"urgent": {"severity_is_urgent": True, "measured_temp_urgent_f": 145}}


def _doc(severity="routine", measurements=None, full_text=""):
    return ParsedDoc(
        summary="s", key_data_point="k", doc_type="evidence", risks=["R8"],
        severity=severity, full_text=full_text, ocr_applied=False, page_count=1,
        measurements=measurements or [],
    )


def test_severity_urgent_fires():
    assert ea.is_urgent(_doc(severity="urgent"), CFG) is True


def test_measured_temp_at_or_above_threshold_fires():
    m = [{"metric": "temperature", "value": 150, "unit": "F", "basis": "measured",
          "well_id": "AHW272"}]
    assert ea.is_urgent(_doc(measurements=m), CFG) is True


def test_permitted_ceiling_does_not_fire():
    # The credibility test: a 180F PERMITTED ceiling must NOT trigger urgent --
    # even though the document text literally says "180 F" (the regex fallback
    # must NOT run once any structured temperature was extracted).
    m = [{"metric": "temperature", "value": 180, "unit": "F",
          "basis": "permitted_limit", "well_id": "AHW263"}]
    doc = _doc(
        severity="notable",
        measurements=m,
        full_text="HOV waiver requested: ceiling of 180 F for well AHW263.",
    )
    assert ea.is_urgent(doc, CFG) is False


def test_measured_below_threshold_does_not_fire():
    m = [{"metric": "temperature", "value": 140, "unit": "F", "basis": "measured"}]
    assert ea.is_urgent(_doc(measurements=m), CFG) is False


def test_max_measured_temp_excludes_permitted():
    m = [
        {"metric": "temperature", "value": 180, "unit": "F", "basis": "permitted_limit"},
        {"metric": "temperature", "value": 138, "unit": "F", "basis": "measured"},
    ]
    assert ea.max_measured_temp_f(_doc(measurements=m)) == 138


def test_celsius_measured_is_converted():
    m = [{"metric": "temperature", "value": 70, "unit": "C", "basis": "measured"}]  # 158F
    assert ea.max_measured_temp_f(_doc(measurements=m)) == 158
    assert ea.is_urgent(_doc(measurements=m), CFG) is True


def test_free_text_fallback_when_no_structured_measurements():
    # No structured measurements -> fall back to scanning text.
    assert ea.is_urgent(_doc(full_text="probe read 165 F at the wellhead"), CFG) is True


# --- recipient resolution: config.yml + private ALERT_RECIPIENTS_EXTRA env ---

def test_resolve_recipients_config_only(monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS_EXTRA", raising=False)
    assert ea.resolve_recipients({"alert_recipients": ["a@x.com"]}) == ["a@x.com"]


def test_resolve_recipients_merges_env_and_dedups(monkeypatch):
    # The env carries PRIVATE addresses kept out of the public repo's config.yml.
    monkeypatch.setenv("ALERT_RECIPIENTS_EXTRA", "a@x.com, b@yahoo.com ; c@x.com")
    assert ea.resolve_recipients({"alert_recipients": ["a@x.com"]}) == [
        "a@x.com", "b@yahoo.com", "c@x.com",
    ]


def test_resolve_recipients_blank_env_is_noop(monkeypatch):
    monkeypatch.setenv("ALERT_RECIPIENTS_EXTRA", "  ")
    assert ea.resolve_recipients({"alert_recipients": ["a@x.com"]}) == ["a@x.com"]


# --- merge_extra_recipients: the generalized helper (any list, any env var) ---

def test_merge_extra_recipients_is_generic_to_env_var_name(monkeypatch):
    # Same private-supplement pattern as ALERT_RECIPIENTS_EXTRA, but parameterized
    # so a second recipient list (e.g. gfl_air's watch_alert_recipients) can use
    # its own env var without a bespoke parser.
    monkeypatch.delenv("SOME_OTHER_EXTRA", raising=False)
    assert ea.merge_extra_recipients(["a@x.com"], "SOME_OTHER_EXTRA") == ["a@x.com"]
    monkeypatch.setenv("SOME_OTHER_EXTRA", "b@x.com, a@x.com")
    assert ea.merge_extra_recipients(["a@x.com"], "SOME_OTHER_EXTRA") == ["a@x.com", "b@x.com"]


# --- send_digest: DIGEST_RECIPIENTS_EXTRA is digest-only, never urgent -------

_DIGEST_CFG = {"alert_recipients": ["base@x.com"]}


def test_send_digest_with_no_extra_uses_resolve_recipients(monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS_EXTRA", raising=False)
    monkeypatch.delenv("DIGEST_RECIPIENTS_EXTRA", raising=False)
    sent = []
    monkeypatch.setattr(ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append(recipients))
    ea.send_digest([], _DIGEST_CFG)
    assert sent == [["base@x.com"]]


def test_send_digest_adds_digest_recipients_extra_on_top_of_the_full_list(monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS_EXTRA", raising=False)
    monkeypatch.setenv("DIGEST_RECIPIENTS_EXTRA", "commissioner@washtenaw.org, base@x.com")
    sent = []
    monkeypatch.setattr(ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append(recipients))
    ea.send_digest([], _DIGEST_CFG)
    assert sent == [["base@x.com", "commissioner@washtenaw.org"]]


# --- format_digest_body: urgent recap section (added 2026-08-21) ------------


def _item(document_name="Doc A", date_filed="2026-08-10"):
    return {
        "parsed": _doc(severity="notable"),
        "metadata": {"date_filed": date_filed, "document_name": document_name},
        "link": "http://x/doc",
    }


def _recap_item(document_name="Urgent Doc", urgent_sent_at="2026-08-15T09:00:00"):
    return {
        "parsed": _doc(severity="urgent"),
        "metadata": {
            "date_filed": "2026-08-15",
            "document_name": document_name,
            "urgent_sent_at": urgent_sent_at,
        },
        "link": "http://x/urgent-doc",
    }


def test_format_digest_body_with_no_recap_is_byte_identical_to_before():
    # Regression guard: every existing format_digest_body caller passes no
    # second argument, so output must be unchanged when urgent_recap is
    # omitted/None/empty.
    items = [_item()]
    baseline = ea.format_digest_body(items)
    assert ea.format_digest_body(items, None) == baseline
    assert ea.format_digest_body(items, []) == baseline
    assert "Arbor Hills (N2688) digest — 1 new document(s)." in baseline
    assert "URGENT ITEMS" not in baseline


def test_format_digest_body_empty_with_no_recap_is_unchanged():
    assert ea.format_digest_body([]) == "No new Arbor Hills (N2688) documents this period."
    assert ea.format_digest_body([], None) == "No new Arbor Hills (N2688) documents this period."


def test_format_digest_body_recap_only_still_renders_not_empty_message():
    body = ea.format_digest_body([], [_recap_item()])
    assert body != "No new Arbor Hills (N2688) documents this period."
    assert "URGENT ITEMS FROM EARLIER:" in body
    assert "already emailed separately" not in body  # digest-only members never got the urgent email
    assert "Sent 2026-08-15T09:00:00  Urgent Doc" in body


def test_format_digest_body_recap_section_renders_before_procedural_and_other():
    body = ea.format_digest_body([_item()], [_recap_item()])
    recap_idx = body.index("URGENT ITEMS FROM EARLIER")
    digest_idx = body.index("Arbor Hills (N2688) digest —")
    assert recap_idx < digest_idx


def test_format_digest_body_recap_line_shows_sent_at_per_item():
    body = ea.format_digest_body([], [
        _recap_item(document_name="First", urgent_sent_at="2026-08-12T10:00:00"),
        _recap_item(document_name="Second", urgent_sent_at="2026-08-14T22:30:00"),
    ])
    assert "Sent 2026-08-12T10:00:00  First" in body
    assert "Sent 2026-08-14T22:30:00  Second" in body


def test_send_digest_passes_urgent_recap_through_and_resolves_recipients(monkeypatch):
    monkeypatch.delenv("ALERT_RECIPIENTS_EXTRA", raising=False)
    monkeypatch.delenv("DIGEST_RECIPIENTS_EXTRA", raising=False)
    seen = {}
    monkeypatch.setattr(
        ea, "format_digest_body",
        lambda items, urgent_recap=None: seen.update(items=items, urgent_recap=urgent_recap) or "body",
    )
    sent = []
    monkeypatch.setattr(ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))
    recap = [_recap_item()]
    ea.send_digest([], _DIGEST_CFG, urgent_recap=recap)
    assert seen == {"items": [], "urgent_recap": recap}
    assert sent == [("Arbor Hills N2688 digest — 0 new document(s)", "body", ["base@x.com"])]


# --- ADR 065: correspondence & enforcement digest section ------------------

# Real text snippets captured from the live specimens during this build's
# spike (not committed PDFs — just the opening lines) so is_correspondence_letter
# is pinned against actual EGLE letter text, not an invented approximation.
_EXTENSION_APPROVAL_TEXT = """
GRETCHEN WHITMER
GOVERNOR
STATE OF MICHIGAN
DEPARTMENT OF
ENVIRONMENT, GREAT LAKES, AND ENERGY
AIR QUALITY DIVISION
DEBORAH A. STABENOW BUILDING • 525 WEST ALLEGAN STREET • P.O. BOX 30260 • LANSING, MICHIGAN 48909-7760
Michigan.gov/EGLE • 800-662-9278
August 27, 2026
Dear David Seegert:
SUBJECT: Green for Life Environmental - Arbor Hills Landfill, Inc. Request for
Extension - Corrective Actions for Perimeter Monitor Action Level Exceedances
The AQD approves of the specified corrective action items proposed for Cells 6A and
6B. We approve the requested 120-day extension until December 19, 2026.
"""

_HOV_RENEWAL_TEXT = """
STATE OF MICHIGAN
DEPARTMENT OF
ENVIRONMENT, GREAT LAKES, AND ENERGY
Michigan.gov/EGLE • 800-662-9278
April 7, 2026
SUBJECT: Renewal of Higher Operating Value Temperature Waivers
"""

# GFL's OWN cover letter to EGLE (not an EGLE letter) -- mentions the
# agency's full name in its own address block, but never the letterhead URL.
_GFL_COVER_LETTER_TEXT = """
July 30, 2026
Scott Miller, District Supervisor
Michigan Department of Environment, Great Lakes, and Energy
Air Quality Division
Re: 2026 Second Quarter Report - Consent Judgment No. 2020-0593-CE
"""

_DMR_STUB_TEXT = """
DISCHARGE MONITORING REPORT (DMR) - DAILY
Facility Name: Arbor Hills Remediation Area
Permit Number: MI0045713 v5.0
DMR Period: 3/1/2022 - 3/31/2022
"""


def test_is_correspondence_letter_true_for_extension_approval():
    assert ea.is_correspondence_letter(_EXTENSION_APPROVAL_TEXT) is True


def test_is_correspondence_letter_true_for_hov_renewal():
    assert ea.is_correspondence_letter(_HOV_RENEWAL_TEXT) is True


def test_is_correspondence_letter_false_for_gfl_cover_letter():
    # Mentions the agency's full name, but never EGLE's own letterhead
    # footer -- a letter ADDRESSED to EGLE is not a letter FROM EGLE.
    assert ea.is_correspondence_letter(_GFL_COVER_LETTER_TEXT) is False


def test_is_correspondence_letter_false_for_dmr_stub():
    assert ea.is_correspondence_letter(_DMR_STUB_TEXT) is False


def test_is_correspondence_letter_false_for_empty_text():
    assert ea.is_correspondence_letter("") is False
    assert ea.is_correspondence_letter(None) is False


def _correspondence_item(document_name="EGLE letter: HOV renewal"):
    doc = _doc(severity="notable")
    doc.is_correspondence = True
    return {
        "parsed": doc,
        "metadata": {"date_filed": "2026-04-07", "document_name": document_name},
        "link": "http://x/hov-renewal",
    }


def test_format_digest_body_correspondence_pinned_section_renders():
    body = ea.format_digest_body([_correspondence_item(), _item()])
    assert "CORRESPONDENCE & ENFORCEMENT" in body
    assert "EGLE letter: HOV renewal" in body


def test_format_digest_body_correspondence_item_not_duplicated_in_other_section():
    body = ea.format_digest_body([_correspondence_item()])
    # Only the pinned section's line, not also under "OTHER NEW DOCUMENTS".
    assert body.count("EGLE letter: HOV renewal") == 1
    assert "OTHER NEW DOCUMENTS" not in body


def test_format_digest_body_no_correspondence_items_omits_section():
    body = ea.format_digest_body([_item()])
    assert "CORRESPONDENCE & ENFORCEMENT" not in body


def test_format_digest_body_correspondence_renders_after_urgent_recap():
    body = ea.format_digest_body([_correspondence_item()], [_recap_item()])
    recap_idx = body.index("URGENT ITEMS FROM EARLIER")
    corr_idx = body.index("CORRESPONDENCE & ENFORCEMENT")
    digest_idx = body.index("Arbor Hills (N2688) digest —")
    assert recap_idx < corr_idx < digest_idx


def test_format_digest_body_missing_is_correspondence_attr_is_false(monkeypatch):
    # _record_to_item on an OLD _meta.pending_digest record (written before
    # ADR 065) has no is_correspondence key -- getattr's default must keep
    # that item in the ordinary flow, not raise.
    body = ea.format_digest_body([_item()])
    assert "CORRESPONDENCE & ENFORCEMENT" not in body


def test_send_urgent_alert_never_sees_digest_recipients_extra(monkeypatch):
    # The whole point: adding someone via DIGEST_RECIPIENTS_EXTRA must NOT put
    # them on the same-day [URGENT] send.
    monkeypatch.delenv("ALERT_RECIPIENTS_EXTRA", raising=False)
    monkeypatch.setenv("DIGEST_RECIPIENTS_EXTRA", "commissioner@washtenaw.org")
    sent = []
    monkeypatch.setattr(ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append(recipients))
    ea.send_urgent_alert(_doc(severity="urgent"), {"document_name": "x"}, "http://x", _DIGEST_CFG)
    assert sent == [None]  # send_urgent_alert passes no explicit recipients -> resolve_recipients only


# --- send_email / send_urgent_alert: bool return (added 2026-08-21) ---------
# So _route_urgent_or_digest can tell "silently skipped" (SMTP unconfigured /
# no recipients) apart from "actually sent" and never durably record a recap
# for an alert that never went out.


def test_send_email_returns_false_when_smtp_unconfigured(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    assert ea.send_email("subj", "body", {}, recipients=["a@x.com"]) is False


def test_send_email_returns_false_when_no_recipients(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    assert ea.send_email("subj", "body", {"alert_recipients": []}, recipients=[]) is False


def test_send_email_returns_true_after_a_real_send(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")

    class _FakeServer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            pass

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout=30: _FakeServer())
    assert ea.send_email("subj", "body", {}, recipients=["a@x.com"]) is True


def test_send_urgent_alert_propagates_send_email_bool(monkeypatch):
    monkeypatch.setattr(ea, "send_email", lambda subj, body, c, recipients=None: False)
    assert ea.send_urgent_alert(_doc(severity="urgent"), {"document_name": "x"}, "http://x", _DIGEST_CFG) is False

    monkeypatch.setattr(ea, "send_email", lambda subj, body, c, recipients=None: True)
    assert ea.send_urgent_alert(_doc(severity="urgent"), {"document_name": "x"}, "http://x", _DIGEST_CFG) is True


# --- route_hand_curated_urgent_or_digest (added 2026-09-20) -----------------
# Hand-Curated Files additions (dedupe-curate intake) never run through
# egle_doc_parser.parse_document(), so they had NO path into pending_digest or
# pending_urgent_recap at all before this — a severe hand-curated finding could
# only reach the Sunday digest via manual Sheet surgery. This gives them the
# same routing the live nSITE watcher's _route_urgent_or_digest uses.


def _hc_kwargs(**overrides):
    kwargs = dict(
        document_name="Test Doc", date_filed="2026-09-20", doc_type="evidence",
        severity="notable", risks=["R5"], key_data_point="k", link="http://x",
        cfg=_DIGEST_CFG, state={}, sent_at="2026-09-20T00:00:00",
    )
    kwargs.update(overrides)
    return kwargs


def test_route_hand_curated_not_urgent_queues_to_pending_digest(monkeypatch):
    sent = []
    monkeypatch.setattr(ea, "send_email", lambda *a, **k: sent.append(1) or True)
    state = {}
    result = ea.route_hand_curated_urgent_or_digest(**_hc_kwargs(state=state))
    assert result is False
    assert sent == []  # no email attempted at all for a non-urgent doc
    assert len(state["pending_digest"]) == 1
    assert state["pending_digest"][0]["document_name"] == "Test Doc"
    assert "urgent_sent_at" not in state["pending_digest"][0]
    assert state["pending_urgent_recap"] == []


def test_route_hand_curated_urgent_sends_and_recaps(monkeypatch):
    sent = []
    monkeypatch.setattr(ea, "send_email", lambda subj, body, c, recipients=None: sent.append(subj) or True)
    state = {}
    result = ea.route_hand_curated_urgent_or_digest(**_hc_kwargs(severity="urgent", state=state))
    assert result is True
    assert len(sent) == 1 and sent[0].startswith("[URGENT]")
    assert state["pending_digest"] == []
    assert len(state["pending_urgent_recap"]) == 1
    recap = state["pending_urgent_recap"][0]
    assert recap["urgent_sent_at"] == "2026-09-20T00:00:00"
    assert recap["severity"] == "urgent"


def test_route_hand_curated_urgent_failed_send_queues_nowhere(monkeypatch):
    # SMTP unconfigured / no recipients -> send_urgent_alert returns False.
    # Matches _route_urgent_or_digest: an alert that never went out has
    # nothing to recap, and it must NOT silently fall back into the digest
    # either (that would misrepresent a routine item as one that "almost" fired).
    monkeypatch.setattr(ea, "send_email", lambda *a, **k: False)
    state = {}
    result = ea.route_hand_curated_urgent_or_digest(**_hc_kwargs(severity="urgent", state=state))
    assert result is False
    assert state["pending_digest"] == []
    assert state["pending_urgent_recap"] == []


def test_route_hand_curated_urgent_smtp_exception_does_not_crash(monkeypatch):
    # Regression (2026-09-20): send_email's own docstring says "A mid-send SMTP
    # failure still raises (unchanged)" -- watcher.py's _route_urgent_or_digest
    # already wraps its send_urgent_alert() call for exactly this reason. This
    # function must do the same (a misconfigured workflow env caused a REAL
    # smtplib.SMTPSenderRefused to propagate uncaught and crash the caller
    # before it ever reached the second document in the batch).
    def _boom(*a, **k):
        raise __import__("smtplib").SMTPSenderRefused(501, b"Error: Bad sender address syntax", "x")
    monkeypatch.setattr(ea, "send_email", _boom)
    state = {}
    result = ea.route_hand_curated_urgent_or_digest(**_hc_kwargs(severity="urgent", state=state))
    assert result is False
    assert state["pending_digest"] == []
    assert state["pending_urgent_recap"] == []


def test_route_hand_curated_preserves_existing_state_entries(monkeypatch):
    monkeypatch.setattr(ea, "send_email", lambda *a, **k: True)
    state = {"pending_digest": [{"document_name": "Earlier Doc"}], "pending_urgent_recap": []}
    ea.route_hand_curated_urgent_or_digest(**_hc_kwargs(state=state))
    assert [r["document_name"] for r in state["pending_digest"]] == ["Earlier Doc", "Test Doc"]


# --- send_email error redaction (security review L1, 2026-10-06) --------------
# A send failure must never put a recipient address or server response text into
# the (world-readable) Actions log via a caller's `print(f"... {e}")`.

def _smtp_env(monkeypatch):
    for k in ("UNSUBSCRIBED_EMAILS", "MONITOR_OWNER_EMAILS", "MONITOR_OWNER_DOMAINS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")


class _RefusingServer:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        import smtplib
        raise smtplib.SMTPRecipientsRefused(
            {"private.owner@proton.me": (550, b"5.1.1 <private.owner@proton.me> no such user")})


def test_send_failure_is_redacted_and_never_names_an_address(monkeypatch, capsys):
    import smtplib
    _smtp_env(monkeypatch)
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout=30: _RefusingServer())
    with pytest.raises(ea.EmailSendError) as ei:
        ea.send_email("subj", "body", {}, recipients=["private.owner@proton.me"])
    e = ei.value
    assert "private.owner" not in str(e) and "proton" not in str(e)
    assert "SMTPRecipientsRefused" in str(e) and "1 recipient(s) refused" in str(e)
    assert e.__cause__ is None and e.__context__ is None             # original not kept at all
    print(f"caller logs: {e}")                                         # what streams do
    assert "proton" not in capsys.readouterr().out


def test_redacted_error_is_still_an_smtp_exception(monkeypatch):
    import smtplib
    _smtp_env(monkeypatch)

    class _AuthFails(_RefusingServer):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 bad creds for user@x.com")
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout=30: _AuthFails())
    with pytest.raises(smtplib.SMTPException) as ei:                   # old catches still work
        ea.send_email("subj", "body", {}, recipients=["a@x.com"])
    assert "SMTP 535" in str(ei.value) and "user@x.com" not in str(ei.value)


def test_network_failure_is_redacted(monkeypatch):
    import smtplib
    _smtp_env(monkeypatch)

    def _down(host, port, timeout=30):
        raise OSError("connect to smtp.example.com:587 for owner@gmail.com failed")
    monkeypatch.setattr(smtplib, "SMTP", _down)
    with pytest.raises(ea.EmailSendError) as ei:
        ea.send_email("subj", "body", {}, recipients=["a@x.com"])
    assert str(ei.value) == "send failed after 0 sent: OSError"


def test_redact_smtp_error_is_pure():
    import smtplib
    assert ea.redact_smtp_error(smtplib.SMTPSenderRefused(501, b"bad <x@y.z>", "x@y.z")) \
        == "SMTPSenderRefused / SMTP 501"


def test_partial_send_reports_count_and_stays_redacted(monkeypatch):
    import smtplib
    _smtp_env(monkeypatch)
    calls = []

    class _SecondFails(_RefusingServer):
        def send_message(self, msg):
            calls.append(msg["To"])
            if len(calls) == 2:
                raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout=30: _SecondFails())
    with pytest.raises(OSError) as ei:                                 # still an OSError too
        ea.send_email("subj", "body", {"unsubscribe": {"owner_addresses": ["a@x.com", "b@y.com"]}},
                      recipients=["a@x.com", "b@y.com"])
    assert "after 1 sent" in str(ei.value) and "b@y.com" not in str(ei.value)


def test_non_smtp_errors_pass_through_unwrapped(monkeypatch):
    import smtplib
    _smtp_env(monkeypatch)

    class _Ok(_RefusingServer):
        def send_message(self, msg):
            pass
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout=30: _Ok())
    with pytest.raises(ValueError):                                    # header injection guard
        ea.send_email("subj", "body", {}, recipients=["a@x.com\r\nBcc: z@z.com"])


def test_redact_refused_recipients_counts_only():
    import smtplib
    e = smtplib.SMTPRecipientsRefused({"p@q.com": (550, b"x"), "r@s.com": (550, b"y")})
    assert ea.redact_smtp_error(e) == "SMTPRecipientsRefused / 2 recipient(s) refused"
