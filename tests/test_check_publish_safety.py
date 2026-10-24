"""Hermetic tests for scripts/check_publish_safety.py -- the pure `evaluate_pages`
gate logic. Builds REAL rendered articles via findings_feed.render_entry so the
gate is tested against the same HTML the generator writes. No network, no Sheet."""
import importlib.util
import json
import os

import pytest

import findings_feed as ff

_SPEC = importlib.util.spec_from_file_location(
    "check_publish_safety",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "check_publish_safety.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
cps = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cps)


def _hc(title="Quarterly monitoring report, Arbor Hills", source_public="EGLE / nSITE"):
    row = ff.parse_handcurated_rows([[
        "f.pdf", title, "internal-source-with-a-name", "2026-01-01", "N2688",
        "procedural", "", "", "internal note", "https://drive.google.com/x",
        "2026-01-01T00:00:00", "no", source_public]])[0]
    return ff.render_entry(row)


def _auto(name="Auto doc", summary=""):
    row = ff.parse_feed_rows([[
        "2026-01-01", name, "evidence", "R5", "notable", summary, "",
        "https://x/1", "Arbor Hills Remediation Area"]])[0]
    return ff.render_entry(row)


def _page(*articles):
    return {"index.html": "\n".join(articles)}


# --- search-index.json entries (ADR 062 Phase 2) ----------------------------
# Same underlying rows as _hc/_auto above, run through build_search_index
# instead of render_entry -- the JSON-side equivalent, exercised against
# evaluate_search_index the same way _hc/_auto feed evaluate_pages.

def _hc_entry(title="Quarterly monitoring report, Arbor Hills", source_public="EGLE / nSITE",
              doc_date="2026-01-01", doc_type="procedural"):
    row = ff.parse_handcurated_rows([[
        "f.pdf", title, "internal-source-with-a-name", doc_date, "N2688",
        doc_type, "", "", "internal note", "https://drive.google.com/x",
        "2026-01-01T00:00:00", "no", source_public]])[0]
    return json.loads(ff.build_search_index([row]))[0]


def _auto_entry(name="Auto doc", summary=""):
    row = ff.parse_feed_rows([[
        "2026-01-01", name, "evidence", "R5", "notable", summary, "",
        "https://x/1", "Arbor Hills Remediation Area"]])[0]
    return json.loads(ff.build_search_index([row]))[0]


def test_handcurated_entries_carry_a_source_key_auto_do_not():
    # The classifier evaluate_search_index relies on.
    assert "source" in _hc_entry()
    assert "source" not in _auto_entry()


def test_denylist_name_in_handcurated_entry_title_blocks():
    r = cps.evaluate_search_index([_hc_entry(title="On-Site Inspection (Kovalchick)")])
    assert len(r["block"]) == 1
    assert not r["warn_auto"]


def test_denylist_name_in_handcurated_entry_source_blocks():
    r = cps.evaluate_search_index([_hc_entry(source_public="EGLE AQD (Diane Kavanaugh Vetort)")])
    assert len(r["block"]) == 1


def test_heuristic_name_shape_in_handcurated_entry_blocks():
    r = cps.evaluate_search_index([_hc_entry(title="Report (Jane Doe)")])
    assert len(r["block"]) == 1


def test_clean_handcurated_entry_does_not_block():
    r = cps.evaluate_search_index([_hc_entry()])
    assert r["block"] == []


def test_name_in_auto_entry_warns_not_blocks():
    r = cps.evaluate_search_index([_auto_entry(summary="email requested by Mike Kovalchick")])
    assert r["block"] == []
    assert len(r["warn_auto"]) == 1


def test_mixed_index_blocks_on_handcurated_only():
    r = cps.evaluate_search_index([
        _auto_entry(summary="note from Anthony Testa"),   # warn
        _hc_entry(title="Clean title"),                    # fine
        _hc_entry(source_public="EGLE (Scott Miller)"),    # block
    ])
    assert len(r["block"]) == 1
    assert len(r["warn_auto"]) == 1


# --- regression: evaluate_search_index must scan EVERY field, not an -------
# allowlist (an earlier version scanned only title/excerpt/facility/source,
# missing hand-curated `type`/`date` -- both free Sheet-cell text with no
# schema validation, same as `source`/`title`).

def test_denylist_name_in_handcurated_entry_type_blocks():
    r = cps.evaluate_search_index([_hc_entry(doc_type="Report (Kovalchick)")])
    assert len(r["block"]) == 1


