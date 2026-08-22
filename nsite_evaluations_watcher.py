"""
nsite_evaluations_watcher.py — runs daily, watching EGLE's nSITE EVALUATIONS
profile (the underlying inspection record a violation or compliance action
often stems from) for every site in config.yml's `nsite_sites` registry that
has an `nsite_evaluations.tiers` entry. Each site is actually fetched+diffed
at its own `poll` cadence (daily/biweekly/quarterly), not every run.
Standalone + self-terminating, the same shape as
nsite_compliance_actions_watcher.py / nsite_violations_watcher.py. See
docs/decisions/029-nsite-evaluations-watch.md.

WHY: ADR 028 (the Compliance Actions watch) named Evaluations as the natural
next profile — it is the underlying INSPECTION record. A Violations record
already carries `evalEvalNum` (VIOLATION_FIELDS' eval_num), so a violation can
be joined back to the evaluation that found it; watching Evaluations gives
visibility into new inspections — the event that often PRECEDES a violation or
compliance action — rather than only their downstream consequences. Live
reconnaissance (2026-08-08, all 19 sites) found N2688 477 evaluations (most
recent 2026-08-07 — an ongoing, actively inspected site), RA 40, N1504 5,
P1488 2, WRD 1.

ONE item per site — `eval:<srn>` (e.g. "eval:N2688") — derived from ONE fetch
per site, so each item's fetch failure is independent (no ROP-style batching).

WHAT IT DOES per site (mirrors nsite_compliance_actions_watcher):
  - build a canonical snapshot of the site's evaluation list + hash it,
  - compare to the last snapshot recorded in the "Evaluations Watch" tab,
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert,
  - hash unchanged -> no-op.

WHY A REF-NUMBER-KEYED DIFF AND NOT A MULTISET (the feasibility gate's finding,
2026-08-08, all 19 sites live-fetched, re-confirmed by this build): UNLIKE
Violations and Compliance Actions, this profile DOES carry a genuine unique
key — `evalEvalNum` was unique within every site's record set with evaluations
(477/477 at N2688, 40/40 at RA, 5/5 at N1504, 1/1 at WRD). So the diff is
keyed on eval_num, the same idiom as nsite_submissions_watcher's
`submSubmRefNum`, rather than the rop/mmd/ride/violations/compliance-actions
Counter-multiset idiom: a brand-new eval_num means a NEW EVALUATION (a new
inspection — the highest-value event this watch exists to surface); an
existing eval_num with a changed field means that evaluation's detail
advanced (e.g. its sample-transmittal date being filled in after the fact).
This profile has no status field to trip-wire (confirmed absent across the
live sample), so a new inspection appearing IS the primary signal.

WHY THE SNAPSHOT STILL NEEDS A BUDGET-DEGRADATION GUARD DESPITE THE KEYED
DESIGN: a Google Sheets cell caps at 50,000 characters. N2688's 477
evaluations serialize to 134,020 chars as one JSON object per record (the
plain Submissions idiom) — 2.7x over the cap — and even the compact
POSITIONAL form (a `fields` header plus `[eval_num, *values]` rows,
positional instead of repeating eight key names 477 times) is 75,494 chars,
still over the 45,000-char default budget. UNLIKE Violations/Compliance
Actions, where the budget guard is inherited insurance that never actually
fires at observed volumes, N2688 is expected to run in DEGRADED (digest) mode
from day one. The degraded form here is deliberately richer than Violations/
Compliance Actions' count-only degradation: because eval_num is a real,
visible (not hashed-away) key, `_cell_payload`'s digest form keeps
`[eval_num, digest]` pairs rather than an anonymous digest multiset, so
`summarize_evaluations_change` can still report EXACTLY WHICH eval_num is new/
changed/removed even when no field-level detail survives the truncation. This
is the reason a new evaluation still reads as "+ NEW EVALUATION E-..." instead
of a bare record-count delta at N2688, the site this watch exists for.

NO SEVERITY JUDGMENT and no status vocabulary to trip-wire (there is none in
this profile) — this watch is a pure ADDITIONS-and-CHANGES trip-wire, and a
human reads what a new inspection or an advanced field means.

FAILURE HANDLING, in three layers (identical to nsite_violations_watcher /
nsite_compliance_actions_watcher):

  - FETCH FAILURE (nsite_client.NsiteFetchError) is TRANSIENT per site: skip-
    and-warn if that site's item already has a baseline; LOUD exit 1 if it
    doesn't yet (an activation-time block must surface, not silently no-op
    forever).
  - Any OTHER per-site exception inside the loop — a rejected Sheets write, a
    malformed stored snapshot, a bad registry entry — is caught per site, so
    one bad site can never abort the run and drop every site after it.
  - A TAB READ FAILURE aborts the whole run BEFORE any write: the batched read
    RAISES rather than swallowing to "no rows", so a throttled read can never
    masquerade as "never seen" and write a spurious baseline over an
    un-alerted change on this append-only, last-write-wins tab.
  - A CHANGE THAT WAS RECORDED BUT NOT EMAILED sets a non-zero exit too — the
    durable row surviving is not success for a stream whose entire deliverable
    is the alert, and the advanced hash means the next run reports "unchanged"
    and never retries.

GATED on nsite_evaluations.enabled, which ships FALSE: a brand-new poller
against a live external system built unattended, so flipping it on is
explicitly a separate human step (overnight-coder Step 3).

TIERED CADENCE: `_is_due` is IMPORTED from nsite_submissions_watcher rather
than reimplemented — it is already generic over (cadence, srn, today). The
TIERS THEMSELVES are this profile's own and deliberately differ from
Submissions', Violations', AND Compliance Actions' (see ADR 029): only N2688
is daily here (its 477 evaluations include one 2026-08-07, two weeks old at
build time — genuinely ongoing); RA/N1504/P1488/WRD are biweekly despite
varying real counts, because none has an evaluation newer than mid-2025
except WRD, whose single 2022 record is bumped up from what its raw count
alone would suggest for the same reason Violations/Compliance Actions bump it
— an open JPA/PFOS matter that could plausibly produce a new inspection.

NO DRIVE / OAUTH (same scope call as every other watch, ADR 012): the
deliverable is the ALERT + the durable Sheet row.

Runs daily — its workflow file was landed directly into .github/workflows/
(this build session's SSH key authenticated non-interactively against
GitHub, so the `workflow` OAuth-scope blocker that parked Stream L/M's
workflow files at docs/pending-workflows/ did not apply here — see
overnight-coder.md Step 4). Harmless while enabled is false.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
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
# (cadence, srn, today) with nothing profile-specific in it, and MOVING it
# would edit a live `enabled: true` stream's file for a new stream's benefit.
from nsite_submissions_watcher import _is_due

# A Google Sheets cell holds at most 50,000 characters — a hard API limit, not
# a tunable. UNLIKE Violations/Compliance Actions, N2688's real volume already
# exceeds this even in the compact positional form, so the degrade path below
# is not theoretical insurance here — see the module docstring.
HARD_SHEETS_CELL_LIMIT = 50000
DEFAULT_SNAPSHOT_CHAR_BUDGET = 45000

# How many ADDED/CHANGED/REMOVED lines an alert email prints before
# summarizing the rest. Cheap insurance carried over from Violations/CA; a
# bulk EGLE re-import is the only realistic trigger, since a single day's real
# new-evaluation count is small even at N2688.
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
    nsite_evaluations.enabled is set) has a direct unit test. Mirrors
    nsite_compliance_actions_watcher._should_run."""
    if not (cfg.get("nsite_evaluations") or {}).get("enabled"):
        return False, "nsite_evaluations.enabled is false — skipping (no-op)."
    return True, ""


