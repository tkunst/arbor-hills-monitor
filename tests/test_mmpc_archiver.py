"""mmpc_archiver.py's activation gate: _should_run() is the same class of
predicate that was MISSING from wds_archiver.py's first draft (ADR 009's
Addendum) — checked here test-first instead of by inspection. Pure, no
Sheets/Drive/network mocking needed."""
import mmpc_archiver as ma


def test_should_run_false_when_mmpc_archive_disabled():
    ok, reason = ma._should_run({"mmpc_archive": {"enabled": False}}, oauth_configured=True)
    assert ok is False
    assert "mmpc_archive.enabled" in reason


def test_should_run_false_when_mmpc_archive_key_absent():
    # cfg with no "mmpc_archive" block at all must not be treated as enabled.
    ok, reason = ma._should_run({}, oauth_configured=True)
    assert ok is False
    assert "mmpc_archive.enabled" in reason


def test_should_run_false_when_oauth_not_configured():
    ok, reason = ma._should_run({"mmpc_archive": {"enabled": True}}, oauth_configured=False)
    assert ok is False
    assert "GOAUTH" in reason


def test_should_run_true_when_both_satisfied():
    ok, reason = ma._should_run({"mmpc_archive": {"enabled": True}}, oauth_configured=True)
    assert ok is True
    assert reason == ""
