"""snapshot_exporter.py unit tests (ADR 071): the flag, the naming rule, and the
never-raises contract of export_snapshot() against a mocked archive_client."""
import json
import os
import re

import pytest

import snapshot_exporter as se

ON = {"data_lifecycle": {"snapshot_export": {"enabled": True}}}


@pytest.fixture(autouse=True)
def _fresh_service_cache(monkeypatch):
    monkeypatch.setattr(se, "_drive", None)
    monkeypatch.setattr(se, "_tripped", None)


def _configure(monkeypatch):
    for k in se.ac.CREDENTIAL_ENV:
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv(se.FOLDER_ENV, "FOLDER123")


def _boom(*a, **k):
    raise AssertionError("must not be called")


# ---------------------------------------------------------------------------
# is_enabled / snapshot_name — pure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cfg", [
    None, {}, {"data_lifecycle": None}, {"data_lifecycle": {}},
    {"data_lifecycle": {"snapshot_export": None}},
    {"data_lifecycle": {"snapshot_export": {}}},
    {"data_lifecycle": {"snapshot_export": {"enabled": False}}},
    {"data_lifecycle": {"snapshot_export": {"enabled": None}}},
])
def test_is_enabled_false_for_every_missing_or_false_shape(cfg):
    assert se.is_enabled(cfg) is False


def test_is_enabled_true_only_on_explicit_true():
    assert se.is_enabled(ON) is True


@pytest.mark.parametrize("cfg", [
    {"data_lifecycle": True},                       # scalar where a mapping belongs
    {"data_lifecycle": "yes"},
    {"data_lifecycle": {"snapshot_export": True}},  # the activation-day typo
    {"data_lifecycle": {"snapshot_export": "on"}},
    {"data_lifecycle": ["enabled"]},
    "not a config at all",
    42,
])
def test_is_enabled_reads_a_config_shape_typo_as_off_not_a_crash(cfg):
    """is_enabled() runs in the watchers BEFORE their Sheet row; a typo must
    degrade to 'no export', never raise there."""
    assert se.is_enabled(cfg) is False


def test_snapshot_name_embeds_stream_key_date_and_hash():
    assert se.snapshot_name("rop", "csv:N2688", "2026-10-23", "abc123") == \
        "rop_csv-N2688_2026-10-23_abc123.json"
    assert se.snapshot_name("pfas", "Arbor Hills PFAS", "2026-10-23", "ff00", "txt") == \
        "pfas_Arbor-Hills-PFAS_2026-10-23_ff00.txt"


def test_snapshot_name_sanitizes_and_caps_the_key():
    name = se.snapshot_name("x", "a/b\\c?d=e&f g" + "Z" * 200, "d", "h")
    key = name[len("x_"):-len("_d_h.json")]
    assert set(key) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
    assert len(key) <= se._MAX_KEY_CHARS
    assert key.startswith("a-b-c-d-e-f-g")
    assert se.snapshot_name("x", "", "d", "h") == "x_item_d_h.json"
    assert se.snapshot_name("x", None, "d", "h") == "x_item_d_h.json"
    assert se.snapshot_name("x", "???", "d", "h") == "x_item_d_h.json"
    assert se.snapshot_name("x", 475946, "d", "h") == "x_475946_d_h.json"   # non-str key


def test_snapshot_name_truncated_keys_never_alias():
    """Two long keys sharing a 60-char prefix must map to two names —
    upload_file() dedupes by name, so an alias would silently drop a snapshot."""
    prefix = "https://www.michigan.gov/egle/about/organization/remediation-and-redevelopment/"
    a = se.snapshot_name("pfas", prefix + "arbor-hills-landfill", "d", "h", "txt")
    b = se.snapshot_name("pfas", prefix + "arbor-hills-energy", "d", "h", "txt")
    assert a != b
    for n in (a, b):
        key = n[len("pfas_"):-len("_d_h.txt")]
        assert len(key) <= se._MAX_KEY_CHARS
        assert re.fullmatch(r"[A-Za-z0-9._-]+", key)
    # a short key is untouched (no hash suffix)
    assert se.snapshot_name("rop", "csv:N2688", "d", "h") == "rop_csv-N2688_d_h.json"


# ---------------------------------------------------------------------------
# export_snapshot — gates
# ---------------------------------------------------------------------------

def test_disabled_returns_none_without_touching_env_or_drive(monkeypatch):
    _configure(monkeypatch)  # even fully configured: the flag wins
    monkeypatch.setattr(se.ac, "is_configured", _boom)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    assert se.export_snapshot({"a": 1}, "n.json", {}) is None
    assert se.export_snapshot({"a": 1}, "n.json", None) is None
    assert se.export_snapshot({"a": 1}, "n.json",
                              {"data_lifecycle": {"snapshot_export": {"enabled": False}}}) is None


