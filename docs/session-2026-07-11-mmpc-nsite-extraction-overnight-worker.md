# Session: 2026-07-11 — MMPC activation, .msg/.docx extraction (ADR 011), overnight-worker procedure

Branch: `main` (work happened across `stream-d-mmpc-archive`,
`nsite-msg-docx-extraction`, `overnight-worker-doc`,
`stream-c-security-review-docs`)

## What happened

1. **PR #2 — Mirror D (MMPC archiver, ADR 010): reviewed, merged, activated.**
   `/review` + resolved all comments, `/security-review` (zero HIGH/MEDIUM),
   wrote the security review to the repo, merged to `main`
   (2026-07-11T19:08:45Z). Activated: `GOAUTH_MMPC_FOLDER_ID` secret set
   (Trisha ran the `gh secret set` herself via `!` — Claude Code's
   permission classifier blocked the tool call despite the exact command
   being proposed first), `mmpc_archive.enabled: true` flipped in
   `config.yml` and committed, then manually triggered via
   `workflow_dispatch` — run succeeded (2026-07-11T20:56:53Z).

2. **PR #3 — .msg/.docx poison-doc extraction for WRD-Groundwater (ADR 011):
   built, reviewed twice, merged.**
   Goal handed in mid-session: the Arbor Hills Remediation Area facility's
   `.msg`/`.docx` filings were silently poisoning (permanently
   skipping/stubbing) after 3 failed attempts, including the 15 real
   documents Trisha hand-pulled 2026-07-07 establishing the un-permitted-
   discharge-to-groundwater finding and the PFAS-to-Johnson-Drain treatment
   thread. Per her explicit instruction, de-risked against those real
   specimens *before* writing production code — this caught two real bugs
   (a silent `fitz.insert_textbox()` failure, a mixed-doc OCR gap) that
   synthetic fixtures never would have. Built `poison_doc_extractor.py`,
   wired it into `nsite_client.py`'s download fallback chain, added
   `RETRY_DOC_IDS` (backfill.py + workflow input) for the retroactive
   backfill of the 14 real doc_ids — not yet triggered. `/review`, plus an
   explicitly-requested second independent code review, found and fixed 6
   more real bugs across two rounds (docx-attachment garbling, missed OCR
   on merged scans, stale skipped/processed state, an unverified `doc_url`
   fallback assumption, then tab/br-gluing and text-box duplication in the
   docx text extractor). `/security-review` twice — zero HIGH/MEDIUM
   findings both times; written up in
   `docs/security-review-2026-07-11-nsite-msg-docx-extraction.md`. Merged
   to `main` (fast-forward, `6ef223d`, 2026-07-12T02:09:11Z).

3. **`docs/overnight-worker.md` — procedure for unattended overnight coding
   loops.** *(Renamed to `docs/overnight-coder.md` at merge — see postscript.)*
   Authored per Trisha's 8-step description (branch → iterate → PR →
   `/review` → `/security-review` → merge), refined via `AskUserQuestion`
   on the ambiguous points (security HIGH always stops for her; full
   autonomy to merge+push without asking; goal supplied at invocation, not
   read from a backlog automatically). Then put through two rounds of
   adversarial review via the `advisor` tool, briefed that the goal will
   typically be one row of the "Need to get / not yet automated" table in
   the Cowork-workspace `arbor-hills-multiple-data-sources.md` — one
   automatable row per night is the intent. Round 1 fixed 7 gaps: a
   clean-tree check that would've tripped on this repo's normal untracked
   clutter; required new data-source integrations to ship `enabled: false`
   rather than merge live off mocked tests alone; added a graceful
   "not automatable" exit for a failed feasibility spike; added same-goal
   PR/branch dedup; added an iteration cap; routed security-relevant
   `/review` findings to the same hard-stop as `/security-review`;
   clarified the goal handoff needs real detail (URL + approach), not a
   bare backlog label. Round 2 fixed 3 more: widened the "ship disabled"
   rule to also require real-specimen verification for edits to
   *already-live* paths, not just brand-new sources (the exact shape of
   the ADR 011 work above); unified every non-security stop onto one
   surface (a draft PR, code-or-doc-only) instead of leaving two of the new
   stops with nowhere defined to land; made the iteration cap concrete
   (~6 write-test-fix cycles).