def test_heuristic_name_shape_in_handcurated_entry_date_blocks():
    r = cps.evaluate_search_index([_hc_entry(doc_date="2026-01-01 (Jane Doe)")])
    assert len(r["block"]) == 1


def test_evaluate_search_index_never_scans_link():
    # `link` is excluded from the field scan because the HTML path is
    # equally blind to it (_visible_text strips tag attributes, so an href
    # is never scanned there either) -- this is a deliberate parity choice,
    # not an oversight, so pin it explicitly rather than let a future
    # "scan everything" refactor silently start blocking on link contents.
    row = ff.parse_handcurated_rows([[
        "f.pdf", "Clean title", "internal", "2026-01-01", "N2688", "procedural", "",
        "", "note", "https://drive.google.com/x?Kovalchick", "2026-01-01T00:00:00",
        "no", "EGLE / nSITE"]])[0]
    entry = json.loads(ff.build_search_index([row]))[0]
    assert "Kovalchick" in entry["link"]  # the link really does carry the name
    r = cps.evaluate_search_index([entry])
    assert r["block"] == []


def _write_index(out_dir, *rows):
    index_json = ff.build_search_index(list(rows))
    (out_dir / cps.SEARCH_INDEX_FILENAME).write_text(index_json, encoding="utf-8")


_LEAKING_HC_ROW = ff.parse_handcurated_rows([[
    "f.pdf", "On-Site Inspection (Kovalchick)", "internal", "2026-01-01",
    "N2688", "procedural", "", "", "note", "https://drive.google.com/x",
    "2026-01-01T00:00:00", "no", "EGLE / nSITE"]])[0]

_CLEAN_HC_ROW = ff.parse_handcurated_rows([[
    "f.pdf", "Quarterly monitoring report, Arbor Hills", "internal",
    "2026-01-01", "N2688", "procedural", "", "", "note",
    "https://drive.google.com/x", "2026-01-01T00:00:00", "no", "EGLE / nSITE"]])[0]


def test_main_merges_json_block_into_html_result(tmp_path, monkeypatch):
    # End-to-end through main(): an HTML-clean page but a leaking JSON index
    # must still fail the whole gate -- one exit code covers both files.
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()
    (out_dir / "index.html").write_text(_page(_hc(title="Clean title"))["index.html"],
                                         encoding="utf-8")
    _write_index(out_dir, _LEAKING_HC_ROW)
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    assert cps.main() == 1


def test_main_passes_when_both_html_and_json_are_clean(tmp_path, monkeypatch):
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()
    (out_dir / "index.html").write_text(_page(_hc())["index.html"], encoding="utf-8")
    _write_index(out_dir, _CLEAN_HC_ROW)
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    assert cps.main() == 0


def test_main_tolerates_a_missing_search_index_json(tmp_path, monkeypatch):
    # gen_findings_feed.py always writes it, but the gate must not hard-crash
    # if it's absent (same tolerance as a missing HTML page).
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()
    (out_dir / "index.html").write_text(_page(_hc())["index.html"], encoding="utf-8")
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    assert cps.main() == 0


# --- regression: the JSON index must be scanned even when NO HTML pages ----
# exist (an earlier version's `if not pages: return 0` skipped the JSON
# check entirely in that case).

def test_main_blocks_on_a_leaking_json_index_with_zero_html_pages(tmp_path, monkeypatch):
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()
    _write_index(out_dir, _LEAKING_HC_ROW)  # no index.html written at all
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    assert cps.main() == 1


def test_main_passes_on_a_clean_json_index_with_zero_html_pages(tmp_path, monkeypatch):
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()
    _write_index(out_dir, _CLEAN_HC_ROW)
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    assert cps.main() == 0


def test_main_warns_zero_to_gate_only_when_both_pages_and_index_are_absent(tmp_path, monkeypatch):
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()  # empty: no HTML, no search-index.json
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    assert cps.main() == 0


