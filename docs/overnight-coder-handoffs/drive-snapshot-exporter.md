# Overnight-coder handoff — one generic Drive-snapshot exporter for ROP, MMD, nSITE Submissions, PFAS

*Staged 2026-10-16. Trisha ruled on this design 2026-07-26 ("PR2" of the
data-lifecycle gap plan). Read `docs/overnight-coder.md` first. This adds a
new, additive capability, shipped **config-flag OFF by default** — per Step 3
this is the new-source pattern (ships disabled, a human flips it on later), not
a change to an already-live path. Recommended tier: Sonnet.

**Cloud-ready:** Runs in cloud mode: ends at a DRAFT PR. No live credentials —
this build cannot create the real Drive folder(s) or obtain the secret folder
ID(s); it ships the capability OFF and well-tested, for Trisha to configure
and enable locally.

**Live-path check:** this is new, additive functionality behind a new config
flag that this PR must default to `false`/off. Confirm at build time that no
part of this accidentally runs by default against any of the four watchers'
live paths (`rop_watcher.py`, `mmd_watcher.py`, `nsite_submissions_watcher.py`,
`pfas_watcher.py` are all currently `enabled: true` in `config.yml` for their
own core function — only the NEW export step must stay gated off).*

## Background

This repo's durability model (`docs/data-lifecycle-architecture.md`) ranks
stores Tier 1 (immutable Drive file) > Tier 2 (append-only Sheet tab) > Tier 3
(mutable Sheet tab). Four watchers — `rop_watcher.py`, `mmd_watcher.py`,
`nsite_submissions_watcher.py`, `pfas_watcher.py` — already write a full
snapshot JSON (or normalized text, for PFAS) to an append-only Sheet tab on
every observed change (Tier 2: durable enough to survive an ordinary pipeline
run, confirmed — grep each for its `append_*_row`/`append_rows` call). **None
of the four has a Drive copy** (verified: no `archive_client`/`upload_file`
import or call in any of the four, as of this writing — re-confirm at build
time).

Trisha's ruling (2026-07-26, internal plan "PR2"): build **ONE generic
snapshot→Drive immutable exporter**, wired into all four, closing all four
gaps at once rather than four near-duplicate one-offs. PDF rendering is
explicitly deferred — this PR only needs to get the raw snapshot payload into
an immutable Drive file; a courtroom-renderable PDF is a separate, later step
if an exhibit is ever actually needed.

