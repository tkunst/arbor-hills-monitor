"""WDS extra (related) sites — ADR 074, coder:pta-expansion-watch.

A plain before/now change watch. Fixtures are the Pine Tree Acres (WDS 398972)
Applications rows as read live on 2026-11-07 (10 records + the trailing blank
template row WDS always renders) and two of its annual report-years."""
import copy

import wds_watcher as ww

PTA = "398972"
AH = "475946"


def _app(typ, receipt, closure="Issued", closure_date=""):
    return {"Application Type": typ, "Receipt Date": receipt,
            "Closure Type": closure, "Closure Date": closure_date}


PTA_APPS_2026_11_07 = [
    _app("Construction Permit", "10/31/2019", "Issued", "3/18/2020"),
    _app("Construction Permit", "10/22/2014", "Issued", "5/8/2015"),
    _app("Construction Permit", "11/17/2006", "Issued", "3/16/2007"),
    _app("Construction Permit", "10/6/2006", "Terminated - Admin. Incomplete", "11/1/2006"),
    _app("Construction Permit", "10/11/2002", "Issued", "2/5/2003"),
    _app("Operating License", "9/15/2025", "Issued", "2/6/2026"),
    _app("Operating License", "6/24/2020", "Issued", "10/20/2020"),
    _app("Operating License", "8/23/2018", "Issued", "12/13/2018"),
    _app("Operating License", "8/25/2015", "Issued", "12/17/2015"),
    _app("Operating License", "11/5/2012", "Issued", "2/28/2013"),
    _app("", "", "", ""),  # WDS's trailing add-a-record template row
]

PTA_ANNUAL_2026_11_07 = [
    {"Year": "2025", "Total Permitted Capacity": "89694901",
     "Capacity Used This Year": "3555538", "Yrs Remaining Start": "7.9",
     "Yrs Remaining End": "6.8", "Waste_Total": "9,317,302.65",
     "Waste_Tons": "3,296,296.29"},
    {"Year": "2024", "Yrs Remaining End": "7.9", "Waste_Total": "1"},
]

NEW_CP = _app("Construction Permit", "12/1/2026", "", "")

AH_APPS = [_app("Operating License", "1/1/2020", "Issued", "2/1/2020")]


def _cfg(recipients=("trisha@example.org",), collections=("applications", "annual"),
         extra=True):
    wds = {"site_id": AH, "collections": ["applications"]}
    if extra:
        wds["extra_sites_enabled"] = True
        wds["extra_sites"] = [{
            "site_id": PTA, "name": "Pine Tree Acres (WM, Macomb)",
            "collections": list(collections), "recipients": list(recipients)}]
    return {"wds": wds}


class Mailer:
    def __init__(self, result=True, fail=False):
        self.sent, self.result, self.fail = [], result, fail

    def __call__(self, subject, body, cfg, recipients=None):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append({"subject": subject, "body": body, "recipients": recipients})
        return self.result


def _fetchers(pta_apps, pta_annual=None, ah_apps=None):
    """Site-aware fake fetchers: the same callable serves both sites, keyed on w."""
    data = {"applications": {PTA: pta_apps, AH: ah_apps or AH_APPS},
            "annual": {PTA: PTA_ANNUAL_2026_11_07 if pta_annual is None else pta_annual,
                       AH: []}}
    return {name: (lambda w, n=name: copy.deepcopy(data[n][w])) for name in data}


def _baselined_state(cfg=None):
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, cfg or _cfg(), Mailer(), fetchers=_fetchers(PTA_APPS_2026_11_07))
    return state


def _run(state, apps, annual=None, cfg=None, mail=None):
    mail = mail or Mailer()
    ww.check_wds(state, cfg or _cfg(), mail, fetchers=_fetchers(apps, annual))
    return mail


# --- state ---------------------------------------------------------------------

