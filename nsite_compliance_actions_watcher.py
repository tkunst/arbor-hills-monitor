"""
nsite_compliance_actions_watcher.py — runs daily, watching EGLE's nSITE
COMPLIANCE ACTIONS profile (the formal actions the regulator takes in response
to a violation — Violation Notices, Consent Orders, Consent Judgments) for
every site in config.yml's `nsite_sites` registry that has a
`nsite_compliance_actions.tiers` entry. Each site is actually fetched+diffed at
its own `poll` cadence (daily/biweekly/quarterly), not every run. Standalone +
self-terminating, the same shape as nsite_violations_watcher.py /
nsite_submissions_watcher.py / rop_watcher.py. See
docs/decisions/028-nsite-compliance-actions-watch.md.

WHY: ADR 023 (the Violations watch) closed by naming Compliance Actions as the
natural next profile — it is the OTHER half of the enforcement story. A
Violation row says the regulator FOUND the facility out of compliance; a
Compliance Action row says what the regulator DID about it. Five of N2688's
violations already sit at "Active – Compliance Action Taken"; this watch gives
visibility into whether those actions resolve, escalate, or multiply. Live
reconnaissance found real depth: N2688 39 compliance actions (most recent a
Violation Notice ISSUED 2026-07-15), RA 10 (incl. the open PFOS VN-011821),
N1504 2.

ONE item per site — `ca:<srn>` (e.g. "ca:N2688") — derived from ONE fetch per
site, so each item's fetch failure is independent (no ROP-style batching).

WHAT IT DOES per site (mirrors nsite_violations_watcher):
  - build a canonical snapshot of the site's compliance-action list + hash it,
  - compare to the last snapshot recorded in the "Compliance Actions Watch" tab,
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert,
  - hash unchanged -> no-op.

WHY A MULTISET DIFF AND NOT A KEYED ONE (the feasibility gate's finding,
2026-08-08, all 5 non-dormant sites live-fetched): the candidate diff key
`cmplActnCmplActnNum` is NOT unique. N2688 files the federal case number
`5:21-cv-12098-S` on TWO records (a Consent Order entered on two dates), so a
ref-number-keyed diff (the Submissions idiom) would silently drop one. Notably,
every FULL-field tuple IS distinct today (39/39 at N2688, 10/10 at RA, 2/2 at
N1504) — a plain set would lose nothing at present — but the Counter multiset is
chosen anyway: it is the proven rop/mmd/ride/violations idiom, it is strictly
safe should EGLE ever file a byte-identical duplicate action, and it keeps this
module line-for-line with the already-reviewed Violations watch. A record whose
fields change shows as its old shape REMOVED plus its new shape ADDED; every
field is printed on those lines, and the ADDED/REMOVED lines LEAD WITH THE
ACTION NUMBER (not the category, which is a bare "Administrative"/"Civil") so a
status change on a known action — the single highest-value event here — reads
legibly: `VN-019436 (…status=Issued)` removed, `VN-019436 (…status=Closed)`
added.

WHY THE SNAPSHOT IS RUN-LENGTH COUNTED: inherited verbatim from ADR 023, where
RA's 299 violations blew past a Google Sheets cell's 50,000-character cap. At
Compliance-Actions volumes it never comes close — N2688 (the largest, 39
records) serializes to ~4,800 chars counted, ~9,200 as one object per record,
both far under the cap. The counted encoding is kept regardless because the
snapshot structure IS the Counter the diff needs (nothing reconstructed twice),
and `_cell_payload`'s truncation guard is kept as cheap, honestly-inherited
insurance against a bulk EGLE re-import — NOT because the cap is a live concern
the way it was for RA. See ADR 028.

NO SEVERITY JUDGMENT. EGLE's status vocabulary is a multi-state lifecycle
("Issued", "Closed", "Entered", "Terminated"), not a good/bad binary. This watch
is a TRIP-WIRE on change, exactly like Violations: it alerts that something
moved and lets a human read what it means. It never decides which status is bad.

FAILURE HANDLING, in three layers (identical to nsite_violations_watcher):

  - FETCH FAILURE (nsite_client.NsiteFetchError) is TRANSIENT per site: skip-
    and-warn if that site's item already has a baseline; LOUD exit 1 if it
    doesn't yet (an activation-time block must surface, not silently no-op
    forever).
  - Any OTHER per-site exception inside the loop — a rejected Sheets write, a
    malformed stored snapshot, a bad registry entry — is caught per site, so
    one bad site can never abort the run and drop every site after it.
  - A TAB READ FAILURE aborts the whole run BEFORE any write: the batched read
    RAISES rather than swallowing to "no rows", so a throttled read can never
    masquerade as "never seen" and write a spurious baseline over an un-alerted
    change on this append-only, last-write-wins tab.
  - A CHANGE THAT WAS RECORDED BUT NOT EMAILED sets a non-zero exit too — the
    durable row surviving is not success for a stream whose entire deliverable
    is the alert, and the advanced hash means the next run reports "unchanged"
    and never retries.

GATED on nsite_compliance_actions.enabled, which ships FALSE: a brand-new poller
against a live external system built unattended, so flipping it on is explicitly
a separate human step (overnight-coder Step 3).

TIERED CADENCE: `_is_due` is IMPORTED from nsite_submissions_watcher rather than
reimplemented — it is already generic over (cadence, srn, today). The TIERS
THEMSELVES are this profile's own and deliberately differ from BOTH Submissions'
and Violations' (see ADR 028): N1504 is daily for Violations (its violations are
open/unresolved) but biweekly here, because its two compliance actions are both
CLOSED.

NO DRIVE / OAUTH (same scope call as every other watch, ADR 012): the
deliverable is the ALERT + the durable Sheet row.

Runs daily — but its workflow file is currently parked at
docs/pending-workflows/nsite-compliance-actions-watch.yml, NOT under
.github/workflows/, because the session that wrote it had no credential with
the `workflow` OAuth scope. Harmless while enabled is false; it MUST be moved
into .github/workflows/ before that flag is flipped, or this watch is never
scheduled. See docs/pending-workflows/README.md.
"""
from __future__ import annotations

