"""nsite_client.fetch_site_submissions + nsite_submissions_watcher.py — the
activation gate, pure snapshot/diff helpers, the fetch-error contract (this is
the one that matters: unlike fetch_site_documents, a failure must never look
like "zero submissions"), and the full baseline/unchanged/new/changed/fetch-
fail flows driven through a fake Sheets service (no network, no creds). Reuses
FakeSheets from test_pfas_watcher, same idiom as test_rop.py."""
import copy

import pytest

import nsite_client as nc
import nsite_submissions_watcher as nw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

# ==============================================================================
# fetch_site_submissions — the fetch-error contract
# ==============================================================================


class _Resp:
    def __init__(self, body=None, status=200, json_ok=True):
        self._body = body if body is not None else {"queryResults": []}
        self._status = status
        self._json_ok = json_ok

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        if not self._json_ok:
            raise ValueError("not JSON")
        return self._body


class _Session:
    """Returns a scripted response per call. A list value scripts a retry
    sequence (one item consumed per call, same idiom as test_nsite_download)."""

    def __init__(self, script):
        self.script = list(script) if isinstance(script, list) else script
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if isinstance(self.script, list):
            return self.script[min(self.calls - 1, len(self.script) - 1)]
        return self.script


_RAW_SUBMISSION = {
    "submRefProgramAreaDescr": "WRD - Resources",
    "submRefFormTypeDescr": "Application",
    "submFormName": "Digital EGLE/USACE Joint Permit Application (JPA)",
    "submSubmRefNum": "HQK-4R25-67T36",
    "submStatus": "In Process",
    "submRcvdDate": "2026-07-23T00:00:00.0000000-04:00",
    "submDescr": None,
}


def test_fetch_normalizes_a_real_shaped_record():
    session = _Session(_Resp({"queryResults": [_RAW_SUBMISSION]}))
    rows = nc.fetch_site_submissions(session, "306291952280313698")
    assert rows == [{
        "ref_num": "HQK-4R25-67T36",
        "form_name": "Digital EGLE/USACE Joint Permit Application (JPA)",
        "form_type": "Application",
        "program_area": "WRD - Resources",
        "status": "In Process",
        "received_date": "2026-07-23",
        "descr": "",
    }]


def test_fetch_empty_queryresults_is_a_valid_zero_result():
    """A structurally-sound response listing zero submissions is NOT an
    error — this is the case fetch_site_submissions must tell apart from a
    genuine failure (see the next tests)."""
    session = _Session(_Resp({"queryResults": []}))
    assert nc.fetch_site_submissions(session, "X") == []


def test_fetch_raises_never_returns_empty_on_http_error():
    """THE contract this whole feature depends on: an HTTP failure must raise,
    never silently degrade to []. If this ever regressed to fetch_site_documents'
    swallow-and-return-[] behavior, the watcher would misread a fetch outage as
    'every submission removed' and fire a false change alert."""
    session = _Session(_Resp(status=500))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_submissions(session, "X")


def test_fetch_raises_on_missing_queryresults_key():
    """A response that parses as JSON but doesn't even have the expected key
    (a structural/shape break) must also raise, not be treated as []."""
    session = _Session(_Resp({"somethingElse": []}))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_submissions(session, "X")


def test_fetch_raises_on_non_json_body():
    session = _Session(_Resp(json_ok=False))
    with pytest.raises(nc.NsiteFetchError):
        nc.fetch_site_submissions(session, "X")


def test_fetch_retries_then_succeeds():
    session = _Session([_Resp(status=500), _Resp({"queryResults": [_RAW_SUBMISSION]})])
    rows = nc.fetch_site_submissions(session, "X")
    assert len(rows) == 1
    assert session.calls == 2


def test_fetch_skips_rows_missing_a_ref_num():
    bad = dict(_RAW_SUBMISSION)
    bad["submSubmRefNum"] = ""
    session = _Session(_Resp({"queryResults": [bad, _RAW_SUBMISSION]}))
    rows = nc.fetch_site_submissions(session, "X")
    assert len(rows) == 1
    assert rows[0]["ref_num"] == "HQK-4R25-67T36"


# ==============================================================================
# Pure snapshot / diff helpers
# ==============================================================================


def _row(ref, status="In Process", form="Some Form", **kw):
    base = {
        "ref_num": ref, "form_name": form, "form_type": "Application",
        "program_area": "WRD - Resources", "status": status,
        "received_date": "2026-07-23", "descr": "",
    }
    base.update(kw)
    return base


def test_snapshot_hash_stable_across_row_order():
    a = nw.submissions_snapshot([_row("B"), _row("A")])
    b = nw.submissions_snapshot([_row("A"), _row("B")])
    assert nw.snapshot_hash(a) == nw.snapshot_hash(b)


def test_snapshot_hash_changes_when_a_field_changes():
    a = nw.submissions_snapshot([_row("A", status="In Process")])
    b = nw.submissions_snapshot([_row("A", status="Completed")])
    assert nw.snapshot_hash(a) != nw.snapshot_hash(b)


