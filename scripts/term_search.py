#!/usr/bin/env python3
"""term_search.py — on-demand full-text term search over the Arbor Hills (N2688)
document corpus for *event* language, with a Claude relevance filter.

WHY THIS EXISTS
---------------
The daily classifier looks at each NEW document once and records a summary; it
does not keep the full OCR'd text, and it can't answer a retrospective question
like "which records ever mention subsidence / leachate outbreaks / TS-01?". This
tool does: it re-reads a whole corpus of PDFs, finds the target terms, and (by
default) asks Claude whether each candidate document actually *documents the
event* at the landfill — as opposed to a false-positive word match.

THE NOISE PROBLEM (why the regex is fussy and why the LLM stage exists)
-----------------------------------------------------------------------
A naive grep for these words is useless: "subsidence" catches "subsidiary",
"boiling" catches "boiler" and "Boiling Springs Road", "outbreak" catches "COVID
outbreak", and "TS-01" catches "TS1 = primary treatment standard". So:
  1. DEFAULT_TERMS uses word boundaries + a per-term NEGATIVE pattern that
     suppresses the known false-positive contexts.
  2. An optional relevance pass (default on; --no-llm to skip) sends each
     candidate's matched snippets to Claude and asks: does THIS document actually
     record one of the target physical events at the Arbor Hills landfill? Only
     the snippet windows are sent (cheap), never whole documents.

CORPORA
-------
  --root DIR [--root DIR ...]     search local PDFs (default corpus). Repeatable.
  --corpus nsite-archive          search the monitor's own OCR'd PDF mirror on
                                  Drive (Mirror B). Needs the GOAUTH_* creds the
                                  archiver uses; downloads to a temp dir. Slow
                                  (the mirror is ~1700 PDFs) — this is the "search
                                  the monitor's corpus" path.

PRIVACY
-------
The FOIA'd local corpus can contain requester PII / strategy. The generated
report quotes document snippets, so **a report over a local/FOIA corpus is
private** — keep it in Lotext, never write it to this public repo or the public
Sheet. Only nsite-archive results (already-public EGLE data) are safe for a public
surface. This script hard-excludes `foia-message-histories` by default.

USAGE
-----
  python3 scripts/term_search.py --root "/path/to/source-docs" --out report.md
  python3 scripts/term_search.py --root DIR --no-llm            # fast, noisier
  python3 scripts/term_search.py --corpus nsite-archive --out report.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterable, Optional

# Repo root on path so the lazy `archive_client` / `config_loader` imports below
# resolve when run as `python3 scripts/term_search.py` (same idiom as the other
# scripts in this dir — scripts/ is sys.path[0], not the repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Default term set (public-safe, generic landfill vocabulary).
# label -> (positive regex, negative regex | None). Both case-insensitive.
# A match survives only if the positive pattern hits AND the negative pattern
# does NOT hit the surrounding window (see _NEG_WINDOW). This is what turns a
# noisy grep into a usable search — see the module docstring.
# ---------------------------------------------------------------------------
DEFAULT_TERMS: dict[str, tuple[str, Optional[str]]] = {
    # --- ground movement ---
    "subsidence": (r"\bsubsidence\b|\bsubsided\b|\bsubsiding\b", None),
    "cave-in": (r"\bcave[\s-]?ins?\b|\bcaved[\s-]?in\b|\bsink\s?holes?\b", None),
    "mudslide": (r"\bmud[\s-]?slides?\b|\blandslides?\b", None),
    "settlement": (
        r"\b(?:surface |differential )?settlement\b|\bsettling\b",
        r"legal settlement|settlement agreement|claim.{0,12}settlement|settlement of",
    ),
    "slope failure": (r"\bslope failure\b|\bsloughing\b|\bslumping\b", None),
    # --- leachate breakouts ---
    "leachate outbreak/seep": (
        r"leachate (?:outbreak|seep\w*|breakout|pooling|ponding|bubbl\w+|flowing)"
        r"|(?:outbreak|seep\w*|breakout) [^.]{0,25}leachate"
        r"|surfac\w+ leachate|standing leachate|leachate on (?:the )?(?:cap|surface|deck|slope)",
        None,
    ),
    # --- thermal / "boiling" ---
    "boiling/bubbling": (
        r"\bboil(?:s|ed|ing)?\b|\bbubbl(?:e|es|ed|ing)\b",
        r"boiler|boiling springs|boiling point|boiling range|boilerplate|air bubble",
    ),
    "fuming/steaming": (
        r"\bfum(?:e|es|ing|ed)\b|\bsteam(?:s|ed|ing)\b",
        r"steam turbine|steam generator|boiler.{0,12}steam|central steam|steam plant|steam radiation",
    ),
    "elevated temperature": (
        r"elevated temperatures?\b|thermal anomal\w+|subsurface (?:oxidation|fire|smolder\w+|reaction)"
        r"|exotherm\w+|\bSET event\b|smolder\w+",
        None,
    ),
    # --- named location ---
    "TS-01": (r"\bTS[\s\-_]?01\b|\bTSO1\b", None),
}
# bare "leachate" is in nearly every landfill doc -> reported as a count, never a
# doc list (this is why it is NOT in DEFAULT_TERMS).
_BARE_LEACHATE = re.compile(r"\bleachate\b", re.I)
_NEG_WINDOW = 45  # chars of context each side used to test the negative pattern
_MIN_TEXT_CHARS = 40  # below this, treat a PDF as image-only (a recall gap)
FOLDER_ENV = "GOAUTH_ARCHIVE_FOLDER_ID"  # Mirror B (same as archive_client)


@dataclass
class DocRef:
    doc_id: str
    name: str
    date: str = ""
    source_link: str = ""
    local_path: str = ""


@dataclass
class Hit:
    label: str
    page: int
    snippet: str


@dataclass
class DocResult:
    ref: DocRef
    hits: list[Hit]
    n_pages: int
    leachate_count: int = 0
    # relevance-pass output (None => not judged)
    relevant: Optional[bool] = None
    sense: str = ""
    confidence: float = 0.0
    why: str = ""

    @property
    def labels(self) -> list[str]:
        seen: dict[str, int] = {}
        for h in self.hits:
            seen[h.label] = seen.get(h.label, 0) + 1
        return [f"{k}×{v}" for k, v in sorted(seen.items(), key=lambda kv: -kv[1])]


# ---------------------------------------------------------------------------
# Matching (pure — no I/O)
# ---------------------------------------------------------------------------
def compile_terms(terms: dict[str, tuple[str, Optional[str]]]):
    out = {}
    for label, (pos, neg) in terms.items():
        out[label] = (re.compile(pos, re.I), re.compile(neg, re.I) if neg else None)
    return out


def _page_of(pages: list[str], offset: int) -> int:
    acc = 0
    for i, p in enumerate(pages, 1):
        if acc + len(p) + 1 > offset:
            return i
        acc += len(p) + 1
    return len(pages) if pages else 1


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def find_hits(pages: list[str], compiled, snippet_width: int = 130) -> list[Hit]:
    """Return one Hit per (label, distinct-context) match across the pages.
    A match is dropped if its label's negative pattern hits the ± _NEG_WINDOW
    context — this is the false-positive suppressor (boiler, subsidiary, ...)."""
    full = "\n".join(pages)
    hits: list[Hit] = []
    for label, (pos, neg) in compiled.items():
        seen_ctx: set[str] = set()
        for m in pos.finditer(full):
            if neg is not None:
                w0 = max(0, m.start() - _NEG_WINDOW)
                w1 = min(len(full), m.end() + _NEG_WINDOW)
                if neg.search(full[w0:w1]):
                    continue  # suppressed false positive
            s = max(0, m.start() - snippet_width)
            e = min(len(full), m.end() + snippet_width)
            snip = _clean(full[s:e])
            key = snip[:70].lower()
            if key in seen_ctx:
                continue
            seen_ctx.add(key)
            hits.append(Hit(label=label, page=_page_of(pages, m.start()), snippet=snip))
    return hits


# ---------------------------------------------------------------------------
# Corpus extraction (I/O)
# ---------------------------------------------------------------------------
def extract_pages(path: str) -> tuple[Optional[list[str]], Optional[str]]:
    """Return (pages, None) or (None, error). Already-OCR'd PDFs just return
    their text layer (no re-OCR). Image-only PDFs come back with thin/empty text
    and are reported as recall gaps by the caller."""
    try:
        import fitz  # PyMuPDF (lazy)
    except Exception as e:  # pragma: no cover
        return None, f"PyMuPDF unavailable: {e}"
    try:
        doc = fitz.open(path)
    except Exception as e:
        return None, f"open-error: {e}"
    try:
        pages = [pg.get_text() for pg in doc]
    except Exception as e:
        return None, f"read-error: {e}"
    finally:
        doc.close()
    return pages, None


def iter_local(roots: list[str], exclude: set[str]) -> Iterable[DocRef]:
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in exclude]
            for fn in sorted(filenames):
                if fn.lower().endswith(".pdf"):
                    p = os.path.join(dirpath, fn)
                    yield DocRef(doc_id=os.path.relpath(p, root), name=fn, local_path=p)


def iter_nsite_archive(tmpdir: str) -> Iterable[DocRef]:
    """Enumerate + download the monitor's OCR'd PDF mirror (Mirror B) to tmpdir.
    Reuses archive_client's OAuth service/creds; adds no state to that module."""
    import archive_client as ac  # local import: keep core module deps lazy

    if not ac.is_configured(FOLDER_ENV):
        raise SystemExit(
            "nsite-archive corpus needs the GOAUTH_* archiver creds "
            "(see scripts/oauth_setup.py). Not configured."
        )
    service = ac.oauth_drive_service()
    folder = ac.folder_id(FOLDER_ENV)
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore

    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder}' in parents and trashed=false and mimeType='application/pdf'",
                fields="nextPageToken, files(id, name, webViewLink)",
                pageSize=1000,
                pageToken=page_token,
            )
            .execute()
        )
        for f in resp.get("files", []):
            dest = os.path.join(tmpdir, f["id"] + ".pdf")
            with open(dest, "wb") as fh:
                dl = MediaIoBaseDownload(fh, service.files().get_media(fileId=f["id"]))
                done = False
                while not done:
                    _, done = dl.next_chunk()
            yield DocRef(
                doc_id=f["id"],
                name=f["name"],
                source_link=f.get("webViewLink", ""),
                local_path=dest,
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


# ---------------------------------------------------------------------------
# Relevance pass (Claude) — same client/idiom as egle_doc_parser.classify()
# ---------------------------------------------------------------------------
_RELEVANCE_SYSTEM = (
    "You screen regulatory documents about the Arbor Hills landfill complex "
    "(EGLE SRN N2688, Northville/Salem Township, Michigan) for records that "
    "DOCUMENT a specific physical event or condition at the landfill: ground "
    "subsidence / cave-ins / settlement, leachate outbreaks / seeps / pooling / "
    "bubbling on the surface, boiling or steaming or elevated-temperature "
    "leachate or gas, or activity at named locations such as TS-01. You are given "
    "a document's name and the text snippets where target terms matched. Decide "
    "whether THIS document actually records such an event/condition at the "
    "landfill, versus a false-positive word match (e.g. 'subsidiary', 'boiler', "
    "'Boiling Springs Road', a treatment-standard 'TS1', a 'COVID outbreak') or a "
    "generic regulation/reference that merely uses the words. Judge only from the "
    "snippets provided."
)


def relevance_judge(dr: DocResult, model: str, client=None, max_snippets: int = 14):
    """Populate dr.relevant/sense/confidence/why from Claude. Cheap: only the
    matched snippets are sent. Injectable client for tests."""
    import anthropic
    from pydantic import BaseModel

    class Relevance(BaseModel):
        relevant: bool
        sense: str  # short phrase: which event(s), or why it's a false positive
        confidence: float
        why: str

    if client is None:
        client = anthropic.Anthropic()

    snips = []
    for h in dr.hits[:max_snippets]:
        snips.append(f"- [{h.label}, p{h.page}] {h.snippet}")
    user = (
        f"Document name: {dr.ref.name}\n"
        f"Matched terms: {', '.join(dr.labels)}\n\n"
        f"Snippets:\n" + "\n".join(snips)
    )
    resp = client.messages.parse(
        model=model,
        max_tokens=512,
        system=[{"type": "text", "text": _RELEVANCE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=Relevance,
    )
    parsed = resp.parsed_output
    if parsed is None:
        dr.relevant = None
        dr.why = f"relevance pass returned no output (stop_reason={getattr(resp, 'stop_reason', '?')})"
        return dr
    dr.relevant = parsed.relevant
    dr.sense = parsed.sense
    dr.confidence = parsed.confidence
    dr.why = parsed.why
    return dr


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _rank_key(dr: DocResult):
    # relevant (True) first, then unjudged (None), then not-relevant (False);
    # within each, more distinct labels + higher confidence first.
    tier = {True: 0, None: 1, False: 2}[dr.relevant]
    return (tier, -len({h.label for h in dr.hits}), -dr.confidence, -len(dr.hits))


def build_report(results: list[DocResult], *, corpus: str, scanned: int,
                 recall_gaps: list[tuple[str, str]], used_llm: bool,
                 term_labels: list[str]) -> str:
    results = sorted(results, key=_rank_key)
    relevant = [r for r in results if r.relevant is True]
    unjudged = [r for r in results if r.relevant is None]
    suppressed = [r for r in results if r.relevant is False]

    L = []
    L.append("# Term-search report — Arbor Hills (N2688)")
    L.append("")
    L.append(f"- **Corpus:** {corpus}")
    L.append(f"- **PDFs scanned (with text):** {scanned}")
    L.append(f"- **Image-only / no-text (recall gaps):** {len(recall_gaps)}")
    L.append(f"- **Docs with >=1 term hit:** {len(results)}")
    L.append(f"- **Relevance filter:** {'Claude (on)' if used_llm else 'off (regex only — noisier; add --llm to enable)'}")
    L.append(f"- **Terms:** {', '.join(term_labels)}")
    L.append("")
    L.append("> Candidate hits for human review. The relevance filter is an aid, "
             "not ground truth. A local/FOIA-corpus report may quote sensitive "
             "material — keep it private (Lotext); do not publish it.")
    L.append("")

    def _emit(dr: DocResult):
        L.append(f"### {dr.ref.name}")
        meta = []
        if dr.ref.date:
            meta.append(f"date: {dr.ref.date}")
        if dr.ref.source_link:
            meta.append(f"[source]({dr.ref.source_link})")
        if dr.ref.local_path:
            meta.append(f"`{dr.ref.doc_id}`")
        if meta:
            L.append(" · ".join(meta))
        L.append(f"- terms: {', '.join(dr.labels)}  ({dr.n_pages}pp)")
        if dr.relevant is not None:
            L.append(f"- relevance: **{dr.relevant}** (conf {dr.confidence:.2f}) — {dr.sense}")
            if dr.why:
                L.append(f"  - {dr.why}")
        for h in dr.hits[:8]:
            L.append(f"- [{h.label} p{h.page}] …{h.snippet}…")
        L.append("")

    L.append(f"## Relevant ({len(relevant)})")
    L.append("")
    if not relevant:
        L.append("_None confirmed by the relevance filter._\n")
    for dr in relevant:
        _emit(dr)

    if unjudged:
        L.append(f"## Not judged ({len(unjudged)})")
        L.append("")
        for dr in unjudged:
            _emit(dr)

    if suppressed:
        L.append(f"## Suppressed as off-topic by the relevance filter ({len(suppressed)})")
        L.append("")
        L.append("> The relevance filter downgraded these. It is an aid, not ground "
                 "truth — the snippets are shown so you can audit every downgrade "
                 "rather than trust it blind (a false-negative would otherwise "
                 "silently drop a real hit).")
        L.append("")
        for dr in suppressed:
            _emit(dr)

    if recall_gaps:
        L.append(f"## Recall gaps — image-only / unreadable ({len(recall_gaps)})")
        L.append("")
        for name, why in recall_gaps[:80]:
            L.append(f"- {name} — {why}")
        if len(recall_gaps) > 80:
            L.append(f"- … and {len(recall_gaps) - 80} more")
        L.append("")

    return "\n".join(L)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run(refs: Iterable[DocRef], compiled, *, use_llm: bool, model: str,
        max_candidates: int, client=None):
    results: list[DocResult] = []
    recall_gaps: list[tuple[str, str]] = []
    scanned = 0
    for ref in refs:
        pages, err = extract_pages(ref.local_path)
        if pages is None:
            recall_gaps.append((ref.name, err or "unknown error"))
            continue
        full = "\n".join(pages)
        if len(full.strip()) < _MIN_TEXT_CHARS:
            recall_gaps.append((ref.name, "empty/thin text layer (image-only?)"))
            continue
        scanned += 1
        hits = find_hits(pages, compiled)
        if not hits:
            continue
        dr = DocResult(ref=ref, hits=hits, n_pages=len(pages),
                       leachate_count=len(_BARE_LEACHATE.findall(full)))
        results.append(dr)

    if use_llm and results:
        # judge the richest candidates first, cap total LLM calls
        results.sort(key=lambda r: -len({h.label for h in r.hits}))
        for dr in results[:max_candidates]:
            try:
                relevance_judge(dr, model=model, client=client)
            except Exception as e:  # noqa: BLE001 — never let one judge abort the run
                dr.relevant = None
                dr.why = f"relevance error: {e}"
    return results, scanned, recall_gaps


def resolve_use_llm(corpus: str, no_llm: bool, llm: bool) -> bool:
    """Decide whether to run the Claude relevance pass.

    Default is ON for the public nSITE archive but OFF for a local corpus: a
    local/FOIA corpus can contain PII, and the relevance pass would egress its
    snippets to a third-party API. So a local run requires an explicit --llm
    opt-in. --no-llm always wins."""
    if no_llm:
        return False
    if llm:
        return True
    return corpus == "nsite-archive"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Term search over the N2688 corpus.")
    ap.add_argument("--root", action="append", default=[],
                    help="local corpus dir (repeatable)")
    ap.add_argument("--corpus", choices=["local", "nsite-archive"], default="local")
    ap.add_argument("--exclude", action="append", default=["foia-message-histories"],
                    help="dir names to skip (repeatable; default excludes FOIA PII)")
    ap.add_argument("--terms-file", help="JSON {label: [positive, negative]} to override defaults")
    ap.add_argument("--no-llm", action="store_true", help="skip the Claude relevance filter")
    ap.add_argument("--llm", action="store_true",
                    help="force the relevance filter ON — REQUIRED to run it over a local "
                         "corpus, whose snippets would otherwise never touch the API "
                         "(default: on for nsite-archive, off for local)")
    ap.add_argument("--max-candidates", type=int, default=60,
                    help="cap docs sent to the relevance filter (default 60)")
    ap.add_argument("--model", help="override the relevance model (default: config anthropic_model)")
    ap.add_argument("--out", help="write the report here (default: stdout)")
    args = ap.parse_args(argv)

    terms = DEFAULT_TERMS
    if args.terms_file:
        with open(args.terms_file) as fh:
            raw = json.load(fh)
        terms = {k: (v[0], v[1] if len(v) > 1 else None) for k, v in raw.items()}
    compiled = compile_terms(terms)

    use_llm = resolve_use_llm(args.corpus, args.no_llm, args.llm)
    if use_llm and args.corpus == "local":
        print("[term_search] NOTE: relevance filter is ON over a LOCAL corpus — "
              "matched snippets will be sent to the Anthropic API. Make sure the "
              "corpus holds nothing you don't want to egress.", file=sys.stderr)

    model = args.model
    if model is None and use_llm:
        try:
            from config_loader import load_config
            model = load_config().get("anthropic_model", "claude-haiku-4-5")
        except Exception:
            model = "claude-haiku-4-5"

    tmpdir = None
    if args.corpus == "nsite-archive":
        tmpdir = tempfile.mkdtemp(prefix="term-search-archive-")
        refs = iter_nsite_archive(tmpdir)
        corpus_label = "nSITE archive (Mirror B, Drive)"
    else:
        if not args.root:
            ap.error("--root is required for --corpus local")
        refs = iter_local(args.root, set(args.exclude))
        corpus_label = "local: " + ", ".join(args.root)

    results, scanned, recall_gaps = run(
        refs, compiled, use_llm=use_llm, model=model or "",
        max_candidates=args.max_candidates,
    )
    report = build_report(
        results, corpus=corpus_label, scanned=scanned, recall_gaps=recall_gaps,
        used_llm=use_llm, term_labels=list(terms.keys()),
    )
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report)
        print(f"[term_search] wrote {args.out} "
              f"({len(results)} docs with hits, {scanned} scanned, {len(recall_gaps)} recall gaps)")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
