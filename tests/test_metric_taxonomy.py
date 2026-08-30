"""Metric taxonomy (ADR 034): the expanded Measurement metric vocabulary, the
single-note classifier reused by the backfill, forward-path no-regression on the
four first-class metrics, and the one-shot `other`-bucket backfill's pure logic.

Hermetic — no API key, no Sheets. The Anthropic client is faked; the backfill's
Sheets service is faked; every classification is injected."""
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError
from typing import get_args

import egle_doc_parser as p
import backfill_metric_taxonomy as b


# ---------------------------------------------------------------------------
# Vocabulary (METRIC_VALUES / MetricLiteral) — the single source of truth
# ---------------------------------------------------------------------------

REQUIRED_SAMPLE = [
    "hydrogen_sulfide", "pfas", "arsenic", "benzene", "major_ions",
    "ammonia_nitrogen", "event_status", "other",
]


def test_vocabulary_has_no_duplicates():
    assert len(p.METRIC_VALUES) == len(set(p.METRIC_VALUES))


def test_four_first_class_metrics_preserved_exactly():
    for m in ("temperature", "carbon_monoxide", "oxygen", "methane"):
        assert m in p.METRIC_VALUES


def test_rulings_additions_present():
    # Trisha's 2026-08-25 rulings: benzene broken out, major_ions added.
    assert "benzene" in p.METRIC_VALUES
    assert "major_ions" in p.METRIC_VALUES


def test_required_representative_sample_present():
    for m in REQUIRED_SAMPLE:
        assert m in p.METRIC_VALUES, m


def test_secondary_metrics_are_distinct_from_first_class():
    # methane/temperature must NOT be merged with their facility/ambient variants,
    # or the existing per-well series get corrupted (handoff Part A.2).
    for pair in (("methane", "methane_secondary"), ("temperature", "temperature_secondary")):
        assert pair[0] in p.METRIC_VALUES and pair[1] in p.METRIC_VALUES
        assert pair[0] != pair[1]


def test_metric_literal_and_tuple_agree():
    # MetricLiteral is the source; METRIC_VALUES is derived from it via get_args.
    assert get_args(p.MetricLiteral) == p.METRIC_VALUES


def test_vocabulary_is_roughly_the_approved_size():
    # ~52 named substances + 4 first-class + `other`. Guard against an accidental
    # trim (ruling 5: do not cut to the old ~30–40 target) or a runaway paste.
    assert 55 <= len(p.METRIC_VALUES) <= 65


# ---------------------------------------------------------------------------
# The structured-output enum accepts every approved metric and rejects unknowns
# ---------------------------------------------------------------------------

class _MetricModel(BaseModel):
    metric: p.MetricLiteral


@pytest.mark.parametrize("name", REQUIRED_SAMPLE + [
    "methane_secondary", "temperature_secondary", "nmoc_voc",
    "sulfur_dioxide", "nitrogen_oxides", "tss", "bod", "ph", "mercury",
])
def test_enum_accepts_named_metric(name):
    assert _MetricModel(metric=name).metric == name


def test_enum_rejects_unknown_metric():
    with pytest.raises(ValidationError):
        _MetricModel(metric="not_a_real_metric")


# ---------------------------------------------------------------------------
# Single-note classifier (classify_note_metric) reused by the backfill
# ---------------------------------------------------------------------------

def _fake_note_client(metric_out, capture=None):
    """Anthropic-shaped fake: messages.parse -> parsed_output.metric == metric_out.
    `capture` (a dict) records the call kwargs for assertions."""
    class Msgs:
        def parse(self, **kw):
            if capture is not None:
                capture.update(kw)
            if metric_out is None:
                return SimpleNamespace(parsed_output=None, stop_reason="max_tokens")
            return SimpleNamespace(
                parsed_output=SimpleNamespace(metric=metric_out), stop_reason="end_turn")
    return SimpleNamespace(messages=Msgs())


def test_classify_note_metric_returns_model_choice():
    m = p.classify_note_metric("Total Arsenic", "ug/L", model="m",
                               client=_fake_note_client("arsenic"))
    assert m == "arsenic"


def test_classify_note_metric_fail_safe_returns_other():
    # A truncated/empty model response must NOT crash the backfill; leave as-is.
    m = p.classify_note_metric("weird note", "x", model="m",
                               client=_fake_note_client(None))
    assert m == "other"


