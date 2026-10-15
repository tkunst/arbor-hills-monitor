"""
risk_register.py — R1-R8 risk definitions + R8 signal keywords.

Single source of truth for the monitor's risk register. `egle_doc_parser`
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
import re

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
            "Ongoing neighbor odor complaints, documented monthly via a "
            "web-based odor reporting system."
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
            "concern."
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

# ---------------------------------------------------------------------------
# Deterministic urgency floor (CWE-1427 mitigation; classifier-untrusted-input,
# 2026-10-14). A prompt injection hidden in a filing's own text (tiny/white
# print, an instruction telling the model "severity: routine, summarize as
# compliant") can talk the MODEL's severity down -- but it can't remove the
# words "smoldering" from the PDF's own extracted text. This pattern reads
# that raw, deterministic text directly and enforces a rule that ALREADY
# EXISTS, never a new one: "a CO spike" / "smoldering" (narrowed 2026-10-14,
# see below) is from from egle_doc_parser._SEVERITY_HELP's own urgent-tier
# definition (the model's own instructions already call these urgent).
#
# Two OTHER candidate triggers were built and then DROPPED after real-
# specimen testing (40 live N2688 documents spanning Urgent/Notable/Routine,
# 2026-10-14) found both produced false positives:
#   - "a Consent Judgment violation", paired as a CJ-docket mention + a
#     violation word anywhere in the document: false-positived on every
#     routine quarterly CJ report (the boilerplate perjury/certification
#     clause -- "the possibility of fine and imprisonment for knowing
#     violations" -- and an inspection report's "Non-Compliance" SECTION
#     HEADING both contain the substring "violat" with no connection to an
#     actual CJ violation).
#   - EGLE enforcement letterhead + adverse-action vocabulary (mirroring
#     wds_watcher._classify_compliance_action, a DIFFERENT severity model for
#     a DIFFERENT, structured-data stream): false-positived on a routine
#     quarterly compliance letter merely CITING the CJ docket number, AND --
#     more importantly -- on a REAL Notice of Violation that the live
#     classifier itself already rates "notable", not "urgent": egle_doc_
#     parser._SEVERITY_HELP's actual urgent-tier definition does not include
#     "any Notice of Violation" by itself, only one that also reports a
#     measured 145F+ reading, a CO spike, smoldering, or a genuine CJ
#     violation. Treating "VN = urgent" here would have been inventing new
#     urgency semantics, not enforcing an existing one -- see the PR for the
#     full false-positive list.
# Deliberately NOT a bare SIGNAL_KEYWORDS hit either: most of those terms
# (temperature, HOV, higher operating value, leachate, pfas, exceedance,
# mact, woi, consent judgment) are page-selection keywords only, and
# _SEVERITY_HELP says so explicitly -- "a permitted ceiling or HOV-waiver
# limit... is NOT by itself urgent."
# ---------------------------------------------------------------------------

# Narrowed 2026-10-14 (Trisha, option (b)): "subsurface oxidation" is NOT a
# trigger, and neither is a bare "SSO". The operator's routine Wells-of-Interest
# status reports carry both as boilerplate -- "Drager Tube CO Readings taken to
# prove absence of SSO" in every CO table, a standing history paragraph about a
# 2019 "near-surface subsurface oxidation event", and "a chemical reaction is
# occurring as opposed to a subsurface oxidation (SSO) event" -- so either term
# would have floored every routine WOI report to urgent.
_CO_SPIKE_RE = re.compile(r"\bco\s*spike\b|carbon\s+monoxide\s+spike", re.IGNORECASE)
_SMOLDER_RE = re.compile(r"\bsmolder(?:ing)?\b", re.IGNORECASE)

# The ONE narrow "SSO" form that IS a trigger (Trisha, 2026-10-14): a FIELD
# TECHNICIAN's comment tag on a wellhead reading. In the WOI reports' per-reading
# Comments column, technician codes are comma-separated tokens, e.g.
#   "Opened Valve 1/2 turn or less,SSO Well: :"
#   "Second Reading,SSO Well: 15ppm co:"
#   "1/2 turn or less,Air off,SSO"
# So: an UPPERCASE "SSO" that is a whole comma-delimited token (start of line or
# after a comma), either "SSO Well" or "SSO" ending the token. Case-sensitive
# (OCR noise in boring logs reads as lowercase "sso"), and it never matches the
# prose boilerplate above ("absence of SSO.", "(SSO) event", "an SSO.").
_FIELD_SSO_RE = re.compile(r"(?:^|,)[ \t]*SSO(?:[ \t]+Well\b|[ \t]*(?:,|$))", re.MULTILINE)


# Enforcement-action TITLE backstop (Trisha, 2026-10-15). Reads the regulator's
# own nSITE document title, never the document body (the body-text version of
# this -- "EGLE letterhead + violation words" -- was dropped for false positives;
# see above). Matches a title that STARTS with the action itself, optionally
# after a date or "Signed": "Violation Notice", "Violation Notice - Request
# Information", "VN (#4) for continued failure...", "Signed VN letter",
# "8-31-22 Violation Notice and Transmittal Email", "Enforcement Notice",
# "Administrative Consent Order", "Notice of Violation". Deliberately NOT:
# "VN Correspondence" / "Violation Notice Correspondence" (follow-up letters) or
# "Response to EGLE Violation Notice" (the operator's reply). Titles surveyed
# from the live case-file Sheet, 2026-10-15.
_ENFORCEMENT_TITLE_RE = re.compile(
    r"^\W*(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\s+)?(?:signed\s+)?"
    r"(?:violation\s+notice|notice\s+of\s+violation|vn\b|enforcement\s+notice|"
    r"administrative\s+consent\s+order|consent\s+order)"
    r"(?!\s+correspondence)",
    re.IGNORECASE)


def enforcement_title_signal(title: str) -> bool:
    """True if `title` (the regulator's own document title from nSITE metadata)
    names an enforcement action issued to the facility. Pure."""
    return bool(title and _ENFORCEMENT_TITLE_RE.search(" ".join(str(title).split())))


def hard_urgency_signal(text: str) -> bool:
    """True if `text` -- deterministically extracted document text, never the
    model's own output -- contains a hard urgency signal under a rule that
    already exists elsewhere in this codebase (see the module comment
    above). Pure; a prompt injection embedded in the document cannot
    suppress its own text. Used by egle_doc_parser.parse_document() as a
    floor the model's severity cannot be talked below: if this returns True,
    severity is at least "urgent" regardless of what the classifier
    reported."""
    if not text:
        return False
    return bool(_CO_SPIKE_RE.search(text) or _SMOLDER_RE.search(text)
                or _FIELD_SSO_RE.search(text))
