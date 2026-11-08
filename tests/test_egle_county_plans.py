"""EGLE county solid-waste plans page watch — ADR 074, coder:pta-expansion-watch.

PAGE_2026_11_07 is an excerpt of the live page as fetched 2026-11-07 (the
heading, the table head and four real rows, markup kept as served: the Luce,
Macomb, Marquette and Mason rows). Macomb reads "Macomb | Appendices" (no
amendment listed yet)."""
import egle_county_plans_watcher as ecp

_P = "/egle/-/media/Project/Websites/egle/Documents/Programs/MMD/Solid-Waste/Planning"

PAGE_2026_11_07 = f"""
<div class="component rich-text component__section" ><div class="component-content">
<p><strong>Currently Approved County Plans and Plan Amendments (subject to change)</strong></p>
<p>(* Items are amendments to the County Plans)</p>
<p>(** Items are "Entire Plans that have been Amended")</p>
<table class="data-table display">
    <thead>
        <tr>
            <th scope="col">County</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td style="text-align: left;">
            <strong><a href="{_P}/SWMP-A-K/Chippewa.pdf?rev=9594d92b&amp;hash=BB89">Luce</a> | <a href="{_P}/SWMP-A-K/Chippewa-2008-Amendment.pdf?rev=3b20&amp;hash=3D3B">2008</a>* (Chippewa-Luce-Mackinac)</strong>
            </td>
        </tr>
        <tr>
            <td style="text-align: left;">
            <strong><a href="{_P}/SWMP-L-Z/Macomb.pdf?rev=3b732ad92c0d46e895efc4ad7dbdbac0&amp;hash=D11882F8F38CAEA995DF541A09038E06">Macomb</a> | <a href="{_P}/SWMP-L-Z/Macomb-Appendices.pdf?rev=a325081e40c24fb59ee71ce76f9e8ec2&amp;hash=3E5EF477397BC3671AB8A6D0205B5687">Appendices</a></strong>
            </td>
        </tr>
        <tr>
            <td style="text-align: left;">
            <strong><a href="{_P}/SWMP-L-Z/Marquette.pdf?rev=d0f5&amp;hash=6E34">Marquette</a> | <a href="{_P}/SWMP-L-Z/Marquette-2007-Amendment.pdf?rev=76ff&amp;hash=2DDF">2007</a>*</strong>
            </td>
        </tr>
        <tr>
            <td style="text-align: left;"><strong><a href="{_P}/SWMP-L-Z/Mason.pdf?rev=4577&amp;hash=EB34">Mason</a> |<a href="{_P}/SWMP-L-Z/Mason-Appendices.pdf?rev=60fa&amp;hash=763">Appendices</a></strong></td>
        </tr>
    </tbody>
</table>
</div></div>
"""

MACOMB_NOW = {"text": "Macomb | Appendices", "files": "Macomb.pdf,Macomb-Appendices.pdf"}

# Synthetic: EGLE lists an approved 2026 amendment for Macomb.
PAGE_CHANGED = PAGE_2026_11_07.replace(
    'Appendices</a></strong>\n            </td>\n        </tr>\n        <tr>\n'
    '            <td style="text-align: left;">\n            <strong><a href="'
    f'{_P}/SWMP-L-Z/Marquette',
    f'Appendices</a> | <a href="{_P}/SWMP-L-Z/Macomb-2026-Amendment.pdf?rev=1">2026</a>*'
    '</strong>\n            </td>\n        </tr>\n        <tr>\n'
    '            <td style="text-align: left;">\n            <strong><a href="'
    f'{_P}/SWMP-L-Z/Marquette', 1)


# --- extraction ----------------------------------------------------------------

def test_extracts_macomb_row_from_todays_page():
    assert ecp.extract_county_row(PAGE_2026_11_07, "Macomb") == MACOMB_NOW


def test_extracts_starred_amendment_rows_and_tidies_spacing():
    assert ecp.extract_county_row(PAGE_2026_11_07, "Marquette")["text"] == "Marquette | 2007*"
    assert ecp.extract_county_row(PAGE_2026_11_07, "Mason")["text"] == "Mason | Appendices"
    luce = ecp.extract_county_row(PAGE_2026_11_07, "luce")
    assert luce["text"] == "Luce | 2008* (Chippewa-Luce-Mackinac)"
    assert luce["files"] == "Chippewa.pdf,Chippewa-2008-Amendment.pdf"


def test_county_name_with_plan_year_in_the_link_matches():
    page = (PAGE_2026_11_07.replace("</tbody>",
            f'<tr><td><strong><a href="{_P}/SWMP-L-Z/Washtenaw.pdf?rev=e">Washtenaw 2019**</a>'
            f'</strong><strong> | <a href="{_P}/SWMP-L-Z/Washtenaw-Appendices.pdf">Appendices</a>'
            '</strong></td></tr></tbody>'))
    assert ecp.extract_county_row(page, "Washtenaw")["text"] == "Washtenaw 2019** | Appendices"
    assert ecp.extract_county_row(page, "Wash") is None


