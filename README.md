# Arbor Hills Document Monitor

An independent, automated monitor of Michigan EGLE regulatory filings for the
**Arbor Hills complex**. It tracks four co-located, EGLE-regulated facilities —
the **Landfill (N2688)**, the **Remediation Area** (water/PFAS), **Arbor Hills
Energy (N1504)** (the SO2 turbine plant), and **Emerald RNG (P1488)** —
backfilling the existing documents and watching for new filings, classifying
each against a fixed risk register, maintaining a full-text-searchable PDF
archive plus a Google Sheet case file, and emailing alerts. Each document is
tagged with its facility (see `docs/decisions/008-multi-facility.md`).

All inputs are **public regulatory data** from EGLE's nSITE portal. No credentials
live in the repo — cloud secrets are GitHub Secrets / local `.env`.

This project takes source data from anyone, provided it has clear provenance —
EGLE filings, the operator, township and county records, community groups.
Every item in the case file is labeled by source so a reader can verify it
independently; the monitor itself does not speak for, or take direction from,
any advocacy organization.

This neutral, records-and-data Monitor is one of two related sites the same
resident maintains; the separate advocacy site (Better Arbor Hills) holds public
comments and policy proposals. They follow different content standards on
purpose, and content must stay on the correct side of that line. Before adding
or reviewing any site page or copy, read **`docs/editorial-standards-two-sites.md`**.
One place this shows up concretely: the **Public Comment Periods** page
(`site/public-comment/`, ADR 035) lists *which* comment periods are open and
when they close (records), and links to the official EGLE notice; the
encouragement to comment lives on the advocacy site, not here.

## What it does

1. **Backfill** (nightly, self-terminating): processes the existing documents
   across the tracked facilities in batches of 50, then becomes a no-op once done.
2. **Daily watcher**: picks up new filings and classifies them. Optionally (when
   `wds.enabled: true`) it also
   polls **Stream C** — the EGLE Waste Data System (Part-115 solid waste, site
   475946): quarterly groundwater reports (R5), annual capacity/airspace (R1),
   permit/license applications (R1), and enforcement (R2). Off by default; see
   `docs/decisions/009-wds-stream-c.md` for activation.
3. **Classify**: each document is OCR'd if needed, then sent to Claude with the
   risk register and signal keywords. Output per doc:
   - `summary`, `key_data_point`
   - `doc_type`: evidence / procedural / opinion
   - `risks`: which of R1–R8 it speaks to
   - `severity`: routine / notable / urgent
   - `measurements[]`: structured readings (temperature/CO/O₂), each flagged
     **measured vs permitted_limit** — see `docs/decisions/004`.
4. **Case file** (Google Sheet tabs):
   - *New Documents* / *Historical Documents* — the live + backfilled feeds.
   - *Evidence by Risk* — evidence docs only, one row per (risk, doc). Filter to
     R8, print, hand to EGLE.
   - *Risk Register* — R1–R8 with auto-counted evidence + most-recent date.
   - *Measurements* — every structured reading; derive per-well temperature
     trends from here without reprocessing.
5. **Alerts** (SMTP): urgent → same-day email; procedural + everything else →
   weekly Sunday digest. Recipients in `config.yml` (edit the list, no code
   change).
6. **PFAS page-watch** (daily, optional — `pfas.enabled: true`): a content-hash
   watch on EGLE's PFAS-investigation page for Arbor Hills (prose EGLE edits in
   place — no feed to parse). Emails a diff when the page's `<main>` content
   changes, ignoring the site's rotating theme cache-busters. Off by default; see
   `docs/decisions/012-pfas-page-watch.md` for activation.
7. **GFL perimeter air (Stream E)** (daily, optional — `gfl_air.enabled: true`):
   the first source of real fenceline **readings**, not documents — GFL's own
   hourly H2S (ppb) / CH4 (ppm) at six perimeter stations, pulled from its public
   ArcGIS FeatureServer. Readings ride the *Measurements* tab (`basis=measured`),
   a small *GFL Air* tab holds the latest-per-station snapshot, and a same-day
   email fires when a reading crosses a conservative, config-driven action level
   (**R3/R4**). A liveness guard (`gfl_air.max_stale_days`) turns a silent stall
   into a same-day "feed appears stale" alert, so a reset ArcGIS cursor can't go
   unseen. Enabled 2026-07-15; see
   `docs/decisions/014-gfl-perimeter-air-stream-e.md`.
