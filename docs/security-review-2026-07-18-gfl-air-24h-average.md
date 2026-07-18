# Security review — GFL air H2S 24-hr average (coder:gfl-air-24h-average)

*PR #19 (`gfl-air-24h-average`), reviewed 2026-07-18 at final state `c3f1ae7`.*
**Result: zero medium/high findings — no exploitable vulnerability introduced.**

`/security-review` was run with an independent identification sub-agent tracing every
data flow newly introduced by the PR. Nothing reached the reporting threshold.

## What the change adds (attack-surface delta)

- `gfl_air_client.fetch_h2s_window_avg` — one server-side ArcGIS grouped-statistics
  GET query returning a per-station 24-hr average H2S value + count.
- `gfl_air_watcher.h2s_average_alert_lines` + wiring — turns that average into an
  exceedance alert line in the same-day email.

## Flows traced

1. **ArcGIS `where` string (query injection).** Built as
   `f"Date > date '{cutoff}'"` + `f" AND H2S <> {float(sentinel):g}"`. Both
   interpolated values are trusted or computed: `cutoff` is a `strftime` UTC
   timestamp (only `YYYY-MM-DD HH:MM:SS`, no quotes/metacharacters possible);
   `sentinel` is a config number coerced through `float(...):g`. The semi-untrusted
   feed fields (`LocName`, `avgH2S`, `n`) are the query **response**, never fed back
   into a `where`. `groupByFieldsForStatistics` is the literal `"LocName"`. No
   feed-controlled input reaches the query. **Not exploitable.**

2. **Feed JSON → email body / logs.** The feed's `LocName` flows into the alert
   lines → `format_alert_body` → `email_alerts.send_email` (`set_content`, a
   **plain-text** body). This is the **same sink and same data class** the
   pre-existing instantaneous `alert_lines` already used — not new attack surface.
   It is a body payload, not a header (the subject is a fixed tag + an int), so no
   header injection; no HTML/template rendering, so no XSS / template injection;
   `avg`/`n` are numeric-formatted. **Not exploitable, and not new.**

3. **Secrets / deserialization / path / SSRF / command exec.** None introduced.
   `fetch_h2s_window_avg` reaches the same host/protocol (config `service_url`) as
   every existing call — no feed control over host/protocol. Response parsed with
   `json.loads` only (no pickle/yaml/eval). No filesystem paths, no new secret
   handling, no subprocess.

## Out of scope (noted, not findings)

- An unsanitized `LocName` in a `print()` log line (`H2S average NOTE: ...`) is
  log-spoofing, an explicitly excluded class.

## Convergence

`/review` surfaced one LOW finding (a hardcoded "72 ppb" in the averaged-alert email
clarifier), fixed in `c3f1ae7` — a 3-line cosmetic email-string change with no logic
or data-flow change. This security pass was run on that final state, so the fix is
covered. No new findings; loop converged in round 1.
