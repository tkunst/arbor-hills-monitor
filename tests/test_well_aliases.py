"""ADR 068 — well-ID alias map: loader, exact-match rule, the WOI (Part A) and
Measurements (Part B) wiring, _needs_canonical_review logging, and the dry-run
backfill planner. Hermetic: synthetic PDFs/maps, no Sheets/API."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

import egle_doc_parser as p
import sheet_writer as sw
import well_aliases as wa
import woi_router
import woi_table_parser as w
from risk_register import RISK_REGISTER, SIGNAL_KEYWORDS
from tests.test_woi_router import CFG, META, _build_pdf, _many

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import backfill_well_aliases as bwa  # noqa: E402

MAP = {"AHWW0177": "AHWW177R", "AHWW0290": "AHWW290R"}


@pytest.fixture(autouse=True)
def _fresh():
    wa.clear_cache()
    wa.reset_review_log()
    yield
    wa.clear_cache()
    wa.reset_review_log()


def _write(tmp_path, doc):
    path = tmp_path / "well_aliases.json"
    path.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
    return str(path)


# --- the committed map -------------------------------------------------------

def test_committed_map_loads_and_is_clean():
    amap = wa.load_alias_map()
    with open(wa.ALIAS_JSON) as fh:
        doc = json.load(fh)
    assert doc["count"] == len(amap) == len(doc["aliases"]) >= 38
    assert amap["AHWW0177"] == "AHWW177R"            # the handoff's example pair
    assert not [k for k, v in amap.items() if k == v]   # no self-alias
    assert not [k for k, v in amap.items() if v in amap]  # no chains


def test_committed_map_never_pairs_redrill_generations_or_excluded_ids():
    amap = wa.load_alias_map()
    assert "AHWTS01R" not in amap and "AHWWTS01" not in amap
    # an excluded id is never renamed (never a key); it may be a target (a
    # spelling mapped onto it, e.g. AHWTRO22 -> AHWTR022, Trisha 2026-10-19)
    assert not set(amap) & wa.EXCLUDE_FROM_NORMALIZATION
    for k, v in amap.items():
        # a curated pair may respell an R2 (AHWW423R2 -> AHW423R2) but never change
        # the redrill generation (R2 vs R3 are different physical wells).
        sk, sv = wa._rename_stem(k), wa._rename_stem(v)
        if sk and sv:
            assert sk[2] == sv[2], (k, v)


@pytest.mark.parametrize("modname", ["watcher", "backfill"])
def test_run_fails_loudly_before_any_doc_when_map_is_bad(monkeypatch, modname):
    import importlib
    mod = importlib.import_module(modname)
    monkeypatch.setenv("GSHEET_ID", "sid")
    monkeypatch.setattr(mod, "load_config", lambda: {
        "anthropic_model": "m", "backfill_batch_size": 1,
        "large_doc_page_threshold": 30, "large_doc_max_keyword_pages": 10})

    def boom(*a, **k):
        raise wa.WellAliasError("well alias map not found: x")
    monkeypatch.setattr(mod.well_aliases, "load_alias_map", boom)
    monkeypatch.setattr(mod.dc, "sheets_service",
                        lambda: pytest.fail("touched the Sheet before loading the map"))
    with pytest.raises(wa.WellAliasError):
        mod.run()


def test_gitignore_allowlists_exactly_the_alias_json():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lines = open(os.path.join(root, ".gitignore")).read().splitlines()
    assert "!data/well_aliases.json" in lines
    assert not [ln for ln in lines if ln.startswith("!data/") and "*" in ln]
    guard = open(os.path.join(root, ".github/workflows/data-guard.yml")).read()
    assert "data/well_aliases.json)" in guard


# --- loader fails loudly -----------------------------------------------------

def test_missing_file_raises(tmp_path):
    with pytest.raises(wa.WellAliasError, match="not found"):
        wa.load_alias_map(str(tmp_path / "nope.json"))


@pytest.mark.parametrize("doc, match", [
    ("{not json", "unreadable"),
    ({"count": 1}, "no 'aliases'"),
    ({"count": 0, "aliases": {}}, "non-empty"),
    ({"count": 2, "aliases": {"AHWW0177": "AHWW177R"}}, "count"),
    ({"count": 1, "aliases": {"AHWW177R": "AHWW177R"}}, "self-aliases"),
    ({"count": 2, "aliases": {"AHWW0177": "AHWW177R", "AHWW177R": "AHW177R2"}}, "chains"),
    ({"count": 1, "aliases": {"272R4": "AHW272R4"}}, "non-AH-form"),
    ({"count": 1, "aliases": {"AHWTS01R": "AHWWTS01"}}, "excluded"),
])
def test_malformed_map_raises(tmp_path, doc, match):
    with pytest.raises(wa.WellAliasError, match=match):
        wa.load_alias_map(_write(tmp_path, doc))


# --- exact-match rule --------------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("AHWW0177", "AHWW177R"),       # mapped rename
    ("AHWW0177**", "AHWW177R"),     # trailing footnote stars stripped before lookup
    ("AHW272R3", "AHW272R3"),       # redrill generation: never an alias
    ("AHW272R4*", "AHW272R4*"),     # unmapped: unchanged, stars and all
    ("MS-2", "MS-2"),               # excluded non-well id passes through
    ("290", "290"),                 # short form: no fuzzy/short-form rule
    ("ahww0177", "ahww0177"),       # exact match only (no case folding)
    ("AHWW 0177", "AHWW 0177"),     # no spacing rule
    (None, None),
])
def test_canonical_well_id(raw, want):
    assert wa.canonical_well_id(raw, MAP) == want


def test_canonicalize_measurements_keeps_as_filed_and_never_raises():
    ms = [{"well_id": "AHWW0290**", "metric": "temperature"},
          {"well_id": "AHW272R4"},
          {"well_id": None},
          "not-a-dict",
          {"well_id": "AHWW0177", "raw_well_id": "AHWW0177*"}]
    changed = wa.canonicalize_measurements(ms, MAP)
    assert changed == [("AHWW0290**", "AHWW290R"), ("AHWW0177", "AHWW177R")]
    assert ms[0] == {"well_id": "AHWW290R", "metric": "temperature", "raw_well_id": "AHWW0290"}
    assert "raw_well_id" not in ms[1]
    assert ms[4]["raw_well_id"] == "AHWW0177*"     # an upstream as-filed id is kept
    assert wa.canonicalize_measurements(None, MAP) == []


# --- Part A: the WOI table hook ----------------------------------------------

HOT = ["8.1", "40", "7.0", "44.9", "0.5", "177.0", "10", "-5", "-40"]


def test_woi_parser_applies_alias_and_carries_raw_id():
    lines = [(s, 1) for s in ["AHWW0177*", "3/14/2022 15:40", *HOT]]
    r = w._parse_lines(lines, alias_map=MAP)[0]
    assert r.well_id == "AHWW177R" and r.raw_well_id == "AHWW0177*"
    ms = w.to_measurements(r)
    assert {m["well_id"] for m in ms} == {"AHWW177R"}
    assert {m["raw_well_id"] for m in ms} == {"AHWW0177"}


def test_woi_parser_unmapped_id_carries_no_raw():
    r = w._parse_lines([(s, 1) for s in ["AHW272R4**", "3/14/2025 15:40", *HOT]], MAP)[0]
    assert r.well_id == "AHW272R4"
    assert all("raw_well_id" not in m for m in w.to_measurements(r))


def test_co_dedupe_logs_alias_collision(capsys):
    page = ["Attachment 2 - June 2022 CO Data", "ppm",
            "AHWW0290", "6/1/2022", "40", "AHWW290R", "6/2/2022", "55"]
    out = w._dedupe_co(w._parse_co_page(page, 9, MAP))
    # the reading printed under the CANONICAL spelling wins, not page order
    assert [(r.well_id, r.raw_well_id, r.ppm) for r in out] == [("AHWW290R", "AHWW290R", 55.0)]
    log = capsys.readouterr().out
    assert "kept AHWW290R=55.0" in log and "dropped AHWW0290=40.0" in log


def test_co_dedupe_plain_duplicate_keeps_first_silently(capsys):
    page = ["Attachment 2 - June 2022 CO Data", "ppm",
            "AHW272R4", "6/1/2022", "40", "AHW272R4", "6/2/2022", "55"]
    out = w._dedupe_co(w._parse_co_page(page, 9, MAP))
    assert [r.ppm for r in out] == [40.0]
    assert capsys.readouterr().out == ""


def test_route_threads_alias_map_into_every_parse(tmp_path):
    # one pre-rename spelling + its canonical spelling -> ONE well, not two.
    reads = _many(54, temp=90) + [("AHWW0177", 177), ("AHWW177R", 150)]
    pdf = _build_pdf(tmp_path / "woi.pdf", reads, co=[("AHWW0177", "6/1/2025", 30)])
    parsed = SimpleNamespace(measurements=[], severity="notable", key_data_point="",
                             summary="", full_text="")
    routed = woi_router.route_measurements(parsed, pdf, META, CFG, alias_map=MAP)
    wells = [d["well"] for d in routed["summary"]]
    assert "AHWW177R" in wells and "AHWW0177" not in wells
    assert next(d for d in routed["summary"] if d["well"] == "AHWW177R")["n_readings"] == 2
    temps = [m for m in parsed.measurements if m["metric"] == "temperature"]
    assert {m["well_id"] for m in temps if m["value"] in (177, 150)} == {"AHWW177R"}
    co = [m for m in parsed.measurements if m["metric"] == "carbon_monoxide"]
    assert co and co[0]["well_id"] == "AHWW177R" and co[0]["raw_well_id"] == "AHWW0177"


def test_route_without_map_is_unchanged(tmp_path):
    reads = _many(54, temp=90) + [("AHWW0177", 177)]
    pdf = _build_pdf(tmp_path / "woi.pdf", reads)
    parsed = SimpleNamespace(measurements=[], severity="notable", key_data_point="",
                             summary="", full_text="")
    routed = woi_router.route_measurements(parsed, pdf, META, CFG)
    assert "AHWW0177" in [d["well"] for d in routed["summary"]]


# --- Part B: the Measurements / egle_doc_parser hook -------------------------

def _fake_fields(ms):
    return {"summary": "s", "key_data_point": "k", "doc_type": "evidence",
            "risks": ["R8"], "severity": "notable", "measurements": ms}


def test_parse_document_canonicalizes_after_temperature_check(monkeypatch, text_pdf):
    order = []
    monkeypatch.setattr(p, "_classify_with_claude", lambda *a, **k: _fake_fields([
        {"metric": "temperature", "value": 140, "unit": "F", "basis": "measured",
         "well_id": "AHWW0290"},
        {"metric": "temperature", "value": 120, "unit": "F", "basis": "measured",
         "well_id": "AHW272R4"}]))
    real = p._enforce_temperature_consistency

    def spy(fields, *a, **k):
        order.append([m["well_id"] for m in fields["measurements"]])
        return real(fields, *a, **k)
    monkeypatch.setattr(p, "_enforce_temperature_consistency", spy)
    parsed = p.parse_document(text_pdf, {"doc_id": "d1"}, RISK_REGISTER,
                              signal_keywords=SIGNAL_KEYWORDS, well_alias_map=MAP)
    assert order == [["AHWW0290", "AHW272R4"]]          # check saw the as-filed ids
    assert [m["well_id"] for m in parsed.measurements] == ["AHWW290R", "AHW272R4"]
    assert parsed.measurements[0]["raw_well_id"] == "AHWW0290"
    assert "raw_well_id" not in parsed.measurements[1]


def test_parse_document_without_map_leaves_ids(monkeypatch, text_pdf):
    monkeypatch.setattr(p, "_classify_with_claude", lambda *a, **k: _fake_fields([
        {"metric": "temperature", "value": 140, "unit": "F", "basis": "measured",
         "well_id": "AHWW0290"}]))
    parsed = p.parse_document(text_pdf, {}, RISK_REGISTER, signal_keywords=SIGNAL_KEYWORDS)
    assert parsed.measurements[0]["well_id"] == "AHWW0290"


def test_measurement_rows_write_as_filed_column():
    parsed = SimpleNamespace(measurements=[
        {"metric": "oxygen", "value": 1, "unit": "%", "basis": "measured",
         "well_id": "AHWW290R", "raw_well_id": "AHWW0290"},
        {"metric": "oxygen", "value": 1, "unit": "%", "basis": "measured",
         "well_id": "AHW272R4"}])
    rows = sw.measurement_rows(parsed, {"date_filed": "2026-01-01"}, "L")
    col = sw.MEASUREMENTS_HEADERS.index("Well ID As Filed")
    assert col == len(sw.MEASUREMENTS_HEADERS) - 1      # appended at the END
    assert sw.MEASUREMENTS_HEADERS.index("Facility") == 10  # positional readers intact
    assert [r[col] for r in rows] == ["AHWW0290", ""]
    assert [r[1] for r in rows] == ["AHWW290R", "AHW272R4"]


# --- _needs_canonical_review -------------------------------------------------

def test_review_flags_unmapped_rename_pair_and_short_forms_only():
    ids = ["AHWW0999", "AHWW999R",          # unresolved era-switch pair -> flag
           "AHW272R3", "AHW272R4",          # redrill generations -> never paired
           "AHWTS01R", "AHWWTS01",          # excluded sensor station -> ignored
           "AHWW0177", "AHWW177R",          # mapped pair -> resolved, not flagged
           "272R4", "WW 237R3", "Well 260R",  # short/spaced forms -> flag
           "MS-2", "Flare 391", "1", "001A", None, ""]
    assert wa.review_candidates(ids, MAP) == sorted(
        ["AHWW0999", "AHWW999R", "272R4", "WW 237R3", "Well 260R"])


def test_review_logs_once_per_process(capsys):
    assert wa.log_review_candidates(["AHWW0999", "AHWW999R"], MAP, context="x")
    assert "_needs_canonical_review (x)" in capsys.readouterr().out
    assert wa.log_review_candidates(["AHWW0999", "AHWW999R"], MAP) == []
    assert capsys.readouterr().out == ""


# --- dry-run backfill planner ------------------------------------------------

HDR = list(sw.MEASUREMENTS_HEADERS)


def _row(wid, link="https://x/downloadpdf/123"):
    r = [""] * len(HDR)
    r[HDR.index("Well ID")] = wid
    r[HDR.index("Link")] = link
    return r


def test_backfill_plan_and_summary():
    rows = [HDR, _row("AHWW0290"), _row("AHW272R4"), _row("AHWW0290*"), _row("AHWW290R")]
    plan = bwa.build_plan(HDR, rows, MAP)
    assert [(u["row"], u["old"], u["new"]) for u in plan] == [
        (2, "AHWW0290", "AHWW290R"), (4, "AHWW0290*", "AHWW290R")]
    text = bwa.render_summary(HDR, rows, plan, MAP)
    assert "rows to relabel: 2" in text and "Well ID As Filed': yes" in text


class _FakeSvc:
    def __init__(self, rows):
        self.rows, self.writes = rows, []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, **k):
        return SimpleNamespace(execute=lambda **kk: {"values": self.rows})

    def batchUpdate(self, spreadsheetId, body):
        self.writes.append(body)
        return SimpleNamespace(execute=lambda **kk: None)


def test_backfill_apply_writes_well_id_and_as_filed(tmp_path):
    svc = _FakeSvc([HDR, _row("AHW272R4"), _row("AHWW0177*")])
    man = tmp_path / "m.json"
    plan = bwa.apply_backfill(svc, "sid", MAP, manifest_path=str(man), meta={})
    assert len(plan) == 1
    data = svc.writes[0]["data"]
    assert data == [{"range": "'Measurements'!B3", "values": [["AHWW177R"]]},
                    {"range": "'Measurements'!L3", "values": [["AHWW0177"]]}]
    assert json.loads(man.read_text())["updates"] == [
        {"row": 3, "old": "AHWW0177*", "new": "AHWW177R", "as_filed_written": True}]


def test_backfill_apply_keeps_existing_as_filed_and_records_it(tmp_path):
    r = _row("AHWW0177")
    r[HDR.index("Well ID As Filed")] = "AHWW0177"
    svc = _FakeSvc([HDR, r])
    bwa.apply_backfill(svc, "sid", MAP, manifest_path=str(tmp_path / "m.json"), meta={})
    assert [d["range"] for d in svc.writes[0]["data"]] == ["'Measurements'!B2"]
    assert json.loads((tmp_path / "m.json").read_text())["updates"][0]["as_filed_written"] is False


def test_backfill_apply_refuses_without_as_filed_header(tmp_path):
    old_hdr = HDR[:-1]
    svc = _FakeSvc([old_hdr, ["", "AHWW0177"] + [""] * 9])
    with pytest.raises(RuntimeError, match="Well ID As Filed"):
        bwa.apply_backfill(svc, "sid", MAP, manifest_path=str(tmp_path / "m.json"), meta={})
    assert svc.writes == []