8. **CivicClerk meeting-change watch (Stream F)** (twice daily —
   `civicclerk_watch.enabled: true`): watches a hand-picked list of MMPC and
   Washtenaw County Board-of-Commissioners meeting events for **any** change —
   title, date/time, publish status, cancellation, or its agenda/minutes/other
   document set — and emails a change alert (recipients scoped to Trisha, not the
   advocacy list). Complements Mirror D, which only *mirrors* MMPC PDFs and is
   blind to a meeting being moved/cancelled and covers no BOC meetings. Cadence is
   decided in code per event (MMPC every run; BOC weekly + daily in the 3 days
   before each meeting). First run baselines silently; see
   `docs/decisions/015-civicclerk-meeting-watch.md`.
9. **Ridge Wood Elementary H2S (Stream G)** (daily, optional —
   `ridgewood.enabled: true`): mirrors + extracts Barr Engineering's monthly,
   QA'd H2S data reports for the monitor at Ridge Wood Elementary School (run
   under a U.S. EPA agreement — a *different location* from the Stream E
   perimeter fenceline, complementary evidence). Scrapes the monthly report PDFs
   off the public page (never constructs a URL — the filename has an
   unpredictable cache-buster), mirrors each new month to Drive, and writes the
   month's max 24-hr-average concentration to the *Measurements* tab
   (`basis=measured`, attributed to the **Barr/EPA-agreement** monitor — not GFL
   self-report, not an EGLE measurement). A same-day email fires on a stated
   24-hr exceedance of **72 ppb** or 15-min exceedance of **750 ppb** — the
   monitor's own published action levels (the 72 ppb value independently confirms
   Stream E's H2S threshold; **R3/R4**). Off by default; see
   `docs/decisions/016-ridgewood-h2s-stream-g.md` for activation.
10. **ROP renewal watch (Stream H)** (daily, optional — `rop.enabled: true`): all
    three Arbor Hills air facilities have a ROP (Title V air permit) renewal *in
    process* — a renewal reaching its **30-day public comment window** is a
    second advocacy venue, easy to miss in a ~1,800-row statewide export. Watches
    EGLE's EPA ROP Monthly Report CSV (task-status/permit-status/dates for
    N2688/N1504/P1488 — excluding the unrelated "Emerald" name-collision M3333),
    the N2688 renewal folder's file list (a new draft ROP appearing), and the
    statewide public-notice PDF (whether each target SRN appears — one item per
    facility). Emails a change alert to the full advocacy list on any of the
    seven watched items changing; first sighting baselines silently. Off by default; see
    `docs/decisions/017-rop-watch.md` for activation.
11. **EGLE MMD Open Data watch (Stream I)** (daily — `mmd.enabled: true`): the
    state's own facility-registry view of the landfill, on EGLE Materials
    Management Division's keyless public ArcGIS service (layer 0 — the master
    facility/module table). Watches the record set per configured wdsid:
    **475946** (the landfill — `disposalareastatus` "Active - Accepting" plus a
    map-hidden `show=0` compost registration expiring 2030-08-05) and **465941**
    (the compost area — absent from the service; its APPEARANCE is the trip-wire
    that the state has started tracking the expansion parcel). Snapshot-diff
    into the `MMD Watch` tab; alerts (Trisha-only to start) on any change; first
    sighting baselines silently — an empty record set is a valid baseline. See
    `docs/decisions/018-mmd-open-data-watch.md`.
12. **EGLE RIDE / Part 201 + UST status watch (Stream J)** (daily, optional —
    `ride.enabled: false`): RIDE's own web app is auth-walled with no anonymous
    document API, but EGLE separately publishes the underlying per-site status on
    a keyless public ArcGIS service (RRDOpenData — same host/idiom as Stream I,
    two layers). Watches Layer 0 (Part 201 remediation status) for the 5 Arbor
    Hills-area sites — Salem Landfill, Arbor Hills - East, 7667 Chubb Rd, 7941
    Salem Rd, MITC Corridor — and Layer 1 (Part 211 UST) for the GFL facility.
    A `RiskCondition` flip, a `Contaminants` change, or a new `Open_Release` is
    early, citable **R5** (water quality) signal. Snapshot-diff into the `RIDE
    Watch` tab; alerts (Trisha-only to start) on any change; first sighting
    baselines silently. Rides EGLE's RRDOpenData ArcGIS service; if EGLE
    reorganizes it, the fetch fails loudly rather than silently going quiet. New
    source, ships disabled — see `docs/decisions/019-ride-part201-watch.md` for
    activation.
