"""
egle_doc_parser.py — THE reusable document-parsing module (the Decode base).

Takes a PDF + metadata + a risk register; returns a ParsedDoc. Domain-agnostic:
the risk register is passed in, never hardcoded, so Decode can reuse this with a
different register (diagnostic criteria, eligibility standards) instead of
R1-R8.

Pipeline:
  1. classify() — PyMuPDF text-layer detection (needs_ocr vs has_text).
     Copied verbatim from zotero-tools/zotero-find-ocr-needed.py.
  2. If needs_ocr -> ocrmypdf --skip-text, in place (searchable PDF).
     Ported from zotero-tools/zotero-ocr-batch.py.
  3. Extract text. Docs over the page threshold use targeted keyword-window
     extraction (only pages with signal keywords + the cover page) instead of
     full text — saves tokens, produces a better key_data_point. Robust for
     Decode too (evaluation reports are also large).
  4. Classify with Claude -> 5 model-derived fields (summary, key_data_point,
     doc_type, risks, severity), validated by a structured-output schema.
  5. Assemble the full 8-field ParsedDoc (adds full_text, ocr_applied,
     page_count locally — these are NOT model-derived).

The structured-output schema is exactly the 5 model fields. ParsedDoc is 8
fields. Two shapes, deliberately.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Literal, Optional, get_args

import fitz  # pymupdf

# ---------------------------------------------------------------------------
# Measurement metric vocabulary (ADR 034) — the SINGLE SOURCE OF TRUTH
# ---------------------------------------------------------------------------
# Every reading extracted into the Measurements tab carries a `metric` label.
# Historically only five values existed (temperature / carbon_monoxide / oxygen /
# methane / other), so every OTHER substance — hydrogen sulfide, PFAS, 14 metals,
# NPDES wastewater parameters, NOx/SO2, operational events — collapsed into a
# single `other` bucket (~5,100 rows, ~45% of the tab), un-chartable and
# un-comparable against permit limits. ADR 034 expands the vocabulary to the
# reviewed ~52-substance taxonomy (Trisha greenlit 2026-08-25).
#
# `MetricLiteral` IS THE ONE SOURCE OF TRUTH. The document classifier's
# `Measurement` model annotates its `metric` field with it directly, and the
# one-shot `other`-bucket backfill (backfill_metric_taxonomy.py) reuses the SAME
# type via `METRIC_VALUES` (derived from it below with typing.get_args) — so the
# two paths can never fork the vocabulary. The Literal is exactly what the
# structured-output schema forces the model to emit.
#
# Classification principle (from the reviewed draft): the UNIT does NOT identify
# the substance (`%` is used for methane, O2, CO2, gas composition, and
# combustion efficiency alike). Classify by note text + context via the MODEL —
# there is deliberately NO keyword ruleset (the NMOC-vs-"non-methane" substring
# trap proved keyword rules brittle). The named traps are handled with brief
# model guidance in _MEASUREMENTS_HELP, not code branches.
MetricLiteral = Literal[
    # --- Four original first-class metrics — PRESERVED EXACTLY (meaning unchanged).
    #     `methane` = per-WELL methane readings; `temperature` = the gas-well
    #     temperature series. Facility/adjusted variants get their own *_secondary
    #     metrics below so these existing series are never corrupted.
    "temperature",
    "carbon_monoxide",
    "oxygen",
    "methane",
    # --- Landfill gas / air quality
    "hydrogen_sulfide",     # H2S (already produced as text by GFL air + Ridge Wood)
    "methane_secondary",    # facility/adjusted CH4: FGPROJECT23, EURNGPLANT, "CH4 adjusted", gas-composition
    "carbon_dioxide",
    "hydrogen_gas",         # H2 (Drager tube, RNG-plant hydrogen)
    "nmoc_voc",             # NMOC / non-methane organic compounds / VOC (never `methane`)
    "nitrogen_oxides",      # NOx
    "sulfur_dioxide",       # SO2
    "hydrogen_chloride",    # HCl
    "particulate_matter",
    "surface_emissions",    # SEM penetration/exceedance readings (ppm)
    "trs",                  # total reduced sulfur
    "combustion_efficiency",
    # --- Water quality: priority contaminants (own permit limits)
    "pfas",                 # every PFAS congener + the "PFAS" aggregate
    "arsenic",
    "mercury",
    "selenium",
    # --- Water quality: other metals & inorganics
    "nickel",
    "chromium",
    "lead",                 # the METAL; "lead" also appears inside "leachate" (trap)
    "cadmium",
    "zinc",
    "copper",
    "barium",
    "boron",
    "antimony",
    "cyanide",
    # --- Water quality: organics, biochemical, nutrient
    "tss",                  # total suspended solids
    "bod",                  # CBOD5 / BOD5
    "cod",                  # chemical oxygen demand
    "toc",                  # total organic carbon
    "ammonia_nitrogen",     # ammonia / nitrate / nitrite / TKN / Total Nitrogen / Kjeldahl (kept LUMPED, ruling 2)
    "phosphorus",
    "btex_chlorinated_voc", # BETX mixture + toluene, xylene, trans-1,2-DCE, Hexane (benzene split out below)
    "benzene",              # own MCL → own metric (ruling 1), NOT btex_chlorinated_voc
    "pahs",                 # benzo(a)pyrene etc.
    "ecoli_coliform",
    # --- Water quality: physical parameters
    "ph",
    "dissolved_oxygen",
    "flow_wastewater",
    "chloride",
    "fluoride",
    "hardness",
    "alkalinity",
    "conductivity",
    "tds",                  # total dissolved solids
    "major_ions",           # calcium / magnesium / other major ions w/o permit limit (ruling 3)
    # --- Temperature / ambient (NON-well readings only — effluent/pond/ambient)
    "temperature_secondary",
    # --- Operational / non-chemical readings
    "event_status",         # operational events, flags, qualitative states
    "well_operational",     # well-impairment counts
    "operational_capacity", # rated/designed capacities, LandGEM output, RNG plant capacity
    "pressure_vacuum",      # wellfield vacuum, forcemain pressure, backpressure
    "wind_odor",            # wind/odor-complaint context readings
    "exceedances_count",    # aggregated SEM exceedance counts
    "qa_sample",            # DUPLICATE / FIELD BLANK / TOX lab-QA rows
    # --- Real fallback: only genuinely-unplaceable readings land here.
    "other",
]

# The runtime tuple form, derived from the Literal above so there is exactly ONE
# place the vocabulary is spelled out. The backfill imports METRIC_VALUES to
# validate/report; Pydantic uses MetricLiteral for the structured-output enum.
METRIC_VALUES: tuple[str, ...] = get_args(MetricLiteral)

# ---------------------------------------------------------------------------
# Output contract — the Decode reuse surface
# ---------------------------------------------------------------------------


@dataclass
class ParsedDoc:
    summary: str
    key_data_point: str  # one-line extractable claim
    doc_type: Literal["evidence", "procedural", "opinion"]
    risks: list[str]  # e.g. ["R4", "R8"]
    severity: Literal["routine", "notable", "urgent"]
    full_text: str
    ocr_applied: bool
    page_count: int
    # Structured readings extracted from the document. Each is a dict with keys:
    #   metric  : one of METRIC_VALUES (the ADR-034 ~52-substance vocabulary —
    #             temperature / carbon_monoxide / oxygen / methane are the four
    #             first-class metrics; everything else is a named substance/event
    #             metric; `other` is the genuine-fallback bucket)
    #   value   : float
    #   unit    : str ("F", "ppm", "percent", ...)
    #   basis   : "measured" | "permitted_limit" | "unknown"  <-- CRITICAL
    #   well_id : str | None
    #   as_of_date : ISO date str | None  (the reading's own date, if stated)
    #   note    : str | None
    # `basis` distinguishes an actual reading from a permitted ceiling / HOV
    # waiver limit. Conflating "180F measured" with "180F permitted ceiling"
    # discredits the whole artifact, so this flag is load-bearing. These atomic
    # readings make per-well time series derivable downstream (by aggregation)
    # WITHOUT reprocessing the source documents.
    measurements: list[dict] = field(default_factory=list)
    # Structured compliance deadlines / dated obligations the document imposes or
    # reports (ADR 025). Each is a dict with keys:
    #   item_description               : what must be done (required — the anchor)
    #   due_date                       : ISO date the obligation is due, if stated
    #   extension_due_date             : revised due date, if the doc grants one
    #   actual_completion_date         : when it was actually met, if stated
    #   compelled_by                   : the order/permit/document imposing it
    #   compliance_doc_effective_date  : effective date of that compelling document
    # Generic across regulatory documents (not landfill-specific), same design as
    # `measurements`: the model extracts them, the caller decides what to do with
    # them (here: the Compliance Deadlines tab). Empty list when the doc imposes
    # no dated obligation.
    deadlines: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Step 1: text-layer detection (verbatim from zotero-find-ocr-needed.py)
# ---------------------------------------------------------------------------


def classify(path: str):
    """Return (verdict, npages, chars_per_page). verdict in
    needs_ocr/likely/has_text/empty/error."""
    try:
        doc = fitz.open(path)
    except Exception:
        return ("error", 0, 0)
    n = len(doc)
    if n == 0:
        doc.close()
        return ("empty", 0, 0)
    check = min(n, 12)
    total_chars = 0
    text_pages = 0
    imageonly_pages = 0
    for i in range(check):
        pg = doc[i]
        t = len(pg.get_text().strip())
        imgs = len(pg.get_images(full=False))
        total_chars += t
        if t >= 100:
            text_pages += 1
        if t < 20 and imgs >= 1:
            imageonly_pages += 1
    doc.close()
    cpp = total_chars / check
    if text_pages == 0 and imageonly_pages >= 1:
        return ("needs_ocr", n, cpp)  # no text anywhere, pages are images
    if cpp < 40 and imageonly_pages >= check * 0.5:
        return ("likely", n, cpp)  # mostly image pages, scraps of text
    if text_pages == 0 and total_chars < 30:
        return ("empty", n, cpp)  # no text, no images -> blank/odd
    return ("has_text", n, cpp)


# ---------------------------------------------------------------------------
# Step 2: OCR in place (ported from zotero-ocr-batch.py)
# ---------------------------------------------------------------------------


def _ocrmypdf_bin() -> str:
    """Resolve the ocrmypdf binary. Override with OCRMYPDF_BIN."""
    return (
        os.environ.get("OCRMYPDF_BIN")
        or shutil.which("ocrmypdf")
        or "/opt/homebrew/bin/ocrmypdf"
    )


def ocr_in_place(pdf_path: str, timeout: int = 600) -> bool:
    """Run `ocrmypdf --skip-text` and replace pdf_path with the searchable
    version. Returns True on success. Raises on failure so the caller can
    decide whether to proceed with the un-OCR'd text."""
    binpath = _ocrmypdf_bin()
    env = dict(os.environ, PATH="/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    tmp = pdf_path + ".ocr_tmp.pdf"
    try:
        r = subprocess.run(
            [binpath, "--skip-text", "-l", "eng", "--output-type", "pdf", pdf_path, tmp],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        if r.returncode != 0 or not os.path.exists(tmp):
            last = (r.stderr or "").strip().split("\n")[-1][:200]
            raise RuntimeError(f"ocrmypdf failed (rc={r.returncode}): {last}")
        os.replace(tmp, pdf_path)
        return True
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------
# Step 3: text extraction (full, or keyword-windowed for large docs)
# ---------------------------------------------------------------------------


def extract_text_for_classification(
    doc: "fitz.Document",
    keywords: list[str],
    page_threshold: int = 30,
    max_keyword_pages: int = 10,
) -> tuple[str, bool]:
    """Return (text, windowed). For docs <= page_threshold, returns full text.
    For larger docs, returns only the cover/summary page plus up to
    max_keyword_pages pages that contain a signal keyword (case-insensitive
    substring) — with page markers and a note that the full report is in Drive.

    Pure function (takes an open fitz doc), so it's directly unit-testable.
    """
    n = len(doc)
    if n <= page_threshold:
        return ("\n".join(doc[i].get_text() for i in range(n)), False)

    kw = [k.lower() for k in keywords]
    selected: list[int] = [0]  # always include the cover / summary page
    for i in range(n):
        if i == 0:
            continue
        if len(selected) - 1 >= max_keyword_pages:
            break
        page_text = doc[i].get_text().lower()
        if any(k in page_text for k in kw):
            selected.append(i)

    parts = [
        f"[LARGE DOCUMENT: {n} pages total. Showing the cover page plus "
        f"{len(selected) - 1} keyword-matched pages. Full report is in the "
        f"Google Drive folder.]"
    ]
    for i in selected:
        parts.append(f"\n--- page {i + 1} ---\n{doc[i].get_text()}")
    return ("\n".join(parts), True)


# ---------------------------------------------------------------------------
# Step 4: classification with Claude (structured output, 5 fields)
# ---------------------------------------------------------------------------

_DOC_TYPE_HELP = (
    "evidence: factual data, measurements, regulatory violations, filed reports "
    "with specific readings (temperature, CO levels, PFAS, violation counts) — "
    "anything a lawyer or commissioner could cite as a factual finding. "
    "procedural: meeting notices, comment deadlines, permit applications, "
    "scheduling items, acknowledgment letters. "
    "opinion: stakeholder statements, advocacy positions, value judgments."
)

_SEVERITY_HELP = (
    "urgent: an actual MEASURED temperature reading at or above 145F, a CO "
    "spike, smoldering / subsurface oxidation language, or a Consent Judgment "
    "violation. NOTE: a permitted ceiling or HOV-waiver limit of 180F is NOT by "
    "itself urgent — only a measured reading is. "
    "notable: substantive new evidence that isn't an emergency (including a new "
    "HOV waiver request, or measured temps in the 131-145F range). "
    "routine: everything else."
)

# Shared metric-classification guidance (ADR 034). Referenced by BOTH the
# document classifier (_MEASUREMENTS_HELP, below) and the one-note classifier
# (classify_note_metric, used by the `other`-bucket backfill) so the two paths
# apply IDENTICAL rules — one source of truth for the traps, never a fork.
_METRIC_CLASSIFY_GUIDANCE = (
    "Classify by what the note/context says the substance IS — the UNIT does not "
    "identify it ('%' is used for methane, O2, CO2, gas composition, and "
    "combustion efficiency alike). The four gas-well first-class metrics are "
    "temperature, carbon_monoxide, oxygen, methane (methane = a per-WELL CH4 "
    "reading). Named-substance metrics exist for the common pollutants — e.g. "
    "hydrogen_sulfide, pfas, arsenic, mercury, tss, bod, ammonia_nitrogen, ph, "
    "phosphorus — plus operational buckets (event_status, operational_capacity, "
    "pressure_vacuum). Use 'other' ONLY when the reading genuinely fits no named "
    "metric. Known traps to get right: NMOC / 'non-methane organic compounds' / "
    "VOC -> nmoc_voc, NEVER methane. Facility or adjusted methane (e.g. "
    "'FGPROJECT23', 'EURNGPLANT', 'CH4 adjusted', 'CH4 gas composition') -> "
    "methane_secondary, NOT methane (methane is reserved for per-well readings). "
    "Non-well temperature (effluent, pond, ambient air) -> temperature_secondary, "
    "NOT temperature. benzene has its own metric 'benzene' (own MCL) — do NOT put "
    "it in btex_chlorinated_voc. 'lead' the metal -> lead, but the word 'lead' "
    "inside 'leachate' is not the metal. calcium / magnesium / other major ions "
    "with no permit limit -> major_ions."
)

_MEASUREMENTS_HELP = (
    "Extract every quantitative reading the document states, as structured "
    "measurements. For EACH reading set:\n"
    "  - metric: the single best-fitting value from the allowed metric list. "
    + _METRIC_CLASSIFY_GUIDANCE +
    " Whatever the metric, ALSO put the substance/parameter name in 'note'.\n"
    "  - value: the number\n"
    "  - unit: F, ppm, percent, etc.\n"
    "  - basis: 'measured' for an actual observed reading; 'permitted_limit' "
    "for a permitted ceiling, MACT limit, or HOV-waiver-requested value; "
    "'unknown' if you cannot tell. THIS FLAG IS CRITICAL — never label a "
    "permitted ceiling as measured or vice versa.\n"
    "  - well_id: the well/probe identifier if given (e.g. AHW272R4), else null\n"
    "  - as_of_date: the reading's own date (ISO YYYY-MM-DD) if stated, else null\n"
    "  - note: short context if helpful\n"
    "If the document states no quantitative readings, return an empty list."
)


def _build_system_prompt(risk_register: list[dict]) -> str:
    risk_lines = "\n".join(
        f"- {r['id']} ({r['name']}): {r['description']}" for r in risk_register
    )
    return (
        "You classify environmental regulatory documents for a community "
        "advocacy group monitoring the Arbor Hills Landfill (SRN N2688).\n\n"
        "For each document, return:\n"
        "- summary: 2-3 plain-English sentences.\n"
        "- key_data_point: one line extracting the single most citable specific "
        "claim (e.g. \"180F - 35F above MACT limit, Well AHW272R4, Feb 2025\"). "
        "If the document has no specific factual reading, say so briefly.\n"
        f"- doc_type: one of evidence/procedural/opinion. {_DOC_TYPE_HELP}\n"
        "- risks: the risk IDs this document speaks to (may be several, may be "
        "empty). Use ONLY IDs from this register:\n"
        f"{risk_lines}\n"
        f"- severity: one of routine/notable/urgent. {_SEVERITY_HELP}\n"
        f"- measurements: {_MEASUREMENTS_HELP}\n"
        "- deadlines: any compliance deadlines or dated obligations the document "
        "imposes or reports on (a corrective-action due date, a public-comment or "
        "response deadline, a permit-condition deadline, etc.). For each, give "
        "item_description (what must be done) and, ONLY where the document states "
        "them, due_date, extension_due_date, actual_completion_date, compelled_by "
        "(the order/permit/notice imposing it), and compliance_doc_effective_date. "
        "Use ISO dates (YYYY-MM-DD). Return an empty list if the document imposes "
        "no dated obligation. Do not invent dates that aren't stated.\n\n"
        "Be precise and conservative: only tag a risk the document actually "
        "addresses, and only mark urgent if an urgent trigger is genuinely "
        "present."
    )


def _classify_with_claude(
    text: str,
    metadata: dict,
    risk_register: list[dict],
    model: str,
    client=None,
    max_tokens: int = 8192,
) -> dict:
    """Call Claude and return a dict with the 5 model-derived fields. Isolated
    so tests can monkeypatch it without an API key. Uses structured output so
    the model is forced to return a valid shape."""
    import anthropic
    from pydantic import BaseModel

    class Measurement(BaseModel):
        metric: MetricLiteral  # the ADR-034 vocabulary — see METRIC_VALUES (single source of truth)
        value: float
        unit: str
        basis: Literal["measured", "permitted_limit", "unknown"]
        well_id: Optional[str] = None
        as_of_date: Optional[str] = None
        note: Optional[str] = None

    class Deadline(BaseModel):
        item_description: str
        due_date: Optional[str] = None
        extension_due_date: Optional[str] = None
        actual_completion_date: Optional[str] = None
        compelled_by: Optional[str] = None
        compliance_doc_effective_date: Optional[str] = None

    class Classification(BaseModel):
        summary: str
        key_data_point: str
        doc_type: Literal["evidence", "procedural", "opinion"]
        # Generic list[str] keeps the schema domain-agnostic; we validate the
        # IDs against the passed-in register in parse_document().
        risks: list[str]
        severity: Literal["routine", "notable", "urgent"]
        measurements: list[Measurement] = []
        deadlines: list[Deadline] = []

    if client is None:
        client = anthropic.Anthropic()

    meta_line = (
        f"Document name: {metadata.get('document_name', '(unknown)')}\n"
        f"Date filed: {metadata.get('date_filed', '(unknown)')}\n"
        f"nSITE type: {metadata.get('type_name', '(unknown)')}\n"
    )
    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=[
            {
                "type": "text",
                "text": _build_system_prompt(risk_register),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"{meta_line}\n--- DOCUMENT TEXT ---\n{text}",
            }
        ],
        output_format=Classification,
    )
    parsed = response.parsed_output
    if parsed is None:
        stop = getattr(response, "stop_reason", "?")
        if stop == "max_tokens":
            # Output hit the cap before the JSON closed — measurements[] on a
            # large multi-well report can be long. Raise classification_max_tokens
            # in config rather than letting this look like a generic failure.
            raise RuntimeError(
                f"Classification truncated at max_tokens={max_tokens} "
                f"(stop_reason=max_tokens) — raise classification_max_tokens."
            )
        raise RuntimeError(f"Classification returned no parsed output (stop_reason={stop})")
    return parsed.model_dump()


# ---------------------------------------------------------------------------
# Single-note metric classifier (ADR 034) — reused by the `other`-bucket backfill
# ---------------------------------------------------------------------------

_NOTE_CLASSIFIER_INSTRUCTIONS = (
    "You are labeling ONE environmental-measurement reading from the Arbor Hills "
    "Landfill Measurements dataset. Given the reading's free-text note (and its "
    "unit as weak context only), choose the single best-fitting metric from the "
    "allowed list. " + _METRIC_CLASSIFY_GUIDANCE +
    " If the note genuinely fits no named metric, choose 'other' — do not guess a "
    "specific substance the note does not support."
)


def classify_note_metric(
    note: str,
    unit: Optional[str] = None,
    *,
    model: str,
    client=None,
    max_tokens: int = 256,
) -> str:
    """Classify a SINGLE Measurements note into one metric from the ADR-034
    vocabulary, using the same model + structured-output mechanism + shared
    guidance (_METRIC_CLASSIFY_GUIDANCE) as the document classifier — one brain,
    one vocabulary (MetricLiteral), never a fork. Used by the one-shot
    `other`-bucket backfill (backfill_metric_taxonomy.py).

    Returns a metric string from METRIC_VALUES. A truncated/empty model response
    (parsed_output is None) fails safe to 'other' — leave the row as-is; 'other'
    never corrupts data. A genuine API error (network, rate-limit, auth) is NOT
    swallowed: it propagates so a real outage fails loud rather than silently
    turning every note into 'other'; the backfill's per-note cache means a re-run
    resumes where it stopped."""
    import anthropic
    from pydantic import BaseModel

    class NoteMetric(BaseModel):
        metric: MetricLiteral  # the ADR-034 vocabulary (single source of truth)

    if client is None:
        client = anthropic.Anthropic()

    content = f"Note: {note!r}\nUnit: {unit!r}"
    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": f"{_NOTE_CLASSIFIER_INSTRUCTIONS}\n\n{content}"}],
        output_format=NoteMetric,
    )
    parsed = response.parsed_output
    if parsed is None:
        return "other"
    return parsed.metric


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_document(
    pdf_path: str,
    metadata: dict,
    risk_register: list[dict],
    *,
    model: str = "claude-haiku-4-5",
    signal_keywords: Optional[list[str]] = None,
    page_threshold: int = 30,
    max_keyword_pages: int = 10,
    max_tokens: int = 8192,
    client=None,
) -> ParsedDoc:
    """Parse one PDF end-to-end. OCRs in place if needed, extracts text
    (windowing large docs), classifies with Claude, and returns a ParsedDoc."""
    if signal_keywords is None:
        signal_keywords = []

    verdict, _, _ = classify(pdf_path)
    ocr_applied = False
    if verdict in ("needs_ocr", "likely"):
        ocr_applied = ocr_in_place(pdf_path)

    doc = fitz.open(pdf_path)
    try:
        page_count = len(doc)
        text, _windowed = extract_text_for_classification(
            doc, signal_keywords, page_threshold, max_keyword_pages
        )
    finally:
        doc.close()

    fields = _classify_with_claude(
        text, metadata, risk_register, model, client=client, max_tokens=max_tokens
    )

    valid_ids = {r["id"] for r in risk_register}
    risks = [r for r in fields.get("risks", []) if r in valid_ids]

    return ParsedDoc(
        summary=fields["summary"],
        key_data_point=fields["key_data_point"],
        doc_type=fields["doc_type"],
        risks=risks,
        severity=fields["severity"],
        full_text=text,
        ocr_applied=ocr_applied,
        page_count=page_count,
        measurements=fields.get("measurements", []) or [],
        deadlines=fields.get("deadlines", []) or [],
    )
