# National / cross-DNO / live sources — reconnaissance

Probed 2026-11-12 ~11:30 UTC with curl (sampling only). "VERIFIED" = observed in a response; "UNVERIFIED" = from web search or inference only.
Per AI_GOTCHAS: resolve CKAN resource URLs via `package_show` each run; record fetch time and as-of date; keep source/licence/fetched_at per row.

## 0. Cadence summary (what is actually live)

| Source | Observed latency / cadence |
|---|---|
| Elexon FREQ (system frequency) | 15 s samples; newest sample was ~1 min old |
| Elexon FUELINST (generation by fuel) | 5-min samples, published every 5 min; newest startTime 11:25 at ~11:35 |
| Elexon FUELHH / INDO / ITSDO / AGPT / AGWS | half-hourly (AGPT/AGWS are the transparency-platform versions, ~30 min behind) |
| Elexon demand outturn summary | 5-min rows (recordType VD) |
| NESO Carbon Intensity API (national + 14 regions) | half-hourly, current half-hour present at 11:34 |
| NESO CKAN historic-generation-mix, regional CI forecast, demand datasets | refreshed several times a day (portal `metadata_modified` 11:10-11:17 today) |
| NESO TEC / embedded / interconnector registers | roughly weekly-ish (file name dated 10 Nov 2026; exact cadence UNVERIFIED) |
| UKPN `ukpn-national-energy-outage`, NPg `live-power-cuts-data`, ENW `live_incidents`, SPEN `distribution-network-live-outages` | metadata modified within last ~5 min (cadence by DNO; other agents cover details) |
| Protomaps basemap | daily build |

Nothing at per-GSP or per-substation level is live nationally. Elexon has no per-GSP data (see 2). Per-substation live data exists only at DNO level (e.g. NPg `npg-live-*` datasets updated 10:44-11:24 today; other agents cover).

## 1. NESO

