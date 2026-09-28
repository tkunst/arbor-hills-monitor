"""
mpart_watcher.py — Stream V: daily watch on MPART's PFAS open-data layers for the
Johnson Drain / Johnson Creek / upper-Rouge area around Arbor Hills. See
docs/decisions/060-mpart-data-watch.md and mpart_client.py.

WHY: the monitor watches MPART's Arbor Hills WEB PAGE (ADR 012) and the public-water-
supply layer (Stream R) but none of the layers that hold the surface-water and fish
results. The Johnson Drain fish results (8/5/2021) and the surface-water results were
found by hand in 9/2026; nothing would have caught a new sample.

WHAT TO EXPECT: EGLE describes both layers as STATIC PULLS (surface water "last pulled
2/2025"; fish "01/14/2026 … updated annually"). A "new" row therefore means EGLE
republished the layer, not that sampling just happened — the collection date says when.
Expect this watch to be quiet for months and then fire when EGLE re-pulls.

THREE watched items, each one query -> a {row key: row hash} snapshot (plus, for surface
water, the set of rows currently above a screening value):
  - mpart:sw     surface-water PFAS samples inside the bounding box
  - mpart:fish   fish PFOS results at the watched stations + the box
  - mpart:sites  MPART's PFAS sites / areas of interest: the Arbor Hills row + the box
NOT here: the public-water-supply layer — the live Stream R already watches it.

WHAT IT DOES per item (the RIDE / PFAS-PWS watch idiom):
  - FIRST sighting -> a silent `baseline` row (no alert); the rows already above a
    screening value at that moment are recorded in the snapshot, so they are never
    announced later as if new;
  - snapshot unchanged -> no-op;
  - a NEW / changed / removed row -> a `changed` row THEN an alert (durable row first).
    A new or changed surface-water row is compared with the Rule 57 non-drinking-water
    human non-cancer values (config `thresholds_ng_l`); only a (row, analyte) pair that
    was NOT already above its value in the stored snapshot raises the subject. The
    comparison is a SCREENING one against the published value — never a regulatory
    determination — applied only when the unit reads as ng/L, when the sample was
    collected on/after the year the value was verified, and never to a 'K' flag (the
    value is the method detection limit). Fish results carry EGLE's own code
    definitions and no threshold.

FAILURE MODES
  - A transient fetch failure is skip-and-warn for a baselined item, but every skipped
    run is RECORDED (`fetch-skipped`); the run that makes it `stale_alert_after_skips`
    (default 3) consecutive skips sends a liveness alert (repeated weekly while the
    outage lasts); the first good run records `fetch-ok`.
  - A SUCCESSFUL response that is empty or has shrunk by more than `max_shrink_fraction`
    (default half) is treated as suspect — recorded as a skip, never diffed — until it
    persists `accept_shrink_after_skips` (default 2) runs, so a republish glitch cannot
    fire "REMOVED (17)" and then re-announce everything as new.
  - A never-baselined item that can't be fetched is LOUD (exit 1); a structural break
    (schema drift, truncated result, rejected query, blank key, oversized snapshot) is
    ALWAYS loud; any other exception in one item is reported and the run moves on to the
    next item (exit 1).
  - A configured recipient whose alert could not be SENT makes the run exit 1 (the row
    is already written, so it will not re-fire — the red run is the signal).
  - The Sheet read raises instead of swallowing errors (a swallowed read would look like
    "never baselined" and absorb genuinely new samples into a fresh baseline).

Recipients are scoped VERBATIM (Trisha only); an EMPTY list is display-only — rows, no
email — and never falls back to the coalition alert list.

GATED on mpart.enabled (a brand-new external source; ships false). `--probe` fetches
every layer and prints the counts regardless of the flag, writing nothing.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    _ET = None

import drive_client as dc
import email_alerts as ea
import mpart_client as mc
import sheet_writer as sw
from config_loader import load_config

_DEFAULT_STALE_SKIPS = 3
_LIVENESS_REPEAT_EVERY = 7        # once the threshold is hit, remind every N further skipped runs
_DEFAULT_MAX_SHRINK = 0.5
_DEFAULT_ACCEPT_SHRINK_AFTER = 2
_EMAIL_LIST_CAP = 25

# Rule 57 non-drinking-water human non-cancer values, ng/L, from EGLE's Rule 57 values
# spreadsheet as saved 2026-09-26 (Lotext .../egle-rule-57-water-quality-values-2026-09-26).
# Config `mpart.thresholds_ng_l` is the live source; these are the documented defaults.
DEFAULT_THRESHOLDS_NG_L = {"PFOS": 12, "PFOA": 170, "PFHxS": 210, "PFNA": 30}
# The year each value was verified into that spreadsheet (PFOS 2014; PFOA 2022 — replacing
# an older, far higher value; PFHxS and PFNA were ADDED in 2023). A sample collected before
# a value's year is not screened: no such value was in force at the sample date.
DEFAULT_VERIFIED_YEAR = {"PFOS": 2014, "PFOA": 2022, "PFHxS": 2023, "PFNA": 2023}

ITEM_SW, ITEM_FISH, ITEM_SITES = "mpart:sw", "mpart:fish", "mpart:sites"
LABELS = {
    ITEM_SW: "MPART surface-water PFAS samples (PfasOpenData layer 0; Johnson Drain / Johnson Creek / upper Rouge box)",
    ITEM_FISH: "MPART fish PFOS results (FcmpOpenData; Johnson Drain stations + the box)",
    ITEM_SITES: "MPART PFAS sites and areas of interest (Arbor Hills + the box)",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return (datetime.now(_ET) if _ET else datetime.now()).date().isoformat()


def _should_run(cfg: dict) -> tuple[bool, str]:
    if not (cfg.get("mpart") or {}).get("enabled"):
        return False, "mpart.enabled is false — skipping (no-op)."
    return True, ""


def load_thresholds(m: dict) -> tuple[dict[str, float], dict[str, int]]:
    """(thresholds, verified_year) from config, validated: an unknown analyte key (e.g. a
    typo such as 'PFHXS') raises ValueError instead of silently disabling a screen."""
    raw = m.get("thresholds_ng_l") or DEFAULT_THRESHOLDS_NG_L
    years = m.get("thresholds_verified_year") or DEFAULT_VERIFIED_YEAR
    for name, table in (("thresholds_ng_l", raw), ("thresholds_verified_year", years)):
        bad = sorted(set(table) - set(mc.ANALYTES))
        if bad:
            raise ValueError(f"mpart.{name}: unknown analyte key(s) {bad}; valid: {sorted(mc.ANALYTES)}")
    return ({k: float(v) for k, v in raw.items()}, {k: int(v) for k, v in years.items()})


# ---------------------------------------------------------------------------
# Screening (pure)
# ---------------------------------------------------------------------------


def is_nondetect(flag: str) -> bool:
    """A surface-water flag means non-detect (the value shown is the method detection
    limit) when it carries the 'K' token. Other qualifiers (J, Q, B, E, I …) are
    printed as published and do NOT suppress a comparison."""
    return "K" in mc.flag_tokens(flag)


def screen_surface_water(view: dict, thresholds: dict, verified_year: dict | None = None) -> list[dict]:
    """Analytes in one canonical surface-water row whose reported value is ABOVE its
    threshold. Pure. Not screened at all when: the unit does not read as ng/L; the sample
    was collected before the year that analyte's value was verified; the flag is K; the
    value is blank or not a number. [{analyte, value, flag, threshold, estimated}]."""
    if mc.normalize_unit(view.get("unit", "")) != "ng/L":
        return []
    try:
        sample_year = int(view.get("date", "")[:4])
    except ValueError:
        sample_year = None
    out = []
    for analyte, limit in thresholds.items():
        raw = view.get(analyte, "")
        if raw == "":
            continue
        since = (verified_year or {}).get(analyte)
        if since and sample_year is not None and sample_year < since:
            continue
        flag = view.get(f"{analyte}_flag", "")
        if is_nondetect(flag):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > float(limit):
            out.append({"analyte": analyte, "value": raw, "flag": flag, "threshold": limit,
                        "estimated": "J" in mc.flag_tokens(flag)})
    return out


def hit_keys(views: dict[str, dict], thresholds: dict, verified_year: dict) -> set[str]:
    """{'<row key>|<analyte>'} for every row currently above a screening value."""
    return {f"{k}|{h['analyte']}" for k, v in views.items() for h in screen_surface_water(v, thresholds, verified_year)}


# ---------------------------------------------------------------------------
# Snapshot state + diff (pure)
# ---------------------------------------------------------------------------


def build_state(rows: list[list]) -> dict:
    """Fold the append-only tab into {item: {hash, snapshot, skips}} — the LAST
    baseline/changed row per item is its snapshot; `skips` counts consecutive
    `fetch-skipped` rows since the last baseline / changed / fetch-ok row. Tolerates rows
    Sheets returned with trailing empty cells stripped."""
    items: dict[str, dict] = {}
    for r in rows:
        if len(r) < 4:
            continue
        item, change = r[1], r[3]
        st = items.setdefault(item, {"hash": "", "snapshot": "", "skips": 0})
        if change in ("baseline", "changed"):
            st.update(hash=r[4] if len(r) > 4 else "", snapshot=r[7] if len(r) > 7 else "", skips=0)
        elif change == "fetch-skipped":
            st["skips"] += 1
        elif change == "fetch-ok":
            st["skips"] = 0
    return {k: v for k, v in items.items() if v["hash"]}


def diff_index(old: dict[str, str], new: dict[str, str]) -> dict:
    """{added, removed, changed} row keys between two {key: row_hash} indexes. Pure."""
    return {
        "added": sorted(k for k in new if k not in old),
        "removed": sorted(k for k in old if k not in new),
        "changed": sorted(k for k in new if k in old and old[k] != new[k]),
    }


def is_suspect_shrink(old_n: int, new_n: int, max_shrink_fraction: float) -> bool:
    """True when a baselined item that had rows now returns none, or fewer than
    `max_shrink_fraction` of what it had — likelier a republish glitch than a real change."""
    return old_n > 0 and (new_n == 0 or new_n < old_n * max_shrink_fraction)


# ---------------------------------------------------------------------------
# Alert copy (pure)
# ---------------------------------------------------------------------------


def _value(view: dict, analyte: str) -> str:
    v = view.get(analyte, "")
    flag = view.get(f"{analyte}_flag", "")
    mdl = view.get(f"{analyte}_mdl", "")
    if v == "":
        return f"{analyte} — [{flag}]" if flag else f"{analyte} —"
    return f"{analyte} {v}" + (f" [{flag}]" if flag else "") + (f" (MDL {mdl})" if mdl else "")


def describe_surface_water(v: dict) -> str:
    where = ", ".join(x for x in (v["waterbody"], v["description"]) if x) or "location not stated"
    unit = v["unit"] or "unit not stated"
    unscreened = f" — NOT screened (unit {unit!r} does not read as ng/L)" if mc.normalize_unit(v["unit"]) != "ng/L" else ""
    kind = f" | {v['sample_type']}" if v["sample_type"] else ""
    return (f"- collected {v['date'] or 'date unknown'} | site {v['site'] or '?'} | {where}{kind} | sample {v['key']}\n"
            f"    {'; '.join(_value(v, a) for a in mc.ANALYTES)} ({unit}){unscreened}")


def describe_fish(v: dict) -> str:
    code, ppb = v["pfos_code"], v["pfos_ppb"]
    meaning = mc.fish_code_meaning(code) if code else ""
    pfos = (f"PFOS {ppb} ppb (edible portion)" if ppb != "" else "PFOS — no concentration reported")
    if meaning:
        pfos += f" — {meaning}"
    return (f"- collected {v['date'] or 'date unknown'} | station {v['station']} | {v['waterbody']}, {v['location']} | "
            f"{v['species'] or 'species not stated'} | sample {v['key']}\n    {pfos}")


def describe_site(v: dict) -> str:
    return (f"- {v['name']} ({v['kind']}, {v['type'] or 'type not stated'}) | {v['address']}, {v['city']}, "
            f"{v['county']} | residential wells sampled: {v['residential_wells'] or 'not stated'}"
            f"{' | ' + v['webpage'] if v['webpage'] else ''}")


DESCRIBE = {ITEM_SW: describe_surface_water, ITEM_FISH: describe_fish, ITEM_SITES: describe_site}


def screen_note(thresholds: dict, verified_year: dict) -> str:
    """The surface-water caveat paragraph, rendered from the thresholds actually in use."""
    vals = "; ".join(f"{a} {thresholds[a]:g} ng/L (verified {verified_year.get(a, '?')})" for a in thresholds)
    return (
        "Rule 57 comparison: the non-drinking-water human non-cancer values (HNV) in EGLE's Rule 57 "
        f"spreadsheet — {vals}. Johnson Drain / Johnson Creek is a non-drink water body. This is a "
        "SCREENING comparison of the reported value with the published value, not a regulatory "
        "determination. Values are compared only for samples collected on or after the year each was "
        "verified (the PFOA value replaced an older one in 2022; the PFHxS and PFNA values were added "
        "in 2023) and only when the unit reads as ng/L. A 'K' flag (below the method detection limit; "
        "the value shown is the detection limit) is never compared. Other lab flags (J, Q, B, E, I …) "
        "are printed as published; EGLE notes that qualifier definitions vary by report — check the "
        "analytical report."
    )


_STATIC_NOTE = (
    "EGLE describes this layer as a static pull (surface water: last pulled 2/2025; fish: pulled "
    "01/14/2026, updated annually): a new or changed row means EGLE republished the layer — the "
    "collection date says when the sample was taken."
)


def format_change_body(item: str, diff: dict, views: dict[str, dict], hit_map: dict[str, list[dict]],
                       new_hits: set[str], thresholds: dict | None = None,
                       verified_year: dict | None = None) -> str:
    """The change-alert email body. Pure — unit-tested. `views` = the FRESH canonical views
    by key (the stored snapshot holds only hashes, so removed rows are listed by key).
    `hit_map` = {row key: [screen hits]} for added/changed rows; `new_hits` = the
    'key|analyte' pairs NOT already above their value in the stored snapshot. Rows with a
    new hit are listed first, so the 25-row cap can never hide the row that raised the
    subject."""
    describe = DESCRIBE[item]
    parts = [f"A watched MPART PFAS open-data layer changed.\n\nSource:  {LABELS[item]}"]

    def rank(k):
        return (0 if any(f"{k}|{h['analyte']}" in new_hits for h in hit_map.get(k, [])) else 1, k)

    for title, keys in (("NEW rows", diff["added"]),
                        ("CHANGED rows (values differ from what was last recorded)", diff["changed"])):
        if not keys:
            continue
        ordered = sorted(keys, key=rank)
        shown = ordered[:_EMAIL_LIST_CAP]
        lines = []
        for k in shown:
            lines.append(describe(views[k]))
            for h in hit_map.get(k, []):
                fresh = f"{k}|{h['analyte']}" in new_hits
                lines.append(f"    >>> {h['analyte']} {h['value']}"
                             f"{' [' + h['flag'] + ']' if h['flag'] else ''} ng/L "
                             f"{'is ABOVE' if fresh else 'was already above (previously recorded)'} the published Rule 57 "
                             f"non-drinking-water value of {h['threshold']:g} ng/L")
        more = (f"\n(+ {len(keys) - len(shown)} more — every row key is in the Snapshot JSON cell of the MPART "
                "Data Watch tab.)") if len(keys) > len(shown) else ""
        parts.append(f"{title} ({len(keys)}):\n\n" + "\n".join(lines) + more)
    if diff["removed"]:
        shown = diff["removed"][:_EMAIL_LIST_CAP]
        parts.append(f"REMOVED rows ({len(diff['removed'])} no longer in the layer):\n\n"
                     + "\n".join(f"- {k}" for k in shown))
    if item == ITEM_SW:
        parts.append(screen_note(thresholds or DEFAULT_THRESHOLDS_NG_L, verified_year or DEFAULT_VERIFIED_YEAR))
    elif item == ITEM_FISH:
        parts.append("Fish PFOS is reported in ppb, edible portion only. Codes are shown with EGLE's own "
                     "definition (K = not detected, MDL shown; J = estimated; I = analytical interference, no "
                     "concentration determined; QNS = not enough sample). No threshold is applied to fish "
                     "results here.")
    if item in (ITEM_SW, ITEM_FISH):
        parts.append(_STATIC_NOTE)
    parts.append("This is an automated watch on MPART's public ArcGIS open-data layers (source: EGLE / MPART). "
                 "Values are printed as published; review at the source before relying on them.")
    return "\n\n".join(parts) + "\n"


def subject_for(item: str, diff: dict, has_new_hit: bool) -> str:
    kind = {ITEM_SW: "surface-water PFAS", ITEM_FISH: "fish PFOS", ITEM_SITES: "PFAS sites/AOIs"}[item]
    if has_new_hit:
        return f"[MPART data] Rule 57 screening: a reported value is above a non-drink value — new/changed {kind} result(s)"
    bits = [f"{n} {w}" for n, w in ((len(diff["added"]), "new"), (len(diff["changed"]), "changed"),
                                    (len(diff["removed"]), "removed")) if n]
    return f"[MPART data] {', '.join(bits)}: {kind}"


def _send(recipients: list[str], subject: str, body_fn, cfg: dict) -> str:
    """Best-effort alert -> 'sent' | 'display-only' | 'failed'. `body_fn` is called INSIDE
    the guard so a formatting bug cannot escape. EMPTY recipients = display-only:
    send_email would fall back to the whole coalition list, so it is never called. A
    'failed' result is not silent — run() turns it into a red run."""
    if not recipients:
        print(f"[mpart] display-only (no recipients): {subject}")
        return "display-only"
    try:
        body = body_fn()
        return "sent" if ea.send_email(subject, body, cfg, recipients=recipients) else "failed"
    except Exception as e:  # noqa: BLE001 — rows are already recorded
        print(f"[mpart] alert could not be prepared/sent (rows are recorded): {type(e).__name__}")
        return "failed"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _bbox(cfg: dict):
    b = (cfg.get("mpart") or {}).get("bbox")
    return tuple(float(x) for x in b) if b else mc.DEFAULT_BBOX


def _item_specs(cfg: dict) -> list[dict]:
    """[{item, fetch(), view, fields}] from config. Each fetch() returns raw attribute dicts."""
    m = cfg.get("mpart") or {}
    bbox = _bbox(cfg)
    stations = (m.get("fish") or {}).get("stations") or mc.DEFAULT_FISH_STATIONS
    like = (m.get("sites") or {}).get("name_like") or "Arbor Hills"
    urls = {k: (m.get(k) or {}).get("url") for k in ("surface_water", "fish", "sites")}
    return [
        {"item": ITEM_SW, "view": mc.surface_water_view, "fields": mc.SW_VIEW,
         "fetch": lambda: mc.fetch_surface_water(bbox, urls["surface_water"] or mc.DEFAULT_SURFACE_WATER_URL)},
        {"item": ITEM_FISH, "view": mc.fish_view, "fields": mc.FISH_VIEW,
         "fetch": lambda: mc.fetch_fish(stations, bbox, urls["fish"] or mc.DEFAULT_FISH_URL)},
        {"item": ITEM_SITES, "view": mc.sites_view, "fields": mc.SITES_VIEW,
         "fetch": lambda: mc.fetch_sites(like, bbox, urls["sites"] or mc.DEFAULT_SITES_URL)},
    ]


def run_probe(cfg: dict) -> int:
    """`--probe`: fetch every layer and print the counts. Reads only; ignores the flag."""
    rc = 0
    for spec in _item_specs(cfg):
        try:
            views = [spec["view"](a) for a in spec["fetch"]()]
            idx, dups = mc.index_rows(views, spec["fields"], spec["item"])
            snap = mc.snapshot_json(idx)
            print(f"[mpart] probe: {spec['item']}: {len(views)} row(s), {dups} duplicate key(s) disambiguated, "
                  f"snapshot {len(snap):,} chars, hash {mc.snapshot_hash(snap)}")
        except (mc.MpartFetchError, mc.MpartParseError) as e:
            print(f"[mpart] PROBE FAILED for {spec['item']}: {e}")
            rc = 1
    if rc == 0:
        print("[mpart] PROBE OK — every layer answered and parsed.")
    return rc


def run(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = load_config()
    if "--probe" in argv:
        return run_probe(cfg)
    ok, reason = _should_run(cfg)
    if not ok:
        print(f"[mpart] {reason}")
        return 0

    m = cfg.get("mpart") or {}
    recipients = [r for r in (m.get("recipients") or []) if str(r or "").strip()]     # empty => display-only
    thresholds, verified_year = load_thresholds(m)
    stale_after = max(1, int(m.get("stale_alert_after_skips", _DEFAULT_STALE_SKIPS)))
    max_shrink = float(m.get("max_shrink_fraction", _DEFAULT_MAX_SHRINK))
    accept_shrink_after = max(1, int(m.get("accept_shrink_after_skips", _DEFAULT_ACCEPT_SHRINK_AFTER)))

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_mpart_tabs(sheets, sheet_id)
    state = build_state(sw.read_mpart_rows(sheets, sheet_id))
    today = _today()
    exit_code = 0
    counts = {"baseline": 0, "changed": 0, "unchanged": 0, "skipped": 0}

    def append(item, change, h, note, snap=""):
        sw.append_mpart_watch_row(sheets, sheet_id, today, item, LABELS[item], change, h, note, _now(), snap)

    def note_skip(item, last, reason):
        """Record a skipped run for a baselined item and send the liveness alert at the
        threshold (and weekly thereafter while the outage lasts)."""
        last["skips"] += 1
        counts["skipped"] += 1
        print(f"[mpart] {item}: skipping this run (baseline preserved; consecutive: {last['skips']}): {reason}")
        append(item, "fetch-skipped", last["hash"], f"skipped (consecutive: {last['skips']}): {reason[:200]}")
        if last["skips"] >= stale_after and (last["skips"] - stale_after) % _LIVENESS_REPEAT_EVERY == 0:
            _send(recipients, f"[MPART data] {item} unreadable for {last['skips']} runs",
                  lambda: (f"The MPART watch could not use {LABELS[item]} on {last['skips']} consecutive runs, so a "
                           f"new sample there is currently going UNSEEN.\n\nLast reason: {reason[:300]}\n\nThe MPART "
                           "Data Watch tab records each skipped run and the recovery.\n"), cfg)

    for spec in _item_specs(cfg):
        item = spec["item"]
        last = state.get(item)
        try:
            try:
                view_list = [spec["view"](a) for a in spec["fetch"]()]
                index, dups = mc.index_rows(view_list, spec["fields"], item)   # blank key raises; dups disambiguated
            except mc.MpartParseError as e:
                print(f"[mpart] {item}: STRUCTURAL failure (failing loudly — not a transient blip): {e}")
                exit_code = 1
                continue
            except mc.MpartFetchError as e:
                if last is None:
                    print(f"[mpart] {item}: NO BASELINE and the fetch failed (failing loudly so activation surfaces it): {e}")
                    exit_code = 1
                    continue
                note_skip(item, last, str(e))
                continue

            views = mc.views_by_index_key(view_list, spec["fields"])
            hits = hit_keys(views, thresholds, verified_year) if item == ITEM_SW else set()
            snap = mc.snapshot_json(index, hits)                       # raises MpartParseError if oversized
            h = mc.snapshot_hash(snap)
            dup_note = f"; {dups} duplicate row key(s) disambiguated (#n)" if dups else ""

            parsed = mc.parse_snapshot(last["snapshot"]) if last else None
            if last is None or parsed is None:
                note = (f"initial snapshot: {len(views)} row(s), {len(hits)} already above a screening value "
                        f"(recorded, not alerted){dup_note}") if last is None else (
                    "stored snapshot unreadable or from another snapshot format — re-baselined silently "
                    f"({len(views)} row(s), {len(hits)} above a screening value){dup_note}")
                append(item, "baseline", h, note, snap)
                print(f"[mpart] {item}: baseline recorded ({len(views)} rows, {h}).")
                counts["baseline"] += 1
                continue

            old_index, old_hits = parsed
            if is_suspect_shrink(len(old_index), len(index), max_shrink) and last["skips"] < accept_shrink_after:
                note_skip(item, last, f"suspect response: {len(index)} row(s) now vs {len(old_index)} recorded "
                                      f"(< {max_shrink:.0%}) — not diffed; accepted if it persists")
                continue
            if last["skips"]:
                append(item, "fetch-ok", last["hash"], f"readable again after {last['skips']} skipped run(s)")
            if h == last["hash"]:
                print(f"[mpart] {item}: unchanged ({h}).")
                counts["unchanged"] += 1
                continue

            diff = diff_index(old_index, index)
            touched = diff["added"] + diff["changed"]
            hit_map = ({k: screen_surface_water(views[k], thresholds, verified_year) for k in touched}
                       if item == ITEM_SW else {})
            new_hits = {x for x in hits if x not in old_hits}
            note = (f"{len(diff['added'])} added, {len(diff['changed'])} changed, {len(diff['removed'])} removed"
                    + (f"; NEW screening hits: {', '.join(sorted(new_hits)[:10])}" if new_hits else "")
                    + (f"; added keys: {', '.join(diff['added'][:20])}" if diff["added"] else "") + dup_note)
            append(item, "changed", h, note, snap)                      # durable row FIRST
            counts["changed"] += 1
            print(f"[mpart] {item}: CHANGED ({last['hash']} -> {h}; {len(diff['added'])} added, "
                  f"{len(diff['changed'])} changed, {len(diff['removed'])} removed).")
            result = _send(recipients, subject_for(item, diff, bool(new_hits)),
                           lambda: format_change_body(item, diff, views, hit_map, new_hits, thresholds, verified_year), cfg)
            if result == "failed":
                exit_code = 1                                           # the row is written; the red run is the signal
        except Exception as e:  # noqa: BLE001 — one item must never abort the others
            print(f"[mpart] {item}: unexpected {type(e).__name__} — continuing with the next item: {str(e)[:120]}")
            exit_code = 1

    print(f"[mpart] done — {counts['changed']} changed, {counts['baseline']} baselined, "
          f"{counts['unchanged']} unchanged, {counts['skipped']} skipped.")
    return exit_code


def main() -> int:
    try:
        return run()
    except Exception as e:  # noqa: BLE001 — never a raw traceback into a public log
        print(f"[mpart] FAILED: {type(e).__name__}: {str(e)[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
