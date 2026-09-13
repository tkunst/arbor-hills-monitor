# ADR 043 — WDS Composting/Utilization + Penalties coverage (Stream C gap)

*Status: active — 2026-09-13 (extends Stream C; `wds.enabled` already `true`, so
the three new collections go live via `wds.collections` the same day — see
Activation).*

## Context

Backlog item `coder:wds-composting-penalties-watch` — the last open row in the
report-coverage matrix (`documents/arbor-hills/arbor-hills-report-coverage-matrix.md`;
the RIDE and GFL-air-durable-exhibit gaps closed 2026-08-07). Stream C
(`wds_watcher.py`, ADR 009) polls five WDS collections for site 475946
(applications, compliance_actions, qmr, annual, evaluations) but had **no code
path** for two sections the 2026-07-09 crawl catalogued:

- **Penalties** — the monetary consequences of enforcement: a nested sub-grid
  under CME Compliance Actions. Six penalties on file, **$447,485.46 assessed**
  (the 2022 consent-judgment $355,109 + the 2023 compliance-order $15,300/$1,424,
  the 2024 stipulated-penalty $750/$1,500, a 2002 $73,402). Compliance_actions
  watches the *actions*; nothing watched the *dollar amounts* or their payment.
- **Composting / Utilization** — the across-the-road compost parcel's Part 115
  authorization, on the WDS **Utilization** module: **6 composting registrations**
  (one currently *Accepting from public*, expiring 2030; five *EXPIRED*) plus the
  **annual composting report-years** (yard-waste in / finished-compost out
  tonnages). R1-adjacent — the compost activity sits on the expansion-facing
  parcel (WDS 465941 carries no independent disposal permit; the activity is
  registered here on the landfill's own 475946 record — see the crawl map).

## Feasibility gate (live, 2026-09-13)

Confirmed BOTH sections exist and parse for 475946 before building (the same
live-gate discipline the nSITE-profile builds used):

- **Penalties** render as nested `<tr ...SummaryRow>` rows under each compliance
  action's `DetailEditRow` on the SAME `Cme/ComplianceActions.aspx` page
  compliance_actions already reads. A document-order walk pairs each penalty row
  `[ , Penalty Type, Assessment Amount, Document #, Payment ID, ]` with a
  following payment row `[Sched Date, $Sched, Date Paid, $Paid]`. Porting the
  hand-verified Lotext `scripts/wds_scrape_penalties.py` reproduces the six-row
  penalty CSV **exactly** ($447,485.46).
- **Composting** lives on `Utilization/Default.aspx?w=475946` as two inline grids:
  `Reg` (6 registration rows — clean single-value detail spans: receipt date,
  status, expiration, admin-completeness) and `RptYr` (report-years: **10
  displayed** 2016→2025 + a `Year:0` add-a-record template; `FilteredCount=18` —
  the same "counts more than it displays" quirk Applications has, harmless for a
  forward-looking watch).

## Decision

Extend Stream C with **three new collections** in the existing `COLLECTIONS` dict
and `wds_client.FETCHERS` — **no fork**, reusing the whole diff engine (rules
A/B, mutation-hash, over-cap), the event router (`_wds_on_row` → `WDS New
Documents` / `WDS Evidence by Risk`), and the digest path. No new tab, workflow,
or secret.

1. **`penalties`** — identity `(Action Date, Penalty Type, Document #, Assessment
   Amount)`; content `(Scheduled Date/Amount, Date Paid, Amount Paid)`. Classifier:
   a **new** penalty is **notable (R2)**, a **changed** one (payment backfilled) is
   **watch** (good news).
2. **`composting_registrations`** — `Reg` grid; identity `(Application Receipt
   Date)`; content = status/expiration/admin-completeness. New = **notable (R1)**,
   in-place change = **watch**.
3. **`composting_reports`** — `RptYr` grid; identity `(Year)`; content = an opaque
   tonnage fingerprint. Always **watch (R1** refresh).

### Why penalties are *notable*, not *urgent* (the key design decision)

Penalties are a sub-grid of the Compliance Actions page. When a penalty-bearing
enforcement action arrives (a violation notice / compliance order / consent
decree / FINAL MONETARY penalty), it is simultaneously a new `compliance_actions`
row — and that classifier **already fires URGENT** on exactly those strings. If
`penalties` were also urgent, one real event would send **two** urgent emails.
So compliance_actions stays the same-day enforcement trip-wire, and the penalty
adds its dollar figure to the weekly digest instead. (This is the same
overlap-disambiguation discipline Stream O used for its ROP-permit overlap.) A
penalty attached to an *already-seen* action still surfaces here — as a digest
line, proportionate since the enforcement urgency already fired when the action
first appeared.

### RptYr fingerprint (why a dedicated parser)

`_detail_rows` last-wins-flattens the repeated `Product Types` spans in a
report-year row, which would silently drop every tonnage. `_parse_composting_reports`
collects **all** span values under each `RptYr_R_ctl<NN>_` row in document order
into one opaque `Report Detail` string — a new year is new; a later tonnage
backfill is a changed content-hash. The `Year:0` template row is filtered by an
explicit 4-digit-year check (`"0".strip()` is truthy, so the generic
identity-emptiness guard would not drop it).

### Identity-collision guard

Two penalties can share (Action Date, Document #) — the real 5/25/2023 pair on
doc 115-05-2023, an FA ($15,300) and an AC ($1,424.46). Penalty Type + Assessment
Amount are in the identity so they track as two distinct records and never flap
"changed" against each other — the same collision class the 2026-07-22
compliance_actions identity fix addressed, with a regression test
(`test_penalties_sharing_date_and_doc_dont_collide`).

### Archiver URL-dedup

`wds_archiver.py` snapshots each collection's page. Since penalties share the
ComplianceActions page and the two composting grids share the Utilization page,
the archiver now de-dups by resolved page URL (`wc.page_url`) so each distinct
page is snapshotted once — the Utilization page gains portal-drift insurance, the
ComplianceActions page is not double-snapshotted.

## Activation

`wds.enabled` is **already `true`** (Stream C is live), so per `overnight-coder.md`
Step 3 this is a **live-path change**, verified against a real specimen — not a
mocked-green merge. The three names are added to `wds.collections`, so they go
live on the next scheduled `watcher.py` run. Two safety properties make that
flood-safe and low-blast-radius:

- **Rule B (silent first-baseline):** the first run records every current record
  and alerts on none (verified live below).
- **Digest-only:** all three tiers are notable/watch, which route to the **weekly
  digest**, never a same-day urgent blast.

**Live full-`check_wds` dry-run (2026-09-13, real portal):** run 1 baselined
penalties 6 / composting_registrations 6 / composting_reports 10 silently — **0
emails, 0 Sheet rows, 0 digest items**; run 2 on the same state produced **0
events** (no flap). 1555 tests green.

**Rollback lever:** remove the three names from `wds.collections` — the code paths
stay, they just go unpolled. No flag flip touched (`wds.enabled` on/off is
Trisha's).

## Residual risks (accepted)

1. *Legacy-portal drift* — same as ADR 009: a markup change fails loudly (fetch
   error → skip-and-warn, never a false "records deleted"); the archiver's raw-HTML
   snapshots are the recovery reference. **Mitigation shipped:** the Utilization
   page is now snapshotted.
2. *RptYr display window* — WDS shows ~10 of 18 counted report-years; the watch is
   forward-looking (a *new* year appearing), so the non-displayed older years being
   absent from the baseline is harmless. Documented so it isn't mistaken for the
   watch missing rows.
3. *Single-field registration identity* — `(Application Receipt Date)` assumes one
   registration per application (true for the six on file). A hypothetical
   same-day collision would at worst produce one spurious "changed"; the content
   diff, not identity, absorbs it — same tolerance the `annual` (Year-only) and
   `qmr` collections already accept.
