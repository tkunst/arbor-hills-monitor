# ADR 027 — Weekly digest "Upcoming Activities" section (private Sheet)

Date: 2026-07-26
Status: accepted (degrades to no-op until `GSHEET_ID_PRIVATE` is set)
Builds on: ADR 006 (state in Sheet, service-account auth), ADR 015 (recipient
scoping precedent). Goal handed via the Lotext handoff
`handoffs/2026-07-13-arbor-hills-monitor-upcoming-activities-email.md`.

## Context

Trisha wanted an "Upcoming Activities (next 14 days)" calendar section at the top
of the weekly Sunday digest. The obvious source — a tab on the existing case-file
Sheet (`GSHEET_ID`) read via the no-auth gviz CSV endpoint — was killed by the
2026-07-19 adversarial review: that Sheet is **shared with the operator (GFL)**,
Google Sheets has no per-tab visibility (only whole-spreadsheet sharing), and the
intended source (key-dates Section A) carries strategy-flavored rows (siting-
criteria drafting, outreach, leverage items). A synced tab would leak them.

Trisha's 2026-07-23 ruling: make the source genuinely private rather than trying
to keep Section A clean forever. Since the service account has no Drive quota and
cannot create its own Sheet, "private" means a **second, separate spreadsheet**
that Trisha owns and shares only with the service account.

## Decision

- **New `upcoming.py`.** `render_upcoming(entries, today, horizon_days=14)` is a
  **pure** function (filter to the window, sort, render; `""` when empty so the
  caller omits the section; a ranged entry shows when its span intersects the
  window). `fetch_upcoming(service, sheet_id, tab)` reads the private tab via the
  **authenticated Sheets API** (the service-account path — NOT gviz, which can't
  read a private Sheet) and never raises on a missing tab (returns `[]`).
  `ensure_upcoming_tab` idempotently creates the tab + header.
- **`email_alerts`.** `format_digest_body(items, upcoming_block="")` /
  `send_digest(items, cfg, upcoming_block="")` prepend the block, including the
  zero-new-documents-but-upcoming-present case (calendar shows above "No new
  documents").
- **`watcher.py` Sunday block.** Builds the block **best-effort** (a missing
  `GSHEET_ID_PRIVATE` or unreachable Sheet degrades to no section, never a crash)
  and **changes the send condition** to fire on new documents **OR** any upcoming
  date — so the calendar surfaces even on an otherwise-quiet week (a deliberate
  change from the old "no email on a no-docs week").
- **`GSHEET_ID_PRIVATE`** wired into `daily.yml`'s watcher env; documented in
  `.env.example`, `README`, and `CLAUDE.md` (a hard invariant: that Sheet is never
  shared with the operator, unlike the operator-visible `GSHEET_ID`).

## Consequences

- The section is additive and self-protecting: with no secret set (the shipped
  default) it is a silent no-op, so this merges without changing behavior until
  activated.
- The Sunday digest now also fires on a zero-new-documents week when upcoming
  dates exist — intended (surface the calendar even on quiet weeks).
- **Activation (Trisha-only):** create the private Sheet, share it with the
  service account (Editor), add `GSHEET_ID_PRIVATE` as a GitHub secret, and
  populate the "Upcoming" tab (`date`, optional `end_date`, `title`) from
  key-dates Section A. The prerequisite Sheet + secret were reported done
  (`9e9adb9`); the tab-populate step remains Trisha's (the monitor repo does not
  read the Lotext master).

## Alternatives considered

- **A tab on the public case-file Sheet via gviz.** The original design; rejected
  by the 2026-07-19 review as an operator-visible leak of strategy dates.
- **Have the code seed the tab from key-dates Section A.** Rejected: that master
  lives in a different (Lotext) repo; the monitor must not reach across into it.
  The code guarantees the tab/header exist; Trisha populates the rows.
