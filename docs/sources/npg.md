# Northern Powergrid (NPG) - Opendatasoft portal recon

Observed 2026-11-12 (portal says "today"; fetched with curl, anonymous). Base: `https://northernpowergrid.opendatasoft.com/api/explore/v2.1/catalog/datasets`
Catalog: 101 datasets. Only what was seen is reported; no full exports downloaded (only `limit=1/2` samples).

## Access (read this first)
- Anonymous `/records` works for **54 of 101** datasets (`data_visible: true` in catalog metadata). The other **47 return HTTP 403 ForbiddenAccess** on `/records`, and `/exports/csv` returns **header row only (HTTP 200, zero rows)**. Catalog metadata (title, fields, count) is still public for the gated ones. Detect via `data_visible` in `/catalog/datasets/{id}`.
- Gated datasets include almost all LTDS appendix tables (3,4,5,6,7,8,9,10,11), `ltds-capacity-heatmap`, `ltds_ehv_sites`, site utilisation (secondary), service areas, circuit live/daily data, curtailment, smart metering, LV monitor strategy. Presumably need a free portal login/API key (not tested; no credentials used).
- Anonymous quota seen: `x-ratelimit-limit: 5000`, reset daily 00:00 UTC.
- Licence: "Northern Powergrid Open Data Licence v1.0" on 97 datasets; 3 "Northern Powergrid Shared Data Licence - Underground Assets" (cables, gated); 1 CC BY 4.0. (AI_GOTCHAS lists NPG Open Data Licence v1.0.)
- `modified` = portal metadata date, not data as-of date.

## CRS / geometry
- WGS84 lat/lon via `geo_point_2d`/`geopoint`/`substation_location`/`latlong` (ODS `{lon,lat}` object). In CSV export `latlong` is `"lat, lon"` string (lat first!).
- BNG eastings/northings (verified: Abbey Road 424557/436941 <-> lon -1.628, lat 53.828): `heatmapdatatable.grid_easting/northing` (double), `substation_sites_list.eastings/northings` (double), ECR `location_x/y_coordinate_*` (**text strings**, e.g. "412755").
- Polygons are GeoJSON Feature in `geo_shape` (WGS84).
- Don't trust national ECR coordinates: `ecr_manual_combine_test` sample (NGED row) had eastings "4191946"/northings "2553826" (scaled/invalid).