4. **Repo housekeeping.**
   `docs/overnight-worker.md` was committed directly to `main` per
   instruction, then — per a follow-up instruction — moved onto its own
   branch `overnight-worker-doc` (`7739a47`), since `main` hadn't been
   pushed yet (a clean `git reset --hard origin/main` + branch move, no
   rewrite of anything already public). Two stray untracked docs left over
   from the 2026-07-10 Stream C session (a security review + session
   write-up, both already describing work merged to `main` that session)
   were read, confirmed clean, and committed onto their own branch
   `stream-c-security-review-docs` (`e788ce4`) off `main`.

## Repo state at end of session

- `main`: `6ef223d`, in sync with `origin/main`. Working tree fully clean —
  nothing untracked, nothing uncommitted.
- Two local branches, each one commit ahead of `main`, **neither pushed,
  no PRs open**:
  - `overnight-worker-doc` (`7739a47`) — the overnight-loop procedure doc.
  - `stream-c-security-review-docs` (`e788ce4`) — the swept-in Stream C
    docs. **Currently checked out.**
- `wds.enabled: true` and `mmpc_archive.enabled: true` on `main` — Stream C
  (WDS) and Mirror D (MMPC) are both live.
- Secrets: `GOAUTH_MMPC_FOLDER_ID` added this session (2026-07-11); all
  others pre-existing.
- CI green on `main`'s latest commit (tests/markdownlint/secrets/links all
  passed at 2026-07-12T02:09:12Z, the PR #3 merge).
- The ADR 011 retroactive backfill (`RETRY_DOC_IDS` for the 14 real
  WRD-Groundwater doc_ids) has **not** been triggered — a known follow-up,
  not something this session did.
- No background jobs running.

## Safe to close

**Yes.** Nothing uncommitted, nothing in-flight, nothing broken. Two loose
ends for next time, neither urgent — nothing live depends on either:

- `overnight-worker-doc` and `stream-c-security-review-docs` are local,
  unpushed branches. Push + open PRs (or fold in some other way) whenever
  convenient.
- The ADR 011 retroactive backfill is still pending, whenever it's wanted.

## Postscript (2026-07-11, late evening — merge prep session)

A follow-up session reviewed the overnight-loop procedure and folded both
loose-end branches into `main` so the loop could run that night:

- `docs/overnight-worker.md` was renamed to `docs/overnight-coder.md`
  (avoiding a name collision with the Lotext overnight-worker Routine,
  which is a read-only queue drainer, not this coding loop). The branch
  commit was amended for the rename (`7739a47` → `92f17cd`), then
  fast-forward-merged to `main`.
- `stream-c-security-review-docs` was rebased onto `main` and merged.
- This session write-up was committed to `main` and both local branches
  were deleted (fully merged, recoverable from `main`).
- `.claude/settings.local.json` allowlist expanded (`git *`, `gh *`,
  `pytest *`) so an unattended run doesn't stall on permission prompts.

## Postscript 2 (2026-07-12 — ADR 011 retroactive backfill: investigated, not run)

Asked to actually do the ADR 011 retroactive backfill (the 14 doc_ids listed
in its Activation section). Per the ADR's own explicit instruction ("confirm
against the live tab before running, don't trust this list blindly"), checked
live Sheet state first rather than just triggering `backfill.yml`. Good thing:

