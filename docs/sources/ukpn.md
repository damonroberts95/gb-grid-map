# UK Power Networks (UKPN) open data portal - reconnaissance

Portal: https://ukpowernetworks.opendatasoft.com (Opendatasoft Explore API v2.1). Probed 2026-11-12 with curl, anonymous.
Catalog size: **136 datasets** (2 pages of 100). Licence on every dataset checked: CC BY 4.0 (publisher "UK Power Networks, Company number 3870728"); exception: `ukpn-ltds-cim` uses "UK Power Networks Shared Data Licence - Common Information Model".
Sample values below are only given where a real response returned them; otherwise "(schema only)" = field name/type from catalog metadata, no row observed.

## 1. CRITICAL: anonymous access is limited (observed)

- Catalog endpoints (`/catalog/datasets`, `/catalog/datasets/{id}`) work anonymously.
- Each dataset has `data_visible` in the catalog. Of 136 datasets, **only 39 are `data_visible: true`**; **97 are `false`**, including nearly all datasets we need (heatmap, grid/primary sites, secondary sites, LTDS tables, ECR 1 & 2, smart meter, DFES NSHR, LCT, transformers, power flow).
- For `data_visible: false` datasets, anonymously:
  - `/records?limit=1` -> **HTTP 403** `{"error_code":"ForbiddenAccess"}`.
  - `/exports/csv`, `/exports/geojson`, `/exports/json` -> **HTTP 200 but empty** (CSV = header row only; geojson `{"features":[]}`; json `[]`). Silent: a naive fetcher would "succeed" with 0 rows. Always compare against catalog `records_count`.
  - Adding a bogus `apikey=x` -> **HTTP 401** `{"error": "API key is not valid"}` (so a key is accepted as a query param; I did not have a valid one, so I could NOT verify that a key unlocks the restricted data).
- **Conclusion: a free portal account + API key is required (not just advisable) to read the main datasets. Not tested: whether a registered key unlocks `data_visible:false` datasets.** Check this first with a real key.
- Anonymous workaround verified for the headline dataset: the heatmap has **file attachments** (JSON, one per licence area) downloadable anonymously (HTTP 200): `/catalog/datasets/ukpn-capacity-heatmap/attachments/ltds_heatmap_{epn|lpn|spn}_2026_01_json_2026_05_29_v10json` (615 KB for EPN). Attachment IDs change per release; resolve from the catalog `attachments` list each run.
- Many large datasets also list `alternative_exports` pointing at SharePoint (power flow data); not tested.
- Old v1 API (`/api/records/1.0/search`) -> error "dataset has no record"; `/explore/dataset/{id}/download` -> 404. Use v2.1 only.
- Rate limit (response headers, anonymous): `x-ratelimit-limit: 10000`, resets daily 00:00 UTC (`x-ratelimit-reset`); ~80 calls used while probing. Per-dataset headers `X-RateLimit-dataset-*` are exposed but were not populated in responses I saw. Python `urllib` default UA got 403 on records; curl fine (that was actually the data_visible 403, not UA).
- `/records` is capped at offset+limit 10,000 (per AI_GOTCHAS; not tested). Use exports for full data.

## 2. Coordinates / CRS (observed)

- All `geo_point_2d` / `geo_shape` / `geopoint` / `spatial_coordinates` fields are Opendatasoft geo types = **WGS84 lon/lat** (EPSG:4326). Observed samples: live faults `{"lon": 1.16212, "lat": 51.31655}`; heatmap attachment `longitude 0.8857, latitude 51.8446`.
- Heatmap CSV also has `geometry_wkt_epsg4326` (label says 4326) plus `latitude`/`longitude` doubles.
- ECR 1 and 2 additionally carry `location_x_coordinate_eastings_where_data_is_held` / `..._northings_...` as **text** (BNG, presumably EPSG:27700; no values observed because restricted) and separate `latitude`/`longitude` doubles. Validate ranges per AI_GOTCHAS section 3.
- `ukpn-secondary-sites.latitude` is typed **text** while `longitude` is double (schema quirk). `gridref` is text (OS grid ref string).
- No BNG eastings/northings found in any other dataset schema.

## 3. Most important datasets (for the capacity map)

Counts = catalog `records_count` (cross-check with export row count after fetch). "Freq" = portal `update_frequency` code. Modified = portal `modified` (not data as-of date).

