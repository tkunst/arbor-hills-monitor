# coder:public-records-search-words -- multi-word, any-order search on the Public Records page

**Follow-up to ADR 062 (public-records search, Phases 1-3 merged 2026-10-01).** Small,
front-end-only change to `site/public-records/search.js`. No data-pipeline change, no new
Sheet reads, no new published field, no privacy-gate change.

## Why (the real miss that prompted this)

A reader searching the Public Records page for **"hydrogeological"** could not find the
records they were looking for. Today `matchesText()` in `site/public-records/search.js`:

```js
var fields = [entry.title, entry.facility, entry.excerpt];
... v.toLowerCase().indexOf(q) !== -1
```

treats the WHOLE query as one literal substring, over three fields only. Consequences:

1. **Multi-word queries fail unless the words are adjacent and in that order.**
   "hydrogeologic monitoring" works only if that exact phrase appears; "monitoring
   hydrogeologic" or "PFAS 2023" never match.
2. **Spelling variants miss.** "hydrogeological" does not match titles that say
   "Hydrogeologic Monitoring Plan" (the query is longer than the word in the text).
3. **The Source line is not searched.** Hand-curated rows carry no automated summary, so
   their only searchable text is the title; the `source` value (e.g. "Civil & Environmental
   Consultants (CEC) for ... -> EGLE") is displayed but not matched.
4. **The date is not searched**, so "PFAS 2023" cannot narrow by year (the date-range
   filter exists, but readers type years into the box).
5. **Punctuation blocks matches**: "PEAS #24917" vs "24917" should both work.

## Goal

Rewrite the text-matching in `search.js` so that:

- **Tokenize the query** on whitespace (and strip surrounding punctuation such as `# , . ; : ( ) " '`),
  lower-cased. Ignore empty tokens.
- **AND semantics, any order:** an entry matches only if EVERY query token matches somewhere in
  the entry's searchable text.
- **Searchable text** = `title` + `facility` + `excerpt` + `source` (when present) + `date`
  (so a year like `2023` matches). Same fields the index already publishes; do NOT add any new
  field to `search-index.json`.
- **Token match rule (light, predictable stemming):** split the entry text into words the same
  way, then a query token matches if it is a substring of the text (keeps today's behavior for
  partial words like `hydrogeolog` or `24917`) **OR** if a text word is a prefix of the query
  token and is at least 6 characters long (so the query `hydrogeological` matches the text
  word `hydrogeologic`; the 6-character floor stops short words like `pfas` or `well` from
  matching every longer query that happens to start with them). Keep it
  deterministic and simple; no fuzzy/edit-distance matching, no external library, no network.
  If you find a cleaner rule that satisfies every acceptance case below, use it and say why in
  the PR.
- Empty query: unchanged behavior (show everything subject to the facet/date filters).
- Facet filters + date range: unchanged, still ANDed with the text match.
- Keep the existing security discipline in `search.js` (no markup interpolation, `textContent`
  only, link-scheme check, no-credentials fetch); the static tests in
  `tests/test_search_js.py` must still pass.

## Acceptance cases (verify against the REAL `site/public-records/search-index.json` on `main`)

Run each query against the real index (a small node or Python port of the matcher over the
JSON is fine for verification; paste the result counts + the top titles into the PR):

| Query | Must return (at least) |
|---|---|
| `hydrogeologic monitoring` | the two "Revised Hydrogeologic Monitoring Plan ... (Golder Associates, 2018)" rows |
| `monitoring hydrogeologic` | same set as the row above (order-independent; today: 0) |
| `hydrogeological` | (today: 3) rows titled "...Hydrogeologic..." AND the 1993 "Report on Hydrogeological Investigation" row |
| `hydrogeolog` | everything the two rows above return (partial word still works) |
| `24917` and `PEAS #24917` | both the 2023-07-13 CEC Field Summary Report row and the 2023-03-16 "Violation Notice Correspondence" row |
| `historic` | about 35 rows (today: 2). The word sits mostly in hand-curated rows' Source line ("...historic file..."), so the jump proves `source` is searched |
| `PFAS 2023` | only rows mentioning PFAS whose date is in 2023 (today: 0; a reference port of the rule gave 9, vs 97 for `PFAS` alone) |
| (empty) | identical result set to today |

Also report the before/after result counts for each query in the PR description.

## Tests

`tests/test_search_js.py` is text-level only (no JS runtime under pytest). Add coverage for the
new matcher: preferably factor the matcher into a small pure function and test it under `node`
if `node` is available locally and in CI; otherwise add a Python reference port of the same
rule with unit tests for the acceptance cases plus a static check that `search.js` still
contains the tokenizing/AND logic. State which approach you used and why.

## Guardrails

- Front-end only: no change to `findings_feed.py`, `scripts/gen_findings_feed.py`,
  `search-index.json` schema, or `scripts/check_publish_safety.py`.
- This changes a LIVE page. Follow `docs/overnight-coder.md` Step 3: verify against the real
  index before merge (the acceptance table above is the real-specimen check). If every Step-8
  gate passes, merging is fine; on any doubt, leave a DRAFT PR for Trisha.
- Out of scope (note in the PR if relevant, don't build): searching inside the PDFs themselves;
  abbreviation expansion (e.g. `HMP` -> "Hydrogeologic Monitoring Plan"). Do NOT propose retitling records as a findability fix: titles are rewritten on every page rebuild, so the fix must live in the matcher.
