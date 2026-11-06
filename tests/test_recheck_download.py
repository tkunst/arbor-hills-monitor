"""scripts/recheck_key_data_points.py _download (CODER-PLAN step 6): a Drive
Mirror link is read through the service account; MiEnviro stays allowed;
anything else is refused."""
import importlib.util
import os

import pytest

import drive_client as dc

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod():
    spec = importlib.util.spec_from_file_location(
        "recheck", os.path.join(_REPO, "scripts", "recheck_key_data_points.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_drive_mirror_link_is_read_by_id(monkeypatch, tmp_path):
    rk = _mod()
    got = []
    monkeypatch.setattr(dc, "download_file",
                        lambda svc, fid, dest: got.append(fid) or open(dest, "wb").write(b"%PDF-1.7"))
    rk._download("https://drive.google.com/file/d/AbC_9-x/view?usp=drivesdk", str(tmp_path / "a.pdf"),
                 drive_factory=lambda: object(), mirror_ids={"AbC_9-x"})
    assert got == ["AbC_9-x"]


def test_drive_link_outside_the_mirror_is_refused(monkeypatch, tmp_path):
    rk = _mod()
    monkeypatch.setattr(dc, "download_file", lambda *a: (_ for _ in ()).throw(AssertionError("no read")))
    for ids in (None, {"OTHER"}):
        with pytest.raises(RuntimeError):
            rk._download("https://drive.google.com/file/d/PRIVATE/view", str(tmp_path / "a.pdf"),
                         drive_factory=lambda: object(), mirror_ids=ids)


def test_mirror_file_ids_come_from_archived_pdfs(monkeypatch):
    rk = _mod()
    monkeypatch.setattr(rk.sw, "archived_doc_links", lambda *a, **k: {
        "1": "https://drive.google.com/file/d/F1/view?usp=drivesdk",
        "2": "https://mienviro.michigan.gov/ncore/downloadpdf/2", "3": ""})
    assert rk.mirror_file_ids(object(), "SID") == {"F1"}


def test_drive_copy_that_is_not_a_pdf_is_refused(monkeypatch, tmp_path):
    rk = _mod()
    monkeypatch.setattr(dc, "download_file", lambda svc, fid, dest: open(dest, "wb").write(b"<html>"))
    with pytest.raises(RuntimeError):
        rk._download("https://drive.google.com/file/d/X/view", str(tmp_path / "a.pdf"),
                     drive_factory=lambda: object(), mirror_ids={"X"})


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://evil.example/file/d/X/view",
                                 "https://drive.google.com.evil.example/file/d/X/view", "", None])
def test_other_links_are_refused(url, tmp_path):
    with pytest.raises(RuntimeError):
        _mod()._download(url, str(tmp_path / "a.pdf"), drive_factory=lambda: object())


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

    def fake_run_llm(items, cfg, mirror_ids=None):
        got["ids"] = mirror_ids
        items[0]["error"] = "HttpError"
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
