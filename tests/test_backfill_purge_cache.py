"""coder:backfill-purge-read-cache (2026-10-23): backfill's FORCE_REPROCESS
paths read each purge tab ONCE per run (sheet_writer.PurgeReadCache) instead of
once per doc, and wait out a Sheets 429 once before failing.

The motivating failure: run 39699682035 (2026-10-21), a FORCE_REPROCESS dry run
over 58 doc ids. Every `purge_doc_rows` call did spreadsheets().get + one
values().get per purge tab (+ the Archived PDFs index), ~8 reads per doc, and
the seventh doc hit "Read requests per minute per user" (60). The fake service
below COUNTS reads so the test pins the cost, not just the result.
"""
import pytest

import backfill as bf
import doc_pipeline as dp
import sheet_writer as sw
import test_sheet_writer as tsw  # FakeSheets; module import keeps its tests out of this file

N_DOCS = 58


class CountingSheets(tsw.FakeSheets):
    """FakeSheets that counts spreadsheets().get and values().get calls, and
    can be told to raise a given exception on the next N values().get calls."""

    def __init__(self, tabs=None):
        super().__init__(tabs)
        self.meta_reads = 0
        self.value_reads = 0
        self.raise_next: list = []  # exceptions to raise, one per values().get
        orig = self._values.get

        def counted(spreadsheetId, range):
            self.value_reads += 1
            if self.raise_next:
                raise self.raise_next.pop(0)
            return orig(spreadsheetId=spreadsheetId, range=range)
        self._values.get = counted

    def get(self, spreadsheetId):
        self.meta_reads += 1
        return super().get(spreadsheetId)


class _Http429(Exception):
    """Duck-typed like googleapiclient's HttpError (.resp.status / .status_code)."""

    def __init__(self, status=429):
        super().__init__(f"HttpError {status}")
        self.status_code = status
        self.resp = type("R", (), {"status": status})()


def _nsite(did):
    return f"https://mienviro.michigan.gov/ncore/downloadpdf/{did}"


def _drive(did):
    return f"https://drive.google.com/file/d/f{did}/view?usp=drivesdk"


def _row(headers, did, link, name="x"):
    r = [""] * len(headers)
    r[0] = "2026-01-01"
    r[1] = name
    r[headers.index("Link")] = link
    return r


def _doc_ids(n=N_DOCS):
    # Negative and positive ids, like real nSITE ids.
    return [str((-1) ** i * (1000 + i)) for i in range(n)]


def _fixture(ids=None, mirrored_every=2):
    """All six purge tabs + the Archived PDFs index. Every doc has one
    Historical row and two Measurements rows; every other doc is Drive-mirrored
    (its rows carry the Drive link, resolvable only via the archive index)."""
    ids = ids or _doc_ids()
    hist, ev, meas, woi, archive = [sw.FEED_HEADERS], [sw.EVIDENCE_HEADERS], \
        [sw.MEASUREMENTS_HEADERS], [sw.WOI_SUMMARY_HEADERS], [sw.ARCHIVE_HEADERS]
    for i, did in enumerate(ids):
        mirrored = (i % mirrored_every == 1)
        link = _drive(did) if mirrored else _nsite(did)
        hist.append(_row(sw.FEED_HEADERS, did, link, f"doc{i}"))
        ev.append(_row(sw.EVIDENCE_HEADERS, did, link))
        meas.append(_row(sw.MEASUREMENTS_HEADERS, did, link))
        meas.append(_row(sw.MEASUREMENTS_HEADERS, did, link))
        woi.append(_row(sw.WOI_SUMMARY_HEADERS, did, link))
        if mirrored:
            a = [""] * len(sw.ARCHIVE_HEADERS)
            a[0], a[5] = did, link
            archive.append(a)
    return {
        sw.TAB_HISTORICAL: hist, sw.TAB_NEW: [sw.FEED_HEADERS],
        sw.TAB_RELATED: [sw.FEED_HEADERS], sw.TAB_EVIDENCE: ev,
        sw.TAB_MEASUREMENTS: meas, sw.TAB_WOI_SUMMARY: woi, sw.TAB_ARCHIVE: archive,
    }


def _links_of(tab_rows, headers):
    col = headers.index("Link")
    return [r[col] for r in tab_rows[1:]]