| ID | Title | Count | Modified | Freq | Access (anon) |
|---|---|---|---|---|---|
| ukpn-capacity-heatmap | LTDS Capacity Heatmap | 967 | 2026-10-19 | ANNUAL_2 | records 403/exports empty; **attachments JSON OK** |
| grid-and-primary-sites | Grid and Primary Sites | 1504 | 2026-09-10 | (see portal) | restricted |
| ukpn-secondary-sites | Secondary Sites | 72750 | 2026-06-15 | ANNUAL | restricted |
| ukpn-secondary-site-utilisation | Secondary Site Utilisation | 116883 | 2025-11-27 | ANNUAL | restricted |
| ukpn-secondary-site-transformers | Secondary Site Transformers | 74719 | 2025-11-27 | ANNUAL | restricted |
| ukpn_secondary_postcode_area | Secondary Substation Distribution Areas (polygons) | 74425 | 2024-06-12 data (portal mod 2026-10-22) | ANNUAL_2 | restricted |
| ukpn_primary_postcode_area | Primary Substation Distribution Areas (polygons + RAG + gen headroom) | 785 | 2026-09-10 | ANNUAL_2 | restricted |
| ukpn-grid-postcode-area | Grid Substation Distribution Areas | 177 | 2025-03-31 | ANNUAL_2 | restricted |
| ukpn-grid-transformers / ukpn-primary-transformers | Grid / Primary Site Transformers | 592 / 1675 | 2026-06-03 | ANNUAL | restricted |
| ukpn-ltds-table-1-circuit-data | LTDS T1 circuits | 5076 | 2026-06-29 | ANNUAL_2 | restricted |
| ukpn-ltds-table-2a-transformer-2w | LTDS T2a transformers 2W | 2171 | 2026-06-29 | ANNUAL_2 | restricted |
| ukpn-ltds-table-2b-transformer-data-3w | LTDS T2b transformers 3W | 143 | 2026-06-29 | ANNUAL_2 | restricted |
| ukpn-ltds-table-3a-load-data-observed | LTDS T3a observed peak demand | 1960 | 2026-06-24 | ANNUAL_2 | restricted |
| ltds-table-3a-load-data-observed-transposed | T3a transposed (year/load rows) | 9800 | 2026-06-01 | none | restricted |
| ukpn-ltds-table-3b-load-data-true | LTDS T3b true peak demand | 1960 | 2026-06-29 | ANNUAL_2 | restricted |
| ukpn-ltds-table-4a-3ph-fault-level | LTDS T4a fault level 3-phase | 2040 | 2026-09-23 | ANNUAL_2 | restricted |
| ukpn-ltds-table-4b-earth-fault-level | LTDS T4b fault level earth | 2040 | 2026-10-05 | ANNUAL_2 | restricted |
| ukpn-ltds-table-5-generation | LTDS T5 generation | 736 | 2026-06-29 | ANNUAL_2 | restricted |
| ukpn-ltds-table-6-interest-connections | LTDS T6 new connection interest | 933 | 2026-09-21 | ANNUAL_2 | restricted |
| ukpn-ltds-table-7-operational-restrictions / -8-gt-95-perc-fault-data | LTDS T7 / T8 | 94 / 44 | 2026-06-29 | ANNUAL_2 | restricted |
| ukpn-ltds-infrastructure-projects | LTDS infrastructure projects | 41 | 2026-10-13 | ANNUAL_2 | restricted |
| ukpn-grid-and-primary-sites-fault-level-data | Earthing fault level data (grid+primary) | 3482 | 2026-10-30 | WEEKLY | restricted |
| ukpn-embedded-capacity-register | ECR Part 2 (>=1 MW) | 1036 | 2026-10-14 | n/a | restricted |
| ukpn-embedded-capacity-register-1-under-1mw | ECR Part 1 (<1 MW) | 4519 | 2026-10-14 | n/a | restricted |
| ukpn-smart-meter-consumption-lv-feeder | Smart meter consumption LV feeder | 30000 | 2026-11-09 | MONTHLY | restricted |
| ukpn-smart-meter-consumption-substation | Smart meter consumption substation | 30000 | 2026-11-09 | MONTHLY | restricted |
| ukpn-low-carbon-technologies-secondary | LCT at secondary sites | 35018 | 2026-11-11 | WEEKLY | restricted |
| low-carbon-technologies | LCT connected (primary level) | 3619 | 2026-11-11 | WEEKLY | restricted |
| ukpn-low-carbon-technologies-lsoa | LCT by LSOA | 37911 | 2026-11-06 | WEEKLY | restricted |
| ukpn-dfes-network-headroom-report | DFES Network Scenario Headroom Report | 177120 | 2026-10-19 | ANNUAL_2 | restricted |
| ukpn-dfes-by-local-authorities | DFES by local authority | 15876 | 2026-06-24 | ANNUAL | restricted |
| ukpn-live-faults | Live Faults | 92 | 2026-11-12 | UPDATE_CONT | **OK anon** |
| ukpn-national-energy-outage | National Energy Outage API | 127 | 2026-11-12 | UPDATE_CONT | **OK anon** |
| ukpn-ehv-network-outages | EHV Network Outages | 20673 | 2026-11-12 | WEEKLY | restricted |
| ukpn-overall-queue-insights | Overall queue insights (aggregate MW by tech/status) | 20 | 2026-08-18 | MONTHLY | OK anon |
| ukpn-wide-planning-status | Pipeline allocation (aggregate) | 56 | 2026-08-18 | MONTHLY | OK anon |
| ukpn-connections-reform-local-authority | Connections reform by LA | 133 | 2026-09-21 | MONTHLY | OK anon |
| ukpn-lv-flex-zones | LV flexibility zones (polygons) | 1793 | 2026-07-22 | ANNUAL_2 | OK anon |
| ukpn-hv-flex-zones | HV flexibility zones | 411 | 2026-07-09 | ANNUAL_2 | restricted |
| ukpn-flexibility-tender-data | Flex tender by postcode | 514187 | 2026-07-22 | QUARTERLY | restricted |
| ukpn-flexibility-dispatches | Flex dispatches | 50888 | 2026-11-12 | DAILY | restricted |
| ukpn-constraints-real-time-meter-readings | Constraint meter readings | 986 | 2026-11-12 | HOURLY | restricted |
| ukpn-constraint-breaches-history | Constraint breaches | 41216 | 2026-11-04 | DAILY | restricted |
| ukpn-curtailment-events-site-specific | Curtailment events by DER | 44618 | 2026-11-04 | UPDATE_CONT | restricted |
| ukpn-grid-supply-points-overview / ukpn-gsp-insights / ukpn-grid-supply-points (areas) | GSP overview / insights / area polygons | 60 / 4042 / 58 | 2026-09-10 / 2026-09-10 / 2025-07-16 | MONTHLY/MONTHLY/ANNUAL | restricted |
| ukpn-super-grid-transformer (+ -header) | SGT power flow daily / latest | 12,292,034 / 194 | 2026-11-12 | DAILY / UPDATE_CONT | restricted |
| ukpn-grid-transformer-operational-data-half-hourly (+ -monthly) | Grid transformer power flow | 14,172,522 / 13500 | 2026-10-17 | MONTHLY | restricted |
| ukpn-primary-transformer-power-flow-historic-half-hourly-spn / -monthly / -epn | Primary transformer power flow (SPN 31.6M rows; monthly 66465; EPN half-hourly count 0, see alternative_exports) | | 2026-10-17/19 | MONTHLY | restricted |
| ukpn-33kv-circuit-operational-data-monthly / ukpn-132kv-circuit-operational-data-monthly | Circuit power flow monthly | 60076 / 20745 | 2026-10 | | restricted |
| ukpn-appendix-g, -summary, -aggregated, -project-progression, -site-specific-conditions | Appendix G (large generation pipeline) | 1106/448/56/52/32 | 2026-08 | MONTHLY | restricted |
| ukpn-large-embedded-power-stations / ukpn-large-demand-list / ukpn-gsp-project-status / ukpn-modification-application | Queue-related | 31/496/1008/70 | 2025-11..2026-11 | | restricted |
| ukpn-dnoa / ukpn-low-voltage-dnoa | DNOA (EHV / LV) | 19 / 315 | 2026-06-29 | ANNUAL | restricted |
| ukpn-ltds-cim | LTDS CIM files | 0 records | 2026-10-19 | none | visible but no records (files only) |

