"""scripts/backfill_mirror_index.py (ADR 073 addendum, CODER-PLAN step 4): the
one-time Mirror Index fill. Loaded by file path, like the other scripts/ tests."""
import importlib.util
import os
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "backfill_mirror_index",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "backfill_mirror_index.py"),
)
bmi = importlib.util.module_from_spec(_SPEC)
sys.modules["backfill_mirror_index"] = bmi  # its dataclasses look the module up
_SPEC.loader.exec_module(bmi)

import mirror_index as mi  # noqa: E402
from ocr_util import OcrStats  # noqa: E402
from test_pfas_watcher import FakeSheets  # noqa: E402

ID1, ID2, ID3 = "1" + "a" * 32, "1" + "b" * 32, "1" + "c" * 32


@pytest.fixture(autouse=True)
def _fresh_ensure_cache():
    mi._ENSURED.clear()
    yield
    mi._ENSURED.clear()


def _link(did):
    return f"https://drive.google.com/file/d/{did}/view?usp=drivesdk"


def _meta(did, sha="s" * 64, mime=bmi.PDF, mod="2026-10-01T00:00:00.000Z", size="10"):
    return {"id": did, "mimeType": mime, "sha256Checksum": sha, "md5Checksum": "m" * 32,
            "size": size, "headRevisionId": "R", "modifiedTime": mod}


def _stats(pages=3, image_only=(), engine="none", sha="s" * 64):
    fz = frozenset(image_only)
    return OcrStats(sha256=sha, pages=pages, image_only_pages=fz, image_only_before=fz,
                    ocr_ran=False, engine=engine)


def _tabs(nsite=(), mmpc=(), civic=(), hc=()):
    return {
        "Archived PDFs": [["Doc ID", "Document Name", "Archive Link"]]
        + [[k, "n", _link(d) if d else ""] for k, d in nsite],
        "MMPC Archived Files": [["File ID", "Archive Link"]] + [[k, _link(d)] for k, d in mmpc],
        "CivicClerk Archived Files": [["File ID", "Group", "Archive Link"]]
        + [[k, "g", _link(d)] for k, d in civic],
        "Hand-Curated Files": [["curated_filename", "drive_link"]] + [[k, _link(d)] for k, d in hc],
    }


# --- pure --------------------------------------------------------------------

@pytest.mark.parametrize("link,want", [
    (_link(ID1), ID1),
    (f"https://drive.google.com/open?id={ID2}", ID2),
    ("https://mienviro.michigan.gov/ncore/downloadpdf/-123", None),
    ("", None),
])
def test_drive_id_from_link(link, want):
    assert bmi.drive_id_from_link(link) == want


def test_sheet_refs_precedence_duplicates_and_missing_links():
    refs, c = bmi.sheet_refs(_tabs(nsite=[("-1", ID1), ("-2", None)], civic=[("9", ID2)],
                                   hc=[("a.pdf", ID1), ("b.pdf", ID3), ("c.pdf", ID3)]))
    assert refs == {ID1: ("nsite", "-1"), ID2: ("civicclerk", "9"), ID3: ("hand_curated", "b.pdf")}
    assert c["nsite:no_link"] == 1 and c["hand_curated:shared_with_nsite"] == 1
    assert c["hand_curated:duplicate_link"] == 1 and c["hand_curated:rows"] == 3


def test_sheet_refs_refuses_a_renamed_column():
    tabs = _tabs()
    tabs["MMPC Archived Files"] = [["File ID", "Link"], ["1", _link(ID1)]]
    with pytest.raises(ValueError):
        bmi.sheet_refs(tabs)


def test_vision_at_is_detroit_local_time():
    # 2026-10-14 is EDT (UTC-4).
    assert bmi.vision_at_utc("2026-10-14T17:22:21").isoformat() == "2026-10-14T21:22:21+00:00"
    assert bmi.vision_at_utc("garbage") is None


