# ADR 017 — Stream H: ROP (air Title V permit) watch

*Status: **BUILT, disabled** — 2026-07-15. Ships `rop.enabled: false`; a brand-new
poller against a live external source stays off until a human flips it on
(overnight-coder procedure). The Activation section below is Trisha's checklist.*

## Context

All three Arbor Hills air facilities have a **Renewable Operating Permit (ROP)
renewal IN PROCESS** as of 2026-07-15:

| SRN | Facility | Permit | Version | Renewal created |
|---|---|---|---|---|
| N2688 | Arbor Hills Landfill | ROP0000224 | 3 | 12/18/2024 |
| N1504 | Arbor Hills Energy | ROP0000656 | 3 | 10/22/2025 |
| P1488 | Emerald RNG | ROP0000236 | 3 | 1/22/2025 |

A ROP renewal reaching a certain stage opens a **30-day public comment window** — a
second advocacy venue (air-permit conditions) distinct from the nSITE document feed
this monitor already watches, and one that's easy to miss buried in a ~1,800-row
statewide export. This is the trip-wire for that moment, especially for N2688.

Full analysis + the baseline used to seed the fixture:
`/Volumes/Samsung-Pro-2TB/Lotext/documents/arbor-hills/arbor-hills-ROP-permit-tracker.md`
(outside this repo). Handoff:
`/Volumes/Samsung-Pro-2TB/Lotext/handoffs/2026-07-15-overnight-coder-rop-watch.md`.

### Feasibility spike (2026-07-15, this build)

Verified live before writing code, per overnight-coder Step 3:

- All three sources return HTTP 200 with a browser User-Agent
  (`egle.state.mi.us` does not 403 like `michigan.gov`):
  1. `.../pub_ntce/1 - EPA ROP Monthly Report/EPA Monthly Report.csv` — 1.8 MB,
     `Last-Modified: Mon, 08 Jun 2026`.
  2. `.../pub_ntce/N2688/` — a plain Apache/IIS directory listing, ~1.9 KB.
  3. `.../pub_ntce/ROP_Public_Notice.pdf` — 341 KB, text-layer, 23 pages;
     as of 2026-07-15 it does **not** mention N2688 (consistent with the CSV's
     "no 2026 date yet" baseline — public comment is not yet imminent).
- **The N2688 folder carries NO `Last-Modified` header on the folder URL itself**,
  and its listed files are dated up to 2024 even though the folder page can look
  freshly generated — confirming the handoff's warning: folder-level mtime is not
  a trustworthy "something changed" signal. `parse_folder_listing` reads each
  file's own date out of the directory-index HTML instead, and the watch's alert
  trigger is "a new file NAME appears," never a folder timestamp.
- The CSV's real header (row 2 — row 1 is a merged-cell group header) has exactly
  22 columns, with **repeated column names** ("Name" ×3, "Status" ×3 — the ROP
  action, the task, and the permit each have their own status). Parsing is
  therefore **positional**, not name-keyed.

## Decision

Add a new watch stream: `rop_client.py` (fetch + parse, pure where possible) +
`rop_watcher.py` (snapshot/diff/alert), gated behind `rop.enabled` (off by
default), modeled on `pfas_watcher.py` / `civicclerk_watcher.py` — alert-only, no
Drive/OAuth, the Sheet tab itself is the append-only state. `egle_doc_parser.py`
is **not** touched (the Decode base stays domain-agnostic; the notice PDF is
routed through `fitz` directly, same posture as `ridgewood_client.py`).

### 1. Filter by exact SRN, never by facility name

`M3333` ("Conway Products Corporation d/b/a Emerald Spa Corp") is an **unrelated**
Grand Rapids facility that happens to also contain "Emerald" in its name — P1488
is "Emerald RNG LLC". `parse_csv_rows` filters on exact membership in
`TARGET_SRNS = ("N2688", "N1504", "P1488")`, never on a name substring, so M3333
is excluded **by construction**. `tests/test_rop.py` pins this with a fixture
built from the real CSV's M3333 rows (verbatim, trimmed) to prove the exclusion
against real data, not an invented case.

### 2. Three sources, five watched items, ONE fetch per source

Each source is fetched once per run and produces the items derived from it:

- The CSV → one item **per target facility** (`csv:N2688`, `csv:N1504`,
  `csv:P1488`) — independent snapshots, so a change at one facility never masks
  or gets conflated with another's.
- The N2688 folder listing → one item (`folder:N2688`).
- The statewide notice PDF → one item (`notice:N2688`) — a boolean "does N2688
  appear" trip-wire, not a full-page content hash (the notice covers every
  facility statewide; hashing the whole PDF would fire on unrelated renewals
  elsewhere and swamp the signal).