Other catalog items (cables, poles, overhead lines, EPR/soil, IIS, IRENES, boundaries, streetworks, DUoS, profiles, glossary, etc.) are not core; full list in portal catalog `domain-dataset0` ("Open Data Portal Catalogue Metadata", 138 rows, anon OK).
Historic note: LTDS tables carry "24_25" / forecast columns, so tables are dated by LTDS cycle.

## 4. Key fields per core dataset (schema from catalog; example values only where observed)

### ukpn-capacity-heatmap (also in attachment JSON; observed example from EPN attachment)
Attachment JSON structure: top-level `date`, `issued` ("2026-05-29"), `rights` (CC BY 4.0 URL), `valid` [2025-03-01, 2026-11-30], `Substations` (list), `Circuits` (empty list). Counts: **EPN 547, LPN 128, SPN 292 = 967**, matching catalog count. Every substation has `type: "Primary"` (even names like "Abberton Grid 33kV"), so don't use `type` to separate grid from primary; use `voltages` and name.
Example EPN row: `mRID "f3f97f72-f5c3-5af5-82d0-f79b7b5b5ada"`, `name "Abberton Grid 33kV"`, `area "EPN"`, `longitude 0.8857`, `latitude 51.8446`, `voltages [33]`, `GSP "Rayleigh"`, `BSP "Abberton Grid 33"`, `demandFirmCapacity 114.3`, `demandAvailableCapacity 42.6`, `demandMaximum 65.9`, `demandMinimum 21.9`, `demandConstraint "GREEN"`, `demandConstraintLimitingFactor "Thermal"`, `generationFirmCapacity 158.2`, `generationAvailableCapacity 77.5`, `generationConstraint "GREEN"`, `reversePowerFlowTotalCapacity 114.3`, `reversePowerFlowAvailableCapacity 114.3`, `PastConnectionActivity [{voltage 33, loadBudgetEstimatesProvidedCapacity 25, ...Count 2}]`, `description` = free text incl. TIA threshold.
Dataset (CSV/portal) columns flatten the same: name, mrid, description, type, area, bsp, gsp, latitude, longitude, voltages, demand*/generation*/load* offer & budget-estimate capacity+count fields, voltage, reversepowerflow*, geometry_wkt_epsg4326, geo_point_2d. Units not stated in the sample (assume MW for headroom; **verify from the commentary PDF attachment `heatmap_commentary_documentpdf`**). Headroom basis (firm vs intact, queue-inclusion) must be taken from that PDF, not assumed. Update freq ANNUAL_2 (twice yearly).

