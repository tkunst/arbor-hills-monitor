"""Tests for scripts/term_search.py.

Focus: the false-positive SUPPRESSION that turns a noisy grep into a usable
search (subsidence!=subsidiary, boiling!=boiler, outbreak!=COVID, TS-01!=TS1),
plus the corpus/relevance/report wiring. All fixtures are synthetic — no PDFs or
real records are committed (the data-guard CI check blocks *.pdf)."""
import os
import sys
import types

import fitz
import pytest

# term_search lives in scripts/ (not a package); put it on the path.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import term_search as ts  # noqa: E402

COMPILED = ts.compile_terms(ts.DEFAULT_TERMS)


def _labels(pages):
    return {h.label for h in ts.find_hits(pages, COMPILED)}


# ---------------------------------------------------------------------------
# Negative-filter precision — the whole point of the tool
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,label", [
    ("Arbor Hills Energy, LLC, a subsidiary of Opal Fuels", "subsidence"),   # subsidiary
    ("Boiler Machinery Policy 10/1/22 covers boilers", "boiling/bubbling"),  # boiler
    ("NE SE SEC 7 - BOILING SPRINGS ROAD, OSCODA", "boiling/bubbling"),      # place name
    ("initial boiling point and boiling range not available", "boiling/bubbling"),
    ("the novel coronavirus outbreak (COVID-19) pandemic", "leachate outbreak/seep"),
    ("treatment standards are TS1 = primary treatment, TS2 = secondary", "TS-01"),
    ("EGT-Typhoon turbines to operate a common steam turbine generator", "fuming/steaming"),
    ("property taxes will return to growth as restrictions subside", "subsidence"),  # 'subside' != subsidence
])
def test_false_positives_are_suppressed(text, label):
    assert label not in _labels([text]), f"{label!r} should NOT match: {text!r}"


@pytest.mark.parametrize("text,label", [
    ("noted minor, localized surface subsidence occurring every six months", "subsidence"),
    ("Leachate bubbling out of sideslope just west of 567", "boiling/bubbling"),
    ("Leachate outbreak and pooling by 285R3 on upper deck", "leachate outbreak/seep"),
    ("Significant leachate seep near C011 on the cap", "leachate outbreak/seep"),
    ("Sample Description: TS-01 Aerated, GFL - AHE Leachate", "TS-01"),
    ("Well 263R5 had elevated temperatures which required sampling", "elevated temperature"),
    ("localized cracking, minor discoloration, and slight surface settlement", "settlement"),
    ("a subsidence area, increase in leachate, increase odors", "subsidence"),
])
def test_real_events_are_matched(text, label):
    assert label in _labels([text]), f"{label!r} SHOULD match: {text!r}"


def test_leachate_settlement_negative_does_not_kill_landfill_settlement():
    # a legal 'settlement agreement' is suppressed...
    assert "settlement" not in _labels(["the parties reached a settlement agreement"])
    # ...but 'surface settlement' at the landfill is kept
    assert "settlement" in _labels(["areas of surface settlement were backfilled"])


# ---------------------------------------------------------------------------
# Page attribution + dedup
# ---------------------------------------------------------------------------
def test_hit_reports_correct_page():
    pages = ["cover page, nothing here", "filler", "surface subsidence observed here"]
    hits = [h for h in ts.find_hits(pages, COMPILED) if h.label == "subsidence"]
    assert hits and hits[0].page == 3


def test_repeated_identical_context_deduped():
    page = "Leachate Seeps Y. " * 5 + "Leachate outbreak on path between 430 and 431."
    hits = [h for h in ts.find_hits([page], COMPILED) if h.label == "leachate outbreak/seep"]
    # identical 'Leachate Seeps Y' contexts collapse; distinct outbreak line kept
    assert 1 <= len(hits) <= 3


# ---------------------------------------------------------------------------
# Extraction + recall gaps
# ---------------------------------------------------------------------------
def _pdf(tmp_path, name, pages_text):
    path = str(tmp_path / name)
    doc = fitz.open()
    for t in pages_text:
        doc.new_page().insert_text((72, 72), t)
    doc.save(path)
    doc.close()
    return path


def test_extract_pages_reads_text(tmp_path):
    p = _pdf(tmp_path, "d.pdf", ["surface subsidence near 16R6", "page two"])
    pages, err = ts.extract_pages(p)
    assert err is None and len(pages) == 2 and "subsidence" in pages[0]


def test_image_only_pdf_is_a_recall_gap(image_pdf):
    # image_pdf fixture from conftest has no text layer
    refs = [ts.DocRef(doc_id="img", name="img.pdf", local_path=image_pdf)]
    results, scanned, gaps = ts.run(refs, COMPILED, use_llm=False, model="", max_candidates=0)
    assert scanned == 0 and len(gaps) == 1


# ---------------------------------------------------------------------------
# Relevance pass (fake client) + end-to-end run
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, parsed):
        self.parsed_output = parsed
        self.stop_reason = "end_turn"


class _FakeClient:
    """Mimics anthropic.Anthropic(): .messages.parse(...) -> resp.parsed_output.
    Returns relevant=True when a snippet mentions 'landfill'-ish event words."""
    def __init__(self):
        self.calls = 0
        self.messages = types.SimpleNamespace(parse=self._parse)

    def _parse(self, **kw):
        self.calls += 1
        blob = kw["messages"][0]["content"].lower()
        real = any(w in blob for w in ("subsidence", "leachate outbreak", "ts-01", "bubbling"))
        return _FakeResp(types.SimpleNamespace(
            relevant=real, sense="event" if real else "false positive",
            confidence=0.9 if real else 0.1, why="test"))


