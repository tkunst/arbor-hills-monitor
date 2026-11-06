"""scripts/recheck_key_data_points.py _download (CODER-PLAN step 6): a Drive
Mirror link listed in Archived PDFs is fetched from its PUBLIC link with NO
credentials (never through the service account: a planted link must not be
able to read a private file); MiEnviro stays allowed; anything else is
refused."""
import importlib.util
import os

import pytest


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod():
    spec = importlib.util.spec_from_file_location(
        "recheck", os.path.join(_REPO, "scripts", "recheck_key_data_points.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def FAKE(fid, dest):
    FAKE.got.append(fid)
    with open(dest, "wb") as f:
        f.write(FAKE.body)


FAKE.got, FAKE.body = [], b"%PDF-1.7"


@pytest.fixture(autouse=True)
def _fresh_fake():
    FAKE.got, FAKE.body = [], b"%PDF-1.7"
    yield


def test_drive_mirror_link_is_read_by_id(tmp_path):
    rk = _mod()
    FAKE.got, FAKE.body = [], b"%PDF-1.7"
    got = FAKE.got
    rk._download("https://drive.google.com/file/d/AbC_9-x/view?usp=drivesdk", str(tmp_path / "a.pdf"),
                 downloader=FAKE, mirror_ids={"AbC_9-x"})
    assert got == ["AbC_9-x"]


def test_drive_link_outside_the_mirror_is_refused(tmp_path):
    rk = _mod()
    FAKE.got = []
    for ids in (None, {"OTHER"}):
        with pytest.raises(RuntimeError):
            rk._download("https://drive.google.com/file/d/PRIVATE/view", str(tmp_path / "a.pdf"),
                         downloader=FAKE, mirror_ids=ids)
    assert FAKE.got == []


def test_mirror_file_ids_come_from_archived_pdfs(monkeypatch):
    rk = _mod()
    monkeypatch.setattr(rk.sw, "archived_doc_links", lambda *a, **k: {
        "1": "https://drive.google.com/file/d/F1/view?usp=drivesdk",
        "2": "https://mienviro.michigan.gov/ncore/downloadpdf/2", "3": ""})
    assert rk.mirror_file_ids(object(), "SID") == {"F1"}


def test_drive_copy_that_is_not_a_pdf_is_refused(tmp_path):
    rk = _mod()
    FAKE.body = b"<html>"
    with pytest.raises(RuntimeError):
        rk._download("https://drive.google.com/file/d/X/view", str(tmp_path / "a.pdf"),
                     downloader=FAKE, mirror_ids={"X"})


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://evil.example/file/d/X/view",
                                 "https://drive.google.com.evil.example/file/d/X/view", "", None])
def test_other_links_are_refused(url, tmp_path):
    with pytest.raises(RuntimeError):
        _mod()._download(url, str(tmp_path / "a.pdf"), downloader=FAKE)


def test_main_counts_only_passes_mirror_ids_and_prints_no_link(monkeypatch, capsys):
    rk = _mod()
    link = "https://drive.google.com/file/d/SECRETID/view"
    item = {"tab": "New Documents", "row": 2, "date_filed": "2026-11-01", "link": link,
            "document_name": "d", "old_kdp_sha256": "", "old": {}, "problems": {"x": ["p"]}}
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rk, "load_config", lambda: {})
    monkeypatch.setattr(rk.kdp_check, "reference_from_config", lambda c: None)
    monkeypatch.setattr(rk.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(rk, "scan", lambda *a: [item])
    monkeypatch.setattr(rk, "mirror_file_ids", lambda *a: {"SECRETID"})
    got = {}

    def fake_run_llm(items, cfg, mirror_ids=None, max_minutes=None, on_item=None):
        got["ids"] = mirror_ids
        items[0]["error"] = "HttpError"
        return 0
    monkeypatch.setattr(rk, "run_llm", fake_run_llm)
    monkeypatch.setattr("sys.argv", ["x", "--run-llm", "--counts-only"])
    assert rk.main() == 0
    assert got["ids"] == {"SECRETID"} and "SECRETID" not in capsys.readouterr().out


def test_run_llm_records_only_the_error_class(monkeypatch):
    rk = _mod()
    monkeypatch.setattr(rk, "_download", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("https://www.googleapis.com/drive/v3/files/SECRETID?alt=media 404")))
    items = [{"link": "x", "document_name": "d", "date_filed": ""}]
    rk.run_llm(items, {"anthropic_model": "m", "large_doc_page_threshold": 1,
                       "large_doc_max_keyword_pages": 1, "classification_max_tokens": 1})
    assert items[0]["error"] == "RuntimeError"


