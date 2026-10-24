"""wds_archiver.py's activation gate: _should_run() is the exact pure predicate
that was MISSING when this file first shipped (it only checked OAuth config,
so merging to main would have started snapshotting nightly before wds.enabled
was ever set). Pure, no Sheets/Drive/network mocking needed.

Also covers run()'s per-collection flow end to end through a fake Sheets
service + fake Drive upload (no network, no creds): a happy two-collection
run, the pre-existing per-page-upload-failure-now-reddens-the-run fix, and
per-item (per-collection) isolation of a _meta read/write failure."""
import re

import wds_archiver as wa
import sheet_writer as sw
from test_pfas_watcher import FakeSheets


def test_should_run_false_when_wds_disabled():
    ok, reason = wa._should_run({"wds": {"enabled": False}}, oauth_configured=True)
    assert ok is False
    assert "wds.enabled" in reason


def test_should_run_false_when_wds_key_absent():
    # cfg with no "wds" block at all must not be treated as enabled.
    ok, reason = wa._should_run({}, oauth_configured=True)
    assert ok is False
    assert "wds.enabled" in reason


def test_should_run_false_when_oauth_not_configured():
    ok, reason = wa._should_run({"wds": {"enabled": True}}, oauth_configured=False)
    assert ok is False
    assert "GOAUTH" in reason


def test_should_run_true_when_both_satisfied():
    ok, reason = wa._should_run({"wds": {"enabled": True}}, oauth_configured=True)
    assert ok is True
    assert reason == ""


def test_hash_is_stable_and_content_sensitive():
    a = wa._hash("<html>x</html>")
    b = wa._hash("<html>x</html>")
    c = wa._hash("<html>y</html>")
    assert a == b
    assert a != c


# ===========================================================================
# run() — fake Sheets + fake Drive (no network, no creds)
# ===========================================================================

_CFG = {"wds": {"enabled": True, "site_id": "475946",
                "collections": ["qmr", "applications"]}}


class _FakeDrive:
    """Stands in for the OAuth drive service object; wds_archiver never calls
    anything on it directly here since ac.upload_file itself is monkeypatched
    below -- this only needs to exist as a non-None return value."""


