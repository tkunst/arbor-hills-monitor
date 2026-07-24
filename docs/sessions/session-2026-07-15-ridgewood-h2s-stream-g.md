# Session 2026-07-15 — Ridge Wood Elementary H2S archiver (Stream G)

Overnight-coder loop against `docs/overnight-coder-handoffs/ridgewood-h2s.md`
(data-sources item 8), then a live daytime activation + full backfill with Trisha.
Shipped as **Stream G / ADR 016** — the handoff and recon both said "Stream F", but
F / ADR 015 were already taken by CivicClerk meeting-watch; the one stale cross-ref
(ADR 014 + the `gfl_air` config comment calling Ridge Wood "Stream F") was corrected
in the same PR.

## What it is

GFL runs an H2S air monitor at Ridge Wood Elementary School under a U.S. EPA
agreement, operated + QA'd by Barr Engineering (the same consultant behind Stream E's
perimeter ArcGIS feed). Barr posts a monthly born-digital PDF report of daily 24-hour
H2S averages to a public page. A **different location** from Stream E's fenceline —
complementary evidence — and the source of the **72 ppb** 24-hr / **750 ppb** 15-min
action levels the page publishes verbatim. The 72 ppb value independently confirms
Stream E's H2S threshold (ADR 014); 750 ppb is a new acute level.

This is a document-archive source (no API), so it follows the Mirror-D
(`mmpc_archiver`) "new PDF appears → mirror + extract" shape, not Stream E's cursor
poller.

## Shipped

- **PR #14 → `main` `28c5820`** (ff/linear: feat `6ebefb0` + `/review` fix `28c5820`),
  ADR 016. New `ridgewood_client.py` + `ridgewood_archiver.py` + a `Ridge Wood Reports`
  tab, `ridgewood.yml` (daily 9am ET, own concurrency group), 26 hermetic tests.
- **PR #15 → `main` `96391a3`** — two-tier alert routing (Trisha's call on the #14
  recipient question).
- **`3b8e686`** — activation (`ridgewood.enabled: true`), committed direct to `main`
  (the documented activation path, matching Mirror D / PFAS).

## Design (see ADR 016)

- **Scrape the report links, never construct a URL.** The filename carries an
  unpredictable `_NNNN` cache-buster (some months have none) + spaces; the reliable
  key is the `YYYY-MM` prefix. The first (Dec 2020) report is also linked under an
  old-format name with no prefix — a duplicate, logged + skipped (a jump in the
  skip-count would surface a page restructure). A failed page fetch raises
  `RidgewoodFetchError` (never a silent `[]`).
- **Deterministic, not the Claude classifier.** Fixed format + a load-bearing alert
  → a pure extractor (no API cost, no non-determinism, directly testable).
- **Fail-safe + footnote-safe classifier.** Every published report is an all-clear,
  so there is no exceedance specimen — and the action-level footnotes literally
  contain "exceeds 750/72 ppb" as definitions. So it never positive-matches
  exceedance wording: 24-hr alerts on any *numeric* daily value ≥ 72 (parse scoped to
  the daily table via date→value pairing, so footnote numbers never leak in); 15-min
  is caught only via the *absence* of the report's all-clear phrase (footnote region
  stripped first); a zero-value parse → alert. Defaults to alerting.
- **One monthly row.** The month's max 24-hr average → Measurements
  (`metric=hydrogen_sulfide`, `unit=ppb`, `basis=measured`, `well_id="Ridge Wood
  Elementary School"`, `as_of_date=<month>-01`, note attributing the Barr/EPA
  monitor). `<1` stored verbatim; exceedance runs on numerics only. No new schema.
- **Drive mirror decoupled from the safety function.** Not configured → skip mirror,
  still extract + alert. A dead token fails loudly up front. A *transient* per-file
  upload failure is caught best-effort (the month is still measured + alerted, blank
  link) — the `/review` fix, since the original ordering let a Drive blip skip the
  alert.
- **Crash-safe order:** Drive upload → Measurements → Reports (dedup) row → email.
- **Backfill suppresses alerts:** a run draining > `max_new_reports_per_run` (12) new
  months is a historical backfill (email about a 2021 reading is noise; the data is
  recorded regardless).
- **Two-tier recipients:** a real exceedance → full `alert_recipients` (Conservancy);
  review-tier (missing all-clear / no-text / parse anomaly) → `review_recipients`
  (Trisha only).

## Process

- Spike re-confirmed feasibility **live** (66 reports 2020-12…2026-05 resolve + carry
  a text layer; format stable 5+ years) and verified the full `run()` end-to-end
  against real downloaded PDFs before any autonomous merge.
- `/review` → one finding (transient-upload could block the alert), fixed in-PR +
  regression test. `/security-review` + an independent adversarial subagent pass →
  **zero** med/high (SSRF / temp-path / email-header surfaces all traced dead).
- CI green on every commit; privacy pre-push gate passed (dropped a "Northville
  Public Schools" parenthetical from new docs, per the local gate's intent).

## Activation + backfill (live, with Trisha)

- Trisha enabled the stream, then provisioned the optional Drive mirror. The local
  `.env` has placeholder OAuth creds (real ones live only in GitHub Secrets — correct
  hygiene), so the folder couldn't be created from this machine; Trisha created it and
  gave the ID. The first ID pasted was the **parent** folder, so the first 12 PDFs
  landed there — corrected by repointing `GOAUTH_RIDGEWOOD_FOLDER_ID` to the dedicated
  subfolder and moving those 12 down (a move preserves each file's Drive ID, so the
  archive links already in the Sheet stayed valid).
- Full **66-month backfill** run manually (6 dispatches, 12/run cap, serialized by the
  concurrency group). Transient `urlopen` download timeouts on a few 2021 months
  self-healed on the next run (per-month try/except → not-marked-done → retry). Final
  verification: **all 66 expected months (2020-12…2026-05) present in the dedicated
  folder, 0 missing, 0 extra, 0 duplicates**; every month all-clear (`<1` ppb), no
  alerts.

## Final state

- `main` @ `3b8e686` (+ this session's comment-accuracy + doc commits); all CI green.
- Stream G **live**: daily 9am-ET run finds ~1 new month/month, extracts + mirrors it,
  alerts only on a stated 72/750 ppb exceedance (review-tier → Trisha, exceedance →
  full list). All 66 historical months archived + mirrored.
- Nothing left for Trisha on Ridge Wood. Overnight-coder queue: `coder:ridgewood-h2s`
  archived; next ready item is `coder:gfl-air-liveness`.
- **Concurrent with this session, Trisha enabled Stream E** (`d3bac7b`,
  `gfl_air.enabled: true`). That *raises* the priority of `coder:gfl-air-liveness`: it
  closes ADR 014's "OBJECTID-reset silent stall" residual, which was meant to ship
  **before** enabling Stream E — so Stream E is now live **without** that guard (a
  silent-stall risk until the hardening merges). The item's "do before enabling"
  framing is overtaken by events; reframe as "Stream E is live — add the guard soon."

## Non-blocking follow-up

`docs/topology/TOPOLOGY.html` (the interactive viewer) still needs a re-inject of the
regenerated `topology.json` — deferred, consistent with prior stream PRs.
