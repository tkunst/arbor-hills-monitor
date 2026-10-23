"""coder:dataset-provenance-sheet: the Dataset Provenance tab, its pointer and
annotation columns, and the seed script. Hermetic: an in-memory grid stands in
for the Sheets API and records the valueInputOption of every write."""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))

import dataset_provenance as dp  # noqa: E402
import findings_feed  # noqa: E402
import sheet_writer as sw  # noqa: E402
import sync_dataset_provenance as seed  # noqa: E402

GID = {sw.TAB_NEW: 11, sw.TAB_HISTORICAL: 12, sw.TAB_ARCHIVE: 13, sw.TAB_HANDCURATED: 14}
NSITE = "https://mienviro.michigan.gov/ncore/downloadpdf/"
D1, D2, D3 = "7894782240984169987", "-2136493721019571731", "8700874927542414020"
HC_FULL = "1RevPABsVmv0D8hL90WIkludSUVB_ES__"


def _col(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _parse(a1: str):
    m = re.match(r"^'(.+)'!([A-Z]+)(\d+)?(?::([A-Z]+)(\d+)?)?$", a1)
    assert m, a1
    tab, c1, r1, c2, r2 = m.groups()
    return tab, _col(c1), int(r1 or 1) - 1, _col(c2 or c1), (int(r2) - 1 if r2 else None)


class FakeSheets:
    """Grid per tab (row 0 = header). Records every write as (op, range, mode)."""

    def __init__(self, tabs):
        self.tabs = {k: [list(r) for r in v] for k, v in tabs.items()}
        self.gids = dict(GID)
        self.writes = []

    # spreadsheets()
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None, valueRenderOption=None):
        if range is None:
            return _Exec({"sheets": [{"properties": {"title": t, "sheetId": self.gids.setdefault(t, 90 + i)}}
                                     for i, t in enumerate(self.tabs)]})
        tab, c1, r1, c2, r2 = _parse(range)
        if tab not in self.tabs:
            raise RuntimeError(f"Unable to parse range: {range}")
        grid = self.tabs[tab]
        rows = grid[r1:(r2 + 1 if r2 is not None else None)]
        # Like the real API: a formula reads back as its displayed value unless
        # valueRenderOption=FORMULA is asked for.
        shown = (lambda v: v) if valueRenderOption == "FORMULA" else (
            lambda v: "(displayed value)" if str(v).startswith("=") else v)
        out = [[shown(r[c] if c < len(r) else "") for c in range_(c1, c2)] for r in rows]
        out = [_rstrip(r) for r in out]
        while out and not out[-1]:
            out.pop()
        return _Exec({"values": out} if out else {})

    def batchUpdate(self, spreadsheetId=None, body=None):
        if "requests" in body:
            for req in body["requests"]:
                self.tabs[req["addSheet"]["properties"]["title"]] = []
            return _Exec({})
        for d in body["data"]:
            self._put(d["range"], d["values"])
            self.writes.append(("update", d["range"], body["valueInputOption"]))
        return _Exec({})

    def update(self, spreadsheetId=None, range=None, valueInputOption=None, body=None):
        self._put(range, body["values"])
        self.writes.append(("update", range, valueInputOption))
        return _Exec({})

    def append(self, spreadsheetId=None, range=None, valueInputOption=None,
               insertDataOption=None, body=None):
        tab = _parse(range)[0]
        self.tabs[tab].extend(list(r) for r in body["values"])
        self.writes.append(("append", range, valueInputOption))
        return _Exec({})

    def _put(self, a1, values):
        tab, c1, r1, _, _ = _parse(a1)
        grid = self.tabs[tab]
        for i, row in enumerate(values):
            while len(grid) <= r1 + i:
                grid.append([])
            g = grid[r1 + i]
            for j, v in enumerate(row):
                while len(g) <= c1 + j:
                    g.append("")
                g[c1 + j] = v


