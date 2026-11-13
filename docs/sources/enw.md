# SP Electricity North West (ENW) - Opendatasoft portal recon

Observed 2026-11-12, anonymous curl. Base: `https://electricitynorthwest.opendatasoft.com/api/explore/v2.1/catalog/datasets`
Catalog: 148 datasets (ids mostly `enwl-*`, `sp-enw-*`, `dfes-*`, `ndp-*`; titles prefixed "SP ENW - ").

## Access (read this first)
- **Only 7 of 148 datasets return rows anonymously.** The other 141 return HTTP 403 ForbiddenAccess on `/records`; `/exports/csv` returns the header row only. Dataset metadata has `visibility: domain`, `data_visible: false`. Description text states: "Actual dataset content is available to registered users only - log-in or create new account" (also accepts other ODS accounts). So a free registration/API key is needed for almost everything; not tested (no credentials used).
- Anonymous-readable (7): `sp-enw-lct-low-carbon-technology-readiness-checker` (2,100,232 rows, mod 2026-11-11), `sp-enw-flexibility-monthly-tender-site-requirements` (322, mod 2026-10-16), `dataset_roadmap0` (118), `enwl-shared-data-licence` (1; a PDF file record), and 3 empty shells (`network-asset-viewer`, `sp-enw-network-data-small-scale-solar-project-planner`, `sp-enw-network-data-grid-charge-planner`, 0 records).
- Rate limit header seen: `x-ratelimit-limit: 5000`.
- Licences: CC BY 4.0 (98 datasets), "SP ENW Shared Licence" (41: includes NEO live outage data and GIS conductor sets), OGL v3.0 (8, external-data boundaries), none (1). Titles carry "Dataset Classification - Open/Shared".
- NO LTDS tables, fault-level datasets, curtailment or connection-queue datasets were found in the ENW catalog (searched ids/titles for fault, ltds, long term, queue, curtail, transformer: no matches). Fault levels for ENW are therefore **not available from this portal** as observed.

## CRS / geometry (from schema + the 2 samples that could be read)
- ODS `geo_point_2d` / `geopoint` = WGS84 `{lon,lat}`; `geo_shape` = GeoJSON in WGS84.
- Easting/northing columns exist, typically as **text** (`x`,`y`, `centroid_x/y`, `location_x_coordinate_eastings_where_data_is_held`), BNG presumably (verified only on the monthly tender: easting 381768, northing 399237 for Frederick Rd BSP, lat/long text "53.489524","-2.276251"). Note the text lat/long differ from `geo_point_2d` by ~100 m (53.4895/-2.2763 vs 53.4885/-2.2777) - use easting/northing or geo_point consistently.
- Gated datasets: CRS of x/y not verified (no rows).

