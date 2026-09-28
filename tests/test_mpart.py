"""mpart_client.py / mpart_watcher.py (Stream V, ADR 060) — the MPART PFAS open-data
layers watch.

Attribute fixtures are shaped like the REAL ArcGIS responses (live-verified
2026-09-28): the surface-water layer's `CAS…_PFOS / …Flag / …Mdl` column groups with
its messy flags (`'K'`, `' J'`, `'J, Q'`, `' '`) and REAL lab sample ids (short Vista
labels such as `UT-0100` next to Eurofins ids such as `240-169452-6`), the fish table's
`PFOScode` (EGLE: K = not detected/MDL shown, J = estimated, I = analytical interference,
QNS = not enough sample), and the sites layer. Values for the Napier Rd 2021-08-05 sample
(PFOS 16.5 ng/L, no flag — the one row in the area data that reports PFOS above the non-drink value) and the
Johnson Drain fish rows are the published ones. No JSON/CSV/PDF is committed (data-guard).
"""
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import mpart_client as mc
import mpart_watcher as mw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets

ROOT = Path(__file__).resolve().parent.parent


def ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


# ==============================================================================
# Fixtures
# ==============================================================================


def sw_row(sample, site, date, water, desc, pfos=None, pfos_flag=None, pfos_mdl=None, pfoa=None, pfoa_flag=None,
           pfhxs=None, pfna=None, pfna_flag=None, unit="ng/L", dup=0, sample_type=None):
    return {
        "LabSampleId": sample, "SiteCode": site, "Waterbody": water, "Description": desc,
        "CollectionDate": ms(date), "SampleType": sample_type, "Unit": unit, "Duplicate": dup,
        "CAS1763231_PFOS": pfos, "CAS1763231_PFOSFlag": pfos_flag, "CAS1763231_PFOSMdl": pfos_mdl,
        "CAS335671_PFOA": pfoa, "CAS335671_PFOAFlag": pfoa_flag, "CAS335671_PFOAMdl": 0.5,
        "CAS355464_PFHxS": pfhxs, "CAS355464_PFHxSFlag": None, "CAS355464_PFHxSMdl": 0.5,
        "CAS375951_PFNA": pfna, "CAS375951_PFNAFlag": pfna_flag, "CAS375951_PFNAMdl": 0.25,
    }


NAPIER = "UT-0100|19-UUTJD-0010|2021-08-05|0"          # the composite key of the real Napier Rd row


def sw_rows():
    return [
        sw_row("240-169452-6", "19-JD-0105", "2022-07-06", "Johnson Drain", "u/s 6 Mile Rd", 3.1, " ", 0.5, 3.6, "", 1.4, 0.48),
        sw_row("UT-0100", "19-UUTJD-0010", "2021-08-05", "Unnamed Trib to an Unnamed Trib", "Napier Rd", 16.5, "", 1.03, 17.2, "",
               26.3, 1.04, "J"),
        sw_row("JD-0100", "19-JD-0100", "2021-08-05", "Johnson Drain ", "6 Mile Rd", 1.03, "K", 1.03, None, None, None, None),
    ]


def fish_row(sample, station, species, ppb, code, date="2021-08-05"):
    return {"SampleID": sample, "FishID": sample.replace("-S", "-F0"), "StationID": float(station), "WaterBody": "Johnson Drain",
            "SamplingLocation": "Fish Hatchery Park" if station == 1484 else "6-mile",
            "CollectionDate": ms(date), "Species": species, "PFOSppb": ppb, "PFOScode": code}


def fish_rows():
    return [fish_row("2021233-S01", 1484, "Brown Trout", None, "I"),
            fish_row("2021268-S01", 1507, "White Sucker", 5.9, None),
            fish_row("2021268-S02", 1507, "White Sucker", 9.5, None)]


def site_row(name="Advanced Disposal Services Arbor Hills Landfill, Inc.", kind="Site", wells="Yes", **kw):
    r = {"Name": name, "SiteOrAoi": kind, "Address": "Napier Road and Six Mile Road", "City": "Northville",
         "County": "Washtenaw County", "Type": "Landfill", "ResidentialWellsSampled": wells,
         "WebpageSite": "https://www.michigan.gov/pfasresponse/investigations/sites-aoi/washtenaw-county/arbor-hills-landfill",
         "FacilityDate": "2020-03-31"}
    r.update(kw)
    return r


def site_rows():
    return [site_row(), site_row("Holloway Sand and Gravel", county="Oakland County ", wells="No")]


# ==============================================================================
# Client — query construction + fetch guards
# ==============================================================================


def test_bbox_where_takes_numbers_only():
    assert mc.bbox_where((-83.66, 42.34, -83.40, 42.46), "Longitude", "Latitude") == \
        "Longitude >= -83.66 AND Longitude <= -83.4 AND Latitude >= 42.34 AND Latitude <= 42.46"
    with pytest.raises(ValueError):
        mc.bbox_where(("-83.66; DROP", 42.34, -83.40, 42.46), "Longitude", "Latitude")


def test_quote_sql_doubles_quotes():
    assert mc.quote_sql("O'Brien") == "'O''Brien'"


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status = body, status

    def read(self):
        return self._b

    def getcode(self):
        return self.status


def payload(records, fields, **extra):
    d = {"fields": [{"name": f} for f in fields], "features": [{"attributes": r} for r in records]}
    d.update(extra)
    return json.dumps(d).encode()


def wire_opener(monkeypatch, body, status=200, seen=None):
    class _Op:
        def open(self, url, timeout=60):
            if seen is not None:
                seen.append(url)
            return _Resp(body, status)
    monkeypatch.setattr(mc, "_opener", lambda: _Op())


def test_fetch_happy_path_returns_the_attribute_dicts(monkeypatch):
    wire_opener(monkeypatch, payload(sw_rows(), mc.SW_OUT_FIELDS))
    rows = mc.fetch_surface_water()
    assert [r["LabSampleId"] for r in rows] == ["240-169452-6", "UT-0100", "JD-0100"]
    wire_opener(monkeypatch, payload(fish_rows(), mc.FISH_OUT_FIELDS))
    assert len(mc.fetch_fish()) == 3
    wire_opener(monkeypatch, payload(site_rows(), mc.SITES_OUT_FIELDS))
    assert len(mc.fetch_sites()) == 2


def test_every_fetch_asks_for_explicit_fields_and_no_geometry_or_staff_contacts(monkeypatch):
    seen = []
    wire_opener(monkeypatch, payload([], mc.SW_OUT_FIELDS), seen=seen)
    mc.fetch_surface_water()
    wire_opener(monkeypatch, payload([], mc.FISH_OUT_FIELDS), seen=seen)
    mc.fetch_fish()
    wire_opener(monkeypatch, payload([], mc.SITES_OUT_FIELDS), seen=seen)
    mc.fetch_sites()
    assert len(seen) == 3
    for url in seen:
        q = parse_qs(urlparse(url).query)
        assert q["returnGeometry"] == ["false"] and q["f"] == ["json"] and q["outFields"][0] != "*"
        fields = q["outFields"][0].split(",")
        assert not ({"OBJECTID", "GlobalID", "Shape", "SiteLeadEmail", "SiteLeadPhone", "SiteLead", "Latitude",
                     "Longitude", "Lat", "Long"} & set(fields))


def test_fish_where_coerces_station_ids_and_the_sites_like_is_sanitized(monkeypatch):
    seen = []
    wire_opener(monkeypatch, payload([], mc.FISH_OUT_FIELDS), seen=seen)
    mc.fetch_fish(("1484", 1507.0), (-83.66, 42.34, -83.4, 42.46))
    assert parse_qs(urlparse(seen[0]).query)["where"][0].startswith("StationID IN (1484,1507) OR (Long >= -83.66")
    with pytest.raises(ValueError):
        mc.fetch_fish(("1484); DROP TABLE x;--",))
    wire_opener(monkeypatch, payload([], mc.SITES_OUT_FIELDS), seen=seen)
    mc.fetch_sites("Arb'or %Hills_")
    where = parse_qs(urlparse(seen[1]).query)["where"][0]
    assert where.startswith("Name LIKE '%Arb''or Hills%' OR (") and "_" not in where.split(" OR ")[0]


