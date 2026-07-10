# ADR 009 — Stream C: EGLE Waste Data System (WDS) solid-waste monitoring

*Status: proposed — 2026-07-09 (dormant, behind `wds.enabled: false`; awaiting review + activation).*

## Context

The monitor watches two streams: **Stream A** — nSITE (EGLE **Air**) filings for
the four tracked facilities (ADR 008) — and **Stream B** — the Washtenaw County
MMPC schedule/minutes. Both miss the landfill's **solid-waste** regulatory life,
which lives in a *different* EGLE portal: the **Waste Data System**
(`egle.state.mi.us/wdspi` = legacy `deq.state.mi.us/wdspi`), the Part-115 (solid
waste) / Part-111 (hazardous / liquid-industrial) system of record.

A 2026-07-09 crawl of WDS site **475946 (Arbor Hills Landfill, Inc.)** found data
the risk register needs and nSITE does not carry (full findings + module map:
Lotext `documents/arbor-hills/source-docs/WDS-crawl-and-monitor-map.md`):

- **Quarterly Monitoring Reports (89)** — groundwater, each with a
  `Statistical Exceedence? Yes/No` flag + EGLE reviewer notes. **37 of 89 quarters
  flagged.** This is the **R5** groundwater tracker the plan said was missing.
- **Annual Landfill Reports (30)** — waste tonnage by type + permitted capacity +
  **estimated years-of-capacity-remaining** (down to ~3–4 yrs). The **R1**
  (expansion) airspace number.
- **Applications (10)** — construction permits + operating licenses. A **new
  Construction Permit is the earliest hard signal of a physical expansion (R1)** —
  it precedes the MMP politics Stream B tracks.
- **Compliance Actions (44) + Evaluations (249)** — the CME enforcement/inspection
  record (**R2**), already CSV-scraped in June.

## Decision

Add **Stream C**: a daily poll of WDS site 475946, diffing against stored state and
alerting on new/changed records — **off by default** (`wds.enabled: false`), enabled
only after review.

- `wds_client.py` — fetch + parse (pure HTTP; server-rendered ASP.NET). Reuses the
  detail-span parser + `ExpandableListControl` pager proven in the Lotext scrapers.
  Raises `WDSFetchError` (never a silent `[]`) so "0 records" is distinguishable
  from "couldn't read it".
- `wds_watcher.py` — the diff engine + WDS-specific classifier. State is a per-collection
  `{records: {idkey: content_hash}, last_count}` map stored in the Sheet `_meta`
  `wds_seen` cell (ADR 006 pattern; ~420 id/hash pairs stay far under the 50k cell cap).
- `watcher.py` — a gated hook after the MMPC block; the `wds_watcher` import is inside
  the gate so a fault there can't affect the nSITE/MMPC path while disabled.
- `sheet_writer.py` — `wds_seen` added to `_META_DEFAULTS` (read/write for free) and four
  tabs — `WDS New Documents` / `WDS Historical Documents` / `WDS Evidence by Risk` /
  `WDS Page Snapshots` — created **on demand only when enabled/dumped** via
  `ensure_wds_tabs()` (deliberately not in `_TAB_HEADERS`, so the Conservancy-visible
  Sheet gains no empty tab until activation). Structurally parallel to the nSITE New/
  Historical/Evidence/Archived-PDFs tabs (added 2026-07-10, see Addendum below).
- `scripts/seed_wds_state.py` — optional one-shot baseline (hash-only, no visible rows).
- `scripts/dump_wds_historical.py` — one-off bulk dump of the ~420 pre-existing records
  as visible rows in `WDS Historical Documents` + `WDS Evidence by Risk`, idempotent
  per collection. Supersedes `seed_wds_state.py` for the "make history visible" case;
  `seed_wds_state.py` remains for a hash-only re-baseline.
- `wds_archiver.py` — nightly, content-hash-gated raw-HTML snapshot of the 5 collection
  pages into Drive, logged in `WDS Page Snapshots`. The real analog of `archiver.py`'s
  PDF mirror for a portal with no per-record PDFs (the record IS the page). Gated on
  `wds.enabled` exactly like every other Stream C entry point — see Addendum.

### Four safety rules (each learned the hard way)

