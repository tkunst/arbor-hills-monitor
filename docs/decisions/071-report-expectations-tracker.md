# ADR 071 — Expected reports, arrivals, and a "missing and overdue" log, for any number of landfills

*Drafted 2026-10-20 in Lotext from the 10/20 report-inventory session (Arbor Hills + Carleton Farms). Status: **APPROVED by Trisha 2026-10-20** (open questions answered; see the end). For handoff to the overnight coder as `coder:report-expectations-tracker`. The coder copies this file to `docs/decisions/071-report-expectations-tracker.md` (renumber to the next free ADR if 071 is taken at merge) as the PR's first commit. Ships disabled. This text is written to be public-safe for the monitor repo: no private names, no home details, no unreviewed claims.*

---

## Context

The monitor logs everything that arrives (nSITE documents, WDS records, hand-curated files, GovQA releases, Ridge Wood reports, perimeter episodes). The project also keeps hand-built lists of what *should* arrive. Nothing compares the two.

What that cost us, found in one session on 2026-10-20:

- Two Consent Judgment quarterly boundary-probe reports (2024 Q1, 2025 Q4) and two CJ semi-annual reports (2025 H2, 2026 H1) were missing, and no system had flagged them.
- For a peer landfill (Carleton Farms, N5986), about 70 scheduled reports were due 8/19/2019 to 10/20/2026 and 2 were held. Counting that took hours of hand work.
- The same Arbor Hills report series are counted by hand in four Lotext files, on different dates, and one (the HOV index §7c) was already wrong.
- Some reports are owed only when a condition is met (a well reaching a temperature, a probe exceeding a limit). No list holds those at all.

The project will cover more landfills (the statewide store, peer comparisons), so the design must not be Arbor Hills-specific.

## Decision

Add one new capability, `report_tracking`, built from four tables and one daily job. Facility is a column everywhere; nothing is keyed to Arbor Hills.

### Tables (PRIVATE Sheet, `GSHEET_ID_PRIVATE`, for v1)

Private (decided 2026-10-20) because the Missing & Overdue log carries our FOIA request IDs and advocacy priorities. A public view can be a later ADR.