def range_(a, b):
    return list(range(a, b + 1))


def _rstrip(r):
    r = list(r)
    while r and r[-1] == "":
        r.pop()
    return r


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self, num_retries=0):
        return self.v


def feed_row(name, link):
    r = [""] * len(sw.FEED_HEADERS)
    r[1], r[7] = name, link
    return r


def archive_row(doc_id, name, link):
    r = [""] * len(sw.ARCHIVE_HEADERS)
    r[0], r[1], r[5] = doc_id, name, link
    return r


def hc_row(name, title, origin, drive):
    r = [""] * dp.HC_WIDTH
    r[0], r[1], r[7], r[9] = name, title, origin, drive
    return r


def base_sheet(**extra):
    tabs = {
        sw.TAB_NEW: [sw.FEED_HEADERS, feed_row("Unrelated letter", NSITE + "555")],
        sw.TAB_HISTORICAL: [sw.FEED_HEADERS,
                            feed_row("WOI 2024 H2 (mirrored)", "https://drive.google.com/file/d/MIRROR1/view"),
                            feed_row("WOI Q2 2023", NSITE + D2),
                            feed_row("Not a dataset doc", NSITE + "777")],
        sw.TAB_ARCHIVE: [sw.ARCHIVE_HEADERS,
                         archive_row(D1, "Site", "https://drive.google.com/file/d/MIRROR1/view"),
                         archive_row(D2, "Site", "https://drive.google.com/file/d/MIRROR2/view"),
                         archive_row(D3, "Site", "https://drive.google.com/file/d/MIRROR3/view")],
        sw.TAB_HANDCURATED: [findings_feed.HANDCURATED_FIELDS,
                             hc_row("2025-01-06-h2.pdf", "WOI 2024 H2 copy", NSITE + D1,
                                    "https://drive.google.com/file/d/11nKy9nYDZaxcuE5x7bleJeyowVS9cXMA/view"),
                             hc_row("jan-feb-2022.pdf", "WOI Jan-Feb 2022", "",
                                    f"https://drive.google.com/file/d/{HC_FULL}/view")],
    }
    tabs.update(extra)
    return FakeSheets(tabs)


# --- sheet_writer wiring -------------------------------------------------------


def test_annotation_columns_follow_the_row_builder_columns():
    assert sw.FEED_TAB_HEADERS == sw.FEED_HEADERS + ["dataset_status"]
    assert sw.ARCHIVE_TAB_HEADERS == sw.ARCHIVE_HEADERS + ["same_content_as"]
    # Not owned by ensure_tabs: the daily pipeline never rewrites these
    # headers, so clearing the column is a complete rollback.
    for tab in (sw.TAB_NEW, sw.TAB_HISTORICAL, sw.TAB_RELATED):
        assert sw._TAB_HEADERS[tab] == sw.FEED_HEADERS
    assert sw._TAB_HEADERS[sw.TAB_ARCHIVE] == sw.ARCHIVE_HEADERS
    assert sw.TAB_DATASET_PROVENANCE not in sw._TAB_HEADERS          # created on demand only
    # A new FEED_HEADERS / ARCHIVE_HEADERS column would collide with L / J.
    assert len(sw.FEED_HEADERS) == 11 and len(sw.ARCHIVE_HEADERS) == 9
    assert dp.col_letter(sw.FEED_HEADERS.index("Link")) == dp._LINK_LETTER


def test_hand_curated_annotation_column_is_after_the_published_columns():
    assert dp.HC_WIDTH == len(findings_feed.HANDCURATED_FIELDS)
    assert dp.col_letter(dp.HC_WIDTH) == "N"
    assert dp.col_letter(sw.FEED_TAB_HEADERS.index("dataset_status")) == "L"
    assert dp.col_letter(sw.ARCHIVE_TAB_HEADERS.index("same_content_as")) == "J"
    assert dp.col_letter(25) == "Z" and dp.col_letter(26) == "AA"


