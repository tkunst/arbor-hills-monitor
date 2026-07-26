"""
nsite_submissions_watcher.py — runs daily, watching EGLE's nSITE Submissions
profile (application/service-request intake) for every site in config.yml's
`nsite_sites` registry that has a `nsite_submissions.tiers` entry — a
DIFFERENT, larger set than the Documents `facilities:` list (ADR 021),
covering all 19 of Trisha's MiEnviro email subscriptions. Each site is
actually fetched+diffed at its own `poll` cadence (daily/biweekly/quarterly —
see TIERED CADENCE below), not every run. Standalone + self-terminating, the
same shape as rop_watcher.py / mmd_watcher.py / ride_watcher.py. See
docs/decisions/020-nsite-submissions-watch.md,
docs/decisions/021-tiered-submissions-polling.md, and
docs/decisions/022-nsite-site-registry.md (`nsite_sites` extraction — this
watcher was the first consumer, `nsite_submissions.sites` is gone).

WHY: on 2026-07-24 a JPA (EGLE/USACE Joint Permit Application — wetlands /
floodplain / inland lakes & streams / dams) for Arbor Hills reached Trisha only
via her personal MiEnviro email subscription. The monitor's nSITE integration
already talks to the right facility (once the WRD Land & Water Interface site
was added to `facilities:`) but only ever polled the Documents profile — the
JPA doesn't appear there; it's only in Submissions, a sibling profile the
monitor had never queried. This watch closes that gap for EVERY tracked
facility, not just the one the JPA happened to land on.

ONE item per facility — `subm:<srn>` (e.g. "subm:N2688") — derived from ONE
fetch per facility (nsite_client.fetch_site_submissions, unlike ROP's CSV
which derives several items from a single fetch, so no _all_baselined-style
batching is needed here: each item's fetch failure is independent).

WHAT IT DOES per facility (mirrors rop_watcher/mmd_watcher/ride_watcher):
  - build a canonical snapshot of the facility's Submissions list + hash it,
  - compare to the last snapshot recorded in the "Submissions Watch" tab,
  - FIRST sighting -> record a silent "baseline" row (no alert),
  - hash changed -> record a "changed" row THEN email an alert, THEN
  - hash unchanged -> no-op.

The diff is keyed by SUBMISSION REFERENCE NUMBER (globally unique per filing,
unlike ROP's task rows which can legitimately repeat a partial identity) —
deliberately, so the alert text tells a BRAND-NEW filing (a ref_num never seen
before) apart from an EXISTING filing's status/detail advancing (same
ref_num, a field changed). See summarize_submissions_change.

FETCH FAILURE (nsite_client.NsiteFetchError) is TRANSIENT per facility: skip-
and-warn if that facility's item already has a baseline; LOUD exit 1 if it
doesn't yet (an activation-time block must surface, not silently no-op
forever — same posture as every other watch in this repo). There is
deliberately no separate "structural break" error class the way ROP splits
RopFetchError/RopParseError — the Submissions response shape has been stable
across all 5 tracked facilities' live data through this build; a future
persistent-vs-transient split can be added if that assumption ever breaks.

GATED on nsite_submissions.enabled (Trisha directed this build live and it
was verified end-to-end via a real workflow_dispatch run before merging — see
the ADR — so it ships enabled, not disabled-by-default like an unattended
overnight new-source build).

TIERED CADENCE (ADR 021): each srn in `nsite_submissions.tiers` maps to a
`daily|biweekly|quarterly` poll cadence (site identity itself lives in the
shared `nsite_sites` registry, ADR 022). `_is_due` is a pure, deterministic
gate — no stored "last polled" state needed — that hash-staggers sites within
a tier across the period so they don't all land on the same day, and fires
across a 3-day WINDOW per period rather than one exact day, so a single
missed/failed run (a GitHub Actions runner-acquisition miss, the same failure
class this repo hit on rop-watch the morning of this build) doesn't blank a
quarterly site out for a full quarter. A site that isn't due today is skipped
entirely — no fetch, no Sheet read, no diff.

NO DRIVE / OAUTH (same scope call as pfas_watcher/rop_watcher/etc, ADR 012):
the deliverable is the ALERT + the durable Sheet row. SMTP + Sheets (both
already live) are all this needs.

Runs daily (see .github/workflows/nsite-submissions-watch.yml).
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
    nsite_submissions.enabled is set) has a direct unit test. Mirrors
    rop_watcher/mmd_watcher/ride_watcher._should_run."""
    if not (cfg.get("nsite_submissions") or {}).get("enabled"):
        return False, "nsite_submissions.enabled is false — skipping (no-op)."
    return True, ""


_POLL_PERIOD_DAYS = {"biweekly": 14, "quarterly": 90}
_DUE_WINDOW_DAYS = 3  # a due tier fires on this many consecutive days per
                       # period, not one exact day — see ADR 021 Decision 3.