**1. `Report Facilities`** (hand-edited registry). One row per tracked landfill.
`facility_id` (short stable key, e.g. `arbor-hills`, `carleton-farms`) · `name` · `operator` · `air_srn` (e.g. N2688, N5986) · `wds_id` (e.g. 475946, 390701) · `nsite_site_id` · `county` · `role` (`primary` / `peer`) · `arrival_streams` (which monitor streams can see this facility's records) · `active` · `notes`.

**2. `Expected Reports`** (hand-edited schedule; the single source of truth for what is owed).
One row per report *type* per facility.
`schedule_id` · `facility_id` · `report_type` (controlled vocabulary, below) · `report_name` (as the regulator titles it) · `obligation_source` (permit/order/letter + section, e.g. "CJ 2020-0593-CE ¶5.12(D)", "ROP GC 23", "HOV Plan rev. Feb 2025 p.2") · `citation_confidence` (`primary` = we read the obligation text; `secondary` = from a summary) · `recipient` (AQD / MMD / WRD / EPA) · `cadence` (`monthly`, `bimonthly`, `quarterly`, `semiannual`, `annual`, `one-time`, `event`) · `period_basis` (`calendar`, `fiscal`, `rolling`) · `due_rule` (machine-readable, below) · `grace_days` (blank = use the global default, `report_tracking.default_grace_days`, set to **7** in config.yml; a number here overrides it for that report type; both can be changed at any time and take effect on the next run) · `first_period` · `last_period` or `end_condition` · `match_rule` (how an arrival is recognized, below) · `active` (Y/N; N = known obligation with unverified cadence, kept visible but not expanded) · `notes` · `edited_by` · `edited_at`.

`due_rule` grammar (small and explicit; reject anything else at load):

- `period_end+Nd` (e.g. `period_end+45d`)
- `fixed:MM-DD` per period half (e.g. `fixed:03-15,09-15` for the federal semi-annual)
- `next_month_day:D` (e.g. `next_month_day:15`)
- `on:YYYY-MM-DD` (one-time)
- `within:Nh` / `within:Nd` after a trigger event (event cadence only)

**3. `Trigger Rules`** (hand-edited). Conditions that create an obligation.
`rule_id` · `facility_id` (or `*` for every facility under the same federal rule) · `condition` (machine-readable over existing monitor data: e.g. `wellhead_temp_f >= 165`, `wellhead_temp_f > 145 AND no_hov(well)`, `wellhead_temp_f >= 170 AND co_ppmv >= 1000 AND no_hov(well)`, `probe_lel_pct > 100`) · `input` (Measurements / WOI Well Summary / Perimeter Action-Level Episodes / Compliance Deadlines) · `creates` (a `report_type` or a monitoring obligation) · `due_rule` · `repeat` (e.g. `annual while condition holds`) · `stop_condition` · `citation` · `citation_confidence` · `notes`.

**4. `Report Arrivals`** (written by the job; hand rows allowed). One row per record matched to an expected report instance.
`arrival_id` · `facility_id` · `instance_key` (below) · `report_type` · `period` · `source_stream` (A nSITE / C WDS / hand-curated / GovQA / Ridge Wood / email-hand) · `source_ref` (doc id, URL, FOIA E-number) · `doc_date` · `filed_date` · `first_seen_at` · `match_method` (`rule` / `hand`) · `match_confidence` (`high` / `proposed`) · `notes`.

**5. `Missing & Overdue Log`** (written by the job; FOIA and date columns hand-edited).
`instance_key` · `facility_id` · `report_name` · `period` · `due_date` · `date_added` · `foia_request_id` · `foia_date_filed` · `date_received` · `received_via` (arrival_id or `hand`) · `notes`.
Rules:

- **Never delete a row.** An item is **complete for collection purposes when `date_received` holds a real date before today.** "Subtractions" in reports mean rows that became complete since the last run.
- The FOIA columns are held for hand entry (and later automation from GovQA Archive Watch, matching our own E-numbers).
- One row per `instance_key`, ever. Re-running never duplicates.

`instance_key` = `facility_id|report_type|period` (e.g. `arbor-hills|cj-quarterly-probe|2024Q1`, `carleton-farms|hov-aoi-semiannual|2025H2`, `arbor-hills|nsps-neshap-semiannual|2023H1`). Triggered instances add the trigger subject: `arbor-hills|downhole-profile|AHW272R4|2025`.

### Daily job (`report_tracker.py`, its own workflow, own concurrency group)

Order matters:

1. **Load** the four hand tables; validate every `due_rule`, `match_rule` and `condition`. A bad row is logged to the owner weekly email and skipped, never guessed.
2. **Expand** every active `Expected Reports` row into instances with due dates from `first_period` through today (+30 days for the "coming due" view).
3. **Evaluate triggers** against the existing tables (Measurements, WOI Well Summary, Perimeter Action-Level Episodes, Compliance Deadlines) and add triggered instances with their due dates.
4. **Match arrivals.** For every record first seen since the last run, in every arrival stream, try each `match_rule` for that facility. A high-confidence match appends a `Report Arrivals` row. Then check the Missing & Overdue Log *first*: if that `instance_key` is on the log with a blank `date_received`, fill `date_received` (= `filed_date` if known, else `first_seen_at` date) and `received_via`. A `proposed` match is never auto-applied; it goes to the owner weekly email for a hand decision.
5. **Find missing.** Every instance whose `due_date + grace_days` < today and that has no arrival is missing. For each: check the log; if its `instance_key` is **not** already there, append it with `due_date` and `date_added` = today; FOIA columns and `date_received` blank. If it is already there, leave it.
6. **Summarize** to a `Report Tracker Runs` tab: run date, open count (rows with no `date_received`, or a future one), added today, completed today, per facility. This row is what the briefing reads.

The job is read-only on every existing tab. It writes only the new tabs.

### `match_rule` (per Expected Reports row)

A small structure, not free code: `stream` + `title_regex` + optional `doc_type` + a `period_from` extractor (named patterns: `year_quarter_words` for "2025 Third Quarter Report", `year_half_words` for "2026 First Semi-Annual", `month_year` for "(October 2025)", `date_range` for "010125 through 063025"). Seed patterns from real titles (fixtures below). Arrivals that match no rule but come from a watched facility and look like a periodic report (title contains Report/Semi-Annual/Quarter/Monthly) go to an "unmatched periodic" section of the owner weekly email, so the schedule gets fixed instead of the record being lost.

### Morning briefing (Lotext side)

- Add infra-snapshot **Check 16** (`run_infra_checks.sh`): a read-only `scripts/report_tracker_summary.py --json` reads the latest `Report Tracker Runs` row. Output line: `Missing/overdue reports: N open (+a added, −c completed since <date>) · <per-facility counts>`.
- The briefing folds a no-change day into the green roll-up (`report tracker: N open, no change`) and breaks out any additions or completions with their `instance_key`s.
- This Lotext edit is a separate commit by path, and the PR says so.

## Validated trigger frequencies (from this session's reading of the rules)

Trisha asked whether a well at 165°F or higher triggers a *weekly* downhole profile. The rules say otherwise, and the difference matters for the trigger table:

| Condition | What it triggers | Frequency | Source |
|---|---|---|---|
| Wellhead over 145°F, **no** HOV | Enhanced monitoring: visual check for subsurface oxidation, O2, wellhead temperature, temperature every 10 vertical feet (by cross-reference to (a)(6)), methane, CO | **Weekly**, starting 7 days after the first reading over 145°F; may drop to monthly after four weekly CO readings under 100 ppmv; stops when an HOV is approved or the well returns to 145°F or below | 40 CFR 63.1961(a)(5)(i)–(ix); Carleton ROP SC VI.4 |
| Wellhead at or above 165°F | Temperature every 10 vertical feet (downhole profile) | **Annually** | 40 CFR 63.1961(a)(6); Carleton ROP SC VI.5 |
| Arbor Hills WOI well over 165°F (HOV condition) | Downhole temperature profile | Quarterly for one year (per the HOV index; **verify against the 4/30/2024 grant letter before seeding**); EGLE confirmed 8/23/2026 that the trigger still applies, though it no longer collects the profiles | AH HOV approval letters |
| At or above 170°F **and** CO at or above 1,000 ppmv, no HOV | Email report to EGLE | **Within 24 hours** | 40 CFR 63.1981(k); Carleton ROP p.45 |
| Over 145°F not corrected in time | Root-cause analysis; corrective-action plan and timeline (plan due no later than 75 days after the first reading at or above 145°F when correction takes longer) | per rule | 40 CFR 63.1960(a)(4), 63.1981(j)(2) (**verify exact day counts against eCFR when seeding**) |
| Arbor Hills boundary probe over the LEL | Weekly probe monitoring until each probe has a clean year; quarterly report | weekly / quarterly | CJ 2020-0593-CE ¶5.12 |
| Methane at or above the LEL at the property boundary or in a structure (any Part 115 landfill) | Notification to EGLE MMD | per R 299.4433(4) (**verify timing when seeding**) | Part 115 Rule 433(4) |

So "weekly" is right for wells over 145°F without an HOV (and that weekly list includes the 10-foot profile by cross-reference), but the explicit 165°F downhole rule is annual. A well with an HOV leaves the weekly enhanced monitoring and follows its HOV letter's conditions instead, which is why the HOV letters themselves belong in `Trigger Rules` with `citation_confidence = primary` only after they're read.

## Seed data (from the 2026-10-20 inventories)

The coder seeds the hand tables from these Lotext files and marks every row `citation_confidence` honestly:

- `documents/arbor-hills/SET-report/C6-peer-carleton-farms-N5986.md` §7 (Carleton) and §7b (Arbor Hills): counts, schedules and held/missing for 8/19/2019 to 10/20/2026.
- `documents/arbor-hills/analysis/semcog-landfill-etlf-comparison-2026-09-21/carleton-hov-timeline-2026-10-20.md`: Carleton plan dates (approved 6/12/2023, revised 2/3/2025), report contents and due rules.
- `documents/arbor-hills/arbor-hills-HOV-ETLF-CO-index.md` §1, §2, §7a, §7b: Arbor Hills WOI cadence history and HOV letters.
- `documents/arbor-hills/analysis/gas-probe-history-2026-10-03/reports.txt`: the 15 CJ quarterly reports held (nSITE ids), so their arrivals backfill.
- `documents/arbor-hills/arbor-hills-monitoring-obligations-inventory.md`: obligation sources and end dates.

Seed rules:

- Rows whose cadence isn't verified (Arbor Hills WOI 2019–2021, Gas Extraction Exceedance reports, pre-plan Carleton HOV letters) go in with `active = N` and a note. They don't expand into instances until a human sets `active = Y`.
- Backfill `Report Arrivals` for everything we already hold, so the first run's Missing & Overdue Log lists only real gaps. Expected first-run open items (approximate; the job's count wins): Arbor Hills: CJ semi-annual 2025 H2 + 2026 H1; CJ quarterly 2024 Q1 + 2025 Q4; NSPS/NESHAP semi-annual 2019 H1 through 2024 H1 (12); annual certifications not yet checked (7). Carleton: monthly HOV reports (38), AOI semi-annual (**6, from 2023-H2**, with the note "AOI tier may have begun with the Feb 2025 revision; if EGLE confirms, set 2023-H2 to 2024-H2 to no-record"), NSPS/NESHAP semi-annual (15), annual certifications (7), plan approvals (2).
- Known FOIA links to pre-fill: Arbor Hills boundary-methane notifications → `E700318-100826` (granted 10/15/2026, deposit pending).

