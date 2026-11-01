# Overnight-coder handoff — recap urgent items in the following Sunday digest

*Staged 2026-08-21. Read `docs/overnight-coder.md` first. This IS a live-path
change (`watcher.py`'s daily routing logic runs today, unconditionally — there
is no `enabled:false` gate to hide behind), so per Step 3 it must be verified
against a real classified document before an autonomous merge, not merged on
mocked-green alone. Recommended model tier: **Sonnet**, not Haiku. This touches
established state-schema conventions (`_META_DEFAULTS`/`_META_CELL_ROWS` in
`sheet_writer.py`) and a live notification path where getting the labeling
wrong reads as a duplicate alert to real recipients (elected officials, an
opposition-seat contact) — it needs judgment about matching existing idioms
exactly, not just mechanical extraction. Haiku is used elsewhere in this repo
only for document classification, never for editing the pipeline itself.*

## Invocation

Branch name suggestion: `digest-urgent-recap`.

## Why (context a fresh session won't have)

Trisha is building a public GitHub Pages site (`site/`) for this project,
including a planned "Findings" feed page that will be sourced from the same
data the Sunday digest email uses. While designing that feed, she and Claude
found a real gap: **an urgent item never appears anywhere except its own
same-day `[URGENT]` email.** Today's routing in `watcher.py` is a strict
if/else — a document is either urgent (send now, nothing else) or routine
(queue for Sunday). Concretely:

```python
# watcher.py, current behavior (~line 177)
if ea.is_urgent(parsed, cfg):
    try:
        ea.send_urgent_alert(parsed, d, link, cfg)
        print(f"  URGENT emailed: {d['document_name'][:50]}")
    except Exception as ae:
        print(f"  URGENT ALERT FAILED to send (doc still recorded): "
              f"{d['document_name'][:50]}: {ae}")
else:
    state["pending_digest"].append(_digest_record(parsed, d, link))
    sw.write_meta(sheets, sheet_id, state)
```

Two consequences, both real: (1) a human who only reads the Sunday digest
(not every mid-week urgent email) has no way to know an urgent event happened
that week; (2) a future public feed built by mirroring "what's in this week's
digest" would systematically omit the single most important tier of finding.

**Trisha's decision:** fix it at the source. Urgent items should ALSO be
recapped in the following Sunday digest, under their own clearly-labeled
section, so a reader who already got the same-day alert immediately
understands it's a repeat, not a new event.

## Goal

When a document is classified urgent and its same-day alert is sent, ALSO
record it so the next Sunday digest includes a section titled **"Urgent items
from earlier this week"**, listing each one with the date/time its urgent
alert actually went out, positioned before the routine "new this week"
content (it's the most important content, and the label makes clear it's not
new).

## Scope (tight)

- **Additive only.** The existing same-day `[URGENT]` send is unchanged in
  every way — same trigger, same content, same recipients (`resolve_recipients`,
  full `alert_recipients` list). This adds a *second*, later mention of the
  same event in the digest; it does not change when or to whom the urgent
  email itself is sent.
- **Recap, not re-alert.** The Sunday digest recap section must not read as a
  new urgent event. It needs to carry the original send date/time explicitly
  in its own label (e.g. "Sent Aug 15, 2026" per item), so nobody mistakes it
  for something that just happened.
- **Do not touch `send_urgent_alert`, `is_urgent`, or the urgent email's own
  content/format.** Everything here is about what happens *in addition to*
  that unchanged path.
- **Do not build the public feed page in this PR.** That's a separate,
  later piece of work (not yet scoped) that will read from the same digest
  data once this recap exists. Out of scope here.
- **Empty-week behavior unchanged.** `send_digest` is still only called when
  there's something to send (`if state["pending_digest"]:` today) — but note
  the condition needs to become "pending_digest OR pending_urgent_recap
  non-empty" (see Approach step 4), so a week with an urgent item but zero
  routine documents still sends a digest containing just the recap section.