# ---------------------------------------------------------------------------
# PurgeReadCache: the read cost
# ---------------------------------------------------------------------------

def test_dry_run_58_ids_reads_each_tab_once_and_matches_the_uncached_preview():
    ids = _doc_ids()
    cached, fresh = CountingSheets(_fixture(ids)), CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    for did in ids:
        with_cache = sw.purge_doc_rows(cached, "SID", did, dry_run=True, cache=cache)
        without = sw.purge_doc_rows(fresh, "SID", did, dry_run=True)
        assert with_cache == without  # identical per-doc preview
        assert with_cache[sw.TAB_HISTORICAL] == 1 and with_cache[sw.TAB_MEASUREMENTS] == 2
    n_tabs = len(sw._PURGE_TABS)
    assert cached.value_reads == n_tabs + 1          # six tabs + the archive index: the spec's bound
    assert cached.meta_reads == 1
    assert cache.reads == cached.value_reads
    assert fresh.value_reads == N_DOCS * (n_tabs + 1)  # the pre-fix cost, for contrast
    assert fresh.meta_reads == N_DOCS


def test_uncached_call_reads_fresh_exactly_as_before():
    svc = CountingSheets(_fixture(_doc_ids(3)))
    sw.purge_doc_rows(svc, "SID", _doc_ids(3)[0], dry_run=True)
    assert svc.meta_reads == 1 and svc.value_reads == len(sw._PURGE_TABS) + 1


def test_fill_is_a_no_op_on_a_warm_cache():
    svc = CountingSheets(_fixture(_doc_ids(2)))
    cache = sw.PurgeReadCache()
    assert cache.fill(svc, "SID") == len(sw._PURGE_TABS) + 1
    assert cache.fill(svc, "SID") == 0
    assert svc.value_reads == len(sw._PURGE_TABS) + 1 and svc.meta_reads == 1


def test_cache_skips_a_tab_absent_at_fill_time():
    tabs = _fixture(_doc_ids(2))
    del tabs[sw.TAB_WOI_SUMMARY]
    svc = CountingSheets(tabs)
    counts = sw.purge_doc_rows(svc, "SID", _doc_ids(2)[0], dry_run=True, cache=sw.PurgeReadCache())
    assert sw.TAB_WOI_SUMMARY not in counts and counts[sw.TAB_HISTORICAL] == 1


# ---------------------------------------------------------------------------
# PurgeReadCache: the apply path (deletes shift rows -> a purged tab is re-read)
# ---------------------------------------------------------------------------

def test_apply_with_cache_deletes_the_same_rows_as_uncached_sequential_purges():
    ids = _doc_ids(6)
    cached, fresh = CountingSheets(_fixture(ids)), CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    # Purge the EVEN docs; their rows interleave with the odd docs' rows, so a
    # stale cached index after the first delete would hit the wrong row.
    for did in ids[::2]:
        a = sw.purge_doc_rows(cached, "SID", did, cache=cache)
        b = sw.purge_doc_rows(fresh, "SID", did)
        assert a == b
    for tab in (sw.TAB_HISTORICAL, sw.TAB_EVIDENCE, sw.TAB_MEASUREMENTS, sw.TAB_WOI_SUMMARY):
        assert cached._values._tabs[tab] == fresh._values._tabs[tab]
        assert cached._values._tabs[tab][0] == fresh._values._tabs[tab][0]  # header intact
    survivors = _links_of(cached._values._tabs[sw.TAB_HISTORICAL], sw.FEED_HEADERS)
    assert survivors == [_drive(d) for d in ids[1::2]]  # the odd (mirrored) docs remain
    # Read cost: one fill (7) + per doc one pre-delete read of each of the 4
    # tabs that hold its rows (New/Related/archive index/metadata stay cached).
    assert cached.value_reads == (len(sw._PURGE_TABS) + 1) + 4 * len(ids[::2])
    assert cached.meta_reads == 1


