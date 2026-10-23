"""egle_doc_parser's Anthropic client carries an explicit per-attempt timeout
well under the SDK's 600 s default (docs/overnight-coder-handoffs/
stall-hardening.md, part (b)): a degraded or throttled key must fail ONE
document in bounded time and hand it to doc_pipeline's transient-retry
handling, not hang the whole `daily` job to its timeout-minutes ceiling.
No network: `anthropic.Anthropic` is replaced by a recorder."""
from __future__ import annotations

from types import SimpleNamespace

import anthropic
import pytest

import egle_doc_parser as p


class _FakeClient:
    def __init__(self, parsed):
        self.messages = SimpleNamespace(parse=lambda **kw: SimpleNamespace(
            parsed_output=parsed, stop_reason="end_turn"))


def _recording_anthropic(monkeypatch, parsed=None):
    calls = []

    def fake_anthropic(**kwargs):
        calls.append(kwargs)
        return _FakeClient(parsed)
    monkeypatch.setattr(anthropic, "Anthropic", fake_anthropic)
    return calls


def test_client_is_built_with_an_explicit_timeout_under_the_sdk_default(monkeypatch):
    calls = _recording_anthropic(monkeypatch)
    p._anthropic_client(16384)
    assert len(calls) == 1 and "timeout" in calls[0]
    t = calls[0]["timeout"]
    assert isinstance(t, anthropic.Timeout)
    assert t.read is not None and t.read < 600
    assert t.read == t.write == t.pool == p.classify_timeout_s(16384)
    assert t.connect == p._CLASSIFY_CONNECT_TIMEOUT_S == 10.0
    assert "max_retries" not in calls[0]       # the SDK's own retry policy is kept


def test_timeout_is_sized_from_the_output_budget_with_the_sdk_estimate_plus_headroom():
    # The SDK's own non-streaming heuristic (128k output tokens/hour) estimates
    # ~460 s for 16384 output tokens. A timeout BELOW the slowest legitimate
    # response would be worse than the 600 s default: the call would time out,
    # be retried twice, and come back as a no-strike transient every run
    # forever. So the timeout is that estimate plus a minute, clamped under 600.
    sdk_estimate = 3600.0 * 16384 / 128_000
    t = p.classify_timeout_s(16384)
    assert sdk_estimate < t < 600.0
    assert t == sdk_estimate + p._CLASSIFY_TIMEOUT_HEADROOM_S
    assert p.classify_timeout_s(256) == p._CLASSIFY_TIMEOUT_MIN_S == 120.0
    assert p.classify_timeout_s(64000) == p._CLASSIFY_TIMEOUT_MAX_S == 600.0
    assert p.classify_timeout_s(8192) < p.classify_timeout_s(16384)


def test_live_config_max_tokens_gets_a_timeout_under_the_sdk_default():
    from config_loader import load_config
    max_tokens = load_config()["classification_max_tokens"]
    t = p.classify_timeout_s(max_tokens)
    assert 3600.0 * max_tokens / 128_000 < t < 600.0


def test_document_classifier_uses_the_timeout_client_when_none_is_passed(monkeypatch):
    parsed = SimpleNamespace(model_dump=lambda: {"summary": "s", "key_data_point": "k",
                                                 "doc_type": "evidence", "risks": ["R8"],
                                                 "severity": "routine", "measurements": [],
                                                 "deadlines": []})
    calls = _recording_anthropic(monkeypatch, parsed)
    out = p._classify_with_claude("text", {"document_name": "x"}, [], "m", max_tokens=16384)
    assert out["summary"] == "s"
    assert len(calls) == 1 and calls[0]["timeout"].read == p.classify_timeout_s(16384)


def test_note_metric_classifier_uses_the_timeout_client_when_none_is_passed(monkeypatch):
    calls = _recording_anthropic(monkeypatch, SimpleNamespace(metric="h2s"))
    assert p.classify_note_metric("n", model="m") == "h2s"       # max_tokens=256
    assert len(calls) == 1 and calls[0]["timeout"].read == 120.0


def test_an_injected_client_is_used_as_is(monkeypatch):
    calls = _recording_anthropic(monkeypatch)
    p.classify_note_metric("n", model="m", client=_FakeClient(SimpleNamespace(metric="ch4")))
    assert calls == []


def test_a_timeout_is_a_transient_no_strike_error_for_the_pipeline():
    import retry_policy as rp
    exc = anthropic.APITimeoutError(request=SimpleNamespace(url="https://api.anthropic.com"))
    assert rp.is_transient(exc)


def test_ocr_timeouts_remain_bounded_and_untouched():
    # Explicitly out of scope for part (b): ocr_util is already bounded.
    import inspect
    import ocr_util
    assert ocr_util._POST_KILL_TIMEOUT_S == 5
    assert inspect.signature(ocr_util.ocr_in_place).parameters["timeout"].default == 600
    assert inspect.signature(ocr_util.ensure_searchable).parameters["timeout"].default == 300