@pytest.mark.parametrize("status,mod,want", [
    ("skip_has_text", "2026-10-14T21:00:00Z", "searchable"),
    ("ocr_nothing", "2026-10-14T21:00:00Z", "not_searchable"),
    # Modified after Vision looked (UTC 21:22): the Vision numbers may be stale.
    ("skip_has_text", "2026-10-14T21:30:00Z", None),
    ("done", "2026-10-14T21:00:00Z", None),
])
def test_vision_shortcut_only_for_unchanged_files(status, mod, want):
    v = {"status": status, "pages": 3, "at": "2026-10-14T17:22:21"}
    assert bmi.vision_shortcut(_meta(ID1, mod=mod), v) == want


def test_vision_shortcut_needs_an_entry_with_pages():
    assert bmi.vision_shortcut(_meta(ID1), None) is None
    assert bmi.vision_shortcut(_meta(ID1), {"status": "skip_has_text", "at": "2026-10-14T17:00:00"}) is None


def test_engine_with_vision_names_vision_only_when_the_fresh_check_matches():
    v = {"status": "done_partial", "pages": 3, "image_only_after": 1}
    got = bmi.engine_with_vision(_stats(pages=3, image_only={2}), v)
    assert got.engine == "Apple Vision" and mi.text_status(got) == "ocr_partial"
    full = bmi.engine_with_vision(_stats(), {"status": "done", "pages": 3, "image_only_after": 0})
    assert mi.text_status(full) == "ocr_full"  # not `native`
    both = bmi.engine_with_vision(_stats(engine="OCRmyPDF 17"),
                                  {"status": "done", "pages": 3, "image_only_after": 0})
    assert both.engine == "OCRmyPDF 17 + Apple Vision"
    # Page count differs: the file isn't what Vision wrote; leave it alone.
    s = _stats(pages=4)
    assert bmi.engine_with_vision(s, {"status": "done", "pages": 3, "image_only_after": 0}) is s
    assert bmi.engine_with_vision(s, {"status": "skip_has_text", "pages": 4}) is s
    assert bmi.engine_with_vision(None, {"status": "done"}) is None


def test_build_plan_skips_only_on_a_live_sha_match():
    refs = {ID1: ("nsite", "-1"), ID2: ("mmpc", "2"), ID3: ("civicclerk", "3"),
            "1" + "d" * 32: ("hand_curated", "x.xlsx"), "1" + "e" * 32: ("hand_curated", "gone.pdf")}
    listed = {ID1: _meta(ID1, sha="A" * 64), ID2: _meta(ID2, sha="b" * 64),
              ID3: _meta(ID3, sha=""), "1" + "d" * 32: _meta("1" + "d" * 32, mime="application/zip")}
    fetched = {"1" + "e" * 32: None}
    index = {ID1: {"mirror_sha256": "a" * 64}, ID2: {"mirror_sha256": "old"},
             ID3: {"mirror_sha256": ""}}
    plan = bmi.build_plan(refs, listed, fetched, index, {}, {})
    act = {it.drive_id: it.action for it in plan.items}
    assert act[ID1] == "skip_indexed"           # case-insensitive equal sha
    assert act[ID2] == "reindex"                # row exists, sha differs
    assert act[ID3] == "reindex"                # blank live sha is never skippable
    assert act["1" + "d" * 32] == "not_pdf" and act["1" + "e" * 32] == "unreachable"
    assert plan.counts["reindex"] == 2 and plan.counts["nsite:skip_indexed"] == 1


def test_build_plan_matches_vision_by_first_root_path():
    refs = {ID1: ("nsite", "-1")}
    v = {"AHLI_1.pdf": {"status": "skip_has_text", "pages": 2, "at": "2026-10-14T17:00:00"}}
    plan = bmi.build_plan(refs, {ID1: _meta(ID1)}, {}, {}, v, {ID1: "AHLI_1.pdf"})
    assert plan.items[0].vision == v["AHLI_1.pdf"] and plan.items[0].shortcut == "searchable"
    plan = bmi.build_plan(refs, {ID1: _meta(ID1)}, {}, {}, v, {})
    assert plan.items[0].vision is None and plan.items[0].shortcut is None