- **All 14 doc_ids were already `processed`**, timestamped 2026-06-15 — the
  monitor's launch date, three weeks before the 2026-07-07 hand-pull and
  five weeks before ADR 011 existed. `RETRY_DOC_IDS` never re-touches a
  `processed` doc by design, so running it with this list would have been a
  guaranteed no-op. **Did not run it.**
- Spot-checked substance (not just status) on all 14, since "processed"
  doesn't mean "processed well" — 13/14 hold real extracted evidence
  (including the two evidentially critical ones: the E. coli/fecal-coliform
  lab result and the Mercury/PFAS sample, both with real analyte values
  already in the Sheet). **1/14 is genuinely thin**: doc
  `1681010528757159679` ("Arbor Hills Compost.msg") — the classifier itself
  flags that its 4 photo attachments aren't accessible to it.
- Cross-checked what's *actually* `skipped` on this facility right now: a
  completely different 15 documents — legacy 2002 `.doc` (the pre-existing,
  still-unsupported gap), a couple of images, one nForm. **None are
  `.msg`/`.docx`** — ADR 011's extractor has no live target today. The ADR's
  doc_id list simply doesn't match current reality (most likely: those 14
  docs were readable as plain PDFs back at original 2026-06-15 backfill
  time, before the "parking as poison" framing applied to them specifically
  — plausibly tied to the render endpoint's behavior on launch day).
- Investigated fixing the one real gap (a narrower `FORCE_REPROCESS_DOC_IDS`
  mechanism, since `RETRY_DOC_IDS` deliberately never touches `processed`
  docs) — tested empirically *before* building anything, per this repo's own
  established practice. Fetched the doc's real bytes: confirmed genuinely
  `.msg`, confirmed `poison_doc_extractor.py` (ADR 011) correctly embeds all
  4 real photos (`IMG_0682`–`0685.JPG`, 2018 storm-water review photos) as
  image pages in a 17-page synthesized PDF — **extraction is not the
  problem.** But `egle_doc_parser._classify_with_claude()` is text-only (it
  sends `get_text()` output, never page pixels), and OCR correctly finds
  zero text on genuine photos. Reprocessing would hand Claude the identical
  envelope text it already has — no gain. **Did not build the mechanism** —
  went back to Trisha with the finding instead of building something with no
  real beneficiary.
- Logged the real underlying gap as `docs/roadmap.md` (new file, committed
  `2daaf88`): vision-based classification for image-only pages. A real,
  separate, not-yet-scoped project, not a bug fix.
- Extracted the 4 real photos + the email's text (previously not saved
  anywhere as readable text) and sent them directly to Trisha. Found they
  were **already on disk** from the original 2026-07-07 hand-pull
  (`source-docs/gw-recon-handpull-2026-07-07/attachments/10-IMG_0682–0685.JPG`)
  — flagged the duplication rather than silently leaving redundant copies.
  Wrote `documents/arbor-hills/arbor-hills-compost-msg-2026-07-11.md` (Cowork
  workspace, not this repo) pointing at the existing photo paths, the real
  nSITE URL, and the email text, so Trisha can find all of it later without
  re-deriving any of this.
- Trisha's call, given both options: **accept the thin row as-is** — it
  already honestly says the substance is unreadable rather than guessing,
  and the Sheet's link goes straight to the real source.

### Repo state after this postscript

- `main`: `2daaf88`, in sync with `origin/main`. Working tree clean.
- No open branches, no open PRs, no production Sheet writes made (every
  check this postscript describes was read-only against the Sheet; the one
  local download was to a scratchpad temp file, cleaned up after).
- `docs/roadmap.md` is the only new artifact in this repo from this
  postscript.

### Safe to close (updated)

**Yes.** Nothing uncommitted, nothing in-flight, no branches waiting on
review, no production data mutated. The ADR 011 "retroactive backfill" line
item from the original write-up above is now resolved — not by running it,
but by determining it's already satisfied (13/14) or not fixable by that
mechanism (1/14, tracked on the roadmap instead). Nothing left pending from
tonight's work.
