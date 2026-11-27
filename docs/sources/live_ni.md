# Live national context + Northern Ireland (recon 2026-11-12)

## Carbon intensity by region (built: `carbon_regions`)
- `carbon_regional` (api.carbonintensity.org.uk/regional, CC BY 4.0, 30 min): 14 DNO regions (ids 1-14) + England/Scotland/Wales/GB aggregates (15-18, ignored).
- Joined to `neso_dno_licence_areas` (GeoJSON in **EPSG:27700**, despite no coordinate hint in properties; 14 polygons, 3 MB). Mapping regionid -> polygon `ID` is in `live_carbon.REGION_TO_AREA`. Reprojected with pyproj, tippecanoe pmtiles (0.23 MB).
- Intensity `forecast` can be 0 for wind-only regions; that is a real zero.

## NESO embedded wind & solar forecast
- CKAN `embedded-wind-and-solar-forecasts` (datastore resource db6c038f-...): **national only** (EMBEDDED_WIND/SOLAR_FORECAST and _CAPACITY, MW, half-hourly, 14 days). No regional split, so no layer. `neso_regional_carbon_forecast` (136k rows) is a regional forecast of intensity only.
- The dated CSV download url 302s; use datastore_search.

## NIE Networks (Northern Ireland) - key needed (works since 2026-11-25)
- portal nienetworks.opendatasoft.com, 20 datasets, CC BY 4.0, all `data_visible=false` anonymously: `/records` 403, `/exports/csv` header-only. Needs a free account key -> `NIE_API_KEY` (not in .env). Sources registered in `sources.d/ni.yaml` as gated; fetch reports `needs_key`.
- Datasets: sites (GSP, BSP, primary, combined `grid-bulk-and-primary-sites`), secondary transformers (has `nominal_rating` + unit, geopoint, Irish Grid x/y), primary/transmission transformers, switchgear etc, `network-faults` (live outages, ~30 min; columns outageid, outagetype, starttime, estrestorefulldatetime, postcode, numcustaffected, statusmessage, updatedtimestamp: **postcode only, no coordinates**).
- No capacity/headroom/heatmap dataset exists on the portal. NIE's capacity map is not published as data here.
- NIE is a full operator (`NIE`), handled like the six GB DNOs. `normalise/nie.py` writes `asset` (GSP/BSP/PRIMARY from the `dataset` column) and `lv_site` (one per secondary site, kVA summed with unit conversion, customers and utilisation null). The old `ni_sites`/`ni_secondary` extra layers are gone.
- NIE publishes no headroom, capacity, queue, fault level or parent links. Sites show as "not published" (grey). LV parents come from the nearest-primary fallback in `/api/lv`.
- Verified 2026-11-25 with a key: 348 sites (10 GSP, 47 BSP, 291 primary; 7 unplaced), 76,921 secondary sites (66 without a usable rating). Site clustering merges same-name sites, so the map has 339 NIE sites.
- The opendatasoft faults (`nie_network_faults`) have no coordinates. They are placed at postcode-sector centres and used only as a fallback (see Powercheck below).

## NIE Powercheck faults (keyless, added 2026-11-27)
- `nie_powercheck_faults`: GET powercheck.nienetworks.co.uk/NIEPowerCheckerWebAPI/api/faults, JSON, every 10 min. No licence stated. Fetched by the backend only.
- Rows are in `outageMessage`. Points are Irish Grid (EPSG:29903) "E,N" strings, converted with pyproj. Times look like "8:44 PM, 26 Nov" (UK local). `estRestoreFullDateTime` has the year or reads "Not available - Update to follow".
- Outage ids match the opendatasoft feed (`5952818`, planned `2026/35481`). When a Powercheck snapshot is current the opendatasoft feed is skipped. If Powercheck is down the postcode-sector fallback still works.

## NI national figures (EirGrid Smart Grid Dashboard, added 2026-11-27)
- GET smartgriddashboard.com/api/chart/?region=NI&chartType=...&areas=...&dateRange=day with today's date (`{today:%d-%b-%Y}` in the registry). No licence stated. 15 min steps. Future slots are null.
- Working pairs: wind/windactual, solar/solaractual, demand/demandactual (not `system`), interconnection/interconnection, co2/co2intensity. `generation` and `default` return nothing. Frequency skipped (1.6 MB a day).
- The API sometimes answers with empty `Rows`. `live.national()` looks back over the kept snapshots.
- Signs: Moyle and net NI interconnection are positive for import into NI (Moyle 190 MW with demand 1184 MW and wind 164 MW). The UI says "Moyle: 190 MW to NI".
- Shown as an "NI:" line under the GB figures, only when data is present.

## SONI
- Dashboard at soni.ltd.uk/how-the-grid-works/system-data-dashboard/ ; robots.txt disallows `/api/`. Skipped. The EirGrid dashboard above carries the NI figures.
