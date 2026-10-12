"""
kdp_check.py — deterministic consistency check for the classifier's free-text
fields (key_data_point, summary) before they reach the public Sheet / feed.

Why (ADR 067): the classifier occasionally writes wellhead-temperature claims
the code itself would never make — a "135°F baseline MACT limit", a
"MACT limit of 204°F", or an H2S reading in ppm reported as "553°F". The
thresholds the monitor actually uses are config values (131°F and 145°F,
plus the well-specific HOV values EGLE has approved). This module checks the
model's prose against those values. Pure functions, no I/O: the caller passes
the reference values in, so tests need no config.

Scope is deliberately narrow, to avoid withholding correct text:
  * only numbers explicitly marked °F are examined;
  * a stated regulatory limit must be one of the reference values;
  * an "N°F above <limit>" gap must reconcile, within GAP_TOLERANCE_F, with the
    nearest °F reading before it and the limit it names (or one of them);
  * a °F value above max_plausible_wellhead_f is flagged only in a sentence
    about wells, and never in a flare / combustion sentence (flare
    temperatures of 1,400°F+ are real).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GAP_TOLERANCE_F = 1.1

_NUM_F = r"(\d{2,4}(?:\.\d+)?)\s*(?:°|º|˚|deg\.?|degrees)\s*F\b|(\d{2,4}(?:\.\d+)?)\s*F\b"
_NUM_F_RE = re.compile(_NUM_F)
_REG_LABEL = r"(?:MACT|NESHAP|NSPS|EPA|federal|baseline|emission guidelines?|Subpart\s+AAAA|Subpart\s+XXX)"
_HOV_LABEL = r"(?:HOV|higher operating value|waiver|ceiling|approved)"
_LIMIT_WORD = r"(?:limit|threshold|standard|operating temperature|operating value)"
_SENT_SPLIT_RE = re.compile(r"(?<=[.;])\s+")
_WELL_CTX_RE = re.compile(r"\b(?:well|wellhead|WOI|gas[- ]extraction|HOV|AHW\w*|AH\w*R\d)\b", re.I)
_FLARE_CTX_RE = re.compile(r"flare|combust|thermal oxidi|destruction|enclosed|stack|engine|RTO", re.I)


@dataclass
class TempReference:
    """Reference temperatures (°F). Built by the caller from config.yml."""
    regulatory: set = field(default_factory=lambda: {131.0, 145.0})
    hov_values: set = field(default_factory=set)
    max_plausible_wellhead_f: float = 250.0

    @property
    def any_limit(self) -> set:
        return set(self.regulatory) | set(self.hov_values)


def reference_from_config(cfg: dict) -> TempReference:
    """Build a TempReference from config.yml's existing temperature_thresholds
    block (epa_gas_operating_f / mact_f, plus hov_values_f and
    max_plausible_wellhead_f added for this check)."""
    tr = cfg.get("temperature_thresholds") or {}
    reg = {float(tr.get("epa_gas_operating_f", 131)), float(tr.get("mact_f", 145))}
    hov = {float(x) for x in (tr.get("hov_values_f") or [])}
    return TempReference(
        regulatory=reg,
        hov_values=hov,
        max_plausible_wellhead_f=float(tr.get("max_plausible_wellhead_f", 250)),
    )


def _f(m: re.Match) -> float:
    return float(m.group(1) or m.group(2))


def _near(x: float, values) -> bool:
    return any(abs(x - v) <= GAP_TOLERANCE_F for v in values)


def check_temperature_claims(text: str, ref: TempReference) -> list[str]:
    """Return a list of human-readable issues (empty = passes)."""
    issues: list[str] = []
    if not text:
        return issues
    for sent in _SENT_SPLIT_RE.split(text):
        nums = list(_NUM_F_RE.finditer(sent))
        if not nums:
            continue

        # 1) "<label> limit of N°F" / "N°F <label> limit" -> N must be a reference value
        for m in nums:
            n = _f(m)
            before = sent[max(0, m.start() - 60):m.start()]
            after = sent[m.end():m.end() + 45]
            reg_before = re.search(_REG_LABEL + r"[^.;()]{0,25}" + _LIMIT_WORD + r"[^.;()]{0,6}$", before, re.I)
            reg_after = re.match(r"\s*\)?\s*(?:the\s+)?(?:\w+\s+){0,2}" + _REG_LABEL + r"[^.;()]{0,15}" + _LIMIT_WORD, after, re.I)
            hov_before = re.search(_HOV_LABEL + r"[^.;()]{0,25}(?:of|at|to|limit)\s*$", before, re.I)
            is_gap = re.match(r"\s*(?:above|below|over|under|higher|lower|more|less)\b", after, re.I)
            if is_gap:
                continue  # "35°F above the MACT limit" is a difference, checked in (2)
            if (reg_before or reg_after) and not hov_before and not _near(n, ref.regulatory):
                issues.append(f"{n:g}°F is called a regulatory limit; reference limits are "
                              + "/".join(f"{v:g}" for v in sorted(ref.regulatory)) + "°F")

        # 2) "D°F above (the) [N°F] <label>" gaps must reconcile
        for g in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*(?:°|º|˚)?\s*F\s+above\s+(?:the\s+)?([^.;)]{0,60})", sent, re.I):
            gap = float(g.group(1))
            tail = g.group(2)
            # The value a gap is measured from: try the nearest earlier °F value
            # (skipping other gaps), then the nearest earlier one that is not
            # itself a limit ("the 140°F permitted limit"). A gap is flagged
            # only if NO candidate reconciles.
            def _is_gap(m):
                return re.match(r"\s*(?:above|below|over|under)\b", sent[m.end():m.end() + 8], re.I)

            def _is_limit(m):
                return re.match(r"[^.;()]{0,25}" + _LIMIT_WORD, sent[m.end():m.end() + 40], re.I)

            earlier = [m for m in nums if m.end() <= g.start() and not _is_gap(m)]
            cands = []
            if earlier:
                cands.append(_f(earlier[-1]))
            non_limit = [m for m in earlier if not _is_limit(m)]
            if non_limit and _f(non_limit[-1]) not in cands:
                cands.append(_f(non_limit[-1]))
            if not cands:
                continue
            named = _NUM_F_RE.search(tail)
            if named and named.start() < 25:
                n = _f(named)
                if not any(abs((c - gap) - n) <= GAP_TOLERANCE_F for c in cands):
                    issues.append(f"{cands[-1]:g}°F is not {gap:g}°F above {n:g}°F")
                continue
            if re.search(_HOV_LABEL, tail, re.I):
                if not ref.hov_values:
                    continue  # no HOV list configured: can't judge, don't flag
                pool = ref.hov_values
            elif re.search(_REG_LABEL + r"|" + _LIMIT_WORD, tail, re.I):
                pool = ref.regulatory
            else:
                continue
            if not any(_near(c - gap, pool) for c in cands):
                issues.append(f"'{gap:g}°F above {tail.strip()[:30]}' implies a {cands[-1] - gap:g}°F limit, "
                              "which is not a reference value")

        # 3) implausible wellhead temperature
        if _WELL_CTX_RE.search(sent) and not _FLARE_CTX_RE.search(sent):
            for m in nums:
                n = _f(m)
                if n > ref.max_plausible_wellhead_f:
                    issues.append(f"{n:g}°F is not a plausible wellhead temperature "
                                  "(likely a ppm or other non-temperature column)")
    # de-duplicate, keep order
    seen, out = set(), []
    for i in issues:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


WITHHELD_KDP = ("Automated key data point withheld: it failed a consistency check "
                "against the monitor's temperature reference values. See the source document.")
WITHHELD_SUMMARY = ("Automated summary withheld: it failed a consistency check against "
                    "the monitor's temperature reference values. See the source document.")


def default_reference() -> TempReference:
    """Reference values from this repo's config.yml; the plain 131/145 default
    (no HOV list, so HOV-named gaps are not judged) if config can't be read."""
    try:
        from config_loader import load_config
        return reference_from_config(load_config())
    except Exception:
        return TempReference()


