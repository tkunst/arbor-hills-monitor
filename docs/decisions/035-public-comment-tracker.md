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
`rop_client.fetch_notice_pdf`, whose text is then parsed section-by-section — see
`parse_rop_30day_comment` / `parse_rop_final`, not a blunt whole-PDF search). It
needs **no secrets** — these are EGLE's public, unauthenticated endpoints — which
also makes the workflow simpler than findings-feed.yml.

### Two notice streams, deduplicated

- **Per-facility notices** (`fetch_site_public_notices`) carry clean
  `start_date`/`end_date` — the primary, dated source (this is how the Wetland 1
  JPA and the Arbor Hills Energy ROP both surface).
- **The statewide ROP public notice** (`rop_client`) is a single monthly PDF with
  three sections in order: 30-DAY PUBLIC COMMENT (open), 45-DAY EPA REVIEW
  (comment ended), and FINAL (issued). We read ONLY the 30-DAY section for open
  periods — a facility in the 45-DAY or FINAL section is NOT open for comment (a
  real bug this guards against: Emerald RNG sat in the 45-DAY section on
  2026-09-01, and a blunt whole-PDF search would have shown it "open"). Each named
  facility's exact close date is parsed from that section ("… until <date>" / "…
  to <date>"). Some facilities' ROP windows appear ONLY here, with no per-facility
  notice (Emerald RNG's did earlier in 2026). A facility that has BOTH a dated
  per-facility notice and a 30-day ROP mention is listed once (labeled as the ROP
  renewal), never duplicated. The FINAL section feeds the closed-period Outcome
  (see the 2026-09-01 addendum).

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

## Addendum 2026-09-01 — two-table layout + persisted state

Trisha asked for a table layout: an **Open** table (Facility / What / Public
comment opened / Public comment closes / Link) and, at the bottom, a **Closed
public comments** table (Facility / What / Link / Outcome) that a period moves to
once its comment window ends.

This required a change the original stateless design could not support. EGLE
**removes a notice from its active feed once the comment period ends**, so a
purely live render would forget a period ever existed and could never populate a
Closed table. The repo forbids committed data files (`*.json` is gitignored and
blocked by the `data-guard` CI check; its convention keeps state in the
Sheet/Drive), so rather than add Sheet secrets to this otherwise secret-free
page, **state rides inside the committed page as a hidden JSON block, and each
run reads its own previously-committed `index.html` back to recover it** -- the
same self-referential pattern `gen_findings_feed` already uses (`_previous_total`
reads its own committed page for the prior document count). Each run:

- upserts the currently-open periods (Open table renders these);
- marks a period **closed** when it has dropped out of the open set AND its source
  was actually checked this run (a transient fetch error can't false-close it);
- renders closed periods from state (Closed table);
- auto-fills a closed ROP period's **Outcome** as "issued" once its SRN reaches
  the statewide notice's **FINAL** section (`parse_rop_final`); every other closed
  period shows **"Pending (awaiting EGLE decision)"** until its disposition is
  known.

`what` and `outcome` are preserved across runs, so they can be hand-curated in
the page's embedded state block (e.g. `what: "Wetland 1 PFAS JPA"`, or a
manually-recorded outcome like "Permit denied") without a later run overwriting
them.

**Outcome is deliberately not scraped for non-ROP permits.** EGLE issues or
denies a wetland/JPA permit as a later, separate action that is not reliably
machine-derivable from the notice feed; auto-guessing it would risk a wrong
public claim. So the ROP case (recurring, cleanly derivable from the statewide
notice) auto-fills, and everything else stays "Pending" until set by hand or a
future targeted rule.

**State is byte-stable on a no-op run** (no volatile fields; deterministic
serialization) and lives in the committed HTML, and the per-row "in N days"
countdown remains client-side, so the diff-quiet guard still commits only on a
real change (a period opened, closed, or its outcome was set) -- nothing new is
staged beyond `site/public-comment/`. `bucket_entries`/`days_between` from the
original card layout were removed (the Open/Closed split is now state-driven).
