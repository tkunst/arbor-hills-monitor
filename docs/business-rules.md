# Arbor Hills Monitor — Business Rules Specification

> **Provenance:** Extracted 2026-07-12 by the `code-modernization:business-rules-extractor`
> subagent (read-only), on request, from the live codebase. It documents the
> monitor's *decision logic* — the rules that decide classification, urgency,
> alerting, scheduling, and eligibility — each with a `file:line` citation.
> The **Notes / discrepancies** at the end are *candidates to verify*, not
> confirmed bugs — **except Note 4 (the 0.0-years edge), which has been
> code-verified as a real defect** (`wds_watcher.py:109`, `if yrs and yrs < floor`
> — `0.0` is falsy, so "airspace exhausted" downgrades to `watch`). Otherwise not
> yet human-reviewed: treat this as a map to check against the code, not gospel.

Every rule cites the exact `file:line` read during extraction. Infrastructure
(Sheets/Drive API plumbing, OAuth, crash-safe write ordering, SMTP transport,
connection retries) is excluded except where a business threshold is embedded in
it.

## Summary table

| ID | Rule | Theme | Category |
|----|------|-------|----------|
| RR-1 | R1–R8 risk taxonomy | Risk register | classification |
| RR-2 | R8-heavy signal keywords gate large-doc windowing | Risk register | policy |
| DC-1 | doc_type = evidence / procedural / opinion | Doc classification | classification |
| DC-2 | severity = routine / notable / urgent (145F embedded) | Doc classification | classification |
| DC-3 | Measurement `basis` = measured / permitted_limit / unknown | Doc classification | classification |
| DC-4 | Only valid register IDs survive classification | Doc classification | validation |
| DC-5 | Large docs (>30 pp): cover page + ≤10 keyword pages only | Doc classification | policy |
| UT-1 | Urgent iff parser-urgent OR measured temp ≥ 145F | Urgency & temperature | eligibility |
| UT-2 | Only `measured` temperatures count; permitted ceilings excluded | Urgency & temperature | calculation |
| UT-3 | Stray Celsius readings converted to F before compare | Urgency & temperature | calculation |
| UT-4 | Free-text temp scan is last-resort only; plausible = 50–1000F | Urgency & temperature | calculation |
| AR-1 | Urgent → same-day email; else → pending digest | Alert routing | state-transition |
| AR-2 | Digest sends Sundays only | Alert routing | policy |
| AR-3 | Digest lists procedural action-items first | Alert routing | policy |
| AR-4 | Recipients = config list + private env addresses | Alert routing | policy |
| AR-5 | Evidence fan-out: evidence-type docs only, one row per risk | Alert routing | classification |
| AR-6 | Risk-register tally: count + most-recent date (string compare) | Alert routing | calculation |
| MM-1…5 | *Retired 2026-07-12 (ADR 013) — the MMPC minutes reminder* | MMPC | — |
| MM-6 | MMPC archive: type = Agenda/Minutes/Other, dedup from Sheet | MMPC | policy |
| WD-1 | QMR: statistical exceedance = notable (R5), else watch | WDS classification | classification |
| WD-2 | Application: Construction Permit = urgent (R1), License = notable | WDS classification | classification |
| WD-3 | Annual report: years-remaining < 3.0 = notable (R1) | WDS classification | threshold |
| WD-4 | CME evaluation = watch (R2) | WDS classification | classification |
| WD-5 | Compliance action: violation/penalty = urgent, PAID/RESOLVED = watch | WDS classification | classification |
| WD-6 | Fetch collapse below 50% of last count = bad fetch, skip diff | WDS classification | validation |
| WD-7 | First-ever sight of a collection self-baselines silently | WDS classification | policy |
| WD-8 | >20 alert events in one run → silent re-baseline | WDS classification | policy |
| WD-9 | Re-alert on record mutation (identity + content hash) | WDS classification | state-transition |
| WD-10 | WDS urgent → email; notable/watch → digest | WDS classification | state-transition |
| WOI-1 | WOI reading valid iff CH4+CO2+O2+Balance ≈ 100 (±1.5) | WOI extraction | validation |
| WOI-2 | Row = Well ID + date-time + ≥6 numbers | WOI extraction | validation |
| WOI-3 | Post-adjustment (ADJ) rows skipped; as-found is headline | WOI extraction | policy |
| WOI-4 | Every WOI table reading is `basis = measured` | WOI extraction | classification |
| WOI-5 | Formal WOI list from Attachment 2, not the full wellfield | WOI extraction | classification |
| WOI-6 | CO reading ≥ 10000 ppm dropped as an Excel-serial leak | WOI extraction | validation |
| WOI-7 | CO dedup: keep first reading per (well, month) | WOI extraction | policy |
| WOI-8 | Well IDs canonicalized (strip `*`, apply alias map) | WOI extraction | calculation |
| WOI-9 | Per-well rollup: max temp (hottest-first) + CO rise trend | WOI extraction | calculation |
| BF-1 | Doc eligibility: skip processed / skipped / poisoned | Backfill & anti-stampede | eligibility |
| BF-2 | Poison threshold = 3 failed attempts | Backfill & anti-stampede | threshold |
| BF-3 | Transient (quota/5xx/429) failures accrue no poison strike | Backfill & anti-stampede | policy |
| BF-4 | Backfill batch = 50 docs/run | Backfill & anti-stampede | policy |
| BF-5 | Watcher defers if >25 unprocessed docs (backfill not done) | Backfill & anti-stampede | policy |
| BF-6 | Backfill facility order (Remediation Area first, N2688 last) | Backfill & anti-stampede | policy |
| BF-7 | Terminal-failure docs stubbed + marked skipped (still visible) | Backfill & anti-stampede | state-transition |
| BF-8 | Backfill self-terminates when nothing remains | Backfill & anti-stampede | state-transition |
| PF-1 | PFAS: alert on `<main>` content-hash change | PFAS page-watch | classification |
| PF-2 | PFAS: body <500 bytes or `<main>` <200 chars = bad fetch | PFAS page-watch | validation |
| PF-3 | PFAS: first sighting baselines silently | PFAS page-watch | policy |
| PF-4 | PFAS: fetch failure on the un-baselined activation run fails loudly | PFAS page-watch | policy |

