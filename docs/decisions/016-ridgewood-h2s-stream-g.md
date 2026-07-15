# ADR 016 — Stream G: Ridge Wood Elementary H2S monthly reports

*Status: **BUILT, disabled** — 2026-07-14. Ships `ridgewood.enabled: false`; a
brand-new poller against a live external source stays off until a human flips it on
(overnight-coder procedure). The Activation section below is Trisha's checklist.*

## Naming note (read first)

The staging handoff and the feasibility recon both call this "Stream F / the ADR
after 014". That is **stale**: while this was queued, **Stream F + ADR 015 were
taken by the CivicClerk meeting-change watch** (merged as `fc3efce`). So this ships
as **Stream G / ADR 016**. The one stale cross-reference that already existed in the
tree — ADR 014 and the `gfl_air:` config comment calling Ridge Wood "Stream F" — is
corrected to "Stream G" in this same change so the codebase doesn't contradict itself.

## Context

GFL operates an **H2S air monitor at Ridge Wood Elementary School**, installed
**December 2020** under an agreement with **U.S. EPA**, operated and QA'd by
**Barr Engineering Co.** — the same independent consultant
behind the GFL perimeter ArcGIS feed (Stream E, ADR 014). Barr posts a **monthly
24-hour-average H2S data report** (one born-digital PDF per month) to a public page:
`https://arborhillsmonitoring.com/Home/Ridgewood`.

This is the **school-adjacent** R3 (odor) / R4 (air) monitor — a **different
location** from Stream E's perimeter fenceline stations (MS-1..MS-6), and therefore
**complementary evidence, not a duplicate**. Stream E is hourly live perimeter
readings; Stream G is a single near-school monitor published as QA'd monthly reports.

There is **no queryable API** here (unlike Stream E's FeatureServer). The data is
published as monthly PDFs listed on the page. That makes this the **document-archive
shape** ("a new PDF appears → mirror + extract") already established by Mirror D
(`mmpc_archiver.py`, ADR 010) / Mirror B (`archiver.py`, ADR 007), **not** the
new-poller-with-a-cursor shape Stream E used.

### The published action levels (the high-value part)

The page states this monitor's own alert thresholds verbatim (confirmed in every
report's footnotes across 66 months, 2020-12 → 2026-05):

- **24-hour average > 72 ppb** → school notified: elevated but no immediate danger.
- **15-minute average > 750 ppb** → school notified *immediately*; take precautions.

The **72 ppb** number **independently confirms the H2S threshold shipped for Stream
E** (ADR 014 — 72 ppb is *both* the Michigan EGLE ITSL and this monitor's own 24-hr
action level; two independent official sources agreeing is what settled it). **750
ppb** is a new **15-minute acute** level worth carrying.

### Feasibility spike (re-confirmed 2026-07-14, this build)

The recon was done when the handoff was staged; this build re-confirmed it live
before writing code (the handoff requires it):

- The page's report list resolves: **66 monthly reports**, Dec 2020 → May 2026, one
  per month, each a `Files/*.pdf` link.
- The filename is `YYYY-MM_Arbor Hills H2S Data_24-hour ave[_NNNN].pdf`. The `_NNNN`
  suffix is an **unpredictable cache-buster** (`_3486`, `_5697`, … and some months
  have **none**); the filename also contains spaces. So the URL is **never
  constructed** — the reliable key is the `YYYY-MM` prefix scraped off the page.
- A recent PDF is a **born-digital, text-layer** ~2-page report (~3.5k chars), so
  extraction is a clean fitz text pass — **no OCR needed**. Sections: Equipment
  Operation, Data Downloading, Meteorological Conditions, Maintenance, Audits, *Data
  – Average Air Concentrations*, and a *Data Report* section whose headline is a
  **per-day table of 24-hr averages** plus the report's own notification determination.

One page link (the very first report, Dec 2020) is *also* served under an old-format
name with no `YYYY-MM_` prefix. It's a **duplicate** of the canonical `2020-12_…`
link, so dropping links whose month doesn't parse is correct; the count of such
skipped links is logged so a genuine format change (every link going unparsed) is
visible, not silent.

## Decision

Add **Stream G**: `ridgewood_client.py` (fetch + parse) + `ridgewood_archiver.py`
(diff + mirror + extract + alert), modeled on Mirror D, gated behind
`ridgewood.enabled` (off by default). `egle_doc_parser.py` is **not touched** (the
Decode base stays domain-agnostic); the PDF bytes are routed through `fitz` directly
for text, and a small dedicated H2S extractor does the rest.

### 1. Scrape the list, never construct a URL

`ridgewood_client.scrape_report_links()` parses the `Files/*.pdf` links out of the
page HTML and keys each by its `YYYY-MM` prefix. A failed page fetch raises
`RidgewoodFetchError` (never a silent `[]`), so the archiver aborts the run rather
than diffing a partial/empty list as "reports were removed" (same posture as
`PFASFetchError` / `MMPCFetchError`).

### 2. Deterministic extractor, not the Claude classifier

The report format is fixed and the alert decision is load-bearing, so extraction is
a **deterministic** pure function, not a Haiku classification call — no API cost, no
non-determinism, and a directly unit-testable alert decision. This is the "small
dedicated H2S extractor" option the handoff allowed, chosen over routing through
`parse_document` (which is built for risk classification of arbitrary EGLE docs).

### 3. The classifier is FAIL-SAFE and FOOTNOTE-SAFE (the load-bearing design)

Every published report to date is an all-clear, so there is **no specimen** of what
an exceedance report's wording looks like — and the action-level **footnotes
literally contain the strings "exceeds 750 ppb" / "exceeds 72 ppb"** as definitions.
A naive grep for exceedance words would therefore fire on the boilerplate **every
single month**. So `classify_report()` never positive-matches exceedance wording.
Instead, per the repo's own fail-safe ruling (the WDS `0.0`-years decision — default
to alerting when external semantics are unknowable):

