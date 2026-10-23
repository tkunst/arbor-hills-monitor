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
    "F-factor 9,340",
    "F-factor 9,340 F",
    "Fd factor = 9340",
    "Fd factor = 9340 F",
    "Fd (factor) 9340 Fw (factor) 11356 Fc (factor) 1729",
    "Fc 1729",
    "9340 F-factor",
    "9340 F (factor)",
])
def test_f_factor_values_are_not_temperatures(text):
    assert [v for v in _values(text) if v > 1000] == []
    assert rf.implausible_text_temps(text, 250) == []


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
])
def test_true_positives_are_kept(text, expected):
    assert ea._max_temperature_f(text) == expected


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