def _is_due(cadence: str, srn: str, today: date) -> bool:
    """Pure: does `srn`'s cadence say to poll on `today`? "daily" is always
    due. "biweekly"/"quarterly" stagger across sites via a stable hash of
    srn (so same-tier sites don't all land on the same day) and are due for
    a _DUE_WINDOW_DAYS-day window each period rather than one exact day, so
    one missed/failed run doesn't push a quarterly site's next check out a
    full quarter — it just retries the next day or two within the same
    window. Deterministic and stateless: no "last polled" tracking needed."""
    if cadence == "daily":
        return True
    period = _POLL_PERIOD_DAYS[cadence]
    offset = int(hashlib.sha256(srn.encode("utf-8")).hexdigest(), 16) % period
    return (today.toordinal() - offset) % period < _DUE_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Snapshot / diff (pure)
# ---------------------------------------------------------------------------

_SUBMISSION_FIELDS = (
    "ref_num", "form_name", "form_type", "program_area", "status", "received_date", "descr",
)


def submissions_snapshot(rows: list[dict]) -> dict:
    """Canonical, hash-stable snapshot of one facility's Submissions list.
    Sorted by ref_num alone — a Submission Reference Number is globally
    unique per filing (unlike ROP's task rows, which can share a partial
    identity), so there's no ROP-style need to sort on the full field tuple
    to keep the hash stable."""
    keyed = sorted(
        ({f: r.get(f, "") for f in _SUBMISSION_FIELDS} for r in rows),
        key=lambda d: d["ref_num"],
    )
    return {"rows": keyed}


def snapshot_hash(snap: dict) -> str:
    """A stable short hash of a canonical snapshot (sorted-key JSON -> sha256).
    Same idiom as rop_watcher.snapshot_hash / mmd_watcher / ride_watcher."""
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def summarize_submissions_change(old: dict, new: dict) -> tuple[str, str]:
    """(note, body) describing what changed between two Submissions snapshots.
    Keyed by ref_num rather than a Counter-over-full-tuples diff (ROP's
    idiom) — deliberately, so a BRAND-NEW filing (a ref_num never seen
    before — the JPA case this watch exists for) reads distinctly in the
    alert from an EXISTING filing's status/detail advancing (same ref_num, a
    field changed). Pure — unit-tested."""
    old_by_ref = {r["ref_num"]: r for r in old.get("rows", [])}
    new_by_ref = {r["ref_num"]: r for r in new.get("rows", [])}

    new_refs = sorted(set(new_by_ref) - set(old_by_ref))
    removed_refs = sorted(set(old_by_ref) - set(new_by_ref))
    changed_refs = sorted(
        ref for ref in (set(new_by_ref) & set(old_by_ref))
        if new_by_ref[ref] != old_by_ref[ref]
    )

    parts: list[str] = []
    lines: list[str] = []

    for ref in new_refs:
        r = new_by_ref[ref]
        parts.append("new submission received")
        lines.append(
            f"+ NEW SUBMISSION  {ref} — {r['form_name']} "
            f"(type={r['form_type'] or '—'}, program={r['program_area'] or '—'}, "
            f"status={r['status'] or '—'}, received={r['received_date'] or '—'})"
        )
    for ref in changed_refs:
        old_r, new_r = old_by_ref[ref], new_by_ref[ref]
        parts.append("existing submission changed")
        changed_fields = ", ".join(
            f"{f}: {old_r.get(f) or '—'} -> {new_r.get(f) or '—'}"
            for f in _SUBMISSION_FIELDS
            if f != "ref_num" and old_r.get(f) != new_r.get(f)
        )
        lines.append(f"~ CHANGED  {ref} — {new_r['form_name']} ({changed_fields})")
    for ref in removed_refs:
        r = old_by_ref[ref]
        parts.append("submission no longer listed")
        lines.append(f"- REMOVED  {ref} — {r['form_name']}")

    if not parts:
        return "changed (no ref-level diff — see snapshot)", ""
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return "; ".join(seen), "\n".join(lines)