- **24-hr / 72 ppb:** alert iff any **numeric** daily-table value ≥ the threshold.
  Wording-independent and reliable. The numeric parse is **scoped to the daily table**
  (each `M/D/YY` date line pairs with the value on the next line — `M/D/YY` dates
  occur only in the table), so footnote/narrative numbers (72, 750, 9,999, the
  0.000–9.999 range, the rounding examples) are never captured.
- **15-min / 750 ppb:** only ever appears in prose. The classifier **positively
  detects the report's own all-clear phrase** ("No notifications required to be sent
  …") in the *footnote-stripped* body, and alerts on its **absence** — never on a
  750 ppb format it has never seen.
- **Parse anomaly:** a report yielding **zero** daily values (a scanned image or a
  format change) → alert. A broken parse must never read as "clean month".

A false alert here is cheap (Trisha reads it); a missed exceedance defeats the
monitor's only job. When in doubt, it alerts.

### 4. Schema mapping — one monthly row, existing Measurements tab

The report's granular data is a **per-day** table of 24-hr averages, but the single
decision-relevant, exceedance-determining number is the **month's maximum**. So one
Measurements row per month (the handoff's `as_of_date = report month` granularity),
**not** ~30 rows/month of mostly `<1` (the daily detail lives in the mirrored PDF):

- `metric = "hydrogen_sulfide"`, `unit = "ppb"`, `basis = "measured"`
- `well_id = "Ridge Wood Elementary School"`, `as_of_date = "<YYYY-MM>-01"`
- `value` = the max as the report presents it — `"<1"` when every day is below the
  1 ppb reporting resolution (the report's own token), else the numeric max. The
  qualifier lives in `note`; **exceedance logic runs on numerics only**, so `"<1"`
  never touches the ≥ 72 check.
- `note` attributes it honestly to the **Barr/EPA-agreement monitor** — *not* GFL
  self-report, *not* an EGLE measurement.

No new measurement schema (the ADR-004 invariant). A new **`Ridge Wood Reports`**
tab is the archive index + provenance (source URL, content hash, fetched-at, the
Drive mirror link, the extracted max, and the alert verdict), keyed by `YYYY-MM`
(col A) for dedup — the `mmpc_archived_file_ids` idiom (Sheet-derived ⇒ race-free,
**not** `_meta`, since this is its own workflow).

### 5. Drive mirror is DECOUPLED from the safety function

A deliberate divergence from `mmpc_archiver` (which no-ops entirely when its OAuth
folder isn't configured): the exceedance alert is this stream's safety-critical job
and must **not** depend on an optional Drive folder secret being present.

- Folder configured + OAuth healthy → mirror each PDF, record the link.
- Folder configured but the OAuth token is dead → **fail loudly** (exit 1), so the
  mirror doesn't fall behind invisibly (mmpc's posture).
- Folder **not** configured → skip the mirror (blank link), but **still extract +
  alert**. Enabling the stream without the folder secret is a valid, safe state.
- Folder configured + healthy token but a **single upload throws** (a transient
  network blip) → that one upload is caught **best-effort**: the month is still
  extracted, measured, and alerted, and recorded with a **blank** archive link. The
  alert never depends on the mirror in the transient case either, not just the
  not-configured case. Residual: that month stays un-mirrored (re-mirroring it is a
  manual re-run — remove its row from the tab); acceptable, since the alert is the
  safety-critical output and the exceedance is also visible on the public source page.

### 6. Crash-safe write order (three durable writes, not two)

Per new month: **(1)** Drive upload → **(2)** Measurements row(s) → **(3)** Ridge
Wood Reports dedup row (the "done" marker, written **last**) → **(4)** alert email
(best-effort, last). A crash between (1) and (3) re-processes the month next run: an
idempotent Drive re-upload plus **at most a duplicate monthly measurement**, never a
dropped month (accepted residual — one row/month). A crash between (3) and (4) loses
that month's real-time email but keeps the durable record (the Measurements rows, the
Reports tab, and the mirrored PDF) and never re-fires — the same best-effort-alert
posture as `pfas_watcher` / `mmpc`.

### 7. Backfill suppresses alerts

The first enabled run finds ~66 historical months. A run processes at most
`max_new_reports_per_run` months (newest first, resumable via the dedup tab), and a
run **draining a backlog** (> cap new months) **suppresses alerts** — those months
are historical (a real-time email about a 2021 reading is noise) and their data is
recorded regardless. Steady-state runs (one new month) alert normally.

## Consequences / residual risks (accepted)

1. **Rides an undocumented public report list.** There is no API; the archiver
   scrapes the page. If the markup or the file path changes, the scrape fails loudly
   (aborts the run) and the per-poll report-count log makes a drop to zero visible.
   Recovery if the source restructures: adapt the scrape, or a documented STOP.
2. **A 15-minute / 750 ppb exceedance is detectable only via the all-clear's
   absence — never a positive numeric signal.** The daily table holds 24-hr
   averages only, so there is no numeric route for a 15-minute reading; detection
   rests *entirely* on Barr dropping or changing the "No notifications required to be
   sent" statement when a 15-min notification was issued. Given zero exceedance
   specimens this is the correct fail-safe (alert on the all-clear's absence), but it
   is load-bearing and unverifiable until a real notification report exists. If Barr
   ever issues a 15-min notification while *keeping* boilerplate that still reads as
   all-clear, this would miss it. Accepted (fail-safe direction); re-examine the
   moment a real exceedance report is published (it becomes the missing specimen).
3. **A future report could be a scanned image.** Verified text-layer across 66
   months, but v1 does **not** auto-OCR: a no-text PDF is **mirrored + flagged for
   manual/OCR review** (fail-safe — the month is archived, never dropped), not run
   through `ocrmypdf` in this workflow. Wiring the repo's existing OCR path
   (`egle_doc_parser.ocr_in_place`) is a deferred enhancement; the alert-for-review
   path is the safe minimum and keeps the workflow lean (no OCR binary to install).
4. **`<1` is below reporting resolution.** The report labels a calculated average of
   0.0–0.9 ppb as `"<1 ppb"`. We store that token verbatim rather than inventing a
   float; it is far below 72 ppb and never enters the numeric exceedance check.
5. **Duplicate monthly measurement on a mid-month crash** (the crash-safe order,
   decision #6 above) — negligible at one row/month; accepted.
6. **New Drive folder secret.** The mirror uploads into its own folder
   (`GOAUTH_RIDGEWOOD_FOLDER_ID`) — a new secret, like Mirror D's
   `GOAUTH_MMPC_FOLDER_ID`. Optional (see decision #5); flagged in Activation.

## Alternatives considered

- **One row per day (~30/month, ~2000 backfill rows)** — full fidelity but mostly
  `<1` noise, and it contradicts the handoff's monthly granularity. The mirrored PDF
  preserves the daily detail. Not chosen (monthly max is the decision-relevant value).
- **Route the PDF through `egle_doc_parser.parse_document` (Claude)** — reuses the
  existing pipeline but adds API cost + non-determinism to a fixed-format report and
  a load-bearing alert. Not chosen (decision #2).
- **Positive-match the exceedance wording** — impossible to do reliably with zero
  exceedance specimens and actively dangerous given the footnote landmine. Not chosen
  (decision #3 — fail-safe on all-clear *absence* instead).
- **No-op entirely when the Drive folder isn't configured (mmpc's gate)** — would
  couple the safety alert to an optional mirror secret. Not chosen (decision #5).

## Activation (Trisha's call — all three steps, same pattern as Stream C / Mirror D)

1. Review + merge this branch to `main`.
2. **(Optional) provision the Drive folder secret** `GOAUTH_RIDGEWOOD_FOLDER_ID` = a
   new Drive folder for the Ridge Wood PDFs (run `scripts/oauth_setup.py` to create
   one, or reuse an existing folder's ID). The three shared `GOAUTH_CLIENT_ID` /
   `_SECRET` / `_REFRESH_TOKEN` secrets are already live. **This secret is optional:**
   without it the stream still extracts + alerts; it just doesn't mirror to Drive.
3. Set `ridgewood.enabled: true` in `config.yml` and commit. The first enabled run
   backfills the ~66 historical months (alerts suppressed during the backfill — the
   reports are years-old all-clears and their data is recorded either way), then it
   settles into ~1 new month per month.

Until `enabled: true` is on `main`, `ridgewood.yml` runs on schedule but
`ridgewood_archiver._should_run()` makes every run a quiet no-op (verified by
`tests/test_ridgewood.py`, mirroring the gate test that has caught this exact class
of bug before — ADR 009's Addendum).
