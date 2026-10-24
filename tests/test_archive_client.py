"""Tests for archive_client.py's oauth_drive_service() (coder:ocr-hang-fix,
2026-10-14): must build its http transport via drive_client.authorized_http()
with an explicit, finite timeout, never a bare `credentials=` kwarg to
build() -- see tests/test_drive_client.py's module docstring for why
`credentials` being absent from the call is a structural proof, and
drive_client.py's own ADDENDUM docstring for why HTTP_TIMEOUT_S doesn't
change actual behavior (60s was already googleapiclient's own default)."""
import archive_client as ac
import drive_client as dc


def test_oauth_drive_service_passes_http_not_credentials(monkeypatch):
    monkeypatch.setattr(ac, "_credentials", lambda: "fake-creds")
    calls = []

    def fake_build(service_name, version, **kwargs):
        calls.append({"service_name": service_name, "version": version, **kwargs})
        return "fake-drive-service"
    monkeypatch.setattr("googleapiclient.discovery.build", fake_build)

    result = ac.oauth_drive_service()

    assert result == "fake-drive-service"
    assert len(calls) == 1
    assert "credentials" not in calls[0]        # mutually exclusive with http=
    assert calls[0]["http"].http.timeout == dc.HTTP_TIMEOUT_S
    assert calls[0]["cache_discovery"] is False


def test_oauth_drive_service_uses_the_shared_authorized_http_helper():
    # Not a reimplementation of drive_client's helper -- the same one, so
    # there is exactly one place that owns the HTTP-timeout policy.
    assert ac.authorized_http is dc.authorized_http
