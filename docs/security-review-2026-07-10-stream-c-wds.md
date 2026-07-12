# Security Review: stream-c-wds

Date: 2026-07-10
Branch reviewed: `stream-c-wds` vs `main`
Reviewer: Claude Code (`/security-review`), multi-agent adversarial pass

No HIGH or MEDIUM confidence security findings identified in this PR.

**Scope reviewed:** `.github/workflows/wds-archive.yml`, `archive_client.py`, `config.yml`, `scripts/dump_wds_historical.py`, `scripts/seed_wds_state.py`, `sheet_writer.py`, `watcher.py`, `wds_archiver.py`, `wds_client.py`, `wds_watcher.py`, plus `email_alerts.py` as a downstream sink.

**Key data-flow paths traced** (untrusted WDS portal content → sinks):
- WDS-scraped field values → Google Sheets writes: all call sites use `valueInputOption="RAW"`, which the Sheets API stores as literal text (never formula-evaluated) — no formula-injection path.
- WDS-scraped field values → email alert subjects/bodies: tested CRLF header-injection payloads against the `email.message.EmailMessage` construction used here — it raises `ValueError` on embedded CRLF rather than emitting forged headers, and this is caught by the existing exception handling in `wds_watcher.check_wds`.
- Link construction (`wds_link()`): built from `config.yml`'s trusted `site_id`, never from scraped content — no injection surface.
- No `subprocess`, `eval`, `exec`, `pickle`, or `yaml.load()` usage introduced; no XXE, path traversal, or command-injection sinks found.
- `.github/workflows/wds-archive.yml`: schedule/`workflow_dispatch`-only (no `pull_request_target` or untrusted-input triggers); secrets are passed via `env:` and referenced as shell variables, never interpolated directly into `run:` script text.

One item was considered and explicitly not reported: `wds_archiver.py` uploads raw WDS page HTML to Drive with `mimetype="text/html"`. This is a theoretical stored-content concern only if the legacy government portal itself ever serves unescaped/unencoded markup in scraped fields, which is unconfirmed (out of scope to verify against the live third-party site) and falls below the >80% exploitability confidence bar required for this report.
