# Security review — NESHAP exceedance-extraction parser (`neshap-exceedance-extraction`)

*PR #53 (`neshap-exceedance-extraction`). This pass reviewed branch tip `c98ef6c`
(the round-1 fix commit — the diff at the time this pass ran). A second pass (see
"Round 2 re-review addendum" below) independently re-reviewed the branch after it
gained the round-2 `_section_end`/`_find_next_any_divider_page` divider-boundary
fix, at branch tip `3889419`, specifically re-tracing that new logic rather than
assuming the first pass already covered it. A round-3 code-only fix (the
nearest-divider correction, non-security) landed after both security passes; see
the "Round 3 note" at the end of this file for why it doesn't reopen either
verdict. **Result across both security passes: zero medium/high findings — no
exploitable vulnerability introduced.***

## Tooling note — read this first

**The literal `/security-review` slash command could not be run against this PR.**
This build session was itself a subagent operating on this repo from a parent
session rooted elsewhere; the skill's automatic `git diff origin/HEAD...` context
gathering runs from a fixed working directory that isn't this repo, so it failed
with `fatal: ambiguous argument 'origin/HEAD...'` regardless of how this session's
own shell was `cd`'d. Confirmed the same command succeeds instantly when run
directly from this repo's root — the skill's own preprocessing was the blocker, not
the repo or the diff.

**Substitute used:** a fresh, diff-only subagent (`code-modernization:security-auditor`,
no session context) was given the PR's actual diff and tasked with the identical
methodology `/security-review` uses — an OWASP Top 10 / CWE-oriented trace of every
new data flow the diff introduces, in this repo's own established write-up format
(see the precedent files below). This is disclosed explicitly rather than presented
as if the slash command itself ran, per this repo's standing rule that the security
gate is Trisha's call, always, and should never be represented as more automated
than it actually was.

## Attack-surface delta

- `neshap_table_parser.py` (new): a pure-Python line state-machine over text
  extracted from a locally-opened PDF via `fitz` (PyMuPDF). Public entry points:
  `parse_exceedances(pdf_path)`, `parse_enhanced_monitoring(pdf_path)`,
  `parse_report(pdf_path)`, `parse_report_metadata(pages, enhanced)`.
- `tests/test_neshap_parser.py` (new): hermetic, synthetic `[(text, page)]` /
  page-text fixtures; the PDF-facing entry points are exercised by monkeypatching
  `_page_texts` (the sole `fitz`-touching function), so no PDF is ever opened at
  test time.
- `docs/decisions/033-neshap-exceedance-extraction.md`,
  `docs/overnight-coder-handoffs/neshap-exceedance-extraction.md` — documentation
  only.
- **Confirmed via grep: zero callers of `neshap_table_parser` anywhere else in the
  repo.** Nothing in `watcher.py`, `backfill.py`, any router, or any GitHub Actions
  workflow invokes it. This is a standalone parser + tests, not a live/reachable
  path in this PR.

## Flows traced

1. **`pdf_path` → `fitz.open(pdf_path)` (path traversal / arbitrary file read).**
   `_page_texts()` opens whatever path is passed. **Not exploitable now** — no
   caller exists in this PR to feed it an attacker-influenced path. Mirrors the
   identical pre-existing pattern in `woi_table_parser.py`, whose callers source
   `pdf_path` from trusted, repo-internal values. **Flag for the named CSV/publish
   follow-on:** when this module gets wired into a poller or a CSV-builder, verify
   the path is built the same trusted way — never derived from a document-controlled
   or user-controlled string.