### grid-and-primary-sites (schema only)
sitefunctionallocation (FLOC), licencearea, sitename, sitetype, sitevoltage (int), gridref (text), postcode, datecommissioned, powertransformercount, maxdemandsummer/winter (double), transratingsummer/winter (text), reversepower, county, siteclassification, spatial_coordinates (geo_point_2d), local_authority(+code), what3words.

### ukpn-secondary-sites (schema only)
functionallocation, substationalias, dno, companyarea, substationdesign, substationvoltage, numberoftransformers, utilisation_band, demand_headroom (text), onanrating, primaryfeeder, primaryfeederfunctionallocation, customer_count, predicted_year_of_reinforcement, postcode, latitude(text)/longitude(double), gridref, geopoint, indooroutdoor, localauthority, llsoa code/name, buildinguprn, what3words.
Secondary utilisation: `ukpn-secondary-site-utilisation` = functionallocation, utilisation_band, predicted_year_of_reinforcement, source (same band also embedded in secondary-sites and the polygon dataset). Utilisation is a band (text), not a number. No fault levels / generation headroom for LV sites.

### LTDS 4a / 4b fault levels (schema only; identical column sets)
gridsupplypoint, substation, nodename, voltage_level_kv (text), system_impedance_r_percent, system_impedance_x_percent, existing_system_fault_currents_peak_make_ka, existing_system_fault_currents_rms_break_ka, fault_rating_peak_make_ka, fault_rating_peak_break_ka, sitefunctionallocation, licencearea, id. 4a = 3-phase, 4b = earth (1-phase); 2040 rows each. Note schema column is named `fault_rating_peak_break_ka` (the "break rating" is labelled peak - flag for check vs LTDS spec). Both have 2040 rows, 4b modified later (2026-10-05 vs 2026-09-23) - versions may differ.
Also `ukpn-grid-and-primary-sites-fault-level-data`: sitefunctionallocation, substationname, voltage, node, threephasermsbreak, threephasepeakmake, earthfaultrmsbreak, earthfaultpeakmake (3482 rows, WEEKLY; earthing-study values, may differ from LTDS).

