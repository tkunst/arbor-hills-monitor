# Session 2026-07-23 — hand-curated public intake: manual precedent (script still unbuilt)

**Status:** the `Hand-Curated Public Records` Drive folder and `Hand-Curated Files`
Sheet tab now exist and hold one real entry, published by hand. The intake
*script* (`scripts/publish_hand_curated.py`) described in the design spec is
still not written — see "What's still open" below.

## Background

A design spec for a hand-curated public-record intake was locked on 2026-07-18
(private write-up, not in this repo — the gap it solves: a document a human
manually curates into the local workbench reaches neither the monitor's public
Drive mirror nor a Sheet tab, since the auto-pipeline only handles what it
*fetches*). The spec calls for:

- A new Drive subfolder **`Hand-Curated Public Records`**, sibling of the
  other public subfolders (`MMPC-meeting-minutes`, the EGLE document mirror,
  etc.) under the same public case-file folder.
- A new Sheet tab **`Hand-Curated Files`**, columns:
  `curated_filename | title | source | doc_date | facility | doc_type | risks | origin_url | note | drive_link | added_at`.
- A local script, `scripts/publish_hand_curated.py`, driven by a
  `to-publish/manifest.yml` with two modes (UPLOAD: file + row; ANNOTATION:
  row only, linking an `already_public_url` already public elsewhere) and a
  layered safety gate (folder-as-source, explicit `public: true` attestation,
  content-type refuse, dry-run default, copy-only).

As of 2026-07-23 the script had never been built. One specimen needed
publishing today (an nSITE-sourced photo whose automated classification keeps
failing — see the note in the Sheet row), so rather than build the full
script for a single file, the folder + tab were created directly and the
one entry was added by hand, following the spec's *documented fallback*
mechanism (§2.2 in the spec): OAuth-as-user credentials for this script were
never filled in locally, so the upload went through the CloudStorage-mounted
copy of the same Drive folder (`cp`, then resolve the resulting file's Drive
ID once it synced) instead of the OAuth-upload path the real script will use.

## What exists now

