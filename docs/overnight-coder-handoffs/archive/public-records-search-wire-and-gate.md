# coder:public-records-search-wire-and-gate -- turn the search index on, safely

**Phase 2 of 3** (ADR 062). **Do not start until `coder:public-records-search-index`
(Phase 1) is merged to `main`.** This PR wires that pure function into the
live daily pipeline AND extends the publish-safety gate to cover it, in the
SAME PR -- the index must never be generated live without the gate covering
it, so there is no window where one ships without the other.

**Goal:** call `findings_feed.build_search_index()` from
`scripts/gen_findings_feed.py`, write `site/public-records/search-index.json`
alongside the HTML, and extend `scripts/check_publish_safety.py` to scan that
new file with the same block/warn rules it already applies to the HTML.

**Why it matters.** Phase 1 built the redaction-safe index function but left
it unreachable on purpose. This phase makes it real -- and because
`site/public-records/` is a live, publicly deployed, daily-regenerated
surface, this is a **live-path change per `docs/overnight-coder.md` Step 3**:
real-specimen verification is mandatory, no mocked-green-only merge.

## Changes

### `scripts/gen_findings_feed.py`

After building `pages = findings_feed.build_pages(rows, generated_at)`, also
call `index_json = findings_feed.build_search_index(rows)` (same `rows` list,
same run -- no second Sheet read, so the two artifacts can never drift out of
lockstep with each other). Write it to
`os.path.join(OUT_DIR, "search-index.json")` in the same loop that writes the
HTML pages, before the existing "clear stale `.html` files" step (which must
NOT delete the `.json` file -- check its `name.endswith(".html")` filter
doesn't accidentally also need updating, or add an explicit
`os.remove(index_path)` immediately before rewriting it, matching the
HTML-clearing intent for a shrinking dataset).

### `scripts/check_publish_safety.py`

Currently `_load_pages(OUT_DIR)` loads only `*.html` files and
`evaluate_pages()` parses `<article>` blocks out of HTML text. Add a parallel
path for the JSON index:

- Load `search-index.json` if present (same "warn, don't crash, if missing"
  tolerance the HTML loader has -- `gen_findings_feed.py` runs before this
  script, so it should always exist, but don't hard-crash if it doesn't).
- For each entry: build a visible-text blob from its `title` + `excerpt` +
  `facility` + `source` (when present) fields -- no HTML-tag-stripping
  needed here (JSON values are raw text already, not HTML fragments), so
  reuse `name_check.find_denylist_hits` / `find_heuristic_hits` directly on
  the concatenated text rather than routing through `_visible_text`.
- Classify hand-curated vs. auto by **key presence**: `"source" in entry`
  means hand-curated (per Phase 1's design -- the key is present, possibly
  `""`, only for hand-curated-origin rows), matching `_is_handcurated`'s
  HTML-side check (`"Source:" in finding-meta`) exactly.
- Same rules as today: hand-curated entry with a denylist OR heuristic hit
  -> add to `block`. Auto entry with a denylist hit -> add to `warn_auto`.
  Merge these into the same `result["block"]`/`result["warn_auto"]` lists
  `evaluate_pages()` already returns (or return a second dict and merge at
  the call site in `main()` -- your call, keep `main()`'s exit-code logic
  the single source of truth for pass/fail either way).
- `main()`'s existing pass/fail/print logic should need no structural
  change -- it already fails on any non-empty `block` list and prints
  `warn_auto` entries; just make sure JSON-sourced findings flow into the
  same lists so one gate, one exit code, covers both files.

### `.github/workflows/findings-feed.yml`

The `git add` step currently stages `site/public-records site/sitemap.xml`
-- `site/public-records` is the whole directory, so the new
`search-index.json` living inside it is already covered; **verify this
rather than assuming it**, and if the workflow is ever changed to add
specific filenames instead of the directory, add the JSON file explicitly.

**Do not add a generation timestamp to `search-index.json`.** The workflow's
diff-quiet guard (`git diff --cached --quiet -I'^<p>Generated .* UTC from the
monitor'`) exists so a byte-identical dataset produces zero commits, even
though every HTML page's footer carries a fresh timestamp every run. If the
JSON carried its own timestamp line, every run would diff non-quiet on that
alone and the guard's whole point (no-op commits skipped) would break for
the new file. Simplest correct answer: the JSON has no timestamp field at
all (ADR 062 already specifies this -- confirm Phase 1 didn't add one; if it
did, remove it here).

## Real-specimen verification (mandatory -- this is a live-path change)

Run locally (needs the service-account creds this repo's other scripts use):

1. `python scripts/gen_findings_feed.py` -- confirm `site/public-records/
   search-index.json` is written and its entry count equals the document
   count in `index.html`'s `class="findings-count"` line (currently ~2,264,
   will have grown by the time this runs -- use whatever the live run
   reports, don't hardcode 2,264 anywhere).
2. `python scripts/check_publish_safety.py` -- must exit 0 against the real,
   already-clean data (same as it does today for the HTML alone).
3. Confirm the gate actually blocks: this is covered by Phase 2's own unit
   tests (below), not a separate live step -- a live run can only prove
   "didn't false-positive on real data," it can't safely prove "blocks a
   real leak" without injecting one.
4. Re-run `gen_findings_feed.py` a second time with no Sheet changes and
   confirm `git status`/`git diff` shows NO change to `search-index.json`
   (proves no embedded timestamp broke determinism -- point 4 above).

## Tests

- `check_publish_safety.py`'s existing test file
  (`tests/test_check_publish_safety.py`) gets new cases mirroring its
  existing HTML ones: a synthetic `search-index.json` entry with
  `source` present + a denylist name -> blocks; same entry as an auto
  entry (no `source` key) + a denylist name -> warns only, does not block;
  a clean index -> passes.
- `gen_findings_feed.py`: a test (or extend an existing one) confirming
  `search-index.json`'s entry count matches the HTML total for a given
  input, and that re-running with identical input produces byte-identical
  JSON output (determinism -- no timestamp, no dict-ordering flakiness;
  Python 3.7+ dicts preserve insertion order so this should hold by
  construction, but assert it).
- Full `pytest -q` green.

## Guardrails

- **This PR's scope is "turn it on safely" -- do not also build the UI
  (Phase 3) here.** Keep the diff to the three files above + tests.
- No em-dashes in code/comments/PR description (repo convention: use `--`).
- If `/security-review` or the Step 5 subagent review flags anything about
  this gate extension as medium/high, that is a hard stop per
  `docs/overnight-coder.md` Step 6 -- this touches the site's only
  privacy-enforcement mechanism, treat findings here conservatively.
- Confirm CI's `bandit`/`gitleaks`/`block-data-files` checks pass same as
  any other PR -- nothing here should trip them (no new secrets, no
  committed data files beyond the generated `site/` output the workflow
  already commits).

## Definition of done

- `search-index.json` generates alongside the HTML on every
  `findings-feed.yml` run, entry-count-matched, redaction-safe (inherited
  from Phase 1).
- `check_publish_safety.py` hard-blocks a hand-curated leak in the JSON
  exactly as it already does for HTML, proven by new unit tests.
- Real-specimen verification (above) run and its results stated in the PR
  description, same as `coder:findings-feed-hand-curated`'s precedent.
- `pytest -q` green; workflow diff-quiet behavior confirmed unbroken.

*Staged 2026-09-30. See ADR 062 for the full three-phase plan.*
