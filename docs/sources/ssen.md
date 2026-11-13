# SSEN data sources (Distribution portal + Transmission portal)

Recon date: 2026-11-12. Everything below observed live unless marked "not inspected".

## SSEN Distribution: https://data.ssen.co.uk

### What it is
- NOT a plain CKAN site. `https://data.ssen.co.uk/api/3/action/package_list` -> 404 HTML. It is a Next.js (PortalJS/Datopian) front end. The backend is CKAN at `https://data-api.ssen.co.uk` (also referenced as `https://ckan-prod.sse.datopian.com` in the page JavaScript, which calls `.../api/3/action/package_search?start=&rows=&q=&fq=` from the browser). Catalogue exports are advertised at `https://ckan-prod.sse.datopian.com/catalog.jsonld|rdf|ttl`.
- Observed 403 Forbidden (nginx/Cloudflare-style HTML) for plain curl against: `data-api.ssen.co.uk/api/3/action/package_list`, `package_search`, `package_show`, `ckan-prod.sse.datopian.com/api/3/action/package_list|package_search`, AND every resource download URL (`data-api.ssen.co.uk/dataset/<pkg>/resource/<res>/download/<file>`; tested substation-data CSV, headroom CSV, real-time-outage CSV). The block is on automated clients; browsers work. I tried only default curl: one attempt to send a browser User-Agent was refused by the tool's permission check so I did not try to bypass it. Conclusion: whether an API key or a normal browser User-Agent / Referer unblocks this is UNTESTED. This is the main open question for SSEN Distribution. Need the user's decision (e.g. a registered account/API key from the portal sign-in at /auth/signin, or an agreed UA).
- What did work anonymously: the HTML pages. Each page embeds `__NEXT_DATA__` JSON with full CKAN metadata:
  - `https://data.ssen.co.uk/collections/<name>` (collections: asset-management 8, insights 3, network-capacity 6, network-flexibility 13, outage-management 3, reports-and-works 3) -> `pageProps.group.packages[]` with full package metadata incl. resources (url, format, size, last_modified).
  - `https://data.ssen.co.uk/@ssen-distribution/<package-name>` -> `pageProps.dataset` (full package_show equivalent).
  - `https://data.ssen.co.uk/search` -> first 10 of 36 datasets (count 36 from the server-rendered fallback); `?page=`, `?offset=`, `?limit=` query params did not change the SSR result. The site total is 36; I enumerated 30 via collections + dataset pages (a few packages are in no collection or were not reached). Missing from my list: roughly 6 packages (not identified).
  - `robots.txt`: `User-agent: * Allow: /`.
- Metadata from these pages is rich. Licence on every package seen: CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/). `is_restricted: false` on all packages inspected. Resource URLs are `https://data-api.ssen.co.uk/dataset/<pkgid>/resource/<resid>/download/<filename>`; filenames embed dates for the weekly/monthly ones (e.g. `20261109_substation_locations_csv.csv`), so resolve each run.
- The Smart Meter LV feeder dataset has a "Big Data" resource at `https://gql.internal.datopian.com/v1/` (a GraphQL endpoint with an "internal" hostname; not tested).
- Because downloads returned 403, NO SSEN Distribution CSV/XLSX/GeoJSON was sampled; column names and CRS below are unknown except where metadata or the real-time API show them.

### Datasets of interest (all CC-BY-4.0; metadata as seen on dataset pages)

