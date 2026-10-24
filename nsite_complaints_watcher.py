"""
nsite_complaints_watcher.py — runs daily, watching EGLE's nSITE COMPLAINTS
profile (citizen/agency reports against a facility — often the trigger for an
inspection) for every site in config.yml's `nsite_sites` registry that has a
`nsite_complaints.tiers` entry. Each site is actually fetched+diffed at its
own `poll` cadence (daily/biweekly/quarterly), not every run. Standalone +
self-terminating, the same shape as nsite_compliance_actions_watcher.py /
nsite_violations_watcher.py / nsite_permits_watcher.py. See
docs/decisions/031-nsite-complaints-watch.md.

WHY: this is the 4th of the six originally-unpolled nSITE profiles (after
Submissions/Violations/Compliance Actions; Evaluations and Permits landed
after it was staged). For Arbor Hills (N2688) it is the single highest-count
profile in the whole nSITE set — 6,396 records, live-fetched 2026-08-22 — and
that is exactly why this module's SHAPE differs from every sibling above it.

ONE item per site — `cmplt:<srn>` (e.g. "cmplt:N2688") — derived from ONE
fetch per site, so each item's fetch failure is independent (no ROP-style
batching), matching every sibling.

WHAT IT DOES per site (mirrors nsite_compliance_actions_watcher for the
orchestration shape; the snapshot/diff CONTENT below is new):
  - build a canonical fingerprint of the site's complaint list,
  - compare to the last fingerprint recorded in the "Complaints Watch" tab,
  - FIRST sighting -> record a silent "baseline" row (no alert), EVEN AT
    N2688'S 6,396 RECORDS — no flood on first enable, verified by test,
  - fingerprint changed -> record a "changed" row THEN email an alert,
  - fingerprint unchanged -> no-op.

WHY THIS PROFILE CANNOT REUSE ANY SIBLING'S STORAGE VERBATIM (the feasibility
gate's finding, 2026-08-22, all 19 sites live-fetched): every prior nSITE
watch persists SOME form of a per-record snapshot in the Sheet cell — a
run-length-counted multiset (Violations/Compliance Actions) or a ref-number-
keyed map, itself budget-degraded to per-record digests at volume
(Evaluations). Both approaches were measured against N2688's real 6,396
complaints before writing any storage code:
  - full per-record dicts:               870,156 chars (17x the 50,000 cap)
  - a minimal {ref_num: [fields]} map:    422,436 chars (8.4x the cap)
  - just the ref-number keys, no values:   89,844 chars (still ~1.8x the cap)
  - a per-record DIGEST set (the exact machinery Violations/Compliance
    Actions degrade to under budget pressure): 102,336 chars — STILL over
    the cap, and for a reason specific to this profile: Violations'
    degradation works because EGLE genuinely repeats violation rows (RA's
    299 records collapsed to 108 distinct tuples, a 64% reduction); Arbor
    Hills' complaints show ZERO such repetition (6,396 of 6,396 records are
    distinct). The multiset/digest machinery this repo has reused four times
    running does not solve THIS profile at N2688's scale, full stop.

THE DESIGN THIS FORCES: a fingerprint that is small BY CONSTRUCTION, not
degraded into smallness after the fact. `complaints_snapshot()` below stores
exactly three things per site:
  - `n`             — the total record count (cheap, exact, always available),
  - `hash`           — sha256 of the SORTED submSubmRefNum set (NOT a hash of
                       full field tuples — ref numbers carry no UTC offset,
                       so this is immune to the twice-yearly EDT/EST flip that
                       would otherwise re-string every date and false-fire a
                       6,396-record "changed" alert every spring and fall;
                       see nsite_client._parse_egle_date's docstring for why
                       every sibling normalizer treats this as load-bearing),
  - `latest`         — the K most-recently-received complaints (ref_num +
                       received_date pairs), K from `nsite_complaints.
                       latest_window` (default 50).
This whole structure serializes to ~1,600 chars at N2688's volume with the
default 50-entry window (measured, not estimated) — a ~28x margin under the
45,000-char budget, not merely under the 50,000-char Sheets cap. There is no
budget-degradation CASCADE here (unlike Violations' _cell_payload), only a
defensive truncate-the-window clamp
(_cell_payload below) as insurance against a pathological config bump, kept
structurally separate from the multiset idiom because its shape doesn't
match: there is no `counted_rows` to degrade to a digest form.

WHY THE WINDOWED "latest" FIELD IS CONTEXT ONLY, NEVER A NAMED DIFF: an
earlier version of this module tried to name specific new complaints exactly
whenever fewer than K arrived since the last check, treating `latest` (this
run's window minus last run's window) as if it could only contain genuinely
new refs. Three independent Step 5 review rounds each found a DIFFERENT way
that assumption breaks:
  - round 1: a removal of an in-window complaint can promote an older,
    previously-invisible survivor into the window; if the counts happened to
    line up, that survivor was misreported as new.
  - round 2: round 1's fix (excusing the window's oldest `delta` entries by
    POSITION, on the theory growth alone would push exactly those out) was
    itself incomplete — a removal of one of those excused bottom entries
    could still promote a survivor undetected.
  - round 3: a value-based fix closed the removal-promotion class for good
    (verified: under pure growth, anything displacing an old window member
    must rank above everything it displaces) — but found a THIRD, different
    mechanism reaching the same failure: an EXISTING complaint's
    received_date being corrected by EGLE (e.g. blank -> populated) re-sorts
    it into the window without any removal, and `hash` — deliberately over
    the ref-number SET only, to stay immune to the EDT/EST date-offset flip —
    does not change when only a date is corrected, so this is invisible to
    the primary change signal too.
Each fix closed the round that found it and was defeated by the next, which
is the pattern the round-3 review correctly read as structural rather than a
missed edge case: this snapshot never stores old's full ref-number set (the
entire reason it fits one Sheets cell), so for any ref newly visible in
`latest` there is no stored fact able to distinguish "this complaint is
genuinely new" from "this complaint existed all along outside the visible
window and something just made it visible." No further check on this data
closes that gap, and a CONDITIONAL caveat doesn't help either — which alerts
are at risk can't be told apart from which aren't. So `latest` is exposed
purely as recent-context (a human reads it and cross-checks nSITE), and
summarize_complaints_change() below never claims a specific ref is new for
a nonzero old baseline — only that the count changed. The one case naming IS
sound is old_n == 0 (a confirmed-empty baseline): the old window was empty
too, so there is no possibility of an old survivor hiding outside it, and
every ref in `latest` is unambiguously new. See ADR 031's residual risks.

WHY A COUNT DECREASE IS NEVER MISREAD AS "NEW": if `n` decreases, the note
says a complaint no longer appears (withdrawn or reclassified), never "new" —
the same mitigation nsite_violations_watcher/nsite_compliance_actions_watcher
apply for their own multiset diffs. UNLIKE those two, this module cannot name
WHICH complaint disappeared (no removal window is kept — see the module's
`latest` design above), so the note is honestly count-only for a removal.

NO SEVERITY JUDGMENT. A complaint's mere existence is the signal this watch
exists to surface (unlike Violations/Compliance Actions, this profile carries
no status/lifecycle field at all in its four-field schema) — it never
characterizes a complaint as founded or unfounded, only that EGLE recorded
one.

FAILURE HANDLING, in three layers (identical to every nSITE sibling watcher):

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
    un-alerted change on this append-only, last-write-wins tab. This matters
    MOST at N2688: a spurious re-baseline there would silently erase the
    site's entire un-alerted complaint history in one write.
  - A CHANGE THAT WAS RECORDED BUT NOT EMAILED sets a non-zero exit too — the
    durable row surviving is not success for a stream whose entire
    deliverable is the alert, and the advanced hash means the next run
    reports "unchanged" and never retries.

GATED on nsite_complaints.enabled, which ships FALSE: a brand-new poller
against a live external system built unattended, so flipping it on is
explicitly a separate human step (overnight-coder Step 3).

TIERED CADENCE: `_is_due` is IMPORTED from nsite_submissions_watcher rather
than reimplemented — it is already generic over (cadence, srn, today). THE
TIERS THEMSELVES are this profile's own, live-fetched across all 19 sites on
2026-08-22 and justified by the NEW-complaint RATE (not the raw 6,396): only
N2688 shows any recent filing activity (60 in the trailing 365 days, ~0.16/
day) -> daily. RA (5 total, none since 2023) and WRD/P1488 (0 complaints on
file, but an open JPA/PFOS matter and a live ROP comment window respectively
— the same harm-asymmetry override every sibling profile applies to these two
sites) -> biweekly. Every other site, including COMP's single 2014 record,
has zero recent or ongoing complaint activity -> quarterly, insurance only.
See ADR 031 for the full table.

NO DRIVE / OAUTH (same scope call as every other watch, ADR 012): the
deliverable is the ALERT + the durable Sheet row.
"""
from __future__ import annotations