def test_summarize_new_ref_reads_as_new_submission_not_a_change():
    old = nw.submissions_snapshot([])
    new = nw.submissions_snapshot([_row("HQK-4R25-67T36", form="JPA thing")])
    note, body = nw.summarize_submissions_change(old, new)
    assert "new submission" in note
    assert "NEW SUBMISSION" in body
    assert "HQK-4R25-67T36" in body
    assert "JPA thing" in body


def test_summarize_status_change_on_existing_ref_reads_as_changed_not_new():
    """THE distinction advisor flagged as worth having: a brand-new filing
    must read differently from an existing filing's status advancing."""
    old = nw.submissions_snapshot([_row("A", status="In Process")])
    new = nw.submissions_snapshot([_row("A", status="Completed")])
    note, body = nw.summarize_submissions_change(old, new)
    assert "existing submission changed" in note
    assert "new submission" not in note
    assert "CHANGED" in body
    assert "status: In Process -> Completed" in body


def test_summarize_removed_ref_is_reported_too():
    old = nw.submissions_snapshot([_row("A")])
    new = nw.submissions_snapshot([])
    note, body = nw.summarize_submissions_change(old, new)
    assert "no longer listed" in note
    assert "REMOVED" in body


def test_summarize_mixed_new_and_changed_reports_both():
    old = nw.submissions_snapshot([_row("A", status="In Process")])
    new = nw.submissions_snapshot([_row("A", status="Completed"), _row("B")])
    note, _ = nw.summarize_submissions_change(old, new)
    assert "new submission" in note
    assert "existing submission changed" in note


def test_format_change_body_has_essentials():
    body = nw.format_change_body("nSITE Submissions — Arbor Hills Landfill (N2688)",
                                  "new submission received", "+ NEW SUBMISSION  X — Y")
    assert "Arbor Hills Landfill (N2688)" in body
    assert "new submission received" in body
    assert "NEW SUBMISSION" in body


def test_should_run_false_when_disabled():
    ok, reason = nw._should_run({"nsite_submissions": {"enabled": False}})
    assert ok is False and "disabled" not in reason and "false" in reason.lower()


def test_should_run_false_when_key_absent():
    ok, _ = nw._should_run({})
    assert ok is False


def test_should_run_true_when_enabled():
    ok, reason = nw._should_run({"nsite_submissions": {"enabled": True}})
    assert ok is True and reason == ""


# ==============================================================================
# Full run() flows through a fake Sheets service
# ==============================================================================

FACILITIES = [
    {"srn": "N2688", "name": "Arbor Hills Landfill", "id": "8094300008956198244"},
    {"srn": "WRD", "name": "GFL-Arbor Hills Landfill-Washtenaw Co", "id": "306291952280313698"},
]

SUB_CFG = {
    "nsite_submissions": {"enabled": True},
    "facilities": FACILITIES,
    "alert_recipients": ["a@example.com"],
}


def _wire(monkeypatch, cfg, fetch_by_srn):
    """fetch_by_srn: dict of srn -> (list[dict] | Exception) OR a callable
    srn -> list[dict], for tests that need per-call variation."""
    fake = FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(nw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(nw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(nw.nc, "make_session", lambda: object())
    monkeypatch.setattr(nw.ea, "send_email",
                        lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)))

    def _fetch(session, nsite_id):
        srn = next(f["srn"] for f in FACILITIES if f["id"] == nsite_id)
        result = fetch_by_srn(srn) if callable(fetch_by_srn) else fetch_by_srn[srn]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(nw.nc, "fetch_site_submissions", _fetch)
    return fake, sent


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_SUBMISSIONS, [])[1:]  # drop header


def test_disabled_run_is_noop_touches_nothing(monkeypatch):
    monkeypatch.setattr(nw, "load_config", lambda: {"nsite_submissions": {"enabled": False}})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    monkeypatch.setattr(nw.dc, "sheets_service", boom)
    assert nw.run() == 0


def test_first_run_baselines_every_facility_silently(monkeypatch):
    fake, sent = _wire(monkeypatch, SUB_CFG, {
        "N2688": [_row("A")], "WRD": [_row("HQK-4R25-67T36", form="the JPA")],
    })
    assert nw.run() == 0
    rows = _rows(fake)
    assert len(rows) == 2
    assert all(r[3] == "baseline" for r in rows)
    assert {r[1] for r in rows} == {"subm:N2688", "subm:WRD"}
    assert sent == []   # the JPA itself baselines silently — it does NOT re-alert


def test_second_run_unchanged_is_noop(monkeypatch):
    fake, sent = _wire(monkeypatch, SUB_CFG, {"N2688": [_row("A")], "WRD": []})
    nw.run()
    assert nw.run() == 0
    assert len(_rows(fake)) == 2
    assert sent == []


