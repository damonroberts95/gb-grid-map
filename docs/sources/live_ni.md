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
- `ni_faults` not built: would need postcode geocoding (ONSPD not NI-complete / not fetched).

## SONI
- Dashboard at soni.ltd.uk/how-the-grid-works/system-data-dashboard/ ; robots.txt disallows `/api/`; no open API/dataset found. Skipped.