def test_enabled_but_unconfigured_returns_none_without_network(monkeypatch, capsys):
    for k in se.ac.CREDENTIAL_ENV + (se.FOLDER_ENV,):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    assert se.export_snapshot({"a": 1}, "n.json", ON) is None
    assert se.FOLDER_ENV in capsys.readouterr().out


def test_creds_present_but_folder_missing_is_unconfigured(monkeypatch):
    for k in se.ac.CREDENTIAL_ENV:
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv(se.FOLDER_ENV, raising=False)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    assert se.export_snapshot("payload", "n.txt", ON) is None


# ---------------------------------------------------------------------------
# export_snapshot — success path
# ---------------------------------------------------------------------------

def _arm_upload(monkeypatch, uploads, service_calls=None):
    def _svc():
        if service_calls is not None:
            service_calls.append(1)
        return "fake-drive"

    def _upload(service, local_path, name, mimetype, folder_id):
        with open(local_path, encoding="utf-8") as fh:
            uploads.append({"service": service, "path": local_path, "content": fh.read(),
                            "name": name, "mimetype": mimetype, "folder": folder_id})
        return f"https://drive.example/{name}"
    monkeypatch.setattr(se.ac, "oauth_drive_service", _svc)
    monkeypatch.setattr(se.ac, "upload_file", _upload)


def test_success_uploads_str_payload_verbatim_and_returns_link(monkeypatch):
    _configure(monkeypatch)
    uploads = []
    _arm_upload(monkeypatch, uploads)
    link = se.export_snapshot('{"k": 1}', "rop_csv-N2688_2026-10-23_abc.json", ON)
    assert link == "https://drive.example/rop_csv-N2688_2026-10-23_abc.json"
    (u,) = uploads
    assert u["content"] == '{"k": 1}'          # the Sheet cell's exact text, untouched
    assert u["service"] == "fake-drive"
    assert u["name"] == "rop_csv-N2688_2026-10-23_abc.json"
    assert u["mimetype"] == "application/json"
    assert u["folder"] == "FOLDER123"
    assert not os.path.exists(u["path"])      # temp file cleaned up


def test_success_serializes_a_dict_payload_exactly_as_the_watchers_do(monkeypatch):
    _configure(monkeypatch)
    uploads = []
    _arm_upload(monkeypatch, uploads)
    snap = {"b": 2, "a": [1, "é"]}
    assert se.export_snapshot(snap, "n.json", ON)
    # byte-identical to the watchers' own snap_json form, so a dict payload's
    # file still matches the hash its Sheet row carries
    assert uploads[0]["content"] == json.dumps(snap, sort_keys=True, ensure_ascii=False)
    assert json.loads(uploads[0]["content"]) == {"a": [1, "é"], "b": 2}


def test_txt_extension_uploads_text_plain(monkeypatch):
    _configure(monkeypatch)
    uploads = []
    _arm_upload(monkeypatch, uploads)
    assert se.export_snapshot("normalized page text", "pfas_x_d_h.txt", ON)
    assert uploads[0]["mimetype"] == "text/plain"
    assert uploads[0]["content"] == "normalized page text"


def test_unknown_extension_falls_back_to_octet_stream(monkeypatch):
    _configure(monkeypatch)
    uploads = []
    _arm_upload(monkeypatch, uploads)
    assert se.export_snapshot("x", "weird.bin", ON)
    assert uploads[0]["mimetype"] == "application/octet-stream"