def test_new_submission_emails_new_submission_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, SUB_CFG, {"N2688": [_row("A")], "WRD": []})
    nw.run()   # baseline
    fake2, sent2 = fake, sent
    monkeypatch.setattr(nw.nc, "fetch_site_submissions",
                        lambda session, nsite_id: (
                            [_row("A"), _row("B", form="A New Filing")]
                            if nsite_id == "8094300008956198244" else []))
    assert nw.run() == 0
    matches = [s for s in sent2 if "N2688" in s[0]]
    assert len(matches) == 1
    assert "NEW SUBMISSION" in matches[0][1]
    assert "A New Filing" in matches[0][1]
    assert matches[0][2] is None   # None -> send_email resolves the full alert_recipients list


def test_status_change_on_existing_ref_emails_changed_alert(monkeypatch):
    fake, sent = _wire(monkeypatch, SUB_CFG, {"N2688": [_row("A", status="In Process")], "WRD": []})
    nw.run()   # baseline
    monkeypatch.setattr(nw.nc, "fetch_site_submissions",
                        lambda session, nsite_id: (
                            [_row("A", status="Completed")]
                            if nsite_id == "8094300008956198244" else []))
    assert nw.run() == 0
    matches = [s for s in sent if "N2688" in s[0]]
    assert len(matches) == 1
    assert "CHANGED" in matches[0][1]
    assert "NEW SUBMISSION" not in matches[0][1]


def test_fetch_failure_after_baseline_is_skip_and_warn(monkeypatch):
    fake, sent = _wire(monkeypatch, SUB_CFG, {"N2688": [_row("A")], "WRD": []})
    nw.run()   # baseline
    monkeypatch.setattr(nw.nc, "fetch_site_submissions",
                        lambda session, nsite_id: (
                            (_ for _ in ()).throw(nc.NsiteFetchError("blip"))
                            if nsite_id == "8094300008956198244" else []))
    assert nw.run() == 0   # not loud
    assert len(_rows(fake)) == 2   # unchanged, nothing new appended
    assert sent == []


def test_fetch_failure_without_baseline_exits_loud_but_other_facility_still_baselines(monkeypatch):
    fake, sent = _wire(monkeypatch, SUB_CFG, {
        "N2688": nc.NsiteFetchError("bot wall on the runner"), "WRD": [_row("X")],
    })
    assert nw.run() == 1
    rows = _rows(fake)
    assert {r[1] for r in rows} == {"subm:WRD"}   # partial activation, not all-or-nothing
    assert sent == []


def test_recipients_override_narrows_audience(monkeypatch):
    cfg = copy.deepcopy(SUB_CFG)
    cfg["nsite_submissions"]["recipients"] = ["trisha@example.com"]
    fake, sent = _wire(monkeypatch, cfg, {"N2688": [_row("A")], "WRD": []})
    nw.run()   # baseline
    monkeypatch.setattr(nw.nc, "fetch_site_submissions",
                        lambda session, nsite_id: (
                            [_row("A"), _row("B")]
                            if nsite_id == "8094300008956198244" else []))
    assert nw.run() == 0
    matches = [s for s in sent if "N2688" in s[0]]
    assert len(matches) == 1
    assert matches[0][2] == ["trisha@example.com"]


def test_alert_email_failure_does_not_lose_the_durable_row(monkeypatch):
    fake, sent = _wire(monkeypatch, SUB_CFG, {"N2688": [_row("A")], "WRD": []})
    nw.run()   # baseline
    monkeypatch.setattr(nw.ea, "send_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP down")))
    monkeypatch.setattr(nw.nc, "fetch_site_submissions",
                        lambda session, nsite_id: (
                            [_row("A"), _row("B")]
                            if nsite_id == "8094300008956198244" else []))
    assert nw.run() == 0   # best-effort alert failure never fails the run
    n2688_rows = [r for r in _rows(fake) if r[1] == "subm:N2688"]
    assert len(n2688_rows) == 2 and n2688_rows[1][3] == "changed"


def test_last_submissions_snapshots_batches_into_one_tab_read(monkeypatch):
    fake = FakeSheets()
    calls = []
    orig = fake.values
    monkeypatch.setattr(fake, "values", lambda: (calls.append(1) or orig()))
    sw.ensure_submissions_tabs(fake, "SID")
    sw.append_submissions_watch_row(fake, "SID", "2026-07-24", "subm:A", "A", "baseline",
                                    "hash1", "note", "now", "{}")
    sw.append_submissions_watch_row(fake, "SID", "2026-07-24", "subm:B", "B", "baseline",
                                    "hash2", "note", "now", "{}")
    calls.clear()
    result = sw.last_submissions_snapshots(fake, "SID", ["subm:A", "subm:B", "subm:C"])
    assert result["subm:A"] == ("hash1", "{}")
    assert result["subm:B"] == ("hash2", "{}")
    assert result["subm:C"] is None
    assert len(calls) == 1   # one values() call for all three keys, not three