The risk this design guards is not "fires overnight" (it's dormant + a feature branch),
but "**is safe the moment `enabled: true` lands on `main`.**"

- **A — Never diff a bad fetch.** A collection returning 0 rows, or collapsing below
  half its last-known count, is a transient read failure: skip the diff, keep the
  seen-set, warn. WDS grids only grow, so a real shrink doesn't happen. (Mirrors
  `watcher.py`'s `if not docs: return 1`.)
- **B — Enabling is self-protecting.** The first run with an empty seen-set silently
  baselines (records all, alerts none), so flipping `enabled: true` can't blast ~420
  historical records even without running the seed script. The same path catches an
  over-cap run (`max_new_wds_alerts_per_run`) — re-baseline + warn instead of blast.
  This is the 2026-07-07 backfill/watcher stampede lesson, generalized.
- **C — Detect mutation, not just new rows.** WDS back-fills fields after a record
  appears: a QMR's exceedance flag arrives later, and — the single most important R1
  signal — an application's `Closure Type` transitions pending → **Issued**. Records
  are keyed on immutable **identity** and carry a **content hash** of mutable fields;
  a changed hash re-alerts. A pure seen-set would stay silent on the permit approval.
- **D — WDS owns its severity.** It never calls `email_alerts.is_urgent` /
  `_max_temperature_f` (they scan free text for °F and would trip on a QMR well id or
  capacity digits). A compliance action fires urgent only on a genuine adverse action
  (violation notice / assessed penalty / order / consent judgment), never on a
  `PAID/RESOLVED`.

### Alert mapping (→ existing 3-tier ladder)

| Signal | Tier |
|---|---|
| New Construction Permit application, or one transitioning to Issued | **urgent** (R1) |
| New violation notice / assessed penalty / compliance order | **urgent** (R2) |
| New/changed QMR with `Statistical Exceedence = Yes` | notable (R5) |
| New Operating License application | notable (R1) |
| Annual report with years-remaining < floor (default 3) | notable (R1) |
| New annual report; new QMR = No; new inspection; resolved/paid action | watch (weekly digest) |

Urgent → same-day email (existing SMTP path, no new recipient surface). notable/watch →
the existing Sunday digest (shaped to the nSITE digest record).

## Consequences / residual risks (accepted)

1. **Legacy portal drift** — WDS is a 2001-era app. The pager/`FilteredCount` probe
   fails loudly (rule A: 0/collapsed → skip + warn), so drift surfaces as a
   scrape-health warning, not silent "no new data". Both hostnames are interchangeable
   fallbacks.
2. **Statistical exceedance semantics** — a `Yes` flag is EGLE's *statistical*
   determination, not a drinking-water exceedance. Alert bodies quote the Review Notes
   verbatim; do not restate as "contamination".
3. **~24 h latency** — WDS has no push feed; nightly polling is fine for this
   quarterly/annual/enforcement cadence.
4. **Digest attribution** — WDS notable/watch items ride the nSITE-titled Sunday
   digest, prefixed `[WDS]`. Acceptable for v1; a separate WDS digest is a later option.

## Activation (all Trisha's call)

1. Review + merge the `stream-c-wds` branch to `main`.
2. (Optional) `python scripts/dump_wds_historical.py` once to make the ~420 existing
   records visible as rows in `WDS Historical Documents` + `WDS Evidence by Risk`
   (this also baselines `wds_seen`) — or let the first enabled run self-baseline
   silently instead (rule B), if visible historical rows aren't wanted yet.
3. Set `wds.enabled: true` in `config.yml` and commit.

Until `enabled: true` is on `main`, the WDS block is a no-op — this now applies to
`wds_archiver.py` too (see Addendum): every Stream C entry point checks `wds.enabled`
before doing any work.

## Addendum — tab parity + page-snapshot archive (2026-07-10)

Added structural parity with the nSITE tabs (`WDS New/Historical Documents`, `WDS
Evidence by Risk`) and a page-snapshot archive (`WDS Page Snapshots`, via
`wds_archiver.py`) — see the updated file list above. One correctness issue was
caught and fixed before merge: `wds_archiver.py`'s first draft only checked that
OAuth was configured, not `wds.enabled` — since the sibling PDF archiver's
`GOAUTH_*` secrets are already live in this repo, that draft would have started
creating WDS tabs and writing to the live Sheet on its first nightly cron tick
after merge, regardless of the `enabled` flag. Fixed with a `_should_run()` gate
(checked first, before OAuth) plus a direct unit test, so this class of gap is
caught by the test suite rather than by inspection next time.