def _wire(monkeypatch, cfg=None, pages_by_collection=None, upload_fails_for=None):
    """pages_by_collection: {collection_name: list[str html pages]} (default:
    one trivial page per collection). upload_fails_for: a set of collection
    names whose Drive upload should raise (pins the pre-existing per-page
    upload failure, now expected to redden the run instead of swallowing)."""
    cfg = cfg or _CFG
    fake = FakeSheets()
    # The real Sheet already has _meta by the time this nightly job runs (the
    # main watcher/backfill pipeline creates it via ensure_tabs() on every run,
    # long before Stream C's archiver ever does); wds_archiver.run() itself
    # only calls ensure_wds_tabs(), which does NOT create _meta. Pre-create it
    # here to match that real precondition, not to paper over a gap.
    sw.ensure_tabs(fake, "SID")
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(wa, "load_config", lambda: cfg)
    monkeypatch.setattr(wa.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(wa.ac, "is_configured", lambda: True)
    monkeypatch.setattr(wa.ac, "oauth_drive_service", lambda: _FakeDrive())
    monkeypatch.setattr(wa.ac, "folder_id", lambda: "FOLDER")

    uploaded = []
    upload_fails_for = upload_fails_for or set()

    def _upload(service, local_path, name, mimetype, folder_id):
        collection = name.split("_")[1]  # "WDS_<name>_p<i>_<date>.html"
        if collection in upload_fails_for:
            raise RuntimeError("drive upload failed")
        uploaded.append(name)
        return f"https://drive.example/{name}"
    monkeypatch.setattr(wa.ac, "upload_file", _upload)

    pages_by_collection = pages_by_collection or {}

    def _fetch(name, w):
        return pages_by_collection.get(name, [f"<html>{name} page 0</html>"])
    monkeypatch.setattr(wa.wc, "fetch_raw_snapshot", _fetch)

    return fake, uploaded


def _snapshot_rows(fake):
    return fake._values._tabs.get(sw.TAB_WDS_SNAPSHOTS, [])[1:]  # drop header


def _arm_meta_write_failures_at(fake, fail_on_calls):
    """Wrap fake._values.update so every call number in `fail_on_calls`
    (1-indexed, counted against the _meta tab specifically) raises; every
    other update (any tab, any other number) behaves normally. write_meta is
    the only caller of values().update() against TAB_META in this flow, and
    it's called once per collection -- this is how ONE collection's _meta
    write fails while the others still succeed."""
    real_update = fake._values.update
    counter = {"n": 0}
    fail_on_calls = set(fail_on_calls)

    def _maybe_boom_update(spreadsheetId, range, valueInputOption, body):
        if re.match(r"'([^']+)'", range).group(1) == sw.TAB_META:
            counter["n"] += 1
            if counter["n"] in fail_on_calls:
                raise RuntimeError("HTTP 503")
        return real_update(spreadsheetId=spreadsheetId, range=range,
                           valueInputOption=valueInputOption, body=body)
    fake._values.update = _maybe_boom_update


def test_disabled_run_is_noop(monkeypatch):
    fake, uploaded = _wire(monkeypatch, cfg={"wds": {"enabled": False}})
    assert wa.run() == 0
    assert uploaded == []
    assert sw.TAB_WDS_SNAPSHOTS not in fake._values._tabs


def test_happy_path_two_collections_each_snapshot_their_own_page(monkeypatch):
    fake, uploaded = _wire(monkeypatch)
    assert wa.run() == 0
    rows = _snapshot_rows(fake)
    assert {r[1] for r in rows} == {"qmr", "applications"}
    assert len(uploaded) == 2


def test_per_page_upload_failure_reddens_the_run(monkeypatch):
    """This predates per-item isolation and is the same bug class PR #135
    fixed in pfas_pws_watcher: a Drive-upload failure for one collection's
    page used to be swallowed into exit 0. It now reddens the run while still
    isolating the OTHER collection, which still snapshots normally."""
    fake, uploaded = _wire(monkeypatch, upload_fails_for={"qmr"})
    assert wa.run() == 1
    rows = _snapshot_rows(fake)
    assert {r[1] for r in rows} == {"applications"}   # qmr's row never written
    assert uploaded == ["WDS_applications_p0_" + rows[0][0] + ".html"]


def test_one_collections_meta_write_failure_isolates_the_other(monkeypatch):
    """Per-item isolation: qmr's _meta read/write (processed first, since
    enabled_collections lists it first) fails, but applications (the other,
    independent collection in the SAME run) still fetches/uploads/records its
    page normally -- proving the failure doesn't abort the rest of run()."""
    fake, uploaded = _wire(monkeypatch)
    _arm_meta_write_failures_at(fake, fail_on_calls={1})   # qmr's write_meta
    assert wa.run() == 1
    rows = _snapshot_rows(fake)
    # Both pages still got fetched + uploaded + their snapshot row recorded --
    # only qmr's trailing _meta write itself failed.
    assert {r[1] for r in rows} == {"qmr", "applications"}
    assert len(uploaded) == 2


def test_item_failure_logs_class_only_not_the_raw_message(monkeypatch, capsys):
    """Rule: a per-item isolation catch must never print the raw exception
    string -- an HttpError's str() embeds the request URI, which contains the
    spreadsheet id -- only the exception class name."""
    fake, uploaded = _wire(monkeypatch)
    real_update = fake._values.update

    def _boom_update(spreadsheetId, range, valueInputOption, body):
        if re.match(r"'([^']+)'", range).group(1) == sw.TAB_META:
            raise RuntimeError("SENTINEL-DO-NOT-PRINT https://sheets.googleapis.com/SID")
        return real_update(spreadsheetId=spreadsheetId, range=range,
                           valueInputOption=valueInputOption, body=body)
    fake._values.update = _boom_update

    assert wa.run() == 1
    out = capsys.readouterr().out
    assert "SENTINEL-DO-NOT-PRINT" not in out
    assert "RuntimeError" in out
