# Security Review: stream-d-mmpc-archive

Date: 2026-07-11
Branch reviewed: `stream-d-mmpc-archive` vs `main` (PR #2)
Reviewer: Claude Code (`/security-review`), multi-agent adversarial pass

No HIGH or MEDIUM confidence security findings identified in this PR.

**Scope reviewed:** `.github/workflows/mmpc-archive.yml`, `archive_client.py`, `archiver.py`, `config.yml`, `mmpc_archiver.py`, `mmpc_client.py`, `sheet_writer.py`, `wds_archiver.py`.

**Methodology:** a finder sub-agent produced 5 candidate findings — CSV/formula injection into Google Sheets via CivicClerk's `name`/`type` fields; SSRF via `@odata.nextLink` pagination plus `requests`'s default redirect-following; path traversal via `file_id` flowing into `os.path.join`; Drive query-injection via incomplete backslash escaping in `find_in_folder`'s `q=` filter; and unencoded `file_id` interpolated into the file-download URL. Each candidate was independently re-verified against the code on disk by a separate adversarial sub-agent applying a fixed false-positive-filtering rubric. All 5 were rejected as false positives / below the reporting threshold (confidence 1-3 out of 10).

Every candidate shared the same root defeater: exploitation requires CivicClerk's own trusted, hardcoded, first-party government API (`washtenawcomi.api.civicclerk.com`) to emit malicious or malformed data it has never been observed to emit — in live verification during design and in every test fixture, `file_id` is consistently a JSON integer, and `name`/`type` are Washtenaw County staff-curated meeting-document metadata, not public-submission input. None of the candidates identified an attacker-reachable input path within this app's actual trust boundary.

**Key data-flow paths traced** (CivicClerk API content → sinks):

- `file_id` / `type` / `name` → Google Sheets writes (`append_mmpc_archive_row`): `append_rows` uses `valueInputOption="RAW"`, so the Sheets API stores values as literal text, never formula-evaluated in-app. A CSV-export-then-open-in-Excel scenario was considered but requires an out-of-band workflow this app doesn't perform, and the underlying data isn't attacker-reachable in the first place.
- `file_id` → local temp-file path (`os.path.join(tmp, f"{file_id}.pdf")`) and Drive filename/query (`find_in_folder`'s `q=` filter): both traced in detail; exploitable in principle only if CivicClerk's API returned a non-integer `file_id`, which it has never done, and impact would be bounded to an ephemeral, single-job GitHub Actions runner with no PII or durable secrets.
- `@odata.nextLink` pagination and `requests`'s default redirect-following: bounded to the fixed, hardcoded `_BASE` host unless CivicClerk itself is compromised or MITM'd — not attacker-controlled input, so this doesn't clear the SSRF bar (host/protocol control required).
- No `subprocess`, `eval`, `exec`, `pickle`, or `yaml.load()` usage introduced; no XXE or command-injection sinks found.
- `.github/workflows/mmpc-archive.yml`: schedule/`workflow_dispatch`-only (no `pull_request_target` or untrusted-input triggers); secrets are passed via `env:` and referenced as shell variables, never interpolated directly into `run:` script text.

One code-quality issue (not a security finding) was caught and fixed during this review: `mmpc_archiver.py`'s per-file exception handler originally only caught `mmpc_client.MMPCFetchError`, so a Drive/Sheets API error during upload would abort the entire batch instead of skipping that file and continuing — unlike the sibling `archiver.py`/`wds_archiver.py`, which both catch `Exception` broadly around the identical download→upload→append sequence. Fixed to `except Exception` in commit `b9bc905`.
