"""
nsite_permits_watcher.py — runs daily, watching EGLE's nSITE PERMITS profile
(a facility's permit lifecycle: issued -> extended -> expiring -> terminated)
for every site in config.yml's `nsite_sites` registry that has an
`nsite_permits.tiers` entry. Each site is actually fetched+diffed at its own
`poll` cadence (daily/biweekly/quarterly), not every run. Standalone +
self-terminating, the same shape as nsite_evaluations_watcher.py /
nsite_compliance_actions_watcher.py / nsite_violations_watcher.py. See
docs/decisions/030-nsite-permits-watch.md.

WHY: a permit's lifecycle is a status signal about a facility's regulatory
standing — broader than Stream H's targeted ROP watch (rop_client.py /
rop_watcher.py, ADR 017), which follows only the Air Renewable Operating
Permit renewal process (its public-comment window) for N2688/N1504/P1488.
This Permits profile covers EVERY permit type across all 19 sites: Air ROPs,
Air Permits to Install, Air Reporting Schedules, NPDES water permits,
Resources (wetland/floodplain) permits. Live reconnaissance (2026-08-22, all
19 sites) found N2688 9 permits (incl. the Air ROP `ROP0000224`, currently
"Extended"), N1504 4 (incl. `ROP0000656`), P1488 3 (incl. `ROP0000236`), RA 2,
WRD 2, P1504 1 (already "Terminated"), SANL 1 (already "Expired").

*** OVERLAP WITH STREAM H — READ BEFORE TOUCHING THE ALERT COPY ***
`ROP0000224`/`ROP0000656`/`ROP0000236` — the exact three ROP permit numbers
Stream H watches — appear in THIS profile too (confirmed live 2026-08-22, all
three currently "Extended"). This is NOT a duplicate stream: Stream H
trip-wires a DIFFERENT event (the permit ENTERING a 30-day public-comment
window, detected via a statewide notice PDF + the renewal folder's file
list); this watch trip-wires PERMIT STATUS/LIFECYCLE changes (Extended ->
Expired, a termination date populating, a new permit appearing) via nSITE's
own structured record. The two can legitimately fire on the same permit
number for different reasons and neither supersedes the other. `format_change_
body` below says so explicitly, in the email itself — NOT just in the ADR —
so a reader is never confused into thinking one alert duplicates the other.
This watch does NOT suppress ROP permits from its diff (that would blind it
to a real status change); it disambiguates in the copy instead.

ONE item per site — `prmt:<srn>` (e.g. "prmt:N2688") — derived from ONE fetch
per site, so each item's fetch failure is independent (no ROP-style batching).

WHAT IT DOES per site (mirrors nsite_evaluations_watcher):
  - build a canonical snapshot of the site's permit list + hash it,
  - compare to the last snapshot recorded in the "Permits Watch" tab,
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert,
  - hash unchanged -> no-op.

WHY A REF-NUMBER-KEYED DIFF AND NOT A MULTISET (the feasibility gate's
finding, 2026-08-22, every site with any permits live-fetched): like
Evaluations and UNLIKE Violations/Compliance Actions, this profile carries a
genuine unique key — `prmtPrmtNum` was unique within every site's record set
with permits (9/9 N2688, 4/4 N1504, 3/3 P1488, 2/2 RA, 2/2 WRD, 1/1 P1504,
1/1 SANL). So the diff is keyed on prmt_num, the same idiom as nsite_
evaluations_watcher/nsite_submissions_watcher's ref-number keys, rather than
the rop/mmd/ride/violations/compliance-actions Counter-multiset idiom.

THE ACTUAL SIGNAL IS STATUS/DATE CHANGES ON AN *EXISTING* PERMIT, NOT JUST A
NEW NUMBER APPEARING (the handoff's explicit design requirement): a brand-new
prmt_num means a NEW PERMIT; an existing prmt_num with ANY changed field —
most importantly `status` (e.g. Extended -> Expired) or a null -> populated
`termination_date` — means that permit's status is advancing. The keyed diff
below reports every changed field, not just a hash flip, exactly like nsite_
evaluations_watcher's "detail changed" case; both this module's docstring and
its tests treat a status/date change on an existing key as the primary
event, not a secondary one.

WHY THE SNAPSHOT STILL CARRIES A BUDGET-DEGRADATION GUARD DESPITE NEVER
FIRING AT REAL VOLUME: inherited from Evaluations/Compliance Actions/
Violations for structural parity (a copy-paste base other reviewers already
know how to read), but at Permits volumes (max 9 records at N2688) it is
verified-inert insurance, like Compliance Actions' — not a live necessity the
way it is for Evaluations' N2688 (477 records). A full per-record snapshot
fits the 50K Sheets cell cap many times over at any watched site.

NO SEVERITY JUDGMENT beyond noting the ROP overlap above. EGLE's permit
status vocabulary ("Extended", "In Effect", "Terminated", "Expired") is a
multi-state lifecycle, not a good/bad binary — this watch is a TRIP-WIRE on
change, exactly like Violations/Compliance Actions, and a human reads what it
means.

FAILURE HANDLING, in three layers (identical to nsite_evaluations_watcher /
nsite_compliance_actions_watcher / nsite_violations_watcher):

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

GATED on nsite_permits.enabled, which ships FALSE: a brand-new poller against
a live external system built unattended, so flipping it on is explicitly a
separate human step (overnight-coder Step 3).

TIERED CADENCE: `_is_due` is IMPORTED from nsite_submissions_watcher rather
than reimplemented — it is already generic over (cadence, srn, today). The
TIERS THEMSELVES are this profile's own and deliberately differ from every
sibling (see ADR 030): N2688/N1504/P1488 are all DAILY here — UNLIKE every
prior profile, where at most one or two sites earn daily — because all three
currently hold an "Extended" (mid-renewal-lifecycle) permit that is this
watch's own headline concern, independent of any OTHER profile's activity at
that srn.

NO DRIVE / OAUTH (same scope call as every other watch, ADR 012): the
deliverable is the ALERT + the durable Sheet row.

Runs daily — its workflow file was landed directly into .github/workflows/
(this build session's SSH key authenticated non-interactively against
GitHub, so the `workflow` OAuth-scope blocker that parked Stream L/M's
workflow files at docs/pending-workflows/ did not apply here — see
overnight-coder.md Step 4, same as Stream N). Harmless while enabled is false.
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
# a tunable. At Permits volumes (max 9 records at N2688) this guard is
# verified-inert insurance, like Compliance Actions'/Violations' — it exists
# so a bulk EGLE re-import degrades gracefully instead of hitting a hard
# 50,000-char write rejection, not because any watched site comes close today.
HARD_SHEETS_CELL_LIMIT = 50000
DEFAULT_SNAPSHOT_CHAR_BUDGET = 45000

# How many ADDED/CHANGED/REMOVED lines an alert email prints before
# summarizing the rest. Cheap insurance carried over from every sibling; a
# bulk EGLE re-import is the only realistic trigger at these volumes.
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
    nsite_permits.enabled is set) has a direct unit test. Mirrors
    nsite_evaluations_watcher._should_run."""
    if not (cfg.get("nsite_permits") or {}).get("enabled"):
        return False, "nsite_permits.enabled is false — skipping (no-op)."
    return True, ""


