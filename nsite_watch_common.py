"""
nsite_watch_common.py — shared scaffolding for the six nSITE-profile daily
watchers: nsite_violations_watcher.py (Stream L, ADR 023),
nsite_compliance_actions_watcher.py (Stream M, ADR 028),
nsite_evaluations_watcher.py (Stream N, ADR 029), nsite_permits_watcher.py
(Stream O, ADR 030), nsite_complaints_watcher.py (Stream P, ADR 031) and
nsite_public_notices_watcher.py (Stream Q, ADR 032).

Each of those six modules had its OWN byte-identical copy of `_now`,
`_today_date`, `_load_json`, `alerting_is_configured` and (five of six)
`_row_digest`, plus a `run()` body that was ~72% similar across all six once
the tab/label/noun/snapshot-builder differences are normalized out. This
module is the ONE place that scaffolding lives now.

DELIBERATELY NOT consolidated here (per the handoff's explicit instruction —
see docs/overnight-coder-handoffs/sheet-writer-watcher-consolidation.md):

  - `nsite_submissions_watcher.py` — only ~28% similar to this family (a
    different, wider site set via `nsite_submissions.tiers`, no
    `snapshot_char_budget` concept, a simpler single-ref-number diff). Left
    completely alone, including its own `_is_due`. Every one of the six
    watchers already imported `_is_due` directly from
    `nsite_submissions_watcher` (never copied — each has its own identity
    test asserting e.g. `vw._is_due is nsite_submissions_watcher._is_due`);
    THIS module does NOT re-import it — `run_profile_watch()` below takes
    `is_due_fn` as a plain parameter instead, so each watcher's own `run()`
    passes ITS OWN current `_is_due` module-global. That is load-bearing,
    not a style choice: an existing test monkeypatches a single watcher's
    `_is_due` directly (`monkeypatch.setattr(vw, "_is_due", ...)`) and
    expects `run()` to pick up the replacement — which only happens if the
    lookup is late-bound through that module's own globals, not resolved
    once at nsite_watch_common's own import time.
  - Any of the 8 watchers PR #137 gave per-item isolation (rop/mmd/ride/
    pfas_pws/nsite_submissions/pfas/civicclerk/gfl_info_site) or
    wds_archiver — different family, different PR.
  - The diff design itself: `violations_snapshot`/`compliance_actions_
    snapshot`/`evaluations_snapshot`/`permits_snapshot`/`complaints_
    snapshot`/`public_notices_snapshot`, `snapshot_hash`, `_cell_payload`,
    `_digest_counter`/`_counter_of` (where they exist), `diff_fields`,
    `summarize_*_change`, `format_change_body`, and each watcher's own
    `_diff_and_record`. Multiset (Violations/Compliance Actions),
    ref-number-keyed (Evaluations/Permits/Public Notices) and fingerprint
    (Complaints) diffs are deliberate (ADR 023/029/031) and are NOT merged
    — only the orchestration AROUND them is. Each watcher binds its own
    `_diff_and_record`'s extra keyword (`budget`, and for Public Notices
    also `suppress_rop_matches`) via `functools.partial` BEFORE calling
    `run_profile_watch()`, so `_diff_and_record` itself needed no signature
    change in any of the six files.

Every watcher still exposes `_now`, `_today_date`, `_load_json`,
`alerting_is_configured`, `_row_digest`, `_should_run` and `run` as module-
level names (most by direct re-import, `_should_run`/`run` as thin
wrappers) — see each watcher's own identity test — so no external caller or
existing test changes.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from typing import Callable

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import email_alerts as ea
import nsite_client as nc

__all__ = [
    "_now", "_today_date", "_load_json", "alerting_is_configured",
    "_row_digest", "should_run", "run_profile_watch",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_date() -> date:
    return (datetime.now(_ET) if _ET else datetime.now()).date()


def _load_json(raw: str, fallback):
    """Parse a stored snapshot cell, falling back for anything that isn't a
    JSON OBJECT — not merely for anything unparseable. A bare scalar (`0`,
    `null`, `true`) parses fine but is not a mapping, and every downstream
    reader does `"fields" not in old` / `"hash" not in old` / `old.get(...)`,
    which raises TypeError on an int. That would wedge the site permanently —
    no row, no alert, the same failure every run — which is exactly what the
    unreadable-snapshot branch exists to prevent."""
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


def _row_digest(values: list) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]


def should_run(cfg: dict, config_key: str) -> tuple[bool, str]:
    """Pure gate — testable without any Sheets/network mocking, so the exact
    bug this guards (a watch doing real work / emailing before its own
    `<config_key>.enabled` is set) has a direct unit test. Generalizes each
    watcher's own `_should_run`, which was identical modulo `config_key`."""
    if not (cfg.get(config_key) or {}).get("enabled"):
        return False, f"{config_key}.enabled is false — skipping (no-op)."
    return True, ""