## Approach (pinned)

1. **New `_meta` singleton: `pending_urgent_recap`.** Follow the exact
   existing pattern for `pending_digest` — add it to `_META_DEFAULTS` in
   `sheet_writer.py` (currently line ~413-418):

   ```python
   _META_DEFAULTS = {
       "pending_digest": [],
       "pending_urgent_recap": [],   # new
       "wds_seen": {},
       "wds_snapshot_hashes": {},
       "last_run": "",
   }
   ```

   `_META_CELL_ROWS` is currently `8` and only 4 keys are live today, so
   adding a 5th key fits with no need to raise it. Per the existing code
   comment right above `_META_DEFAULTS`, adding a key here is genuinely
   zero-extra-plumbing: `read_meta`/`read_state`/`write_meta` all iterate
   `_META_DEFAULTS`, so the new key is automatically loaded, defaulted, and
   persisted once it's added there. Do not hand-roll separate read/write
   logic for it.

2. **New record builder in `watcher.py`,** parallel to the existing
   `_digest_record` (~line 52):

   ```python
   def _urgent_recap_record(parsed, d: dict, link: str, sent_at: str) -> dict:
       return {
           "date_filed": d["date_filed"],
           "document_name": d["document_name"],
           "doc_type": parsed.doc_type,
           "severity": parsed.severity,
           "risks": parsed.risks,
           "key_data_point": parsed.key_data_point,
           "link": link,
           "urgent_sent_at": sent_at,   # the new field: when the [URGENT] email actually went out
       }
   ```

   `sent_at` should be `_now()` captured at the point `send_urgent_alert`
   succeeds (not before — if the send fails and falls into the existing
   `except` branch, do NOT add a recap record; an alert that never went out
   has nothing to "recap"). This mirrors the existing failure handling:
   `send_urgent_alert` failing already just logs and moves on without
   touching `pending_digest` either.

3. **Wire it into the if/else in `watcher.py` (~line 177-186).** On the
   success path of the urgent branch, in addition to the existing
   `send_urgent_alert` call, append the recap record and persist:

   ```python
   if ea.is_urgent(parsed, cfg):
       try:
           sent_at = _now()
           ea.send_urgent_alert(parsed, d, link, cfg)
           state["pending_urgent_recap"].append(
               _urgent_recap_record(parsed, d, link, sent_at))
           sw.write_meta(sheets, sheet_id, state)
           print(f"  URGENT emailed: {d['document_name'][:50]}")
       except Exception as ae:
           print(f"  URGENT ALERT FAILED to send (doc still recorded): "
                 f"{d['document_name'][:50]}: {ae}")
   else:
       state["pending_digest"].append(_digest_record(parsed, d, link))
       sw.write_meta(sheets, sheet_id, state)
   ```

   (Both branches now call `write_meta` on their own success path — matches
   the existing style, don't try to de-duplicate the call into one shared
   line outside the if/else, since the failure branch must skip it.)

4. **Sunday digest trigger in `watcher.py` (~line 273-283).** Today:

   ```python
   if today.weekday() == 6:
       if state["pending_digest"]:
           items = [_record_to_item(r) for r in state["pending_digest"]]
           ea.send_digest(items, cfg)
           state["pending_digest"] = []
           sw.write_meta(sheets, sheet_id, state)
           print(f"[watcher] sent Sunday digest ({len(items)} item(s)).")
   ```

   Change the condition to fire if EITHER queue is non-empty, build both item
   lists (reusing `_record_to_item` for both — it doesn't care which queue a
   record came from, though the recap records carry the extra
   `urgent_sent_at` field `_record_to_item` will need to pass through; either
   extend `_record_to_item` to copy it into the returned dict when present, or
   write a small parallel `_recap_record_to_item`), pass the recap list into
   `send_digest`, and clear BOTH queues on success:

   ```python
   if today.weekday() == 6:
       if state["pending_digest"] or state["pending_urgent_recap"]:
           items = [_record_to_item(r) for r in state["pending_digest"]]
           recap_items = [_record_to_item(r) for r in state["pending_urgent_recap"]]
           ea.send_digest(items, cfg, urgent_recap=recap_items)
           state["pending_digest"] = []
           state["pending_urgent_recap"] = []
           sw.write_meta(sheets, sheet_id, state)
           print(f"[watcher] sent Sunday digest ({len(items)} item(s), "
                 f"{len(recap_items)} urgent recap item(s)).")
   ```

