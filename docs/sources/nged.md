# NGED (National Grid Electricity Distribution) data sources

Recon date: 2026-11-12. Everything below was observed live with curl unless marked "not inspected".

Portal: https://connecteddata.nationalgrid.co.uk  (CKAN, API v3, works anonymously for catalogue calls)
- `GET /api/3/action/package_list` -> 92 package ids. `package_show?id=<name>` works for all 92 anonymously.
- `package_search?q=headroom` works (note: search is weak, returned 5 hits incl. network-capacity).
- Licence field on packages is `rights` (not `license_id`): "WPD Open Data Licence" or "WPD Shared Data Licence". `license_id` is `wpd-open-data` or empty. Update frequency field: `updatefrequency` (e.g. "1 Month", "12 Months", "Near Real Time", "Not Applicable"). Package `metadata_modified` is the portal date, not data date.
- Licences: NGED/WPD Open Data Licence on open datasets; "WPD Shared Data Licence" on the registered-only ones (terms not read).

## CRITICAL: auth model ("restricted_level")

Each resource has `restricted_level`: `public`, `registered`, `only_allowed_users`, or null (null observed behaving as open).
- For `registered` resources, anonymous `package_show` returns `"url": "redacted"` and `"restricted": "redacted"`. There is NO download URL without an account.
- Anonymous `datastore_search` on a registered resource returns HTTP 403 `{"__type":"Authorization Error","message":"Access denied: Resource access restricted to registered users"}` (tested on the Network Capacity Map resource).
- `datastore_search` on an open resource works anonymously (tested connection-queue: 200, fields typed).
- I do not have an account/API key; registered resources were NOT sampled. Their column lists below are "not inspected". Free registration + CKAN API key is presumably required (not tested).
- Observed anonymous full-file downloads (HTTP 200/206, no key) from `/dataset/<pkgid>/resource/<resid>/download/<filename>`. Range requests: server returned 206 for some, and ignored the range (200, full file) for others.
- Resource URL contains package id + resource id + filename; filenames for connection-queue include a numeric id and year (`..._connection_queue_id_310_2026.csv`), so always resolve via package_show.

### Anonymous-visible vs registered (counts of resources, from package_show)
Key datasets that are REGISTERED-ONLY (data resources redacted anonymously; only the PDF "Data Sharing Assessment" is public):
- nged-network-capacity (Network Capacity Map CSV, 24 MB) 
- network-opportunity-map-headroom (20 MB CSV)
- generation-capacity-register (GCR.csv 1.1 MB, summaries)
- distribution-substations (47 MB CSV)
- distribution-substation-location-easting-northings (18 MB)
- primary-substation-location-easting-northings (178 KB)
- gsp-technical-limits, lct-connections, lct-enquiries, smart-meter-volumes, distribution-transformer-phasing
- aggregated-smart-meter-data-lv-feeder (4733 registered resources) and ...-secondary-substation (1584)
- *-transformer-flows (BSP/primary/super-grid historic), spatial-datasets (GPKG, 16 of 17 registered), historic-transformer-voltage-flows
- live-bsp-data--* (nearly all), live-primary-data--* (most; a minority of East Midlands files are public)
- lv-load-monitor-data (172 registered, 127 open, 2 public)
- rota-load-disconnection, flexibility-primacy (`only_allowed_users`)

Key datasets that are OPEN anonymously: ltds-capacity-heatmap, ltds-tabular-model, ltds-common-information-model, embedded-capacity-register (ECR), connection-queue, nged-connections-reform-register, live-power-cuts, live-data, live-gsp-data--*, substation-loading, substation-loading-details, dfes, system-voltage-optimisation, asset-limits, branch-loading, sensitivity-factors, generic-generator-profiles, dnoa, flexibility-charts.

## Datasets of interest