def test_extra_site_state_keys_never_collide_with_arbor_hills():
    seen = _baselined_state()["wds_seen"]
    assert set(seen) == {"applications", f"{PTA}:applications", f"{PTA}:annual"}
    assert seen["applications"]["last_count"] == 1
    assert seen[f"{PTA}:applications"]["last_count"] == 10   # template row dropped
    assert seen[f"{PTA}:annual"]["last_count"] == 2


def test_stored_value_is_readable_text():
    recs = _baselined_state()["wds_seen"][f"{PTA}:applications"]["records"]
    assert recs["Construction Permit|10/31/2019"] == "Closure Type: Issued; Closure Date: 3/18/2020"


def test_first_run_baselines_silently_and_nothing_reaches_the_digest():
    state = {"wds_seen": {}, "pending_digest": []}
    mail = _run(state, PTA_APPS_2026_11_07)
    assert mail.sent == [] and state["pending_digest"] == []


def test_unchanged_second_run_sends_nothing():
    state = _baselined_state()
    assert _run(state, PTA_APPS_2026_11_07).sent == []


# --- before / now -------------------------------------------------------------------

def test_new_application_reports_not_listed_to_now():
    state = _baselined_state()
    mail = _run(state, [NEW_CP] + PTA_APPS_2026_11_07)
    (m,) = mail.sent
    assert m["subject"] == "Pine Tree Acres (WM, Macomb) WDS: 1 record change(s)"
    assert m["recipients"] == ["trisha@example.org"]
    assert ("Construction Permit (received 12/1/2026)\n"
            "  Before: (not listed)\n"
            "  Now:    Closure Type: (blank); Closure Date: (blank)\n") in m["body"]
    assert "SolidWaste/Default.aspx?w=398972" in m["body"]
    assert state["pending_digest"] == []


def test_status_change_reports_before_and_now():
    state = {"wds_seen": {}, "pending_digest": []}
    _run(state, PTA_APPS_2026_11_07 + [NEW_CP])
    issued = PTA_APPS_2026_11_07 + [_app("Construction Permit", "12/1/2026", "Issued", "5/1/2027")]
    (m,) = _run(state, issued).sent
    assert "  Before: Closure Type: (blank); Closure Date: (blank)\n" in m["body"]
    assert "  Now:    Closure Type: Issued; Closure Date: 5/1/2027\n" in m["body"]
    assert "URGENT" not in m["subject"]


def test_any_field_edit_is_reported_without_judgement():
    state = _baselined_state()
    rows = [dict(r) for r in PTA_APPS_2026_11_07]
    rows[0]["Closure Type"] = "Issued - Modified"
    rows[1]["Closure Date"] = "5/9/2015"
    m = _run(state, rows).sent[0]
    assert m["subject"].endswith("2 record change(s)")
    assert "Now:    Closure Type: Issued - Modified; Closure Date: 3/18/2020" in m["body"]
    assert "Now:    Closure Type: Issued; Closure Date: 5/9/2015" in m["body"]


def test_corrected_receipt_date_shows_as_new_then_gone():
    state = _baselined_state()
    rows = [dict(r) for r in PTA_APPS_2026_11_07]
    rows[0]["Receipt Date"] = "11/1/2019"   # forward correction re-keys the 2019 permit
    (m,) = _run(state, rows).sent
    assert m["subject"].endswith("1 record change(s)")
    assert ("Construction Permit (received 11/1/2019)\n  Before: (not listed)\n"
            "  Now:    Closure Type: Issued; Closure Date: 3/18/2020\n") in m["body"]
    # The old key is reported gone only after a second run without it.
    (m,) = _run(state, rows).sent
    assert ("Construction Permit (received 10/31/2019)\n"
            "  Before: Closure Type: Issued; Closure Date: 3/18/2020\n"
            "  Now:    (no longer listed)\n") in m["body"]
    # Dropped once reported, so it is reported once.
    assert "Construction Permit|10/31/2019" not in state["wds_seen"][f"{PTA}:applications"]["records"]
    assert _run(state, rows).sent == []