@pytest.mark.parametrize("body,status,exc", [
    (b"gone", 503, mc.MpartFetchError),
    (b"<html>bot wall</html>", 200, mc.MpartFetchError),
    (b"[1,2]", 200, mc.MpartFetchError),
    (json.dumps({"error": {"code": 503}}).encode(), 200, mc.MpartFetchError),               # a server-side blip
    (json.dumps({"error": {"code": 400}}).encode(), 200, mc.MpartParseError),               # a REJECTED query: persists
    (json.dumps({"fields": []}).encode(), 200, mc.MpartParseError),                         # no features
    (payload([], mc.SW_OUT_FIELDS, exceededTransferLimit=True), 200, mc.MpartParseError),   # truncated
    (payload([], mc.SW_OUT_FIELDS[:-1]), 200, mc.MpartParseError),                          # schema lost a field
])
def test_fetch_guards_split_transient_from_structural(monkeypatch, body, status, exc):
    wire_opener(monkeypatch, body, status)
    with pytest.raises(exc):
        mc.fetch_surface_water()


@pytest.mark.parametrize("error", ["boom\n::add-mask::secret ##[error]forged", {"code": 503, "message": "x\n::stop-commands:: ##[add-mask]y"},
                                   {"code": "##[error]not-an-int"}, {"code": True}])
def test_upstream_error_free_text_never_reaches_an_exception(monkeypatch, error):
    """The runner honours `::cmd::` and legacy `##[cmd]` anywhere a log line carries them, so upstream
    FREE TEXT is never put into an exception message — only an integer code is."""
    wire_opener(monkeypatch, json.dumps({"error": error}).encode())
    with pytest.raises(mc.MpartFetchError) as e:
        mc.fetch_surface_water()
    msg = str(e.value)
    assert "\n" not in msg and "::" not in msg and "##[" not in msg and "forged" not in msg and "secret" not in msg
    assert ("code 503" in msg) or ("no numeric code" in msg)
    wire_opener(monkeypatch, json.dumps({"error": {"code": 400, "message": "bad\n##[error]field"}}).encode())
    with pytest.raises(mc.MpartParseError) as e2:
        mc.fetch_surface_water()
    assert "##[" not in str(e2.value) and "field" not in str(e2.value) and "code 400" in str(e2.value)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "http://insecure.example/x", ""])
def test_a_non_https_layer_url_is_refused_before_any_request(monkeypatch, url):
    def boom():
        raise AssertionError("must not open")
    monkeypatch.setattr(mc, "_opener", boom)
    with pytest.raises(mc.MpartParseError):
        mc.fetch_surface_water(url=url)


def test_network_errors_are_fetch_errors_and_carry_no_url(monkeypatch):
    class _Op:
        def open(self, url, timeout=60):
            raise OSError("connection reset for https://example/secret?x=1")
    monkeypatch.setattr(mc, "_opener", lambda: _Op())
    with pytest.raises(mc.MpartFetchError) as e:
        mc.fetch_surface_water()
    assert "OSError" in str(e.value) and "connection reset" not in str(e.value)


# ==============================================================================
# Client — canonical views (pure)
# ==============================================================================


def test_flag_text_is_verbatim_and_tokens_are_for_logic_only():
    assert mc.flag_text(" J") == "J" and mc.flag_text("J, Q") == "J, Q" and mc.flag_text("Not Measured") == "Not Measured"
    assert mc.flag_text(" ") == "" and mc.flag_text(None) == "" and mc.flag_text("k") == "k"     # never re-cased
    assert mc.flag_tokens("J, Q") == ("J", "Q") and mc.flag_tokens("Q,J") == ("J", "Q") and mc.flag_tokens("k") == ("K",)
    assert mc.flag_tokens("Not Measured") == ("MEASURED", "NOT") and mc.flag_tokens(None) == ()


@pytest.mark.parametrize("raw,norm", [("ng/L", "ng/L"), (" NG/L ", "ng/L"), ("ppt", "ng/L"), ("Parts Per Trillion", "ng/L"),
                                      ("ug/L", "ug/L"), ("ppb", "ppb"), (None, ""), ("", "")])
def test_normalize_unit(raw, norm):
    assert mc.normalize_unit(raw) == norm


def test_surface_water_view_uses_a_composite_key_and_verbatim_flags():
    v = mc.surface_water_view(sw_rows()[0])
    assert v["key"] == "240-169452-6|19-JD-0105|2022-07-06|0" and v["date"] == "2022-07-06" and v["site"] == "19-JD-0105"
    assert v["PFOS"] == "3.1" and v["PFOS_flag"] == "" and v["PFOS_mdl"] == "0.5" and v["unit"] == "ng/L"
    assert mc.surface_water_view(sw_rows()[1])["key"] == NAPIER
    k = mc.surface_water_view(sw_rows()[2])
    assert k["waterbody"] == "Johnson Drain" and k["PFOS_flag"] == "K" and k["PFOA"] == ""      # trailing space stripped
    assert mc.surface_water_view(dict(sw_rows()[0], CAS1763231_PFOSFlag="J, Q"))["PFOS_flag"] == "J, Q"
    assert mc.surface_water_view(dict(sw_rows()[0], CAS1763231_PFOSFlag="Not Measured"))["PFOS_flag"] == "Not Measured"


def test_a_reused_short_lab_label_does_not_collide():
    """Vista labels are short and site-derived (`JD-0100`); a later job at the same site can
    reuse one. The composite key keeps them distinct — so the layer can never wedge."""
    a = mc.surface_water_view(sw_row("JD-0100", "19-JD-0100", "2021-08-05", "Johnson Drain", "6 Mile Rd", 1.0, "K", 1.0))
    b = mc.surface_water_view(sw_row("JD-0100", "19-JD-0100", "2026-05-01", "Johnson Drain", "6 Mile Rd", 2.0, "", 0.5))
    c = mc.surface_water_view(sw_row("JD-0100", "19-JD-0100", "2026-05-01", "Johnson Drain", "6 Mile Rd", 2.0, "", 0.5, dup=1))
    d = mc.surface_water_view(sw_row("JD-0100", "19-JD-0105", "2026-05-01", "Johnson Drain", "u/s", 2.0, "", 0.5))
    assert len({a["key"], b["key"], c["key"], d["key"]}) == 4
    idx, dups = mc.index_rows([a, b, c, d], mc.SW_VIEW, "sw")
    assert dups == 0 and len(idx) == 4


def test_surface_water_key_when_the_lab_id_is_missing():
    assert mc.surface_water_view(dict(sw_rows()[0], LabSampleId=None))["key"] == "no-lab-id|19-JD-0105|2022-07-06|0"


def test_fish_view_keeps_the_code_as_published():
    v = mc.fish_view(fish_rows()[0])
    assert v["station"] == "1484" and v["pfos_ppb"] == "" and v["pfos_code"] == "I" and v["key"] == "2021233-S01"
    assert mc.fish_view(fish_rows()[1])["pfos_ppb"] == "5.9" and mc.fish_view(fish_rows()[1])["pfos_code"] == ""
    assert mc.fish_view(dict(fish_rows()[0], PFOScode=" K "))["pfos_code"] == "K"
    assert mc.fish_view(dict(fish_rows()[0], SampleID=None))["key"] == "no-sample-id|2021233-F001|1484|2021-08-05"


def test_fish_code_meanings_are_EGLEs_own_wording():
    assert "analytical interference" in mc.fish_code_meaning("I") and "could not be determined" in mc.fish_code_meaning("I")
    assert mc.fish_code_meaning("K").startswith("K: not detected") and "detection limit" in mc.fish_code_meaning("K")
    assert "estimated" in mc.fish_code_meaning("J") and "not enough sample" in mc.fish_code_meaning("QNS")
    assert mc.fish_code_meaning("NA") == "code NA (not defined here)"                        # unknown: verbatim, uninterpreted
    assert "K:" in mc.fish_code_meaning("K, J") and "J:" in mc.fish_code_meaning("K, J")
    assert mc.fish_code_meaning("") == ""


def test_sites_view_is_keyed_by_name_and_never_carries_staff_contacts():
    raw = dict(site_row(), SiteLead="Jane Staffer", SiteLeadEmail="j@example.gov", SiteLeadPhone="555", OBJECTID=9, GlobalID="{x}")
    v = mc.sites_view(raw)
    assert v["key"] == "advanced disposal services arbor hills landfill, inc.|site"
    assert v["county"] == "Washtenaw County" and v["residential_wells"] == "Yes" and v["facility_date"] == "2020-03-31"
    assert "Staffer" not in json.dumps(v) and "example.gov" not in json.dumps(v)
    assert mc.sites_view(dict(site_row(), County="Oakland County "))["county"] == "Oakland County"
    assert mc.sites_view(dict(site_row(), FacilityDate=ms("2020-03-31")))["facility_date"] == "2020-03-31"


