"""mpart_client.py / mpart_watcher.py (Stream V, ADR 060) — the MPART PFAS open-data
layers watch.

Attribute fixtures are shaped like the REAL ArcGIS responses (live-verified
2026-09-28): the surface-water layer's `CAS…_PFOS / …Flag / …Mdl` column groups with
its messy flags (`'K'`, `' J'`, `'J, Q'`, `' '`), the fish table's `PFOScode` ('I' =
no value published), and the sites layer. Values for the Napier Rd 2021-08-05 sample
(PFOS 16.5 ng/L, no flag — the one real Rule 57 exceedance in the area data) and the
Johnson Drain fish rows are the published ones. No JSON/CSV/PDF is committed (data-guard).
"""
import copy
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

import mpart_client as mc
import mpart_watcher as mw
import sheet_writer as sw
from test_pfas_watcher import FakeSheets


def ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


# ==============================================================================
# Fixtures
# ==============================================================================


def sw_row(sample, site, date, water, desc, pfos=None, pfos_flag=None, pfos_mdl=None, pfoa=None, pfoa_flag=None,
           pfhxs=None, pfna=None, unit="ng/L"):
    return {
        "LabSampleId": sample, "SiteCode": site, "Waterbody": water, "Description": desc,
        "CollectionDate": ms(date), "SampleType": None, "Unit": unit, "Duplicate": 0,
        "CAS1763231_PFOS": pfos, "CAS1763231_PFOSFlag": pfos_flag, "CAS1763231_PFOSMdl": pfos_mdl,
        "CAS335671_PFOA": pfoa, "CAS335671_PFOAFlag": pfoa_flag, "CAS335671_PFOAMdl": 0.5,
        "CAS355464_PFHxS": pfhxs, "CAS355464_PFHxSFlag": None, "CAS355464_PFHxSMdl": 0.5,
        "CAS375951_PFNA": pfna, "CAS375951_PFNAFlag": None, "CAS375951_PFNAMdl": 0.25,
    }


def sw_rows():
    return [
        sw_row("240-169452-6", "19-JD-0105", "2022-07-06", "Johnson Drain", "u/s 6 Mile Rd", 3.1, " ", 0.5, 3.6, "", 1.4, 0.48),
        sw_row("L-NAPIER-1", "19-UUTJD-0010", "2021-08-05", "Unnamed Trib to an Unnamed Trib", "Napier Rd", 16.5, "", 1.03, 17.2, "", 26.3, 1.04),
        sw_row("L-JD-100", "19-JD-0100", "2021-08-05", "Johnson Drain ", "6 Mile Rd", 1.03, "K", 1.03, None, None, None, None),
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
# Client — query construction (pure)
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
    (json.dumps({"error": {"code": 400}}).encode(), 200, mc.MpartFetchError),
    (json.dumps({"fields": []}).encode(), 200, mc.MpartParseError),                         # no features
    (payload([], mc.SW_OUT_FIELDS, exceededTransferLimit=True), 200, mc.MpartParseError),   # truncated
    (payload([], mc.SW_OUT_FIELDS[:-1]), 200, mc.MpartParseError),                          # schema lost a field
])
def test_fetch_guards_split_transient_from_structural(monkeypatch, body, status, exc):
    wire_opener(monkeypatch, body, status)
    with pytest.raises(exc):
        mc.fetch_surface_water()


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


def test_flag_tokens_normalize_padding_and_combinations():
    assert mc.flag_tokens(" J") == ("J",) and mc.flag_tokens("J, Q") == ("J", "Q") and mc.flag_tokens("Q,J") == ("J", "Q")
    assert mc.flag_tokens(" ") == () and mc.flag_tokens(None) == () and mc.flag_tokens("k") == ("K",)


def test_surface_water_view_canonicalizes_real_messy_values():
    v = mc.surface_water_view(sw_rows()[0])
    assert v["key"] == "240-169452-6" and v["date"] == "2022-07-06" and v["site"] == "19-JD-0105"
    assert v["PFOS"] == "3.1" and v["PFOS_flag"] == "" and v["PFOS_mdl"] == "0.5" and v["unit"] == "ng/L"
    assert mc.surface_water_view(sw_rows()[2])["waterbody"] == "Johnson Drain"           # trailing space stripped
    assert mc.surface_water_view(sw_rows()[2])["PFOS_flag"] == "K" and mc.surface_water_view(sw_rows()[2])["PFOA"] == ""
    assert mc.surface_water_view(dict(sw_rows()[0], CAS1763231_PFOSFlag="J, Q"))["PFOS_flag"] == "J,Q"


