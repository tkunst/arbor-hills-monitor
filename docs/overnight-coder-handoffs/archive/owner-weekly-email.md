# Overnight-coder handoff: "For Trisha only" weekly email + same-day owner alert for unreadable enforcement notices

*Staged 2026-10-15 (Trisha-directed). SUPERSEDES `coder:hand-curated-unrouted-reminder`
(its spec is folded in as section B). Read `docs/overnight-coder.md` first. New behavior on
live daily paths, owner-only email, ships disabled: **DRAFT PR for Trisha's review, no
autonomous merge.** Recommended tier: **Sonnet**.*

## Why

Three things today reach no one, or reach the wrong audience:

1. **Unreadable filings vanish.** After `MAX_ERRORS_PER_DOC = 3` failed attempts (one per
   daily run), `poison_stub.stub_if_poisoned` writes a stub feed row (severity `skipped`) and
   the document is never retried. It is never classified, so it gets no urgent alert and no
   digest line. A locked or malformed Violation Notice would go unseen.
2. **Hand-curated rows can go unrouted** if a curation session skips dedupe-curate Step 5b
   (two YCUA PFOS NOVs sat unrouted in September 2026).
3. **Failed alert sends** are logged to Actions only.

The Sunday digest is ONE email to everyone, including the county commissioners
(`DIGEST_RECIPIENTS_EXTRA`), so a "private section" inside it is impossible. Trisha (2026-10-15):
these must go to her only.

## What to build

### A. Weekly "For Trisha only" email (Sunday, after the digest)

- Recipients: **owner only**, `email_alerts.load_owner_emails(cfg)`. Never
  `resolve_recipients`, never the alert, digest or watch lists, never a fallback to them.
  If the owner set is empty, send nothing and log that.
- Sent from the Sunday branch of `watcher.run`, right after the digest and the Upcoming
  section (same pattern as `send_upcoming_section`: its own email, its own recipients).
- Subject: `[Arbor Hills monitor] Weekly housekeeping (for Trisha only)`. Sections, each
  omitted when empty; no email at all when every section is empty:
  1. **Could not be read:** every document marked `skipped` since the last weekly email:
     nSITE title (`egle_title`), date filed, facility, native download link, the
     error class.
  2. **Hand-curated rows not yet routed** (section B).
  3. **Alerts that failed to send** this week: urgent alerts and the digest itself (where a
     send raised or no-op'd). This needs a small durable record; see "State".
- Body is plain text like the digest. No personal names beyond what the Sheet already shows.

### B. Hand-curated routing log + unrouted check (folded in from the superseded handoff)

1. `scripts/route_hand_curated.py` appends one row per routed document to a new tab
   `Hand-Curated Routing Log` (drive link, document_name, severity, routed_at), written
   BEFORE `write_meta` (Sheet row before state). Its urgent/digest behavior is unchanged.
2. The weekly email's section 2 = `Hand-Curated Files` rows with `added_at` on or after
   `owner_weekly.hand_curated_since` (default `2026-10-15`, so the backlog never floods it)
   that have no routing-log row, matched by Drive link (fallback: curated_filename).
   ANNOTATION rows (no new file) are excluded; derive that from the tab's columns
   (see `docs/hand-curated-intake-design.md`).
3. Include the exact `gh workflow run route-hand-curated.yml ...` command shape. It
   reminds and never routes, because routing an urgent row needs Trisha's OK.

### C. Same-day owner-only alert for an unreadable enforcement notice

- In `poison_stub.stub_if_poisoned`, when a document is stubbed, check its regulator title
  with `risk_register.enforcement_title_signal` (added by PR #142, `classifier-untrusted-input`;
  if #142 is not merged yet, rebase onto it or wait; do NOT duplicate the regex). If it
  matches, send an immediate email to the **owner only** (`load_owner_emails`):
  subject `[URGENT, Trisha only] Enforcement notice filed but unreadable: <title>`,
  body = title, date, facility, native download link, that the monitor could not read it
  after 3 attempts.
- Send AFTER the stub row and state are written (stub row, then state, then email); a failed
  send must never block stubbing. Record the attempt so the weekly email's section 3 can
  list a failed one.
- It goes to Trisha only, so it is NOT added to `pending_urgent_recap` (which the
  commissioners see on Sunday).

## State

The weekly email needs "since last weekly email" and "failed sends this week". Pick the
smallest durable option and justify it: a `last_owner_weekly` key in `_meta`, plus either an
append-only `Owner Notices` tab (preferred: also a permanent record) or a bounded `_meta` list.
State reads must use the strict reader (PR #135). The weekly email is best-effort: its
failure must not fail the watcher run's own work (isolate it, print class only).

## Config

New block `owner_weekly:` with `enabled` (ship **false**) and `hand_curated_since`. Section C
(the same-day enforcement alert) is gated on `owner_weekly.enforcement_alert_enabled`
(ship **false**) so Trisha can turn it on separately.

## Constraints

- Public Actions log: counts and classes only, never titles, links or addresses.
- Owner addresses come from config/secret as today; never hardcode one.
- `poison_stub.py` is shared by `watcher.py` and `backfill.py`; both paths get section C.

## Verification

Read-only against the live Sheet with all sends intercepted: print (locally, not in CI logs)
the weekly email it WOULD send today: the real skipped documents since 2026-10-01, any unrouted
hand-curated rows since the cutoff. Show Trisha that rendered email in the PR. Unit tests for:
owner-only recipients (and empty-owner no-send), each section's content and omission,
no email when all sections are empty, the routing-log row written by `route_hand_curated.py`,
annotation exclusion, the cutoff, section C firing only on an enforcement title, ordering
(stub, state, then email), and a failed send not blocking stubbing.