def run_profile_watch(
    *,
    cfg: dict,
    log_prefix: str,
    config_key: str,
    key_prefix: str,
    label_prefix: str,
    unit_noun: str,
    tab_name: str,
    ensure_tabs_fn: Callable[..., None],
    last_snapshots_fn: Callable[..., dict],
    is_due_fn: Callable[..., bool],
    fetch_fn: Callable[..., list],
    build_snapshot_fn: Callable[[list], dict],
    diff_and_record_fn: Callable[..., tuple[str, str | None]],
    extra_counts: tuple[str, ...] = (),
    extra_summary_fn: Callable[[dict], str] | None = None,
    extra_abort_note: str = "",
) -> int:
    """The orchestration shared by all six nSITE profile watchers' `run()`:
    the `<config_key>.enabled` gate, resolving the working site list from the
    shared `nsite_sites` registry (ADR 022) joined with `<config_key>.tiers`,
    the alerting-configured pre-flight (never consume a change it can't
    deliver), the per-site cadence (`is_due_fn`) gate, ONE batched tab read
    for every due site (never per-site — see `last_snapshots_fn`'s own
    docstring for why a swallowed read failure here would be a silent
    data-loss bug), the per-site fetch with the STRUCTURAL-vs-transient
    `nc.NsiteFetchError` split (checked in that order —
    `NsiteStructuralError` is a subclass), and the final summary line.
    Returns the process exit code.

    `is_due_fn` and `diff_and_record_fn` are taken as PARAMETERS rather than
    looked up as a shared `_is_due`/per-profile `_diff_and_record` here, on
    purpose: each watcher's own `run()` passes its OWN current module-global
    (`_is_due`, `_diff_and_record`), looked up fresh every time `run()`
    executes — so an existing test's `monkeypatch.setattr(vw, "_is_due",
    ...)` or `monkeypatch.setattr(vw, "_diff_and_record", ...)` still reaches
    this function's behavior exactly as it did before this refactor.

    By contrast, `should_run()`/`alerting_is_configured()`/`_today_date()`
    used below are THIS module's own implementations, not re-dispatched
    through the calling watcher's re-exported copies — unlike `is_due_fn`/
    `diff_and_record_fn`, nothing here looks them up via the watcher's
    globals. That is safe today only because no existing test monkeypatches
    `<watcher>._today_date`/`<watcher>.alerting_is_configured`/
    `<watcher>._should_run` and expects `run()` itself to pick up the
    replacement (every existing use of those three is a direct, standalone
    call like `vw.alerting_is_configured(cfg, recipients)`, which still
    works since it's the same shared object). A future test that DOES need
    to patch one of those three at the `run()`-call level would need the
    same parameter treatment `is_due_fn`/`diff_and_record_fn` got here.

    Each caller supplies ITS OWN diff design via `build_snapshot_fn` (a
    one-argument closure over whatever extra config that profile's snapshot
    builder needs — `fields` for the multiset/ref-keyed profiles, a plain
    window size for Complaints) and `diff_and_record_fn` (its own
    `_diff_and_record`, bound via `functools.partial` to its own `budget`
    and, for Public Notices, `suppress_rop_matches`, so the 9-positional-
    argument call below is identical for every profile).

    `extra_counts`/`extra_summary_fn` exist for Public Notices' one real
    extra: a `"changed_suppressed"` result kind and its own clause in the
    done-summary line (ADR 032's ROP-suppression heuristic). The other five
    profiles pass neither and get the plain five-profile summary line."""
    should, reason = should_run(cfg, config_key)
    if not should:
        print(f"{log_prefix} {reason}")
        return 0

    scfg = cfg.get(config_key) or {}
    recipients = scfg.get("recipients") or None  # None -> full alert_recipients list

    # Resolve the working site list by joining the shared identity registry
    # (nsite_sites, ADR 022) with THIS profile's own cadence map. A `tiers`
    # srn absent from the registry is a config error — KeyError raises
    # naturally, on purpose (ADR 022): a loud failure on a config typo is
    # correct, a silently-unwatched site is not.
    registry = {s["srn"]: s for s in cfg.get("nsite_sites") or []}
    sites = [
        {**registry[srn], "poll": poll}
        for srn, poll in (scfg.get("tiers") or {}).items()
    ]

    # If alerting is ALREADY known to be impossible, stop before touching
    # anything. Writing a row here would advance the stored hash, so
    # tomorrow's run would compare equal, report "unchanged", and never
    # retry — the notification would be gone permanently even after the
    # secret was fixed. Nothing is lost by stopping: the underlying records
    # are still in nSITE, so the next healthy run records AND alerts on
    # them. (A send failure discovered mid-run, after the row is already
    # durable, is unavoidable and handled separately in each profile's own
    # `_diff_and_record`.)
    alerting_ok, alerting_error = alerting_is_configured(cfg, recipients)
    if not alerting_ok:
        print(f"{log_prefix} {alerting_error} — aborting BEFORE any "
              f"fetch or write. A change found now could not be emailed, and "
              f"recording it would advance the stored hash so the next run would "
              f"report 'unchanged' and never retry. Nothing is lost: fix the "
              f"configuration and the next run records and alerts normally.")
        return 1

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    ensure_tabs_fn(sheets, sheet_id)

    session = nc.make_session()
    today_date = _today_date()
    today = today_date.isoformat()
    counts = {"baseline": 0, "changed": 0, "unchanged": 0, "skipped": 0,
              "fetch_failed": 0, "failed": 0, "alert_failed": 0}
    for extra_key in extra_counts:
        counts[extra_key] = 0
    exit_code = 0

    # Cadence gate first, so the batched read below asks only about sites
    # this run will actually touch. An UNRECOGNIZED cadence is treated as
    # due rather than raising: unlike a `tiers` srn missing from the
    # registry (we don't know WHAT to poll, so ADR 022 fails loudly), an
    # unknown cadence only means we don't know HOW OFTEN — and polling every
    # run is the complete, fail-safe answer. A typo must never blind a site.
    due_sites = []
    for site in sites:
        cadence = site.get("poll", "daily")
        srn = site["srn"]
        try:
            due = is_due_fn(cadence, srn, today_date)
        except Exception as e:  # noqa: BLE001 — unknown cadence -> poll, don't skip
            print(f"{log_prefix} {srn}: unrecognized poll cadence "
                  f"{cadence!r} ({e}) — treating as due (fail-safe).")
            due = True
        if due:
            due_sites.append(site)
        else:
            print(f"{log_prefix} {srn}: not due today "
                  f"({cadence} cadence) — skipping.")
            counts["skipped"] += 1

    # ONE tab read for every due site, not one per site. Two reasons, both
    # load-bearing: it cuts the read count (less throttling exposure), and
    # `last_snapshots_fn` RAISES on a read failure instead of returning a
    # per-key None. A swallowed read error here would make every site look
    # never-seen, write fresh "baseline" rows, and — because the tab is
    # append-only with last-write-wins — PERMANENTLY erase an un-alerted
    # change rather than deferring it. So a read failure aborts before any
    # write, leaving last run's state intact and correct.
    try:
        last_by_key = last_snapshots_fn(
            sheets, sheet_id, [f"{key_prefix}:{s['srn']}" for s in due_sites])
    except Exception as e:  # noqa: BLE001
        print(f"{log_prefix} could not read the {tab_name} tab "
              f"({type(e).__name__}: {e}) — aborting before any DATA row is "
              f"written, so no site can be spuriously re-baselined."
              f"{extra_abort_note}")
        return 1

    for site in due_sites:
        # One try per site, so NOTHING (a fetch failure, an oversized or
        # rejected Sheets write, a malformed stored snapshot, a malformed
        # registry entry) can abort the run and silently drop every site
        # queued after this one.
        try:
            srn, name, nsite_id = site["srn"], site["name"], site["id"]
            key = f"{key_prefix}:{srn}"
            label = f"{label_prefix} — {name} ({srn})"
            last = last_by_key.get(key)

            try:
                rows = fetch_fn(session, nsite_id)
                print(f"{log_prefix} {label}: fetched "
                      f"{len(rows)} {unit_noun}.")
            except nc.NsiteStructuralError as e:
                # NOT transient, so it must NOT take the skip-and-warn path
                # below: nSITE having changed the response shape (e.g.
                # started paging) would otherwise fail identically every
                # single day behind a green build. Checked before the base
                # class.
                counts["fetch_failed"] += 1
                exit_code = 1
                print(f"{log_prefix} {label}: STRUCTURAL break in "
                      f"nSITE's response — this will NOT fix itself and needs "
                      f"code changes; failing loudly rather than going quiet: {e}")
                continue
            except nc.NsiteFetchError as e:
                counts["fetch_failed"] += 1
                if last is not None:
                    print(f"{log_prefix} {label}: fetch failed, skipping "
                          f"this run (baseline preserved, not diffed): {e}")
                else:
                    print(f"{log_prefix} {label}: NO BASELINE and fetch "
                          f"failed (failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                continue

            snap = build_snapshot_fn(rows)
            result, alert_error = diff_and_record_fn(
                sheets, sheet_id, today, key, label, snap, cfg, recipients, last)
            counts[result] += 1
            if alert_error:
                counts["alert_failed"] += 1
                exit_code = 1
        except Exception as e:  # noqa: BLE001 — isolate this site, keep the run going
            print(f"{log_prefix} {site.get('srn', '?')}: UNEXPECTED "
                  f"failure, continuing with the remaining sites: "
                  f"{type(e).__name__}: {e}")
            counts["failed"] += 1
            exit_code = 1

    extra_summary = extra_summary_fn(counts) if extra_summary_fn else ""
    print(f"{log_prefix} done — {counts['changed']} changed, {extra_summary}"
          f"{counts['baseline']} baselined, {counts['unchanged']} unchanged, "
          f"{counts['skipped']} not-due-today, {counts['fetch_failed']} fetch-failed, "
          f"{counts['failed']} errored, {counts['alert_failed']} change(s) recorded "
          f"but NOT emailed (across {len(sites)} site"
          f"{'' if len(sites) == 1 else 's'}).")
    return exit_code
