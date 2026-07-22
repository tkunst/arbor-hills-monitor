# ADR 008 — Multi-facility tracking (one monitor, several nSITE facilities)

*Status: accepted — 2026-06-15.*

## Context

The monitor shipped tracking a single nSITE facility, the **Arbor Hills Landfill
(SRN N2688)**. But the Conservancy's case spans more than the landfill. Three
adjacent EGLE-regulated facilities hold evidence the risk register (R1–R8) needs:

- **Arbor Hills Remediation Area** (no SRN; nSITE `-714792003991405124`, ~740
  docs) — the Water/RRD record: DMRs, Compliance Communications, and Violation
  Notice Correspondence. The richest source for the **R5 water-quality / PFAS**
  case (the YCUA leachate exceedances, the Jan-2026 ACO, the 2024 EGLE CSI-Toxics
  PFAS lab report).
- **Arbor Hills Energy, LLC (N1504)** (~54 docs) — the methane-to-electricity /
  diesel turbine plant; the **$750,000 federal/state SO2 Consent-Decree** facility.
  Feeds R1/R2 (enforcement) and R4/R8 (the plant's emissions/toxics).
- **Emerald RNG (P1488)** (16 docs) — the OPAL Fuels / GFL renewable-natural-gas
  plant built as the SO2 compliance pathway. Its Air PTIs (67-23 / 67-23A) and
  PTI-67-23A monthly reports live here. (The PTI *paper* is filed under the
  landfill SRN N2688, but the standalone facility profile is P1488.)

We do **not** track the empty sibling record `P1504` (0 docs), and `N1488` is an
unrelated company (Minth Group), so neither is in the list.

We also do **not** poll a **second nSITE record also named "EMERALD RNG, LLC" that
carries no SRN** (nSITE id `-1194242252385100852` — distinct from the tracked P1488
profile; likely a non-air-program pin with ~0 air docs). Tracking it would only add
noise. It **is** recorded — as a commented-out entry in the `facilities:` block of
`config.yml` — so a future maintainer can find it and enable it in one edit if it ever
becomes material (the comment flags the empty-SRN handling and doc_id-collision checks to
do first). *(Added 2026-07-22, per the Emerald RNG dossier follow-up.)*

## Decision

Replace the single `facility_id` / `srn` config with a **`facilities` list**, and
fetch + concatenate all of them, **tagging every document with its facility**.

```yaml
facilities:
  - {srn: "RA",    name: "Arbor Hills Remediation Area",  id: "-714792003991405124"}  # first
  - {srn: "N1504", name: "Arbor Hills Energy",            id: "-4937599654678851055"}
  - {srn: "P1488", name: "Emerald RNG",                   id: "-5064275074930604158"}
  - {srn: "N2688", name: "Arbor Hills Landfill",         id: "8094300008956198244"}   # last
```

**Order is significant for backfill priority.** Backfill processes the
concatenated list in config order (`todo[:batch_size]` per run), so the facility
listed first is pulled first. The Remediation Area (the R5/PFAS record) leads;
N2688's ~704 not-yet-processed docs are listed last so they don't block it (its
~50 already-processed docs are skipped by `done_or_poisoned` regardless). The
watcher and archiver are order-independent (the watcher handles only the small
daily delta; the archiver mirrors `processed` docs, which inherit this order).

**One Sheet, one `_state` tab, no composite key.** The load-bearing fact:
**nSITE doc_ids are globally unique across these four facilities — verified 0
pairwise overlap (2026-06-15, 754 / 740 / 54 / 16 docs).** So the existing
doc_id-keyed `_state` event log and the shared case-file Sheet work unchanged; a
doc from any facility is just another row. No `(facility, doc_id)` composite key.

**The loop lives in one place.** `nsite_client.fetch_all_documents(session, cfg)`
loops `cfg["facilities"]`, calls the unchanged single-facility
`fetch_site_documents()` per entry, and tags each doc with `facility_srn` /
`facility_name`. `backfill.py`, `watcher.py`, `archiver.py`, and
`scripts/smoke_one.py` all call it instead of fetching one facility. Everything
downstream (todo/batch, parse, classify, Sheet write, `_state`, archive) is
unchanged.

**A trailing `Facility` column, appended (never inserted).** `FEED_HEADERS`,
`EVIDENCE_HEADERS`, and `MEASUREMENTS_HEADERS` gain a `Facility` column **at the
end**, populated from `facility_name`. Appending (not inserting) means the ~50
existing N2688 rows from the live backfill keep their column alignment; their
Facility cell is simply blank (harmless; optionally back-fillable).

**`ensure_tabs` now reconciles the header row every run.** It previously wrote a
tab's header only on creation, so the new `Facility` header would never appear on
the already-created live tabs. It now rewrites row 1 from `_TAB_HEADERS` on every
run — idempotent (touches only the header, never data), which also self-heals
future header drift.

## Adversarial review (folded into the implementation)

- **Schema change shifts existing live data.** *Mitigation:* the column is
  **appended**, not inserted. *Detection:* eyeball the feed tabs after deploy.
  *Recovery:* Sheet version history.
- **New header missing on pre-existing tabs.** *Mitigation:* `ensure_tabs`
  reconciles row 1 every run. *Detection:* a missing `Facility` header.
  *Recovery:* re-run / manual header write.
- **doc_id collision across facilities** would corrupt the shared `_state`.
  *Mitigation:* VERIFIED 0 overlap before shipping (all four facilities, both
  Air and Water programs). *If it ever changed:* a collision would double-write
  one row; the fix would be a composite key. Not needed today.
- **Archiver can't find a non-N2688 PDF's download URL.** *Mitigation:* the
  archiver also uses `fetch_all_documents`, so `by_id` covers every facility; the
  mirrored filename is now `{srn}_{doc_id}.pdf` (was hardcoded `N2688_`).
- **Backfill scope ~doubles** (754 → ~1,564 docs; +~$2–4, +~16 cron nights at
  50/night). *Expected and safe:* the backfill is self-terminating and resumable
  from `_state`; drive it faster with manual `workflow_dispatch` if wanted.
- **740 Remediation DMRs are mostly routine** (Water program). They accrue to
  Historical/digest, never urgent; the value is the Violation Notices and
  PFAS-relevant ones, which still surface. Accepted.

## Consequences

- `config.yml` is the only place facilities are declared; adding a fifth is one
  line. The four call-sites share `fetch_all_documents`.
- The case file now spans four entities, separable by the `Facility` column
  (landfill vs remediation-water vs energy-plant vs RNG-plant evidence).
- After deploy, the next backfill run picks up ~810 new docs automatically (the
  watcher's `max_new_docs_per_run` guard defers the surge to backfill by design).
- Follow-up (not code): update the Conservancy-facing
  `arbor-hills-monitor-sheet-guide.md` for the `Facility` column once the Sheet
  is repopulated (it auto-publishes to the operator-visible Drive folder, so it
  is gated on Trisha's go-ahead).