def test_new_annual_year_and_annual_revision_are_reported():
    state = _baselined_state()
    annual = [{"Year": "2026", "Yrs Remaining End": "5.9"}] + copy.deepcopy(PTA_ANNUAL_2026_11_07)
    annual[1]["Yrs Remaining End"] = "6.7"
    (m,) = _run(state, PTA_APPS_2026_11_07, annual).sent
    assert "Annual Landfill Report — FY2026\n  Before: (not listed)" in m["body"]
    assert "Before: Total Permitted Capacity: 89694901; Yrs Remaining End: 6.8" in m["body"]
    assert "Now:    Total Permitted Capacity: 89694901; Yrs Remaining End: 6.7" in m["body"]


def test_many_changes_are_one_email_never_a_silent_rebaseline():
    state = _baselined_state()
    rekeyed = [dict(r, **{"Receipt Date": "0" + r["Receipt Date"]}) if r["Receipt Date"] else r
               for r in PTA_APPS_2026_11_07]
    (m,) = _run(state, rekeyed).sent
    assert m["subject"].endswith("10 record change(s)")   # the 10 old keys go next run
    (m,) = _run(state, rekeyed).sent
    assert m["subject"].endswith("10 record change(s)")


# --- guards ---------------------------------------------------------------------------

def test_bad_fetch_reports_nothing_gone():
    state = _baselined_state()
    assert _run(state, PTA_APPS_2026_11_07[:2]).sent == []   # Rule A: collapse skipped
    assert state["wds_seen"][f"{PTA}:applications"]["last_count"] == 10


def test_one_short_page_is_not_reported_as_deleted_then_re_added():
    """A partial fetch above Rule A's floor (7 of 10) must not send "no longer
    listed", and the next good fetch must not send "(not listed) -> ..."."""
    state = _baselined_state()
    assert _run(state, PTA_APPS_2026_11_07[:7]).sent == []
    assert _run(state, PTA_APPS_2026_11_07).sent == []
    assert "missing" not in state["wds_seen"][f"{PTA}:applications"]


def test_duplicate_keys_in_one_fetch_do_not_flap():
    state = _baselined_state()
    dup = _app("Construction Permit", "10/31/2019", "Withdrawn", "1/1/2020")
    rows = PTA_APPS_2026_11_07 + [dup]
    (m,) = _run(state, rows).sent
    assert m["subject"].endswith("1 record change(s)")
    assert "Construction Permit (received 10/31/2019 #2)" in m["body"]
    assert _run(state, rows).sent == []
    assert _run(state, rows).sent == []


def test_send_failure_or_false_reverts_and_resends():
    for bad in (Mailer(fail=True), Mailer(result=False)):
        state = _baselined_state()
        before = copy.deepcopy(state["wds_seen"])
        rows = [dict(r) for r in PTA_APPS_2026_11_07]
        rows[0]["Receipt Date"] = "11/1/2019"
        _run(state, [NEW_CP] + rows, mail=bad)
        assert state["wds_seen"] == before          # whole collection restored
        (m,) = _run(state, [NEW_CP] + rows).sent
        assert m["subject"].endswith("2 record change(s)")


def test_unsent_gone_report_is_resent():
    state = _baselined_state()
    rows = PTA_APPS_2026_11_07[1:]
    assert _run(state, rows).sent == []              # first miss: nothing yet
    _run(state, rows, mail=Mailer(fail=True))        # second miss: report fails
    (m,) = _run(state, rows).sent                    # re-sent next run
    assert "Now:    (no longer listed)" in m["body"]


def test_empty_recipients_logs_only_never_the_shared_list():
    cfg = _cfg(recipients=())
    state = _baselined_state(cfg)
    assert _run(state, [NEW_CP] + PTA_APPS_2026_11_07, cfg=cfg).sent == []