13. **nSITE Submissions watch (Stream K)** (`nsite_submissions.enabled:
    true`): watches a DIFFERENT, wider list than `facilities:` — all 19 of
    Trisha's MiEnviro email subscriptions, resolved to their real nSITE site
    IDs — for a SIBLING nSITE profile to Documents — Submissions
    (application/service-request intake, carrying a stable Submission
    Reference Number, form name, program area, and status). Built after a JPA
    (EGLE/USACE wetlands/floodplain permit application) reached Trisha only
    via her personal MiEnviro email subscription — it never appeared in
    Documents at all, only Submissions. Also added a 5th tracked facility,
    the WRD Land & Water Interface site (`GFL-Arbor Hills Landfill-Washtenaw
    Co`) the JPA itself is filed under, and a 6th, `AHLI` (a bare duplicate
    "Arbor Hills Landfill, Inc." registration with genuine recent activity).
    Alerts distinguish a **brand-new filing** (a Submission Reference Number
    never seen before) from an **existing filing's status advancing** —
    keyed on the reference number, not a generic row diff. Snapshot-diff into
    the `Submissions Watch` tab; alerts (Trisha-only to start) on any change;
    first sighting baselines silently. See
    `docs/decisions/020-nsite-submissions-watch.md`.
14. **Tiered Submissions polling across all 19 subscriptions**: the 14 sites
    beyond the original 5 are duplicate/historical nSITE registrations for
    the same Arbor Hills entities, most with sparse-to-zero activity — polled
    at a **daily/biweekly/quarterly** cadence per site (each srn's entry in
    `nsite_submissions.tiers`) rather than daily for all 19, so a
    dormant duplicate registration still gets checked periodically (typo/
    misfiled-submission insurance) without polling it as if it were active.
    Cadence is a pure, stateless, hash-staggered function
    (`nsite_submissions_watcher._is_due`) firing across a 3-day window per
    period — no stored "last polled" state, and one missed/failed daily run
    doesn't blank a quarterly site out for a full quarter. The Documents
    `facilities:` list (backfill/watcher) is untouched by this — only the
    Submissions watch reads the per-site cadence. See
    `docs/decisions/021-tiered-submissions-polling.md`.
15. **Shared nSITE site registry**: `config.yml`'s top-level `nsite_sites`
    holds the canonical srn/name/id identity for every nSITE site the
    monitor knows about, extracted out of `nsite_submissions.sites` so a
    second profile-specific watch (Violations, queued next) can reference
    sites by srn instead of duplicating the 19-tuple list a second time. Each
    profile watch keeps only its own cadence (`nsite_submissions.tiers`
    today); site identity corrections now have one place to land. Pure
    refactor — the resolved 19-site set is unchanged. See
    `docs/decisions/022-nsite-site-registry.md`.
16. **nSITE Violations watch (Stream L)** (daily, optional —
    `nsite_violations.enabled: true`): watches a THIRD nSITE profile for the
    same 19 sites — **Violations**, which is EGLE's own *enforcement record*
    (a formal finding that the facility was out of compliance, not a filing
    or a permit status). Real depth: RA carries 299 violations back to 2004,
    N2688 58 (most recent 2026-07-08), N1504 3 still unresolved. Unlike
    Submissions, this profile has **no unique-ID field** — verified live
    across all 360 records, where not even a five-field composite is unique —
    so the diff is a full-record **multiset** (`collections.Counter`), the
    ROP/MMD/RIDE idiom. The multiset matters: RA's 299 records collapse to
    only 108 distinct tuples because EGLE genuinely files repeated identical
    rows, and deduplicating would destroy 191 real records. The snapshot is
    stored run-length **counted** rather than one object per record, because
    the plain form is 130,188 characters against a Google Sheets cell's
    50,000-character cap. Alerts on **any** change — a new violation, a
    status advancing, a record withdrawn — and makes **no judgment about
    which status is good or bad** (EGLE's vocabulary is a multi-state
    lifecycle, so this is a trip-wire and a human reads it); a site going
    from zero violations to some gets its own headline. Own per-site
    daily/biweekly/quarterly tiers in `nsite_violations.tiers`, assigned from
    observed violation counts *and* recency — deliberately **not** a copy of
    `nsite_submissions.tiers` (3/3/13 here vs 6/6/7 there). Snapshot-diff
    into the `Violations Watch` tab; first sighting baselines silently. New
    source, ships disabled — see
    `docs/decisions/023-nsite-violations-watch.md` for activation, which has
    **two** steps here rather than the usual one: this stream's workflow file
    is parked at `docs/pending-workflows/nsite-violations-watch.yml` and must
    be moved into `.github/workflows/` before `enabled` is flipped, or the
    watch is never scheduled. (The build session's credentials lacked the
    `workflow` OAuth scope — see `docs/pending-workflows/README.md`.)

