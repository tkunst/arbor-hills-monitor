"""review_flags: the 'needs human review' log for parser output the monitor
deliberately did not use (>250F temperatures, rejected WOI tables, unreadable
pages). Hermetic."""
from types import SimpleNamespace
from unittest import mock

import pytest

import review_flags as rf
import woi_table_parser as w

CFG = {"temperature_thresholds": {"max_plausible_wellhead_f": 250},
       "urgent": {"severity_is_urgent": True, "measured_temp_urgent_f": 145}}


def _parsed(measurements=None, kdp="", summary="", full_text=""):
    return SimpleNamespace(measurements=measurements or [], key_data_point=kdp,
                           summary=summary, full_text=full_text, severity="notable")


def test_text_fallback_number_above_ceiling_is_flagged_not_urgent():
    import email_alerts as ea
    p = _parsed(full_text="Well AHW259R4 read 553 F at the wellhead on 7/1.")
    assert ea.is_urgent(p, CFG) is False          # still never URGENT on its own
    flags = rf.parsed_flags(p, CFG)
    assert [(f["kind"], f["value_f"]) for f in flags] == [("text_temp_above_ceiling", 553)]


def test_flare_context_is_not_flagged():
    p = _parsed(full_text="The enclosed flare operated at 1550 F all quarter.")
    assert rf.parsed_flags(p, CFG) == []


def test_text_scan_skipped_when_a_structured_temperature_exists():
    p = _parsed(measurements=[{"metric": "temperature", "value": 120, "basis": "measured"}],
                full_text="Well read 553 F.")
    assert rf.parsed_flags(p, CFG) == []


def test_kdp_retagged_measurement_is_flagged():
    m = {"metric": "temperature", "value": 553, "basis": "unknown", "well_id": "AHW259R4",
         "as_of_date": "2025-07-01",
         "note": "[implausible wellhead temperature, re-tagged by kdp_check] H2S"}
    flags = rf.parsed_flags(_parsed(measurements=[m]), CFG)
    assert [(f["kind"], f["value_f"], f["well_id"]) for f in flags] == [
        ("measurement_temp_above_ceiling", 553, "AHW259R4")]


def test_woi_flags_caps_per_reading_rows():
    rs = [w.WOIReading(f"AHW{i}", f"AHW{i}", "8/3/2023 11:18", False, 19 + i // 10,
                       temp=500.0 + i) for i in range(30)]
    t = w.RejectedTable(5, 9, 40, "column layout not recognized")
    flags = rf.woi_flags(rs, [t], [71, 72, 73], 250)
    kinds = [f["kind"] for f in flags]
    assert kinds.count("woi_temp_above_ceiling") == rf.MAX_WOI_TEMP_FLAGS_PER_DOC + 1
    assert "5 more" in flags[rf.MAX_WOI_TEMP_FLAGS_PER_DOC]["detail"]
    assert flags[-2]["pages"] == "5-9" and flags[-1]["pages"] == "71-73"


def test_rows_shape():
    f = rf.flag(rf.KIND_TEXT_TEMP, value_f=553.0, detail="d")
    row = rf.rows([f], {"doc_id": "123", "document_name": "Doc"}, "https://x", "2026-10-15T10:00:00Z")[0]
    assert row == ["2026-10-15T10:00:00Z", "123", "Doc", "text_temp_above_ceiling", "553",
                   "", "", "", "d", "https://x"]
    assert len(row) == len(rf.REVIEW_FLAG_HEADERS)


def test_log_for_document_is_best_effort(capsys, monkeypatch):
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PRIV")
    monkeypatch.setenv("GSHEET_ID", "PUB")
    svc = mock.MagicMock()
    with mock.patch.object(rf.sw, "ensure_tab", side_effect=RuntimeError("boom https://secret")):
        n = rf.log_for_document(svc, [rf.flag(rf.KIND_TEXT_TEMP)], {}, "", "t")
    assert n == 0
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "secret" not in out


def test_log_for_document_writes_private_sheet_only(monkeypatch):
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PRIV")
    monkeypatch.setenv("GSHEET_ID", "PUB")
    calls = []
    with mock.patch.object(rf.sw, "ensure_tab", lambda *a: calls.append(("ensure", a[1]))), \
            mock.patch.object(rf.sw, "append_rows", lambda *a: calls.append(("append", a[1]))):
        assert rf.log_for_document(object(), [rf.flag(rf.KIND_TEXT_TEMP)], {}, "", "t") == 1
    assert {sid for _, sid in calls} == {"PRIV"}


@pytest.mark.parametrize("priv", ["", "PUB"])
def test_log_for_document_never_falls_back_to_public_sheet(monkeypatch, capsys, priv):
    monkeypatch.setenv("GSHEET_ID_PRIVATE", priv)
    monkeypatch.setenv("GSHEET_ID", "PUB")
    with mock.patch.object(rf.sw, "append_rows", side_effect=AssertionError("wrote")):
        assert rf.log_for_document(object(), [rf.flag(rf.KIND_TEXT_TEMP)], {}, "", "t") == 0
    assert "no private Sheet" in capsys.readouterr().out


def test_log_for_document_survives_a_bad_parsed_object(monkeypatch, capsys):
    monkeypatch.setenv("GSHEET_ID_PRIVATE", "PRIV")
    bad = SimpleNamespace(measurements=["not-a-dict"], key_data_point="", summary="", full_text="")
    assert rf.log_for_document(object(), [], {}, "", "t", parsed=bad, cfg=CFG) == 0
    assert "AttributeError" in capsys.readouterr().out


def test_log_for_document_no_flags_touches_nothing():
    svc = mock.MagicMock()
    assert rf.log_for_document(svc, [], {}, "", "t") == 0
    svc.assert_not_called()


def test_thousands_separator_is_read_whole():
    assert rf.implausible_text_temps("Well AHW1 reached 1,500 F.", 250) == [1500]
    assert rf.implausible_text_temps("Well AHW1 read 120 F.", 250) == []


def test_flags_since_filters_and_is_strict():
    rows = [["2026-10-01T00:00:00Z", "1", "A", "text_temp_above_ceiling", "553"],
            ["2026-10-15T00:00:00Z", "2", "B", "rejected_gas_table", "", "", "", "5-9", "x", "l"]]
    with mock.patch.object(rf.sw, "_tab_rows_strict", return_value=rows) as rd:
        got = rf.flags_since(object(), "sid", "2026-10-10")
        assert [g["Doc ID"] for g in got] == ["2"]
        assert rd.call_args.args[2] == rf.TAB_REVIEW_FLAGS
        assert rf.flags_since(object(), "sid", "2026-09-01", kinds=rf.TEMP_KINDS)[0]["Doc ID"] == "1"
    with mock.patch.object(rf.sw, "_tab_rows_strict", side_effect=OSError("transport")):
        with pytest.raises(OSError):
            rf.flags_since(object(), "sid", "2026-10-10")