import functools
import hashlib
import json
import sys

# dc/ea/nc/sw stay imported here even though run() itself now calls into
# nsite_watch_common for the Sheets/email/fetch plumbing: both this module
# and nsite_watch_common.py import the SAME singleton modules, so a test's
# monkeypatch.setattr(cw.dc, "sheets_service", ...) (etc.) still reaches the
# real call inside nsite_watch_common.run_profile_watch unchanged — see
# tests/test_nsite_complaints.py's _wire(). ea is also still used directly
# by this module's own _diff_and_record (ea.send_email).
import drive_client as dc
import email_alerts as ea
import sheet_writer as sw
import nsite_client as nc
from config_loader import load_config
import nsite_watch_common as nwc
# _now/_today_date/_load_json/alerting_is_configured are now shared -- SIX
# of the 16/8/12/6 copies of these helpers that existed repo-wide (ROP/MMD/
# PFAS-PWS/RIDE/Submissions/the other watchers keep their own; this
# consolidation is scoped to the six nSITE profile watchers only; this
# profile never had its own `_row_digest`, unlike 5 of its siblings);
# re-exported here so this module keeps its own names unchanged
# (cw._now()/cw._load_json(...)/cw.alerting_is_configured(...) all still
# work exactly as before — see the
# existing per-watcher identity tests).
from nsite_watch_common import (  # noqa: F401 -- re-exported, not just used
    _now, _today_date, _load_json, alerting_is_configured,
)
# Imported in place, NOT copied or moved (same as before this refactor): a
# pure function of (cadence, srn, today) with nothing profile-specific in
# it, kept as a module-global HERE (not forwarded through
# nsite_watch_common) so cw._is_due stays the identical object
# nsite_submissions_watcher._is_due IS (see its own identity test) and a
# monkeypatch.setattr(cw, "_is_due", ...) in a test still reaches run()'s
# behavior via the fresh look-up run() passes to run_profile_watch().
from nsite_submissions_watcher import _is_due

