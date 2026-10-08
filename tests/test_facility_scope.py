"""Facility scope (ADR 064): a `scope: related` facility's documents land ONLY in
the Related Documents tab — never New/Historical Documents, Evidence by Risk,
Measurements, Compliance Deadlines, the Sunday digest or an urgent alert — and
the eight neighbor-development sites are not collected at all.
"""
import pytest

import backfill as bf
import nsite_client as nc
import sheet_writer as sw
import watcher as w
from config_loader import load_config
from egle_doc_parser import ParsedDoc

CORE = {"RA", "WRD", "N1504", "P1488", "N2688", "AHLI", "NAPR", "PTI87", "BORE15", "MITIG"}
RELATED = {"JCDR", "CHUBB18", "CHUBBRD", "WIP25", "BDPH", "WIP19", "FP89", "ASB26", "CMP23", "H95"}
DROPPED = {"COLDR", "TOLL", "RIDGE", "PTEX", "LUMB", "DTE5M", "FMCOM", "NTWP"}


# --- config pin -------------------------------------------------------------

def test_config_facility_scopes_are_pinned():
    # Adding a facility means classifying it on purpose: update this test.
    facs = load_config()["facilities"]
    scope = {f["srn"]: f.get("scope", "core") for f in facs}
    assert {s for s, v in scope.items() if v == "core"} == CORE
    assert {s for s, v in scope.items() if v == "related"} == RELATED
    assert set(scope.values()) <= set(nc.FACILITY_SCOPES)


def test_dropped_sites_left_documents_but_keep_profile_watches():
    cfg = load_config()
    assert not DROPPED & {f["srn"] for f in cfg["facilities"]}
    # Still in the shared registry, so the Trisha-only profile watches go on.
    assert DROPPED <= {s["srn"] for s in cfg["nsite_sites"]}


# --- nsite_client tagging ---------------------------------------------------

def _cfg(*facs):
    return {"facilities": list(facs)}


def test_fetch_all_documents_tags_scope(monkeypatch):
    monkeypatch.setattr(nc, "fetch_site_documents",
                        lambda session, fid: [{"doc_id": f"d{fid}"}])
    docs = nc.fetch_all_documents(None, _cfg(
        {"srn": "A", "name": "Core", "id": "1"},
        {"srn": "B", "name": "Near", "id": "2", "scope": "related"},
    ))
    assert [d["facility_scope"] for d in docs] == ["core", "related"]


def test_fetch_all_documents_refuses_unknown_scope(monkeypatch):
    fetched = []
    monkeypatch.setattr(nc, "fetch_site_documents",
                        lambda session, fid: fetched.append(fid) or [])
    with pytest.raises(ValueError, match="relatd"):
        nc.fetch_all_documents(None, _cfg(
            {"srn": "A", "name": "Core", "id": "1"},
            {"srn": "B", "name": "Near", "id": "2", "scope": "relatd"},
        ))
    assert fetched == []  # validated before any fetch


# --- sheet_writer routing ---------------------------------------------------

def _parsed(severity="routine"):
    return ParsedDoc(
        summary="s", key_data_point="k", doc_type="evidence", risks=["R5"],
        severity=severity, full_text="", ocr_applied=False, page_count=1,
        measurements=[{"metric": "temperature", "value": 150, "unit": "F",
                       "basis": "measured"}],
    )


def test_feed_tab_for():
    assert sw.feed_tab_for({"facility_scope": "related"}, sw.TAB_NEW) == sw.TAB_RELATED
    assert sw.feed_tab_for({"facility_scope": "core"}, sw.TAB_NEW) == sw.TAB_NEW
    assert sw.feed_tab_for({}, sw.TAB_HISTORICAL) == sw.TAB_HISTORICAL


def test_related_tab_is_created_and_purgeable():
    assert sw._TAB_HEADERS[sw.TAB_RELATED] == sw.FEED_HEADERS
    assert sw.TAB_RELATED in {t for t, _ in sw._PURGE_TABS}


def test_write_related_document_touches_only_related_tab(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "append_rows",
                        lambda svc, sid, tab, rows: calls.append((tab, list(rows))))
    sw.write_related_document(None, "SID", _parsed(), {"facility_name": "Near"}, "L")
    assert [t for t, _ in calls] == [sw.TAB_RELATED]


def test_stub_row_for_related_doc_goes_to_related_tab(monkeypatch):
    calls = []
    monkeypatch.setattr(sw, "append_rows",
                        lambda svc, sid, tab, rows: calls.append(tab))
    sw.write_stub_row(None, "SID", {"facility_scope": "related"}, "L", "bad",
                      feed_tab=sw.TAB_NEW)
    assert calls == [sw.TAB_RELATED]


# --- watcher / backfill end to end -----------------------------------------

DOCS = [
    {"doc_id": "core1", "document_name": "Core filing", "date_filed": "2026-10-07",
     "facility_srn": "N2688", "facility_name": "Arbor Hills Landfill",
     "facility_scope": "core"},
    {"doc_id": "rel1", "document_name": "Neighbor filing", "date_filed": "2026-10-07",
     "facility_srn": "JCDR", "facility_name": "Johnson Creek", "facility_scope": "related"},
]