def format_change_body(label: str, note: str, body: str) -> str:
    """The change-alert email body. Pure — unit-tested."""
    shown = body or "(no further detail — see the Submissions Watch tab's Snapshot JSON.)"
    return (
        "A watched Arbor Hills nSITE Submissions list changed.\n\n"
        f"Source:  {label}\n"
        f"Change:  {note}\n\n"
        "What changed:\n\n"
        f"{shown}\n\n"
        "This is an automated watch on EGLE's nSITE Submissions profile "
        "(application / service-request intake) — trip-wiring a brand-new "
        "filing the moment EGLE records it under this nSITE site, or an "
        "existing filing's status advancing. Review MiEnviro directly for "
        "full context.\n"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _diff_and_record(sheets, sheet_id, today, key, label, snap, cfg, recipients) -> str:
    """Baseline/compare/record/alert for one facility. Returns "baseline" /
    "changed" / "unchanged". Durable row FIRST, alert email SECOND (best-
    effort) — a crash between them loses the alert, never the record, and
    never re-fires next run since the row already advanced the stored hash."""
    new_hash = snapshot_hash(snap)
    snap_json = json.dumps(snap, sort_keys=True, ensure_ascii=False)
    last = sw.last_submissions_snapshot(sheets, sheet_id, key)

    if last is None:
        sw.append_submissions_watch_row(sheets, sheet_id, today, key, label, "baseline",
                                         new_hash, "initial snapshot (no alert)", _now(), snap_json)
        print(f"[nsite-submissions-watch] {label}: baseline recorded ({new_hash}).")
        return "baseline"

    last_hash, last_snap_json = last
    if new_hash == last_hash:
        print(f"[nsite-submissions-watch] {label}: unchanged ({new_hash}).")
        return "unchanged"

    old_snap = _load_json(last_snap_json, {})
    note, body = summarize_submissions_change(old_snap, snap)
    sw.append_submissions_watch_row(sheets, sheet_id, today, key, label, "changed",
                                     new_hash, note, _now(), snap_json)
    print(f"[nsite-submissions-watch] {label}: CHANGED ({last_hash} -> {new_hash}; {note}).")
    # The row above is already durable — everything from here down is
    # best-effort alerting for THIS facility only, so a bug in either step
    # can never escape _diff_and_record and abort run()'s processing of every
    # other independent facility.
    try:
        email_body = format_change_body(label, note, body)
    except Exception as e:  # noqa: BLE001 — formatting is best-effort; row is recorded
        print(f"[nsite-submissions-watch] {label}: change recorded but alert body "
              f"FORMATTING failed: {e}")
        return "changed"
    try:
        ea.send_email(f"[Submissions watch] {label} changed", email_body, cfg,
                       recipients=recipients)
    except Exception as e:  # noqa: BLE001 — alert is best-effort; row is recorded
        print(f"[nsite-submissions-watch] {label}: change recorded but alert email "
              f"FAILED: {e}")
    return "changed"


def run() -> int:
    cfg = load_config()
    should_run, reason = _should_run(cfg)
    if not should_run:
        print(f"[nsite-submissions-watch] {reason}")
        return 0

    scfg = cfg.get("nsite_submissions") or {}
    recipients = scfg.get("recipients") or None  # None -> full alert_recipients list
    # Resolve the working site list by joining the shared identity registry
    # (nsite_sites, ADR 022) with this profile's own cadence map. A `tiers`
    # srn absent from the registry is a config error — KeyError raises
    # naturally, on purpose (see ADR 022): a loud failure on a config typo is
    # correct, a silently-unwatched site is not.
    registry = {s["srn"]: s for s in cfg.get("nsite_sites") or []}
    sites = [
        {**registry[srn], "poll": poll}
        for srn, poll in (scfg.get("tiers") or {}).items()
    ]

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_submissions_tabs(sheets, sheet_id)

    session = nc.make_session()
    today_date = _today_date()
    today = today_date.isoformat()
    counts = {"baseline": 0, "changed": 0, "unchanged": 0, "skipped": 0}
    exit_code = 0

    for site in sites:
        srn, name, nsite_id = site["srn"], site["name"], site["id"]
        cadence = site.get("poll", "daily")
        key = f"subm:{srn}"
        label = f"nSITE Submissions — {name} ({srn})"

        if not _is_due(cadence, srn, today_date):
            print(f"[nsite-submissions-watch] {label}: not due today ({cadence} cadence) — skipping.")
            counts["skipped"] += 1
            continue

        try:
            subs = nc.fetch_site_submissions(session, nsite_id)
            print(f"[nsite-submissions-watch] {label}: fetched {len(subs)} submission(s).")
        except nc.NsiteFetchError as e:
            has_baseline = sw.last_submissions_snapshot(sheets, sheet_id, key) is not None
            if has_baseline:
                print(f"[nsite-submissions-watch] {label}: fetch failed, skipping this run "
                      f"(baseline preserved, not diffed): {e}")
            else:
                print(f"[nsite-submissions-watch] {label}: NO BASELINE and fetch failed "
                      f"(failing loudly so activation surfaces it): {e}")
                exit_code = 1
            continue

        snap = submissions_snapshot(subs)
        result = _diff_and_record(sheets, sheet_id, today, key, label, snap, cfg, recipients)
        counts[result] += 1

    print(f"[nsite-submissions-watch] done — {counts['changed']} changed, "
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today (across {len(sites)} site"
          f"{'' if len(sites) == 1 else 's'}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
