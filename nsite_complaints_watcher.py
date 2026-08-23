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
This whole structure serializes to well under 1,000 chars even at N2688's
volume — there is no budget-degradation CASCADE here (unlike Violations'
_cell_payload), only a defensive truncate-the-window clamp
(_cell_payload below) as insurance against a pathological config bump, kept
structurally separate from the multiset idiom because its shape doesn't
match: there is no `counted_rows` to degrade to a digest form.

WHY THE WINDOWED "latest" FIELD CAN NAME NEW COMPLAINTS EXACTLY, MOST OF THE
TIME: `latest` is the top-K by received_date, both today and at the last
recorded snapshot. If FEWER than K complaints arrived since the last check —
true almost always, given N2688's trailing-365-day rate is ~60/year, roughly
one every six days, against a default K=50 — then the newly-visible refs in
`latest` (this run's window minus last run's window) are EXACTLY the newly-
arrived complaints: the K-minus-the-new-count carried-over entries are by
construction the same entries that were already in the old window, so they
cancel out of the set difference, leaving only the genuinely new ones. This
is verified, not assumed — summarize_complaints_change() below cross-checks
that arithmetic (the windowed diff's size must equal the count delta) before
ever presenting it as exact, and falls back to an honest, un-named "count
changed, more than the window can confirm — see nSITE" message the moment
that check fails (a burst exceeding the window, or overlapping adds and
removals in the same period). N2688's own history contains exactly this
burst case — 246 complaints arrived on a single day in 2019 — so the
fallback path is real insurance, not a hypothetical.

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
# a tunable. The complaints fingerprint never approaches it (see module
# docstring: {n, hash, latest[K]} serializes to well under 1,000 chars even at
# N2688's 6,396 records), so this budget is defensive insurance against a
# pathological `latest_window` config bump, NOT a live concern the way it was
# for Violations' RA (299 records, 24,884 chars counted).
HARD_SHEETS_CELL_LIMIT = 50000
DEFAULT_SNAPSHOT_CHAR_BUDGET = 45000

# How many of the most-recently-received complaints the snapshot keeps —
# both for the windowed exact-diff (see module docstring) and as fallback
# display context when the exact diff can't be established. 50 against
# N2688's trailing-365-day rate of ~60/year (~0.16/day) gives roughly 300
# days of margin before a normal filing pace could exceed the window; the
# site's own history contains a real burst (246 in one day, 2019) the exact
# diff explicitly detects and declines to name rather than mis-describes.
DEFAULT_LATEST_WINDOW = 50


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_date() -> date:
    return (datetime.now(_ET) if _ET else datetime.now()).date()


def _load_json(raw: str, fallback):
    """Parse a stored snapshot cell, falling back for anything that isn't a
    JSON OBJECT — not merely for anything unparseable. A bare scalar (`0`,
    `null`, `true`) parses fine but is not a mapping, and every downstream
    reader does `"hash" not in old` / `old.get(...)`, which raises TypeError
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
    nsite_complaints.enabled is set) has a direct unit test. Mirrors
    nsite_compliance_actions_watcher._should_run."""
    if not (cfg.get("nsite_complaints") or {}).get("enabled"):
        return False, "nsite_complaints.enabled is false — skipping (no-op)."
    return True, ""


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
    every run. See summarize_complaints_change for how two consecutive
    `latest` windows combine with `n` to name new complaints exactly when
    fewer than K arrived between runs.

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


def _window_refs(snap: dict) -> dict[str, str]:
    """{ref_num: received_date} for a snapshot's `latest` window, tolerant of
    a missing/malformed field (an old or unreadable stored snapshot) — callers
    treat an empty result the same as "no window information available",
    which degrades summarize_complaints_change to its honest fallback rather
    than raising."""
    out: dict[str, str] = {}
    for entry in snap.get("latest") or []:
        try:
            ref, received = entry[0], entry[1]
        except (IndexError, TypeError):
            continue
        if ref:
            out[ref] = received
    return out


def summarize_complaints_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two complaint snapshots.
    Pure — unit-tested.

    Handles, in order, five cases a bare count-and-hash comparison would
    misreport:
      1. the previous snapshot is MISSING or UNREADABLE — which must never be
         confused with "the site had zero complaints",
      2. zero -> some / some -> zero, the single highest-value alert for a
         site whose baseline is empty,
      3. growth (n increased) where the windowed diff is PROVABLY exact —
         named, with ref numbers and dates,
      4. growth where the windowed diff cannot be trusted (a burst exceeding
         the window, or the count delta and the windowed diff disagree,
         meaning simultaneous adds and removals happened) — an honest,
         un-named count-only note,
      5. a decrease (n went down) — always labeled a removal/withdrawal,
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
        latest_lines = "\n".join(
            f"+ NEW      {ref} (received {received or 'unknown date'})"
            for ref, received in new.get("latest", [])
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
        delta = new_n - old_n
        old_window, new_window = _window_refs(old), _window_refs(new)
        added = sorted(set(new_window) - set(old_window))
        # The windowed diff is trustworthy ONLY when it's internally
        # self-consistent: the delta the COUNT reports must exactly equal how
        # many refs are newly visible in the window, AND that must be fewer
        # than the window itself (otherwise a burst could exceed the window
        # and we'd silently under-report). Either check failing means either
        # a burst (delta >= window) or simultaneous adds+removals inside the
        # window (the two numbers disagree) — both get the honest fallback
        # rather than a confidently wrong name-the-complaint claim.
        window_size = new.get("latest_window") or len(new.get("latest") or []) or DEFAULT_LATEST_WINDOW
        if added and len(added) == delta and delta < window_size:
            lines = "\n".join(
                f"+ NEW      {ref} (received {new_window.get(ref) or 'unknown date'})"
                for ref in added
            )
            return (
                f"{delta} new complaint(s) — count went {old_n} -> {new_n}",
                lines,
            )
        return (
            f"{delta} new complaint(s) likely — count went {old_n} -> {new_n} "
            f"(the {window_size} most recently-received complaints on file "
            f"can't confirm all of them — see nSITE for the complete list)",
            "Most recent complaints currently on file (context only — not "
            "necessarily an exhaustive list of what's new):\n" + "\n".join(
                f"  {ref}  (received {received or 'unknown date'})"
                for ref, received in new.get("latest", [])[:10]
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
        "trigger for an inspection. It trip-wires a brand-new complaint the "
        "moment EGLE records one under this site. It makes NO judgment about "
        "a complaint's substance — only that EGLE recorded one — so read the "
        "change in MiEnviro directly for full context. At high volume (this "
        "profile's largest site carries thousands of historical records), an "
        "exact list of what's new is only possible when fewer complaints "
        "arrived since the last check than this watch's recent-window size; "
        "beyond that it reports the count change and points you at nSITE.\n"
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
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[nsite-complaints-watch] {reason}")
        return 0

    ccfg = cfg.get("nsite_complaints") or {}
    recipients = ccfg.get("recipients") or None  # None -> full alert_recipients list
    # Clamped, because `snapshot_char_budget` sits in config.yml directly under
    # a comment naming the 50,000 cap — so "raise it a bit" is a plausible edit,
    # and any value at or above the cap would disable the truncation guard
    # entirely and hand the site a permanently rejected write.
    budget = min(int(ccfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET),
                 HARD_SHEETS_CELL_LIMIT - 1000)
    # Clamped to at least 1: a window of 0 would make EVERY growth event look
    # like a burst (delta < window_size can never hold), silently disabling
    # the named-diff path entirely rather than just shrinking its margin.
    latest_window = max(1, int(ccfg.get("latest_window") or DEFAULT_LATEST_WINDOW))
    # Resolve the working site list by joining the shared identity registry
    # (nsite_sites, ADR 022) with THIS profile's own cadence map. A `tiers` srn
    # absent from the registry is a config error — KeyError raises naturally,
    # on purpose (ADR 022): a loud failure on a config typo is correct, a
    # silently-unwatched site is not.
    registry = {s["srn"]: s for s in cfg.get("nsite_sites") or []}
    sites = [
        {**registry[srn], "poll": poll}
        for srn, poll in (ccfg.get("tiers") or {}).items()
    ]

    # If alerting is ALREADY known to be impossible, stop before touching
    # anything. Writing a row here would advance the stored hash, so tomorrow's
    # run would compare equal, report "unchanged", and never retry — the
    # notification gone permanently even after the secret was fixed. Nothing is
    # lost by stopping: the complaints are still in nSITE, so the next healthy
    # run records AND alerts on them.
    alerting_ok, alerting_error = alerting_is_configured(cfg, recipients)
    if not alerting_ok:
        print(f"[nsite-complaints-watch] {alerting_error} — aborting BEFORE "
              f"any fetch or write. A change found now could not be emailed, "
              f"and recording it would advance the stored hash so the next "
              f"run would report 'unchanged' and never retry. Nothing is "
              f"lost: fix the configuration and the next run records and "
              f"alerts normally.")
        return 1

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_complaints_tabs(sheets, sheet_id)

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
            print(f"[nsite-complaints-watch] {srn}: unrecognized poll cadence "
                  f"{cadence!r} ({e}) — treating as due (fail-safe).")
            due = True
        if due:
            due_sites.append(site)
        else:
            print(f"[nsite-complaints-watch] {srn}: not due today "
                  f"({cadence} cadence) — skipping.")
            counts["skipped"] += 1

    # ONE tab read for every due site, not one per site. Two reasons, both
    # load-bearing: it cuts the read count (less throttling exposure), and
    # last_complaints_snapshots RAISES on a read failure instead of returning
    # a per-key None. A swallowed read error here would make every site look
    # never-seen, write fresh "baseline" rows, and — because the tab is
    # append-only with last-write-wins — PERMANENTLY erase an un-alerted
    # change rather than deferring it. So a read failure aborts before any
    # write, leaving last run's state intact and correct.
    try:
        last_by_key = sw.last_complaints_snapshots(
            sheets, sheet_id, [f"cmplt:{s['srn']}" for s in due_sites])
    except Exception as e:  # noqa: BLE001
        print(f"[nsite-complaints-watch] could not read the Complaints Watch "
              f"tab ({type(e).__name__}: {e}) — aborting before any DATA row "
              f"is written, so no site can be spuriously re-baselined.")
        return 1

    for site in due_sites:
        # One try per site, so NOTHING (a fetch failure, an oversized or
        # rejected Sheets write, a malformed stored snapshot, a malformed
        # registry entry) can abort the run and silently drop every site
        # queued after this one.
        try:
            srn, name, nsite_id = site["srn"], site["name"], site["id"]
            key = f"cmplt:{srn}"
            label = f"nSITE Complaints — {name} ({srn})"
            last = last_by_key.get(key)

            try:
                complaints = nc.fetch_site_complaints(session, nsite_id)
                print(f"[nsite-complaints-watch] {label}: fetched "
                      f"{len(complaints)} complaint(s).")
            except nc.NsiteStructuralError as e:
                # NOT transient, so it must NOT take the skip-and-warn path
                # below: nSITE having changed the response shape (e.g. started
                # paging — the most likely profile to trip this given its
                # record count) would otherwise fail identically every single
                # day behind a green build. Checked before the base class.
                counts["fetch_failed"] += 1
                exit_code = 1
                print(f"[nsite-complaints-watch] {label}: STRUCTURAL break in "
                      f"nSITE's response — this will NOT fix itself and needs "
                      f"code changes; failing loudly rather than going quiet: {e}")
                continue
            except nc.NsiteFetchError as e:
                counts["fetch_failed"] += 1
                if last is not None:
                    print(f"[nsite-complaints-watch] {label}: fetch failed, "
                          f"skipping this run (baseline preserved, not "
                          f"diffed): {e}")
                else:
                    print(f"[nsite-complaints-watch] {label}: NO BASELINE and "
                          f"fetch failed (failing loudly so activation "
                          f"surfaces it): {e}")
                    exit_code = 1
                continue

            snap = complaints_snapshot(complaints, latest_window)
            result, alert_error = _diff_and_record(
                sheets, sheet_id, today, key, label, snap, cfg, recipients, last, budget)
            counts[result] += 1
            if alert_error:
                counts["alert_failed"] += 1
                exit_code = 1
        except Exception as e:  # noqa: BLE001 — isolate this site, keep the run going
            print(f"[nsite-complaints-watch] {site.get('srn', '?')}: UNEXPECTED "
                  f"failure, continuing with the remaining sites: "
                  f"{type(e).__name__}: {e}")
            counts["failed"] += 1
            exit_code = 1

    print(f"[nsite-complaints-watch] done — {counts['changed']} changed, "
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today, {counts['fetch_failed']} fetch-failed, "
          f"{counts['failed']} errored, {counts['alert_failed']} change(s) recorded "
          f"but NOT emailed (across {len(sites)} site"
          f"{'' if len(sites) == 1 else 's'}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