def test_extra_site_fetch_failure_does_not_touch_arbor_hills():
    state = _baselined_state()
    before = copy.deepcopy(state["wds_seen"])
    fetch = _fetchers(PTA_APPS_2026_11_07)
    orig = fetch["applications"]

    def apps(w):
        if w == PTA:
            raise RuntimeError("down")
        return orig(w)
    fetch["applications"] = apps
    mail = Mailer()
    ww.check_wds(state, _cfg(), mail, fetchers=fetch)
    assert mail.sent == [] and state["wds_seen"] == before


def test_a_raising_collection_keeps_its_state_and_the_others_still_report(monkeypatch):
    state = _baselined_state()
    before_annual = copy.deepcopy(state["wds_seen"][f"{PTA}:annual"])
    orig = ww.wc.page_url

    def page_url(name, w):
        if name == "annual":
            raise ValueError("boom")
        return orig(name, w)
    monkeypatch.setattr(ww.wc, "page_url", page_url)
    annual = [{"Year": "2026"}] + PTA_ANNUAL_2026_11_07
    (m,) = _run(state, [NEW_CP] + PTA_APPS_2026_11_07, annual).sent
    assert m["subject"].endswith("1 record change(s)")
    assert state["wds_seen"][f"{PTA}:annual"] == before_annual


def test_primary_site_listed_as_extra_is_ignored():
    cfg = _cfg()
    cfg["wds"]["extra_sites"][0]["site_id"] = AH
    state = {"wds_seen": {}, "pending_digest": []}
    _run(state, PTA_APPS_2026_11_07, cfg=cfg)
    assert set(state["wds_seen"]) == {"applications"}


def test_unwatchable_collection_is_skipped():
    cfg = _cfg(collections=("qmr", "applications"))
    state = {"wds_seen": {}, "pending_digest": []}
    _run(state, PTA_APPS_2026_11_07, cfg=cfg)
    assert f"{PTA}:qmr" not in state["wds_seen"] and f"{PTA}:applications" in state["wds_seen"]


def test_extra_sites_paused_by_flag():
    cfg = _cfg()
    cfg["wds"]["extra_sites_enabled"] = False
    state = {"wds_seen": {}, "pending_digest": []}
    _run(state, PTA_APPS_2026_11_07, cfg=cfg)
    assert set(state["wds_seen"]) == {"applications"}


# --- Arbor Hills unchanged -------------------------------------------------------

def test_arbor_hills_alerts_identical_with_and_without_extra_sites():
    ah_cp = _app("Construction Permit", "6/1/2026", "", "")
    outs = []
    for extra in (False, True):
        cfg = _cfg(extra=extra)
        state = {"wds_seen": {}, "pending_digest": []}
        ww.check_wds(state, cfg, Mailer(), fetchers=_fetchers(PTA_APPS_2026_11_07))
        mail = Mailer()
        ww.check_wds(state, cfg, mail,
                     fetchers=_fetchers(PTA_APPS_2026_11_07, ah_apps=AH_APPS + [ah_cp]))
        outs.append((state["wds_seen"]["applications"], mail.sent, state["pending_digest"]))
    assert outs[0] == outs[1]
    assert outs[0][1][0]["subject"] == "[URGENT] Arbor Hills WDS: Construction Permit (received 6/1/2026)"
    assert outs[0][1][0]["recipients"] is None  # the default (whole-list) audience, as before


def test_live_config_watches_pta_and_keeps_arbor_hills_primary():
    from config_loader import load_config
    wds = load_config()["wds"]
    assert str(wds["site_id"]) == AH
    assert wds["extra_sites_enabled"] is True
    extra = {str(s["site_id"]): s for s in wds.get("extra_sites") or []}
    assert set(extra[PTA]["collections"]) == {"applications", "annual"}
    assert extra[PTA]["recipients"], "scoped recipients must be set (empty = log only)"
    for c in extra[PTA]["collections"]:
        assert c in ww.RELATED_FIELDS