---

## Risk register

### RR-1 — R1–R8 risk taxonomy

Every document is tagged against a fixed 8-risk register that defines what this community group is fighting.

- **Given** a document about the landfill; **When** it is classified; **Then** it may be tagged with any of: R1 Expansion eligibility, R2 Violations history, R3 Odor nuisance, R4 Air quality, R5 Water quality, R6 Environmental justice, R7 Truck traffic, R8 Overheating/ETLF — using only these IDs, tagging none if none apply.
- `risk_register.py:19-91`
- classification

### RR-2 — R8-heavy signal keywords gate large-document windowing

The keyword list that decides which pages of a huge report get read is deliberately weighted toward the overheating/ETLF story.

- **Given** a report over the page threshold containing the terms "hov", "woi", "exceedance", "temperature", "mact", "leachate", "pfas", "consent judgment", "2020-0593-ce", etc.; **When** the parser windows it; **Then** only pages containing one of these 18 lowercased substrings (plus the cover page) are sent to the classifier.
- `risk_register.py:95-114`
- policy

---

## Document classification (parser output contract)

### DC-1 — Document type: evidence / procedural / opinion

- **Given** a filed EGLE document; **When** classified; **Then** doc_type is `evidence` (factual data a lawyer/commissioner could cite — measurements, violations, filed reports), `procedural` (notices, comment deadlines, permit applications, scheduling), or `opinion` (advocacy, value judgments).
- `egle_doc_parser.py:193-200` (help text), `:45` (enum)
- classification

### DC-2 — Severity: routine / notable / urgent

- **Given** a document; **When** classified; **Then** severity is `urgent` for a **measured** temperature ≥ 145F, a CO spike, smoldering/subsurface-oxidation language, or a Consent Judgment violation; `notable` for substantive non-emergency evidence (a new HOV waiver request, or **measured** temps in the 131–145F band); `routine` otherwise. A permitted ceiling / HOV-waiver limit of 180F is explicitly **not** urgent on its own.
- `egle_doc_parser.py:202-210` (help text), `:285` (enum)
- classification

### DC-3 — Measurement `basis`: measured vs permitted_limit vs unknown (load-bearing)

The single most credibility-critical field. Every extracted reading is stamped with whether it is a real observation or a regulatory ceiling.

