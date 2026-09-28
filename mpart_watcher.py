"""
mpart_watcher.py — Stream V: daily watch on MPART's PFAS open-data layers for the
Johnson Drain / Johnson Creek / upper-Rouge area around Arbor Hills. See
docs/decisions/060-mpart-data-watch.md and mpart_client.py.

WHY: the monitor watches MPART's Arbor Hills WEB PAGE (ADR 012) and the public-water-
supply layer (Stream R) but none of the layers that hold the surface-water and fish
results. The Johnson Drain fish results (8/5/2021) and the surface-water results were
found by hand in 9/2026; nothing would have caught a new sample.

THREE watched items, each one query -> a {row key: row hash} snapshot:
  - mpart:sw     surface-water PFAS samples inside the bounding box (key LabSampleId)
  - mpart:fish   fish PFOS results at the watched stations + the box (key SampleID)
  - mpart:sites  MPART's PFAS sites / areas of interest: the Arbor Hills row + the box
NOT here: the public-water-supply layer — the live Stream R already watches it.

WHAT IT DOES per item (the RIDE / PFAS-PWS watch idiom):
  - FIRST sighting -> a silent `baseline` row (no alert);
  - snapshot unchanged -> no-op;
  - a NEW row (a new sample) / a changed row / a removed row -> a `changed` row THEN
    an alert (durable row first, alert best-effort second). A new or changed
    surface-water row is compared with the Rule 57 non-drinking-water human non-cancer
    values for PFOS / PFOA / PFHxS / PFNA (config `thresholds_ng_l`); an exceedance
    raises the subject. The comparison is a SCREENING one against the published value
    (the alert says so, and to cite the value in force at the sample date) — never a
    regulatory determination, and never applied to a non-detect ('K': the value is the
    method detection limit). Fish PFOS code 'I' means NO VALUE PUBLISHED — never a
    detection; no fish threshold is applied (none was specified).

FAILURE MODES: a transient fetch failure (MpartFetchError) is skip-and-warn for a
baselined item — but every skipped run is RECORDED (`fetch-skipped`) and the run that
makes it `stale_alert_after_skips` (default 3) consecutive skips sends ONE liveness
alert; the first good run records `fetch-ok`. A never-baselined item that can't be
fetched is LOUD (exit 1), and a structural break (MpartParseError: schema drift,
truncated result, duplicate row key, oversized snapshot) is ALWAYS loud. The Sheet
read raises instead of swallowing errors (a swallowed read would look like "never
baselined" and absorb genuinely new samples into a fresh baseline).

Recipients are scoped VERBATIM (Trisha only); an EMPTY list is display-only — rows, no
email — and never falls back to the coalition alert list.

GATED on mpart.enabled (a brand-new external source; ships false). `--probe` fetches
every layer and prints the counts regardless of the flag, writing nothing.
"""
from __future__ import annotations

import json
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
_EMAIL_LIST_CAP = 25

# Rule 57 non-drinking-water human non-cancer values, ng/L, from EGLE's Rule 57 values
# spreadsheet as saved 2026-09-26 (Lotext .../egle-rule-57-water-quality-values-2026-09-26).
# Config `mpart.thresholds_ng_l` is the live source; this is the documented default.
DEFAULT_THRESHOLDS_NG_L = {"PFOS": 12, "PFOA": 170, "PFHxS": 210, "PFNA": 30}

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


# ---------------------------------------------------------------------------
# Screening (pure)
# ---------------------------------------------------------------------------


def is_nondetect(flag: str) -> bool:
    """A surface-water flag cell means non-detect (the value is the method detection
    limit) when it carries the 'K' token. 'J' (estimated), 'Q' etc. are detections."""
    return "K" in mc.flag_tokens(flag)


def screen_surface_water(view: dict, thresholds: dict) -> list[dict]:
    """Analytes in one canonical surface-water row whose reported value is ABOVE its
    threshold and is not a non-detect. Pure. [{analyte, value, flag, threshold,
    estimated}]; `estimated` is True when the flag carries 'J'."""
    out = []
    for analyte, limit in thresholds.items():
        raw = view.get(analyte, "")
        if raw == "":
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


# ---------------------------------------------------------------------------
# Snapshot state + diff (pure)
# ---------------------------------------------------------------------------


def build_state(rows: list[list]) -> dict:
    """Fold the append-only tab into {item: {hash, snapshot, skips}} — the LAST
    baseline/changed row per item is its snapshot; `skips` counts consecutive
    `fetch-skipped` rows since the last baseline / changed / fetch-ok row."""
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


def load_index(snapshot: str) -> dict[str, str]:
    try:
        rows = json.loads(snapshot).get("rows", {})
        return {str(k): str(v) for k, v in rows.items()} if isinstance(rows, dict) else {}
    except (ValueError, AttributeError):
        return {}


# ---------------------------------------------------------------------------
# Alert copy (pure)
# ---------------------------------------------------------------------------


