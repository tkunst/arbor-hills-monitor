"""
nsite_public_notices_watcher.py — runs daily, watching EGLE's nSITE ACTIVE
PUBLIC NOTICES profile (the formal comment-window announcements — permit
renewals, draft permits, hearings) for every site in config.yml's
`nsite_sites` registry that has an `nsite_public_notices.tiers` entry. Each
site is actually fetched+diffed at its own `poll` cadence (daily/biweekly/
quarterly), not every run. Standalone + self-terminating, the same shape as
nsite_evaluations_watcher.py / nsite_permits_watcher.py. See
docs/decisions/032-nsite-active-public-notices-watch.md.

THIS IS THE SIXTH AND LAST of the six originally-unpolled nSITE profiles
(Documents/Submissions/Violations/Compliance Actions/Evaluations/Permits/
Complaints/Public Notices — eight profiles now covered in total, counting
Documents). Unlike every sibling above, it ships holding a design question
open rather than a settled default — see "THE ROP-OVERLAP QUESTION" below.

WHY THIS PROFILE, AND WHY IT'S DIFFERENT: Active Public Notices are the
highest-value EGLE record type for a public comment window — an announced
notice IS an actionable deadline. But the highest-value instance of that for
the Arbor Hills air facilities (a ROP renewal reaching public comment) is
ALREADY watched by Stream H (rop_client.py / rop_watcher.py, ADR 017) via a
completely different mechanism: a statewide `ROP_Public_Notice.pdf` mention
check for each target SRN. Live sampling of THIS profile found exactly that
overlap is not hypothetical: on 2026-07-24 the one live record across all 19
sites was a P1488 ROP renewal notice; re-sampled on 2026-08-25 (this build),
the one live record was a DIFFERENT ROP renewal notice, this time for N1504
(comment window 2026-08-10..2026-09-09) — and `rop.enabled: true` already
watches N1504 for exactly this event. Two consecutive independent samples,
a year apart in EGLE's own filing calendar, both landing on a ROP notice, is
what makes this profile's marginal value (the NON-ROP notices, other program
areas/types) genuinely uncertain rather than a one-off sampling artifact.

THE ROP-OVERLAP QUESTION (surfaced, not resolved, per the handoff that staged
this build and per overnight-coder.md's "stop for a human on a genuine design
decision"): three options exist for how this watch's alert should relate to
Stream H's —
  1. STANDALONE (what this code does today): poll every site, alert on every
     new/changed notice, full stop. Simplest, but will double-alert on a ROP
     renewal window Stream H already emailed about — recipients get two
     independently-worded emails about the same comment window.
  2. DEDUPE against Stream H: suppress a notice whose window matches an
     already-alerted ROP trip-wire. More correct, needs a shared key between
     two mechanisms not designed to share one (rop_watcher keys on SRN +
     "mentioned in the statewide notice PDF"; this profile keys on a per-
     notice URL id) — not attempted here.
  3. SCOPE to non-ROP notices only: this profile's marginal value is exactly
     what Stream H doesn't cover, so filtering out ROP-type notices before
     alerting would be the cleanest fix IF a reliable field existed to filter
     on. It does NOT: confirmed 2026-08-25 against the API's own
     `lookups.defaultElement.metadata` field-description block (not just the
     one live record) that this profile serves exactly six fields — none of
     them a program-area, permit-type, or facility-category field of any
     kind. The only signal that a notice is ROP-related is free-text
     inspection of `comments` (both live samples say "Renewable Operating
     Permit (ROP)" in prose) — a fragile keyword match, not a structured
     filter, and NOT implemented here.
This module ORIGINALLY shipped doing (1) with disambiguating alert copy (see
format_change_body) so a human reading either email at least knows the two
streams are distinct mechanisms, not that this profile resolves the overlap
— Which of the three Trisha wants was left as an open question for the PR,
not a choice this build made for her.

RESOLUTION (2026-08-26, Trisha-directed): a hybrid, built from (1) and (3).
`_all_changed_notices_look_like_rop` inspects the CHANGED notices' `comments`
text for ROP-renewal language (the same boilerplate phrase, "Renewable
Operating Permit (ROP)," both live records have used) and, if EVERY notice
in a given change matches, suppresses the EMAIL for that change —
`_diff_and_record` still writes the durable Sheet row unconditionally either
way, so nothing is ever silently lost from the audit trail, only the
redundant notification. The check FAILS OPEN: any notice that doesn't
clearly match, any degraded/truncated snapshot, a duplicate-key state, or an
unreadable previous snapshot all mean the email fires — the design
deliberately errs toward an occasional redundant email over ever silently
dropping a real alert. This was chosen over pure standalone (both real
alerts to date would have been redundant with Stream H) and over a Stream H
state-based dedupe (would couple two independently-evolving mechanisms with
different keys — a URL id here vs. "mentioned in a statewide PDF" there —
for uncertain benefit over a simpler, self-contained heuristic). Gated by
`nsite_public_notices.rop_alert_suppression` (defaults true; set false to
revert to pure standalone without a code change). Open Decision 2 (the n=1
evidence base for the diff key itself) is unaffected and remains a disclosed
residual risk — see ADR 032's 2026-08-26 addendum for the full writeup.

ONE item per site — `pubntc:<srn>` (e.g. "pubntc:N2688") — derived from ONE
fetch per site, so each item's fetch failure is independent (no ROP-style
batching).

WHAT IT DOES per site (mirrors nsite_evaluations_watcher/nsite_permits_watcher):
  - build a canonical snapshot of the site's active-public-notice list + hash it,
  - compare to the last snapshot recorded in the "Public Notices Watch" tab,
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert,
  - hash unchanged -> no-op.

WHY A REF-NUMBER-KEYED DIFF, AND WHY THE KEY IS A URL-EMBEDDED ID (NOT
publicNotifExtrnlPublNoticeNum, the field the handoff that staged this build
suggested preferring "if it ever populates"): `publicNotifExtrnlPublNoticeNum`
has been null on BOTH live records seen (2026-07-24 and 2026-08-25) — so it
is not usable as the key today, and it must not become the key CONDITIONALLY
either. If it were preferred-when-present, a notice first seen with it null
(keyed by its URL id) that later has EGLE populate that field would have its
computed key change out from under it mid-lifecycle — the diff would report
the OLD key REMOVED (a false "comment window closed") and the NEW key ADDED
(a false "new comment window opened") for what is really one unchanged
notice. So this profile keys UNCONDITIONALLY on the id embedded in
`publicNotifPnurl` (regex-extracted by nsite_client._extract_notice_id),
which has been present on both live records and carries no such instability.
`ext_num` is still an ORDINARY diffed field, so the day it does populate on
an existing notice, that shows up as a benign "changed" line rather than a
silent miss or a false remove+add pair.

THIS KEY IS UNVERIFIED AT SCALE. Every sibling ref-keyed profile (Evaluations'
evalEvalNum, Permits' prmtPrmtNum) had its uniqueness confirmed against
hundreds of live records. This profile has had exactly ONE live record on
each of two sampling dates — not because sampling was thin, but because the
real population is that small. `_duplicate_key_count` (identical guard to
nsite_evaluations_watcher's) exists here for the SAME reason it does there,
but earns its place more here than anywhere else: if this profile's true key
uniqueness assumption is ever wrong, one record is enough for it to show up
on the very first real multi-record snapshot, and the guard is what stops
that from silently corrupting the diff instead of being reported honestly.

WHY publicNotifComments IS CAPPED IN THE STORED SNAPSHOT (a mitigation named
explicitly in the handoff): unlike Violations'/Compliance Actions'/
Evaluations' cell-budget guard (which only bites at real multi-hundred-record
volumes this profile has never approached), a SINGLE verbose comments field
could threaten the cell on its own regardless of record count — the live
samples are a paragraph (~500-800 chars), but nothing guarantees EGLE never
files a multi-page hearing notice's full text in this field. `_capped_comments`
truncates to COMMENTS_STORED_CHARS and appends a full-text content hash, so a
change beyond the truncation point still changes the stored value (and so
still fires a change alert) even though the verbatim text past that point is
never persisted. One documented simplification versus the handoff's literal
"truncate/hash in storage, keep full text in the email" split: this module
uses ONE capped value for both storage and the alert body, rather than
threading a second, uncapped copy through to email formatting only. At
COMMENTS_STORED_CHARS (4,000) against the longest real sample observed
(~800 chars), this is not a live gap — flagged in ADR 032 as a documented
choice, not hidden.

NO SEVERITY JUDGMENT beyond what's stated above about the ROP-overlap
disambiguation. This profile carries no status field to trip-wire — a new
notice appearing, or an existing one's window/comments changing, IS the
signal; a human reads what it means.

FAILURE HANDLING, in three layers (identical to nsite_evaluations_watcher /
nsite_permits_watcher):

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

GATED on nsite_public_notices.enabled, which still ships FALSE — the
ROP-overlap DESIGN question above is now resolved (see RESOLUTION), but
turning this live poller on against a real external system is a separate,
later human step, same as every other new-source stream in this series
(overnight-coder Step 3).

TIERED CADENCE: `_is_due` is IMPORTED from nsite_submissions_watcher rather
than reimplemented — it is already generic over (cadence, srn, today). The
TIERS THEMSELVES are this profile's own: the three ROP-permit-holding sites
(N2688/N1504/P1488) are daily, since a live comment window has now been
observed at two of the three; every other site is quarterly except RA/WRD/
AHLI (biweekly — RA and WRD both carry other open matters tracked elsewhere
in this monitor, and AHLI is N2688's bare duplicate registration, the same
mis-filing-insurance reasoning Compliance Actions/Violations already apply
to it).

NO DRIVE / OAUTH (same scope call as every other watch, ADR 012): the
deliverable is the ALERT + the durable Sheet row.

Runs daily — its workflow file landed directly into .github/workflows/ (this
build session's SSH key authenticated non-interactively against GitHub, the
same path Streams N/O/P used; see overnight-coder.md Step 4). Harmless while
enabled is false.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
# a tunable. At this profile's observed volume (0-1 records per site) the
# general multi-record budget guard below is inherited insurance, never a
# live concern — see COMMENTS_STORED_CHARS for the guard that actually
# matters at this profile's scale.
HARD_SHEETS_CELL_LIMIT = 50000
DEFAULT_SNAPSHOT_CHAR_BUDGET = 45000

# A single verbose publicNotifComments value could threaten the cell on its
# own regardless of record count — see the module docstring. Generous against
# both live samples observed (~500-800 chars).
COMMENTS_STORED_CHARS = 4000

# How many ADDED/CHANGED/REMOVED lines an alert email prints before
# summarizing the rest. Inherited for parity; at this profile's observed
# volume a single day's real change count is 0-1, so this has never bitten.
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
    nsite_public_notices.enabled is set) has a direct unit test. Mirrors
    nsite_evaluations_watcher._should_run."""
    if not (cfg.get("nsite_public_notices") or {}).get("enabled"):
        return False, "nsite_public_notices.enabled is false — skipping (no-op)."
    return True, ""