def _wire(monkeypatch, mod, severity):
    """Fake every external call `mod.run()` makes; return the recorded writes."""
    rec = {"append": [], "urgent": [], "deadlines": [], "woi": [], "processed": []}
    state = {"processed": {}, "skipped": {}, "errors": {}, "pending_digest": [],
             "pending_urgent_recap": []}
    cfg = {"anthropic_model": "m", "large_doc_page_threshold": 1,
           "large_doc_max_keyword_pages": 1, "classification_max_tokens": 1,
           "backfill_batch_size": 50, "watcher": {"max_new_docs_per_run": 25}}
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(mod, "load_config", lambda: cfg)
    monkeypatch.setattr(mod.dc, "sheets_service", lambda: object())
    monkeypatch.setattr(mod.sw, "ensure_tabs", lambda *a: None)
    monkeypatch.setattr(mod.sw, "read_state", lambda *a: state)
    monkeypatch.setattr(mod.sw, "write_meta", lambda *a: None)
    monkeypatch.setattr(mod.sw, "rebuild_risk_register_tab", lambda *a: None)
    monkeypatch.setattr(mod.sw, "mark_processed",
                        lambda svc, sid, did, payload, ts: rec["processed"].append(did))
    monkeypatch.setattr(mod.sw, "append_rows",
                        lambda svc, sid, tab, rows: rec["append"].append((tab, list(rows))))
    monkeypatch.setattr(mod.sw, "write_compliance_deadlines",
                        lambda *a, **k: rec["deadlines"].append(a))
    monkeypatch.setattr(mod.nc, "make_session", lambda: None)
    monkeypatch.setattr(mod.nc, "fetch_all_documents", lambda s, c: [dict(d) for d in DOCS])
    monkeypatch.setattr(mod.nc, "download_pdf", lambda *a: None)
    monkeypatch.setattr(mod, "parse_document", lambda *a, **k: _parsed(severity))
    monkeypatch.setattr(mod.av, "mirror_one_now", lambda *a, **k: "LINK-" + a[3]["doc_id"])
    monkeypatch.setattr(mod.woi_router, "route_measurements",
                        lambda parsed, local, d, cfg: rec["woi"].append(d["doc_id"]))
    if mod is w:
        monkeypatch.setattr(w, "_today", lambda: __import__("datetime").date(2026, 10, 7))
        monkeypatch.setattr(w.ea, "is_urgent", lambda parsed, cfg: parsed.severity == "urgent")
        monkeypatch.setattr(w.ea, "send_urgent_alert",
                            lambda parsed, d, link, cfg: rec["urgent"].append(d["doc_id"]) or True)
    return rec, state


def _tabs_for(rec, doc_id):
    return {tab for tab, rows in rec["append"] for r in rows if f"LINK-{doc_id}" in r}


@pytest.mark.parametrize("severity", ["routine", "urgent"])
def test_watcher_routes_related_doc_to_related_tab_only(monkeypatch, severity):
    rec, state = _wire(monkeypatch, w, severity)
    assert w.run() == 0
    assert _tabs_for(rec, "rel1") == {sw.TAB_RELATED}
    assert sw.TAB_NEW in _tabs_for(rec, "core1")
    assert sw.TAB_MEASUREMENTS in _tabs_for(rec, "core1")
    assert rec["woi"] == ["core1"]
    assert "rel1" not in rec["urgent"]
    links = [r["link"] for r in state["pending_digest"] + state["pending_urgent_recap"]]
    assert "LINK-rel1" not in links and "LINK-core1" in links
    assert sorted(rec["processed"]) == ["core1", "rel1"]


def test_backfill_routes_related_doc_to_related_tab_only(monkeypatch):
    rec, _ = _wire(monkeypatch, bf, "routine")
    for env in ("RETRY_POISONED", "RETRY_DOC_IDS", "FORCE_REPROCESS_DOC_IDS",
                "FORCE_REPROCESS_APPLY"):
        monkeypatch.delenv(env, raising=False)
    assert bf.run() == 0
    assert _tabs_for(rec, "rel1") == {sw.TAB_RELATED}
    assert sw.TAB_HISTORICAL in _tabs_for(rec, "core1")
    assert rec["woi"] == ["core1"]
    assert sorted(rec["processed"]) == ["core1", "rel1"]


# --- one-off migration (pure parts) ------------------------------------------

def test_oneoff_plan_moves_related_and_drops_dropped():
    import importlib.util, pathlib
    path = pathlib.Path(__file__).parent.parent / "scripts" / "oneoff_move_neighbor_rows.py"
    spec = importlib.util.spec_from_file_location("oneoff_move", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    def feed(name, link):
        return ["2026-10-07", "doc", "t", "", "routine", "s", "k", link, name]
    vals = {
        sw.TAB_NEW: [feed("Core", "L1"), feed("Near", "L2"), feed("Gone", "L3")],
        sw.TAB_HISTORICAL: [feed("Near", "L4")],
        sw.TAB_MEASUREMENTS: [["", "", "", "", "", "", "", "", "", "L2", "Near"],
                              ["", "", "", "", "", "", "", "", "", "L1", "Core"]],
    }
    p = m.plan(vals, frozenset({"Near"}), frozenset({"Gone"}))
    assert p["delete"][sw.TAB_NEW] == [1, 2]
    assert p["delete"][sw.TAB_HISTORICAL] == [0]
    assert p["delete"][sw.TAB_MEASUREMENTS] == [0]
    assert [r[7] for r in p["copy"]] == ["L2", "L4"]   # dropped rows are not copied
    assert p["links"] == {"L2", "L3", "L4"}
    kept, removed = m.prune_pending([{"link": "L1"}, {"link": "L3"}], p["links"])
    assert kept == [{"link": "L1"}] and removed == [{"link": "L3"}]