def check_fields(fields: dict, ref: TempReference) -> dict:
    """{field_name: [issues]} for the public free-text fields that fail."""
    out = {}
    for k in ("key_data_point", "summary"):
        iss = check_temperature_claims(fields.get(k) or "", ref)
        if iss:
            out[k] = iss
    return out


def sanitize_measurements(measurements: list, ref: TempReference) -> tuple[list, int]:
    """Gap 2 (ADR 067): a structured reading the classifier tagged as a MEASURED
    temperature above max_plausible_wellhead_f, tied to a well (well_id or a
    well word in the note) and not flare/combustion, is
    almost certainly another column (H2S ppm, flow). Re-tag it basis='unknown'
    so is_urgent's measured-temperature path ignores it; the value is kept,
    with a note, for review. Returns (new_list, n_retagged)."""
    out, n = [], 0
    for m in measurements or []:
        m = dict(m)
        try:
            v = float(m.get("value"))
        except (TypeError, ValueError):
            out.append(m)
            continue
        unit = (m.get("unit") or "F").strip().lower()
        note = m.get("note") or ""
        well_ctx = bool((m.get("well_id") or "").strip()) or bool(_WELL_CTX_RE.search(note))
        if (m.get("metric") == "temperature" and m.get("basis") == "measured"
                and unit.startswith("f") and v > ref.max_plausible_wellhead_f
                and well_ctx and not _FLARE_CTX_RE.search(note)):
            m["basis"] = "unknown"
            m["note"] = ("[implausible wellhead temperature, re-tagged by kdp_check] "
                         + (m.get("note") or "")).strip()
            n += 1
        out.append(m)
    return out, n


def max_plausible_measured_f(measurements: list, ref: TempReference):
    """Highest measured °F temperature at or below the plausibility cap."""
    best = None
    for m in measurements or []:
        if m.get("metric") != "temperature" or m.get("basis") != "measured":
            continue
        try:
            v = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if (m.get("unit") or "F").strip().lower().startswith("c"):
            v = v * 9 / 5 + 32
        if v <= ref.max_plausible_wellhead_f:
            best = v if best is None else max(best, v)
    return best