import functools
import hashlib
import json
import sys
from collections import Counter

# dc/ea/nc/sw stay imported here even though run() itself now calls into
# nsite_watch_common for the Sheets/email/fetch plumbing: both this module
# and nsite_watch_common.py import the SAME singleton modules, so a test's
# monkeypatch.setattr(caw.dc, "sheets_service", ...) (etc.) still reaches the
# real call inside nsite_watch_common.run_profile_watch unchanged — see
# tests/test_nsite_compliance_actions.py's _wire(). ea is also still used
# directly by this module's own _diff_and_record (ea.send_email).
import drive_client as dc
import email_alerts as ea
import sheet_writer as sw
import nsite_client as nc
from config_loader import load_config
import nsite_watch_common as nwc
# _now/_today_date/_load_json/alerting_is_configured/_row_digest are now
# shared (byte-identical copies, previously 16/8/12/6/5 of them repo-wide —
# see nsite_watch_common.py's own module docstring); re-exported here so
# this module keeps its own names unchanged (caw._now()/caw._load_json(...)/
# caw.alerting_is_configured(...) all still work exactly as before — see
# the existing per-watcher identity tests).
from nsite_watch_common import (  # noqa: F401 -- re-exported, not just used
    _now, _today_date, _load_json, alerting_is_configured, _row_digest,
)
# Imported in place, NOT copied or moved (same as before this refactor): a
# pure function of (cadence, srn, today) with nothing profile-specific in
# it, kept as a module-global HERE (not forwarded through
# nsite_watch_common) so caw._is_due stays the identical object
# nsite_submissions_watcher._is_due IS (see its own identity test) and a
# monkeypatch.setattr(caw, "_is_due", ...) in a test still reaches run()'s
# behavior via the fresh look-up run() passes to run_profile_watch().
from nsite_submissions_watcher import _is_due

# A Google Sheets cell holds at most 50,000 characters — a hard API limit. The
# counted snapshot never approaches it at CA volumes (N2688, the largest, is
# ~4,800 chars), but the budget guard is inherited verbatim from ADR 023 as
# insurance against a bulk re-import — see the module docstring.
HARD_SHEETS_CELL_LIMIT = 50000
DEFAULT_SNAPSHOT_CHAR_BUDGET = 45000

