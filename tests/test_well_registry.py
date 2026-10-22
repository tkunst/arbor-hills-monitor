"""ADR 068 addendum 2026-10-22: the well registry (data/well_registry.json) is
the single source of truth for well-id rulings (aliases, ruled new wells,
GFL-labeled probes). Loader + validation, the generated alias map, and the
monitor's use of it (rename check, per-well summary, headline, well_watch).
Hermetic: synthetic maps and PDFs."""
import copy
import json
import os

import pytest

import well_aliases as wa
import woi_router
import woi_table_parser as w
from tests.test_woi_router import CFG, META, _build_pdf, _many, _parsed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _fresh():
    wa.clear_cache()
    wa.reset_review_log()
    yield
    wa.clear_cache()
    wa.reset_review_log()


def _committed():
    with open(wa.REGISTRY_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def _write(tmp_path, doc):
    path = tmp_path / "well_registry.json"
    path.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
    return str(path)


def _mini():
    return {
        "schema_version": 1,
        "counts": {"aliases": 1, "new_wells": 1, "probes": 1},
        "aliases": [{"alias": "AHWW0CS9", "canonical": "AHWWCS09", "source": "p309", "ruled": None}],
        "new_wells": [{"id": "AHWWCS09", "source": "Well ID column", "ruled": None}],
        "probes": [{"id": "AHMP0002", "source": "Probe ID column", "ruled": None}],
    }


# --- the committed registry --------------------------------------------------

def test_committed_registry_loads_with_its_counts():
    reg = wa.load_registry()
    doc = _committed()
    assert doc["counts"] == {"aliases": len(reg.aliases), "new_wells": len(reg.new_wells),
                             "probes": len(reg.probes)}
    assert len(reg.aliases) >= 60 and len(reg.probes) == 31
    assert reg.aliases["AHWW0CS9"] == "AHWWCS09"          # GFL's own pair (2026-10-22)
    assert {"AHWWCO01", "AHWWCS1A"} <= reg.new_wells
    assert {"AHETGP20", "AHMP012R", "AHTGP18D"} <= reg.probes


def test_every_entry_cites_a_source_and_probes_cite_gfls_heading():
    doc = _committed()
    for key in ("aliases", "new_wells", "probes"):
        assert all(e["source"].strip() for e in doc[key])
    assert all('"Probe ID"' in e["source"] for e in doc["probes"])


def test_generated_alias_map_equals_the_registry():
    # data/well_aliases.json is generated (`python3 well_aliases.py --export`);
    # the two can never disagree.
    with open(wa.ALIAS_JSON, encoding="utf-8") as fh:
        assert fh.read() == wa.alias_json_payload(wa.load_registry())
    assert wa.load_alias_map() == wa.load_registry().aliases


def test_check_and_export_cli(tmp_path, monkeypatch, capsys):
    assert wa._main(["--check"]) == 0
    monkeypatch.setattr(wa, "ALIAS_JSON", str(tmp_path / "well_aliases.json"))
    assert wa._main(["--check"]) == 1
    assert wa._main(["--export"]) == 0
    assert wa._main(["--check"]) == 0
    assert wa._main([]) == 2


def test_gitignore_and_data_guard_allowlist_the_registry():
    lines = open(os.path.join(ROOT, ".gitignore")).read().splitlines()
    assert "!data/well_registry.json" in lines and "!data/well_aliases.json" in lines
    guard = open(os.path.join(ROOT, ".github/workflows/data-guard.yml")).read()
    assert "data/well_registry.json|data/well_aliases.json)" in guard


# --- validation fails loudly -------------------------------------------------

def test_mini_registry_is_valid(tmp_path):
    reg = wa.load_registry(_write(tmp_path, _mini()))
    assert reg.point_type("AHMP0002") == "probe" and reg.point_type("AHMP0002*") == "probe"
    assert reg.point_type("AHWWCS09") == "well"
    assert {"AHWWCS09", "AHMP0002"} <= reg.ruled_ids


def _bad(fn):
    doc = _mini()
    fn(doc)
    return doc


@pytest.mark.parametrize("doc, match", [
    ("{not json", "unreadable"),
    (_bad(lambda d: d.update(schema_version=2)), "schema_version"),
    (_bad(lambda d: d["counts"].update(probes=2)), "counts"),
    (_bad(lambda d: d.pop("probes")), "no 'probes'"),
    (_bad(lambda d: d["probes"][0].update(source="")), "lacks"),
    (_bad(lambda d: (d["probes"].append({"id": "AHWWCS09", "source": "x"}),
                     d["counts"].update(probes=2))), "twice"),
    (_bad(lambda d: (d["new_wells"].append({"id": "AHWW0CS9", "source": "x"}),
                     d["counts"].update(new_wells=2))), "twice"),
    (_bad(lambda d: d["new_wells"].__setitem__(0, {"id": "AHWW0CS9X", "source": "x"})
          or d["aliases"].__setitem__(0, {"alias": "AHWW0CS9X", "canonical": "AHWWCS09",
                                           "source": "x"})), "twice"),
    (_bad(lambda d: d["aliases"].__setitem__(0, {"alias": "AHMP0002", "canonical": "AHWWCS09",
                                                  "source": "x"})), "twice"),
    (_bad(lambda d: d["aliases"].__setitem__(0, {"alias": "AHWW0CS9", "canonical": "AHMP0002",
                                                  "source": "x"})), "probes used"),
    (_bad(lambda d: d["probes"].__setitem__(0, {"id": "MP-2", "source": "x"})), "non-AH"),
    (_bad(lambda d: d["aliases"].__setitem__(0, {"alias": "AHWTS01R", "canonical": "AHWWTS01",
                                                  "source": "x"})), "excluded"),
])
def test_malformed_registry_raises(tmp_path, doc, match):
    with pytest.raises(wa.WellAliasError, match=match):
        wa.load_registry(_write(tmp_path, doc))


def test_ruled_new_well_as_alias_key_raises(tmp_path):
    doc = _mini()
    doc["aliases"] = [{"alias": "AHWWCS09", "canonical": "AHWWCS9X", "source": "x"}]
    with pytest.raises(wa.WellAliasError, match="twice"):
        wa.load_registry(_write(tmp_path, doc))


def test_missing_registry_raises(tmp_path):
    with pytest.raises(wa.WellAliasError, match="not found"):
        wa.load_registry(str(tmp_path / "nope.json"))


@pytest.mark.parametrize("modname", ["watcher", "backfill"])
def test_run_fails_loudly_before_any_doc_when_registry_is_bad(monkeypatch, modname):
    import importlib
    mod = importlib.import_module(modname)
    monkeypatch.setenv("GSHEET_ID", "sid")
    monkeypatch.setattr(mod, "load_config", lambda: {
        "anthropic_model": "m", "backfill_batch_size": 1,
        "large_doc_page_threshold": 30, "large_doc_max_keyword_pages": 10})

    def boom(*a, **k):
        raise wa.WellAliasError("well registry not found: x")
    monkeypatch.setattr(mod.well_aliases, "load_registry", boom)
    monkeypatch.setattr(mod.dc, "sheets_service",
                        lambda: pytest.fail("touched the Sheet before loading the registry"))
    with pytest.raises(wa.WellAliasError):
        mod.run()


# --- the monitor uses it ------------------------------------------------------

def test_rename_check_skips_ruled_ids():
    ids = ["AHMP012R", "AHMP0012"]                 # same stem: both flagged by the old check
    assert wa.review_candidates(ids, {}) == ["AHMP0012", "AHMP012R"]
    assert wa.review_candidates(ids, {}, ruled={"AHMP012R"}) == ["AHMP0012"]


def test_log_review_candidates_reads_the_committed_registry(capsys):
    new = wa.log_review_candidates(["AHMP012R", "AHMP0012"], {})
    assert new == ["AHMP0012"]                     # AHMP012R is a ruled probe
    assert "data/well_registry.json" in capsys.readouterr().out


def test_per_well_summary_marks_probes():
    rs = [w.WOIReading("AHW1", "AHW1", "1/2/2025 10:00", False, 1, 50, 40, 0, 10, 0.5, 120,
                       5, -1, -2),
          w.WOIReading("AHMP0002", "AHMP0002", "1/2/2025 10:00", False, 1, 0, 0, 21, 79)]
    rows = w.per_well_summary(rs, probe_ids={"AHMP0002"})
    assert {d["well"]: d["point_type"] for d in rows} == {"AHW1": "well", "AHMP0002": "probe"}
    assert all(d["point_type"] == "well" for d in w.per_well_summary(rs))


def test_headline_never_names_a_probe():
    routed = {"summary": [
        {"well": "AHMP15R2", "max_temp_f": 160.0, "max_temp_date": "1/2/2025 10:00",
         "point_type": "probe"},
        {"well": "AHW1", "max_temp_f": 120.0, "max_temp_date": "1/2/2025 10:00",
         "point_type": "well"}]}
    assert woi_router.build_key_data_point(routed).startswith("AHW1: max measured 120°F")


def test_route_measurements_counts_probes_apart_and_keeps_them_out_of_well_watch(tmp_path):
    reg = wa.validate_registry(_mini())
    rows = _many(60) + [("AHMP0002", 150.0)]       # a probe that (unusually) prints a temp
    pdf = _build_pdf(tmp_path / "woi.pdf", rows)
    parsed = _parsed()
    routed = woi_router.route_measurements(parsed, pdf, META, CFG, registry=reg)
    assert routed["n_probes"] == 1
    assert {d["well"] for d in routed["summary"] if d["point_type"] == "probe"} == {"AHMP0002"}
    assert "AHMP0002" in {r.well_id for r in routed["readings"]}
    assert "AHMP0002" not in {r.well_id for r in routed["well_readings"]}
    assert woi_router.build_key_data_point(routed).startswith("AHW")


def test_route_measurements_without_a_registry_is_unchanged(tmp_path):
    pdf = _build_pdf(tmp_path / "woi.pdf", _many(60))
    a = woi_router.route_measurements(_parsed(), pdf, META, CFG)
    b = woi_router.route_measurements(_parsed(), pdf, META, CFG, registry=wa.EMPTY_REGISTRY)
    assert [copy.deepcopy(d) for d in a["summary"]] == b["summary"]
    assert a["n_probes"] == b["n_probes"] == 0
