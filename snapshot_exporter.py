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
network, quota. The caller's own Sheet row and alert are never affected. A
PER-RUN CIRCUIT BREAKER (see `_tripped`) turns the first failure in a process
into a skip for every later item that run, so a Drive outage costs one
timeout, not one per item — latency, not just exceptions, must never hold a
Sheet row hostage.

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

import hashlib
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

# PER-RUN CIRCUIT BREAKER. "Never blocks the row" is true for exceptions but
# not for latency: upload_file() makes two Drive calls, each with the shared
# exponential-backoff retry on a 60 s socket timeout, so under a sustained
# Drive/OAuth outage ONE item can sit for minutes — and ROP's baseline run has
# seven items inside a 15-minute job. A runner kill is not an exception, so the
# except-envelope below would never fire and the remaining items' Sheet rows
# would go unwritten that run. So the FIRST failure in a process trips this
# breaker and every later export_snapshot() call in the same run skips
# straight to the Sheet row (one line logged, once). A dead token likewise
# fails once, not once per item. Reset only by a new process (= the next run).
# Process-global, not per folder_env: every caller today runs one watcher per
# process against the one shared folder; a future second folder in the SAME
# process would share this breaker (fine — the outage it guards is Drive/OAuth
# itself, not a folder).
_tripped: str | None = None


def is_enabled(cfg) -> bool:
    """The cross-cutting flag: `data_lifecycle.snapshot_export.enabled`.
    Missing block, missing key, None, false -> off. A scalar where a mapping
    is expected (`data_lifecycle: true`, a config typo) also reads as OFF
    rather than raising — this runs before the caller's Sheet row. Pure."""
    dl = cfg.get("data_lifecycle") if isinstance(cfg, dict) else None
    se = dl.get("snapshot_export") if isinstance(dl, dict) else None
    return bool(se.get("enabled")) if isinstance(se, dict) else False


def snapshot_name(stream: str, key, date: str, digest: str, ext: str = "json") -> str:
    """`<stream>_<key>_<date>_<digest>.<ext>` with the key reduced to a safe
    filename fragment (anything outside [A-Za-z0-9._-] collapses to `-`). A
    key longer than the cap is truncated AND suffixed with a short hash of the
    full key, so two long keys sharing a prefix can never alias to one name
    (upload_file() dedupes by name — an alias would silently drop the second
    snapshot). Pure; accepts any key (str()'d)."""
    raw = "" if key is None else str(key)
    safe_key = _SAFE_RE.sub("-", raw).strip("-")
    if len(safe_key) > _MAX_KEY_CHARS:
        tag = hashlib.sha1(raw.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()[:8]
        safe_key = safe_key[:_MAX_KEY_CHARS - 9].rstrip("-") + "-" + tag
    return f"{stream}_{safe_key or 'item'}_{date}_{digest}.{ext}"


def _serialize(payload) -> str:
    """A str payload is written verbatim (it IS the Sheet cell); anything else
    is single-line sorted-keys JSON with json.dumps's default separators —
    byte-identical to the form the four watchers build with
    `json.dumps(snap, sort_keys=True, ensure_ascii=False)` and hash, so a dict
    payload's file still matches its row's hash."""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _service():
    global _drive
    if _drive is None:
        _drive = ac.oauth_drive_service()
    return _drive


def _reset_service() -> None:
    global _drive
    _drive = None


def _trip(reason: str) -> None:
    global _tripped
    _tripped = reason


def export_snapshot(payload, name: str, cfg,
                    folder_env: str = FOLDER_ENV) -> str | None:
    """Upload one snapshot payload as an immutable Drive file named `name`.

    Returns the file's webViewLink, or None when the export did not happen for
    ANY reason — never raises. Order of checks: the config flag first (off ->
    no env read, no network), then the per-run breaker (a prior failure this
    process -> skip silently), then Drive configuration (missing creds or
    folder -> None, no network), then the upload itself (any exception ->
    None, logged by class only, service cache dropped, breaker tripped).

    Logging is count/class-only — the payload is public regulatory data but
    an exception message can carry a URL or a token fragment (SEC-001)."""
    if not is_enabled(cfg):
        return None
    if _tripped is not None:
        return None
    if not ac.is_configured(folder_env):
        print(f"[snapshot-export] {folder_env} / GOAUTH_* not configured — "
              "skipping Drive copies this run (Sheet rows still written).")
        _trip("not configured")
        return None

    tmp = None
    try:
        # Item-LOCAL preparation (serialize + temp file). A failure here says
        # nothing about Drive, so it returns None for THIS item without
        # tripping the breaker — the next item still gets its copy.
        try:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else "json"
            mimetype = _MIME.get(ext, "application/octet-stream")
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=f".{ext}",
                                             delete=False) as fh:
                tmp = fh.name
                fh.write(_serialize(payload))
        except Exception as e:  # noqa: BLE001 — best-effort by contract; class only
            try:
                print(f"[snapshot-export] FAILED to prepare {name} ({type(e).__name__}) — "
                      "Sheet row still written; no Drive copy of this item.")
            except Exception:  # noqa: BLE001 — logging itself must never raise
                pass
            return None
        # Drive-side work (token refresh + two Drive calls). A failure here IS
        # an outage signal for the rest of the run: trip the breaker.
        try:
            link = ac.upload_file(_service(), tmp, name, mimetype, ac.folder_id(folder_env))
            print(f"[snapshot-export] ok {name}")   # inside the envelope: a stdout error must not escape either
            return link
        except Exception as e:  # noqa: BLE001 — best-effort by contract; class only
            _reset_service()
            _trip(type(e).__name__)
            try:
                print(f"[snapshot-export] FAILED ({type(e).__name__}) for {name} — "
                      "Sheet row still written; skipping further Drive copies this run.")
            except Exception:  # noqa: BLE001 — logging itself must never raise
                pass
            return None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
