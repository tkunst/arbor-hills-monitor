"""
nsite_violations_watcher.py — runs daily, watching EGLE's nSITE VIOLATIONS
profile (the state's own enforcement record) for every site in config.yml's
`nsite_sites` registry that has a `nsite_violations.tiers` entry. Each site is
actually fetched+diffed at its own `poll` cadence (daily/biweekly/quarterly),
not every run. Standalone + self-terminating, the same shape as
nsite_submissions_watcher.py / rop_watcher.py / mmd_watcher.py. See
docs/decisions/023-nsite-violations-watch.md.

WHY: ADRs 020/021 built the Submissions watch and both flagged that the same
nSITE API exposes six more unpolled profiles per site. Violations is the first
of them, and the highest-value: it is a formal VIOLATION FINDING by the
regulator, not a filing or a permit status. Live reconnaissance found real
history — RA 299 records, N2688 58, N1504 3 — going back to 2004.

ONE item per site — `viol:<srn>` (e.g. "viol:N2688") — derived from ONE fetch
per site, so each item's fetch failure is independent (no ROP-style batching).

WHAT IT DOES per site (mirrors nsite_submissions_watcher):
  - build a canonical snapshot of the site's violation list + hash it,
  - compare to the last snapshot recorded in the "Violations Watch" tab,
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert,
  - hash unchanged -> no-op.

WHY A MULTISET DIFF AND NOT A KEYED ONE (the feasibility gate's finding,
2026-08-04, all 19 sites live-fetched): unlike Submissions, whose
`submSubmRefNum` is a guaranteed unique key, the Violations profile has NO
unique-ID field. Not one of the eight fields is unique within a site's record
set, and neither is any composite — a five-field composite still leaves 191
collisions across RA's 299 records. EGLE genuinely files repeated,
byte-identical violation rows: RA's 299 records collapse to just 108 DISTINCT
field tuples, N2688's 58 to 55. So the diff is a full-record
`collections.Counter` MULTISET (the rop/mmd/ride idiom) — counts are load-
bearing, and a plain set would silently destroy 191 real enforcement records
on RA alone. A record whose fields change shows as its old shape REMOVED plus
its new shape ADDED; every field is printed on those lines, so a change
confined to any one field is always visible (rop_watcher's lesson).

WHY THE SNAPSHOT IS RUN-LENGTH COUNTED: a Google Sheets cell caps at 50,000
characters. RA's 299 violations serialize to 130,188 chars as one JSON object
per record — 2.6x over the cap, i.e. the naive mirror of the Submissions
design would simply fail to write. Encoding the Counter directly (a `fields`
header plus `[count, *values]` rows, positional instead of repeating eight key
names 299 times) is exact, lossless, still human-readable in the tab, and
24,884 chars. `_cell_payload` additionally guards the residual headroom: over
`snapshot_char_budget` it persists a digest multiset with a "truncated" marker
rather than letting an oversized write abort the run.

NO SEVERITY JUDGMENT. EGLE's status vocabulary is a multi-state lifecycle
("Active - Addressed not Resolved", "Active – Reviewed/Evaluated", "Inactive -
Resolved", ...), not a good/bad binary. This watch is a TRIP-WIRE on change,
exactly like Submissions: it alerts that something moved and lets a human read
what it means. It never decides which status is bad.

FAILURE HANDLING, in three layers:

  - FETCH FAILURE (nsite_client.NsiteFetchError) is TRANSIENT per site: skip-
    and-warn if that site's item already has a baseline; LOUD exit 1 if it
    doesn't yet (an activation-time block must surface, not silently no-op
    forever).
  - Any OTHER per-site exception inside the loop — a rejected Sheets write, a
    malformed stored snapshot, a bad registry entry — is caught per site, so
    one bad site can never abort the run and drop every site after it. (Setup
    BEFORE the loop — config load, ensure_violations_tabs, the batched tab
    read — deliberately still aborts: there is no per-site work to salvage and
    proceeding would write against unknown state.)
  - A TAB READ FAILURE aborts the whole run BEFORE any write. This is the one
    non-obvious call, and it is the reason the read is batched and raising: if
    a throttled read were swallowed to "no rows" (which sheet_writer._tab_rows
    does, correctly, for its own append-only-accumulator callers), every site
    would look never-seen, a fresh "baseline" row would be written, and because
    the tab is append-only with last-write-wins that spurious baseline BECOMES
    the state — permanently ERASING a real un-alerted change instead of
    deferring it by one run.
  - A CHANGE THAT WAS RECORDED BUT NOT EMAILED sets a non-zero exit too. The
    durable row surviving is not success for a stream whose entire deliverable
    is the alert, and since the row already advanced the stored hash, the next
    run reports "unchanged" and never retries — so a green check over a
    silently-undelivered violation notice would be the worst outcome here.

GATED on nsite_violations.enabled, which ships FALSE: this is a brand-new
poller against a live external system built unattended, so flipping it on is
explicitly a separate human step (overnight-coder Step 3), unlike ADR 020/021
which Trisha directed live.

TIERED CADENCE: `_is_due` is IMPORTED from nsite_submissions_watcher rather
than reimplemented — it is already generic over (cadence, srn, today) and
nothing about it is Submissions-specific. The TIERS THEMSELVES are this
profile's own and deliberately differ from Submissions' (see ADR 023): six
sites that are `daily` there are not here, because violation activity does not
track submission activity.

NO DRIVE / OAUTH (same scope call as every other watch, ADR 012): the
deliverable is the ALERT + the durable Sheet row.

Runs daily — but its workflow file is currently parked at
docs/pending-workflows/nsite-violations-watch.yml, NOT under .github/workflows/,
because the session that wrote it had no credential with the `workflow` OAuth
scope. Harmless while nsite_violations.enabled is false (nothing would run
anyway); it MUST be moved into .github/workflows/ before that flag is flipped,
or this watch is never scheduled. See docs/pending-workflows/README.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import sheet_writer as sw
import nsite_client as nc
import email_alerts as ea
from config_loader import load_config
# Imported in place, NOT copied or moved: _is_due is already a pure function of
# (cadence, srn, today) with nothing Submissions-specific in it, and MOVING it
# would edit a live `enabled: true` stream's file for a new stream's benefit.
from nsite_submissions_watcher import _is_due

# A Google Sheets cell holds at most 50,000 characters — a hard API limit, not
# a tunable. Stay under it with real margin: the snapshot JSON is the whole
# point of the durable row, and a rejected write would take the row down too.
HARD_SHEETS_CELL_LIMIT = 50000
DEFAULT_SNAPSHOT_CHAR_BUDGET = 45000

# How many ADDED/REMOVED lines an alert email prints before summarizing the
# rest. Routine changes are 1-5 lines; this only bites a wholesale EGLE
# re-import (RA alone could otherwise emit ~600 lines). Whatever is dropped is
# stated explicitly in the body — the durable Sheet row is always complete.
MAX_ALERT_LINES = 200


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_date() -> date:
    return (datetime.now(_ET) if _ET else datetime.now()).date()


def _load_json(raw: str, fallback):
    """Parse a stored snapshot cell, falling back for anything that isn't a
    JSON OBJECT — not merely for anything unparseable. A bare scalar (`0`,
    `null`, `true`) parses fine but is not a mapping, and every downstream
    reader does `"fields" not in old` / `old.get(...)`, which raises TypeError
    on an int. That would wedge the site permanently — no row, no alert, the
    same failure every run — which is exactly what the unreadable-snapshot
    branch exists to prevent."""
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return fallback
    return parsed if isinstance(parsed, dict) else fallback


def alerting_is_configured(cfg: dict, recipients: list | None) -> tuple[bool, str]:
    """Whether a change alert could actually be delivered right now. Pure apart
    from reading os.environ, so it is directly unit-testable.

    email_alerts.send_email deliberately NO-OPS (prints and returns, no
    exception) when SMTP env vars are missing or the recipient list resolves
    empty, so a dry/local run doesn't crash. That is right for it and wrong for
    us: catching only exceptions would let the single most likely cause of
    non-delivery — a missing, renamed, or rotated GitHub secret — record a
    change, advance the stored hash so the next run says "unchanged", and exit
    0. Checking the same condition up front is what makes that case loud."""
    missing = [k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")
               if not os.environ.get(k)]
    if missing:
        return False, f"SMTP not configured (missing {', '.join(missing)})"
    if not (list(recipients) if recipients else ea.resolve_recipients(cfg)):
        return False, "no alert recipients resolved"
    return True, ""


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact
    bug this guards (the watch doing real work / emailing before
    nsite_violations.enabled is set) has a direct unit test. Mirrors
    nsite_submissions_watcher._should_run."""
    if not (cfg.get("nsite_violations") or {}).get("enabled"):
        return False, "nsite_violations.enabled is false — skipping (no-op)."
    return True, ""