def test_unreferenced_pdfs_counts_only_unlinked_pdfs():
    listed = {ID1: _meta(ID1), ID2: _meta(ID2), ID3: _meta(ID3, mime="text/html")}
    assert bmi.unreferenced_pdfs(listed, {ID1: ("nsite", "1")}) == 1


def test_summary_carries_counts_only_never_ids_or_names():
    refs = {ID1: ("nsite", "secret-key")}
    v = {"secret name.pdf": {"status": "done", "pages": 3, "at": "2026-10-14T17:00:00"}}
    plan = bmi.build_plan(refs, {ID1: _meta(ID1)}, {}, {}, v, {ID1: "secret name.pdf"})
    _refs, rc = bmi.sheet_refs(_tabs(nsite=[("secret-key", ID1)]))
    text = bmi.render_summary(plan, rc, 0, None)
    assert ID1 not in text and "secret" not in text
    assert "download 1 files" in text


# --- Drive listing -------------------------------------------------------------

class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self, num_retries=0):
        return self._fn()


class FakeDrive:
    """files().list over a {folder: [file, ...]} tree, 1 file per page."""

    def __init__(self, tree, missing=()):
        self.tree, self.missing = tree, set(missing)

    def files(self):
        return self

    def get(self, fileId, fields, supportsAllDrives=True):
        def run():
            if fileId in self.missing:
                raise RuntimeError("404")
            return {"id": fileId}
        return _Req(run)

    def list(self, q, fields, pageSize, pageToken, supportsAllDrives, includeItemsFromAllDrives):
        folder = q.split("'")[1]
        items = self.tree.get(folder, [])
        i = int(pageToken or 0)

        def run():
            out = {"files": items[i:i + 1]}
            if i + 1 < len(items):
                out["nextPageToken"] = str(i + 1)
            return out
        return _Req(run)


def test_list_tree_recurses_all_pages_with_paths():
    tree = {"ROOT": [{"id": "F", "name": "Sub", "mimeType": bmi.FOLDER}, _meta(ID1) | {"name": "a.pdf"}],
            "F": [_meta(ID2) | {"name": "b.pdf"}, _meta(ID3) | {"name": "c.html", "mimeType": "text/html"}]}
    files, paths = bmi.list_tree(FakeDrive(tree), "ROOT", 0)
    assert set(files) == {ID1, ID2, ID3}
    assert paths == {ID1: "a.pdf", ID2: "Sub/b.pdf", ID3: "Sub/c.html"}


def test_list_tree_raises_when_the_root_is_not_visible():
    with pytest.raises(RuntimeError):
        bmi.list_tree(FakeDrive({}, missing={"ROOT"}), "ROOT", 0)


# --- apply -----------------------------------------------------------------------

def _plan(*items):
    p = bmi.Plan()
    for did, action, vision in items:
        p.items.append(bmi.Item(did, "nsite", "k-" + did[-1], _meta(did), action=action, vision=vision))
    return p


def test_apply_appends_rows_from_downloaded_stats_and_skips_failures(monkeypatch):
    s = FakeSheets()
    outcomes = {ID1: (_stats(), "ok"), ID2: (None, "changed_mid_run"), ID3: (None, "unreadable")}
    monkeypatch.setattr(bmi, "stats_for", lambda drive, it, td, r: outcomes[it.drive_id])
    plan = _plan((ID1, "index", {"status": "done", "pages": 3, "image_only_after": 0}),
                 (ID2, "index", None), (ID3, "index", None), ("1" + "z" * 32, "skip_indexed", None))
    c = bmi.apply(None, s, "SID", plan, batch=1, limit=0, retries=0, log=lambda *a: None)
    idx = mi.read_index(s, "SID")
    assert set(idx) == {ID1, ID3}                       # changed-mid-run never written
    assert idx[ID1]["text_status"] == "ocr_full" and idx[ID1]["ocr_engine"] == "Apple Vision"
    assert idx[ID3]["text_status"] == "unknown"         # unreadable = process it later
    assert idx[ID1]["original_sha256"] == "" and idx[ID1]["source_kind"] == "nsite"
    assert c["appended"] == 2 and c["changed_mid_run"] == 1


