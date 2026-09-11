# Overnight-coder handoff — Unsubscribe for the outbound alert emails (CAN-SPAM)

*Staged 2026-09-03 (Trisha-directed). Read `docs/overnight-coder.md` first. **Feasibility-gate
first** (Step 1 below): the send mechanism decides the cleanest compliant approach, so spike that
before building. LIVE-PATH change (real emails go out) -> per Step 8 open a **DRAFT PR for
Trisha's review, do NOT auto-merge.** Recommended tier: **Sonnet**.*

## Why / urgency

The monitor emails **three streams that go to more than just Trisha** (she confirmed these are the
only >1-recipient streams):

1. **GFL Air perimeter action-level alerts** (the consolidated WATCH email — see
   `coder:gfl-air-action-level-alerts`; recipients `GFL_AIR_WATCH_RECIPIENTS_EXTRA`).
2. **The Sunday monitor digest** (`email_alerts.send_digest`; committed `alert_recipients`
   + `ALERT_RECIPIENTS_EXTRA` + `DIGEST_RECIPIENTS_EXTRA`).
3. **The same-day `[URGENT]` alerts** (committed `alert_recipients` + `ALERT_RECIPIENTS_EXTRA`).

Once the 5 county commissioners (and later the MMPC committee) join these lists (~2026-09-17),
we are sending unsolicited commercial-ish mail to third parties, so **CAN-SPAM requires a working
opt-out**: a clear, functioning unsubscribe honored within 10 business days, plus a valid physical
postal address and a clear sender identity in every such email. **This should ideally land BEFORE
the 9/17 recipient expansion.**

## Goal

Give all three streams a **working, one-touch unsubscribe** and a **suppression list that every
send path honors**, so any recipient can opt out of any/all of these emails and immediately stop
receiving them.

## Step 1 — FEASIBILITY SPIKE (do this first; it picks the mechanism)

Determine how these emails actually go out. `grep` the send path in `email_alerts.py` (the
central `send_email`) and the workflows:
- **If sending via Resend** (the `@trishakunst.com` aliases relay via Resend per Trisha's setup):
  prefer Resend's native unsubscribe / suppression + a `List-Unsubscribe` +
  `List-Unsubscribe-Post` header (RFC 8058 one-click). Resend manages the suppression; the send
  path just honors it.
- **If sending via plain SMTP:** there is no hosted backend (the site is static GitHub Pages, no
  POST endpoint), so use a **`List-Unsubscribe: <mailto:unsubscribe@trishakunst.com?subject=...>`**
  header (mail clients render a native unsubscribe button; the action sends an email) PLUS a
  self-service suppression list the senders read. A small inbox-reader (or a manual triage step)
  moves `unsubscribe@` requests into the suppression store.

Pick the simplest compliant option for the ACTUAL infrastructure and state it in the PR. Do NOT
stand up a new web backend just for this.

## Approach (pinned, mechanism-agnostic)

1. **One suppression store, honored everywhere.** Add a suppression list (a new `Unsubscribes`
   Sheet tab keyed by lowercased email, or a config/secret list if a Sheet write is unwanted) and
   a single choke point — extend `email_alerts.resolve_recipients` (and the GFL-air watch
   recipient resolution + the digest recipient resolution) so **every** send path filters
   suppressed addresses out before sending. One filter, all three streams. Trisha's own addresses
   are never suppressible (guard against self-lockout).
2. **`List-Unsubscribe` + `List-Unsubscribe-Post` headers** on all three streams (RFC 8058
   one-click where the send path supports it; mailto otherwise).
3. **Footer** on all three streams: a plain "You're receiving this because … . Unsubscribe: <link/
   mailto>." line + a **valid physical postal address** + clear sender identity (CAN-SPAM's other
   two requirements, not just the link). Confirm the postal address to use with Trisha (do not
   invent one).
4. **Capture path:** whatever Step 1 picks, an opt-out reliably lands in the suppression store
   within the CAN-SPAM window. If it needs an inbox-reader for `unsubscribe@`, keep it minimal and
   idempotent; if manual triage is acceptable to Trisha for the current tiny list, document that as
   the interim and note the automation as a follow-on.
5. **Tests:** a suppressed address is dropped from each of the three streams; Trisha's own
   addresses are never dropped; the List-Unsubscribe header is present + well-formed on each
   stream; adding then removing a suppression round-trips; an empty suppression list is a no-op.

## Open decisions to surface in the PR (do NOT decide autonomously)

- The physical postal address to print (CAN-SPAM requires one — Trisha's call; a PO box is fine).
- Whether unsubscribe is per-stream (opt out of just the digest) or global (all monitor mail) for
  v1 — recommend GLOBAL for v1 (simplest, safest), per-stream as a follow-on.
- Whether to auto-read the `unsubscribe@` mailbox now or start with manual triage (tiny list).

## Definition of done

Green `pytest -q`; all three streams (GFL-air WATCH, Sunday digest, `[URGENT]`) carry a working
unsubscribe (header + footer + postal address) and honor one shared suppression list at a single
choke point; Trisha's own addresses can't be suppressed; opt-out lands in the store within the
CAN-SPAM window; ADR + tests + topology in the PR. **DRAFT PR for Trisha — do NOT auto-merge**
(live path; also needs her postal-address decision). Ideally merged + live before the 9/17
recipient expansion.