def test_apply_rereads_a_purged_tab_even_when_the_caller_never_refills():
    """purge_doc_rows never deletes from cached indices: the apply path reads the
    tab right before building the delete, whatever the caller did in between."""
    ids = _doc_ids(4)
    svc = CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    sw.purge_doc_rows(svc, "SID", ids[0], cache=cache)
    # After a successful delete the cache holds the post-delete layout (== the Sheet).
    assert cache.tab_values[sw.TAB_HISTORICAL] == svc._values._tabs[sw.TAB_HISTORICAL][1:]
    assert sw.TAB_NEW in cache.tab_values             # untouched tab stays cached
    counts = sw.purge_doc_rows(svc, "SID", ids[2], cache=cache)
    assert counts[sw.TAB_HISTORICAL] == 1
    assert _links_of(svc._values._tabs[sw.TAB_HISTORICAL], sw.FEED_HEADERS) == \
        [_drive(ids[1]), _drive(ids[3])]


def test_apply_takes_delete_indices_from_a_read_made_now_not_the_cache():
    """Between backfill's fill and doc_pipeline's purge, minutes pass (download,
    OCR, classification). If someone inserts a row above the doc's rows in
    that window, the cached indices point one row too high; the pre-delete
    re-read must catch it so the right rows are deleted."""
    ids = _doc_ids(3)
    svc = CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    cache.fill(svc, "SID")
    stranger = _row(sw.FEED_HEADERS, "999", _nsite("999"), "inserted-by-hand")
    svc._values._tabs[sw.TAB_HISTORICAL].insert(1, stranger)  # shifts every row down
    counts = sw.purge_doc_rows(svc, "SID", ids[2], cache=cache)
    assert counts[sw.TAB_HISTORICAL] == 1
    assert _links_of(svc._values._tabs[sw.TAB_HISTORICAL], sw.FEED_HEADERS) == \
        [_nsite("999"), _nsite(ids[0]), _drive(ids[1])]  # the stranger survives, ids[2] is gone


def test_apply_keeps_an_untouched_tab_with_real_matches_cached_across_docs():
    """A tab holding rows for LATER docs but none for the doc being purged is
    served from the cache (no read) and still purged correctly later."""
    ids = _doc_ids(4)
    tabs = _fixture(ids)
    tabs[sw.TAB_NEW] = [sw.FEED_HEADERS, _row(sw.FEED_HEADERS, ids[2], _nsite(ids[2])),
                        _row(sw.FEED_HEADERS, ids[3], _drive(ids[3]))]
    svc = CountingSheets(tabs)
    cache = sw.PurgeReadCache()
    sw.purge_doc_rows(svc, "SID", ids[0], cache=cache)      # New Documents: 0 matches
    reads_after_first = svc.value_reads
    assert sw.TAB_NEW in cache.tab_values
    counts = sw.purge_doc_rows(svc, "SID", ids[2], cache=cache)
    assert counts[sw.TAB_NEW] == 1
    assert svc._values._tabs[sw.TAB_NEW][1:] == [_row(sw.FEED_HEADERS, ids[3], _drive(ids[3]))]
    # Second doc: a pre-delete read for each of the 5 tabs holding its rows, no more.
    assert svc.value_reads - reads_after_first == 5


def test_apply_then_fresh_rows_appended_then_next_doc_purged():
    """Between two purges the pipeline appends the re-extracted doc's fresh rows
    (at the table's end). Those rows must survive the next doc's purge and the
    next doc's stale rows must still be found at the right indices."""
    ids = _doc_ids(3)
    svc = CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    sw.purge_doc_rows(svc, "SID", ids[0], cache=cache)
    fresh = _row(sw.FEED_HEADERS, ids[0], _nsite(ids[0]), "fresh")
    svc._values._tabs[sw.TAB_HISTORICAL].append(fresh)
    svc._values._tabs[sw.TAB_NEW].append(fresh)       # a tab the purge did NOT touch
    counts = sw.purge_doc_rows(svc, "SID", ids[2], cache=cache)
    assert counts[sw.TAB_HISTORICAL] == 1 and counts[sw.TAB_NEW] == 0
    assert _links_of(svc._values._tabs[sw.TAB_HISTORICAL], sw.FEED_HEADERS) == \
        [_drive(ids[1]), _nsite(ids[0])]
    assert svc._values._tabs[sw.TAB_NEW][1:] == [fresh]