- **Given** a reading of "180F"; **When** it is extracted; **Then** `basis` = `measured` only if it is an actual observed reading, `permitted_limit` if it is a permitted ceiling / MACT limit / HOV-waiver-requested value, `unknown` if undeterminable — and a permitted ceiling must never be labeled measured or vice-versa.
- `egle_doc_parser.py:55-64` (contract), `:212-228` (help text), `:272-273` (enum)
- classification

### DC-4 — Only valid register IDs survive classification

- **Given** the classifier returns risk tags including a hallucinated "R9"; **When** the ParsedDoc is assembled; **Then** tags are filtered to the passed-in register's IDs, so "R9" is dropped and only R1–R8 persist.
- `egle_doc_parser.py:369-370`
- validation

### DC-5 — Large documents: cover page + up to 10 keyword-matched pages only

- **Given** a 323-page WOI Status Report (threshold = 30 pages, cap = 10 keyword pages); **When** text is extracted for classification; **Then** the classifier sees page 1 plus at most the first 10 pages that contain a signal keyword, with a note that the full report is in Drive — not the whole document.
- `egle_doc_parser.py:151-186`; parameters `config.yml:44-45`
- policy

---

## Urgency & temperature

### UT-2 — Only `measured` temperatures count (crown-jewel rule; ADR 004)

Worked example, because this distinction is the whole credibility model.

- **Given** a WOI report whose highest structured reading is a **180F HOV-waiver permitted ceiling** with no measured reading at or above 145F; **When** urgency is computed; **Then** the document is **NOT** urgent (max measured temperature = none). **Given instead** the same report contains a **measured** well reading of **152F**; **Then** it **IS** urgent (152 ≥ 145). The measured-max scan iterates only `measurements` where `metric == "temperature"` AND `basis == "measured"`.
- `email_alerts.py:36-53`
- calculation

### UT-1 — Urgent iff parser-urgent OR measured temp ≥ threshold

- **Given** a document with parser severity `notable` but a measured temperature of 146F (threshold 145F); **When** `is_urgent` runs; **Then** it returns urgent (measured ≥ 145). If the parser already marked it `urgent` (and the `severity_is_urgent` switch is on), it is urgent regardless of temperature.
- `email_alerts.py:56-82`; parameters `config.yml:208-216`
- eligibility

### UT-3 — Stray Celsius readings converted before comparison

- **Given** a measured temperature recorded as "70 C"; **When** the measured-max is computed; **Then** it is converted (70×9/5+32 = 158F) and rounded before the ≥145 test.
- `email_alerts.py:49-52`
- calculation

### UT-4 — Free-text temperature scan is a guarded last resort

- **Given** a document where the parser extracted **any** structured temperature (even permitted/unknown basis); **When** urgency is computed and no measured value fired; **Then** the system trusts the structured path and returns not-urgent — it does **not** regex the free text, precisely so a permitted "180F" mentioned in prose can't falsely fire. Free-text regex runs **only** when the parser produced zero structured temperatures, and then only counts numbers in the plausible 50–1000F range.
- `email_alerts.py:21`, `:24-33`, `:71-82`
- calculation

---

## Alert routing

### AR-1 — Urgent → same-day email; everything else → pending weekly digest

- **Given** a newly processed document; **When** it is urgent; **Then** a same-day URGENT email is sent immediately; **When** it is not urgent; **Then** it is appended to the pending digest for the next Sunday.
- `watcher.py:141-150`
- state-transition

### AR-2 — Digest sends on Sundays only

- **Given** accumulated non-urgent items; **When** today's weekday is Sunday (weekday == 6) and the pending list is non-empty; **Then** the digest email is sent and the pending list cleared.
- `watcher.py:218-223`
- policy

### AR-3 — Digest lists procedural action-items first

- **Given** a digest containing procedural notices (deadlines) and evidence docs; **When** the body is composed; **Then** procedural items are listed first under "ACTION ITEMS (deadlines / notices)", followed by "OTHER NEW DOCUMENTS".
- `email_alerts.py:103-129`
- policy

### AR-4 — Recipients = config list plus private env addresses

- **Given** the five addresses in `config.yml` and an `ALERT_RECIPIENTS_EXTRA` env value; **When** recipients resolve; **Then** the union (order-preserved, de-duplicated) is used — the env exists so a private inbox can receive alerts without committing it to the public repo.
- `email_alerts.py:137-147`; parameters `config.yml:199-204`
- policy

### AR-5 — Evidence fan-out: evidence-type only, one row per risk