# How many ADDED/REMOVED lines an alert email prints before summarizing the
# rest. Routine changes are 1-5 lines; this only bites a wholesale EGLE
# re-import. Whatever is dropped is stated explicitly in the body — the durable
# Sheet row is always complete.
MAX_ALERT_LINES = 200


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact
    bug this guards (the watch doing real work / emailing before
    nsite_compliance_actions.enabled is set) has a direct unit test. Thin
    wrapper over nsite_watch_common.should_run(); see its docstring."""
    return nwc.should_run(cfg, "nsite_compliance_actions")


def diff_fields(cfg: dict) -> tuple[str, ...]:
    """The field set this run diffs on: every nsite_client.COMPLIANCE_ACTION_FIELD
    except any listed in `nsite_compliance_actions.exclude_fields`.

    The lever is inherited from ADR 023 for parity, but UNLIKE Violations there
    is no known volatile field to point it at — all six CA fields are controlled
    vocabularies or a reference number, none free text. It ships excluding
    nothing; it exists only as a config-only rollback should some field prove to
    churn after activation. Changing this list changes the hash basis, so it
    fires exactly one "changed" row per site — which summarize_compliance_
    actions_change labels as configuration, not as an EGLE change."""
    excluded = set((cfg.get("nsite_compliance_actions") or {}).get("exclude_fields") or [])
    return tuple(f for f in nc.COMPLIANCE_ACTION_FIELDS if f not in excluded)


# ---------------------------------------------------------------------------
# Snapshot / diff (pure)
# ---------------------------------------------------------------------------


def compliance_actions_snapshot(rows: list[dict], fields: tuple[str, ...]) -> dict:
    """Canonical, hash-stable snapshot of one site's compliance-action list,
    encoded as a run-length COUNTED multiset: `counted_rows` is
    [[count, *values], ...] sorted by the value tuple, with the field order it
    was built from recorded in `fields` so the snapshot is self-describing.

    This IS the Counter the diff needs, so nothing is reconstructed twice. An
    EMPTY record set is a valid snapshot — for most of the watched sites, "no
    compliance actions on file" IS the baseline and the first action appearing
    is the change."""
    counts = Counter(tuple(r.get(f) or "" for f in fields) for r in rows)
    return {
        "fields": list(fields),
        "n": sum(counts.values()),
        "counted_rows": [[n, *list(t)] for t, n in sorted(counts.items())],
    }


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as nsite_violations_watcher/nsite_submissions_watcher.

    Always computed over the FULL snapshot, never over the possibly-truncated
    cell payload — otherwise raising or lowering snapshot_char_budget would
    silently re-baseline every site and fire a change alert for each."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _cell_payload(snap: dict, budget: int = DEFAULT_SNAPSHOT_CHAR_BUDGET) -> str:
    """Serialize a snapshot for the Sheet's Snapshot JSON cell, degrading to a
    digest multiset if the full form would exceed `budget` characters.

    Inherited from ADR 023. At Compliance-Actions volumes this never fires
    (N2688, the largest, is ~4,800 chars against a 45,000 budget) — it exists so
    a bulk EGLE re-import degrades gracefully instead of hitting a hard
    50,000-char write rejection. Exactly ONE fallback form: the truncated
    payload keeps `fields`, `n` and a per-row digest multiset, which is enough
    to detect and COUNT change but not to describe it field-by-field —
    summarize_compliance_actions_change says so explicitly rather than inventing
    a diff it cannot support."""
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
    # budget past enough distinct rows — and an over-cap write is rejected
    # outright. Drop the digests as a final clamp: the hash still detects any
    # change and `n` still counts it, which is all a truncated snapshot
    # promises anyway.
    return json.dumps(
        {"fields": snap.get("fields", []), "n": snap.get("n", 0), "truncated": True,
         "digests": [], "digests_dropped": True},
        sort_keys=True, ensure_ascii=False,
    )


def _digest_counter(snap: dict) -> Counter | None:
    """The per-row digest multiset for a snapshot, whichever form it is in:
    read from `digests` for a truncated payload, computed from `counted_rows`
    for a full one. None when neither is available (a truncated payload past
    the final clamp), so callers can tell "nothing changed at row level" from
    "we cannot tell"."""
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
    structurally invalid payload — summarize_compliance_actions_change catches
    that and reports it as an unreadable previous snapshot rather than diffing
    against garbage."""
    counts: Counter = Counter()
    for row in snap.get("counted_rows", []):
        n, *vals = row
        if not isinstance(n, int):
            raise ValueError(f"counted_rows entry has a non-integer count: {row!r}")
        counts[tuple(vals)] += n
    return counts