### LTDS other tables (schema only)
- T3a/T3b: gridsupplypoint, substation, season, maximum_demand_24_25_mw/pf, forecast_m_d_mw_25_26..29_30, firm_capacity_mw, unutilised_capacity_percent (3a), functional_location, licencearea, substation_mrid (3a only; **matches heatmap mRID format - candidate join, unverified**).
- T2a/T2b: hv_node/hv_substation/voltage_hv/lv_*, ratings `transformer_rating_mva_winter/summer`, impedances, sitefunctionallocation.
- T1: from/to substation/node, linename, rating_amps, fromsubstation_sitefunctionallocation.
- T5: installedcapacity_mva, fuel_type, connected_accepted, spatial_coordinates, substation_mrid. T6: demand/generation interest counts + capacity, spatial_coordinates.

### ECR 1 (<1 MW, 4519) and ECR 2 (>=1 MW, 1036) (schema only)
customer_name/site, address, postcode, eastings/northings (text), grid_supply_point, bulk_supply_point, primary, point_of_connection_poc_voltage_kv, licence_area, energy_source_1..3, energy_conversion_technology_1..3, registered capacities per tech (MW), storage MWh/hours, flexible_connection, connection_status, already_connected_registered_capacity_mw, accepted_to_connect_registered_capacity_mw, maximum_export/import_capacity_mw/mva, date_connected, date_accepted, target_energisation_date, in_a_connection_queue_y_n, distribution/transmission_service_provider_y_n, reference, last_updated, latitude, longitude, sitefunctionallocation, primary_resource_type_group. ECR2 also has spatialcoordinates_customer and local_authority(+county). Type differences between parts (e.g. POC voltage is double in ECR2, text in ECR1; ECR1 export-capacity field names have `already_connected_` prefix) - normalise. Status values not observed (restricted); filter on `connection_status` + `in_a_connection_queue_y_n` per AI_GOTCHAS.

### Smart meter (schema only, 30000 rows each - suspiciously round; probably a capped/rolling extract, verify)
LV feeder: dataset_id, dno, dno_alias, secondary_substation_id, lv_feeder_id (int), geopoint, aggregated_device_count_active/reactive, primary/secondary/total_consumption_active_import (int), total_consumption_reactive_import, data_collection_log_timestamp. Substation-level dataset: same minus lv_feeder_id. Attachment PDF `smart_meter_consumption_data_azure_blob_storage` suggests full data is on Azure blob storage (not checked).

### DFES NSHR (schema only)
category, substation_name, voltage_kv, sitefunctionallocation, bulksupplypoint, gridsupplypoint, scenario, year (date), headroom_mw, headroom_percent, demand_rag, physical_level, licencearea, spatial_coordinates. 177,120 rows = sites x scenarios x years.

### Live faults (anon OK, observed)
92 rows at probe. Fields: incidentreference ("INCD-105944-V"), powercuttype ("Planned"), incidenttypename, creationdatetime, receiveddate, planneddate, estimatedrestorationdate, restoreddatetime, nocustomeraffected (56), noplannedcustomers, nocallsreported, postcodesaffected ("CT3 1;CT3 4" sector list), fullpostcodedata (semicolon list of full postcodes), incidentcategory, incidentpriority, statusid, operatingzone ("East Kent Zone"), geopoint ({lon 1.16212, lat 51.31655}). Geopoint is aggregated from postcode (privacy), faults with <=5 customers omitted. Timestamps are text, no timezone.
National Energy Outage API (anon OK, 127 rows): fault_id ("INCD-646507-Z"), geometry_wkt (GeoJSON Feature Polygon), postcode (messy escaped string), licence_area ("East England"), voltage ("LV"), status ("In Progress"), planned ("FALSE"), confirmed_off (58.0), restored (12), etr, outage_start_date, date_of_restoration, as_at_date, geo_point_2d. Likely the cross-DNO-standard feed.