def test_classify_note_metric_passes_note_and_unit_and_uses_vocab_schema():
    cap = {}
    p.classify_note_metric("hydrogen sulfide", "ppm", model="m",
                           client=_fake_note_client("hydrogen_sulfide", cap))
    content = cap["messages"][0]["content"]
    assert "hydrogen sulfide" in content and "ppm" in content
    # The output_format is the constrained vocabulary — it validates a real metric
    # and rejects an unknown, proving the model can only emit approved values.
    of = cap["output_format"]
    assert of(metric="benzene").metric == "benzene"
    with pytest.raises(ValidationError):
        of(metric="bogus")


def test_doc_and_note_classifiers_share_one_guidance_source():
    # No-fork guarantee: the trap guidance is literally the SAME string in both
    # the document prompt and the single-note prompt.
    assert p._METRIC_CLASSIFY_GUIDANCE in p._MEASUREMENTS_HELP
    assert p._METRIC_CLASSIFY_GUIDANCE in p._NOTE_CLASSIFIER_INSTRUCTIONS


# ---------------------------------------------------------------------------
# Forward path: the real Measurement schema accepts new metrics with basis intact
# and never corrupts the four first-class metrics
# ---------------------------------------------------------------------------

def _fake_doc_client(measurements):
    class Msgs:
        def parse(self, *, output_format, **kw):
            inst = output_format(
                summary="s", key_data_point="k", doc_type="evidence",
                risks=[], severity="routine", measurements=measurements, deadlines=[])
            return SimpleNamespace(parsed_output=inst, stop_reason="end_turn")
    return SimpleNamespace(messages=Msgs())


def test_forward_schema_accepts_new_metrics_and_preserves_basis():
    ms = [
        {"metric": "arsenic", "value": 1.0, "unit": "ug/L", "basis": "measured"},
        {"metric": "hydrogen_sulfide", "value": 5.0, "unit": "ppm", "basis": "measured"},
        {"metric": "tss", "value": 30.0, "unit": "mg/L", "basis": "permitted_limit"},
    ]
    out = p._classify_with_claude("txt", {}, [{"id": "R1", "name": "n", "description": "d"}],
                                  "claude-haiku-4-5", client=_fake_doc_client(ms))
    got = out["measurements"]
    assert [m["metric"] for m in got] == ["arsenic", "hydrogen_sulfide", "tss"]
    # basis is load-bearing — must round-trip untouched (CLAUDE.md invariant).
    assert [m["basis"] for m in got] == ["measured", "measured", "permitted_limit"]


def test_forward_path_keeps_per_well_methane_as_methane_not_secondary():
    ms = [
        {"metric": "methane", "value": 55.0, "unit": "percent", "basis": "measured",
         "well_id": "AHW272R4"},
        {"metric": "methane_secondary", "value": 50.0, "unit": "%", "basis": "measured"},
    ]
    out = p._classify_with_claude("txt", {}, [{"id": "R1", "name": "n", "description": "d"}],
                                  "claude-haiku-4-5", client=_fake_doc_client(ms))
    metrics = [m["metric"] for m in out["measurements"]]
    # The two are distinct and preserved — per-well methane is never downgraded.
    assert metrics == ["methane", "methane_secondary"]


def test_forward_schema_rejects_a_bogus_metric():
    ms = [{"metric": "totally_invalid", "value": 1.0, "unit": "x", "basis": "measured"}]
    with pytest.raises(ValidationError):
        p._classify_with_claude("txt", {}, [{"id": "R1", "name": "n", "description": "d"}],
                                "claude-haiku-4-5", client=_fake_doc_client(ms))


# ---------------------------------------------------------------------------
# Backfill pure logic
# ---------------------------------------------------------------------------

HEADER = ["As-Of Date", "Well ID", "Metric", "Value", "Unit", "Basis",
          "Date Filed", "Document Name", "Note", "Link", "Facility"]


def _rows():
    return [
        HEADER,
        ["2025-01-01", "W1", "other", "5", "ppm", "measured", "", "d", "hydrogen sulfide", "", "F"],   # row 2
        ["2025-01-02", "W2", "other", "1", "ug/L", "measured", "", "d", "Total Arsenic", "", "F"],       # row 3
        ["2025-01-03", "W3", "methane", "55", "percent", "measured", "", "d", "CH4", "", "F"],           # row 4
        ["2025-01-04", "W4", "other", "9", "count", "measured", "", "d", "Gas shortfall", "", "F"],       # row 5
        ["2025-01-05", "W5", "other", "2", "ppm", "measured", "", "d", "hydrogen sulfide", "", "F"],       # row 6
    ]