def _value(view: dict, analyte: str) -> str:
    v = view.get(analyte, "")
    if v == "":
        return f"{analyte} —"
    flag = view.get(f"{analyte}_flag", "")
    mdl = view.get(f"{analyte}_mdl", "")
    return f"{analyte} {v}" + (f" [{flag}]" if flag else "") + (f" (MDL {mdl})" if mdl else "")


def describe_surface_water(v: dict) -> str:
    where = ", ".join(x for x in (v["waterbody"], v["description"]) if x) or "location not stated"
    return (f"- {v['date'] or 'date unknown'} | site {v['site'] or '?'} | {where} | sample {v['key']}\n"
            f"    {'; '.join(_value(v, a) for a in mc.ANALYTES)} ({v['unit'] or 'unit not stated'})")


def describe_fish(v: dict) -> str:
    code = v["pfos_code"]
    if v["pfos_ppb"] != "":
        pfos = f"PFOS {v['pfos_ppb']} ppb" + (f" (code {code})" if code else "")
    elif code == "I":
        pfos = "no PFOS value published (code I)"
    else:
        pfos = "PFOS —" + (f" (code {code})" if code else "")
    return (f"- {v['date'] or 'date unknown'} | station {v['station']} | {v['waterbody']}, {v['location']} | "
            f"{v['species'] or 'species not stated'} | sample {v['key']}\n    {pfos}")


def describe_site(v: dict) -> str:
    return (f"- {v['name']} ({v['kind']}, {v['type'] or 'type not stated'}) | {v['address']}, {v['city']}, "
            f"{v['county']} | residential wells sampled: {v['residential_wells'] or 'not stated'}"
            f"{' | ' + v['webpage'] if v['webpage'] else ''}")


DESCRIBE = {ITEM_SW: describe_surface_water, ITEM_FISH: describe_fish, ITEM_SITES: describe_site}

_SCREEN_NOTE = (
    "Rule 57 comparison: the non-drinking-water human non-cancer values (HNV) in EGLE's Rule 57 "
    "spreadsheet (PFOS 12, PFOA 170, PFHxS 210, PFNA 30 ng/L) — Johnson Drain / Johnson Creek is a "
    "non-drink water body. This is a SCREENING comparison of the reported value with the published "
    "value, not a regulatory determination; cite the value in force at the sample date (the PFOA, "
    "PFHxS and PFNA values were revised in 2022-2023). A 'K' flag is a non-detect (the value is the "
    "method detection limit) and is never compared; 'J' is an estimate."
)


def format_change_body(item: str, diff: dict, views: dict[str, dict],
                       screen: dict[str, list[dict]]) -> str:
    """The change-alert email body. Pure — unit-tested. `views` = the FRESH canonical
    views by key (the stored snapshot holds only hashes, so removed rows are listed by
    key). `screen` = {key: [exceedance dicts]} for surface-water rows."""
    describe = DESCRIBE[item]
    parts = [f"A watched MPART PFAS open-data layer changed.\n\nSource:  {LABELS[item]}"]
    for title, keys in (("NEW rows", diff["added"]), ("CHANGED rows (values differ from what was last recorded)",
                                                      diff["changed"])):
        if not keys:
            continue
        shown = keys[:_EMAIL_LIST_CAP]
        lines = []
        for k in shown:
            lines.append(describe(views[k]))
            for x in screen.get(k, []):
                lines.append(f"    >>> {x['analyte']} {x['value']} ng/L{' (J, estimated)' if x['estimated'] else ''} "
                             f"is ABOVE the Rule 57 non-drinking-water value of {x['threshold']} ng/L")
        more = f"\n(+ {len(keys) - len(shown)} more — see the row keys in the MPART Data Watch tab's note.)" if len(keys) > len(shown) else ""
        parts.append(f"{title} ({len(keys)}):\n\n" + "\n".join(lines) + more)
    if diff["removed"]:
        shown = diff["removed"][:_EMAIL_LIST_CAP]
        parts.append(f"REMOVED rows ({len(diff['removed'])} no longer in the layer):\n\n"
                     + "\n".join(f"- {k}" for k in shown))
    if item == ITEM_SW:
        parts.append(_SCREEN_NOTE)
    elif item == ITEM_FISH:
        parts.append("Fish PFOS is reported in ppb (fillet). Code 'I' means no value was published and is "
                     "not a detection. No threshold is applied to fish results here.")
    parts.append("This is an automated watch on MPART's public ArcGIS open-data layers (source: EGLE / MPART). "
                 "Values are printed as published; review at the source before relying on them.")
    return "\n\n".join(parts) + "\n"


def subject_for(item: str, diff: dict, screen: dict[str, list[dict]]) -> str:
    n = len(diff["added"]) + len(diff["changed"]) + len(diff["removed"])
    kind = {ITEM_SW: "surface-water PFAS", ITEM_FISH: "fish PFOS", ITEM_SITES: "PFAS sites/AOIs"}[item]
    if any(screen.get(k) for k in diff["added"] + diff["changed"]):
        return f"[MPART data] EXCEEDANCE of a Rule 57 value: new/changed {kind} result(s)"
    what = "new sample(s)" if diff["added"] and item != ITEM_SITES else "row change(s)"
    return f"[MPART data] {n} {what}: {kind}"


