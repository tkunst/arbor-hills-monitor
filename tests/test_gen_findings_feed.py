"""Hermetic tests for scripts/gen_findings_feed.py's search-index wiring (ADR 062
Phase 2) -- no Sheet/network access. Builds the same `rows` -> build_pages() +
build_search_index() pairing main() performs, and asserts the two artifacts stay
in lockstep: same entry count, and a stable (byte-identical) JSON output across
repeated calls with unchanged input (no embedded timestamp, no dict-ordering
flakiness -- see build_search_index's docstring)."""
import importlib.util
import json
import os

import findings_feed as ff

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")

_SPEC = importlib.util.spec_from_file_location(
    "gen_findings_feed", os.path.join(_SCRIPTS_DIR, "gen_findings_feed.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
gff = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gff)

_CPS_SPEC = importlib.util.spec_from_file_location(
    "check_publish_safety", os.path.join(_SCRIPTS_DIR, "check_publish_safety.py"),
)
assert _CPS_SPEC is not None and _CPS_SPEC.loader is not None
cps = importlib.util.module_from_spec(_CPS_SPEC)
_CPS_SPEC.loader.exec_module(cps)


def _rows(n):
    return ff.parse_feed_rows([
        [f"2026-08-{i + 1:02d}", f"Doc {i}", "evidence", "R5", "notable",
         f"A summary for doc {i}.", "", f"https://x/{i}", "Arbor Hills Remediation Area"]
        for i in range(n)
    ])


def test_search_index_entry_count_matches_html_total():
    rows = _rows(5)
    pages = ff.build_pages(rows, "2026-01-01 00:00 UTC")
    index_json = ff.build_search_index(rows)

    m = gff._COUNT_RE.search(pages["index.html"])
    assert m is not None
    html_total = int(m.group(1).replace(",", ""))

    assert html_total == len(rows)
    assert len(json.loads(index_json)) == len(rows)


def test_search_index_entry_count_matches_html_total_across_pagination():
    # Same invariant with enough rows to force multiple HTML pages -- the
    # index stays one flat array regardless of the HTML's pagination.
    rows = _rows(ff.PAGE_SIZE + 3)
    pages = ff.build_pages(rows, "2026-01-01 00:00 UTC")
    index_json = ff.build_search_index(rows)

    m = gff._COUNT_RE.search(pages["index.html"])
    assert m is not None
    html_total = int(m.group(1).replace(",", ""))

    assert len(pages) > 1
    assert html_total == len(rows)
    assert len(json.loads(index_json)) == len(rows)


def test_search_index_is_deterministic_across_repeated_calls():
    # Same input, run twice -- must be byte-identical (no timestamp, stable
    # key order) so findings-feed.yml's diff-quiet guard stays a no-op on an
    # unchanged day.
    rows = _rows(5)
    first = ff.build_search_index(rows)
    second = ff.build_search_index(rows)
    assert first == second


def test_search_index_filename_matches_the_gate_script():
    # gen_findings_feed.py (the writer) and check_publish_safety.py (the
    # gate) each hardcode this filename independently -- no shared import
    # between the two entry points. If either side ever drifts (a rename, a
    # typo), the gate's `_load_search_index` silently treats the real file
    # as "not found" and skips the scan instead of failing -- pin the two
    # constants equal so that drift fails CI instead of silently degrading
    # the gate.
    assert gff.SEARCH_INDEX_FILENAME == cps.SEARCH_INDEX_FILENAME


def _run_main(monkeypatch, tmp_path, n_rows, previous):
    feed = [[f"2026-08-{i + 1:02d}", f"Doc {i}", "evidence", "R5", "notable",
             "s", "", f"https://x/{i}", "Arbor Hills Landfill"] for i in range(n_rows)]
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(gff, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(gff.drive_client, "sheets_service", lambda: None)
    monkeypatch.setattr(gff, "_archive_links", lambda svc, sid: {})
    monkeypatch.setattr(gff, "_previous_total", lambda out_dir: previous)
    monkeypatch.setattr(gff, "_tab_values", lambda svc, sid, tab, a1="A2:I":
                        feed if tab == gff.sheet_writer.TAB_NEW else [])
    gff.main()


def test_big_shrink_still_refused_without_expected_total(monkeypatch, tmp_path):
    monkeypatch.delenv("FINDINGS_EXPECTED_TOTAL", raising=False)
    import pytest
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, tmp_path, 5, previous=100)
    assert os.listdir(tmp_path) == []


def test_shrink_refused_when_expected_total_does_not_match(monkeypatch, tmp_path):
    monkeypatch.setenv("FINDINGS_EXPECTED_TOTAL", "6")
    import pytest
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, tmp_path, 5, previous=100)


def test_deliberate_shrink_with_exact_expected_total_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("FINDINGS_EXPECTED_TOTAL", "5")
    _run_main(monkeypatch, tmp_path, 5, previous=100)
    assert "index.html" in os.listdir(tmp_path)
