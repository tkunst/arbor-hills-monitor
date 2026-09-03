# Editorial standards: two sites, one maintainer

This project is one half of a pair of related but deliberately separate public
websites, both maintained by Trisha Kunst, a resident near the Arbor Hills
Landfill. The two sites follow **different content standards on purpose.** This
document explains the boundary so that anyone building, editing, or reviewing
either site keeps content on the correct side of it.

The distinction is not a claim that the two sites have different owners. They do
not. The claim is narrower and honest: the two sites hold different *kinds* of
content, held to different editorial standards.

## The two sites

**Arbor Hills Monitor** (this repository; `arborhillsmonitor.org`)
Blue. Sourced records, data, and fact-based reporting drawn from Michigan EGLE's
public filing systems. Fact-based, and it never asks the reader to take an action
(see the bright line below).

**The advocacy site** (currently `tkunst.github.io/better-arbor-hills`,
intended future home `advocacy.arborhillsmonitor.org`)
Orange. Public comments, policy proposals, evidence briefs, and advocacy. These
express the author's views.

### The color is the signal

The two sites are distinguished by the **color of their favicon** (the small icon
in the browser tab), and that color is carried through headers and link colors on
every page, not only the homepages:

- **Blue** (`#2f7fb3`, the hills-and-signal-dot mark) means you are on the
  **Monitor**: records and data.
- **Orange** means you are on the **advocacy site**: advocacy and policy.

A visitor should be able to tell which kind of site they are on before they read
a word, from the tab color alone. Preserve the favicon, header, and link colors
site-wide when adding pages. Do not mix them.

## The content firewall

To protect the Monitor's credibility as a sourced, fact-based record, content is
kept on the correct side:

**On the blue Monitor site (this repo):**

- The **automated data layer** (alerts, the Sunday digest, the case-file Sheet,
  the Findings feed, the Public Comment Periods page, anything the pipeline emits
  without a human in the loop) stays strictly factual and **source-labeled**.
- **Authored analysis and visualization pages** (the thermal map, briefs, hosted
  visualizations) may carry a clear, sourced community-concern **point of view**,
  per the "accuracy over posturing" ruling in this repository's `CLAUDE.md`,
  provided every checkable claim is sourced and survives the refutation gate. The
  Monitor's brand is accuracy, not the mere appearance of neutrality.
- **The bright line is the ASK.** No page on the Monitor issues a call to action,
  a campaign ask, or a "submit a comment" prompt. Sourced facts and sourced
  analysis can live here; the request to *do* something cannot. Where a link to
  the advocacy site is useful, it is a neutral pointer, never a call to act (see
  the "advocacy-note" box on the homepage for the accepted wording).

**On the orange advocacy site:**

- Public comments, policy proposals, and recommendations live here, and only
  here.
- Advocacy pages **link to** the Monitor's records as their factual foundation
  rather than copying advocacy framing back into the record.
- Public comments and policy pieces carry clear authorship and dates.

### Where specific things go

- A **tracker of open public-comment periods and deadlines** (what is open, when
  it closes, a link to the official EGLE notice) is **automated factual records**,
  so it lives on the **blue Monitor** (`/public-comment/`). It states facts and
  links to the source. It does not tell anyone to comment.
- A **"submit a comment" call to action**, sample comment language, or an ask to
  weigh in on a permit is **advocacy**, so it lives on the **orange** site and
  links back to the Monitor's tracker for the facts.

That division is the reason the tracker is on the Monitor and the encouragement
to act is not.

## Transparency and disclosure

Both sites should carry, in an About or footer context, a plain disclosure to the
effect of:

> Both sites are maintained by Trisha Kunst for different purposes. Arbor Hills
> Monitor presents sourced records, data, and fact-based reporting. The advocacy
> site contains public comments, policy proposals, and advocacy.

## For builders and reviewers

When adding or reviewing a page or a piece of copy, ask: is this a *record or
sourced report*, or a *call to act*? Records, data, and sourced reporting or
analysis belong on the blue Monitor. Calls to action, sample comment language,
policy asks, and organizing belong on the orange advocacy site. When a change
would put a call to action onto the Monitor, or would strip the sourcing from a
factual page, it is on the wrong side of the line.

This standard sits alongside this repository's `CLAUDE.md` editorial posture
("accuracy over posturing," 2026-08-30): the automated data layer stays strictly
factual and source-labeled, authored analysis pages may carry a sourced point of
view, and neither ever issues the ask. This document is the fuller explanation of
how that posture maps onto the two related sites.
