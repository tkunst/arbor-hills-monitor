"""Pure parts of scripts/oneoff_move_neighbor_rows.py (ADR 064). Delete with the script."""
import sheet_writer as sw

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
