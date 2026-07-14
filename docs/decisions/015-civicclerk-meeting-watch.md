# ADR 015 — CivicClerk meeting-change watch (Stream F)

*Status: built — 2026-07-14. Ships `civicclerk_watch.enabled: true`; the first run
baselines every event silently (no alert), so enabling is safe (it cannot flood).
Client + snapshot verified against the live CivicClerk API end-to-end.*

## Context

Trisha needs to know the moment specific public meetings change — an agenda or
minutes posting, a date move, a cancellation, or a document being replaced/removed.
Two bodies, on Washtenaw County's CivicClerk portal:

- **MMPC** (Materials Management Planning Committee, category 72) — the siting arc
  for Arbor Hills. Three meetings have portal pages: **Aug 19 / Sep 9 / Dec 9 2026**
  (events 4005 / 4006 / 4007). Oct/Nov have **no** portal page (the committee isn't
  meeting then), so there is nothing to watch there.
- **Washtenaw County Board of Commissioners** — Working Session (category 27,
  5:30 PM) + Board Meeting (category 26, 7:00 PM), on eight dates Aug 5 → Dec 2
  2026 (15 events). Every date **does** have a live portal page (unlike MMPC's
  Oct/Nov). Less time-critical than MMPC.

**Why not the existing MMPC path (Mirror D / ADR 010)?** Mirror D *mirrors* every
category-72 PDF to Drive by `fileId`. It is an archive: silent (no alert), and
blind to a meeting being **moved, renamed, cancelled**, or a document being
**replaced/removed** (only brand-new fileIds register). It also covers **no BOC
meetings at all**. This watch is the complement — it alerts, and it watches the
whole *meeting record*, not just the PDF set. The two overlap only in that both
will notice a *new* MMPC PDF; that is fine and intended (Mirror D captures the
file, this watch tells Trisha).

## Decision

A new `civicclerk_watcher.py`, structurally a twin of `pfas_watcher.py` (silent
baseline → row-first-then-email → gated on a flag, with an append-only Sheet tab
as race-free state), but driven by the **structured OData API** (`mmpc_client
.fetch_event`, one event by id — same host as Mirror D) instead of HTML hashing.
Each run, for every event that is **due today**:

1. fetch the event; build a canonical **snapshot** and hash it;
2. compare to the last snapshot in the append-only **`Meeting Watch`** tab;
3. first sighting → silent `baseline` row; hash changed → `changed` row **then**
   an email describing what changed; unchanged → no-op.

### What the snapshot hashes (and deliberately does NOT)

Included: `eventName`, `eventDate`, `isPublished`, `eventNotice`, `isDeleted`, and
the document set as `(fileId, type, name, publishOn)` **sorted by fileId**.

Excluded: the per-file display-order `sort` and the per-upload storage `url` (a
rotating GUID path). This is the same class of bug the repo already ate with PFAS's
Sitecore cache-busters (ADR 012): a volatile field in the hash → recurring
false-positive alerts. `eventName` is kept precisely because a `"CANCELLED — …"`
rename is exactly what we want to catch.

### Vanish vs. error (the load-bearing rule)

A watched event can stop returning data two ways, which must be handled
differently (this mirrors the fail-safe ruling recorded in memory
`fail-safe-when-external-semantics-unknowable` and `MMPCFetchError`'s transient
contract):

- **HTTP/JSON error** (`MMPCFetchError`) → **transient**. If a baseline exists,
  skip-and-warn (a blip must never diff into a false alert). If **no** baseline
  exists yet, exit **1** loudly — an activation-time block should surface, not
  no-op forever looking healthy (same as `pfas_watcher`).
- **Successful HTTP 200 that returns no event** for a previously-seen meeting →
  a **real change** (cancelled / removed / renumbered) → alert. `fetch_event`
  returns `None` for this case specifically, distinct from raising.
- 200-empty on the **first** sighting of a configured event is treated as a
  probable bad id: **not baselined** (warn, retry next run), so a later real
  200-empty can't read as "unchanged".

### Cadence is code, not cron

One workflow fires **twice daily**; `is_due_today(cadence, event_date, today)` (a
pure, unit-tested function) decides per event whether to actually check:

- **MMPC** — `every_run` (checked both runs; the siting arc is high-stakes).
- **BOC** — `{weekly_weekday: 0 (Mon), daily_before_days: 3}` — checked weekly,
  plus **daily in the 3 days before** each meeting, so a last-minute agenda drop or
  cancellation is caught. An unrecognized cadence **fails safe to due** (never
  silently stops watching).

### Recipients

`civicclerk_watch.recipients` (Trisha only) is passed to `email_alerts.send_email`
via a new optional `recipients=` param that is used **verbatim** — NOT merged with
the shared `alert_recipients` advocacy list or `ALERT_RECIPIENTS_EXTRA`. Meeting-
logistics changes are hers to triage and forward.

### Scope

Alert-only. No Drive/OAuth, no PDF download (the deliverable is the alert, like
`pfas_watcher`); MMPC PDFs are still archived by Mirror D, and BOC PDFs are not
mirrored anywhere (out of scope — this watch just flags that they appeared).

## Adversarial review (per the plan-hardening rule)

| Risk | Class | Detection | Mitigation (shipped) |
|---|---|---|---|
| Volatile field in the hash → false-alarm flood | show-stopper for credibility | a "changed" alert with a no-op diff | `sort`/`url` excluded; canonical file sort; verified a real re-fetch of event 4005 re-hashes identically |
| Transient blip diffed into a false "cancelled" alert | show-stopper | alert body says "no longer on the portal" with no real cause | error vs 200-empty split; blip → skip-and-warn; unit-tested both branches |
| Watcher silently stops (bad id / bad cadence) | manageable | a meeting changes but no alert arrives | first-sight 200-empty is warned-not-baselined; unknown cadence fails safe to due; loud exit-1 on no-baseline fetch failure |
| Meeting-logistics noise reaching the advocacy list | manageable | Conservancy/MMPC members get emails they didn't want | `recipients` override sends to Trisha only; unit-tested that the override list is what's passed |
| Cursor/state clobbered by a concurrent job | manageable | lost/duplicated baseline | state is the append-only `Meeting Watch` tab, NOT `_meta`; own `concurrency` group |

**Residual risk (accepted):** rides CivicClerk's undocumented OData API (same
dependency Mirror D already accepts, ADR 010). If the API shape changes, fetches
fail → skip-and-warn (after baseline) or loud exit-1 (before) — never a silent
wrong answer. The twice-daily cadence means "the minute they change" is really
"within ~12 hours" (agendas post days ahead, minutes days after, so this rarely
matters); tightening the cron is a one-line change.

## Activation

Ships **on** (`enabled: true`) because, unlike a classified feed, it cannot flood:
the first run only baselines. To pause without losing the tab, set
`enabled: false`. It is keyless (no secret to provision) — Sheets I/O via the
existing service account, alerts via the existing SMTP secrets.

## Consequences

- New: `civicclerk_watcher.py`, `.github/workflows/meeting-watch.yml`,
  `tests/test_civicclerk_watcher.py`, the `Meeting Watch` Sheet tab.
- Additive: `mmpc_client.fetch_event`, `sheet_writer` (tab + 3 helpers),
  `email_alerts.send_email(recipients=…)`, the `config.yml` `civicclerk_watch:`
  block. No existing behaviour changed.
