# Overnight-coder handoff -- pull EGLE RRD records routinely (Phase 1) + MPART PFAS data layers (Phase 2)

*Staged 2026-09-28 (Trisha-directed). Read `docs/overnight-coder.md` first -- this file IS the goal.
Both phases are NEW external sources, so they ship `enabled: false` and are feasibility-gated. One
config edit touches a LIVE stream (`ride:` is `enabled: true`), so treat that edit as a live-path change
per `overnight-coder.md` Step 3 (real-specimen verification, no mocked-green merge). Recommended model
tier: **Sonnet** (source recon + a new watcher; not Haiku). Open a PR; merge only if every Step-8 gate
passes. Anything that publishes to the public feed stays GATED on Trisha.*

## Invocation

Branch name suggestion: `rrd-mpart-records`. If Phase 1's spike finds no pollable RRD document channel
beyond the GovQA archive, still ship the GovQA sweep (Phase 1C) and Phase 2. Neither depends on RIDE.

---

## Why

EGLE has four divisions with Arbor Hills records. The monitor pulls three of them routinely:
- **AQD** (air): via nSITE.
- **MMD** (solid waste): via WDS and nSITE.
- **WRD** (water): via nSITE and EPA ECHO.

The fourth, **RRD (Remediation and Redevelopment Division)**, is watched only for **status** (Stream J /
ADR 019: RRDOpenData layers 0 and 1, RiskCondition/Contaminants), never for **documents**.

What that cost us: EGLE FOIA **E614007-080526** (filed 8/2026 by defense counsel Gordon Rees in *Urban
Investment v. Arbor Hills*; released publicly on EGLE's GovQA archive) contained about 45 RRD file
documents we had never seen:
- the 1981-2004 Act 307 / Part 201 file on the **Holloway Landfill** (now Arbor Hills East, site 81000004);
- a 2016 leaking-UST release + closure (Leak C-0076-16, **UST facility 00038889**, which is NOT in our
  watch list);
- **EGLE RRD's October 2021 PFAS memo**. Its draft concludes groundwater contamination at and east of the
  landfill came "from landfill leachate and not from the use of AFFF," and it says RRD "did not have
  access to any records from [MMD] or [WRD]."

Those records are now hand-curated (Hand-Curated rows 92-141). But nothing will catch the next ones.

**MPART** (Michigan PFAS Action Response Team, a multi-agency team housed at EGLE, not a division) publishes
PFAS results as **keyless ArcGIS open-data layers**. The monitor watches MPART's Arbor Hills *web page* (PFAS
Page Watch, ADR 012) but none of the data layers. The Johnson Drain fish results (8/5/2021) and the
surface-water results were found by hand in 9/2026.

---

## Phase 1 -- RRD records

### 1A. Feasibility spike (READ-ONLY; record findings in the PR description)

Known recon: queue item #69 (7/2026) found RIDE's web app is an Angular SPA behind a login with **no
anonymous document API**. Do NOT try to log in, and do not use credentials. Re-check only these anonymous
channels, in order, and stop each at the first clear yes/no:

1. **RIDE public "Inventory of Facilities"** (`https://www.egle.state.mi.us/RIDE/inventory-of-facilities/facilities?...&957388bf_programNum=<SiteID>`).
   MPART links this page publicly. A plain GET returns only the SPA shell (checked 2026-09-28). Use
   Playwright to load it anonymously and capture its XHR/fetch calls. Does any **anonymous** JSON endpoint
   list facility records or documents? If it needs auth, it's a no.
2. **nSITE / MiEnviro** (anonymous per-site profiles via `nsite_client`): do RRD-program sites or records
   exist for these sites? (Part 213 LUST closures and some RRD submittals may be in MiEnviro.) Check by
   site identifiers and addresses. Site *search* needs a login, so look for a known site id via the
   existing registry or an nSITE document that references the RRD site.
3. **RRDOpenData ArcGIS** (`gisagoegle.state.mi.us/arcgis/rest/services/EGLE/RRDOpenData/MapServer?f=json`):
   enumerate ALL layers, not just 0 and 1. Is there any documents, events or releases layer?
4. **EGLE GovQA public FOIA archive** (`https://michiganegle.govqa.us/WEBAPP/_rs/OpenRecordsSummary.aspx?view=1`).
   This is the channel **known to work**: E614007 came from here. Prior-art tools live OUTSIDE this repo
   at `/Volumes/Samsung-Pro-2TB/Cowork-claude/documents/arbor-hills/analysis/semcog-landfill-etlf-comparison-2026-09-21/data/egle-foia-archive/_tools/`
   (`pw_search.py`, `rid.py`, `dl.py`, `getall.py`) and `../README.md` there. Gotchas they solved:
   - the search only filters after posting to the session URL;
   - the date filter is ignored, so page with headless Playwright;
   - CSV export times out;
   - downloads need `-L` to follow a 302 to Azure blob storage.