1. `generation-availability-and-network-capacity` -- "Generation Availability and Network Capacity" (the heatmap/headroom data)
   - Portal modified 2026-03-11; no frequency set. 34 resources.
   - Newest: CSV and XLSX "Headroom Dashboard Data - March 2026" (CSV 342,983 bytes, XLSX 305,841), `headroom-dashboard-data-march-2026.csv`. Also an ArcGIS "Centralised Network View" experience https://experience.arcgis.com/experience/763db55663e845c7b6697995a32c32b5 (format "Graph") + PDF guidance.
   - Dec 2024 generation heat map CSVs split by level: SEPD GSP (48,912 B), SEPD BSP (47,806), SEPD Primary Substation (195,839), SEPD Contracted Generation (65,428), SEPD Quoted Jobs (12,024); SHEPD GSP (28,988), SHEPD Primary (97,940), SHEPD Contracted Generation (109,402); Shetland GSP (3,848), Shetland Primary (17,352). Also XLSX heat maps (SEPD/SHEPD/Shetland Dec 2024) and older Aug/Sept 2023 + Feb 2024 XLSX (demand + generation), "Generation Availability Map Attributes" and "Network Capacity Map Attributes" CSV data dictionaries, methodology PDF (2025-09-15).
   - Columns/coords: NOT inspected (403). Names suggest GSP/BSP/Primary levels, contracted generation and quoted jobs tabs; headroom basis unknown.
   - SEPD = Southern England, SHEPD = North of Scotland; Shetland separate. NOTE: in Scotland 132 kV is transmission, so SHEPD BSP/GSP structure differs (no SHEPD BSP CSV exists in the resource list).

2. `sepd_long_term_development_statement` / `shepd_long_term_development_statement` -- LTDS
   - Modified 2026-05-29; 45 / 49 resources; formats ZIP, KML, XLSX, JSON, PDF, CSV, GeoJSON. SEPD resources include: "SEPD Long Term Development Statement Capacity Heatmap - May 2026" as GeoJSON (2,240,838 B), JSON (1,922,348 B), KML (4.4 MB); LTDS Tables 1-7 XLSX (May 2026, 730,943 B); CIM ZIPs: "SEPD - LTDS CIM Fault Level Existing (EQ-SC-SCR) May 2026" (6.8 MB), "System Capacity Existing (EQ-SYSCAP)" (6.5 MB), "System Capacity Future Year 1" (6.9 MB), "CIM EQ Profile" (5.6 MB); PDFs (Part 1&2, schematics, geographic plans up to 42 MB). Data definitions CSV (31 KB).
   - The GeoJSON heatmap is likely the best coordinate-bearing headroom/fault-level source here but was not downloaded (403). CRS unknown (GeoJSON would normally be WGS84; verify).
   - LTDS tables are XLSX/ZIP/PDF only (no tabular CSV observed in SEPD list; flag).

3. `ssen-substation-data` -- "SSEN Substation Data" (substation list with type, ID and location coordinates, SEPD + SHEPD; "Derived from the Shapefile data")
   - Modified 2026-11-09, weekly. CSV 18,202,223 B (`20261109_substation_locations_csv.csv`), XLSX 8,579,878 B, methodology PDF. Likely the LV/secondary substation location source (size suggests ~100k+ rows) but UNVERIFIED. Coordinate fields/CRS unknown.

4. Supply-area boundaries: `secondary-supply-areas` (weekly; CSV 67.6 MB / GeoJSON 99.9 MB SEPD, CSV 46.7 MB / GeoJSON 68.3 MB SHEPD), `primary-supply-areas` (monthly; GeoJSON 10.5 MB SEPD, 31.7 MB SHEPD), `bsp-gsp-supply-areas` (monthly; BSP + GSP, SEPD and SHEPD; GeoJSON 3-30 MB). All dated 2026-10-19 in filenames. These exceed the ~20 MB sample limit for full download; sizes noted only. Likely the join between substation points and polygons. Fields not inspected.

5. `embedded_capacity_register` -- ECR
   - Modified 2026-10-12, monthly, 49 resources: Part 1 CSVs "SSEN ECR Part 1 - 1MW October 2026" (1,278,228 B) and "Part 1 - 50kW October 2026" (1,450,958 B), plus monthly XLSX history back to Aug 2023 (v4.0), attributes/definitions CSV (13.7 KB), ArcGIS dashboard. Columns not inspected. Two parts (>=1 MW / 50 kW-1 MW as named) -> avoid double counting when summing.

6. `ssen-connections-reform-queue` -- "SSEN Connections Reform Queue"
   - Modified 2026-10-29, monthly. XLSX only (41,514 B) + a data triage PDF. FLAG: XLSX-only.

