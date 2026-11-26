"""digest_render: the Sunday digest's text and HTML bodies (layout B, 2026-11-26)."""
import smtplib
from datetime import date
from types import SimpleNamespace

import digest_render as dr
import email_alerts as ea


def _it(name, date_filed="2026-11-20", doc_type="evidence", severity="routine",
        document_date="", risks=("R8",), corr=False, link="https://drive.google.com/x",
        kdp="Key point.", sent_at=None):
    meta = {"date_filed": date_filed, "document_name": name, "document_date": document_date}
    if sent_at:
        meta["urgent_sent_at"] = sent_at
    parsed = SimpleNamespace(doc_type=doc_type, severity=severity, risks=list(risks),
                             key_data_point=kdp, summary=kdp, is_correspondence=corr)
    return {"parsed": parsed, "metadata": meta, "link": link}


def _week():
    items = [
        _it("Letter A", doc_type="procedural", severity="notable", corr=True, document_date="2026-11-19"),
        _it("Permit step", doc_type="procedural", date_filed="2026-10-28", document_date="2026-11-23"),
        _it("Notice old", doc_type="procedural", date_filed="2026-11-23", document_date="2026-08-04"),
        _it("RCA", severity="notable", document_date="2026-09-25"),
        _it("Handout", doc_type="opinion", date_filed="2026-09-09"),
    ]
    recap = [_it("Hot well", severity="urgent", document_date="2026-11-21",
                 sent_at="2026-11-24T14:05:00")]
    return items, recap


def test_sections_order_and_newest_first():
    items, recap = _week()
    keys = [(k, [it["metadata"]["document_name"] for it in its]) for k, _l, its in dr.sections(items, recap)]
    assert keys == [
        ("urgent", ["Hot well"]),
        ("correspondence", ["Letter A"]),
        ("deadlines", ["Permit step", "Notice old"]),   # by document date, not filed date
        ("notable", ["RCA"]),
        ("routine", ["Handout"]),
    ]


def test_count_line_matches_sections():
    items, recap = _week()
    assert dr.count_line(items, recap) == (
        "5 new documents · 1 urgent from the past two weeks · 1 correspondence · "
        "2 deadlines and notices · 1 notable")
    assert dr.count_line(items[1:2], None) == "1 new document · 1 deadlines and notices · 0 notable"


def test_no_risk_codes_in_either_part():
    items, recap = _week()
    text = dr.render_text(items, recap, send_date=date(2026, 11, 29))
    html = dr.render_html(items, recap, send_date=date(2026, 11, 29))
    for body in (text, html):
        assert "R8" not in body


def test_text_lists_dates_and_labels_filed_fallback():
    items, recap = _week()
    text = dr.render_text(items, recap, send_date=date(2026, 11, 29))
    assert text.startswith("Arbor Hills Monitor weekly digest, Sunday, November 29, 2026\n")
    assert "  - 2026-11-23  Permit step" in text
    assert "  - filed 2026-09-09  Handout" in text
    assert "urgent alert sent 2026-11-24" in text
    assert "—" not in text.replace(" · ", "")       # no em-dashes in our own text


def test_html_escapes_untrusted_text_and_blocks_non_http_links():
    bad = _it('<script>alert(1)</script> & "quote"', kdp="<img src=x onerror=alert(1)>",
              link="javascript:alert(1)")
    html = dr.render_html([bad])
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert "javascript:" not in html
    good = dr.render_html([_it("ok", link="https://drive.google.com/file/d/1/view")])
    assert 'href="https://drive.google.com/file/d/1/view"' in good


def test_html_has_footer_slot_logo_preheader_and_auto_summary_note():
    items, recap = _week()
    html = dr.render_html(items, recap, send_date=date(2026, 11, 29))
    assert html.count(dr.FOOTER_SLOT) == 1
    assert dr.LOGO_URL in html and 'alt=""' in html
    assert "Weekly digest &middot; Sunday, November 29, 2026" in html
    assert dr.AUTO_SUMMARY_NOTE in html


