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
each month, plus manual dispatch):

1. Pull every row of every layer and table of the FeatureServer with all fields
   (OBJECTID keyset paging), plus service and layer metadata and the public dashboard's
   config. Any layer whose row count differs from the server's own `returnCountOnly`
   count fails the run (exit 1) and nothing is uploaded.
2. Write one zip, `gfl-feed-snapshot-<UTC stamp>.zip`: a CSV per layer, the metadata,
   the dashboard config, and `manifest.json` (source URL, fetch time, row counts against
   server counts, SHA-256 of every file). Upload it to the app-only GFL Air Exhibit Drive
   folder (`GOAUTH_GFL_AIR_FOLDER_ID`, the same folder and OAuth identity as the daily
   capture). Each snapshot is a new, immutable file.
3. Download the previous snapshot from that folder and compare readings by OBJECTID on
   the measurement fields (station, time, H2S, CH4, both labels, wind, temperature,
   humidity, pressure). Readings that existed before but are gone now (deleted), or
   whose measurement values changed (edited), are emailed to the owner list only, never
   the public recipient lists. Bookkeeping fields such as `last_edited_date` are not
   compared. New readings are expected and are only counted.

The first run with no earlier snapshot in the folder is a silent baseline.

## Failure handling

Every failure exits 1 so the GitHub failure email surfaces it: an incomplete pull, a
missing Drive folder or credentials, an unreadable previous snapshot, changes found but
an empty owner list, or a change email that fails to send. A partial pull is never
uploaded.

## Real-specimen check

A live build on 2026-10-06 pulled every layer in 331 seconds: readings 225,699 of
225,699, wind 36,314 of 36,314, stations 7 of 7, labels 8 of 8. The zip was 12.7 MB. Its
readings, read back from the zip, compared clean against the same pull, and against
the separate manual snapshot taken about an hour earlier (0 deleted, 0 edited). Nothing
was uploaded from the local check.

## Consequences

- About 13 MB a month in the Drive folder (about 150 MB a year).
- Ships `enabled: false` (a new job against a live external system). To switch it on,
  set `gfl_feed_snapshot.enabled: true`. Run the workflow once by hand to create the
  first baseline in Drive.
- Residual: if GFL deletes or edits readings and then the feed disappears before the
  next monthly run, the change is not detected. The newest snapshot is still the record.