### 1a. CKAN API
- Base: `https://api.neso.energy/api/3/action/` — `package_search`, `package_show`, `datastore_search` (VERIFIED, HTTP 200, `access-control-allow-origin: *`).
- Auth: none observed. Rate limit: none seen in headers (UNVERIFIED whether any exist; Cloudflare-fronted). Be polite and cache.
- Licence: "NESO Open Data Licence" (`license_id: ESO`). Condition (VERIFIED from https://www.neso.energy/data-portal/neso-open-licence): attribute exactly "Supported by National Energy SO Open Data"; commercial use allowed.
- Resource download URLs (`/dataset/<id>/resource/<id>/download/<file>`) return a 302 to Cloudflare R2 signed URL — use `curl -L`. Filenames change with each publish (e.g. `tec-register-10-november-2026.csv`).
- Datastore-enabled resources (`datastore_active: True`) can be paged with `datastore_search`; use `limit`/`offset` and compare `total`.

### 1b. Registers (package ids; VERIFIED)
| Package | Resource id | Rows | Key fields (example) |
|---|---|---|---|
| `transmission-entry-capacity-tec-register` | 17becbab-e3e8-473f-b303-3806f43a6a10 | 2220 | Project Name "012 NEP Coventry West"; Connection Site "Berkswell GSP"; MW Connected 0.0; MW Increase / Decrease 92.4; Cumulative Total Capacity (MW) 92.4; MW Effective From 2034-10-31; Project Status "Scoping"; Agreement Type "Embedded"; HOST TO "NGET"; Plant Type "Energy Storage System"; Project ID; Project Number PRO-003804; Gate |
| `embedded-register` | 68b6f3a1-e1bf-403b-9062-0269fc758d77 | 560 | same shape, no Agreement Type. Dataset notes say it is a list of embedded generation projects **in Scotland** (the sample row is HOST TO "SHET"). It is NOT the GB-wide distribution ECR. |
| `interconnector-register` | 64f7908f-f787-4977-93e1-5342a5f1357f | 35 | MW Import/Export Current/Total; Connection Site "Alverdiscott 400kV Substation"; XLINKS 1 LTD 1800 MW |
- No coordinates in any register; `Connection Site` is free text (e.g. "Berkswell GSP", "BRORA 132/33KV GRID SUPPLY POINT SUBSTATION"). Matching to sites is a name-matching job (see AI_GOTCHAS).
- Since 21 Nov 2025 registers carry a `Gate` column (1/2), blank until agreement countersigned.
- No MW/MVA basis stated; status values include Scoping (others UNVERIFIED — enumerate before use).

### 1c. GSP / licence-area boundaries and GSP locations (VERIFIED)
- Package `gis-boundaries-for-gb-grid-supply-points`. Newest: `gsp_regions_20260209.zip` (resource 5dfab3dd-f192-40ab-b97f-b365a594293c, 16 MB). Contains `Proj_4326/` and `Proj_27700/` each with `.geojson` (10-11 MB) and `.gpkg` (6 MB).
- 362 features. Properties: `fid`, `GSPs` ("LEGA_1"), `GSPGroup` ("_D"), `SSEP17Zone`, `DSCPs`, `InGroup_`, `CDCA_I030`. Some `GSPGroup` null.
- CRS: 4326 file declares CRS84 (lon,lat); 27700 file is EPSG:27700. The older 20181031 geojson URL returned a 302 HTML page when not following redirects — use `-L`. 20220314 geojson (13 MB) declares `urn:ogc:def:crs:EPSG::27700` in the file.
- Changelog resource (`gsp-regions-changelog.txt`) documents splits/merges (e.g. COWL_1/ECLA_1 split in 20250102). Region IDs change between versions; keep the version.
- GSP locations: resource `bbe2cc72-a6c6-46e6-8f4e-48b879467368` "GSP - Gnode - Direct Connect - Region Lookup 20181031", 380 rows (old, 2018): `gsp_id` 1, `gsp_name` "COWL_1", `gsp_lat` 51.71105, `gsp_lon` -1.18901, `region_name` "Cowley", `pes_id` 20, `pes_name` "_H" (WGS84).
- Newer GSP point list: FES 2024 "Grid Supply Point Info" (resource 21c2b09c-24ff-4837-a3b1-b6aea88f8124, 373 rows): `GSP ID` "ABNE_P", `GSP Group` "_P", `Name` "Abernethy", `Latitude` 56.339, `Longitude` -3.294, `Minor FLOP` "T4". (WGS84 assumed from values; 3-decimal precision.) In package `regional-breakdown-of-fes-data-electricity`; latest regional breakdown there is FES 2024 (FES 2025 not seen there — UNVERIFIED if published elsewhere).
- Package `gis-boundaries-for-gb-dno-license-areas`: 20240503 GeoJSON/Shapefile (resource 1c6a7dc0-1b6c-443a-bc67-5f7125649434). DNO `_A`..`_P` group codes = Elexon GSP group IDs.
- `etys-gb-transmission-system-boundaries`: ZIP `etys-boundary-gis-data-mar25.zip` (ETYS 2024 boundaries; modified 2025-06-02).

### 1d. Demand / generation datasets on CKAN (VERIFIED existence + sample)
- `historic-generation-mix` (resource f93d1835-75bc-43e5-84ad-12472b180a98, 312,816 rows, datastore): `DATETIME` "2026-05-23T09:00:00", GAS 3662, WIND 3772, WIND_EMB 972, SOLAR 10520, GENERATION 30302, CARBON_INTENSITY 137 ... (MW, half-hourly). Note the sampled first record was from May 2026 — records are not sorted newest-first; use `sort=DATETIME desc`.
- `regional-carbon-intensity-forecast` (resource c16b0e19-c02a-44a8-ba05-4db2c0545a2a, 136,190 rows): `datetime`, one column per 14 regions ("North Scotland": 44.0 ...). gCO2/kWh.
- `historic-demand-data`, `daily-demand-update`, `1-day-ahead-demand-forecast`, `7-day-ahead-national-forecast` etc.: national only, not per-GSP (field-level not sampled).
- `future-energy-scenario-fes-building-block-data` (FES 2020-2025 CSVs), `local-authority-level-spatial-heat-model-outputs-fes`, `tresp-*` pathways: exist (VERIFIED listing, not sampled).
- No "connections queue" dataset on NESO beyond the TEC/embedded/interconnector registers (search for "connections" returns only those). Joint connections dashboard is on energynetworks.org (see 4).

### 1e. Carbon Intensity API (carbonintensity.org.uk) — VERIFIED
- Base `https://api.carbonintensity.org.uk`. No key, CORS `*`, served by CloudFront.
- `/regional` (all 14 + England/Scotland/Wales aggregates, current half-hour): `regionid` 1, `dnoregion` "Scottish Hydro Electric Power Distribution", `shortname` "North Scotland", `intensity.forecast` 0, `index` "very low", `generationmix[{fuel,perc}]` (wind 98.8 ...).
- `/regional/postcode/SW1A` (outward code only): regionid 13 "UKPN London", forecast 130.
- `/regional/intensity/{from}/{to}/regionid/13` returns 30-min history/forecast: forecast 246 "very high".
- `/generation` national mix. Cadence: half-hourly. Rate limit: none observed, undocumented (UNVERIFIED). Licence: CC BY 4.0 per the project's published terms (UNVERIFIED in this session).
- Regions are DNO licence areas (14), not GSPs. Useful for a regional "live mix" overlay only.

## 2. Elexon BMRS Insights API — VERIFIED
- Base `https://data.elexon.co.uk/bmrs/api/v1`; swagger at `https://data.elexon.co.uk/swagger/v1/swagger.json` (326 paths). **No API key needed** for the endpoints below (HTTP 200 anonymous). Rate limits not observed in headers (responses are `Cache-Control: no-store`); published limits UNVERIFIED. Licence: BSC open licence / Elexon terms (not checked; confirm attribution wording).
- Times UTC, `format=json` (csv/xml also supported — UNVERIFIED), JSON key names in camelCase.

| Need | Endpoint | Cadence | Example |
|---|---|---|---|
| Generation by fuel, 5-min | `datasets/FUELINST?format=json` (also `/stream?publishDateTimeFrom=..&publishDateTimeTo=..`) | 5 min | `{"startTime":"2026-11-12T11:25:00Z","settlementPeriod":23,"fuelType":"CCGT","generation":7483}` (MW; interconnectors can be negative e.g. INTELEC -202) |
| Generation by fuel, half-hourly | `datasets/FUELHH?publishDateTimeFrom=..&publishDateTimeTo=..` | 30 min | BIOMASS 3144, CCGT 7883 |
| Current snapshot + % | `generation/outturn/current` | 5 min | `fuelType BIOMASS currentUsage 3144 currentPercentage 9.72` |
| Last 24 h summary | `generation/outturn/summary` | 30 min | |
| Transparency per-type | `datasets/AGPT?publishDateTimeFrom=..` ; `generation/actual/per-type/wind-and-solar?from=&to=` | 30 min | `psrType "Biomass" quantity 3142.000` |
| System frequency | `system/frequency?from=..&to=..` or `datasets/FREQ?measurementDateTimeFrom=..` | 15 s | `{"measurementTime":"2026-11-12T11:20:00Z","frequency":49.976}` |
| Initial national demand outturn | `datasets/INDO`, `datasets/ITSDO` | 30 min | INDO 29105; ITSDO 31398 (MW; INDO excludes embedded, ITSDO is transmission system demand) |
| Demand outturn 5-min | `demand/outturn/summary` | 5 min | `recordType VD, demand 38600` |
| Day-ahead demand forecast | `forecast/demand/day-ahead`; `forecast/generation/wind-and-solar/day-ahead` (needs `processType`) | | |
- Gotchas: `datasets/FREQ` ignores `from/to` (use `measurementDateTimeFrom/To`); `datasets/FREQ` default returns today's data newest-first only up to a cut-off, and `system/frequency` default is the previous day. `demand/actual/total` and `generation/actual/per-type` returned empty `data` for the recent window (publication lag). `datasets/B1610` returned empty for a 2-day-old settlement period (lag).
- **Per-GSP data: none found.** Searched all 326 swagger paths for gsp/group/embedded: only `demand/*`, `forecast/*`, `system/frequency`, `temperature`. GSP-group level data exists in Elexon settlement but not in this API (UNVERIFIED beyond the path search).
- Embedded wind/solar are in NESO `historic-generation-mix` (WIND_EMB, SOLAR) not in Elexon FUELINST.

## 3. Transmission

- **SSEN Transmission portal** (Opendatasoft): `https://ssentransmission.opendatasoft.com/api/explore/v2.1/` — VERIFIED, 69 datasets, CC BY 4.0.
  - Open (anonymous 200): ETYS 25 index of substation codes `etys-25-ssen-transmission-index-of-substation-codes` (455 rows: `site_code` "ABED", `site_name` "ABERARDER ONSHORE WIND FARM", `voltage_kv` 132.0 — no coordinates), ETYS 25 transformers `etys-25-202526-ssen-transmission-transformers` (252 rows: `node_1` "ABNE1Q", `node_2`, `rating_mva` 120.0, R/X/B in per-unit on 100 MVA), ETYS 25 circuits, reactive compensation, fault levels (kA, winter/summer, years 1/2/4/6/9 e.g. `etys-25-year-01-202526-ssen-transmission-fault-levels-winter` 237 rows), `ssen-transmission-data-triage-assessments` (148 rows), modified 2026-08-13.
  - **Locked (HTTP 403 ForbiddenAccess anonymously):** `ssen-transmission-substation-site-supergrid` (42 rows), `...-substation-site-grid` (120), `ssen-transmission-gi-location-points` (13,118), `...-tec-transmission-entry-capacity-register`. Open Net Zero listing says login required. Free account/login possibly unlocks them — UNVERIFIED; this is the likely route to SHET transmission substation coordinates.
  - Also listed (not sampled): tower/pole/overhead line grid & supergrid layers, `ssen-transmission-interconnector-register`, `ssen-transmission-embedded-register`.
- **SP Energy Networks portal** (covers SP Transmission): `spenergynetworks.opendatasoft.com`. `transmission-generation-heat-map0` (786 rows, CC BY 4.0, modified 2026-10-09) and `gsp-overview` (247,596 rows) are listed; `/records` calls on `transmission-generation-heat-map0` and `spd_spt_gis_shapefiles_point_assets` (322,391 rows, line assets 3.2M) returned 403 anonymously — need account / data request ("Shared Data Licence Terms" per search, UNVERIFIED). Other agents cover SPEN; flagged only because SPT asset/heatmap data is there.
- **National Grid ET (England & Wales transmission):** no open API found. nationalgrid.com is behind a Cloudflare challenge (not fetchable with curl). NG ET connection/ETYS-related data is mostly via NESO (TEC register above, ETYS boundaries ZIP, FES). NG ET substation locations: not found (UNVERIFIED that none exist). `connecteddata.nationalgrid.co.uk` is the NGED (distribution) CKAN, not NG ET.
- ETYS: full ETYS documents/appendix B workbooks are on neso.energy (not fetched). Only SSEN-T's ETYS tables were verified open.
- Substation names + 400/275/132 kV node codes (4-letter site codes, e.g. "ABED") come from ETYS appendix B; useful as join keys for NESO register `Connection Site`.

## 4. Cross-DNO

- **ENA connections dashboard** (https://www.energynetworks.org/industry/connecting-to-the-networks/connections-data): HTTP 403 Cloudflare to curl; contents known only from search snippets (queue totals e.g. 348 GW renewables, 222 GW storage; UNVERIFIED). No ENA open-data API found; candidate hosts `energynetworks.opendatasoft.com`, `opendata.energynetworks.org` do not exist. "ENA Data Triage" — no ENA portal found. (Note SSEN-T publishes its own `data-triage-assessments`.)
- **Power-cut / outage feeds — ENA "NEO" (National Energy Outage) format:** UKPN dataset `ukpn-national-energy-outage` is described as "aligned to the ENA NEO API design specification" (VERIFIED). Fields: `fault_id` "INCD-83391-N", `geometry_wkt` (GeoJSON Point, WGS84), `postcode` (malformed string e.g. `[\To Be Confirmed\"]"`), `licence_area` "London", `voltage` "LV", `status`, `restored`, `etr`, `confirmed_off`, `planned`, `as_at_date`, `outage_start_date`, `date_of_restoration`. 127 rows, as_at 11:26 today, CC BY 4.0, anonymous. Same-named SPEN dataset `national-energy-outage-data` (63 rows) returned 403 for `/records`; ENW has `sp-enw-live-neop` (48 rows). Other live-outage datasets seen in catalogs (existence only; other agents cover): UKPN `ukpn-live-faults`, `ukpn-ehv-network-outages`; NPg `live-power-cuts-data`, `npg-live-{33,66,132}kv-circuit`, `npg-live-bsp-operational`, `live-primary-operational-metering-14-day`; ENW `live_incidents`, `unplanned-outages`, `sp-enw-live-system-demand`, `sp-enw-live-customers-off-supply`; SPEN `distribution-network-live-outages`; NGED CKAN `live-power-cuts` (CSV `power_outage_ext.csv`, datastore active). SSEN distribution portal (data.ssen.co.uk) is not CKAN-API compatible at `/api/3` (404). NEOP (national platform, Arup/ENA) status: UNVERIFIED.
- **DFES:** national roll-up is NESO FES regional breakdown (above, to GSP level, FES 2024). DNO DFES at primary level is in each DNO portal (other agents).
- **EV chargepoints:** National Chargepoint Registry was decommissioned 28 Nov 2024 (web search; no replacement aggregate registry found; archived data on request to OZEV — UNVERIFIED contact). `api.openchargemap.io/v3/poi` returned HTTP 403 "You must specify an API key" — free key needed (verified requirement; free signup UNVERIFIED but standard). gov.uk publishes monthly "Electric vehicle public charging infrastructure statistics" (local-authority counts; built from Zapmap data per search) — URL not verified (guessed slugs 404). UKPN hosts an NCR mirror `ozev-ukpn-national-chargepoint-register` (three licence areas, stale).
- **MCS:** dashboard at https://datadashboard.mcscertified.com (HTTP 200 shell). Free registration; installs geographically at LA/region level; bulk-download/licence UNVERIFIED.
- **Ofgem:** not probed beyond search; no per-substation data identified.

## 5. Boundaries / reference
- GSP and DNO polygons: section 1c.
- **ONS LSOA 2021 (E&W):** ArcGIS REST `https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/LSOA_2021_EW_BSC_V4_RUC/FeatureServer/0` — VERIFIED anonymous; 35,672 features; `maxRecordCount` 2000 (page with `resultOffset`); CRS EPSG:27700; fields `LSOA21CD`, `LSOA21NM`, `BNG_E`, `BNG_N`, `LAT`, `LONG`, `RUC21CD`. BSC = super-generalised (small). Scotland uses Data Zones (not probed). OGL v3, attribution "Contains public sector information licensed under OGL v3.0 / ONS / OS data © Crown copyright" (standard wording; UNVERIFIED here). A full 2021 BFC/BGC variant was not enumerated.
- DNO 14 licence areas: use NESO polygons for consistency with GSP regions.

## 6. Offline basemap (VERIFIED unless noted)
- **Source:** Protomaps daily planet builds `https://build.protomaps.com/YYYYMMDD.pmtiles` (e.g. 20261111.pmtiles = 139.4 GB, last-modified 2026-11-11 09:03 UTC; 20261110, 20261109 also exist). Docs say do not hotlink; copy to own storage. Index UI: https://maps.protomaps.com/builds/ ; JSON index at https://build-metadata.protomaps.dev/builds.json (list of `{key,size,uploaded,version}`).
- **Extract (dry-run, bbox -8.7,49.8,1.9,60.9 — includes N. Ireland/Ireland/sea fringe; local pmtiles CLI at /opt/homebrew/bin/pmtiles, "dev" build):**
  | maxzoom | tiles in region | archive size | requests |
  |---|---|---|---|
  | 12 | 22,012 entries | ~350 MB | 62 |
  | 14 | 251,482 | ~1.5 GB (1.6 GB transferred) | 78 |
  | 15 (planet max) | 904,097 | ~3.0 GB (3.2 GB transferred) | 85 |
  Each dry-run completed in ~6-8 s. Command: `pmtiles extract https://build.protomaps.com/20261111.pmtiles gb.pmtiles --bbox=-8.7,49.8,1.9,60.9 --maxzoom=14`. A tighter GB-only polygon (`--region=geojson`) would cut size (not measured). Recommend z0-z14 (~1.5 GB) with overzoom for z15+, since the grid map's own layers are the focus; z0-z12 (~350 MB) is a "light" option.
- **Licence:** ODbL produced work; attribution "© OpenStreetMap contributors" required (docs.protomaps.com/basemaps/downloads). Include "Protomaps" credit as customary (not mandatory per docs; UNVERIFIED).
- **Style package (npm, queried today):** `@protomaps/basemaps` **5.7.2** (modified 2026-03-10, BSD-3, 443 KB unpacked) — current package. Old `protomaps-themes-base` 4.5.0 (Feb 2025, 48 MB unpacked) is legacy. Flavors: `light`, `dark`, `white`, `grayscale`, `black` (white/grayscale/black are dataviz-oriented, suit overlaying grid data). Use `layers(source, namedFlavor('grayscale'), {lang})` (API per docs; verify against package types when wiring up). Other versions: `pmtiles` 4.5.0 (browser protocol for MapLibre), `maplibre-gl` 6.18.0 (modified 2026-11-11; v6 major, check migration notes), `protomaps-leaflet` 5.1.0.
- **Glyphs/sprites:** the style needs `glyphs` and `sprite` URLs. Hosted copies return 200: `https://protomaps.github.io/basemaps-assets/fonts/Noto%20Sans%20Regular/0-255.pbf`, `.../sprites/v4/light.json`. For a fully offline build, vendor these from the `basemaps-assets` repo (not downloaded here).
- Plan: self-host `gb.pmtiles` (static file, HTTP range requests) next to the app; register `pmtiles` Protocol in MapLibre; layer our own grid vector tiles (tippecanoe → PMTiles) on top; ship a `grayscale`/`white` flavor to keep the basemap visually quiet.

## 7. Credentials the user may need to create
- Open Charge Map API key (free signup; 403 without). Everything in sections 1, 2, 5, 6 worked anonymously.
- Possibly free logins: SSEN Transmission portal (substation site/GI location layers 403 anonymously), SPEN portal (SPT shapefiles, heat map records 403), MCS dashboard registration. Whether login unlocks these via API is UNVERIFIED.
