"""
email_alerts.py — SMTP urgent alerts + weekly digest.

SMTP (not the Gmail MCP connector — that's interactive-only). Creds come from
SMTP_* env vars; recipients from config.yml (edit the list, no code change).

Urgency is decided here, not in the parser: a doc is urgent if the parser marked
it "urgent" OR a temperature reading at/above the configured threshold appears in
its key data point / text. `is_urgent` and `_max_temperature_f` are pure and
unit-tested.
"""
from __future__ import annotations

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Optional

# Matches "180F", "180 F", "180°F", "180 degrees F", "180 deg F", and reads a
# thousands separator whole: "2,145°F" is 2145 (a flare value the plausibility
# ceiling then skips), never 145 (which would false-fire an URGENT alert).
_TEMP_RE = re.compile(r"(?<!\d)(?<!\d,)(\d{1,3}(?:,\d{3})+|\d{2,4})\s*(?:°|deg(?:rees)?\.?\s*)?\s*f\b",
                      re.IGNORECASE)

# An EPA Method 19 F-factor label: "F-factor", "Fd factor", "F (factor)", and the
# OCR forms of a subscripted F, "Fy (factor)", "F,, (factor)", "F, (factor)". A lab
# sheet prints them as a column ("Fd (factor) 9340 Fw (factor) 11356 Fc (factor)
# 1729"), so a bare _TEMP_RE read "9340 F" from the NEXT label's F as 9340°F (the
# 2026-10-16 N1504 ICT sulfur report, nSITE doc 4935451998137733993).
_F_FACTOR_LABEL_RE = re.compile(r"F[a-z]{0,2}[\s,_-]{0,4}\(?\s*factor\b", re.IGNORECASE)
# The label right before a number ("Fd (factor) 9340", "F-factor = 9,340").
_F_FACTOR_BEFORE_RE = re.compile(r"\bF[a-z]{0,2}[\s,_-]{0,4}\(?\s*factor\)?\s*[=:]?\s*$",
                                 re.IGNORECASE)


def _is_f_factor(text: str, m: re.Match) -> bool:
    """True when a _TEMP_RE match is an F-factor value, not a temperature: its
    "F" starts an F-factor label, or an F-factor label sits right before the
    number. Deliberately narrow (the label must touch the match): this guards
    the urgency fallback, so a nearby "Method 19" or "factor" alone never hides
    a real reading."""
    f_at = m.end() - 1
    if _F_FACTOR_LABEL_RE.match(text, f_at):
        return True
    return bool(_F_FACTOR_BEFORE_RE.search(text[max(0, m.start() - 30):m.start()]))


def temperature_matches(text: str):
    """_TEMP_RE matches in text, minus F-factor values (_is_f_factor). The one
    iterator for every free-text °F reader (_max_temperature_f and
    review_flags.implausible_text_temps)."""
    for m in _TEMP_RE.finditer(text):
        if not _is_f_factor(text, m):
            yield m


def _max_temperature_f(text: str, max_f: int = 250) -> Optional[int]:
    """Return the highest plausible WELLHEAD Fahrenheit reading found in text,
    or None. Values above max_f (temperature_thresholds.max_plausible_wellhead_f,
    ADR 067) are skipped: on this fallback path they are flare/combustion
    temperatures or a misread non-temperature column (e.g. H2S ppm), never a
    wellhead reading that should fire an urgent alert. review_flags.parsed_flags()
    logs the ones outside flare/combustion context for review (ADR 067
    amendment)."""
    if not text:
        return None
    best = None
    for m in temperature_matches(text):
        val = int(m.group(1).replace(",", ""))
        if 50 <= val <= max_f:  # ignore stray years / tiny numbers / non-wellhead values
            best = val if best is None else max(best, val)
    return best


def max_measured_temp_f(parsed) -> Optional[int]:
    """Highest MEASURED temperature (deg F) among the structured measurements.
    Permitted ceilings / HOV-waiver limits are deliberately excluded — a 180F
    permitted ceiling is not a 180F reading."""
    best = None
    for m in getattr(parsed, "measurements", []) or []:
        if m.get("metric") != "temperature" or m.get("basis") != "measured":
            continue
        unit = (m.get("unit") or "F").lower()
        try:
            val = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if unit.startswith("c"):  # convert any stray Celsius readings
            val = val * 9 / 5 + 32
        ival = int(round(val))
        best = ival if best is None else max(best, ival)
    return best


def _peak_measured(parsed) -> Optional[dict]:
    """The highest MEASURED temperature measurement (same filter as
    max_measured_temp_f, whose rounded value it always matches), with its
    value in deg F as `value_f`. For naming the well in the urgent body;
    never used to decide."""
    best = None
    for m in getattr(parsed, "measurements", []) or []:
        if m.get("metric") != "temperature" or m.get("basis") != "measured":
            continue
        try:
            val = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if (m.get("unit") or "F").lower().startswith("c"):
            val = val * 9 / 5 + 32
        if best is None or val > best["value_f"]:
            best = {**m, "value_f": val}
    return best


def _fmt_f(v: float) -> str:
    return f"{round(float(v), 1):g}°F"


