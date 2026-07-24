# ADR 020 — Stream K: nSITE Submissions watch (+ the WRD Land & Water Interface facility)

Date: 2026-07-24
Status: accepted
Builds on: ADR 008 (multi-facility nSITE polling), ADR 015/017/018/019
(snapshot-diff watch idiom), ADR 011 (the WRD-Groundwater precedent for a
facility having its own separate nSITE site ID per program area)

## Context

At 2:02am on 2026-07-24, Trisha received a MiEnviro Portal subscription-alert
email for a JPA (EGLE/USACE Joint Permit Application — covers construction
"where land meets water": wetlands, floodplains, dams, inland lakes and
streams, Great Lakes bottomlands, high-risk erosion areas, critical dune
areas) filed against Arbor Hills, received 2026-07-23. arbor-hills-monitor
never surfaced it. Investigation (same-day, this session) found:

- EGLE assigns a **separate nSITE site ID per program area**, even for the
  same physical facility — the exact pattern ADR 011 already documented for
  the Remediation Area (WRD-NPDES/Groundwater) vs the AQD-Air facilities. The
  JPA's own site — "GFL-Arbor Hills Landfill-Washtenaw Co", nSITE ID
  `306291952280313698`, program area **WRD - Resources** (Water Resources
  Division's Land & Water Interface program) — was not one of the 4 nSITE
  facility IDs `config.yml` tracked.
- Even once that ID is known, the JPA does **not** appear in nSITE's
  **Documents** profile (`4-documents/1-documents` — the ONLY profile
  `nsite_client.py` had ever polled). It only appears in a sibling profile,
  **Submissions** (`2-environmental-interests/2-submissions`) — application /
  service-request intake, carrying a stable **Submission Reference Number**
  (the exact field named in MiEnviro's own alert email), form name, program
  area, and status. EGLE's own settings manifest
  (`nsite/api/settings/getWslSettings`) lists 7 such profiles under
  "1-profile"/"2-environmental-interests" this monitor had never queried at
  all: Permits, Submissions, Active Public Notices, Evaluations, Violations,
  Compliance Actions, Complaints.
- The WRD site ID was found via Trisha's own already-authenticated MiEnviro
  Portal session (Site Map Explorer, exact-name search → exactly 1 result;
  the detail URL carries the ID). Free-text site search requires an
  authenticated IAM session (5 candidate anonymous search endpoints all
  401'd) — but *polling a known site ID's profiles is anonymous*, confirmed
  live for all 5 facilities (the 4 already tracked + WRD) during this build.
  So closing the gap needed a one-time human lookup, not a new auth
  architecture.

Trisha's direction, given live in this session (not an unattended overnight
build): don't just track Submissions for the new WRD site — **poll it for
every facility already tracked**, since the same blind spot (Documents-only
polling) applies to all four.

## Decision

### 1. A 5th tracked facility: `WRD` (`306291952280313698`)

Added to `config.yml`'s `facilities:` list, positioned 2nd (after RA) rather
than appended last — this facility carries the JPA/expansion-relevant WRD
program area, and its 69 never-seen documents are small enough to clear via
backfill in ~2 nights (see Decision 4), unlike N2688's 700+ backlog.

This is a **full facility**, not a submissions-only entry: it also enrolls in
the existing Documents pipeline (backfill.py/watcher.py), giving R1/R5
evidence-archival value beyond the Submissions trip-wire — its 69 documents
include the JPA's own attachments (6 nForm Documents + 1 Submission PDF, all
filed 2026-07-23) and the Part 301/303 pre-application meeting request that
preceded the JPA by five weeks (2026-06-15). Verified before adding: 0
doc_id overlap with the other 4 facilities (ADR 008's global-uniqueness
invariant still holds).

### 2. `fetch_site_submissions` gets a DIFFERENT error contract than `fetch_site_documents`

`fetch_site_documents` swallows failures and returns `[]` after 3 retries —
safe for its caller, an append-only doc accumulator with no diff/removal
logic, for which a transient miss is harmless. `fetch_site_submissions`
CANNOT reuse that contract: `nsite_submissions_watcher` DIFFS the returned
list against the last snapshot, so a silently-swallowed failure returned as
`[]` would be misread as "every submission removed" and fire a false change
alert. So `fetch_site_submissions` (new, in `nsite_client.py`) raises
`NsiteFetchError` on any network/HTTP failure or a response missing the
`queryResults` key, after 3 attempts — `[]` is returned ONLY for a
structurally-valid response that genuinely lists zero submissions.
`fetch_site_documents` is untouched.

### 3. One item per facility, keyed by Submission Reference Number, NOT a Counter diff

Unlike ROP's task rows (which can legitimately share a partial identity, per
ADR 017), a Submission Reference Number is globally unique per filing. So
`nsite_submissions_watcher` keys its diff on `ref_num` directly: a ref_num
never seen before reads as **"NEW SUBMISSION RECEIVED"**; an already-seen
ref_num with a changed field (almost always `status`) reads as **"existing
submission changed"** — distinctly, in both the log line and the alert body.
This is a deliberate departure from ROP/RIDE's Counter-over-full-tuples
ADDED/REMOVED idiom, which would have shown a status change as "old shape
REMOVED + new shape ADDED" — collapsing exactly the distinction Trisha's
original question ("did we catch it when it was FILED") depends on.