NOTE_MAP = {"hydrogen sulfide": "hydrogen_sulfide", "Total Arsenic": "arsenic",
            "Gas shortfall": "other"}


def test_select_other_rows_uses_1based_sheet_numbers_and_skips_named():
    other = b.select_other_rows(HEADER, _rows())
    assert [r["row"] for r in other] == [2, 3, 5, 6]  # row 4 (methane) excluded
    assert other[0]["note"] == "hydrogen sulfide" and other[0]["unit"] == "ppm"


def test_distinct_notes_dedup():
    other = b.select_other_rows(HEADER, _rows())
    rep = b.distinct_notes(other)
    assert set(rep) == {"hydrogen sulfide", "Total Arsenic", "Gas shortfall"}
    assert rep["hydrogen sulfide"] == "ppm"


def test_build_plan_skips_named_rows_and_noop_notes():
    other = b.select_other_rows(HEADER, _rows())
    plan = b.build_plan(other, NOTE_MAP)
    # rows 2,3,6 move; row 5 (Gas shortfall -> other) is a no-op and skipped
    assert {u["row"] for u in plan} == {2, 3, 6}
    assert all(u["old"] == "other" for u in plan)
    assert {u["new"] for u in plan} == {"hydrogen_sulfide", "arsenic"}


def test_project_after_math():
    rows = _rows()
    other = b.select_other_rows(HEADER, rows)
    plan = b.build_plan(other, NOTE_MAP)
    before = b.metric_distribution(HEADER, rows)
    after = b.project_after(before, plan)
    assert before["other"] == 4
    assert after["other"] == 1                # only Gas shortfall stays
    assert after["hydrogen_sulfide"] == 2     # two H2S rows moved in
    assert after["arsenic"] == 1
    assert after["methane"] == before["methane"]  # untouched


def test_backfill_is_idempotent():
    # After applying, those rows are no longer 'other' -> a second plan is empty.
    rows = _rows()
    other = b.select_other_rows(HEADER, rows)
    plan = b.build_plan(other, NOTE_MAP)
    mi = HEADER.index("Metric")
    for u in plan:
        rows[u["row"] - 1][mi] = u["new"]     # simulate apply (row-1 -> 0-based list)
    other2 = b.select_other_rows(HEADER, rows)
    plan2 = b.build_plan(other2, NOTE_MAP)
    assert plan2 == []                        # nothing left to do (except the residual)
    assert {r["note"] for r in other2} == {"Gas shortfall"}


def test_apply_plan_writes_only_metric_column():
    captured = {"bodies": []}

    class Vals:
        def batchUpdate(self, spreadsheetId, body):
            captured["bodies"].append(body)
            return SimpleNamespace(execute=lambda **kw: {})

    class SS:
        def values(self):
            return Vals()

    svc = SimpleNamespace(spreadsheets=lambda: SS())
    other = b.select_other_rows(HEADER, _rows())
    plan = b.build_plan(other, NOTE_MAP)
    b.apply_plan(svc, "SID", HEADER, plan)
    data = captured["bodies"][0]["data"]
    ranges = [d["range"] for d in data]
    assert ranges == ["'Measurements'!C2", "'Measurements'!C3", "'Measurements'!C6"]
    assert captured["bodies"][0]["valueInputOption"] == "RAW"
    # Pin the WRITTEN VALUE, not just the range — writing note text or "other"
    # into the Metric column would pass a range-only assertion but corrupt the tab.
    assert [d["values"] for d in data] == [
        [["hydrogen_sulfide"]], [["arsenic"]], [["hydrogen_sulfide"]]]


def test_apply_plan_batches():
    calls = {"n": 0}

    class Vals:
        def batchUpdate(self, spreadsheetId, body):
            calls["n"] += 1
            return SimpleNamespace(execute=lambda **kw: {})

    svc = SimpleNamespace(spreadsheets=lambda: SimpleNamespace(values=lambda: Vals()))
    plan = [{"row": i, "old": "other", "new": "arsenic", "note": "Total Arsenic"}
            for i in range(2, 2 + 1200)]
    b.apply_plan(svc, "SID", HEADER, plan, batch=500)
    assert calls["n"] == 3                     # 1200 / 500 -> 3 requests