def _urgency_decision(parsed, cfg: dict) -> tuple[bool, list[str]]:
    """THE urgency decision: (fires, reasons). is_urgent() returns the bool and
    urgency_reason() joins the reasons, so the two can never drift apart. Every
    branch that fires is listed (severity AND a measured reading, when both),
    so an [URGENT] subject over a body saying "Severity (as recorded): routine"
    always says which path fired (the 2026-10-08 email that didn't)."""
    urgent_cfg = cfg.get("urgent", {})
    threshold = urgent_cfg.get("measured_temp_urgent_f", 145)
    mact = (cfg.get("temperature_thresholds") or {}).get("mact_f", 145)
    label = " (MACT limit)" if threshold == mact else ""
    reasons: list[str] = []
    if urgent_cfg.get("severity_is_urgent", True) and parsed.severity == "urgent":
        # Not "the classifier": parse_document's deterministic floor (a
        # measured reading >= MACT, an SSO/CO-spike/smoldering tag, an
        # enforcement title) also sets severity, and ParsedDoc doesn't say which.
        reasons.append("the document's recorded severity is \"urgent\" (set by the "
                       "classifier, parse_document's deterministic urgency floor, or a "
                       "hand-curator)")

    # Prefer structured measurements (correctly excludes permitted ceilings).
    measured = max_measured_temp_f(parsed)
    if measured is not None:
        if measured >= threshold:
            peak = _peak_measured(parsed) or {}
            val = peak.get("value_f", measured)
            shown = _fmt_f(val)
            if float(val) < threshold:  # only the rounding made it cross
                shown += f" (rounds to {measured}°F)"
            well = peak.get("well_id")
            reasons.append(f"measured temperature {shown}"
                           + (f" at {well}" if well else "")
                           + f", at or above the {threshold}°F threshold{label}")
        return bool(reasons), reasons

    # If the parser extracted ANY temperature reading at all (even a permitted
    # ceiling or unknown-basis one), trust the structured path and do NOT regex
    # free text — otherwise a permitted 180F ceiling mentioned in the text would
    # falsely fire, which is the exact conflation this whole design avoids.
    has_any_temp = any(
        (m.get("metric") == "temperature")
        for m in (getattr(parsed, "measurements", []) or [])
    )
    if has_any_temp:
        return bool(reasons), reasons

    # Last resort, ONLY when the parser produced no structured temperature at
    # all: scan free text. Less safe (can't tell measured from permitted).
    hay = f"{parsed.key_data_point}\n{parsed.summary}\n{parsed.full_text}"
    max_f = int((cfg.get("temperature_thresholds") or {}).get("max_plausible_wellhead_f", 250))
    temp = _max_temperature_f(hay, max_f)
    if temp is not None and temp >= threshold:
        reasons.append(f"a {temp}°F temperature in the document text, at or above the "
                       f"{threshold}°F threshold (free-text fallback: no structured "
                       f"temperature was extracted, so this may not be a measured reading)")
    return bool(reasons), reasons


def is_urgent(parsed, cfg: dict) -> bool:
    # The severity short-circuit is kept ahead of the shared decision so an
    # urgent-severity document fires without its measurements being read,
    # exactly as before (a malformed measurement can't turn it into a raise).
    urgent_cfg = cfg.get("urgent", {})
    if urgent_cfg.get("severity_is_urgent", True) and parsed.severity == "urgent":
        return True
    return _urgency_decision(parsed, cfg)[0]


def urgency_reason(parsed, cfg: dict) -> str:
    """Why is_urgent() fires, in one line ("" when it doesn't). Presentation
    only: it never changes whether an alert fires."""
    return "; and ".join(_urgency_decision(parsed, cfg)[1])


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")


def hov_ceiling_for(well_id: str, as_of_date: Optional[str], cfg: dict) -> Optional[float]:
    """The well's EGLE-approved HOV (Higher Operating Value) ceiling in deg F,
    from temperature_thresholds.hov_ceilings, or None. Exact well-id match
    (case and trailing '*' ignored), never by well number: a redrill (R3 vs
    R4) is a different well with its own ceiling, if any. A reading dated
    outside an entry's effective_start..effective_end window doesn't match;
    an undated reading matches any window. CONTEXT ONLY: never a trigger or
    a suppressor (is_urgent never reads it)."""
    wid = (well_id or "").strip().rstrip("*").upper()
    if not wid:
        return None
    # Only an ISO date is compared ("8/29/2023" would compare lexically
    # wrong); anything else is treated as undated.
    date = (as_of_date or "")[:10]
    if not _ISO_DATE_RE.match(date):
        date = ""
    for e in (cfg.get("temperature_thresholds") or {}).get("hov_ceilings") or []:
        if str(e.get("well_id") or "").strip().rstrip("*").upper() != wid:
            continue
        start, end = str(e.get("effective_start") or ""), str(e.get("effective_end") or "")
        if date and ((start and date < start) or (end and date > end)):
            continue
        try:
            return float(e["ceiling_f"])
        except (KeyError, TypeError, ValueError):
            # A hand-edited roster entry with no/garbled ceiling: label the
            # well as having none on record rather than break the alert.
            print(f"[email_alerts] hov_ceilings entry for {wid} has no usable ceiling_f; ignored")
            continue
    return None


# Cap on HOV context lines in one urgent body (a hot WOI report can carry
# dozens of wells at/above 145F).
_HOV_LINES_MAX = 10


def hov_context_lines(parsed, cfg: dict) -> list[str]:
    """One line per well with a MEASURED reading at/above the urgent threshold
    (its highest such reading), labeled with its approved HOV ceiling or
    "Approved ceiling for <well> not in this monitor's roster" (accurate
    whether or not an approval exists; owner wording 2026-10-17). Additive
    context; pure."""
    threshold = (cfg.get("urgent") or {}).get("measured_temp_urgent_f", 145)
    by_well: dict = {}
    for m in getattr(parsed, "measurements", []) or []:
        if m.get("metric") != "temperature" or m.get("basis") != "measured":
            continue
        try:
            val = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        if (m.get("unit") or "F").lower().startswith("c"):
            val = val * 9 / 5 + 32
        if int(round(val)) < threshold:
            continue
        well = m.get("well_id") or ""
        if well not in by_well or val > by_well[well][0]:
            by_well[well] = (val, m.get("as_of_date"))
    lines = []
    for well, (val, date) in sorted(by_well.items(), key=lambda kv: -kv[1][0]):
        if not well:
            lines.append(f"Measured {_fmt_f(val)} with no well named; no ceiling lookup possible.")
            continue
        ceiling = hov_ceiling_for(well, date, cfg)
        if ceiling is None:
            lines.append(f"Approved ceiling for {well} not in this monitor's roster. "
                         f"Measured: {_fmt_f(val)}.")
        else:
            rel = "below ceiling" if val < ceiling else ("at ceiling" if val == ceiling
                                                         else "above ceiling")
            lines.append(f"HOV approved ceiling for {well}: {ceiling:g}°F. "
                         f"Measured: {_fmt_f(val)} ({rel}).")
    if len(lines) > _HOV_LINES_MAX:
        more = len(lines) - _HOV_LINES_MAX
        lines = lines[:_HOV_LINES_MAX] + [f"(+{more} more well(s) at or above {threshold}°F)"]
    return lines