def test_apply_drops_rows_a_racing_upload_already_wrote(monkeypatch):
    s = FakeSheets()
    s._values._tabs[mi.TAB] = [list(mi.HEADERS)]
    monkeypatch.setattr(bmi, "stats_for", lambda drive, it, td, r: (_stats(), "ok"))

    def racing_stats(drive, it, td, r):
        # The nightly upload indexes ID1 while the backfill is downloading it.
        s._values._tabs[mi.TAB].append(mi.build_row(_meta(ID1), _stats(), source_kind="nsite",
                                                    source_key="live", now="T"))
        return _stats(), "ok"
    monkeypatch.setattr(bmi, "stats_for", racing_stats)
    c = bmi.apply(None, s, "SID", _plan((ID1, "index", None)), batch=10, limit=0, retries=0,
                  log=lambda *a: None)
    rows = s._values._tabs[mi.TAB][1:]
    assert len(rows) == 1 and rows[0][2] == "live" and c["raced_already_indexed"] == 1


def test_apply_replaces_a_stale_row_in_place(monkeypatch):
    s = FakeSheets()
    s._values._tabs[mi.TAB] = [list(mi.HEADERS),
                               mi.build_row(_meta(ID1, sha="o" * 64), None, source_kind="nsite",
                                            source_key="k-a", now="T")]
    monkeypatch.setattr(bmi, "stats_for", lambda drive, it, td, r: (_stats(), "ok"))
    c = bmi.apply(None, s, "SID", _plan((ID1, "reindex", None)), batch=10, limit=0, retries=0,
                  log=lambda *a: None)
    rows = s._values._tabs[mi.TAB][1:]
    assert len(rows) == 1 and rows[0][3] == "s" * 64 and c["replaced"] == 1


def test_apply_stops_on_a_failed_index_read(monkeypatch):
    s = FakeSheets()
    s._values._tabs[mi.TAB] = [list(mi.HEADERS)]
    monkeypatch.setattr(bmi, "stats_for", lambda drive, it, td, r: (_stats(), "ok"))

    def boom(spreadsheetId, range):
        raise RuntimeError("503")
    s._values.get = boom
    with pytest.raises(RuntimeError):
        bmi.apply(None, s, "SID", _plan((ID1, "index", None)), batch=10, limit=0, retries=0,
                  log=lambda *a: None)
    assert s._values._tabs[mi.TAB] == [list(mi.HEADERS)]   # never appended blind


def test_apply_respects_limit(monkeypatch):
    s = FakeSheets()
    monkeypatch.setattr(bmi, "stats_for", lambda drive, it, td, r: (_stats(), "ok"))
    c = bmi.apply(None, s, "SID", _plan((ID1, "index", None), (ID2, "index", None)), batch=10,
                  limit=1, retries=0, log=lambda *a: None)
    assert c["appended"] == 1


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    """main() without --apply: reads only, no Sheet write, no download."""
    s = FakeSheets()
    s._values._tabs.update(_tabs(nsite=[("-1", ID1)]))
    s._values._tabs[mi.TAB] = [list(mi.HEADERS)]
    tree = {"ROOT": [_meta(ID1) | {"name": "a.pdf"}]}
    import drive_client as dc
    import config_loader
    monkeypatch.setattr(config_loader, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(dc, "drive_service", lambda: FakeDrive(tree))
    monkeypatch.setattr(dc, "sheets_service", lambda: s)
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setattr(bmi, "stats_for", lambda *a: pytest.fail("dry run downloaded"))
    before = {k: [list(r) for r in v] for k, v in s._values._tabs.items()}
    assert bmi.main(["--root", "ROOT", "--detail-out", str(tmp_path / "d.tsv")]) == 0
    assert s._values._tabs == before
    assert "index" in (tmp_path / "d.tsv").read_text()