def test_surface_water_key_falls_back_when_the_lab_id_is_missing():
    v = mc.surface_water_view(dict(sw_rows()[0], LabSampleId=None))
    assert v["key"] == "19-JD-0105|2022-07-06|0"


def test_fish_view_and_the_no_value_code():
    v = mc.fish_view(fish_rows()[0])
    assert v["station"] == "1484" and v["pfos_ppb"] == "" and v["pfos_code"] == "I" and v["key"] == "2021233-S01"
    assert mc.fish_view(fish_rows()[1])["pfos_ppb"] == "5.9"


def test_sites_view_is_keyed_by_name_and_never_carries_staff_contacts():
    raw = dict(site_row(), SiteLead="Jane Staffer", SiteLeadEmail="j@example.gov", SiteLeadPhone="555", OBJECTID=9, GlobalID="{x}")
    v = mc.sites_view(raw)
    assert v["key"] == "advanced disposal services arbor hills landfill, inc.|site"
    assert v["county"] == "Washtenaw County" and v["residential_wells"] == "Yes" and v["facility_date"] == "2020-03-31"
    assert "Staffer" not in json.dumps(v) and "example.gov" not in json.dumps(v)
    assert mc.sites_view(dict(site_row(), County="Oakland County "))["county"] == "Oakland County"


def test_row_hash_is_stable_and_sensitive_to_every_watched_field():
    v = mc.surface_water_view(sw_rows()[1])
    h = mc.row_hash(v, mc.SW_VIEW)
    assert h == mc.row_hash(copy.deepcopy(v), mc.SW_VIEW)
    for field in mc.SW_VIEW[1:]:
        assert mc.row_hash(dict(v, **{field: "changed"}), mc.SW_VIEW) != h, field


def test_index_rows_rejects_blank_or_duplicate_keys():
    views = [mc.fish_view(fish_rows()[0]), mc.fish_view(fish_rows()[0])]
    with pytest.raises(mc.MpartParseError):
        mc.index_rows(views, mc.FISH_VIEW, "fish")
    with pytest.raises(mc.MpartParseError):
        mc.index_rows([dict(mc.fish_view(fish_rows()[0]), key="")], mc.FISH_VIEW, "fish")


def test_snapshot_is_order_independent_and_refuses_to_truncate():
    a = {"b": "1", "a": "2"}
    assert mc.snapshot_json(a) == mc.snapshot_json(dict(reversed(list(a.items()))))
    assert mc.snapshot_hash(mc.snapshot_json(a)) != mc.snapshot_hash(mc.snapshot_json({"a": "2", "b": "3"}))
    with pytest.raises(mc.MpartParseError):
        mc.snapshot_json({f"key-{i:06d}": "0" * 16 for i in range(2000)})


def test_epoch_dates():
    assert mc.epoch_ms_to_date(ms("2021-08-05")) == "2021-08-05" and mc.epoch_ms_to_date(None) == ""
    assert mc.epoch_ms_to_date("garbage") == "garbage"


# ==============================================================================
# Watcher — screening (pure)
# ==============================================================================

TH = {"PFOS": 12.0, "PFOA": 170.0, "PFHxS": 210.0, "PFNA": 30.0}


def test_screen_flags_only_detected_values_above_the_threshold():
    napier = mc.surface_water_view(sw_rows()[1])
    hits = mw.screen_surface_water(napier, TH)
    assert [(h["analyte"], h["value"], h["threshold"], h["estimated"]) for h in hits] == [("PFOS", "16.5", 12.0, False)]
    assert mw.screen_surface_water(mc.surface_water_view(sw_rows()[0]), TH) == []          # 3.1 < 12
    exact = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 12.0, "", 1))
    assert mw.screen_surface_water(exact, TH) == []                                          # "above", not "at"


