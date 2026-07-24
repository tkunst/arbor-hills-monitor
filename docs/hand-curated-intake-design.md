# Hand-curated public-record intake — design

**Status:** design locked 2026-07-18. Two of the three pieces below are built
(the Drive folder and Sheet tab, created manually 2026-07-23); the intake
*script* itself is not yet written. See
`docs/sessions/session-2026-07-23-hand-curated-intake-manual-precedent.md` for exactly
what exists today and what a manual publish looked like without the script.

## The gap

The monitor's auto-pipeline (the nSITE archiver, WDS, MMPC Mirror D, Ridge
Wood) does two things for every document it *fetches*: mirrors the file into
a public Drive folder **and** writes a row to the matching Sheet tab. A
document that's found and curated by hand — rather than fetched by one of the
pollers — has no path to either surface. This is that path.

**Done looks like:** a record + a one-line manifest entry go into a local
`to-publish/` folder, one command previews exactly what will publish, a
confirm flag makes it real, and the file lands in a dedicated public Drive
subfolder **with** a descriptive row in a dedicated Sheet tab — while nothing
that *shouldn't* be public can leak onto a publicly-shared surface.

## Design decisions (locked, with rationale)

### Surface: a new dedicated folder + a new dedicated tab, not the existing streams

- **Drive:** `Hand-Curated Public Records` — a sibling subfolder of the
  monitor's other public Drive subfolders (the EGLE document mirror, the
  MMPC minutes archive, etc.), same parent. **Built 2026-07-23.**
- **Sheet:** `Hand-Curated Files` tab, columns:
  `curated_filename | title | source | doc_date | facility | doc_type | risks | origin_url | note | drive_link | added_at`.
  **Built 2026-07-23.**

**Why a separate surface, not folded into the existing archive tabs/folder:**

1. **Provenance.** A human vouched for these records specifically; keeping
   them visibly distinct from the auto-archived streams is the point.
2. **Dedup safety.** The auto tabs key on a source-system id (nSITE `doc_id`,
   CivicClerk `file_id`) that a hand-added record doesn't have. A hand-added
   row with no such key risks confusing the auto-dedup logic or being
   silently overwritten by a later pipeline run.
3. **No double-listing.** Some hand-curated records' raw files are already
   mirrored elsewhere under a generic auto-classification. A separate tab
   lets the curated, descriptive entry sit *alongside* the auto entry instead
   of duplicating or overwriting it (see the ANNOTATION mode below).

### Mechanism: OAuth-primary local script (cp-to-mount is the documented fallback)

A **repo-versioned, locally-run** script, `scripts/publish_hand_curated.py`
— invoked by hand, like the existing local-only scripts (`scripts/
oauth_setup.py`, `scripts/seed_wds_state.py`). Not a GitHub Actions workflow:
its source files live in a local directory CI can't see.

- **Drive upload → OAuth-as-user** (`archive_client.oauth_drive_service()` +
  `ac.upload_pdf(drive, local, name, folder)`), into
  `GOAUTH_HANDCURATED_FOLDER_ID`. Same idiom `mmpc_archiver.py` already uses
  — zero new upload code.
- **Sheet row → service account** (`drive_client.sheets_service()` + a new
  `sheet_writer` helper trio, same shape as the MMPC archive helpers).

**Why OAuth over cp-to-mount:** every archive tab carries a `link` column,
and `ac.upload_pdf` returns the Drive file id **synchronously**, so the row's
link can be written inline right after upload (crash-safe: upload first,
write the row second). cp-to-mount gives a local path but no file id — Drive
Desktop assigns one asynchronously after sync — so building the link that
way means cp → wait for sync → list the folder by name via the API → extract
the id: racy, and it still needs the API. `drive_client.upload_file` is also
idempotent-by-name, so a re-run updates in place rather than duplicating.

**Documented fallback:** if the OAuth refresh token is ever dead (or, as of
2026-07-23, simply never provisioned locally — see below), `cp` the file
directly into the Drive-mounted subfolder, then resolve its file id with one
folder-list call once it syncs. The script must fail **loudly** (exit 1) on
a dead token — never a silent skip — mirroring `mmpc_archiver.run()`'s own
OAuth-failure branch.

### Gate (the load-bearing safety, not the upload path)

Anything uploaded into this folder inherits its sharing (publicly readable),
and a publish is effectively irreversible in practice — content may be
cached or indexed elsewhere before it can be removed. So the gate matters
more than the mechanism:

1. **Folder-as-gate.** The script reads *only* the configured source
   directory. Nothing else on disk is ever considered.
