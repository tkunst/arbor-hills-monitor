# coder:public-records-search-index -- pure search-index builder for the public Public Records feed

**Phase 1 of 3** (ADR 062). This phase is NOT wired to anything live -- it adds
a pure function + tests only. Nothing about the deployed site changes when
this PR merges. Phase 2 (`coder:public-records-search-wire-and-gate`) wires it
into the daily pipeline and MUST NOT start until this PR is merged to `main`.

**Goal:** add `findings_feed.build_search_index(rows) -> str` -- a pure
function mirroring `build_pages()` -- that renders the same rows `build_pages`
already receives into a compact JSON array for client-side search, WITHOUT
introducing any information not already public in the HTML.

**Why it matters.** `site/public-records/` is 2,264 documents across 46
chronological-only pages with no search. Full plan + rationale: ADR 062
(`docs/decisions/062-public-records-findability-search.md`) -- read it first,
this handoff is its Phase 1 in executable detail.

## The one thing that must be exactly right

`render_entry(row)` (`findings_feed.py`) is the ONLY place two things happen
today:

1. `redact_names()` + `strip_embedded_date()` run on `document_name`,
   `summary`, `key_data_point` -- computed locally inside `render_entry` and
   **never written back to the row dict**. `row["document_name"]` etc. are
   still the RAW, un-redacted values everywhere else.
2. `link` gets scheme-checked: a Hand-Curated `drive_link` is human-typed free
   text, so only `http(s)://`-prefixed values survive; anything else becomes
   `""`.

`source` is a different case -- it's ALREADY safe by the time a row exists at
all: `parse_handcurated_rows` maps `source_public` (never the internal
`source`/`note` columns) into `row["source"]`, so reading `row.get("source")`
directly is fine. Auto rows never have a `source` key.

**If `build_search_index` reads `row["document_name"]` / `row["summary"]` /
`row["key_data_point"]` / `row["link"]` directly, it WILL leak un-redacted
names and unsafe links into a new public file.** Do not let this happen.

**Required refactor:** extract a small shared helper, e.g.
`_public_view(row: dict) -> dict` (name is your call), that computes the
redacted `title` (`redact_names(strip_embedded_date(...))`), the redacted
`summary`/`key_data_point`, and the scheme-checked `link` -- and have BOTH
`render_entry` and `build_search_index` call it. One place this logic lives,
not two that can drift apart. Keep `render_entry`'s existing HTML output
byte-identical (run the existing test suite to confirm -- `tests/
test_findings_feed.py` pins `build_pages()`'s exact output).

## Output schema

One JSON object per row, keys: `date`, `title`, `facility`, `type`,
`severity`, `source`, `excerpt`, `link`.

- `date` <- `date_filed`, unchanged.
- `title` <- redacted `document_name` (via the shared helper), `"(untitled
  document)"` fallback matching `render_entry`'s.
- `facility` <- `facility_display(row.get("facility") or "")`.
- `type`, `severity` <- unchanged passthrough.
- `source` <- **present as a key (even `""`) iff `"source" in row`; absent
  entirely otherwise.** This exactly mirrors `render_entry`'s `"source" in
  row` check and is the field Phase 2's `check_publish_safety.py` extension
  will use to tell hand-curated entries from auto entries -- do not use a
  sentinel value instead of key-presence, Phase 2 depends on the same test.
- `excerpt` <- redacted `summary`, truncated to ~200 chars on a word
  boundary; if `summary` is blank, fall back to redacted `key_data_point`
  (also truncated); if both are blank, omit the key. **Do NOT include
  `risks`** -- `render_entry` deliberately never shows it (internal
  case-file taxonomy, meaningless to a public reader); the index should not
  either.
- `link` <- the same scheme-checked value `render_entry` computes (via the
  shared helper) -- `""` when the row's link isn't `http(s)://`.

A blank/omitted field should be OMITTED from the JSON object, not written as
`null` or `""` where avoidable (keeps the file smaller; mirrors `render_entry`
never rendering an empty `<p>`), except `source` which per above is `""` on
purpose when a hand-curated row's `source_public` is blank (that's the
key-presence signal Phase 2 needs).

## Not in scope for this PR

- No change to `gen_findings_feed.py`, `findings-feed.yml`, or
  `check_publish_safety.py` -- that's Phase 2, deliberately separate so this
  PR stays small and single-concern (a pure function + tests), and so the
  index is never generated live without Phase 2's gate covering it.
- No UI/JS -- that's Phase 3.
- No new dependency (stdlib `json` only).

## Tests (hermetic, mirror `tests/test_findings_feed.py`'s existing style)

- A row with a `REDACT_NAMES`-matching name in `document_name`/`summary`/
  `key_data_point` produces a redacted `title`/`excerpt` in the JSON output
  -- set the env var in the test the same way existing redaction tests do.
- A hand-curated row (has `source` key, possibly blank) produces a JSON
  entry with a `source` key present; an auto row (no `source` key) produces
  an entry with `source` absent.
- A row with a non-`http(s)` `link` (or blank) produces `link` omitted/`""`,
  never the raw unsafe value.
- A row with blank `summary` falls back to `key_data_point` for `excerpt`;
  a row with both blank omits `excerpt` entirely.
- `risks` never appears in any output object, even when the input row has a
  non-empty `risks` value.
- Output is valid JSON (`json.loads` round-trips) and its length equals
  `len(rows)`.
- Existing `build_pages()` tests still pass unchanged after the shared-helper
  refactor -- HTML output must be byte-identical to before this PR.

## Guardrails

- No em-dashes in code/comments (repo convention: use `--`).
- Stdlib only (`json`, already-imported `html`/`re`/`os`) -- no new
  dependency.
- This is NOT a live-path change (nothing calls `build_search_index` yet) --
  merge-eligible on green tests, same bar as any pure-logic addition. No
  `enabled` flag needed since the function is simply unwired.
- Run the full `pytest -q` suite before finishing -- must stay green.

## Definition of done

- `findings_feed.build_search_index(rows) -> str` exists, unwired, fully
  tested per above.
- The shared redaction/link-safety helper exists and both `render_entry`
  and `build_search_index` use it; `render_entry`'s HTML output is
  unchanged (existing tests prove it).
- PR description states the schema (fields + omission rules) so Phase 2 can
  build against it without re-reading the diff.

*Staged 2026-09-30. See ADR 062 for the full three-phase plan and the reason
each phase is split the way it is.*