- **Given** a document classified `evidence` and tagged R4 + R8; **When** it is written to the case file; **Then** it produces **two** "Evidence by Risk" rows (one under R4, one under R8). A `procedural` or `opinion` doc, or an evidence doc with no risk tags, produces **zero** evidence rows.
- `sheet_writer.py:236-253`
- classification

### AR-6 — Risk-register tally: evidence count + most-recent date (string comparison)

- **Given** the Evidence-by-Risk tabs (nSITE + WDS unioned); **When** the register summary rebuilds; **Then** each risk shows its evidence-row count and the maximum date, where "most recent" is a plain `>` **string** comparison — which is why every date is normalized to ISO first (unpadded M/D/YYYY would sort by month).
- `sheet_writer.py:391-399`; ISO-normalization rationale `wds_watcher.py:67-82`
- calculation

---

## MMPC document archive

**MM-1 … MM-5 were retired 2026-07-12 (ADR 013).** They described the old MMPC
minutes *reminder*: the 2nd-Wednesday meeting-date math (MM-1), the
published-calendar override (MM-2), the +3-day / 10-day poll window (MM-3), the
"HTTP 200 + >500-byte body = minutes posted" heuristic (MM-4), and the
one-alert-per-meeting `mmpc_minutes_found` gate (MM-5). All of that code
(`mmpc_watcher.py`, the `watcher.py` polling block, the `mmpc:` config block, and
the `mmpc_minutes_found` state key) was removed once Mirror D made the reminder
redundant. The IDs are retained here so downstream references stay stable; the
only live MMPC rule is MM-6.

### MM-6 — MMPC archive: file type Agenda/Minutes/Other, dedup from the Sheet

- **Given** CivicClerk (category_id 72) publishes Agenda/Minutes/Other PDFs; **When** the archiver runs; **Then** each file's own `type` label is preserved and the "already archived" set is derived from the MMPC Archived Files Sheet tab (not a `_meta` key), so a file is mirrored exactly once.
- `mmpc_archiver.py:102-108`, `:124-128`; file-typing `mmpc_client.py:13-14`, `:89`
- policy

---

## WDS classification (Stream C — solid-waste system, site 475946)

Each WDS collection has its own classifier; none touch the temperature-based `is_urgent`.

### WD-1 — QMR groundwater report: exceedance = notable (R5)

- **Given** a Quarterly Monitoring Report row with `Statistical Exceedence? = Yes`; **When** classified; **Then** severity `notable`, doc_type `evidence`, risk R5. `No`/blank → `watch`.
- `wds_watcher.py:85-90`
- classification

### WD-2 — Application: Construction Permit = urgent (R1), Operating License = notable

- **Given** a solid-waste application whose `Application Type` contains "construction permit"; **When** classified; **Then** severity `urgent`, R1 (earliest hard signal of physical expansion). Any other application type → `notable`, procedural, R1.
- `wds_watcher.py:93-100`
- classification

### WD-3 — Annual report: years-of-capacity-remaining below 3.0 = notable (R1)

- **Given** an Annual Landfill Report with `Yrs Remaining End = 2.4` and floor 3.0; **When** classified; **Then** severity `notable`, R1 (airspace pressure). A new report with adequate capacity → `watch`. **See Note 4: an exact `0.0` reading is currently mis-classified as `watch`.**
- `wds_watcher.py:103-113`; parameter `config.yml:103`
- threshold

### WD-4 — CME evaluation/inspection = watch (R2)

- **Given** a compliance-monitoring evaluation record; **When** classified; **Then** `watch`, procedural, R2 (enforcement weight lives in compliance actions, not routine CEIs).
- `wds_watcher.py:116-119`
- classification

### WD-5 — Compliance action: adverse = urgent, resolved = watch, changed = downgrade

- **Given** a `Compliance Action Type` of "115 - VIOLATION NOTICE" seen for the first time; **When** classified; **Then** `urgent`, R2. **Given** "315 - STIPULATED PENALTY PAID/RESOLVED"; **Then** `watch` (good news — PAID/RESOLVED/CLOSED/WITHDRAWN/RESCIND is checked first). **Given** an already-alerted adverse action whose mutable field changed; **Then** downgraded to `notable` (never re-fires a duplicate urgent). **See Note 5 on possible under-firing.**
- `wds_watcher.py:122-140`
- classification

### WD-6 — Fetch collapse below 50% of last count = bad fetch (skip the diff)