def diff_fields(cfg: dict) -> tuple[str, ...]:
    """The field set this run diffs on: every nsite_client.PERMIT_FIELDS
    except any listed in `nsite_permits.exclude_fields` — MINUS `prmt_num`,
    which can never be excluded regardless of config. Unlike Violations/CA's
    `exclude_fields` (which only ever drops a display/headline field from a
    multiset diff), prmt_num here is the structural diff KEY — every snapshot/
    diff helper below assumes it is present, so silently honoring an
    `exclude_fields: [prmt_num]` typo would break the diff rather than merely
    degrade a line's readability."""
    excluded = set((cfg.get("nsite_permits") or {}).get("exclude_fields") or [])
    excluded.discard("prmt_num")
    return tuple(f for f in nc.PERMIT_FIELDS if f not in excluded)


# ---------------------------------------------------------------------------
# Snapshot / diff (pure)
# ---------------------------------------------------------------------------


def permits_snapshot(rows: list[dict], fields: tuple[str, ...]) -> dict:
    """Canonical, hash-stable snapshot of one site's permit list, keyed on
    `prmt_num` (verified unique per site with permits — 9/9 at N2688, 4/4 at
    N1504, 3/3 at P1488, 2/2 at RA, 2/2 at WRD, 1/1 at P1504, 1/1 at SANL,
    live spike). Sorted by prmt_num for a stable hash regardless of API
    ordering, encoded POSITIONALLY (`fields` header + `[prmt_num, *values]`
    rows) rather than one JSON object per record — the same compact form
    nsite_evaluations_watcher uses, though at Permits volumes it never comes
    close to needing it.

    ASSUMES `prmt_num` is in `fields` — true for every real caller, since this
    is only ever invoked with diff_fields()'s output (which can never exclude
    it) or a fields tuple built directly from nc.PERMIT_FIELDS."""
    keyed = sorted(
        ({f: r.get(f) or "" for f in fields} for r in rows),
        key=lambda d: d["prmt_num"],
    )
    return {
        "fields": list(fields),
        "n": len(keyed),
        "rows": [[d[f] for f in fields] for d in keyed],
    }


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as every sibling watch.

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
    per-permit digest map if the full form would exceed `budget` chars.

    Inherited from Evaluations' guard, but at Permits volumes (max 9 records)
    this is verified-inert insurance — it exists so a bulk EGLE re-import
    degrades gracefully instead of hitting a hard 50,000-char write rejection,
    not because any watched site comes close today. The degraded form keeps
    `[prmt_num, digest]` pairs (prmt_num stays VISIBLE, unlike Violations/CA's
    anonymous digest multiset) so summarize_permits_change can still name
    exactly which permit is new/changed/removed without a field-level diff."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    if len(blob) <= budget:
        return blob
    fields = snap.get("fields") or []
    # Looked up by field NAME, not position 0, for the same reason
    # nsite_evaluations_watcher._cell_payload does: this snapshot was just
    # built by permits_snapshot() so prmt_num IS at position 0 today, but
    # nothing here should silently depend on PERMIT_FIELDS' declared order.
    key_idx = fields.index("prmt_num") if "prmt_num" in fields else 0
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
    # Past enough distinct prmt_nums even the digest form outgrows the budget,
    # and an over-cap write is rejected outright. Drop the digests as a final
    # clamp: the hash still detects any change and `n` still counts it, which
    # is all a fully-truncated snapshot promises anyway.
    return json.dumps(
        {"fields": snap.get("fields", []), "n": snap.get("n", 0), "truncated": True,
         "digests": [], "digests_dropped": True},
        sort_keys=True, ensure_ascii=False,
    )


