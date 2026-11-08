"""WDS extra (related) sites — ADR 074, coder:pta-expansion-watch.

Fixtures are the Pine Tree Acres (WDS 398972) Applications rows as read live on
2026-11-07 (10 records + the trailing blank template row WDS always renders),
plus a synthetic new Construction Permit application and a synthetic
pending -> Issued transition."""
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
            "alert_label": "Pine Tree Acres expansion",
            "collections": list(collections), "recipients": list(recipients)}]
    return {"wds": wds}


class Mailer:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, subject, body, cfg, recipients=None):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append({"subject": subject, "body": body, "recipients": recipients})


def _fetchers(pta_apps, pta_annual=None, ah_apps=None):
    """Site-aware fake fetchers: the same callable serves both sites, keyed on w."""
    data = {"applications": {PTA: pta_apps, AH: ah_apps or AH_APPS},
            "annual": {PTA: pta_annual or PTA_ANNUAL_2026_11_07, AH: []}}
    return {name: (lambda w, n=name: copy.deepcopy(data[n][w])) for name in data}


def _baselined_state(cfg):
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, cfg, Mailer(), fetchers=_fetchers(PTA_APPS_2026_11_07))
    return state


# --- state keys ------------------------------------------------------------

def test_extra_site_state_keys_never_collide_with_arbor_hills():
    state = _baselined_state(_cfg())
    seen = state["wds_seen"]
    assert set(seen) == {"applications", f"{PTA}:applications", f"{PTA}:annual"}
    # Arbor Hills' entry holds only Arbor Hills' record.
    assert seen["applications"]["last_count"] == 1
    # PTA: the 10 real rows, the blank template row dropped.
    assert seen[f"{PTA}:applications"]["last_count"] == 10
    assert seen[f"{PTA}:annual"]["last_count"] == 2


def test_first_run_baselines_silently():
    mail = Mailer()
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, _cfg(), mail, fetchers=_fetchers(PTA_APPS_2026_11_07))
    assert mail.sent == []
    assert state["pending_digest"] == []


def test_unchanged_second_run_sends_nothing():
    cfg = _cfg()
    state = _baselined_state(cfg)
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(PTA_APPS_2026_11_07))
    assert mail.sent == []


# --- the two URGENT rules ----------------------------------------------------

def test_new_construction_permit_application_is_urgent_with_fixed_subject():
    cfg = _cfg()
    state = _baselined_state(cfg)
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers([NEW_CP] + PTA_APPS_2026_11_07))
    assert len(mail.sent) == 1
    m = mail.sent[0]
    assert m["subject"] == ("[URGENT] Pine Tree Acres expansion: construction permit "
                            "application received")
    assert m["recipients"] == ["trisha@example.org"]
    assert "Pine Tree Acres (WM, Macomb), WDS site 398972" in m["body"]
    assert "Applications/Default.aspx?w=398972" in m["body"] or "w=398972" in m["body"]
    # Never queued for the coalition Sunday digest.
    assert state["pending_digest"] == []


def test_construction_permit_closure_to_issued_is_urgent():
    cfg = _cfg()
    pending = PTA_APPS_2026_11_07 + [NEW_CP]
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, cfg, Mailer(), fetchers=_fetchers(pending))  # baseline incl. pending CP
    issued = PTA_APPS_2026_11_07 + [_app("Construction Permit", "12/1/2026", "Issued", "5/1/2027")]
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(issued))
    assert [m["subject"] for m in mail.sent] == [
        "[URGENT] Pine Tree Acres expansion: construction permit issued"]


def test_other_construction_permit_change_is_watch_not_urgent():
    cfg = _cfg()
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, cfg, Mailer(), fetchers=_fetchers(PTA_APPS_2026_11_07 + [NEW_CP]))
    terminated = PTA_APPS_2026_11_07 + [
        _app("Construction Permit", "12/1/2026", "Terminated - Admin. Incomplete", "1/5/2027")]
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(terminated))
    assert len(mail.sent) == 1
    assert mail.sent[0]["subject"] == "[WDS watch] Pine Tree Acres (WM, Macomb): 1 change(s)"


def test_operating_license_rows_are_watch_only():
    sev, _, risks = ww._classify_related_application(
        _app("Operating License", "9/1/2027", "", ""), False)
    assert (sev, risks) == ("watch", [])
    sev, _, _ = ww._classify_related_application(
        _app("Operating License", "9/1/2027", "Issued", "1/1/2028"), True)
    assert sev == "watch"