def _headline_field(fields: tuple[str, ...] | list) -> str | None:
    """The field an ADDED/REMOVED line leads with — normally `num` (the action's
    reference/notice number, the most identifying thing about a compliance
    action; the category is a bare "Administrative"/"Civil"), but read from the
    snapshot's OWN field list rather than hardcoded so that excluding it via
    `exclude_fields` degrades to the next field instead of printing a bare
    em-dash on every line."""
    if not len(fields):
        return None
    return "num" if "num" in fields else fields[0]


def _detail(rec: dict, fields: tuple[str, ...] | list) -> str:
    """Every diffed field except the one already used as the line's headline —
    an unprinted field renders two identical-looking ADDED/REMOVED lines
    (rop_watcher's documented lesson), so nothing is elided here."""
    headline = _headline_field(fields)
    return ", ".join(f"{f}={rec.get(f) or '—'}" for f in fields if f != headline)


def summarize_compliance_actions_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two compliance-action
    snapshots. Pure — unit-tested.

    Handles, in order, four cases a bare multiset diff would misreport:
      1. the previous snapshot is MISSING or UNREADABLE — which must never be
         confused with "the site had zero compliance actions",
      2. the diffed FIELD SET changed (a config edit, not an EGLE event),
      3. either side was TRUNCATED (no field-level diff is possible),
      4. zero -> some / some -> zero, the single highest-value alert for a
         site whose baseline is empty."""
    # A genuine snapshot ALWAYS carries "fields", even when it holds zero
    # records — so its absence means the stored cell was empty, cleared, or
    # unparseable, not that the site was clean. Without this branch an
    # unreadable cell diffs as {} and fires the loudest alert in the stream at a
    # site that may have had actions all along. The snapshot lives in a cell on
    # the OPERATOR-VISIBLE case-file Sheet, where a JSON blob is exactly the
    # sort of thing a human tidies away.
    if "fields" not in old:
        return (
            "changed — the previous snapshot was missing or unreadable, so no "
            "diff could be computed (this row re-baselines the site; it does "
            "NOT mean the site previously had no compliance actions)",
            "The last stored snapshot for this site could not be read — the "
            "Snapshot JSON cell was empty, cleared, or malformed. This row "
            "restores a good baseline. Check the Compliance Actions Watch tab's "
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
            "The nsite_compliance_actions.exclude_fields setting changed, so "
            "this run's snapshot is not comparable to the previous one. This row "
            "re-baselines the site; no compliance-action-level change is implied.",
        )

    old_n, new_n = old.get("n", 0), new.get("n", 0)
    if old.get("truncated") or new.get("truncated"):
        # A count-only note would describe NOTHING when N records are wholly
        # replaced by N different ones. The truncated form keeps a per-row
        # digest multiset precisely so the row-level magnitude of the change
        # survives even when its content can't — use it when both sides have one.
        rows_note = ""
        old_d, new_d = _digest_counter(old), _digest_counter(new)
        if old_d is not None and new_d is not None:
            gone, arrived = sum((old_d - new_d).values()), sum((new_d - old_d).values())
            if gone or arrived:
                rows_note = f"; {arrived} row(s) appeared, {gone} row(s) disappeared"
        return (
            f"changed — {old_n} -> {new_n} compliance-action record(s){rows_note} "
            "(snapshot too large to store in full; no field-level diff available)",
            "This site's compliance-action list is too large to persist in full "
            "in one Sheet cell, so only per-record digests were stored. The "
            "record counts changed as shown above; review MiEnviro directly for "
            "detail.",
        )

    fields = tuple(new_fields or old_fields or nc.COMPLIANCE_ACTION_FIELDS)
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
            f"Compliance Actions Watch tab's Snapshot JSON for this row is complete."
        )

    if new_n and not old_n:
        note = (f"FIRST COMPLIANCE ACTION(S) RECORDED — this site had none on "
                f"file (now {new_n})")
    elif old_n and not new_n:
        note = (f"ALL COMPLIANCE ACTIONS NO LONGER LISTED — this site had "
                f"{old_n} on file, now none")
    elif not lines:
        note = "changed (no record-level diff — see snapshot)"
    else:
        parts = []
        if added:
            parts.append(f"{sum(added.values())} compliance-action record(s) added/updated")
        if removed:
            parts.append(f"{sum(removed.values())} compliance-action record(s) removed/superseded")
        note = "; ".join(parts)
        if dropped:
            note += f" ({dropped} change line(s) summarized in the email)"
    return note, "\n".join(lines)


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested. All EGLE-derived text
    lands HERE, in the body — never in the subject line (see run())."""
    shown = body or "(no further detail — see the Compliance Actions Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills nSITE COMPLIANCE ACTIONS list changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's nSITE Compliance Actions profile "
        "— the formal actions the regulator takes in response to a violation "
        "(Violation Notices, Consent Orders, Consent Judgments). It trip-wires "
        "a brand-new compliance action the moment EGLE records one under this "
        "site, or an existing action's status advancing (e.g. a Violation "
        "Notice moving from Issued to Closed). It makes NO judgment about which "
        "status is good or bad — EGLE's status vocabulary is a multi-state "
        "lifecycle, so read the change in MiEnviro directly for full context.\n"
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
        sw.append_compliance_actions_watch_row(sheets, sheet_id, today, key, label,
                                                "baseline", new_hash,
                                                "initial snapshot (no alert)", _now(),
                                                snap_json)
        print(f"[nsite-compliance-actions-watch] {label}: baseline recorded "
              f"({new_hash}, {snap.get('n', 0)} record(s)).")
        return "baseline", None

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-compliance-actions-watch] {label}: unchanged ({new_hash}).")
        return "unchanged", None

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_compliance_actions_change(old_snap, snap)
    sw.append_compliance_actions_watch_row(sheets, sheet_id, today, key, label,
                                           "changed", new_hash, note, _now(), snap_json)
    print(f"[nsite-compliance-actions-watch] {label}: CHANGED "
          f"({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is alerting
    # for THIS site only, so a failure in either step is reported rather than
    # raised, and can never abort run()'s other sites.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-compliance-actions-watch] {label}: change recorded but alert "
              f"body FORMATTING failed: {e}")
        return "changed", f"body formatting failed: {e}"
    try:
        # Subject carries ONLY the maintainer-authored label from nsite_sites —
        # no EGLE-derived text ever reaches an email header.
        ea.send_email(f"[Compliance Actions watch] {label} changed", email_body, cfg,
                      recipients=recipients)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-compliance-actions-watch] {label}: change recorded but alert "
              f"email FAILED (the change IS durable in the Compliance Actions Watch "
              f"tab — this run exits non-zero so the lost notification is visible): {e}")
        return "changed", f"send failed: {e}"
    return "changed", None


