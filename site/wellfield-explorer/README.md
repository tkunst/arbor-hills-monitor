# Arbor Hills Wellfield Explorer

An interactive explorer for the Arbor Hills Landfill wellfield (Michigan EGLE facility /
SRN N2688, Salem Twp / Washtenaw County). Plot any well's EGLE-filed wellhead readings over
time (temperature, oxygen, and the methane-to-carbon-dioxide ratio) against the subsurface
elevated temperature (SET) thresholds, one well at a time or a whole thermal cluster at once.

All figures are the operator's own EGLE-filed wellhead readings, mechanically extracted; the
tool makes no interpretive claims the data does not support.

## Data + method

- **Readings:** the public wellfield dataset (WOI Status Reports + Gas-Extraction Exceedance
  filings), as-found readings only (ADJ duplicates excluded). Only gas extraction wellheads
  are shown: gas probes (`point_type` = "probe"), the flare
  points, the gas plant reading, the vacuum point, the RNG plant inlet (`point_type` =
  "rng_inlet"), valve points and test points (`point_type` = "test_point") are left out (the
  same rule as the dataset's wellhead counts, `wellhead_points.py`). Built from the
  `wellfield-data-2026-11-22` release (wellhead readings 2021-03-01 to 2026-09-30).
- **Well locations (lineages):** successive redrill wells at one location are merged into a
  single continuous series when their reading dates do not overlap; overlapping candidates are
  kept separate.
- **Clusters:** Eastern Hot Core / Western Warm Field, from single-linkage clustering at 400 ft
  of the wells that reached 131F or higher (identical to the thermal map). The two-cluster split
  is robust: re-run at 100/200/400 ft, across two coordinate sources, and per-year 2021-2026.
- **Well types:** EGLE Well Master List (Unknown where not covered).

## Controls

Search (matches merged predecessor IDs); pick up to 8 individual wells (each its own color);
On/Off slide toggles for the 2026 HOV-approved, Wells Over 145F, Eastern Hot Core, Western Warm Field, and
Flagged groups (plot a whole group in one color); a "Copy view link" permalink; light/dark
theme; and a table-view accessibility fallback. Reference bands mark the SET thresholds on each
chart.

Built with Plotly (vendored locally). Self-contained: no external scripts or CDNs. The
generator script (`build_data.py`) lives with the working copy in the Lotext corpus; this folder
holds the self-contained runtime.
