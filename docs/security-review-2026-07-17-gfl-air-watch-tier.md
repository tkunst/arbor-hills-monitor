# Security Review: gfl-air-watch-tier

Date: 2026-07-17
Commit reviewed: `ac53d30` (`origin/main..HEAD`) — "feat(gfl-air): add CH4 40 ppm early-warning WATCH tier (coder:gfl-air-thresholds)"
Reviewer: Claude Code (`/security-review`), manual adversarial pass

No HIGH or MEDIUM confidence security findings identified in this change.

**Scope reviewed:** `config.yml` (the new `gfl_air.watch_thresholds` block), `gfl_air_client.py` (`classify_reading` watch tier), `gfl_air_watcher.py` (`station_snapshot` / `alert_lines` / `format_alert_body` / `_baseline` / `run`), `tests/test_gfl_air.py`.

**Methodology:** the change adds a lower-urgency "watch" severity tier (a reading `>=` a watch level but below the action level) to Stream E's own classifier, routes it to a lower-key email, and shows a `watch` status in the snapshot tab. It introduces **no new external inputs, sinks, secrets, or trust boundaries** — it re-partitions an existing numeric comparison and adds email/formatting branches over data the stream already handled. The change surface was traced end-to-end against the four attacker-relevant sink classes (email construction, Sheets writes, config parsing, control flow). *(Note: the `/security-review` skill's automated `git` bootstrap targeted the wrong repository — this Claude Code session is bound to a different project root, so its `origin/HEAD` probe failed there. The review below was performed by hand against the exact `ac53d30` diff.)*

**Key data-flow paths traced:**

- **Email subject (header-injection check).** `subject = f"[{tag}] Arbor Hills GFL perimeter air: {len(lines)} flagged reading(s)"`. `tag` is one of three hardcoded literals (`URGENT` / `GFL air watch` / `GFL air anomaly`) selected by the boolean `has_exceedance` / `has_watch`; `len(lines)` is an `int`. **No feed-derived string reaches the subject**, so there is no CR/LF header-injection vector. Unchanged from the pre-existing two-tag logic — the watch tier only adds a third fixed literal.

- **Email body (content-injection check).** Feed-derived values reach the body only as plain text via `alert_lines` → `format_alert_body`: the station name (`gc.station_of(r)`), the timestamp (`gc.reading_iso(r)`), and numeric readings formatted with `{val:g}` after `_as_float()` coercion. These are the **same fields, formatted the same way, as the existing `EXCEEDANCE`/`anomaly` lines** — the `watch` line is a structural clone. The email is sent as plain text (`ea.send_email(subject, body, cfg)`), so there is no HTML/script-injection sink. A malicious station name would require compromising GFL's first-party Barr Engineering ArcGIS FeatureServer, and even then is bounded to plain-text body content with no escalation.

- **Config parsing.** `watch_thresholds = cfg_gfl.get("watch_thresholds") or {}` and per-pollutant `watch.get(cfgkey)` → `float(wthr)` in the `>=` comparison. `config.yml` is **first-party, repo-controlled, version-pinned YAML**, not attacker-reachable input; a malformed value would raise a loud `ValueError` at classification time, not produce a silent-wrong or injectable state. No `yaml.load()` (the loader uses the existing safe path); no new deserialization.

- **New `watch` status string → Google Sheets snapshot tab.** `station_snapshot` now attaches a `"watch"` status that flows through `sw.write_gfl_air_summary`. It is a **fixed code-emitted literal**, not attacker-controlled, and joins the existing `ok`/`exceedance`/`sentinel`/`missing` enum written by the same path — no new formula/CSV-injection consideration beyond what Stream E's snapshot write already covers.

- **No new sinks introduced.** No `subprocess`, `eval`, `exec`, `pickle`, `os.system`, path construction, file writes, network calls, redirects, or `pull_request_target`-style workflow triggers are added by this diff. The workflow (`.github/workflows/gfl-air.yml`) is untouched.

**Availability / noise (not a security finding):** the watch tier can increase alert *email volume* if perimeter CH4 trends above 40 ppm. This is a spam/fatigue concern, not a vulnerability, and is bounded by the stream's existing controls — one batched email per daily poll and the `_MAX_ALERT_LINES = 20` display cap — plus the `watch_thresholds` value is operator-tunable and the block can be removed entirely to disable the tier.

**Cross-reference:** the companion `/code-review` (high effort) reported four **low-severity, non-security** findings (a missing `watch < action` config-sanity guard, a cosmetic sentinel-detail leak into a watch line when `alert_on_sentinel` is off, hardcoded pollutant keys in the watch-levels email line mirroring the existing action-levels line, and a missing ADR-014 addendum). None are security-relevant. 389 tests green.