7. `ssen_smart_meter_prod_lv_feeder` -- "Smart Meter LV Feeder Usage"
   - Modified 2026-10-30, "daily". 13 resources. Not a bulk CSV: the data is a "Big Data" GraphQL resource (`https://gql.internal.datopian.com/v1/`) plus small samples: Orkney Smart Meter Volumes CSV (64,827 B), Bressay aggregated demand 30-min CSV (5.4 MB), Bressay-Lerwick primary ZIP (13.5 MB), Tayvallich XLSX (1.1 MB), walkthrough MP4 (83 MB) and PDF guides. So full LV feeder data access is via the portal's query tool/GraphQL; not tested (403/unknown).
   - Identifier/coordinate fields unknown.

8. `low_carbon_technologies` -- "Distribution Future Energy Scenarios Datasets" (annual; modified 2025-12-17; 36 resources). DFES 2025 CSVs: "DFES 2025 SEPD Local Authority Technology Projections" (2.3 MB), "... SHEPD ..." (756 KB), XLSX "SSEN DFES 2025 - Results by licence area and ESA" (42.7 MB), DFES 2024 and 2023 equivalents (XLSX 25-50 MB), FES2020 OA XLSX. Mostly XLSX; only LA-level projections are CSV. Primary-substation-level projections would be inside the XLSX (not verified).

9. Network development reports: `sepd_network_development_report`, `shepd_network_development_report` (2026-05-01; CSV/XLSX/PDF, not inspected). `overhead lines`: `ssen_overhead_lines` (monthly; CSV 40.8 MB + route ZIPs EHV/EHVP/HV per licence area). `gis_network_line_data` ("[SHARED DATA]" format list empty).

10. Flexibility: `flexibility-day-ahead-bidding`, `flexibility-dispatch-report`, `flexibility-services-contract-register`, `short-term-bidding`, `long-term-bidding`, `sepd-flexibility-market-price-statement-april-2023`, `can-reporting-contract-award-notice`, `slc31e-procurement-report` -- XLSX/ZIP/PDF mostly; not relevant to the capacity map beyond flexibility zones. Not inspected. Also `seasonal-operability-report` (PDF only), `orkney_active_network_management`, `nerda_opengrid_dashboard`.

11. Faults/outages
   - `real_time_outage` -- "Real Time Outage": CSV 13,580 B (`20261112_real_time_outage_csv_csv.csv`), hourly, modified 2026-11-12 (403 to curl; not sampled).
   - `realtime_outage_dataset` -- "Real Time Outage Dataset": resource "Power Track Real Time Outage API" = `https://external.distribution.prd.ssen.co.uk/opendataportal-prd/v4/api/getallfaults`. THIS WORKS ANONYMOUSLY with plain curl (HTTP 200 JSON, 35.9 KB, no key). Structure: `{"timestampUtc": "2026-11-12T11:34:00Z", "faults": [...]}`, 49 faults at sample time (types: PSI 27, LV 16, HV 6). Fault fields: `title` "RG27 Area", `reference` "254579" / "UH9885", `loggedAtUtc`, `type` (PSI/LV/HV), `location` {`latitude` 51.2797798, `longitude` -0.8805178} (WGS84, plausible), `estimatedArrivalOnSiteUtc`, `estimatedRestorationTimeUtc`, `estimatedFaultUpdateTimeUtc`, `engineerOnSiteTimeUtc`, `message`, `affectedAreas` (postcodes list), `localSevereMessage`, `uri` (`.../getbyid/<reference>`), `depotCode` "29", `primaryNrn` 1002, `hvFeederNrn` 6, `transformerNrn` 20, `lvFeederNrn` 1, `jobStatus`, `jobSubType`, `transformerType` ("O" overhead / "U" underground / ""), `mergedTo`, `customerCount`. 
   - JOIN KEY (potentially valuable): `primaryNrn`, `hvFeederNrn`, `transformerNrn`, `lvFeederNrn` are network reference numbers (NRN) that look like they identify primary / HV feeder / distribution transformer / LV feeder. Whether they match any ID in `ssen-substation-data` or the smart-meter feeder data is UNVERIFIED.
   - `nafirs-sepd-faults` (annually, 2 resources CSV) and `nafirs-shepd-faults` (monthly): NaFIRS historic HV/LV outages, created 2026-11-10. 403, not sampled.