## Fixtures (real titles; tests must parse these)

- `2025 Third Quarter Report - CJ No. 2020-0593-CE` → `cj-quarterly-probe`, `2025Q3`
- `2023 Second Quarter Report - CJ No. 2020-0593-CE` → `cj-quarterly-probe`, `2023Q2`
- `2026 First Semi-Annual WOI Status Report` → `woi-status`, `2026H1`
- `AAAA Semi-Annual NESHAP 070125 through 123125` → `nsps-neshap-semiannual`, `2025H2`
- `Carleton Farms Landfill (CFL) HOV Traditional Well Monthly Status Report (October 2025)` → `hov-traditional-monthly`, `2025-10`
- `N5986 CFL HOV Plan rev. February 2025` → not a periodic report (plan document; `one-time`)
- Negative: `On-Site Inspection (08/19/2026)` (EGLE's own evaluation, not an operator report), `N5986 CFL HOV Map June 2026` (an attachment, not the AOI report).

## Controlled `report_type` vocabulary (v1; extend by ADR amendment)

`woi-status` · `cj-semiannual` · `cj-quarterly-probe` · `cj-perimeter-rca` · `nsps-neshap-semiannual` · `rop-annual-certification` · `rop-deviation-semiannual` · `hov-request` · `hov-approval` · `hov-plan-approval` · `hov-traditional-monthly` · `hov-aoi-semiannual` · `gas-extraction-exceedance` · `downhole-profile` · `neshap-24h-170f-co` · `rule-433-notification` · `hmp-quarterly` (groundwater QMR) · `annual-landfill-report` · `ridgewood-monthly`.

## What happens to the existing trackers (`documents/arbor-hills/arbor-hills-report-tracking-INDEX.md`)

Principle: **arrival logs and catalogs survive and become inputs. Hand-kept "held vs. missing" judgments retire to a pointer at the Missing & Overdue Log, once the tracker is live and has run for two weeks with no correction needed.** Nothing is deleted; retired sections get a one-line pointer and keep their history.

### Monitor side (Index §A): all survive

| Item | Fate | Role in the new design |
|---|---|---|
| New / Historical Documents, Archived PDFs | **Survives** | Arrival source (Stream A) |
| WDS tabs, `_wds_seen` | **Survives** | Arrival source (Stream C: QMR, annual landfill reports, compliance actions) |
| Hand-Curated Files | **Survives** | Arrival source for reports received by email or FOIA |
| GovQA Archive Watch (private) | **Survives** | Arrival source for FOIA releases; later fills the FOIA columns |
| RRD Documents (private), Ridge Wood Reports / Revisions | **Survives** | Arrival sources |
| Compliance Deadlines | **Survives** | Trigger input: one-time obligations extracted from documents become triggered instances |
| Measurements, WOI Well Summary, Perimeter Action-Level Episodes | **Survives** | Trigger inputs (temperature, CO, action-level events) |
| Evidence by Risk, Risk Register, Related Documents | **Survives** | Not used by v1 |
| Submissions / Violations / Compliance Actions / Evaluations / Permits / Complaints / Public Notices / ROP / MMD / RIDE Watch tabs | **Survives** | Change logs; v1 doesn't match them as report arrivals (they're EGLE's records, not operator reports). Evaluations and Compliance Actions may become trigger inputs later |
| PFAS, MPART, GFL Info Site, EGLE Newsroom, Meeting Watch, MMPC/CivicClerk archives, GFL Air | **Survives** | Not report arrivals; unchanged |
| Drive mirrors, site Public Records pages, GitHub Releases, repo provenance files, digest + owner emails | **Survives** | Unchanged; the owner weekly email gains the "proposed matches" and "unmatched periodic" sections |

### Lotext side (Index §B): survive vs. pointer

| Item | Fate | Why |
|---|---|---|
| `arbor-hills-report-coverage-matrix.md` | **Survives** (catalog of report types × monitor coverage); its held/missing remarks → **pointer** | Seeds `Expected Reports`; type catalog stays useful |
| `arbor-hills-HOV-ETLF-CO-index.md` §7c (held vs. missing table) | **Retires → pointer** | Exactly what the log replaces; already stale |
| same §1, §2, §2d, §7a (letters, report types), §7b (data coverage by period) | **Survives** | Data coverage ≠ report arrival; letters are trigger citations |
| `arbor-hills-monitoring-obligations-inventory.md` | **Survives** + pointer to `Expected Reports` | Explains *why* each obligation exists and when it ends; it's the citation source |
| `draft/DRAFT-INTERNAL-arbor-hills-hmp-report-inventory-2026-08-01.md` | Held/missing part → **pointer**; plan description **survives** | |
| `analysis/gas-probe-history-2026-10-03/README.md` "Missing reports" list | **Pointer**; the analysis **survives** | |
| `arbor-hills-ROP-permit-tracker.md` | **Survives** | Tracks permit renewal process and comment windows, not periodic reports |
| `arbor-hills-key-dates.md` | **Survives**; report-due rows → **pointer** | Timeline stays; due dates come from the schedule |
| `SET-report/C6-peer-carleton-farms-N5986.md` §7, §7b | Tables → **pointer** + one-line generated summary | Counts become a view of the log |
| `wellfield-auto-staging/` ledger + MANIFEST | **Survives** | Tracks parsing into datasets, a different question |
| `analysis/.../carleton-hov-report-coverage.md` | **Pointer** (superseded) | Its 39-month grid becomes Carleton schedule rows |
| `analysis/.../carleton-hov-timeline-2026-10-20.md` | **Survives** | Citations and FOIA wording for Carleton rows |
| `analysis/.../peer-data-coverage-by-half-year-2026-09-25.md` | **Survives** | Data coverage, not report arrival |
| `data/egle-foia-archive/INDEX.csv`, `statewide-landfill-data/foia-archive/README.md` | **Survives** | Catalogs of FOIA releases (arrival evidence) |
| `arbor-hills-foia-wishlist.md` | **Survives** + cross-reference | Wishlist items for scheduled reports cite `instance_key`s; the log's FOIA columns link back |
| liner checklist, curation wishlist, the 7/24 CJ-coverage note, 10/02 records-request draft | **Survives** | One-off asks, not schedules |
| `source-docs/INDEX.md`, FCE package INDEX, `hov-etlf/README.md`, Drive case-file INDEX, `dedupe-curate-ledger.md`, `DATA-SET-INVENTORY.md`, A1, SEM index, MMPC minutes index | **Survives** | Catalogs of holdings; `dedupe-curate-ledger.md` is also an arrival source via Hand-Curated |
| violations ledger, compliance-actions ledger, H2S exceedance ledger, SEM coverage gap, communications log | **Survives** | Event records; may feed triggers later (e.g. Rule 433). A report received by email must also be entered as Hand-Curated or a hand Arrival row |

After go-live, `arbor-hills-report-tracking-INDEX.md` gets a top line pointing to the Missing & Overdue Log as the one held/missing record.

## Consequences

- One place answers "what's missing, since when, and have we asked for it." Gaps surface the day after they're due.
- Multi-landfill from day one: a new landfill is a `Report Facilities` row plus schedule rows. No code change.
- Peer facilities the monitor doesn't poll (Carleton today) still get schedules and a log; their arrivals come from hand rows and GovQA until a stream covers them.
- Risks:
  - **False "missing" from a bad match rule.** Mitigation: high-confidence auto-match only; proposed matches go to the owner email; the first two weeks run in report-only mode (log written, briefing line marked "shadow").
  - **Schedules drift from the real obligation.** Mitigation: `citation_confidence` on every row; `secondary` rows are flagged in the weekly email until upgraded.
  - **Cell or volume limits.** At about 60 months × 10 types × N facilities, rows stay in the low thousands; append-only, no JSON blobs in cells.
  - **Reports that legitimately don't exist** (a "no exceedance" month). Mitigation: the log keeps them; the `notes` column records EGLE's "no records" answer, and `date_received` stays blank. A `no-record` value in `received_via` (with a date) closes them for collection purposes.

## Goal (paste this)

```
Follow docs/overnight-coder.md. Goal + full spec: /Volumes/Samsung-Pro-2TB/Lotext/documents/arbor-hills/report-tracking/ADR-071-DRAFT-expected-reports-and-missing-overdue-log.md -- read that file first, it IS the goal. Copy it to docs/decisions/071-report-expectations-tracker.md (next free ADR number if 071 is taken) as the PR's first commit.

In one line: build report_tracker.py + its own workflow (ships disabled behind report_tracking.enabled: false) that keeps five PRIVATE-Sheet tabs (Report Facilities, Expected Reports, Trigger Rules, Report Arrivals, Missing & Overdue Log) plus a Report Tracker Runs summary; expands hand-edited schedules and trigger rules into due instances; matches every newly arrived record from the existing streams (nSITE, WDS, Hand-Curated, GovQA, Ridge Wood) to instances with high-confidence title/period rules, filling date_received on the Missing & Overdue Log when an item on it arrives; appends never-duplicated missing items with due date and date added (FOIA ID, FOIA date filed, date received columns held for hand entry); and writes a daily open/added/completed count. Seed Arbor Hills and Carleton Farms from the Lotext files named in the ADR; mark unverified cadences active=N. Apply the Decisions section (private Sheet; default_grace_days: 7 in config with per-row override; Carleton AOI = 6; agency records are never arrivals) and add the Roadmap entry to docs/roadmap.md. Read-only on every existing tab. Tests: every title in the Fixtures section, due-rule expansion edge cases (fixed 03-15/09-15, period_end+45d, next_month_day:15), never-duplicate on re-run, completion when date_received < today. Then, as a SEPARATE Lotext commit by path: add Check 16 (scripts/report_tracker_summary.py --json) to ~/Library/Application Support/briefing-infra/run_infra_checks.sh and the matching line to skills/morning-briefing/SKILL.md Step 4. Start from a fresh branch off origin/main. End at a DRAFT PR.
```

## Decisions (Trisha, 2026-10-20)

1. **Private Sheet** for v1.
2. **Grace period 7 days**, editable: global default in config (`report_tracking.default_grace_days: 7`) plus a per-row `grace_days` override in `Expected Reports`. Changing either needs no code change.
3. **Carleton AOI reports seeded as 6** (2023-H2 through 2026-H1) with the note above.
4. **EGLE's own records are NOT arrivals.** Inspections, violation notices and other agency records stay out of matching.

## Roadmap (add to `docs/roadmap.md` in the same PR)

- **Violation-closure tracking (future ADR).** Track every violation EGLE records, whether from a Violation Notice or cited in an inspection report, through to its closure: violation date, citation, source document, what EGLE required (response, corrective action, deadline), the operator's response date, and EGLE's closure (date and document, e.g. "Compliance Communication discretionary" or a return-to-compliance date). Flag violations with no closure past their response deadline, the same way this ADR flags missing reports. Inputs already exist: the Violations Watch, Compliance Actions Watch and Evaluations Watch tabs (nSITE), the WDS compliance-action records, and Compliance Deadlines. Multi-landfill like this ADR (facility_id keyed). Example gap it would catch: Carleton's 9/14/2021 R 299.4432 violation, still listed open on EGLE's WDS portal.

---

## Implementation notes (as built, 2026-10-23 — coder:report-expectations-tracker)

The build follows the Decision above; these are the places where the code
is more specific than the draft, or deviates from it, so the ADR matches
what shipped.

**Tabs and columns.** Seven PRIVATE-Sheet tabs
(`sheet_writer.REPORT_TRACKER_TABS`, never in `_TAB_HEADERS`): the five in
the Decision plus `Report Tracker Review` (proposed matches, unmatched
periodic titles, rejected hand-table rows — the owner's weekly review
list) and `Report Tracker Runs`. Two columns are appended LAST, the repo's
convention: `name_aliases` on `Report Facilities` (`;`-separated strings a
record's facility text or title must contain to resolve to that facility)
and `active` on `Trigger Rules`. Headers are reconciled on every run.

**Period strings.** `YYYY-MM` (month), `YYYYBn` (bi-month, n = 1..6 —
Arbor Hills' 2022–2023 WOI cadence needed a kind of its own), `YYYYQn`,
`YYYYHn`, `YYYY` (annual), `YYYY-MM-DD` (one-time / event). The
`instance_key` is as specified.

**`due_rule`.** The grammar is as specified. `fixed:` picks the first fixed
date strictly after the period's last day, rolling into the next year
(`fixed:03-15,09-15`: H1 → 9/15 same year, H2 → 3/15 next year);
`next_month_day:D` clamps D to the month's length; `within:Nh` rounds up to
whole days (the hour count is kept for the note). For a TRIGGERED instance
`period_end+Nd` anchors on the trigger year's end and the other rules on the
trigger date.

**`match_rule`.** `stream=a|b; title_regex=...; [exclude_regex=...;]
[doc_type=...;] period_from=e1,e2,...`. Extractors: `year_quarter_words`,
`year_half_words` (incl. `1H26`, "first half 2024"), `month_year`,
`month_range` ("July–December 2025", "May and June 2022", "Jan 1 - Jun 30
2025"), `date_range` (`070125 through 123125`), `year_in_title` (`FY2024`,
`CY2024`), `date_in_title`, `record_period` (Ridge Wood's Month),
`record_date`, and `prior_{month,bimonth,quarter,half,year}_from_date`
(anchored on a "dated m/d/yyyy" / "(due m/d/yyyy)" in the title first,
then the record's own dates — nSITE's Document Date column was found to
be wrong often enough that the title's date wins). A period must be of the
schedule's own kind. **Window rule (added from the live pass):** a schedule
answers for a period inside its `first_period`/`last_period` window; a
period named in the title's own words outside every window is still a HIGH
arrival, noted "outside the schedule's window" (a 2004 QMR is a QMR, and
nothing is expected for it); a period that only a date-fallback extractor
produced outside the window is PROPOSED. Match rules were checked against
every record on the live case-file Sheet on 2026-10-23 (2,620 records: 277
high, 1 proposed, 26 unmatched periodic titles — DMRs, the SEP annual
report, the EPA ACO monthly reports, candidates for future rows).

**Decision 4 in code.** `report_tracking.agency_title_regex` (inspections,
Violation/Enforcement Notices, Consent Orders, Compliance Communications,
Evaluations, nSITE "Schedule - ..." placeholders, emails) is applied before
any rule. EGLE's HOV approval letters are deliberately NOT in it: the
`hov-approval` / `hov-plan-approval` rows need them as arrivals.
`periodic_title_regex` requires a cadence word (a bare "Report" is not a
schedule).

**Triggers (v1 subset).** `condition` terms: `wellhead_temp_f <op> N`,
`co_ppmv <op> N` (joined to the well's carbon_monoxide readings in the same
calendar month) and `no_hov(well)` (the well id's absence from
`temperature_thresholds.hov_ceilings`); `input` must be `Measurements`;
`repeat` is blank / `once per well` / `annual while condition holds`;
`stop_condition` is text for the reader, not evaluated. `probe_lel_pct` is
not in v1 (no Measurements metric carries it). **Every seeded trigger rule
ships `active = N`** — enabling one is a hand step after reviewing what it
would create against Measurements, the same bar the draft set for seeding.

**nSITE arrivals** come from the New/Historical Documents feed tabs (the
title there is the resolved display name; the doc id is read from an nSITE
Link or looked up through Archived PDFs when the Link was rewritten to the
Drive copy). **GovQA arrivals** are the `file-listed` rows of the private
GovQA Archive Watch tab. **WDS Historical Documents** turned out to carry
real QMR and Annual Landfill Report rows (2002 / FY1996 onward), so those
two rows have working match rules but stay `active = N` until WDS coverage
of recent periods and the annual report's due date are confirmed.

**Owner weekly email.** Rather than editing the live `owner_notices`
weekly (a path already enabled in production), the tracker sends its OWN
owner-only review email on Sundays (or `--email`), through
`owner_notices.send_owner_email`, gated on
`report_tracking.review_email_enabled` (ships false). Folding it into the
housekeeping email is a later, separate change.

**Shadow mode.** `report_tracking.shadow_until` (blank = live): while
today ≤ that date the Runs row's `mode` is `shadow` and the briefing line
carries `[shadow]`. Set it to activation day + 14.

**Seed.** `report_tracker_seed.py` (dry run; `--apply` writes only into
EMPTY private tabs). The seed carries no FOIA request id and no person's
name (a test scans every row against `name_check`'s denylist); the request
id quoted in the Seed-data section above stays as written in the approved
draft, and is otherwise entered only in the private Sheet's
`foia_request_id` column. 2 facilities, 27 Expected Reports rows (14 active),
4 trigger rules (all inactive), 46 hand Report Arrivals rows (every WOI, CJ
semi-annual, CJ quarterly and NESHAP report held on 2026-10-23, plus
Carleton's two monthly reports). Expected first-run open items from the
seed + the live Sheet, computed 2026-10-23: Arbor Hills 20 (CJ semi-annual
2025 H2 + 2026 H1; NESHAP 2019 H1–2024 H1 = 11 halves, not the draft's 12,
plus 2026 H1 is on nSITE; annual certifications 7; CJ quarterly 0 — 2024 Q1
and 2025 Q4 both arrived on nSITE after the 10/20 inventory), Carleton 68
(38 monthly, 6 AOI, 15 NESHAP, 7 certifications, 2 plan approvals). The
job's count wins.

**Morning briefing.** Check 16 in `run_infra_checks.sh` calls
`scripts/report_tracker_summary.py --json` (status `ok | stale | no-runs |
disabled | skipped | error`, always exit 0). `GSHEET_ID_PRIVATE` is not in
the Mac's `.env` today, so the check reports `skipped` until it is added.
`run_infra_checks.sh` lives under `~/Library/Application Support` and is not
git-tracked; only the SKILL.md half of that edit is a Lotext commit.

**Hand-edited dates.** Type `date_received` / `foia_date_filed` /
`first_period` as `YYYY-MM-DD`; a date Sheets auto-formats to `m/d/yyyy`
is read too, but anything else non-blank in `date_received` is flagged in
`Report Tracker Review` (and the row stays open) rather than passing as
blank. A hand-added `Report Arrivals` row closes its open log row on the
next run (received date = the row's `filed_date`, else `doc_date`, else
`first_seen_at`). Trigger rules: `no_hov(well)` is evaluated against the
CURRENT HOV roster in `config.yml`, and a Measurements row with a blank
Facility belongs to the one facility the nSITE stream feeds.

**Activation (Trisha's steps, in order):** `python3 report_tracker_seed.py`
(dry run) → `--apply` → set `shadow_until` → flip `enabled: true` → add
`GSHEET_ID_PRIVATE` to the Mac `.env` for Check 16 → after two clean weeks,
`review_email_enabled: true` and retire the Lotext held/missing tables to
pointers per the table above.
