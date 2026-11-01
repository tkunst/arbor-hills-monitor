# Overnight-coder handoff — defer the poison_doc_extractor import out of nsite_client's module level

*Staged 2026-10-16, from the modernization assessment's "optional decoupling"
recommendation. Read `docs/overnight-coder.md` first. Small, mechanical,
no-behavior-change refactor. Recommended tier: Sonnet (this one doesn't need
a strong model).

**Cloud-ready:** Runs in cloud mode: ends at a DRAFT PR. No live credentials
needed — pure refactor, hermetic tests only.

**Live-path check:** `nsite_client.py` backs the core, always-live document
pipeline AND all seven nSITE profile watchers (Streams K–Q). This change must
produce **zero behavior change** to any of them — verify via the "no
behavior change" test below before considering this done. Since nothing here
changes what any watcher does, a green test suite is sufficient evidence;
this isn't the kind of live-path change Step 3 means by "verify against a
real specimen" (no parsing/extraction logic changes), but say so explicitly
in the PR rather than assuming.*

## Background — what the problem actually is (read before assuming "just move the import")

The modernization assessment's exact finding: **"The low-level API client
pulls in the parser. `nsite_client.py:93` imports `poison_doc_extractor`,
which imports `egle_doc_parser`. As a result all 7 Stream K–Q watchers load
the PDF and LLM stack even though they never classify anything."**

So the actual problem is a **module-level import graph** issue, not a
behavior/correctness issue: `nsite_client.py` has `import poison_doc_extractor
as pde` at its top (alongside stdlib imports and `requests`, confirmed at
roughly line 93 as of this writing — re-check the exact line). Because Python
imports are eager, EVERY module that does `import nsite_client` —
including the seven profile watchers (`nsite_violations_watcher.py`,
`nsite_compliance_actions_watcher.py`, `nsite_evaluations_watcher.py`,
`nsite_permits_watcher.py`, `nsite_complaints_watcher.py`,
`nsite_public_notices_watcher.py`, `nsite_submissions_watcher.py` — confirm
which of these actually import `nsite_client` vs. a thinner sibling client;
check each file's own imports before listing it in the PR) — transitively
loads `poison_doc_extractor` and, through it, `egle_doc_parser` (the PDF/LLM
parsing stack: `fitz`/pymupdf, `anthropic`, `pydantic`, etc.), even though
none of those seven watchers ever download or parse a PDF — they only poll
EGLE's nSITE JSON API profiles.

**`poison_doc_extractor` is used inside `nsite_client.py`'s `download_pdf()`
function only** (confirmed: `pde.sniff_format`, `pde.synthesize_pdf`,
`pde.ExtractionError` all appear only inside that one function, as a
last-resort fallback when every URL source returns non-PDF content — this
logic is woven into `download_pdf()`'s multi-URL retry loop, not cleanly
separable into a different file without a larger, riskier refactor). The
TASKS.md line describing this item calls it "optional" and groups it with "give
WDS its own workflow" (explicitly out of scope — see below) as a pair of small
cleanup items, which matches: **the fix is to defer the import, not to
relocate the extraction logic to a different module.** Do not attempt the
larger refactor of moving `download_pdf()`'s poison-extraction fallback to a
different "processing layer" file — that would touch retry/URL-fallback logic
that's been through careful, documented hardening (see the ADR 011 addendum
comments right above the fallback code about a real corrupted-PDF incident)
and isn't what this item is asking for.

## What to build

1. Remove `import poison_doc_extractor as pde` from `nsite_client.py`'s
   top-level imports.
2. Add a **local import inside `download_pdf()`** (`import poison_doc_extractor
   as pde` as the first line of the function, or immediately before its
   first use — your call, match this repo's existing style for any other
   lazy/deferred imports if it has one, e.g. `archive_client.py`'s own
   docstring about being "import-light" and lazily importing google OAuth
   libs is the precedent to match).
3. Confirm no OTHER function in `nsite_client.py` uses `pde.*` — if one does,
   the local import needs to go somewhere that covers all of them (or each
   gets its own local import; your call, prefer the simpler option).
4. Verify this actually achieves the goal: importing `nsite_client` alone,
   without ever calling `download_pdf()`, should NOT pull `poison_doc_extractor`
   or `egle_doc_parser` into `sys.modules`. Write the test below to prove it.

## Tests

- **The key regression/goal test:** in a clean subprocess or using
  `importlib`/`sys.modules` manipulation (clear any cached import of
  `nsite_client`, `poison_doc_extractor`, `egle_doc_parser` first), `import
  nsite_client` and assert `"poison_doc_extractor"` and `"egle_doc_parser"`
  are NOT in `sys.modules` afterward. Then call `nsite_client.download_pdf(...)`
  (mocked/stubbed appropriately) and assert they ARE now present. This is the
  actual thing being fixed — test it directly, don't just trust that moving
  the import line worked.
- Existing `tests/test_nsite_download.py` (the `download_pdf()` test suite,
  including its poison/extraction-fallback tests) must stay green,
  unmodified in behavior — this is a pure refactor, so if any of those tests
  need a behavior change to pass, something went wrong.
- All seven profile watchers' existing test suites must stay green,
  unmodified.
- `pytest -q` fully green overall.

## Out of scope

- **"Give WDS its own workflow"** — the other half of the same TASKS.md
  line. This repo's `origin` is SSH, so per `docs/overnight-coder.md` Step 4
  a new `.github/workflows/*.yml` file CAN land via a normal commit if SSH
  push works in this session — but this handoff explicitly scopes that out;
  leave it for a local session to decide, since it also involves a scheduling
  decision (how often, replacing or supplementing `daily.yml`) that's more
  than a mechanical import move. Note in the PR: `wds-archive.yml` already
  exists in `.github/workflows/` — verify whether WDS's core watcher
  (distinct from `wds_archiver.py`) genuinely still runs only inside
  `watcher.py`/`daily.yml` before assuming this follow-on is even still
  needed; either way, don't act on it here.
- Any change to `download_pdf()`'s actual retry/fallback behavior, URL
  ordering, or error handling.
- Any change to `poison_doc_extractor.py` itself.

## Acceptance

- `pytest -q` fully green.
- The new import-isolation test (above) passes, proving the seven profile
  watchers no longer transitively load the PDF/LLM stack merely by importing
  `nsite_client`.
- The PR states plainly this is a zero-behavior-change refactor and names
  which test proves it.
- CLAUDE.md: the `nsite_client.py` Architecture entry already documents
  (per the modernization assessment: "now documented in CLAUDE.md, no code
  change") the fact that it imports the PDF/LLM parser via
  `poison_doc_extractor` — update that entry to reflect that the import is
  now deferred/lazy rather than module-level, so the documentation matches
  the new reality rather than describing a problem that's now fixed.
- Regenerate `docs/topology/` artifacts (`scripts/extract_topology.py` /
  `scripts/gen_mermaid.py`) since this changes a real dependency edge (static
  import graph) between `nsite_client.py` and `poison_doc_extractor.py` /
  `egle_doc_parser.py` — the topology map should reflect that the edge is now
  conditional/deferred rather than a module-load-time edge, if the generator
  distinguishes that; if it doesn't, at least confirm the map isn't now
  actively wrong about which watchers depend on the parser stack.