def test_a_K_flag_is_a_nondetect_and_is_never_compared():
    v = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 20.0, "K", 20.0))     # value = the MDL
    assert mw.is_nondetect(v["PFOS_flag"]) and mw.screen_surface_water(v, TH) == []
    assert not mw.is_nondetect("J") and not mw.is_nondetect("J,Q") and mw.is_nondetect(" k ")


def test_an_estimated_J_value_above_the_threshold_is_flagged_as_estimated():
    v = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", 13.0, "J", 1))
    assert [(h["analyte"], h["estimated"]) for h in mw.screen_surface_water(v, TH)] == [("PFOS", True)]


def test_screening_ignores_blank_and_unparseable_values_and_analytes_without_a_threshold():
    v = mc.surface_water_view(sw_row("x", "s", "2024-01-01", "w", "d", None, None, None))
    assert mw.screen_surface_water(v, TH) == []
    assert mw.screen_surface_water(dict(v, PFOS="n/a"), TH) == []
    assert mw.screen_surface_water(mc.surface_water_view(sw_rows()[1]), {"PFOA": 170.0}) == []


# ==============================================================================
# Watcher — state + diff + copy (pure)
# ==============================================================================


def _row(item, change, h="", note="", snap=""):
    return ["2026-09-28", item, "label", change, h, note, "now", snap]


def test_build_state_takes_the_last_snapshot_row_and_counts_consecutive_skips():
    st = mw.build_state([_row("mpart:sw", "baseline", "h1", snap='{"rows":{}}'), _row("mpart:sw", "fetch-skipped", "h1"),
                         _row("mpart:sw", "fetch-skipped", "h1"), _row("mpart:fish", "baseline", "f1"),
                         _row("mpart:fish", "fetch-skipped"), _row("mpart:fish", "fetch-ok"), _row("mpart:fish", "fetch-skipped")])
    assert st["mpart:sw"]["skips"] == 2 and st["mpart:sw"]["hash"] == "h1"
    assert st["mpart:fish"]["skips"] == 1                                                    # the fetch-ok reset the count
    st2 = mw.build_state([_row("mpart:sw", "baseline", "h1"), _row("mpart:sw", "fetch-skipped"),
                          _row("mpart:sw", "changed", "h2", snap="S")])
    assert st2["mpart:sw"] == {"hash": "h2", "snapshot": "S", "skips": 0}
    assert mw.build_state([_row("mpart:sw", "fetch-skipped")]) == {}                         # never baselined


def test_diff_index_and_load_index():
    d = mw.diff_index({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "9", "d": "4"})
    assert d == {"added": ["d"], "removed": ["c"], "changed": ["b"]}
    assert mw.load_index('{"rows": {"a": "1"}}') == {"a": "1"} and mw.load_index("not json") == {} and mw.load_index("[]") == {}


def test_surface_water_copy_prints_values_verbatim_and_carries_the_screening_caveats():
    views = {"L-NAPIER-1": mc.surface_water_view(sw_rows()[1])}
    screen = {"L-NAPIER-1": mw.screen_surface_water(views["L-NAPIER-1"], TH)}
    diff = {"added": ["L-NAPIER-1"], "removed": [], "changed": []}
    body = mw.format_change_body(mw.ITEM_SW, diff, views, screen)
    assert "PFOS 16.5" in body and "Napier Rd" in body and "ABOVE the Rule 57 non-drinking-water value of 12.0 ng/L" in body
    assert "SCREENING comparison" in body and "cite the value in force at the sample date" in body and "'K' flag" in body
    assert mw.subject_for(mw.ITEM_SW, diff, screen).startswith("[MPART data] EXCEEDANCE")
    assert not mw.subject_for(mw.ITEM_SW, diff, {"L-NAPIER-1": []}).startswith("[MPART data] EXCEEDANCE")


def test_fish_copy_never_calls_an_I_code_a_detection_and_applies_no_threshold():
    views = {"2021233-S01": mc.fish_view(fish_rows()[0]), "2021268-S01": mc.fish_view(fish_rows()[1])}
    body = mw.format_change_body(mw.ITEM_FISH, {"added": sorted(views), "removed": [], "changed": []}, views, {})
    assert "no PFOS value published (code I)" in body and "PFOS 5.9 ppb" in body and "No threshold is applied" in body
    assert "EXCEEDANCE" not in mw.subject_for(mw.ITEM_FISH, {"added": ["a"], "removed": [], "changed": []}, {})


