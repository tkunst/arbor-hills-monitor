# ADR 037 — CivicClerk DPW + BOC Drive archive (a Mirror D sibling)

*Status: built — 2026-09-04. Ships `civicclerk_archive.enabled: false` — a
new external-write capability, activated once the destination Drive folders
and secrets exist (see Activation).*

## Context

Mirror D (`mmpc_archiver.py`, ADR 010) has been mirroring every MMPC
(categoryId 72) Agenda/Minutes/Other PDF into Trisha's Drive since
2026-07-11 — replacing a manual "email alert → Trisha downloads and
uploads by hand" flow. ADR 036 extended the separate alert-only watcher
(`civicclerk_watcher.py`) to DPW (categoryId 68) and keyword-scanned its
text, which is how Trisha found a real signal: DPW's August 2026 minutes
recording GFL's overdue flare test / anticipated EGLE violation notice, in
Theo Eggermont's (DPW Director) standing report. She asked for the SAME
durable-archive treatment MMPC already gets — DPW and Board of Commissioners
(BOC, categories 26 Meeting + 27 Working Session) PDFs mirrored to Drive,
not just alerted on — into 2 new subfolders under a parent folder she
provided.

## Decision — a new sibling module, not an extension of Mirror D

`mmpc_archiver.py` is hardcoded to one category and one destination folder
(`GOAUTH_MMPC_FOLDER_ID`) and has been running live, nightly, unattended for
almost two months. Rather than generalize it to accept multiple categories/
folders — which would mean touching a proven, live code path for a change
that doesn't need to touch it — this ships as a new module,
`civicclerk_archiver.py`, that reuses the exact same building blocks
(`mmpc_client.fetch_mmpc_files`/`iter_new_files`/`download_file`,
`archive_client.upload_pdf`, the dedup-via-Sheet-tab idiom) but drives them
from a **configurable list of mirrors** instead of one hardcoded
category/folder pair:

```yaml
civicclerk_archive:
  enabled: false
  mirrors:
    - {category_id: 68, folder_env: "GOAUTH_DPW_FOLDER_ID", group: "Board of Public Works"}
    - {category_id: 26, folder_env: "GOAUTH_BOC_FOLDER_ID", group: "Board of Commissioners"}
    - {category_id: 27, folder_env: "GOAUTH_BOC_FOLDER_ID", group: "Board of Commissioners"}
```

`mmpc_archiver.py`/`mmpc_archive:`/`MMPC Archived Files` are completely
untouched — zero regression risk to Mirror D.

### Two folders, not three

Trisha asked for "Board of Public Works and Board of Commissioners" to
"probably have separate folders" — read as DPW getting its own folder and
BOC (as one body) getting its own, not a further split of BOC's own two
CivicClerk categories (26 Meeting, 27 Working Session). The config above
reflects that: both BOC categories share one `folder_env`
(`GOAUTH_BOC_FOLDER_ID`). This is a judgment call on ambiguous phrasing,
stated explicitly here rather than assumed silently — trivial to change
(add a fourth mirror entry with its own `folder_env`) if a 3-folder split is
actually wanted.

### Dedup stays global, across mirrors

CivicClerk file IDs are one global sequence, not scoped per category (the
same fact `mmpc_archived_file_ids()` already relies on). So this module uses
ONE new Sheet tab, `CivicClerk Archived Files`, and ONE "already archived"
set (`civicclerk_archived_file_ids`) shared across every mirror — a file
already archived under one mirror entry is never re-fetched under another.
The tab adds a `Group` column (MMPC's tab doesn't need one; this tab spans
multiple categories/folders, so provenance is real information here).

### Per-mirror fail-safes (the one real design difference from Mirror D)

Mirror D only ever has one category/folder to reason about, so a fetch
failure hard-aborts the whole run (`return 1`) and a missing folder secret
is a whole-run no-op. With three mirror entries across two folders, neither
behavior is right:

- **A mirror whose folder secret isn't set yet is SKIPPED, not fatal** — so
  DPW and BOC can be activated independently as each folder gets
  provisioned, rather than requiring both secrets to exist before either
  starts archiving.
- **A mirror's CivicClerk fetch failing does not abort OTHER mirrors** in
  the same run (isolated per-mirror, mirroring `run_historical_backfill`'s
  per-category try/except in `civicclerk_watcher.py`) — but the run still
  exits non-zero so a persistent failure surfaces rather than looking
  silently healthy.