def _rows_by_key(snap: dict) -> dict[str, dict]:
    """Rebuild {prmt_num: {field: value}} from a snapshot's positional rows.
    Raises on a structurally invalid payload — summarize_permits_change
    catches that and reports it as an unreadable previous snapshot rather than
    diffing against garbage."""
    fields = snap.get("fields") or []
    if "prmt_num" not in fields:
        raise ValueError("snapshot fields do not include prmt_num")
    out: dict[str, dict] = {}
    for row in snap.get("rows", []):
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError(f"rows entry does not match fields: {row!r}")
        d = dict(zip(fields, row))
        out[d["prmt_num"]] = d
    return out


def _digest_map(snap: dict) -> dict[str, str] | None:
    """prmt_num -> digest of its non-key fields, from whichever form the
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
        idx = fields.index("prmt_num")
        return {
            row[idx]: _row_digest([v for i, v in enumerate(row) if i != idx])
            for row in snap.get("rows", [])
        }
    except Exception:  # noqa: BLE001 — malformed payload: "cannot tell"
        return None


def _duplicate_key_count(snap: dict) -> int:
    """How many prmt_num occurrences in this snapshot are duplicates of an
    earlier one in the same snapshot — 0 when prmt_num is behaving as the
    genuine unique key this profile's whole diff design rests on (verified
    live for every real site with permits, ADR 030). A non-zero count means
    that assumption has stopped holding for THIS snapshot, and summarize_
    permits_change must not proceed to _rows_by_key/_digest_map — both
    collapse same-key rows into a dict, so a genuinely NEW record that happens
    to share an existing prmt_num would silently vanish into a same-key
    "changed" line instead of being reported at all. Checked on whichever form
    the snapshot is in (truncated digests or full rows). Returns 0 (nothing to
    check) rather than raising on a malformed payload — that failure mode is
    _rows_by_key/_digest_map's to report, not this one's."""
    try:
        if snap.get("truncated"):
            keys = [k for k, _ in snap.get("digests", [])] if not snap.get("digests_dropped") else []
        else:
            fields = snap.get("fields") or []
            if "prmt_num" not in fields:
                return 0
            idx = fields.index("prmt_num")
            keys = [row[idx] for row in snap.get("rows", [])
                    if isinstance(row, list) and len(row) > idx]
        return len(keys) - len(set(keys))
    except Exception:  # noqa: BLE001 — malformed payload: not this check's job
        return 0


def summarize_permits_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two permit snapshots.
    Pure — unit-tested.

    Handles, in order, five cases a bare ref-keyed diff would misreport:
      1. the previous snapshot is MISSING or UNREADABLE — which must never be
         confused with "the site had zero permits",
      2. the diffed FIELD SET changed (a config edit, not an EGLE event),
      3. prmt_num has STOPPED BEING UNIQUE in either snapshot — the load-
         bearing assumption behind this whole profile's design (verified
         live, not guaranteed by the API) — checked BEFORE any by-key dict is
         built, same guard as nsite_evaluations_watcher,
      4. either side was TRUNCATED — still reports exactly which prmt_num is
         new/changed/removed (digest-level, no field detail), NOT a bare
         count delta,
      5. the untruncated case — full field-level ADDED/CHANGED/REMOVED. THIS
         is where a status change (Extended -> Expired) or a termination_date
         populating shows up, since every changed field is printed."""
    # A genuine snapshot ALWAYS carries "fields", even when it holds zero
    # records — so its absence means the stored cell was empty, cleared, or
    # unparseable, not that the site was clean.
    if "fields" not in old:
        return (
            "changed — the previous snapshot was missing or unreadable, so no "
            "diff could be computed (this row re-baselines the site; it does "
            "NOT mean the site previously had no permits)",
            "The last stored snapshot for this site could not be read — the "
            "Snapshot JSON cell was empty, cleared, or malformed. This row "
            "restores a good baseline. Check the Permits Watch tab's history, "
            "and review MiEnviro directly for anything that changed in the "
            "meantime.",
        )

    old_fields, new_fields = old.get("fields"), new.get("fields")
    if old_fields is not None and new_fields is not None and old_fields != new_fields:
        return (
            "diffed field set changed by configuration — NOT an EGLE change "
            f"({', '.join(old_fields)} -> {', '.join(new_fields)})",
            "The nsite_permits.exclude_fields setting changed, so this run's "
            "snapshot is not comparable to the previous one. This row "
            "re-baselines the site; no permit-level change is implied.",
        )

    old_dupes, new_dupes = _duplicate_key_count(old), _duplicate_key_count(new)
    if old_dupes or new_dupes:
        return (
            f"changed — {old.get('n', 0)} -> {new.get('n', 0)} permit record(s), "
            f"but prmt_num was NOT unique in "
            f"{'both snapshots' if old_dupes and new_dupes else 'this snapshot'} "
            f"({new_dupes} duplicate key occurrence(s) now, {old_dupes} before) "
            "— no reliable per-permit diff could be computed",
            "This profile's diff assumes prmt_num uniquely identifies each "
            "permit (verified live for every site with permits — see ADR 030), "
            "but two or more records now share the same prmt_num at this site. "
            "Comparing by key under that condition risks misreporting a "
            "genuinely new permit as a change to an existing one, so this row "
            "only reports total record counts. This row re-baselines the "
            "site; review MiEnviro directly for what actually changed.",
        )

    if old.get("truncated") or new.get("truncated"):
        old_d, new_d = _digest_map(old), _digest_map(new)
        if old_d is None or new_d is None:
            return (
                f"changed — {old.get('n', 0)} -> {new.get('n', 0)} permit "
                "record(s) (a previous or current snapshot was too large to "
                "persist even as per-permit digests; no ref-level diff "
                "available)",
                "This site's permit list is too large to persist in full or "
                "as per-permit digests in one Sheet cell. Review MiEnviro "
                "directly for detail.",
            )
        new_nums = sorted(set(new_d) - set(old_d))
        removed_nums = sorted(set(old_d) - set(new_d))
        changed_nums = sorted(n for n in (set(new_d) & set(old_d)) if new_d[n] != old_d[n])
        lines = (
            [f"+ NEW PERMIT  {n}" for n in new_nums]
            + [f"~ CHANGED  {n} (status/detail changed — snapshot too large "
               f"for a field-level diff)" for n in changed_nums]
            + [f"- REMOVED  {n}" for n in removed_nums]
        )
        dropped = 0
        if len(lines) > MAX_ALERT_LINES:
            dropped = len(lines) - MAX_ALERT_LINES
            lines = lines[:MAX_ALERT_LINES]
            lines.append(
                f"... and {dropped} more change line(s) not shown here — the "
                f"Permits Watch tab's Snapshot JSON for this row is complete."
            )
        if not (new_nums or changed_nums or removed_nums):
            return (
                f"changed — snapshot hash changed but no per-permit "
                f"difference was detected at the digest level "
                f"({old.get('n', 0)} -> {new.get('n', 0)} record(s))",
                "\n".join(lines),
            )
        parts = []
        if new_nums:
            parts.append(f"{len(new_nums)} new permit(s)")
        if changed_nums:
            parts.append(f"{len(changed_nums)} permit(s) with changed status/detail "
                          f"(no field-level diff — snapshot too large)")
        if removed_nums:
            parts.append(f"{len(removed_nums)} permit(s) no longer listed")
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

    fields = tuple(new_fields or old_fields or nc.PERMIT_FIELDS)
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
        parts.append("new permit recorded")
        lines.append(
            f"+ NEW PERMIT  {num} — status={r.get('status') or '—'}, "
            f"category={r.get('category') or '—'}, "
            f"type={r.get('permit_type') or '—'}, "
            f"effective={r.get('effective_date') or '—'}, "
            f"expiration={r.get('expiration_date') or '—'}, "
            f"termination={r.get('termination_date') or '—'}"
        )
    for num in changed_nums:
        old_r, new_r = old_by_key[num], new_by_key[num]
        parts.append("existing permit changed")
        changed_detail = ", ".join(
            f"{f}: {old_r.get(f) or '—'} -> {new_r.get(f) or '—'}"
            for f in fields
            if f != "prmt_num" and old_r.get(f) != new_r.get(f)
        )
        lines.append(f"~ CHANGED  {num} — status={new_r.get('status') or '—'} "
                      f"({changed_detail})")
    for num in removed_nums:
        r = old_by_key[num]
        parts.append("permit no longer listed")
        lines.append(f"- REMOVED  {num} — status={r.get('status') or '—'}")

    dropped = 0
    if len(lines) > MAX_ALERT_LINES:
        dropped = len(lines) - MAX_ALERT_LINES
        lines = lines[:MAX_ALERT_LINES]
        lines.append(
            f"... and {dropped} more change line(s) not shown here — the "
            f"Permits Watch tab's Snapshot JSON for this row is complete."
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
    lands HERE, in the body — never in the subject line (see run()).

    Carries the ROP-overlap disambiguation paragraph EXPLICITLY, per the
    handoff's adversarial-review finding: three of this profile's permits
    (ROP0000224/0656/0236) are ALSO watched by Stream H's targeted ROP watch,
    and a reader seeing both alerts must not conclude one is a duplicate of
    the other."""
    shown = body or "(no further detail — see the Permits Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills nSITE PERMITS list changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's nSITE Permits profile — a "
        "facility's permit lifecycle (issued, extended, expiring, "
        "terminated), across EVERY permit type on file, not just Air ROPs. "
        "It trip-wires a brand-new permit the moment EGLE records one under "
        "this site, or an existing permit's status or dates advancing (e.g. "
        "a status moving from Extended to Expired, or a termination date "
        "being filled in). It makes NO judgment about which status is good "
        "or bad — EGLE's status vocabulary is a multi-state lifecycle, so "
        "read the change in MiEnviro directly for full context.\n\n"
        "Note on overlap with the ROP watch: this profile includes the Air "
        "Renewable Operating Permits (ROP0000224 / ROP0000656 / ROP0000236) "
        "that the monitor's separate ROP watch (Stream H) also tracks. This "
        "alert reports a PERMIT STATUS/LIFECYCLE change (e.g. Extended -> "
        "Expired) via EGLE's own permit record — a DIFFERENT event from the "
        "ROP watch's public-comment-window trip-wire (which fires when a "
        "renewal enters its 30-day comment period via a separate statewide "
        "notice). Seeing both alerts on the same permit number does not mean "
        "one duplicates the other.\n"
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
        sw.append_permits_watch_row(sheets, sheet_id, today, key, label,
                                    "baseline", new_hash,
                                    "initial snapshot (no alert)", _now(),
                                    snap_json)
        print(f"[nsite-permits-watch] {label}: baseline recorded "
              f"({new_hash}, {snap.get('n', 0)} record(s)).")
        return "baseline", None

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-permits-watch] {label}: unchanged ({new_hash}).")
        return "unchanged", None

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_permits_change(old_snap, snap)
    sw.append_permits_watch_row(sheets, sheet_id, today, key, label,
                                "changed", new_hash, note, _now(), snap_json)
    print(f"[nsite-permits-watch] {label}: CHANGED "
          f"({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is
    # alerting for THIS site only, so a failure in either step is reported
    # rather than raised, and can never abort run()'s other sites.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-permits-watch] {label}: change recorded but alert "
              f"body FORMATTING failed: {e}")
        return "changed", f"body formatting failed: {e}"
    try:
        # Subject carries ONLY the maintainer-authored label from nsite_sites —
        # no EGLE-derived text ever reaches an email header.
        ea.send_email(f"[Permits watch] {label} changed", email_body, cfg,
                      recipients=recipients)
    except Exception as e:  # noqa: BLE001 — row is recorded; surface the lost alert
        print(f"[nsite-permits-watch] {label}: change recorded but alert "
              f"email FAILED (the change IS durable in the Permits Watch "
              f"tab — this run exits non-zero so the lost notification is "
              f"visible): {e}")
        return "changed", f"send failed: {e}"
    return "changed", None


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[nsite-permits-watch] {reason}")
        return 0

    pcfg = cfg.get("nsite_permits") or {}
    recipients = pcfg.get("recipients") or None  # None -> full alert_recipients list
    # Clamped, because `snapshot_char_budget` sits in config.yml directly under
    # a comment naming the 50,000 cap — so "raise it a bit" is a plausible edit,
    # and any value at or above the cap would disable the truncation guard
    # entirely and hand the site a permanently rejected write.
    budget = min(int(pcfg.get("snapshot_char_budget") or DEFAULT_SNAPSHOT_CHAR_BUDGET),
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
        for srn, poll in (pcfg.get("tiers") or {}).items()
    ]

    # If alerting is ALREADY known to be impossible, stop before touching
    # anything. Writing a row here would advance the stored hash, so tomorrow's
    # run would compare equal, report "unchanged", and never retry — the
    # notification gone permanently even after the secret was fixed. Nothing is
    # lost by stopping: the permits are still in nSITE, so the next healthy
    # run records AND alerts on them.
    alerting_ok, alerting_error = alerting_is_configured(cfg, recipients)
    if not alerting_ok:
        print(f"[nsite-permits-watch] {alerting_error} — aborting BEFORE "
              f"any fetch or write. A change found now could not be emailed, and "
              f"recording it would advance the stored hash so the next run would "
              f"report 'unchanged' and never retry. Nothing is lost: fix the "
              f"configuration and the next run records and alerts normally.")
        return 1

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_permits_tabs(sheets, sheet_id)

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
            print(f"[nsite-permits-watch] {srn}: unrecognized poll cadence "
                  f"{cadence!r} ({e}) — treating as due (fail-safe).")
            due = True
        if due:
            due_sites.append(site)
        else:
            print(f"[nsite-permits-watch] {srn}: not due today "
                  f"({cadence} cadence) — skipping.")
            counts["skipped"] += 1

    # ONE tab read for every due site, not one per site. Two reasons, both
    # load-bearing: it cuts the read count (less throttling exposure), and
    # last_permits_snapshots RAISES on a read failure instead of returning a
    # per-key None. A swallowed read error here would make every site look
    # never-seen, write fresh "baseline" rows, and — because the tab is
    # append-only with last-write-wins — PERMANENTLY erase an un-alerted
    # change rather than deferring it. So a read failure aborts before any
    # write, leaving last run's state intact and correct.
    try:
        last_by_key = sw.last_permits_snapshots(
            sheets, sheet_id, [f"prmt:{s['srn']}" for s in due_sites])
    except Exception as e:  # noqa: BLE001
        print(f"[nsite-permits-watch] could not read the Permits Watch tab "
              f"({type(e).__name__}: {e}) — aborting before any DATA row is "
              f"written, so no site can be spuriously re-baselined.")
        return 1

    for site in due_sites:
        # One try per site, so NOTHING (a fetch failure, an oversized or
        # rejected Sheets write, a malformed stored snapshot, a malformed
        # registry entry) can abort the run and silently drop every site
        # queued after this one.
        try:
            srn, name, nsite_id = site["srn"], site["name"], site["id"]
            key = f"prmt:{srn}"
            label = f"nSITE Permits — {name} ({srn})"
            last = last_by_key.get(key)

            try:
                permits = nc.fetch_site_permits(session, nsite_id)
                print(f"[nsite-permits-watch] {label}: fetched "
                      f"{len(permits)} permit(s).")
            except nc.NsiteStructuralError as e:
                # NOT transient, so it must NOT take the skip-and-warn path
                # below: nSITE having changed the response shape (e.g. started
                # paging) would otherwise fail identically every single day
                # behind a green build. Checked before the base class.
                counts["fetch_failed"] += 1
                exit_code = 1
                print(f"[nsite-permits-watch] {label}: STRUCTURAL break in "
                      f"nSITE's response — this will NOT fix itself and needs "
                      f"code changes; failing loudly rather than going quiet: {e}")
                continue
            except nc.NsiteFetchError as e:
                counts["fetch_failed"] += 1
                if last is not None:
                    print(f"[nsite-permits-watch] {label}: fetch failed, "
                          f"skipping this run (baseline preserved, not diffed): {e}")
                else:
                    print(f"[nsite-permits-watch] {label}: NO BASELINE and "
                          f"fetch failed (failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                continue

            snap = permits_snapshot(permits, fields)
            result, alert_error = _diff_and_record(
                sheets, sheet_id, today, key, label, snap, cfg, recipients, last, budget)
            counts[result] += 1
            if alert_error:
                counts["alert_failed"] += 1
                exit_code = 1
        except Exception as e:  # noqa: BLE001 — isolate this site, keep the run going
            print(f"[nsite-permits-watch] {site.get('srn', '?')}: UNEXPECTED "
                  f"failure, continuing with the remaining sites: "
                  f"{type(e).__name__}: {e}")
            counts["failed"] += 1
            exit_code = 1

    print(f"[nsite-permits-watch] done — {counts['changed']} changed, "
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today, {counts['fetch_failed']} fetch-failed, "
          f"{counts['failed']} errored, {counts['alert_failed']} change(s) recorded "
          f"but NOT emailed (across {len(sites)} site"
          f"{'' if len(sites) == 1 else 's'}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
