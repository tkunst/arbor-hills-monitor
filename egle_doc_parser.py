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
from typing import Literal, Optional

import fitz  # pymupdf

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
    #   metric  : "temperature" | "carbon_monoxide" | "oxygen" | "other"
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

_MEASUREMENTS_HELP = (
    "Extract every quantitative reading the document states, as structured "
    "measurements. For EACH reading set:\n"
    "  - metric: temperature / carbon_monoxide / oxygen / other. For any other "
    "substance (e.g. benzene, hydrogen sulfide) use 'other' and put the "
    "substance name in 'note'.\n"
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
        f"- measurements: {_MEASUREMENTS_HELP}\n\n"
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
        metric: Literal["temperature", "carbon_monoxide", "oxygen", "other"]
        value: float
        unit: str
        basis: Literal["measured", "permitted_limit", "unknown"]
        well_id: Optional[str] = None
        as_of_date: Optional[str] = None
        note: Optional[str] = None

    class Classification(BaseModel):
        summary: str
        key_data_point: str
        doc_type: Literal["evidence", "procedural", "opinion"]
        # Generic list[str] keeps the schema domain-agnostic; we validate the
        # IDs against the passed-in register in parse_document().
        risks: list[str]
        severity: Literal["routine", "notable", "urgent"]
        measurements: list[Measurement] = []

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
    )