Each item is snapshotted, hashed (sha256[:16] of sorted-key JSON, the
`civicclerk_watcher.snapshot_hash` idiom), and diffed against the last row for
its key in the new **`ROP Watch`** tab (append-only ⇒ race-free, not `_meta`).
First sighting baselines silently; a hash change records a row and emails an
alert with a human-readable diff.

### 3. Facility snapshot rows sort by the FULL field tuple, not a partial key

A ROP task row's natural identity is `(permit_number, version, task_name)` —
but the real CSV can carry **two rows sharing that identity** with different
other fields (verified in the actual N2688 data: permit v2's "Send working
draft conditions to applicant" task appears twice, once recorded `Superseded`
or once `Extended`, at different `task_completed` dates). Sorting — and
diffing — on the partial key alone is unsafe two ways:

- **A `sorted(..., key=partial)` is not a full order.** Python's sort is
  stable, so two rows tied on the partial key keep whatever relative order
  they arrived in — meaning the *same* underlying data, fetched twice, could
  hash *differently* purely from row order, a false "changed" alert with
  nothing to point to. Caught by `test_facility_snapshot_hash_stable_across_row_order`.
  Fixed by sorting on the full field tuple (a genuine total order).
- **A `{partial_key: row}` dict silently drops one of the tied rows.** Applied
  originally in `summarize_facility_change`'s diff (not detection — the hash
  above was already collision-safe); a real collision would have produced a
  diff email describing only one of the two rows, or missing the change
  entirely if the surviving dict entry happened to be identical before/after.
  Fixed by diffing the full row as a `collections.Counter` multiset instead of
  a dict keyed on the partial identity — a changed row now reads as its old
  shape REMOVED plus its new shape ADDED (less granular than a field-by-field
  "~ CHANGED x -> y" would be, but never loses a row to a key collision).
  `test_summarize_handles_partial_key_collision_without_dropping_a_row` pins
  this directly.

### 4. Fail-safe per source, not per item

A source's fetch failure (`RopFetchError`) is treated as **transient**:
skip-and-warn if every item derived from that source already has a baseline
(`_all_baselined`); **loud exit 1** if any of them has none yet (an
activation-time block must surface, never silently no-op forever — the same
posture as `pfas_watcher`/`civicclerk_watcher`). Because the three sources are
independent, a failure in one (e.g. the notice PDF 403s on a runner) does
**not** block the other two from baselining or diffing normally — a
**partial** activation block, not all-or-nothing, so a single flaky source
can't hide the other two working correctly. `tests/test_rop.py` pins this per
source.

A **structural** CSV parse failure (`RopParseError` — wrong column count) is
a different failure class and is deliberately **not** folded into that
same baseline-gated logic: it is always loud (`exit 1`), regardless of
whether every CSV item already has a baseline. A column-layout break is very
unlikely to be a transient blip, and the earlier draft of this stream bucketed
it with `RopFetchError` — meaning once all three CSV items had baselined, a
genuine structural break would have silently no-op'd forever behind a log
line, the exact silent-stall failure class ADR 014's liveness guard exists to
catch. Fixed before merge; `test_csv_structural_parse_error_is_always_loud_even_with_existing_baseline`
pins it.

### 5. Recipients default to the FULL advocacy list

Unlike the CivicClerk meeting-watch or Ridge Wood's review-tier (both scoped to
Trisha alone — operational/logistics noise), a ROP renewal advancing — especially
reaching public comment — is a substantive signal for the whole Conservancy, the
same tier as a real Ridge Wood exceedance. `rop.recipients` is an optional
override (unset ⇒ the full `alert_recipients` list via `send_email`'s
`recipients=None` default).

## Consequences / residual risks (accepted)

1. **Rides an undocumented CSV export and an undocumented directory listing.**
   Neither is a documented API; a column-count or markup change breaks the
   parse. The CSV's column-count check (`RopParseError` on a mismatch) and the
   folder regex's natural exclusion of non-dated lines make a structural
   change **fail loudly** (activation-time block) rather than silently
   misparse. Recovery: adapt the parser, or a documented STOP.
2. **The folder + notice items track existence/mention, not content.** A folder
   diff answers "did a new file appear," not "did an existing file's content
   change" (the handoff's ask); an existing renewal PDF being silently
   replaced in place would not fire. Accepted — same scope call as
   `pfas_watcher`'s <main>-content-only hash: matches the stated signal
   ("a new file appears"), not a broader promise.
3. **The notice PDF's N2688 check is a single whole-word regex match**, not a
   structured "which facilities are in their comment window" table (none
   exists on the source). A comment-window announcement using different
   phrasing that never spells out "N2688" (e.g. referencing only the permit
   number `ROP0000224`) would be missed. Partially mitigated by watching the
   CSV's own renewal task-status advancement as an independent, earlier
   signal — the notice item is corroboration, not the sole trip-wire.
