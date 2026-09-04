"""scripts/create_oauth_folder.py — the folder-creation request body, with and
without an optional parent (ADR 037's addition). Hermetic: a fake Drive
service records the create() call's body, no network/creds."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import create_oauth_folder as cof


class _FakeFiles:
    def __init__(self):
        self.created_bodies = []

    def create(self, body, fields):
        self.created_bodies.append(body)

        class _Req:
            def execute(_self):
                return {"id": "new-folder-id"}
        return _Req()


class _FakeDrive:
    def __init__(self):
        self._files = _FakeFiles()

    def files(self):
        return self._files


def _wire(monkeypatch, env):
    for k in ("GOAUTH_CLIENT_ID", "GOAUTH_CLIENT_SECRET", "GOAUTH_REFRESH_TOKEN",
              "FOLDER_NAME", "PARENT_FOLDER_ID"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # main() falls back to sys.argv[1]/[2] when the env vars are unset (the
    # script's positional-arg CLI convenience) — pytest's own argv would
    # otherwise leak in as a false "folder name"/"parent id".
    monkeypatch.setattr(sys, "argv", ["create_oauth_folder.py"])
    fake = _FakeDrive()
    monkeypatch.setattr(cof.ac, "oauth_drive_service", lambda: fake)
    return fake


def test_creates_at_root_when_no_parent_given(monkeypatch, capsys):
    fake = _wire(monkeypatch, {
        "GOAUTH_CLIENT_ID": "cid", "GOAUTH_CLIENT_SECRET": "csecret",
        "GOAUTH_REFRESH_TOKEN": "rtoken", "FOLDER_NAME": "DPW",
    })
    assert cof.main() == 0
    body = fake._files.created_bodies[0]
    assert body["name"] == "DPW"
    assert "parents" not in body
    out = capsys.readouterr().out
    assert "move the new folder under your public-records parent" in out


def test_creates_under_given_parent(monkeypatch, capsys):
    fake = _wire(monkeypatch, {
        "GOAUTH_CLIENT_ID": "cid", "GOAUTH_CLIENT_SECRET": "csecret",
        "GOAUTH_REFRESH_TOKEN": "rtoken", "FOLDER_NAME": "DPW",
        "PARENT_FOLDER_ID": "1hqhI0XUD8LFeUUrAO9uHoRf8uol-J897",
    })
    assert cof.main() == 0
    body = fake._files.created_bodies[0]
    assert body["parents"] == ["1hqhI0XUD8LFeUUrAO9uHoRf8uol-J897"]
    out = capsys.readouterr().out
    assert "Already placed under the given parent" in out


def test_missing_folder_name_is_a_usage_error(monkeypatch):
    _wire(monkeypatch, {"GOAUTH_CLIENT_ID": "cid", "GOAUTH_CLIENT_SECRET": "csecret",
                        "GOAUTH_REFRESH_TOKEN": "rtoken"})
    assert cof.main() == 2


def test_missing_goauth_creds_is_an_error(monkeypatch):
    _wire(monkeypatch, {"FOLDER_NAME": "DPW"})
    assert cof.main() == 2