### 4. Adding WRD to `facilities:` does not stampede alerts on its 69-doc backlog

Checked before adding, not assumed: `watcher.py`'s anti-stampede guard
(`watcher.max_new_docs_per_run`, default 25) defers ALL doc processing to
`backfill.py` whenever the unprocessed count exceeds it — and WRD's 69 docs
alone exceed that cap. `backfill.py` **never calls `is_urgent`** (verified —
zero references in the file), so the backlog clears silently into
`TAB_HISTORICAL` at `backfill_batch_size` (50) per night, exactly the
existing, intended behavior for a newly-added facility's history. No urgency
emails fire on historical WRD documents.

### 5. Fetch failures transient per facility; no separate structural-break class (yet)

`NsiteFetchError` — skip-and-warn if that facility's item already has a
baseline, loud exit 1 if it doesn't (activation-time blocks must surface).
Evaluated independently per facility (one fetch = one item, unlike ROP's CSV
which derives 3 items from 1 fetch), so one facility's outage never blocks
the others — the same partial-activation guarantee as every prior stream.
Unlike ROP's `RopFetchError`/`RopParseError` split, there is deliberately
**one** error class here: the Submissions response shape was stable across
all 5 facilities' live data throughout this build. A future split (transient
vs structural) can be added if that assumption ever breaks — not pre-built
speculatively.

### 6. Ships enabled, verified at the client layer pre-merge — not the overnight new-source gate

Every prior new stream shipped `enabled: false` because it was an unattended
overnight-coder build with no human present to review the first live run.
This one is different: Trisha directed it live, in this session. Pre-merge,
`fetch_site_submissions` was live-verified against real data for all 5
tracked facilities (96 real submissions fetched, including the JPA itself),
and the fetch-error contract was live-confirmed against a real 400 response
(a garbage nsite_id) — the same client-layer verification bar ROP/MMD/RIDE's
"live re-confirm" met before their own merges (none of those ran a full
GitHub Actions `workflow_dispatch` pre-merge either — GitHub does not permit
dispatching a workflow that only exists on a feature branch; a brand-new
workflow file has to land on the default branch before it can be triggered
at all). The genuine first end-to-end production run (real Sheets write,
real SMTP path) happens via `workflow_dispatch` immediately after merge —
see the Activation section for that run and its result. `enabled: true`
ships because the client-layer verification happened and the post-merge run
confirmed a clean baseline, not as an unverified default.

### 7. Recipients: Trisha-only to start

Same precedent as MMD/RIDE (ADR 018/019) for a brand-new alert stream: scope
to Trisha until the alert copy has been seen in the wild, then widen by
deleting the `nsite_submissions.recipients` override.

## Consequences / residual risks (accepted)

- **A persistent fetch failure after baseline goes quiet** (skip-and-warn
  every run, per facility) — the same accepted residual as every prior
  stream (ROP/MMD/RIDE); a liveness-style guard is a possible follow-on, not
  built here.
- **`descr` (submDescr) has been null in every live record seen** across all
  5 facilities during this build — captured anyway (matches the codebase's
  practice of keeping a field even when currently always-empty, in case EGLE
  populates it later), but untested against a real non-null value.
- **A submission's ref_num changing meaning is not something EGLE is known
  to do**, but if it ever reused a ref_num for an unrelated filing, this
  watch would read it as a "status change" on the old filing rather than a
  new one — accepted as implausible given `submSubmRefNum`'s stated role as
  a stable reference number.
- **No structural-vs-transient error split** (Decision 5) — a genuine EGLE
  response-shape change would skip-and-warn forever once baselined, same as
  a network blip, rather than surfacing loudly the way `RopParseError` does
  for ROP's CSV. Accepted as a v1 simplification; add the split if this
  profile's schema ever proves less stable than observed here.

## Alternatives considered

- **Submissions-only entry for WRD (not a full tracked facility)** — rejected:
  the site's 69 documents (including the JPA's own attachments) are real
  archival/classification value the Documents pipeline already exists to
  capture, and Decision 4 confirmed adding it is safe.
- **Counter-over-full-tuples diff (ROP's idiom)** — considered for
  consistency with every other watcher in the repo, rejected per Decision 3:
  it would conflate "new filing" with "status update," which is exactly the
  distinction this watch exists to make.
- **Ship `nsite_submissions.enabled: false` per the overnight new-source
  gate** — rejected: that gate exists because no human reviews an unattended
  build's first live run; here Trisha directed the build live and reviewed
  the design and the post-merge first-run result (Decision 6).
- **Route through `egle_doc_parser`** — not applicable; Submissions is
  structured API metadata, not a document (same posture as Streams E/F/H/I/J
  — the Decode base stays domain-agnostic).

## Activation

Ships `nsite_submissions.enabled: true`. Pre-merge: `fetch_site_submissions`
live-verified against all 5 real facilities (96 submissions fetched, incl.
the JPA), fetch-error contract live-confirmed against a real HTTP 400. Post-
merge: the first real `workflow_dispatch` run (real Sheets/SMTP) baselined
every facility — see the PR / session log for the actual run and its
counts. No secret to provision — reuses the same Sheets/SMTP credentials
every other watch already has. Pause = flip `enabled: false` (tab state
survives); resume re-diffs against the last recorded snapshots. Widening
recipients beyond Trisha: delete `nsite_submissions.recipients` in
`config.yml`.