def test_main_blocks_with_both_origins_tagged_when_both_artifacts_leak(tmp_path, monkeypatch):
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()
    (out_dir / "index.html").write_text(
        _page(_hc(title="On-Site Inspection (Kovalchick)"))["index.html"], encoding="utf-8")
    _write_index(out_dir, _LEAKING_HC_ROW)
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    assert cps.main() == 1

    pages = cps._load_pages(cps.OUT_DIR)
    html_block = cps.evaluate_pages(pages)["block"]
    entries = cps._load_search_index(os.path.join(cps.OUT_DIR, cps.SEARCH_INDEX_FILENAME))
    json_block = cps.evaluate_search_index(entries)["block"]
    assert len(html_block) == 1 and html_block[0]["origin"] == "html"
    assert len(json_block) == 1 and json_block[0]["origin"] == "json"


# --- regression: a falsy-but-malformed search-index.json (not just bad ----
# JSON syntax) must fail closed, never silently read as "no entries, same
# as absent" -- an earlier version's `not index_entries` check in main()
# couldn't distinguish a present-but-`{}` file from a genuinely absent one.

@pytest.mark.parametrize("bad_json", ["{}", "null", "0", '""', "[1, 2]",
                                       '[{"title": "ok"}, "not a dict"]'])
def test_load_search_index_raises_on_wrong_shape_json(tmp_path, bad_json):
    p = tmp_path / cps.SEARCH_INDEX_FILENAME
    p.write_text(bad_json, encoding="utf-8")
    with pytest.raises(ValueError):
        cps._load_search_index(str(p))


def test_main_fails_closed_on_a_falsy_wrong_shape_json_index_with_zero_html_pages(
        tmp_path, monkeypatch):
    out_dir = tmp_path / "public-records"
    out_dir.mkdir()
    (out_dir / cps.SEARCH_INDEX_FILENAME).write_text("{}", encoding="utf-8")  # no index.html
    monkeypatch.setattr(cps, "OUT_DIR", str(out_dir))
    with pytest.raises(ValueError):
        cps.main()


def test_evaluate_pages_never_scans_html_href_attributes():
    # Parity with evaluate_search_index's deliberate exclusion of `link`
    # (see its docstring) -- _visible_text strips whole tags including
    # attributes, so a name embedded only in an href never surfaces in the
    # scanned text on the HTML side either. Pinned explicitly rather than
    # left as an unverified assumption in a comment.
    art = ('<article class="finding"><h3><a href="https://x/?Kovalchick">Clean title</a>'
           '</h3><p class="finding-meta">Source: EGLE</p></article>')
    r = cps.evaluate_pages({"index.html": art})
    assert r["block"] == []


def test_handcurated_articles_carry_a_source_tag_auto_do_not():
    # The classifier this gate relies on.
    assert "Source:" in _hc()
    assert "Source:" not in _auto()


def test_denylist_name_in_handcurated_title_blocks():
    r = cps.evaluate_pages(_page(_hc(title="On-Site Inspection (Kovalchick)")))
    assert len(r["block"]) == 1
    assert not r["warn_auto"]


def test_denylist_name_in_handcurated_source_blocks():
    r = cps.evaluate_pages(_page(_hc(source_public="EGLE AQD (Diane Kavanaugh Vetort)")))
    assert len(r["block"]) == 1


def test_heuristic_name_shape_in_handcurated_now_blocks():
    # Unlike the intake redaction loop, the publish GATE blocks hand-curated
    # heuristic hits (fail-safe): a novel name-shaped token stops the deploy for
    # human review. A false positive is cleared by rewording or allowlisting.
    r = cps.evaluate_pages(_page(_hc(title="Report (Jane Doe)")))
    assert len(r["block"]) == 1


def test_internal_marker_in_handcurated_blocks():
    r = cps.evaluate_pages(_page(_hc(source_public="Found in Trisha's FOIA Downloads")))
    assert len(r["block"]) == 1


def test_clean_handcurated_article_does_not_block():
    r = cps.evaluate_pages(_page(_hc()))
    assert r["block"] == []


def test_name_in_auto_feed_warns_not_blocks():
    # Pre-existing auto exposure ("Mike Kovalchick" in a scraped summary) must
    # WARN, never block the daily regeneration.
    r = cps.evaluate_pages(_page(_auto(summary="email requested by Mike Kovalchick")))
    assert r["block"] == []
    assert len(r["warn_auto"]) == 1


def test_mixed_page_blocks_on_handcurated_only():
    r = cps.evaluate_pages(_page(
        _auto(summary="note from Anthony Testa"),       # warn
        _hc(title="Clean title"),                        # fine
        _hc(source_public="EGLE (Scott Miller)"),        # block
    ))
    assert len(r["block"]) == 1
    assert len(r["warn_auto"]) == 1
