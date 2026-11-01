# Handoff — public "Findings" feed for the GitHub Pages site (historical replay + ongoing)

*Staged 2026-08-21, ahead of a context clear. Read `docs/overnight-coder.md`
first for the branch/test/review/merge procedure. Recommended model tier:
**Sonnet**, not Haiku — this is architecturally open-ended (pagination
scheme, one-time-vs-recurring regeneration) and has to match this repo's and
the site's established conventions exactly, not mechanical extraction. This
is bigger and less pinned-down than the last handoff
(`digest-urgent-recap.md`, PR #43, merged `ac16bce`) — expect the
implementing session to make and document real design calls, not just
follow a recipe.*

## Where things stand (context you'd otherwise lose on a clear)

- The GitHub Pages site (`site/`) is live at
  `https://tkunst.github.io/arbor-hills-monitor/`: `site/index.html`, one
  article (`site/articles/why-i-built-this.html`, series "Unanswered
  Questions Regarding Arbor Hills"), `site/style.css`, deployed by
  `.github/workflows/pages.yml` (GitHub Actions build, not Jekyll — plain
  static files served as-is, no templating layer exists today).
- Both pages have a "Data releases" box linking three GitHub Releases:
  `wellfield-data-2026-08-20`, `perimeter-air-data-2026-08-04`,
  `flagged-wells-2026-08-07`.
- PR #43 (merged today, `ac16bce`) added `pending_urgent_recap`: a
  successfully-sent same-day `[URGENT]` email is now ALSO recapped, labeled
  with its real send time, in the following Sunday digest. That's about the
  *email*. This handoff is about giving the same underlying findings a home
  on the *website* — Trisha's framing: "the monitor feed with a replay from
  the start," i.e. not just new findings going forward, but the full
  historical record, visible on the page.

## Standing site rules (established this session — do not relitigate, just follow)

1. **No email address anywhere on the site.** No mailto link, no "contact
   us," no signup form of any kind. Confirmed explicitly by Trisha; GitHub
   Issues existing implicitly (any public repo has them) is already "more
   than she wants" — don't add an explicit pointer to them either.
2. **No sign-up mechanism for the monitor's distribution.** Same reasoning
   as above — this was explicitly rejected when the site was first built.
3. **Public links only — no Drive links.** Every document's link field in
   the Sheet (`FEED_HEADERS` → `Link` column, see Data source below) is
   already the canonical **nSITE source URL** (`d["doc_url"]`), not a Drive
   mirror link — this matches the existing digest/urgent email behavior
   exactly (see `_digest_record`/`watcher.py`), so reusing this data for the
   feed satisfies the public-link-only rule with zero extra work. Do not
   introduce a Drive archive-mirror link anywhere on the public site, even
   though one exists privately (ADR 007's durable PDF mirror) — that
   decision was made deliberately and should not be revisited without
   asking Trisha first.
4. **No em-dashes, no AI-writing tells** in any new prose (headers, captions,
   intro copy). Full checklist:
   `/Volumes/Samsung-Pro-2TB/Lotext/documents/arbor-hills/arbor-hills-voice-guide.md`
   (Lotext repo, not this one) — em-dashes, triads, banned AI verbs/phrases,
   bullet-list overuse, closer lines, etc. Grep any new HTML for `—` before
   committing; there should be zero.
5. **Refutation-gate reasoning for auto-generated feed content (a judgment
   call already made, for the record):** the classification text going onto
   this feed (`Summary`, `Key Data Point`) is not new prose Trisha is
   writing — it's the SAME data already sitting in the "public/
   operator-visible" case-file Sheet (per `CLAUDE.md`: `GSHEET_ID` is
   public/operator-visible). Republishing it as HTML doesn't create new
   exposure; it's the same information in a more readable form. The full
   blind-adversarial refutation pass (`arbor-hills-prepublish-refutation-gate.md`)
   is written for hand-authored analysis documents and isn't practical at
   1,000+ item scale — don't try to run it per item. If the implementing
   session adds any HAND-WRITTEN intro/framing copy for the feed page
   itself (not the auto-generated per-document rows), THAT copy should still
   get the AI-writing-tells sweep (rule 4 above), same as the existing
   article.

## Goal

Add a "Findings" (or similarly-named — implementer's call, keep it plain)
section/page to the site that replays the monitor's **entire historical
record** of processed documents, oldest data not filtered out, one entry per
document, each with its date filed, facility, document type, severity,
risk tags, the extracted summary/key data point, and its public nSITE link.
Then keep it current going forward without manual intervention.

## Data source

Everything needed already exists, computed, in the case-file Google Sheet —
**no new classification, no new LLM calls, this is purely a read-and-render
job.** Two tabs hold exactly the row shape needed:

```python
# sheet_writer.py
TAB_NEW = "New Documents"          # live-fed docs
TAB_HISTORICAL = "Historical Documents"   # backfilled docs
FEED_HEADERS = [
    "Date Filed", "Document Name", "Type", "Risks", "Severity",
    "Summary", "Key Data Point", "Link", "Facility",
]
```

Both tabs share this schema (`feed_row()` writes both the same way). Read
both via the Sheets API (`values().get`, the same auth pattern every other
script in this repo already uses — `drive_client.sheets_service()`), concat,
sort by Date Filed descending, and that's the entire dataset. This is a
read-only operation against production data; there is no risk of corrupting
anything as long as the implementation never calls a `write`/`update`/`append`
against the case-file Sheet.

**Before committing to a pagination scheme, actually query the row counts**
(`Historical Documents` + `New Documents`, both tabs). Cross-repo context
from earlier this project (now stale, multiple facilities added since) put
N2688 alone around ~750 backfilled documents; six facilities are tracked
today (`config.yml` → `facilities:` — RA, WRD, N1504, P1488, N2688, AHLI),
so the real total is almost certainly in the **1,000-2,000+ row** range.
Confirm the actual number before designing pagination — don't guess and
build for the wrong scale.

## Scope

- **In scope:** a one-time "replay from the start" (render the full
  historical backlog) PLUS a mechanism to keep the feed current as new
  documents get processed going forward.
- **Out of scope:** re-litigating the digest/urgent email behavior (PR #43
  is done, don't touch it). No signup mechanism. No Drive links. No new
  LLM/classification calls — this reads already-computed data only.
- **Open design decisions the implementer should make and document (not
  guess at silently):**
  1. **Pagination scheme**, given the real row count from the check above —
     candidates: fixed N-per-page reverse-chronological with prev/next
     (simplest, scales to any volume), or grouped by year/quarter, or
     grouped by facility. Pick one, explain why in the PR description.
  2. **One-time backfill vs. recurring full regeneration.** Recommended
     default: **regenerate the whole feed from the Sheet on a schedule**
     (e.g., piggyback on the existing Sunday cadence, or its own daily
     cron) rather than incrementally appending from the live watcher. This
     is simpler and more robust — no incremental state to get out of sync,
     no risk of a missed run silently dropping an entry — and matches how
     the rest of this site already works (deployed fresh from `site/` on
     every push). The tradeoff: re-reading 1,000+ Sheet rows on every run is
     more API calls than an incremental append, but it's still a single
     bulk `values().get` per tab, not per-row calls, so the cost is
     negligible. If the implementer has a good reason to prefer incremental
     instead, that's a legitimate call, just document why.
  3. **How the generated files get from a GitHub Actions run back into the
     repo.** This is a genuinely new pattern for this repo — every existing
     workflow writes to the Sheet/Drive, none of them commit generated
     files back into git. Needs: `contents: write` permission, a bot commit
     identity (mirror how other public GitHub-Actions-authored commits are
     typically made — e.g. `github-actions[bot]`), and a plain
     `git add site/... && git commit && git push` at the end of the
     generation job. Confirm this doesn't fight with `pages.yml`'s own
     `paths: site/**` trigger — it shouldn't: the generation workflow
     pushes new static files, which is exactly what should re-trigger a
     Pages deploy, that's the desired chain, not a loop (it's not
     triggered BY a Pages deploy, so there's no cycle).

## Adversarial review (things to get right, not just fast)

- **Read-only against the production Sheet.** Triple-check the
  implementation never calls anything but `values().get` against
  `TAB_NEW`/`TAB_HISTORICAL`. A bug that accidentally clears or reorders
  either tab would be a real data-loss incident, not just a broken feed.
- **Volume vs. GitHub Pages/repo limits.** 1,000-2,000+ small HTML files (or
  a smaller number of larger paginated files) is well within GitHub's
  normal limits, but avoid one giant single-file page if the real count
  comes back large — slow to load, bad for anyone linking to a specific
  finding.
- **Stale data between regenerations.** If going with the recommended
  "regenerate on a schedule" approach, the feed is only as fresh as the last
  scheduled run — that's fine and expected (same latency the digest already
  has), just don't claim "real-time" anywhere in the page copy.
- **Missing/blank fields.** Some historical rows may have blank
  `Key Data Point` or `Summary` (older backfill entries, or a doc that
  failed classification and was written as a stub row via
  `write_stub_row()`, per `sheet_writer.py` — those have
  `doc_type: "(unprocessable source)"` and empty risks). Render these
  gracefully (skip the blank field, don't print "None" or an empty
  bullet), don't crash the whole generation on one malformed row.
- **Facility name consistency.** The `Facility` column values should match
  the human-readable names already used elsewhere on the site ("the
  landfill," "Remediation Area," etc.) — check what's actually stored there
  before assuming it matches; it may be the raw config `name:` field
  (`"Arbor Hills Remediation Area"`, `"GFL-Arbor Hills Landfill-Washtenaw Co"`
  for WRD, etc.) which could read oddly to a public reader unfamiliar with
  the acronyms — consider a small display-name mapping if the raw values
  are too technical, but don't over-engineer this into a big rewrite.

## Tests

- Hermetic unit tests for the render/format logic (given a list of fake
  Sheet-row dicts, produces correct HTML: correct pagination boundaries,
  correct handling of a blank field, correct sort order, correct link
  passthrough with no Drive links introduced). Model the test style on
  `tests/test_email.py`'s `format_digest_body` tests (added this session) —
  same idea, different renderer.
- If a new workflow file is added (for the scheduled regeneration), validate
  its YAML (`python -c "import yaml; yaml.safe_load(open(...))"`, same check
  used for every other workflow change this session) and confirm it doesn't
  break `gh workflow list` or the CI's own path filters.
- Run the full `pytest -q` suite before finishing — should stay green, same
  bar as every other change this session (803 passed as of the last check
  before PR #43; 814 after it merged).

## Definition of done

- A live, publicly-reachable page (or set of paginated pages) on
  `https://tkunst.github.io/arbor-hills-monitor/` showing the full
  historical record of processed documents, oldest data included, each
  entry showing date filed, facility, type, severity, risk tags, summary/
  key data point, and its public nSITE link — no Drive links, no email, no
  signup mechanism anywhere on the page.
- A documented, working mechanism (scheduled regeneration recommended) that
  keeps it current without manual intervention going forward.
- All new/changed HTML swept for em-dashes and the AI-writing-tells
  checklist (see rule 4 above) before committing.
- `pytest -q` green; any new workflow YAML validated.
- PR description states which pagination scheme was chosen and why, and
  confirms the actual row count that was queried before designing it.
