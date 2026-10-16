# Overnight-coder handoff — name the publishing entity (Landfill Records Project LLC) in site schema + footers

*Staged 2026-10-16. The entity was confirmed by Trisha 2026-10-08:
**Landfill Records Project LLC** (filed 9/16/2026). Read `docs/overnight-coder.md`
first. This is a public-site change (merge = deploy) with no code-logic
behavior change — low risk, but needs a careful browser review because it
touches every page's structured data and footer. Recommended tier: Sonnet.

**Cloud-ready:** Runs in cloud mode: ends at a DRAFT PR. No live credentials
needed — this is a static HTML/content edit, no external calls.

**Live-path check:** `site/` deploys on merge via `pages.yml`. There's no
`enabled` flag for a static site — treat any merge as an immediate live
change, so this MUST end at a draft PR for Trisha's browser review regardless
of how mechanical it looks.

**Merge-order note:** PR #91 (`insurance-legal-pages`, branch
`insurance-legal-pages`) is an existing OPEN draft PR that also touches these
same footers (it adds 5 legal pages: Terms, Privacy, Disclaimer, Corrections,
IP/Fair-Use). Whichever of the two PRs merges first, the other will need a
rebase. Say so explicitly in this PR's description; do not try to resolve or
merge PR #91 yourself.*

## Background — what Trisha actually asked for

The publisher/entity identity on the public site should be the publishing LLC
(formed specifically to ring-fence the adversarial-publishing risk of this
project), not the natural person. The **editorial byline** (Trisha Kunst, as
the person who writes/curates analysis and advocacy material) is explicitly
NOT changing — only the *publisher* identity. Concretely:

- "Publisher: Landfill Records Project LLC"
- "Editor: Trisha Kunst" / "by Trisha Kunst" stays, wherever it already
  appears as an editorial byline.

## What's actually in the current code — read this before assuming the obvious find-replace

The original internal spec this handoff is built from assumed the site's
schema.org markup directly named a Person as publisher, and that footers read
"An independent project by Trisha Kunst." **Neither is true as of this
writing** — verify both findings below against current code before building,
since the site evolves:

1. **Schema.org `publisher` is already an Organization reference, not a
   Person, on every page that has one.** `site/index.html`'s JSON-LD graph
   defines `{"@type":"Organization","@id":".../#org","name":"Arbor Hills
   Monitor",...,"founder":{"@type":"Person","name":"Trisha Kunst"},...}` and
   a `WebSite` node whose `"publisher"` is `{"@id":".../#org"}` — i.e. the
   Organization, referenced by id. The Person only appears as `founder`, a
   DIFFERENT schema.org property (who started the thing, not who publishes
   it). Six of this repo's pages carry a JSON-LD block at all (confirmed by
   grepping for `"@context"`): `site/index.html`, `site/data/index.html`,
   `site/data/flagged-wells/index.html`, `site/data/perimeter-air/index.html`,
   `site/data/wellfield/index.html`, `site/data/wellhead-h2s/index.html`,
   `site/faq/index.html`. The five `site/data/*` pages already use
   `"publisher": {"@type":"Organization","name":"Arbor Hills Monitor", ...}`
   for their Dataset entries (no Person anywhere in them). **So there is no
   literal Person-typed `publisher` field anywhere in this repo's JSON-LD to
   find-and-replace.** The actionable change is additive: give the `#org`
   Organization node (and/or the dataset `publisher`/`creator` Organization
   objects) its OWN `publisher` property naming
   `{"@type":"Organization","name":"Landfill Records Project LLC"}` — i.e.
   "Arbor Hills Monitor" is the site/work's own name/brand; "Landfill Records
   Project LLC" is who publishes it. Leave `founder: Person (Trisha Kunst)`
   untouched — that's accurate and is a different relationship than
   publisher. Apply this consistently across all pages carrying a JSON-LD
   block, and verify there isn't a SEVENTH page with different markup you
   haven't seen (re-grep `"@context"` yourself at build time — the count
   above may be stale).
2. **Footers do NOT currently name Trisha Kunst.** Every `<footer
   class="site-footer">` found in this repo (dozens of instances across
   `site/**/index.html`) reads some variant of `"An independent project. ..."`
   — e.g. `"An independent project. All source data is public regulatory
   records from Michigan EGLE..."` — with **no person's name in it at all.**
   So there is nothing to literally replace; the actual work is **adding**
   the entity name consistently, e.g. changing the common lead phrase from
   "An independent project." to "An independent project of Landfill Records
   Project LLC." (pick wording that reads naturally and stays consistent
   across every footer variant you find — grep `class="site-footer"` across
   `site/**/*.html` for the full set of variants before writing the
   replacement regex/script, since the trailing sentence differs per page).
3. **Two files DO carry an actual "Trisha Kunst" line, and they're a
   different kind of statement — read before touching:** `site/thermal-map/index.html`
   and `site/thermal-map-embed/index.html` each have an HTML comment at the
   top: `Copyright (c) 2026 Trisha Kunst`, immediately followed by
   `Code: SPDX-License-Identifier: MIT` / `Data & charts: SPDX-License-Identifier: CC-BY-4.0`.
   **This is a code/content COPYRIGHT HOLDER line, not a "who publishes this
   page" statement** — changing it would reassign legal copyright ownership
   of that code, which is a different and bigger decision than "who is the
   named publisher." TASKS.md separately tracks "code license undecided" as
   Trisha's own open call. **Do not change the named copyright holder in
   these two comments.** If you believe it should change too, say so
   explicitly as an open question in the PR description rather than making
   the edit — do not silently reassign copyright.
