"""civicclerk_archiver.py — the activation gate (pure), and the full run() flow
(multi-mirror dedup, missing-folder-secret skip, per-mirror fetch-failure
isolation, per-file upload-failure isolation) driven through a fake Sheets
service + fake mmpc_client/archive_client calls (no network, no creds).
Mirrors test_mmpc_archiver.py's gate tests; the run() tests are new since
Mirror D never had more than one mirror to isolate."""
import civicclerk_archiver as ca
import mmpc_client as mc
import sheet_writer as sw
from test_pfas_watcher import FakeSheets


# --- gate (pure) ------------------------------------------------------------

def test_should_run_false_when_disabled():
    ok, reason = ca._should_run({"civicclerk_archive": {"enabled": False}})
    assert ok is False and "civicclerk_archive.enabled" in reason


def test_should_run_false_when_key_absent():
    ok, reason = ca._should_run({})
    assert ok is False and "civicclerk_archive.enabled" in reason


def test_should_run_true_when_enabled():
    ok, reason = ca._should_run({"civicclerk_archive": {"enabled": True}})
    assert ok is True and reason == ""


# --- run() flow ---------------------------------------------------------------

CFG = {
    "civicclerk_archive": {
        "enabled": True,
        "mirrors": [
            {"category_id": 68, "folder_env": "GOAUTH_DPW_FOLDER_ID", "group": "Board of Public Works"},
            {"category_id": 26, "folder_env": "GOAUTH_BOC_FOLDER_ID", "group": "Board of Commissioners"},
            {"category_id": 27, "folder_env": "GOAUTH_BOC_FOLDER_ID", "group": "Board of Commissioners"},
        ],
    }
}


def _flat(file_id, category_id, event_id=1, ftype="Agenda", name="doc"):
    return {"file_id": file_id, "type": ftype, "name": name, "publish_on": "2026-01-01T00:00:00Z",
            "event_id": event_id, "event_date": "2026-01-01T00:00:00Z", "event_name": "ev"}


