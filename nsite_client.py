"""
nsite_client.py — minimal client for EGLE's nSITE document API.

Ported from michigan-egle-database-auto-scraper/EGLE-AQD-database-autoscraper.py,
stripped to the three things this monitor needs:
  - make_session()        — session with the required cookie
  - fetch_site_documents()— full document list for one facility
  - download_pdf()        — fetch one document's PDF bytes to disk

Deliberately DOES NOT inherit the scraper's `doc_date == today` filter — that was
for a daily all-facilities sweep. Backfill needs the full history; the watcher
filters by checkpoint date itself. We also dropped the pandas / CSV-merge
machinery. fetch_all_documents() loops the facilities configured in config.yml
and tags each doc with its facility (the multi-facility design, ADR 008).

Also carries fetch_site_submissions() (Stream K, ADR 020) — a SIBLING nSITE
profile (application/service-request intake, not filed documents) for the same
facilities, added 2026-07-24 after a JPA (wetlands/floodplain permit
application) never showed up in Documents at all. See its own docstring for
why it has a DIFFERENT error-handling contract than fetch_site_documents.

And fetch_site_violations() (Stream L, ADR 023) — a THIRD sibling profile,
EGLE's own enforcement record (formal violation findings). Same raise-don't-
swallow contract as fetch_site_submissions, but with no unique-key field and
therefore no record filter; see its docstring.

And fetch_site_compliance_actions() (Stream M, ADR 028) — a FOURTH sibling
profile, the OTHER half of the enforcement story: the formal actions EGLE takes
in response to a violation (Violation Notices, Consent Orders, Consent
Judgments). Same raise-don't-swallow contract and same no-filter/multiset
posture as fetch_site_violations — its candidate reference number
(cmplActnCmplActnNum) proved non-unique in the live spike (N2688 files one
number, a federal case number, on two records), so it too keeps every record.

And fetch_site_evaluations() (Stream N, ADR 029) — a FIFTH sibling profile,
the underlying INSPECTION record a violation or compliance action stems from.
Same raise-don't-swallow contract as its three siblings, but UNLIKE Violations
and Compliance Actions it DOES filter — the live spike found evalEvalNum IS a
genuine unique key per site (477/477 at N2688, 40/40 at RA, 5/5 at N1504,
1/1 at WRD), so this profile is keyed like fetch_site_submissions rather than
diffed as a multiset. See nsite_evaluations_watcher for why the snapshot still
needs a budget-degradation guard despite the keyed design.

And fetch_site_permits() (Stream O, ADR 030) — a SIXTH sibling profile, the
facility's PERMIT lifecycle (issued -> extended -> expiring -> terminated).
Same raise-don't-swallow contract; DOES filter (like fetch_site_evaluations),
because the live spike found prmtPrmtNum IS a genuine unique key everywhere it
has any records (9/9 at N2688, 4/4 at N1504, 3/3 at P1488, 2/2 at RA, 2/2 at
WRD, 1/1 at P1504, 1/1 at SANL) — so this profile is ref-number-keyed too. This
profile OVERLAPS Stream H's targeted ROP watch (rop_client.py) at exactly three
permits (ROP0000224/0656/0236, confirmed live) but watches a DIFFERENT event
(permit status/lifecycle, not the public-comment trip-wire) — see
nsite_permits_watcher for the disambiguation.
"""
from __future__ import annotations

import gzip
import re
import time
import urllib.parse
from datetime import datetime, date
from typing import Optional

import requests

import poison_doc_extractor as pde

NSITE_BASE = "https://mienviro.michigan.gov"
SETTINGS_URL = f"{NSITE_BASE}/nsite/api/settings/getWslSettings"
DOCS_ENDPOINT = (
    f"{NSITE_BASE}/nsite/ss/api/nsite-explorer/default-mode"
    "/profiles/4-documents/1-documents"
)
# Sibling profile to DOCS_ENDPOINT — application/service-request INTAKE (a
# stable Submission Reference Number, form name, program area, status),
# distinct from filed Documents. Found 2026-07-24 tracing a JPA that never
# appeared in Documents: EGLE's own settings manifest lists 7 profiles under
# "2-environmental-interests"/"1-profile" this monitor has never polled;
# Submissions is the one carrying "Submission Reference Number", the field
# named in MiEnviro's own subscription-alert emails.
SUBMISSIONS_ENDPOINT = (
    f"{NSITE_BASE}/nsite/ss/api/nsite-explorer/default-mode"
    "/profiles/2-environmental-interests/2-submissions"
)
# Another sibling profile — EGLE's own ENFORCEMENT record (a formal violation
# finding, not a filing or a permit status). Same query shape as the two above;
# anonymous, no auth. Confirmed live 2026-07-24 and re-confirmed across all 19
# `nsite_sites` on 2026-08-04 (Stream L, ADR 023): 360 records total, all
# carrying exactly the eight VIOLATION_FIELDS below, with real history only at
# N2688 (58), RA (299) and N1504 (3).
VIOLATIONS_ENDPOINT = (
    f"{NSITE_BASE}/nsite/ss/api/nsite-explorer/default-mode"
    "/profiles/3-compliance/2-violations"
)
DOWNLOAD_BASE = f"{NSITE_BASE}/ncore/downloadpdf"
# In-browser quirk (harmless): opening a downloadpdf/<id> link can render an
# ASP.NET "Server Error in '/ncore' Application" page WHILE STILL downloading the
# file — an intermittent portal-side fault, not a bad document or a bad link. A
# direct server-side GET (which is all this client ever does) returns a clean
# HTTP 200 application/pdf; verified 2026-07-13 against doc 7022559137978826651
# (the 181pp WOI report): plain, ranged, and HEAD requests all returned the full
# valid %PDF, cookie or not. So the monitor is unaffected; the error page only
# appears in interactive browser use. See README "A note on the document links".
# Native-file endpoint: serves the document's ORIGINAL bytes (legacy .doc, zips,
# images) where downloadpdf returns HTTP 400 "PDF content could not be found"
# for any non-PDF source. Used for stub links to docs the parser can't ingest.
DOWNLOAD_FILE_BASE = f"{NSITE_BASE}/ncore/downloadfile"


