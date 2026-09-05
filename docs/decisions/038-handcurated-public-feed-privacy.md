# ADR 038 -- Publishing hand-curated records: the internal/external privacy split + a no-LLM publish gate

**Date:** 2026-09-05
**Status:** Accepted
**Context stream:** the public Public Records feed (`site/public-records/`,
`findings_feed.py` / `scripts/gen_findings_feed.py`), extending the
hand-curated intake (`docs/hand-curated-intake-design.md`).

## Context

PR #61 surfaced the `Hand-Curated Files` Sheet tab (~71 human-vouched public
records) on the public Public Records feed. A privacy/editorial review of the
actual content found that publishing those rows verbatim would put **personal
names** and **internal working content** on the public page:

- The `note` field is an internal annotation -- working-folder names
  ("full-circle-future-nation-trashfire"), "Trisha-directed", "Found in
  Trisha's FOIA Downloads", internal `.md` cross-references, strategy framing.
- `source` and `title` carry personal names (EGLE/GFL staff, a records
  requester): ~20/71 sources and 5/71 titles.
- Separately, the pre-existing **auto** feed already publishes names (from
  nSITE document titles + the classifier's summaries) -- ~28 rows.

CLAUDE.md makes the automated data layer's source-labeling non-negotiable and
"accuracy over posturing" the brand; a name or internal note on the page is
exactly the failure mode to avoid.

## Decision

**1. Publish only safe, curated fields -- safe by construction.**
`parse_handcurated_rows` maps the published `source` from a NEW redacted
external column **`source_public` (col M)**, never the internal `source`
(col C, which keeps the names for the record). `note` is **not published at
all** (the title is the public description). The internal columns are read
positionally for alignment but never placed in the rendered dict (pinned by
`test_handcurated_never_publishes_internal_source_or_note`). A blank
`source_public` renders "Source: not stated" -- it never falls back to the
internal source.

**2. Redact personal names from published fields at curation time (primary
control).** `title` publishes verbatim, so the dedupe-curate intake process
runs the deterministic `name_check.py` on it and redacts until clean before a
row is written. `source_public` is written already-redacted (org/role/date
kept, person dropped). The 71 existing rows were reviewed and redacted before
merge.

**3. A no-LLM publish gate as a backstop (`scripts/check_publish_safety.py`).**
Wired into `findings-feed.yml` before the commit/push step, it scans the
**generated HTML** (the exact bytes about to deploy -- not a separate Sheet
re-read, which could diverge) and HARD-BLOCKS the deploy if a personal name or
name-shaped token appears in a published hand-curated field. Deterministic:
a denylist of known names + internal markers, plus a name-shape heuristic
(parenthetical / "signed X" / possessive) minus an org/term allowlist. NO
Claude/LLM API.

**4. Scope: hand-curated hard-block, auto warn-only.** The gate hard-blocks
hand-curated fields but only WARNS on the pre-existing auto-feed names. Cleaning
the auto feed (classifier summaries + nSITE titles) is a separate, larger
project (tracked separately), deliberately not coupled to shipping this feature.

## Consequences / residual risks (accepted)

- **The gate is a backstop, not an absolute filter.** Being no-LLM, it catches
  KNOWN names (denylist) and name-SHAPED tokens (heuristic). A brand-new
  person's name written in ordinary prose ("letter from John Smith") is neither
  and would pass -- so **human redaction of the published fields is the primary
  control**, the gate the safety net. Documented plainly, not overstated.
- **A denylist needs upkeep.** A newly-encountered person must be added to
  `name_check.KNOWN_NAMES`; an org/term the heuristic mis-flags goes in
  `ORG_ALLOWLIST`. Both are plain lists.
- **The auto-feed name exposure stays live** until the separate cleanup lands;
  deploying this PR does not change it (warn-only by decision 4).
- **Deploy remains Trisha's step** (public GitHub Pages / privacy pre-push
  gate).