# ---------------------------------------------------------------------------
# Correspondence & enforcement detection (ADR 065, absorbing the retired
# schedule-title-overrides.md handoff's section (b)/(c)) — a generic-titled
# EGLE letter (filed as "Schedule - ...", "nForm Document", etc.) that's
# actually a substantive approval/extension/corrective-action/enforcement
# letter, buried under a bland title, gets bumped to its own pinned digest
# section instead of the ordinary soft "OTHER NEW DOCUMENTS" line. Only ever
# checked for a doc the caller has already determined has a generic nSITE
# title (document_titles.title_is_generic) — a document with a real, specific
# title (e.g. "Violation Notice") doesn't need this rescue.
# ---------------------------------------------------------------------------

# EGLE's own letter template footer -- distinctive enough NOT to match a
# letter merely ADDRESSED to the agency (confirmed on a real GFL->EGLE cover
# letter, which spells out "Department of Environment, Great Lakes, and
# Energy" in its own address block but never this URL+phone footer line).
_EGLE_LETTERHEAD_RE = re.compile(r"Michigan\.gov/EGLE", re.IGNORECASE)

# The substantive-correspondence signals from the retired handoff's own
# research (8-for-8 on the live N2688 Schedule- corpus): an extension
# request, a corrective-action item, an approval, a Consent Judg(e)ment
# reference, a perimeter-monitor action-level exceedance, or an HOV /
# "Higher Operating Value" waiver.
_CORRESPONDENCE_SIGNAL_RE = re.compile(
    r"request for extension|corrective action|approv(?:e|es|ed|al)|"
    r"consent judg(?:e)?ment|action level exceedance|"
    r"higher operating value|\bHOV\b",
    re.IGNORECASE,
)

# Letters run many pages once attachments are included; the letterhead/signal
# itself is always on the letter's own first page(s), never buried in an
# attachment -- bounding the scan avoids a false hit from an attachment deep
# inside an otherwise-unrelated large filing (confirmed: a 949pp GFL quarterly
# report reprints EGLE's agency name in its own address block on page 1, but
# never the letterhead URL anywhere in its first pages).
_CORRESPONDENCE_SCAN_CHARS = 6000


def is_correspondence_letter(full_text: str) -> bool:
    """True if `full_text` reads as an EGLE-originated approval/extension/
    corrective-action/enforcement letter: EGLE's own letterhead marker AND at
    least one substantive signal phrase, both within the document's own
    opening pages. Pure, unit-tested — mirrors is_urgent's style. Fails safe
    toward False: a miss just leaves the item on today's ordinary soft digest
    line (no regression from before this feature existed); this is never
    used to suppress or escalate an email on its own, only to choose which
    digest section an already-queued item renders under."""
    text = (full_text or "")[:_CORRESPONDENCE_SCAN_CHARS]
    return bool(_EGLE_LETTERHEAD_RE.search(text)) and bool(_CORRESPONDENCE_SIGNAL_RE.search(text))


# ---------------------------------------------------------------------------
# Composition (pure-ish — no network)
# ---------------------------------------------------------------------------


def format_urgent_body(parsed, metadata: dict, link: str, cfg: dict | None = None) -> str:
    """The [URGENT] email body. States WHY it fired (urgency_reason) next to
    the classifier's own severity, so the hardcoded [URGENT] subject and the
    body never disagree silently, and labels each alerted reading with its HOV
    ceiling (context only). `cfg` None = built-in defaults, no HOV roster."""
    cfg = cfg or {}
    try:
        reason = urgency_reason(parsed, cfg) or "(no urgency trigger matched)"
    except Exception as e:  # noqa: BLE001 — the reason must never cost the alert
        print(f"[email_alerts] urgency reason unavailable: {type(e).__name__}")
        reason = "(reason unavailable)"
    try:
        hov = hov_context_lines(parsed, cfg)
    except Exception as e:  # noqa: BLE001 — context must never cost the alert
        print(f"[email_alerts] HOV context skipped: {type(e).__name__}")
        hov = []
    hov_block = ("Approved-ceiling context (does not affect whether this alert fires):\n"
                 + "".join(f"  {line}\n" for line in hov) + "\n") if hov else ""
    return (
        f"URGENT — Arbor Hills (N2688) document flagged.\n\n"
        f"Document: {metadata.get('document_name', '(unknown)')}\n"
        f"Date filed: {metadata.get('date_filed', '(unknown)')}\n"
        f"Type: {parsed.doc_type}\n"
        f"Risks: {', '.join(parsed.risks) or '(none tagged)'}\n\n"
        f"Urgent because: {reason}.\n"
        f"Severity (as recorded): {parsed.severity}.\n\n"
        f"{hov_block}"
        f"Key data point:\n  {parsed.key_data_point}\n\n"
        f"Summary:\n  {parsed.summary}\n\n"
        f"Document: {link}\n"
    )


