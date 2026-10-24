"""EPA Method 19 F-factors are not Fahrenheit readings (coder:temp-regex-ffactor).

The 2026-10-16 N1504 ICT sulfur test report (nSITE doc 4935451998137733993)
carries an AtmAA lab sheet "Calculated values for Specific Volume, BTU, and F
(factor)". OCR prints the subscripted labels as "Fy", "F,,", "F,", so the free-text
regex read "9340 F" (the value, then the NEXT label's F) as 9340°F and raised a
"9340F above the 250F ceiling" owner review flag.
"""
import pytest

import email_alerts as ea
import review_flags as rf

# The OCR line as the coder:temp-regex-ffactor block quotes it.
QUEUE_OCR_LINE = "Fy (factor) 9340 F,, (factor) 11356 F, (factor) 1729"

# The same lab-sheet block as a local ocrmypdf run on the real PDF prints it
# (one value per line), both sampling-run sheets.
SPECIMEN_OCR_BLOCKS = [
    "(LHV) \nF, (factor) \n9364 \nF,, (factor) \n11393 \nF, (factor) \n1731 \n"
    "Compressibility Factor (@60F, 14.696 psia) \n0.9971 \n",
    "(LHV) \nFy (factor) \n9340 \nF,, (factor) \n11356 \nF, (factor) \n1729 \n"
    "Compressibility Factor (@60F, 14.696 psia) \n0.9971 \n",
]


def _values(text):
    return [int(m.group(1).replace(",", "")) for m in ea.temperature_matches(text)]


@pytest.mark.parametrize("text", [
    QUEUE_OCR_LINE,
    *SPECIMEN_OCR_BLOCKS,
    "F-factor 9,340 F",
    "Fd factor = 9340 F",
    "9340 F-factor",
    "9340 F\u2013factor",
    "9340 F (factor)",
    "9340 F (Factor)",
    "F d factor 9340 F w factor 11356",
])
def test_f_factor_values_are_not_temperatures(text):
    # Each fixture is a real failing input: the bare regex reads a >1,000 value.
    assert [m for m in ea._TEMP_RE.finditer(text) if int(m.group(1).replace(",", "")) > 1000]
    assert [v for v in _values(text) if v > 1000] == []
    assert rf.implausible_text_temps(text, 250) == []


@pytest.mark.parametrize("text", ["F-factor 9,340", "Fd factor = 9340", "Fc 1729",
                                  "Fd (factor) 9340 Fw (factor) 11356 Fc (factor) 1729"])
def test_f_factor_forms_without_a_trailing_f_never_matched(text):
    # The queue's other forms: no F after the number, so neither regex reads them.
    assert list(ea._TEMP_RE.finditer(text)) == []
    assert _values(text) == []


def test_the_bare_regex_still_matches_the_specimen_line():
    # The fixture really is the failing input: the raw pattern reads 9340.
    assert "9340" in [m.group(1) for m in ea._TEMP_RE.finditer(QUEUE_OCR_LINE)]


@pytest.mark.parametrize("text,expected", [
    ("180F", 180),
    ("180 F", 180),
    ("180°F", 180),
    ("180 degrees F", 180),
    ("180 deg F", 180),
    ("Well AHW1 reached 145 F.", 145),
    ("145 F. Factors include cover depth.", 145),
    ("145 F. Factor in the cover depth.", 145),
    ("Temperature correction factor 1.02 applied; well read 150 F", 150),
    ("Method 19 F-factor used for stack flow. Wellhead 150°F.", 150),
    ("131°F,145°F", 145),
    # Ordinary wording next to a reading is never an F-factor label (security
    # review round 1, low: these were suppressed by the first draft's looser label).
    ("Well AHW12 reached 180 F factor of safety", 180),
    ("180°F, factor in the cover depth", 180),
    ("Well read 180 F (factor 2)", 180),
    ("Well read 180 F\nfactor in the cover depth", 180),
    ("Fan factor 180 F", 180),
    ("a few factor 180°F", 180),
    ("Fill factor 180 F", 180),
    # Code review round 1: real readings beside "factor" wording, and F-factor
    # labels beside a wellhead-range value (the guard never applies below 1,000).
    ("Wellhead temperature 150 F, factor in cover depth", 150),
    ("Well AHW1 read 150 F - factor of safety", 150),
    ("Temperatures reached 150 F (factor 2 above baseline)", 150),
    ("AHW12 150F (factor: settlement)", 150),
    ("150°F, factor", 150),
    ("150 F Factor", 150),
    ("Fd factor\n150 F", 150),
    ("Feb factor 150 F", 150),
    ("fan factor = 150 F", 150),
    ("Fit factor: 150 F", 150),
    ("Leaf factor:                     150 F", 150),
    ("Fd (factor) 150 F", 150),
    ("150 F (factor)", 150),
    ("150 F-factor", 150),
])
def test_true_positives_are_kept(text, expected):
    assert ea._max_temperature_f(text) == expected


@pytest.mark.parametrize("label", ["F (factor) ", "Fd factor = ", "F-factor ", "F,, (factor) "])
def test_guard_never_touches_a_value_below_the_f_factor_floor(label):
    # Every wellhead-range value reads exactly as the bare regex reads it.
    for v in range(50, 1000, 7):
        for text in (f"{label}{v} F", f"{v} F (factor)", f"{v} F-factor"):
            assert _values(text) == [int(x.group(1)) for x in ea._TEMP_RE.finditer(text)]


def test_thousands_separator_still_read_whole():
    assert _values("2,145°F") == [2145]
    assert rf.implausible_text_temps("Flare 2,145 F", 250) == []  # flare context
    assert rf.implausible_text_temps("Well AHW1 reached 1,500 F.", 250) == [1500]


def test_standard_condition_temperatures_next_to_f_factor_text_survive():
    # "60°F). The F-factor ..." is a real (standard-condition) temperature.
    text = "at standard conditions (68°F or 60°F). The F-factor is 9340."
    assert _values(text) == [68, 60]
    for block in SPECIMEN_OCR_BLOCKS:
        assert _values(block) == [60]