# --- pure helpers ------------------------------------------------------------


@pytest.mark.parametrize("good", [D1, D2, "HC-" + HC_FULL, "REL-WOI-status-report-2022-H2.pdf"])
def test_valid_doc_ids(good):
    assert dp.valid_doc_id(good)


@pytest.mark.parametrize("bad", ["", "abc", 'x")&HYPERLINK("evil', "HC-short", "1 2", "123\n",
                                 "REL-a b.pdf", "=1+1", "1" * 21])
def test_doc_id_guard_rejects_anything_formula_unsafe(bad):
    assert not dp.valid_doc_id(bad)
    with pytest.raises(dp.ProvenanceError):
        dp.pointer_formula(bad, 5, NSITE + "1", 2)


def test_pointer_formula_checks_its_own_row_and_hides_a_missing_row():
    f = dp.pointer_formula(D2, 42, NSITE + D2, 7)
    assert f == (f'=IF($H7="{NSITE}{D2}",IFERROR(HYPERLINK("#gid=42&range=A"&MATCH("{D2}",'
                 "'Dataset Provenance'!A:A,0),\"see Dataset Provenance\"),\"\"),\"\")")
    assert '$H3="a""b"' in dp.pointer_formula(D2, 1, 'a"b', 3)    # quotes escaped
    for bad_link in ("", "a\nb", "x" * 2001):
        with pytest.raises(dp.ProvenanceError):
            dp.pointer_formula(D2, 1, bad_link, 3)


def test_resolve_drive_prefix_needs_exactly_one_match():
    ids = [HC_FULL, "1RevOTHER0000000000000000000000000"]
    assert dp.resolve_drive_prefix("1RevPABsVmv0D8hL90WI", ids) == HC_FULL
    with pytest.raises(dp.ProvenanceError):
        dp.resolve_drive_prefix("1RevZZZZ", ids)
    with pytest.raises(dp.ProvenanceError):
        dp.resolve_drive_prefix("1Rev", ids)
    with pytest.raises(dp.ProvenanceError):
        dp.resolve_drive_prefix("", ids)


def test_feed_row_doc_id_reads_mirror_and_nsite_links_only():
    idx = {"https://drive.google.com/file/d/M/view": D1}
    assert dp.feed_row_doc_id("https://drive.google.com/file/d/M/view", idx) == D1
    assert dp.feed_row_doc_id(NSITE + D2 + "?x=1", idx) == D2
    assert dp.feed_row_doc_id("https://drive.google.com/file/d/OTHER/view", idx) == ""
    assert dp.feed_row_doc_id("https://example.com/12345", idx) == ""
    # exact host only (CodeQL py/incomplete-url-substring-sanitization)
    assert dp.feed_row_doc_id("https://evil.example/mienviro.michigan.gov/123", idx) == ""
    assert dp.feed_row_doc_id("https://mienviro.michigan.gov.evil.example/x/123", idx) == ""
    assert dp.feed_row_doc_id("", idx) == ""


def _row(**kw):
    d = {"doc_id": D1, "document_title": "t", "link": "l", "dataset": "wellfield-data-x",
         "source_report": "2024 H2", "status": "published (since a)",
         "first_release_tag": "a", "rows_contributed": "5"}
    d.update(kw)
    return d


def test_plan_upsert_appends_updates_and_skips():
    existing = [dp.row_from(_row(first_release_tag="first", last_updated="T0"))]
    up, ap, counts = dp.plan_upsert(existing, [_row(first_release_tag="first")], "T1")
    assert (up, ap, counts["unchanged"]) == ([], [], 1)          # nothing changed: no write
    up, ap, counts = dp.plan_upsert(existing, [_row(status="x", first_release_tag="later")], "T1")
    assert ap == [] and len(up) == 1
    grid_row, new = up[0]
    assert grid_row == 2 and new[5] == "x"
    assert new[6] == "first"                                      # first_release_tag is immutable
    assert new[8] == "T1"
    up, ap, counts = dp.plan_upsert(existing, [_row(dataset="other")], "T1")
    assert len(ap) == 1 and ap[0][8] == "T1"                      # new (doc_id, dataset) key


