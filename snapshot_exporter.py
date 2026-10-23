"""
snapshot_exporter.py — ONE generic "snapshot -> immutable Drive file" exporter
for the watchers whose only durable record is an append-only Sheet tab.

WHY (docs/data-lifecycle-architecture.md; Trisha's 2026-07-26 "PR2" ruling):
the durability model ranks Tier 1 (an immutable Drive file) above Tier 2 (an
append-only Sheet tab). Four watchers — rop_watcher, mmd_watcher,
nsite_submissions_watcher, pfas_watcher — write their full snapshot payload to
a Tier-2 tab on every observed change but had NO Drive copy. Rather than four
near-duplicate one-offs, this module is the one shared helper, wired into all
four at the same moment: immediately BEFORE each one appends its snapshot row,
so the write order is the architecture doc's invariant

    Drive upload (Tier 1) -> Sheet row (Tier 2) -> alert email

A courtroom-renderable PDF is explicitly deferred — this exports the RAW
payload (the exact JSON / normalized text that goes into the Sheet cell) and
nothing more.

BEST-EFFORT, NEVER RAISES. An archive upload is a nicety, not a blocker (the
same convention as archiver.mirror_one_now() and wds_archiver's per-page
isolation): export_snapshot() returns the Drive webViewLink on success and
None on ANY failure — flag off, Drive not configured, dead OAuth token,
network, quota. The caller's own Sheet row and alert are never affected.

GATED, DEFAULT OFF. `data_lifecycle.snapshot_export.enabled` in config.yml is
cross-cutting (one switch for all four streams), so it lives in its own
top-level block rather than inside any one watcher's. While it is false the
four watchers behave EXACTLY as before — is_enabled() is the first check and
short-circuits before any env read or network call. Flipping it on is a human
step (overnight-coder Step 3, new-source pattern), after creating the folder
and setting the secret below.

ONE SHARED FOLDER for all four streams, keyed by GOAUTH_SNAPSHOT_FOLDER_ID (the
existing per-mirror pattern: GOAUTH_ARCHIVE_FOLDER_ID for Mirror B,
GOAUTH_MMPC_FOLDER_ID for Mirror D, GOAUTH_GFL_AIR_FOLDER_ID for ADR 026).
One folder rather than four because: a snapshot file is small and only
written on change (a few per week across all four streams at today's rates),
the filename prefix (`rop_`, `mmd_`, `subm_`, `pfas_`) already keeps the
streams apart exactly the way wds_archiver's `WDS_` prefix shares Mirror B's
folder with the nSITE PDFs, and one secret is one activation step instead of
four. A future stream (RIDE, per ADR 019) can pass its own folder_env if it
ever needs a separate folder.

FILE NAMING — `snapshot_name()` builds `<stream>_<key>_<date>_<hash>.<ext>`.
The hash is load-bearing: archive_client.upload_file() is idempotent BY NAME
(find_in_folder() reuses an existing same-named file instead of uploading),
so a name without the content hash would silently keep the FIRST of two
distinct changes observed on the same day. With the hash in the name, a
re-run of an already-exported snapshot is a cheap reuse and two different
snapshots are two different files. The (date, key, hash) triple is also
exactly what the Sheet row carries, so the Drive file joins back to its row
without a new Sheet column (Rule U2 — the row schemas are untouched).

Import-light: archive_client lazily imports the google libs itself, so a
watcher importing this module gains no new import-time dependency.
"""
from __future__ import annotations

import json
import os
import re
import tempfile

import archive_client as ac

FOLDER_ENV = "GOAUTH_SNAPSHOT_FOLDER_ID"

_MIME = {"json": "application/json", "txt": "text/plain"}
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_KEY_CHARS = 60

# One OAuth Drive service per process: oauth_drive_service() exchanges the
# refresh token on every call (a network round-trip), and a run that exports
# several items (ROP's seven, Submissions' per-site loop) should not pay that
# once per item. Dropped on any failure so the next call rebuilds it fresh.
_drive = None


def is_enabled(cfg: dict | None) -> bool:
    """The cross-cutting flag: `data_lifecycle.snapshot_export.enabled`.
    Missing block, missing key, None, false -> off. Pure."""
    dl = (cfg or {}).get("data_lifecycle") or {}
    se = dl.get("snapshot_export") or {}
    return bool(se.get("enabled"))


def snapshot_name(stream: str, key: str, date: str, digest: str, ext: str = "json") -> str:
    """`<stream>_<key>_<date>_<digest>.<ext>` with the key reduced to a safe
    filename fragment (anything outside [A-Za-z0-9._-] collapses to `-`,
    capped so a long PFAS label can't produce an unwieldy name). Pure."""
    safe_key = _SAFE_RE.sub("-", key or "").strip("-")[:_MAX_KEY_CHARS].strip("-") or "item"
    return f"{stream}_{safe_key}_{date}_{digest}.{ext}"


def _serialize(payload) -> str:
    """A str payload is written verbatim (it IS the Sheet cell); anything else
    is JSON with sorted keys, the same canonical form the watchers hash."""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)


def _service():
    global _drive
    if _drive is None:
        _drive = ac.oauth_drive_service()
    return _drive


def _reset_service() -> None:
    global _drive
    _drive = None


def export_snapshot(payload, name: str, cfg: dict | None,
                    folder_env: str = FOLDER_ENV) -> str | None:
    """Upload one snapshot payload as an immutable Drive file named `name`.

    Returns the file's webViewLink, or None when the export did not happen for
    ANY reason — never raises. Order of checks: the config flag first (off ->
    no env read, no network), then Drive configuration (missing creds or
    folder -> None, no network), then the upload itself (any exception ->
    None, logged by class only, service cache dropped).

    Logging is count/class-only — the payload is public regulatory data but
    an exception message can carry a URL or a token fragment (SEC-001)."""
    if not is_enabled(cfg):
        return None
    if not ac.is_configured(folder_env):
        print(f"[snapshot-export] {folder_env} / GOAUTH_* not configured — "
              f"skipping Drive copy of {name} (Sheet row still written).")
        return None

    tmp = None
    try:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "json"
        mimetype = _MIME.get(ext, "application/octet-stream")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=f".{ext}",
                                         delete=False) as fh:
            fh.write(_serialize(payload))
            tmp = fh.name
        link = ac.upload_file(_service(), tmp, name, mimetype, ac.folder_id(folder_env))
        print(f"[snapshot-export] ok {name}")
        return link
    except Exception as e:  # noqa: BLE001 — best-effort by contract; class only
        _reset_service()
        print(f"[snapshot-export] FAILED ({type(e).__name__}) for {name} — "
              "Sheet row still written; no Drive copy this run.")
        return None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
