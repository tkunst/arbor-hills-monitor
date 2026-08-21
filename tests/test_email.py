"""Urgency logic — especially the permitted-vs-measured temperature distinction,
which is the credibility-critical case."""
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
    assert "URGENT ITEMS FROM EARLIER THIS WEEK (already emailed separately):" in body
    assert "Sent 2026-08-15T09:00:00  Urgent Doc" in body


def test_format_digest_body_recap_section_renders_before_procedural_and_other():
    body = ea.format_digest_body([_item()], [_recap_item()])
    recap_idx = body.index("URGENT ITEMS FROM EARLIER THIS WEEK")
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