4. **No Drive mirror.** Same scope call as `pfas_watcher` (ADR 012): the
   deliverable is the alert + the durable Sheet row (full snapshot JSON), not a
   document archive. A future draft ROP appearing in the N2688 folder is
   flagged by name only; fetching/mirroring it is a manual follow-up, not this
   stream's job.

## Alternatives considered

- **One combined snapshot across all three SRNs** — simpler to implement, but
  would conflate an N1504 change with an N2688 change in one alert, burying the
  landfill signal (the actual advocacy priority) inside routine Energy/RNG
  churn. Not chosen — one item per facility keeps alerts attributable.
- **Hash the whole statewide notice PDF** (`pfas_watcher`'s whole-page-content
  approach) — would fire on every renewal reaching comment statewide, not just
  N2688's, drowning the one signal that matters here. Not chosen — a targeted
  boolean mention-check instead.
- **Trust the CSV's or folder's HTTP `Last-Modified` header as the change
  signal** — the CSV's header is real but reflects the WHOLE statewide file
  (any facility's edit ticks it, most of them irrelevant here); the folder URL
  carries no header at all (verified live). Not chosen for the alert decision;
  the CSV's `Last-Modified` is still recorded in the run log as provenance.

## Activation (Trisha's call — same pattern as Stream C / Mirror D / PFAS / GFL air)

1. Review + merge this branch to `main`.
2. The first enabled run baselines all seven items (records a snapshot, alerts
   on **none**) — no seed script needed.
3. Set `rop.enabled: true` in `config.yml` and commit.

Until `enabled: true` is on `main`, `rop-watch.yml` runs on schedule but
`rop_watcher._should_run()` makes every run a quiet no-op (verified by
`tests/test_rop.py`, mirroring the gate test that has caught this exact class of
bug before — ADR 009's Addendum).

## Amendment — 2026-07-23 (two fixes after Emerald RNG's window slipped through)

Trisha noticed the ROP watch produced **no email** when Emerald RNG's (P1488) ROP
renewal reached its 30-day public comment window (EGLE notice 2026-07-22, comment
period July 20 – Aug 1); it was surfaced only by the separate morning-briefing
scan. Investigation (ROP Watch tab + the 2026-07-22 run log + a live fetch of the
notice PDF) found two independent causes, both fixed here:

1. **The public-notice trip-wire was hardcoded to N2688 only.** `rop_watcher.run()`
   called `notice_mentions_srn(pdf_bytes, "N2688")` and stored a single
   `notice:N2688` item — so P1488 (and N1504) appearing in `ROP_Public_Notice.pdf`
   was never searched for. Confirmed against the live PDF: it currently contains
   *"Emerald RNG LLC – SRN: P1488 … public comment period … July 20 … August 1"*
   and **zero** occurrences of "N2688", yet the run recorded `N2688 mentioned =
   False → unchanged` and sent nothing. **Fix:** one `notice:<SRN>` item per target
   SRN (N2688 / N1504 / P1488), reusing the existing snapshot/diff/alert path — the
   "one item per facility keeps alerts attributable" principle from *Alternatives
   considered* above, now applied to the notice check itself (previously CSV-only).
   The N2688 folder listing stays N2688-only (no verified N1504/P1488 folder URL).

2. **The CSV monthly-report fetch had been failing HTTP 406 on every scheduled run
   since activation (2026-07-16).** EGLE's server content-negotiates the CSV
   endpoint and returned `406 Not Acceptable` for the opener's
   `Accept: text/html,application/pdf,text/csv` header. Because all `csv:*` items
   were already baselined, `RopFetchError` was (correctly, per this ADR's transient
   rule) skip-and-warned every run — so the failure was silent, and the only path
   that watches P1488 via the CSV was effectively blind. **Fix:** `_opener()` now
   sends `Accept: */*` (verified: the CSV returns the full ~1.8 MB body, and all
   three sources return 200). Reproduced from a non-Actions IP, so it was the
   header, not an egress block.

**Blast radius of merge — verified zero emails:** all three `csv:*` items hash
*identically* to their 2026-07-16 baseline (the monthly report has not changed),
so un-blinding the CSV fetch fires nothing on the next run. The notice fix is
**forward-only**: the new `notice:N1504`/`notice:P1488` items baseline silently on
first sighting (the uniform "first sighting is silent" invariant is deliberately
left unchanged — not bolted-over in the shared `_diff_and_record` path). One
consequence, documented so it is a choice not a surprise: **the currently-open
P1488 window will NOT retroactively alert** — it baselines silent, and the only
auto-fire would be a low-value "window closed" notice around Aug 1. If the
Conservancy should be looped in on the *open* window, that is a one-time manual
send. All *future* comment windows for any of the three SRNs now fire normally.

Tests: `tests/test_rop.py` gains a P1488-appears-while-N2688-absent regression
(`test_notice_p1488_appearing_emails_alert_the_exact_gap`) and an SRN-naming unit
test; the item-count/key-set assertions move 5 → 7. Full suite green (438).