- **Given** a collection last seen with 89 rows that now returns 3 (or 0); **When** diffed; **Then** it is treated as a transient read failure — the old seen-set is kept, nothing is emitted, a warning logs. WDS grids only grow, so a real shrink never happens.
- `wds_watcher.py:49`, `:265-266`
- validation

### WD-7 — First-ever sight of a collection self-baselines silently

- **Given** a collection with an empty seen-set (never baselined); **When** it is first polled; **Then** every current row is recorded as seen and **zero** alerts fire — so flipping the feature on can't blast the list with ~420 historical records.
- `wds_watcher.py:269-271`
- policy

### WD-8 — More than 20 alert events in one run → silent re-baseline

- **Given** a single run that would produce 30 alert events (cap 20); **When** the diff completes; **Then** it re-baselines silently (records all, alerts none) and warns, assuming a first-enable-without-seed or data anomaly rather than 30 real same-day filings.
- `wds_watcher.py:291-295`; parameter `config.yml:107`
- policy

### WD-9 — Re-alert on record mutation (identity + content hash)

- **Given** a Construction Permit application already seen as `pending` whose `Closure Type` later transitions to `Issued`; **When** re-polled; **Then** because the record's identity (immutable key) is unchanged but its content hash changed, it re-alerts as `changed` — WDS back-fills fields after a record first appears.
- `wds_watcher.py:143-198`, `:278-285`
- state-transition

### WD-10 — WDS urgent → same-day email; notable/watch → weekly digest