- **One file's download/upload failure never aborts the batch** — unchanged
  from Mirror D's existing behavior.

### `create_oauth_folder.py` gets an optional parent

The existing one-off helper (used to provision the GFL air exhibit folder,
ADR 026) always created folders at Drive root, requiring a manual "move
under the parent" step afterward — because at the time, there was no given
parent ID to place it under directly. This time Trisha gave an exact parent
folder ID
(`1hqhI0XUD8LFeUUrAO9uHoRf8uol-J897`), so the script (and its matching
`create-oauth-folder` workflow input) now accepts an optional
`PARENT_FOLDER_ID`/`parent_folder_id`. Backward compatible: omitting it
keeps the original root-then-move behavior exactly as before.

**Why this works under `drive.file` scope even though Trisha's parent
folder wasn't created by this OAuth app:** there's direct precedent already
live in this repo — `archive_client.upload_file()` has been uploading MMPC
PDFs as children of `GOAUTH_MMPC_FOLDER_ID`, a folder Trisha hand-created
herself, since Mirror D shipped. Creating a new FOLDER (a file with
`mimeType: application/vnd.google-apps.folder`) as a child of an existing
folder ID uses the exact same `parents` mechanism as that upload call — if
one works under `drive.file`, so does the other.

## Adversarial review (per the plan-hardening rule)

| Risk | Class | Detection | Mitigation (shipped) |
|---|---|---|---|
| A change to `civicclerk_archiver.py` regresses Mirror D | show-stopper if it happened | `mmpc_archiver.py`'s own test suite | impossible by construction — `mmpc_archiver.py` is not touched by this ADR at all; verified the full pre-existing `test_mmpc_archiver.py`/`test_mmpc_client.py` suites stay green, unmodified |
| BOC's 2-folder-vs-3-folder read of Trisha's phrasing is wrong | manageable | she reviews the PR/ADR before activating | stated explicitly here as a judgment call, not silently assumed; one-line config change to split if wrong, before any real file is archived (ships `enabled: false`) |
| One mirror's folder secret being unset blocks the OTHER mirror too | manageable | archiving silently stops entirely when only one folder is half-provisioned | per-mirror skip, not a whole-run no-op — unit-tested (`test_mirror_with_no_folder_secret_is_skipped_not_fatal`) |
| A DPW/BOC fetch outage silently looks like "nothing new" forever | show-stopper if unmitigated | no distinct signal from a genuinely quiet week | fetch failure surfaces as a non-zero exit + a printed warning per mirror, isolated from other mirrors — unit-tested |
| `PARENT_FOLDER_ID` creation silently fails under `drive.file` scope (untested against the LIVE API, only unit-tested against a fake) | manageable, disclosed | the workflow run log would show a Drive API error on first real use | real Mirror D upload precedent makes this low-risk, but genuinely unverified against a real Drive call as of this PR — flagged for Trisha to confirm on the first real `create-oauth-folder` run |

## Activation

1. Create the 2 destination folders, directly under Trisha's given parent,
   via the `create-oauth-folder` workflow (`workflow_dispatch`, now with the
   new `parent_folder_id` input) or locally with `PARENT_FOLDER_ID` set —
   once for "Board of Public Works", once for "Board of Commissioners".
2. Store each printed folder ID as a GitHub secret: `GOAUTH_DPW_FOLDER_ID`,
   `GOAUTH_BOC_FOLDER_ID`. The shared `GOAUTH_CLIENT_ID`/`CLIENT_SECRET`/
   `REFRESH_TOKEN` already exist (same OAuth account Mirror D uses) — no new
   consent flow needed.
3. Set `civicclerk_archive.enabled: true` in `config.yml` and commit — or
   leave a mirror's folder secret unset to activate just one body first
   (per-mirror skip, not all-or-nothing).

## Consequences

- New: `civicclerk_archiver.py`, `.github/workflows/civicclerk-archive.yml`,
  `tests/test_civicclerk_archiver.py`, `tests/test_create_oauth_folder.py`
  (this script had no test coverage before), the `CivicClerk Archived Files`
  Sheet tab.
- Additive: `sheet_writer.py` (tab + 3 helpers), the `config.yml`
  `civicclerk_archive:` block, `scripts/create_oauth_folder.py` +
  `.github/workflows/create-oauth-folder.yml` (optional parent param, both
  backward compatible).
- Unchanged: `mmpc_archiver.py`, `mmpc_archive:`, `MMPC Archived Files`,
  every other stream.
