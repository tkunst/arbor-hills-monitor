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