- **Given** a classified WDS event; **When** severity is `urgent`; **Then** a same-day email sends (and on send-failure the record's seen-hash is reverted so it re-alerts next run); **When** `notable`/`watch`; **Then** it is appended to the pending digest.
- `wds_watcher.py:374-392`
- state-transition

---

## WOI table extraction (R8 overheating evidence)

Standalone analysis path: `woi_table_parser.py` is invoked only by `scripts/woi_summary.py` and `scripts/co_summary.py`, **not** by the live watcher/backfill (see Note 6). It exhaustively parses the ~180–320-page WOI Status Reports where the generic keyword-windowing would capture <5% of readings.

### WOI-1 — Gas-extraction reading valid iff the gas fractions sum to ~100

- **Given** a parsed row with CH4 55%, CO2 40%, O2 4%, Balance 1% (sum 100); **When** validity is checked; **Then** it is valid (|sum − 100| ≤ 1.5). A row summing to, say, 87 is rejected as column misalignment. On the 2025 1st-semi-annual report this passed 99% of 13,976 readings.
- `woi_table_parser.py:66-71`
- validation

### WOI-2 — Row recognition: Well ID + date-time + at least 6 numbers

- **Given** linearized text where a well id (`^AH...`) is immediately followed by a `M/D/YYYY HH:MM` line; **When** the state machine reads it; **Then** it collects up to 9 numeric columns (CH4…Header-Pres) and records a reading only if ≥6 numbers are present (through the Temp column). The **time** component distinguishes Attachment-1 gas rows from Attachment-2 date-only CO rows.
- `woi_table_parser.py:93-119`, `:40-41`
- validation

### WOI-3 — Post-adjustment (ADJ) rows skipped; as-found is the headline

- **Given** a well with both an as-found reading and an "ADJ" post-valve-adjustment reading at the same time; **When** measurements are emitted (default `include_adj=False`); **Then** only the as-found reading is kept — the same well/time is never double-counted, and the well's headline state is what it was actually doing.
- `woi_table_parser.py:161-166`, `:327`
- policy

### WOI-4 — Every WOI-table reading is `basis = measured`

- **Given** any temperature, O2, CH4, or CO value parsed from the WOI tables; **When** converted to an ADR-004 measurement; **Then** `basis = "measured"` (these tables are observed field data, never permitted ceilings).
- `woi_table_parser.py:182-184`, `:277-280`
- classification

### WOI-5 — The formal Wells of Interest list comes from Attachment 2, not the wellfield

- **Given** Attachment 1 lists 478 wells (the full wellfield) while Attachment 2 lists the formally-designated WOI; **When** a well is tagged `is_woi`; **Then** membership is taken only from the Attachment-2 "Wells of Interest" tables — never inferred from asterisk/footnote count in Attachment 1.
- `woi_table_parser.py:142-158`, `:316-348`
- classification

### WOI-6 — CO reading at or above 10000 ppm is dropped as a data leak

- **Given** a CO cell of 45658.00 (an Excel date-serial leak); **When** parsed; **Then** it is discarded (real landfill-gas CO ≪ 10000 ppm), so spreadsheet artifacts never enter the trend.
- `woi_table_parser.py:208`, `:238`
- validation

### WOI-7 — CO dedup: keep the first reading per (well, month)

- **Given** a well appearing on both the canonical CO page and the malformed "%" double-table for the same month; **When** deduped; **Then** the first (canonical) reading is kept, one per (well, month).
- `woi_table_parser.py:249-258`
- policy

### WOI-8 — Well IDs canonicalized (strip asterisks, apply alias map)

- **Given** a well printed as "AHW272R4**" and an alias map reflecting EGLE's 6/22/2023 WOI-id update; **When** canonicalized; **Then** trailing asterisks are stripped and the alias applied, so a physical well's history stays under one id across reports.
- `woi_table_parser.py:74-83`
- calculation

### WOI-9 — Per-well rollup: hottest temperature and CO rise trend

- **Given** all valid as-found readings for a well; **When** summarized; **Then** the rollup reports max temperature (with the O2 and CH4 read at that same moment) sorted hottest-first, and — for CO — the monthly series with max ppm and the rise from first to last reported month.
- `woi_table_parser.py:289-313`, `:316-348`
- calculation

---

## Backfill, eligibility & anti-stampede

### BF-1 — Doc eligibility: skip processed, terminally skipped, or poisoned

- **Given** the nSITE document list; **When** selecting work; **Then** a doc is skipped if it is already `processed`, terminally `skipped` (unprocessable source — legacy .doc, encrypted PDF, raw image), or has ≥3 recorded errors — leaving only genuinely new/unfinished docs.
- `watcher.py:97-102`; `backfill.py:59-89`
- eligibility

### BF-2 — Poison threshold = 3 failed attempts

- **Given** a document that has failed classification; **When** its error count reaches 3 (`MAX_ERRORS_PER_DOC`); **Then** it is considered poison and no longer retried on normal runs.
- `watcher.py:40`; `backfill.py:32`, `:86`
- threshold

### BF-3 — Transient failures (quota / 429 / 5xx) accrue no poison strike

- **Given** a classification that fails with a monthly workspace usage-cap 400, a 429, a 5xx, or a network error; **When** the error is triaged; **Then** it is transient — the doc is retried on a later run with **no** strike, so an outage/quota window can't permanently drop a real filing (the 2026-07-07 false-poison incident). A genuine bad-document 400 (un-openable PDF, max_tokens truncation) still accrues a strike.
- `retry_policy.py:53-88`; callers `watcher.py:164-170`, `backfill.py:176-182`
- policy

### BF-4 — Backfill processes 50 docs per run

- **Given** the remaining unprocessed backlog; **When** a nightly backfill run executes; **Then** it takes the next 50 docs (`backfill_batch_size`).
- `backfill.py:110`, `:136`; parameter `config.yml:48`
- policy

### BF-5 — Watcher defers if more than 25 docs are unprocessed

- **Given** the daily watcher sees 300 unprocessed docs (cap 25); **When** it runs; **Then** it processes **no** documents this run (backfill hasn't cleared history) — avoiding stampeding years-old exceedances into the live feed and firing false urgent alerts — while digest housekeeping still runs.
- `watcher.py:109-113`; parameter `config.yml:69`
- policy

### BF-6 — Backfill facility processing order

- **Given** four facilities; **When** backfill concatenates them; **Then** the Remediation Area (R5/PFAS, ~740 docs) is processed first so it clears first, and the landfill N2688 (~704 remaining) is processed last so it doesn't block the others.
- parameter `config.yml:17-21`
- policy

### BF-7 — Terminal-failure docs are stubbed and marked skipped (still visible)

- **Given** a doc that hits its 3rd failure (e.g. a legacy .doc that `downloadpdf` 400s on); **When** the terminal failure is handled; **Then** a stub feed row (title/date/native-download link) is written and the doc is marked `skipped` — visible to a human, never silently dropped, and not retried.
- `backfill.py:193-207`
- state-transition

### BF-8 — Backfill self-terminates when nothing remains

- **Given** state showing every doc processed or poisoned; **When** backfill runs; **Then** it logs "Backfill complete" and exits 0 as a no-op — the completion signal counts poison docs as done so a stuck poison doc can't keep it running forever.
- `backfill.py:92-103`, `:132-134`
- state-transition

---

## PFAS page-watch

### PF-1 — Alert on a change to the page's `<main>` content hash

- **Given** EGLE edits a word of visible text (or adds a new document link) on the Arbor Hills PFAS page; **When** the daily watch runs; **Then** the normalized `<main>` content hash differs from the last snapshot and a change alert (with a capped visible-text diff) is sent. Rotating Sitecore cache-busters in the URL query are stripped, so a theme redeploy alone does not fire.
- `pfas_client.py:120-154`; change logic `pfas_watcher.py:160-186`
- classification

### PF-2 — Bad-fetch validation: body <500 bytes or `<main>` <200 chars

- **Given** a fetch whose body is under 500 bytes, or whose `<main>` visible text is under 200 characters; **When** validated; **Then** it is treated as a bot-wall/partial read and **not** hashed or diffed into a false "changed" alert.
- `pfas_client.py:48-51`, `:108`, `:145-146`; parameter `config.yml:192`
- validation

### PF-3 — First sighting baselines silently

- **Given** a watched page with no prior snapshot; **When** first fetched successfully; **Then** a silent "baseline" row is recorded and **no** alert fires (there is no change to report yet).
- `pfas_watcher.py:163-169`
- policy

### PF-4 — Fetch failure on an un-baselined page fails loudly

- **Given** a page that has **no** baseline yet **and** the fetch/parse fails on the activation run; **When** the watch runs; **Then** it exits 1 (triggering a workflow-failure email) rather than silently no-oping forever — surfacing an Actions-runner bot-wall immediately. A failure **after** a baseline exists is merely skipped-and-warned.
- `pfas_watcher.py:148-158`
- policy

---

## Consolidated parameters (candidates for configuration)

| Parameter | Current value | Where | Rule |
|-----------|--------------|-------|------|
| `measured_temp_urgent_f` | **145** (MACT limit) | `config.yml:213` | UT-1, DC-2 |
| `measured_temp_watch_f` | **131** (EPA gas-well) — *no code consumer* | `config.yml:216` | see Note 2 |
| `temperature_thresholds.epa_gas_operating_f` / `mact_f` | **131 / 145** — *no code consumer* | `config.yml:219-222` | see Note 2 |
| `severity_is_urgent` | **true** (gates parser-urgent → urgent) | `config.yml:209` | UT-1 |
| free-text temp plausibility | **50–1000 F** | `email_alerts.py:31` | UT-4 |
| `large_doc_page_threshold` / `large_doc_max_keyword_pages` | **30 / 10** | `config.yml:44-45` | DC-5 |
| `wds.years_remaining_floor` | **3.0** | `config.yml:103` | WD-3 |
| `wds.max_new_wds_alerts_per_run` | **20** | `config.yml:107` | WD-8 |
| `_COLLAPSE_FRACTION` | **0.5** | `wds_watcher.py:49` | WD-6 |
| `watcher.max_new_docs_per_run` | **25** | `config.yml:69` | BF-5 |
| `backfill_batch_size` | **50** | `config.yml:48` | BF-4 |
| `MAX_ERRORS_PER_DOC` | **3** | `watcher.py:40`, `backfill.py:32` | BF-2 |
| WOI gas-sum tolerance | **±1.5** of 100 | `woi_table_parser.py:71` | WOI-1 |
| `CO_IMPLAUSIBLE_PPM` | **10000** | `woi_table_parser.py:208` | WOI-6 |
| PFAS `min_content_chars` / body floor | **200 / 500** | `config.yml:192`, `pfas_client.py:48` | PF-2 |
| PFAS diff cap | **60 lines** | `pfas_watcher.py:55` | PF-1 |

No secrets appear in any rule parameter. Credentials (SMTP password, Anthropic key, service-account key, OAuth token, GitHub Secrets) are referenced by env-var name only in the code paths read; none are hardcoded.

---

## Notes / discrepancies

1. **All three optional streams are ON in committed config — these rules are live, not hypothetical.** `wds.enabled: true` (`config.yml:86`), `mmpc_archive.enabled: true` (`config.yml:167`), and `pfas.enabled: true` (`config.yml:188`) are all set. **Neutral observation, not a bug:** the in-code docstrings still describe these as "OFF BY DEFAULT" / "default false" (`wds_watcher.py:6`, `mmpc_archiver.py:23`, `pfas_watcher.py:17`), so the prose lags the committed switches. **Confidence: High.**

2. **The 131F "watch" threshold and the entire `temperature_thresholds` block have no executable consumer.** `grep` across all `.py` confirms only `severity_is_urgent` and `measured_temp_urgent_f` are read in code (`email_alerts.py:58-60`). `measured_temp_watch_f: 131`, `temperature_thresholds.epa_gas_operating_f: 131`, and `mact_f: 145` are **documentary today** — no routing, tiering, or alert depends on 131. The config comment itself says the Watch tier is "(future)". **Confidence: High** (grep-verified). **SME question:** Is a Watch tier planned, and if so should a measured 131–144F reading drive any routing?

3. **The "131–145F = notable" tiering is model judgment, not a coded rule.** That band appears only inside the classifier prompt (`egle_doc_parser.py:202-210`); no deterministic threshold enforces it. `is_urgent` reads only 145. So a measured 140F reading is `notable` only if the model chose to say so. **Confidence: High.**

4. **`_classify_annual` short-circuits on exactly 0.0 years remaining (CODE-VERIFIED DEFECT).** The guard is `if yrs and yrs < floor` (`wds_watcher.py:109`). In Python `0.0` is falsy, so an Annual Report stating **0.0 years of capacity remaining** — the single most R1-critical case (airspace exhausted) — skips the `notable` branch and returns `watch`. **Confidence: High** (logic explicit, re-read 2026-07-12). **Fix:** `if yrs is not None and yrs < floor:`. **SME question:** Does EGLE ever report exactly `0.0` as a real reading versus a missing-data sentinel? (A blank already raises in `float()` and is caught, so only a literal `0`/`0.0` hits this path.)

5. **WDS urgent compliance vocabulary is substring-matched and may under-fire.** `_classify_compliance_action` fires urgent on substrings VIOLATION / PENALTY DEMAND / COMPLIANCE ORDER / CONSENT / CIVIL / CRIMINAL / ENFORCEMENT / FINAL MONETARY (`wds_watcher.py:136-138`). The config comment promises "assessed penalty = URGENT", and an assessed-penalty row not containing those literal substrings would fall through to `notable`. **Confidence: Medium.** **SME question:** Enumerate EGLE's actual `Compliance Action Type` values that should count as adverse, so the match list is complete.

6. **Live-pipeline evidence-completeness gap on large reports — and the exhaustive fix is not wired in.** In a >30-page report, DC-5 shows the classifier only pages containing a signal keyword plus the cover; a real measurement on a page with no keyword is never seen. The exhaustive table parser that would capture all readings (`woi_table_parser.py`) exists precisely for this — but it is imported only by `scripts/woi_summary.py` / `scripts/co_summary.py`, and the repo's own topology doc calls the "WOI cluster … disconnected." So the automated watcher/backfill still under-captures WOI readings; exhaustive extraction happens only when a human runs the script. **Confidence: High.** **Eng question:** Should WOI Status Reports route through `woi_table_parser` automatically rather than depending on a manual script run?

7. **Duplicate hardcoding of the same thresholds (DRY risk).** 131 appears at both `measured_temp_watch_f` (`config.yml:216`) and `temperature_thresholds.epa_gas_operating_f` (`config.yml:220`); 145 appears at both `measured_temp_urgent_f` (`config.yml:213`) and `mact_f` (`config.yml:221`). Editing one without the other would silently diverge the "watch" and "urgent" reference numbers. **Confidence: High.**

8. **Anti-stampede caps suppress rather than alert (accepted residual risk).** Both BF-5 (>25 nSITE docs) and WD-8 (>20 WDS events) respond to a burst by silently deferring / re-baselining, not by notifying anyone. A genuine flood of ~30 real same-day filings would be quietly suppressed. This is a deliberate trade-off, but the failure mode "many real filings arrived at once" is invisible unless a human notices the log line. **Confidence: High.** Worth an explicit product decision on whether an over-cap event should itself send a heads-up.

9. **Good disciplines worth preserving (so nobody "optimizes" them away):** the measured-vs-permitted `basis` distinction (DC-3 / UT-2, ADR 004) is the credibility spine of the whole system; the WOI parser's refusal to infer WOI membership from asterisk count (WOI-5); and the transient-vs-poison split that prevents quota outages from dropping real filings (BF-3). These are load-bearing policy, not incidental code.