def diff_fields(cfg: dict) -> tuple[str, ...]:
    """The field set this run diffs on: every nsite_client.PUBLIC_NOTICE_FIELDS
    except any listed in `nsite_public_notices.exclude_fields` — MINUS
    `notice_id`, which can never be excluded regardless of config (it is the
    structural diff KEY; every snapshot/diff helper below assumes it is
    present, matching nsite_evaluations_watcher.diff_fields' eval_num guard)."""
    excluded = set((cfg.get("nsite_public_notices") or {}).get("exclude_fields") or [])
    excluded.discard("notice_id")
    return tuple(f for f in nc.PUBLIC_NOTICE_FIELDS if f not in excluded)


# ---------------------------------------------------------------------------
# Snapshot / diff (pure)
# ---------------------------------------------------------------------------


def _capped_comments(comments: str) -> str:
    """Cap a notice's comments text to COMMENTS_STORED_CHARS before it enters
    the snapshot, appending a content hash of the FULL text so a change
    beyond the truncation point still changes the stored value (and so still
    fires a change alert) even though the verbatim text past that point is
    never persisted. See the module docstring for why this guard exists
    independent of the general multi-record budget guard every sibling
    profile already carries."""
    comments = comments or ""
    if len(comments) <= COMMENTS_STORED_CHARS:
        return comments
    digest = hashlib.sha256(comments.encode("utf-8")).hexdigest()[:12]
    return comments[:COMMENTS_STORED_CHARS] + f"...[truncated; full-text sha256={digest}]"


