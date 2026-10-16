# ADR 070 — An owner-only channel: weekly housekeeping email + same-day owner alerts

*2026-10-15, coder:owner-weekly-email (handoff: `docs/overnight-coder-handoffs/owner-weekly-email.md`).
Ships disabled.*

## Context

Some things the monitor knows reach no one, or reach the wrong audience:

- A filing that fails `MAX_ERRORS_PER_DOC` times is stubbed `skipped` and never
  classified. It gets no alert and no digest line, even when it is a Violation Notice.
- Hand-curated rows can go unrouted.
- A failed alert send shows up only in the Actions log.
- Since ADR 067's 2026-10-15 amendment, values above the plausibility ceiling go to
  the private Parser Review Flags tab, where nothing reads them yet.

The Sunday digest goes to everyone, including county commissioners, so a "private
section" inside it is impossible.

## Decision

`owner_notices.py` sends to `email_alerts.load_owner_emails(cfg)` ONLY. It never uses
`resolve_recipients`, never any list, and never falls back to one. An empty owner set
means no send.

- **Weekly email** (`owner_weekly.enabled`, Sunday, after the digest and the Upcoming
  section). Each of the four sections is omitted when it is empty, and no email is sent
  when all four are empty:
  1. documents stubbed `skipped` since the last weekly email;
  2. Hand-Curated Files rows added on or after `owner_weekly.hand_curated_since` that
     have no row in the new **Hand-Curated Routing Log** tab. Matching is by Drive link,
     falling back to filename/title. ANNOTATION-mode rows are excluded. The section
     includes the `gh workflow run route-hand-curated.yml` command shape: it reminds,
     it never routes;
  3. alert sends that raised or were not handed to SMTP;
  4. parser review flags.
- **Same-day owner alerts** (`owner_weekly.enforcement_alert_enabled`):
  - In `poison_stub.stub_if_poisoned`, after the stub row and then the `skipped`
    state are written, a document whose regulator title passes
    `risk_register.enforcement_title_signal` gets
    `[URGENT, Trisha only] Enforcement notice filed but unreadable: <title>`.
  - Under the same gate, a document the WATCHER parses whose parse produced an
    above-ceiling temperature flag gets
    `[Trisha only] Needs review: value above 250F in <doc>`. Backfill does not send
    this one: it re-reads history and never alerts on it. Its flags still reach the
    weekly email.
  - Neither is added to `pending_urgent_recap`.
  - A failed send never blocks stubbing or the document.

## State

- **`_meta.last_owner_weekly`**: the weekly window. It advances only after a
  successful send, or after a week with nothing to send. A failed send keeps the window
  and the email is retried next Sunday.
- **Owner Notices tab, append-only, on the PRIVATE Sheet.** It records each owner-alert
  attempt and each failed send, and is the permanent record. It goes through the same
  private-Sheet guard as the review flags: `GSHEET_ID_PRIVATE` must be set, must differ
  from `GSHEET_ID`, and must have no public case-file tabs.
- **Hand-Curated Routing Log, on the PRIVATE Sheet.** `scripts/route_hand_curated.py`
  writes it before `write_meta`, best-effort, so a failed log write can never cost the
  `_meta` write after an urgent email has gone out. `route-hand-curated.yml` now passes
  `GSHEET_ID_PRIVATE`. A row whose log write failed shows as unrouted in the weekly
  email: a reminder, never a lost alert. Severity triage stays off the
  operator-visible Sheet.
- **While both switches are off,** `record_notice` is a no-op: no new tab and no extra
  API calls (ships disabled).

Every read the weekly email depends on is strict (`_tab_rows_strict` /
`sw.state_events_since`). A failed read skips the weekly; it never produces a false
"all clear". The weekly runs isolated inside the watcher, so it can never fail the run,
and only the exception class is logged.

## Consequences

- `send_email(log_subject=False)` keeps owner-only subjects, which carry document
  titles, out of the public Actions log.
- `send_digest` now returns `send_email`'s bool, so a skipped digest can be recorded.
- The `_state` skipped payload now also carries `egle_title`, `facility` and
  `error_class`.
- `backfill.yml` gains `MONITOR_OWNER_EMAILS` and `UNSUBSCRIBED_EMAILS`.
