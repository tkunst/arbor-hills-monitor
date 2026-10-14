"""Tests for drive_client.py's service builders (coder:ocr-hang-fix,
2026-10-14): drive_service()/sheets_service() must build their http
transport with an EXPLICIT, finite socket timeout via authorized_http(),
never a bare `credentials=` kwarg to build() -- build() raises ValueError if
both `http=` and `credentials=` are given (confirmed by reading
googleapiclient.discovery.build_from_document's own source before writing
this fix), so asserting `credentials` is absent from the call is also a
structural proof that only one path is ever taken.

See drive_client.py's own ADDENDUM docstring: this does NOT newly bound a
previously-unbounded call -- `build(credentials=...)` already resolves,
internally, to `googleapiclient.http.build_http()`, which defaults to
`httplib2.Http(timeout=60)`. HTTP_TIMEOUT_S is set to that SAME value on
purpose. These tests exist so that fact is asserted in-repo, not just true
by accident of a third-party library default."""
import httplib2

import drive_client as dc


def test_authorized_http_wraps_an_httplib2_http_with_the_given_timeout():
    http = dc.authorized_http(credentials="fake-creds-object", timeout=42)
    assert isinstance(http.http, httplib2.Http)
    assert http.http.timeout == 42
    assert http.credentials == "fake-creds-object"


def test_authorized_http_default_timeout_is_the_module_constant():
    http = dc.authorized_http(credentials="fake-creds-object")
    assert http.http.timeout == dc.HTTP_TIMEOUT_S


def _capture_build(monkeypatch):
    calls = []

    def fake_build(service_name, version, **kwargs):
        calls.append({"service_name": service_name, "version": version, **kwargs})
        return f"fake-{service_name}-service"

    monkeypatch.setattr("googleapiclient.discovery.build", fake_build)
    return calls


def test_drive_service_passes_http_not_credentials(monkeypatch):
    monkeypatch.setattr(dc, "_credentials", lambda: "fake-creds")
    calls = _capture_build(monkeypatch)

    result = dc.drive_service()

    assert result == "fake-drive-service"
    assert len(calls) == 1
    assert "credentials" not in calls[0]        # mutually exclusive with http=
    assert calls[0]["http"].http.timeout == dc.HTTP_TIMEOUT_S
    assert calls[0]["cache_discovery"] is False


def test_sheets_service_passes_http_not_credentials(monkeypatch):
    monkeypatch.setattr(dc, "_credentials", lambda: "fake-creds")
    calls = _capture_build(monkeypatch)

    result = dc.sheets_service()

    assert result == "fake-sheets-service"
    assert len(calls) == 1
    assert "credentials" not in calls[0]
    assert calls[0]["http"].http.timeout == dc.HTTP_TIMEOUT_S
    assert calls[0]["cache_discovery"] is False
