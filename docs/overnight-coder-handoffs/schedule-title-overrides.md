# Overnight-coder handoff — Real display names + priority tier for substantive EGLE correspondence mislabeled "Schedule -…"

*Opened 2026-09-01 (Trisha-directed, from the June-16 SEM session). Read `docs/overnight-coder.md`
first. This is a LIVE-PATH change (it edits classification/display in the daily pipeline and the
digest) and it has a one-time live-Sheet backfill component, so per Step 8 open a **DRAFT PR for
Trisha's review, not an autonomous merge**, and the Sheet backfill stays GATED on Trisha's explicit
go. Recommended model tier: **Sonnet** (schema + prompt + classifier judgment; not Haiku).*

## Invocation

Branch name suggestion: `schedule-title-overrides`.

## Why (the concrete incident this fixes)

EGLE's nSITE portal files substantive correspondence under generic `docMgmtDocDescr` titles like
`Schedule - Air General Compliance Report` and `Schedule - CJ-2020-0593-CE General Report`. The
monitor copies that title verbatim into the Sheet, the Archived-PDFs row, the digest line, and the
public feed. Result: important letters hide in plain sight.

Trigger case (2026-08): doc `5234117446316116012` (filed 2026-08-26) is the **August 27, 2026 EGLE
letter approving GFL a 120-day extension (to Dec 19, 2026)** for perimeter-monitor action-level
corrective actions in Cells 6A/6B (Consent Judgment No. 2020-0593-CE, Subparagraph 5.5(E)); doc
`5149154739899361698` is its EGLE cover email. Both were titled `Schedule - Air General Compliance
Report`, classified severity `notable` / `procedural`, and went out in the **Sunday 2026-08-30
digest** as two soft lines under "ACTION ITEMS," buried between wetland JPA applications and leachate
correspondence. The one-line summary led with the exceedance facts, not the actual news (that EGLE
granted a 120-day extension). A community member emailed the same letter around two days later; it
had not announced itself.

This is not a one-off. A read-only scan (2026-09-01) of **every** `Schedule - Air General Compliance
Report` (4) and `Schedule - CJ-2020-0593-CE General Report` (4) doc at N2688 found **8 of 8 are
substantive EGLE regulatory correspondence** (5 full letters + 3 cover/transmittal emails), **0
genuine schedule stubs**. Four of the eight are 120-day extension approvals for corrective actions,
a pattern worth surfacing rather than burying.

## The change

### (a) A `doc_id -> display_name` override map (the durable, deterministic fix)

- Add a `document_title_overrides:` map to `config.yml` keyed by nSITE `doc_id` -> corrected display
  name. Consult it in the normalize/display path (candidates: `nsite_client._normalize` sets
  `document_name`; or apply at `sheet_writer` write time so the same corrected name flows to the New
  Documents row, the Archived PDFs row, the digest line, and `gen_findings_feed`/`public_comment`
  output). Pick the single chokepoint that reaches all four surfaces; add a test that a mapped
  `doc_id` renders the override everywhere.
- **Display-only.** The override changes ONLY the shown name. It must never change `doc_id`, the
  dedupe/state key, or the source `doc_url` (so no re-processing, no duplicate rows, links unchanged).
- Seed the map with the 8 confirmed docs in the table below.

### (b) A lightweight classifier so FUTURE correspondence is caught without a manual override

- Detect EGLE approval / extension / corrective-action / enforcement letters from content signals in
  the extracted text or the parser summary: e.g. "Request for Extension", "corrective action(s)",
  "approve(s)/approval", "Consent Judgement"/"Consent Judgment", "action level exceedance(s)", HOV /
  "Higher Operating Value" waiver, standard EGLE letterhead markers, sender EGLE AQD (Dawn Hayslip
  transmittals are a reliable cover-email signal). Keep it a small, testable pure function
  (mirror `email_alerts.is_urgent`'s pure-and-unit-tested style).
- On a match with no explicit override, **synthesize a real display name** (e.g. from the SUBJECT
  line of the letter, which the parser already has in `full_text`).
- **Bump it off the soft digest line.** See the open decision below on which tier.

### (c) Tighten the summary so an approval/extension is stated AS such

- For a matched correspondence letter, the parser's `summary` / `key_data_point` should **lead with
  the disposition**, not the background facts. Target phrasing for the trigger case:
  *"EGLE approved a 120-day extension (to Dec 19, 2026) for perimeter-monitor corrective actions in
  Cells 6A and 6B (Consent Judgment 5.5(E))."* Adjust the classification prompt/schema in
  `egle_doc_parser.py` so a correspondence-class doc states the ruling first; keep the exceedance
  facts as secondary context.

### (d) One-time cleanup of the already-filed docs (GATED on Trisha)

Apply the override + re-summary to the 8 existing rows (New Documents / Historical Documents +
Archived PDFs). **Live-Sheet writes are gated on Trisha's explicit go** (the case-file Sheet is a
public surface). Prepare the change; do not write cells until cleared.