2. **Manifest gate = explicit human attestation.** A file publishes only if
   it has an entry in `to-publish/manifest.yml` with `public: true` — an
   exact boolean `true`; reject the strings `"true"`/`"yes"`/`1` to avoid a
   fuzzy-truthy leak. No entry → skipped, logged. The manifest also carries
   the curation (title, source, date, facility, doc_type…), which doubles
   as the Sheet-row source.
3. **Content-type refuse.** Refuse `.md`/`.markdown`/`.txt` and any filename
   matching `DRAFT-*`. Allow only document/scan types:
   `.pdf .png .jpg .jpeg .tif .tiff`.
4. **Dry-run by default.** A bare invocation prints each file's disposition
   (would-publish / skipped / refused / already-published) plus the exact
   Sheet row and destination it would write — uploads and writes nothing.
   Publishing for real requires an explicit `--commit` flag.
5. **Copy-only, never delete.** The intake never removes anything from
   Drive. Un-publishing stays a deliberate manual act.

## Manifest format (`to-publish/manifest.yml`)

One entry per record. Required fields: `file`, `public`, `title`. Everything
else is optional (blank Sheet cell if omitted) — the point of the automation
is to remove friction, not add a new kind.

```yaml
# UPLOAD mode (no already_public_url) — the record is absent from every
# public surface: upload the file + write a row linking to the new copy.
- file: "example-record.pdf"
  public: true                     # required — must be exact boolean true
  title: "A descriptive curated title"
  source: "issuing/holding body"   # optional
  doc_date: "2025-01-01"           # optional
  facility: "N2688"                # optional — N2688 | RA | N1504 | P1488
  doc_type: "procedural"           # optional — evidence | procedural | opinion
  risks: []                        # optional — R1..R8
  origin_url: "https://…"          # optional — the public source page
  note: "why this record matters / where it came from"

# ANNOTATION mode (already_public_url set) — the raw file is ALREADY public
# somewhere; only the curated description is missing. Skips the upload
# entirely; the physical file need not exist in to-publish/ for this mode.
- file: "already-mirrored-record.pdf"   # required — dedup key + label
  public: true
  title: "A more specific/curated title than the auto-classification gave it"
  already_public_url: "https://…"       # triggers annotation mode
  note: "Raw copy already public elsewhere under a generic auto-classification; this row adds the curated identification."
```

`already_public_url` is the mode switch: absent → UPLOAD (upload the file,
link to the new Drive copy); present → ANNOTATION (skip the upload, link
straight to the existing public copy) — this avoids creating a duplicate
copy of a record whose raw file already sits on the public surface under a
generic auto-classification.

## New `Hand-Curated Files` tab schema

`curated_filename | title | source | doc_date | facility | doc_type | risks | origin_url | note | drive_link | added_at`

- `curated_filename` = the manifest `file:` value → the dedup key.
- `drive_link` = the link `ac.upload_pdf` returns (UPLOAD mode) or
  `already_public_url` verbatim (ANNOTATION mode).
- `added_at` = an ISO timestamp, same convention the other archivers use.

## Code to add (mirrors existing patterns — minimal new surface)