def test_stale_cached_match_with_nothing_to_delete_keeps_the_fresh_read_cached():
    ids = _doc_ids(3)
    svc = CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    cache.fill(svc, "SID")
    # Someone removed ids[0]'s rows from every tab between the fill and the purge.
    for tab, col in sw._PURGE_TABS:
        rows = svc._values._tabs[tab]
        svc._values._tabs[tab] = [rows[0]] + [r for r in rows[1:] if sw._link_doc_id(r[col]) != ids[0]]
    reads_before = svc.value_reads
    counts = sw.purge_doc_rows(svc, "SID", ids[0], cache=cache)
    assert counts[sw.TAB_HISTORICAL] == 0 and sum(counts.values()) == 0
    assert svc.value_reads - reads_before == 4            # one reread per tab with a cached match
    for tab, _ in sw._PURGE_TABS:
        assert cache.tab_values[tab] == svc._values._tabs[tab][1:]  # fresh read kept, no re-fill
    assert cache.fill(svc, "SID") == 0


def test_cache_is_invalidated_before_the_delete_is_issued():
    ids = _doc_ids(2)
    svc = CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()

    def boom(spreadsheetId, body):
        raise RuntimeError("connection dropped")
    svc.batchUpdate = boom
    with pytest.raises(RuntimeError):
        sw.purge_doc_rows(svc, "SID", ids[0], cache=cache)
    for tab in (sw.TAB_HISTORICAL, sw.TAB_EVIDENCE, sw.TAB_MEASUREMENTS, sw.TAB_WOI_SUMMARY):
        assert tab not in cache.tab_values  # unknown layout -> must be re-read


def test_dry_run_never_invalidates():
    ids = _doc_ids(2)
    svc = CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    for did in ids:
        sw.purge_doc_rows(svc, "SID", did, dry_run=True, cache=cache)
    assert set(cache.tab_values) == {t for t, _ in sw._PURGE_TABS}


def test_cache_reads_the_archive_index_strictly():
    """A throttled Archived PDFs read must RAISE (so backfill's quota wait sees
    it), never read as "nothing mirrored" — that would miss every Drive-linked
    row and an apply would append fresh rows beside the stale ones. This holds
    for an UNCACHED purge_doc_rows call too (the one deliberate behaviour
    change for callers that pass no cache)."""
    svc = CountingSheets(_fixture(_doc_ids(2)))
    svc.raise_next = [_Http429()]
    with pytest.raises(_Http429):
        sw.PurgeReadCache().fill(svc, "SID")
    svc.raise_next = [_Http429()]
    with pytest.raises(_Http429):
        sw.purge_doc_rows(svc, "SID", _doc_ids(2)[0], dry_run=True)
    # The lenient default of archived_doc_links is unchanged for its dedup callers.
    svc.raise_next = [_Http429()]
    assert sw.archived_doc_links(svc, "SID") == {}