def test_synthetic_changed_macomb_row():
    row = ecp.extract_county_row(PAGE_CHANGED, "Macomb")
    assert row["text"] == "Macomb | Appendices | 2026*"
    assert row["files"].endswith("Macomb-2026-Amendment.pdf")


def test_missing_heading_or_row_is_none():
    assert ecp.extract_county_row("<html>no table</html>", "Macomb") is None
    assert ecp.extract_county_row(PAGE_2026_11_07, "Washtenaw") is None


def test_rev_query_change_alone_is_not_a_change():
    reuploaded = PAGE_2026_11_07.replace("rev=3b732ad92c0d46e895efc4ad7dbdbac0", "rev=ffff")
    assert ecp.extract_county_row(reuploaded, "Macomb") == MACOMB_NOW


# --- decide() ----------------------------------------------------------------------

def _row(date, status, text="", files="", alerted="", county="Macomb"):
    return [date, county, status, text, files, alerted, "", date + "T06:00:00"]


BASE = [_row("2026-11-08", "baseline", MACOMB_NOW["text"], MACOMB_NOW["files"], "no")]


def test_first_read_baselines_silently():
    rows, alerts = ecp.decide([], "Macomb", MACOMB_NOW, "", "2026-11-08", "t")
    assert alerts == [] and rows[0][2] == "baseline"


def test_unchanged_writes_nothing():
    assert ecp.decide(BASE, "Macomb", MACOMB_NOW, "", "2026-11-09", "t") == ([], [])


def test_changed_row_alerts_quoting_old_and_new():
    new = ecp.extract_county_row(PAGE_CHANGED, "Macomb")
    rows, alerts = ecp.decide(BASE, "Macomb", new, "", "2026-11-09", "t")
    assert rows[0][2] == "changed" and rows[0][3] == "Macomb | Appendices | 2026*"
    (kind, subject, body, idx), = alerts
    assert kind == "changed" and idx == 0
    assert subject == "[EGLE county plans] Macomb row changed"
    assert "Before: Macomb | Appendices\n" in body
    assert "Now:    Macomb | Appendices | 2026*" in body


def test_other_county_rows_are_ignored():
    other = [_row("2026-11-08", "baseline", "Mason | Appendices", "", "no", county="Mason")]
    rows, alerts = ecp.decide(other, "Macomb", MACOMB_NOW, "", "2026-11-09", "t")
    assert rows[0][2] == "baseline" and alerts == []


def test_failed_check_is_never_a_change():
    rows, alerts = ecp.decide(BASE, "Macomb", None, "fetch failed: X", "2026-11-09", "t")
    assert rows[0][2] == "fetch_failed" and alerts == []


def test_stale_alert_once_after_three_days_of_failures():
    hist = BASE + [_row("2026-11-09", "fetch_failed"), _row("2026-11-10", "fetch_failed")]
    rows, alerts = ecp.decide(hist, "Macomb", None, "row not found", "2026-11-11", "t")
    assert [a[0] for a in alerts] == ["stale"] and rows[0][5] == "stale"
    hist += rows
    rows, alerts = ecp.decide(hist, "Macomb", None, "row not found", "2026-11-12", "t")
    assert alerts == [] and rows[0][5] == ""   # once per outage


def test_same_day_failures_count_as_one_day():
    hist = BASE + [_row("2026-11-09", "fetch_failed"), _row("2026-11-09", "fetch_failed")]
    _, alerts = ecp.decide(hist, "Macomb", None, "x", "2026-11-09", "t")
    assert alerts == []


def test_recovery_resets_the_streak_without_alerting():
    hist = BASE + [_row("2026-11-09", "fetch_failed")]
    rows, alerts = ecp.decide(hist, "Macomb", MACOMB_NOW, "", "2026-11-10", "t")
    assert rows[0][2] == "recovered" and alerts == []
    hist += rows + [_row("2026-11-11", "fetch_failed"), _row("2026-11-12", "fetch_failed")]
    _, alerts = ecp.decide(hist, "Macomb", None, "x", "2026-11-12", "t")
    assert alerts == []   # only 2 days since the recovery


# --- check() orchestration ------------------------------------------------------------

class FakeSheets:
    """Stands in for sheet_writer's three county-plans helpers."""

    def __init__(self, rows=None, fail_read=False):
        self.rows, self.fail_read = list(rows or []), fail_read