- `sheet_writer.py` (additive, disjoint from every live tab):
  - `TAB_HANDCURATED = "Hand-Curated Files"` + a headers entry (model on the
    MMPC archive tab's).
  - `ensure_handcurated_tab(sheets, sheet_id)` — mirrors `ensure_mmpc_tabs`.
  - `append_handcurated_row(...)` — mirrors `append_mmpc_archive_row`.
  - `handcurated_filenames(sheets, sheet_id) -> set[str]` — reads the
    `curated_filename` column for dedup, mirrors `mmpc_archived_file_ids`.
- `scripts/publish_hand_curated.py` (new, local-run only), flow mirroring
  `mmpc_archiver.run()`:
  1. Load env; resolve the source directory, `GOAUTH_HANDCURATED_FOLDER_ID`,
     `GSHEET_ID`.
  2. Parse the manifest; reject any entry whose `public` isn't exactly
     boolean `true`, or that's missing a required field.
  3. Classify each entry into UPLOAD or ANNOTATION mode + a disposition (see
     the gate above); a physical file with no manifest entry is reported as
     skipped, not silently ignored.
  4. Read existing curated filenames; mark already-published entries skipped
     unless `--force` (which refreshes the row, and in UPLOAD mode
     re-uploads in place).
  5. Dry-run by default: print every entry's mode, disposition, and the
     exact row it would write. Upload/write nothing.
  6. `--commit`: ensure the tab exists; open the OAuth Drive client (loud
     exit 1 on auth failure — but only if at least one UPLOAD entry exists;
     a pure-annotation run needs no Drive upload); per entry, upload-then-row
     (UPLOAD) or row-only (ANNOTATION); one entry's exception logs and
     continues rather than aborting the batch.
  - Keep the gate and mode-classification logic pure/testable without
    network access, so unit tests cover it hermetically.

## Dedup / idempotency

- **Key:** `curated_filename`.
- **Drive side:** upload is idempotent-by-name — a re-run updates in place,
  never duplicates.
- **Sheet side:** already-rowed filenames are skipped; `--force` refreshes
  both sides.

## Adversarial review

**Severe residual risk (mitigated, not eliminated): an accidental publish of
a document that shouldn't be public.** This Drive subfolder is publicly
shared, so anything placed there is effectively public and may be cached or
indexed before it can be removed. Mitigated by: the dry-run default showing
the exact disposition and destination before anything is written; the
manifest's explicit `public: true` attestation, per file; the content-type
refuse blocking the file types most likely to carry non-public analysis
(markdown, `DRAFT-*`); and the tab itself doubling as an audit log of
everything ever published. Recovery is manual — remove the file from Drive
**and** delete the Sheet row — and anything already cached or indexed before
removal is not recoverable.

**Manageable risks:**
- *Dead or unconfigured OAuth token* → loud exit 1, never a silent skip; the
  cp-to-mount fallback is documented above.
- *Manifest typo / fuzzy-truthy `public`* → reject non-boolean values; the
  dry-run surfaces incomplete entries before commit.
- *Duplicate copies on the public surface* → eliminated by ANNOTATION mode
  for records whose raw file is already mirrored elsewhere; UPLOAD mode only
  runs for records genuinely absent from every public surface.
- *An `already_public_url` pointing somewhere not actually public* → low
  stakes (a URL in a cell, not a file upload), but the dry-run prints the
  exact link before commit so it can be eyeballed.
- *One bad file aborting the whole batch* → per-entry exception handling;
  the batch continues.

## v1 scope

Both modes (UPLOAD and ANNOTATION) ship together — the annotation branch is
small (a mode check plus "skip the upload, use the given URL as the link"),
not a separate code path. Deferred to a later version: auto-detecting an
existing mirror copy's link so `already_public_url` doesn't need to be
pasted by hand; batch-editing existing rows.

## Setup prerequisites

1. ~~Create the `Hand-Curated Public Records` Drive subfolder~~ — **done
   2026-07-23.**
2. ~~Create the `Hand-Curated Files` Sheet tab~~ — **done 2026-07-23** (via
   the service account's write scope, ahead of the script existing).
3. Add to the **local** `.env` (not committed, not a GitHub secret — this
   script only ever runs locally):
   ```
   GOAUTH_HANDCURATED_FOLDER_ID=<the folder id from step 1>
   HAND_CURATED_SOURCE_DIR=<a local directory you designate>
   ```
   **As of 2026-07-23 the local `.env`'s `GOAUTH_CLIENT_ID` /
   `GOAUTH_CLIENT_SECRET` / `GOAUTH_REFRESH_TOKEN` are still
   `.env.example` placeholders, not live credentials** — the OAuth-upload
   path in this design can't run yet without those being filled in for
   real (see `scripts/oauth_setup.py`).
4. Create the local source directory + an empty `manifest.yml`.

## Acceptance criteria (for whoever builds the script)

1. Dry-run over a sample manifest prints correct mode + dispositions +
   exact rows; uploads/writes nothing.
2. A `.md` file and a `DRAFT-*.pdf` in the source directory are refused in
   dry-run (UPLOAD mode only).
3. A file with no manifest entry is skipped, not silently ignored.
4. `--commit` in UPLOAD mode uploads the file and appends a row with a
   working `drive_link` to the new copy.
5. `--commit` in ANNOTATION mode (an entry with `already_public_url` set)
   uploads nothing and appends a row whose `drive_link` is the given URL —
   and a pure-annotation run doesn't require a live OAuth token.
6. Re-running `--commit` is idempotent (Drive updates in place for UPLOAD;
   the Sheet skips already-present entries in both modes); `--force`
   refreshes.
7. Unit tests cover the gate and mode-classification logic hermetically (no
   network), mirroring the existing archivers' own tests for the same kind
   of logic.
8. Code comments carry no private-workbench-path or strategy detail — this
   design doc is the place for that kind of narrative, not the script.

## Build kickoff

From a branch off green `main`. Check this repo's own
`.claude/COORDINATION.md` first. Ships with no `enabled` flag — it's a
manual script, not a scheduled stream — and touches no live automated path.
Run the first `--commit` attended (not unattended/overnight), since it's the
first real write to a brand-new public surface.