def test_row_hash_is_stable_and_sensitive_to_every_watched_field():
    v = mc.surface_water_view(sw_rows()[1])
    h = mc.row_hash(v, mc.SW_VIEW)
    assert h == mc.row_hash(copy.deepcopy(v), mc.SW_VIEW)
    for field in mc.SW_VIEW[1:]:
        assert mc.row_hash(dict(v, **{field: "changed"}), mc.SW_VIEW) != h, field


def test_index_rows_disambiguates_duplicates_deterministically_and_rejects_blank_keys():
    a = mc.fish_view(fish_row("2021268-S01", 1507, "White Sucker", 5.9, None))
    b = mc.fish_view(fish_row("2021268-S01", 1507, "White Sucker", 8.1, None))               # same key, different data
    idx1, d1 = mc.index_rows([a, b], mc.FISH_VIEW, "fish")
    idx2, d2 = mc.index_rows([b, a], mc.FISH_VIEW, "fish")                                    # service order flipped
    assert d1 == d2 == 1 and idx1 == idx2 and set(idx1) == {"2021268-S01#1", "2021268-S01#2"}
    v1, v2 = mc.views_by_index_key([a, b], mc.FISH_VIEW), mc.views_by_index_key([b, a], mc.FISH_VIEW)
    assert {k: x["pfos_ppb"] for k, x in v1.items()} == {k: x["pfos_ppb"] for k, x in v2.items()}
    assert {k: mc.row_hash(x, mc.FISH_VIEW) for k, x in v1.items()} == idx1                  # views and index agree
    with pytest.raises(mc.MpartParseError):
        mc.index_rows([dict(a, key="")], mc.FISH_VIEW, "fish")


def test_snapshot_roundtrip_carries_hits_and_is_versioned():
    idx = {"b": "1", "a": "2"}
    hits = {"s2|2021-08-05|PFOS": "16.5", "s1|2022-01-01|PFOA": "180"}
    s = mc.snapshot_json(idx, hits)
    assert s == mc.snapshot_json(dict(reversed(list(idx.items()))), dict(reversed(list(hits.items()))))   # order-independent
    parsed_idx, parsed_hits = mc.parse_snapshot(s)
    assert parsed_idx == idx and parsed_hits == hits and json.loads(s)["v"] == mc.SNAPSHOT_VERSION == 2
    assert mc.parse_snapshot(mc.snapshot_json(idx))[1] == {}


@pytest.mark.parametrize("stored", ["not json", "[]", '{"rows": {"a": "1"}}', '{"v": 999, "rows": {}}', '{"v": 2, "rows": []}',
                                    '{"v": 1, "rows": {"a": "1"}, "hits": ["a|PFOS"]}',      # the old list-shaped format
                                    '{"v": 2, "rows": {"a": "1"}, "hits": ["a|PFOS"]}', ""])
def test_an_unreadable_or_differently_versioned_snapshot_parses_to_none(stored):
    assert mc.parse_snapshot(stored) is None


@pytest.mark.parametrize("code,exc", [(404, mc.MpartParseError), (410, mc.MpartParseError),
                                      (500, mc.MpartFetchError), (503, mc.MpartFetchError), (429, mc.MpartFetchError)])
def test_a_retired_layer_is_structural_while_server_errors_are_transient(monkeypatch, code, exc):
    import urllib.error

    class _Op:
        def open(self, url, timeout=60):
            raise urllib.error.HTTPError(url, code, "x", None, None)
    monkeypatch.setattr(mc, "_opener", lambda: _Op())
    with pytest.raises(exc) as e:
        mc.fetch_surface_water()
    assert str(code) in str(e.value)


def test_snapshot_refuses_to_truncate():
    with pytest.raises(mc.MpartParseError):
        mc.snapshot_json({f"key-{i:06d}": "0" * 16 for i in range(2000)})
    assert mc.snapshot_hash(mc.snapshot_json({"a": "2"})) != mc.snapshot_hash(mc.snapshot_json({"a": "3"}))


def test_epoch_dates():
    assert mc.epoch_ms_to_date(ms("2021-08-05")) == "2021-08-05" and mc.epoch_ms_to_date(None) == ""
    assert mc.epoch_ms_to_date("garbage") == "garbage"


# ==============================================================================
# Watcher — screening (pure)
# ==============================================================================

TH = {"PFOS": 12.0, "PFOA": 170.0, "PFHxS": 210.0, "PFNA": 30.0}
VY = {"PFOS": 2014, "PFOA": 2022, "PFHxS": 2023, "PFNA": 2023}


def test_screen_flags_only_detected_values_above_the_threshold():
    hits = mw.screen_surface_water(mc.surface_water_view(sw_rows()[1]), TH, VY)
    assert [(h["analyte"], h["value"], h["threshold"]) for h in hits] == [("PFOS", "16.5", 12.0)]
    assert mw.screen_surface_water(mc.surface_water_view(sw_rows()[0]), TH, VY) == []             # 3.1 < 12
    exact = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 12.0, "", 1))
    assert mw.screen_surface_water(exact, TH, VY) == []                                           # "above", not "at"


def test_a_K_flag_is_a_nondetect_and_is_never_compared():
    v = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 20.0, "K", 20.0))         # value = the MDL
    assert mw.is_nondetect(v["PFOS_flag"]) and mw.screen_surface_water(v, TH, VY) == []
    assert not mw.is_nondetect("J") and not mw.is_nondetect("J, Q") and mw.is_nondetect(" k ")


def test_a_J_flagged_value_above_the_threshold_is_compared_and_carries_its_flag_as_published():
    v = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 13.0, "J", 1))
    assert [(h["analyte"], h["flag"]) for h in mw.screen_surface_water(v, TH, VY)] == [("PFOS", "J")]


def test_screening_needs_a_unit_that_reads_as_ng_per_l():
    for unit in ("ng/L", "ppt", " NG/L "):
        assert mw.screen_surface_water(mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 30.0, "", 1, unit=unit)), TH, VY)
    for unit in ("ug/L", "ppb", "", "mg/L"):
        assert mw.screen_surface_water(mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 30.0, "", 1, unit=unit)), TH, VY) == []


def test_a_sample_older_than_the_value_in_force_is_not_screened():
    old = mc.surface_water_view(sw_row("x", "s", "2021-06-01", "w", "d", 5.0, "", 1, pfoa=200.0, pfhxs=None, pfna=99.0))
    assert mw.screen_surface_water(old, TH, VY) == []               # PFOA value dates from 2022, PFNA from 2023
    new = mc.surface_water_view(sw_row("x", "s", "2024-06-01", "w", "d", 5.0, "", 1, pfoa=200.0, pfna=99.0))
    assert sorted(h["analyte"] for h in mw.screen_surface_water(new, TH, VY)) == ["PFNA", "PFOA"]
    pfos_old = mc.surface_water_view(sw_row("x", "s", "2021-06-01", "w", "d", 40.0, "", 1))
    assert [h["analyte"] for h in mw.screen_surface_water(pfos_old, TH, VY)] == ["PFOS"]            # PFOS value dates from 2014


def test_screening_ignores_blank_and_unparseable_values_and_analytes_without_a_threshold():
    v = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", None, None, None))
    assert mw.screen_surface_water(v, TH, VY) == []
    assert mw.screen_surface_water(dict(v, PFOS="n/a"), TH, VY) == []
    assert mw.screen_surface_water(mc.surface_water_view(sw_rows()[1]), {"PFOA": 170.0}, VY) == []


NAPIER_HIT = "19-UUTJD-0010|2021-08-05|PFOS"


def test_hits_are_identified_by_site_date_analyte_not_by_row_key():
    views = mc.views_by_index_key([mc.surface_water_view(r) for r in sw_rows()], mc.SW_VIEW)
    hit_map = mw.screen_all(views, TH, VY)
    assert list(hit_map) == [NAPIER] and mw.hits_by_id(hit_map) == {NAPIER_HIT: "16.5"}
    # the same result re-issued under a new lab id / duplicate flag keeps its identity
    reissued = mc.views_by_index_key([mc.surface_water_view(dict(sw_rows()[1], LabSampleId="UT-0100R", Duplicate=1))], mc.SW_VIEW)
    assert mw.hits_by_id(mw.screen_all(reissued, TH, VY)) == {NAPIER_HIT: "16.5"}
    undated = mc.surface_water_view(dict(sw_rows()[1], CollectionDate=None, SiteCode=None))
    assert mw.hit_id(undated, "PFOS") == f"{undated['key']}|PFOS"                               # falls back to the row key


