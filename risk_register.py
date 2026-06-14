"""
risk_register.py — R1-R8 risk definitions + R8 signal keywords.

Single source of truth for the Conservancy's risk register. `egle_doc_parser`
takes a risk register as a parameter (it does NOT hardcode this), so the parser
stays domain-agnostic and reusable by Decode with a different register. This
module is the Arbor-Hills-specific instance.

A "risk" is a dict: {id, name, description}. The parser shows these to Claude so
it can tag each document with the risks it speaks to.

KEYWORDS drive the large-document page-windowing branch in the parser: for docs
over the page threshold, only pages containing one of these terms (plus the
cover/summary page) are sent to Claude. They are deliberately R8-heavy because
the overheating/ETLF story is the new, evidence-dense one buried in the giant
WOI Status Reports.
"""

RISK_REGISTER = [
    {
        "id": "R1",
        "name": "Expansion eligibility",
        "description": (
            "GFL needs Arbor Hills included in the next Washtenaw County "
            "Materials Management Plan to expand; the MMPC drafts that plan. "
            "Tracked via MMPC meetings and the Board of Commissioners."
        ),
    },
    {
        "id": "R2",
        "name": "Violations history",
        "description": (
            "Documented pattern of permit violations used to argue the site "
            "can't be trusted with more capacity. Tracked via FOIA and EGLE "
            "inspector contact."
        ),
    },
    {
        "id": "R3",
        "name": "Odor nuisance",
        "description": (
            "Ongoing neighbor odor complaints, documented monthly via the "
            "Conservancy's web-based odor reporting system."
        ),
    },
    {
        "id": "R4",
        "name": "Air quality",
        "description": (
            "Perimeter air monitoring including the Ridge Wood monitor. Two "
            "active community air monitors; monthly charts published."
        ),
    },
    {
        "id": "R5",
        "name": "Water quality",
        "description": (
            "Potential leachate / groundwater contamination. Named as a "
            "concern; no dedicated tracker yet."
        ),
    },
    {
        "id": "R6",
        "name": "Environmental justice",
        "description": (
            "Northville and nearby communities bear the pollution load while "
            "tipping fees benefit others. Public-comment messaging."
        ),
    },
    {
        "id": "R7",
        "name": "Truck traffic",
        "description": (
            "Heavy truck impacts near schools and residential streets. "
            "'No Truck Zone' on Six Mile Rd; Northville Township working group."
        ),
    },
    {
        "id": "R8",
        "name": "Overheating / ETLF (Elevated Temperature Landfill)",
        "description": (
            "Subsurface oxidation inside the waste mass, smoldering risk, and "
            "toxic air emissions from combustion. 16+ wells on active HOV "
            "(Higher Operating Value) waivers since 2019, one at 180F (35F "
            "above the MACT limit). Evidence lives in the semi-annual WOI "
            "(Wells of Interest) Status Reports and gas-extraction exceedance "
            "reports with temperature columns. Consent Judgment CJ No. "
            "2020-0593-CE."
        ),
    },
]

# Terms that mark a page as worth sending to Claude in the large-doc branch.
# Lowercased substring match against page text.
SIGNAL_KEYWORDS = [
    "hov",
    "higher operating value",
    "woi",
    "wells of interest",
    "well of interest",
    "subsurface oxidation",
    "smolder",
    "etlf",
    "elevated temperature",
    "carbon monoxide",
    "co spike",
    "exceedance",
    "temperature",
    "consent judgment",
    "2020-0593-ce",
    "mact",
    "leachate",
    "pfas",
]

# Convenience: id -> name, used by sheet_writer for the "Evidence by Risk" tab.
RISK_NAMES = {r["id"]: r["name"] for r in RISK_REGISTER}
VALID_RISK_IDS = set(RISK_NAMES)