17. **nSITE Compliance Actions watch (Stream M)** (daily, optional —
    `nsite_compliance_actions.enabled: false`, ships disabled): watches a
    FOURTH nSITE profile for the same 19 sites — **Compliance Actions**, the
    formal actions EGLE takes in *response* to a violation (Violation Notices,
    Consent Orders, Consent Judgments) — the documented other half of the
    enforcement story the Violations watch (Stream L) covers. Live-fetched
    2026-08-08: N2688 39 actions (a Violation Notice **issued last month**,
    still open, plus a federal Consent Order), RA 10 (incl. the open PFOS
    VN-011821), N1504 2 (both closed). Its candidate key `cmplActnCmplActnNum`
    proved **non-unique** — N2688 files one federal case number on two records
    — so, like Violations, the diff is a full-record **multiset**; every
    ADDED/REMOVED line **leads with the action number** so a status change on a
    known action (Issued → Closed — the highest-value event) reads legibly.
    Makes **no judgment** about which status is good or bad. Own per-site
    tiers (`2/4/13`), deliberately **not** a copy of the Violations tiers:
    N1504 is *daily* for Violations (unresolved) but *biweekly* here (its
    actions are closed). Snapshot-diff into the `Compliance Actions Watch`
    tab; first sighting baselines silently. New source, ships disabled — see
    `docs/decisions/028-nsite-compliance-actions-watch.md` for activation
    (**two** steps: its workflow is parked at
    `docs/pending-workflows/nsite-compliance-actions-watch.yml` and must be
    moved into `.github/workflows/` before `enabled` is flipped).

18. **nSITE Evaluations watch (Stream N)** (daily, optional —
    `nsite_evaluations.enabled: false`, ships disabled): watches a FIFTH nSITE
    profile for the same 19 sites — **Evaluations**, the underlying
    inspection record a violation or compliance action often stems from (a
    Violations record already carries `evalEvalNum`, joining a violation back
    to its evaluation). Live-fetched 2026-08-08/2026-08-22: N2688 477
    evaluations (most recent **2026-08-07**, actively ongoing), RA 40, N1504
    5, P1488 2, WRD 1. UNLIKE Violations/Compliance Actions, `evalEvalNum`
    proved a **genuine unique key** everywhere (477/477 at N2688) — so the
    diff is **ref-number-keyed** (the Submissions idiom), not a multiset: a
    new `eval_num` alerts as a brand-new evaluation, an existing one with a
    changed field alerts as that evaluation's detail advancing. There is no
    status field to trip-wire, so a new inspection appearing IS the primary
    signal. N2688's 477 records serialize to 75,494 chars even in the
    compact positional encoding — over the Sheets cell cap — so N2688
    **permanently runs in a digest-degraded mode** (unlike Violations/CA,
    where the same guard never actually fires); the degraded form still keeps
    `eval_num` visible so a truncated diff can name exactly which evaluation
    is new even without field-level detail. Own per-site tiers (`1/4/14`),
    deliberately not a copy of any sibling: only N2688 is daily. Snapshot-diff
    into the `Evaluations Watch` tab; first sighting baselines silently. New
    source, ships disabled — see
    `docs/decisions/029-nsite-evaluations-watch.md` for activation (**one**
    step here: the workflow file landed directly in `.github/workflows/`
    since the build session's SSH key authenticated non-interactively, so
    only `enabled` needs to flip).