2. **Regex injection / ReDoS.** Every regex in the module is single-pass and
   linearly-matchable — no nested/overlapping quantifiers of the ambiguous
   `(a+)+`/`(a|a)*` shape. Verified empirically against adversarial inputs
   (200k–560k char worst-case strings) for the least-trivial patterns (`WELL_RE`'s
   digit-lookahead, `_REPORTING_PERIOD_RE`'s lazy match, `_RCA_NOT_REQUIRED_RE`):
   all sub-20ms. The rest (`DT_RE_APPENDIX_A`, `DT_RE_APPENDIX_F`, `FLOAT_RE`,
   `DURATION_RE`, `_DOWNWELL_NOT_CONDUCTED_RE`, `_TRANSMITTAL_DATE_RE`,
   `_ANY_APPENDIX_DIVIDER_RE`) are single-quantifier literal/character-class
   patterns with no backtracking ambiguity by inspection. **Not exploitable.**

3. **Format-string injection.** The only string formatting on non-constant data is
   `f"APPENDIX {letter}"` in `_find_divider_page`, where `letter` is always a
   module-internal literal (`"A"`, `"B"`, `"F"`, `"G"`) passed by the module's own
   entry points — never derived from parsed PDF content. No `.format()`/`%`-style
   formatting is applied to any parsed/extracted value anywhere. **Not applicable.**

4. **Command / OS injection.** No `subprocess`, `os.system`, `os.popen`, or shell-out
   anywhere in the new module or tests. **Not applicable.**

5. **SQL / NoSQL / LDAP / XPath injection.** No database or query-string
   construction in this module. **Not applicable.**

6. **Deserialization.** The only parsing beyond stdlib string/regex operations is
   `fitz.open(pdf_path)` + `.get_text()` — PyMuPDF's own PDF-format parser, an
   already-used, unmodified dependency (`requirements.txt` untouched by this PR).
   Same disposition this repo's own precedent review gave the identical pattern
   (`docs/security-reviews/security-review-2026-07-11-nsite-msg-docx-extraction.md`):
   a "native parser could have bugs" observation naming no concrete CVE is below
   this review's reporting threshold — and this instance is weaker than that
   precedent's, since there is no caller feeding it untrusted bytes at all right
   now, and the source PDFs are hand-curated records from a controlled Drive
   folder. **Not exploitable, not new.**

7. **Secrets handling.** No credentials, API keys, tokens, or connection strings
   anywhere in the diff. No `.env`, config, or auth code touched. **Not applicable.**

8. **Logging / sensitive-data exposure.** No `print()`/`logging` calls anywhere in
   `neshap_table_parser.py`. Extracted values (`well_id`, `reading_date`,
   `staff_name`) stay in-process (dataclasses only); nothing is written to disk,
   emailed, or otherwise egressed by this PR. **Not applicable.**

9. **Input validation at the parse boundary.** Every field is regex-gated before a
   dataclass row is constructed (`WELL_RE` requires an `AH` prefix + at least one
   digit; date/float/duration all regex-gated), and the review-fix rounds added
   `plausible` sanity-range properties (temperature/pressure/percent bounds,
   available as API surface, not yet wired into the parse path itself — see the
   ADR) plus `_section_end`'s safer divider-boundary fallback. Malformed/unexpected
   input is dropped, not coerced — a data-quality posture, not a security control,
   but it does mean malformed PDF text can't produce a type-confused value reaching
   a downstream sink (there is no downstream sink in this PR). **Not exploitable.**

## Out of scope (noted, not findings)

- **CSV/formula injection (CWE-1236) at the eventual CSV-builder sink.** This PR
  produces no CSV — explicitly a parser-only PR per the ADR and handoff.
  `staff_name` is free text copied verbatim from the PDF (e.g. `"Philip Trela"`);
  if a future report's Appendix F staff-name cell ever led with `=`/`+`/`-`/`@`,
  a formula-injection payload could ride along unless the follow-on CSV writer
  neutralizes it. **Flag for whoever builds the named follow-on CSV/publish step** —
  not a finding against this PR, since that sink doesn't exist yet.
- **`fitz`/PyMuPDF native-parser memory-safety bugs** — per this repo's own
  established convention (2026-07-11 review), excluded absent a concrete
  CVE/attack path.
- **Log-spoofing from unsanitized log lines** — moot here (zero logging calls in
  this module), noted only for consistency with house convention.

## Convergence

Single review pass on the code paths reviewed here; no findings reached the
reporting threshold. Confirmed no hardcoded local filesystem paths in either new
Python file, consistent with this repo's `docs/decisions/002` forbidden-patterns
rule. This pass's diff snapshot (`c98ef6c`) predated the round-2 correctness fixes
(the `_section_end` divider-bleed fix, the `plausible` docstring correction, and
the added regression tests) — those changes were non-security in nature (data-
integrity/correctness, traced in full in ADR 033), but rather than assume that on
faith, a **second pass was run** (below) specifically to re-trace the new
`_section_end`/`_find_next_any_divider_page` logic.

## Round 2 re-review addendum

*Independent pass, fresh subagent, no context from the pass above. Reviewed
branch tip `3889419` (after the round-2 `_section_end` fix landed). Result:
**zero medium/high findings — confirms, rather than inherits, the verdict above.***

- **Independently re-verified (not re-cited) that zero callers of
  `neshap_table_parser` exist anywhere else in the repo** — a fresh whole-repo
  grep, since this fact is what "not exploitable" rests on for the path-traversal
  flow and the new divider-boundary logic alike.
- **`_section_end` / `_find_next_any_divider_page` traced specifically**: both
  add only bounded linear re-scans (`O(pages)`) of the `pages` list that
  `_page_texts()` already fully materializes before any boundary logic runs — no
  recursion, no unbounded backtracking, no quadratic blowup, so no new
  resource-exhaustion surface. Verified the `hi > lo` invariant holds for every
  call site (both helper functions are only ever called with
  `start_from = start + 1`, so any index returned is strictly greater than
  `start`), meaning `_lines_for_pages(pages, lo, hi)`'s `range(lo, hi)` can never
  go negative or out of bounds. `_ANY_APPENDIX_DIVIDER_RE` (the one new regex) is
  an anchored literal-prefix match with no ambiguous quantifiers — no ReDoS.
  `f"APPENDIX {letter}"` (pre-existing, unchanged) is only ever formatted with a
  hardcoded single-character literal, never parsed PDF content — no format-string
  injection.
- **Framed the underlying data-integrity motivation against this repo's actual
  trust boundary**: source PDFs are hand-curated GFL/EPA compliance filings from
  a controlled Drive folder, not attacker-submitted documents, and this PR has no
  downstream sink (no CSV writer, no publish step) yet — so a future
  adversarially-crafted PDF manipulating page layout to fool this boundary
  heuristic is a data-integrity concern for a future public dataset, not a
  confidentiality/integrity-of-the-system security issue, and stays correctly out
  of scope for this parser-only PR (flagged, alongside the CSV-formula-injection
  item above, for whoever builds the named follow-on).
- Also checked all three new doc files (this one included) for instruction-shaped
  text aimed at manipulating an automated reviewer (prompt-injection-via-doc) —
  found none; the "zero medium/high findings" language here is a genuine reported
  finding, re-derived independently, not inherited on trust.

## Round 3 note

A round-3 code review (Step 5, third and final round under the review cap) found
one genuine bug in the round-2 `_section_end` fix itself: it *preferred* the
specifically-expected next appendix letter's divider over the nearest divider of
any letter, which only closed the "expected letter entirely absent" case — an
intervening different-letter divider closer than the expected one would still be
skipped past, reopening the same class of bleed-through bug. Fixed by taking the
**minimum** of both searches unconditionally (see ADR 033 for the full account and
the empirical reproduction that caught it). This is a **correctness-only** change
— same bounded-linear-scan shape, same `hi > lo` invariant, no new I/O, no new
regex, no new sink — so it does not reopen either security verdict above; a third
security pass was not run for that reason, consistent with how the round-1→round-2
transition was handled (a fresh pass was run there because the actual *logic*
being traced was new, not merely because a commit landed).

## Files reviewed

- `neshap_table_parser.py`
- `tests/test_neshap_parser.py`
- `docs/decisions/033-neshap-exceedance-extraction.md`
- `docs/overnight-coder-handoffs/neshap-exceedance-extraction.md`
- Comparison references: `woi_table_parser.py`,
  `docs/security-reviews/security-review-2026-07-11-nsite-msg-docx-extraction.md`,
  `docs/security-reviews/security-review-2026-07-18-gfl-air-24h-average.md`
