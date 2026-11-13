# Area extras: supply areas, flexibility, constraints (recon 2026-11-12)

Modules: `extra/area_primary.py` (primary_areas), `area_flex.py` (flex_zones), `area_constraints.py` (curtailment, anm_zones).

## primary_areas (pmtiles, z5-13, ~21 MB)
Polygon sources: ukpn_primary_areas / ukpn_grid_areas, enw_primary_polygons / enw_bsp_polygons,
ssen_primary_supply_areas / ssen_bsp_gsp_supply_areas, spen_polygons_spd / spm, npg_service_areas (Primary + BSP classes).
Join: UKPN by FLOC (asset native_ids), ENW by pry/bsp number -> asset_site, others by name via match.NameIndex with polygon centre as tie-break.
Colour: demand headroom (MW, else MVA; `u` carries the unit), viridis-like steps; grey = not published. NGED: only a PDF (dsa041), no polygons.
Skipped: UKPN/SSEN secondary-area polygons (74k+, large).

## flex_zones (pmtiles)
UKPN LV/HV zones, ENW monthly/biannual tender sites, SPEN flex/Piclo/competition polygons, NPG upcoming needs.
NGED / SSEN publish flexibility requirements without geometry: not drawn.

## curtailment (geojson) and anm_zones (pmtiles)
UKPN curtailment events + constraint breaches (per GSP scheme, no coordinates: placed at the GSP site/area centre), NPG site-specific events (geo_point_2d is "lat, lon").
UKPN energy-lost is an estimate (kWh); NPG is outage-related MWh: not comparable. SSEN publishes no ANM boundary: Orkney / Isle of Wight areas are approximated by the primary supply areas they cover.
SPEN non-firm insight is fetched but not drawn (no geography).