**The existing precedent to reuse, not reinvent:** `archive_client.py` is this
repo's OAuth-as-user Drive client (ADR 007) — `is_configured(folder_env)`,
`oauth_drive_service()`, `upload_file(drive_service, local_path, drive_name,
mime_type, folder_id)`, `folder_id()`. `wds_archiver.py` and
`gfl_feed_snapshot.py` are the two existing callers; read both for the
established calling pattern (credential check → best-effort upload → never
let an upload failure block the watcher's own core write path).

## What to build

1. **One shared helper** (new module, e.g. `snapshot_exporter.py`, or a
   function added to `archive_client.py` if that reads more naturally once
   you're in the code) — something like `export_snapshot(payload: dict | str,
   name: str, folder_env: str, cfg: dict) -> str | None`: best-effort,
   returns the Drive file id/link on success or `None` on any failure
   (network, not configured, quota) — **never raises**, matching this repo's
   existing "an archive upload is a nicety, not a blocker" convention (see
   how `wds_archiver.py` and `archiver.py` treat upload failures).
2. **A new config flag, default OFF**, gating whether any of the four
   watchers calls the exporter at all — e.g. `data_lifecycle.snapshot_export.enabled:
   false` (pick a key name that reads naturally next to this repo's existing
   per-stream `enabled:` flags; don't bury it inside one of the four
   watchers' own blocks since it's cross-cutting). While off, the four
   watchers behave EXACTLY as they do today — zero behavior change, so this
   can merge on green tests without a live-specimen check, consistent with
   the new-source pattern in Step 3.
3. **Folder ID(s) via new secrets Trisha sets later** — follow the existing
   per-mirror pattern (`GOAUTH_ARCHIVE_FOLDER_ID` for Mirror B,
   `GOAUTH_MMPC_FOLDER_ID` for Mirror D, etc.): either one shared new folder
   env var for all four streams, or one per stream — your call, but say which
   you picked and why in the PR. **Do not create the folder or the secret
   yourself** (no credentials here); just wire the env var name(s) through
   `is_configured()`/`oauth_drive_service()`/`folder_id()` the same way the
   existing archivers do.
4. **Wire the exporter into all four watchers**, called on every observed
   change (the same moment each watcher already builds its snapshot payload
   and is about to append the Sheet row) — **before** the Sheet row write,
   matching the architecture doc's write-ordering invariant: `Drive upload
   (Tier 1) → Sheet index row → alert email`. Grep each file's existing
   `append_*_row`/`send_email` call sites (`rop_watcher.py`, `mmd_watcher.py`,
   `nsite_submissions_watcher.py` all build a `snap`/`snap_json` dict before
   their Sheet append; `pfas_watcher.py`'s equivalent is its normalized
   `content` string before `append_pfas_snapshot_row`) and insert the export
   call immediately before that append, not after.
5. **Write-ordering test per watcher** (this is the plan's own named
   mitigation for "a new exporter that writes the Drive copy after the alert
   would silently violate the durability guarantee"): with the flag on, mock
   the Drive upload and the Sheet append, and assert the Drive call happened
   before the Sheet append call in each of the four watchers.

## Tests

- Exporter unit tests: success path returns a link; a Drive failure (mocked
  exception) returns `None` and does not raise; not-configured returns `None`
  without attempting a network call.
- Per-watcher test (flag ON): exporter is called with the same payload that
  gets written to the Sheet; Drive call precedes Sheet append (the
  write-ordering test from item 5).
- Per-watcher test (flag OFF, the default): exporter is never called; all
  existing tests for the four watchers pass completely unmodified. This is
  the key regression guard proving the default-off behavior is truly
  unchanged.
- A config-flag test: missing/false key behaves as off; explicit `true`
  turns it on.
- Existing tests for all four watchers must stay green with ZERO
  modification when the flag is off (if you find yourself needing to edit an
  existing test just to make this land, that's a signal the wiring isn't as
  additive as it should be — stop and reconsider before pushing).

## Out of scope

- **RIDE Part-201** (`ride_watcher.py`) — the plan names it as a candidate to
  "plug into the same helper," but RIDE is currently `enabled: false` and
  ADR 019 already has its own plan for if/when it's re-enabled. Build the
  shared helper generically enough that RIDE could use it later, but do not
  wire RIDE into anything in this PR.
- **PDF rendering** of any snapshot into a courtroom exhibit — explicitly
  deferred per the ruling. This PR stops at an immutable raw snapshot file in
  Drive.
- **WDS's own `wds_archiver.py` and `gfl_feed_snapshot.py`** — already have
  their own Drive uploads; don't refactor them to use the new shared helper
  unless doing so is trivial and genuinely improves things. If you do touch
  either, call it out as a deliberate, separable change in the PR, don't bury
  it in the four-watcher wiring.
- Creating the actual Drive folder(s) or secrets — not possible from this
  cloud session; that's Trisha's activation step, exactly like the existing
  GFL-air-capture precedent (ADR 026: ships disabled, a documented 3-step
  human activation).

## Acceptance

- `pytest -q` fully green.
- With the flag off (default), behavior of all four watchers is provably
  unchanged (existing test suites pass with zero edits).
- With the flag on (tests only — no real Drive access here), each watcher's
  snapshot reaches the exporter before its Sheet row write, proven by the
  ordering tests.
- The PR's "Before merging" section spells out Trisha's activation steps
  explicitly, mirroring the GFL-air-capture pattern: create the Drive
  folder(s), add the secret(s), flip the flag, commit.
- CLAUDE.md: add an Architecture entry for the new exporter module/function
  and note the four call sites. Regenerate `docs/topology/README.md`'s
  artifacts for the new module and its new dependency edges into the four
  watchers.
- `docs/data-lifecycle-architecture.md`: update the per-stream status for
  ROP/MMD/nSITE-Submissions/PFAS to reflect the new Tier-1 capability
  (currently/on-flag), since this document is the one Trisha and any outside
  reader would check for "does this stream have a durable exhibit copy."
