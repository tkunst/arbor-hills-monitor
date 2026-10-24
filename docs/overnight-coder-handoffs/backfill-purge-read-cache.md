# Handoff: coder:backfill-purge-read-cache

Source: Lotext `documents/overnight-coder-queue.md`, the
`coder:backfill-purge-read-cache` block (staged 2026-10-22 from the failed
`backfill` run 39699682035; approved by Trisha 2026-10-23, "approved. Do not
launch yet"; launched in Wave 1, 2026-10-23). The block below is copied
verbatim; it IS the goal.

````
### coder:backfill-purge-read-cache -- backfill FORCE_REPROCESS: read each purge tab once per run instead of once per doc (Sheets read-quota 429 on a 58-id dry-run) -- staged 2026-10-22, Trisha-approved 2026-10-23

- **Dependency:** null
- **Handoff:** none needed; this block is the spec (from the failed `backfill` run 39699682035 on main `997ccc6`, 2026-10-21 8:26 PM ET, "All jobs have failed").
- **Readiness:** ✅ APPROVED by Trisha 2026-10-23 ("approved. Do not launch yet"). LOCAL or CLOUD (offline-testable with a fake Sheets service); touches a LIVE write path (`sheet_writer.purge_doc_rows`), so the apply path needs a real-Sheet check on one doc id; ends at a DRAFT PR. LOW priority: the night's work completed anyway by re-dispatching with `FORCE_REPROCESS_APPLY=true` in batches of 10 (six green runs 10/21 8:29 PM to 10:49 PM ET).
- **What happened:** `backfill.py` ran a FORCE_REPROCESS dry-run over 58 doc ids. For each id it calls `sw.purge_doc_rows(..., dry_run=True)`, which does `spreadsheets().get` plus one `values().get('<tab>'!A2:Z)` per purge tab (6 tabs), so about 7 reads per doc and ~400 reads for the batch, against the Sheets API limit of 60 read requests per minute per user. The seventh doc raised `HttpError 429 ... 'Read requests per minute per user'` and the job exited 1 (`backfill.py` line 245 -> `sheet_writer.py` line 3002). `execute(num_retries=GOOGLE_API_NUM_RETRIES)` backs off for seconds, not the minute the quota needs.
- **Do:** (a) in the dry-run loop, read the spreadsheet metadata and each purge tab ONCE per run (a small in-memory cache keyed by tab, invalidated after any write to that tab) and compute every doc's purge preview from the cached values; (b) the same cache for the APPLY path within one run, re-reading a tab only after it has been purged; (c) on a 429 anywhere in backfill, sleep to the next minute window and retry once before failing (log the wait); (d) a unit test with a fake service that counts `values().get` calls: 58 force ids must cost at most (tabs + 1) reads in dry-run, not 58 x (tabs + 1); (e) keep the printed per-doc preview lines identical.
- **Verify:** a dry-run over the same 58 ids completes in one run with no 429 (count the read calls in the log); APPLY on one already-reprocessed id produces the same rows as the 10/21 batches (diff the tab rows before and after); no change to any other job.
- **Goal (paste this):**

  ```
  Follow docs/overnight-coder.md. Goal + full spec: the coder:backfill-purge-read-cache block in /Volumes/Samsung-Pro-2TB/Lotext/documents/overnight-coder-queue.md -- read that block first, it IS the goal. Copy it to docs/overnight-coder-handoffs/backfill-purge-read-cache.md as the PR's first commit.

  In one line: make backfill's FORCE_REPROCESS dry-run and apply paths read each purge tab once per run (cached) instead of once per doc, and wait out a Sheets 429 once before failing, so a 58-id dry-run no longer dies on the 60-reads-per-minute quota (run 39699682035). Fake-service test counting reads; real-Sheet check on one id for apply. Start from a fresh branch off origin/main. End at a DRAFT PR.
  ```
````