# A Google Sheets cell holds at most 50,000 characters — a hard API limit, not
# a tunable. The complaints fingerprint never approaches it (see module
# docstring: {n, hash, latest[K]} serializes to ~1,600 chars, measured, even at
# N2688's 6,396 records), so this budget is defensive insurance against a
# pathological `latest_window` config bump, NOT a live concern the way it was
# for Violations' RA (299 records, 24,884 chars counted).
HARD_SHEETS_CELL_LIMIT = 50000
DEFAULT_SNAPSHOT_CHAR_BUDGET = 45000

# How many of the most-recently-received complaints the snapshot keeps as
# display context on a change alert (see module docstring — this is NEVER
# diffed to name a specific complaint as new, for a nonzero old baseline).
# 50 is generous relative to N2688's trailing-365-day rate of ~60/year
# (~0.16/day) — plenty of recent complaints shown as a starting point for
# checking nSITE, without implying the list is exhaustive.
DEFAULT_LATEST_WINDOW = 50


def _should_run(cfg: dict) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact
    bug this guards (the watch doing real work / emailing before
    nsite_complaints.enabled is set) has a direct unit test. Thin wrapper
    over nsite_watch_common.should_run(); see its docstring."""
    return nwc.should_run(cfg, "nsite_complaints")


# ---------------------------------------------------------------------------
# Snapshot / diff (pure)
# ---------------------------------------------------------------------------


def complaints_snapshot(rows: list[dict], latest_window: int = DEFAULT_LATEST_WINDOW) -> dict:
    """Canonical fingerprint of one site's complaint list — SMALL BY
    CONSTRUCTION, not a degraded per-record encoding. See the module
    docstring for the measured sizes that rule out every per-record approach
    at N2688's scale.

    `hash` is sha256 over the SORTED submSubmRefNum set (bare ref numbers,
    not full field tuples) — deliberately narrower than every sibling
    watcher's whole-record hash, for two reasons: (1) ref numbers carry no
    UTC offset, so the hash is immune to the EDT/EST flip that would
    otherwise re-string `received_date` on all 6,396 records twice a year and
    false-fire a "changed" alert each time; (2) this profile's other two
    fields (form_type, program_area) are set once at filing and never
    observed to change across the double-fetch in the 2026-08-22 spike, so
    they carry no live diff value worth the correctness risk of including
    unnormalized dates in the hash basis.

    `latest` is the K most-recently-received complaints (by received_date,
    ties broken by ref_num for determinism), K = `latest_window`. This is NOT
    a degraded form of some larger stored structure — it is the whole
    per-record content this snapshot ever keeps, computed fresh from `rows`
    every run. It is display CONTEXT only — see summarize_complaints_change
    and the module docstring for why it is never diffed to name a specific
    complaint as new once the site has a nonzero baseline.

    An EMPTY record set is a valid snapshot — for 17 of the 19 watched sites,
    "no complaints on file" IS the baseline and the first complaint appearing
    is the change."""
    refs_sorted = sorted(r.get("ref_num") or "" for r in rows if r.get("ref_num"))
    hash_ = hashlib.sha256(
        json.dumps(refs_sorted, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    ordered = sorted(
        rows,
        key=lambda r: (r.get("received_date") or "", r.get("ref_num") or ""),
        reverse=True,
    )
    latest = [[r.get("ref_num") or "", r.get("received_date") or ""] for r in ordered[:latest_window]]
    return {"n": len(rows), "hash": hash_, "latest": latest, "latest_window": latest_window}


def snapshot_hash(snap: dict) -> str:
    """The stored "Snapshot Hash" column value. UNLIKE every sibling watcher
    (which hashes the WHOLE canonical snapshot structure as a compact
    change-fingerprint), this returns `snap["hash"]` directly — the
    ref-number-SET hash IS already a complete "did the underlying complaint
    set change" fingerprint on its own: `n` and `latest` are both derived
    from the same `rows` `hash` is computed from, so nothing about them can
    change without `hash` also changing. A second, redundant whole-structure
    hash would add nothing. Kept as its own function anyway (not inlined at
    the two call sites) purely for call-site parity with every sibling
    watcher's `_diff_and_record`, which keeps this module's orchestration
    code readable against the four it was rebased from."""
    return snap.get("hash", "")


def _cell_payload(snap: dict, budget: int = DEFAULT_SNAPSHOT_CHAR_BUDGET) -> str:
    """Serialize a snapshot for the Sheet's Snapshot JSON cell.

    DELIBERATELY NOT Violations'/Compliance Actions' _cell_payload: this
    snapshot has no `counted_rows` to degrade into a digest multiset — that
    machinery's shape does not match what this module stores (see the module
    docstring for why no per-record encoding survives N2688's scale in the
    first place). The clamp here fits this snapshot's actual shape instead:
    if a config-bumped `latest_window` ever pushed the serialized form over
    budget (verified-inert at every value up to several hundred — `latest`
    entries are ~30-40 chars each, so budget holds until roughly
    latest_window ~= 1,000), trim the `latest` list from the end (the OLDEST
    entries first — `latest` is already sorted most-recent-first, so this
    preserves exactly the entries summarize_complaints_change needs most)
    until it fits, rather than reject the write outright. `n` and `hash`
    are NEVER trimmed — they are O(1) regardless of window size and are what
    the "did anything change" fast path in run() depends on."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    # `latest_truncated: true` is added to EVERY probe inside the loop, not
    # only to the final return — checking size without it and adding it
    # afterward would let that key's own ~25 chars push the result back over
    # budget, silently defeating the guard it exists to enforce.
    trimmed = dict(snap)
    trimmed["latest_truncated"] = True
    latest = list(trimmed.get("latest") or [])
    while latest and len(json.dumps(
        {**trimmed, "latest": latest}, sort_keys=True, ensure_ascii=False,
    )) > budget:
        latest = latest[:-1]
    trimmed["latest"] = latest
    return json.dumps(trimmed, sort_keys=True, ensure_ascii=False)