4. The body paragraph on `site/index.html` — "Trisha Kunst, the resident who
   maintains this project, also publishes submitted public comments, evidence
   briefs, and proposed policy protections on a separate advocacy site..." —
   is editorial byline content describing her as the person behind a
   separate site. Leave it alone; it's exactly the kind of "by Trisha Kunst"
   byline the task says to preserve, just in paragraph form rather than a
   footer line.
5. No `<meta name="author">` or `<meta ... publisher>` tags were found
   anywhere in the site as of this writing. If you add one for consistency
   with the schema.org change, keep it minimal (e.g. `<meta name="publisher"
   content="Landfill Records Project LLC">` on pages where that reads
   naturally) — don't invent an elaborate meta-tag scheme beyond what's asked.

## What to build

1. **JSON-LD:** on every page carrying a `"@context":"https://schema.org"`
   block, add `"publisher":{"@type":"Organization","name":"Landfill Records
   Project LLC"}` to the top-level Organization node (`#org` on the homepage;
   the equivalent "Arbor Hills Monitor" Organization object on the `site/data/*`
   pages and `site/faq/index.html`, wherever it's defined inline rather than
   by `@id` reference). Leave every existing `founder`, `creator`, `@id`
   reference structure, dataset metadata, and license URL exactly as-is —
   this is additive, not a restructure.
2. **Footers:** across every `site/**/index.html` with `class="site-footer"`,
   add the entity name to the lead sentence consistently (see point 2 above
   for the exact current wording to build from). Do not touch the rest of
   each footer's own trailing sentence (several differ per page — e.g. a
   generated-timestamp note on some pages).
3. **Do not touch** the two thermal-map files' copyright-holder comment
   lines (point 3 above) or the homepage's editorial byline paragraph (point
   4 above).
4. **`/about`-equivalent page, if one exists:** grep for it
   (`site/about/index.html` or similar); if found, add a short, factual
   mention of Landfill Records Project LLC as publisher with Trisha Kunst as
   editor/author, matching the tone of the rest of the site. If no such page
   exists, don't create one as part of this PR — note that absence in the PR
   instead.
5. **Legal-notice/corrections/terms pages:** these live on PR #91
   (`insurance-legal-pages`), not yet merged. Do not try to edit that PR's
   branch. If PR #91 has already merged by the time this cloud session runs
   (check `gh pr view 91`), include those newly-merged pages in your
   footer/entity sweep too; if it's still open, just note the dependency in
   this PR's description and move on.

## Tests / verification

- This is markup/content, not logic — there's no pytest surface for it.
  Instead: write a small script or a `block-data-files`/lint-style check (or
  just a manual grep-based verification you paste into the PR description)
  confirming (a) every page with a JSON-LD block now has the LLC named as a
  `publisher` somewhere in its graph, (b) every footer with `site-footer`
  class now names the LLC, (c) the two copyright comment lines are
  byte-for-byte unchanged, (d) the homepage editorial-byline paragraph is
  unchanged.
- Run this repo's existing `lychee`/link-check and `markdownlint` equivalents
  if they apply to `site/` (check CI config) — a malformed JSON-LD edit could
  break page rendering even though it's "just markup."
- If this repo has a JSON-LD validity check anywhere (grep for `jsonld` or
  `schema.org` in CI config), make sure it still passes; if there is none,
  at minimum manually validate the edited blocks are well-formed JSON before
  pushing (a syntax error in a `<script type="application/ld+json">` block
  fails silently in browsers — no visible breakage, just broken structured
  data, so this needs a deliberate check, not just "the page still renders").

## Out of scope

- Any content/editorial change beyond the publisher/entity identity.
- Section 5.4's "label fact vs. analysis consistently throughout the site" —
  explicitly flagged in the source spec as an editorial-judgment task, not a
  mechanical coder task. Do not attempt it here.
- PR #91's legal pages — do not edit that branch or try to resolve the
  merge-order conflict yourself; just flag it.
- The code-copyright-holder lines (point 3) and the editorial byline
  paragraph (point 4) — explicitly preserved, not touched.

## Acceptance

- Browser review (Trisha, before merge): every page's visible footer and
  rendered JSON-LD (view page source / a schema.org validator) names
  Landfill Records Project LLC as publisher; the "by Trisha Kunst" editorial
  presence is unchanged everywhere it appeared before.
- The PR description explicitly calls out: the stale assumptions corrected
  above (schema.org was already Organization-typed; footers never named a
  person), the deliberate non-change to the copyright-holder comments, and
  the PR #91 merge-order dependency.
- No change to any `.py` file, test, or CI config — this PR should be
  `site/` only (plus maybe `CLAUDE.md` if you choose to note the entity
  change there, though that's optional for a pure content PR like this).
