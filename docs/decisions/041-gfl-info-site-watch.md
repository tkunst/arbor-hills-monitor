# ADR 041 — GFL "informational website" change-watch (Stream S)

*Status: built — 2026-09-11 (`gfl_info_site.enabled: false` pending Trisha's
review + recipient choice + a runner fetch-path probe; see Activation).*

## Context

On 2026-09-10 GFL launched a public "informational website" for Arbor Hills
(`https://arborhillslandfill.com`) — announced by Area Landfill Director Tami
Craig at the 2026-09-09 MMPC meeting — to address community questions about the
proposed expansion (six pages: Home, What We Do, Landfill Improvements,
Community Relations, FAQ, Location & Hours; build credit "TheeDigital", a
marketing agency). It is the **operator's own narrative**, not a regulatory
filing. A launch-day human baseline was captured to Lotext
(`documents/arbor-hills/source-docs/gfl-arborhillslandfill-com-snapshot-2026-09-10/`).

Every other stream in this monitor watches an EGLE or county system. This is the
first watch on the operator's own marketing site, and the signal is different:
not "a new document was filed" but **when and how GFL's public narrative
changes** — a FAQ answer edited, a claim added or quietly walked back, a page
added or removed, or an email contact finally published (the launch site is
phone-only). Being able to diff GFL's live claims against what it said on launch
day is directly useful to the refutation work (the launch README already flags
several checkable claims: the 1000-ft setback, the "never above background"
school-monitor claim, "$130M in improvements", the PFAS-in-wastewater admission).

New external (non-EGLE) source → it **never routes through `egle_doc_parser`**
(the EGLE-document parsing base), per the repo's forbidden-patterns rule.

## Step-1 fetch spike (the gating uncertainty)

The site is behind **Cloudflare**, and plain curl/wget hit an "Attention
Required" challenge in earlier testing — so the spike's real job was to find the
lightest reliable fetch path and confirm the FAQ *answers* (collapsed accordions)
are actually captured. Findings (2026-09-11):

- **Plain `requests` + a browser User-Agent renders the full DOM**, including the
  collapsed FAQ accordion answers — WordPress **server-renders** them; the
  accordion is only CSS-collapsed. So **no headless browser is needed** (verified:
  the FAQ setback / PFAS / school-monitor claims are all present in the raw HTML).
- Verified from **both a residential IP (`requests`) and a datacenter IP
  (`WebFetch`)** — both returned the real content page, not a challenge. The
  earlier curl challenge is consistent with Cloudflare's block being
  **IP/ASN-reputation-based**, not a UA block.
- `/sitemap.xml` is a WP/Rank-Math **sitemap INDEX** (`<sitemapindex>`) pointing
  at child sitemaps (`page-sitemap.xml`, `local-sitemap.xml`); the real page URLs
  are one level down.

**Decision: ship the `requests`-based fetch path on the normal GitHub Actions
runner** (the pfas-watch precedent), NOT a headless browser. See Residual risk 1
for the one thing this leaves open (the Azure-runner ASN) and the probe that
closes it.

## The load-bearing decision: what to hash

Hashing the raw page is wrong — every fetch carries rotating per-request tokens
(the Cloudflare `/cdn-cgi/challenge-platform` beacon + `__cf*` tokens, WordPress
`nonce=` attributes, `?ver=` cache-busters). A whole-page hash would fire a false
"the site changed!" alert on **every run**.

**Decision — hash the normalized `<main>` content** (`gfl_info_site_client`):

1. isolate the single `<main id="SiteContent">` region (drops nav/header/footer
   and the Cloudflare beacon, which live outside `<main>`),
2. decode any Cloudflare-obfuscated (`data-cfemail`) or `mailto:` email **first**
   (so a future email contact is recovered in cleartext and surfaces in the
   diff),
3. drop `<script>`/`<style>`/comments,
4. reduce to visible text (block-per-line, so the alert diff reads line-by-line)
   **plus** the sorted set of link/asset target **paths with the query string
   stripped** — a rotating cache-buster is invisible; a genuinely new document/
   page link (a new *path*) still trips the hash,
5. SHA-1 that (16 chars — a change token in a Sheet cell, not a security digest).