5. **`email_alerts.py` changes.** `format_digest_body` (~line 103) and
   `send_digest` (~line 211, already modified this session to merge
   `DIGEST_RECIPIENTS_EXTRA` — keep that intact) both need a new optional
   parameter:

   ```python
   def format_digest_body(items: list[dict], urgent_recap: list[dict] | None = None) -> str:
       """items: [{parsed, metadata, link}]. urgent_recap: same shape, plus each
       item's metadata should carry 'urgent_sent_at' when present. The recap
       section renders FIRST and is clearly labeled as already-sent, so a
       reader who got the same-day [URGENT] email recognizes it as a repeat,
       not a new event."""
       ...
       lines = []
       if urgent_recap:
           lines.append("URGENT ITEMS FROM EARLIER THIS WEEK (already emailed separately):")
           for it in urgent_recap:
               p, m = it["parsed"], it["metadata"]
               sent_at = m.get("urgent_sent_at", "")
               lines.append(f"  - Sent {sent_at}  {m.get('document_name','')}")
               lines.append(f"      {p.key_data_point}")
               lines.append(f"      {it['link']}")
           lines.append("")
       # ... existing procedural/others rendering continues below, appended after
   ```

   Adjust the "No new Arbor Hills (N2688) documents this period" early-return
   so it doesn't fire when `urgent_recap` is non-empty but `items` is empty
   (a week with only a recapped urgent item and nothing new is not an empty
   digest). And `send_digest`:

   ```python
   def send_digest(items: list[dict], cfg: dict, urgent_recap: list[dict] | None = None) -> None:
       subject = f"Arbor Hills N2688 digest — {len(items)} new document(s)"
       recipients = merge_extra_recipients(resolve_recipients(cfg), "DIGEST_RECIPIENTS_EXTRA")
       send_email(subject, format_digest_body(items, urgent_recap), cfg, recipients=recipients)
   ```

   (Keep the subject line based on `len(items)` only, not including recap
   count, so the email header still reads as "N new documents" — the recap
   section inside the body is what surfaces the urgent items, not the subject.)

6. **Exact recap label wording.** Use "Sent {date}" per item (not "Urgent —
   {date}" or similar) so the word choice matches how a reader would describe
   an email they already received, and pairs naturally with the section
   header "URGENT ITEMS FROM EARLIER THIS WEEK (already emailed separately)".
   Do not use an em dash in the section header or anywhere in new copy — see
   `arbor-hills-voice-guide.md`'s AI-writing-tells checklist (Lotext repo,
   not this one) if touching any other public-facing text; this specific
   change is email-body copy, not site copy, but the em-dash rule is global.

## Tests

No `tests/test_watcher.py` exists today — `watcher.py`'s orchestration isn't
directly unit-tested; the pieces it calls are (`tests/test_email.py` for
`email_alerts`, `tests/test_state.py` for the `_META_DEFAULTS`/read/write
plumbing). Follow that existing split rather than inventing a new pattern:

- **`tests/test_state.py`:** add a case confirming `pending_urgent_recap`
  defaults to `[]`, round-trips through `write_meta`/`read_meta`, and that an
  orphaned old value doesn't leak (mirror whatever existing `pending_digest`
  coverage already does there).
