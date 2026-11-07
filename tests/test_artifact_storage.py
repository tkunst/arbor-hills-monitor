"""coder:pages-artifact-cleanup: pages.yml deletes its github-pages artifacts
once the deploy job is over (success, failure or cancel), and the daily check warns the owner before Actions
artifact storage reaches the free plan's 0.5 GB (the 2026-11-06 account lock)."""
import importlib.util
import os

import yaml

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MB = 1000 * 1000


def _mod():
    spec = importlib.util.spec_from_file_location(
        "artifact_storage_check", os.path.join(_REPO, "scripts", "artifact_storage_check.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _art(name, mb, expired=False):
    return {"name": name, "size_in_bytes": int(mb * MB), "expired": expired}


def _wf(name):
    with open(os.path.join(_REPO, ".github", "workflows", name)) as f:
        return yaml.safe_load(f)


def test_total_counts_only_unexpired_artifacts():
    m = _mod()
    n, total, by_name = m.unexpired_total([_art("github-pages", 64), _art("github-pages", 64, expired=True),
                                           _art("bandit-report", 0.02)])
    assert n == 2 and round(total / MB, 2) == 64.02
    assert set(by_name) == {"github-pages", "bandit-report"}


def test_threshold_is_350_mb_and_strict():
    m = _mod()
    assert m.ALERT_MB == 350 and m.ALLOWANCE_MB == 500
    assert not m.over_threshold(350 * MB) and m.over_threshold(350 * MB + 1)


def test_over_threshold_sends_the_owner_alert_with_sizes_only(capsys):
    m = _mod()
    sent = []
    arts = [_art("github-pages", 64) for _ in range(6)]
    status = m.run(arts, "tkunst/arbor-hills-monitor", {},
                   send=lambda subj, body, cfg: sent.append((subj, body)) or True)
    assert status == "alerted" and len(sent) == 1
    subj, body = sent[0]
    assert "384 MB" in subj and "github-pages: 384.0 MB" in body
    out = capsys.readouterr().out
    assert "6 unexpired" in out and "http" not in out          # public log: counts/sizes only


def test_under_threshold_sends_nothing():
    m = _mod()
    status = m.run([_art("github-pages", 64)], "r", {},
                   send=lambda *a: (_ for _ in ()).throw(AssertionError("no send")))
    assert status == "ok"


def test_a_failed_send_never_fails_the_job():
    m = _mod()
    status = m.run([_art("x", 400)], "r", {}, send=lambda *a: (_ for _ in ()).throw(OSError("smtp")))
    assert status == "alert_not_sent"


def test_main_without_token_or_on_api_error_exits_zero(monkeypatch, capsys):
    m = _mod()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(m, "fetch_artifacts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert m.main() == 0 and "GH_TOKEN not set; check skipped" in capsys.readouterr().out
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(m, "fetch_artifacts", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert m.main() == 0 and "OSError" in capsys.readouterr().out


def test_fetch_artifacts_pages_through(monkeypatch):
    import io
    import json
    m = _mod()
    pages = {1: {"total_count": 150, "artifacts": [_art("a", 1)] * 100},
             2: {"total_count": 150, "artifacts": [_art("a", 1)] * 50}}

    def fake_urlopen(req, timeout=0):
        page = int(req.full_url.split("page=")[-1])
        assert req.full_url.startswith("https://api.github.com/repos/o/r/actions/artifacts")
        return io.BytesIO(json.dumps(pages[page]).encode())
    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    assert len(m.fetch_artifacts("o/r", "t")) == 150


def test_pages_cleanup_is_its_own_job_after_deploy_and_targets_only_github_pages():
    wf = _wf("pages.yml")
    assert "actions" not in wf["permissions"]                  # the deploy job keeps its old token scope
    jobs = wf["jobs"]
    assert any(str(s.get("uses", "")).startswith("actions/deploy-pages") for s in jobs["deploy"]["steps"])
    clean = jobs["cleanup"]
    assert clean["needs"] == "deploy" and clean["if"] == "${{ always() }}"   # after a failed/cancelled deploy too
    assert clean["permissions"] == {"actions": "write"}
    run = clean["steps"][0]["run"]
    # the list is filtered to the name twice (query + jq) and only those ids are deleted
    assert "actions/artifacts?name=github-pages" in run
    assert '--jq \'.artifacts[] | select(.name == "github-pages" and .expired == false) | .id\'' in run
    assert run.count("gh api -X DELETE") == 1 and 'for id in $ids' in run
    assert "failed=$((failed+1))" in run and "exit 1" in run       # a failed delete is visible
    # (the 404 / expired / API-error branches are exercised for real in
    # test_cleanup_script_behaviour_under_bash_e below)
    for line in run.splitlines():                               # gh error text (ids) never reaches the log
        if "gh api -X DELETE" in line:
            assert ">/dev/null 2>&1" in line
        elif "state=$(gh api" in line:
            assert "2>&1)" in line
    assert "checkout" not in str(clean["steps"])             # no repo code runs with actions: write


def test_fetch_stops_on_total_count_and_on_an_empty_page(monkeypatch):
    import io
    import json
    m = _mod()
    calls = []

    def serve(pages):
        def fake_urlopen(req, timeout=0):
            page = int(req.full_url.split("page=")[-1])
            calls.append(page)
            return io.BytesIO(json.dumps(pages[page]).encode())
        return fake_urlopen
    monkeypatch.setattr(m.urllib.request, "urlopen",
                        serve({1: {"total_count": 100, "artifacts": [_art("a", 1)] * 100}}))
    assert len(m.fetch_artifacts("o/r", "t")) == 100 and calls == [1]
    calls.clear()
    monkeypatch.setattr(m.urllib.request, "urlopen", serve({1: {"total_count": 0, "artifacts": []}}))
    assert m.fetch_artifacts("o/r", "t") == [] and calls == [1]
    calls.clear()                                    # no total_count: a short page ends it
    monkeypatch.setattr(m.urllib.request, "urlopen", serve({1: {"artifacts": [_art("a", 1)] * 3}}))
    assert len(m.fetch_artifacts("o/r", "t")) == 3 and calls == [1]


def test_config_or_import_failure_still_exits_zero(monkeypatch, capsys):
    m = _mod()
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(m, "fetch_artifacts", lambda *a, **k: [_art("github-pages", 400)])
    import config_loader
    monkeypatch.setattr(config_loader, "load_config", lambda *a, **k: (_ for _ in ()).throw(ValueError("yaml")))
    import builtins
    real_import = builtins.__import__

    def no_owner_notices(name, *a, **k):
        if name == "owner_notices":
            raise ImportError("broken install")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_owner_notices)
    assert m.main() == 0
    out = capsys.readouterr().out
    assert "config unreadable (ValueError)" in out and "owner alert failed (ImportError)" in out


def test_daily_runs_the_storage_check_with_read_only_actions_permission():
    wf = _wf("daily.yml")
    assert wf["permissions"]["actions"] == "read"
    step = next(s for s in wf["jobs"]["watch"]["steps"] if "artifact_storage_check.py" in s.get("run", ""))
    assert step["continue-on-error"] is True and step["if"] == "${{ !cancelled() }}"
    for var in ("MONITOR_OWNER_EMAILS", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        assert step["env"][var] == "${{ secrets.%s }}" % var    # the alert can actually send
    steps = wf["jobs"]["watch"]["steps"]
    watcher = next(i for i, s in enumerate(steps) if s.get("run", "").strip() == "python watcher.py")
    assert steps.index(step) < watcher            # a watcher timeout can't skip the check
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


def _run_cleanup(tmp_path, delete_fails, get_result):
    """Run pages.yml's cleanup script under bash -e with a stub `gh` on PATH.
    Artifacts 11, 22, 33 are listed; DELETE fails for ids in delete_fails; the
    follow-up GET for those prints/returns get_result = (stdout, stderr, rc)."""
    import shutil
    import subprocess
    if not shutil.which("bash"):
        import pytest
        pytest.skip("bash not available")
    run = _wf("pages.yml")["jobs"]["cleanup"]["steps"][0]["run"]
    out, err, rc = get_result
    stub = tmp_path / "gh"
    stub.write_text(f"""#!/bin/bash
if [ "$2" = "-X" ]; then
  case "$4" in {"|".join(f"*/{i}" for i in delete_fails) or "NONE"}) echo "gh: boom id $4" >&2; exit 1;; esac
  exit 0
fi
case "$2" in
  *"?name="*) printf '11\n22\n33\n'; exit 0;;
  *) printf '%s' '{out}'; printf '%s' '{err}' >&2; exit {rc};;
esac
""")
    stub.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "REPO": "o/r"}
    p = subprocess.run(["bash", "-e", "-c", run], env=env, capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout + p.stderr


def test_cleanup_script_behaviour_under_bash_e(tmp_path):
    rc, out = _run_cleanup(tmp_path, [], ("", "", 0))
    assert rc == 0 and "deleted 3 github-pages artifact(s); 0 delete(s) failed" in out
    rc, out = _run_cleanup(tmp_path, [22], ("", "gh: Not Found (HTTP 404)", 1))      # gone meanwhile
    assert rc == 0 and "deleted 3" in out and "0 delete(s) failed" in out
    rc, out = _run_cleanup(tmp_path, [22], ("true", "", 0))                          # expired meanwhile
    assert rc == 0 and "deleted 2" in out and "0 delete(s) failed" in out
    rc, out = _run_cleanup(tmp_path, [22], ("false", "", 0))                         # still there
    assert rc == 1 and "1 delete(s) failed" in out and "::error::" in out
    rc, out = _run_cleanup(tmp_path, [22], ("", "gh: Server Error (HTTP 502)", 1))   # API down: not hidden
    assert rc == 1 and "1 delete(s) failed" in out
    assert "boom" not in out and "Server Error" not in out                           # gh errors never logged