def test_col_letter():
    assert b._col_letter(0) == "A"
    assert b._col_letter(2) == "C"
    assert b._col_letter(26) == "AA"


def test_top_notes_by_frequency_picks_highest_volume():
    # A bounded (--limit) sample must classify the notes covering the MOST rows,
    # not the first ones seen. "common" appears 3x, "rare" 1x -> limit 1 keeps
    # "common".
    other = [
        {"row": 2, "note": "rare", "unit": "x"},
        {"row": 3, "note": "common", "unit": "ppm"},
        {"row": 4, "note": "common", "unit": "ppm"},
        {"row": 5, "note": "common", "unit": "ppm"},
    ]
    rep = b.distinct_notes(other)
    freq = b.note_frequencies(other)
    assert freq["common"] == 3 and freq["rare"] == 1
    top1 = b.top_notes_by_frequency(rep, freq, 1)
    assert list(top1) == ["common"]
    assert top1["common"] == "ppm"


def test_md_inline_neutralizes_table_breakers():
    out = b._md_inline("a|b`c\nd")
    assert "|" not in out.replace("\\|", "")   # pipe escaped
    assert "`" not in out                        # backtick removed
    assert "\n" not in out                       # newline flattened


def test_apply_backfill_rereads_and_targets_current_rows(tmp_path):
    # TOCTOU guard: apply must write to the row numbers in the tab AS IT IS AT
    # APPLY TIME, not a plan captured at an earlier read. Simulate a concurrent
    # mid-sheet shift: at apply time the H2S `other` row is now at sheet row 4
    # (not 2), so the write must target C4, not a stale C2.
    fresh = [
        HEADER,
        ["", "", "methane", "1", "%", "measured", "", "d", "CH4", "", "F"],        # row 2
        ["", "", "arsenic", "1", "ug/L", "measured", "", "d", "Total Arsenic", "", "F"],  # row 3 (already named)
        ["", "", "other", "5", "ppm", "measured", "", "d", "hydrogen sulfide", "", "F"],  # row 4 (shifted)
    ]
    captured = {"ranges": []}

    class Vals:
        def get(self, spreadsheetId, range):
            return SimpleNamespace(execute=lambda **kw: {"values": fresh})

        def batchUpdate(self, spreadsheetId, body):
            captured["ranges"] += [d["range"] for d in body["data"]]
            return SimpleNamespace(execute=lambda **kw: {})

    svc = SimpleNamespace(spreadsheets=lambda: SimpleNamespace(values=lambda: Vals()))
    applied = b.apply_backfill(
        svc, "SID", {"hydrogen sulfide": "hydrogen_sulfide", "Total Arsenic": "arsenic"},
        manifest_path=str(tmp_path / "m.json"),
        meta={"generated_at": "now", "model": "m"},
    )
    # Only the row that is STILL `other` at apply time is written, at its CURRENT
    # position (C4) — the already-arsenic row 3 is not re-touched.
    assert captured["ranges"] == ["'Measurements'!C4"]
    assert applied == [{"row": 4, "old": "other", "new": "hydrogen_sulfide",
                        "note": "hydrogen sulfide"}]
    # Manifest written (before apply) with the applied plan.
    import json as _json
    with open(tmp_path / "m.json") as fh:
        assert _json.load(fh)["updates"] == applied


# ---------------------------------------------------------------------------
# Backfill classification loop: caching/resume + empty-note short-circuit
# ---------------------------------------------------------------------------

def test_classify_notes_caches_and_resumes(tmp_path):
    cache = tmp_path / "cache.json"
    calls = []

    def clf(note, unit, *, model, client):
        calls.append(note)
        return "arsenic"

    rep = {"Total Arsenic": "ug/L", "Total Arsenic 2": "ug/L"}
    out1 = b.classify_notes(rep, model="m", client=None, cache_path=str(cache), classifier=clf)
    assert out1 == {"Total Arsenic": "arsenic", "Total Arsenic 2": "arsenic"}
    assert len(calls) == 2 and cache.exists()

    # Second run: everything cached -> classifier is never called again.
    calls.clear()
    out2 = b.classify_notes(rep, model="m", client=None, cache_path=str(cache), classifier=clf)
    assert out2 == out1 and calls == []


