"""Transient-vs-permanent error classification (retry_policy) — the poison-strike
guard added 2026-07-07 after the workspace API cap falsely poisoned a real
June-16 SEM inspection package. Pure function over an exception: no network, no
credentials, no anthropic needed (the SDK import inside is optional)."""
import retry_policy as rp


class _HttpError(Exception):
    """Mimics an anthropic APIStatusError: carries a `status_code` attribute."""

    def __init__(self, status_code, message=""):
        super().__init__(message or f"Error code: {status_code}")
        self.status_code = status_code


# --- transient: must NOT accrue a poison strike ---------------------------------


def test_workspace_usage_cap_is_transient():
    # The exact 2026-06/07 incident: a 400 whose body is the monthly-cap message.
    exc = _HttpError(
        400,
        "Error code: 400 - {'type': 'error', 'error': {'type': "
        "'invalid_request_error', 'message': 'You have reached your specified "
        "workspace API usage limits. You will regain access on 2026-07-01.'}}",
    )
    assert rp.is_transient(exc) is True


def test_rate_limit_and_server_errors_are_transient():
    assert rp.is_transient(_HttpError(429)) is True
    assert rp.is_transient(_HttpError(500)) is True
    assert rp.is_transient(_HttpError(503)) is True
    assert rp.is_transient(_HttpError(529, "overloaded")) is True


def test_requests_style_response_status_is_read():
    # requests.HTTPError-style: the status lives on exc.response.status_code.
    class _Resp:
        status_code = 503

    class _ReqErr(Exception):
        response = _Resp()

    assert rp.is_transient(_ReqErr("boom")) is True


def test_overloaded_message_without_status_is_transient():
    assert rp.is_transient(RuntimeError("The server is overloaded, please retry")) is True


# --- permanent: MUST accrue a strike so poison docs stay self-terminating -------


def test_unopenable_pdf_is_permanent():
    assert (
        rp.is_transient(
            RuntimeError("Failed to open file '/tmp/N2688_123.pdf' as type pdf.")
        )
        is False
    )


def test_generic_400_is_permanent():
    # A real bad request (not the cap) must still count toward poison.
    assert rp.is_transient(_HttpError(400, "invalid base64 data in document block")) is False


def test_max_tokens_truncation_is_permanent():
    assert (
        rp.is_transient(
            RuntimeError(
                "Classification truncated at max_tokens=8192 (stop_reason=max_tokens)"
            )
        )
        is False
    )


def test_missing_pdf_content_is_permanent():
    # nSITE downloadpdf 400 for a non-PDF source (legacy .doc / image / zip).
    assert rp.is_transient(RuntimeError("PDF content could not be found")) is False


def test_plain_value_error_is_permanent():
    assert rp.is_transient(ValueError("unexpected classification shape")) is False


# --- a key problem is never the document's fault (stall-hardening, 2026-10-23) --


def test_auth_and_permission_errors_are_transient():
    # A revoked/expired/mis-scoped key would otherwise stub every real filing
    # "skipped" within three runs, and read to the document-pipeline liveness
    # guard as progress instead of the stall it is.
    assert rp.is_transient(_HttpError(401, "invalid x-api-key")) is True
    assert rp.is_transient(_HttpError(403, "permission denied")) is True


def test_sdk_authentication_errors_are_transient():
    anthropic = __import__("anthropic")
    from types import SimpleNamespace
    resp = SimpleNamespace(status_code=401, headers={}, request=None)
    exc = anthropic.AuthenticationError("bad key", response=resp, body=None)
    assert rp.is_transient(exc) is True
    resp = SimpleNamespace(status_code=403, headers={}, request=None)
    exc = anthropic.PermissionDeniedError("no", response=resp, body=None)
    assert rp.is_transient(exc) is True


def test_generic_400_and_404_still_strike():
    assert rp.is_transient(_HttpError(400, "malformed request")) is False
    assert rp.is_transient(_HttpError(404)) is False


# --- the cause chain: an nSITE download wrapped `from` a 5xx/connection error --


def test_wrapped_download_failure_with_a_5xx_cause_is_transient():
    class _Resp:
        status_code = 503
    inner = Exception("503 Server Error: Service Unavailable for url")
    inner.response = _Resp()
    try:
        raise RuntimeError("download failed for doc 123: 503 Server Error") from inner
    except RuntimeError as exc:
        assert rp.is_transient(exc) is True


def test_wrapped_download_failure_with_a_requests_connection_error_is_transient():
    requests = __import__("requests")
    try:
        try:
            raise requests.exceptions.ConnectionError("Max retries exceeded with url")
        except requests.exceptions.ConnectionError as inner:
            raise RuntimeError(f"download failed for doc 123: {inner}") from inner
    except RuntimeError as exc:
        assert rp.is_transient(exc) is True
    try:
        try:
            raise requests.exceptions.ReadTimeout("Read timed out")
        except requests.exceptions.Timeout as inner:
            raise RuntimeError(f"download failed for doc 123: {inner}") from inner
    except RuntimeError as exc:
        assert rp.is_transient(exc) is True


def test_wrapped_download_failure_with_a_404_or_non_pdf_cause_still_strikes():
    class _Resp:
        status_code = 404
    inner = Exception("404 Client Error: Not Found for url")
    inner.response = _Resp()
    try:
        raise RuntimeError("download failed for doc 123: 404 Client Error") from inner
    except RuntimeError as exc:
        assert rp.is_transient(exc) is False
    inner = RuntimeError("non-PDF response from https://x (starts b'<html>')")
    try:
        raise RuntimeError(f"download failed for doc 123: {inner}") from inner
    except RuntimeError as exc:
        assert rp.is_transient(exc) is False


def test_chain_is_cycle_safe():
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert rp.is_transient(a) is False


def test_download_pdf_chains_the_underlying_failure(monkeypatch, tmp_path):
    import nsite_client as nc
    requests = __import__("requests")
    monkeypatch.setattr(nc.time, "sleep", lambda s: None)

    class _Session:
        def get(self, url, headers=None, timeout=None):
            raise requests.exceptions.ConnectionError("Max retries exceeded")
    doc = {"doc_id": "123", "doc_url": "https://example.invalid/d/123"}
    try:
        nc.download_pdf(_Session(), doc, str(tmp_path / "x.pdf"))
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as exc:
        assert "download failed for doc 123" in str(exc)
        assert isinstance(exc.__cause__, requests.exceptions.ConnectionError)
        assert rp.is_transient(exc) is True


# --- round-3 review: an nSITE per-document 401/403 must still strike ----------


def test_nsite_download_401_403_behind_the_wrapper_still_strikes():
    # nSITE refuses specific documents on referer grounds (a 403). Before the
    # cause chain existed this struck and stubbed after three runs; it must
    # not become a silent forever-retry just because the chain is visible.
    requests = __import__("requests")
    for code in (401, 403):
        resp = requests.Response()
        resp.status_code = code
        try:
            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError as inner:
                raise RuntimeError(f"download failed for doc 123: {inner}") from inner
        except RuntimeError as exc:
            assert rp.is_transient(exc) is False, code


def test_anthropic_shaped_401_403_direct_status_code_is_transient_but_requests_shaped_is_not():
    assert rp.is_transient(_HttpError(403)) is True          # direct .status_code

    class _Resp:
        status_code = 403
    exc = Exception("403 Client Error")
    exc.response = _Resp()                                   # requests shape
    assert rp.is_transient(exc) is False
