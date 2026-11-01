# coder:findings-feed-hand-curated -- surface hand-curated public records on the Findings feed

**Goal:** make the public Findings page (`site/public-records/`) also render the
`Hand-Curated Files` Sheet tab, so human-vouched public records appear alongside the
auto-scraped `New Documents` + `Historical Documents`. Today `scripts/gen_findings_feed.py`
pulls only `TAB_NEW` + `TAB_HISTORICAL` (+ `Archived PDFs` for links), so every
hand-curated record (~71 as of 2026-09-02, and growing) is invisible on the page.

**Why it matters.** Hand-curated records are genuine public regulatory documents the nSITE
pollers cannot reach -- e.g. GFL's 2026-08-21 GCCS 120-day extension-request letter, which
EGLE never posted public on MiEnviro (only the transmittal), so the scraper never saw it.
They are already public in the `Hand-Curated Public Records` Drive folder; they just do not
appear in the public Findings list. This closes that gap for all of them at once.

## Scope (what to change)

- **`scripts/gen_findings_feed.py`** -- after reading `TAB_NEW` + `TAB_HISTORICAL`, also read
  the `Hand-Curated Files` tab, map each data row into a feed row (schema below), and merge
  it into the combined list *before* the existing sort/paginate step (`parse_feed_rows` ->
  `build_feed`). Reuse the existing `_tab_values(service, sheet_id, tab, a1)` helper.
- **`sheet_writer.py`** -- add `TAB_HANDCURATED = "Hand-Curated Files"` (the constant does
  not exist yet; the append path uses the literal). Optionally add a small
  `handcurated_feed_rows(sheets, sheet_id) -> list[list]` reader that returns rows already
  in `FEED_HEADERS` order, mirroring how `feed_row()` shapes auto rows -- keeps
  `gen_findings_feed.py` thin.
- **`findings_feed.py`** -- pure render logic; likely NO change if the mapped rows conform to
  `FEED_FIELDS`. Only touch it if you add the "editor-added" label in Decision 1.

## Schema mapping (Hand-Curated Files -> `FEED_FIELDS`)

`Hand-Curated Files` columns: `curated_filename | title | source | doc_date | facility |
doc_type | risks | origin_url | note | drive_link | added_at | folded_into_public`.

`FEED_FIELDS` = `date_filed, document_name, type, risks, severity, summary, key_data_point,
link, facility`. Map:

- `date_filed`   <- `doc_date`
- `document_name`<- `title`
- `type`         <- `doc_type`
- `risks`        <- `risks`
- `severity`     <- `""` (Hand-Curated has no severity column; blank is fine -- confirm the
  renderer tolerates an empty severity, several auto rows already have blank/routine)
- `summary`      <- `note` (fall back to `title` if `note` is blank)
- `key_data_point` <- `""`
- `link`         <- `drive_link`
- `facility`     <- `facility` (may be blank; `facility_display()` already passes unknowns
  through unchanged)

## Decisions (pick the safe default, or surface + stop as a draft PR)

1. **Distinguish hand-curated rows in the feed?** Recommended: yes, a small "Editor-added
   record" label so a reader can tell a human-vouched doc from an auto-scraped one (this
   respects the separate-surface provenance rationale in `docs/hand-curated-intake-design.md`
   and ADR 010). Cheapest option: prefix `document_name` or set a badge in `render_entry`.
   If unsure, ship v1 WITHOUT the badge (rows just merge in) and name the badge as a
   follow-on -- do not block the merge on it.
2. **Partial dates.** At least one Hand-Curated row has a partial `doc_date` (e.g. `2022-03`).
   `parse_feed_rows` sorts "newest Date Filed first"; verify a partial date does not crash the
   sort (pad/coerce to a sortable value if it does). Add a test row for this.
3. **Double-listing (ANNOTATION-mode overlaps).** A few hand-curated records' raw files are
   ALSO in the auto tabs under a generic classification (intake design's ANNOTATION mode), so
   they could appear twice (once auto, once curated). For v1, ACCEPT the double-listing (the
   curated title is more descriptive and it is harmless); note cross-surface de-dup as a
   follow-on. Do NOT attempt cross-surface auto-dedup in v1 -- that is the fragile path the
   intake design warns about.

## Tests (hermetic, mirror the existing findings_feed tests -- no network)

- A sample Hand-Curated row maps to a valid `FEED_FIELDS` dict (all 9 keys present).
- A partial-date row (`2022-03`) sorts without raising.
- A blank-`facility` / blank-`severity` row renders via `render_entry`.
- The combined feed count == old count + number of Hand-Curated data rows.

## Verify against a real specimen (this is a LIVE, scheduled path)

`findings-feed.yml` regenerates `site/public-records/` daily and `pages.yml` deploys it, so
this is a live-path change per `docs/overnight-coder.md` Step 3: real-specimen verification is
mandatory, no mocked-green-only merge. Run `python scripts/gen_findings_feed.py` locally (it
needs the service-account creds; it is read-only against the Sheet + writes the static HTML)
and confirm:

- The ~71 hand-curated rows now appear in the generated feed.
- **GFL's 2026-08-21 extension-request letter appears near the top** -- curated_filename
  `2026-08-21-arbor-hills-gfl-120-day-gccs-extension-request.pdf`, `doc_date` 2026-08-21,
  `drive_link` `https://drive.google.com/file/d/1ia9-7tJeKUUuJ8cBfw5R0YkCzmrq7Kqx/view` --
  with a working link.
- The page's total count == previous total + hand-curated count.
- No crash on the partial-date row; pagination still works (`PAGE_SIZE` unchanged).
- The feed's own shrink/diff guard (findings-feed.yml's `git diff --cached --quiet` regex)
  still behaves -- a large one-time jump in count is expected and fine on this run.

## Guardrails

- **Never add rows to the auto tabs** (`New Documents` / `Historical Documents`). The intake
  design forbids hand-added rows there (they key on nSITE `doc_id`; a keyless row risks
  dedup confusion or silent overwrite). This change reads the SEPARATE `Hand-Curated Files`
  tab into the render only -- it writes nothing to any Sheet.
- No new external source and no `enabled` flag -- this is a render-pipeline change.
- No em-dashes in code/comments (repo convention: use `--`).
- The public-repo push is Trisha's step (privacy pre-push gate). Open a PR; ship v1 with the
  safe defaults above, or stop as a draft PR if you want her to rule on Decision 1/3.

*Staged 2026-09-02. Context: the 2026-08-21 GFL extension letter was hand-curated to the
monitor that day (Hand-Curated Files row 72) and Trisha noted hand-curated docs never reach
the public Findings page. This is the fix for all of them.*
