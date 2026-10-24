# Overnight-coder handoff — harden the nSITE document pipeline against silent stalls

*Staged 2026-10-16, from the 2026-08-03 digest-recovery lesson. Read
`docs/overnight-coder.md` first. Parts (a) and (b) below are additive
liveness/timeout hardening with no behavior change to a healthy run; part (c)
changes the anti-stampede cap's own decision logic on the live document
pipeline. Recommended tier: Sonnet. **This handoff is explicitly splittable —
see "Suggested PR split" below.**

**Cloud-ready:** Runs in cloud mode: ends at a DRAFT PR (or draft PRs, if
split). No live credentials. Any real-specimen / live verification is done
locally by Trisha before merge.

**Live-path check:** the core nSITE document pipeline (`watcher.py` /
`backfill.py` / `doc_pipeline.py`) has no `enabled` flag — it's the monitor's
primary path and runs daily regardless. Treat every part of this as a
live-path change per `docs/overnight-coder.md` Step 3: no mocked-green
autonomous merge. Part (c) in particular changes when real documents get
deferred vs. processed — be conservative and prefer a draft PR with the
tradeoffs spelled out over a confident autonomous call.*

## Background

On 2026-08-02 the weekly digest silently failed to send for roughly two weeks
and nobody noticed, because:

1. Two new facilities were added to the document pipeline on 2026-07-24,
   pushing the unprocessed-doc count over the watcher's anti-stampede cap
   (`watcher.max_new_docs_per_run`, default 25). Per that cap's own logic
   (`watcher.py`, the "Anti-stampede guard" block — grep
   `max_new_docs_per_run` to find its current location, verify the line
   number below), once the backlog exceeds the cap, **the watcher defers ALL
   doc processing to backfill, every single run**, until something manually
   drains it.
2. Backfill's own nightly schedule had been separately disabled, so nothing
   was draining the backlog.
3. There is **no liveness alarm on the document pipeline at all.** The GFL
   perimeter-air stream (Stream E) has one (`gfl_air_liveness.py`,
   `max_stale_days`); the far more consequential document pipeline — the one
   that carries every N2688 violation, enforcement letter, and WOI
   temperature report — does not. So zero documents processing for two weeks
   produced zero alarms.
4. A separate, narrower risk found during the same incident: a degraded or
   throttled Anthropic API key made every classify call hang toward the GitHub
   Actions job's own `timeout-minutes` ceiling rather than failing fast,
   because `egle_doc_parser.py`'s `anthropic.Anthropic()` client (two call
   sites, both using the SDK's 600-second default — no explicit `timeout=`)
   has no per-call timeout shorter than the whole job.

Full incident writeup (Lotext-internal, not in this repo):
`handoffs/archive/DONE-2026-08-02-arbor-hills-digest-recovery.md` — summarized
above; this handoff captures only what became "follow-up fixes worth doing"
at the end of that writeup, in priority order.

## What to build

### (a) Liveness/stall alarm for the document pipeline

Model this closely on the existing `gfl_air_liveness.py` pattern — it's a
clean, already-reviewed precedent in this exact repo:

- A **pure decision function** (`liveness_decision`-equivalent): given "last
  successful doc-processing activity" (pick ONE unambiguous signal — e.g. the
  most recent `mark_processed` timestamp recorded in `_state`, or a dedicated
  `_meta` cell updated every run the watcher actually processes >=1 doc) and
  "now," return whether to warn, with a once-per-episode gate so it doesn't
  re-alert every single day of an ongoing stall (mirror
  `gfl_air_liveness.liveness_decision()`'s `warned_as_of` idiom — here it
  would be something like `warned_through` keyed off whatever timestamp you
  pick).
- **Also alarm on consecutive over-cap deferrals**, not just "no doc
  processed in N days" — those are different failure shapes (a facility
  backlog deferring everything every run vs. a genuinely dead pipeline) and
  the 2026-08-02 incident was actually the FIRST one. Track a consecutive-
  deferral counter (reset on any run that actually processes docs) and alarm
  past a configurable N.
- A **pure body-formatter** (mirror `format_liveness_body()`), clearly
  labeled a feed/pipeline-health notice — never formatted like an exceedance
  or an urgent document alert, so it can't be mistaken for one.
- An **orchestration wrapper** in `watcher.py` (mirror `_check_liveness()` in
  `gfl_air_watcher.py`), reading/writing whatever state cell you choose via
  the existing `sw.read_meta`/`write_meta` (or `read_state`/equivalent)
  helpers — don't invent a new state-storage mechanism when this repo already
  has one.
- New `config.yml` keys under `watcher:` (or wherever `max_new_docs_per_run`
  already lives) for `max_stale_days`-equivalent and the deferral-count
  threshold, with sane defaults (match `gfl_air`'s `max_stale_days: 3`-ish
  order of magnitude, adjusted for this pipeline's normal cadence — look at
  how often the watcher actually runs and processes something in a healthy
  week before picking a number, and say in the PR how you picked it).

### (b) Explicit, shorter per-call timeout on the classifier's Anthropic client