def format_digest_body(items: list[dict], urgent_recap: list[dict] | None = None) -> str:
    """items: [{parsed, metadata, link}]. Procedural items (deadlines and notices) first.

    urgent_recap: same shape, plus each item's metadata carries
    'urgent_sent_at' — a recap of items sent as their own same-day [URGENT]
    email to the urgent-tier list. Rendered FIRST. NOTE: the header deliberately
    does NOT say the items were "already emailed separately" — digest-only
    recipients (e.g. DIGEST_RECIPIENTS_EXTRA — the commissioners) do NOT receive
    the same-day [URGENT] emails, so for them this recap is their FIRST sighting,
    not a repeat; claiming otherwise would be false. The 'Sent <date>' line still
    records when the urgent email went to the urgent-tier list.

    ADR 065: an item whose `parsed.is_correspondence` is True (a generic-titled
    EGLE letter is_correspondence_letter() flagged as substantive — see that
    function) gets pinned to its OWN "CORRESPONDENCE & ENFORCEMENT" section,
    right after the urgent recap and before the ordinary procedural/other
    split — the rescue this feature exists for: such an item no longer
    renders in "OTHER NEW DOCUMENTS" at all (pulled out, not also-listed)."""
    if not items and not urgent_recap:
        return "No new Arbor Hills (N2688) documents this period."
    lines = []
    if urgent_recap:
        lines.append("URGENT ITEMS FROM EARLIER:")
        for it in urgent_recap:
            p, m = it["parsed"], it["metadata"]
            sent_at = m.get("urgent_sent_at", "")
            lines.append(f"  - Sent {sent_at}  {m.get('document_name','')}")
            lines.append(f"      {p.key_data_point}")
            lines.append(f"      {it['link']}")
        lines.append("")
    correspondence = [it for it in items if getattr(it["parsed"], "is_correspondence", False)]
    remaining = [it for it in items if not getattr(it["parsed"], "is_correspondence", False)]
    if correspondence:
        lines.append("CORRESPONDENCE & ENFORCEMENT (EGLE letter under a generic filing title):")
        for it in correspondence:
            p, m = it["parsed"], it["metadata"]
            lines.append(f"  - {m.get('date_filed','')}  {m.get('document_name','')}")
            lines.append(f"      {p.key_data_point}")
            lines.append(f"      {it['link']}")
        lines.append("")
    procedural = [it for it in remaining if it["parsed"].doc_type == "procedural"]
    others = [it for it in remaining if it["parsed"].doc_type != "procedural"]
    lines += [f"Arbor Hills (N2688) digest — {len(items)} new document(s).", ""]
    if procedural:
        lines.append("DEADLINES AND NOTICES:")
        for it in procedural:
            p, m = it["parsed"], it["metadata"]
            lines.append(f"  - {m.get('date_filed','')}  {m.get('document_name','')}")
            lines.append(f"      {p.key_data_point}")
            lines.append(f"      {it['link']}")
        lines.append("")
    if others:
        lines.append("OTHER NEW DOCUMENTS:")
        for it in others:
            p, m = it["parsed"], it["metadata"]
            risks = ", ".join(p.risks) or "-"
            lines.append(
                f"  - {m.get('date_filed','')}  [{p.doc_type}/{p.severity}/{risks}]  "
                f"{m.get('document_name','')}"
            )
            lines.append(f"      {p.key_data_point}")
            lines.append(f"      {it['link']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------


def merge_extra_recipients(base: list, env_var: str) -> list:
    """Merge `base` with addresses from `env_var` (comma/semicolon-separated),
    order preserved, de-duplicated. Generalizes the ALERT_RECIPIENTS_EXTRA
    pattern (a PRIVATE address added WITHOUT committing it to this PUBLIC
    repo's config.yml) so any recipient list — not just the main
    `alert_recipients` — can take a private supplement via its own env var.
    See resolve_recipients() below for the original single-purpose caller, and
    gfl_air_watcher.py's use with GFL_AIR_WATCH_RECIPIENTS_EXTRA for a second."""
    out = list(base or [])
    for addr in (os.environ.get(env_var, "") or "").replace(";", ",").split(","):
        addr = addr.strip()
        if addr and addr not in out:
            out.append(addr)
    return out


def resolve_recipients(cfg: dict) -> list:
    """Recipients = config.yml `alert_recipients` PLUS any in the
    ALERT_RECIPIENTS_EXTRA env (comma/semicolon-separated). The env is how a
    PRIVATE address (e.g. a personal stopgap inbox) gets added WITHOUT committing
    it to this PUBLIC repo's config.yml. Order preserved, de-duplicated."""
    return merge_extra_recipients(cfg.get("alert_recipients", []) or [], "ALERT_RECIPIENTS_EXTRA")


# ---------------------------------------------------------------------------
# Unsubscribe / suppression (CAN-SPAM) — ADR 040
#
# One shared suppression list + a List-Unsubscribe header + a compliant footer,
# applied HERE at send_email — the single choke point every stream already calls
# (urgent, Sunday digest, GFL-air WATCH, and every nsite_*/mmd/ride/rop/... watch)
# — so there is no per-stream wiring and no other module is touched. Mechanism is
# mailto: the send path is plain SMTP (no Resend), and RFC-8058 one-click needs an
# HTTPS POST endpoint this static-GitHub-Pages project has no backend for.
# ---------------------------------------------------------------------------


def _norm_email(addr: str) -> str:
    """Bare address, lowercased + trimmed — the canonical form for ALL address
    comparison (suppression, owner-matching), so case/whitespace/display-name
    never defeats the guard. parseaddr pulls the address out of a
    'Name <foo@x.com>' form so a display-name recipient still matches a bare
    suppressed 'foo@x.com' (SEC-004)."""
    return parseaddr(addr or "")[1].strip().lower()


def _email_domain(addr: str) -> str:
    """Lowercased domain of a (normalized) address, or '' if it has none."""
    n = _norm_email(addr)
    return n.rsplit("@", 1)[1] if "@" in n else ""


def _split_env_emails(env_var: str) -> list:
    """Addresses from a comma/semicolon-separated env var — same shape as the
    ALERT_RECIPIENTS_EXTRA / GFL_AIR_WATCH_RECIPIENTS_EXTRA private-supplement
    pattern already used here."""
    raw = (os.environ.get(env_var, "") or "").replace(";", ",")
    return [a.strip() for a in raw.split(",") if a.strip()]


def load_owner_emails(cfg: dict | None = None) -> set:
    """Trisha's OWN addresses — NEVER suppressed, NEVER dropped by the fail-safe,
    and exempt from the unsubscribe footer/header (the self-lockout guard the goal
    requires). Two sources, UNIONED:
      - config `unsubscribe.owner_addresses` — fine in this PUBLIC repo for a
        non-sensitive address like the arbor-hills@ catch-all, which is ALREADY
        committed in `alert_recipients`; and
      - the `MONITOR_OWNER_EMAILS` env secret — for her PRIVATE addresses
        (proton / gmail) that must NOT be committed to a public repo.
    Populate BOTH so every owner address that appears on ANY recipient list is
    protected; a missing one would let the fail-safe drop her (see send_email)."""
    owners = set()
    if cfg:
        for a in ((cfg.get("unsubscribe") or {}).get("owner_addresses") or []):
            n = _norm_email(a)
            if n:
                owners.add(n)
    for a in _split_env_emails("MONITOR_OWNER_EMAILS"):
        n = _norm_email(a)
        if n:
            owners.add(n)
    return owners


def load_owner_domains(cfg: dict | None = None) -> set:
    """Whole domains that are Trisha's — ANY address at them is an owner (never
    suppressed, held, or footered). Her @trishakunst.com is a Cloudflare catch-all
    that forwards to her, so every alias at it is hers; listing the DOMAIN protects
    all present and future aliases without enumerating them. Two sources UNIONED:
    config `unsubscribe.owner_domains` (a domain is not sensitive — fine in this
    PUBLIC repo) and the `MONITOR_OWNER_DOMAINS` env secret. Leading '@' and case
    are stripped, so '@Trishakunst.COM' and 'trishakunst.com' are the same."""
    domains = set()
    if cfg:
        for d in ((cfg.get("unsubscribe") or {}).get("owner_domains") or []):
            d = (d or "").strip().lower().lstrip("@")
            if d:
                domains.add(d)
    for d in _split_env_emails("MONITOR_OWNER_DOMAINS"):
        d = d.strip().lower().lstrip("@")
        if d:
            domains.add(d)
    return domains


def load_suppressed_emails(owners: set | None = None, cfg: dict | None = None) -> set:
    """Opted-out addresses from the `UNSUBSCRIBED_EMAILS` env secret (comma/
    semicolon list). Deliberately a PRIVATE secret, never a Sheet/config tab: a
    suppression list is third-party PII, and both the case-file Sheet and
    config.yml are PUBLIC. Owner addresses AND owner-domain addresses are removed
    from the result, so an owner can NEVER be suppressed — even by a mistaken
    entry (the self-lockout guard, extended to the whole owner domain)."""
    if owners is None:
        owners = load_owner_emails(cfg)
    owner_domains = load_owner_domains(cfg)
    supp = {_norm_email(a) for a in _split_env_emails("UNSUBSCRIBED_EMAILS")}
    supp -= owners
    if owner_domains:
        supp = {a for a in supp if _email_domain(a) not in owner_domains}
    return supp


def _unsubscribe_settings(cfg: dict) -> tuple:
    """(mailto, postal_address) from config, trimmed. `mailto` being set ARMS the
    apparatus — a working opt-out method is the one thing these alerts must give
    recipients. `postal_address` is OPTIONAL: these are advocacy (non-commercial)
    alerts, so CAN-SPAM's physical-address rule does not bind, and Trisha's call
    (2026-09-13) is to expose no postal address. When postal IS set it is added to
    the footer; when empty the footer simply omits it. There is deliberately no
    separate `enabled` flag — 'mailto configured' is the single arming switch, so a
    recipient can never get an armed message without a live opt-out address in it."""
    u = cfg.get("unsubscribe") or {}
    return (u.get("mailto") or "").strip(), (u.get("postal_address") or "").strip()


def build_list_unsubscribe(mailto: str, recipient: str) -> str:
    """RFC 2369 List-Unsubscribe value: a mailto whose body carries the EXACT
    address to remove, so manual triage (or a future inbox-reader) can add the
    right address to UNSUBSCRIBED_EMAILS without guessing from headers."""
    from urllib.parse import quote
    addr = quote(mailto, safe="@")   # SEC-003: encode the address, like subj/body
    subj = quote("Unsubscribe: Arbor Hills Monitor")
    body = quote(
        "Unsubscribe this address from all Arbor Hills Monitor "
        f"emails: {recipient}"
    )
    return f"<mailto:{addr}?subject={subj}&body={body}>"


def unsubscribe_footer(cfg: dict, mailto: str, postal: str, recipient: str) -> str:
    """The opt-out footer: why they receive it, how to opt out (of ALL monitor
    mail), a clear sender identity, and — only if configured — a physical postal
    address. A working opt-out is the one required element for these advocacy
    alerts; the postal line is optional and omitted when unset (Trisha's call).
    Uses the standard '-- ' signature separator."""
    u = cfg.get("unsubscribe") or {}
    sender = (u.get("sender_identity") or "Arbor Hills Monitor").strip()
    reason = (u.get("receiving_reason")
              or "You are receiving this because your address was added to the "
                 "Arbor Hills Monitor alert list.").strip()
    postal_line = f"{postal}\n" if postal else ""
    return (
        "\n\n-- \n"
        f"{reason}\n"
        f"To unsubscribe from all Arbor Hills Monitor emails, reply to "
        f"this message or email {mailto} (this address: {recipient}).\n"
        f"{sender}\n"
        f"{postal_line}"
    )


class EmailSendError(smtplib.SMTPException):
    """A send_email failure with a REDACTED message: exception type, SMTP code and
    refused-recipient count only, never an address or server response text. Every
    stream logs send failures with `{e}` into world-readable Actions logs, and an
    `SMTPRecipientsRefused` stringifies to a dict keyed by the refused address, so an
    unredacted error could print a private owner address (GitHub masks only the
    whole comma-separated MONITOR_OWNER_EMAILS secret, not one address inside it).
    Subclasses SMTPException (itself an OSError) so existing handlers still catch it.
    Trade-off: the server's response text (e.g. "Username and Password not
    accepted", a rate-limit reply) is dropped with the addresses; the type name and
    SMTP code are what remain for triage, plus how many messages had already gone
    out. Security review L1, 2026-10-06 (the SEC-001 count-only logging rule)."""


def redact_smtp_error(e: BaseException) -> str:
    """Pure: a log-safe description of an SMTP or network failure."""
    parts = [type(e).__name__]
    code = getattr(e, "smtp_code", None)
    if code is not None:
        parts.append(f"SMTP {code}")
    refused = getattr(e, "recipients", None)
    if isinstance(refused, dict):
        parts.append(f"{len(refused)} recipient(s) refused")
    return " / ".join(parts)


def send_email(subject: str, body: str, cfg: dict, recipients: list | None = None,
               log_subject: bool = True) -> bool:
    """Send to all configured recipients via SMTP (TLS). No-op with a warning if
    SMTP env vars are missing (so a dry/local run doesn't crash) — returns False
    in that case, True once at least one message was actually handed to the SMTP
    server, so a caller that needs to distinguish "sent" from "silently skipped"
    can (e.g. _route_urgent_or_digest in watcher.py, which must not record a
    same-day [URGENT] alert as sent — and recap it later — if it never actually
    went out). A mid-send SMTP failure still raises, as an EmailSendError whose
    message is redacted (no addresses, no server text; see EmailSendError).

    `recipients` overrides the audience: when a non-empty list is passed it is used
    VERBATIM (not merged with the shared `alert_recipients` list or the
    ALERT_RECIPIENTS_EXTRA env). This is how a narrowly-scoped alert reaches only a
    subset — e.g. the CivicClerk meeting-watch (ADR 015) sends meeting-logistics
    changes to Trisha alone, not the whole advocacy list. When None/empty, the
    default whole-list behaviour (resolve_recipients) is unchanged.

    CAN-SPAM unsubscribe (ADR 040) — applied HERE so all three multi-recipient
    streams (urgent, digest, GFL-air WATCH) inherit it from one choke point:
      * The shared suppression list (`UNSUBSCRIBED_EMAILS`) is filtered out of
        EVERY send. Owner addresses (load_owner_emails) are never suppressed.
      * A non-owner recipient's message gets a `List-Unsubscribe` mailto header
        and a footer (sender identity + opt-out, plus a postal address only if one
        is configured) — but ONLY when the apparatus is ARMED (unsubscribe.mailto
        set). These are advocacy (non-commercial) alerts, so a working opt-out is
        the one required element; a postal address is optional. Owner copies never
        get the footer.
      * Fail-safe when NOT armed (no unsubscribe.mailto): a non-owner recipient is
        DROPPED rather than sent mail with no way to opt out — self-arming the
        moment the mailto is set. Owners are always sent. If the owner list is
        EMPTY we cannot classify third parties, so we DON'T drop (never risk
        silencing Trisha) and only warn.

    TRAP GUARDS:
      * An emptied SCOPED (explicitly-passed) list sends to NOBODY — it never
        falls through to the default `alert_recipients` audience (that fallthrough
        only runs when NO list was passed). Prevents a suppressed private/scoped
        list from leaking to the coalition list.
      * The URGENT path always includes an owner (arbor-hills@…), so suppression
        and the fail-safe can never empty it → never a spurious False → never a
        silently-unsent-and-unrecapped urgent doc.

    `log_subject=False` keeps the subject out of this function's own log lines
    (the Actions log is public): the owner-only emails (owner_notices.py)
    carry document titles that their constraint keeps out of the log."""
    shown = subject if log_subject else "(owner-only email)"
    scoped = bool(recipients)
    recipients = list(recipients) if scoped else resolve_recipients(cfg)

    owners = load_owner_emails(cfg)
    owner_domains = load_owner_domains(cfg)
    suppressed = load_suppressed_emails(owners, cfg)
    if suppressed and not owners:
        print("[email_alerts] WARNING: UNSUBSCRIBED_EMAILS is set but the owner "
              "list (MONITOR_OWNER_EMAILS / unsubscribe.owner_addresses) is empty "
              "— suppressed addresses are still dropped, but owner self-protection "
              "is inert and the unarmed fail-safe holds no one (a third party "
              "can't be told from an owner). Set the owner list so Trisha's own "
              "addresses can never be suppressed.")
    recipients = [r for r in recipients if _norm_email(r) not in suppressed]

    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not (host and user and password and recipients):
        # An emptied SCOPED list lands here (recipients falsy) and returns False —
        # it does NOT fall through to resolve_recipients (that only happens above,
        # and only when `scoped` is False). This is the private-list-leak guard.
        print(f"[email_alerts] SMTP not configured / no recipients — would send: {shown!r}")
        return False

    mailto, postal = _unsubscribe_settings(cfg)
    # mailto (a working opt-out) is the arming switch; postal is an optional
    # footer line (advocacy alerts don't require a physical address). ADR 040.
    armed = bool(mailto)

    # SEC-003 (classifier-untrusted-input, 2026-10-14): `subject` can carry a
    # raw, filer-controlled nSITE document title (send_urgent_alert's
    # f-string). A literal CR/LF inside it makes EmailMessage raise ValueError
    # the instant the Subject header is set (email.policy.default's header-
    # folding check) -- collapsed ONCE here, in the shared send path, so
    # every caller (urgent, digest, GFL-air watch, ...) is covered without
    # each one having to sanitize its own subject first.
    subject = " ".join(subject.split())

    sender = os.environ.get("SMTP_FROM") or user
    sent = 0
    dropped = []
    skipped_headers = 0
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=ssl.create_default_context())  # SEC-004
            server.login(user, password)
            for recipient in recipients:
                is_owner = (_norm_email(recipient) in owners
                            or _email_domain(recipient) in owner_domains)
                out_body = body
                add_unsub_header = False
                if not is_owner:
                    if armed:
                        out_body = body + unsubscribe_footer(cfg, mailto, postal, recipient)
                        add_unsub_header = True
                    elif owners:
                        # We can positively tell this is a third party AND there is no
                        # opt-out method configured (unarmed) → do NOT send mail with
                        # no way to unsubscribe. Self-arms once unsubscribe.mailto is
                        # set. Owners are never dropped, so a stream that always carries
                        # an owner (e.g. URGENT) can never be emptied here (trap 4).
                        dropped.append(recipient)
                        continue
                    # else: owners empty → can't classify a third party → send as-is
                    # (never risk silencing Trisha); the WARNING above surfaced it.
                msg = EmailMessage()
                try:
                    # SEC-003: header assignment is pure/local (no network I/O
                    # yet) -- a ValueError here (a bad header value) is a
                    # per-message problem, not a connection failure, so it
                    # must not abort every OTHER recipient's send. Subject is
                    # already collapsed above; this remains in case any other
                    # per-recipient header value (e.g. a malformed address
                    # slipping past config) carries an embedded CR/LF.
                    msg["Subject"] = subject
                    msg["From"] = sender
                    msg["To"] = recipient
                    if add_unsub_header:
                        # No List-Unsubscribe-Post header: RFC-8058 one-click needs an
                        # HTTPS POST endpoint, and this static-Pages project has no
                        # backend — mailto is the compliant mechanism.
                        msg["List-Unsubscribe"] = build_list_unsubscribe(mailto, recipient)
                    msg.set_content(out_body)
                except ValueError:
                    skipped_headers += 1
                    continue
                server.send_message(msg)
                sent += 1
    except OSError as e:   # SMTPException, socket, timeout and TLS errors are all OSError
        # Redact before anything can log it: no addresses, no server text. Build the
        # message here but raise OUTSIDE the except block, so the original (with its
        # address-bearing text) is not even kept as __context__.
        failure = f"send failed after {sent} sent: {redact_smtp_error(e)}"
    else:
        failure = None
    if failure is not None:
        raise EmailSendError(failure)
    if dropped:
        # SEC-001: log the COUNT only, never the addresses. Held recipients can
        # include intentionally-private third parties, and Actions logs are
        # world-readable (CWE-532/CWE-359). If addresses are ever needed for
        # triage, route them to a private sink, not this log line.
        print(f"[email_alerts] {len(dropped)} non-owner recipient(s) NOT sent "
              f"{shown!r} — unsubscribe apparatus not armed. Set "
              f"unsubscribe.mailto to send them. Held: {len(dropped)} recipient(s)")
    if skipped_headers:
        # SEC-001/SEC-003: count only, never the address or header value.
        print(f"[email_alerts] {skipped_headers} recipient(s) SKIPPED for "
              f"{shown!r} — a malformed header value (never logged here); "
              f"other recipients were still sent.")
    print(f"[email_alerts] sent {shown!r} to {sent} recipient(s)")
    return sent > 0


