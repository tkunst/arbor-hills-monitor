# coder:public-records-search-ui -- client-side search/facet UI over the index

**Phase 3 of 3** (ADR 062). **Do not start until `coder:public-records-search-wire-and-gate`
(Phase 2) is merged and its `findings-feed.yml` run has produced a real
`search-index.json` on `main`.** This PR is pure front-end: it adds markup to
the existing HTML template and new static JS/CSS. It makes NO data-pipeline
change (no new Sheet reads, no redaction logic, no gate change) and
introduces NO new privacy surface -- it only ever displays what Phase 2's
`check_publish_safety.py` extension already cleared.

**Goal:** add a search box + facet filters (Facility, Type, Severity, date
range) to `site/public-records/`, backed by `search-index.json`, filtering
entirely in the visitor's browser. Keep the existing chronological
page-1..46 view as the default landing content -- search is an additional
view/toggle, not a replacement.

**Why it matters.** Phases 1-2 made the redacted, gate-cleared data exist as
a static file; this phase is what actually fixes findability for a human
visitor. Full plan + rationale: ADR 062
(`docs/decisions/062-public-records-findability-search.md`).

## Where the markup goes

`findings_feed.render_page()` is the single template that generates all 46
pages (`index.html` = page 1, `page-2.html`..`page-46.html`). The relevant
block today (same on every page):

```html
<h1>Public Records on Arbor Hills</h1>
{intro}<p class="findings-count">{total_count:,} documents &middot; page {page_num} of {total_pages}</p>

<div class="findings-list">
{entries}
</div>

<p class="findings-nav">{nav}</p>
```

Add the search UI's markup (a search `<input>`, facet `<select>`s/checkboxes,
a results container, a "back to browsing" affordance) between the
`findings-count` paragraph and the `findings-list` div, **on every page**
(the template is shared -- there's no clean way to special-case page 1 only
without splitting the template, which is out of scope here). Toggle
behavior: search UI hidden/collapsed by default so the existing chronological
list is what a visitor and a crawler both see first (IndexNow targets
`/public-records/`, i.e. page 1 -- don't change what that URL primarily
shows); a click/focus on the search box (or a small "Search all records"
affordance) switches the view to filtered results, replacing (or hiding, JS's
call) the `.findings-list` content -- reuse the existing `.finding` /
`.finding-meta` / `.finding-kdp` CSS classes (`site/style.css`, lines ~102-150)
so search results look identical to the existing chronological entries, not
bolted-on.

## Data contract (from Phase 1/2 -- do not re-derive)

Fetch `search-index.json` (same directory, relative path). Each entry:
`date`, `title`, `facility`, `type`, `severity`, `source` (may be absent),
`excerpt` (may be absent), `link` (may be absent/empty). All text values are
**raw text, not HTML** -- Phase 1 redacted them but did NOT HTML-escape them
(JSON doesn't need escaping; escaping is the DOM-rendering step's job, which
is this PR).

## Implementation

- **New file `site/public-records/search.js`** (vendored inline, no CDN --
  matches this repo's `site/wellfield-explorer/plotly-2.35.2.min.js`
  convention of shipping the library IN the repo, not loaded from a
  third-party host). At this corpus size (~2,264 rows, low hundreds of KB
  gzipped) a hand-rolled case-insensitive substring filter across
  `title`/`facility`/`excerpt` needs no external library -- do not add a
  new dependency (FlexSearch/Lunr/etc.) for v1; note it as a possible
  follow-on in the PR description if fuzzy/ranked matching is wanted later.
- Facets: Facility, Type, Severity as `<select>` (values populated from the
  index itself at load time -- don't hardcode the list, it must track
  whatever's actually in the data, including a Hand-Curated row with a
  blank `facility`, which should bucket under a literal "Not stated /
  other" option, not be silently dropped from the filter). Date range:
  simple two-date-input `min`/`max` filter; tolerate a partial `date`
  value (Hand-Curated `doc_date` can be `"2004-07"` or blank -- see
  `findings_feed.parse_handcurated_rows` -- don't let it throw when parsed
  as a date; treat blank/unparseable dates as always-matching a date-range
  filter rather than crashing or silently excluding them).
- Debounce the text input (~150ms) before re-filtering, so typing feels
  smooth on the full dataset.
- Render each result as a card matching the existing `.finding` structure
  (`<article class="finding">` with a `.finding-meta` line and an `<h3>`
  title, linked via `link` when present exactly like the HTML does today
  for auto/hand-curated entries alike). **Build this DOM via `textContent`
  assignment or an explicit escape helper -- never `innerHTML =` with raw
  field text interpolated in.** The Python side treats every rendered field
  through `_esc()`; match that discipline here even though the JSON source
  is already curated -- defense in depth, not because the data is expected
  to be hostile.
- Show a result count ("Showing N of {total}") and a clear/reset control
  back to the plain chronological view.
- No network calls other than the one `fetch('search-index.json')` (or
  `XMLHttpRequest` if this repo's existing JS convention avoids `fetch` for
  browser-compat reasons -- check `site/wellfield-explorer/index.html` /
  `data.js` for the established pattern and match it rather than guessing).
- **CSS:** add search-UI-specific rules to `site/style.css` (the shared
  stylesheet every page already loads), not a new page-scoped `<style>`
  block, matching how `.finding`/`.findings-*` rules already live there.

## Guardrails

- No em-dashes in code/comments/PR description or any new visible copy
  (repo convention: use `--`; also the site-wide AI-writing-tells rule --
  check `/Volumes/Samsung-Pro-2TB/Lotext/documents/arbor-hills/arbor-hills-voice-guide.md`
  for any new user-facing copy, e.g. placeholder text, empty-state
  messaging, button labels).
- Do not change `findings-feed.yml`'s diff-quiet guard or `git add` scope --
  this PR's only pipeline-adjacent touch is the template markup baked into
  every regenerated HTML page, which the existing guard already handles
  (it diffs the whole rendered page minus the timestamp line, same as any
  other template change).
- Do not fetch `search-index.json` with credentials/cookies or add any
  cross-origin request -- it's a same-origin static file, keep it that way.
- Confirm the page still renders and is still usable with JavaScript
  disabled (the chronological list + pagination nav must keep working
  exactly as today -- the search UI is progressive enhancement, not a
  replacement for the base page).

## Tests

- This repo's Python test suite has nothing to exercise here (no Python
  changed) beyond re-running `pytest -q` to confirm nothing broke
  incidentally (e.g. an accidental `findings_feed.py` diff outside the
  template block). If `render_page`'s output is covered by an existing
  snapshot/golden test, update it deliberately and explain the diff in the
  PR description -- don't let a golden-file test silently start failing.
- Manual verification (describe in the PR): load a locally generated
  `site/public-records/index.html` + its `search-index.json` in a browser,
  confirm search narrows results as expected, facets populate from real
  data, a blank-facility Hand-Curated row appears under "Not stated /
  other," a partial-date row doesn't crash the date filter, and the page
  still works with JS disabled (chronological list unaffected).

## Definition of done

- Search box + facet filters live on every `site/public-records/*.html`
  page, backed by `search-index.json`, filtering client-side with no new
  network calls beyond the one same-origin fetch.
- Existing chronological view is unchanged as the default; search is
  additive.
- No `innerHTML` interpolation of raw field text; no new external script
  host; no new dependency for v1.
- `pytest -q` green; manual verification steps above run and described in
  the PR.

*Staged 2026-09-30. See ADR 062 for the full three-phase plan.*