## Relevant datasets (fields from catalog metadata; rows gated unless marked VIS)
Capacity heatmaps (Voronoi polygons, "interlocking"; keys are numbers):
- `enwl-gsp-heatmap` (20, mod 2026-10-30): gsp_number, voltage_kv, connected_mw, accepted_mw, materiality_headroom_mw, tia_threshold, awaiting_tia_mw, transmission_fl_headroom_ka, thematic_* bands.
- `enwl-bsp-heatmap` (70, 2026-10-29): bsp_number, gsp_number, demand_headroom_firm_mw / non_firm_mw, generation_headroom_non_firm_inverter_based_mw / synchronous_mw, battery_storage_headroom_non_firm_mw.
- `enwl-pry-heatmap` (377, 2026-10-29): pry_number, bsp_number, gsp_number, individual_circuit_capacity (A, MVA), same headroom set (HV and LV synchronous separate). Headroom here is non-firm/firm split and inverter/synchronous/battery specific - different basis from NPG/SPEN; keep `headroom_basis`.
- NDP: `ndp-pry-bsp-headroom` (38,892; group, group_number, year, scenario, firm/non_firm_headroom_mva, centroid_x/y), `ndp-pry-bsp-generation` (38,269), `ndp-pry-voronoi` (371), `ndp-bsp-voronoi` (70).
- Lower voltage: `sp-enw-capacity-distribution-tx` (50,145; per distribution transformer: distribution_number, rating_kva, load_kva, generation_kva, utilisation, demand/generation headroom kva, primary_number, polygon), `sp-enw-capacity-11kv-network` (111,015 line sections), `sp-enw-capacity-6-6kv-network` (82,765).
Site lists / hierarchy:
- `enwl-substation` "GIS - Substations" (41,158, mod 2026-08-07): group (GSP/BSP/Primary/Distribution/Network), **number** (6-digit ID), infeed/outfeed voltage, situation, state, dso_pry_number_alias, dso_bsp_number_alias, dso_gsp_number_alias, x, y (double), geopoint. `sp-enw-substation-hierarchy` (41,868; same, x/y text). `dfes-2024-primary-sites` (377), `dfes-2024-bsp-sites` (70) with `group_number`.
- Polygons: `enwl-substation-gsp-polygons` 17, `-bsp-polygons` 70, `-primary-polygons` 372, `enwl-substation-dso-*-polygons` (370 primary / 70 BSP / 17 GSP, updated 2026-11-02..05), `sp-enw-substation-dso-primary-feeder-polygons` 2,732, `dfes-bsp-polygons` 65, `dfes-primary-polygons` 368.
ECR (monthly, mod 2026-10-20): `enwl-embedded-capacity-register-2-1mw-and-above` (568), `-1-under-1mw` (2,301; 50kW-1MW), `-3-dsr` (1), `ecr-supplementary-data` (3, file links). Schema same family as NPG ECR (grid_supply_point/bulk_supply_point/primary text, eastings/northings, geopoint, capacities); 1MW+ has MW fields as text in places.
LV / secondary utilisation and smart meter (gated):
- `dfes-lv-headroom-monitored` (2,444) / `-unmonitored` (27,658) / `dfes-lv-peak-demand-monitored` (2,444) / `-unmonitored` (27,845): key `secondary_substation` (6-digit), x/y text, headroom/peak kVA by year 2024-2051 across scenarios (best_view, counterfactual, hydrogen_evolution, ...). Monitored = PRESense units. This is the secondary-substation utilisation source.
- `lv_load_duration` (6,385): sub_number, feeder, percentile_10..90, rating kVA, timeseries_url.
- `smart-meter-installation` (32,990): dist_number, pry_number, total_customers, smart_meter_customers, smart_meter %.
- `mcs-lct-per-enwl-substation` (14,245): heat pump / PV / battery MCS counts per substation_number. `enwl-lsoa-polygons-looped-service-count`, `lsoa-domestic-heating-demand` (62,140), `enwl-area-dvla-ev-registration-data-per-lsoa` (3,156).
- **VIS** `sp-enw-lct-low-carbon-technology-readiness-checker` (2.1M): per-property (uprn, postcode, house_number) with `lv_substation` ("238047"), `hv_feeder` ("200106409"), geopoint, LCT-readiness flags. Note: contains addresses/UPRNs (personal-data-adjacent); would give property-to-LV-substation lookup but is huge.
Flex: **VIS** `sp-enw-flexibility-monthly-tender-site-requirements` (322): site_number "100504", substation_name "Frederick Rd BSP", maximum_requirement_mva, need_type, delivery_start_date, easting/northing, geo_point_2d + geo_shape. Gated: `enwl-flexibility-tender-site-requirements` (558), `enwl-historical-flexibility-tender-site-requirements` (1,236), `flexibility-half-hourly-data` (35,088; one column per site group), `slc31e-procurement` (633), `slc31e-procurement-locational` (56), `slc31e-dispatch` (973), `primacy-risk-of-conflict-report` (45).
DFES (gated): `dfes-2023-primary-data0` (59,778), `dfes-2023-bulk-supply-point-data0` (11,088), `dfes-2023-local-authority-data0` (6,480), `dfes-2023-county-data0` (1,620), `dfes-2023-entire-network-data` (162), `dfes-2023-lake-district-data`. Dataset ids say "2023" but descriptions say "New 2026 Release" (mod 2026-05-14). Keys: `dso_pry_number_alias`, `dso_bsp_number_alias`, `group`; columns: min/max demand MVA, domestic/non-domestic MWh, battery MW, ... (heat pump/EV columns truncated in my view; fields exist further right - not inspected).
Operational / live faults (gated): `live_incidents` (11; 5+ customers off, updated every 15 min), `sp-enw-live-neop` (48, NEO format, shared licence, 15 min), `psi` (215 future planned interruptions polygons, daily), `unplanned-outages` (313,919 historic, daily, primary_substation field, PDF guide attached), `sp-enw-live-customers-off-supply` (14,257), `sp-enw-live-system-demand` (15,639; 15-min total demand), `enwl-boundary-flow-*` (merge 49,654 wide, GSP XY 10, BSP XY 64).
Other: `sp-enw-capital-projects-investment-map` (94), `sp-enw-gis-conductors-<local authority>` (~50 datasets; hundreds of thousands of line features, shared licence), `enwl-11kv/33kv/132kv/6-6kv/lv overhead conductors`, `sp-enw-gis-idno-polygons`, `enwl-control-areas`, biodiversity and external-data (SSSI, AONB, LSOA) sets, portal analytics.

## Attachment-only / non-tabular
- `enwl-shared-data-licence`: 1 record = licence PDF. `ecr-supplementary-data`: 3 file records. `unplanned-outages`: 1 PDF guide attached to tabular data. Alternative-export section holds the original multi-tab ECR spreadsheet (per description). No dataset in ENW was found that is only a zip/PDF apart from these.

## Join keys (observed schema)
- 6-digit substation `number` / `sub_number` / `dist_number` / `secondary_substation` / `lv_substation`; hierarchy aliases `dso_pry_number_alias` -> `dso_bsp_number_alias` -> `dso_gsp_number_alias`; `group_number`. These look like a consistent ID scheme across GIS, DFES, LV, smart meter, MCS (descriptions say substation references are "aligned to other datasets"). Heatmaps use `pry_number`/`bsp_number`/`gsp_number`. ECR joins to network by names only.

## Surprises
- 95% of datasets are login-gated, including every heatmap, ECR and LV dataset; the portal looks open in the catalog.
- No LTDS/fault-level tables on this portal.
- DFES dataset ids still say 2023 but content is 2026 release.
- Text x/y and text lat/long inconsistencies.