def run() -> int:
    cfg = load_config()
    # Computing `budget`/`fields` even when the watch is disabled costs
    # nothing — both are pure, side-effect-free, and run_profile_watch's own
    # enabled gate still short-circuits before any Sheets/network call
    # either way.
    ccfg = cfg.get("nsite_compliance_actions") or {}
    budget = min(int(ccfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET),
                 HARD_SHEETS_CELL_LIMIT - 1000)
    fields = diff_fields(cfg)
    return nwc.run_profile_watch(
        cfg=cfg,
        log_prefix="[nsite-compliance-actions-watch]",
        config_key="nsite_compliance_actions",
        key_prefix="ca",
        label_prefix="nSITE Compliance Actions",
        unit_noun="compliance action(s)",
        tab_name=sw.TAB_COMPLIANCE_ACTIONS,
        ensure_tabs_fn=sw.ensure_compliance_actions_tabs,
        last_snapshots_fn=sw.last_compliance_actions_snapshots,
        is_due_fn=_is_due,
        fetch_fn=nc.fetch_site_compliance_actions,
        build_snapshot_fn=lambda rows: compliance_actions_snapshot(rows, fields),
        diff_and_record_fn=functools.partial(_diff_and_record, budget=budget),
    )


if __name__ == "__main__":
    sys.exit(run())