def _send(recipients: list[str], subject: str, body: str, cfg: dict) -> bool:
    """Best-effort alert; True only if handed to SMTP. EMPTY recipients = display-only:
    send_email would fall back to the whole coalition list, so it is never called."""
    if not recipients:
        print(f"[mpart] display-only (no recipients): {subject}")
        return False
    try:
        return bool(ea.send_email(subject, body, cfg, recipients=recipients))
    except Exception as e:  # noqa: BLE001 — rows are already recorded
        print(f"[mpart] alert email FAILED (rows are recorded): {subject}: {type(e).__name__}")
        return False


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
            idx = mc.index_rows(views, spec["fields"], spec["item"])
            snap = mc.snapshot_json(idx)
            print(f"[mpart] probe: {spec['item']}: {len(views)} row(s), snapshot {len(snap):,} chars, "
                  f"hash {mc.snapshot_hash(snap)}")
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
    thresholds = {k: float(v) for k, v in (m.get("thresholds_ng_l") or DEFAULT_THRESHOLDS_NG_L).items()
                  if k in mc.ANALYTES}
    stale_after = max(1, int(m.get("stale_alert_after_skips", _DEFAULT_STALE_SKIPS)))

    sheet_id = os.environ["GSHEET_ID"]
    sheets = dc.sheets_service()
    sw.ensure_mpart_tabs(sheets, sheet_id)
    state = build_state(sw.read_mpart_rows(sheets, sheet_id))
    today = _today()
    exit_code = 0
    counts = {"baseline": 0, "changed": 0, "unchanged": 0, "skipped": 0}

    def append(item, change, h, note, snap=""):
        sw.append_mpart_watch_row(sheets, sheet_id, today, item, LABELS[item], change, h, note, _now(), snap)

    for spec in _item_specs(cfg):
        item, label = spec["item"], LABELS[spec["item"]]
        last = state.get(item)
        try:
            view_list = [spec["view"](a) for a in spec["fetch"]()]
            index = mc.index_rows(view_list, spec["fields"], item)     # raises on a blank/duplicate key BEFORE any dict merge
            views = {v["key"]: v for v in view_list}
            snap = mc.snapshot_json(index)
        except mc.MpartParseError as e:
            print(f"[mpart] {item}: STRUCTURAL failure (failing loudly — not a transient blip): {e}")
            exit_code = 1
            continue
        except mc.MpartFetchError as e:
            if last is None:
                print(f"[mpart] {item}: NO BASELINE and the fetch failed (failing loudly so activation surfaces it): {e}")
                exit_code = 1
                continue
            last["skips"] += 1
            counts["skipped"] += 1
            print(f"[mpart] {item}: fetch failed, skipping this run (baseline preserved; consecutive: {last['skips']}): {e}")
            append(item, "fetch-skipped", last["hash"], f"skipped (consecutive: {last['skips']}): {str(e)[:200]}")
            if last["skips"] == stale_after:
                _send(recipients, f"[MPART data] {item} unreadable for {stale_after} runs",
                      f"The MPART watch could not read {label} on {stale_after} consecutive runs, so a new "
                      f"sample there is currently going UNSEEN.\n\nLast error: {str(e)[:300]}\n\nThis alert fires "
                      "once per outage; the MPART Data Watch tab records each skipped run and the recovery.\n", cfg)
            continue

        h = mc.snapshot_hash(snap)
        if last is None:
            append(item, "baseline", h, f"initial snapshot: {len(views)} row(s) (no alert)", snap)
            print(f"[mpart] {item}: baseline recorded ({len(views)} rows, {h}).")
            counts["baseline"] += 1
            continue
        if last["skips"]:
            append(item, "fetch-ok", last["hash"], f"readable again after {last['skips']} skipped run(s)")
        if h == last["hash"]:
            print(f"[mpart] {item}: unchanged ({h}).")
            counts["unchanged"] += 1
            continue

        old = load_index(last["snapshot"])
        diff = diff_index(old, index)
        screen = ({k: screen_surface_water(views[k], thresholds) for k in diff["added"] + diff["changed"]}
                  if item == ITEM_SW else {})
        note = (f"{len(diff['added'])} added, {len(diff['changed'])} changed, {len(diff['removed'])} removed"
                + (f"; EXCEEDANCE screen: {sum(1 for v in screen.values() if v)} row(s)" if any(screen.values()) else "")
                + (f"; added keys: {', '.join(diff['added'][:20])}" if diff["added"] else ""))
        append(item, "changed", h, note, snap)                # durable row FIRST
        counts["changed"] += 1
        print(f"[mpart] {item}: CHANGED ({last['hash']} -> {h}; {note}).")
        _send(recipients, subject_for(item, diff, screen), format_change_body(item, diff, views, screen), cfg)

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