def test_plan_upsert_refuses_a_duplicate_key_or_bad_id():
    with pytest.raises(dp.ProvenanceError):
        dp.plan_upsert([], [_row(), _row()], "T")
    with pytest.raises(dp.ProvenanceError):
        dp.plan_upsert([], [_row(doc_id="bad id")], "T")


# --- Sheets I/O ----------------------------------------------------------------


def test_upsert_writes_ids_raw_and_is_idempotent():
    svc = base_sheet()
    dp.ensure_tab(svc, "S")
    dp.upsert(svc, "S", [_row(doc_id=D2)], "T1")
    assert svc.tabs[dp.TAB][1][0] == D2                           # string, all 19 digits
    assert all(mode == "RAW" for _, _, mode in svc.writes)
    n = len(svc.writes)
    assert dp.upsert(svc, "S", [_row(doc_id=D2)], "T2") == {"appended": 0, "updated": 0, "unchanged": 1, "kept": 0}
    assert len(svc.writes) == n


def test_read_rows_raises_when_the_tab_cannot_be_read():
    with pytest.raises(RuntimeError):
        dp.read_rows(base_sheet(), "S")


def test_sync_pointers_sets_clears_and_is_a_noop_second_time():
    svc = base_sheet()
    svc.tabs[sw.TAB_HISTORICAL][3] = feed_row("Not a dataset doc", NSITE + "777") + ["=OLD"]
    dp.ensure_tab(svc, "S")
    dp.upsert(svc, "S", [_row(doc_id=D1), _row(doc_id=D2)], "T")
    svc.writes.clear()
    counts = dp.sync_pointers(svc, "S")
    hist = svc.tabs[sw.TAB_HISTORICAL]
    gid = svc.gids[dp.TAB]
    assert hist[0][11] == "dataset_status"
    assert hist[1][11] == dp.pointer_formula(                      # via the Drive mirror link
        D1, gid, "https://drive.google.com/file/d/MIRROR1/view", 2)
    assert hist[2][11] == dp.pointer_formula(D2, gid, NSITE + D2, 3)   # via the nSITE link
    assert hist[3][11] == ""                                      # stale pointer cleared
    assert counts[sw.TAB_HISTORICAL] == {"set": 2, "cleared": 1}
    assert counts[sw.TAB_NEW] == {"set": 0, "cleared": 0}
    data_modes = {mode for op, rng, mode in svc.writes if not rng.endswith("L1")}
    assert data_modes == {"USER_ENTERED"}
    svc.writes.clear()
    dp.sync_pointers(svc, "S")
    assert svc.writes == []


def test_sync_pointers_needs_the_formula_render_to_stay_idempotent(monkeypatch):
    svc = base_sheet()
    dp.ensure_tab(svc, "S")
    dp.upsert(svc, "S", [_row(doc_id=D2)], "T")
    dp.sync_pointers(svc, "S")
    real_get = dp._get
    monkeypatch.setattr(dp, "_get", lambda s, i, a1, render=None: real_get(s, i, a1))
    assert dp.sync_pointers(svc, "S", apply=False)[sw.TAB_HISTORICAL]["set"] == 1


def test_a_pointer_on_the_wrong_row_points_nowhere():
    f = dp.pointer_formula(D2, 9, NSITE + D2, 3)
    assert f.startswith(f'=IF($H3="{NSITE}{D2}",')                 # blank unless row 3 is D2's