def native_download_url(doc_id: str) -> str:
    """The downloadfile URL for a doc_id — the original bytes, not a PDF render."""
    return f"{DOWNLOAD_FILE_BASE}/{doc_id}"


def make_session() -> requests.Session:
    """Return a requests.Session primed with a valid nSITE cookie."""
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "Mozilla/5.0 (compatible; arbor-hills-monitor/1.0)"}
    )
    s.get(SETTINGS_URL, timeout=30)
    return s


def _parse_doc_url(html_anchor: str) -> str:
    """Extract href from '<a href="URL">Download</a>' string."""
    m = re.search(r'href="([^"]+)"', html_anchor or "")
    return m.group(1) if m else (html_anchor or "")


def _normalize(raw: dict) -> dict:
    """Convert a raw nSITE document dict into the fields the pipeline uses."""
    descr = raw.get("docMgmtDocDescr", "")
    srctype = raw.get("docMgmtSourcetype", descr)
    date_str = raw.get("docMgmtDocRvcdCreatedDate", "")
    doc_id = str(raw.get("docMgmtDocMgmtId", ""))

    parsed_date: Optional[date] = None
    if date_str:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(date_str[: len(fmt) + 2], fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            # Last resort: take the leading YYYY-MM-DD if present.
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
            if m:
                parsed_date = date(int(m[1]), int(m[2]), int(m[3]))

    raw_url = _parse_doc_url(raw.get("docMgmtDocurl", ""))
    doc_url = raw_url or f"{DOWNLOAD_BASE}/{doc_id}"
    if doc_url.startswith("/"):
        doc_url = NSITE_BASE + doc_url

    return {
        "doc_id": doc_id,
        "date_filed": parsed_date.isoformat() if parsed_date else "",
        "date_obj": parsed_date,
        "document_name": descr or srctype or f"doc-{doc_id}",
        "type_name": descr or srctype,
        "doc_url": doc_url,
        "category": raw.get("docMgmtCategory", ""),
    }


def fetch_site_documents(session: requests.Session, nsite_id: str) -> list[dict]:
    """Return the full list of normalized documents for one facility (no date
    filter). Returns [] on persistent error."""
    query_params = urllib.parse.quote('{"filter":[{"id":"' + str(nsite_id) + '"}]}')
    url = (
        f"{DOCS_ENDPOINT}"
        f"?responseContentType=application/json"
        f"&includeMetadataInResponse=true"
        f"&loadChildren=true"
        f"&queryParams={query_params}"
        f"&filterString="
    )
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results/detail/{nsite_id}/Documents"

    for attempt in range(3):
        try:
            r = session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json"},
                timeout=30,
            )
            data = r.json()
            raw_docs = data.get("queryResults", [])
            return [_normalize(d) for d in raw_docs if d.get("docMgmtDocMgmtId")]
        except Exception:
            if attempt == 2:
                return []
            time.sleep(2 ** attempt)
    return []


def fetch_all_documents(session: requests.Session, cfg: dict) -> list[dict]:
    """Fetch and concatenate the document lists for every facility in
    cfg["facilities"], tagging each doc with facility_srn / facility_name.

    nSITE doc_ids are globally unique across these facilities (verified 0 pairwise
    overlap), so the combined list safely shares one Sheet + one _state tab with
    no composite key (ADR 008). A facility that returns [] (transient error /
    empty record) simply contributes nothing; it never aborts the others.
    """
    docs: list[dict] = []
    for f in cfg["facilities"]:
        for d in fetch_site_documents(session, f["id"]):
            d["facility_srn"] = f["srn"]
            d["facility_name"] = f["name"]
            docs.append(d)
    return docs


class NsiteFetchError(RuntimeError):
    """A facility's submissions couldn't be fetched cleanly (network error,
    non-200, or a response missing the 'queryResults' key) after retries.

    UNLIKE fetch_site_documents (which swallows failures and returns [] because
    its caller, fetch_all_documents/backfill/watcher, is an append-only doc
    accumulator for which a transient miss is harmless), fetch_site_submissions
    NEVER returns [] for a failure — only for a genuinely valid, structurally-
    sound response that happens to list zero submissions. This distinction
    matters because nsite_submissions_watcher DIFFS this list against the last
    snapshot: a silently-swallowed failure returned as [] would be misread as
    'every submission removed' and fire a false change alert — the same class
    of silent corruption RopFetchError/MmdFetchError/RideFetchError exist to
    prevent for their sources."""