def test_bodies_cap_long_lists_and_name_removed_keys():
    views = {f"k{i:03d}": mc.fish_view(fish_row(f"2021{i:03d}-S01", 1507, "Sucker", 6.0, None)) for i in range(40)}
    body = mw.format_change_body(mw.ITEM_FISH, {"added": sorted(views), "removed": ["gone-1"], "changed": []}, views, {})
    assert "NEW rows (40)" in body and "+ 15 more" in body and "REMOVED rows (1" in body and "- gone-1" in body


# ==============================================================================
# Watcher — run() flows
# ==============================================================================

CFG = {"mpart": {"enabled": True, "recipients": ["trisha@example.org"], "stale_alert_after_skips": 3}}


class World:
    def __init__(self):
        self.sw, self.fish, self.sites = sw_rows(), fish_rows(), site_rows()
        self.error = {}          # item-name -> exception to raise


def _wire(monkeypatch, world=None, cfg=CFG, fake=None):
    world = world or World()
    fake = fake or FakeSheets()
    sent = []
    monkeypatch.setenv("GSHEET_ID", "PUBSHEET")
    monkeypatch.setattr(mw, "load_config", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(mw.dc, "sheets_service", lambda: fake)
    monkeypatch.setattr(mw.ea, "send_email", lambda subj, body, c, recipients=None: sent.append((subj, body, recipients)) or True)

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


def test_first_run_baselines_all_three_items_silently(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    rows = rows_of(fake)
    assert sorted(r[1] for r in rows) == ["mpart:fish", "mpart:sites", "mpart:sw"] and all(r[3] == "baseline" for r in rows)
    assert sent == []
    assert json.loads(rows_of(fake, "mpart:sw")[0][7])["rows"].keys() == {r["LabSampleId"] for r in sw_rows()}


def test_second_run_unchanged_writes_nothing(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    assert mw.run([]) == 0 and len(rows_of(fake)) == 3 and sent == []


def test_a_new_exceeding_sample_alerts_with_the_screening_and_a_durable_row_first(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    world.sw = world.sw[:1] + world.sw[2:]                    # baseline WITHOUT the Napier sample
    assert mw.run([]) == 0
    world.sw = sw_rows()                                      # ...then it appears
    assert mw.run([]) == 0
    ch = rows_of(fake, "mpart:sw", "changed")
    assert len(ch) == 1 and "1 added, 0 changed, 0 removed" in ch[0][5] and "L-NAPIER-1" in ch[0][5]
    assert "EXCEEDANCE screen: 1 row(s)" in ch[0][5]
    assert len(sent) == 1
    subj, body, recipients = sent[0]
    assert subj.startswith("[MPART data] EXCEEDANCE") and "PFOS 16.5" in body and recipients == ["trisha@example.org"]
    assert mw.run([]) == 0 and len(sent) == 1                 # not re-alerted


def test_a_new_nondetect_or_low_sample_alerts_as_a_new_sample_without_an_exceedance(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.sw.append(sw_row("L-NEW", "19-JD-0105", "2026-09-01", "Johnson Drain", "u/s 6 Mile Rd", 20.0, "K", 20.0))
    assert mw.run([]) == 0
    assert len(sent) == 1 and "EXCEEDANCE" not in sent[0][0] and "new sample" in sent[0][0]


def test_a_revised_value_is_a_changed_row_and_a_dropped_row_is_removed(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.sw[0]["CAS1763231_PFOS"] = 40.0                     # lab revised a published value upward
    world.sw = world.sw[:2]                                    # and the K row vanished
    assert mw.run([]) == 0
    ch = rows_of(fake, "mpart:sw", "changed")
    assert len(ch) == 1 and "1 changed, 1 removed" in ch[0][5]
    subj, body, _ = sent[0]
    assert "EXCEEDANCE" in subj and "CHANGED rows" in body and "REMOVED rows" in body and "L-JD-100" in body


def test_new_fish_and_new_sites_alert_and_staff_contacts_never_appear(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.fish.append(fish_row("2026100-S01", 1507, "White Sucker", 7.7, None, "2026-08-01"))
    world.sites.append(dict(site_row("New PFAS Site", wells="TBD"), SiteLead="Jane Staffer", SiteLeadEmail="j@example.gov"))
    assert mw.run([]) == 0
    assert len(sent) == 2
    blob = " ".join(s[1] for s in sent) + json.dumps(fake._values._tabs[sw.TAB_MPART])
    assert "PFOS 7.7 ppb" in blob and "New PFAS Site" in blob
    assert "Staffer" not in blob and "example.gov" not in blob


def test_empty_or_blank_recipients_are_display_only_never_the_coalition_list(monkeypatch):
    for recips in ([], [""], [None], ["  "]):
        cfg = copy.deepcopy(CFG)
        cfg["mpart"]["recipients"] = recips
        world, fake, sent = _wire(monkeypatch, cfg=cfg)
        assert mw.run([]) == 0
        world.sw.append(sw_row("L-NEW", "s", "2026-09-01", "w", "d", 30.0, "", 1))
        assert mw.run([]) == 0
        assert len(rows_of(fake, "mpart:sw", "changed")) == 1 and sent == [], recips


def test_a_send_failure_still_leaves_the_row(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.sw.append(sw_row("L-NEW", "s", "2026-09-01", "w", "d", 30.0, "", 1))
    monkeypatch.setattr(mw.ea, "send_email", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp")))
    assert mw.run([]) == 0 and len(rows_of(fake, "mpart:sw", "changed")) == 1


# --- failure modes -----------------------------------------------------------------------------


def test_a_fetch_failure_after_baseline_is_recorded_and_alerts_once_at_the_threshold_then_recovers(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    assert mw.run([]) == 0
    world.error["fish"] = mc.MpartFetchError("GET failed: OSError")
    for i in (1, 2):
        assert mw.run([]) == 0 and sent == [], i
    assert mw.run([]) == 0
    assert len(sent) == 1 and "mpart:fish unreadable for 3 runs" in sent[0][0] and "going UNSEEN" in sent[0][1]
    assert len(rows_of(fake, "mpart:fish", "fetch-skipped")) == 3
    assert mw.run([]) == 0 and len(sent) == 1                  # once per outage
    world.error.clear()
    assert mw.run([]) == 0 and len(rows_of(fake, "mpart:fish", "fetch-ok")) == 1
    world.error["fish"] = mc.MpartFetchError("blip")
    assert mw.run([]) == 0 and len(sent) == 1                  # the counter was reset: 1 skip, no new alert
    assert rows_of(fake, "mpart:fish", "changed") == []        # skips never invent changes


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


def test_a_duplicate_row_key_is_structural_not_silently_merged(monkeypatch):
    world, fake, sent = _wire(monkeypatch)
    world.fish = fish_rows() + [fish_rows()[0]]
    assert mw.run([]) == 1 and rows_of(fake, "mpart:fish") == []


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


def test_main_reports_only_the_class_and_a_short_message(monkeypatch, capsys):
    monkeypatch.setattr(mw, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x" * 500)))
    assert mw.main() == 1
    assert "RuntimeError" in capsys.readouterr().out


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
    assert {k: float(v) for k, v in cfg["thresholds_ng_l"].items()} == mw.DEFAULT_THRESHOLDS_NG_L
    assert [float(x) for x in cfg["bbox"]] == list(mc.DEFAULT_BBOX) and [int(s) for s in cfg["fish"]["stations"]] == [1484, 1507]


def test_the_public_water_supply_layer_is_not_rewatched_here():
    """Stream R (`pfas_pws`, live) already watches it; a second watcher would double-alert."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("mpart_client.py", "mpart_watcher.py"):
        code = re.sub(r'""".*?"""', "", (root / name).read_text(), flags=re.S)
        code = re.sub(r"#.*", "", code)
        assert "PublicWaterSupply" not in code and "WSSN" not in code, name
    assert "PublicWaterSupply" not in json.dumps(__import__("config_loader").load_config()["mpart"])


def test_workflow_is_scheduled_gated_and_public_data_only():
    from pathlib import Path
    wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "mpart-watch.yml").read_text()
    assert "python mpart_watcher.py" in wf and "GSHEET_ID_PRIVATE" not in wf and "GOAUTH" not in wf
    import re as _re
    assert not _re.search(r"^\s*ALERT_RECIPIENTS_EXTRA:", wf, _re.M)         # recipients are scoped in config, verbatim
