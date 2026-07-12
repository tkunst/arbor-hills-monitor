# ADR 012 — PFAS investigation page-watch (content-hash + alert)

*Status: built — 2026-07-12 (`pfas.enabled: false` pending Trisha's review; see Activation).*

## Context

Backlog row 4 of `arbor-hills-multiple-data-sources.md`: watch EGLE's PFAS-
investigation page for Arbor Hills
(`michigan.gov/pfasresponse/investigations/sites-aoi/washtenaw-county/arbor-hills-landfill`)
for status updates. Unlike nSITE (Air docs), WDS (solid-waste records), or
CivicClerk (MMPC PDFs), this source has **no structured feed and no per-record
documents** — it's a prose page EGLE edits in place when the investigation
moves (site lead, sampling results, "anticipated activities", a new linked map
or report). There is nothing to parse or classify; the only signal is *the page
changed*. So the row asks for a lightweight content-hash page-watch — the
pattern `wds_archiver.py` uses for WDS page snapshots — that **alerts** on any
change (`wds_archiver` only mirrors; alerting is the new part). It's early
warning that doesn't wait on the FOIA in rows 2–3.

## The load-bearing decision: what to hash

**Hashing the raw page is wrong**, and the spike proved it against the live page
(2026-07-12, fetched twice + inspected). michigan.gov runs on **Sitecore**: every
theme JS/CSS asset carries a `?rev=…&hash=…` cache-buster that rotates on any
*state-wide* theme redeploy — **44** of them on this page, none related to Arbor
Hills content. A whole-page hash would fire a false "the PFAS page changed!"
alert every time Michigan pushes an unrelated site-wide asset. Two
near-simultaneous fetches were byte-identical, so a naive fetch-and-diff spike
would have missed this entirely — it was only visible by reading the markup.

**Decision — hash the normalized `<main>` content** (`pfas_client.extract_content`):

1. isolate the single `<main>` region (the substantive content; ~20% of the
   page — the other 80% is nav/theme/footer chrome, incl. all 44 cache-busters),
2. drop `<script>`/`<style>`/comments,
3. reduce to visible text, block-boundaries → newlines (so the alert diff reads
   line-by-line), **plus** the sorted set of link/asset target **paths with the
   query string stripped** — so an asset's cache-buster rotating is invisible,
   but a genuinely new document link (a new *path*) still trips the hash,
4. SHA-1 that (16 chars — a change token in a Sheet cell, not a security digest).

**Verified on the real page bytes** (these four are the test spec, reproduced on
a synthetic fixture in `tests/test_pfas_client.py` — the real page can't be
committed, per the no-data-files rule):

| Mutation | Expected | Result |
|---|---|---|
| rotate the **theme** `rev=/hash=` (a state-wide redeploy) | hash **unchanged** | ✅ |
| rotate the **content-asset** `rev=` (Map.pdf republish) | hash **unchanged** | ✅ |
| change a word of `<main>` visible text | hash **changes** | ✅ |
| add a document `<a href>` inside `<main>` | hash **changes** | ✅ |

## Decision (architecture)

- **`pfas_client.py`** — `fetch_page()` (network; rejects non-200 / short bodies)
  + pure `extract_content()` / `hash_text()` / `visible_text()`. Stdlib-only
  (urllib/re/hashlib), no new dependency — same as `wds_client.py`.
- **`pfas_watcher.py`** — standalone, self-terminating, gated on `pfas.enabled`.
  Per page: baseline silently on first sighting; on a hash change, append a row
  **then** email a capped visible-text diff (row first = durable; email best-
  effort — the monitor's standard crash-safe ordering); unchanged → no-op.
- **State is the append-only `PFAS Page Watch` tab, not a `_meta` key.** The most
  recent row per URL holds the last hash + normalized text. This is a deliberate
  improvement over `wds_archiver`'s `_meta["wds_snapshot_hashes"]`: append-only
  means the watch **can't be clobbered by a concurrent job** the way every
  `_meta`-writing job races the single `_meta` cell — so there's no scheduling
  dance and no fresh-read mitigation to get right. The row's last column carries
  the full normalized text: it's both the diff basis and a durable dated snapshot
  (the honest "snapshot" the row asked for), ~8 KB, well under the 50k cell cap.
- **No Drive / OAuth.** The deliverable is the *alert*; the tab row already gives
  the durable snapshot. So this needs only Sheets (service account) + SMTP, both
  already live — nothing new to provision. Raw-HTML-to-Drive mirroring
  (`wds_archiver`-style) is a possible fast-follow, not v1.
- **`pfas-watch.yml`** — daily 7am ET; gated no-op safe from day one; own
  `concurrency` group. Lighter than the other jobs (no Anthropic, no OCR, no
  OAuth).

## Consequences / residual risks (accepted)

*Adversarial review per the standing process rule — none are show-stoppers.*

1. **CI fetch ≠ local fetch (the one to watch).** The page was verified
   fetchable from a residential IP; a GitHub Actions runner (Azure IP) could hit
   an Akamai/bot wall a Mac never sees. **Mitigation, shipped:** when a page has
   **no baseline yet AND the fetch/parse fails**, that run exits **1** (→
   workflow-failure email), so a persistent block **surfaces on the activation
   run** instead of silently no-oping forever while looking healthy. A transient
   failure *after* a baseline exists is skip-and-warn (a blip must never be
   diffed into a false alert). *Detection:* the activation run's exit status +
   the first-day baseline row appearing (or not) in the tab. *Recovery:* if
   blocked, fall back to a browser-based fetch or a proxy — out of v1 scope.
2. **A bot-wall/error page that still returns 200 with a `<main>` > 200 chars**
   could baseline or diff as if it were content. Guarded by the non-200 + min-
   body-bytes + min-`<main>`-chars checks, but not fully eliminable by hashing
   alone. Low probability; the diff in the alert makes a garbage "change" obvious
   to a human on sight.
3. **A page redesign that removes `<main>`** raises `PFASContentError` → treated
   as a bad fetch (skip-and-warn once a baseline exists), so it is **logged, not
   alerted**. Deliberate: a missing `<main>` is far more often a served error
   page than a real edit, and we won't fire an alert we can't trust. If EGLE ever
   restructures the page for real, the watch goes quiet and the missing baseline/
   silence is the tell.
4. **Recall vs. precision — tuned toward precision.** Stripping URL queries means
   a map/report *replaced at the same path with only its `rev=` bumped* won't
   alert on the link alone. Accepted: such a revision is nearly always
   accompanied by visible-text edits (an updated "Content posted" date, new
   "anticipated activities"), which do fire — and a false alert on every
   state-wide theme deploy would erode trust far faster than missing a bare
   rev-bump. This mirrors the repo's `basis`/credibility ethos (ADR 004): a noisy
   watch that cries wolf is worse than a quiet one.

## Alternatives considered

- **Hash the raw page** (literal `wds_archiver` port) — rejected; the 44 rotating
  theme cache-busters make it fire constantly (the whole reason for the `<main>`
  normalization above).
- **Hash `<main>` visible text only, drop the links** — simpler, but blind to a
  new document link added with no surrounding text change. Including link *paths*
  (query-stripped) costs nothing and catches new reports; kept.
- **Fold into `watcher.py`** (like `check_wds` / MMPC polling) instead of a
  standalone job — the row explicitly names the `wds_archiver` page-snapshot
  pattern, which is standalone; standalone also gives fault isolation (a
  michigan.gov hang can't delay the nSITE watcher). Standalone chosen.
- **Store state in `_meta`** (like `wds_archiver`) — rejected in favor of the
  append-only tab; see Decision. Strictly more robust (race-free) and simpler.
- **Archive raw HTML to Drive** (full `wds_archiver` parity) — deferred; adds an
  OAuth dependency for a snapshot the tab row already provides. Fast-follow if
  wanted.

## Activation (Trisha's call)

1. Review + merge this branch to `main`.
2. Set `pfas.enabled: true` in `config.yml` and commit. No new secrets: it reuses
   the live `GSHEET_ID` / `GDRIVE_SA_KEY` (Sheets) and `SMTP_*` (alerts).
3. The first enabled run **baselines every page silently** (records a hash,
   alerts on none) — no seed script needed. Watch that run: a green exit + a new
   `baseline` row means CI can reach the page; a **red exit 1** means the runner
   is blocked (residual risk #1) — resolve the fetch path before relying on it.

Until `enabled: true` is on `main`, `pfas-watch.yml` runs on schedule but
`pfas_watcher._should_run()` makes every run a quiet no-op (verified by
`tests/test_pfas_watcher.py`, the same gate-test that caught the equivalent
"runs before the flag is set" gap in `wds_archiver` — ADR 009's Addendum).