### Flexibility
LV flex zones (anon OK): transformer_id ("LPN-S000000073429"), tender_round ("TR14"), status ("Live"), geo_shape polygon, geo_point_2d. HV flex zones: flexibility_zone, tender_round, constraint_type, dno, polygon. Tender data by postcode: competition_pot, flexibility_zone, postcode, tender_round. Dispatches: zone, fu_id, product, availability/utilisation MW, prices, times (no coordinates).

### Curtailment / constraints
constraint_id (text) links constraints-real-time-meter-readings (present_amps_value, trim_amps, release_limit_amps, breach_flag, utilisation_percentage, der_name) and constraint-breaches-history (event_id, constraint_voltage_kv, duration_hours, total_der_access_reduction_kwh). curtailment-events-site-specific: der_name, event_id, scheme, durations, kwh lost, driver. No coordinates and no obvious substation ID in any of these - joining to a site needs der_name/constraint_description parsing or ECR (unverified).

## 5. Join keys (from schemas; cross-dataset equality NOT yet verified with real data because restricted)

- **`sitefunctionallocation` / `functionallocation` / `*_floc` (SAP functional location)** is the strongest candidate: present in grid-and-primary-sites, primary/grid transformers, secondary-sites (`functionallocation`, `primaryfeederfunctionallocation`), secondary utilisation, LTDS 1-6/7/8 (`sitefunctionallocation`; T3a also `functional_location`), 4a/4b, ECR 1 & 2, DFES NSHR, polygons (`grid_site_floc`, `primary_floc`, `grid_supply_point_floc`, `sitefunctionallocationclean`, `site_functional_location`), LCT, DNOA, modification-application, GSP overview (`floc`), EHV outages. Format not observed -> check normalisation (e.g. "sitefunctionallocationclean" implies raw values are dirty).
- **Heatmap `mRID`** (UUID) and LTDS `substation_mrid` (T3a, T5) - possible LTDS<->heatmap link. Heatmap has no FLOC, so heatmap <-> FLOC datasets needs the mRID bridge, or name + lat/lon (AI_GOTCHAS name-noise warning). Heatmap `name` e.g. "Abberton Grid 33kV" vs LTDS `substation` names - unverified match.
- GSP: `gsp` / `gridsupplypoint` / `grid_supply_point` (name text) across heatmap, LTDS, ECR, DFES, polygons; plus `grid_supply_point_floc`. BSP: `bsp`, `bulk_supply_point`, `bulksupplypoint`.
- Transformer power flow: `tx_id` + `ltds_name`, `hv_ltds_node`/`lv_ltds_node` (nodes link to LTDS 2a/2b `hv_node`/`lv_node`); SGT: `gsp`, `sgt`.
- Secondary: `functionallocation` (secondary-sites, utilisation, LCT secondary `functionallocation`), `secondarysitefunctionallocation` (transformers), `secondary_substation_id` (smart meter; format unseen, may or may not equal FLOC), `transformer_id` in LV flex zones (format "LPN-S000000073429" looks like licence-area prefix + FLOC-like number; unverified), `secondary_site_functional_location` (LV DNOA).
- Licence area codes: heatmap `area` = EPN/LPN/SPN; other datasets use `licencearea`/`dno`/`licence_area` ("East England" seen in outage API) - value vocab differs.

## 6. Surprises / caveats

1. 97/136 datasets restricted to authenticated users; exports silently return empty (HTTP 200). Need API key. Key behaviour unverified.
2. Heatmap JSON attachments (anonymous) are the only core capacity data I could actually read; no FLOC in them.
3. Heatmap `type` is "Primary" for all 967 rows; no separate BSP/grid rows at this level. Headroom units/basis need the commentary PDF.
4. `ukpn-secondary-sites.latitude` is text; ECR eastings/northings are text.
5. Portal `modified` != data as-of: `ukpn_secondary_postcode_area` data_processed 2024-06-12 vs modified 2026-10-22 (differing metadata fields); secondary-sites/utilisation are Nov 2025/Jun 2026 vintages.
6. Smart meter counts both exactly 30000. Primary transformer half-hourly EPN has 0 records (data via alternative export links); SPN has 31.6M; row counts above 10k need `/exports`.
7. LTDS tables are published ANNUAL_2 (twice yearly); heatmap "valid" window 2025-03-01 to 2026-11-30 - expires at end of this month, expect refresh.
8. Raw sampled artefacts were kept in the session scratchpad only; not stored in `data/raw` yet.