19. **nSITE Permits watch (Stream O)** (daily, optional —
    `nsite_permits.enabled: false`, ships disabled): watches a SIXTH nSITE
    profile for the same 19 sites — **Permits**, a facility's permit
    lifecycle (issued → extended → expiring → terminated) across EVERY
    permit type on file (Air ROPs, Air Permits to Install, Air Reporting
    Schedules, NPDES water permits, Resources/wetland permits) — broader than
    Stream H's targeted ROP watch, which follows only the Air ROP renewal
    process for N2688/N1504/P1488. Live-fetched 2026-08-22: N2688 9 permits
    (incl. Air ROP `ROP0000224`, currently "Extended"), N1504 4 (incl.
    `ROP0000656`), P1488 3 (incl. `ROP0000236`), RA 2, WRD 2, P1504 1
    (already "Terminated"), SANL 1 (already "Expired"). Like Evaluations,
    `prmtPrmtNum` proved a **genuine unique key** everywhere it has any
    records — so the diff is **ref-number-keyed**: a new `prmt_num` alerts as
    a brand-new permit; an existing one with a changed field — most
    importantly `status` advancing (e.g. Extended → Expired) or
    `termination_date` populating — alerts as that permit's status changing,
    the primary signal this watch exists to surface, not a secondary case.
    **Overlaps Stream H at exactly three permit numbers** (confirmed live);
    this watch does NOT suppress them (that would blind it to a real status
    change) and instead disambiguates explicitly in the alert copy — the two
    streams trip-wire different events (permit status/lifecycle here vs. the
    public-comment-window entry there) and neither supersedes the other. Own
    per-site tiers (`3/2/14`), deliberately not a copy of any sibling:
    N2688/N1504/P1488 are all daily here (each currently holds an "Extended"
    Air ROP, this profile's own headline concern), unlike any prior profile
    where at most one or two sites earn daily. Snapshot-diff into the
    `Permits Watch` tab; first sighting baselines silently. New
    source, ships disabled — see
    `docs/decisions/030-nsite-permits-watch.md` for activation (**one**
    step here: the workflow file landed directly in `.github/workflows/`
    since the build session's SSH key authenticated non-interactively, so
    only `enabled` needs to flip).
20. **nSITE Complaints watch (Stream P)** (daily, optional —
    `nsite_complaints.enabled: false`, ships disabled): watches a SEVENTH
    nSITE profile for the same 19 sites — **Complaints**, citizen/agency
    reports filed against a facility, often the trigger for an inspection.
    Live-fetched 2026-08-22: N2688 carries **6,396** complaints (2007-2026, a
    2016-2020 filing burst is ~92% of the total, but only 60 in the trailing
    365 days — roughly one every 6 days at today's rate), RA 5, COMP 1
    (ancient), every other site 0. `submSubmRefNum` proved a **genuine unique
    key**, but the volume rules out every per-record encoding this repo's
    other nSITE watches use — even Violations'/Compliance Actions' own
    digest-degradation machinery still serializes to ~102,000 chars here (2x
    the 50,000-char Sheets cell cap), since complaints carry zero
    duplicate-tuple compression, unlike Violations' RA. So the snapshot is a
    tiny `{n, hash, latest[K]}` fingerprint instead — small **by
    construction**, not degraded into smallness. It deliberately does NOT try
    to name which complaint(s) are new: an earlier version tried to, and
    three independent adversarial-review rounds each found a different way a
    windowed top-K snapshot can misname an old survivor as new (a removal
    promoting a hidden entry into view; the same at the bottom of the window;
    an existing complaint's date being corrected by EGLE) — a structural
    limit of never storing the old full ref-set, not a fixable edge case, so
    every change alert is an honest count-change note plus recent-complaint
    context, never a claimed diff. A removed/withdrawn complaint is always
    labeled a removal, never misreported as new. Own per-site tiers
    (`1/3/15`), assigned from the complaint-filing RATE, not the raw
    6,396 — only N2688 earns daily.
    Snapshot-diff into the `Complaints Watch` tab; first sighting baselines
    silently, verified at N2688's real scale. New source, ships disabled —
    see `docs/decisions/031-nsite-complaints-watch.md` for activation (**one**
    step here: the workflow file landed directly in `.github/workflows/`
    since the build session's SSH key authenticated non-interactively, so
    only `enabled` needs to flip).
21. **nSITE Active Public Notices watch (Stream Q)** (daily, optional —
    `nsite_public_notices.enabled: true`, activated 2026-08-26): watches the
    EIGHTH and last nSITE profile for the same 19 sites — **Active Public
    Notices**, the formal comment-window announcements (permit renewals,
    draft permits, hearings) EGLE files per site. Live-fetched twice a month
    apart (2026-07-24 and 2026-08-25): **one record total**, across all 19
    sites, both times — and both times it was itself a ROP renewal
    comment-window notice (P1488 in July, N1504 in August) that the
    monitor's separate, targeted ROP watch (Stream H) already tracks and
    alerts on through a different mechanism. No program-area or
    facility-category field exists anywhere in this profile's schema
    (confirmed against the API's own field-metadata block) to scope around
    that overlap via a structured filter. **Resolved 2026-08-26
    (Trisha-directed):** rather than pick pure standalone or a Stream-H
    state-based dedupe, this watch suppresses its own EMAIL — never the
    durable Sheet row — when a notice's text reads as a ROP renewal by
    keyword match (`nsite_public_notices.rop_alert_suppression`, defaults
    `true`), failing open (still alerting) on any non-match, mixed batch, or
    degraded/unreadable snapshot. Diff is ref-number-keyed on an id
    regex-extracted from the notice's own URL (`publicNotifPnurl`) —
    deliberately NOT `publicNotifExtrnlPublNoticeNum` (null on every live
    record seen), and NOT conditional on that field ever populating, to
    avoid misreporting one unchanged notice as a false "window closed" +
    "new window opened" pair if EGLE later fills it in. A single long
    comment field is capped + content-hashed per record, independent of the
    general multi-record Sheets-cell budget guard every sibling nSITE watch
    also carries. Snapshot-diff into the `Public Notices Watch` tab; first
    sighting baselines silently. This completes the six-profile nSITE
    build-out staged 2026-08-08 — see `docs/decisions/032-nsite-active-
    public-notices-watch.md` (incl. its 2026-08-26 addendum, which also
    flags the suppression heuristic's untested false-positive direction —
    validated only against the two real ROP records seen) for the full
    writeup.

> **A note on the document links (corrected 2026-08-23 — see ADR 007's
> addendum).** Every case-file row's **Link** column points to EGLE's nSITE
> portal (`https://mienviro.michigan.gov/ncore/downloadpdf/<id>`). Clicking one
> can show a **"Server Error in '/ncore' Application"** page (or a plain
> `{"errorCode":401,...}` JSON error) **with no download at all** — an earlier
> version of this note called this harmless and said the file downloads
> anyway; that was wrong, confirmed by direct reproduction. **The real cause:**
> nSITE's `downloadpdf`/`downloadfile` endpoints reject requests whose
> `Referer` header isn't one they recognize — and Google's own link-click-
> through (opening a link from inside Gmail, or clicking a URL in a Sheets
> cell) routes through a `google.com`-hosted redirect first, which nSITE's
> server rejects. It is NOT random and NOT harmless: a direct paste of the
> same URL, with no referer, always works — the monitor's own server-side
> fetches never hit this because they never carry a browser referer either,
> which is why ingestion was never affected. **The actual fix, live since
> 2026-08-23:** new documents are now mirrored to Google Drive *immediately*
> when first processed (`archiver.mirror_one_now()`), and the Sheet row/alert
> link to that durable Drive copy instead — a Drive-hosted link isn't subject
> to nSITE's referer check at all, since the destination is a Google service.
> If a row still shows a raw nSITE link (documents processed before
> 2026-08-23, or a mirror attempt that failed), the safe workaround remains:
> paste the URL directly into the address bar rather than clicking it from
> Gmail or a Sheets cell.

## Risk register (R1–R8)

R1 expansion eligibility · R2 violations history · R3 odor nuisance · R4 air
quality · R5 water quality · R6 environmental justice · R7 truck traffic ·
**R8 overheating / ETLF** (the evidence-dense new one — HOV waivers, WOI Status
Reports, temperature exceedances, CJ No. 2020-0593-CE). Full text in
`risk_register.py`.

## Develop / run tests

```sh
bash scripts/setup.sh   # venv + deps; <10 min from a fresh clone
pytest -q               # hermetic: synthetic PDFs, all APIs mocked, no secrets
```

## Deploy checklist (before the first scheduled run)

1. **GCP service account** — create project, enable the Sheets API, make a
   service account, download its key JSON. Full steps: `scripts/setup_gcp.md`.
2. **Share** the case-file Sheet with the service-account email **as Editor**.
   (No Drive folder share is needed for the *service account* — it has no Drive
   storage quota on a personal Gmail and cannot create files there, so the main
   Sheet rows link to the canonical nSITE source URL and processing state lives
   in the Sheet's own `_state` / `_meta` tabs. See
   `docs/decisions/006-state-in-sheet-no-drive-archive.md`. Durable PDF archiving
   is handled separately by the OAuth archiver — step 10, active since
   2026-06-15.)
3. **GitHub Secrets**: `ANTHROPIC_API_KEY`, `GDRIVE_SA_KEY` (the key JSON
   contents), `GSHEET_ID`, and (for email) `SMTP_HOST`, `SMTP_PORT`,
   `SMTP_USER`, `SMTP_PASSWORD`. `GDRIVE_FOLDER_ID` is no longer used and can be
   left unset. `GSHEET_ID_PRIVATE` is **optional** — a separate, private Sheet
   (shared only with the service account, never the operator) for the Sunday
   digest's "Upcoming Activities" section; if unset, that section is simply
   omitted (see `upcoming.py` + `docs/decisions/027`).
4. **MMPC document archive (Mirror D)** — **done, active since 2026-07-11**: the
   `GOAUTH_MMPC_FOLDER_ID` secret is set and `mmpc_archive.enabled: true` is on
   `main`, so Mirror D auto-downloads every MMPC Agenda/Minutes PDF from
   CivicClerk (see `docs/decisions/010`). The older "go check the minutes page"
   reminder email was retired — see `docs/decisions/013`.
5. **Set the real alert recipients** in `config.yml`.
6. **Branch protection** on `main` (require the CI checks).
7. **Verify Sheet-backed state against the real API first** (no Anthropic call,
   no cost): `python scripts/verify_state.py` — creates the tabs, writes a
   throwaway `_state` row, reads it back through the same `read_state` the jobs
   use, asserts it round-trips, then clears it. This proves the append-only
   state actually persists against the live Sheets API (the unit tests only
   exercise a fake), so the backfill can't silently reprocess the same batch
   forever. Run it before the first real batch.
8. **Smoke-test one document live** (~$0.01, needs only `ANTHROPIC_API_KEY`):
   `python scripts/smoke_one.py` — validates the live `messages.parse` shape and
   surfaces any output truncation before you commit to a 50-doc batch. If it ever
   reports "Classification truncated at max_tokens", raise
   `classification_max_tokens` in `config.yml`.
9. **Run `backfill` to completion BEFORE enabling the daily schedule.** Trigger
   `backfill` manually (`workflow_dispatch`); each run does 50 docs and logs
   `N total, M done, K remaining`. Confirm `M` advances run-to-run (≈50→100→…).
   Repeat (or let the 2am `backfill` cron run nightly, ~15 days) until it logs
   **"Backfill complete"**. Only THEN uncomment the `schedule:` block in
   `.github/workflows/daily.yml` and push. Enabling the daily watcher while
   history is unprocessed would flood the live feed with historical docs and
   fire urgent alerts on years-old exceedances (the watcher has a
   `max_new_docs_per_run` backstop, but disabling the schedule is the clean fix).
10. **Durable PDF mirror — ACTIVE since 2026-06-15** (was optional; now set up).
    Insurance against nSITE link rot AND (since 2026-08-23, ADR 007's
    addendum) the functional fix for nSITE's referer-rejection error (see the
    document-links note above). The four `GOAUTH_*` secrets are set, the
    mirror folder ("Arbor Hills EGLE Document Mirror") is created and shared
    "Anyone with the link → Viewer", and every new document is now mirrored
    **inline** the moment it's processed (`watcher.py`/`backfill.py` calling
    `archiver.mirror_one_now()`), with `archive.yml` (3am ET daily) as a
    catch-up net for anything that misses the inline path. To re-do the setup
    (e.g. after a token revoke): run `python scripts/oauth_setup.py
    <oauth-client.json>`, re-set the `GOAUTH_*` secrets it prints (now needed
    by `daily.yml`/`backfill.yml` too, not just `archive.yml`), and re-share
    the mirror folder. Full steps: `scripts/setup_gcp.md` §9.

## Scheduling

- `backfill.yml` — 2am ET daily, batches of 50, self-terminating.
- `daily.yml` — 6am ET daily (new docs + alerts). **Schedule starts
  DISABLED** (only `workflow_dispatch`); uncomment the `schedule:` block after
  backfill completes — see deploy step 9.
- `archive.yml` — 3am ET daily (durable PDF mirror). **ACTIVE since 2026-06-15**
  (the `GOAUTH_*` secrets are set); mid-backfill — see deploy step 10.

## Cost

Backfill of ~754 docs at Haiku rates ≈ **$2–4 total**; routine monitoring (a few
docs/day) is essentially free. Model is configurable in `config.yml`.

## Residual risks (accepted)

- **Nothing watches the watcher.** If a scheduled run fails, detection relies on
  **GitHub's workflow-failure emails** — confirm those are enabled for the repo
  owner (GitHub → Settings → Notifications → Actions). Recovery: re-run the
  failed workflow; runs are idempotent and resume from the Sheet's `_state` tab.
- **nSITE link rot AND referer rejection (now actively mitigated by the
  archive, ADR 007 + its 2026-08-23 addendum).** A raw nSITE link dies if
  EGLE removes/renames the document, AND separately errors for a human
  clicking it from Gmail or a Sheets cell (nSITE rejects the `google.com`
  referer Google's own link-click-through sends — see the document-links
  note above). The OAuth archiver closes both: every new document is now
  mirrored to Drive **inline**, the moment it's processed, with the nightly
  `archive.yml` run as a catch-up net. The residual window is whatever the
  inline mirror attempt missed (a transient Drive/OAuth blip) plus documents
  processed before 2026-08-23 that haven't been backfilled to a Drive link
  yet — shrinking as `archive.yml`'s catch-up runs.
- **MMPC archiving rides CivicClerk's undocumented JSON API.** Mirror D (ADR 010)
  downloads MMPC PDFs through a public API found by inspecting the portal's own
  traffic; if CivicClerk changes it, the fetch fails loudly (aborts the run)
  rather than silently archiving nothing. The older poll-a-URL "go check the
  minutes" reminder this superseded was retired in ADR 013.
- **GFL perimeter air rides GFL's undocumented ArcGIS service (Stream E, ADR 014).**
  The poller reads Barr Engineering's public FeatureServer behind GFL's dashboard;
  if Barr changes the service or schema the fetch fails loudly (aborts the run)
  rather than silently capturing nothing, and a per-poll reading-count log makes a
  drop to zero visible. It is also GFL's own **self-reported** data (attributed as
  such in every row), and by default the shared Measurements tab keeps a daily
  digest of the hourly feed (the source remains the system of record for full
  history — see ADR 014). One silent stall the loud-fetch guard can't catch — the
  ArcGIS cursor resetting below the stored value, so `OBJECTID > cursor` returns
  nothing forever — is covered by a liveness check that alerts after
  `gfl_air.max_stale_days` (default 3) of zero new readings. Enabled 2026-07-15.
- **Ridge Wood H2S rides an undocumented public report list (Stream G, ADR 016).**
  The archiver scrapes the monthly report links off Barr Engineering's public page
  (there is no API); if the page restructures or a link path changes, the scrape
  fails loudly (aborts the run) and a per-poll report-count log makes a drop to zero
  visible — it never diffs a partial list as "no reports". The exceedance classifier
  is deliberately **fail-safe**: it alerts on any numeric daily value ≥ 72 ppb and on
  the *absence* of the report's standard all-clear statement (never positive-matching
  a 750 ppb exceedance format that no published report has yet shown), and a report
  with no text layer (a future scan) is mirrored + flagged for manual/OCR review, not
  dropped. Off by default (`ridgewood.enabled: false`).
