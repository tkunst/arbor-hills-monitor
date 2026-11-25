"""ADR 005 / ADR 068 addenda 2026-11-15 (coder:non-ah-points-hc1laron-rnginlet):
the WOI parser reads two non-AH ids GFL prints under "Well ID", admitted BY NAME
(Trisha 2026-11-15): HC1LARON (an extraction well) and RNGINLET (the RNG plant
inlet, registry point_type "rng_inlet", never counted as a well). Every other
non-AH id (STEST/ETEST/NTEST/WTEST, RGINLET2, lab rows) stays unread.
Hermetic: synthetic maps and PDFs."""
import json

import pytest

import well_aliases as wa
import woi_router
import woi_table_parser as w
from tests.test_woi_router import CFG, META, _build_pdf, _many, _parsed


@pytest.fixture(autouse=True)
def _fresh():
    wa.clear_cache()
    wa.reset_review_log()
    yield
    wa.clear_cache()
    wa.reset_review_log()


# --- the admitted list is one list ------------------------------------------

def test_parser_and_registry_admit_the_same_non_ah_ids():
    assert set(w.NON_AH_POINT_IDS) == wa.NON_AH_IDS == {"HC1LARON", "RNGINLET"}
    reg = wa.load_registry()
    committed = {i for i in reg.new_wells | reg.probes | set(reg.points)
                 if not i.startswith("AH")}
    assert committed == wa.NON_AH_IDS   # every admitted id carries a ruling + citation


def test_committed_registry_types_the_two_points():
    reg = wa.load_registry()
    assert "HC1LARON" in reg.new_wells and reg.point_type("HC1LARON") == "well"
    assert dict(reg.points) == {"RNGINLET": "rng_inlet"}
    assert reg.point_type("RNGINLET") == "rng_inlet" == reg.point_type("RNGINLET*")
    assert {"HC1LARON", "RNGINLET"} <= reg.ruled_ids
    assert "145 degrees F" in reg.sources["HC1LARON"]
    assert "HC1LARON" not in reg.aliases and "RNGINLET" not in reg.aliases


@pytest.mark.parametrize("wid", ["HC1LARON", "RNGINLET", "HC1LARON*", "RNGINLET**", "AHW272R4"])
def test_well_re_reads_the_admitted_ids(wid):
    assert w.WELL_RE.match(wid)


@pytest.mark.parametrize("wid", ["STEST3", "STEST2", "STESTFL1", "STEST180", "ETEST3", "NTEST3",
                                 "WTEST7", "STEST18W", "RGINLET2", "NVALVE06", "RNGINLET2",
                                 "XRNGINLET", "HC1LARONX", "HC1LARO", "LCS", "Blank",
                                 "R-2", "ARHICAL3", "hc1laron"])
def test_well_re_still_refuses_every_other_non_ah_id(wid):
    assert not w.WELL_RE.match(wid)


ALIAS = [{"alias": "AHWW0CS9", "canonical": "AHWWCS09", "source": "p309", "ruled": "2026-10-22"}]


# --- PDF rows ----------------------------------------------------------------

def test_admitted_rows_parse_and_test_points_stay_dropped(tmp_path):
    rows = (_many(10) + [("HC1LARON", 72.5), ("HC1LARON", 72.5, True), ("RNGINLET", 93.1),
                         ("STEST3", 80.0), ("ETEST3", 80.0), ("WTEST7", 80.0)])
    gp = w.parse_gas_extraction_report(_build_pdf(tmp_path / "r.pdf", rows))
    got = [(r.well_id, r.adj, r.temp) for r in gp.readings]
    assert ("HC1LARON", False, 72.5) in got and ("HC1LARON", True, 72.5) in got
    assert ("RNGINLET", False, 93.1) in got
    assert not {"STEST3", "ETEST3", "WTEST7"} & {r.well_id for r in gp.readings}
    assert gp.rejected_tables == []


