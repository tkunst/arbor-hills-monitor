"""wds_archiver.py's activation gate: _should_run() is the exact pure predicate
that was MISSING when this file first shipped (it only checked OAuth config,
so merging to main would have started snapshotting nightly before wds.enabled
was ever set). Pure, no Sheets/Drive/network mocking needed."""
import wds_archiver as wa


def test_should_run_false_when_wds_disabled():
    ok, reason = wa._should_run({"wds": {"enabled": False}}, oauth_configured=True)
    assert ok is False
    assert "wds.enabled" in reason


def test_should_run_false_when_wds_key_absent():
    # cfg with no "wds" block at all must not be treated as enabled.
    ok, reason = wa._should_run({}, oauth_configured=True)
    assert ok is False
    assert "wds.enabled" in reason


def test_should_run_false_when_oauth_not_configured():
    ok, reason = wa._should_run({"wds": {"enabled": True}}, oauth_configured=False)
    assert ok is False
    assert "GOAUTH" in reason


def test_should_run_true_when_both_satisfied():
    ok, reason = wa._should_run({"wds": {"enabled": True}}, oauth_configured=True)
    assert ok is True
    assert reason == ""


def test_hash_is_stable_and_content_sensitive():
    a = wa._hash("<html>x</html>")
    b = wa._hash("<html>x</html>")
    c = wa._hash("<html>y</html>")
    assert a == b
    assert a != c