def _normalize_submission(raw: dict) -> dict:
    """Convert a raw nSITE submission dict into the fields the Submissions
    watch tracks. `ref_num` (submSubmRefNum) is the stable, globally-unique
    key: a ref_num not seen before means a BRAND-NEW filing (the JPA case this
    watch exists for); an already-seen ref_num with a different `status` (or
    other field) means an existing filing advanced — see
    nsite_submissions_watcher.summarize_submissions_change for how the two are
    told apart."""
    date_str = raw.get("submRcvdDate", "")
    parsed_date: Optional[date] = None
    if date_str:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                parsed_date = datetime.strptime(date_str[: len(fmt) + 2], fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
            if m:
                parsed_date = date(int(m[1]), int(m[2]), int(m[3]))
    return {
        "ref_num": raw.get("submSubmRefNum", ""),
        "form_name": raw.get("submFormName", ""),
        "form_type": raw.get("submRefFormTypeDescr", ""),
        "program_area": raw.get("submRefProgramAreaDescr", ""),
        "status": raw.get("submStatus", ""),
        "received_date": parsed_date.isoformat() if parsed_date else "",
        "descr": raw.get("submDescr") or "",
    }


def fetch_site_submissions(session: requests.Session, nsite_id: str) -> list[dict]:
    """Return the full list of normalized submissions for one facility.

    Raises NsiteFetchError after 3 attempts on ANY network/HTTP/structural
    failure — see NsiteFetchError for why this deliberately does NOT mirror
    fetch_site_documents' swallow-and-return-[] contract."""
    query_params = urllib.parse.quote('{"filter":[{"id":"' + str(nsite_id) + '"}]}')
    url = (
        f"{SUBMISSIONS_ENDPOINT}"
        f"?responseContentType=application/json"
        f"&includeMetadataInResponse=true"
        f"&loadChildren=true"
        f"&queryParams={query_params}"
        f"&filterString="
    )
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results/detail/{nsite_id}/Documents"

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if "queryResults" not in data:
                raise NsiteFetchError(
                    f"submissions response for facility {nsite_id} is missing "
                    f"'queryResults' — nSITE may have changed its response shape"
                )
            raw_subs = data["queryResults"]
            return [_normalize_submission(s) for s in raw_subs if s.get("submSubmRefNum")]
        except Exception as e:  # noqa: BLE001 — network/HTTP/structural: retry, then raise loud
            last_exc = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise NsiteFetchError(
        f"GET submissions for facility {nsite_id} failed after 3 attempts: {last_exc}"
    ) from last_exc


# The eight fields EGLE's Violations profile serves, renamed to the short
# readable names the watch diffs on. Confirmed 2026-08-04 to be present on ALL
# 360 records across every site that has any (RA/N2688/N1504) — one distinct
# field-set per site, no optional fields, no nesting. Ordered as EGLE serves
# them. See docs/decisions/023-nsite-violations-watch.md.
VIOLATION_FIELDS = (
    "category", "viol_type", "status", "start_date", "comments", "program",
    "eval_num", "eval_type",
)

_VIOLATION_RAW_KEYS = {
    "category": "violRefViolCatgDescr",      # "Rule 1001: Performance tests by owner"
    "viol_type": "violRefViolTypeDescr",     # "Testing/Sampling", "2nd VN Notice"
    "status": "violRefViolStatDescr",        # "Active - Addressed not Resolved"
    "start_date": "violNonCmplStartDate",    # ISO datetime w/ UTC offset
    "comments": "violViolNotifCmnts",        # free text: permit + condition cites
    "program": "violDescr",                  # "AQD - Air", "WRD - NPDES"
    "eval_num": "evalEvalNum",               # the parent evaluation's number
    "eval_type": "evalRefEvalTypeDescr",     # "On-Site Inspection"
}


class NsiteStructuralError(NsiteFetchError):
    """A response that came back cleanly but whose SHAPE this client cannot
    safely interpret — as opposed to a network/HTTP blip.

    A subclass, not a parallel class, so every existing `except NsiteFetchError`
    still catches it and no caller's behavior changes silently. It exists so a
    caller that WANTS to can tell the two apart: a transient fetch failure is
    correctly skip-and-warn (it'll work tomorrow), while a structural break is
    provably NOT transient and would otherwise go quiet forever behind a green
    build."""


def _normalize_violation(raw: dict) -> dict:
    """Convert a raw nSITE violation dict into the fields the Violations watch
    diffs on.

    There is deliberately NO unique-key field here, unlike Submissions'
    `submSubmRefNum` — verified 2026-08-04 across all 360 live records: not one
    of the eight fields is unique within a site's record set, and neither is any
    composite of them (a 5-field composite still leaves 191 collisions on RA's
    299 records). EGLE genuinely files repeated identical rows. That is why
    nsite_violations_watcher diffs a full-record Counter MULTISET rather than
    keying by id — see its module docstring.

    Two deliberate canonicalizations, both of which exist to stop a
    representation change from firing a false "changed" alert:
      - `start_date` -> a bare ISO date. The raw value carries a UTC offset
        ("...-04:00" in EDT, "...-05:00" in EST); the calendar date is the
        signal and the offset is not.
      - `comments` -> CRLF/CR collapsed to LF and stripped. Real values contain
        "\\r\\n"; a server-side line-ending change is not an enforcement event.

    Every other field is passed through with `or ""` (NOT `.get(f, "")`):
    `program` is genuinely null on 17 of RA's 299 records, so a present-but-null
    key must normalize to the same "" that an absent key would, or the day EGLE
    serves "" instead of null the hash flips for no real reason."""
    date_str = raw.get(_VIOLATION_RAW_KEYS["start_date"]) or ""
    if not isinstance(date_str, str):
        # EGLE serves ISO strings here today, but a sibling EGLE ArcGIS feed
        # (see mmd_client) serves epoch-ms integers for its dates. A non-str
        # would raise TypeError out of the slicing/regex below — NOT the
        # ValueError the parse paths catch — escape into fetch_site_violations'
        # broad retry loop, and surface as a permanent NsiteFetchError that
        # blinds the whole site. Coerce so it simply fails soft to "".
        date_str = str(date_str)
    parsed_date: Optional[date] = None
    if date_str:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(date_str[: len(fmt) + 2], fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
            if m:
                # date() rejects an out-of-range day (a real "2026-02-30" would
                # otherwise raise here, escape into fetch_site_violations' retry
                # loop, and blind the WHOLE site behind a permanent
                # NsiteFetchError over one bad record). Every other parse path
                # above is fail-soft; this one must be too.
                try:
                    parsed_date = date(int(m[1]), int(m[2]), int(m[3]))
                except (ValueError, TypeError):
                    parsed_date = None
    comments = (raw.get(_VIOLATION_RAW_KEYS["comments"]) or "")
    out = {f: (raw.get(_VIOLATION_RAW_KEYS[f]) or "") for f in VIOLATION_FIELDS}
    out["start_date"] = parsed_date.isoformat() if parsed_date else ""
    out["comments"] = comments.replace("\r\n", "\n").replace("\r", "\n").strip()
    return out


def fetch_site_violations(session: requests.Session, nsite_id: str) -> list[dict]:
    """Return the full list of normalized violations for one nSITE site.

    Raises NsiteFetchError after 3 attempts on ANY network/HTTP/structural
    failure — the same contract as fetch_site_submissions and for the same
    reason (nsite_violations_watcher DIFFS this list, so a swallowed failure
    returned as [] would read as "every violation resolved at once"), NOT
    fetch_site_documents' swallow-and-return-[].

    UNLIKE fetch_site_submissions, this keeps EVERY element of `queryResults`
    unconditionally. Submissions can filter on `submSubmRefNum` because that
    field is its guaranteed unique key; Violations has no such field, so any
    filter here would silently drop real enforcement records. If nSITE ever
    serves a non-dict element, the AttributeError propagates into the retry
    loop and surfaces as a loud NsiteFetchError — the correct outcome for a
    structural break, not a quiet partial list.

    An empty list is a VALID result (16 of the 19 watched sites have zero
    violations); only a fetch/structural failure raises."""
    query_params = urllib.parse.quote('{"filter":[{"id":"' + str(nsite_id) + '"}]}')
    url = (
        f"{VIOLATIONS_ENDPOINT}"
        f"?responseContentType=application/json"
        f"&includeMetadataInResponse=true"
        f"&loadChildren=true"
        f"&queryParams={query_params}"
        f"&filterString="
    )
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results/detail/{nsite_id}/Documents"

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if "queryResults" not in data:
                raise NsiteFetchError(
                    f"violations response for site {nsite_id} is missing "
                    f"'queryResults' — nSITE may have changed its response shape"
                )
            if data.get("hasResultsRemaining"):
                # The envelope carries `hasResultsRemaining`/`totalCount` but
                # leaves both null today — every site returns its full set in
                # one response (verified across all 19 on 2026-08-04). If nSITE
                # ever enables server-side paging, a partial page would be
                # INDISTINGUISHABLE from a shrunken record set: the caller's
                # multiset diff would read the first 100 of RA's 299 records as
                # "199 violation records removed" and email that as fact. Fail
                # loudly instead — a paged response needs real pagination
                # support, not a silent truncation.
                raise NsiteStructuralError(
                    f"violations response for site {nsite_id} reports "
                    f"hasResultsRemaining — nSITE has started paging this "
                    f"profile and this client would otherwise diff a PARTIAL "
                    f"page as mass deletions. Pagination support is needed."
                )
            return [_normalize_violation(v) for v in data["queryResults"]]
        except NsiteStructuralError:
            # Re-raise IMMEDIATELY, before the retry loop can swallow it and
            # re-raise it as a generic NsiteFetchError. Two reasons: retrying a
            # shape change is pointless (it will fail identically), and the
            # caller distinguishes the two types deliberately — a generic
            # NsiteFetchError is treated as transient (skip-and-warn, exit 0),
            # which is exactly the silent-forever outcome this class exists to
            # avoid.
            raise
        except Exception as e:  # noqa: BLE001 — network/HTTP: retry, then raise loud
            last_exc = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise NsiteFetchError(
        f"GET violations for site {nsite_id} failed after 3 attempts: {last_exc}"
    ) from last_exc


# Another sibling profile — EGLE's own COMPLIANCE ACTIONS (the formal actions
# the regulator takes in response to a violation: Violation Notices, Consent
# Orders, Consent Judgments), the documented other half of the enforcement
# story fetch_site_violations watches. Same query shape as the three profiles
# above; anonymous, no auth. Confirmed live 2026-07-24 and re-confirmed across
# the 5 known sites on 2026-08-08 (Stream M, ADR 028): 51 records total, all
# carrying exactly the six COMPLIANCE_ACTION_FIELDS below, with real history
# only at N2688 (39), RA (10) and N1504 (2).
COMPLIANCE_ACTIONS_ENDPOINT = (
    f"{NSITE_BASE}/nsite/ss/api/nsite-explorer/default-mode"
    "/profiles/3-compliance/3-compliance-actions"
)

# The six fields EGLE's Compliance Actions profile serves, renamed to short
# readable names. Confirmed 2026-08-08 present on ALL 51 records across every
# site that has any (N2688/RA/N1504) — one distinct field-set per site, no
# optional fields, no nesting. `num` leads because it is the closest thing to
# an identifier (the Violation-Notice / order number), so the multiset diff's
# ADDED/REMOVED lines lead with it — see nsite_compliance_actions_watcher.
COMPLIANCE_ACTION_FIELDS = (
    "num", "type", "status", "action_date", "category", "program",
)

_COMPLIANCE_ACTION_RAW_KEYS = {
    "num": "cmplActnCmplActnNum",              # "VN-019436", "5:21-cv-12098-S", "16-2015"
    "type": "cmplActnRefCmplActnTypeDescr",    # "Violation Notice", "Consent Judgment"
    "status": "cmplActnRefCmplActnStatDescr",  # "Issued", "Closed", "Entered", "Terminated"
    "action_date": "cmplActnActnDate",         # ISO datetime w/ UTC offset
    "category": "cmplActnRefCmplActnCatgDescr",  # "Administrative", "Civil"
    "program": "cmplActnRefProgramAreaDescr",  # "AQD - Air", "WRD - NPDES"
}


def _normalize_compliance_action(raw: dict) -> dict:
    """Convert a raw nSITE compliance-action dict into the fields the watch
    diffs on.

    There is deliberately NO unique-key field, unlike Submissions'
    `submSubmRefNum`. The candidate — `cmplActnCmplActnNum` — proved non-unique
    in the 2026-08-08 live spike: N2688 files the federal case number
    `5:21-cv-12098-S` on TWO records (a Consent Order entered on two dates). So
    nsite_compliance_actions_watcher diffs a full-record Counter MULTISET rather
    than keying by it, exactly like the Violations watch — see its module
    docstring.

    One canonicalization, matching _normalize_violation, exists to stop a
    representation change from firing a false "changed" alert:
      - `action_date` -> a bare ISO date. The raw value carries a UTC offset
        ("...-04:00" in EDT, "...-05:00" in EST); the calendar date is the
        signal and the offset is not, so the EDT->EST flip each fall must not
        read as a change.

    There is no free-text field here (all six are controlled vocabularies or a
    reference number), so no CRLF-collapsing is needed the way `comments`
    required it.

    Every field is read with `or ""` (NOT `.get(f, "")`) for parity with
    _normalize_violation: a present-but-null key must normalize to the same ""
    an absent key would, so the day EGLE serves "" instead of null the hash
    does not flip for no real reason (no nulls were observed in the spike, but
    the guard is free)."""
    date_str = raw.get(_COMPLIANCE_ACTION_RAW_KEYS["action_date"]) or ""
    if not isinstance(date_str, str):
        # Same soft-fail as _normalize_violation: a non-str (a sibling EGLE
        # ArcGIS feed serves epoch-ms ints for dates) would raise TypeError out
        # of the slicing/regex below — NOT the ValueError the parse paths catch
        # — escape into fetch_site_compliance_actions' broad retry loop, and
        # surface as a permanent NsiteFetchError blinding the whole site.
        date_str = str(date_str)
    parsed_date: Optional[date] = None
    if date_str:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(date_str[: len(fmt) + 2], fmt).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
            if m:
                # date() rejects an out-of-range day; a real "2026-02-30" would
                # otherwise raise here, escape into the retry loop, and blind
                # the WHOLE site over one bad record. Every other parse path is
                # fail-soft; this one must be too.
                try:
                    parsed_date = date(int(m[1]), int(m[2]), int(m[3]))
                except (ValueError, TypeError):
                    parsed_date = None
    out = {f: (raw.get(_COMPLIANCE_ACTION_RAW_KEYS[f]) or "") for f in COMPLIANCE_ACTION_FIELDS}
    out["action_date"] = parsed_date.isoformat() if parsed_date else ""
    return out


def fetch_site_compliance_actions(session: requests.Session, nsite_id: str) -> list[dict]:
    """Return the full list of normalized compliance actions for one nSITE site.

    Raises NsiteFetchError after 3 attempts on ANY network/HTTP/structural
    failure — the same contract as fetch_site_violations / fetch_site_submissions
    and for the same reason (nsite_compliance_actions_watcher DIFFS this list,
    so a swallowed failure returned as [] would read as "every compliance action
    closed at once"), NOT fetch_site_documents' swallow-and-return-[].

    Like fetch_site_violations, this keeps EVERY element of `queryResults`
    unconditionally — the CA reference number is not a guaranteed-unique key
    (see _normalize_compliance_action), so any filter would silently drop real
    enforcement records. A non-dict element surfaces as a loud NsiteFetchError.

    An empty list is a VALID result (P1488, WRD and the 14 dormant sites have
    zero compliance actions); only a fetch/structural failure raises."""
    query_params = urllib.parse.quote('{"filter":[{"id":"' + str(nsite_id) + '"}]}')
    url = (
        f"{COMPLIANCE_ACTIONS_ENDPOINT}"
        f"?responseContentType=application/json"
        f"&includeMetadataInResponse=true"
        f"&loadChildren=true"
        f"&queryParams={query_params}"
        f"&filterString="
    )
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results/detail/{nsite_id}/Documents"

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if "queryResults" not in data:
                raise NsiteFetchError(
                    f"compliance-actions response for site {nsite_id} is missing "
                    f"'queryResults' — nSITE may have changed its response shape"
                )
            if data.get("hasResultsRemaining"):
                # Null on every site today (verified 2026-08-08). If nSITE ever
                # enables server-side paging, a partial page would be
                # INDISTINGUISHABLE from a shrunken record set: the caller's
                # multiset diff would read a truncated page as mass deletions
                # and email it as fact. Fail loudly instead — the same guard as
                # fetch_site_violations.
                raise NsiteStructuralError(
                    f"compliance-actions response for site {nsite_id} reports "
                    f"hasResultsRemaining — nSITE has started paging this "
                    f"profile and this client would otherwise diff a PARTIAL "
                    f"page as mass deletions. Pagination support is needed."
                )
            return [_normalize_compliance_action(c) for c in data["queryResults"]]
        except NsiteStructuralError:
            # Re-raise immediately, before the retry loop can swallow it into a
            # generic NsiteFetchError: retrying a shape change is pointless, and
            # the caller distinguishes the two types deliberately (a generic
            # NsiteFetchError is treated as transient skip-and-warn, the exact
            # silent-forever outcome this class exists to avoid).
            raise
        except Exception as e:  # noqa: BLE001 — network/HTTP: retry, then raise loud
            last_exc = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise NsiteFetchError(
        f"GET compliance actions for site {nsite_id} failed after 3 attempts: {last_exc}"
    ) from last_exc


# Another sibling profile — the underlying INSPECTION record. A Violations
# record already carries `evalEvalNum` (see VIOLATION_FIELDS' eval_num), so an
# evaluation can be joined back to the violation(s) it produced; watching
# Evaluations gives visibility into new inspections — the event that often
# PRECEDES a violation or compliance action — rather than only their
# downstream consequences. Same query shape as the three profiles above;
# anonymous, no auth. Confirmed live 2026-08-08 and re-confirmed across all 19
# `nsite_sites` (Stream N, ADR 029): 525 records total, all carrying exactly
# the eight EVALUATION_FIELDS below, with real history at N2688 (477, most
# recent 2026-08-07 — actively ongoing), RA (40), N1504 (5), P1488 (2) and
# WRD (1).
EVALUATIONS_ENDPOINT = (
    f"{NSITE_BASE}/nsite/ss/api/nsite-explorer/default-mode"
    "/profiles/3-compliance/1-evaluations"
)

# The eight fields EGLE's Evaluations profile serves, renamed to short
# readable names. Confirmed 2026-08-08 present on ALL 525 records across every
# site that has any (N2688/RA/N1504/P1488/WRD) — one distinct key-set per
# site, no optional fields, no nesting. `eval_num` leads because, UNLIKE
# Violations/Compliance Actions, it IS a genuine unique key (see
# _normalize_evaluation) — it is what nsite_evaluations_watcher diffs on.
EVALUATION_FIELDS = (
    "eval_num", "program_area", "eval_type", "eval_category", "permit_num",
    "start_date", "sample_transmit_date", "site_name",
)

_EVALUATION_RAW_KEYS = {
    "eval_num": "evalEvalNum",                    # "E-AHZX-TW7C-8EN5" — stable, unique per site
    "program_area": "evalRefProgramAreaDescr",     # "AQD - Air", "WRD - NPDES"
    "eval_type": "evalRefEvalTypeDescr",           # "Records Review", "Stormwater Inspection"
    "eval_category": "evalRefEvalCatgDescr",       # "On-Site Inspection"
    "permit_num": "evalPrmtNum",                   # cross-references the Permits profile; often null
    "start_date": "evalStartDate",                 # ISO datetime w/ UTC offset
    "sample_transmit_date": "evalSmplTransmtDate",  # ISO datetime w/ UTC offset; null in 525/525 live
    "site_name": "siteName",                       # site name echo
}


def _parse_egle_date(date_str) -> str:
    """Shared date-normalization for the two date-shaped Evaluations fields
    (start_date, sample_transmit_date) — inlined logic elsewhere in this file
    (_normalize_violation/_normalize_compliance_action) is duplicated once
    per field; here it's needed TWICE in the same function, so factoring it
    out avoids a third copy-paste of the same six-line block within one
    function. Same fail-soft contract as its siblings: a non-str (a sibling
    EGLE ArcGIS feed serves epoch-ms ints for dates) or an out-of-range day
    (a real "2026-02-30") must degrade to "" rather than raise and blind the
    whole site behind a permanent NsiteFetchError over one bad record."""
    if date_str is None:
        return ""
    if not isinstance(date_str, str):
        date_str = str(date_str)
    if not date_str:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str[: len(fmt) + 2], fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except (ValueError, TypeError):
            return ""
    return ""


def _normalize_evaluation(raw: dict) -> dict:
    """Convert a raw nSITE evaluation dict into the fields the watch diffs on.

    UNLIKE _normalize_violation/_normalize_compliance_action, `eval_num` IS a
    genuine unique key within a site's record set — verified 2026-08-08 across
    all 525 live records (477/477 at N2688, 40/40 at RA, 5/5 at N1504, 1/1 at
    WRD) — so nsite_evaluations_watcher diffs a REF-NUMBER-KEYED snapshot (the
    Submissions idiom), not a Counter multiset.

    Two date fields need the same offset-stripping canonicalization
    _normalize_violation's start_date gets (a UTC-offset flip each EDT/EST
    transition must not read as a change): `start_date` and
    `sample_transmit_date`. The latter is null on all 525 live records, but
    the schema promises it and a future evaluation could populate it.

    Every field is read with `or ""` (NOT `.get(f, "")`), matching
    _normalize_violation/_normalize_compliance_action: a present-but-null key
    (permit_num is null on 470/477 at N2688) must normalize to the same "" an
    absent key would."""
    out = {f: (raw.get(_EVALUATION_RAW_KEYS[f]) or "") for f in EVALUATION_FIELDS}
    out["start_date"] = _parse_egle_date(raw.get(_EVALUATION_RAW_KEYS["start_date"]))
    out["sample_transmit_date"] = _parse_egle_date(
        raw.get(_EVALUATION_RAW_KEYS["sample_transmit_date"]))
    return out


def fetch_site_evaluations(session: requests.Session, nsite_id: str) -> list[dict]:
    """Return the full list of normalized evaluations for one nSITE site.

    Raises NsiteFetchError after 3 attempts on ANY network/HTTP/structural
    failure — the same contract as fetch_site_violations / fetch_site_submissions
    / fetch_site_compliance_actions (nsite_evaluations_watcher DIFFS this list,
    so a swallowed failure returned as [] would read as "every evaluation
    withdrawn at once"), NOT fetch_site_documents' swallow-and-return-[].

    UNLIKE fetch_site_violations/fetch_site_compliance_actions, this DOES
    filter — on `evalEvalNum` present, the same filter fetch_site_submissions
    applies to `submSubmRefNum` — because eval_num is this profile's genuine
    unique diff key (see _normalize_evaluation) and a keyless record cannot be
    placed in a ref-number-keyed snapshot. No such record has been observed
    live; if nSITE ever serves one, it is silently excluded from the diff
    rather than raising, matching fetch_site_submissions' precedent.

    An empty list is a VALID result (14 of the 19 watched sites have zero
    evaluations); only a fetch/structural failure raises."""
    query_params = urllib.parse.quote('{"filter":[{"id":"' + str(nsite_id) + '"}]}')
    url = (
        f"{EVALUATIONS_ENDPOINT}"
        f"?responseContentType=application/json"
        f"&includeMetadataInResponse=true"
        f"&loadChildren=true"
        f"&queryParams={query_params}"
        f"&filterString="
    )
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results/detail/{nsite_id}/Documents"

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if "queryResults" not in data:
                raise NsiteFetchError(
                    f"evaluations response for site {nsite_id} is missing "
                    f"'queryResults' — nSITE may have changed its response shape"
                )
            if data.get("hasResultsRemaining"):
                # Null on every site today (verified 2026-08-08). If nSITE ever
                # enables server-side paging, a partial page would be
                # INDISTINGUISHABLE from a shrunken record set: the caller's
                # ref-keyed diff would read the missing tail as evaluations
                # withdrawn and email that as fact. Fail loudly instead — the
                # same guard as fetch_site_violations/fetch_site_compliance_actions.
                raise NsiteStructuralError(
                    f"evaluations response for site {nsite_id} reports "
                    f"hasResultsRemaining — nSITE has started paging this "
                    f"profile and this client would otherwise diff a PARTIAL "
                    f"page as mass withdrawals. Pagination support is needed."
                )
            return [_normalize_evaluation(e) for e in data["queryResults"]
                    if e.get("evalEvalNum")]
        except NsiteStructuralError:
            # Re-raise immediately, before the retry loop can swallow it into a
            # generic NsiteFetchError: retrying a shape change is pointless, and
            # the caller distinguishes the two types deliberately (a generic
            # NsiteFetchError is treated as transient skip-and-warn, the exact
            # silent-forever outcome this class exists to avoid).
            raise
        except Exception as e:  # noqa: BLE001 — network/HTTP: retry, then raise loud
            last_exc = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise NsiteFetchError(
        f"GET evaluations for site {nsite_id} failed after 3 attempts: {last_exc}"
    ) from last_exc


# A SIXTH sibling profile — the facility's PERMIT lifecycle (issued ->
# extended -> expiring -> terminated), broader than Stream H's targeted ROP
# watch (every permit type, not just Air ROPs). Same query shape as the four
# profiles above; anonymous, no auth. Confirmed live 2026-08-22 across all 19
# `nsite_sites` (Stream O, ADR 030): 22 records total, all carrying exactly
# the seven PERMIT_FIELDS below, with real history at N2688 (9, incl. the
# Air ROP `ROP0000224`), N1504 (4, incl. `ROP0000656`), P1488 (3, incl.
# `ROP0000236`), RA (2), WRD (2), P1504 (1) and SANL (1).
PERMITS_ENDPOINT = (
    f"{NSITE_BASE}/nsite/ss/api/nsite-explorer/default-mode"
    "/profiles/2-environmental-interests/1-permits"
)

# The seven fields EGLE's Permits profile serves, renamed to short readable
# names. Confirmed 2026-08-22 present on ALL 22 live records across every site
# that has any — one distinct key-set, no optional fields, no nesting (also
# confirmed against the response's own `lookups.metadata` field descriptions).
# `prmt_num` leads because, like `eval_num`, it IS a genuine unique key (see
# _normalize_permit) — it is what nsite_permits_watcher diffs on.
PERMIT_FIELDS = (
    "prmt_num", "status", "category", "permit_type",
    "effective_date", "expiration_date", "termination_date",
)

_PERMIT_RAW_KEYS = {
    "prmt_num": "prmtPrmtNum",                # "ROP0000224", "MIS210766" — stable, unique per site
    "status": "prmtRefPrmtStatDescr",         # "Extended", "In Effect", "Terminated", "Expired"
    "category": "prmtRefPrmtCatgDescr",       # "Air Renewable Operating Permit", "NPDES ... (COC)"
    "permit_type": "prmtPrmtTypeDescr",       # null on 18/22 live records; e.g. "SW-Industrial CY2"
    "effective_date": "prmtEfctvDate",        # ISO datetime w/ UTC offset
    "expiration_date": "prmtExprDate",        # ISO datetime w/ UTC offset; often null
    "termination_date": "prmtTermDate",       # ISO datetime w/ UTC offset; null until terminated
}


def _normalize_permit(raw: dict) -> dict:
    """Convert a raw nSITE permit dict into the fields the watch diffs on.

    UNLIKE _normalize_violation/_normalize_compliance_action, `prmt_num` IS a
    genuine unique key within a site's record set — verified 2026-08-22 across
    every site with any permits (9/9 N2688, 4/4 N1504, 3/3 P1488, 2/2 RA,
    2/2 WRD, 1/1 P1504, 1/1 SANL) — so nsite_permits_watcher diffs a
    REF-NUMBER-KEYED snapshot (the Submissions/Evaluations idiom), not a
    Counter multiset.

    Three date fields need the same offset-stripping canonicalization
    `_parse_egle_date` already provides for `_normalize_evaluation` — reused
    here rather than re-duplicating the inline parsing
    `_normalize_violation`/`_normalize_compliance_action` still carry (this is
    the third caller, and the first with three date fields in one record), so
    a UTC-offset flip each EDT/EST transition must not read as a change on any
    of the three.

    Every field is read with `or ""` (NOT `.get(f, "")`), matching every
    sibling normalizer: a present-but-null key (`permit_type` is null on 18/22
    live records) must normalize to the same "" an absent key would."""
    out = {f: (raw.get(_PERMIT_RAW_KEYS[f]) or "") for f in PERMIT_FIELDS}
    out["effective_date"] = _parse_egle_date(raw.get(_PERMIT_RAW_KEYS["effective_date"]))
    out["expiration_date"] = _parse_egle_date(raw.get(_PERMIT_RAW_KEYS["expiration_date"]))
    out["termination_date"] = _parse_egle_date(raw.get(_PERMIT_RAW_KEYS["termination_date"]))
    return out


def fetch_site_permits(session: requests.Session, nsite_id: str) -> list[dict]:
    """Return the full list of normalized permits for one nSITE site.

    Raises NsiteFetchError after 3 attempts on ANY network/HTTP/structural
    failure — the same contract as fetch_site_violations / fetch_site_
    compliance_actions / fetch_site_evaluations (nsite_permits_watcher DIFFS
    this list, so a swallowed failure returned as [] would read as "every
    permit withdrawn at once"), NOT fetch_site_documents' swallow-and-return-[].

    Like fetch_site_evaluations (and UNLIKE fetch_site_violations/fetch_site_
    compliance_actions), this DOES filter — on `prmtPrmtNum` present — because
    prmt_num is this profile's genuine unique diff key and a keyless record
    cannot be placed in a ref-number-keyed snapshot. No such record has been
    observed live; if nSITE ever serves one, it is silently excluded from the
    diff rather than raising, matching the Evaluations/Submissions precedent.

    An empty list is a VALID result (12 of the 19 watched sites have zero
    permits on file); only a fetch/structural failure raises."""
    query_params = urllib.parse.quote('{"filter":[{"id":"' + str(nsite_id) + '"}]}')
    url = (
        f"{PERMITS_ENDPOINT}"
        f"?responseContentType=application/json"
        f"&includeMetadataInResponse=true"
        f"&loadChildren=true"
        f"&queryParams={query_params}"
        f"&filterString="
    )
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results/detail/{nsite_id}/Documents"

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if "queryResults" not in data:
                raise NsiteFetchError(
                    f"permits response for site {nsite_id} is missing "
                    f"'queryResults' — nSITE may have changed its response shape"
                )
            if data.get("hasResultsRemaining"):
                # Null on every site today (verified 2026-08-22). If nSITE ever
                # enables server-side paging, a partial page would be
                # INDISTINGUISHABLE from a shrunken record set: the caller's
                # ref-keyed diff would read the missing tail as permits
                # withdrawn and email that as fact. Fail loudly instead — the
                # same guard as every sibling fetch above.
                raise NsiteStructuralError(
                    f"permits response for site {nsite_id} reports "
                    f"hasResultsRemaining — nSITE has started paging this "
                    f"profile and this client would otherwise diff a PARTIAL "
                    f"page as mass withdrawals. Pagination support is needed."
                )
            return [_normalize_permit(p) for p in data["queryResults"]
                    if p.get("prmtPrmtNum")]
        except NsiteStructuralError:
            # Re-raise immediately, before the retry loop can swallow it into a
            # generic NsiteFetchError: retrying a shape change is pointless, and
            # the caller distinguishes the two types deliberately (a generic
            # NsiteFetchError is treated as transient skip-and-warn, the exact
            # silent-forever outcome this class exists to avoid).
            raise
        except Exception as e:  # noqa: BLE001 — network/HTTP: retry, then raise loud
            last_exc = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise NsiteFetchError(
        f"GET permits for site {nsite_id} failed after 3 attempts: {last_exc}"
    ) from last_exc


def _looks_like_pdf(body: bytes) -> bool:
    """A cheap 'is this a PDF' check. Readers tolerate junk before the %PDF
    header, so scan the first 1 KB rather than requiring it at byte 0."""
    return b"%PDF" in body[:1024]


_GZIP_MAGIC = b"\x1f\x8b\x08"


def _maybe_gunzip(body: bytes) -> bytes:
    """nSITE's native-file responses are sometimes gzip-compressed regardless
    of the Accept-Encoding request header — requests' automatic decompression
    relies on a correct Content-Encoding response header, which this endpoint
    doesn't set, so the raw gzip blob otherwise lands in r.content undecoded
    (curl's --compressed flag papers over this by decoding unconditionally;
    we do the same explicitly). Verified against real nSITE responses during
    the 2026-07-07/2026-07-11 WRD-Groundwater hand-pull. A no-op for content
    that doesn't start with the gzip magic."""
    if body.startswith(_GZIP_MAGIC):
        try:
            return gzip.decompress(body)
        except OSError:
            return body  # magic matched but it wasn't valid gzip — pass through
    return body


def download_pdf(session: requests.Session, doc: dict, dest_path: str, timeout: int = 120) -> str:
    """Download one document to dest_path as a PDF the parser can open. Returns
    dest_path; raises on HTTP error, empty body, or if no source yields a PDF.

    nSITE's per-record link (`doc_url`) usually points at a PDF, but for some
    documents it points at the ORIGINAL file — an Outlook .msg, a Word .docx,
    an image, an nForm submission — which PyMuPDF cannot open directly. When
    the record's own link is not a PDF, fall back to nSITE's `downloadpdf/<id>`
    render endpoint, which rasterizes images/nForms into a PDF but 400s on
    .msg/.docx/legacy .doc. If every source still isn't a PDF, the last non-PDF
    body fetched gets one more chance: poison_doc_extractor.synthesize_pdf()
    (ADR 011) can build a real PDF out of .msg and .docx sources (envelope +
    recursed attachments for .msg; body text for .docx). Legacy Word .doc has
    no render AND no extractor here — those still fail and accrue a poison
    strike, which is correct: the monitor can't read them without a .doc
    converter. See ADR 011 / the 2026-07-07 handoff.

    `native_download_url(doc_id)` (the same `downloadfile/<id>` endpoint used
    for stub links) is included explicitly as a final source, not just relied
    on implicitly via `doc_url` — `_normalize()` falls back to the RENDER
    endpoint (not the native one) when a record's own `docMgmtDocurl` is
    empty, which would otherwise mean the extraction fallback above never
    even gets a non-PDF body to work with for that record."""
    doc_id = doc["doc_id"]
    primary = doc["doc_url"]
    render = f"{DOWNLOAD_BASE}/{doc_id}"
    native = f"{DOWNLOAD_FILE_BASE}/{doc_id}"
    # The record's own link first, then the render endpoint, then the native
    # file endpoint explicitly — deduped, since these often coincide.
    urls = []
    for u in (primary, render, native):
        if u not in urls:
            urls.append(u)
    referer = f"{NSITE_BASE}/nsite/DEFAULT/map/results"
    last_exc: Optional[Exception] = None
    last_non_pdf_content: Optional[bytes] = None
    for url in urls:
        for attempt in range(3):
            try:
                r = session.get(url, headers={"Referer": referer}, timeout=timeout)
                r.raise_for_status()
                if not r.content:
                    raise RuntimeError("empty response body")
            except Exception as e:  # noqa: BLE001 — transient HTTP/network: retry this url
                last_exc = e
                time.sleep(2 ** attempt)
                continue
            content = _maybe_gunzip(r.content)
            if _looks_like_pdf(content):
                with open(dest_path, "wb") as f:
                    f.write(content)
                return dest_path
            # A valid response that isn't a PDF (the .msg / .docx / image /
            # nForm case). Retrying the same URL won't help — fall through to
            # the next source, but remember this body: if every source fails,
            # it's the last chance for the extractor fallback below.
            last_non_pdf_content = content
            last_exc = RuntimeError(f"non-PDF response from {url} (starts {content[:8]!r})")
            break

    if last_non_pdf_content is not None:
        try:
            return pde.synthesize_pdf(last_non_pdf_content, dest_path)
        except pde.ExtractionError as e:
            last_exc = e

    raise RuntimeError(f"download failed for doc {doc_id}: {last_exc}")
