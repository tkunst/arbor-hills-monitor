# ADR 063: Monthly full snapshot of the GFL perimeter feed, with change detection

Date: 2026-10-06
Status: accepted (ships disabled; Trisha switches it on)
Builds on: ADR 014 (Stream E), ADR 026 (durable capture) and its 2026-10-06 addendum.

## Context

GFL's public dashboard shows only the `H2S_Text`/`CH4_Text` labels ("BDL" below 7 ppb),
so the numeric hydrogen-sulfide and methane readings exist only in the ArcGIS feed. The
daily capture (ADR 026, `capture.mode: all`) saves each NEW reading as it arrives, but it
cannot recover readings it never saw (a long outage past the over-cap batch, a failed
upload) and it cannot see the source deleting or editing PAST readings. Trisha
(2026-10-06): "make sure we have a backup of all actual readings for both CH4 and H2S...
Don't want to rely on that dashboard staying live." She chose a monthly full re-pull,
alerts to her only.

## Decision

`gfl_feed_snapshot.py`, run monthly by `.github/workflows/gfl-feed-snapshot.yml` (2nd of
each month at 14:17 UTC, off the hour when hourly rows land, plus manual dispatch):

1. Pull every row of every layer and table of the FeatureServer with all fields
   (OBJECTID keyset paging; CSV columns are the metadata fields plus any field actually
   returned), plus service and layer metadata and the public dashboard's config. Each
   layer's row count is checked against the server's own count of rows up to the last
   OBJECTID fetched (so a row arriving mid-pull is not a mismatch); a mismatch fails the
   run (exit 1) and nothing is uploaded.
2. Write one zip, `gfl-feed-snapshot-<UTC stamp>.zip`: a CSV per layer, the metadata,
   the dashboard config, and `manifest.json` (source URL, fetch time, readings layer,
   row counts against server counts, SHA-256 of every file). Upload it to the app-only
   GFL Air Exhibit Drive folder (`GOAUTH_GFL_AIR_FOLDER_ID`, the same folder and OAuth
   identity as the daily capture). The data is saved before any comparison runs.
3. Compare the readings with the baseline: the newest earlier snapshot that was itself
   fully compared (it has a `<name>.compared` marker), or, if none was, the oldest
   earlier snapshot. Rows match by OBJECTID and station + time first; an unmatched
   earlier row may then take an unmatched current row with the same station + time whose
   OBJECTID is new or was held by a different reading (a renumbered copy), so a
   source-side reinsert reads as "renumbered", not deleted (the source is known to do
   full reinserts; see ADR 014), while a deletion among duplicate-time rows is still a
   deletion. A mass renumbering (100 or more rows) is itself reported.
   Measurement fields (station, time, H2S, CH4, both labels, wind, temperature,
   humidity, pressure) present in both snapshots are compared; a change in the field set
   is reported once, not as an edit on every row. Deleted readings, edited readings, a
   field-set change, or a mass renumbering email the owner list only, never the public
   recipient lists. If the preferred baseline cannot be read, the next-older compared
   snapshot is used and the email names the one skipped; only when no baseline is
   readable does the run fail.
4. The new snapshot gets its `.compared` marker only after the comparison and any email
   succeed. A failed comparison or email therefore exits 1 and is retried against the
   same baseline next run, instead of the next run comparing against the unreported
   snapshot and hiding the change.

The first run with no earlier snapshot in the folder is a silent baseline.

## Failure handling

Every failure exits 1 so the GitHub failure email surfaces it: an incomplete pull, a
missing Drive folder or credentials, an unreadable baseline snapshot, changes found but an
empty owner list, or a change email that fails or raises. A partial pull is never
uploaded. Network errors and ArcGIS server-side errors (code 500 or above, which the
server returns under load) retry with backoff; other ArcGIS errors fail at once. A re-run
within the same UTC minute finds its snapshot already saved and stops.

Recovery if every compared snapshot became unreadable: remove the unreadable snapshots'
`.compared` markers from the Drive folder; the next run then compares against the oldest
readable snapshot.

## Real-specimen check

Two live builds on 2026-10-06. The final code pulled every layer in 352 seconds:

| Layer | Rows fetched / server count |
|---|---|
| Readings | 225,704 / 225,704 |
| Wind | 36,314 / 36,314 |
| Stations | 7 / 7 |
| Labels | 8 / 8 |

The zip was 12.7 MB. Its readings, read back from the zip, compared clean against the
same pull (0 deleted, 0 edited, 0 renumbered, no field change; 2.6 seconds). Against the
separate manual snapshot taken a few hours earlier: 0 deleted, 0 edited, 0 renumbered,
5 new readings (one hour from the five stations still reporting). Nothing was uploaded
from the local check.

## Security notes (security review 2026-10-06: no high or medium findings)

- The CSVs are the source's values verbatim (they are evidence, and the manifest's SHA-256
  covers them), so they are NOT sanitized against spreadsheet formula injection. Open
  them by importing as text (Sheets/Excel "import", all columns as text), not by
  double-clicking into Excel.
- Layer ids from the service JSON must be integers (they go into file names and URL
  paths); a response over 200 MB, or a readings CSV over 1 GB in a stored snapshot, is
  refused.
- Tracked follow-up (repo-wide, not this change): `send_email` failures are logged with
  the exception text in every stream; an SMTP recipient refusal can include an address.
  Redact once inside `email_alerts.send_email`.

## Consequences

- About 13 MB a month in the Drive folder (about 150 MB a year).
- Ships `enabled: false` (a new job against a live external system). To switch it on,
  set `gfl_feed_snapshot.enabled: true`. Run the workflow once by hand to create the
  first baseline in Drive.
- Residual: if GFL deletes or edits readings and then the feed disappears before the
  next monthly run, the change is not detected. The newest snapshot is still the record.
- Residual: only the readings layer is compared. The wind, station and label layers are
  saved in every snapshot but not diffed.