def test_an_undated_row_is_screened_against_the_current_value():
    v = mc.surface_water_view(dict(sw_row("x", "s", "2024-01-01", "w", "d", 30.0, "", 1, pfna=99.0), CollectionDate=None))
    assert v["date"] == "" and sorted(h["analyte"] for h in mw.screen_surface_water(v, TH, VY)) == ["PFNA", "PFOS"]


def test_rescreened_keys_are_unchanged_rows_carrying_a_new_hit_id():
    hit_map = {"a": [{"id": "x|d|PFOS"}], "b": [{"id": "y|d|PFOS"}], "c": [{"id": "z|d|PFOS"}]}
    diff = {"added": ["a"], "changed": [], "removed": []}
    assert mw.rescreened_keys(hit_map, {"x|d|PFOS", "y|d|PFOS"}, diff) == ["b"]        # `a` is already shown as NEW; `c` is not new


def test_threshold_config_is_validated():
    assert mw.load_thresholds({}) == (TH, VY)
    assert mw.load_thresholds({"thresholds_ng_l": {"PFOS": 10}})[0] == {"PFOS": 10.0}
    with pytest.raises(ValueError, match="PFHXS"):
        mw.load_thresholds({"thresholds_ng_l": {"PFHXS": 1}})
    with pytest.raises(ValueError):
        mw.load_thresholds({"thresholds_verified_year": {"BOGUS": 2020}})
    with pytest.raises(ValueError, match="> 0"):
        mw.load_thresholds({"thresholds_ng_l": {"PFOS": 0}})
    # overriding ONE analyte's verified year must not silently drop the others' (they would then screen every old sample)
    assert mw.load_thresholds({"thresholds_verified_year": {"PFOS": 2016}})[1] == {**VY, "PFOS": 2016}


def test_guard_config_is_validated():
    assert mw.load_guards({}) == (3, 0.5, 2)
    assert mw.load_guards({"stale_alert_after_skips": 0, "max_shrink_fraction": 1, "accept_shrink_after_skips": 0}) == (1, 1.0, 1)
    for bad in (0, -0.1, 1.5, "0"):
        with pytest.raises(ValueError, match="max_shrink_fraction"):
            mw.load_guards({"max_shrink_fraction": bad})


# ==============================================================================
# Watcher — state + diff + copy (pure)
# ==============================================================================


def _row(item, change, h="", note="", snap=""):
    return ["2026-09-28", item, "label", change, h, note, "now", snap]


_ZERO = {"skips": 0, "held": 0, "held_stable": 0, "held_hash": ""}


def test_build_state_takes_the_last_snapshot_row_and_counts_consecutive_skips():
    st = mw.build_state([_row("mpart:sw", "baseline", "h1", snap="S1"), _row("mpart:sw", "fetch-skipped", "h1"),
                         _row("mpart:sw", "fetch-skipped", "h1"), _row("mpart:fish", "baseline", "f1"),
                         _row("mpart:fish", "fetch-skipped"), _row("mpart:fish", "fetch-ok"), _row("mpart:fish", "fetch-skipped")])
    assert st["mpart:sw"]["skips"] == 2 and st["mpart:sw"]["hash"] == "h1"
    assert st["mpart:fish"]["skips"] == 1                                                     # the fetch-ok reset the count
    st2 = mw.build_state([_row("mpart:sw", "baseline", "h1"), _row("mpart:sw", "fetch-skipped"),
                          _row("mpart:sw", "changed", "h2", snap="S")])
    assert st2["mpart:sw"] == {"hash": "h2", "snapshot": "S", **_ZERO}
    assert mw.build_state([_row("mpart:sw", "fetch-skipped")]) == {}                          # never baselined
    assert mw.build_state([["d", "mpart:sw", "l", "baseline", "h9"]])["mpart:sw"] == {"hash": "h9", "snapshot": "", **_ZERO}  # trailing cells stripped


def test_build_state_keeps_fetch_failures_and_shrink_holds_in_separate_counters():
    rows = [_row("mpart:sw", "baseline", "h1", snap="S"), _row("mpart:sw", "fetch-skipped", "h1"), _row("mpart:sw", "fetch-skipped", "h1"),
            _row("mpart:sw", "shrink-held", "X"), _row("mpart:sw", "shrink-held", "X"), _row("mpart:sw", "shrink-held", "Y")]
    st = mw.build_state(rows)["mpart:sw"]
    assert st["skips"] == 2 and st["held"] == 3                        # two failed fetches never count toward the holds
    assert st["held_hash"] == "Y" and st["held_stable"] == 1           # X, X, Y: the streak of the SAME response restarted
    assert mw.build_state(rows[:5])["mpart:sw"]["held_stable"] == 2
    assert mw.build_state(rows + [_row("mpart:sw", "fetch-ok")])["mpart:sw"]["held"] == 3      # a fetch-ok is about fetching only
    for reset in ("held-cleared", "changed", "baseline"):
        st2 = mw.build_state(rows + [_row("mpart:sw", reset, "h2", snap="S2")])["mpart:sw"]
        assert (st2["held"], st2["held_stable"], st2["held_hash"]) == (0, 0, ""), reset


def test_diff_index():
    d = mw.diff_index({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "9", "d": "4"})
    assert d == {"added": ["d"], "removed": ["c"], "changed": ["b"]}


@pytest.mark.parametrize("old,new,suspect", [(17, 0, True), (17, 8, True), (17, 9, False), (17, 17, False), (0, 0, False), (7, 2, True)])
def test_suspect_shrink(old, new, suspect):
    assert mw.is_suspect_shrink(old, new, 0.5) is suspect


def _sw_views(rows=None):
    return mc.views_by_index_key([mc.surface_water_view(r) for r in (rows or sw_rows())], mc.SW_VIEW)


def _hit_map(views, keys):
    return {k: mw.screen_surface_water(views[k], TH, VY) for k in keys}


def test_surface_water_copy_prints_values_verbatim_with_flags_and_the_screening_caveats():
    views = _sw_views()
    diff = {"added": [NAPIER], "removed": [], "changed": []}
    body = mw.format_change_body(mw.ITEM_SW, diff, views, _hit_map(views, [NAPIER]), {NAPIER_HIT}, TH, VY)
    assert "PFOS 16.5 (MDL 1.03)" in body and "PFNA 1.04 [J]" in body and "collected 2021-08-05" in body and "Napier Rd" in body
    assert ">>> PFOS 16.5 ng/L is ABOVE the published Rule 57 non-drinking-water value of 12 ng/L" in body
    assert "SCREENING comparison" in body and "PFOS 12 ng/L (verified 2014)" in body and "PFHxS 210 ng/L (verified 2023)" in body
    # the non-drink designation is documented for one water body; the values are applied to every row — and the note says so
    assert "applied to EVERY surface-water row" in body and "carries no water-body designation" in body
    assert "PFOS 11, PFOA 66, PFHxS 59, PFNA 19 ng/L" in body                              # the lower drink-water values
    assert "CURRENT value was verified" in body and "for PFOA, a much higher one" in body and "a row with no collection date is screened" in body
    assert "no such value was in force" not in body and "vary by report" not in body       # both were inaccurate
    assert "qualifiers are analytical-laboratory specific" in body and "'K' flag" in body
    assert 'were not detected in the sample and therefore the method detection limit (MDL) is displayed' in body   # EGLE's own words, attributed
    assert "every flag is printed exactly as published — the monitor adds no meaning of its own" in body
    assert "monitor's own notes" in body and "Rule 100 designation" in body               # the designation's source is named
    assert "revised in 2022-2023" not in body                                              # PFHxS/PFNA were ADDED, not revised


def test_the_static_pull_note_attributes_the_dates_to_the_saved_layer_description():
    views = _sw_views()
    body = mw.format_change_body(mw.ITEM_SW, {"added": [NAPIER], "removed": [], "changed": []}, views, {}, set(), TH, VY)
    assert "as saved 2026-09-26" in body and "'last pulled 2/2025'" in body and "current pull date" in body
    assert "republished the layer" in body
    assert "static pull" not in mw.format_change_body(mw.ITEM_SITES, {"added": [], "removed": ["x"], "changed": []}, {}, {}, set())


def test_the_screening_note_is_rendered_from_the_thresholds_in_use():
    note = mw.screen_note({"PFOS": 10.0}, {"PFOS": 2014})
    assert "PFOS 10 ng/L (verified 2014)" in note and "PFOA" not in note.split("spreadsheet —")[1].split(".")[0]


def test_a_hit_that_was_already_recorded_is_not_announced_as_new():
    views = _sw_views()
    body = mw.format_change_body(mw.ITEM_SW, {"added": [], "removed": [], "changed": [NAPIER]}, views,
                                 _hit_map(views, [NAPIER]), set(), TH, VY, {NAPIER_HIT: "16.5"})
    assert "already recorded; not new" in body and "is ABOVE" not in body and "recorded earlier" not in body