def test_default_drive_fetch_uses_no_credentials(monkeypatch, tmp_path):
    rk = _mod()
    import requests
    seen = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b"%PDF-1.7"

    def fake_get(url, params=None, stream=None, timeout=None, **kw):
        seen.update(url=url, params=params, kw=kw)
        return R()
    class S:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            seen["trust_env"] = self.trust_env
            return fake_get(url, **kw)
    monkeypatch.setattr(requests, "Session", S)
    rk._download("https://drive.google.com/file/d/F1/view", str(tmp_path / "a.pdf"), mirror_ids={"F1"})
    assert seen["url"] == "https://drive.usercontent.google.com/download" and seen["params"]["id"] == "F1"
    assert seen["kw"] == {} and seen["trust_env"] is False   # no auth, no ~/.netrc


class _Resp:
    def __init__(self, ctype="application/pdf", chunks=(b"%PDF-1.7",), status_error=None):
        self.headers = {"Content-Type": ctype}
        self._chunks, self._err = chunks, status_error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self._err:
            raise self._err

    def iter_content(self, n):
        yield from self._chunks


def _session(monkeypatch, resp):
    import requests

    class S:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            return resp
    monkeypatch.setattr(requests, "Session", S)


def test_public_download_html_page_is_throttled_not_a_pdf(monkeypatch, tmp_path):
    rk = _mod()
    _session(monkeypatch, _Resp(ctype="text/html; charset=utf-8", chunks=(b"<html>sign in",)))
    with pytest.raises(rk.MirrorThrottled):
        rk._public_drive_download("F", str(tmp_path / "a.pdf"))


def test_public_download_size_and_time_caps(monkeypatch, tmp_path):
    rk = _mod()
    monkeypatch.setattr(rk, "_MAX_PDF_BYTES", 10)
    _session(monkeypatch, _Resp(chunks=(b"%PDF-1.7", b"0123456789")))
    with pytest.raises(rk.MirrorTooLarge):
        rk._public_drive_download("F", str(tmp_path / "a.pdf"))
    monkeypatch.setattr(rk, "_MAX_PDF_BYTES", 10**9)
    ticks = iter([0, 0, rk._MAX_DOWNLOAD_S + 1])
    _session(monkeypatch, _Resp(chunks=(b"%PDF", b"-1.7")))
    with pytest.raises(rk.MirrorTooLarge):
        rk._public_drive_download("F", str(tmp_path / "b.pdf"), clock=lambda: next(ticks))


def test_public_download_http_error_propagates(monkeypatch, tmp_path):
    rk = _mod()
    _session(monkeypatch, _Resp(status_error=OSError("403")))
    with pytest.raises(OSError):
        rk._public_drive_download("F", str(tmp_path / "a.pdf"))


def test_run_llm_stops_starting_documents_after_the_time_budget(monkeypatch):
    rk = _mod()
    calls = []
    monkeypatch.setattr(rk, "_download", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(OSError()))
    ticks = iter([0, 0, 5 * 60, 30 * 60])
    items = [{"link": "x", "document_name": "d", "date_filed": ""} for _ in range(3)]
    saved = []
    left = rk.run_llm(items, {}, max_minutes=22, clock=lambda: next(ticks), on_item=lambda: saved.append(1))
    assert left == 1 and len(calls) == 2 and "error" not in items[2] and len(saved) == 2


