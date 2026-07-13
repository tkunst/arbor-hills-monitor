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


def _state(processed=(), errors=None, skipped=()):
    return {"processed": {p: {} for p in processed},
            "errors": dict(errors or {}),
            "skipped": {s: {} for s in skipped}}


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


def test_select_todo_excludes_skipped_even_under_retry():
    # A 'skipped' doc is terminal (stubbed + visible): never re-attempted, even
    # in retry mode — unlike a poison doc, which retry re-includes.
    docs = _docs("a", "b", "c")
    state = _state(skipped=["a"], errors={"b": ME})
    assert [d["doc_id"] for d in bf.select_todo(docs, state)] == ["c"]
    assert [d["doc_id"] for d in bf.select_todo(docs, state, retry_poisoned=True)] == ["b", "c"]


def test_count_remaining_excludes_skipped():
    docs = _docs("a", "b")
    state = _state(skipped=["a"])  # a terminal-skipped, b fresh
    assert bf.count_remaining(docs, state) == 1


def test_retry_poisoned_env_parsing(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", " Yes "):
        monkeypatch.setenv("RETRY_POISONED", truthy)
        assert bf._retry_poisoned() is True
    for falsy in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("RETRY_POISONED", falsy)
        assert bf._retry_poisoned() is False
    monkeypatch.delenv("RETRY_POISONED", raising=False)
    assert bf._retry_poisoned() is False  # unset -> normal (skip poison)


# --- ADR 011: RETRY_DOC_IDS, the targeted retroactive-retry override ---


def test_retry_doc_ids_env_parsing(monkeypatch):
    monkeypatch.setenv("RETRY_DOC_IDS", " 111, 222 ,333")
    assert bf._retry_doc_ids() == {"111", "222", "333"}
    monkeypatch.setenv("RETRY_DOC_IDS", "")
    assert bf._retry_doc_ids() == set()
    monkeypatch.delenv("RETRY_DOC_IDS", raising=False)
    assert bf._retry_doc_ids() == set()  # unset -> no override


def test_select_todo_retry_doc_ids_bypasses_skipped():
    # The exact ADR 011 scenario: a doc terminally 'skipped' before the
    # .msg/.docx extractor existed is now known-processable and named
    # explicitly — it alone should re-enter todo, everything else obeys the
    # normal gates unchanged.
    docs = _docs("a", "b", "c")
    state = _state(skipped=["a"], errors={"b": ME})
    todo = [d["doc_id"] for d in bf.select_todo(docs, state, retry_doc_ids={"a"})]
    assert todo == ["a", "c"]  # a retried (named), b still poisoned+not-named, c fresh


def test_select_todo_retry_doc_ids_never_reincludes_processed():
    # A genuine success is never re-attempted, even if explicitly named —
    # retry_doc_ids is for terminally-skipped docs, not a re-run-everything switch.
    docs = _docs("a", "b")
    state = _state(processed=["a"])
    todo = [d["doc_id"] for d in bf.select_todo(docs, state, retry_doc_ids={"a", "b"})]
    assert todo == ["b"]


def test_select_todo_retry_doc_ids_composes_with_retry_poisoned():
    docs = _docs("a", "b", "c")
    state = _state(skipped=["a"], errors={"b": ME})
    todo = [d["doc_id"] for d in bf.select_todo(
        docs, state, retry_poisoned=True, retry_doc_ids={"a"})]
    assert todo == ["a", "b", "c"]  # a via retry_doc_ids, b via retry_poisoned, c fresh


# --- FORCE_REPROCESS_DOC_IDS: the only override that bypasses the processed gate ---


def test_force_reprocess_env_parsing(monkeypatch):
    monkeypatch.setenv("FORCE_REPROCESS_DOC_IDS", " 111, 222 ,333")
    assert bf._force_reprocess_doc_ids() == {"111", "222", "333"}
    monkeypatch.setenv("FORCE_REPROCESS_DOC_IDS", "")
    assert bf._force_reprocess_doc_ids() == set()
    monkeypatch.delenv("FORCE_REPROCESS_DOC_IDS", raising=False)
    assert bf._force_reprocess_doc_ids() == set()  # unset -> no override


def test_force_reprocess_apply_env_parsing(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", " Yes "):
        monkeypatch.setenv("FORCE_REPROCESS_APPLY", truthy)
        assert bf._force_reprocess_apply() is True
    for falsy in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("FORCE_REPROCESS_APPLY", falsy)
        assert bf._force_reprocess_apply() is False
    monkeypatch.delenv("FORCE_REPROCESS_APPLY", raising=False)
    assert bf._force_reprocess_apply() is False  # unset -> safe default: dry-run


def test_select_todo_force_reprocess_reincludes_processed():
    # The one override that re-processes an already-'processed' doc (a WOI report
    # processed via the old windowed path, now routed through woi_router).
    docs = _docs("a", "b", "c")
    state = _state(processed=["a", "b"])
    todo = [d["doc_id"] for d in bf.select_todo(
        docs, state, force_reprocess_doc_ids={"a"})]
    assert todo == ["a"]  # a forced despite being processed


def test_select_todo_force_reprocess_is_surgical():
    # A force run processes ONLY the named docs — never the normal backlog too
    # (backfill is alert-less, so silently sweeping up a fresh pending doc is the
    # exact foot-gun that disabled the nightly schedule). Here 'd' is a genuinely
    # unprocessed doc, but it must NOT ride along on a force-reprocess run.
    docs = _docs("a", "b", "c", "d")
    state = _state(processed=["a", "b", "c"])  # d is normally 'remaining'
    todo = [d["doc_id"] for d in bf.select_todo(
        docs, state, force_reprocess_doc_ids={"c"})]
    assert todo == ["c"]  # only the forced doc; the pending 'd' is left untouched


def test_select_todo_force_reprocess_multiple_in_docs_order():
    docs = _docs("a", "b", "c")
    state = _state(processed=["a", "b", "c"])
    todo = [d["doc_id"] for d in bf.select_todo(
        docs, state, force_reprocess_doc_ids={"c", "a"})]
    assert todo == ["a", "c"]  # both forced, returned in docs order