def _wire(monkeypatch, cfg, fetch_by_category, upload=None, drive_auth_ok=True):
    fake = FakeSheets()
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setenv("GOAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOAUTH_REFRESH_TOKEN", "rtoken")
    monkeypatch.setattr(ca, "load_config", lambda: cfg)
    monkeypatch.setattr(ca.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(ca.mc, "fetch_mmpc_files",
                        lambda session, category_id: fetch_by_category(category_id))
    monkeypatch.setattr(ca.mc, "download_file", lambda session, file_id, dest: dest)
    uploaded = []

    def _upload(drive, local, name, folder):
        uploaded.append((name, folder))
        return f"https://drive.example/{name}"

    monkeypatch.setattr(ca.ac, "upload_pdf", upload or _upload)
    if drive_auth_ok:
        monkeypatch.setattr(ca.ac, "oauth_drive_service", lambda: object())
    else:
        def _fail():
            raise RuntimeError("refresh token revoked")
        monkeypatch.setattr(ca.ac, "oauth_drive_service", _fail)
    return fake, uploaded


def _rows(fake):
    return fake._values._tabs.get(sw.TAB_CIVICCLERK_ARCHIVE, [])[1:]


def test_disabled_is_noop(monkeypatch):
    cfg = {"civicclerk_archive": {"enabled": False, "mirrors": CFG["civicclerk_archive"]["mirrors"]}}
    fake, uploaded = _wire(monkeypatch, cfg, lambda cid: [])
    assert ca.run() == 0
    assert uploaded == []
    assert sw.TAB_CIVICCLERK_ARCHIVE not in fake._values._tabs


def test_no_mirrors_configured_is_noop(monkeypatch):
    cfg = {"civicclerk_archive": {"enabled": True, "mirrors": []}}
    fake, uploaded = _wire(monkeypatch, cfg, lambda cid: [])
    assert ca.run() == 0
    assert uploaded == []


def test_missing_shared_credentials_is_noop(monkeypatch):
    fake, uploaded = _wire(monkeypatch, CFG, lambda cid: [_flat(1, cid)])
    monkeypatch.delenv("GOAUTH_REFRESH_TOKEN")
    assert ca.run() == 0
    assert uploaded == []


def test_dead_refresh_token_is_loud(monkeypatch):
    fake, uploaded = _wire(monkeypatch, CFG, lambda cid: [_flat(1, cid)], drive_auth_ok=False)
    monkeypatch.setenv("GOAUTH_DPW_FOLDER_ID", "dpw-folder")
    assert ca.run() == 1
    assert uploaded == []


def test_mirror_with_no_folder_secret_is_skipped_not_fatal(monkeypatch):
    # DPW's folder secret is set; BOC's is not — DPW must still archive, and
    # the overall run must not fail just because BOC isn't provisioned yet.
    fake, uploaded = _wire(monkeypatch, CFG,
                           lambda cid: [_flat(100 + cid, cid)])
    monkeypatch.setenv("GOAUTH_DPW_FOLDER_ID", "dpw-folder")
    assert ca.run() == 0
    assert [f for f, _ in uploaded] == ["168.pdf"]  # only DPW's file (cat 68) uploaded
    rows = _rows(fake)
    assert len(rows) == 1 and rows[0][5] == "Board of Public Works"


def test_boc_two_categories_share_one_folder_and_one_dedup_set(monkeypatch):
    fake, uploaded = _wire(monkeypatch, CFG,
                           lambda cid: [_flat(200 + cid, cid)] if cid in (26, 27) else [])
    monkeypatch.setenv("GOAUTH_BOC_FOLDER_ID", "boc-folder")
    assert ca.run() == 0
    assert {f for f, _ in uploaded} == {"226.pdf", "227.pdf"}
    assert all(folder == "boc-folder" for _, folder in uploaded)
    rows = _rows(fake)
    assert len(rows) == 2 and all(r[5] == "Board of Commissioners" for r in rows)


def test_already_archived_file_id_is_not_reuploaded(monkeypatch):
    # Same file_id already present in the archive tab (e.g. archived under a
    # different mirror in a prior run) must never be re-fetched/re-uploaded —
    # file IDs are a single global CivicClerk sequence.
    fake, uploaded = _wire(monkeypatch, CFG, lambda cid: [_flat(999, cid)])
    monkeypatch.setenv("GOAUTH_DPW_FOLDER_ID", "dpw-folder")
    monkeypatch.setenv("GOAUTH_BOC_FOLDER_ID", "boc-folder")
    sw.ensure_civicclerk_archive_tabs(fake, "SID")
    sw.append_civicclerk_archive_row(
        fake, "SID", 999, "2025-01-01", "Agenda", "old doc", 1,
        "Board of Public Works", "https://drive.example/999.pdf", "2025-01-01T00:00:00")
    assert ca.run() == 0
    assert uploaded == []  # every mirror's file_id 999 was already archived


def test_mirror_fetch_failure_is_isolated_other_mirrors_still_run(monkeypatch):
    def _fetch(cid):
        if cid == 68:
            raise mc.MMPCFetchError("DPW category down")
        return [_flat(300 + cid, cid)]

    fake, uploaded = _wire(monkeypatch, CFG, _fetch)
    monkeypatch.setenv("GOAUTH_DPW_FOLDER_ID", "dpw-folder")
    monkeypatch.setenv("GOAUTH_BOC_FOLDER_ID", "boc-folder")
    assert ca.run() == 1  # surfaced, but...
    assert {f for f, _ in uploaded} == {"326.pdf", "327.pdf"}  # ...BOC still archived


def test_one_file_upload_failure_does_not_abort_the_batch(monkeypatch):
    attempted = []

    def _upload(drive, local, name, folder):
        attempted.append(name)
        if name == "168.pdf":
            raise RuntimeError("upload quota exceeded")
        return f"https://drive.example/{name}"

    fake, uploaded = _wire(
        monkeypatch, CFG,
        lambda cid: [_flat(100 + cid, cid), _flat(400 + cid, cid)] if cid == 68 else [],
        upload=_upload)
    monkeypatch.setenv("GOAUTH_DPW_FOLDER_ID", "dpw-folder")
    assert ca.run() == 0
    assert attempted == ["168.pdf", "468.pdf"]   # both attempted...
    assert len(_rows(fake)) == 1                 # ...but only the 2nd succeeded/recorded