- **Classification is model output.** `key_data_point` and `measurements` can be
  wrong. The original PDF link is on every row; the `basis` flag and the
  measured-only urgency rule guard the highest-stakes error (permitted ceiling
  read as a crisis). Spot-check the Sheet against source PDFs.
- **nSITE API shape could change.** Verified working 2026-06-13 (754 docs). If a
  daily run returns 0 docs it aborts rather than wiping state.

## License / reuse

Public regulatory-data tooling — useful to other Great Lakes advocacy groups.
The parser (`egle_doc_parser.py`) is intentionally domain-agnostic.

## Privacy pre-push hook (fresh clones — read this if a push is blocked)

This is a **public** repository. It ships a local `pre-push` git hook that scans
what you are about to push and **blocks the push** if it finds configured private
strings (names, etc.) in tracked text or in a PDF's text layer — so they cannot be
published by mistake. It is a local prevention layer only; there is no CI for it.

Enable it after cloning:

```sh
scripts/install-hooks.sh
```

That sets `core.hooksPath=.githooks` for this clone.

### Fail-closed behavior (this will bite a fresh clone)

The hook is **fail-closed and global to every clone**. Once `install-hooks.sh` has
run on a machine, that machine **cannot push this repo** (or any other repo that
uses this hook) until a local term-list file exists at:

