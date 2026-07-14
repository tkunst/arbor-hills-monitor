# Session 2026-07-14 — CivicClerk meeting-change watch (Stream F)

Trisha-directed (daytime, active session): a change-watch on specific public
meetings so she learns the moment one changes. Shipped as **Stream F**.

## Shipped (PR #13 → `main` `fc3efce`, rebased/linear, ADR 015, `enabled: true`)

A new `civicclerk_watcher.py` that snapshots a hand-picked list of Washtenaw County
CivicClerk meeting events and emails a change alert (to Trisha only) when one
changes — its title, date/time, publish status, cancellation, or its agenda/
minutes/other document set.

- **MMPC** — 3 events (4005/4006/4007 = Aug 19 / Sep 9 / Dec 9 2026), checked every
  run (2×/day). Oct/Nov have no portal page (committee not meeting), so nothing to
  watch there.
- **Washtenaw County BOC** — 15 events (Working Session cat 27 5:30 PM + Board
  Meeting cat 26 7:00 PM, Aug 5 → Dec 2 2026; Dec 2 is meeting-only). Checked
  weekly (Mon) + daily in the 3 days before each meeting. Every BOC date HAS a live
  portal page (unlike MMPC's Oct/Nov) — a point Trisha had inverted; corrected with
  the event ids.

### Design (see ADR 015)

- Structured OData API via a new additive `mmpc_client.fetch_event` (one event by
  id, same host as Mirror D) — not HTML hashing.
- Snapshot hashes title / date / publish-status / notice + the sorted document set;
  **excludes** volatile `sort` and the rotating storage `url` (the PFAS Sitecore
  cache-buster lesson, ADR 012). Verified a live re-fetch of event 4005 re-hashes
  identically.
- **Vanish vs. error:** a transient HTTP/JSON error → skip-and-warn (loud exit-1 if
  no baseline yet); a *successful* 200 returning no event → a real "cancelled /
  removed" alert. `fetch_event` returns `None` for the latter, distinct from raising.
- **Cadence is a pure per-event function** (`is_due_today`), not the cron: one
  workflow fires twice daily and each event decides if it's due. Unknown cadence
  fails safe to due.
- First run **baselines silently** — which is why `enabled: true` is safe (cannot
  flood). Recipients scoped via a new additive `email_alerts.send_email(recipients=)`
  override (Trisha only, not the shared advocacy list). State lives in the
  append-only `Meeting Watch` tab, not `_meta`.
- Alert-only — no Drive, no PDF download (MMPC PDFs are still mirrored by Mirror D;
  BOC PDFs are out of scope, just flagged when they appear).

### Verification

- 27 new tests (`tests/test_civicclerk_watcher.py`); full suite **291 green**;
  markdownlint clean; **verified live end-to-end** against the real CivicClerk API.
- Push-to-main CI all green (pytest / block-data-files / gitleaks / markdownlint /
  lychee — no flake). Privacy pre-push gate passed (no hard-block terms).
- First production run (`workflow_dispatch`) **baselined the 3 MMPC events** (hash
  matched the local live test); the 15 BOC events were correctly **not-due-today**
  (Tue, not the weekly Mon, and all meetings >3 days out).

### Files

New: `civicclerk_watcher.py`, `tests/test_civicclerk_watcher.py`,
`.github/workflows/meeting-watch.yml`, `docs/decisions/015-civicclerk-meeting-watch.md`.
Additive: `mmpc_client.fetch_event`, `sheet_writer` (Meeting Watch tab + 3 helpers),
`email_alerts.send_email(recipients=)`, `config.yml` (`civicclerk_watch:` block),
`README.md`, `CLAUDE.md`. No existing behaviour changed.

## Also done (outside this repo)

- The 15 BOC meetings + the 2 remaining MMPC meetings (Sep 9 / Dec 9, matching the
  Aug 19 "MUST GO" style, 10 AM, with alarms) placed on a personal calendar.
- The BOC dates + an Election-Day (Nov 3, polls 7 AM–8 PM) marker added to the
  private Lotext key-dates file.
- *(AppleScript gotchas for next time: `set de to ds` ALIASES the date object —
  build start/end independently; and Calendar writes sync per-op over iCloud, so
  batch runs must go in the background, not a 2-min foreground call.)*

## Follow-ups (non-blocking)

- BOC config entries have no expiry — prune after Dec 2026 (harmless until then;
  actually catches late minutes postings).
- Optional refinement offered to Trisha: baseline an event on first sight regardless
  of cadence, so BOC gets a day-1 baseline instead of on the next Monday (closes a
  small blind window). One-line change; not yet made.
