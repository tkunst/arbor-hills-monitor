#!/usr/bin/env python3
"""scripts/artifact_storage_check.py -- early warning before GitHub Actions
artifact storage locks the account again (coder:pages-artifact-cleanup).

The account is on the free plan with no payment method: its Actions artifact
storage allowance is 0.5 GB, and once it is used up GitHub blocks every
workflow ("account is locked due to a billing issue"). That happened on
2026-11-06 after eight ~67 MB `github-pages` artifacts piled up. pages.yml now
deletes them after each deploy; this check is the backstop: it sums the
repository's unexpired artifact bytes and sends the owner-only alert
(ADR 070, owner_notices.send_owner_email) when they pass ALERT_MB (70% of
the allowance).

Run from daily.yml with GH_TOKEN (actions: read) and GITHUB_REPOSITORY.
Public-log rule: only counts and sizes are printed, never an artifact URL,
id or signed link. Never fails the job: an API error prints its class and
exits 0 (the daily watcher must not depend on this).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ALLOWANCE_MB = 500
ALERT_MB = 350
MB = 1024 * 1024
_API = "https://api.github.com"


def unexpired_total(artifacts: list[dict]) -> tuple[int, int, dict]:
    """(count, total bytes, {name: bytes}) over the artifacts not yet expired. Pure."""
    by_name: dict[str, int] = {}
    n = total = 0
    for a in artifacts:
        if a.get("expired"):
            continue
        size = int(a.get("size_in_bytes") or 0)
        n += 1
        total += size
        by_name[a.get("name") or "?"] = by_name.get(a.get("name") or "?", 0) + size
    return n, total, by_name


def over_threshold(total_bytes: int, alert_mb: float = ALERT_MB) -> bool:
    return total_bytes > alert_mb * MB


def alert_email(n: int, total_bytes: int, by_name: dict, repo: str) -> tuple[str, str]:
    top = sorted(by_name.items(), key=lambda kv: -kv[1])[:5]
    lines = [f"  {name}: {size / MB:.1f} MB" for name, size in top]
    subject = (f"[owner] Actions artifact storage at {total_bytes / MB:.0f} MB of "
               f"{ALLOWANCE_MB} MB")
    body = "\n".join([
        f"GitHub Actions artifact storage for {repo} is at {total_bytes / MB:.0f} MB "
        f"({n} unexpired artifact(s)). The free plan allows {ALLOWANCE_MB} MB; past that, "
        "GitHub locks every workflow until storage drops (as on 2026-11-06).",
        "",
        "Largest by name:",
        *lines,
        "",
        "To free space now: delete unexpired github-pages artifacts (only used during a "
        "deploy), e.g.",
        f"  gh api 'repos/{repo}/actions/artifacts?name=github-pages' --jq '.artifacts[].id'",
        "then gh api -X DELETE repos/<repo>/actions/artifacts/<id> for each.",
    ])
    return subject, body


def fetch_artifacts(repo: str, token: str, timeout: int = 30) -> list[dict]:
    """Every artifact of the repo (all pages)."""
    out: list[dict] = []
    page = 1
    while True:
        req = urllib.request.Request(
            f"{_API}/repos/{repo}/actions/artifacts?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — fixed https API host
            data = json.load(resp)
        batch = data.get("artifacts", [])
        out.extend(batch)
        if len(batch) < 100 or len(out) >= int(data.get("total_count", 0)):
            return out
        page += 1


def run(artifacts: list[dict], repo: str, cfg: dict, send=None) -> str:
    """Prints the summary; sends the owner alert when over the threshold.
    Returns "ok", "alerted" or "alert_not_sent"."""
    n, total, by_name = unexpired_total(artifacts)
    print(f"artifact storage: {n} unexpired artifact(s), {total / MB:.1f} MB "
          f"(alert above {ALERT_MB} MB, allowance {ALLOWANCE_MB} MB)")
    if not over_threshold(total):
        return "ok"
    if send is None:
        import owner_notices
        send = owner_notices.send_owner_email
    subject, body = alert_email(n, total, by_name, repo)
    try:
        sent = send(subject, body, cfg)
    except Exception as e:  # noqa: BLE001 — never fail the daily job
        print(f"artifact storage: owner alert failed ({type(e).__name__})")
        return "alert_not_sent"
    print("artifact storage: owner alert sent" if sent else "artifact storage: no owner address")
    return "alerted" if sent else "alert_not_sent"


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    if not repo or not token:
        print("artifact storage: GITHUB_REPOSITORY / GH_TOKEN not set; check skipped")
        return 0
    try:
        artifacts = fetch_artifacts(repo, token)
    except Exception as e:  # noqa: BLE001 — class only (public log)
        print(f"artifact storage: GitHub API read failed ({type(e).__name__}); check skipped")
        return 0
    from config_loader import load_config
    run(artifacts, repo, load_config())
    return 0


if __name__ == "__main__":
    sys.exit(main())