def send_test_email(cfg: dict) -> None:
    """Send a one-off test message to the configured recipients to verify SMTP is
    wired up end-to-end. Triggered by the daily workflow's `send_test` input."""
    send_email(
        "[TEST] Arbor Hills monitor — email is working",
        "If you're reading this, the monitor's SMTP alerts are configured "
        "correctly. Urgent alerts (same-day) and the weekly digest will arrive "
        "this way. This is a manual test send; no action needed.",
        cfg,
    )


def send_urgent_alert(parsed, metadata: dict, link: str, cfg: dict) -> bool:
    """Returns True iff the email was actually handed to the SMTP server (False
    if SMTP is unconfigured/no recipients — see send_email). watcher.py's
    _route_urgent_or_digest uses this to avoid recording a same-day [URGENT]
    alert as sent, and recapping it in the Sunday digest, when it never
    actually went out."""
    subject = f"[URGENT] Arbor Hills N2688: {metadata.get('document_name', 'new document')}"
    return send_email(subject, format_urgent_body(parsed, metadata, link, cfg), cfg)


def route_hand_curated_urgent_or_digest(
    *, document_name: str, date_filed: str, doc_type: str, severity: str,
    risks: list | None, key_data_point: str, link: str, cfg: dict, state: dict,
    sent_at: str,
) -> bool:
    """Route a hand-curated document (dedupe-curate intake — never runs through
    egle_doc_parser.parse_document(), so it has no `measurements`/`full_text`)
    into the SAME urgent-vs-digest decision the live nSITE watcher uses, via the
    is_urgent()/send_urgent_alert() this module already exposes.

    Gap this closes (found 2026-09-20): Hand-Curated Files additions otherwise
    have NO path into either `pending_digest` or `pending_urgent_recap` at all —
    two severe YCUA PFOS Notices of Violation sat hand-curated with no same-day
    alert and had to be manually spliced into `pending_digest` by hand, one of
    them TWICE incorrectly the first time (see that session's history). Mirrors
    watcher.py's `_route_urgent_or_digest`, adapted for a record with no
    structured `measurements` — `is_urgent()`'s temperature-threshold path can
    never fire for a hand-curated doc, so routing here rests entirely on the
    caller's own `severity` judgment ("urgent" or not; hand-curation has no
    classifier to call `is_urgent()`'s other branch instead).

    Mutates `state["pending_digest"]` or `state["pending_urgent_recap"]` in
    place (creating either key if absent); does NOT persist `state` to the
    Sheet — same contract as watcher.py's own routing function, so the caller
    writes it via sheet_writer.write_meta() afterward, in the same
    Sheet-row-then-state crash-safety order the rest of this codebase uses.

    `sent_at` is caller-supplied (not computed here, e.g. via datetime.now())
    so this function stays pure and deterministically testable, matching this
    module's existing style (compare `_urgent_recap_record` in watcher.py,
    which takes `sent_at` from the caller's own `_now()` call).

    A failed OR skipped urgent send (SMTP unconfigured / no recipients, OR a
    mid-send SMTP exception — send_email's own docstring: "A mid-send SMTP
    failure still raises (unchanged)") is dropped from BOTH queues — matches
    `_route_urgent_or_digest`'s documented behavior: an alert that never went
    out has nothing to recap. watcher.py's routing function wraps its own
    send_urgent_alert() call in a try/except for exactly this reason; this
    function mirrors that (caught missing here 2026-09-20 on a real send: a
    misconfigured workflow env — SMTP_FROM omitted, so send_email fell back to
    SMTP_USER as the From address, which the relay rejected with "Bad sender
    address syntax" — crashed this function uncaught, unlike the watcher's own
    routing path).

    Returns True iff a real urgent email was actually sent."""
    from egle_doc_parser import ParsedDoc  # local import: avoids a module-load-time

    # dependency from email_alerts.py (imported by every watcher) onto the
    # heavier egle_doc_parser.py (which imports fitz/pymupdf) — ParsedDoc is
    # only needed inside this one function.
    parsed = ParsedDoc(
        summary=key_data_point, key_data_point=key_data_point, doc_type=doc_type,
        risks=list(risks or []), severity=severity, full_text="",
        ocr_applied=False, page_count=0, measurements=[],
    )
    metadata = {"document_name": document_name, "date_filed": date_filed}
    record = {
        "date_filed": date_filed, "document_name": document_name,
        "doc_type": doc_type, "severity": severity, "risks": list(risks or []),
        "key_data_point": key_data_point, "link": link,
    }
    state.setdefault("pending_digest", [])
    state.setdefault("pending_urgent_recap", [])

    if not is_urgent(parsed, cfg):
        state["pending_digest"].append(record)
        return False

    try:
        sent = send_urgent_alert(parsed, metadata, link, cfg)
    except Exception as ae:  # noqa: BLE001 — notification is best-effort, same
        # scope/reasoning as watcher.py's _route_urgent_or_digest: a send
        # failure must not crash the caller, and there is nothing to recap
        # for an alert that never went out.
        print(f"  URGENT ALERT FAILED to send: {document_name[:50]}: {ae}")
        return False
    if sent:
        record["urgent_sent_at"] = sent_at
        state["pending_urgent_recap"].append(record)
    return sent