def test_size_guard_keeps_footer_and_urgent_under_gmail_clip():
    items = [_it(f"Routine doc {i:03d} " + "x" * 80, date_filed=f"2020-01-{1 + i % 28:02d}",
                 kdp="y" * 200) for i in range(200)]
    recap = [_it("Hot well", severity="urgent", sent_at="2026-11-24T14:05:00")]
    html = dr.render_html(items, recap)
    assert len(html.encode()) <= dr.HTML_BUDGET_BYTES
    assert dr.FOOTER_SLOT in html
    assert "Hot well" in html
    assert "more, listed in the plain-text version" in html
    assert "Other new documents: routine <span" in html and "(200)" in html
    text = dr.render_text(items, recap)
    assert all(f"Routine doc {i:03d}" in text for i in range(200))


def test_wds_and_old_records_render():
    wds = _it("[WDS] Evaluation record (updated)", date_filed="2019-11-07", document_date="")
    del wds["metadata"]["document_date"]                     # pre-field record shape
    text = dr.render_text([wds])
    html = dr.render_html([wds])
    assert "  - filed 2019-11-07  [WDS] Evaluation record (updated)" in text
    assert "[WDS] Evaluation record (updated)" in html


def test_announcement_box_links_urls():
    html = dr.render_html([_it("d")], announcement="New tool: https://policy.arborhillsmonitor.org/capacity/")
    assert 'href="https://policy.arborhillsmonitor.org/capacity/"' in html
    assert ">Announcement<" in html


# --- send_email multipart --------------------------------------------------

class _Srv:
    def __init__(self):
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        self.messages.append(msg)


def _smtp(monkeypatch):
    for k, v in {"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASSWORD": "p",
                 "SMTP_FROM": "monitor@trishakunst.com"}.items():
        monkeypatch.setenv(k, v)
    for k in ("UNSUBSCRIBED_EMAILS", "MONITOR_OWNER_EMAILS", "ALERT_RECIPIENTS_EXTRA"):
        monkeypatch.delenv(k, raising=False)
    srv = _Srv()
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout=30: srv)
    return srv


_CFG = {"unsubscribe": {"mailto": "unsub@x.org", "owner_addresses": ["owner@x.org"]}}


def test_send_email_without_html_stays_plain_text(monkeypatch):
    srv = _smtp(monkeypatch)
    ea.send_email("s", "BODY", _CFG, recipients=["owner@x.org", "ally@y.org"])
    for m in srv.messages:
        assert m.get_content_type() == "text/plain"
    ally = [m for m in srv.messages if m["To"] == "ally@y.org"][0]
    assert "(this address: ally@y.org)" in ally.get_content()   # other streams unchanged


def test_send_email_with_html_is_multipart_and_footers_non_owners_only(monkeypatch):
    srv = _smtp(monkeypatch)
    html = dr.render_html([_it("d")])
    ea.send_email("s", "BODY", _CFG, recipients=["owner@x.org", "ally@y.org"], html=html,
                  footer_show_address=False)
    by = {m["To"]: m for m in srv.messages}
    for m in by.values():
        assert m.get_content_type() == "multipart/alternative"
    ally_html = by["ally@y.org"].get_body(("html",)).get_content()
    owner_html = by["owner@x.org"].get_body(("html",)).get_content()
    assert "unsub@x.org" in ally_html and "To unsubscribe" in ally_html
    assert "To unsubscribe" not in owner_html
    assert dr.FOOTER_SLOT not in ally_html and dr.FOOTER_SLOT not in owner_html
    assert "(this address:" not in by["ally@y.org"].get_body(("plain",)).get_content()


def test_partial_hand_curated_dates_render_like_full_ones():
    month = _it("Guidance", document_date="2016-09")
    year = _it("Old memo", document_date="2016")
    html = dr.render_html([month, year])
    assert 'width:56px;">Sep<br>2016</td>' in html
    assert 'width:56px;">2016</td>' in html