Both `client = anthropic.Anthropic()` call sites in `egle_doc_parser.py`
(verify current line numbers — they were approximately `:723` and `:819` when
this handoff was written) currently rely on the SDK's 600-second default, which
is exactly as long as — or longer than — the GitHub Actions job's own
`timeout-minutes`, so a degraded key hangs the *entire run* rather than
failing one document fast and letting the poison-strike/retry machinery (already
in `doc_pipeline.py`) handle it. Add an explicit `timeout=` (pick something
well under the job timeout — e.g. 60–120 seconds; check what
`.github/workflows/daily.yml`'s `timeout-minutes` actually is and leave
comfortable headroom) to both client constructions. Confirm this doesn't
fight with the SDK's own internal retry behavior — read `anthropic`'s timeout
docs/semantics before picking a number, and note your reasoning in the PR.

**Explicitly excluded from this item:** `ocr_util.py`'s OCR timeout
(`_POST_KILL_TIMEOUT_S`) is already bounded (5 seconds, post-kill grace) — the
root-cause writeup's mention of a 600s `ocr_in_place` stall predates that
fix. Verify `ocr_util.py` is still bounded as expected and leave it alone;
don't "fix" something already fixed.

### (c) Anti-stampede cap: distinguish a new-facility backlog from a genuine filing burst

**This part is the hardest to get right and the most likely to need real
judgment calls — treat it as optional for this cloud run if (a)+(b) already
make a solid PR on their own.** The problem: today, ANY run where
unprocessed docs exceed `max_new_docs_per_run` defers ALL doc processing,
with no distinction between "a brand-new facility was just added and has
years of historical filings to drain" (expected, backfill's job) and
"an established, already-monitored facility just filed an unusually large
batch of genuinely new documents" (should probably still get processed,
or at least not block forever).

A plausible design: track, per facility, whether it has ever had >=1 doc in
`state["processed"]` before. If the over-cap backlog is concentrated in
facilities with ZERO processing history, treat it as a new-facility backlog
(defer to backfill, as today). If an established facility (nonzero history)
suddenly contributes a large batch, don't block the ENTIRE run on it — either
process up to the cap across all facilities (oldest-first or
facility-balanced, your call) rather than deferring everything, or defer only
that facility's overflow while still processing everything else. Whatever you
choose, the acceptance bar is: **a brand-new facility must never again
silently freeze live monitoring of every OTHER already-established
facility**, which is exactly what happened on 2026-07-24.

If this gets large or the design choice feels genuinely ambiguous without
Trisha's input, stop after (a)+(b), open that PR, and leave (c) as a named,
scoped follow-up in the PR description (or its own separate draft PR with
just the design options laid out, no code) rather than forcing a guess on a
change this consequential.

## Suggested PR split

- **PR 1 (a)+(b):** liveness alarm + classifier timeout. Lower risk, fully
  additive, no change to what gets processed or when — safe to build and
  review as one unit.
- **PR 2 (c), if attempted:** the anti-stampede redesign. Higher risk (changes
  which real documents get processed on which run), deserves its own,
  narrower review.

## Tests

- `liveness_decision`-equivalent: pure unit tests mirroring
  `gfl_air_liveness.py`'s own test style — stale-but-already-warned doesn't
  re-fire, a fresh warn after the gate resets, unparseable/missing timestamp
  never fires a quantified alarm.
- A `watcher.py`-level test: N consecutive over-cap-deferral runs trips the
  alarm; a run that processes even one doc resets the counter.
- `egle_doc_parser.py`: a test that the Anthropic client is constructed with
  an explicit `timeout=` less than the default 600s (inspect the call kwargs
  via a mock, don't make a real network call).
- If (c) is attempted: a test with a mixed backlog (an established facility
  with history + a brand-new facility with none, combined over the cap)
  proving the established facility's docs still get processed while the new
  facility's historical backlog is deferred.
- Existing tests must stay green.

## Out of scope

- `ocr_util.py` timeouts (already bounded — see above).
- Re-enabling backfill's nightly schedule, if it's still disabled — that's an
  operational/workflow-scheduling decision for Trisha, not a code fix this
  handoff should make unilaterally. Mention the current schedule state in
  the PR if you happen to notice it, but don't change it.
- Any change to how documents are classified, parsed, or alerted on —
  this handoff is purely about the pipeline not going silently dark.

## Acceptance

- `pytest -q` fully green.
- The PR (or PR 1, if split) states clearly what signal the liveness alarm
  watches, how it avoids re-alerting on every run of an ongoing stall, and
  what the new config defaults are and why.
- If (c) ships, the PR states explicitly which facilities would have been
  reclassified (new-backlog vs. established-burst) in the 2026-07-24
  incident, as a sanity check that the new logic would have actually
  prevented it.
- CLAUDE.md: add a short Architecture entry for any new module this creates
  (e.g. a `doc_pipeline_liveness.py`-style split, if you follow the
  `gfl_air_liveness.py` split pattern) and regenerate
  `docs/topology/README.md`'s artifacts if a new module/dependency edge is
  added.