def test_new_annual_report_year_is_watch_summary():
    cfg = _cfg()
    state = _baselined_state(cfg)
    annual = [{"Year": "2026", "Yrs Remaining End": "5.9"}] + PTA_ANNUAL_2026_11_07
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(PTA_APPS_2026_11_07, annual))
    assert len(mail.sent) == 1
    assert "[WDS watch]" in mail.sent[0]["subject"]
    assert "Annual Landfill Report — FY2026" in mail.sent[0]["body"]


def test_related_events_carry_no_arbor_hills_risk_codes():
    for name, fn in ww.RELATED_CLASSIFIERS.items():
        for changed in (False, True):
            _, dtype, risks = fn(NEW_CP if name == "applications" else {"Year": "2026"}, changed)
            assert risks == [] and dtype == "related"


# --- delivery safety -----------------------------------------------------------

def test_urgent_send_failure_reverts_and_realerts_next_run():
    cfg = _cfg()
    state = _baselined_state(cfg)
    rows = [NEW_CP] + PTA_APPS_2026_11_07
    ww.check_wds(state, cfg, Mailer(fail=True), fetchers=_fetchers(rows))
    spec = ww.COLLECTIONS["applications"]
    assert ww._idkey(spec, NEW_CP) not in state["wds_seen"][f"{PTA}:applications"]["records"]
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(rows))
    assert len(mail.sent) == 1 and mail.sent[0]["subject"].startswith("[URGENT]")


def test_watch_summary_send_failure_reverts_and_resends():
    cfg = _cfg()
    state = _baselined_state(cfg)
    annual = [{"Year": "2026"}] + PTA_ANNUAL_2026_11_07
    ww.check_wds(state, cfg, Mailer(fail=True), fetchers=_fetchers(PTA_APPS_2026_11_07, annual))
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(PTA_APPS_2026_11_07, annual))
    assert len(mail.sent) == 1


def test_empty_recipients_logs_only_never_the_shared_list():
    cfg = _cfg(recipients=())
    state = _baselined_state(cfg)
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers([NEW_CP] + PTA_APPS_2026_11_07))
    assert mail.sent == []


def test_extra_site_fetch_failure_does_not_touch_arbor_hills():
    cfg = _cfg()
    state = _baselined_state(cfg)
    before = copy.deepcopy(state["wds_seen"])

    def boom(w):
        raise RuntimeError("down")

    fetch = _fetchers(PTA_APPS_2026_11_07)
    orig = fetch["applications"]
    fetch["applications"] = lambda w: boom(w) if w == PTA else orig(w)
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=fetch)
    assert mail.sent == []
    assert state["wds_seen"] == before


def test_primary_site_listed_as_extra_is_ignored():
    cfg = _cfg()
    cfg["wds"]["extra_sites"][0]["site_id"] = AH
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, cfg, Mailer(), fetchers=_fetchers(PTA_APPS_2026_11_07))
    assert set(state["wds_seen"]) == {"applications"}


def test_unsupported_collection_is_skipped():
    cfg = _cfg(collections=("qmr", "applications"))
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, cfg, Mailer(), fetchers=_fetchers(PTA_APPS_2026_11_07))
    assert f"{PTA}:qmr" not in state["wds_seen"]
    assert f"{PTA}:applications" in state["wds_seen"]


# --- Arbor Hills unchanged ---------------------------------------------------

def test_arbor_hills_alerts_identical_with_and_without_extra_sites():
    """Replay: the same Arbor Hills state + a new Arbor Hills Construction Permit
    produce the same seen-state, the same email and the same digest whether or
    not extra_sites is configured (PTA quiet)."""
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
    assert PTA in extra
    assert set(extra[PTA]["collections"]) == {"applications", "annual"}
    assert extra[PTA]["recipients"], "scoped recipients must be set (empty = log only)"
    for c in extra[PTA]["collections"]:
        assert c in ww.RELATED_CLASSIFIERS


# --- review round 1 (PR #218) ------------------------------------------------

class FalseMailer(Mailer):
    """send_email returns False (no exception) when SMTP isn't configured."""

    def __call__(self, subject, body, cfg, recipients=None):
        self.sent.append({"subject": subject})
        return False


def test_send_returning_false_is_not_delivered_and_realerts():
    cfg = _cfg()
    state = _baselined_state(cfg)
    rows = [NEW_CP] + PTA_APPS_2026_11_07
    ww.check_wds(state, cfg, FalseMailer(), fetchers=_fetchers(rows))
    spec = ww.COLLECTIONS["applications"]
    assert ww._idkey(spec, NEW_CP) not in state["wds_seen"][f"{PTA}:applications"]["records"]
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(rows))
    assert [m["subject"][:8] for m in mail.sent] == ["[URGENT]"]