def test_a_429_mid_purge_on_the_apply_path_is_a_no_strike_retry(monkeypatch, tmp_path):
    """The reason backfill does NOT wrap process_one_doc in the quota wait: a
    429 raised by the pre-delete reread inside doc_pipeline._purge must come
    back as a transient 'retry' (no strike, no stub, no row written) through
    the REAL process_one_doc + retry_policy, not just by assertion in a
    comment."""
    ids = _doc_ids(2)
    svc = CountingSheets(_fixture(ids))
    cache = sw.PurgeReadCache()
    cache.fill(svc, "SID")
    svc.raise_next = [_Http429()]  # the next values().get is the pre-delete reread
    written, marked = [], []
    monkeypatch.setattr(dp.nc, "download_pdf", lambda *a: None)
    monkeypatch.setattr(dp.av, "mirror_one_now", lambda *a, **k: "LINK")
    monkeypatch.setattr(dp.woi_router, "route_measurements", lambda *a, **k: None)
    monkeypatch.setattr(dp.sw, "write_document", lambda *a, **k: written.append(1))
    monkeypatch.setattr(dp.sw, "mark_processed", lambda *a, **k: written.append("state"))
    monkeypatch.setattr(dp.sw, "mark_error", lambda *a, **k: marked.append(1))
    parsed = type("P", (), dict(doc_type="evidence", severity="routine", risks=["R8"],
                               display_title="", document_date="", measurements=[], deadlines=[],
                               key_data_point="", summary="", full_text=""))()
    ctx = dp.RunContext(cfg={"large_doc_page_threshold": 30, "large_doc_max_keyword_pages": 10,
                             "classification_max_tokens": 10},
                        sheets=svc, sheet_id="SID", session=None, model="m", well_alias_map=None,
                        parse=lambda *a, **k: parsed, now=lambda: "T")
    job = dp.Job(name="backfill", feed_tab=sw.TAB_HISTORICAL, payload=lambda p, d: {})
    state = {"processed": {}, "errors": {}, "skipped": {}}
    d = {"doc_id": ids[0], "document_name": "Doc", "date_filed": "2026-01-01", "facility_scope": "core"}
    assert dp.process_one_doc(ctx, state, d, job, str(tmp_path / "x.pdf"),
                              purge_first=True, purge_cache=cache) == "retry"
    assert state["errors"] == {} and marked == [] and written == []
    # Nothing was deleted: the doc's rows are all still there for the next run.
    assert _links_of(svc._values._tabs[sw.TAB_HISTORICAL], sw.FEED_HEADERS) == [_nsite(ids[0]), _drive(ids[1])]
    # The cache is still only used to MATCH; the retried purge rereads before deleting.
    counts = sw.purge_doc_rows(svc, "SID", ids[0], cache=cache)
    assert counts[sw.TAB_HISTORICAL] == 1
    assert _links_of(svc._values._tabs[sw.TAB_HISTORICAL], sw.FEED_HEADERS) == [_drive(ids[1])]


# ---------------------------------------------------------------------------
# The once-only 429 wait
# ---------------------------------------------------------------------------

def test_is_rate_limited_duck_types_a_429():
    assert bf._is_rate_limited(_Http429()) is True
    assert bf._is_rate_limited(_Http429(503)) is False
    assert bf._is_rate_limited(RuntimeError("HTTP 429")) is False  # no status attr
    resp_only = type("E", (Exception,), {})()
    resp_only.resp = type("R", (), {"status": 429})()
    assert bf._is_rate_limited(resp_only) is True


def test_quota_wait_covers_a_full_minute_window():
    assert bf.QUOTA_WAIT_S >= 61  # a sliding 60 s window needs the whole minute


def test_waiting_out_quota_retries_once_after_a_429(monkeypatch, capsys):
    slept = []
    monkeypatch.setattr(bf, "_sleep", slept.append)
    calls = []

    def flaky(x, k=None):
        calls.append((x, k))
        if len(calls) == 1:
            raise _Http429()
        return "ok"
    assert bf._waiting_out_quota(flaky, 1, k=2) == "ok"
    assert calls == [(1, 2), (1, 2)] and slept == [bf.QUOTA_WAIT_S]
    assert "HTTP 429" in capsys.readouterr().out


def test_waiting_out_quota_fails_on_a_second_429(monkeypatch):
    monkeypatch.setattr(bf, "_sleep", lambda s: None)

    def always():
        raise _Http429()
    with pytest.raises(_Http429):
        bf._waiting_out_quota(always)


