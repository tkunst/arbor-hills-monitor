# Term search (`scripts/term_search.py`)

An on-demand full-text search over a corpus of Arbor Hills (N2688) PDFs for
specific **event language** — ground subsidence / cave-ins / settlement, leachate
outbreaks / seeps / pooling, boiling / steaming / elevated-temperature signals,
and named locations such as TS-01 — with an optional Claude **relevance filter**.

It answers a retrospective question the daily classifier cannot: *"which records
ever mention X?"* The classifier reads each new document once and keeps only a
summary; it does not retain full text and cannot be queried across the whole
corpus after the fact. This tool re-reads a corpus and does exactly that.

This is a **tool, not a watch stream** — there is no `config.yml` block, no
schedule, and no alerting. Run it when you have a question.

## Why it is fussy about matches

A naive grep for these words is unusable. In testing against the real record it
produced mostly false positives:

- `subsidence` matched **subsidiary** (an SEC 10-K scored 48 hits, all "a
  subsidiary of…")
- `boiling` matched **boiler** and **Boiling Springs Road**
- `outbreak` matched **COVID outbreak**
- `TS-01` matched **"TS1 = primary treatment standard"** in a septic manual

So the tool does two things a grep does not:

1. **Corrected patterns.** Each term has a word-boundary positive pattern plus a
   negative pattern that suppresses the known false-positive contexts (see
   `DEFAULT_TERMS` in the script). Bare `leachate` is deliberately excluded from
   the term list — it appears in nearly every landfill document, so it is a
   count, not a doc list.
2. **A relevance pass.** For each candidate document, the matched snippets —
   never the whole document — are sent to Claude, which judges whether the
   document actually *records one of the target events at the landfill* versus a
   false-positive word match or a generic reference. Output is ranked
   relevant-first; suppressed off-topic hits are listed separately **with their
   snippets shown**, so a human can audit every downgrade (a false-negative would
   otherwise silently drop a real hit).

   The pass is **on by default for `--corpus nsite-archive`** (public EGLE data)
   but **off by default for a local corpus** — a local/FOIA corpus can contain
   PII, and the pass would send its snippets to a third-party API. Add `--llm` to
   run it over a local corpus (you will get a one-line egress notice), or
   `--no-llm` to force it off anywhere.

The relevance filter is an aid, not ground truth: the report is a list of
**candidate hits for human review**.

## Corpora

- **`--root DIR`** (default): search local PDFs under one or more directories.
  Repeatable. `foia-message-histories` is excluded by default (requester PII).
- **`--corpus nsite-archive`**: search the monitor's own OCR'd PDF mirror
  (Mirror B) on Drive. Reuses the archiver's `GOAUTH_*` credentials and downloads
  to a temp directory. This is the "search the monitor's corpus" path; it is slow
  (the mirror holds ~1,700 PDFs).

## Privacy

The generated report **quotes document snippets**. A report over the local /
FOIA corpus can therefore contain sensitive material and is **private** — keep it
in the private workspace, and never write it to this public repo or the public
Sheet. Only `nsite-archive` results (already-public EGLE data) are safe for a
public surface. The script itself contains no private data; its default term set
is generic landfill vocabulary.

Because the relevance pass sends snippets to a third-party API, it is **off by
default for a local corpus** (you must add `--llm`, and you get an egress
notice); it is on by default only for the already-public `nsite-archive`.

## Usage

```
# local corpus, regex only (the default for local — no API calls), write a report
python3 scripts/term_search.py --root "/path/to/source-docs" --out report.md

# local corpus WITH the Claude relevance filter (opt-in; snippets go to the API)
python3 scripts/term_search.py --root DIR --llm --out report.md

# the monitor's own OCR'd archive on Drive (relevance filter on by default)
python3 scripts/term_search.py --corpus nsite-archive --out report.md

# override the term set
python3 scripts/term_search.py --root DIR --terms-file my_terms.json
```

`--terms-file` is JSON of the form `{ "label": ["positive_regex",
"negative_regex_or_empty"] }`.

Key flags: `--no-llm`, `--max-candidates N` (cap docs sent to the relevance
filter, default 60), `--model` (override; default is `config.yml`'s
`anthropic_model`), `--exclude DIR` (repeatable), `--out FILE`.