def public_notices_snapshot(rows: list[dict], fields: tuple[str, ...]) -> dict:
    """Canonical, hash-stable snapshot of one site's active-public-notice
    list, keyed on `notice_id` (the URL-embedded id — see the module
    docstring for why this, not publicNotifExtrnlPublNoticeNum, is the key).
    Sorted by notice_id for a stable hash regardless of API ordering, encoded
    POSITIONALLY (`fields` header + `[notice_id, *values]` rows), the same
    idiom as nsite_evaluations_watcher.evaluations_snapshot.

    ASSUMES `notice_id` is in `fields` — true for every real caller, since
    this is only ever invoked with diff_fields()'s output (which can never
    exclude it) or a fields tuple built directly from
    nc.PUBLIC_NOTICE_FIELDS."""
    capped = []
    for r in rows:
        d = {f: r.get(f) or "" for f in fields}
        if "comments" in d:
            d["comments"] = _capped_comments(d["comments"])
        capped.append(d)
    keyed = sorted(capped, key=lambda d: d["notice_id"])
    return {
        "fields": list(fields),
        "n": len(keyed),
        "rows": [[d[f] for f in fields] for d in keyed],
    }


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as every sibling nSITE watch.

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
    per-notice digest map if the full form would exceed `budget` chars.

    Inherited from ADR 029's guard (Evaluations) for structural parity across
    every sibling nSITE watch — at this profile's observed volume (0-1
    records per site) this is verified-inert insurance, never a live concern
    (comments is ALREADY capped by public_notices_snapshot before this
    function ever sees it, so the dominant size risk this profile actually
    has is handled upstream). The degraded form keeps `[notice_id, digest]`
    pairs (notice_id stays VISIBLE) so summarize_public_notices_change can
    still name exactly which notice is new/changed/removed even in the
    (currently theoretical) truncated case."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    fields = snap.get("fields") or []
    key_idx = fields.index("notice_id") if "notice_id" in fields else 0
    truncated = {
        "fields": fields,
        "n": snap.get("n", 0),
        "truncated": True,
        "digests": sorted(
            [row[key_idx], _row_digest([v for i, v in enumerate(row) if i != key_idx])]
            for row in snap.get("rows", [])
        ),
    }
    blob = json.dumps(truncated, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    # Past enough distinct notice_ids even the digest form outgrows the
    # budget, and an over-cap write is rejected outright. Drop the digests as
    # a final clamp: the hash still detects any change and `n` still counts
    # it, which is all a fully-truncated snapshot promises anyway.
    return json.dumps(
        {"fields": snap.get("fields", []), "n": snap.get("n", 0), "truncated": True,
         "digests": [], "digests_dropped": True},
        sort_keys=True, ensure_ascii=False,
    )


def _rows_by_key(snap: dict) -> dict[str, dict]:
    """Rebuild {notice_id: {field: value}} from a snapshot's positional rows.
    Raises on a structurally invalid payload — summarize_public_notices_change
    catches that and reports it as an unreadable previous snapshot rather than
    diffing against garbage."""
    fields = snap.get("fields") or []
    if "notice_id" not in fields:
        raise ValueError("snapshot fields do not include notice_id")
    out: dict[str, dict] = {}
    for row in snap.get("rows", []):
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError(f"rows entry does not match fields: {row!r}")
        d = dict(zip(fields, row))
        out[d["notice_id"]] = d
    return out


def _digest_map(snap: dict) -> dict[str, str] | None:
    """notice_id -> digest of its non-key fields, from whichever form the
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
        idx = fields.index("notice_id")
        return {
            row[idx]: _row_digest([v for i, v in enumerate(row) if i != idx])
            for row in snap.get("rows", [])
        }
    except Exception:  # noqa: BLE001 — malformed payload: "cannot tell"
        return None


def _duplicate_key_count(snap: dict) -> int:
    """How many notice_id occurrences in this snapshot are duplicates of an
    earlier one in the same snapshot — 0 when notice_id is behaving as the
    unique key this profile's whole diff design rests on. UNLIKE Evaluations/
    Permits (whose uniqueness was confirmed against hundreds of live
    records), this profile has had exactly ONE live record on each of two
    sampling dates — see the module docstring for why this guard earns its
    place here more than anywhere else. Checked on whichever form the
    snapshot is in so the guard covers a (currently theoretical) degraded
    steady state too. Returns 0 (nothing to check) rather than raising on a
    malformed payload — that failure mode is _rows_by_key/_digest_map's to
    report, not this one's."""
    try:
        if snap.get("truncated"):
            keys = [k for k, _ in snap.get("digests", [])] if not snap.get("digests_dropped") else []
        else:
            fields = snap.get("fields") or []
            if "notice_id" not in fields:
                return 0
            idx = fields.index("notice_id")
            keys = [row[idx] for row in snap.get("rows", [])
                    if isinstance(row, list) and len(row) > idx]
        return len(keys) - len(set(keys))
    except Exception:  # noqa: BLE001 — malformed payload: not this check's job
        return 0


def summarize_public_notices_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two public-notice
    snapshots. Pure — unit-tested.

    Handles, in order, five cases a bare ref-keyed diff would misreport (the
    same five nsite_evaluations_watcher.summarize_evaluations_change
    handles, in the same order):
      1. the previous snapshot is MISSING or UNREADABLE,
      2. the diffed FIELD SET changed (a config edit, not an EGLE event),
      3. notice_id has STOPPED BEING UNIQUE in either snapshot,
      4. either side was TRUNCATED,
      5. the untruncated case — full field-level ADDED/CHANGED/REMOVED."""
    if "fields" not in old:
        return (
            "changed — the previous snapshot was missing or unreadable, so no "
            "diff could be computed (this row re-baselines the site; it does "
            "NOT mean the site previously had no active public notices)",
            "The last stored snapshot for this site could not be read — the "
            "Snapshot JSON cell was empty, cleared, or malformed. This row "
            "restores a good baseline. Check the Public Notices Watch tab's "
            "history, and review MiEnviro directly for anything that changed "
            "in the meantime.",
        )

    old_fields, new_fields = old.get("fields"), new.get("fields")
    if old_fields is not None and new_fields is not None and old_fields != new_fields:
        return (
            "diffed field set changed by configuration — NOT an EGLE change "
            f"({', '.join(old_fields)} -> {', '.join(new_fields)})",
            "The nsite_public_notices.exclude_fields setting changed, so this "
            "run's snapshot is not comparable to the previous one. This row "
            "re-baselines the site; no notice-level change is implied.",
        )

    old_dupes, new_dupes = _duplicate_key_count(old), _duplicate_key_count(new)
    if old_dupes or new_dupes:
        return (
            f"changed — {old.get('n', 0)} -> {new.get('n', 0)} public notice "
            f"record(s), but notice_id was NOT unique in "
            f"{'both snapshots' if old_dupes and new_dupes else 'this snapshot'} "
            f"({new_dupes} duplicate key occurrence(s) now, {old_dupes} before) "
            "— no reliable per-notice diff could be computed",
            "This profile's diff assumes the URL-embedded notice id uniquely "
            "identifies each active public notice — verified on the live "
            "records seen so far, but this profile's real volume has never "
            "exceeded one record per site, so that assumption is far less "
            "tested than its sibling nSITE watches (see ADR 032). Two or more "
            "records now share the same key at this site. Comparing by key "
            "under that condition risks misreporting a genuinely new notice "
            "as a change to an existing one, so this row only reports total "
            "record counts. This row re-baselines the site; review MiEnviro "
            "directly for what actually changed.",
        )

    if old.get("truncated") or new.get("truncated"):
        old_d, new_d = _digest_map(old), _digest_map(new)
        if old_d is None or new_d is None:
            return (
                f"changed — {old.get('n', 0)} -> {new.get('n', 0)} public "
                "notice record(s) (a previous or current snapshot was too "
                "large to persist even as per-notice digests; no ref-level "
                "diff available)",
                "This site's active-public-notice list is too large to "
                "persist in full or as per-notice digests in one Sheet cell. "
                "Review MiEnviro directly for detail.",
            )
        new_ids = sorted(set(new_d) - set(old_d))
        removed_ids = sorted(set(old_d) - set(new_d))
        changed_ids = sorted(i for i in (set(new_d) & set(old_d)) if new_d[i] != old_d[i])
        lines = (
            [f"+ NEW NOTICE  {i}" for i in new_ids]
            + [f"~ CHANGED  {i} (detail changed — snapshot too large for a "
               f"field-level diff)" for i in changed_ids]
            + [f"- REMOVED  {i}" for i in removed_ids]
        )
        dropped = 0
        if len(lines) > MAX_ALERT_LINES:
            dropped = len(lines) - MAX_ALERT_LINES
            lines = lines[:MAX_ALERT_LINES]
            lines.append(
                f"... and {dropped} more change line(s) not shown here — the "
                f"Public Notices Watch tab's Snapshot JSON for this row is "
                f"complete."
            )
        if not (new_ids or changed_ids or removed_ids):
            return (
                f"changed — snapshot hash changed but no per-notice "
                f"difference was detected at the digest level "
                f"({old.get('n', 0)} -> {new.get('n', 0)} record(s))",
                "\n".join(lines),
            )
        parts = []
        if new_ids:
            parts.append(f"{len(new_ids)} new notice(s)")
        if changed_ids:
            parts.append(f"{len(changed_ids)} notice(s) with changed detail "
                          f"(no field-level diff — snapshot too large)")
        if removed_ids:
            parts.append(f"{len(removed_ids)} notice(s) no longer listed")
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

    fields = tuple(new_fields or old_fields or nc.PUBLIC_NOTICE_FIELDS)
    new_ids = sorted(set(new_by_key) - set(old_by_key))
    removed_ids = sorted(set(old_by_key) - set(new_by_key))
    changed_ids = sorted(
        i for i in (set(new_by_key) & set(old_by_key))
        if new_by_key[i] != old_by_key[i]
    )

    parts: list[str] = []
    lines: list[str] = []
    for nid in new_ids:
        r = new_by_key[nid]
        parts.append("new public notice recorded")
        lines.append(
            f"+ NEW NOTICE  {nid} — coverage={r.get('coverage') or '—'}, "
            f"comment window {r.get('start_date') or '—'} to "
            f"{r.get('end_date') or '—'}\n"
            f"    {r.get('comments') or '(no comments text)'}"
        )
    for nid in changed_ids:
        old_r, new_r = old_by_key[nid], new_by_key[nid]
        parts.append("existing public notice changed")
        changed_detail = ", ".join(
            f"{f}: {old_r.get(f) or '—'} -> {new_r.get(f) or '—'}"
            for f in fields
            if f != "notice_id" and old_r.get(f) != new_r.get(f)
        )
        lines.append(f"~ CHANGED  {nid} ({changed_detail})")
    for nid in removed_ids:
        r = old_by_key[nid]
        parts.append("public notice no longer listed")
        lines.append(f"- REMOVED  {nid} — coverage={r.get('coverage') or '—'}")

    dropped = 0
    if len(lines) > MAX_ALERT_LINES:
        dropped = len(lines) - MAX_ALERT_LINES
        lines = lines[:MAX_ALERT_LINES]
        lines.append(
            f"... and {dropped} more change line(s) not shown here — the "
            f"Public Notices Watch tab's Snapshot JSON for this row is "
            f"complete."
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


# ---------------------------------------------------------------------------
# ROP-overlap alert suppression (ADR 032's Open Decision 1, resolved
# 2026-08-26) — suppress the redundant EMAIL, never the durable Sheet row,
# for a notice that reads as a ROP renewal comment window Stream H
# (rop_watcher.py) already tracks and alerts on separately.
# ---------------------------------------------------------------------------

# Matches EGLE's own boilerplate phrasing for a ROP renewal notice (both live
# records seen so far say "Renewable Operating Permit (ROP)" in prose) plus a
# bare "ROP" token, case-insensitive. A free-text heuristic, not a structured
# filter — confirmed 2026-08-25 that no structured field exists in this
# profile's schema to do this cleanly (ADR 032, Open Decision 1 Option 3).
_ROP_KEYWORD_RE = re.compile(r"renewable operating permit|\brop\b", re.IGNORECASE)


def _looks_like_rop_notice(comments: str) -> bool:
    """Whether a notice's comments text reads as a ROP renewal notice, by
    keyword match. Pure, unit-tested. FALSE means "alert" (fail open); TRUE
    is what lets the caller consider suppressing the email — never the
    durable row, which is always written regardless."""
    return bool(_ROP_KEYWORD_RE.search(comments or ""))


def _all_changed_notices_look_like_rop(old: dict, new: dict) -> bool:
    """True iff EVERY notice this diff would otherwise alert on — newly
    added, changed, or no-longer-listed — reads as a ROP renewal notice by
    keyword match. Used by _diff_and_record to decide whether to suppress
    the redundant EMAIL for this profile's one real event pattern observed
    so far (see the module docstring's RESOLUTION note and ADR 032).

    FAILS OPEN (returns False, so the caller still emails) whenever the
    check cannot be confidently evaluated: either snapshot is in the
    truncated/degraded form (no full comments text to inspect) — in
    practice only `old`, the stored/reloaded snapshot, can actually arrive
    truncated at the real call site (_diff_and_record always passes the
    freshly built, full `new` snapshot; public_notices_snapshot() never
    marks its own output truncated), so the `new`-side check is defensive
    insurance for a future caller, not a currently-exercised path — or
    notice_id has stopped being unique in either snapshot (the by-key
    comparison below would be unreliable), or the previous snapshot is
    missing/malformed, or there is nothing to check.
    `summarize_public_notices_change` is what reports those cases to a
    human; this function isn't meant to also reason about them, so it
    declines to and lets the email through instead.

    A NON-matching notice among several changed ones means the WHOLE diff
    still alerts — this does not try to alert on "just the non-ROP part",
    since the email already describes every changed notice together, and
    splitting that is not worth the complexity this profile's real volume
    (0-1 records per site) does not call for."""
    if old.get("truncated") or new.get("truncated"):
        return False
    if _duplicate_key_count(old) or _duplicate_key_count(new):
        return False
    try:
        old_by_key = _rows_by_key(old)
        new_by_key = _rows_by_key(new)
    except Exception:  # noqa: BLE001 — malformed/missing payload: fail open
        return False
    new_ids = set(new_by_key) - set(old_by_key)
    removed_ids = set(old_by_key) - set(new_by_key)
    changed_ids = {i for i in (set(new_by_key) & set(old_by_key))
                   if new_by_key[i] != old_by_key[i]}
    if not (new_ids or removed_ids or changed_ids):
        return False
    for nid in new_ids | changed_ids:
        if not _looks_like_rop_notice(new_by_key[nid].get("comments", "")):
            return False
    for nid in removed_ids:
        if not _looks_like_rop_notice(old_by_key[nid].get("comments", "")):
            return False
    return True


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested. All EGLE-derived text
    lands HERE, in the body — never in the subject line (see run()).

    Carries an explicit disambiguation paragraph (per ADR 032's Open Decision
    1, resolved 2026-08-26): this watch normally SUPPRESSES its own email
    (never the durable row) for a notice that reads as a ROP renewal comment
    window — the same event type Stream H already tracks separately — so by
    the time this function runs at all, the change either didn't match that
    pattern, or suppression is disabled. Either way, the disclosure below
    still fires: a keyword match can miss real phrasing variance, so a
    reader should not assume a mismatch here means "definitely not ROP"."""
    shown = body or "(no further detail — see the Public Notices Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills nSITE ACTIVE PUBLIC NOTICES list changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's nSITE Active Public Notices "
        "profile — the formal comment-window announcements (permit renewals, "
        "draft permits, hearings) EGLE files per site. It trip-wires a "
        "brand-new notice the moment EGLE records one, or an existing "
        "notice's detail (including its comment-window dates) advancing. It "
        "makes no severity judgment beyond that.\n\n"
        "NOTE ON OVERLAP: this watch normally suppresses its own email "
        "(never the durable Sheet row above) when a notice's text reads as "
        "a ROP renewal comment window — the SAME event type the monitor's "
        "separate ROP renewal watch (Stream H) already tracks and alerts on "
        "through a different mechanism. You are receiving this email "
        "because this change did not clearly match that pattern. If it "
        "nonetheless turns out to be a ROP renewal notice, the automatic "
        "filter missed it this time — a separate email from that watch "
        "about the same window would be a known, disclosed overlap, not a "
        "duplicate error. See ADR 032.\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _diff_and_record(sheets, sheet_id, today, key, label, snap, cfg, recipients,
                     last, budget=DEFAULT_SNAPSHOT_CHAR_BUDGET,
                     suppress_rop_matches: bool = True) -> tuple[str, str | None]:
    """Baseline/compare/record/alert for one site. Returns
    (result, alert_error) where result is "baseline"/"changed"/
    "changed_suppressed"/"unchanged" and alert_error is None unless a change
    was recorded but its email could not be sent ("changed_suppressed" never
    carries an alert_error — a deliberate suppression is not a failure).

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
        sw.append_public_notices_watch_row(sheets, sheet_id, today, key, label,
                                           "baseline", new_hash,
                                           "initial snapshot (no alert)", _now(),
                                           snap_json)
        print(f"[nsite-public-notices-watch] {label}: baseline recorded "
              f"({new_hash}, {snap.get('n', 0)} record(s)).")
        return "baseline", None

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-public-notices-watch] {label}: unchanged ({new_hash}).")
        return "unchanged", None

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_public_notices_change(old_snap, snap)
    sw.append_public_notices_watch_row(sheets, sheet_id, today, key, label,
                                       "changed", new_hash, note, _now(), snap_json)
    print(f"[nsite-public-notices-watch] {label}: CHANGED "
          f"({last_hash} -> {new_hash}; {note}).")

    if suppress_rop_matches and _all_changed_notices_look_like_rop(old_snap, snap):
        print(f"[nsite-public-notices-watch] {label}: every notice in this "
              f"change reads as a ROP renewal comment window Stream H "
              f"already tracks — email suppressed by design (the row above "
              f"is durable regardless; see ADR 032). Set "
              f"nsite_public_notices.rop_alert_suppression: false to disable this.")
        return "changed_suppressed", None

    # The row above is already durable — everything from here down is
    # alerting for THIS site only, so a failure in either step is reported
    # rather than raised, and can never abort run()'s other sites.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-public-notices-watch] {label}: change recorded but alert "
              f"body FORMATTING failed: {e}")
        return "changed", f"body formatting failed: {e}"
    try:
        # Subject carries ONLY the maintainer-authored label from nsite_sites —
        # no EGLE-derived text ever reaches an email header.
        ea.send_email(f"[Public Notices watch] {label} changed", email_body, cfg,
                      recipients=recipients)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-public-notices-watch] {label}: change recorded but alert "
              f"email FAILED (the change IS durable in the Public Notices Watch "
              f"tab — this run exits non-zero so the lost notification is "
              f"visible): {e}")
        return "changed", f"send failed: {e}"
    return "changed", None


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[nsite-public-notices-watch] {reason}")
        return 0

    pcfg = cfg.get("nsite_public_notices") or {}
    recipients = pcfg.get("recipients") or None  # None -> full alert_recipients list
    # Clamped, because `snapshot_char_budget` sits in config.yml directly under
    # a comment naming the 50,000 cap — so "raise it a bit" is a plausible edit,
    # and any value at or above the cap would disable the truncation guard
    # entirely and hand the site a permanently rejected write.
    budget = min(int(pcfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET),
                 HARD_SHEETS_CELL_LIMIT - 1000)
    # Config-only rollback lever (ADR 032's Open Decision 1, resolved
    # 2026-08-26): defaults True, so a bare/minimal config still gets the
    # safer behavior. Set to false to revert to pure standalone alerting
    # without a code change.
    suppress_rop_matches = bool(pcfg.get("rop_alert_suppression", True))
    fields = diff_fields(cfg)
    # Resolve the working site list by joining the shared identity registry
    # (nsite_sites, ADR 022) with THIS profile's own cadence map. A `tiers` srn
    # absent from the registry is a config error — KeyError raises naturally,
    # on purpose (ADR 022): a loud failure on a config typo is correct, a
    # silently-unwatched site is not.
    registry = {s["srn"]: s for s in cfg.get("nsite_sites") or []}
    sites = [
        {**registry[srn], "poll": poll}
        for srn, poll in (pcfg.get("tiers") or {}).items()
    ]

    # If alerting is ALREADY known to be impossible, stop before touching
    # anything. Writing a row here would advance the stored hash, so tomorrow's
    # run would compare equal, report "unchanged", and never retry — the
    # notification gone permanently even after the secret was fixed. Nothing is
    # lost by stopping: the notices are still in nSITE, so the next healthy
    # run records AND alerts on them.
    alerting_ok, alerting_error = alerting_is_configured(cfg, recipients)
    if not alerting_ok:
        print(f"[nsite-public-notices-watch] {alerting_error} — aborting BEFORE "
              f"any fetch or write. A change found now could not be emailed, and "
              f"recording it would advance the stored hash so the next run would "
              f"report 'unchanged' and never retry. Nothing is lost: fix the "
              f"configuration and the next run records and alerts normally.")
        return 1

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_public_notices_tabs(sheets, sheet_id)

    session = nc.make_session()
    today_date = _today_date()
    today = today_date.isoformat()
    counts = {"baseline": 0, "changed": 0, "changed_suppressed": 0,
              "unchanged": 0, "skipped": 0, "fetch_failed": 0, "failed": 0,
              "alert_failed": 0}
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
            print(f"[nsite-public-notices-watch] {srn}: unrecognized poll cadence "
                  f"{cadence!r} ({e}) — treating as due (fail-safe).")
            due = True
        if due:
            due_sites.append(site)
        else:
            print(f"[nsite-public-notices-watch] {srn}: not due today "
                  f"({cadence} cadence) — skipping.")
            counts["skipped"] += 1

    # ONE tab read for every due site, not one per site. Two reasons, both
    # load-bearing: it cuts the read count (less throttling exposure), and
    # last_public_notices_snapshots RAISES on a read failure instead of
    # returning a per-key None. A swallowed read error here would make every
    # site look never-seen, write fresh "baseline" rows, and — because the tab
    # is append-only with last-write-wins — PERMANENTLY erase an un-alerted
    # change rather than deferring it. So a read failure aborts before any
    # write, leaving last run's state intact and correct.
    try:
        last_by_key = sw.last_public_notices_snapshots(
            sheets, sheet_id, [f"pubntc:{s['srn']}" for s in due_sites])
    except Exception as e:  # noqa: BLE001
        print(f"[nsite-public-notices-watch] could not read the Public "
              f"Notices Watch tab ({type(e).__name__}: {e}) — aborting before "
              f"any DATA row is written, so no site can be spuriously "
              f"re-baselined.")
        return 1

    for site in due_sites:
        # One try per site, so NOTHING (a fetch failure, an oversized or
        # rejected Sheets write, a malformed stored snapshot, a malformed
        # registry entry) can abort the run and silently drop every site
        # queued after this one.
        try:
            srn, name, nsite_id = site["srn"], site["name"], site["id"]
            key = f"pubntc:{srn}"
            label = f"nSITE Active Public Notices — {name} ({srn})"
            last = last_by_key.get(key)

            try:
                notices = nc.fetch_site_public_notices(session, nsite_id)
                print(f"[nsite-public-notices-watch] {label}: fetched "
                      f"{len(notices)} active public notice(s).")
            except nc.NsiteStructuralError as e:
                # NOT transient, so it must NOT take the skip-and-warn path
                # below: nSITE having changed the response shape (e.g. started
                # paging) would otherwise fail identically every single day
                # behind a green build. Checked before the base class.
                counts["fetch_failed"] += 1
                exit_code = 1
                print(f"[nsite-public-notices-watch] {label}: STRUCTURAL break in "
                      f"nSITE's response — this will NOT fix itself and needs "
                      f"code changes; failing loudly rather than going quiet: {e}")
                continue
            except nc.NsiteFetchError as e:
                counts["fetch_failed"] += 1
                if last is not None:
                    print(f"[nsite-public-notices-watch] {label}: fetch failed, "
                          f"skipping this run (baseline preserved, not diffed): {e}")
                else:
                    print(f"[nsite-public-notices-watch] {label}: NO BASELINE and "
                          f"fetch failed (failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                continue

            snap = public_notices_snapshot(notices, fields)
            result, alert_error = _diff_and_record(
                sheets, sheet_id, today, key, label, snap, cfg, recipients, last,
                budget, suppress_rop_matches)
            counts[result] += 1
            if alert_error:
                counts["alert_failed"] += 1
                exit_code = 1
        except Exception as e:  # noqa: BLE001 — isolate this site, keep the run going
            print(f"[nsite-public-notices-watch] {site.get('srn', '?')}: UNEXPECTED "
                  f"failure, continuing with the remaining sites: "
                  f"{type(e).__name__}: {e}")
            counts["failed"] += 1
            exit_code = 1

    print(f"[nsite-public-notices-watch] done — {counts['changed']} changed, "
          f"{counts['changed_suppressed']} changed-but-email-suppressed "
          f"(looked like an already-covered ROP notice), "
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today, {counts['fetch_failed']} fetch-failed, "
          f"{counts['failed']} errored, {counts['alert_failed']} change(s) recorded "
          f"but NOT emailed (across {len(sites)} site"
          f"{'' if len(sites) == 1 else 's'}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
