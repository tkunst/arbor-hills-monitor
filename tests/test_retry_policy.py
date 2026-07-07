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