def test_relevance_judge_populates_fields():
    dr = ts.DocResult(
        ref=ts.DocRef(doc_id="x", name="cover integrity.pdf"),
        hits=[ts.Hit("leachate outbreak/seep", 1, "Leachate outbreak and pooling by 285R3")],
        n_pages=1,
    )
    ts.relevance_judge(dr, model="m", client=_FakeClient())
    assert dr.relevant is True and dr.confidence == 0.9


def test_run_end_to_end_flags_real_and_suppresses_noise(tmp_path):
    real = _pdf(tmp_path, "cover.pdf", ["Leachate outbreak and pooling by 285R3 on upper deck"])
    noise = _pdf(tmp_path, "10k.pdf", ["OPAL Fuels Inc. and its subsidiary companies; boiler policy"])
    refs = [ts.DocRef("cover.pdf", "cover.pdf", local_path=real),
            ts.DocRef("10k.pdf", "10k.pdf", local_path=noise)]
    client = _FakeClient()
    results, scanned, gaps = ts.run(refs, COMPILED, use_llm=True, model="m",
                                    max_candidates=10, client=client)
    assert scanned == 2
    # the 'subsidiary'/'boiler' doc produced NO hits at the regex stage
    names = {r.ref.name for r in results}
    assert "cover.pdf" in names and "10k.pdf" not in names
    cover = next(r for r in results if r.ref.name == "cover.pdf")
    assert cover.relevant is True and client.calls == 1


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def test_report_ranks_relevant_first_and_warns_private():
    good = ts.DocResult(ts.DocRef("a", "a.pdf"), [ts.Hit("subsidence", 1, "surface subsidence")], 1,
                        relevant=True, confidence=0.9, sense="subsidence")
    bad = ts.DocResult(ts.DocRef("b", "b.pdf"), [ts.Hit("boiling/bubbling", 1, "boiler")], 1,
                       relevant=False, sense="false positive")
    rep = ts.build_report([bad, good], corpus="local: x", scanned=2, recall_gaps=[],
                          used_llm=True, term_labels=list(ts.DEFAULT_TERMS))
    assert "keep it private" in rep.lower()
    assert rep.index("## Relevant") < rep.index("Suppressed as off-topic")
    assert "a.pdf" in rep and "b.pdf" in rep
    # SEC-001: suppressed docs must still show their snippet so a human can audit
    # every LLM downgrade (a false-negative would otherwise silently drop a hit).
    assert "boiler" in rep


@pytest.mark.parametrize("corpus,no_llm,llm,expected", [
    ("local", False, False, False),          # SEC-002: local default OFF (FOIA/PII egress)
    ("local", False, True, True),            # explicit --llm opts in
    ("local", True, True, False),            # --no-llm always wins
    ("nsite-archive", False, False, True),   # public archive default ON
    ("nsite-archive", True, False, False),   # --no-llm wins over the archive default
])
def test_resolve_use_llm(corpus, no_llm, llm, expected):
    assert ts.resolve_use_llm(corpus, no_llm, llm) is expected


def test_report_marks_llm_off():
    rep = ts.build_report([], corpus="local: x", scanned=0, recall_gaps=[],
                          used_llm=False, term_labels=list(ts.DEFAULT_TERMS))
    assert "regex only" in rep


# ---------------------------------------------------------------------------
# nSITE-archive corpus (fake Drive service — no live Drive)
# ---------------------------------------------------------------------------
def test_iter_nsite_archive_pages_and_downloads(tmp_path, monkeypatch):
    """The Drive path enumerates (with pagination) and downloads each PDF to a
    temp dir. Exercised with a fake archive_client + fake MediaIoBaseDownload so
    the shipped code path is covered without a real Drive."""
    class _Exec:
        def __init__(self, payload):
            self._p = payload

        def execute(self):
            return self._p

    class _Files:
        def list(self, **kw):
            if kw.get("pageToken") is None:
                return _Exec({"files": [{"id": "A", "name": "doc-a.pdf",
                                         "webViewLink": "http://x/a"}],
                              "nextPageToken": "p2"})
            return _Exec({"files": [{"id": "B", "name": "doc-b.pdf",
                                     "webViewLink": "http://x/b"}]})

        def get_media(self, fileId):  # noqa: N803 (Drive API kwarg name)
            return ("media", fileId)

    class _Service:
        def files(self):
            return _Files()

    fake_ac = types.SimpleNamespace(
        is_configured=lambda env=None: True,
        oauth_drive_service=lambda: _Service(),
        folder_id=lambda env=None: "FOLDER",
    )

    class _DL:
        def __init__(self, fh, request):
            self.fh = fh

        def next_chunk(self):
            self.fh.write(b"%PDF-1.4 fake")
            return (None, True)

    monkeypatch.setitem(sys.modules, "archive_client", fake_ac)
    monkeypatch.setitem(sys.modules, "googleapiclient.http",
                        types.SimpleNamespace(MediaIoBaseDownload=_DL))

    refs = list(ts.iter_nsite_archive(str(tmp_path)))
    assert [r.name for r in refs] == ["doc-a.pdf", "doc-b.pdf"]
    assert [r.doc_id for r in refs] == ["A", "B"]
    assert all(os.path.exists(r.local_path) for r in refs)
    assert all(open(r.local_path, "rb").read().startswith(b"%PDF") for r in refs)


def test_iter_nsite_archive_requires_creds(tmp_path, monkeypatch):
    fake_ac = types.SimpleNamespace(is_configured=lambda env=None: False)
    monkeypatch.setitem(sys.modules, "archive_client", fake_ac)
    with pytest.raises(SystemExit):
        list(ts.iter_nsite_archive(str(tmp_path)))