def _patch(monkeypatch, fake):
    import sheet_writer as sw
    monkeypatch.setattr(sw, "ensure_county_plans_tab", lambda s, i: None)

    def read(s, i):
        if fake.fail_read:
            raise RuntimeError("429")
        return list(fake.rows)
    monkeypatch.setattr(sw, "read_county_plans_rows", read)
    monkeypatch.setattr(sw, "append_county_plans_rows", lambda s, i, r: fake.rows.extend(r))


CFG = {"egle_county_plans": {"enabled": True, "counties": ["Macomb"],
                             "recipients": ["trisha@example.org"]}}


class Mailer:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, subject, body, cfg, recipients=None):
        if self.fail:
            raise RuntimeError("smtp")
        self.sent.append((subject, recipients))


def test_check_baseline_then_change_emails_then_records(monkeypatch):
    fake = FakeSheets()
    _patch(monkeypatch, fake)
    mail = Mailer()
    ecp.check(None, "sid", CFG, mail, fetch_page=lambda u: PAGE_2026_11_07)
    assert mail.sent == [] and fake.rows[-1][2] == "baseline"
    ecp.check(None, "sid", CFG, mail, fetch_page=lambda u: PAGE_CHANGED)
    assert mail.sent == [("[EGLE county plans] Macomb row changed", ["trisha@example.org"])]
    assert fake.rows[-1][2] == "changed"


def test_check_change_send_failure_writes_nothing_so_it_retries(monkeypatch):
    fake = FakeSheets(BASE)
    _patch(monkeypatch, fake)
    ecp.check(None, "sid", CFG, Mailer(fail=True), fetch_page=lambda u: PAGE_CHANGED)
    assert fake.rows == BASE
    mail = Mailer()
    ecp.check(None, "sid", CFG, mail, fetch_page=lambda u: PAGE_CHANGED)
    assert len(mail.sent) == 1


def test_check_fetch_error_records_failed_check(monkeypatch):
    fake = FakeSheets(BASE)
    _patch(monkeypatch, fake)

    def boom(u):
        raise RuntimeError("403")
    ecp.check(None, "sid", CFG, Mailer(), fetch_page=boom)
    assert fake.rows[-1][2] == "fetch_failed"
    assert fake.rows[-1][6] == "fetch failed: RuntimeError"


def test_check_state_read_error_raises(monkeypatch):
    import pytest
    fake = FakeSheets(fail_read=True)
    _patch(monkeypatch, fake)
    with pytest.raises(RuntimeError):
        ecp.check(None, "sid", CFG, Mailer(), fetch_page=lambda u: PAGE_2026_11_07)


def test_check_empty_recipients_never_emails(monkeypatch):
    fake = FakeSheets(BASE)
    _patch(monkeypatch, fake)
    cfg = {"egle_county_plans": {"counties": ["Macomb"], "recipients": []}}
    mail = Mailer()
    ecp.check(None, "sid", cfg, mail, fetch_page=lambda u: PAGE_CHANGED)
    assert mail.sent == [] and fake.rows[-1][5] == "no"


def test_live_config_ships_disabled_and_watches_macomb():
    from config_loader import load_config
    c = load_config()["egle_county_plans"]
    assert c["enabled"] is False
    assert "Macomb" in c["counties"]
    assert c["recipients"]


# --- review round 1 (PR #218) ------------------------------------------------------

def test_send_returning_false_writes_nothing_so_it_retries(monkeypatch):
    fake = FakeSheets(BASE)
    _patch(monkeypatch, fake)
    ecp.check(None, "sid", CFG, lambda s, b, c, recipients=None: False,
              fetch_page=lambda u: PAGE_CHANGED)
    assert fake.rows == BASE


def test_heading_repeated_above_the_table_still_finds_the_row():
    page = ('<a href="#plans">Currently Approved County Plans</a>'
            '<table><tr><td>Contents</td></tr></table>' + PAGE_2026_11_07)
    assert ecp.extract_county_row(page, "Macomb") == MACOMB_NOW


def test_one_county_failing_does_not_stop_the_next(monkeypatch):
    import sheet_writer as sw
    fake = FakeSheets()
    _patch(monkeypatch, fake)
    calls = []

    def append(s, i, rows):
        calls.append(rows[0][1])
        if rows[0][1] == "Macomb":
            raise RuntimeError("429")
        fake.rows.extend(rows)
    monkeypatch.setattr(sw, "append_county_plans_rows", append)
    cfg = {"egle_county_plans": {"counties": ["Macomb", "Mason"], "recipients": ["t@x"]}}
    ecp.check(None, "sid", cfg, Mailer(), fetch_page=lambda u: PAGE_2026_11_07)
    assert calls == ["Macomb", "Mason"] and fake.rows[-1][1] == "Mason"