### 1B. Config fix on the LIVE `ride:` stream (small, do it regardless of 1A)

- Add **UST facility `00038889`** (Advanced Disposal / Arbor Hills Landfill, 10690 W Six Mile; LUST
  C-0076-16, 2016) to `ride.facility_ids`, alongside the existing `00040223` (GFL, 7811 Chubb Rd).
- `ride` is `enabled: true`, so this is a LIVE-path change. Verify with one real layer-1 query that
  00038889 returns a record and that the first sighting baselines silently (no alert storm). Update the
  config comment and ADR 019's watched-set note.

### 1C. Build: GovQA archive watch (the reliable channel)

A new stream, e.g. `govqa_watcher.py` + `govqa_client.py`, shipped `enabled: false`:
- **Daily keyword sweep** of the public archive: "Arbor Hills", "Holloway", "10690", "Six Mile" +
  "Salem", "Napier", "Great Lakes Recycling", "10833 Five Mile", "N2688", "475946", "81000004". Keep the
  list in `config.yml`.
- **New request number -> alert + Sheet row** in a new `GovQA Archive Watch` tab: request no., filed/closed
  dates, status, request text excerpt, requester org when shown, release file list.
- Dedupe key = request number.
- **Download released attachments** to a local/private staging area (NOT a public Drive folder). Record
  the SHA-256 per file, skip files already held (SHA-256 against the Archived-PDFs mirror and Hand-Curated
  folder listings), and add a row per file.
- **HARD RULE: never auto-publish GovQA attachments to any public surface** (public Drive folder, public
  feed, site). FOIA releases can contain residents' names and addresses, and EGLE items marked privileged.
  They route to Trisha's hand-curation (`dedupe-curate`) queue only. Add a test pinning that no GovQA
  path writes to a public folder id or to `gen_findings_feed`.
- Also alert when a watched **open** request changes status (e.g. "Cost estimate sent" -> "Closed") so
  law-firm releases are caught the day they post. Known example: E614606, Liddle Sheets, Pine Tree; it
  is a peer site, so it's config-driven and optional.

### 1C-bis. GovQA: the method that works without timing out (added 2026-09-28; READ BEFORE BUILDING 1C)

These are lessons from the 9/25-9/27/2026 manual runs. Prior-art code is in Lotext
`.../egle-foia-archive/_tools/` (`pw_search.py`, `rid.py`, `dl.py`, `getall.py`).

**Don't:**
- **Don't use the UI's CSV Export headless.** It returned nothing within 10 minutes. The grid is a DevExpress
  control tied to a live session.
- **Don't trust the date filter.** It is ignored; filtering happens only after the search form is POSTed to the
  session URL (`.../_rs/(S(<session>))/OpenRecordsSummary.aspx`).
- **Don't parallelize.** Run one browser or one cookie jar at a time, at about 1 request/second.

**Keyword search (the list of requests):** use headless Playwright on the pattern in `pw_search.py`.
- `goto` the summary URL, then wait for `#txtSearch_I` (timeout 180 s).
- `fill("#txtSearch_I", term)`, then click `#filterButton` inside `expect_navigation(timeout=300000)`.
- Read rows from `tr[class*=dxgvDataRow]`. The first cell is the E-number (`E\d{6}-\d{6}`).
- Page with `ASPx.GVPagerOnClick('gridView','PBN')`. Then poll up to 60 s (every 500 ms) until the first row's
  E-number changes. Read "Page X of Y (N items)" from body text to know when to stop.
- **One term per search.** Terms are OR-ed across separate searches; there are no boolean queries.
- **Incremental daily run:** results come newest-first, so stop paging a term as soon as a page contains only
  E-numbers already in the `GovQA Archive Watch` tab. Don't re-walk the whole history daily.
- On any timeout: close the context, start a fresh browser context/session, retry with backoff (e.g. 30 s, 2 min,
  10 min). After 3 failures, log a structural error and move on. Never hang the run.

**One request by E-number (status checks + attachments):** use curl and a cookie jar, no browser (`rid.py` +
`dl.py`).
1. GET the summary with `-L`, capturing the effective URL (it contains the session id).
2. Scrape all `<input>` name/value pairs from the page, drop `main-nav`/`viewport`, set `txtRefsearch=<E-number>`
   + `filterButton=FILTER`, and POST back to that URL with `-e <url>`.