def diff_fields(cfg: dict) -> tuple[str, ...]:
    """The field set this run diffs on: every nsite_client.VIOLATION_FIELD
    except any listed in `nsite_violations.exclude_fields`.

    The lever exists for ONE named residual (see ADR 023): `comments` is free
    text, and it is not knowable from a single build session whether EGLE edits
    its wording without a substantive change. It ships INCLUDED — it carries
    the violation's substance (permit and condition citations), and this repo's
    posture under unknowable external behavior is to alert, not to guess. If
    activation shows comment-only churn, excluding it is a config edit rather
    than a code change. Note that changing this list changes the hash basis, so
    it fires exactly one "changed" row per site — which summarize_violations_
    change labels as configuration, not as an EGLE change."""
    excluded = set((cfg.get("nsite_violations") or {}).get("exclude_fields") or [])
    return tuple(f for f in nc.VIOLATION_FIELDS if f not in excluded)


# ---------------------------------------------------------------------------
# Snapshot / diff (pure)
# ---------------------------------------------------------------------------


def violations_snapshot(rows: list[dict], fields: tuple[str, ...]) -> dict:
    """Canonical, hash-stable snapshot of one site's violation list, encoded as
    a run-length COUNTED multiset: `counted_rows` is [[count, *values], ...]
    sorted by the value tuple, with the field order it was built from recorded
    in `fields` so the snapshot is self-describing.

    This IS the Counter the diff needs, so nothing is reconstructed twice, and
    it is 4.1x smaller than one JSON object per record (see the module
    docstring's cell-cap note). An EMPTY record set is a valid snapshot — for
    16 of the 19 watched sites, "no violations on file" IS the baseline and the
    first violation appearing is the change."""
    counts = Counter(tuple(r.get(f) or "" for f in fields) for r in rows)
    return {
        "fields": list(fields),
        "n": sum(counts.values()),
        "counted_rows": [[n, *list(t)] for t, n in sorted(counts.items())],
    }


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as nsite_submissions_watcher/rop_watcher/mmd_watcher.

    Always computed over the FULL snapshot, never over the possibly-truncated
    cell payload — otherwise raising or lowering snapshot_char_budget would
    silently re-baseline every site and fire a change alert for each."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _row_digest(values: list) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]


