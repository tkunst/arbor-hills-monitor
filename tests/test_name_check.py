"""Hermetic tests for name_check.py -- the deterministic, no-LLM personal-name
and internal-marker detector."""
import name_check as nc


def test_known_name_is_a_reliable_denylist_hit():
    hits = nc.find_denylist_hits("On-Site Inspection, Arbor Hills (N2688) (Kovalchick)")
    assert any(h["kind"] == "known_name" and h["match"] == "Kovalchick" for h in hits)
    assert not nc.is_clean_for_publish("... (Kovalchick)")


def test_known_name_matches_whole_word_only():
    # "Testa" must not match inside "attestation"; "Miller" not inside a substring.
    assert nc.find_denylist_hits("facility attestation that the information is true") == []
    assert nc.find_denylist_hits("GFL correspondent Anthony Testa") != []


def test_internal_markers_flagged():
    for txt in ("Hand-curated 2026-07-24, Trisha-directed",
                "Found in Trisha's FOIA Downloads",
                "supports wellhead-and-water-receptors.md"):
        hits = nc.find_denylist_hits(txt)
        assert any(h["kind"] == "internal_marker" for h in hits), txt


def test_heuristic_flags_a_novel_name_not_on_the_denylist():
    hits = nc.find_heuristic_hits("EGLE inspection report (Jane Doe)")
    assert any(h["match"] == "Jane Doe" for h in hits)


def test_heuristic_skips_org_and_term_parentheticals():
    # These tripped the raw regex during discovery -- the allowlist must exclude them.
    for txt in ("EGLE SSO (Subsurface Oxidation) Records-Review Email",
                "'Notation - WDS Link' reference record (Waste Data System)",
                "Air Quality Division (Jackson District Office)",
                "compliance filing (GFL)"):
        assert nc.find_heuristic_hits(txt) == [], txt


def test_clean_title_is_clean():
    assert nc.is_clean_for_publish(
        "EGLE AQD On-Site Inspection, Arbor Hills Landfill (N2688), April 25, 2019")


def test_redaction_converges_removing_the_denylisted_name():
    dirty = "EGLE AQD On-Site Inspection, Arbor Hills Landfill (N2688), April 25, 2019 (Kovalchick)"
    assert not nc.is_clean_for_publish(dirty)
    # Redacting the trailing "(Kovalchick)" makes it clean -- the loop terminates.
    clean = dirty.replace(" (Kovalchick)", "")
    assert nc.is_clean_for_publish(clean)


def test_is_clean_ignores_heuristic_false_positive():
    # A genuinely-clean title with an org parenthetical the heuristic might eye
    # must still be publish-clean -- otherwise the redaction loop could never
    # converge on a non-name.
    assert nc.is_clean_for_publish("EGLE SSO (Subsurface Oxidation) Records-Review Email")