def test_a_revised_value_on_an_already_recorded_hit_says_what_was_recorded_and_is_still_not_new():
    views = _sw_views([dict(sw_rows()[1], CAS1763231_PFOS=18.0)])
    key = next(iter(views))
    body = mw.format_change_body(mw.ITEM_SW, {"added": [], "removed": [], "changed": [key]}, views,
                                 _hit_map(views, [key]), set(), TH, VY, {NAPIER_HIT: "16.5"})
    assert ">>> PFOS 18 ng/L" in body.replace("18.0", "18") and "the value on record for it is 16.5 ng/L; not new" in body
    assert "is ABOVE" not in body and "revised" not in body and "recorded earlier" not in body


def test_a_J_hit_is_printed_as_published_and_never_glossed_as_estimated():
    """EGLE words the surface-water J two ways ('an estimated concentration … above the MDL but below
    the laboratory reporting limit' in its prose, 'below the Reporting Limit/LOQ' in its table) and
    says qualifiers are lab-specific. The HIT LINE prints the flag exactly and adds no meaning of the
    monitor's own; EGLE's prose appears only as an attributed quote in the caveat paragraph."""
    views = _sw_views([sw_row("x", "s", "2024-01-01", "w", "d", 13.0, "J", 1)])
    key = next(iter(views))
    body = mw.format_change_body(mw.ITEM_SW, {"added": [key], "removed": [], "changed": []}, views, _hit_map(views, [key]),
                                 {mw.hit_id(views[key], "PFOS")}, TH, VY)
    hit_lines = [ln for ln in body.splitlines() if ln.lstrip().startswith(">>>")]
    assert hit_lines == ["    >>> PFOS 13 [J] ng/L is ABOVE the published Rule 57 non-drinking-water value of 12 ng/L"]
    assert "estimated" not in body.split("Rule 57 comparison")[0]                        # no gloss on the hit or anywhere above the caveat
    assert 'EGLE describes a \'J\' flag as "an estimated concentration as the result is above the MDL' in body   # ...attributed


def test_unchanged_rows_newly_above_a_value_get_their_own_section():
    views = _sw_views()
    body = mw.format_change_body(mw.ITEM_SW, {"added": [], "removed": [], "changed": [], "rescreened": [NAPIER]}, views,
                                 _hit_map(views, [NAPIER]), {NAPIER_HIT}, TH, VY)
    assert "UNCHANGED rows now above a screening value" in body and "none of the watched fields on the row changed" in body and "(1):" in body
    assert "is ABOVE" in body and "NEW rows" not in body


def test_subject_wording_is_a_screening_not_a_determination_and_counts_are_honest():
    d = {"added": ["a"], "removed": [], "changed": ["b", "c"]}
    assert mw.subject_for(mw.ITEM_SW, d, True).startswith("[MPART data] Rule 57 screening:")
    assert "EXCEEDANCE" not in mw.subject_for(mw.ITEM_SW, d, True)
    assert mw.subject_for(mw.ITEM_SW, d, False) == "[MPART data] 1 new, 2 changed: surface-water PFAS"
    assert mw.subject_for(mw.ITEM_SITES, {"added": [], "removed": ["x"], "changed": []}, False) == "[MPART data] 1 removed: PFAS sites/AOIs"
    assert mw.subject_for(mw.ITEM_SW, {"added": [], "removed": [], "changed": []}, False) == "[MPART data] changed: surface-water PFAS"   # never an empty list


def test_fish_copy_uses_EGLEs_definitions_and_never_calls_an_I_code_a_detection_or_a_publication_gap():
    rows = [fish_row("2021233-S01", 1484, "Brown Trout", None, "I"), fish_row("2021268-S01", 1507, "White Sucker", 5.9, None),
            fish_row("2021999-S01", 1507, "White Sucker", 2.1, "K"), fish_row("2021998-S01", 1507, "Carp", None, "QNS")]
    views = mc.views_by_index_key([mc.fish_view(r) for r in rows], mc.FISH_VIEW)
    body = mw.format_change_body(mw.ITEM_FISH, {"added": sorted(views), "removed": [], "changed": []}, views, {}, set())
    assert "no concentration reported — I: analytical interference was present; a concentration could not be determined" in body
    assert "PFOS 5.9 ppb (edible portion)" in body
    assert "PFOS 2.1 ppb (edible portion) — K: not detected — the value shown is the method detection limit" in body
    assert "QNS: not enough sample remained" in body and "No threshold is applied" in body
    assert "no value published" not in body and "fillet" not in body


def test_screened_rows_are_listed_first_so_the_cap_cannot_hide_them():
    rows = [sw_row(f"L{i:03d}", "19-JD-0105", "2025-01-01", "w", "d", 1.0, "", 0.5) for i in range(40)]
    rows.append(sw_row("ZZZ-LAST", "19-JD-0105", "2025-02-01", "w", "d", 99.0, "", 0.5))       # sorts last alphabetically
    views = _sw_views(rows)
    hk = next(k for k in views if k.startswith("ZZZ-LAST"))
    hit_map = {hk: mw.screen_surface_water(views[hk], TH, VY)}
    body = mw.format_change_body(mw.ITEM_SW, {"added": sorted(views), "removed": [], "changed": []}, views, hit_map,
                                 {mw.hit_id(views[hk], "PFOS")}, TH, VY)
    assert "NEW rows (41)" in body and "+ 16 more" in body and "sample ZZZ-LAST" in body
    assert body.index("ZZZ-LAST") < body.index("sample L000") and "Snapshot JSON cell" in body


def test_sites_and_removed_copy():
    views = mc.views_by_index_key([mc.sites_view(r) for r in site_rows()], mc.SITES_VIEW)
    body = mw.format_change_body(mw.ITEM_SITES, {"added": sorted(views), "removed": ["gone|site"], "changed": []}, views, {}, set())
    assert "Holloway Sand and Gravel" in body and "residential wells sampled: Yes" in body and "- gone|site" in body


def test_an_unscreenable_unit_is_said_out_loud():
    views = _sw_views([sw_row("X1", "s", "2025-01-01", "w", "d", 30.0, "", 1, unit="ug/L")])
    body = mw.format_change_body(mw.ITEM_SW, {"added": list(views), "removed": [], "changed": []}, views, {}, set(), TH, VY)
    assert "NOT screened (unit 'ug/L' does not read as ng/L)" in body


# ==============================================================================
# Watcher — run() flows
# ==============================================================================

CFG = {"mpart": {"enabled": True, "recipients": ["trisha@example.org"], "stale_alert_after_skips": 3}}


class World:
    def __init__(self):
        self.sw, self.fish, self.sites = sw_rows(), fish_rows(), site_rows()
        self.error = {}          # item-name -> exception to raise from its fetch