def test_sync_pointers_refuses_an_occupied_header_before_writing_anything():
    svc = base_sheet()
    svc.tabs[sw.TAB_HISTORICAL][0] = sw.FEED_HEADERS + ["Someone else's column"]
    dp.ensure_tab(svc, "S")
    dp.upsert(svc, "S", [_row(doc_id=D2)], "T")
    svc.writes.clear()
    with pytest.raises(dp.ProvenanceError):
        dp.sync_pointers(svc, "S")
    assert svc.writes == []


def test_sync_pointers_skips_before_the_tab_exists():
    assert dp.sync_pointers(base_sheet(), "S") == {"skipped": "no Dataset Provenance tab yet"}


def test_archive_duplicates_fill_blank_cells_only_raw():
    svc = base_sheet()
    out = dp.annotate_archive_duplicates(svc, "S", {D3: D2, "123": D2})
    assert out == {"written": [D3], "present": [], "conflict": [], "missing": ["123"]}
    assert svc.tabs[sw.TAB_ARCHIVE][0][9] == "same_content_as"
    assert svc.tabs[sw.TAB_ARCHIVE][3][9] == D2
    assert {mode for *_, mode in svc.writes} == {"RAW"}
    assert dp.annotate_archive_duplicates(svc, "S", {D3: D2})["present"] == [D3]
    assert dp.annotate_archive_duplicates(svc, "S", {D3: D1})["conflict"] == [D3]
    assert svc.tabs[sw.TAB_ARCHIVE][3][9] == D2                   # never overwritten
    with pytest.raises(dp.ProvenanceError):
        dp.annotate_archive_duplicates(svc, "S", {D3: D3})
    with pytest.raises(dp.ProvenanceError):
        dp.annotate_archive_duplicates(svc, "S", {"HC-" + HC_FULL: D2})


def test_dry_run_writes_nothing():
    svc = base_sheet()
    dp.annotate_archive_duplicates(svc, "S", {D3: D2}, apply=False)
    dp.annotate_hand_curated(svc, "S", {"2025-01-06-h2.pdf": D1}, apply=False)
    assert dp.upsert(svc, "S", [_row()], "T", apply=False)["appended"] == 1
    assert svc.writes == [] and dp.TAB not in svc.tabs


def test_hand_curated_needs_its_origin_url_to_name_the_doc():
    svc = base_sheet()
    out = dp.annotate_hand_curated(svc, "S", {"2025-01-06-h2.pdf": D1, "jan-feb-2022.pdf": D2,
                                              "absent.pdf": D1})
    assert out["written"] == ["2025-01-06-h2.pdf"]
    assert out["conflict"] == ["jan-feb-2022.pdf"]                # origin_url doesn't name D2
    assert out["missing"] == ["absent.pdf"]
    assert svc.tabs[sw.TAB_HANDCURATED][0][13] == "same_as_monitor_doc"
    assert svc.tabs[sw.TAB_HANDCURATED][1][13] == D1
    assert len(svc.tabs[sw.TAB_HANDCURATED][2]) <= 13             # untouched
    assert {mode for *_, mode in svc.writes} == {"RAW"}


def test_record_staged_takes_titles_from_the_sheet_and_points_the_feed():
    svc = base_sheet()
    counts = dp.record_staged(svc, "S", [{"doc_id": D2, "rows_contributed": 7},
                                         {"doc_id": "HC-" + HC_FULL, "rows_contributed": 3}], "T")
    rows = {r[0]: r for r in svc.tabs[dp.TAB][1:]}
    assert rows[D2][1] == "WOI Q2 2023"                           # the feed's display name
    assert rows[D2][2] == "https://drive.google.com/file/d/MIRROR2/view"
    assert rows[D2][3] == dp.NOT_IN_RELEASE and rows[D2][5] == dp.STATUS_STAGED
    assert rows[D2][7] == "7"
    assert rows["HC-" + HC_FULL][1] == "WOI Jan-Feb 2022"         # the Hand-Curated title
    assert counts["appended"] == 2
    assert svc.tabs[sw.TAB_HISTORICAL][2][11] == dp.pointer_formula(D2, svc.gids[dp.TAB], NSITE + D2, 3)
    again = dp.record_staged(svc, "S", [{"doc_id": D2, "rows_contributed": 7}], "2026-10-24T01:00:00Z")
    assert again["unchanged"] == 1 and again["appended"] == 0
    rows = {r[0]: r for r in svc.tabs[dp.TAB][1:]}
    assert again["withdrawn"] == 1                                # HC copy dropped out of the set
    assert rows["HC-" + HC_FULL][5] == "withdrawn from staging 2026-10-24: no longer in the staged set"
    assert rows[D2][5] == dp.STATUS_STAGED