def summarize_complaints_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two complaint snapshots.
    Pure — unit-tested.

    Handles, in order, four cases a bare count-and-hash comparison would
    misreport:
      1. the previous snapshot is MISSING or UNREADABLE — which must never be
         confused with "the site had zero complaints",
      2. zero -> some / some -> zero, the single highest-value alert for a
         site whose baseline is empty — the only case where naming specific
         refs as new is actually sound (old_n == 0 means the old window was
         also empty, so there is no possibility of an old survivor hiding
         outside it),
      3. growth (n increased) with a nonzero old baseline — an honest,
         un-named count-only note. Naming specific refs here was tried and
         reverted: three Step 5 review rounds each found a different way a
         windowed top-K snapshot can misname an old survivor as new (see the
         comment at the growth branch below and ADR 031's residual risks),
      4. a decrease (n went down) — always labeled a removal/withdrawal,
         NEVER misread as "new", and never claims to name which one (no
         removal window is kept)."""
    if "hash" not in old:
        return (
            "changed — the previous snapshot was missing or unreadable, so no "
            "diff could be computed (this row re-baselines the site; it does "
            "NOT mean the site previously had no complaints)",
            "The last stored snapshot for this site could not be read — the "
            "Snapshot JSON cell was empty, cleared, or malformed. This row "
            "restores a good baseline. Check the Complaints Watch tab's "
            "history, and review MiEnviro directly for anything that changed "
            "in the meantime.",
        )

    old_n, new_n = old.get("n", 0), new.get("n", 0)

    if new_n and not old_n:
        shown = new.get("latest", [])
        latest_lines = "\n".join(
            f"+ NEW      {ref} (received {received or 'unknown date'})"
            for ref, received in shown
        )
        # `latest` is capped at the window size — if the true count exceeds
        # what's shown (a zero-baseline site that turns out to have more
        # than K complaints on its very first sighting), say so explicitly
        # rather than silently implying the list below is complete. This
        # mirrors the caveat the >K-growth fallback below already carries.
        if new_n > len(shown):
            latest_lines += (
                f"\n... and {new_n - len(shown)} more not shown here — see "
                f"nSITE for the complete list."
            )
        return (
            f"FIRST COMPLAINT(S) RECORDED — this site had none on file (now {new_n})",
            latest_lines or "(no per-complaint detail available — see the Complaints "
                             "Watch tab's Snapshot JSON.)",
        )
    if old_n and not new_n:
        return (
            f"ALL COMPLAINTS NO LONGER LISTED — this site had {old_n} on file, now none",
            "Every complaint previously on file for this site is gone from "
            "nSITE's response. Review MiEnviro directly — this is unusual "
            "enough that a data or portal issue is at least as likely as a "
            "genuine mass removal.",
        )

    if new_n > old_n:
        # Deliberately NOT a named diff. Three independent Step 5 review
        # rounds each found a different, genuine way for a windowed top-K
        # snapshot to misname an old survivor as new: round 1, a removal
        # promoting a previously-invisible entry into view; round 2, the
        # same promotion at the bottom of the window (the round-1 fix's
        # position-based excuse missed it); round 3, an EXISTING complaint's
        # `received_date` being corrected by EGLE (blank -> populated),
        # which re-sorts it into the window without changing `hash` (hash is
        # deliberately over the ref-number SET only, to stay immune to the
        # EDT/EST date-offset flip) and without any removal at all. Each
        # fix closed the round that found it and was defeated by the next.
        # The reason is structural, not a missed edge case: this snapshot
        # never stores old's full ref-number set (that's the entire point —
        # N2688 has 6,396 records and won't fit one Sheets cell otherwise),
        # so for any ref newly visible in `latest`, there is no stored fact
        # that can distinguish "this complaint didn't exist before" from
        # "this complaint existed all along, outside the visible window,
        # and something just made it visible." No further check on this
        # data can close that gap — see ADR 031's "Residual risks" section.
        # A conditional caveat doesn't help either: which alerts are at risk
        # can't be told apart from which aren't, so the honest count+context
        # note below is used for EVERY growth case, unconditionally.
        shown = new.get("latest", [])[:10]
        return (
            f"complaint count changed — {old_n} -> {new_n} on file "
            "(a windowed diff can't safely name which complaint(s) are new "
            "at this site's volume — see nSITE for the complete list)",
            f"Most recent {len(shown)} complaint(s) currently on file, "
            "context only — NOT a list of what changed:\n"
            + "\n".join(
                f"  {ref}  (received {received or 'unknown date'})"
                for ref, received in shown
            ),
        )

    if new_n < old_n:
        return (
            f"{old_n - new_n} complaint(s) no longer listed — count went "
            f"{old_n} -> {new_n} (removed or withdrawn, not a new complaint; "
            f"which record cannot be identified from the stored snapshot)",
            "A complaint that was previously on file for this site no longer "
            "appears in nSITE's response. Review MiEnviro directly for detail.",
        )

    # new_n == old_n but the hash changed: the ref-number SET differs (one
    # complaint replaced by another) even though the count didn't move.
    return (
        f"complaint record set changed with no net count change ({new_n} on "
        "file) — a complaint may have been added and another removed or "
        "superseded in the same period",
        "The set of complaint reference numbers on file changed, but the "
        "total count did not. Review MiEnviro directly for detail.",
    )


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested. All EGLE-derived text
    lands HERE, in the body — never in the subject line (see run())."""
    shown = body or "(no further detail — see the Complaints Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills nSITE COMPLAINTS list changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's nSITE Complaints profile — "
        "citizen/agency reports filed against this facility, often the "
        "trigger for an inspection. It trip-wires the moment EGLE's record "
        "for this site differs from the last check — a change to the "
        "complaint count, or which complaints are on file even if the count "
        "didn't move. It makes NO judgment about a complaint's substance — "
        "only that EGLE's record changed — so read MiEnviro directly for "
        "full context. This watch deliberately does NOT try to name which "
        "complaint(s) are new: at this profile's volume (its largest site "
        "carries thousands of historical records), no snapshot small enough "
        "to store can distinguish a genuinely new complaint from an existing "
        "one that simply became newly visible, so it reports the change "
        "plus recent context and points you at nSITE for the rest.\n"
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
        sw.append_complaints_watch_row(sheets, sheet_id, today, key, label,
                                        "baseline", new_hash,
                                        "initial snapshot (no alert)", _now(),
                                        snap_json)
        print(f"[nsite-complaints-watch] {label}: baseline recorded "
              f"({new_hash}, {snap.get('n', 0)} record(s)).")
        return "baseline", None

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-complaints-watch] {label}: unchanged ({new_hash}).")
        return "unchanged", None

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_complaints_change(old_snap, snap)
    sw.append_complaints_watch_row(sheets, sheet_id, today, key, label,
                                    "changed", new_hash, note, _now(), snap_json)
    print(f"[nsite-complaints-watch] {label}: CHANGED "
          f"({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is alerting
    # for THIS site only, so a failure in either step is reported rather than
    # raised, and can never abort run()'s other sites.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-complaints-watch] {label}: change recorded but alert "
              f"body FORMATTING failed: {e}")
        return "changed", f"body formatting failed: {e}"
    try:
        # Subject carries ONLY the maintainer-authored label from nsite_sites —
        # no EGLE-derived text ever reaches an email header.
        ea.send_email(f"[Complaints watch] {label} changed", email_body, cfg,
                      recipients=recipients)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-complaints-watch] {label}: change recorded but alert "
              f"email FAILED (the change IS durable in the Complaints Watch "
              f"tab — this run exits non-zero so the lost notification is "
              f"visible): {e}")
        return "changed", f"send failed: {e}"
    return "changed", None