## Rename map — the 8 confirmed docs (all N2688; verified 2026-09-01, read-only)

| doc_id | filed | current title | true content | proposed display name |
|---|---|---|---|---|
| `5234117446316116012` | 2026-08-26 | Schedule - Air General Compliance Report | Aug 27 2026 AQD letter: 120-day extension (to Dec 19 2026), perimeter-monitor exceedance corrective actions, Cells 6A/6B vacuum lines | EGLE letter: 120-day perimeter-monitor extension approval (Cells 6A/6B) |
| `5149154739899361698` | 2026-08-27 | Schedule - Air General Compliance Report | EGLE cover email transmitting the Cells 6A/6B extension approval | EGLE cover email: perimeter-monitor extension approval (Cells 6A/6B) |
| `5130343428992210792` | 2025-12-11 | Schedule - CJ-2020-0593-CE General Report | Dec 11 2025 AQD letter: 120-day extension (to Apr 4 2026), perimeter-monitor exceedance corrective actions, Cell 5C vacuum lines | EGLE letter: 120-day extension approval, perimeter monitor (Cell 5C) |
| `-2622199756316422732` | 2025-06-09 | Schedule - CJ-2020-0593-CE General Report | Jun 9 2025 AQD letter: 120-day extension (to Sept 27 2025), forcemain corrective actions at 2 VGC wells (AH148BR2, AHWW0208) | EGLE letter: 120-day extension approval, VGC corrective (2 wells) |
| `3589987381183586634` | 2025-03-06 | Schedule - CJ-2020-0593-CE General Report | Mar 6 2025 AQD letter: 63-day + 120-day extensions for corrective actions at 29 VGC wells | EGLE letter: extension approval, VGC corrective actions (29 wells) |
| `-4679635065984934930` | 2025-03-06 | Schedule - CJ-2020-0593-CE General Report | EGLE cover email transmitting the VGC extension-request approval (pairs with the 29-well letter) | EGLE cover email: Consent Judgement extension-request approval |
| `8510351745791368151` | 2026-04-02 | Schedule - Air General Compliance Report | Apr 7 2026 AQD letter: 2-year renewal (to May 1 2028) of HOV temperature waivers for 16 WOI wells/sumps; sets semi-annual WOI reporting | EGLE letter: 2-year HOV temperature-waiver renewal (16 WOI wells/sumps) |
| `-2935564993291663255` | 2026-04-07 | Schedule - Air General Compliance Report | EGLE cover email transmitting the HOV temperature-waiver renewal approval | EGLE cover email: HOV temperature-waiver renewal approval letter |

*(Proposed names use plain hyphens only; the EGLE source subject lines use en-dashes, deliberately
not copied. No em-dashes.)*

## Guardrails

- Additive and config-driven, matching the existing watcher patterns. No change to dedupe/state keys.
- The classifier must **fail safe**: a false negative just leaves today's soft line (no regression); a
  false positive should at most over-promote a routine doc, so keep the promoted tier non-spammy (see
  open decision 1).
- Preserve the source link and `doc_id` untouched. The override is display metadata only.
- Tests: override-map rendering across all four surfaces; classifier pure-function unit tests (the 8
  positives here + a couple of genuine schedule/DMR negatives from the RA facility as controls).

## Definition of done / open decisions for Trisha (surface, do not decide)

1. **Tier choice.** Two options: (A) a new distinct **"Correspondence & enforcement"** section pinned
   at the TOP of the digest (recommended: visible, but does not blast the coalition list a same-day
   email for every letter); or (B) extend `email_alerts.is_urgent` so a correspondence-class doc fires
   a same-day email. Recommend A, with a config lever to escalate specific classes (e.g. enforcement
   / violation letters) to same-day. Ship neither as auto-merged until Trisha picks.
2. **Sheet backfill.** Whether to write the 8 corrected names into the live public Sheet now (gated).
3. **Classifier breadth.** Just EGLE letters/emails, or every substantive doc filed under any generic
   `Schedule -…` title (the scan shows N2688's `Schedule - Air General…` and `Schedule -
   CJ-…General Report` are 100% substantive; DMR / Test Plan / SWPPP schedule titles at other
   facilities are genuine stubs and should stay excluded).

## Related

- Decision record: this handoff doubles as the note documenting the mislabeled-title problem.
- Lotext session context: the June-16 SEM close-out work, the FOIA wishlist
  (`documents/arbor-hills/arbor-hills-foia-wishlist.md`), and the two corrective-action flowcharts.
