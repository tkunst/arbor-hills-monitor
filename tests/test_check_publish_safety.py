"""Hermetic tests for scripts/check_publish_safety.py -- the pure `evaluate_pages`
gate logic. Builds REAL rendered articles via findings_feed.render_entry so the
gate is tested against the same HTML the generator writes. No network, no Sheet."""
import importlib.util
import os

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