def test_record_staged_never_undoes_a_hand_set_status_and_marks_consumed():
    svc = base_sheet()
    dp.record_staged(svc, "S", [{"doc_id": D2, "rows_contributed": 7}, {"doc_id": D3}], "T1")
    svc.tabs[dp.TAB][1][5] = "published (since wellfield-data-2026-11-01)"   # Trisha's flip
    out = dp.record_staged(svc, "S", [{"doc_id": D2, "rows_contributed": 9}, {"doc_id": D3}], "T2")
    assert out["kept"] == 1 and svc.tabs[dp.TAB][1][5].startswith("published")
    assert svc.tabs[dp.TAB][1][7] == "7"
    out = dp.record_staged(svc, "S", [], "2026-11-02T00:00:00Z", consumed_by="wellfield-data-2026-11-01")
    assert out["withdrawn"] == 1
    assert svc.tabs[dp.TAB][2][5] == ("withdrawn from staging 2026-11-02: "
                                      "consumed by release wellfield-data-2026-11-01")
    # a document staged again later is back to "staged"
    dp.record_staged(svc, "S", [{"doc_id": D3}], "T4")
    assert svc.tabs[dp.TAB][2][5] == dp.STATUS_STAGED


def test_record_staged_with_nothing_staged_and_no_tab_writes_nothing():
    svc = base_sheet()
    assert dp.record_staged(svc, "S", [], "T")["withdrawn"] == 0
    assert svc.writes == [] and dp.TAB not in svc.tabs


def test_record_staged_checks_both_feed_headers_before_any_write():
    svc = base_sheet()
    svc.tabs[sw.TAB_HISTORICAL][0] = sw.FEED_HEADERS + ["Someone else's column"]
    with pytest.raises(dp.ProvenanceError):
        dp.record_staged(svc, "S", [{"doc_id": D2}], "T")
    assert svc.writes == [] and dp.TAB not in svc.tabs


def test_record_staged_refuses_a_bad_id_before_writing():
    svc = base_sheet()
    with pytest.raises(dp.ProvenanceError):
        dp.record_staged(svc, "S", [{"doc_id": "nsite:123"}], "T")
    assert svc.writes == []


# --- seed script -------------------------------------------------------------


def _map(**kw):
    d = {"dataset": "wellfield-data-2026-10-09", "source_report": "L", "first_release_tag": "t0",
         "rows_in_latest": "10", "nsite_doc_id": "", "drive_file_id": "", "bundled_source_file": "f.pdf"}
    d.update(kw)
    return d