- **`tests/test_email.py`:** add cases for `format_digest_body`'s new
  `urgent_recap` param — a recap-only digest (no new `items`) still renders
  (not the "no new documents" message), the recap section appears before the
  procedural/other sections, each recap line shows its `urgent_sent_at`
  value, and an empty/`None` `urgent_recap` reproduces today's output
  byte-for-byte (regression guard — every existing `format_digest_body`
  test should still pass unmodified). Also add a `send_digest` case
  confirming `urgent_recap` reaches `format_digest_body` and recipients are
  still resolved the same way (reuse the pattern from the
  `test_send_digest_*` tests added 2026-08-21).
- **New, minimal `watcher.py`-level test** (a small addition to
  `tests/test_state.py` or a new file, implementer's call) covering just the
  if/else change: an urgent-classified doc whose `send_urgent_alert` succeeds
  appends to `pending_urgent_recap` and NOT `pending_digest`; a routine doc
  still appends to `pending_digest` only; a FAILED `send_urgent_alert` (raise
  inside the mock) results in NEITHER queue getting the record (matches
  today's existing behavior of not queuing a failed urgent send anywhere).

## Real-specimen verification (required — this is a live path, Step 3)

Before merging, verify end-to-end against at least one real classified
document from the live corpus (not a synthetic fixture): confirm an actual
historical urgent-tier document, run through the real classification +
routing path, produces a `pending_urgent_recap` entry with a sensible
`urgent_sent_at`, and that `format_digest_body` renders it correctly. Do not
merge on mocked-green alone per `docs/overnight-coder.md` Step 3 — this
changes what actually gets written to the live `_meta` tab and what actually
gets emailed to the real `alert_recipients` list (elected officials included).

## Adversarial review

- **Duplicate-looking alert:** the entire point of the "Sent {date}" labeling
  and the "(already emailed separately)" section header is to prevent a
  digest recipient from thinking a new urgent event just happened. Don't
  weaken this wording during implementation for brevity.
- **Failed urgent send silently vanishing twice:** confirm a failed
  `send_urgent_alert` doesn't get queued to EITHER `pending_digest` or
  `pending_urgent_recap` — today's behavior already drops a failed urgent
  send from `pending_digest` too (it's in the `if` branch, not `else`), so
  this preserves existing behavior rather than introducing a new gap; call
  this out explicitly in the PR description so it's a documented decision,
  not a silent gap.
- **`_meta` cell growth:** `pending_urgent_recap` is bounded by actual urgent
  volume between Sundays (expected to be small — urgent is the rare tier by
  design), well under the 50k-char cell cap; no special handling needed
  beyond what `pending_digest` already gets.
- **Concurrent-write race with the Meeting Watch / other same-day writers:**
  no different from the existing `pending_digest` write pattern — same
  `write_meta` call, same file, same risk profile as today; not a new
  concern introduced by this change.
- **`upcoming.py`'s separate email path:** unaffected — that's a fully
  separate `send_upcoming` call scoped to `upcoming.recipients`, untouched by
  this change.

## Docs

Update `docs/business-rules.md`'s digest-scheduling notes (wherever the
existing "urgent → same-day, routine → next Sunday" rule is stated) to add:
"an urgent item is also recapped, clearly labeled, in the next Sunday digest
after it fires." No new ADR needed (this is a refinement of the existing
alert-tier design, not a new stream) unless the implementer's judgment says
otherwise after reading the existing tier documentation.

## Definition of done

Green `pytest -q` (full suite, not just new tests); a week with an urgent
item and zero routine documents still sends a digest (recap-only); a week
with both renders the recap section first, each item labeled with its actual
send date/time, followed by the unchanged procedural/other sections; a week
with neither still sends nothing (today's empty-week behavior preserved);
same-day `[URGENT]` emails are byte-for-byte unchanged in trigger, content,
and recipients; verified against at least one real historical urgent-tier
document per the Real-specimen section above; `docs/business-rules.md`
updated. The public feed page itself stays out of scope, someone (Trisha or
a later session) will design that separately once this recap data exists to
draw from.