def test_no_ah_row_changes_when_admitted_rows_are_interleaved(tmp_path):
    base = _many(12)
    mixed = base[:4] + [("HC1LARON", 70.0), ("RNGINLET", 40.0)] + base[4:8] + \
        [("STEST3", 80.0)] + base[8:]
    a = w.parse_gas_extraction_report(_build_pdf(tmp_path / "a.pdf", base)).readings
    b = w.parse_gas_extraction_report(_build_pdf(tmp_path / "b.pdf", mixed)).readings

    def key(r):
        return (r.well_id, r.raw_well_id, r.dt, r.adj, r.ch4, r.co2, r.o2, r.balance,
                r.diff_press, r.temp, r.flow, r.well_pres, r.header_pres, r.valid)
    assert [key(r) for r in b if r.well_id.startswith("AH")] == [key(r) for r in a]
    assert sorted(r.well_id for r in b if not r.well_id.startswith("AH")) == ["HC1LARON", "RNGINLET"]


def test_admitted_ids_take_the_star_strip_but_never_an_alias(tmp_path):
    gp = w.parse_gas_extraction_report(
        _build_pdf(tmp_path / "r.pdf", _many(5) + [("HC1LARON*", 70.0)]),
        alias_map=wa.load_alias_map())
    r = [r for r in gp.readings if r.raw_well_id.startswith("HC1")][0]
    assert r.well_id == "HC1LARON"


# --- per-well summary + router: RNGINLET is never a well ----------------------

def test_per_well_summary_types_points():
    rs = [w.WOIReading("AHW1", "AHW1", "1/2/2025 10:00", False, 1, 50, 40, 0, 10, 0.5, 120,
                       5, -1, -2),
          w.WOIReading("HC1LARON", "HC1LARON", "1/2/2025 10:00", False, 1, 50, 40, 0, 10, 0.5, 70,
                       5, -1, -2),
          w.WOIReading("RNGINLET", "RNGINLET", "1/2/2025 10:00", False, 1, 50, 40, 0, 10, 0.5, 90,
                       5, -1, -2),
          w.WOIReading("AHMP0002", "AHMP0002", "1/2/2025 10:00", False, 1, 0, 0, 21, 79)]
    rows = w.per_well_summary(rs, probe_ids={"AHMP0002"}, point_types={"RNGINLET": "rng_inlet"})
    assert {d["well"]: d["point_type"] for d in rows} == {
        "AHW1": "well", "HC1LARON": "well", "RNGINLET": "rng_inlet", "AHMP0002": "probe"}
    # no registry: unknown, never asserted (point_types alone does not assert types)
    assert {d["point_type"] for d in w.per_well_summary(rs, point_types={"RNGINLET": "rng_inlet"})} == {None}


def test_headline_never_names_the_rng_inlet():
    routed = {"summary": [
        {"well": "RNGINLET", "max_temp_f": 160.0, "max_temp_date": "1/2/2025 10:00",
         "point_type": "rng_inlet"},
        {"well": "HC1LARON", "max_temp_f": 120.0, "max_temp_date": "1/2/2025 10:00",
         "point_type": "well"}]}
    assert woi_router.build_key_data_point(routed).startswith("HC1LARON: max measured 120°F")


def _reg_with_points():
    return wa.validate_registry({
        "schema_version": 1,
        "counts": {"aliases": 1, "new_wells": 1, "probes": 1, "points": 1},
        "aliases": ALIAS,
        "new_wells": [{"id": "HC1LARON", "source": "alt standard list", "ruled": "2026-11-15"}],
        "probes": [{"id": "AHMP0002", "source": "Probe ID column", "ruled": "2026-10-22"}],
        "points": [{"id": "RNGINLET", "point_type": "rng_inlet", "source": "x", "ruled": "2026-11-15"}],
    })


def test_route_measurements_keeps_the_rng_inlet_out_of_wells_and_well_watch(tmp_path, capsys):
    rows = _many(60) + [("RNGINLET", 150.0), ("HC1LARON", 140.0), ("AHMP0002", 150.0)]
    pdf = _build_pdf(tmp_path / "woi.pdf", rows)
    routed = woi_router.route_measurements(_parsed(), pdf, META, CFG, registry=_reg_with_points())
    types = {d["well"]: d["point_type"] for d in routed["summary"]}
    assert types["RNGINLET"] == "rng_inlet" and types["HC1LARON"] == "well"
    assert types["AHMP0002"] == "probe"
    assert routed["n_probes"] == 1
    assert "RNGINLET" in {r.well_id for r in routed["readings"]}
    well_ids = {r.well_id for r in routed["well_readings"]}
    assert "RNGINLET" not in well_ids and "AHMP0002" not in well_ids and "HC1LARON" in well_ids
    # the hottest point is RNGINLET/probe (150F); the headline names the hottest WELL
    assert woi_router.build_key_data_point(routed).startswith("HC1LARON: max measured 140°F")
    out = capsys.readouterr().out
    assert "61 wells + 1 probes + 1 other points" in out
    assert "1 wells >= 131F" in out   # HC1LARON only; RNGINLET + probe excluded