def test_classify_notes_returns_only_requested_notes_not_full_cache(tmp_path):
    # MEDIUM guard: a pre-existing cache may hold classifications from a wider
    # prior run. classify_notes must return ONLY the notes requested THIS run, so
    # a bounded (--limit) run's --apply plans/writes only its sample — never the
    # whole cache. Here the cache has 3 notes; we request only 1.
    cache = tmp_path / "cache.json"
    import json as _json
    with open(cache, "w") as fh:
        _json.dump({"A": "arsenic", "B": "benzene", "C": "chloride"}, fh)

    def clf(note, unit, *, model, client):
        raise AssertionError("should not classify — all requested notes are cached")

    out = b.classify_notes({"B": "ug/L"}, model="m", client=None,
                           cache_path=str(cache), classifier=clf)
    assert out == {"B": "benzene"}          # scoped to the request, NOT A/B/C
    # And a plan built from it touches only B's rows, never A's or C's.
    other = [{"row": 2, "note": "A", "unit": "x"},
             {"row": 3, "note": "B", "unit": "ug/L"}]
    assert b.build_plan(other, out) == [
        {"row": 3, "old": "other", "new": "benzene", "note": "B"}]


def test_apply_backfill_refuses_out_of_vocab_value(tmp_path):
    # Defense-in-depth: a stale/hand-edited cache producing a non-vocabulary label
    # must fail loud before any Sheet write, never write garbage to the tab.
    fresh = [HEADER, ["", "", "other", "1", "x", "measured", "", "d", "weird", "", "F"]]

    class Vals:
        def get(self, spreadsheetId, range):
            return SimpleNamespace(execute=lambda **kw: {"values": fresh})

        def batchUpdate(self, spreadsheetId, body):
            raise AssertionError("must not write when a value is out-of-vocabulary")

    svc = SimpleNamespace(spreadsheets=lambda: SimpleNamespace(values=lambda: Vals()))
    with pytest.raises(ValueError, match="not in the vocabulary"):
        b.apply_backfill(svc, "SID", {"weird": "not_a_metric"},
                         manifest_path=str(tmp_path / "m.json"),
                         meta={"generated_at": "now", "model": "m"})


def test_classify_notes_short_circuits_empty_note(tmp_path):
    called = []

    def clf(note, unit, *, model, client):
        called.append(note)
        return "arsenic"

    out = b.classify_notes({"": ""}, model="m", client=None,
                           cache_path=str(tmp_path / "c.json"), classifier=clf)
    assert out == {"": "other"}    # empty note -> other, no classifier call
    assert called == []


# ---------------------------------------------------------------------------
# Report rendering (the committed, reversible record)
# ---------------------------------------------------------------------------

def test_report_has_headline_residual_and_full_mapping():
    rows = _rows()
    other = b.select_other_rows(HEADER, rows)
    plan = b.build_plan(other, NOTE_MAP)
    before = b.metric_distribution(HEADER, rows)
    after = b.project_after(before, plan)
    md = b.render_report_md(before, after, plan, NOTE_MAP, other,
                            generated_at="now", model="claude-haiku-4-5", applied=False)
    assert "other` BEFORE | 4" in md
    assert "other` AFTER (projected) | 1" in md
    assert "DRY-RUN" in md
    # residual (unplaceable) note is surfaced for eyeballing
    assert "Gas shortfall" in md
    # full note->metric mapping present (reversible record)
    assert "`hydrogen sulfide` | `hydrogen_sulfide`" in md
    assert "`Total Arsenic` | `arsenic`" in md
    # a full run (every note classified) has no "Not sampled" section
    assert "Not sampled this run" not in md


def test_report_separates_unsampled_from_residual_under_limit():
    # Bounded --limit run: only "hydrogen sulfide" was classified; the other
    # `other` notes were not sampled. They must appear as "Not sampled", NOT as
    # residual (genuinely unplaceable).
    rows = _rows()
    other = b.select_other_rows(HEADER, rows)
    partial = {"hydrogen sulfide": "hydrogen_sulfide"}   # only 1 of 3 notes classified
    plan = b.build_plan(other, partial)
    before = b.metric_distribution(HEADER, rows)
    after = b.project_after(before, plan)
    md = b.render_report_md(before, after, plan, partial, other,
                            generated_at="now", model="m", applied=False)
    assert "Not sampled this run" in md
    # "Total Arsenic" / "Gas shortfall" were not classified -> not residual
    resid = md.split("## Residual")[1].split("## ")[0]
    assert "Total Arsenic" not in resid and "Gas shortfall" not in resid
    # residual is empty here (nothing classified resolved to other)
    assert "none — every classified" in resid
