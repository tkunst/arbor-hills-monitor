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
                 drive_factory=lambda: object())
    assert got == ["AbC_9-x"]


def test_drive_copy_that_is_not_a_pdf_is_refused(monkeypatch, tmp_path):
    rk = _mod()
    monkeypatch.setattr(dc, "download_file", lambda svc, fid, dest: open(dest, "wb").write(b"<html>"))
    with pytest.raises(RuntimeError):
        rk._download("https://drive.google.com/file/d/X/view", str(tmp_path / "a.pdf"),
                     drive_factory=lambda: object())


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://evil.example/file/d/X/view",
                                 "https://drive.google.com.evil.example/file/d/X/view", ""])
def test_other_links_are_refused(url, tmp_path):
    with pytest.raises(RuntimeError):
        _mod()._download(url, str(tmp_path / "a.pdf"), drive_factory=lambda: object())