### 1. LTDS Capacity Heatmap  -- `ltds-capacity-heatmap`  (OPEN, best headroom source found)
- Licence WPD Open Data Licence; portal modified 2026-07-27; updatefrequency "12 Months".
- 4 resources, JSON, one per licence area: `.../download/east-midlands.json` (638 KB), west-midlands.json (283 KB), south-wales.json (269 KB), south-west.json (471 KB). Resource ids change on republish; resolve via package_show.
- Sampled East Midlands: JSON array of 451 objects (72 BSP + 379 Primary), flat (not GeoJSON).
- Fields (example): `mRID` 680022; `name` "Nottingham North 132Kv S Stn"; `type` "BSP"|"Primary"; `area` "East Midlands"; `voltages` "132-33"; `GSP` "Stoke Bardolph 132Kv"; `BSP`; `Primary` (blank = " "); `demandFirmCapacity` 90; `demandAvailableCapacity` 22.41; `demandMaximum`; `demandMinimum`; `demandConstraint` "green"/"red"/other; `demandConstraintLimitingFactor` "Thermal"; `generationFirmCapacity`; `generationAvailableCapacity`; `generationConstraint`; `generationConstraintLimitingFactor`; `reversePowerFlowTotalCapacity`; `reversePowerFlowAvailableCapacity`; `voltage` 33; `loadBudgetEstimatesProvided{Count,Capacity}`; `generationBudgetEstimatesProvided{Count,Capacity}`; `loadConnectionOffersAccepted{Count,Capacity}`; `generationConnectionOffersAccepted{Count,Capacity}`; `loadConnectionOffersMade{Count,Capacity}`; `generationConnectionOffersMade{Count,Capacity}`; `longitude` -1.173381; `latitude` 52.978954.
- Coordinates: WGS84 lat/lon (values plausible for Nottingham). Units not stated in file; capacity fields look like MW/MVA (not verified which). Gotcha: a `generationConstraint` value of the string "0" occurred for one Primary (null-ish); keep raw.
- Join key: `mRID` = "Substation Number" in `LTDS CIM Substation Numbers` (see #3). In East Midlands sample 392 of 451 mRIDs (48 BSP, 344 Primary) were found in that list; 59 not found (e.g. mRID 690059 "Warwick 11Kv S Stn"; the list has 690020 "Warwick 132/33/11kV" as BSP). Names differ in style ("132Kv S Stn" vs "132/33kV") so name matching needs normalisation.
- Headroom basis: demand and generation both given, firm capacity based (as named); contracted/queue effect not stated in the file. Check the LTDS methodology before assuming.

### 2. Network Capacity Map / Network Opportunity Map -- `nged-network-capacity`, `network-opportunity-map-headroom`  (REGISTERED, not sampled)
- nged-network-capacity: CSV "Network Capacity Map" 24,062,652 bytes, last_modified 2025-09-23, datastore_active true, restricted_level registered; portal modified 2026-01-30. Description says BSP and primary headroom for demand and generation incl. key asset ratings and location. Columns NOT inspected.
- network-opportunity-map-headroom: CSV 20,338,056 bytes, last_modified 2026-04-02, registered. Not inspected.
- Public links only: map page https://www.nationalgrid.co.uk/our-network/network-capacity-map/ and PDF Data Sharing Assessment.
- Anonymous datastore_search -> 403.

### 3. LTDS Tabular Model -- `ltds-tabular-model`  (OPEN, CSV, datastore)
- modified 2026-07-03; 10 resources; CSVs with datastore: Table 1 Circuit (2.7 MB), 2a Two-winding Transformer (308 KB), 2b Three-winding (11 KB), 3 Demand (331 KB, file name table-3-2025.csv), 4 Fault Level (2.0 MB), 5 Generation (85 KB), 6 Interest in Connections (111 KB), 7 Planned Work (12 KB), Attachment 8 Node (1.8 MB); plus a DSA PDF.
- Table 4 Fault Level columns (sampled): `Licence` (EMIDS), `GSP Group`, `Substation or Busbar Name` ("Banbury Road 33/11kV"), `Node A`, `Node B`, `Branch ID`, `Status`, `Operating Voltage` (kV), `R1`, `X1`, `3ph Peak Make` 13.42543, `1ph Peak Make`, `3ph RMS Break` 5.70159, `1ph RMS Break`, `3ph Peak Make Rating` 32.75, `1ph Peak Make Rating` ("-"), `3ph RMS Break Rating` 13.1, `1ph RMS Break Rating`. Units not in the header (presumably kA; not verified). "-" is used for null. All four fault columns (make/break x 3ph/1ph) are separate, plus ratings.
- Table 3 Demand: `Licence` (header has a stray tab: `"Licence\t"`), `GSP Group`, `BSP Group`, `Primary Substation`, `Node`, `Status`, `Operating Voltage`, `Current Year Max Demand` 2.99, `Current Year Power Factor`, `Forecast Year 1..5`, `Firm Capacity of Substation` 23, `Minimum demand Scaling Factor (%)` "50.77%".
- Table 2a: `Node 1`, `Operating Voltage 1`, `Node 2`, `Operating Voltage 2`, `Status`, `Vector Group`, `R1`,`X1`,`X0`, taps, `Nominal Rating` 12, `Emergency Rating` 19, `Reverse Power Capability`, `Method of Earthing`.
- Table 5 Generation: `Licence`,`GSP Group`,`Bulk Supply Point`,`Primary Substation`,`Connection Voltage`,`Agreed Export Capacity`,`Fuel Type`,`Connection Status`.
- Table 6: `Grid Supply Point`,`Bulk Supply Point`,`Primary Substation`,`Proposed Connection Voltage (kV)`,`Connection Status`,`Total Demand Number/Capacity (MW)`,`Total Generation Number/Capacity (MW)`.
- Attachment 8 Node: `Code`, `Name`, `BSP Group`, `GSP Group`, `Status`. Node codes look like `BANR5_BB_1_2` (first 4-5 chars = site code). Node code is the join between tables 1/2/3/4 and the CIM and the connection-queue `Bus Name`.
- No coordinates in the tabular tables.
- Names here are style "Berkswell 132kV" / "Banbury Road 33/11kV"; heatmap uses "132Kv S Stn"; ECR uses "Walpole 132Kv S Stn". Three name styles.

### 4. LTDS CIM -- `ltds-common-information-model`  (OPEN)
- 4 ZIPs by area (emids 11 MB, swales 5.3 MB, swest 8.3 MB, wmids 7.4 MB), PDF notes, and CSV `LTDS CIM Substation Numbers` (95 KB, 1923 rows: 975 Primary, 328 BSP, 536 EHV Switching Stn, 51 Super Grid, 33 "132kv Switching Station"). Columns: `Substation Number`, `LTDS Substation Name`, `Substation Type`. ZIP contents not inspected.
- This CSV is the lookup for heatmap `mRID`.

### 5. Embedded Capacity Register (ECR) -- `embedded-capacity-register`  (OPEN CSV)
- Resource "ECR AUG 2026" CSV 5,915,173 bytes, last_modified 2026-08-14 (portal date; `last_updated` inside rows e.g. 06/08/2026). Monthly. Also an "ECR Dashboard" link. Filename `nged_ecr_aug_2026.csv` changes monthly.
- Fully downloaded in scratch (5.9 MB): 7,211 rows, 60 columns. connection_status values: Connected 5598, "Accepted to connect" 1613 (no offered/scoping rows observed in this file). Combined >=50kW file (note scope says >=50 kW, so no separate 1 MW split).
- Key fields: `export_mpan/msid`, `customer_name`, `postcode`, eastings col `location(x-coordinate):_eastings_(where_data_is_held)` 521470, northings `location(y-coordinate):_northings_(where_data_is_held)` 316792, `grid_supply_point` "Walpole 132Kv S Stn", `bulk_supply_point` "Spalding Clay Lake 33Kv S Stn", `primary` "Crowland 33 11Kv S Stn", `point_of_connection(poc)_voltage(kv)` 0.4, `licence_area`, `energy_source_1/2/3`, `energy_source_&_conversion_tech_N_reg_capacity_mw`, `connection_status`, `already_connected_registered_capacity(mw)`, `connected_maximum_export_capacity(mw|mva)`, `accepted_to_connect_registered_capacity(mw)`, `date_connected` (dd/mm/yyyy), `target_energisation_date`, `in_a_connection_queue(y/n)`, `last_updated`, `lon`, `lat`, `local_authority`.
- Coordinates: both BNG (E/N) and WGS84 `lat`/`lon` columns. lat/lon blank for 1161 of 7211 rows (all of those have E/N). lat/lon all within GB bounds where present. DATA QUALITY: 1353 rows have eastings > 700000 (e.g. E=4897060, N=3700455 with lat 53.2189 lon -0.6513, i.e. the BNG pair appears multiplied by 10 for those; others have 8-11 digit garbage). Do not trust raw E/N blindly; validate ranges (0-700000, 0-1250000) and prefer lat/lon when present. Missing values written as "data not available"/"data not applicable".
- Join keys: names of GSP / BSP / primary as strings only (e.g. "Crowland 33 11Kv S Stn"); no IDs.

### 6. Generation Capacity Register -- `generation-capacity-register`  (REGISTERED)
- CSVs GCR.csv (1,143,983 B), GCR_SummaryByStatus.csv, GCR_SummaryByTechnology.csv, last_modified 2026-11-01, monthly. All redacted anonymously; columns not inspected. Description: Connected / Accepted-not-yet-Connected / Offered-not-yet-Accepted. (Whether it overlaps the open ECR was not checked.)

### 7. Connection Queue -- `connection-queue`  (OPEN, datastore)
- 45 CSV resources, one per GSP group ("Aberthaw And Cardiff East", ...), each small (1-6 KB). Portal modified 2026-10-30; resource last_modified 2026-03-18 (sampled). Fields: `Licence Area` "South Wales", `GSP`, `TANM`, `DANM` (bool text), `Status` "Accepted not yet Connected", `Bus Number` 502401, `Bus Name` "EAST3_MAIN2", `Site ID`, `Application ID`, `Site Export Capacity (MW)` 20.0, `Site Import Capacity (MW)`, `Machine Export Capacity (MW)`, `Machine Import Capacity (MW)`, `Fuel type`, `Machine ID`, `Position` 99.
- No coordinates, no names. `Bus Name` = LTDS node code (EAST3_MAIN2); a bus-number/name join to LTDS Attachment 8 / network model is possible but not tested.
- Other queue data: `nged-connections-reform-register` (OPEN CSV 10.6 MB, 2025-12-12; CMP435 projects; the first 3 lines are title text and the header is on line 4: `Licence Area,GSP,Company Name,Site Name,G2TWQ Status,Technology,Export Capacity (MW),Firm Connection Date,Non-Firm Connection Date`; GSP in "BERKSWELL 132kV S STN" case), `nged-projects-connections-reforms-outcomes` (open, not sampled), `clearview-connect-queue-positions` (public, not sampled).

### 8. Live power cuts -- `live-power-cuts`  (OPEN, near real time)
- CSV "Live Power Cuts" `power_outage_ext.csv` (5.7 KB when sampled) and "Live Detailed Power Cuts" `live_detailed_power_cuts.csv` (19 KB); updated 2026-11-12 (upload_date stamps seen: 11:30 and 11:35 on the day sampled, i.e. roughly 5-minute refresh).
- Fields (power_outage_ext): `Upload Date`, `Region`, `Incident ID` "INCD-21184-u", `Confirmed Off`, `Predicted Off`, `Restored`, `Status`, `Planned`, `Category` "LV GENERIC", `Resource Status`, `Start Time`, `ETR`, `Voltage` "LV", `Location Latitude` 51.46934, `Location Longitude` -2.588195, `Postcodes` (comma list). Detailed adds planned_outage_*, `date_of_reported_fault`, `last_updated`, `number_of_psr_customers`, `number_of_psr_critical_customers`, `postcode`.
- Coordinates WGS84. No substation identifier in the live faults file. Quoted CSV.

### 9. Live data / flows
- `live-data`: 4 area CSVs (eastmidlands.csv etc, ~10 KB, 5-minute rows, columns `Timestamp,Demand,Generation,Import,Solar,Wind,STOR,Other`, East Midlands sample; other areas not sampled). Area-level, not per-substation. Open.
- `live-gsp-data--<area>`: one CSV per GSP (e.g. berkswell_emids.csv, ~4.5 KB), open (null restriction). Columns `Timestamp,Net Demand,Generation,Import,Solar,Wind,STOR,Other`.
- `live-primary-data--<area>`: per-primary CSVs, columns `MW,MVA,Amps,MVAr,Volts,ValueDate`. Only a minority public; majority registered (e.g. East Midlands 311 registered of ~351). Filename carries the primary name only (no id) e.g. `atherstone-primary-transformer-flows.csv`.
- `live-bsp-data--*`: nearly all registered.
- `substation-loading`: 90 open CSVs ("<GSP group> Substation Load"), large (10-25 MB each, "_id_310_2026"), per-node load time series; not sampled beyond size.

### 10. Distribution (secondary) substations
- `distribution-substations` (47 MB CSV, modified 2026-11-01, monthly) and `distribution-substation-location-easting-northings` (18 MB, resource last_modified 2024-05-17): REGISTERED. Description: distribution substations and their primary parent. Columns not inspected. Location file is E/N by name.
- `substation-loading-details` (OPEN; 4 gzip/zip-named CSVs: Asset Types 1.6 MB, Customers 1.8 MB, IDNO Connections 36 KB, Substation Demand 1.7 MB, plus ERD PNG). Modified 2026-11-09. Resource format labels say CSV/.csv.gz but URLs end `.zip` -- inspect before parsing (not downloaded). Description: transformer make/model/rating and day/night max demand (kVA) per distribution substation. Join to location via the registered substation tables (not tested).
- `lv-load-monitor-data`: 301 resources; 172 registered, 127 open (datastore). Monitoring of a subset of LV substations. Not sampled.
- Shapefile/CAD layers (11kV/33kV/66kV/132kV OHL, poles, towers, UG cables, substations): packages exist but only a PDF Data Sharing Assessment is listed; the data is downloaded from https://dataportal2.nationalgrid.co.uk (separate portal, not checked). PDF-only on this CKAN.

### 11. Smart meter data
- `aggregated-smart-meter-data-lv-feeder` (4734 resources, portal modified 2026-11-01, "1 Month") and `-secondary-substation` (1585 resources). Resources are gzip CSV parts, named like `Aggregated-Smart-Meter-Data-Lv-Feeder-2026-09-part0231.csv.gz`, ~6.9 MB each (so tens of GB in total). All data parts REGISTERED (url redacted). Columns not inspected. Description cites Ofgem Data Best Practice (published from 28 Feb 2024). `smart-meter-volumes` (registered, count of meters).

### 12. Forecasts / LCT
- `dfes`: 10 resources; DFES Profiles CSV 40.5 MB, Profile Splits 1.3 MB, Energy Projections 1.6 MB, Annual Scaling Data 8.3 MB, Volume Projections by Local Authority 124 MB CSV, Volume Projections by ESA ZIP 55 MB. Mostly open. Not sampled. ESA = electricity supply area (spatial-datasets GPKG gives ESA polygons but that package is registered).
- `lct-connections` (LCT counts by primary substation per month since April 2017; 5.7 MB; REGISTERED), `lct-enquiries` (registered).
- `distribution-network-options-assessment-dnoa`, `system-planning_network-development-plan-ndp` (XLSX; open): not inspected.
- `gsp-technical-limits` (registered, 4 KB), `asset-limits-pre-event-transmission-distribution-limits` (open, 180 resources), `branch-loading`, `sensitivity-factors`, `generic-generator-profiles`, `curtailment_buses`: open, not inspected.

### 13. Spatial -- `spatial-datasets`
- GPKG for BSP/GSP/Primary supply areas per licence area (e.g. East Midlands Primary 7 MB) -- 16 of 17 resources registered. Substation boundaries, CRS not inspected.

## Join-key summary (NGED)
- LTDS node codes (`BANR5_BB_1_2`, connection-queue `Bus Name`) -> LTDS tables 1-4, Attachment 8.
- Heatmap `mRID` <-> CIM "Substation Number" (392/451 matched in East Midlands).
- Names (GSP/BSP/Primary) differ in style across ECR ("Crowland 33 11Kv S Stn"), heatmap ("Warwick 11Kv S Stn"), LTDS tables ("Banbury Road 33/11kV", "Berkswell 132kV"), reform register ("BERKSWELL 132kV S STN"). No single ID across all.
- ECR has lat/lon (WGS84) for most rows; heatmap has lat/lon for every row.

## Flags
- PDF-only/off-portal: all shapefile/CAD network layers (dataportal2.nationalgrid.co.uk), "Data Sharing Assessment" PDFs. LTDS CIM is ZIP (contents not inspected).
- Biggest blocker: Network Capacity Map, Generation Capacity Register, distribution substations, LV/secondary smart meter data, BSP/primary flows are all registered-only (needs account/API key).