def test_custom_folder_env_is_honoured(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("GOAUTH_OTHER_FOLDER_ID", "OTHER")
    uploads = []
    _arm_upload(monkeypatch, uploads)
    assert se.export_snapshot("x", "n.json", ON, folder_env="GOAUTH_OTHER_FOLDER_ID")
    assert uploads[0]["folder"] == "OTHER"


def test_drive_service_is_built_once_per_process(monkeypatch):
    _configure(monkeypatch)
    uploads, service_calls = [], []
    _arm_upload(monkeypatch, uploads, service_calls)
    se.export_snapshot("a", "a.json", ON)
    se.export_snapshot("b", "b.json", ON)
    assert len(uploads) == 2
    assert len(service_calls) == 1


# ---------------------------------------------------------------------------
# export_snapshot — never raises
# ---------------------------------------------------------------------------

def test_upload_failure_returns_none_and_resets_the_service_cache(monkeypatch, capsys):
    _configure(monkeypatch)
    monkeypatch.setattr(se.ac, "oauth_drive_service", lambda: "fake-drive")

    def _upload(*a, **k):
        raise ConnectionError("https://secret.example/token?x=1")
    monkeypatch.setattr(se.ac, "upload_file", _upload)
    assert se.export_snapshot("x", "n.json", ON) is None
    assert se._drive is None                   # rebuilt fresh next call
    out = capsys.readouterr().out
    assert "ConnectionError" in out            # class only ...
    assert "secret.example" not in out         # ... never the raw message


def test_dead_oauth_token_returns_none(monkeypatch):
    _configure(monkeypatch)

    def _svc():
        raise RuntimeError("invalid_grant")
    monkeypatch.setattr(se.ac, "oauth_drive_service", _svc)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    assert se.export_snapshot("x", "n.json", ON) is None


# ---------------------------------------------------------------------------
# per-run circuit breaker
# ---------------------------------------------------------------------------

def test_first_failure_trips_the_breaker_for_the_rest_of_the_run(monkeypatch, capsys):
    """After one failure, later items in the same process skip Drive entirely
    (no refresh, no upload, no retry backoff) and log nothing more — a Drive
    outage costs one timeout per run, not one per item."""
    _configure(monkeypatch)
    service_calls, upload_calls = [], []

    def _svc():
        service_calls.append(1)
        return "fake-drive"

    def _upload(*a, **k):
        upload_calls.append(1)
        raise TimeoutError("drive down")
    monkeypatch.setattr(se.ac, "oauth_drive_service", _svc)
    monkeypatch.setattr(se.ac, "upload_file", _upload)

    assert se.export_snapshot("a", "a.json", ON) is None
    assert se._tripped == "TimeoutError"
    out1 = capsys.readouterr().out
    assert "FAILED (TimeoutError)" in out1

    assert se.export_snapshot("b", "b.json", ON) is None
    assert se.export_snapshot("c", "c.json", ON) is None
    assert len(service_calls) == 1 and len(upload_calls) == 1
    assert capsys.readouterr().out == ""          # silent skips after the one log line


def test_dead_token_fails_once_not_once_per_item(monkeypatch):
    _configure(monkeypatch)
    refreshes = []

    def _svc():
        refreshes.append(1)
        raise RuntimeError("invalid_grant")
    monkeypatch.setattr(se.ac, "oauth_drive_service", _svc)
    monkeypatch.setattr(se.ac, "upload_file", _boom)
    for n in ("a", "b", "c"):
        assert se.export_snapshot(n, f"{n}.json", ON) is None
    assert len(refreshes) == 1


def test_not_configured_logs_once_per_run(monkeypatch, capsys):
    for k in se.ac.CREDENTIAL_ENV + (se.FOLDER_ENV,):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(se.ac, "oauth_drive_service", _boom)
    for n in ("a", "b", "c"):
        assert se.export_snapshot(n, f"{n}.json", ON) is None
    out = capsys.readouterr().out
    assert out.count("not configured") == 1


def test_breaker_is_not_consulted_while_disabled(monkeypatch):
    """A tripped breaker is irrelevant with the flag off (still a pure no-op),
    and a success never trips it."""
    _configure(monkeypatch)
    uploads = []
    _arm_upload(monkeypatch, uploads)
    assert se.export_snapshot("a", "a.json", ON)
    assert se.export_snapshot("b", "b.json", ON)
    assert se._tripped is None and len(uploads) == 2
    monkeypatch.setattr(se, "_tripped", "X")
    monkeypatch.setattr(se.ac, "is_configured", _boom)
    assert se.export_snapshot("c", "c.json", {}) is None


def test_unserializable_payload_returns_none_without_tripping_the_breaker(monkeypatch):
    """An item-LOCAL preparation failure says nothing about Drive: this item
    gets no copy, but the next item in the run still does."""
    _configure(monkeypatch)
    uploads = []
    _arm_upload(monkeypatch, uploads)
    assert se.export_snapshot({"bad": object()}, "n.json", ON) is None
    assert se._tripped is None
    assert se.export_snapshot("fine", "m.json", ON)
    assert [u["name"] for u in uploads] == ["m.json"]


def test_temp_file_is_removed_even_when_upload_fails(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(se.ac, "oauth_drive_service", lambda: "fake-drive")
    seen = []

    def _upload(service, local_path, *a, **k):
        seen.append(local_path)
        raise OSError("quota")
    monkeypatch.setattr(se.ac, "upload_file", _upload)
    assert se.export_snapshot("x", "n.json", ON) is None
    assert seen and not os.path.exists(seen[0])