def test_waiting_out_quota_reraises_anything_else_without_sleeping(monkeypatch):
    slept = []
    monkeypatch.setattr(bf, "_sleep", slept.append)

    def boom():
        raise _Http429(503)
    with pytest.raises(_Http429):
        bf._waiting_out_quota(boom)
    with pytest.raises(RuntimeError):
        bf._waiting_out_quota(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert slept == []


# ---------------------------------------------------------------------------
# backfill.run(): a 58-id FORCE_REPROCESS dry run, end to end on the fake
# ---------------------------------------------------------------------------

def _wire_run(monkeypatch, svc, ids):
    monkeypatch.setattr(bf, "load_config", lambda: {
        "anthropic_model": "m", "backfill_batch_size": 100,
        "large_doc_page_threshold": 50, "large_doc_max_keyword_pages": 10,
    })
    monkeypatch.setenv("GSHEET_ID", "SID")
    monkeypatch.setenv("FORCE_REPROCESS_DOC_IDS", ",".join(ids))
    monkeypatch.delenv("FORCE_REPROCESS_APPLY", raising=False)
    monkeypatch.setattr(bf.dc, "sheets_service", lambda: svc)
    monkeypatch.setattr(bf.sw, "ensure_tabs", lambda s, sid: None)
    monkeypatch.setattr(bf.sw, "read_state", lambda s, sid: {
        "processed": {d: {} for d in ids}, "errors": {}, "skipped": {}})
    monkeypatch.setattr(bf.nc, "make_session", lambda: None)
    monkeypatch.setattr(bf.nc, "fetch_all_documents", lambda session, cfg: [
        {"doc_id": d, "date_filed": "2026-01-01", "document_name": f"doc{i}"}
        for i, d in enumerate(ids)])

    def _must_not_run(*a, **k):
        raise AssertionError("a dry run must not process any document")
    monkeypatch.setattr(bf.dp, "process_one_doc", _must_not_run)


def test_run_dry_run_over_58_ids_completes_within_the_read_budget(monkeypatch, capsys):
    ids = _doc_ids()
    svc = CountingSheets(_fixture(ids))
    _wire_run(monkeypatch, svc, ids)
    assert bf.run() == 0
    out = capsys.readouterr().out
    previews = [ln for ln in out.splitlines() if ln.startswith("  [FORCE dry-run]")]
    assert len(previews) == N_DOCS
    # The per-doc preview line is unchanged in shape.
    assert all("would PURGE 5 row(s) {" in ln and "then re-extract this doc." in ln
               for ln in previews)
    assert svc.value_reads == len(sw._PURGE_TABS) + 1 and svc.meta_reads == 1
    assert f"purge reads this run: {len(sw._PURGE_TABS) + 1} values().get" in out
    assert "DRY RUN complete — nothing was changed." in out
    assert all(len(t) == len(_fixture(ids)[n]) for n, t in svc._values._tabs.items())  # mutated nothing


def test_run_dry_run_waits_out_one_429_then_finishes(monkeypatch, capsys):
    ids = _doc_ids(10)
    svc = CountingSheets(_fixture(ids))
    svc.raise_next = [_Http429()]  # the very first tab read is throttled
    slept = []
    monkeypatch.setattr(bf, "_sleep", slept.append)
    _wire_run(monkeypatch, svc, ids)
    assert bf.run() == 0
    assert len(slept) == 1
    out = capsys.readouterr().out
    assert "waiting" in out and "retrying once" in out
    assert len([ln for ln in out.splitlines() if ln.startswith("  [FORCE dry-run]")]) == 10
    assert svc.value_reads == len(sw._PURGE_TABS) + 2  # the failed read + a full fill


def test_run_dry_run_fails_on_a_second_consecutive_429(monkeypatch):
    ids = _doc_ids(3)
    svc = CountingSheets(_fixture(ids))
    svc.raise_next = [_Http429(), _Http429()]
    monkeypatch.setattr(bf, "_sleep", lambda s: None)
    _wire_run(monkeypatch, svc, ids)
    with pytest.raises(_Http429):
        bf.run()


def test_run_apply_threads_the_cache_into_process_one_doc(monkeypatch):
    ids = _doc_ids(3)
    svc = CountingSheets(_fixture(ids))
    _wire_run(monkeypatch, svc, ids)
    monkeypatch.setenv("FORCE_REPROCESS_APPLY", "1")
    seen = []

    def fake_process(ctx, state, d, job, local, purge_first=False, purge_cache=None):
        seen.append((d["doc_id"], purge_first, purge_cache))
        sw.purge_doc_rows(ctx.sheets, ctx.sheet_id, d["doc_id"], cache=purge_cache)
        return "processed"
    monkeypatch.setattr(bf.dp, "process_one_doc", fake_process)
    monkeypatch.setattr(bf.sw, "rebuild_risk_register_tab", lambda *a, **k: None)
    monkeypatch.setattr(bf.sw, "rebuild_all_evidence_tab", lambda *a, **k: None)
    assert bf.run() == 0
    assert [s[0] for s in seen] == ids and all(s[1] for s in seen)
    assert len({id(s[2]) for s in seen}) == 1 and isinstance(seen[0][2], sw.PurgeReadCache)
    assert svc.meta_reads == 1
    assert _links_of(svc._values._tabs[sw.TAB_HISTORICAL], sw.FEED_HEADERS) == []