3. Get the internal `rid` from `redirectInfo(...)` or `OnMoreInfoClick(this, ...)` in the response.
4. The detail page is `RequestArchiveDetails.aspx?rid=<rid>&view=1`. Status, dates and the attachment list come
   from `__doPostBack('rptAttachments$ctlNN$lnkStreamCloud','')` links.
5. **Download each attachment** by re-POSTing the detail page's form with `__EVENTTARGET=<that target>`, following
   the **302 to a time-limited Azure blob URL with `-L`**.
   - If the response content-type is `text/html`, it failed (session expired): rename the file `*.ERR.html`,
     restart the session, retry once.
   - Skip files already present and non-empty. SHA-256 each.

Use this path, not the grid, to **re-check open requests** (status flips like "Cost estimate sent" → "Closed").

**CSV fallback for bulk backfills:**
- The site's **Export button works in a real, human-driven browser**. Trisha exported
  `govqa_export_gridView_2026-09-25_{a,b}.csv` that way (313 requests).
- The watcher should accept **a gridView CSV dropped into a configured folder** as an alternative input (same
  columns as those files), and ingest new E-numbers from it. That is the fallback when scraping the grid is too
  slow for a large backfill. **Do not** try to automate the Export button headless.
- If a large backfill is ever needed, the run should **stop and ask Trisha for an export** rather than loop on
  timeouts.

**Volume sanity:** a full "Arbor Hills" history is small (dozens of requests). A daily incremental run should take
a few minutes, not tens.

### 1D. If 1A finds an anonymous RRD document channel

Add an RRD document watch mirroring the nSITE documents pattern: a Sheet tab `RRD Documents` and a private
Drive mirror, keyed by source document id, `enabled: false`. Same no-auto-publish rule until Trisha
reviews what comes through.

---

## Phase 2 -- MPART PFAS open-data layers (keyless ArcGIS; snapshot-diff; `enabled: false`)

The same shape as Stream J: explicit `outFields`, `returnGeometry=false` where possible, and one query
per layer.

| Layer | Endpoint | Filter |
|---|---|---|
| PFAS surface-water sampling | `gisagoegle.state.mi.us/arcgis/rest/services/EGLE/PfasOpenData/MapServer/0` | bbox -83.66,42.34,-83.40,42.46 (Johnson Drain / Johnson Creek / upper Rouge); key = GlobalID or SiteCode+CollectionDate |
| Fish contaminant monitoring, sites + results | `.../EGLE/FcmpOpenData/FeatureServer/0` and `/1` | StationID IN (1484, 1507) + the bbox; key = SampleID |
| Public water-supply PFAS results | `.../EGLE/PublicWaterSupplySamplingOpenData/FeatureServer/1` | WSSN IN (2001381 Salem Elementary, 2046881 and 2037081 landfill supplies); key = SysSampleCode or WSSN+SampleDate |
| MPART PFAS sites/AOIs | `services1.arcgis.com/FNjlrOFR0aGJ71Tg/.../Michigan_PFAS_Sites_and_Areas_of_Interest_PUBLIC_view/FeatureServer/1` | Name LIKE '%Arbor Hills%' + bbox; diff site lead, status, ResidentialWellsSampled |

**Alert rules:**
- Any **new sample row** -> alert, risk R5.
- An exceedance flag -> higher severity: PFOS above the Rule 57 non-drink value **12 ng/L** in surface
  water, or any regulated PFAS detected in the PWS layer. Values: see Lotext
  `documents/arbor-hills/source-docs/egle-rule-57-water-quality-values-2026-09-26/README.md`: PFOS 12,
  PFOA 170, PFHxS 210, PFNA 30 ng/L non-drink.
- Surface-water values: K = non-detect (value is the MDL), J = estimated. Strip stray spaces from flags
  before comparing.
- Fish PFOS is in ppb (fillet); **code "I" means no value published** (station 1484 trout). Do not treat
  "I" as a detection.

Snapshot tab: `MPART Data Watch`. The first sighting baselines silently.

---

## Acceptance

- The Phase 1A findings table is in the PR (channel -> anonymous? -> documents? -> verdict).
- 1B: live layer-1 query returns 00038889; silent baseline confirmed on a real run.
- 1C and Phase 2: unit tests with recorded fixtures; one real read-only run each, `enabled: false`; the
  no-public-publish test passes; config keys documented; ADRs added (one per new stream).
- `name_check` / publish gate untouched. No credentials added. No login attempted.

## Out of scope

- Logging into RIDE or any authenticated EGLE system.
- Filing FOIA requests.
- Publishing any GovQA or RRD document publicly. That's Trisha's hand-curation decision.
- Peer-landfill expansion beyond a config list.