### SSEN Distribution summary
- Open licence CC-BY-4.0; no key requirement is documented on the dataset pages, but programmatic access via curl was blocked (403) for both the CKAN API and file downloads. Only the PowerTrack faults API and HTML pages worked.
- Formats flagged: connections queue XLSX-only; DFES mostly XLSX; LTDS tables XLSX; heat maps XLSX (with CSV for Dec 2024 and March 2026 "Headroom Dashboard Data").

## SSEN Transmission: https://ssentransmission.opendatasoft.com

- Opendatasoft (found via link on ssen-transmission.co.uk). Catalogue: `GET /api/explore/v2.1/catalog/datasets?limit=100` -> 200, `total_count` 69. Note `select=` on the catalogue call with `metas` fails (400); use no select.
- Licence on all listed: CC BY 4.0. Anonymous API works for datasets with public visibility; datasets with `visibility: "domain"` return 403 `ForbiddenAccess` on `/records` and an EMPTY export (CSV with header row only, 0 data rows). Observed for these key sets: `ssen-transmission-substation-site-supergrid` (42 records listed, fields geo_point_2d, geo_shape, ex_date, name, voltage), `ssen-transmission-substation-site-grid` (120), `ssen-transmission-tec-transmission-entry-capacity-register` (442), `ssen-transmission-embedded-register` (351), `ssen-transmission-interconnector-register` (2), `overhead-line-grid`, `ssen-transmission-tower-grid`. So these need login (not available to me).
- Public and working: ETYS data, e.g. `etys-25-year-01-202526-ssen-transmission-fault-levels-winter` (237 records, modified 2026-08-13). Sample record: `location` "Abernethy", `voltage_kv` 33.0, `three_phase_initial_peak_current_ka` 33.801, `symmetrical_three_phase_rms_break_current_ka` 13.419, `three_phase_dc_break_current_ka`, `asymmetrical_three_phase_peak_break_current_ka`, `single_phase_initial_peak_current_ka`, `symmetrical_single_phase_rms_break_current_ka`, etc. Fault level by site name only; no coordinates.
- `etys-25-ssen-transmission-index-of-substation-codes` (455 records): `site_code` "ABED", `site_name` "ABERARDER ONSHORE WIND FARM", `voltage_kv` 132.0 -- the join between ETYS site codes and names.
- `etys-25-202526-ssen-transmission-transformers` (252) and `...-circuits` (297): circuit sample `node_1` "ABNE1Q", `node_2` "ABNE3-", `r_on_100_mva`, `x_on_100_mva`, `b_on_100_mva`, `rating_mva` 120.0 (4-char site code prefixes match NESO/ETYS conventions).
- ETYS 22/23/24/25 versions are all present (several years, summer/winter, years 1-12) -- many near-duplicate datasets.
- Other datasets listed: GI location points (13,118), pole/tower grid, overhead lines, Eastern Green Link 2 points/route, glossary, data triage assessments, C17 reports.
- No generation availability / headroom heatmap dataset on the Transmission portal catalogue (none of the 69 titles). NOTE: SSEN's "generation availability" heat maps for the distribution network are on the Distribution portal (item 1 above); for 132 kV in the north of Scotland, see transmission/NESO data.
- Not checked: whether NESO TEC register covers the SSEN Transmission TEC data; the SSEN transmission "project map" page (https://www.ssen-transmission.co.uk/projects/project-map/).

## Join keys (SSEN)
- Distribution: no verified join key. Candidates: substation ID fields in `ssen-substation-data` and supply-area CSVs (unseen), and PowerTrack `primaryNrn`/`transformerNrn`/`lvFeederNrn` (NRN numbers). LTDS CIM likely has mRID/identifiers (unseen).
- Transmission: ETYS `site_code` (4 chars, e.g. "ABED") <-> index-of-substation-codes; fault-level files use site name.