def _cell_payload(snap: dict, budget: int = DEFAULT_SNAPSHOT_CHAR_BUDGET) -> str:
    """Serialize a snapshot for the Sheet's Snapshot JSON cell, degrading to a
    digest multiset if the full form would exceed `budget` characters.

    Today's worst case (RA, 299 records) is 24,884 chars against a 45,000
    budget, so this never fires in practice — it exists because the ONLY thing
    standing between a bulk EGLE re-import and a hard 50,000-char write
    rejection would otherwise be that margin. Exactly ONE fallback form, not a
    cascade: the truncated payload keeps `fields`, `n` and a per-row digest
    multiset (2,285 chars for RA), which is enough to detect and COUNT change
    but not to describe it field-by-field — summarize_violations_change says so
    explicitly rather than inventing a diff it cannot support."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    truncated = {
        "fields": snap.get("fields", []),
        "n": snap.get("n", 0),
        "truncated": True,
        "digests": sorted(
            [_row_digest(list(vals)), n]
            for n, *vals in snap.get("counted_rows", [])
        ),
    }
    blob = json.dumps(truncated, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    # The digest form is ~140 chars per DISTINCT row, so it too outgrows the
    # budget somewhere past ~2,000 distinct rows — and an over-cap write is
    # rejected outright, which is the exact outcome this guard exists to
    # prevent. Drop the digests as a final clamp: the hash still detects any
    # change and `n` still counts it, which is all summarize_violations_change
    # promises for a truncated snapshot anyway.
    return json.dumps(
        {"fields": snap.get("fields", []), "n": snap.get("n", 0), "truncated": True,
         "digests": [], "digests_dropped": True},
        sort_keys=True, ensure_ascii=False,
    )


def _digest_counter(snap: dict) -> Counter | None:
    """The per-row digest multiset for a snapshot, whichever form it is in:
    read from `digests` for a truncated payload, computed from `counted_rows`
    for a full one. None when neither is available (a truncated payload past
    the final clamp, where the digests were dropped), so callers can tell
    "nothing changed at row level" from "we cannot tell"."""
    try:
        if snap.get("truncated"):
            if snap.get("digests_dropped"):
                return None
            return Counter({d: n for d, n in snap.get("digests", [])})
        return Counter({_row_digest(list(vals)): n
                        for n, *vals in snap.get("counted_rows", [])})
    except Exception:  # noqa: BLE001 — malformed payload: "cannot tell"
        return None


def _counter_of(snap: dict) -> Counter:
    """Rebuild the multiset from a snapshot's counted_rows. Raises on a
    structurally invalid payload — summarize_violations_change catches that and
    reports it as an unreadable previous snapshot rather than diffing against
    garbage."""
    counts: Counter = Counter()
    for row in snap.get("counted_rows", []):
        n, *vals = row
        if not isinstance(n, int):
            raise ValueError(f"counted_rows entry has a non-integer count: {row!r}")
        counts[tuple(vals)] += n
    return counts


def _headline_field(fields: tuple[str, ...] | list) -> str | None:
    """The field an ADDED/REMOVED line leads with — normally `category` (the
    rule or regulation cited, the most identifying thing about a violation),
    but read from the snapshot's OWN field list rather than hardcoded so that
    excluding it via `exclude_fields` degrades to the next field instead of
    printing a bare em-dash on every line."""
    if not len(fields):
        return None
    return "category" if "category" in fields else fields[0]


def _detail(rec: dict, fields: tuple[str, ...] | list) -> str:
    """Every diffed field except the one already used as the line's headline —
    an unprinted field renders two identical-looking ADDED/REMOVED lines
    (rop_watcher's documented lesson), so nothing is elided here."""
    headline = _headline_field(fields)
    return ", ".join(f"{f}={rec.get(f) or '—'}" for f in fields if f != headline)


def summarize_violations_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two violation snapshots.
    Pure — unit-tested.

    Handles, in order, four cases a bare multiset diff would misreport:
      1. the previous snapshot is MISSING or UNREADABLE — which must never be
         confused with "the site had zero violations", see below,
      2. the diffed FIELD SET changed (a config edit, not an EGLE event),
      3. either side was TRUNCATED (no field-level diff is possible),
      4. zero -> some / some -> zero, which for the 16 zero-violation sites is
         the single highest-value alert this stream can produce and must not
         read as a bland "1 record added"."""
    # A genuine snapshot ALWAYS carries "fields", even when it holds zero
    # records — so its absence means the stored cell was empty, cleared, or
    # unparseable, not that the site was clean. Without this branch an
    # unreadable cell diffs as {} and fires the loudest alert in the stream
    # ("FIRST VIOLATION(S) RECORDED") at a site that may have had hundreds of
    # violations all along. That is a live risk, not a theoretical one: the
    # snapshot lives in a cell on the OPERATOR-VISIBLE case-file Sheet, where a
    # 25 KB JSON blob is exactly the sort of thing a human tidies away.
    if "fields" not in old:
        return (
            "changed — the previous snapshot was missing or unreadable, so no "
            "diff could be computed (this row re-baselines the site; it does "
            "NOT mean the site was previously clean)",
            "The last stored snapshot for this site could not be read — the "
            "Snapshot JSON cell was empty, cleared, or malformed. This row "
            "restores a good baseline. Check the Violations Watch tab's "
            "history, and review MiEnviro directly for anything that changed "
            "in the meantime.",
        )
    try:
        old_counts = _counter_of(old)
    except Exception:  # noqa: BLE001 — structurally invalid stored payload
        return (
            "changed — the previous snapshot was structurally invalid, so no "
            "diff could be computed (this row re-baselines the site)",
            "The last stored snapshot for this site parsed as JSON but was not "
            "a valid snapshot. This row restores a good baseline; review "
            "MiEnviro directly for anything that changed in the meantime.",
        )

    old_fields, new_fields = old.get("fields"), new.get("fields")
    if old_fields is not None and new_fields is not None and old_fields != new_fields:
        return (
            "diffed field set changed by configuration — NOT an EGLE change "
            f"({', '.join(old_fields)} -> {', '.join(new_fields)})",
            "The nsite_violations.exclude_fields setting changed, so this run's "
            "snapshot is not comparable to the previous one. This row re-"
            "baselines the site; no violation-level change is implied.",
        )

    old_n, new_n = old.get("n", 0), new.get("n", 0)
    if old.get("truncated") or new.get("truncated"):
        # A count-only note would describe NOTHING when 300 records are wholly
        # replaced by 300 different ones ("300 -> 300"). The truncated form
        # keeps a per-row digest multiset precisely so the row-level magnitude
        # of the change survives even when its content can't — use it when both
        # sides have one.
        rows_note = ""
        old_d, new_d = _digest_counter(old), _digest_counter(new)
        if old_d is not None and new_d is not None:
            gone, arrived = sum((old_d - new_d).values()), sum((new_d - old_d).values())
            if gone or arrived:
                rows_note = f"; {arrived} row(s) appeared, {gone} row(s) disappeared"
        return (
            f"changed — {old_n} -> {new_n} violation record(s){rows_note} "
            "(snapshot too large to store in full; no field-level diff available)",
            "This site's violation list is too large to persist in full in one "
            "Sheet cell, so only per-record digests were stored. The record "
            "counts changed as shown above; review MiEnviro directly for detail.",
        )

    fields = tuple(new_fields or old_fields or nc.VIOLATION_FIELDS)
    new_counts = _counter_of(new)
    added = new_counts - old_counts
    removed = old_counts - new_counts

    headline = _headline_field(fields)
    lines: list[str] = []
    for t, n in sorted(added.items()):
        r = dict(zip(fields, t))
        lines.extend([f"+ ADDED    {r.get(headline) or '—'} ({_detail(r, fields)})"] * n)
    for t, n in sorted(removed.items()):
        r = dict(zip(fields, t))
        lines.extend([f"- REMOVED  {r.get(headline) or '—'} ({_detail(r, fields)})"] * n)

    dropped = 0
    if len(lines) > MAX_ALERT_LINES:
        dropped = len(lines) - MAX_ALERT_LINES
        lines = lines[:MAX_ALERT_LINES]
        lines.append(
            f"... and {dropped} more change line(s) not shown here — the "
            f"Violations Watch tab's Snapshot JSON for this row is complete."
        )

    if new_n and not old_n:
        note = (f"FIRST VIOLATION(S) RECORDED — this site had none on file "
                f"(now {new_n})")
    elif old_n and not new_n:
        note = (f"ALL VIOLATIONS NO LONGER LISTED — this site had {old_n} on "
                f"file, now none")
    elif not lines:
        note = "changed (no record-level diff — see snapshot)"
    else:
        parts = []
        if added:
            parts.append(f"{sum(added.values())} violation record(s) added/updated")
        if removed:
            parts.append(f"{sum(removed.values())} violation record(s) removed/superseded")
        note = "; ".join(parts)
        if dropped:
            note += f" ({dropped} change line(s) summarized in the email)"
    return note, "\n".join(lines)


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested. All EGLE-derived text
    lands HERE, in the body — never in the subject line (see run())."""
    shown = body or "(no further detail — see the Violations Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills nSITE VIOLATIONS list changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's nSITE Violations profile — the "
        "state's own enforcement record. It trip-wires a brand-new violation "
        "the moment EGLE records one under this site, or an existing "
        "violation's status advancing. It makes NO judgment about which "
        "status is good or bad — EGLE's status vocabulary is a multi-state "
        "lifecycle, so read the change in MiEnviro directly for full "
        "context.\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _diff_and_record(sheets, sheet_id, today, key, label, snap, cfg, recipients,
                     last, budget=DEFAULT_SNAPSHOT_CHAR_BUDGET) -> tuple[str, str | None]:
    """Baseline/compare/record/alert for one site. Returns
    (result, alert_error) where result is "baseline"/"changed"/"unchanged" and
    alert_error is None unless a change was recorded but its email could not be
    sent.

    `last` is passed IN (the run's single batched tab read) rather than looked
    up here — see run(): a per-site read that could fail is what would let a
    throttled Sheets response masquerade as "never seen" and write a spurious
    baseline over an un-alerted change.

    Durable row FIRST, alert email SECOND — a crash between them loses the
    alert, never the record, and never re-fires next run since the row already
    advanced the stored hash. If the row write itself raises, it propagates
    BEFORE any email is sent, so an alert can never describe a row that failed
    to land. A LOST ALERT IS STILL REPORTED as a non-zero exit by run(): the
    row surviving is not success for a stream whose entire deliverable is the
    alert, and a green check on a silently-undelivered violation notice is the
    worst outcome this module has."""
    new_hash = snapshot_hash(snap)
    snap_json = _cell_payload(snap, budget)

    if last is None:
        sw.append_violations_watch_row(sheets, sheet_id, today, key, label, "baseline",
                                       new_hash, "initial snapshot (no alert)", _now(),
                                       snap_json)
        print(f"[nsite-violations-watch] {label}: baseline recorded "
              f"({new_hash}, {snap.get('n', 0)} record(s)).")
        return "baseline", None

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-violations-watch] {label}: unchanged ({new_hash}).")
        return "unchanged", None

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_violations_change(old_snap, snap)
    sw.append_violations_watch_row(sheets, sheet_id, today, key, label, "changed",
                                   new_hash, note, _now(), snap_json)
    print(f"[nsite-violations-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is
    # alerting for THIS site only, so a failure in either step is reported
    # rather than raised, and can never abort run()'s other sites.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-violations-watch] {label}: change recorded but alert body "
              f"FORMATTING failed: {e}")
        return "changed", f"body formatting failed: {e}"
    try:
        # Subject carries ONLY the maintainer-authored label from nsite_sites —
        # no EGLE-derived text ever reaches an email header.
        ea.send_email(f"[Violations watch] {label} changed", email_body, cfg,
                      recipients=recipients)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-violations-watch] {label}: change recorded but alert email "
              f"FAILED (the change IS durable in the Violations Watch tab — this "
              f"run exits non-zero so the lost notification is visible): {e}")
        return "changed", f"send failed: {e}"
    # NOTE: send_email raises nothing when SMTP is unconfigured or the recipient
    # list resolves empty — it prints and returns. That silent no-op cannot be
    # reached from here, because run() checks the identical condition via
    # alerting_is_configured() and ABORTS before any fetch or write rather than
    # consuming a change it could never deliver. Only a genuine mid-send
    # failure (connection dropped, auth rejected) reaches the handler above,
    # and by then the row is already durable, so exiting non-zero is the most
    # that can be done.
    return "changed", None


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[nsite-violations-watch] {reason}")
        return 0

    vcfg = cfg.get("nsite_violations") or {}
    recipients = vcfg.get("recipients") or None  # None -> full alert_recipients list
    # Clamped, because `snapshot_char_budget` sits in config.yml directly under
    # a comment naming the 50,000 cap — so "raise it a bit" is a plausible edit,
    # and any value at or above the cap would disable the truncation guard
    # entirely and hand the site a permanently rejected write.
    budget = min(int(vcfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET),
                 HARD_SHEETS_CELL_LIMIT - 1000)
    fields = diff_fields(cfg)
    # Resolve the working site list by joining the shared identity registry
    # (nsite_sites, ADR 022) with THIS profile's own cadence map. A `tiers` srn
    # absent from the registry is a config error — KeyError raises naturally,
    # on purpose (ADR 022): a loud failure on a config typo is correct, a
    # silently-unwatched site is not.
    registry = {s["srn"]: s for s in cfg.get("nsite_sites") or []}
    sites = [
        {**registry[srn], "poll": poll}
        for srn, poll in (vcfg.get("tiers") or {}).items()
    ]

    # If alerting is ALREADY known to be impossible, stop before touching
    # anything. This is the same principle the batched tab read rests on:
    # deferring a change by a run is fine, CONSUMING one is not. Writing the
    # row here would advance the stored hash, so tomorrow's run would compare
    # equal, report "unchanged", and never retry — the notification would be
    # gone permanently even after the secret was fixed. Nothing is actually
    # lost by stopping: the violations are still in nSITE, so the next healthy
    # run records AND alerts on them. (A send failure discovered mid-run, after
    # the row is already durable, is unavoidable and handled separately in
    # _diff_and_record.)
    alerting_ok, alerting_error = alerting_is_configured(cfg, recipients)
    if not alerting_ok:
        print(f"[nsite-violations-watch] {alerting_error} — aborting BEFORE any "
              f"fetch or write. A change found now could not be emailed, and "
              f"recording it would advance the stored hash so the next run would "
              f"report 'unchanged' and never retry. Nothing is lost: fix the "
              f"configuration and the next run records and alerts normally.")
        return 1

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_violations_tabs(sheets, sheet_id)

    session = nc.make_session()
    today_date = _today_date()
    today = today_date.isoformat()
    counts = {"baseline": 0, "changed": 0, "unchanged": 0, "skipped": 0,
              "fetch_failed": 0, "failed": 0, "alert_failed": 0}
    exit_code = 0

    # Cadence gate first, so the batched read below asks only about sites this
    # run will actually touch. An UNRECOGNIZED cadence is treated as due rather
    # than raising: unlike a `tiers` srn missing from the registry (which means
    # we don't know WHAT to poll, so ADR 022 fails loudly), an unknown cadence
    # only means we don't know HOW OFTEN — and polling every run is the
    # complete, fail-safe answer. A typo must never blind a site.
    due_sites = []
    for site in sites:
        cadence = site.get("poll", "daily")
        srn = site["srn"]
        try:
            due = _is_due(cadence, srn, today_date)
        except Exception as e:  # noqa: BLE001 — unknown cadence -> poll, don't skip
            print(f"[nsite-violations-watch] {srn}: unrecognized poll cadence "
                  f"{cadence!r} ({e}) — treating as due (fail-safe).")
            due = True
        if due:
            due_sites.append(site)
        else:
            print(f"[nsite-violations-watch] {srn}: not due today "
                  f"({cadence} cadence) — skipping.")
            counts["skipped"] += 1

    # ONE tab read for every due site, not one per site. Two reasons, both
    # load-bearing: it cuts ~19 reads/run to 1 (less throttling exposure), and
    # last_violations_snapshots RAISES on a read failure instead of returning
    # a per-key None. A swallowed read error here would make every site look
    # never-seen, write fresh "baseline" rows, and — because the tab is
    # append-only with last-write-wins — PERMANENTLY erase an un-alerted
    # change rather than deferring it. So a read failure aborts before any
    # write, leaving last run's state intact and correct.
    try:
        last_by_key = sw.last_violations_snapshots(
            sheets, sheet_id, [f"viol:{s['srn']}" for s in due_sites])
    except Exception as e:  # noqa: BLE001
        print(f"[nsite-violations-watch] could not read the Violations Watch tab "
              f"({type(e).__name__}: {e}) — aborting before any DATA row is "
              f"written, so no site can be spuriously re-baselined. (The tab's "
              f"header may have been reconciled by ensure_violations_tabs above; "
              f"that is idempotent and touches no data row.)")
        return 1

    for site in due_sites:
        # One try per site, so NOTHING (a fetch failure, an oversized or
        # rejected Sheets write, a malformed stored snapshot, a malformed
        # registry entry) can abort the run and silently drop every site
        # queued after this one.
        try:
            srn, name, nsite_id = site["srn"], site["name"], site["id"]
            key = f"viol:{srn}"
            label = f"nSITE Violations — {name} ({srn})"
            last = last_by_key.get(key)

            try:
                viols = nc.fetch_site_violations(session, nsite_id)
                print(f"[nsite-violations-watch] {label}: fetched "
                      f"{len(viols)} violation(s).")
            except nc.NsiteStructuralError as e:
                # NOT transient, so it must NOT take the skip-and-warn path
                # below: nSITE having changed the response shape (e.g. started
                # paging) would otherwise fail identically every single day
                # behind a green build, while every real violation change at
                # every site went unnoticed. Checked before the base class.
                counts["fetch_failed"] += 1
                exit_code = 1
                print(f"[nsite-violations-watch] {label}: STRUCTURAL break in "
                      f"nSITE's response — this will NOT fix itself and needs "
                      f"code changes; failing loudly rather than going quiet: {e}")
                continue
            except nc.NsiteFetchError as e:
                counts["fetch_failed"] += 1
                if last is not None:
                    print(f"[nsite-violations-watch] {label}: fetch failed, skipping "
                          f"this run (baseline preserved, not diffed): {e}")
                else:
                    print(f"[nsite-violations-watch] {label}: NO BASELINE and fetch "
                          f"failed (failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                continue

            snap = violations_snapshot(viols, fields)
            result, alert_error = _diff_and_record(
                sheets, sheet_id, today, key, label, snap, cfg, recipients, last, budget)
            counts[result] += 1
            if alert_error:
                counts["alert_failed"] += 1
                exit_code = 1
        except Exception as e:  # noqa: BLE001 — isolate this site, keep the run going
            print(f"[nsite-violations-watch] {site.get('srn', '?')}: UNEXPECTED "
                  f"failure, continuing with the remaining sites: "
                  f"{type(e).__name__}: {e}")
            counts["failed"] += 1
            exit_code = 1

    print(f"[nsite-violations-watch] done — {counts['changed']} changed, "
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today, {counts['fetch_failed']} fetch-failed, "
          f"{counts['failed']} errored, {counts['alert_failed']} change(s) recorded "
          f"but NOT emailed (across {len(sites)} site"
          f"{'' if len(sites) == 1 else 's'}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