**Proven live (spike, 2026-09-11):** fetching each of the six pages **twice**
yields a **byte-identical normalized hash while the raw HTML differs every
time** — the whole test spec. `test_gfl_info_site` reproduces it on a synthetic
fixture carrying the same per-request noise (the real page can't be committed —
the repo's data-file rule).

## Discovery, and new/removed-page detection

The page set can grow/shrink, and the watch must alert on a new or removed page,
so the list is **discovered each run**: follow `/sitemap.xml` one level (index →
child urlset), fall back to a nav-crawl of the home page, and always union in a
configured `seed_paths` floor (the six launch pages) so the core pages are
checked even if discovery degrades. `ignore_path_patterns` drops the derived WP
index pages (`/sitemap`, `/sitemap/blog`) and non-page assets (`.kml`, `.xsl`).
(`/privacy-policy` is a real content page — deliberately watched, not ignored.)

The check set each run = **discovered ∪ every URL ever recorded ∪ seed floor**,
and each is fetched and classified against its last row in the tab:

| state | classification |
|---|---|
| never seen, **initial run** OR a **seed** page | silent baseline (initial run = one atomic write) |
| never seen, steady state, **non-seed** | **new-page** alert |
| seen, hash unchanged | no-op |
| seen, hash changed | **changed** alert (with a diff) |
| seen (200 before), **first** HTTP 404/410 | `pending-removal` (SILENT — debounce) |
| `pending-removal`, **second** consecutive 404/410 | **removed-page** alert |
| `pending-removal`, now 200 again | recover (silent if identical, else **changed** with a diff) |
| was `removed-page`, now 200 again | **new-page (returned)** alert |

**Removal is confirmed by a real HTTP 404/410 on the page itself, never by mere
absence from the sitemap** (which can be incomplete or transiently fail) — so a
flaky sitemap fetch can never fire a false "page removed" alert.

**Removal is also DEBOUNCED over two runs** (review-driven, ADR-041 finding #1):
one 404 records a silent `pending-removal` row (preserving the last real snapshot
so a recovery can still be diffed); only a *second consecutive* 404 confirms it
and alerts. This stops a transient site-wide 404 — a WordPress permalink/rewrite
flush, a CDN purge, an origin deploy — from firing a burst of false "REMOVED"
emails (the exact overstatement this project must avoid). A confirmed removal
stores a `(removed)` sentinel hash so a re-run that still 404s does **not**
re-alert. A **seed page's first successful fetch is a silent baseline, not a
"new page"** — a launch page we merely hadn't fetched yet (e.g. it 404'd on the
activation run) must never fire a false "Page ADDED".

## Anti-stampede + crash safety

- The **initial baseline is one atomic append** of all discovered pages, so a
  crash can't leave a partial baseline that the next run misreads as a burst of
  "new" pages (the failure mode an independent review flagged).
- In steady state, if **more than `max_new_pages_per_run` (default 3)** pages
  look new at once (a whole-site republish, a host change, the sitemap
  ballooning), the run **re-baselines them silently** instead of blasting
  new-page emails — the same defense as `watcher.max_new_docs_per_run` / `wds` /
  `gfl_air`. The **same cap applies symmetrically to confirmed removals**: a mass
  removal confirming at once (a multi-run outage, not page-by-page deletion) sends
  **one consolidated notice**, not N — while still recording every row.
- **Liveness** (review-driven, finding #2): a real run (baselines exist) where
  *every* page failed to fetch — most likely Cloudflare walling the runner or the
  site down — **exits 1** so the workflow-failure email surfaces it, rather than a
  green no-op that silently hides the watch having gone blind.
- Durable Sheet row is written **before** the alert email (crash-safe: a kill
  between them loses the alert, never the record, and never re-fires next run
  since the row advanced the stored hash).

## Where the snapshots live (the "into the repo" interpretation)

The handoff said "snapshot each page **into the repo** date-keyed (model on
civicclerk-archive) ... or a repo diff log." **We store state in the append-only
"GFL Info Site Watch" Sheet tab instead of committing snapshot files to the
repo**, for three reasons, and this is called out here explicitly so it can be
redirected if that's not what was wanted:

1. The repo's `data-guard` CI **blocks committing data files**, and a watcher
   that commits from a GitHub Actions run would be a new, riskier pattern (commit
   perms, push races) no other stream uses.
2. The append-only tab **is** a date-keyed durable snapshot — every row carries
   the full normalized text with its Date column — exactly as `pfas_watcher`'s
   own docstring documents ("the tab row carries the full normalized text, which
   is both the diff basis and a durable dated snapshot"). It's also race-free
   (append-only ⇒ no `_meta` clobber).
3. The launch-day human baseline already lives durably in Lotext; this automation
   is the *ongoing* record, and the Sheet is where all of this monitor's state
   lives.

The change email carries the full readable unified diff, so the human-facing "diff
log" the handoff asked for lands in Trisha's inbox on every change.

## Recipients / audience

Scoped **verbatim** to `gfl_info_site.recipients` (Trisha only to start — the
Meeting Watch / MMD / RIDE precedent for a new stream; this is operator-narrative
intelligence, not a coalition-wide broadcast), plus an optional private
`GFL_INFO_SITE_RECIPIENTS_EXTRA` env (the `ALERT_RECIPIENTS_EXTRA` pattern).
**Empty ⇒ DISPLAY-ONLY:** durable rows are written but no email is sent — it
never falls back to the full `alert_recipients` list (the `send_upcoming`
fail-safe, so it can't accidentally broadcast to the coalition list / a public
official).

## Activation

Ships `gfl_info_site.enabled: false` (new-source gate). All Trisha's:

1. review + merge;
2. **before flipping enabled, verify the Actions-runner fetch path is not
   Cloudflare-walled** — run the workflow_dispatch PROBE (below) and confirm it
   logs green;
3. pick recipients, then set `enabled: true` and commit.

Until then, `gfl-info-site-watch.yml` runs on schedule but `_should_run()` makes
every run a quiet no-op. Keyless — no secret to provision (the private-recipient
env is optional).

**PROBE mode** (`gh workflow run gfl-info-site-watch.yml -f probe=true`, or
`python gfl_info_site_watcher.py --probe`): a fetch-path diagnostic that writes
**nothing** to the Sheet and sends **no** email, and runs **regardless** of the
enabled flag. It fetches + normalizes every seed page from the runner and exits
non-zero if any is Cloudflare-walled — the definitive Azure-runner verification.

## Adversarial review — residual risks

1. **The GitHub Actions (Azure) runner IP could be Cloudflare-walled** even
   though residential + datacenter IPs pass — a different ASN. *Detection:* the
   PROBE mode surfaces it green/red before activation; and once enabled, an
   activation-run block on a seed page **exits 1 loudly** (workflow-failure
   email) rather than no-oping silently. *Recovery:* the fallback is a
   self-hosted / local runner — **none exists today**, so that is a real setup
   step for Trisha, and `runs-on` in the workflow is a one-line swap. (Flagged so
   this isn't discovered only at activation.)
2. **Cloudflare could later escalate to a mandatory JS/Turnstile challenge** that
   `requests` can't pass from any IP. *Detection:* `extract_content` raises on a
   challenge page (no `<main>`), so the watch skips-and-warns (partial) or exits 1
   — never a false "changed"; and the **liveness guard exits 1 when *every* page
   fails** (the total-wall case), surfacing it via the workflow-failure email
   rather than a silent green no-op (review finding #2). *Recovery:* switch to a
   headless-browser fetcher behind the same `fetch_page` interface; the diff /
   state layer is unchanged. (A *partial*, page-specific persistent failure is
   still only a log line — a fast-follow could add a per-page consecutive-failure
   alert, matching `gfl_air`'s `max_stale_days`.)
3. **A page redesign moving content out of `<main>`** would drop that content
   from the hash. *Detection:* the change would show as a large diff (or a
   too-short → skip). *Accepted:* `<main id="SiteContent">` is the WP theme's
   stable content wrapper; a theme change is itself newsworthy and would surface.
4. **The `data-cfemail` decode is best-effort** — a malformed token falls back to
   the visible "[email protected]" text, which still surfaces that an email was
   added even if the address isn't recovered. Accepted (kept minimal).
5. **A whole-site republish** could flip every page's normalized hash at once →
   up to ~7 "changed" emails in one run. Accepted: a full rewrite of the
   operator's narrative is genuinely newsworthy, and 7 is the ceiling for a
   fixed-size site; the anti-stampede guard covers new-page bursts, a mass
   *new*-page burst re-baselines silently, and a mass *removal* sends one
   consolidated notice.
6. **A page that returns after a *confirmed* removal is not diffed against its
   pre-removal content** (the confirmed `removed-page` row stores no text), so the
   "returned" alert says a page is back but not what changed while it was gone. A
   *transient* (single-404) removal-then-return **is** diffed (the `pending`
   row preserves the last real snapshot). Accepted as LOW (review finding #4):
   rare, and the returned page is now baselined so the next edit diffs normally.
7. **`fetch_page` follows redirects with no post-redirect host re-check**
   (theoretical redirect-SSRF): a same-host page 301-ing to a link-local address
   would be followed. Requires compromising GFL's origin (remote threat) and is
   inherent to any redirect-follower; the more plausible vector — a hostile
   *sitemap* child — is explicitly host-guarded (review finding #6). Accepted;
   noted so a future hardening pass can disable redirects + canonicalize if the
   threat model changes.

## Tests

`tests/test_gfl_info_site.py` — hermetic (synthetic HTML + a monkeypatched
`requests.get` URL router; no network, no creds): canonicalization, sitemap-index
discovery + nav/seed-only fallbacks, hash-stability across rotated per-request
noise, real text/link-change detection, cf-email decode, challenge detection by
content-presence, HTTP-status handling, and the full watcher classify matrix
(initial atomic baseline, unchanged, changed+diff, new-page, removed-page +
no-re-alert, removed-then-returned, anti-stampede re-baseline, fetch-fail
skip-vs-loud, display-only recipients, new-email surfacing).