- Drive folder `Hand-Curated Public Records` — created under the same public
  case-file parent folder as the other archiver-facing subfolders. Empty
  except for the one specimen below; publicly readable (inherited from the
  parent folder's sharing).
- Sheet tab `Hand-Curated Files` — created with the header row above, via the
  service-account credentials already used for every other tab (needs the
  `spreadsheets` write scope, not `spreadsheets.readonly`).
- One row: a 2023-10-05 site photo from the Remediation Area facility, hand-
  pulled because the automated pipeline's OCR step fails on it in CI
  (deterministically, not transiently — confirmed the same input processes
  cleanly against a current local OCR toolchain, so this is an environment/
  version gap in CI, not a corrupt source file). The row's `note` field
  records this and flags that the photo's actual content hasn't been visually
  reviewed yet.

## What's still open

The script itself — `scripts/publish_hand_curated.py` — is not built. The
manual steps taken today (create folder, create tab, `cp` + resolve file ID,
append row by hand) are a one-off precedent, not a repeatable process; doing
this again for the next hand-curated document means either repeating these
manual steps or building the real script. The design spec has everything
needed to build it (manifest format, gate logic, acceptance criteria) —
building it is a separate, scoped session, with the first `--commit` run
attended rather than autonomous (per the spec's own kickoff note).

Before that build can use the OAuth-upload path the spec designs for (rather
than today's `cp`-to-mount fallback), the local `.env`'s `GOAUTH_CLIENT_ID` /
`GOAUTH_CLIENT_SECRET` / `GOAUTH_REFRESH_TOKEN` / `GOAUTH_HANDCURATED_FOLDER_ID`
need real values — as of this session they were still the `.env.example`
placeholders, not live credentials.

## 2026-07-24 addendum — three more entries, same manual precedent, folder ID recorded

Trisha asked (from the Lotext session, not this repo) to publish three
enforcement-instrument PDFs cited in `arbor-hills-violations-enforcement-summary.md`'s
"four enforcement threads" framing, following this spec. Same manual fallback
as 2026-07-23 (`.env`'s `GOAUTH_*` vars are still placeholders): `cp` into the
CloudStorage-mounted folder, resolve each Drive file ID via the API once
synced (`Hand-Curated Public Records` folder id
`1Zk-tq08E0iUWBLSVg9U7Axw8Ox1Lt7pR`, confirmed inherits public-reader sharing),
append rows via `sheet_writer.append_rows()` against the existing tab (service
account, same as every other archiver tab — no code changes needed, the
generic `append_rows` helper already covers this).

Three new rows (see the Sheet for full `note` text):

1. **Federal Consent Decree** (`Case 4:21-cv-12098-SDD-EAS`, *US & EGLE v.
   Arbor Hills Energy LLC*, N1504) — not an EGLE nSITE document (federal
   court/DOJ filing), so never eligible for the nSITE mirror. **Went through
   two versions the same day.** First pull: DOJ's own public Consent Decree
   library (`justice.gov/enrd/consent-decree/file/1431506/download`) — this
   turned out to be the *as-lodged* copy (filed 9/9/21, ECF No. 2-1), whose
   own docket stamp reads "Case 5:21-cv-12098" throughout, different from the
   public doc's existing (correct) citation. That led to briefly "correcting"
   the public doc's citation to 5:21, which was wrong. Trisha then pointed the
   session back at the Lotext workbench (`documents/arbor-hills/`, not
   searched carefully enough the first time), which turned out to hold
   `source-docs/egle-documents/N2688_CD_20211215.pdf` — the actually-*entered*
   decree (filed/entered 12/15/21, ECF No. 9), docket-stamped "Case
   4:21-cv-12098-SDD-EAS" throughout, matching the public doc's original
   citation. Same substantive terms in both versions ($375,000+$375,000).
   Published the entered version, removed the lodged one from the public
   folder, reverted the case-number "fix" in the Lotext doc, and updated the
   Sheet row in place (same `curated_filename`, new Drive link/note).
   **Lesson:** check the local workbench thoroughly before reaching for an
   external source, even a primary one — DOJ's own library isn't wrong, it
   just isn't necessarily the *same* filing as what a downstream doc already
   cites.
2. **2023 MMD Consent Order No. 115-05-2023** (N2688) — already held in the
   Lotext workbench (harvested 2026-07-15 from EGLE's Air facility-info page,
   not nSITE); searched the nSITE mirror first per the check-before-pulling
   rule and it wasn't there, so this is a first public upload, not a
   re-mirror.
3. **YCUA Administrative Consent Order, Jan 5 2026** (Industrial User Permit
   AD 6-27, arsenic/PFOA/PFOS Pollutant Reduction Work Plan) — YCUA-issued,
   not EGLE, so also never nSITE-eligible. Located in Trisha's own case-file
   Drive workbench (`AHL-GFL_010526_SignedACO.pdf`) after both an nSITE-mirror
   search and a Gmail search came up empty. Pages 2-15 (the signed Order
   itself) are scanned images with no OCR text layer — not re-OCR'd this
   session, content verified only against the cover letter (case ID, permit
   number, effective-on-signature language).

**Mechanism note for whoever builds the real script:** all three files went
through `cp`-to-mount (same as 2026-07-23), then the Drive file ID was
resolved per file via a live API search rather than a local folder-list call
(the assisting session had API access but not a mounted-Drive `find`-by-name
helper handy) — same net effect, still a resolve-after-sync, not a
synchronous ID. The upload-first-row-second ordering was preserved.

## 2026-07-24 addendum #2 — three more entries (Wetland 1 JPA), `GDRIVE_SA_KEY` confirmed live, not a placeholder

Trisha-directed: publish the 3-document core set explaining the newly-filed
Wetland 1 JPA (Submission Ref `HQK-4R25-67T36`, filed 2026-07-23 — the PFOS/
AFFF remediation-and-fill story; see the memory `wetland1-pfas-jpa-2026` for
the substance). Same manual `cp`-to-mount mechanism as both prior entries.

**Correction to "OAuth-upload path can't run yet":** that's still true for
the OAuth-as-user path specifically (`GOAUTH_CLIENT_ID`/`SECRET`/
`REFRESH_TOKEN` are still `.env.example` placeholders, unverified this
session too), but **`GDRIVE_SA_KEY` is NOT a placeholder** — it's a *path*
variable whose example value (`./gdrive-sa-key.json`) doubles as the correct
real value once the key file exists at that path in the repo root, which it
does (gitignored, present locally). Tested live: `sheets_service()` opened
the actual case-file Sheet and read/wrote the `Hand-Curated Files` tab
directly. A future session shouldn't re-flag this as blocked without
checking whether the file itself exists first.

**Drive-side mechanism note:** resolved each file's ID via the
`mcp__claude_ai_Google_Drive` connector (`search_files`, authenticated as
the personal Google account that owns the public case-file Drive tree)
rather than the Drive v3 API — this session had
that MCP connector loaded already for an unrelated reason, and it can also
write directly (`create_file` with `parentId`), which would skip the
`cp`-to-mount step entirely for small files. Not used for upload here only
because the largest of the three files (2.7MB PDF) would have meant inlining
a ~3.6M-character base64 blob through the conversation — `cp`-to-mount has
no such limit since the bytes never pass through the model. Confirmed each
uploaded file inherited the folder's public `reader`/`anyone` permission
(spot-checked one via `get_file_permissions`) before writing the Sheet rows.

Three rows appended (`Hand-Curated Files`, now 9 data rows total):
`2026-07-20-arbor-hills-wetland1-jpa-additional-information.pdf`,
`2026-07-23-arbor-hills-wetland1-jpa-application-HQK-4R25-67T36.pdf`,
`2026-07-23-arbor-hills-wetland1-jpa-conceptual-mitigation-plan.pdf` — full
provenance in each row's `note` column, not repeated here.

## 2026-07-24 addendum #3 — three E613161 HOV-waiver FOIA docs, same manual precedent

Trisha-directed (from the Lotext session): hand-curate the three files returned by
**EGLE FOIA E613161-071626** — the two HOV (higher-operating-value) temperature-waiver
letters that were missing from the nSITE N2688 record, plus a WDS-link notation that rode
along. Source files were in the Lotext workbench under
`source-docs/foia-obtained-2026-07-15/` (foia-prefixed). Same manual `cp`-to-mount
mechanism as the prior addenda (`GOAUTH_*` OAuth-as-user vars still placeholders;
`GDRIVE_SA_KEY` live). All three were read and verified before upload, and all three are
public EGLE regulatory records (AQD decision letters + an MMD WDS reference), appropriate
for the public folder. All UPLOAD mode (genuinely absent from nSITE — the reason they were
FOIA'd), no ANNOTATION dedup needed.

Three rows appended (`Hand-Curated Files`, now 15 data rows total):

1. `2024-07-10-arbor-hills-HOV-temperature-waiver-four-replacement-wells.pdf`
   (Drive id `1mT6yur9GjkmwMf5AHlei0UBqTDyL07-C`) — AQD **approved** HOV temperatures for
   four newly-installed replacement WOI wells (AHW259R5/285R3/311R2/312R2); signed Diane
   Kavanaugh Vetort. R8/ETLF evidence.
2. `2025-02-19-arbor-hills-HOV-temperature-waiver-AHW272R4-180F.pdf`
   (Drive id `16qC55hdB8s0SYSm1VJtCl-cYYuqqP5Z_`) — AQD **approved 180 deg F** for AHW272R4,
   explicitly noting it lets AHL operate above the 145 deg F MACT Subpart AAAA limit.
   AHW272R4 is the hottest WOI / ETLF-signature well; key R8 evidence.
3. `arbor-hills-N2688-wds-link-notation-foia-E613161.pdf`
   (Drive id `1t8mPafiagJQ48Dz7B8X1Nu3Lj3MnZRxG`) — a 4-page MMD "Notation - WDS Link"
   reference; low-substance pointer record, published for FOIA-return completeness.

Mechanism: `cp` into the mounted `Hand-Curated Public Records` folder; resolved each Drive
ID via the `mcp__claude_ai_Google_Drive` connector (`search_files` by parentId + title);
spot-checked public `anyone:reader` sharing on one; appended rows via
`sheet_writer.append_rows()` (service account), upload-first-row-second. File sizes verified
against the originals (228728 / 113165 / 1469755 bytes). A dedup guard on `curated_filename`
kept the run idempotent (0 of 3 pre-existing).

**Correction (2026-07-24, same day):** doc 2 (the 2/19/2025 AHW272R4 180F grant) turned
out to be a **duplicate of a copy already public** in the `Arbor Hills Landfill EGLE
Documents` mirror (`N2688 Arbor Hills Request for Higher Operating Value Temp Waiver.pdf`,
id `1ymkY07bajEfuoZ_EKYTW5C-JUIerAkfv`). Trisha-directed reconcile: removed the
hand-curated upload (`rm` from the mount) and converted its `Hand-Curated Files` row to
**ANNOTATION mode** (the row's `drive_link` now points at the existing public copy). **Lesson
for the real script:** the dedup / check-before-pulling step must cover the manually-curated
public mirror (`Arbor Hills Landfill EGLE Documents`), not only "is it in nSITE" — this doc
was missing from nSITE yet already public in that folder. docs 1 and 3 are genuinely new and
remain UPLOAD.

## 2026-07-29 addendum #4 — Nov 2024 expansion Advisory Analysis exchange (2 rows)

Trisha-directed: hand-curate the two documents from the Nov 2024 GFL Advisory Analysis
pre-application exchange (from the Lotext workbench,
`source-docs/2024-11-advisory-analysis-proposed-expansion/`). Same manual cp-to-mount
mechanism as the prior addenda (`GOAUTH_*` OAuth vars still placeholder; `GDRIVE_SA_KEY` live).

1. **GFL-2024-11-06-request-for-advisory-analysis-with-conceptual-expansion-plan.pdf**
   (Drive id `1GC_4kad-eup1-h5Oq4pEwRCFeV4aqKqV`) — UPLOAD mode (not in nSITE mirror;
   sent via UPS, not electronic filing). GFL's Nov 6 2024 formal Advisory Analysis
   request to EGLE MMD (Dave Seegert to Gary Schwerin), with Tetra Tech conceptual
   expansion plan (Project 4241201, Oct 2024 survey) as Page 2. R1 evidence.
2. **EGLE-2024-11-19-advisory-analysis-response.pdf** — ANNOTATION mode (already in
   the automated nSITE mirror as Doc ID `3664986507789835729`, Drive link
   `1uSiwPI4c-lIC3bx3V3ljtISgtwkdXXgF`). EGLE MMD's Nov 19 2024 response confirming
   MMP consistency as the prerequisite before any construction permit application.

Both rows include `folded_into_public = public/arbor-hills-violations-enforcement-summary.md`
— content was added to that doc's R1 section (new bullet in "Permit, capacity & expansion
status") in the same session. Rows appended via `sheet_writer.append_rows()` (service
account) using a standalone script, same pattern as prior addenda.

## 2026-07-30 addendum #5 — Kovalchick well-head data, closes the Q1 2024 gap (5 rows)

Trisha-directed: hand-curate `documents/arbor-hills/source-docs/kovalchick-2026-07-30-well-heads/`
(Lotext workbench) — six files EGLE AQD Senior Environmental Engineer Mike Kovalchick emailed David
Drinan "per our recent phone call," forwarded to Trisha same day. Same manual `cp`-to-mount mechanism
as every prior addendum (`GOAUTH_*` OAuth-as-user vars still `.env.example` placeholders;
`GDRIVE_SA_KEY` live). Hit a new `dotenv.load_dotenv()` snag running from a `python3 <<'PYEOF'` heredoc
— its frame-based auto-discovery (`find_dotenv()`) throws `AssertionError` when the call stack has no
caller frame (stdin/heredoc execution); fixed by passing `dotenv_path` explicitly rather than relying
on auto-discovery. Worth a note for whoever eventually writes the real script if it ever runs via a
similar non-file entry point.

**Excluded, not curated:** `image001.jpg` (17K) — confirmed by content to be Kovalchick's email-signature
graphic (MiEnviro Portal logo), not a standalone record. `Gmail - Fwd_ Well Head Data.pdf` (the cover
email itself) — Trisha-directed exclusion; its provenance chain (Kovalchick → Drinan → Trisha, 2026-07-30)
is instead carried in each row's `source` field rather than publishing the email itself. Read in full
before excluding either — the email body is clean, official EGLE correspondence with no other content,
excluded on relevance grounds (a cover note, not a record), not a content-risk finding.

**Content review before publish (per this spec's own severe-residual-risk gate):** all 3 `.xlsx` files
opened and inspected sheet-by-sheet — no hidden sheets, no cell comments, standard tabular
well-ID/gas-reading/coordinate data throughout, no personal information. The big PDF's first several
pages read directly (standard GFL-to-EGLE regulatory cover-letter format, matching every other WOI
status report already public in this project). Cross-checked the local nSITE mirror
(`source-docs/egle-documents/Documents.csv`) before publishing: the Q1 2024 WOI report is *listed*
there under two separate `doc_id`s (both "Air Site Documents," dated 2/5/2026) but neither ever
produced a locally-mirrored file — confirms this is the same "corrupt, unrecoverable" report this
project's own docs (`source-docs/hov-etlf/README.md`, the temperature-trend v2 draft) have flagged
repeatedly as a data gap, now genuinely closed by a fresh copy obtained directly from EGLE rather than
through nSITE's rendering pipeline.

Five rows appended (`Hand-Curated Files`, now 26 data rows total), all UPLOAD mode (none had an
existing public copy):

1. `2024-04-15-arbor-hills-woi-status-report-q1-2024.pdf` (Drive id `1tMWQs52b__5gOWzTDmFyztgqVLvTDZbS`)
   — the Q1 2024 WOI Quarterly Status Report itself. **The headline item** — directly closes the gap
   referenced in `draft/DRAFT-arbor-hills-temperature-trend-2021-2025-full-dataset-2026-07-30.md`
   (the doc that's failed three refutation-gate passes) and `source-docs/hov-etlf/README.md`.
2. `arbor-hills-gas-extraction-exceedance-detailed-2024-04.xlsx` (id `1GBxDW385mF08QFTT1oBRtXiZCKT3KO28`)
   — richer companion to the already-public `WOI-gas-extraction-exceedance-2024-04.pdf`: adds monthly
   CO and H2/N2 attachment sheets plus a 12-month trend sheet the existing PDF doesn't have.
3. `arbor-hills-gas-extraction-exceedance-general-2026-04.xlsx` (id `1GcE-iz7S6UyCq11Hqx79IPW7uMToSjQx`)
   — new reporting period, no prior public copy.
4. `arbor-hills-gas-extraction-exceedance-general-2026-05.xlsx` (id `18pyXOpY9ieJ8wIE_RbmZlfo3rIC-B1RP`)
   — most recent reporting period on file, no prior public copy.
5. `arbor-hills-well-master-list-with-coordinates-2026-07-30.xlsx` (id `1L83Cufub4IUZc1LHeUulEL55O39xo6M2`)
   — well ID/type/lat-long master list (520 rows), sent by Kovalchick specifically for KML/map-building.

Spot-checked sharing on the largest upload (`anyone:reader` confirmed, inherited from the folder) before
writing rows. File sizes verified identical to the Lotext-workbench originals via the Drive search
response (`fileSize` field matched all 5). Mechanism: `cp` to the CloudStorage-mounted folder, resolved
each Drive file id via `mcp__claude_ai_Google_Drive` `search_files` (`parentId` + `title`), appended rows
via `sheet_writer.append_rows()` (service account) from a standalone script. `sheet_writer.py` does not
actually have a `handcurated_filenames()` dedup helper yet (that's still design-doc-only, per "Code to
add" — confirmed by `hasattr` check); dedup for this batch was done by eye against a full read of the
existing 24-row tab, which is how every addendum before the real script existed has done it.

**Flagging for whoever picks this up next:** the Q1 2024 report closing that data gap is directly
relevant to the temperature-trend v2 draft, currently blocked pending a clean refutation-gate pass --
that connection was raised in the same Lotext session this addendum's data came from, not yet acted on.

## 2026-08-05 addendum #6 -- blog-sweep-obtained-2026-08-05 (6 rows, rows 40-45)

Trisha-directed (interactive session): hand-curate the six documents obtained by overnight-worker Item
117 (blog-sweep acquisition, 2026-08-05). Same manual cp-to-mount mechanism as prior addenda
(`GOAUTH_*` OAuth-as-user vars still `.env.example` placeholders; `GDRIVE_SA_KEY` live).

**Mirror dedup check performed:** grepped the N2688-Documents.csv, N2688-Documents 2.csv, and
N2688-Documents 3.csv files against all document dates and names. Results:
- `N2688_VN_20231025.pdf` -- IN Mirror as N2688_Violation Notice_10-25-2023 (doc_id -1516052274233321969); Drive `1Qy3Ko-kdgUJEMEwf0sS7PnEqkJBKzcZu` -- ANNOTATION mode
- `N2688_RVN_20231025.pdf` -- IN Mirror as N2688_Violation Notice Response_10-25-2023 (doc_id -1905948147172766549); Drive `1EWyclas-wavlPOaz3haCTkWZj5t_1ias` -- ANNOTATION mode
- `N2688_VN_20211110.pdf` -- IN Mirror as N2688_Violation Notice_11-10-2021 (doc_id -4806871774930027183); Drive `1GxZFI2sFGOtbywOGsdOxzoaGCHOHwyJQ` -- ANNOTATION mode
- `N2688_RVN_20211110.pdf` -- NOT in Mirror (no Feb 2022 or 02-12-2022 entry in any N2688 CSV); public EGLE server URL exists -- UPLOAD mode
- `gfl-incident-report-leachate-spill-aug2022.pdf` -- NOT in Mirror (WRD document; Mirror covers AQD only) -- UPLOAD mode
- `egle-inspection-report-leachate-spill-aug2022.pdf` -- NOT in Mirror (WRD document; Mirror covers AQD only) -- UPLOAD mode

**Files uploaded (UPLOAD mode):**
- `N2688_RVN_20211110.pdf` (244744 bytes) -- Drive id `1Pbg2WhFS5EBBlnjqG_g-rMgk931TmxPA`
- `gfl-incident-report-leachate-spill-aug2022.pdf` (5637794 bytes) -- Drive id `1UdjGjbAXedZSGYvUUa5hMBDQYMRBsTp8`
- `egle-inspection-report-leachate-spill-aug2022.pdf` (18715607 bytes) -- Drive id `1VOLNS5kMji5DIJfO-F7KHsN_TYv8kGBS`

**Mechanism:** cp to the CloudStorage-mounted Hand-Curated Public Records folder; Drive file IDs
resolved via `mcp__b833bde3-8936-4a44-934e-01df1aade79e__search_files` by parentId
(`1Zk-tq08E0iUWBLSVg9U7Axw8Ox1Lt7pR`) + title; file sizes verified against originals.
For ANNOTATION-mode rows, Drive IDs resolved via title search in the Case File Mirror parent
folder (`1QY44JGfOiHmhmD7qnz5OzGMvLX4HbAFI`). Six rows appended via `sheet_writer.append_rows()`
(service account, `.venv/bin/python3`, `GDRIVE_SA_KEY` set via `os.environ` -- dotenv not available
in system Python 3.14.5, used `.venv` Python instead). Sheet now has 45 data rows total.
