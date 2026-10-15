# Overnight-coder handoff: daily reminder for hand-curated rows that were never routed

*Staged 2026-10-15 (Trisha-directed). Read `docs/overnight-coder.md` first. New behavior
on a live daily path, owner-only email, no Sheet writes: **DRAFT PR for Trisha's review,
no autonomous merge.** Recommended tier: **Sonnet**.*

## Why

Hand-curated documents (Lotext `dedupe-curate` skill -> `Hand-Curated Files` tab) never pass
through the classifier, so they reach the same-day urgent email or the Sunday digest only if
the curation session dispatches `route-hand-curated.yml` (`scripts/route_hand_curated.py` ->
`email_alerts.route_hand_curated_urgent_or_digest`). On 2026-10-15 the skill gained a
mandatory Step 5b that always routes. This is the safety net in code in case a session still
skips it: two YCUA PFOS Notices of Violation sat unrouted in September 2026.

## What to build

A small daily check (a step in `daily.yml` after the watcher, or its own module called from
`watcher.run`, your call; justify it) that:

1. Reads `Hand-Curated Files` (read-only) and finds rows added (`added_at`) on or after a
   config cutoff (`hand_curated_reminder.since`, default `2026-10-15`, so the historical
   backlog never floods the reminder) that were **never routed**.
2. "Routed" needs a durable record. Spike the options and pick one:
   - `route_hand_curated.py` appends a row per routed document to a new small tab
     (`Hand-Curated Routing Log`: doc link, document_name, severity, routed_at), Sheet-row
     before email, crash-safe; the reminder reads it. **Preferred.** It also gives a
     permanent record of every routing decision.
   - Matching against `_meta.pending_digest` / `pending_urgent_recap` is NOT enough (cleared
     every Sunday).
   Match rows by the Drive link (unique per curated file), falling back to curated_filename.
   ANNOTATION rows (no new file) are excluded; work out how to tell them apart from the
   tab's columns (see `docs/hand-curated-intake-design.md`).
3. If any unrouted rows exist, send ONE owner-only email (`ea.load_owner_emails`, never the
   alert or digest lists): subject `[Hand-curated] N document(s) not yet routed`, body = name,
   date, Drive link per row, plus the exact `gh workflow run route-hand-curated.yml ...`
   command shape. It **reminds**, it never routes: routing an urgent row needs Trisha's OK.
4. Do not re-send the same reminder every day forever: remind on the first day a row is
   unrouted and then at most weekly (state in the routing-log tab or `_meta`), until it is
   routed.
5. Gated on a new config flag `hand_curated_reminder.enabled` (ship `false`).

## Constraints

- `route_hand_curated.py` writing the log row must not change its existing urgent/digest
  behavior or ordering; the log row is written before `write_meta`, matching the repo's
  Sheet-row-before-state invariant.
- Public Actions log: counts only, no titles or links printed.
- The reminder must not fail the daily run if the Sheet read fails: per PR #135's strict-read
  rule, a failed read of the routing log raises inside this step, the step fails loudly on
  its own, and the watcher's own work is unaffected (isolate it).

## Verification

Read-only run against the live Sheet with email intercepted: list the rows it WOULD remind
about, and show Trisha that list in the PR. Unit tests for: routed vs unrouted matching,
annotation exclusion, cutoff, weekly re-remind cadence, and the log row written by
`route_hand_curated.py`.
