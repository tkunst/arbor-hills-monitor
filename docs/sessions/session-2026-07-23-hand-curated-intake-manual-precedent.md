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
