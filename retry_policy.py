"""
retry_policy.py — classify a per-document processing error as TRANSIENT
(infrastructure/quota: retry on a later run, do NOT count a poison strike) or
PERMANENT (the source is genuinely unprocessable: accrue a strike toward the
MAX_ERRORS_PER_DOC poison threshold).

Why this exists (2026-07-07): the $10/mo workspace API cap was consumed by the
one-time historical backfill. While capped, every classification raised a 400
"You have reached your specified workspace API usage limits" error. Both
watcher.py and backfill.py counted each failure as a poison strike, so three
cap-hits in a row permanently marked real filings "skipped" — a June-16 EGLE
Surface Emission Monitoring inspection package (two urgent docs) was silently
dropped with no recovery path. A monthly cap, a 429, a 5xx, or a network blip is
never the document's fault, so it must not poison the document.

Detection is duck-typed (a status_code attribute plus a narrow set of message
substrings) so callers need not import anthropic. An optional isinstance check
catches the SDK's connection/timeout/rate-limit/server errors, which may carry
no status_code. The message set is kept deliberately narrow: a genuine
bad-document 400 (malformed request, un-openable PDF, max_tokens truncation)
must still accrue strikes so truly poison docs stay self-terminating.
"""
from __future__ import annotations

# Substrings (matched case-insensitively against str(exc)) that mark an
# infrastructure/quota failure regardless of exception type. Narrow on purpose —
# see the module docstring. "usage limit" covers the workspace/monthly cap that
# triggered this module; "rate limit" is defensive (429s are also caught by
# status_code); "overloaded" is the 529 phrasing.
_TRANSIENT_MARKERS = (
    "usage limit",          # workspace / monthly API cap (the 2026-07 incident)
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",           # 529 / server overload
)


def _status_of(exc: BaseException):
    """The HTTP status an exception carries, if any. Handles anthropic
    APIStatusError (.status_code), a bare .status, and requests-style
    HTTPError (.response.status_code)."""
    for val in (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(val, int) and not isinstance(val, bool):
            return val
    return None


def is_transient(exc: BaseException) -> bool:
    """True if `exc` is an infrastructure/quota error to retry on a later run
    WITHOUT a poison strike; False for genuine unprocessable-source errors
    (un-openable PDF, OCR failure, max_tokens truncation, generic 400), which
    SHOULD accrue strikes toward the poison threshold."""
    # 1) HTTP status: 429 (rate limit) and any 5xx (server / overload) are
    #    transient. A 400 is usually a real bad request; the one transient 400 is
    #    the usage-cap message, caught by the marker check below — so do NOT treat
    #    all 400s as transient here.
    status = _status_of(exc)
    if status is not None and (status == 429 or status >= 500):
        return True

    # 2) The anthropic SDK's connection/timeout/rate-limit/server errors may carry
    #    no status_code. Import is optional so watcher.py / backfill.py need not
    #    depend on anthropic being importable to use this module.
    try:
        import anthropic

        if isinstance(
            exc,
            (
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.RateLimitError,
                anthropic.InternalServerError,
            ),
        ):
            return True
    except Exception:
        pass

    # 3) Message-substring fallback: catches the usage-cap 400 and any infra error
    #    whose type we did not special-case but whose text is unambiguous.
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)