## Key datasets - usable anonymously (VIS)
| id | title | total_count | modified | notes |
|---|---|---|---|---|
| `heatmapdatatable` | Heat Map Data - Table | 670 | 2026-07-27 | headroom + fault level + firm cap per site. typetable: GSP 41 / Primary 551 / Supply Point 78. Fields: psp_name, grid_easting/northing, pvoltage, firm_cap, mindemand, maxdemand, rpf, gentot, demtot, genhr (gen headroom), demhr (demand headroom), fl_break_d/r, fl_make_d/r (kA, d=demand? r=rating - verify vs glossary), fl (ratio), fault_level_ (%), worst_tap, genconstraint/demconstraint, worst_case_constraint_gen/dem_colour (Red/Amber/Green), gsp_name, upstreamname, odp_primary_name, substation_location. Example: psp_name "Abbey Road", pvoltage 11, firm_cap 23, genhr 2.042, demhr 14.06, fl_make_d 22.06, fl_make_r 32.80. **No stable ID** (join by name). |
| `heatmapsubstationareas` | Heat Map Data - Substation Areas | 683 | 2026-10-06 | same columns as above but with `name` + `geo_shape` polygon + local_authority. |
| `substation_sites_list` | Substation Sites List | 57,933 | 2026-07-23 | site list incl. secondary: site_level GSP 41 / BSP 100 / Primary 542 / Distribution 57,250; dno_area North East 26,410 / Yorkshire 31,523. Fields: site_name, **substation_id** (e.g. "480944446082"), site_type (Ground/Pole Mounted), transformer_rating_kva, firm_capacity_mva (text, "Not Applicable"), customer_numbers (text, "<=5"), primary/secondary_voltage_kv, maximum_demand ("Redacted" in sample), upstream_substation (name), postcode, eastings, northings, latlong. Methodology PDF attached. Many text fields hold "Redacted"/"Not Applicable". |
| `embedded-capacity-register` | ECR 1MW and Over | 934 | 2026-10-14 | monthly (10th working day). ecrid "NPg15-000863", status Connected 485 / Accepted To Connect 449, grid_supply_point/bulk_supply_point/primary names (text), POC voltage, MW capacities mostly **text** type, eastings/northings text, `geopoint`, in_a_connection_queue_y_n. |
| `embedded-capacity-register-part-2` | ECR < 1MW | 1,881 | 2026-10-14 | same schema; Connected 1,311 / Accepted 570. |
| `ecr_manual_combine_test` | National ECR (all DNOs combined) | 25,167 | 2026-09-12 | cross-DNO ECR on NPG portal; name suggests test; coordinate issues above. Treat as convenience only. |
| `connection-queue-information` | Connection Queue Information | 445 | 2026-02-18 (processed 2026-06-17) | gsp, technology, export_capacity, queue_position, transmission_gate, final_queue_rank. Example: gsp "Borders Connection Node A" (note: odd GSP name, not NPG's own - check). |
| `grid-supply-point-connection-information` | GSP Connection Information | 50 | 2026-02-19 | gsp_id, title ("Spennymoor 400/132kV"), connected_capacity_to_date_mw, technical import/export limits. |
| `gsp-appendix-g-information` | Appendix G Information | 40 | 2025-05-13 | contracted parts 1-5 MW per GSP, geo_shape. |
| `historical-peak-demand` | Historical Peak Demand | 638 | 2025-04-01 | EHV sites, max load MVA 2019/20-2023/24 (grid_supply_point, bulk_supply_point, primary names). |
| `northern-powergrid-load-indices` | Load Indices | 612 | 2024-01-26 | firm capacity (MVA), observed max demand, limiting_factor, by substation_name. |
| `large-scale-demand` | Large Scale Demand | 406 | 2026-01-08 | accepted demand by gsp/bsp/psp name and con_name. |
| `npg_ndp_demand_headroom` / `npg_ndp_generation_headroom` | NDP thermal demand / generation headroom | 1,881 each | 2026-04-24 (annual, May) | MW per year 2026-2035, 2040/45/50 by scenario (DFES 2026 Holistic Transition etc.), substation_name, voltage_kv, supply_voltage_kv, bulk_supply_point_or_primary, bsp_group, gsp_group, geo_point_2d + geo_shape. Copies `...-thermal-demand-headroom-copy` / `...-generation-headroom-copy` (24,453 rows, "portal format"). PDFs attached. |
| `northern-powergrid-dfes` | DFES 2026 Draft | 91,906 | 2026-08-21 | wide (columns 2025..2050). dfes_level BSP/GSP/Primary/Local Authority; 3 scenarios (NG FES Electric Engagement / Holistic Transition / Hydrogen Evolution); 31 output_parameters incl. "Number of heat pumps (total)", "Number of electric cars and vans...", demand MWh. Key = `substation_local_authority` (name, upper-case e.g. "SKELDERGATE"). |
| `northern-powergrid-dfes-portalformat` | same, long format | 2,389,281 | 2026-08-21 | `year`, `value` columns. |
| `lct-datasets` / `lct-datasets-ps-upload` | LCT by postal district / sector | 354 / 1,254 | 2025-11-19 | counts PV, heat pumps, storage, EV chargers + polygons. Current-installed, not forecast. |
| `substation_combined_service_areas` | EHV Substation Combined Service Areas | 683 | 2026-01-15 | polygons: primary (name), substation_class. |
| `technical-limits` | Technical Limits tracker | 35 | 2026-07-14 | GSP process dates. |
| `slc31e-procurement`, `-locational`, `slc31e-dispatch` | Flexibility procurement/dispatch | 459 / 40 / 149 | 2026-06-09 | tender results (GSP name, constraint_management_zone_name). Methodology PDFs attached. |
| `npg-network-development-report-upcoming-flexibility-services` | Upcoming flex needs | 99 | 2026-04-30 (annual May) | substation point + polygon. |
| `distribution-network-options-assessment-dnoa` | DNOA | 13 | 2026-10-13 (6-monthly) | substation_name, flexibility_required_mw by year. |
| `live-power-cuts-data` | Live Power Cut Data | 578 | 2026-11-12 | **updated every 5 min** (per description). lat/lng + geopoint, type (e.g. "LV"), incidentstatus, totalpredictedpowercut. This is the live-faults source. |
| `lv-support-locations` | LV poles/towers | 179,065 | 2026-03-20 | points only; not substations. |
| `whole-electricity-system-co-ordination-register` 45; `third-party-data-sources`; `icp-and-idno-briefings`; boundaries (`all_dno_boundaries`, `npg_postcode_sectors`, `npg-postal-district`, LA/RESP/CA/AONB) | supporting | - | - | boundary polygons are useful for context. |

## Gated (403 anonymously; fields from catalog metadata only, no sample values seen)
- `ltds-capacity-heatmap` (670, mod 2026-05-29): mrid, name, latitude, longitude, type, voltages, gsp, bsp, demand/generation firm/available capacity, reverse power flow, constraint text, pastconnectionactivity, area. Has `mrid` (likely the best join key) - this is the LTDS-standard heatmap.
- `ltds-appendix-6` Fault Level Data (1,066, mod 2025-11-29): s_s_group, s_s_or_busbar_name, nominal_voltage_kv, 3_ph_or_1_ph, peak make / RMS break kA, make/break ratings kA. Fault levels are also available anonymously in `heatmapdatatable`.
- `ltds-appendix-4` Transformer Data (939), `-3` Circuit Data (1,670), `-5` Load Information (661, with geo_point_2d), `-7` Generation (993), `-8` Interest in Connections (476), `-9` Development Proposals (41), `-10` LA table (868), `-11` Substation Feeding Postcodes (555; supply point/primary reference IDs + coordinate x/y).
- `ltds_ehv_sites` (846; asset_number, site_name, site_purpose, geo) mod 2023.
- `npg-site-utilisation` (28,043, mod 2026-08-03): per distribution substation max_demand_kw, firm_capacity_kw, transformer_headroom_kw, utilisation, eam_site_asset_id, site_easting/northing (text), geopoint. `npg-site-utilisation-forecasted` (29,121) forecasts to 2050. `distribution-substation-service-areas` (57,978 polygons; source_site_spid/eamid). These are the secondary-substation utilisation sources; join to `substation_sites_list` plausibly via EAM id (not verified).
- Smart meter: `aggregated-smart-metering-data` (21 rows - fields month, substation_download, feeder_download = **file-link/attachment style**, half-hourly LV/feeder data are downloads); `smartmap` LA-level penetration.
- Live/ops: `npg-live-33kv/66kv/132kv-circuit` (860,812 / 327,212 / 242,364 rows; 30-day half-hourly current), `npg-daily-*-circuit`, `npg-live-bsp-operational` (236,492; kW half-hourly, site_name key), `live-primary-operational-metering-14-day` (418,086), `operational-data-connectivity` (3,918; asset link table GSP/BSP/feeder).
- Curtailment: `curtailment-events-site-specific` (348 events RIIO-ED2; customer_unique_identifier, scheme_name, start/end time, MWh, geo).
- `lv-monitor-deployment-strategy` (15,062; eam_site_asset_id, utilisation, geopoint). `small-scale-generation` (446; by primary_substation, G98/G99 <50kW). `project-progression-programme`, `npg-flexibility-dispatch-ehv-zone` (1,149).
- Underground cable/feeder geometry (`lv-underground-cables` 508,552, `npg-hv-underground-cables`, `npg-ehv-underground-feeders`, OH feeders): shared-licence/gated.

## Attachment-only / non-tabular
- `primary-operational-metering` and `grid-supply-point-operational-metering`: records_count 0, data only as attached zip files (2022). `data-roadmap`, `npg-network-development-plan-report-and-methodology`, `artificial-forecasting-project-outputs0`: 0 records.
- `ltds-zips`, `ltds-documents` (4 rows each): links to LTDS PDF/summary files. `ltds-cim-files` (41): CIM model files per substation (file fields).
- Many tabular datasets also carry methodology/report PDFs as attachments (NDP, ECR, DNOA, SLC31E).

## Join keys (observed)
- Names only: heatmap `psp_name`/`odp_primary_name`/`upstreamname`/`gsp_name`; ECR `grid_supply_point`/`bulk_supply_point`/`primary`; DFES `substation_local_authority`; NDP `substation_name`; LTDS `s_s_or_busbar_name`. Name normalisation needed (examples: "Kirkstall C/Ring Road Horsforth", "Sowerby Bridge 132/33kV", "Annfield").
- IDs: `substation_sites_list.substation_id` (12-digit), `ltds-capacity-heatmap.mrid`, `eam_site_asset_id` (gated), `ecrid`.
- Coordinates are the tie-breaker.

## Surprises
- Metadata says "Open" but ~47% of datasets (nearly all LTDS appendix tables and the LTDS heatmap) return no rows anonymously; the gap is silent (200 with header only on export).
- `substation_sites_list` gives the whole secondary substation estate with BNG + WGS84 anonymously, but demand/utilisation fields are redacted.
- Fault levels, firm capacity and headroom for 670 EHV sites are available anonymously via `heatmapdatatable`.