def test_closure_date_correction_on_issued_permit_is_no_event():
    cfg = _cfg()
    state = _baselined_state(cfg)
    corrected = [dict(r) for r in PTA_APPS_2026_11_07]
    corrected[0]["Closure Date"] = "3/19/2020"   # was 3/18/2020, still Issued
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(corrected))
    assert mail.sent == []


def test_new_row_first_seen_already_issued_says_issued():
    cfg = _cfg()
    state = _baselined_state(cfg)
    issued_cp = _app("Construction Permit", "12/1/2026", "Issued", "5/1/2027")
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers([issued_cp] + PTA_APPS_2026_11_07))
    assert [m["subject"] for m in mail.sent] == [
        "[URGENT] Pine Tree Acres expansion: construction permit issued"]


def test_rekeyed_rows_send_one_bundled_email_not_a_flood():
    """A WDS date-format change re-keys every row: over the per-site threshold
    -> ONE bundled email, not five urgent ones."""
    cfg = _cfg()
    state = _baselined_state(cfg)
    rekeyed = [dict(r, **{"Receipt Date": "0" + r["Receipt Date"]}) if r["Receipt Date"] else r
               for r in PTA_APPS_2026_11_07]
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(rekeyed))
    assert [m["subject"] for m in mail.sent] == [
        "[URGENT] Pine Tree Acres expansion: 10 WDS changes at once, 5 urgent"]
    assert "how WDS prints its records" in mail.sent[0]["body"]


def test_milestone_on_a_busy_day_is_delivered_not_rebaselined():
    """A new CP application the same run as 3 other edits (over the threshold)
    still reaches Trisha, bundled and marked URGENT."""
    cfg = _cfg()
    state = _baselined_state(cfg)
    rows = [dict(r) for r in PTA_APPS_2026_11_07]
    for r in rows[5:8]:
        r["Closure Type"] = "Issued (amended)"   # 3 Operating License edits
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers([NEW_CP] + rows))
    assert len(mail.sent) == 1
    assert mail.sent[0]["subject"].startswith("[URGENT] Pine Tree Acres expansion: 4 WDS changes")
    assert "Construction Permit (received 12/1/2026) (NEW)" in mail.sent[0]["body"]


def test_bundle_send_failure_reverts_all():
    cfg = _cfg()
    state = _baselined_state(cfg)
    rows = [dict(r) for r in PTA_APPS_2026_11_07]
    for r in rows[5:8]:
        r["Closure Type"] = "Issued (amended)"
    ww.check_wds(state, cfg, FalseMailer(), fetchers=_fetchers([NEW_CP] + rows))
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers([NEW_CP] + rows))
    assert len(mail.sent) == 1 and "4 WDS changes" in mail.sent[0]["subject"]


def test_issued_variants_are_matched_case_insensitively():
    sev, _, _ = ww._classify_related_application(
        _app("Construction Permit", "12/1/2026", "Issued - Modified", "1/1/2027"), True)
    assert sev == "urgent"
    ev = {"name": "applications", "kind": "new", "label": "Construction Permit (received x)",
          "detail": "Closure: ISSUED 1/1/2027"}
    assert ww.related_urgent_subject(ev, "PTA").endswith("construction permit issued")


def test_extra_sites_paused_by_flag():
    cfg = _cfg()
    cfg["wds"]["extra_sites_enabled"] = False
    state = {"wds_seen": {}, "pending_digest": []}
    ww.check_wds(state, cfg, Mailer(), fetchers=_fetchers(PTA_APPS_2026_11_07))
    assert set(state["wds_seen"]) == {"applications"}


def test_a_raising_collection_keeps_its_state_and_the_others_still_alert(monkeypatch):
    cfg = _cfg()
    state = _baselined_state(cfg)
    before_annual = copy.deepcopy(state["wds_seen"][f"{PTA}:annual"])
    orig = ww.wc.page_url

    def page_url(name, w):
        if name == "annual":
            raise ValueError("boom")
        return orig(name, w)
    monkeypatch.setattr(ww.wc, "page_url", page_url)
    terminated = PTA_APPS_2026_11_07[:4] + [
        _app("Construction Permit", "10/11/2002", "Withdrawn", "2/5/2003")] + PTA_APPS_2026_11_07[5:]
    annual = [{"Year": "2026"}] + PTA_ANNUAL_2026_11_07
    mail = Mailer()
    ww.check_wds(state, cfg, mail, fetchers=_fetchers(terminated, annual))
    assert len(mail.sent) == 1 and "1 change(s)" in mail.sent[0]["subject"]
    assert state["wds_seen"][f"{PTA}:annual"] == before_annual
