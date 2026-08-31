# Arbor Hills Two-Cluster Thermal Map

A static, interactive map of the elevated-temperature wells at Arbor Hills Landfill
(Michigan EGLE facility / SRN N2688, Salem Twp / Washtenaw County). It shows the two
persistent clusters of hot wells (an eastern hot core and a western warm field) on
satellite or street imagery, with year, temperature-threshold, and layer controls.

All figures are the operator's own EGLE-filed wellhead readings, mechanically extracted;
the map makes no interpretive claims the data does not support.

## Data + method

- **Well temperatures:** the public wellfield dataset (WOI Status Reports + Gas-Extraction
  Exceedance filings), deduped, peak temperature per well per year.
- **Well coordinates:** the operator's tuning-location table plus the Well Master List.
- **Clustering:** single-linkage at 400 ft; identical to the SET-report C1 spatial analysis.
  "Run B" placement (actual coordinates plus rebore approximation for retired redrill wells).
- **Parcels:** Washtenaw County GIS, reprojected to WGS84 (GFL-owned 305 ac, Salem
  Township-owned 164 ac, and the Section-12 GFL-owned expansion parcel, 250 ac of land).
- **Schools:** Salem Elementary and Ridge Wood Elementary (the Ridge Wood outline is
  approximate). The "School Air Monitor" point is read from aerial imagery.

The generator scripts (`build_spatial_data.py`, `build_parcels.py`) live with the working
copy in the Lotext corpus; this folder holds the self-contained runtime.

## Controls

Year slider (Overall + 2021-2026), 131/145 F threshold, satellite/street basemap, toggles
for other located wells and the expansion parcel, and a "Copy view link" button that
encodes the current view (center, zoom, and every control) in the URL for sharing.

Built with Leaflet (vendored locally). Self-contained except for the basemap tiles.