```text
~/.config/privacy-gate/terms.txt
```

The term list is **deliberately not stored in this repository** — committing the
private strings would publish exactly what the hook is meant to protect. A fresh
clone that lacks the file will therefore see **every push blocked**, with:

```text
privacy_scan: no terms configured.
Create ~/.config/privacy-gate/terms.txt (one term per line).
Push blocked by privacy gate.
```

Fix it by creating the file (one private string per line; blank lines and lines
starting with `#` are ignored):

```sh
mkdir -p ~/.config/privacy-gate
"$EDITOR" ~/.config/privacy-gate/terms.txt
```

Then push again.

### Other behavior and troubleshooting

- **Matching** is case-insensitive and fixed-string. Committed data files
  (`*.csv`, `*.xlsx`, `*.xls`) are skipped by the text scan, because public
  datasets contain unrelated real names by design.
- **Non-searchable (image-only) PDFs are blocked**, because their text cannot be
  read. Make the PDF searchable first — `ocrmypdf in.pdf out.pdf`, or `vision-ocr`
  for higher accuracy on hard scans — then re-check. Searchable PDFs have their
  text layer scanned like any other text.
- **Emergency bypass:** `git push --no-verify` skips the hook. Use it only when you
  are certain the content is clean.
- **Disable the hook entirely:** `git config --unset core.hooksPath`.
