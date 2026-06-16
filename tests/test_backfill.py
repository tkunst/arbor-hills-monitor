"""Backfill todo-selection + remaining-count logic, including the RETRY_POISONED
recovery path. Pure functions over a (docs, state) pair — no network, no creds.

A "poison" doc is one with >= MAX_ERRORS_PER_DOC failed attempts and no later
success; normal runs skip it so the job self-terminates. After raising
classification_max_tokens to fix a truncation, a RETRY_POISONED run re-attempts
those docs without touching the append-only _state log."""
import backfill as bf

ME = bf.MAX_ERRORS_PER_DOC


def _docs(*ids):
    return [{"doc_id": i} for i in ids]


def _state(processed=(), errors=None):
    return {"processed": {p: {} for p in processed}, "errors": dict(errors or {})}


def test_select_todo_skips_processed_and_poisoned():
    docs = _docs("a", "b", "c", "d")
    state = _state(processed=["a"], errors={"b": ME, "c": ME + 2})  # b,c poisoned
    todo = [d["doc_id"] for d in bf.select_todo(docs, state)]
    assert todo == ["d"]  # a done, b/c poisoned, only d remains


def test_select_todo_keeps_sub_threshold_errors():
    docs = _docs("a", "b")
    state = _state(errors={"a": ME - 1})  # 2 failures, not yet poisoned
    todo = [d["doc_id"] for d in bf.select_todo(docs, state)]
    assert todo == ["a", "b"]  # a still retryable under the normal gate


def test_retry_poisoned_reincludes_poison_but_not_processed():
    docs = _docs("a", "b", "c")
    state = _state(processed=["a"], errors={"b": ME, "c": ME + 5})
    todo = [d["doc_id"] for d in bf.select_todo(docs, state, retry_poisoned=True)]
    assert todo == ["b", "c"]  # poison re-attempted; processed 'a' still skipped


def test_count_remaining_is_poison_aware_regardless_of_retry():
    # The end-of-run signal must ignore poison docs so the job stays
    # self-terminating even on a retry run that re-listed them as todo.
    docs = _docs("a", "b", "c")
    state = _state(processed=["a"], errors={"b": ME})  # b poisoned, c fresh
    assert bf.count_remaining(docs, state) == 1  # only c counts


def test_count_remaining_zero_when_all_done_or_poisoned():
    docs = _docs("a", "b")
    state = _state(processed=["a"], errors={"b": ME})
    assert bf.count_remaining(docs, state) == 0  # "Backfill complete" condition


def test_retry_poisoned_env_parsing(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", " Yes "):
        monkeypatch.setenv("RETRY_POISONED", truthy)
        assert bf._retry_poisoned() is True
    for falsy in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("RETRY_POISONED", falsy)
        assert bf._retry_poisoned() is False
    monkeypatch.delenv("RETRY_POISONED", raising=False)
    assert bf._retry_poisoned() is False  # unset -> normal (skip poison)