def diff_fields(cfg: dict) -> tuple[str, ...]:
    """The field set this run diffs on: every nsite_client.EVALUATION_FIELDS
    except any listed in `nsite_evaluations.exclude_fields` — MINUS `eval_num`,
    which can never be excluded regardless of config. Unlike Violations/CA's
    `exclude_fields` (which only ever drops a display/headline field from a
    multiset diff), eval_num here is the structural diff KEY — every snapshot/
    diff helper below assumes it is present, so silently honoring an
    `exclude_fields: [eval_num]` typo would break the diff rather than merely
    degrade a line's readability."""
    excluded = set((cfg.get("nsite_evaluations") or {}).get("exclude_fields") or [])
    excluded.discard("eval_num")
    return tuple(f for f in nc.EVALUATION_FIELDS if f not in excluded)


# ---------------------------------------------------------------------------
# Snapshot / diff (pure)
# ---------------------------------------------------------------------------


def evaluations_snapshot(rows: list[dict], fields: tuple[str, ...]) -> dict:
    """Canonical, hash-stable snapshot of one site's evaluation list, keyed on
    `eval_num` (verified unique per site with evaluations — 477/477 at N2688,
    40/40 at RA, 5/5 at N1504, 1/1 at WRD, live spike). Sorted by eval_num for
    a stable hash regardless of API ordering, encoded POSITIONALLY (`fields`
    header + `[eval_num, *values]` rows) rather than one JSON object per
    record — see the module docstring for the char-count comparison.

    ASSUMES `eval_num` is in `fields` — true for every real caller, since this
    is only ever invoked with diff_fields()'s output (which can never exclude
    it) or a fields tuple built directly from nc.EVALUATION_FIELDS."""
    keyed = sorted(
        ({f: r.get(f) or "" for f in fields} for r in rows),
        key=lambda d: d["eval_num"],
    )
    return {
        "fields": list(fields),
        "n": len(keyed),
        "rows": [[d[f] for f in fields] for d in keyed],
    }


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as nsite_violations_watcher/nsite_compliance_actions_watcher.

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
    per-evaluation digest map if the full form would exceed `budget` chars.

    Inherited from ADR 023/028's guard, but at Evaluations volumes this is NOT
    theoretical insurance — N2688's 477 records are 75,494 chars even in the
    compact positional form, over the 45,000 default budget, so N2688 runs in
    this degraded branch on every real run. The degraded form keeps
    `[eval_num, digest]` pairs (eval_num stays VISIBLE, unlike Violations/CA's
    anonymous digest multiset) so summarize_evaluations_change can still name
    exactly which evaluation is new/changed/removed without a field-level
    diff — see the module docstring."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    truncated = {
        "fields": snap.get("fields", []),
        "n": snap.get("n", 0),
        "truncated": True,
        "digests": sorted([row[0], _row_digest(row[1:])] for row in snap.get("rows", [])),
    }
    blob = json.dumps(truncated, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    # Past ~2,000 distinct eval_nums even the digest form outgrows the budget,
    # and an over-cap write is rejected outright. Drop the digests as a final
    # clamp: the hash still detects any change and `n` still counts it, which
    # is all a fully-truncated snapshot promises anyway.
    return json.dumps(
        {"fields": snap.get("fields", []), "n": snap.get("n", 0), "truncated": True,
         "digests": [], "digests_dropped": True},
        sort_keys=True, ensure_ascii=False,
    )


def _rows_by_key(snap: dict) -> dict[str, dict]:
    """Rebuild {eval_num: {field: value}} from a snapshot's positional rows.
    Raises on a structurally invalid payload — summarize_evaluations_change
    catches that and reports it as an unreadable previous snapshot rather than
    diffing against garbage."""
    fields = snap.get("fields") or []
    if "eval_num" not in fields:
        raise ValueError("snapshot fields do not include eval_num")
    out: dict[str, dict] = {}
    for row in snap.get("rows", []):
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError(f"rows entry does not match fields: {row!r}")
        d = dict(zip(fields, row))
        out[d["eval_num"]] = d
    return out


def _digest_map(snap: dict) -> dict[str, str] | None:
    """eval_num -> digest of its non-key fields, from whichever form the
    snapshot is in: read from `digests` for a truncated payload, computed
    from `rows` for a full one (looked up by field NAME, not position, so a
    hand-reordered stored cell still parses). None when neither is available
    (a truncated payload past the final clamp, or a malformed cell), so
    callers can tell "nothing changed" from "we cannot tell"."""
    try:
        if snap.get("truncated"):
            if snap.get("digests_dropped"):
                return None
            return dict(snap.get("digests", []))
        fields = snap.get("fields") or []
        idx = fields.index("eval_num")
        return {
            row[idx]: _row_digest([v for i, v in enumerate(row) if i != idx])
            for row in snap.get("rows", [])
        }
    except Exception:  # noqa: BLE001 — malformed payload: "cannot tell"
        return None


def summarize_evaluations_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two evaluation snapshots.
    Pure — unit-tested.

    Handles, in order, four cases a bare ref-keyed diff would misreport:
      1. the previous snapshot is MISSING or UNREADABLE — which must never be
         confused with "the site had zero evaluations",
      2. the diffed FIELD SET changed (a config edit, not an EGLE event),
      3. either side was TRUNCATED — still reports exactly which eval_num is
         new/changed/removed (digest-level, no field detail), NOT a bare
         count delta, because eval_num survives truncation (see the module
         docstring) — this is the case N2688 hits on every real run,
      4. the untruncated case — full field-level ADDED/CHANGED/REMOVED,
         mirroring nsite_submissions_watcher.summarize_submissions_change."""
    # A genuine snapshot ALWAYS carries "fields", even when it holds zero
    # records — so its absence means the stored cell was empty, cleared, or
    # unparseable, not that the site was clean.
    if "fields" not in old:
        return (
            "changed — the previous snapshot was missing or unreadable, so no "
            "diff could be computed (this row re-baselines the site; it does "
            "NOT mean the site previously had no evaluations)",
            "The last stored snapshot for this site could not be read — the "
            "Snapshot JSON cell was empty, cleared, or malformed. This row "
            "restores a good baseline. Check the Evaluations Watch tab's "
            "history, and review MiEnviro directly for anything that changed "
            "in the meantime.",
        )

    old_fields, new_fields = old.get("fields"), new.get("fields")
    if old_fields is not None and new_fields is not None and old_fields != new_fields:
        return (
            "diffed field set changed by configuration — NOT an EGLE change "
            f"({', '.join(old_fields)} -> {', '.join(new_fields)})",
            "The nsite_evaluations.exclude_fields setting changed, so this "
            "run's snapshot is not comparable to the previous one. This row "
            "re-baselines the site; no evaluation-level change is implied.",
        )

    if old.get("truncated") or new.get("truncated"):
        old_d, new_d = _digest_map(old), _digest_map(new)
        if old_d is None or new_d is None:
            return (
                f"changed — {old.get('n', 0)} -> {new.get('n', 0)} evaluation "
                "record(s) (a previous or current snapshot was too large to "
                "persist even as per-evaluation digests; no ref-level diff "
                "available)",
                "This site's evaluation list is too large to persist in full "
                "or as per-evaluation digests in one Sheet cell. Review "
                "MiEnviro directly for detail.",
            )
        new_nums = sorted(set(new_d) - set(old_d))
        removed_nums = sorted(set(old_d) - set(new_d))
        changed_nums = sorted(n for n in (set(new_d) & set(old_d)) if new_d[n] != old_d[n])
        lines = (
            [f"+ NEW EVALUATION  {n}" for n in new_nums]
            + [f"~ CHANGED  {n} (detail changed — snapshot too large for a "
               f"field-level diff)" for n in changed_nums]
            + [f"- REMOVED  {n}" for n in removed_nums]
        )
        dropped = 0
        if len(lines) > MAX_ALERT_LINES:
            dropped = len(lines) - MAX_ALERT_LINES
            lines = lines[:MAX_ALERT_LINES]
            lines.append(
                f"... and {dropped} more change line(s) not shown here — the "
                f"Evaluations Watch tab's Snapshot JSON for this row is complete."
            )
        if not (new_nums or changed_nums or removed_nums):
            return (
                f"changed — snapshot hash changed but no per-evaluation "
                f"difference was detected at the digest level "
                f"({old.get('n', 0)} -> {new.get('n', 0)} record(s))",
                "\n".join(lines),
            )
        parts = []
        if new_nums:
            parts.append(f"{len(new_nums)} new evaluation(s)")
        if changed_nums:
            parts.append(f"{len(changed_nums)} evaluation(s) with changed detail "
                          f"(no field-level diff — snapshot too large)")
        if removed_nums:
            parts.append(f"{len(removed_nums)} evaluation(s) no longer listed")
        note = "; ".join(parts)
        if dropped:
            note += f" ({dropped} change line(s) summarized in the email)"
        return note, "\n".join(lines)

    try:
        old_by_key = _rows_by_key(old)
        new_by_key = _rows_by_key(new)
    except Exception:  # noqa: BLE001 — structurally invalid stored payload
        return (
            "changed — the previous snapshot was structurally invalid, so no "
            "diff could be computed (this row re-baselines the site)",
            "The last stored snapshot for this site parsed as JSON but was not "
            "a valid snapshot. This row restores a good baseline; review "
            "MiEnviro directly for anything that changed in the meantime.",
        )

    fields = tuple(new_fields or old_fields or nc.EVALUATION_FIELDS)
    new_nums = sorted(set(new_by_key) - set(old_by_key))
    removed_nums = sorted(set(old_by_key) - set(new_by_key))
    changed_nums = sorted(
        n for n in (set(new_by_key) & set(old_by_key))
        if new_by_key[n] != old_by_key[n]
    )

    parts: list[str] = []
    lines: list[str] = []
    for num in new_nums:
        r = new_by_key[num]
        parts.append("new evaluation recorded")
        lines.append(
            f"+ NEW EVALUATION  {num} — {r.get('eval_type') or '—'} "
            f"(program={r.get('program_area') or '—'}, "
            f"category={r.get('eval_category') or '—'}, "
            f"start={r.get('start_date') or '—'})"
        )
    for num in changed_nums:
        old_r, new_r = old_by_key[num], new_by_key[num]
        parts.append("existing evaluation changed")
        changed_detail = ", ".join(
            f"{f}: {old_r.get(f) or '—'} -> {new_r.get(f) or '—'}"
            for f in fields
            if f != "eval_num" and old_r.get(f) != new_r.get(f)
        )
        lines.append(f"~ CHANGED  {num} — {new_r.get('eval_type') or '—'} "
                      f"({changed_detail})")
    for num in removed_nums:
        r = old_by_key[num]
        parts.append("evaluation no longer listed")
        lines.append(f"- REMOVED  {num} — {r.get('eval_type') or '—'}")

    dropped = 0
    if len(lines) > MAX_ALERT_LINES:
        dropped = len(lines) - MAX_ALERT_LINES
        lines = lines[:MAX_ALERT_LINES]
        lines.append(
            f"... and {dropped} more change line(s) not shown here — the "
            f"Evaluations Watch tab's Snapshot JSON for this row is complete."
        )

    if not parts:
        return "changed (no ref-level diff — see snapshot)", ""
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    note = "; ".join(seen)
    if dropped:
        note += f" ({dropped} change line(s) summarized in the email)"
    return note, "\n".join(lines)


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested. All EGLE-derived text
    lands HERE, in the body — never in the subject line (see run())."""
    shown = body or "(no further detail — see the Evaluations Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills nSITE EVALUATIONS list changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's nSITE Evaluations profile — the "
        "underlying inspection record a violation or compliance action often "
        "stems from. It trip-wires a brand-new evaluation the moment EGLE "
        "records one under this site (a new inspection), or an existing "
        "evaluation's detail advancing (e.g. its sample-transmittal date being "
        "filled in). This profile carries no status field, so there is no "
        "severity judgment to make — read the change in MiEnviro directly for "
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
    up here — a per-site read that could fail is what would let a throttled
    Sheets response masquerade as "never seen" and write a spurious baseline
    over an un-alerted change.

    Durable row FIRST, alert email SECOND — a crash between them loses the
    alert, never the record, and never re-fires next run since the row already
    advanced the stored hash. A LOST ALERT IS STILL REPORTED as a non-zero exit
    by run()."""
    new_hash = snapshot_hash(snap)
    snap_json = _cell_payload(snap, budget)

    if last is None:
        sw.append_evaluations_watch_row(sheets, sheet_id, today, key, label,
                                        "baseline", new_hash,
                                        "initial snapshot (no alert)", _now(),
                                        snap_json)
        print(f"[nsite-evaluations-watch] {label}: baseline recorded "
              f"({new_hash}, {snap.get('n', 0)} record(s)).")
        return "baseline", None

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-evaluations-watch] {label}: unchanged ({new_hash}).")
        return "unchanged", None

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_evaluations_change(old_snap, snap)
    sw.append_evaluations_watch_row(sheets, sheet_id, today, key, label,
                                    "changed", new_hash, note, _now(), snap_json)
    print(f"[nsite-evaluations-watch] {label}: CHANGED "
          f"({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is
    # alerting for THIS site only, so a failure in either step is reported
    # rather than raised, and can never abort run()'s other sites.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-evaluations-watch] {label}: change recorded but alert "
              f"body FORMATTING failed: {e}")
        return "changed", f"body formatting failed: {e}"
    try:
        # Subject carries ONLY the maintainer-authored label from nsite_sites —
        # no EGLE-derived text ever reaches an email header.
        ea.send_email(f"[Evaluations watch] {label} changed", email_body, cfg,
                      recipients=recipients)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-evaluations-watch] {label}: change recorded but alert "
              f"email FAILED (the change IS durable in the Evaluations Watch "
              f"tab — this run exits non-zero so the lost notification is "
              f"visible): {e}")
        return "changed", f"send failed: {e}"
    return "changed", None


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[nsite-evaluations-watch] {reason}")
        return 0

    ecfg = cfg.get("nsite_evaluations") or {}
    recipients = ecfg.get("recipients") or None  # None -> full alert_recipients list
    # Clamped, because `snapshot_char_budget` sits in config.yml directly under
    # a comment naming the 50,000 cap — so "raise it a bit" is a plausible edit,
    # and any value at or above the cap would disable the truncation guard
    # entirely and hand the site a permanently rejected write.
    budget = min(int(ecfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET),
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
        for srn, poll in (ecfg.get("tiers") or {}).items()
    ]

    # If alerting is ALREADY known to be impossible, stop before touching
    # anything. Writing a row here would advance the stored hash, so tomorrow's
    # run would compare equal, report "unchanged", and never retry — the
    # notification gone permanently even after the secret was fixed. Nothing is
    # lost by stopping: the evaluations are still in nSITE, so the next healthy
    # run records AND alerts on them.
    alerting_ok, alerting_error = alerting_is_configured(cfg, recipients)
    if not alerting_ok:
        print(f"[nsite-evaluations-watch] {alerting_error} — aborting BEFORE "
              f"any fetch or write. A change found now could not be emailed, and "
              f"recording it would advance the stored hash so the next run would "
              f"report 'unchanged' and never retry. Nothing is lost: fix the "
              f"configuration and the next run records and alerts normally.")
        return 1

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_evaluations_tabs(sheets, sheet_id)

    session = nc.make_session()
    today_date = _today_date()
    today = today_date.isoformat()
    counts = {"baseline": 0, "changed": 0, "unchanged": 0, "skipped": 0,
              "fetch_failed": 0, "failed": 0, "alert_failed": 0}
    exit_code = 0

    # Cadence gate first, so the batched read below asks only about sites this
    # run will actually touch. An UNRECOGNIZED cadence is treated as due rather
    # than raising: unlike a `tiers` srn missing from the registry (we don't
    # know WHAT to poll, so ADR 022 fails loudly), an unknown cadence only means
    # we don't know HOW OFTEN — and polling every run is the complete, fail-safe
    # answer. A typo must never blind a site.
    due_sites = []
    for site in sites:
        cadence = site.get("poll", "daily")
        srn = site["srn"]
        try:
            due = _is_due(cadence, srn, today_date)
        except Exception as e:  # noqa: BLE001 — unknown cadence -> poll, don't skip
            print(f"[nsite-evaluations-watch] {srn}: unrecognized poll cadence "
                  f"{cadence!r} ({e}) — treating as due (fail-safe).")
            due = True
        if due:
            due_sites.append(site)
        else:
            print(f"[nsite-evaluations-watch] {srn}: not due today "
                  f"({cadence} cadence) — skipping.")
            counts["skipped"] += 1

    # ONE tab read for every due site, not one per site. Two reasons, both
    # load-bearing: it cuts the read count (less throttling exposure), and
    # last_evaluations_snapshots RAISES on a read failure instead of
    # returning a per-key None. A swallowed read error here would make every
    # site look never-seen, write fresh "baseline" rows, and — because the tab
    # is append-only with last-write-wins — PERMANENTLY erase an un-alerted
    # change rather than deferring it. So a read failure aborts before any
    # write, leaving last run's state intact and correct.
    try:
        last_by_key = sw.last_evaluations_snapshots(
            sheets, sheet_id, [f"eval:{s['srn']}" for s in due_sites])
    except Exception as e:  # noqa: BLE001
        print(f"[nsite-evaluations-watch] could not read the Evaluations "
              f"Watch tab ({type(e).__name__}: {e}) — aborting before any "
              f"DATA row is written, so no site can be spuriously re-baselined.")
        return 1

    for site in due_sites:
        # One try per site, so NOTHING (a fetch failure, an oversized or
        # rejected Sheets write, a malformed stored snapshot, a malformed
        # registry entry) can abort the run and silently drop every site
        # queued after this one.
        try:
            srn, name, nsite_id = site["srn"], site["name"], site["id"]
            key = f"eval:{srn}"
            label = f"nSITE Evaluations — {name} ({srn})"
            last = last_by_key.get(key)

            try:
                evals = nc.fetch_site_evaluations(session, nsite_id)
                print(f"[nsite-evaluations-watch] {label}: fetched "
                      f"{len(evals)} evaluation(s).")
            except nc.NsiteStructuralError as e:
                # NOT transient, so it must NOT take the skip-and-warn path
                # below: nSITE having changed the response shape (e.g. started
                # paging) would otherwise fail identically every single day
                # behind a green build. Checked before the base class.
                counts["fetch_failed"] += 1
                exit_code = 1
                print(f"[nsite-evaluations-watch] {label}: STRUCTURAL break in "
                      f"nSITE's response — this will NOT fix itself and needs "
                      f"code changes; failing loudly rather than going quiet: {e}")
                continue
            except nc.NsiteFetchError as e:
                counts["fetch_failed"] += 1
                if last is not None:
                    print(f"[nsite-evaluations-watch] {label}: fetch failed, "
                          f"skipping this run (baseline preserved, not diffed): {e}")
                else:
                    print(f"[nsite-evaluations-watch] {label}: NO BASELINE and "
                          f"fetch failed (failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                continue

            snap = evaluations_snapshot(evals, fields)
            result, alert_error = _diff_and_record(
                sheets, sheet_id, today, key, label, snap, cfg, recipients, last, budget)
            counts[result] += 1
            if alert_error:
                counts["alert_failed"] += 1
                exit_code = 1
        except Exception as e:  # noqa: BLE001 — isolate this site, keep the run going
            print(f"[nsite-evaluations-watch] {site.get('srn', '?')}: UNEXPECTED "
                  f"failure, continuing with the remaining sites: "
                  f"{type(e).__name__}: {e}")
            counts["failed"] += 1
            exit_code = 1

    print(f"[nsite-evaluations-watch] done — {counts['changed']} changed, "
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today, {counts['fetch_failed']} fetch-failed, "
          f"{counts['failed']} errored, {counts['alert_failed']} change(s) recorded "
          f"but NOT emailed (across {len(sites)} site"
          f"{'' if len(sites) == 1 else 's'}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