def _wire(monkeypatch, world=None, cfg=CFG, fake=None, send=None):
    world = world or World()
    fake = fake or FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "PUBSHEET")
    monkeypatch.setattr(mw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(mw.dc, "sheets_service", lambda: fake)

    def _send(subj, body, c, recipients=None):
        if send is not None:
            return send(subj, body, c, recipients)
        sent.append((subj, body, recipients))
        return True
    monkeypatch.setattr(mw.ea, "send_email", _send)

    def _mk(name, attr):
        def fetch(*a, **k):
            if name in world.error:
                raise world.error[name]
            return copy.deepcopy(getattr(world, attr))
        return fetch
    monkeypatch.setattr(mw.mc, "fetch_surface_water", _mk("sw", "sw"))
    monkeypatch.setattr(mw.mc, "fetch_fish", _mk("fish", "fish"))
    monkeypatch.setattr(mw.mc, "fetch_sites", _mk("sites", "sites"))
    return world, fake, sent


def rows_of(fake, item=None, change=None):
    rows = fake._values._tabs.get(sw.TAB_MPART, [])[1:]
    return [r for r in rows if (item is None or r[1] == item) and (change is None or r[3] == change)]


def test_disabled_is_a_noop_touching_nothing(monkeypatch):
    world, fake, sent = _wire(monkeypatch, cfg={"mpart": {"enabled": False}})
    monkeypatch.setattr(mw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    assert mw.run([]) == 0


def test_first_run_baselines_all_three_items_silently_and_records_the_hits_already_present(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    rows = rows_of(fake)
    assert sorted(r[1] for r in rows) == ["mpart:fish", "mpart:sites", "mpart:sw"] and all(r[3] == "baseline" for r in rows)
    assert sent == []
    snap = json.loads(rows_of(fake, "mpart:sw")[0][7])
    assert set(snap["rows"]) == {mc.surface_water_view(r)["key"] for r in sw_rows()}
    assert snap["hits"] == {NAPIER_HIT: "16.5"} and "1 result(s) already above a screening value" in rows_of(fake, "mpart:sw")[0][5]
    assert "above a screening" not in rows_of(fake, "mpart:fish")[0][5] + rows_of(fake, "mpart:sites")[0][5]   # only surface water is screened


def test_second_run_unchanged_writes_nothing(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    assert mw.run([]) == 0 and len(rows_of(fake)) == 3 and sent == []


def test_a_new_screened_sample_writes_the_durable_row_BEFORE_the_alert(monkeypatch):
    seen_at_send = []

    def send(subj, body, c, recipients):
        seen_at_send.append((len(rows_of(fake, "mpart:sw", "changed")), subj))     # the row must already be there
        return True
    world, fake, sent = _wire(monkeypatch, send=send)
    world.sw = world.sw[:1] + world.sw[2:]                    # baseline WITHOUT the Napier sample
    assert mw.run([]) == 0
    world.sw = sw_rows()                                      # ...then EGLE republishes with it
    assert mw.run([]) == 0
    ch = rows_of(fake, "mpart:sw", "changed")
    assert len(ch) == 1 and "1 added, 0 changed, 0 removed" in ch[0][5] and NAPIER in ch[0][5] and f"NEW screening hits: {NAPIER_HIT}" in ch[0][5]
    assert seen_at_send == [(1, "[MPART data] Rule 57 screening: a reported value is above a non-drink value — new/changed surface-water PFAS result(s)")]
    assert mw.run([]) == 0 and len(rows_of(fake, "mpart:sw", "changed")) == 1     # not re-alerted
    assert json.loads(ch[0][7])["hits"] == {NAPIER_HIT: "16.5"}                   # the hit is now on record


def test_an_already_recorded_hit_is_not_re_announced_when_its_row_changes(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0                                    # Napier's PFOS hit is baselined
    world.sw[1]["SampleType"] = "Grab"                        # an unrelated edit to the Napier row (e.g. a bulk re-pull)
    assert mw.run([]) == 0
    assert len(sent) == 1
    subj, body, _ = sent[0]
    assert "Rule 57 screening" not in subj and subj == "[MPART data] 1 changed: surface-water PFAS"
    assert "already recorded; not new" in body and "is ABOVE" not in body


def test_a_hit_that_newly_appears_on_a_changed_row_is_announced(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.sw[0]["CAS1763231_PFOS"] = 40.0                     # the lab revised 3.1 -> 40 on a previously clean row
    assert mw.run([]) == 0
    assert len(sent) == 1 and sent[0][0].startswith("[MPART data] Rule 57 screening") and "CHANGED rows" in sent[0][1]


def test_a_new_nondetect_or_low_sample_is_a_plain_new_sample_alert(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.sw.append(sw_row("JD-0100", "19-JD-0100", "2026-09-01", "Johnson Drain", "6 Mile Rd", 20.0, "K", 20.0))
    assert mw.run([]) == 0
    assert len(sent) == 1 and sent[0][0] == "[MPART data] 1 new: surface-water PFAS"


def test_removed_and_new_fish_and_sites_alert_and_staff_contacts_never_appear(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.fish.append(fish_row("2026100-S01", 1507, "White Sucker", 7.7, None, "2026-08-01"))
    world.sites.append(dict(site_row("New PFAS Site", wells="TBD"), SiteLead="Jane Staffer", SiteLeadEmail="j@example.gov"))
    assert mw.run([]) == 0
    assert len(sent) == 2
    blob = " ".join(s[1] for s in sent) + json.dumps(fake._values._tabs[sw.TAB_MPART])
    assert "PFOS 7.7 ppb" in blob and "New PFAS Site" in blob
    assert "Staffer" not in blob and "example.gov" not in blob


def test_duplicate_keys_are_disambiguated_recorded_and_never_wedge_the_layer(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    world.fish = fish_rows() + [fish_row("2021268-S01", 1507, "White Sucker", 8.1, None)]      # a second row with a taken SampleID
    assert mw.run([]) == 0
    assert "1 duplicate row key(s) disambiguated" in rows_of(fake, "mpart:fish")[0][5]
    assert mw.run([]) == 0 and len(rows_of(fake, "mpart:fish")) == 1 and sent == []            # stable across runs


def test_empty_or_blank_recipients_are_display_only_never_the_coalition_list(monkeypatch):
    for recips in ([], [""], [None], ["  "]):
        cfg = copy.deepcopy(CFG)
        cfg["mpart"]["recipients"] = recips
        world, fake, sent = _wire(monkeypatch, cfg=cfg)
        assert mw.run([]) == 0
        world.sw.append(sw_row("L-NEW", "s", "2026-09-01", "w", "d", 30.0, "", 1))
        assert mw.run([]) == 0
        assert len(rows_of(fake, "mpart:sw", "changed")) == 1 and sent == [], recips


# --- alert delivery is never silent ------------------------------------------------------------


def test_a_send_that_returns_false_or_raises_makes_the_run_red_but_keeps_the_row(monkeypatch):
    for failing in (lambda *a: False, lambda *a: (_ for _ in ()).throw(RuntimeError("smtp"))):
        world, fake, sent = _wire(monkeypatch, send=failing)
        assert mw.run([]) == 0
        world.sw.append(sw_row("L-NEW", "s", "2026-09-01", "w", "d", 30.0, "", 1))
        assert mw.run([]) == 1                                    # the alert was lost: loud
        assert len(rows_of(fake, "mpart:sw", "changed")) == 1


def test_a_formatting_bug_is_contained_and_reported(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    monkeypatch.setattr(mw, "format_change_body", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    world.sw.append(sw_row("L-NEW", "s", "2026-09-01", "w", "d", 30.0, "", 1))
    assert mw.run([]) == 1 and len(rows_of(fake, "mpart:sw", "changed")) == 1 and sent == []


def test_one_broken_item_does_not_stop_the_others(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    monkeypatch.setattr(mw.mc, "fish_view", lambda a: (_ for _ in ()).throw(ValueError("weird attributes")))
    assert mw.run([]) == 1
    assert len(rows_of(fake, "mpart:sw", "baseline")) == 1 and len(rows_of(fake, "mpart:sites", "baseline")) == 1
    assert rows_of(fake, "mpart:fish") == []


# --- failure modes -----------------------------------------------------------------------------------


def test_a_fetch_failure_is_recorded_alerts_at_the_threshold_repeats_weekly_and_recovers(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.error["fish"] = mc.MpartFetchError("GET failed: OSError")
    for i in (1, 2):
        assert mw.run([]) == 0 and sent == [], i
    assert mw.run([]) == 0
    assert len(sent) == 1 and "mpart:fish unreadable for 3 runs" in sent[0][0] and "going UNSEEN" in sent[0][1]
    assert len(rows_of(fake, "mpart:fish", "fetch-skipped")) == 3
    for _ in range(6):                                                                    # skips 4..9: no repeat yet
        assert mw.run([]) == 0
    assert len(sent) == 1
    assert mw.run([]) == 0 and len(sent) == 2 and "unreadable for 10 runs" in sent[1][0]   # weekly reminder
    world.error.clear()
    assert mw.run([]) == 0 and len(rows_of(fake, "mpart:fish", "fetch-ok")) == 1
    world.error["fish"] = mc.MpartFetchError("blip")
    assert mw.run([]) == 0 and mw.run([]) == 0 and len(sent) == 2                          # skips 1, 2 after the reset: quiet
    assert mw.run([]) == 0 and len(sent) == 3 and "unreadable for 3 runs" in sent[2][0]    # the counter DID restart (3, not 13)
    assert rows_of(fake, "mpart:fish", "changed") == []                                     # skips never invent changes


def test_an_empty_success_is_a_suspect_response_not_a_mass_removal(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    good = world.sw
    world.sw = []                                             # a republish window: 200 OK with zero features
    assert mw.run([]) == 0
    assert sent == [] and rows_of(fake, "mpart:sw", "changed") == []
    held = rows_of(fake, "mpart:sw", "shrink-held")
    assert len(held) == 1 and "suspect response: 0 row(s) now vs 3 recorded" in held[0][5]
    assert rows_of(fake, "mpart:sw", "fetch-skipped") == []   # a shrink is NOT a fetch failure
    world.sw = good                                           # ...and it recovers: nothing was lost, nothing re-announced
    assert mw.run([]) == 0 and sent == []
    assert len(rows_of(fake, "mpart:sw", "held-cleared")) == 1


def test_a_shrink_that_persists_is_accepted_as_real(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.sites = world.sites[:0]                             # the sites layer really is emptied
    assert mw.run([]) == 0 and mw.run([]) == 0 and sent == []                             # two identical suspect runs: held
    assert len(rows_of(fake, "mpart:sites", "shrink-held")) == 2
    assert mw.run([]) == 0                                                                  # the third identical observation: accepted
    assert len(sent) == 1 and "REMOVED rows (2" in sent[0][1]
    assert len(rows_of(fake, "mpart:sites", "changed")) == 1
    assert rows_of(fake, "mpart:sites", "held-cleared") == []                               # the changed row ends the streak itself


def test_a_partial_shrink_below_the_fraction_is_also_held_back(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    world.fish = [fish_row(f"2021{i:03d}-S01", 1507, "Sucker", 6.0, None) for i in range(10)]
    assert mw.run([]) == 0
    full = world.fish
    world.fish = full[:3]                                     # 10 -> 3 (< 50%): a republish glitch, not a real change
    assert mw.run([]) == 0
    assert rows_of(fake, "mpart:fish", "changed") == [] and sent == []
    assert len(rows_of(fake, "mpart:fish", "shrink-held")) == 1
    world.fish = full[:6]                                     # 6 of 10 is a normal-sized drop: diffed and alerted at once
    assert mw.run([]) == 0
    assert len(rows_of(fake, "mpart:fish", "changed")) == 1 and "REMOVED rows (4" in sent[-1][1]


def _ten_fish(world):
    world.fish = [fish_row(f"2021{i:03d}-S01", 1507, "Sucker", 6.0, None) for i in range(10)]
    return world.fish


def test_fetch_failures_never_pre_authorize_accepting_a_truncated_response(monkeypatch):
    """The HIGH finding: a shared counter let two failed fetches (skips=2) satisfy the shrink guard,
    so the first truncated response after an outage was accepted and a mass-removal alert fired."""
    world, fake, sent = _wire(monkeypatch)
    full = _ten_fish(world)
    assert mw.run([]) == 0
    world.error["fish"] = mc.MpartFetchError("GET failed: OSError")
    assert mw.run([]) == 0 and mw.run([]) == 0                                              # skips == 2
    world.error.clear()
    world.fish = full[:2]                                                                    # the outage ends with a TRUNCATED response
    assert mw.run([]) == 0
    assert rows_of(fake, "mpart:fish", "changed") == [] and sent == []                       # held, not accepted
    assert len(rows_of(fake, "mpart:fish", "shrink-held")) == 1 and len(rows_of(fake, "mpart:fish", "fetch-ok")) == 1
    assert mw.run([]) == 0 and sent == []                                                    # still held (2nd identical)
    assert mw.run([]) == 0                                                                   # 3rd identical: accepted
    assert len(rows_of(fake, "mpart:fish", "changed")) == 1 and "REMOVED rows (8" in sent[-1][1]


def test_a_flapping_shrunken_response_is_never_accepted_and_the_liveness_alert_fires(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    full = _ten_fish(world)
    assert mw.run([]) == 0
    for n in (1, 2, 3, 1, 2):                                                                # a DIFFERENT shrunken response each run
        world.fish = full[:n]
        assert mw.run([]) == 0
    assert rows_of(fake, "mpart:fish", "changed") == []
    assert len(rows_of(fake, "mpart:fish", "shrink-held")) == 5
    assert [s[0] for s in sent] == ["[MPART data] mpart:fish returning a suspect (much smaller) response for 3 runs"]


def test_a_recovery_clears_the_hold_streak_so_a_later_shrink_starts_over(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    full = _ten_fish(world)
    assert mw.run([]) == 0
    world.fish = full[:2]
    assert mw.run([]) == 0 and mw.run([]) == 0                                               # two identical holds
    world.fish = full                                                                        # normal again
    assert mw.run([]) == 0 and len(rows_of(fake, "mpart:fish", "held-cleared")) == 1
    world.fish = full[:2]                                                                    # the SAME shrunken shape, weeks later
    assert mw.run([]) == 0
    assert rows_of(fake, "mpart:fish", "changed") == [] and sent == []                       # held again from scratch, not accepted
    assert len(rows_of(fake, "mpart:fish", "shrink-held")) == 3


def test_an_empty_first_response_is_loud_and_baselines_nothing(monkeypatch, capsys):
    world, fake, sent = _wire(monkeypatch)
    world.sites = []
    assert mw.run([]) == 1
    assert rows_of(fake, "mpart:sites") == [] and len(rows_of(fake, "mpart:sw", "baseline")) == 1   # the others still ran
    assert "refusing to baseline nothing" in capsys.readouterr().out
    world.sites = site_rows()                                                                # fixed: the next run baselines normally
    assert mw.run([]) == 0 and len(rows_of(fake, "mpart:sites", "baseline")) == 1


def test_a_hits_only_change_is_alerted_with_the_rows_and_never_as_an_empty_alert(monkeypatch):
    """Tightening a screening value re-screens rows the layer did not change: the alert must
    list them (not a subject-only 'changed:' email), and the durable row records why."""
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    tight = copy.deepcopy(CFG)
    tight["mpart"]["thresholds_ng_l"] = {"PFOS": 2.0}                                        # JD-0105's 3.1 is now above 2
    monkeypatch.setattr(mw, "load_config", lambda: copy.deepcopy(tight))
    assert mw.run([]) == 0
    ch = rows_of(fake, "mpart:sw", "changed")
    assert len(ch) == 1 and "0 added, 0 changed, 0 removed, 1 re-screened" in ch[0][5]
    assert len(sent) == 1
    assert sent[0][0] == ("[MPART data] Rule 57 screening: 1 unchanged surface-water PFAS row(s) now above a non-drink value "
                          "(screening values or watch logic changed)")                   # it does NOT blame the layer
    body = sent[0][1]
    assert body.startswith("The monitor re-screened a watched MPART PFAS open-data layer. None of the fields this watch reads changed")
    assert "UNCHANGED rows now above a screening value" in body and "19-JD-0105" in body and "3.1" in body and "is ABOVE" in body
    assert mw.run([]) == 0 and len(sent) == 1                                                # and it is not re-announced


def test_the_subject_says_so_when_rows_changed_AND_an_unchanged_row_is_newly_above_a_value():
    d = {"added": [], "changed": ["a"], "removed": ["b"], "rescreened": ["c"]}
    assert mw.subject_for(mw.ITEM_SW, d, True, new_hit_in_rows=False) == (
        "[MPART data] Rule 57 screening: 1 unchanged surface-water PFAS row(s) now above a non-drink value "
        "(screening values or watch logic changed) — also 1 changed, 1 removed")
    assert mw.subject_for(mw.ITEM_SW, d, True, new_hit_in_rows=True).endswith("new/changed surface-water PFAS result(s)")
    assert mw.subject_for(mw.ITEM_SW, d, False) == "[MPART data] 1 changed, 1 removed: surface-water PFAS"


def test_a_reissued_row_with_a_new_lab_id_is_not_announced_as_a_new_exceedance(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0                                                                   # Napier PFOS 16.5 baselined as a hit
    world.sw[1] = dict(world.sw[1], LabSampleId="UT-0100R", CAS1763231_PFOS=18.0)            # re-issued under a new id, value revised
    assert mw.run([]) == 0
    assert len(sent) == 1
    subj, body, _ = sent[0]
    assert "Rule 57 screening" not in subj and "the value on record for it is 16.5 ng/L; not new" in body and "is ABOVE" not in body


def test_a_result_that_leaves_the_layer_and_returns_is_not_announced_twice(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    napier = world.sw.pop(1)
    assert mw.run([]) == 0 and len(sent) == 1 and "REMOVED rows" in sent[0][1]
    world.sw.insert(1, napier)                                                               # EGLE puts it back
    assert mw.run([]) == 0 and len(sent) == 2
    assert "Rule 57 screening" not in sent[1][0] and "already recorded; not new" in sent[1][1]      # the union kept the hit on record


def test_a_snapshot_that_moved_with_nothing_to_say_writes_its_row_and_no_email(monkeypatch):
    """The real path: two rows share a site/date/analyte (a field duplicate). Raising the threshold
    stops the first (13) hitting while the second (20) still does — no row changed, no new result, but
    the value on record for the shared id moves. The row records it; nobody is emailed."""
    world, fake, sent = _wire(monkeypatch)
    twin = dict(world.sw[1], CAS1763231_PFOS=13.0)                       # same LabSampleId/site/date, Duplicate flag differs
    world.sw[1] = dict(world.sw[1], CAS1763231_PFOS=20.0, Duplicate=1)
    world.sw.append(dict(twin, Duplicate=0))
    assert mw.run([]) == 0 and json.loads(rows_of(fake, "mpart:sw", "baseline")[0][7])["hits"] == {NAPIER_HIT: "13"}
    loose = copy.deepcopy(CFG)
    loose["mpart"]["thresholds_ng_l"] = {"PFOS": 15.0}
    monkeypatch.setattr(mw, "load_config", lambda: copy.deepcopy(loose))
    assert mw.run([]) == 0
    ch = rows_of(fake, "mpart:sw", "changed")
    assert len(ch) == 1 and "0 added, 0 changed, 0 removed" in ch[0][5] and sent == []
    assert json.loads(ch[0][7])["hits"] == {NAPIER_HIT: "20"}


def test_a_failed_liveness_alert_makes_the_run_red(monkeypatch):
    world, fake, sent = _wire(monkeypatch, send=lambda *a: False)
    assert mw.run([]) == 0
    world.error["fish"] = mc.MpartFetchError("GET failed: OSError")
    assert mw.run([]) == 0 and mw.run([]) == 0
    assert mw.run([]) == 1                                                                   # the 3rd skip's liveness email failed
    assert len(rows_of(fake, "mpart:fish", "fetch-skipped")) == 3


def test_an_oversized_snapshot_is_a_loud_structural_failure_for_that_item_only(monkeypatch, capsys):
    world, fake, sent = _wire(monkeypatch)
    monkeypatch.setattr(mc, "MAX_SNAPSHOT_CHARS", 200)
    assert mw.run([]) == 1
    assert "STRUCTURAL failure" in capsys.readouterr().out and len(rows_of(fake, "mpart:sites", "baseline")) == 1


def test_a_fetch_failure_without_a_baseline_is_loud(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    world.error["sites"] = mc.MpartFetchError("bot wall")
    assert mw.run([]) == 1
    assert len(rows_of(fake, "mpart:sw", "baseline")) == 1 and rows_of(fake, "mpart:sites") == []   # the other items still ran


def test_a_structural_error_is_always_loud_even_with_a_baseline(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.error["sw"] = mc.MpartParseError("schema drift")
    assert mw.run([]) == 1


def test_a_blank_row_key_is_structural_and_loud(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    original = mc.fish_view
    monkeypatch.setattr(mw.mc, "fish_view", lambda a: dict(original(a), key=""))
    assert mw.run([]) == 1 and rows_of(fake, "mpart:fish") == []
    assert len(rows_of(fake, "mpart:sw", "baseline")) == 1                # the other items were unaffected


@pytest.mark.parametrize("stored", ["not json", '{"rows": {"a": "1"}}', '{"v": 999, "rows": {}}'])
def test_an_unreadable_or_old_format_snapshot_rebaselines_silently_and_says_so(monkeypatch, stored):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    fake._values._tabs[sw.TAB_MPART] = [r if r[1] != "mpart:sw" or r[0] == "Date" else r[:7] + [stored]
                                        for r in fake._values._tabs[sw.TAB_MPART]]
    assert mw.run([]) == 0 and sent == []
    sw_rows_ = rows_of(fake, "mpart:sw", "baseline")
    assert len(sw_rows_) == 2 and "unreadable or from another snapshot format" in sw_rows_[1][5]


def test_a_sheet_read_failure_propagates_instead_of_rebaselining(monkeypatch):
    class Flaky(FakeSheets):
        broken = False

        def __init__(self):
            super().__init__()
            outer, inner = self, self._values

            class _V:
                def get(self, spreadsheetId, range):
                    if outer.broken and "MPART Data Watch" in range:
                        raise RuntimeError("sheets 503")
                    return inner.get(spreadsheetId, range)

                def append(self, *a, **k):
                    return inner.append(*a, **k)

                def update(self, *a, **k):
                    return inner.update(*a, **k)
            self._v = _V()

        def values(self):
            return self._v

    fake = Flaky()
    world, fake, sent = _wire(monkeypatch, fake=fake)
    assert mw.run([]) == 0
    world.sw.append(sw_row("L-NEW", "s", "2026-09-01", "w", "d", 30.0, "", 1))
    fake.broken = True
    with pytest.raises(RuntimeError):
        mw.run([])
    assert len(rows_of(fake, "mpart:sw", "changed")) == 0 and len(rows_of(fake, "mpart:sw", "baseline")) == 1


def test_main_and_the_per_item_guard_print_the_exception_class_only(monkeypatch, capsys):
    with monkeypatch.context() as m:
        m.setattr(mw, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x" * 500 + " ##[error]forged")))
        assert mw.main() == 1
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "forged" not in out and "xxx" not in out
    world, fake, sent = _wire(monkeypatch)
    monkeypatch.setattr(mw.mc, "fish_view", lambda a: (_ for _ in ()).throw(ValueError("weird ##[error]attributes")))
    assert mw.run([]) == 1
    out = capsys.readouterr().out
    assert "unexpected ValueError" in out and "##[" not in out and "weird" not in out


# --- probe -----------------------------------------------------------------------------------------


def test_probe_runs_even_when_disabled_and_writes_nothing(monkeypatch, capsys):
    world, fake, sent = _wire(monkeypatch, cfg={"mpart": {"enabled": False}})
    monkeypatch.setattr(mw.dc, "sheets_service", lambda: (_ for _ in ()).throw(AssertionError("touched")))
    assert mw.run(["--probe"]) == 0
    out = capsys.readouterr().out
    assert "mpart:sw: 3 row(s)" in out and "mpart:fish: 3 row(s)" in out and "PROBE OK" in out and sent == []


def test_probe_failure_exits_nonzero(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    world.error["sw"] = mc.MpartFetchError("blocked")
    assert mw.run(["--probe"]) == 1


# --- shipped config + scope pins -----------------------------------------------------------------------


def test_shipped_config_ships_disabled_scoped_and_screens_the_four_rule_57_values():
    from config_loader import load_config
    cfg = load_config()["mpart"]
    assert cfg["enabled"] is False and cfg["recipients"] == ["arbor-hills@trishakunst.com"]
    thresholds, years = mw.load_thresholds(cfg)                       # also validates the keys
    assert mw.load_guards(cfg) == (3, 0.5, 2)                         # ...and the shrink-guard numbers
    assert thresholds == mw.DEFAULT_THRESHOLDS_NG_L and years == mw.DEFAULT_VERIFIED_YEAR
    assert [float(x) for x in cfg["bbox"]] == list(mc.DEFAULT_BBOX) and [int(s) for s in cfg["fish"]["stations"]] == [1484, 1507]
    assert float(cfg["max_shrink_fraction"]) == 0.5 and int(cfg["accept_shrink_after_skips"]) == 2


def test_the_public_water_supply_layer_is_not_rewatched_here():
    """Stream R (`pfas_pws`, live) already watches it; a second watcher would double-alert."""
    for name in ("mpart_client.py", "mpart_watcher.py"):
        code = re.sub(r'""".*?"""', "", (ROOT / name).read_text(), flags=re.S)
        code = re.sub(r"#.*", "", code)
        assert "PublicWaterSupply" not in code and "WSSN" not in code, name
    assert "PublicWaterSupply" not in json.dumps(__import__("config_loader").load_config()["mpart"])


def test_workflow_is_public_data_only_and_never_passes_the_coalition_extras():
    wf = (ROOT / ".github" / "workflows" / "mpart-watch.yml").read_text()
    assert "python mpart_watcher.py" in wf and "GSHEET_ID_PRIVATE" not in wf and "GOAUTH" not in wf
    assert not re.search(r"^\s*ALERT_RECIPIENTS_EXTRA:", wf, re.M)              # recipients are scoped in config, verbatim
    assert "schedule:" in wf and "workflow_dispatch:" in wf


def test_the_client_never_prints_and_its_error_messages_carry_no_query_string():
    """The client returns/raises; only the watcher prints. Its fetch errors name the layer URL
    (public, from config) but never the query (where clause / outFields)."""
    src = (ROOT / "mpart_client.py").read_text()
    assert "print(" not in src
    checked = 0
    for line in src.splitlines():
        if "raise Mpart" in line and 'f"' in line:
            checked += 1
            assert "params" not in line and "where" not in line.split("f\"", 1)[1], line
    assert checked >= 6                                                # the scan really looked at the raise sites