def test_budget_counts_from_process_start_by_default(monkeypatch):
    rk = _mod()
    monkeypatch.setattr(rk, "_PROCESS_START", rk.time.monotonic() - 60 * 60)   # setup took an hour
    monkeypatch.setattr(rk, "_download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no start")))
    assert rk.run_llm([{"link": "x", "document_name": "d", "date_filed": ""}], {}) == 1


def test_main_saves_proposals_after_each_document(monkeypatch, tmp_path):
    import json as _json
    rk = _mod()
    item = {"tab": "New Documents", "row": 2, "date_filed": "", "link": "x", "document_name": "d",
            "old_kdp_sha256": "", "old": {}, "problems": {"x": ["p"]}}
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rk, "load_config", lambda: {})
    monkeypatch.setattr(rk.kdp_check, "reference_from_config", lambda c: None)
    monkeypatch.setattr(rk.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(rk, "scan", lambda *a: [item])
    monkeypatch.setattr(rk, "mirror_file_ids", lambda *a: set())
    out = tmp_path / "o"

    def fake_run_llm(items, cfg, mirror_ids=None, max_minutes=None, on_item=None):
        items[0]["error"] = "X"
        on_item()
        assert _json.loads((out / "kdp-recheck-proposals.json").read_text())[0]["error"] == "X"
        raise SystemExit(3)   # killed mid-run: the saved file must already exist
    monkeypatch.setattr(rk, "run_llm", fake_run_llm)
    monkeypatch.setattr("sys.argv", ["x", "--run-llm", "--counts-only", "--out-dir", str(out)])
    with pytest.raises(SystemExit):
        rk.main()
    assert (out / "kdp-recheck-proposals.json").exists()


def test_no_failing_rows_reads_no_archive_index(monkeypatch):
    rk = _mod()
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rk, "load_config", lambda: {})
    monkeypatch.setattr(rk.kdp_check, "reference_from_config", lambda c: None)
    monkeypatch.setattr(rk.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(rk, "scan", lambda *a: [])
    monkeypatch.setattr(rk, "mirror_file_ids", lambda *a: (_ for _ in ()).throw(AssertionError("no read")))
    monkeypatch.setattr("sys.argv", ["x", "--run-llm", "--counts-only"])
    assert rk.main() == 0


def test_pdf_header_after_leading_junk_is_accepted(tmp_path):
    rk = _mod()
    FAKE.body = b"\r\n junk \n%PDF-1.4 rest"
    rk._download("https://drive.google.com/file/d/F/view", str(tmp_path / "a.pdf"),
                 downloader=FAKE, mirror_ids={"F"})


def test_archive_index_read_failure_stops_with_class_only(monkeypatch, capsys):
    rk = _mod()
    item = {"tab": "New Documents", "row": 2, "date_filed": "", "link": "x", "document_name": "d",
            "old_kdp_sha256": "", "old": {}, "problems": {"x": ["p"]}}
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(rk, "load_config", lambda: {})
    monkeypatch.setattr(rk.kdp_check, "reference_from_config", lambda c: None)
    monkeypatch.setattr(rk.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(rk, "scan", lambda *a: [item])
    monkeypatch.setattr(rk, "mirror_file_ids", lambda *a: (_ for _ in ()).throw(
        OSError("https://sheets.googleapis.com/v4/spreadsheets/SECRETSHEET 429")))
    monkeypatch.setattr(rk, "run_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no run")))
    monkeypatch.setattr("sys.argv", ["x", "--run-llm", "--counts-only"])
    assert rk.main() == 1 and "SECRETSHEET" not in capsys.readouterr().out


def test_kdp_recheck_workflow_uploads_even_after_a_cut_off_step():
    import yaml
    wf = yaml.safe_load(open(os.path.join(_REPO, ".github", "workflows", "kdp-recheck.yml")))
    steps = wf["jobs"]["recheck"]["steps"]
    run = next(s for s in steps if "recheck_key_data_points.py" in s.get("run", ""))
    up = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert up.get("if") == "always()"
    job_min = wf["jobs"]["recheck"]["timeout-minutes"]
    assert _mod().DEFAULT_MAX_MINUTES < run["timeout-minutes"] < job_min
