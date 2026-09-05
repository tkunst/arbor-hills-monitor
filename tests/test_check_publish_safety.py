"""Hermetic tests for scripts/check_publish_safety.py -- the pure `evaluate`
gate logic (no network, no Sheet)."""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "check_publish_safety",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "check_publish_safety.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
cps = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cps)


def _hc(document_name="A clean title", source="EGLE / nSITE"):
    return {"document_name": document_name, "source": source, "summary": "",
            "key_data_point": "", "type": "procedural", "facility": "N2688"}


def _auto(document_name="Auto doc", summary="", key_data_point=""):
    # auto rows have NO `source` key
    return {"document_name": document_name, "summary": summary,
            "key_data_point": key_data_point, "type": "evidence", "facility": "RA"}


def test_denylist_name_in_handcurated_title_blocks():
    r = cps.evaluate([_hc(document_name="Inspection (Kovalchick)")], [])
    assert len(r["block"]) == 1
    assert not r["warn_auto"]


def test_denylist_name_in_handcurated_source_blocks():
    r = cps.evaluate([_hc(source="EGLE AQD (Diane Kavanaugh Vetort)")], [])
    assert len(r["block"]) == 1


def test_clean_handcurated_row_does_not_block():
    r = cps.evaluate([_hc()], [])
    assert r["block"] == []


def test_name_in_auto_feed_warns_not_blocks():
    # The pre-existing auto exposure (e.g. "Mike Kovalchick" in a scraped title)
    # must WARN, never block the daily regeneration.
    r = cps.evaluate([], [_auto(summary="email requested by Mike Kovalchick")])
    assert r["block"] == []
    assert len(r["warn_auto"]) == 1


def test_heuristic_only_hit_in_handcurated_warns_not_blocks():
    # A novel name-shaped token the denylist does not know is advisory only --
    # it must not hard-block (a false positive there can't be removed).
    r = cps.evaluate([_hc(document_name="Report (Jane Doe)")], [])
    assert r["block"] == []
    assert len(r["warn_handcurated"]) == 1


def test_internal_marker_in_handcurated_blocks():
    r = cps.evaluate([_hc(source="Found in Trisha's FOIA Downloads")], [])
    assert len(r["block"]) == 1
