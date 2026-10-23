# ADR 071 — One generic snapshot → immutable Drive exporter (ROP, MMD, nSITE Submissions, PFAS)

Date: 2026-10-23
Status: accepted (ships disabled)
Builds on: ADR 007 (OAuth durable Drive archive), ADR 012 (PFAS page watch),
ADR 017 (ROP), ADR 018 (MMD), ADR 020 (nSITE Submissions), ADR 026 (the
ships-disabled Drive-exhibit precedent), `docs/data-lifecycle-architecture.md`
(the durability model). Trisha's ruling: 2026-07-26 ("PR2" of the
data-lifecycle gap plan); handoff
`docs/overnight-coder-handoffs/drive-snapshot-exporter.md`.

## Context

The durability model ranks stores Tier 1 (immutable Drive file) > Tier 2
(append-only Sheet tab) > Tier 3 (mutable tab). Four watchers —
`rop_watcher.py`, `mmd_watcher.py`, `nsite_submissions_watcher.py`,
`pfas_watcher.py` — write their FULL snapshot payload (a sorted-keys JSON
document, or PFAS's normalized page text) to an append-only Sheet tab on every
observed change. That is Tier 2: it survives an ordinary pipeline run, but the
Sheet is still one API call or one shared-Sheet mishap away from loss, and a
Sheet cell is not a self-contained exhibit. None of the four had a Drive copy
(re-confirmed at build time: no `archive_client` import in any of them).

Trisha's ruling was ONE generic exporter wired into all four, not four
near-duplicate one-offs, and explicitly NO PDF rendering: the raw payload into
an immutable Drive file is the deliverable; a courtroom-renderable PDF is a
later step taken only if an exhibit is ever actually needed.

## Decision

### 1. One shared helper: `snapshot_exporter.py`

`export_snapshot(payload, name, cfg, folder_env=FOLDER_ENV) -> str | None`
writes the payload (a `str` verbatim; anything else as sorted-keys JSON) to a
temp file and uploads it through the existing OAuth-as-user Drive client
(`archive_client.oauth_drive_service()` / `upload_file()` / `folder_id()`),
returning the `webViewLink` or `None`. It **never raises**: flag off, Drive not
configured, dead token, network, quota, an unserializable payload — every
failure returns `None` and logs the exception CLASS only. An archive upload is
a nicety, never a blocker, the same convention as `archiver.mirror_one_now()`
and `wds_archiver`'s per-page isolation. The OAuth Drive service is built once
per process (the refresh-token exchange is a network round-trip) and dropped
on any failure so the next call rebuilds it.

`snapshot_name(stream, key, date, digest, ext)` builds
`<stream>_<key>_<date>_<hash>.<ext>` with the key reduced to a safe filename
fragment. **The hash is load-bearing:** `archive_client.upload_file()` is
idempotent BY NAME (it reuses an existing same-named file rather than
uploading), so a name without the content hash would silently keep only the
first of two distinct changes observed on the same day. With it, a re-run of an
already-exported snapshot is a cheap reuse and two different snapshots are two
different files.

### 2. Cross-cutting flag, default OFF

`data_lifecycle.snapshot_export.enabled: false` in `config.yml` — its own
top-level block, not inside any one watcher's, because it is one switch for all
four streams. `is_enabled()` is the FIRST check in `export_snapshot()`, before
any env read or network call, so with the flag off the four watchers behave
exactly as before. The four live `enabled: true` flags (`rop`, `mmd`,
`nsite_submissions`, `pfas`) are untouched: only the NEW export step is gated.

### 3. One shared folder: `GOAUTH_SNAPSHOT_FOLDER_ID`

The existing per-mirror pattern (`GOAUTH_ARCHIVE_FOLDER_ID`,
`GOAUTH_MMPC_FOLDER_ID`, `GOAUTH_GFL_AIR_FOLDER_ID`, …), with ONE new folder
for all four streams rather than four. Reasons: a snapshot file is small and is
written only on change (a few per week across all four at today's rates); the
filename prefix (`rop_` / `mmd_` / `subm_` / `pfas_`) already keeps the
streams apart, exactly as `wds_archiver`'s `WDS_` prefix shares Mirror B's
folder with the nSITE PDFs; and one secret is one activation step instead of
four. `export_snapshot()` takes `folder_env` as a parameter, so a future
stream (RIDE, per ADR 019) can use its own folder if it ever needs one. The
four workflows pass the `GOAUTH_*_KUNST` creds and the new folder secret
through; an unset secret is an empty string, which `is_configured()` reads as
"not configured" — inert.

### 4. Wired BEFORE the Sheet row, in all four

Each watcher calls the exporter immediately before EVERY snapshot append —
the `baseline` row as well as the `changed` row, since both are an observed
snapshot and the baseline of a newly added item is as much a record as a
change — so the write order is the architecture doc's invariant:

```
Drive upload (Tier 1)  ->  Sheet row (Tier 2)  ->  alert email
```

The payload exported is the exact string that goes into the Sheet cell
(`snap_json` for ROP/MMD/Submissions, `content` for PFAS). The Sheet row
schemas are unchanged: the `(date, key, hash)` triple in the filename is the
same triple the row carries, so the Drive file joins back to its row without a
new column (Rule U2).

### 5. Ships DISABLED; activation is Trisha's

Mirrors ADR 026's three steps: (1) create the app-only folder — the
`create-oauth-folder` workflow with identity `kunst`, e.g. `FOLDER_NAME`
"Arbor Hills Watch Snapshots"; (2) add its id as the `GOAUTH_SNAPSHOT_FOLDER_ID`
repo secret; (3) set `data_lifecycle.snapshot_export.enabled: true` and commit.
No seed or backfill: the first enabled run exports whatever changes next; the
existing Sheet history stays Tier 2 as before.

## Tests

- `tests/test_snapshot_exporter.py`: the flag's missing/false/true shapes; the
  naming rule; disabled → `None` with no env read and no Drive call; enabled-
  but-unconfigured → `None` with no network; success → link, verbatim payload,
  right mimetype/folder, temp file removed, service built once; upload failure /
  dead token / unserializable payload → `None`, class-only log, cache reset.
- `tests/test_snapshot_export_wiring.py`: per watcher, through the REAL
  exporter with only `archive_client`'s Drive I/O faked — flag ON: every Sheet
  append is immediately preceded by a Drive upload of the byte-identical
  payload under the expected name, and the email comes after the row; a Drive
  failure never blocks the row or the alert; flag OFF (each suite's own cfg):
  the exporter never reaches Drive, OAuth, or `is_configured()`. The committed
  `config.yml` is pinned to ship the flag `false`.
- The four watchers' existing suites pass with zero edits.

## Consequences

- Once enabled, ROP / MMD / nSITE Submissions / PFAS each gain a Tier-1
  immutable copy of every snapshot they record, independent of the Sheet,
  written before any human-facing signal.
- No behavior change until activated (flag off = no-op, no env read).
- A Drive failure after activation is logged (class only) and the run stays
  green: the Sheet row is still the system of record for that observation, and
  the gap is visible in the log, not the exit code. This matches the handoff's
  "nicety, not a blocker" ruling; a louder posture (exit 1 on export failure,
  as ADR 026's capture does) can be added later if Trisha wants it.
- Not in scope, by ruling: PDF rendering; RIDE (`ride_watcher.py`) wiring;
  refactoring `wds_archiver.py` / `gfl_feed_snapshot.py` onto this helper.