def run() -> int:
    cfg = load_config()
    # Gate BEFORE parsing any config value below: `_should_run` must be the
    # very first thing checked, exactly like every sibling watcher, because
    # `int(...)` on a malformed `snapshot_char_budget`/`latest_window` (a
    # config edit made while the watch is disabled -- Complaints IS the one
    # profile that currently ships disabled) would otherwise raise before
    # run_profile_watch ever gets a chance to no-op on the disabled gate.
    should, reason = _should_run(cfg)
    if not should:
        print(f"[nsite-complaints-watch] {reason}")
        return 0

    ccfg = cfg.get("nsite_complaints") or {}
    budget = min(int(ccfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET),
                 HARD_SHEETS_CELL_LIMIT - 1000)
    # Clamped to at least 1 for a NEGATIVE config value (a typo like -5) —
    # NOT for an explicit 0, which the `or DEFAULT_LATEST_WINDOW` above
    # already redirects to the default (50) before max() ever sees it,
    # since 0 is falsy in Python. Either way there is no correctness issue
    # to guard against, only a degraded-context-list edge case: the
    # count+context note never depends on the window covering the full
    # delta — see summarize_complaints_change.
    latest_window = max(1, int(ccfg.get("latest_window") or DEFAULT_LATEST_WINDOW))
    return nwc.run_profile_watch(
        cfg=cfg,
        log_prefix="[nsite-complaints-watch]",
        config_key="nsite_complaints",
        key_prefix="cmplt",
        label_prefix="nSITE Complaints",
        unit_noun="complaint(s)",
        tab_name=sw.TAB_COMPLAINTS,
        ensure_tabs_fn=sw.ensure_complaints_tabs,
        last_snapshots_fn=sw.last_complaints_snapshots,
        is_due_fn=_is_due,
        fetch_fn=nc.fetch_site_complaints,
        build_snapshot_fn=lambda rows: complaints_snapshot(rows, latest_window),
        diff_and_record_fn=functools.partial(_diff_and_record, budget=budget),
    )


if __name__ == "__main__":
    sys.exit(run())
