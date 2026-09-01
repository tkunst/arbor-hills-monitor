# ADR 035 — Public Comment Periods tracker (site/public-comment/)

Date: 2026-09-01
Status: **accepted.** Ships as a live page generator plus a scheduled workflow;
first deploy to the live site is a human step (merge to main), same posture as
every other site change.
Builds on: ADR 032 (Active Public Notices watch — the `fetch_site_public_notices`
client this reuses), ADR 017 (ROP renewal watch — the statewide-notice source
this reuses), ADR 022 (the shared `nsite_sites` registry), ADR 027 (Upcoming
Activities digest — the sibling "what's coming up" surface, but email not web),
and the site's Findings feed (`findings_feed.py` / `gen_findings_feed.py`, whose
pure-render + thin-I/O-wrapper split this mirrors).

## Context

Residents need one place to see which Arbor Hills applications and permit actions
are open for public comment at EGLE right now, and when each closes. The pieces
already exist inside the monitor (Public Notices Watch, ROP Watch), but only as
internal snapshot tabs and per-change emails — there is no at-a-glance public
page of "what is open, when it closes, where to comment."

A concrete miss motivated this: the Emerald RNG (P1488) draft-ROP comment window
lapsed in August 2026 with no comment filed, partly because these windows are
easy to lose track of across several facilities and two different notice
mechanisms.

This page is **factual records**, so it belongs on the blue Monitor, not the
orange advocacy site — it states what is open and links to the official EGLE
notice; it does not urge anyone to comment. See
`docs/editorial-standards-two-sites.md`.

## Decision

Add a static page at `site/public-comment/index.html`, built the same way as the
Findings feed:

- `public_comment_feed.py` — pure render/bucket logic (open / opening-soon /
  recently-closed; "closing soon" flag; deadline math). No I/O, unit-tested.
- `scripts/gen_public_comment_feed.py` — thin I/O wrapper that fetches the data
  and writes the HTML.
- `.github/workflows/public-comment.yml` — scheduled regenerate + commit-if-changed
  (diff-quiet guard keyed to the footer "Generated …" line, same idiom as
  findings-feed.yml). pages.yml redeploys on the resulting commit.

### Data source: LIVE fetch, not the Sheet

The Findings feed renders the case-file Sheet. This page instead **live-fetches
EGLE at generation time**, for two reasons: (1) a deadline tracker must be as
current as possible, and the Public Notices Watch tab only refreshes on each
facility's polling cadence (biweekly for the key sites), which is too laggy for
deadlines; (2) the authoritative comment-window dates live in EGLE's notice feed,
not the Sheet. It reuses the exact read-only client calls the watchers already
make (`nsite_client.fetch_site_public_notices` per `nsite_sites` facility, plus
`rop_client.fetch_notice_pdf` / `notice_mentions_srn`). It needs **no secrets** —
these are EGLE's public, unauthenticated endpoints — which also makes the workflow
simpler than findings-feed.yml.

### Two notice streams, deduplicated

- **Per-facility notices** (`fetch_site_public_notices`) carry clean
  `start_date`/`end_date` — the primary, dated source (this is how the Wetland 1
  JPA and the Arbor Hills Energy ROP both surface).
- **The statewide ROP public notice** (`rop_client`) is a single monthly PDF.
  Some facilities' ROP windows appear ONLY there, with no per-facility notice
  (Emerald RNG's did). Its exact close date is not machine-readable from the PDF,
  so any facility it names that has no dated notice this run is listed as an
  **undated** entry that says "about a 30-day window; see the notice for the exact
  deadline" and links to the PDF. A facility that has BOTH a dated notice and a
  ROP mention is listed once (the dated row), annotated to note the ROP tie —
  never duplicated.

### Failure posture (folded in from the adversarial review)

- **Total fetch failure aborts the write** (`SystemExit`), so an EGLE/network
  outage can never overwrite a good page with a false "nothing is open." This is
  the analog of the Findings feed's shrink guard.
- **Partial failure is surfaced on the page**, not hidden: any facility that could
  not be checked is listed in a visible "may be incomplete" block, and the
  statewide-ROP check is wrapped so its failure degrades to a note-plus-link
  rather than silently dropping ROP coverage.
- Every entry links to the official EGLE notice, and the footer states plainly
  that this is an informational summary, **not a legal notice**, to be confirmed
  on EGLE's own portal.

## Consequences

- One public, auto-updating page answers "what's open for comment and when does it
  close," across every Arbor Hills facility and both notice mechanisms.
- The Monitor's neutrality is preserved: facts and official links only; the
  "submit a comment" call to action lives on the orange advocacy site and links
  back here.

### Known limitations (no silent caps)

- EGLE's public-notice API returns no descriptive title (coverage reads only
  "Facility Location"), so entries show facility + window + link, not a rich
  subject line. A reader clicks through to the notice for specifics. Enriching the
  label (e.g. by cross-referencing the filed application documents) is a possible
  future improvement, deliberately out of scope for v1.
- Statewide-ROP-only entries carry no precise close date (the PDF is free text);
  they show the ~30-day framing and link to the notice. Parsing the exact date out
  of the PDF is a possible future improvement.
- The page is only as complete as the `nsite_sites` registry and the statewide ROP
  notice; a comment venue outside EGLE MiEnviro (USACE's side of a joint permit, a
  county or township process) is not covered by v1 and would need a separate,
  clearly-labeled curated source.