def test_seed_rows_apply_the_verified_corrections():
    rows = seed.build_seed_rows([
        _map(source_report="Nov 2022", nsite_doc_id="4283353693233058745",
             bundled_source_file="WOI-status-report-2022-H2.pdf"),
        _map(source_report="Jan-Feb 2022", drive_file_id="1RevPABsVmv0D8hL90WI"),
        _map(source_report="Q2 2023", nsite_doc_id=D2),
        _map(source_report="Q2 2023", nsite_doc_id=D3),
        _map(source_report="Q2 2024", nsite_doc_id="-6975595422894935530"),
        _map(source_report="Apr", drive_file_id="1RevPABsVmv0D8hL90WI"),
        _map(source_report="Apr", nsite_doc_id=D1),
        _map(dataset="neshap-data-2026-08-29", nsite_doc_id=D1),
    ], [HC_FULL])
    by = {(r["doc_id"], r["source_report"]): r for r in rows}
    assert len(rows) == 7                                          # non-wellfield row skipped
    nov = by[("REL-WOI-status-report-2022-H2.pdf", "Nov 2022")]   # rejected candidate id dropped
    assert nov["link"] == dp.RELEASE_URL.format(tag="wellfield-data-2026-10-09")
    assert by[("HC-" + HC_FULL, "Jan-Feb 2022")]["status"] == "published (since t0)"
    assert by[(D3, "Q2 2023")]["status"] == f"covered (0 new; via {D2})"
    assert by[(D3, "Q2 2023")]["rows_contributed"] == "0"
    assert by[(D2, "Q2 2023")]["rows_contributed"] == "10"         # a covered copy isn't "combined"
    assert by[("-6975595422894935530", "Q2 2024")]["status"].endswith(
        "needs review: 114 rows validation_ok=no")
    assert by[(D1, "Apr")]["rows_contributed"] == f"10 (combined with HC-{HC_FULL})"


def test_seed_rows_refuse_a_name_in_source_report():
    with pytest.raises(dp.ProvenanceError):
        seed.build_seed_rows([_map(source_report="Letter from Scott Miller", nsite_doc_id=D1)], [])


def test_seed_rows_take_counts_from_the_release_csv():
    rows = seed.build_seed_rows([_map(source_report="Jan-Feb 2023", nsite_doc_id=D1)], [],
                                {"Jan-Feb 2023": 4232})
    assert rows[0]["rows_contributed"] == "4232"
    with pytest.raises(dp.ProvenanceError):
        seed.build_seed_rows([_map(source_report="Gone", nsite_doc_id=D1)], [], {"Other": 1})


def test_release_row_counts(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("source_report,well_id\nA,1\nA,2\nB,3\n")
    assert seed.release_row_counts(str(p)) == {"A": 2, "B": 1}


def test_proposed_pairs_are_separate_from_the_approved_ones():
    assert seed.ARCHIVE_PAIRS == {D3: D2}
    assert not set(seed.PROPOSED_ARCHIVE_PAIRS) & set(seed.ARCHIVE_PAIRS)
    assert not set(seed.PROPOSED_HAND_CURATED_MATCHES) & set(seed.HAND_CURATED_MATCHES)


def test_describe_never_copies_a_listed_name():
    svc = base_sheet()
    svc.tabs[sw.TAB_HISTORICAL][2] = feed_row("Records request by Mike Kovalchick", NSITE + D2)
    svc.tabs[sw.TAB_NEW].append(feed_row("WOI Q2 2023 (clean title)", NSITE + D2))
    svc.tabs[sw.TAB_ARCHIVE][2][1] = "Kovalchick email"
    assert dp.describe(svc, "S", [D2])[D2][0] == "WOI Q2 2023 (clean title)"
    svc.tabs[sw.TAB_NEW].pop()
    assert dp.describe(svc, "S", [D2])[D2][0] == f"nSITE document {D2}"
    svc.tabs[sw.TAB_HANDCURATED][2][1] = "Letter from Scott Miller"
    assert dp.describe(svc, "S", ["HC-" + HC_FULL])["HC-" + HC_FULL][0] == "jan-feb-2022.pdf"


def test_only_the_scripts_own_status_text_counts_as_staging_owned():
    assert dp._staging_owned(dp.STATUS_STAGED)
    assert dp._staging_owned("withdrawn from staging 2026-11-02: no longer in the staged set")
    assert dp._staging_owned("withdrawn from staging 2026-11-02: consumed by release wellfield-data-2026-11-01")
    assert not dp._staging_owned("withdrawn from staging: wrong doc (Trisha)")
    assert not dp._staging_owned("published (since wellfield-data-2026-11-01)")