def test_route_measurements_without_points_logs_as_before(tmp_path, capsys):
    reg = wa.validate_registry({
        "schema_version": 1, "counts": {"aliases": 1, "new_wells": 0, "probes": 0},
        "aliases": ALIAS, "new_wells": [], "probes": []})
    woi_router.route_measurements(_parsed(), _build_pdf(tmp_path / "w.pdf", _many(60)),
                                  META, CFG, registry=reg)
    out = capsys.readouterr().out
    assert "60 wells + 0 probes (" in out and "other points" not in out


# --- registry validation ------------------------------------------------------

def _doc(**over):
    d = {"schema_version": 1,
         "counts": {"aliases": 1, "new_wells": 0, "probes": 0, "points": 1},
         "aliases": ALIAS, "new_wells": [], "probes": [],
         "points": [{"id": "RNGINLET", "point_type": "rng_inlet", "source": "x", "ruled": "2026-11-15"}]}
    d.update(over)
    return d


def test_points_list_is_optional_and_counted_only_when_present():
    assert dict(wa.validate_registry(_doc()).points) == {"RNGINLET": "rng_inlet"}
    old = {"schema_version": 1, "counts": {"aliases": 1, "new_wells": 0, "probes": 0},
           "aliases": ALIAS, "new_wells": [], "probes": []}
    assert dict(wa.validate_registry(old).points) == {}
    with pytest.raises(wa.WellAliasError, match="counts"):
        wa.validate_registry(dict(old, counts={"aliases": 1, "new_wells": 0, "probes": 0, "points": 0}))
    with pytest.raises(wa.WellAliasError, match="counts"):
        wa.validate_registry(_doc(counts={"aliases": 1, "new_wells": 0, "probes": 0}))


@pytest.mark.parametrize("doc, match", [
    (_doc(points=[{"id": "RNGINLET", "point_type": "well", "source": "x", "ruled": "2026-11-15"}]),
     "unknown point_type"),
    (_doc(points=[{"id": "RNGINLET", "point_type": "probe", "source": "x", "ruled": "2026-11-15"}]),
     "unknown point_type"),
    (_doc(points=[{"id": "RNGINLET", "source": "x", "ruled": "2026-11-15"}]), "exactly"),
    (_doc(points=[{"id": "STEST3", "point_type": "rng_inlet", "source": "x", "ruled": "2026-11-15"}]),
     "non-AH"),
    (_doc(points="RNGINLET"), "no 'points' list"),
    (_doc(new_wells=[{"id": "STEST3", "source": "x", "ruled": "2026-11-15"}],
          counts={"aliases": 1, "new_wells": 1, "probes": 0, "points": 1}), "non-AH"),
    (_doc(new_wells=[{"id": "RNGINLET", "source": "x", "ruled": "2026-11-15"}],
          counts={"aliases": 1, "new_wells": 1, "probes": 0, "points": 1}), "twice"),
    (_doc(probes=[{"id": "RNGINLET", "source": "x", "ruled": "2026-11-15"}],
          counts={"aliases": 1, "new_wells": 0, "probes": 1, "points": 1}), "twice"),
    # aliases stay AH-form only, even for an admitted id
    (_doc(aliases=[{"alias": "HC1LARON", "canonical": "AHW1", "source": "x", "ruled": "2026-11-15"}],
          counts={"aliases": 1, "new_wells": 0, "probes": 0, "points": 1}), "non-AH-form"),
    (_doc(aliases=[{"alias": "AHW1", "canonical": "RNGINLET", "source": "x", "ruled": "2026-11-15"}],
          counts={"aliases": 1, "new_wells": 0, "probes": 0, "points": 1}), "non-AH-form"),
])
def test_malformed_points_raise(doc, match):
    with pytest.raises(wa.WellAliasError, match=match):
        wa.validate_registry(json.loads(json.dumps(doc)))


def test_review_candidates_never_flag_the_admitted_ids():
    ruled = wa.load_registry().ruled_ids
    assert wa.review_candidates(["HC1LARON", "RNGINLET", "AHW1"], {}, ruled=ruled) == []
