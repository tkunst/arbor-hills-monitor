"""coder:pages-artifact-cleanup: pages.yml deletes its github-pages artifacts
after a successful deploy, and the daily check warns the owner before Actions
artifact storage reaches the free plan's 0.5 GB (the 2026-11-06 account lock)."""
import importlib.util
import os

import yaml

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MB = 1024 * 1024


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
    assert m.main() == 0
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


def test_pages_cleanup_step_runs_after_deploy_and_targets_only_github_pages():
    wf = _wf("pages.yml")
    perms = wf["permissions"]
    assert perms["actions"] == "write" and perms["pages"] == "write" and perms["contents"] == "read"
    steps = wf["jobs"]["deploy"]["steps"]
    deploy = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/deploy-pages"))
    clean = next(i for i, s in enumerate(steps) if "artifacts" in s.get("run", "") and "DELETE" in s.get("run", ""))
    assert clean > deploy
    step = steps[clean]
    assert step["if"].startswith("success()") and step.get("continue-on-error") is True
    assert 'select(.name == "github-pages"' in step["run"] and "name=github-pages" in step["run"]


def test_daily_runs_the_storage_check_with_read_only_actions_permission():
    wf = _wf("daily.yml")
    assert wf["permissions"]["actions"] == "read"
    step = next(s for s in wf["jobs"]["watch"]["steps"] if "artifact_storage_check.py" in s.get("run", ""))
    assert step["if"] == "always()" and step["continue-on-error"] is True
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