def send_digest(items: list[dict], cfg: dict, urgent_recap: list[dict] | None = None) -> bool:
    """The weekly digest goes to resolve_recipients(cfg) PLUS anyone in
    DIGEST_RECIPIENTS_EXTRA (comma/semicolon-separated env, private — not
    committed to this PUBLIC repo's config.yml). This is a DIGEST-ONLY
    supplement: send_urgent_alert() above does not read this env, so adding
    someone here gets them the Sunday roundup without also putting them on the
    same-day [URGENT] send. Added 2026-08-21, Trisha's direction, to bring
    Washtenaw commissioners onto the digest ahead of adding them to urgent/
    methane-perimeter alerts later — see SUSPENSE.md for the follow-up dates.

    urgent_recap: items already sent as their own same-day [URGENT] email,
    recapped here under their own labeled section. Kept out of the subject
    line on purpose — the header still reads as "N new documents"; the recap
    section inside the body is what surfaces the urgent items.

    Returns send_email's result (False = nothing handed to SMTP), so the
    watcher can record a skipped digest for the owner weekly email."""
    subject = f"Arbor Hills N2688 digest — {len(items)} new document(s)"
    recipients = merge_extra_recipients(resolve_recipients(cfg), "DIGEST_RECIPIENTS_EXTRA")
    return send_email(subject, format_digest_body(items, urgent_recap), cfg, recipients=recipients)


def send_upcoming(upcoming_block: str, cfg: dict) -> None:
    """Send the digest's "Upcoming Activities" section as its OWN email, scoped
    VERBATIM to `upcoming.recipients` in config (the same `send_email(recipients=…)`
    override the CivicClerk meeting-watch uses, ADR 015). Kept SEPARATE from
    send_digest on purpose: the section is sourced from the PRIVATE Sheet
    (strategy-flavored key dates), so it must never ride in the coalition document
    digest that goes to the full `alert_recipients` list. FAIL-SAFE on the
    confidential direction — if `upcoming.recipients` is unset/empty the section is
    NOT sent at all, rather than falling back to the whole list the way
    `resolve_recipients` would. No-op on an empty block."""
    if not upcoming_block:
        return
    recipients = (cfg.get("upcoming") or {}).get("recipients")
    if not recipients:
        print("[email_alerts] upcoming section not sent: no upcoming.recipients configured")
        return
    send_email(
        "Arbor Hills — Upcoming Activities (next 14 days)", upcoming_block, cfg,
        recipients=recipients,
    )
