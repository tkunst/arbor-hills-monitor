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
27,431 chars. `_cell_payload` additionally guards the residual headroom: over
`snapshot_char_budget` it persists a digest multiset with a "truncated" marker
rather than letting an oversized write abort the run.

NO SEVERITY JUDGMENT. EGLE's status vocabulary is a multi-state lifecycle
("Active - Addressed not Resolved", "Active – Reviewed/Evaluated", "Inactive -
Resolved", ...), not a good/bad binary. This watch is a TRIP-WIRE on change,
exactly like Submissions: it alerts that something moved and lets a human read
what it means. It never decides which status is bad.

FETCH FAILURE (nsite_client.NsiteFetchError) is TRANSIENT per site: skip-and-
warn if that site's item already has a baseline; LOUD exit 1 if it doesn't yet
(an activation-time block must surface, not silently no-op forever). Any OTHER
per-site exception — notably a Sheets write failure — is caught per site too,
so one bad site can never abort the run and silently drop every site after it.

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

# A Google Sheets cell holds at most 50,000 characters. Stay under it with
# real margin — the snapshot JSON is the whole point of the durable row, and a
# rejected write would take the row down with it.
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
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return fallback


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
    it is 4.7x smaller than one JSON object per record (see the module
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

    Today's worst case (RA, 299 records) is 27,431 chars against a 45,000
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
    return json.dumps(truncated, sort_keys=True, ensure_ascii=False)


def _counter_of(snap: dict) -> Counter:
    counts: Counter = Counter()
    for n, *vals in snap.get("counted_rows", []):
        counts[tuple(vals)] += n
    return counts


def _detail(rec: dict, fields: tuple[str, ...] | list) -> str:
    """Every diffed field except the one already used as the line's headline —
    an unprinted field renders two identical-looking ADDED/REMOVED lines
    (rop_watcher's documented lesson), so nothing is elided here."""
    return ", ".join(f"{f}={rec.get(f) or '—'}" for f in fields if f != "category")


def summarize_violations_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two violation snapshots.
    Pure — unit-tested.

    Handles, in order, three cases a bare multiset diff would misreport:
      1. the diffed FIELD SET changed (a config edit, not an EGLE event),
      2. either side was TRUNCATED (no field-level diff is possible),
      3. zero -> some / some -> zero, which for the 16 zero-violation sites is
         the single highest-value alert this stream can produce and must not
         read as a bland "1 record added"."""
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
        return (
            f"changed — {old_n} -> {new_n} violation record(s) "
            "(snapshot too large to store in full; no field-level diff available)",
            "This site's violation list is too large to persist in full in one "
            "Sheet cell, so only per-record digests were stored. The record "
            "count changed as shown above; review MiEnviro directly for detail.",
        )

    fields = tuple(new_fields or old_fields or nc.VIOLATION_FIELDS)
    old_counts, new_counts = _counter_of(old), _counter_of(new)
    added = new_counts - old_counts
    removed = old_counts - new_counts

    lines: list[str] = []
    for t, n in sorted(added.items()):
        r = dict(zip(fields, t))
        lines.extend([f"+ ADDED    {r.get('category') or '—'} ({_detail(r, fields)})"] * n)
    for t, n in sorted(removed.items()):
        r = dict(zip(fields, t))
        lines.extend([f"- REMOVED  {r.get('category') or '—'} ({_detail(r, fields)})"] * n)

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
                     budget=DEFAULT_SNAPSHOT_CHAR_BUDGET) -> str:
    """Baseline/compare/record/alert for one site. Returns "baseline" /
    "changed" / "unchanged". Durable row FIRST, alert email SECOND (best-
    effort) — a crash between them loses the alert, never the record, and never
    re-fires next run since the row already advanced the stored hash. If the
    row write itself raises, it propagates BEFORE any email is sent, so an
    alert can never describe a row that failed to land."""
    new_hash = snapshot_hash(snap)
    snap_json = _cell_payload(snap, budget)
    last = sw.last_violations_snapshot(sheets, sheet_id, key)

    if last is None:
        sw.append_violations_watch_row(sheets, sheet_id, today, key, label, "baseline",
                                       new_hash, "initial snapshot (no alert)", _now(),
                                       snap_json)
        print(f"[nsite-violations-watch] {label}: baseline recorded "
              f"({new_hash}, {snap.get('n', 0)} record(s)).")
        return "baseline"

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-violations-watch] {label}: unchanged ({new_hash}).")
        return "unchanged"

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_violations_change(old_snap, snap)
    sw.append_violations_watch_row(sheets, sheet_id, today, key, label, "changed",
                                   new_hash, note, _now(), snap_json)
    print(f"[nsite-violations-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is best-
    # effort alerting for THIS site only, so a bug in either step can never
    # escape _diff_and_record and abort run()'s processing of other sites.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — formatting is best-effort; row is recorded
        print(f"[nsite-violations-watch] {label}: change recorded but alert body "
              f"FORMATTING failed: {e}")
        return "changed"
    try:
        # Subject carries ONLY the maintainer-authored label from nsite_sites —
        # no EGLE-derived text ever reaches an email header.
        ea.send_email(f"[Violations watch] {label} changed", email_body, cfg,
                      recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
        print(f"[nsite-violations-watch] {label}: change recorded but alert email "
              f"FAILED: {e}")
    return "changed"


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[nsite-violations-watch] {reason}")
        return 0

    vcfg = cfg.get("nsite_violations") or {}
    recipients = vcfg.get("recipients") or None  # None -> full alert_recipients list
    budget = int(vcfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET)
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

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_violations_tabs(sheets, sheet_id)

    session = nc.make_session()
    today_date = _today_date()
    today = today_date.isoformat()
    counts = {"baseline": 0, "changed": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    exit_code = 0

    for site in sites:
        srn, name, nsite_id = site["srn"], site["name"], site["id"]
        cadence = site.get("poll", "daily")
        key = f"viol:{srn}"
        label = f"nSITE Violations — {name} ({srn})"

        if not _is_due(cadence, srn, today_date):
            print(f"[nsite-violations-watch] {label}: not due today "
                  f"({cadence} cadence) — skipping.")
            counts["skipped"] += 1
            continue

        # One try per site, so NOTHING (a fetch failure, an oversized or
        # rejected Sheets write, a malformed stored snapshot) can abort the run
        # and silently drop every site queued after this one.
        try:
            try:
                viols = nc.fetch_site_violations(session, nsite_id)
                print(f"[nsite-violations-watch] {label}: fetched "
                      f"{len(viols)} violation(s).")
            except nc.NsiteFetchError as e:
                has_baseline = sw.last_violations_snapshot(sheets, sheet_id, key) is not None
                if has_baseline:
                    print(f"[nsite-violations-watch] {label}: fetch failed, skipping "
                          f"this run (baseline preserved, not diffed): {e}")
                else:
                    print(f"[nsite-violations-watch] {label}: NO BASELINE and fetch "
                          f"failed (failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                continue

            snap = violations_snapshot(viols, fields)
            result = _diff_and_record(sheets, sheet_id, today, key, label, snap, cfg,
                                      recipients, budget)
            counts[result] += 1
        except Exception as e:  # noqa: BLE001 — isolate this site, keep the run going
            print(f"[nsite-violations-watch] {label}: UNEXPECTED failure, continuing "
                  f"with the remaining sites: {type(e).__name__}: {e}")
            counts["failed"] += 1
            exit_code = 1

    print(f"[nsite-violations-watch] done — {counts['changed']} changed, "
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today, {counts['failed']} failed "
          f"(across {len(sites)} site{'' if len(sites) == 1 else 's'}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
